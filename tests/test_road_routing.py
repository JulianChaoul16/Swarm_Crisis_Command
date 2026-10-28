import unittest

from crisis_command.model import City, Coordinate, EmergencyService, Incident, IncidentStatus, Road, Severity
from crisis_command.planner import Planner
from crisis_command.simulation import Simulation, World


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

    def test_assigned_service_replans_when_a_road_closes(self) -> None:
        start = Coordinate(0, 0)
        junction = Coordinate(1, 0)
        destination = Coordinate(2, 0)
        detour = Coordinate(1, 1)
        city = City("Changing City")
        for road in (
            Road("approach", start, junction),
            Road("planned", junction, destination),
            Road("detour-1", junction, detour),
            Road("detour-2", detour, destination),
        ):
            city.add_road(road)
        incident = Incident(
            "i1",
            "fire",
            destination,
            Severity.HIGH,
            frozenset({"fire"}),
        )
        service = EmergencyService(
            "s1",
            "Engine",
            start,
            frozenset({"fire"}),
            speed=1,
        )
        simulation = Simulation(World([incident], [service], city=city))

        simulation.step()
        self.assertEqual(service.location, junction)
        city.set_road_closed("planned", True, reason="Road blocked in simulation.")
        simulation.step()

        self.assertEqual(service.location, detour)
        self.assertEqual(incident.status, IncidentStatus.ASSIGNED)

        simulation.step()
        self.assertEqual(incident.status, IncidentStatus.ASSIGNED)
        simulation.step()
        self.assertEqual(incident.status, IncidentStatus.RESOLVED)


if __name__ == "__main__":
    unittest.main()
