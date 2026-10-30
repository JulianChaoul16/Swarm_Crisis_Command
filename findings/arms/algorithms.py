"""Algorithm arm: rule-based text handling plus classic optimization, run on every case.

    python -m findings.arms.algorithms

Variants per task:
  reports, conflicts   rules           keyword/regex rules over the rubric and the gazetteer
  routing              fewest_hops     breadth-first search that ignores road length
                       dijkstra        exact shortest path
                       astar           exact shortest path guided by straight-line distance
  triage               rule            the rubric's sort order
  assignment           greedy_priority incidents in triage order, nearest capable unit each
                       greedy_global   cheapest remaining slot-unit pair first
                       hungarian       optimal slot-unit matching (scipy linear_sum_assignment)
  replanning           the three assignment variants, plus hungarian_sticky which prefers
                       keeping the prior plan's units when costs are close
"""

from collections import deque
from datetime import datetime, timezone
import heapq
from math import hypot, inf
import re
from time import perf_counter

import numpy as np
from scipy.optimize import linear_sum_assignment

from findings.common import (
    CASES,
    RESULTS,
    SEVERITY_WEIGHT,
    UNFILLED_MINUTES,
    coordinates,
    load,
    load_town,
    open_adjacency,
    save,
    slots,
    triage_key,
)

# --- Reports ---------------------------------------------------------------------------

NUMBER_WORDS = {
    "a dozen": 12, "dozen": 12, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "dos": 2, "tres": 3,
}
NUMBER = r"\d+|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
PEOPLE_COUNT = re.compile(
    rf"\b({NUMBER})\s*\+?\s*(?:[a-z]+\s+)?"
    r"(people|persons|kids|children|adults|teachers|guys|men|women|residents|students|"
    r"patients|of us|personas|niños)\b"
)
ONE_PERSON = re.compile(
    r"\b(old man|old woman|man|woman|driver|mother|madre|mom|dad|father|padre|mrs|mr|"
    r"grandma|grandpa|child|boy|girl|baby)\b"
)
NEGATED_INJURY = re.compile(r"\b(no one|nobody|not|no)\s+(?:\w+\s+)?(hurt|injured)\b")
VEHICLE_PHRASES = re.compile(r"\bfire (truck|engine|station|dept|department)\b|\bfirehouse\b")

FIRE = re.compile(r"\b(smoke|fire|flames?|burning)\b")
TRAPPED = re.compile(
    r"\b(trapped|stuck|atrapad[oa]s?|on the roof|holding onto|flipped|drowning|swept)\b|"
    r"\bcan'?t get out\b"
)
WATER = re.compile(r"\b(water|flood\w*|river|current|agua|rising)\b")
MEDICAL = re.compile(
    r"\b(hurt|injur\w*|bleeding|broke\w*|fell|hypothermia|diabetic|unconscious|"
    r"not breathing|chest pain|herid[oa]s?)\b"
)
EVACUATE = re.compile(r"\b(get out|evacuat\w*|need to leave)\b")
PROPERTY = re.compile(r"\b(basement|damage|damaged)\b")
ROAD_WORD = re.compile(r"\b(bridge|road|rd|crossing)\b")
CLOSED_WORDS = re.compile(
    r"\b(closed|closure|blocked|blocking|out|washed out)\b|\bunder\b.{0,25}\bwater\b|"
    r"\bwater over\b"
)
OPEN_WORDS = re.compile(r"\b(open|opened|reopened|passable|clear|fine|crossed)\b")
HEDGE = re.compile(r"\b(i think|maybe|may|might|probably|not sure)\b|\?")
CLAUSE_SPLIT = re.compile(r"[.,;!–—]|\s-\s|\bbut\b")


def _alias_matches(text: str, entries: list[dict]) -> list[tuple[int, int, str]]:
    """(position, -length, id) for every alias occurrence, earliest and longest first."""
    found = []
    for entry in entries:
        for alias in entry["aliases"]:
            for match in re.finditer(rf"\b{re.escape(alias)}\b", text):
                found.append((match.start(), -len(alias), entry["id"]))
    return sorted(found)


def _road_status(clause: str) -> str | None:
    if HEDGE.search(clause):
        return None
    closed, opened = CLOSED_WORDS.search(clause), OPEN_WORDS.search(clause)
    if closed and not opened:
        return "closed"
    if opened and not closed:
        return "open"
    return None


