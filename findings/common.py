"""Shared loading, map handling and scoring rules for the findings test.

Everything here follows cases/rubric.md; the oracle, the arms and the scorer all use it.
"""

import json
from math import hypot
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases"
ANSWERS = CASES / "answers"
RESULTS = ROOT / "results"

SEVERITY_WEIGHT = {"critical": 8, "high": 4, "medium": 2, "low": 1}
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
UNFILLED_MINUTES = 100


def load(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_town() -> dict:
    return load(CASES / "town.json")


def coordinates(town: dict) -> dict[str, tuple[float, float]]:
    return {place["id"]: (place["x"], place["y"]) for place in town["places"]}


def straight_line(town: dict, a: str, b: str) -> float:
    coords = coordinates(town)
    return hypot(coords[a][0] - coords[b][0], coords[a][1] - coords[b][1])


def open_adjacency(town: dict, closed) -> dict[str, list[tuple[str, float, str]]]:
    """Neighbours over open roads: place -> [(neighbour, length_km, road_id)]."""
    closed = set(closed)
    adjacency = {place["id"]: [] for place in town["places"]}
    for road in town["roads"]:
        if road["id"] in closed:
            continue
        adjacency[road["a"]].append((road["b"], road["length"], road["id"]))
        adjacency[road["b"]].append((road["a"], road["length"], road["id"]))
    return adjacency


def path_length(town: dict, closed, path) -> float | None:
    """Length of a submitted path, or None if it uses a missing or closed road."""
    adjacency = open_adjacency(town, closed)
    total = 0.0
    for left, right in zip(path, path[1:]):
        lengths = [length for neighbour, length, _ in adjacency.get(left, []) if neighbour == right]
        if not lengths:
            return None
        total += min(lengths)
    return total


def slots(scenario: dict) -> list[dict]:
    incidents = {incident["id"]: incident for incident in scenario["incidents"]}
    return [
        {
            "slot": f"{incident['id']}#{index}",
            "incident": incident["id"],
            "need": need,
            "severity": incidents[incident["id"]]["severity"],
            "place": incident["place"],
        }
        for incident in scenario["incidents"]
        for index, need in enumerate(incident["needs"])
    ]


def triage_key(incident: dict) -> tuple:
    return (
        -SEVERITY_RANK[incident["severity"]],
        -incident["people"],
        incident["reported_minute"],
        incident["id"],
    )


def evaluate_plan(scenario: dict, plan, distances: dict) -> dict:
    """Cost a plan under the rubric. Invalid entries are reported and treated as unfilled.

    `distances` maps (from_place, to_place) to km, or None when unreachable.
    """
    units = {unit["id"]: unit for unit in scenario["units"]}
    slot_table = {slot["slot"]: slot for slot in slots(scenario)}
    violations: list[str] = []
    filled: dict[str, float] = {}
    used: set[str] = set()
    for entry in plan or []:
        slot_id, unit_id = entry.get("slot"), entry.get("unit")
        slot, unit = slot_table.get(slot_id), units.get(unit_id)
        if slot is None or unit is None:
            violations.append(f"unknown slot or unit: {slot_id} / {unit_id}")
            continue
        if slot_id in filled:
            violations.append(f"slot {slot_id} filled twice")
            continue
        if unit_id in used:
            violations.append(f"unit {unit_id} used twice")
            continue
        if slot["need"] not in unit["capabilities"]:
            violations.append(f"unit {unit_id} lacks {slot['need']} for {slot_id}")
            continue
        distance = distances.get((unit["location"], slot["place"]))
        if distance is None:
            violations.append(f"unit {unit_id} cannot reach {slot_id} over open roads")
            continue
        used.add(unit_id)
        filled[slot_id] = SEVERITY_WEIGHT[slot["severity"]] * distance / unit["speed"]
    cost = sum(filled.values()) + sum(
        SEVERITY_WEIGHT[slot["severity"]] * UNFILLED_MINUTES
        for slot_id, slot in slot_table.items()
        if slot_id not in filled
    )
    return {"cost": cost, "filled": len(filled), "slots": len(slot_table), "violations": violations}


def churn(prior, plan) -> int:
    """Slots whose unit changed between two plans, counting filled <-> unfilled."""
    before = {entry["slot"]: entry["unit"] for entry in prior or []}
    after = {entry["slot"]: entry["unit"] for entry in plan or []}
    return sum(before.get(slot) != after.get(slot) for slot in before.keys() | after.keys())
