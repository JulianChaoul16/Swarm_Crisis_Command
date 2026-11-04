"""Generate AI test images (Muse Image) of a street at different flood depths and a fire.

These are synthetic test inputs for the Vision models, not real photos. The manifest records
what each prompt asked for (intended depth, passability); the generated image may differ.
Existing images are kept; run again to fill in any that failed.

    python tools/make_test_images.py
"""

import base64
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crisis_command.agents.model_client import _env_file_values  # noqa: E402

OUT = ROOT / "test_media" / "images"
STYLE = ("Top-down satellite photograph, daytime, realistic, of a downtown riverside street block in an "
         "older US East Coast city with brick warehouses.")
# name, kind, intended depth range (inches), passable for ordinary cars, scene
SCENES = [
    ("01_dry_street", "dry", 0, 0, True, "The road is completely dry, normal traffic, no water anywhere."),
    ("02_flood_shallow", "flood", 2, 6, True,
     "A thin sheet of shallow floodwater, a few inches deep, covers the road; cars are still driving through "
     "it and lane markings are visible through the water."),
    ("03_flood_knee", "flood", 18, 24, False,
     "Brown floodwater about knee deep covers the whole street; parked cars have water up to their wheel "
     "hubs; lane markings are no longer visible."),
    ("04_flood_deep", "flood", 42, 54, False,
     "Brown floodwater about chest deep fills the street; parked cars are submerged up to their windows and "
     "an empty city bus is stuck in the water."),
    ("05_flood_severe", "flood", 96, 144, False,
     "The whole riverfront block is inundated by the river; only the roofs of parked cars and the tops of "
     "first-floor windows show above the brown water."),
    ("06_building_fire", "fire", 0, 0, True,
     "Firefighters are responding at one warehouse: grey smoke rises from its roof, and two red fire "
     "engines and a ladder truck are parked on the dry street with hoses laid out."),
]


def main() -> None:
    env = _env_file_values()
    key, url, model = env["META_API_KEY"], env["META_IMAGE_URL"], env["META_IMAGE_MODEL"]
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"images": []}
    done = {item["name"]: item for item in manifest["images"]}
    for name, kind, low, high, passable, scene in SCENES:
        if name in done and (OUT / done[name]["file"]).exists():
            print(f"{name}: already generated")
            continue
        prompt = f"{STYLE} {scene}"
        body = {"model": model, "prompt": prompt, "n": 1, "size": "1024x1024"}
        request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as error:
            print(f"{name}: HTTP {error.code} {error.read()[:300].decode(errors='replace').replace(key, '<key>')}")
            continue
        extension = data.get("output_format", "png")
        path = OUT / f"{name}.{extension}"
        path.write_bytes(base64.b64decode(data["data"][0]["b64_json"]))
        done[name] = {"name": name, "file": path.name, "kind": kind, "intended_depth_in": [low, high],
                      "intended_passable_for_cars": passable, "prompt": prompt, "model": model,
                      "seconds": round(time.time() - started, 1)}
        print(f"{name}: saved {path.name} ({path.stat().st_size // 1024} KB, {done[name]['seconds']}s)")
    manifest = {
        "note": ("AI-GENERATED TEST IMAGES (Muse Image), not real photos or real satellite imagery. intended_* "
                 "fields are what each prompt asked for; the generated image may not match exactly. For "
                 "testing the Vision models only."),
        "images": [done[name] for name, *_ in SCENES if name in done],
    }
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
