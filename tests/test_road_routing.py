import unittest

from model import City, Coordinate, EmergencyService, Incident, IncidentStatus, Road, Severity
from planner import Planner
from simulation import Simulation, World


class RoadRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = Coordinate(0, 0)
        self.end = Coordinate(10, 0)
        self.detour_top = Coordinate(0, 3)
        self.detour_end = Coordinate(10, 3)
        self.city = City("Test City")
        for road in (
            Road("direct", self.start, self.end),
            Road("detour-1", self.start, self.detour_top),
            Road("detour-2", self.detour_top, self.detour_end),
            Road("detour-3", self.detour_end, self.end),
        ):
            self.city.add_road(road)

    def test_closed_road_is_excluded_from_shortest_route(self) -> None:
        self.city.set_road_closed("direct", True)

        self.assertEqual(
            self.city.shortest_route(self.start, self.end),
            [self.start, self.detour_top, self.detour_end, self.end],
        )
        self.assertEqual(self.city.route_distance(self.start, self.end), 16)

    def test_route_supports_locations_on_road_segments(self) -> None:
        self.assertEqual(
            self.city.route_distance(Coordinate(2, 0), Coordinate(8, 0)),
            6,
        )

    def test_planner_marks_road_inaccessible_incident_unassigned(self) -> None:
        for road in self.city.roads:
            self.city.set_road_closed(road.id, True)
        incident = Incident("i1", "fire", self.end, Severity.HIGH, frozenset({"fire"}))
        service = EmergencyService(
            "s1",
            "Engine",
            self.start,
            frozenset({"fire"}),
        )

        plan = Planner().plan([incident], [service], self.city)

        self.assertFalse(plan.assignments)
        self.assertEqual(plan.unassigned_incident_ids, ("i1",))

    def test_simulation_moves_along_road_detour(self) -> None:
        self.city.set_road_closed("direct", True)
        incident = Incident("i1", "fire", self.end, Severity.HIGH, frozenset({"fire"}))
        service = EmergencyService(
            "s1",
            "Engine",
            self.start,
            frozenset({"fire"}),
            speed=2,
        )
        simulation = Simulation(World([incident], [service], city=self.city))

        simulation.run(max_steps=10)

        self.assertEqual(incident.status, IncidentStatus.RESOLVED)
        self.assertEqual(service.location, self.end)


if __name__ == "__main__":
    unittest.main()
