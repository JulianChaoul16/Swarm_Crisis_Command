"""Build the Richmond display geometry for the flood scenario.

Each scenario place is pinned to a real Richmond location, and each scenario road is
drawn along real streets using the public OSRM router (OpenStreetMap data). The output
is a static JSON file, so the app never calls OSRM at runtime.

The planner still works on the scenario's simplified graph; this file only changes how
that graph is drawn. Run from the repo root:

    python tools/build_richmond_geo.py
"""

import json
from pathlib import Path
import sys
from time import sleep
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crisis_command.operations.scenario import FLOOD_PLACES, FLOOD_ROADS  # noqa: E402

OUT = ROOT / "crisis_command" / "api" / "richmond_geo.json"
OSRM = "https://router.project-osrm.org"
USER_AGENT = "crisis-command-hackathon-demo/0.1"

# Real anchors (lng, lat). Labels in the scenario are kept; these say where they sit.
ANCHORS: dict[str, tuple[float, float]] = {
    "West End": (-77.4502, 37.5469),             # Monroe Park, Belvidere & Franklin
    "Upriver North": (-77.4488, 37.5360),        # north end of the Lee Bridge
    "Upriver South": (-77.4527, 37.5254),        # south end of the Lee Bridge
    "Fire Station North": (-77.4385, 37.5480),   # Jackson Ward
    "Broad & 3rd": (-77.4415, 37.5445),          # E Broad St & N 3rd St
    "Hospital": (-77.4301, 37.5403),             # VCU Medical Center
    "Downtown Junction": (-77.4331, 37.5318),    # 14th & Main, north end of the Mayo Bridge
    "High School Shelter": (-77.4194, 37.5427),  # school shelter, Church Hill
    "Shockoe Bottom": (-77.4290, 37.5338),       # Main Street Station / 17th St
    "East Junction": (-77.4150, 37.5170),        # Rocketts Landing
    "Manchester": (-77.4450, 37.5200),           # Hull St, Manchester
    "South Junction": (-77.4355, 37.5255),       # south end of the Mayo Bridge, Hull St
    "South Station": (-77.4335, 37.5150),        # Commerce Rd
    "Riverside Apartments": (-77.4265, 37.5205), # south bank, east of the Mayo Bridge
}

# Optional via points that keep a road on the street it is named after.
VIA: dict[str, list[tuple[float, float]]] = {
    "main-street-bridge": [(-77.4345, 37.5280)],  # Mayo Bridge
}


def get(url: str) -> dict:
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=30) as response:
        return json.load(response)


def snap(point: tuple[float, float]) -> list[float]:
    data = get(f"{OSRM}/nearest/v1/driving/{point[0]},{point[1]}")
    return [round(value, 6) for value in data["waypoints"][0]["location"]]


def route(points: list[list[float]] | list[tuple[float, float]]) -> tuple[list[list[float]], float]:
    path = ";".join(f"{lng},{lat}" for lng, lat in points)
    data = get(f"{OSRM}/route/v1/driving/{path}?overview=full&geometries=geojson")
    best = data["routes"][0]
    coordinates = [[round(lng, 6), round(lat, 6)] for lng, lat in best["geometry"]["coordinates"]]
    return coordinates, best["distance"]


def main() -> None:
    missing = set(FLOOD_PLACES) - set(ANCHORS)
    if missing:
        raise SystemExit(f"No anchor for: {sorted(missing)}")
    places = {}
    for name, anchor in ANCHORS.items():
        places[name] = snap(anchor)
        sleep(0.3)
    roads = {}
    for road_id, start, end, _closed in FLOOD_ROADS:
        line, metres = route([places[start], *VIA.get(road_id, []), places[end]])
        # Pin both ends exactly on the place markers.
        roads[road_id] = [places[start], *line, places[end]]
        print(f"{road_id:22s} {metres / 1000:5.2f} km  {len(line)} points")
        sleep(0.3)
    OUT.write_text(json.dumps({
        "source": "Street geometry: OpenStreetMap contributors via OSRM (ODbL).",
        "places": places,
        "roads": roads,
        "labels": {"James River": [-77.4420, 37.5300]},
    }, indent=1), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
