"""Vision Agent eval: Muse Wonder on the six AI-generated test images.

Each image in `test_media/images` is sent with the same question (no hint of the intended
scene) and a strict JSON schema. Answers are scored against what each image's prompt asked
for (`manifest.json`). Caveat: the images are AI-generated, so "intended" is the prompt, not
measured ground truth.

    python -m evaluation.vision_eval [effort]      # effort: low (default) | medium | high
"""

import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

from crisis_command.agents.model_client import _env_file_values, parse_json_object

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "test_media" / "images"
OUT = ROOT / "evaluation" / "vision_results.json"

QUESTION = (
    "You are the vision agent in a flood-response exercise. This is an overhead image of a city "
    "street area. Report what you can see on the roads: whether there is floodwater on them, the "
    "water depth on the road in inches (0 if dry), whether an ordinary passenger car could safely "
    "drive through, and whether there is a fire. Give your confidence from 0 to 1 and one short "
    "reason based on visible cues (e.g. how high water is on parked cars)."
)
SCHEMA = {
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


def ask(image_path: Path, env: dict, effort: str) -> tuple[dict | None, float, str | None]:
    image = base64.b64encode(image_path.read_bytes()).decode()
    mime = "image/webp" if image_path.suffix == ".webp" else "image/png"
    body = {
        "model": env["META_WONDER_MODEL"],
        "store": False,
        "reasoning": {"effort": effort},
        "text": {"format": {"type": "json_schema", "name": "vision", "schema": SCHEMA, "strict": True}},
        "input": [{"role": "user", "content": [
            {"type": "input_image", "image_url": f"data:{mime};base64,{image}"},
            {"type": "input_text", "text": QUESTION},
        ]}],
    }
    request = urllib.request.Request(env["META_WONDER_URL"], data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {env['META_API_KEY']}",
                                              "Content-Type": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        return None, time.time() - started, f"HTTP {error.code}: {error.read()[:200].decode(errors='replace')}"
    text = "".join(part.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                   for part in item.get("content") or [] if part.get("type") == "output_text")
    return parse_json_object(text), time.time() - started, None if text else "no text output"


def main() -> None:
    effort = sys.argv[1] if len(sys.argv) > 1 else "low"
    env = _env_file_values()
    manifest = json.loads((IMAGES / "manifest.json").read_text(encoding="utf-8"))["images"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        answers = list(pool.map(lambda item: ask(IMAGES / item["file"], env, effort), manifest))
    rows = []
    for item, (answer, took, error) in zip(manifest, answers):
        low, high = item["intended_depth_in"]
        row = {"file": item["file"], "intended_depth_in": [low, high], "intended_passable": item["intended_passable_for_cars"],
               "intended_fire": item["kind"] == "fire", "seconds": round(took, 1), "error": error, "answer": answer}
        if answer:
            got_low, got_high = answer["depth_in_low"], answer["depth_in_high"]
            row["depth_overlaps_intended"] = got_low <= high and got_high >= low
            row["passable_correct"] = answer["passable_for_cars"] == item["intended_passable_for_cars"]
            row["fire_correct"] = answer["fire_visible"] == (item["kind"] == "fire")
            row["water_correct"] = answer["water_on_road"] == (item["kind"] == "flood")
        rows.append(row)
        verdict = (f"depth {answer['depth_in_low']}-{answer['depth_in_high']} in (intended {low}-{high}) "
                   f"{'OK' if row['depth_overlaps_intended'] else 'MISS'} | passable {answer['passable_for_cars']} "
                   f"{'OK' if row['passable_correct'] else 'MISS'} | fire {answer['fire_visible']} "
                   f"{'OK' if row['fire_correct'] else 'MISS'} | conf {answer['confidence']}") if answer else error
        print(f"{item['file']:22s} {took:5.1f}s  {verdict}")
    scored = [r for r in rows if r["answer"]]
    summary = {key: f"{sum(r[key] for r in scored)}/{len(scored)}" for key in
               ("water_correct", "depth_overlaps_intended", "passable_correct", "fire_correct")}
    OUT.write_text(json.dumps({"model": env["META_WONDER_MODEL"], "reasoning_effort": effort, "summary": summary,
                               "note": "Images are AI-generated (Muse Image); 'intended' values are what each prompt asked for.",
                               "rows": rows}, indent=1), encoding="utf-8")
    print("summary:", summary)


if __name__ == "__main__":
    main()
