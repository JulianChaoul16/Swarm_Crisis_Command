"""Listener Agent eval: Muse Voice Transcribe on the six synthesized test calls.

Each clip in `test_media/audio` is converted to 24 kHz 16-bit mono PCM, streamed to the
realtime ASR WebSocket, and the last cumulative transcript (once all audio is processed) is
compared with the exact script in `scripts.json`. Word error rate (WER) uses lowercased
words with punctuation removed. Two numbers are reported: raw, and with number formatting
normalized (the scripts spell "nine one one" and "fourteenth"; the model writes "911" and
"14th"), so formatting is not counted as a recognition error.

Audio is streamed at real-time pace (one 0.2 s chunk every 0.2 s), as a live call would be.

    python -m evaluation.voice_eval
"""

import asyncio
import json
from pathlib import Path
import re
import time
import wave

import numpy as np
from scipy.signal import resample_poly
import websockets

from crisis_command.agents.model_client import _env_file_values

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / "test_media" / "audio"
OUT = ROOT / "evaluation" / "voice_results.json"
URI = "wss://api.meta.ai/v1/asr/realtime"


def pcm_24k(path: Path) -> tuple[bytes, float]:
    with wave.open(str(path)) as clip:
        rate = clip.getframerate()
        samples = np.frombuffer(clip.readframes(clip.getnframes()), dtype=np.int16).astype(np.float32)
    from math import gcd
    g = gcd(24000, rate)
    resampled = resample_poly(samples, 24000 // g, rate // g)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes(), len(samples) / rate


async def transcribe(pcm: bytes, seconds: float, key: str, model: str) -> tuple[str, float]:
    started = time.time()
    async with websockets.connect(URI, open_timeout=30) as ws:
        await ws.send(json.dumps({"authorization": {"accessToken": f"Bearer {key}"}, "audioEncoding": "PCM_24KHZ",
                                  "model": model, "mode": "PUSH_TO_TALK", "partialMode": "CUMULATIVE",
                                  "emitAudioProgress": False}))
        await ws.recv()  # handshake: {"sessionId": ...}
        chunk = 24000 * 2 // 5  # 0.2 s of audio
        for i in range(0, len(pcm), chunk):
            await ws.send(pcm[i:i + chunk])
            await asyncio.sleep(0.2)
        transcript = ""
        try:
            while True:
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=6))
                if message.get("type") == "transcript":
                    transcript = message["transcript"]
                    if message.get("final"):
                        break
        except asyncio.TimeoutError:
            pass  # no final flag in push-to-talk without an end signal: keep the last cumulative text
    return transcript, time.time() - started


NUMBERS = {
    "nine one one": "911", "9 1 1": "911", "fourteenth": "14th", "eighteenth": "18th", "seventh": "7th",
    "seventy eight": "78", "one": "1", "two": "2", "four": "4", "five": "5",
}


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def normalized(text: str) -> str:
    """Spell numbers one way on both sides; nothing else changes."""
    out = " ".join(words(text.replace("-", " ")))
    for spoken, written in sorted(NUMBERS.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(r"\b" + re.escape(spoken) + r"\b", written, out)
    return out


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = words(reference), words(hypothesis)
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        previous, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (r != h))
    return row[-1] / max(1, len(ref))


async def main() -> None:
    env = _env_file_values()
    key, model = env["META_API_KEY"], env["META_VOICE_MODEL"]
    clips = json.loads((AUDIO / "scripts.json").read_text(encoding="utf-8"))["clips"]
    rows = []
    for clip in clips:
        pcm, seconds = pcm_24k(AUDIO / clip["file"])
        transcript, took = await transcribe(pcm, seconds, key, model)
        reference = " ".join(turn["text"] for turn in clip["script"])
        rows.append({"file": clip["file"], "audio_s": round(seconds, 1), "took_s": round(took, 1),
                     "wer": round(wer(reference, transcript), 3),
                     "wer_numbers_normalized": round(wer(normalized(reference), normalized(transcript)), 3),
                     "transcript": transcript, "reference": reference})
        print(f"{clip['file']:28s} WER {rows[-1]['wer']:.1%}  normalized {rows[-1]['wer_numbers_normalized']:.1%}"
              f"  ({seconds:.0f}s audio, {took:.0f}s)")
    total_ref = sum(len(words(r["reference"])) for r in rows)
    pooled = sum(r["wer"] * len(words(r["reference"])) for r in rows) / total_ref
    pooled_norm = sum(r["wer_numbers_normalized"] * len(words(r["reference"])) for r in rows) / total_ref
    OUT.write_text(json.dumps({"model": model, "pooled_wer": round(pooled, 3),
                               "pooled_wer_numbers_normalized": round(pooled_norm, 3), "clips": rows,
                               "note": "Synthesized voices (Windows TTS) reading fictional scripts."},
                              indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"pooled WER {pooled:.1%}, numbers normalized {pooled_norm:.1%}, over {total_ref} words")


if __name__ == "__main__":
    asyncio.run(main())
