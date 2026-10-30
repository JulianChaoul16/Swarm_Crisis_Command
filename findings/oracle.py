"""Brute-force answer keys for the scenario tasks.

Deliberately shares no search code with the arms: routes come from enumerating every simple
path, assignments from enumerating every slot-to-unit choice. Slow, but exact on these sizes.
"""

from itertools import permutations
from math import inf

import networkx as nx

from findings.common import (
    SEVERITY_WEIGHT,
    UNFILLED_MINUTES,
    evaluate_plan,
    open_adjacency,
    slots,
    triage_key,
)


def all_routes(town: dict, closed) -> dict[tuple[str, str], tuple[float, list[str]] | None]:
    """Shortest route for every ordered place pair, or None when unreachable."""
    graph = nx.Graph()
    graph.add_nodes_from(place["id"] for place in town["places"])
    for place, neighbours in open_adjacency(town, closed).items():
        for neighbour, length, _ in neighbours:
            graph.add_edge(place, neighbour, length=length)
    routes: dict[tuple[str, str], tuple[float, list[str]] | None] = {}
    for start, goal in permutations(graph.nodes, 2):
        best: tuple[float, list[str]] | None = None
        for path in nx.all_simple_paths(graph, start, goal):
            length = sum(graph[a][b]["length"] for a, b in zip(path, path[1:]))
            if best is None or length < best[0] - 1e-12:
                best = (length, path)
        routes[(start, goal)] = best
    for place in graph.nodes:
        routes[(place, place)] = (0.0, [place])
    return routes


def distance_table(routes) -> dict[tuple[str, str], float | None]:
    return {pair: (None if route is None else route[0]) for pair, route in routes.items()}


def best_plan(scenario: dict, distances) -> tuple[float, list[dict]]:
    """Exhaustive search over every slot -> (unit or unfilled) choice."""
    slot_list = slots(scenario)
    options = []
    for slot in slot_list:
        weight = SEVERITY_WEIGHT[slot["severity"]]
        choices = [
            (weight * distances[(unit["location"], slot["place"])] / unit["speed"], unit["id"])
            for unit in scenario["units"]
            if slot["need"] in unit["capabilities"]
            if distances[(unit["location"], slot["place"])] is not None
        ]
        choices.sort()
        choices.append((weight * UNFILLED_MINUTES, None))
        options.append(choices)

    best_cost, best_choice = inf, None

    def search(index: int, used: frozenset, cost: float, chosen: tuple) -> None:
        nonlocal best_cost, best_choice
        if cost >= best_cost - 1e-12:
            return
        if index == len(slot_list):
            best_cost, best_choice = cost, chosen
            return
        for slot_cost, unit_id in options[index]:
            if unit_id is not None and unit_id in used:
                continue
            search(
                index + 1,
                used | {unit_id} if unit_id else used,
                cost + slot_cost,
                chosen + (unit_id,),
            )

    search(0, frozenset(), 0.0, ())
    plan = [
        {"slot": slot["slot"], "unit": unit_id}
        for slot, unit_id in zip(slot_list, best_choice)
        if unit_id is not None
    ]
    checked = evaluate_plan(scenario, plan, distances)["cost"]
    assert abs(checked - best_cost) < 1e-9, (checked, best_cost)
    return best_cost, plan


def answer_scenario(town: dict, scenario: dict) -> dict:
    closed = scenario["closed_roads"]
    routes = all_routes(town, closed)
    distances = distance_table(routes)
    answer: dict = {
        "routing": {},
        "triage": [incident["id"] for incident in sorted(scenario["incidents"], key=triage_key)],
    }
    for query in scenario["routing_queries"]:
        route = routes[(query["from"], query["to"])]
        answer["routing"][query["id"]] = (
            {"distance": None, "path": None}
            if route is None
            else {"distance": route[0], "path": route[1]}
        )
    cost, plan = best_plan(scenario, distances)
    answer["assignment"] = {"objective": cost, "plan": plan}
    if scenario.get("disruption"):
        after = distance_table(all_routes(town, [*closed, scenario["disruption"]["closes"]]))
        cost, plan = best_plan(scenario, after)
        answer["replanning"] = {"objective": cost, "plan": plan}
    return answer