def _road_claims(text: str, town: dict) -> list[dict]:
    claims: dict[str, str] = {}
    for clause in CLAUSE_SPLIT.split(text):
        status = _road_status(clause)
        if status is None:
            continue
        taken: list[tuple[int, int]] = []
        for position, negative_length, road_id in _alias_matches(clause, town["roads"]):
            span = (position, position - negative_length)
            if any(span[0] < end and start < span[1] for start, end in taken):
                continue
            taken.append(span)
            claims[road_id] = status
    return [{"road_id": road_id, "status": status} for road_id, status in claims.items()]


def _people(text: str) -> int | None:
    total = 0
    for match in PEOPLE_COUNT.finditer(text):
        number = match.group(1)
        total += int(number) if number.isdigit() else NUMBER_WORDS[number]
    if total:
        if re.search(r"\b(me|myself) and my\b", text):
            total += 1
        return total
    singles = len(ONE_PERSON.findall(text))
    return singles or None


def extract_report(text: str, town: dict) -> dict:
    lowered = text.lower()
    scrubbed = NEGATED_INJURY.sub(" ", VEHICLE_PHRASES.sub(" ", lowered))
    claims = _road_claims(lowered, town)
    people = _people(lowered)
    mentions_road_status = any(
        (ROAD_WORD.search(clause) or _alias_matches(clause, town["roads"]))
        and (CLOSED_WORDS.search(clause) or OPEN_WORDS.search(clause))
        for clause in CLAUSE_SPLIT.split(lowered)
    )

    if FIRE.search(scrubbed):
        kind = "fire"
    elif TRAPPED.search(scrubbed) and WATER.search(scrubbed):
        kind = "flood_rescue"
    elif MEDICAL.search(scrubbed):
        kind = "medical"
    elif EVACUATE.search(scrubbed):
        kind = "evacuation"
    elif claims or mentions_road_status:
        kind = "road_status"
    elif PROPERTY.search(scrubbed):
        kind = "property_damage"
    else:
        kind = "other"

    if kind == "flood_rescue":
        severity = "critical"
    elif kind == "fire":
        severity = "critical" if people and "inside" in scrubbed else "high"
    elif kind == "medical":
        severity = "high"
    elif kind == "evacuation":
        severity = "high" if WATER.search(scrubbed) else "medium"
    elif kind == "other" and people:
        severity = "medium"
    else:
        severity = "low"

    needs = {
        "flood_rescue": ["boat"],
        "fire": ["fire"],
        "medical": ["medic"],
    }.get(kind, [])
    if kind in ("flood_rescue", "evacuation") and people and people >= 10:
        needs = [*needs, "transport"]

    place = None
    if kind != "road_status":
        matches = _alias_matches(lowered, town["places"])
        place = matches[0][2] if matches else None

    return {
        "kind": kind,
        "place": place,
        "people": people,
        "severity": severity,
        "needs": needs,
        "road_claims": claims,
    }


TIER = {"official": 3, "responder": 2, "public": 1}
STALE_MINUTES = 20


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def resolve_conflict(claim_set: dict) -> str:
    newest = max(_minutes(claim["time"]) for claim in claim_set["claims"])
    usable = [
        (claim, status)
        for claim in claim_set["claims"]
        if newest - _minutes(claim["time"]) <= STALE_MINUTES
        if (status := _road_status(claim["text"].lower())) is not None
    ]
    if not usable:
        return "unknown"
    top_tier = max(TIER[claim["source"]] for claim, _ in usable)
    tier_claims = [(claim, status) for claim, status in usable if TIER[claim["source"]] == top_tier]
    latest = max(_minutes(claim["time"]) for claim, _ in tier_claims)
    statuses = {status for claim, status in tier_claims if _minutes(claim["time"]) == latest}
    return statuses.pop() if len(statuses) == 1 else "unknown"


# --- Routing ---------------------------------------------------------------------------

def _rebuild(previous: dict, goal: str) -> list[str]:
    path = [goal]
    while path[-1] in previous:
        path.append(previous[path[-1]])
    return path[::-1]


