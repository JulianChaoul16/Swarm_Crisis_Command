"""The planner's assignment matches an exhaustive search on small random instances."""

from itertools import product
import random
import unittest

from crisis_command.model import City, Coordinate, EmergencyService, Incident, Road, Severity
from crisis_command.planner import SEVERITY_WEIGHT, Planner, default_priority

CAPABILITIES = ["rescue", "medical", "fire"]


def grid_city(rng: random.Random) -> City:
    city = City("Random grid")
    points = [Coordinate(x, y) for x in range(3) for y in range(3)]
    index = 0
    for a in points:
        for b in points:
            if (b.x - a.x, b.y - a.y) in ((1, 0), (0, 1)):
                city.add_road(Road(f"r{index}", a, b, is_closed=rng.random() < 0.2))
                index += 1
    return city


def random_instance(seed: int):
    rng = random.Random(seed)
    city = grid_city(rng)
    nodes = [Coordinate(x, y) for x in range(3) for y in range(3)]
    incidents = [
        Incident(f"i{n}", "test", rng.choice(nodes), rng.choice(list(Severity)),
                 frozenset(rng.sample(CAPABILITIES, rng.randint(1, 2))),
                 people=rng.choice([0, 0, 2, 5]))
        for n in range(rng.randint(2, 4))
    ]
    services = [
        EmergencyService(f"s{n}", f"Unit {n}", rng.choice(nodes),
                         frozenset(rng.sample(CAPABILITIES, rng.randint(1, 2))),
                         speed=rng.choice([1.0, 1.5, 2.0]), capacity=rng.choice([0, 0, 2, 4]))
        for n in range(rng.randint(3, 6))
    ]
    return city, incidents, services


def exhaustive(city, incidents, services):
    """Served set chosen lexicographically by priority, then the lowest weighted ETA."""
    ordered = sorted(incidents, key=default_priority)

    etas = {}
    for service in services:
        for incident in ordered:
            try:
                distance = city.route_distance(service.location, incident.location)
                etas[(service.id, incident.id)] = distance / service.speed
            except Exception:
                etas[(service.id, incident.id)] = None

    def eta(service, incident):
        return etas[(service.id, incident.id)]

    best = None
    for choice in product([None, *range(len(ordered))], repeat=len(services)):
        groups = {index: [] for index in range(len(ordered))}
        feasible = True
        for service, index in zip(services, choice):
            if index is None:
                continue
            if eta(service, ordered[index]) is None:
                feasible = False
                break
            groups[index].append(service)
        if not feasible:
            continue
        served = []
        for index, incident in enumerate(ordered):
            units = groups[index]
            covered = set().union(*(unit.capabilities for unit in units)) if units else set()
            if units and incident.required_services <= covered and \
                    sum(unit.capacity for unit in units) >= incident.people:
                served.append(index)
            elif units:
                feasible = False  # units sent to an incident they cannot fully serve
                break
        if not feasible:
            continue
        served_key = tuple(index not in served for index in range(len(ordered)))
        cost = sum(SEVERITY_WEIGHT[ordered[index].severity] * eta(unit, ordered[index])
                   for index in served for unit in groups[index])
        candidate = (served_key, round(cost, 9))
        if best is None or candidate < best:
            best = candidate
    return best


class PlannerOptimalityTests(unittest.TestCase):
    def test_matches_exhaustive_search_on_random_instances(self) -> None:
        for seed in range(60):
            city, incidents, services = random_instance(seed)
            plan = Planner().plan(incidents, services, city, world_version=1)
            ordered = sorted(incidents, key=default_priority)
            served = {dispatch.incident_id for dispatch in plan.assignments}
            served_key = tuple(incident.id not in served for incident in ordered)
            weight = {incident.id: SEVERITY_WEIGHT[incident.severity] for incident in incidents}
            cost = sum(weight[d.incident_id] * d.estimated_arrival for d in plan.assignments)
            expected = exhaustive(city, incidents, services)
            with self.subTest(seed=seed):
                self.assertEqual(served_key, expected[0])
                self.assertAlmostEqual(cost, expected[1], places=6)


if __name__ == "__main__":
    unittest.main()
