"""Meta model hub: every model call in Crisis Command Swarm lives in this file.

Each function returns a `ModelResult`: the value, the model it is attributed to, and where
it actually came from:

- "live": the model was called just now.
- "cache": a response recorded from an earlier live call of the same model with the same
  input (`demo_cache/responses/`). Demo mode uses only these, so it works offline and every
  cached answer is a real one.
- "local": code that runs on this machine (PII redaction, plan search); not a model.
- "sample": hand-made data for a model this account is not connected to (e.g. SAM 3.1).
- "fallback": rules used because the model call failed and nothing was cached.

The UI shows that source next to the model pill, so nothing is presented as a model output
unless a model produced it. Configuration: META_API_KEY / META_MODEL / META_BASE_URL (see
`agents/model_client.py`); META_MODE=demo replays the cache only.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
import re
from typing import Any, Callable

from .agents.model_client import (
    ModelClient,
    UnavailableModelClient,
    _env_file_values,
    model_client_from_env,
    parse_json_object,
)

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "demo_cache" / "responses"
KNOWLEDGE_DIR = ROOT / "demo_cache" / "knowledge"
SAMPLE_DIR = ROOT / "demo_cache" / "samples"

# Display names, and whether this account can call each model. Only Muse Spark is wired to
# a live endpoint; the others are named for the architecture and fall back as labelled.
MUSE_SPARK = "Muse Spark"
MUSE_VOICE = "Muse Voice Transcribe"
SAM = "SAM 3.1"
MUSE_WONDER = "Muse Wonder"
# Code that runs on this machine (no model).
LOCAL_REDACTION = "Local redaction"
PLAN_SEARCH = "Plan search"
MUSE_IMAGE = "Muse Image"

NOT_CONNECTED = {
    MUSE_VOICE: "no transcription endpoint configured; recorded sample transcripts are used",
    SAM: "no segmentation endpoint configured; sample masks are used",
    MUSE_WONDER: "no multimodal endpoint configured; sample estimates are used",
    MUSE_IMAGE: "no image endpoint configured; no alert graphic is generated",
}


# Appended to every Muse Spark prompt.
GROUNDING_RULE = (
    " GROUNDING RULE: Never invent a shelter, a capacity, a road status, an evacuation zone, a unit, "
    "a place, a time or a number. Use only the facts given and cite them; if something is not in the "
    "facts, say it is unknown."
)
# Where the operation is, and what hazard. Set per scenario (CommandService does it), so
# prompts and fallback text name the right city; the defaults are the bundled demo's.
DEFAULT_CITY, DEFAULT_CITY_SHORT, DEFAULT_HAZARD = "Richmond, VA", "Richmond", "flood"
NOT_IN_PLAN = f"Not in {DEFAULT_CITY_SHORT} plan - requires human verification"


def not_in_plan(city_short: str) -> str:
    return f"Not in {city_short} plan - requires human verification"


def unsupported_numbers(value: Any, facts: Any) -> list[str]:
    """Numbers in a model's text that appear nowhere in the facts it was given.

    A cheap hallucination check: each flagged number is shown to the operator as
    unverified. (Numbers written as words are not checked.)"""
    texts = []

    def collect(item: Any) -> None:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict):
            for v in item.values():
                collect(v)
        elif isinstance(item, (list, tuple)):
            for v in item:
                collect(v)

    collect(value)
    known = set(re.findall(r"\d+(?:\.\d+)?", json.dumps(facts, default=str)))
    flagged = []
    for text in texts:
        for number in re.findall(r"\d+(?:\.\d+)?", text):
            if number not in known and number not in flagged:
                flagged.append(number)
    return flagged


# .env settings naming each model's ID (and endpoint, if not META_BASE_URL).
MODEL_SETTINGS = {
    MUSE_VOICE: "META_VOICE",
    SAM: "META_SAM",
    MUSE_WONDER: "META_WONDER",
    MUSE_IMAGE: "META_IMAGE",
}


# Models with a live call implemented here (besides Muse Spark).
WIRED = {SAM, MUSE_WONDER, MUSE_VOICE}
ASR_URL = "wss://api.meta.ai/v1/asr/realtime"


def wav_to_pcm24k(path: Path) -> bytes:
    """16-bit mono PCM at 24 kHz, the format the realtime ASR expects."""
    import wave
    from math import gcd

    import numpy as np
    from scipy.signal import resample_poly

    with wave.open(str(path)) as clip:
        rate, channels = clip.getframerate(), clip.getnchannels()
        samples = np.frombuffer(clip.readframes(clip.getnframes()), dtype=np.int16).astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != 24000:
        g = gcd(24000, rate)
        samples = resample_poly(samples, 24000 // g, rate // g)
    return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()


async def _stream_asr(pcm: bytes, key: str, model: str, pace_s: float = 0.2) -> str:
    """Stream PCM to the realtime ASR at real-time pace; return the last cumulative transcript."""
    import asyncio

    import websockets

    async with websockets.connect(ASR_URL, open_timeout=30) as ws:
        await ws.send(json.dumps({"authorization": {"accessToken": f"Bearer {key}"}, "audioEncoding": "PCM_24KHZ",
                                  "model": model, "mode": "PUSH_TO_TALK", "partialMode": "CUMULATIVE",
                                  "emitAudioProgress": False}))
        await ws.recv()  # handshake: {"sessionId": ...}
        chunk = int(24000 * 2 * pace_s)
        for i in range(0, len(pcm), chunk):
            await ws.send(pcm[i:i + chunk])
            await asyncio.sleep(pace_s)
        transcript = ""
        try:
            while True:
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=6))
                if message.get("type") == "transcript":
                    transcript = message.get("transcript", "")
                    if message.get("final"):
                        break
        except asyncio.TimeoutError:
            pass  # push-to-talk without an end signal: keep the last cumulative text
        return transcript.strip()


def _mime(path: Path) -> str:
    return {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(
        path.suffix.lower(), "application/octet-stream")


def _sam_boxes(text: str) -> dict[str, Any] | None:
    """Parse SAM 3.1 output: '<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|>' per object; empty = none found."""
    boxes, size = [], None
    for match in re.finditer(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)\|>", text):
        x1, y1, x2, y2, w, h = (int(v) for v in match.groups())
        boxes.append([x1, y1, x2, y2])
        size = [w, h]
    if not boxes and text.strip() not in ("", "<0f>"):
        return None if "<|" in text else {"boxes": [], "size": [0, 0]}
    return {"boxes": boxes, "size": size or [0, 0]}


def setting(name: str) -> str:
    return (os.environ.get(name) or _env_file_values().get(name, "")).strip()


@dataclass(frozen=True)
class ModelResult:
    value: Any
    model: str
    source: str  # live | cache | local | sample | fallback
    note: str = ""
    latency_ms: int = 0

    @property
    def label(self) -> str:
        return f"{self.model} · {self.source}"


@dataclass
class MetaHub:
    client: ModelClient
    mode: str = "live"  # live | demo
    # Read at construction so tests can point it elsewhere.
    cache_dir: Path = field(default_factory=lambda: CACHE_DIR)
    timeout: float = 20.0
    # Called with (model, message) when a call falls back; the swarm logs it to the feed.
    on_fallback: Callable[[str, str], None] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    # Live answers are recorded only where nothing is recorded yet, unless re-recording, so
    # ordinary live use never changes what the demo replays.
    overwrite: bool = False
    # Whether live answers are saved at all. The running server turns this off so everyday
    # use never writes into the repo; re-recording the demo turns it on.
    record: bool = True
    city: str = DEFAULT_CITY
    city_short: str = DEFAULT_CITY_SHORT
    hazard: str = DEFAULT_HAZARD

    @property
    def spark_live(self) -> bool:
        return self.mode == "live" and not isinstance(self.client, UnavailableModelClient)

    # Demo mode replays the recording when there is one and calls the live model only when
    # there is none (e.g. a resident's new report), without recording it, so the scripted run
    # stays identical and the committed recordings stay the demo's own.
    def _may_call(self, model: str) -> bool:
        # An unavailable client means offline (no key, or tests): no model is called live.
        if self.mode not in ("live", "demo") or isinstance(self.client, UnavailableModelClient):
            return False
        return model == MUSE_SPARK or self.wired(model)

    def _replay(self, function: str, payload: Any, model: str) -> "ModelResult | None":
        if self.mode != "demo":
            return None
        cached = self._cached(function, payload)
        if cached is None:
            return None
        return ModelResult(cached["output"], model, "cache", f"recorded {cached['recorded_at'][:16]}")

    def _keep(self, function: str, payload: Any, model: str, output: Any) -> None:
        if self.mode == "live" and self.record:
            self._record(function, payload, model, output)

    def wired(self, model: str) -> bool:
        """An image model is called live when its ID is configured (SAM 3.1, Muse Wonder)."""
        return model in WIRED and bool(setting(f"{MODEL_SETTINGS[model]}_MODEL"))

    def status(self) -> list[dict[str, str]]:
        spark = ("live" if self.spark_live else ("recordings, live when none" if self._may_call(MUSE_SPARK)
                 else "cache (demo mode)") if self.mode == "demo" else "not connected")
        rows = [{"model": MUSE_SPARK, "status": spark,
                 "detail": f"{self.client.model_id} via Meta Responses API" if self.spark_live else ""}]
        for model, detail in NOT_CONNECTED.items():
            if self.wired(model):
                rows.append({"model": model, "status": "live" if self.mode == "live" else "recordings, live when none",
                             "detail": setting(f"{MODEL_SETTINGS[model]}_MODEL")})
                continue
            model_id = setting(f"{MODEL_SETTINGS[model]}_MODEL")
            # Filling in an ID records it; the call itself still has to be written for that
            # model's input type (audio, image), so it is not reported as connected.
            rows.append({"model": model, "status": "configured, call not wired yet" if model_id else "not connected",
                         "detail": f"{MODEL_SETTINGS[model]}_MODEL={model_id}" if model_id else detail})
        return rows

    # ----- record and replay ----------------------------------------------

    def _key(self, function: str, payload: Any) -> Path:
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
        return self.cache_dir / function / f"{digest}.json"

    def _cached(self, function: str, payload: Any) -> dict[str, Any] | None:
        path = self._key(function, payload)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _record(self, function: str, payload: Any, model: str, output: Any) -> None:
        path = self._key(function, payload)
        if path.exists() and not self.overwrite:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "function": function, "model": model, "model_id": self.client.model_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(), "input": payload, "output": output,
        }, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    def _spark(self, function: str, system: str, payload: dict[str, Any], schema: dict[str, Any],
               fallback: Callable[[], Any]) -> ModelResult:
        """Muse Spark with record/replay; falls back to the cache, then to rules."""
        started = datetime.now(timezone.utc)
        if (replayed := self._replay(function, payload, MUSE_SPARK)) is not None:
            return replayed
        if self._may_call(MUSE_SPARK):
            try:
                output = self.client.complete_json(system=system, user=json.dumps(payload, default=str),
                                                   schema=schema, timeout=self.timeout)
                self._keep(function, payload, MUSE_SPARK, output)
                ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
                self.calls.append({"function": function, "source": "live", "ms": ms})
                return ModelResult(output, MUSE_SPARK, "live", latency_ms=ms)
            except Exception as error:  # noqa: BLE001 - reported, then cache or rules
                failure = f"{function} live call failed ({type(error).__name__}: {error})"
        else:
            failure = None
        cached = self._cached(function, payload)
        if cached is not None:
            if failure and self.on_fallback:
                self.on_fallback(MUSE_SPARK, f"{failure}; used cached response from {cached['recorded_at'][:16]}")
            return ModelResult(cached["output"], MUSE_SPARK, "cache", f"recorded {cached['recorded_at'][:16]}")
        if self.on_fallback:
            self.on_fallback(MUSE_SPARK, (failure or f"{function}: no live model and no cached response")
                             + "; used rules")
        return ModelResult(fallback(), MUSE_SPARK, "fallback", failure or "no live model, no cached response")

    # ----- 1. Muse Voice Transcribe ----------------------------------------

    def transcribe(self, pcm: bytes) -> ModelResult:
        """Muse Voice Transcribe over the realtime WebSocket (24 kHz 16-bit mono PCM), with the
        same record/replay as the other models; the value is {"text": ...}."""
        import asyncio

        payload = {"pcm_sha256": hashlib.sha256(pcm).hexdigest()[:16], "seconds": round(len(pcm) / 48000, 1)}
        failure = None
        if (replayed := self._replay("transcribe", payload, MUSE_VOICE)) is not None:
            return replayed
        if self._may_call(MUSE_VOICE):
            started = datetime.now(timezone.utc)
            try:
                text = asyncio.run(_stream_asr(pcm, setting("META_API_KEY"), setting("META_VOICE_MODEL")))
                if not text:
                    raise ValueError("empty transcript")
                self._keep("transcribe", payload, MUSE_VOICE, {"text": text})
                ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
                self.calls.append({"function": "transcribe", "source": "live", "ms": ms})
                return ModelResult({"text": text}, MUSE_VOICE, "live", latency_ms=ms)
            except Exception as error:  # noqa: BLE001 - reported, then the recording
                failure = f"transcribe live call failed ({type(error).__name__}: {str(error)[:120]})"
        cached = self._cached("transcribe", payload)
        if cached is not None:
            if failure and self.on_fallback:
                self.on_fallback(MUSE_VOICE, f"{failure}; used recorded transcript")
            return ModelResult(cached["output"], MUSE_VOICE, "cache", f"recorded {cached['recorded_at'][:16]}")
        if self.on_fallback:
            self.on_fallback(MUSE_VOICE, failure or "transcribe: no live model and no recorded transcript")
        return ModelResult(None, MUSE_VOICE, "fallback", failure or "no live model and no recorded transcript")

    def transcribe_file(self, path: Path) -> ModelResult:
        return self.transcribe(wav_to_pcm24k(path))

    # ----- 2. Local PII redaction (code) ------------------------------------

    def redact(self, text: str) -> ModelResult:
        """Redact personal data before any cloud call. Plain code on this machine."""
        redacted = re.sub(r"\+?\d[\d\s().-]{7,}\d", "[PHONE]", text)
        redacted = re.sub(r"(?i)\b(my name is|this is|soy|me llamo)\s+[A-ZÁÉÍÓÚÑ][\w'-]+(\s+[A-ZÁÉÍÓÚÑ][\w'-]+)?",
                          lambda m: f"{m.group(1)} [NAME]", redacted)
        redacted = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[EMAIL]", redacted)
        return ModelResult(redacted, LOCAL_REDACTION, "local", "rules on this machine; nothing leaves it")

    # ----- 3. Muse Spark: triage --------------------------------------------

    @property
    def TRIAGE_SYSTEM(self) -> str:
        return self._TRIAGE_SYSTEM.format(city=self.city, hazard=self.hazard)

    _TRIAGE_SYSTEM = (
        "You are the triage agent in a SIMULATED {city} {hazard} exercise. Score how urgent "
        "this incident is from 0 to 100 (100 = lives at immediate risk). Use only the facts given. "
        "vulnerable is true only if the facts mention a child, elderly, disabled or injured person "
        "or a medical need. reasons: at most 2 short reasons. Return JSON only." + GROUNDING_RULE
    )
    TRIAGE_SCHEMA = {
        "type": "object",
        "properties": {
            "severity_score": {"type": "integer"},
            "vulnerable": {"type": "boolean"},
            "summary": {"type": "string"},
            "reasons": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["severity_score", "vulnerable", "summary", "reasons"],
        "additionalProperties": False,
    }

    def triage(self, facts: dict[str, Any], fallback: Callable[[], dict[str, Any]]) -> ModelResult:
        payload = {**facts, "text": self.redact(facts.get("text", "")).value} if "text" in facts else facts
        return self._spark("triage", self.TRIAGE_SYSTEM, payload, self.TRIAGE_SCHEMA, fallback)

    def extract(self, source: str, text: str, system: str, schema: dict[str, Any]) -> ModelResult:
        """Read a raw report into structured fields (Muse Spark). Text is redacted first."""
        payload = {"source": source, "report": self.redact(text).value}
        return self._spark("extract", system, payload, schema, lambda: None)

    # ----- 4. SAM 3.1 and 5. Muse Wonder ------------------------------------

    def _image_call(self, function: str, model: str, image: Path, key_extra: dict[str, Any],
                    content_text: str, extra_body: dict[str, Any], parse) -> ModelResult:
        """An image + text request to /v1/responses, with the same record/replay as Spark."""
        digest = hashlib.sha256(image.read_bytes()).hexdigest()[:16]
        payload = {"image_sha256": digest, **key_extra}
        failure = None
        if (replayed := self._replay(function, payload, model)) is not None:
            return replayed
        if self._may_call(model):
            prefix = MODEL_SETTINGS[model]
            body = {
                "model": setting(f"{prefix}_MODEL"),
                "store": False,
                "input": [{"role": "user", "content": [
                    {"type": "input_image", "image_url": f"data:{_mime(image)};base64,"
                                                         f"{base64.b64encode(image.read_bytes()).decode()}"},
                    {"type": "input_text", "text": content_text},
                ]}],
                **extra_body,
            }
            started = datetime.now(timezone.utc)
            try:
                request = urllib.request.Request(
                    setting(f"{prefix}_URL") or setting("META_BASE_URL"), data=json.dumps(body).encode(),
                    method="POST", headers={"Authorization": f"Bearer {setting('META_API_KEY')}",
                                            "Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=max(self.timeout, 60)) as response:
                    data = json.loads(response.read())
                text = "".join(part.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                               for part in item.get("content") or [] if part.get("type") == "output_text")
                value = parse(text)
                if value is None:
                    raise ValueError("unreadable model output")
                self._keep(function, payload, model, value)
                ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
                self.calls.append({"function": function, "source": "live", "ms": ms})
                return ModelResult(value, model, "live", latency_ms=ms)
            except Exception as error:  # noqa: BLE001 - reported, then the recording
                failure = f"{function} live call failed ({type(error).__name__}: {str(error)[:120]})"
        cached = self._cached(function, payload)
        if cached is not None:
            if failure and self.on_fallback:
                self.on_fallback(model, f"{failure}; used recorded response")
            return ModelResult(cached["output"], model, "cache", f"recorded {cached['recorded_at'][:16]}")
        if self.on_fallback:
            self.on_fallback(model, failure or f"{function}: no live model and no recorded response")
        return ModelResult(None, model, "fallback", failure or "no live model and no recorded response")

    def segment(self, image: Path, prompts: list[str]) -> ModelResult:
        """SAM 3.1 boxes for each text prompt: {"size": [w, h], "objects": {prompt: [[x1, y1, x2, y2], ...]}}.

        SAM also returns a pixel mask per object; its encoding is not decoded yet, so only
        boxes are used (approximate regions).
        """
        results: dict[str, list[list[int]]] = {}
        size, sources = None, set()
        for prompt in prompts:
            result = self._image_call("segment", SAM, image, {"prompt": prompt}, prompt, {}, _sam_boxes)
            if result.value is None:
                return result
            results[prompt] = result.value["boxes"]
            size = size or result.value["size"]
            sources.add(result.source)
        source = "live" if sources == {"live"} else "cache" if "live" not in sources else "live"
        return ModelResult({"size": size or [0, 0], "objects": results}, SAM, source,
                           "boxes only; mask encoding not decoded")

    WATER_QUESTION = (
        "You are the vision agent in a flood-response exercise. This is an overhead image of a city street "
        "area. Report what you can see on the roads: whether there is floodwater on them, the water depth on "
        "the road in inches (0 if dry), whether an ordinary passenger car could safely drive through, and "
        "whether there is a fire. Give your confidence from 0 to 1 and one short reason based on visible cues. "
        "Describe only what is visible; if something cannot be seen, say so."
    )
    WATER_SCHEMA = {
        "type": "object",
        "properties": {
            "water_on_road": {"type": "boolean"},
            "depth_in_low": {"type": "integer"},
            "depth_in_high": {"type": "integer"},
            "passable_for_cars": {"type": "boolean"},
            "fire_visible": {"type": "boolean"},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["water_on_road", "depth_in_low", "depth_in_high", "passable_for_cars", "fire_visible",
                     "confidence", "reason"],
        "additionalProperties": False,
    }

    def assess_water(self, image: Path) -> ModelResult:
        """Muse Wonder: water on the road, depth range, passability, fire, confidence, reason."""
        return self._image_call(
            "assess_water", MUSE_WONDER, image, {"question": self.WATER_QUESTION}, self.WATER_QUESTION,
            {"reasoning": {"effort": "low"},
             "text": {"format": {"type": "json_schema", "name": "vision", "schema": self.WATER_SCHEMA, "strict": True}}},
            parse_json_object,
        )

    # ----- 6. Plan search (local retrieval, code) ---------------------------

    def guidance(self, query: str, limit: int = 2) -> ModelResult:
        """Passages from the local knowledge base that match the query, with citations.

        Retrieval runs locally (keyword overlap), so it keeps working without internet. The
        knowledge base is a sample written for this exercise, and citations name it as such.
        """
        words = {w for w in re.findall(r"[a-z]+", query.lower()) if len(w) > 3}
        scored = []
        for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
            title, section = path.stem, None
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                elif line.startswith("## "):
                    section = line[3:].strip()
                elif line.strip() and section:
                    overlap = len(words & set(re.findall(r"[a-z]+", line.lower())))
                    if overlap:
                        scored.append((overlap, {"text": line.strip(), "citation": f"{title}, {section}"}))
        # A passage must share at least two words with the query; otherwise the plan has no answer.
        passages = [item for overlap, item in sorted(scored, key=lambda pair: -pair[0])[:limit] if overlap >= 2]
        return ModelResult(passages, PLAN_SEARCH, "local",
                           "keyword search over the sample knowledge base" if passages else not_in_plan(self.city_short))

    # ----- 7. Muse Spark: recommendation and public alert -------------------

    @property
    def RECOMMEND_SYSTEM(self) -> str:
        return self._RECOMMEND_SYSTEM.format(city=self.city_short, hazard=self.hazard)

    _RECOMMEND_SYSTEM = (
        "You are the command agent for {city} {hazard} emergency operations. The dispatch decision "
        "was made by an exact solver and is given; do not change it. Write why: 2-3 short bullets "
        "explaining it, each ending with its source in brackets (an agent name or a citation from "
        "the facts). Write tradeoff: one sentence on what this delays or costs, from the facts. "
        "Use only the facts given. Return JSON only." + GROUNDING_RULE
    )
    RECOMMEND_SCHEMA = {
        "type": "object",
        "properties": {"why": {"type": "array", "items": {"type": "string"}}, "tradeoff": {"type": "string"}},
        "required": ["why", "tradeoff"],
        "additionalProperties": False,
    }

    def recommend(self, facts: dict[str, Any], fallback: Callable[[], dict[str, Any]]) -> ModelResult:
        return self._spark("recommend", self.RECOMMEND_SYSTEM, facts, self.RECOMMEND_SCHEMA, fallback)

    @property
    def ALERT_SYSTEM(self) -> str:
        return self._ALERT_SYSTEM.format(city=self.city, hazard=self.hazard)

    _ALERT_SYSTEM = (
        "Write a public {hazard} alert for residents of {city}, from the "
        "approved actions given. Two versions: English and Spanish, each at most 3 short sentences "
        "at a 6th-grade reading level: what is happening, where to avoid, what to do. Do not invent "
        "places, numbers or shelters beyond the facts. This text is for the public: no citations, "
        "brackets or field names. Never invent a shelter, capacity, road status, evacuation zone or number; "
        "leave out anything not in the facts. Return JSON only."
    )
    ALERT_SCHEMA = {
        "type": "object",
        "properties": {"english": {"type": "string"}, "spanish": {"type": "string"}},
        "required": ["english", "spanish"],
        "additionalProperties": False,
    }

    def public_alert(self, facts: dict[str, Any], fallback: Callable[[], dict[str, Any]]) -> ModelResult:
        return self._spark("public_alert", self.ALERT_SYSTEM, facts, self.ALERT_SCHEMA, fallback)

    # ----- 8. Muse Image ----------------------------------------------------

    def alert_graphic(self, facts: dict[str, Any]) -> ModelResult:
        """Alert map card. Not connected: none is generated (the UI says so)."""
        return ModelResult(None, MUSE_IMAGE, "sample", NOT_CONNECTED[MUSE_IMAGE])


def hub_from_env(client: ModelClient | None = None) -> MetaHub:
    mode = (setting("META_MODE") or "live").lower()
    return MetaHub(client or model_client_from_env(), mode="demo" if mode == "demo" else "live")