def dijkstra(adjacency: dict, start: str, goal: str | None = None):
    """Single-source shortest paths; stops early once `goal` is settled."""
    distance = {start: 0.0}
    previous: dict[str, str] = {}
    queue = [(0.0, start)]
    settled: set[str] = set()
    while queue:
        dist, place = heapq.heappop(queue)
        if place in settled:
            continue
        settled.add(place)
        if place == goal:
            break
        for neighbour, length, _ in adjacency[place]:
            candidate = dist + length
            if candidate < distance.get(neighbour, inf):
                distance[neighbour] = candidate
                previous[neighbour] = place
                heapq.heappush(queue, (candidate, neighbour))
    return distance, previous


def dijkstra_path(adjacency: dict, coords: dict, start: str, goal: str) -> list[str] | None:
    distance, previous = dijkstra(adjacency, start, goal)
    return _rebuild(previous, goal) if goal in distance else None


def astar_path(adjacency: dict, coords: dict, start: str, goal: str) -> list[str] | None:
    def heuristic(place: str) -> float:
        return hypot(coords[place][0] - coords[goal][0], coords[place][1] - coords[goal][1])

    distance = {start: 0.0}
    previous: dict[str, str] = {}
    queue = [(heuristic(start), start)]
    settled: set[str] = set()
    while queue:
        _, place = heapq.heappop(queue)
        if place == goal:
            return _rebuild(previous, goal)
        if place in settled:
            continue
        settled.add(place)
        for neighbour, length, _ in adjacency[place]:
            candidate = distance[place] + length
            if candidate < distance.get(neighbour, inf):
                distance[neighbour] = candidate
                previous[neighbour] = place
                heapq.heappush(queue, (candidate + heuristic(neighbour), neighbour))
    return None


def fewest_hops_path(adjacency: dict, coords: dict, start: str, goal: str) -> list[str] | None:
    previous: dict[str, str] = {}
    seen = {start}
    queue = deque([start])
    while queue:
        place = queue.popleft()
        if place == goal:
            return _rebuild(previous, goal)
        for neighbour, _, _ in sorted(adjacency[place]):
            if neighbour not in seen:
                seen.add(neighbour)
                previous[neighbour] = place
                queue.append(neighbour)
    return None


ROUTERS = {"fewest_hops": fewest_hops_path, "dijkstra": dijkstra_path, "astar": astar_path}


# --- Assignment ------------------------------------------------------------------------

def _slot_costs(scenario: dict, town: dict, closed) -> tuple[list[dict], dict]:
    """Weighted ETA for every feasible (slot, unit) pair, from Dijkstra per unit."""
    adjacency = open_adjacency(town, closed)
    from_unit = {unit["id"]: dijkstra(adjacency, unit["location"])[0] for unit in scenario["units"]}
    slot_list = slots(scenario)
    costs = {}
    for slot in slot_list:
        weight = SEVERITY_WEIGHT[slot["severity"]]
        for unit in scenario["units"]:
            distance = from_unit[unit["id"]].get(slot["place"])
            if slot["need"] in unit["capabilities"] and distance is not None:
                costs[(slot["slot"], unit["id"])] = weight * distance / unit["speed"]
    return slot_list, costs


def greedy_priority(scenario: dict, town: dict, closed, prior=None) -> list[dict]:
    slot_list, costs = _slot_costs(scenario, town, closed)
    order = {incident["id"]: rank for rank, incident in
             enumerate(sorted(scenario["incidents"], key=triage_key))}
    used: set[str] = set()
    plan = []
    for slot in sorted(slot_list, key=lambda item: (order[item["incident"]], item["slot"])):
        options = sorted(
            (cost, unit_id) for (slot_id, unit_id), cost in costs.items()
            if slot_id == slot["slot"] and unit_id not in used
        )
        if options:
            used.add(options[0][1])
            plan.append({"slot": slot["slot"], "unit": options[0][1]})
    return plan


def greedy_global(scenario: dict, town: dict, closed, prior=None) -> list[dict]:
    _, costs = _slot_costs(scenario, town, closed)
    filled: set[str] = set()
    used: set[str] = set()
    plan = []
    for (slot_id, unit_id), _ in sorted(costs.items(), key=lambda item: (item[1], item[0])):
        if slot_id not in filled and unit_id not in used:
            filled.add(slot_id)
            used.add(unit_id)
            plan.append({"slot": slot_id, "unit": unit_id})
    return plan


