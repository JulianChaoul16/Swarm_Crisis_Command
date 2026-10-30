"""Build the town map, the scenario cases and their answer keys.

    python -m findings.build_cases

Scenarios are drawn from a fixed seed that is not tuned to favour any arm. Reports and
conflict sets are hand-written in cases/ and are not touched here.
"""

from math import hypot
import random

from findings.common import ANSWERS, CASES, save, slots
from findings.oracle import all_routes, answer_scenario, best_plan, distance_table

SEED = 2026
SCENARIO_COUNT = 20

# (id, name, x, y, aliases); places without aliases are road junctions, not incident sites.
PLACES = [
    ("fire_station_1", "Fire Station 1", 1, 6, ["fire station 1", "station 1", "firehouse"]),
    ("oak_and_3rd", "Oak Ave & 3rd St", 3, 6, ["oak and 3rd", "oak & 3rd", "3rd and oak"]),
    ("county_hospital", "County Hospital", 5, 6, ["county hospital", "county general", "hospital"]),
    ("old_mill", "Old Mill", 7, 5.2, ["old mill", "the mill"]),
    ("north_school", "North School", 1.5, 4.5, ["north school", "north elementary", "the school"]),
    ("main_st_north", "Main St bridge, north end", 4, 4, []),
    ("upriver_north", "Upriver bridge, north end", 8, 4, []),
    ("main_st_south", "Main St bridge, south end", 4, 3, []),
    ("elm_and_5th", "Elm St & 5th St", 2, 2, ["elm st", "elm and 5th", "elm & 5th", "calle elm"]),
    ("riverside_apts", "Riverside Apartments", 5.5, 2.2, ["riverside apartments", "riverside apts"]),
    ("pine_market", "Pine St Market", 7, 1.5, ["pine market", "pine st market", "pine st"]),
    ("upriver_south", "Upriver bridge, south end", 8, 3, []),
    ("southgate_shelter", "Southgate Shelter", 4, 0.5, ["southgate", "the shelter"]),
    ("lowtown_depot", "Lowtown Ambulance Depot", 1, 0.5, ["lowtown", "ambulance depot"]),
    ("cedar_lane_park", "Cedar Lane Trailer Park", 9, 1, ["cedar lane", "cedar ln", "trailer park"]),
]

# (id, a, b, winding factor >= 1 so straight-line distance never overestimates, aliases)
ROADS = [
    ("station-rd", "fire_station_1", "oak_and_3rd", 1.1, ["station rd", "station road"]),
    ("oak-ave", "oak_and_3rd", "county_hospital", 1.0, ["oak ave", "oak avenue"]),
    ("school-ln", "fire_station_1", "north_school", 1.2, ["school ln", "school lane"]),
    ("school-connector", "north_school", "main_st_north", 1.1, []),
    ("third-st", "oak_and_3rd", "main_st_north", 1.05, ["3rd st", "third st"]),
    ("mill-rd", "county_hospital", "old_mill", 1.2, ["mill rd", "mill road"]),
    ("river-rd-north", "old_mill", "upriver_north", 1.1, ["river rd", "river road"]),
    ("hospital-dr", "county_hospital", "main_st_north", 1.05, ["hospital dr", "hospital drive"]),
    ("main-st-bridge", "main_st_north", "main_st_south", 1.0,
     ["main st bridge", "main street bridge", "main bridge"]),
    ("upriver-bridge", "upriver_north", "upriver_south", 1.0, ["upriver bridge"]),
    ("low-water-crossing", "north_school", "elm_and_5th", 1.3, ["low water crossing"]),
    ("elm-st", "main_st_south", "elm_and_5th", 1.1, ["elm st", "elm street"]),
    ("main-st-south", "main_st_south", "riverside_apts", 1.05, []),
    ("pine-st", "riverside_apts", "pine_market", 1.1, ["pine st"]),
    ("river-rd-south", "pine_market", "upriver_south", 1.15, ["river rd south"]),
    ("fifth-st", "elm_and_5th", "lowtown_depot", 1.1, ["5th st", "fifth st"]),
    ("elm-southgate", "elm_and_5th", "southgate_shelter", 1.2, []),
    ("shelter-rd", "southgate_shelter", "riverside_apts", 1.1, ["shelter rd"]),
    ("depot-rd", "southgate_shelter", "lowtown_depot", 1.0, ["depot rd"]),
    ("cedar-ln", "pine_market", "cedar_lane_park", 1.2, ["cedar ln", "cedar lane"]),
    ("river-rd-east", "upriver_south", "cedar_lane_park", 1.1, []),
]

