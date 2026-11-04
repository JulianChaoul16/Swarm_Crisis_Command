"""Meta model hub: every model call in Crisis Command Swarm lives in this file.

Each function returns a `ModelResult`: the value, the model it is attributed to, and where
it actually came from:

- "live": the model was called just now.
- "cache": a response recorded from an earlier live call of the same model with the same
  input (`demo_cache/responses/`). Demo mode uses only these, so it works offline and every
  cached answer is a real one.
- "local": code that runs on this machine (PII redaction rules, plan retrieval).
- "sample": hand-made data for a model this account is not connected to (e.g. SAM 3.1).
- "fallback": rules used because the model call failed and nothing was cached.

The UI shows that source next to the model pill, so nothing is presented as a model output
unless a model produced it. Configuration: META_API_KEY / META_MODEL / META_BASE_URL (see
`agents/model_client.py`); META_MODE=demo replays the cache only.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable

from .agents.model_client import ModelClient, UnavailableModelClient, _env_file_values, model_client_from_env

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "demo_cache" / "responses"
KNOWLEDGE_DIR = ROOT / "demo_cache" / "knowledge"
SAMPLE_DIR = ROOT / "demo_cache" / "samples"

# Display names, and whether this account can call each model. Only Muse Spark is wired to
# a live endpoint; the others are named for the architecture and fall back as labelled.
MUSE_SPARK = "Muse Spark"
MUSE_SPARK_OPEN = "Muse Spark Open"
MUSE_VOICE = "Muse Voice Transcribe"
SAM = "SAM 3.1"
MUSE_WONDER = "Muse Wonder"
MUSE_GLIMMER = "Muse Glimmer"
MUSE_IMAGE = "Muse Image"

NOT_CONNECTED = {
    MUSE_SPARK_OPEN: "open weights not installed; local redaction rules run instead",
    MUSE_VOICE: "no transcription endpoint configured; recorded sample transcripts are used",
    SAM: "no segmentation endpoint configured; sample masks are used",
    MUSE_WONDER: "no multimodal endpoint configured; sample estimates are used",
    MUSE_GLIMMER: "open weights not installed; local retrieval over the sample knowledge base runs instead",
    MUSE_IMAGE: "no image endpoint configured; no alert graphic is generated",
}


# .env settings naming each model's ID (and endpoint, if not META_BASE_URL).
MODEL_SETTINGS = {
    MUSE_VOICE: "META_VOICE",
    SAM: "META_SAM",
    MUSE_WONDER: "META_WONDER",
    MUSE_GLIMMER: "META_GLIMMER",
    MUSE_SPARK_OPEN: "META_SPARK_OPEN",
    MUSE_IMAGE: "META_IMAGE",
}


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

    @property
    def spark_live(self) -> bool:
        return self.mode == "live" and not isinstance(self.client, UnavailableModelClient)

    def status(self) -> list[dict[str, str]]:
        spark = ("live" if self.spark_live else "cache (demo mode)" if self.mode == "demo"
                 else "not connected")
        rows = [{"model": MUSE_SPARK, "status": spark,
                 "detail": f"{self.client.model_id} via Meta Responses API" if self.spark_live else ""}]
        for model, detail in NOT_CONNECTED.items():
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
        if self.spark_live:
            try:
                output = self.client.complete_json(system=system, user=json.dumps(payload, default=str),
                                                   schema=schema, timeout=self.timeout)
                self._record(function, payload, MUSE_SPARK, output)
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

    def transcribe(self, audio_id: str) -> ModelResult:
        """Transcript of a 911/radio clip. Not connected: returns the recorded sample transcript."""
        path = SAMPLE_DIR / "transcripts" / f"{audio_id}.json"
        if path.exists():
            return ModelResult(json.loads(path.read_text(encoding="utf-8")), MUSE_VOICE, "sample",
                               NOT_CONNECTED[MUSE_VOICE])
        return ModelResult(None, MUSE_VOICE, "sample", f"no sample transcript for {audio_id}")

    # ----- 2. Muse Spark Open (local PII redaction) -------------------------

    def redact(self, text: str) -> ModelResult:
        """Redact personal data before any cloud call. Runs locally (rules stand in for Spark Open)."""
        redacted = re.sub(r"\+?\d[\d\s().-]{7,}\d", "[PHONE]", text)
        redacted = re.sub(r"(?i)\b(my name is|this is|soy|me llamo)\s+[A-ZÁÉÍÓÚÑ][\w'-]+(\s+[A-ZÁÉÍÓÚÑ][\w'-]+)?",
                          lambda m: f"{m.group(1)} [NAME]", redacted)
        redacted = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[EMAIL]", redacted)
        return ModelResult(redacted, MUSE_SPARK_OPEN, "local", NOT_CONNECTED[MUSE_SPARK_OPEN])

    # ----- 3. Muse Spark: triage --------------------------------------------

    TRIAGE_SYSTEM = (
        "You are the triage agent in a SIMULATED Richmond, VA flood exercise. Score how urgent "
        "this incident is from 0 to 100 (100 = lives at immediate risk). Use only the facts given. "
        "vulnerable is true only if the facts mention a child, elderly, disabled or injured person "
        "or a medical need. reasons: at most 2 short reasons. Return JSON only."
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

    def segment(self, image_id: str) -> ModelResult:
        """Flood-water mask for a photo, as GeoJSON. Not connected: the sample mask."""
        path = SAMPLE_DIR / "masks" / f"{image_id}.geojson"
        if path.exists():
            return ModelResult(json.loads(path.read_text(encoding="utf-8")), SAM, "sample", NOT_CONNECTED[SAM])
        return ModelResult(None, SAM, "sample", f"no sample mask for {image_id}")

    def assess_water(self, image_id: str) -> ModelResult:
        """Depth and passability estimate for a photo. Not connected: the sample estimate."""
        path = SAMPLE_DIR / "estimates" / f"{image_id}.json"
        if path.exists():
            return ModelResult(json.loads(path.read_text(encoding="utf-8")), MUSE_WONDER, "sample",
                               NOT_CONNECTED[MUSE_WONDER])
        return ModelResult(None, MUSE_WONDER, "sample", f"no sample estimate for {image_id}")

    # ----- 6. Muse Glimmer (local retrieval) --------------------------------

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
        passages = [item for _, item in sorted(scored, key=lambda pair: -pair[0])[:limit]]
        return ModelResult(passages, MUSE_GLIMMER, "local", NOT_CONNECTED[MUSE_GLIMMER])

    # ----- 7. Muse Spark: recommendation and public alert -------------------

    RECOMMEND_SYSTEM = (
        "You are the command agent in a SIMULATED Richmond flood exercise. The dispatch decision "
        "was made by an exact solver and is given; do not change it. Write why: 2-3 short bullets "
        "explaining it, each ending with its source in brackets (an agent name or a citation from "
        "the facts). Write tradeoff: one sentence on what this delays or costs, from the facts. "
        "Use only the facts given. Return JSON only."
    )
    RECOMMEND_SCHEMA = {
        "type": "object",
        "properties": {"why": {"type": "array", "items": {"type": "string"}}, "tradeoff": {"type": "string"}},
        "required": ["why", "tradeoff"],
        "additionalProperties": False,
    }

    def recommend(self, facts: dict[str, Any], fallback: Callable[[], dict[str, Any]]) -> ModelResult:
        return self._spark("recommend", self.RECOMMEND_SYSTEM, facts, self.RECOMMEND_SCHEMA, fallback)

    ALERT_SYSTEM = (
        "Write a public flood alert for residents of Richmond, VA in a SIMULATED exercise, from the "
        "approved actions given. Two versions: English and Spanish, each at most 3 short sentences "
        "at a 6th-grade reading level: what is happening, where to avoid, what to do. Do not invent "
        "places, numbers or shelters beyond the facts. Return JSON only."
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