STICKY_BONUS = 1.0  # weighted minutes a kept assignment is worth; only used for replanning
INFEASIBLE = 1e9


def hungarian(scenario: dict, town: dict, closed, prior=None) -> list[dict]:
    """Rows are slots; columns are units plus one 'leave unfilled' column per slot."""
    slot_list, costs = _slot_costs(scenario, town, closed)
    units = [unit["id"] for unit in scenario["units"]]
    kept = {entry["slot"]: entry["unit"] for entry in prior or []}
    matrix = np.full((len(slot_list), len(units) + len(slot_list)), INFEASIBLE)
    for row, slot in enumerate(slot_list):
        for column, unit_id in enumerate(units):
            cost = costs.get((slot["slot"], unit_id))
            if cost is not None:
                matrix[row, column] = cost - (STICKY_BONUS if kept.get(slot["slot"]) == unit_id else 0)
        matrix[row, len(units) + row] = SEVERITY_WEIGHT[slot["severity"]] * UNFILLED_MINUTES
    rows, columns = linear_sum_assignment(matrix)
    return [
        {"slot": slot_list[row]["slot"], "unit": units[column]}
        for row, column in zip(rows, columns)
        if column < len(units) and matrix[row, column] < INFEASIBLE
    ]


def hungarian_sticky(scenario: dict, town: dict, closed, prior=None) -> list[dict]:
    return hungarian(scenario, town, closed, prior)


def hungarian_fresh(scenario: dict, town: dict, closed, prior=None) -> list[dict]:
    return hungarian(scenario, town, closed, None)


ASSIGNERS = {
    "greedy_priority": greedy_priority,
    "greedy_global": greedy_global,
    "hungarian": hungarian_fresh,
}
REPLANNERS = {**ASSIGNERS, "hungarian_sticky": hungarian_sticky}


# --- Run -------------------------------------------------------------------------------

def _timed(function):
    started = perf_counter()
    output = function()
    return output, perf_counter() - started


def run() -> dict:
    town = load_town()
    coords = coordinates(town)
    reports = load(CASES / "reports.json")["reports"]
    conflict_sets = load(CASES / "conflicts.json")["sets"]
    scenarios = load(CASES / "scenarios.json")["scenarios"]
    outputs: dict = {}
    seconds: dict = {}

    def record(task: str, variant: str, function) -> None:
        output, elapsed = _timed(function)
        outputs.setdefault(task, {})[variant] = output
        seconds.setdefault(task, {})[variant] = elapsed

    record("reports", "rules",
           lambda: {report["id"]: extract_report(report["text"], town) for report in reports})
    record("conflicts", "rules",
           lambda: {claim_set["id"]: resolve_conflict(claim_set) for claim_set in conflict_sets})
    for name, router in ROUTERS.items():
        record("routing", name, lambda router=router: {
            scenario["id"]: {
                query["id"]: router(open_adjacency(town, scenario["closed_roads"]), coords,
                                    query["from"], query["to"])
                for query in scenario["routing_queries"]
            }
            for scenario in scenarios
        })
    record("triage", "rule", lambda: {
        scenario["id"]: [incident["id"] for incident in
                         sorted(scenario["incidents"], key=triage_key)]
        for scenario in scenarios
    })
    for name, assigner in ASSIGNERS.items():
        record("assignment", name, lambda assigner=assigner: {
            scenario["id"]: assigner(scenario, town, scenario["closed_roads"])
            for scenario in scenarios
        })
    for name, replanner in REPLANNERS.items():
        record("replanning", name, lambda replanner=replanner: {
            scenario["id"]: replanner(
                scenario,
                town,
                [*scenario["closed_roads"], scenario["disruption"]["closes"]],
                scenario["disruption"]["prior_plan"],
            )
            for scenario in scenarios
            if scenario.get("disruption")
        })

    return {
        "arm": "algorithms",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "outputs": outputs,
        "seconds": seconds,
    }


def main() -> None:
    result = run()
    save(RESULTS / "algorithms.json", result)
    print(f"Wrote {RESULTS / 'algorithms.json'}")


if __name__ == "__main__":
    main()