UNIT_TYPES = {
    "boat": (["boat"], 0.6),
    "medic": (["medic"], 1.0),
    "engine": (["fire"], 0.8),
    "bus": (["transport"], 0.5),
    "rescue": (["boat", "medic"], 0.7),
}
NEED_SETS = [
    ["boat"], ["boat"], ["medic"], ["medic"], ["fire"], ["transport"],
    ["boat", "medic"], ["boat", "transport"], ["fire", "medic"],
]
SEVERITIES = ["critical", "high", "high", "medium", "low"]
SEED_CLOSURES = ["low-water-crossing", "upriver-bridge", "elm-st", "river-rd-north", "pine-st"]
DISRUPTIONS = ["main-st-bridge", "upriver-bridge", "low-water-crossing", "elm-st", "hospital-dr",
               "third-st", "shelter-rd"]
MAX_SLOTS = 6


def build_town() -> dict:
    coords = {place[0]: (place[2], place[3]) for place in PLACES}
    return {
        "name": "Millbrook (fictional)",
        "units_of_measure": {"distance": "km", "speed": "km per minute", "time": "minutes"},
        "river": "Runs east-west between y=3 and y=4; crossings are main-st-bridge, "
                 "upriver-bridge and low-water-crossing.",
        "places": [
            {"id": pid, "name": name, "x": x, "y": y, "aliases": aliases}
            for pid, name, x, y, aliases in PLACES
        ],
        "roads": [
            {
                "id": rid,
                "a": a,
                "b": b,
                "length": round(factor * hypot(coords[a][0] - coords[b][0],
                                               coords[a][1] - coords[b][1]), 3),
                "aliases": aliases,
            }
            for rid, a, b, factor, aliases in ROADS
        ],
    }


def build_scenario(rng: random.Random, number: int) -> dict:
    sites = [place[0] for place in PLACES if place[4]]
    all_places = [place[0] for place in PLACES]
    closed = sorted(rng.sample(SEED_CLOSURES, rng.randint(0, 2)))

    while True:
        incidents = []
        for index in range(rng.randint(3, 4)):
            needs = list(rng.choice(NEED_SETS))
            incidents.append({
                "id": f"inc-{index + 1}",
                "place": rng.choice(sites),
                "severity": rng.choice(SEVERITIES),
                "people": rng.randint(1, 14 if "transport" in needs else 6),
                "needs": needs,
                "reported_minute": rng.randint(0, 60),
            })
        if sum(len(incident["needs"]) for incident in incidents) <= MAX_SLOTS:
            break

    units = []
    counts: dict[str, int] = {}
    for _ in range(rng.randint(4, 7)):
        kind = rng.choice(list(UNIT_TYPES))
        counts[kind] = counts.get(kind, 0) + 1
        capabilities, speed = UNIT_TYPES[kind]
        units.append({
            "id": f"{kind}-{counts[kind]}",
            "capabilities": capabilities,
            "speed": speed,
            "location": rng.choice(all_places),
        })

    queries = []
    for index in range(3):
        start, goal = rng.sample(all_places, 2)
        queries.append({"id": f"q{index + 1}", "from": start, "to": goal})

    return {
        "id": f"S{number:02d}",
        "closed_roads": closed,
        "units": units,
        "incidents": incidents,
        "routing_queries": queries,
        "disruption": None,
    }


def choose_disruption(town: dict, scenario: dict, number: int) -> dict:
    """Close a road that the committed plan actually drives over.

    Uses its own seeded draw so the rest of the scenario set does not depend on this choice.
    Falls back to DISRUPTIONS when the plan uses no closable road.
    """
    routes = all_routes(town, scenario["closed_roads"])
    _, prior_plan = best_plan(scenario, distance_table(routes))
    units = {unit["id"]: unit for unit in scenario["units"]}
    places = {slot["slot"]: slot["place"] for slot in slots(scenario)}
    road_between = {frozenset((road["a"], road["b"])): road["id"] for road in town["roads"]}
    used = sorted({
        road_between[frozenset(pair)]
        for entry in prior_plan
        for path in [routes[(units[entry["unit"]]["location"], places[entry["slot"]])][1]]
        for pair in zip(path, path[1:])
    })
    pick = random.Random(SEED * 1000 + number)
    options = used or [road for road in DISRUPTIONS if road not in scenario["closed_roads"]]
    # The prior plan is part of the case input: it is what was committed before the closure.
    return {"closes": pick.choice(options), "at_minute": 60, "prior_plan": prior_plan}


def main() -> None:
    town = build_town()
    rng = random.Random(SEED)
    scenarios, answers = [], {}
    for number in range(1, SCENARIO_COUNT + 1):
        scenario = build_scenario(rng, number)
        if number % 2 == 0:
            scenario["disruption"] = choose_disruption(town, scenario, number)
        answer = answer_scenario(town, scenario)
        scenarios.append(scenario)
        answers[scenario["id"]] = answer
        print(f"{scenario['id']}: {len(scenario['incidents'])} incidents, "
              f"{len(scenario['units'])} units, closed {scenario['closed_roads']}")
    save(CASES / "town.json", town)
    save(CASES / "scenarios.json", {"seed": SEED, "scenarios": scenarios})
    save(ANSWERS / "scenarios.json", answers)


if __name__ == "__main__":
    main()
