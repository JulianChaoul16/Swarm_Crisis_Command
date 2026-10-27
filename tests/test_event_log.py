import unittest

from model import City, Coordinate, EmergencyService, Incident, Road, Severity
from simulation import Simulation, World


class EventLogTests(unittest.TestCase):
    def test_world_changes_create_ordered_explanatory_events(self) -> None:
        start = Coordinate(0, 0)
        end = Coordinate(3, 0)
        city = City("Test City")
        city.add_road(Road("main-road", start, end))
        world = World(city=city)
        initial_version = world.version

        incident = Incident("i1", "medical emergency", end, Severity.HIGH)
        world.add_incident(incident, reason="Operator entered a new report.")
        service = EmergencyService("s1", "Ambulance", start, frozenset())
        world.add_emergency_service(service, reason="Unit checked in.")
        world.city.set_road_closed(
            "main-road",
            True,
            reason="Flooding reported on the road.",
        )

        events = world.event_log
        self.assertEqual(
            [event.sequence for event in events],
            list(range(1, len(events) + 1)),
        )
        self.assertEqual(events[0].kind, "world_initialized")
        self.assertEqual(events[1].kind, "incident_added")
        self.assertEqual(events[1].reason, "Operator entered a new report.")
        self.assertEqual(events[2].kind, "service_added")
        self.assertEqual(events[2].reason, "Unit checked in.")
        self.assertEqual(events[3].kind, "road_closed")
        self.assertEqual(events[3].reason, "Flooding reported on the road.")
        self.assertEqual(events[3].version, initial_version + 3)
        self.assertEqual(events[3].details[-1], ("is_closed", "true"))

    def test_simulation_records_dispatch_movement_and_resolution(self) -> None:
        start = Coordinate(0, 0)
        end = Coordinate(2, 0)
        incident = Incident(
            "i1",
            "medical emergency",
            end,
            Severity.HIGH,
            frozenset({"medical"}),
        )
        service = EmergencyService(
            "s1",
            "Ambulance",
            start,
            frozenset({"medical"}),
            speed=1,
        )
        simulation = Simulation(World([incident], [service]))

        simulation.run(max_steps=3)

        kinds = [event.kind for event in simulation.world.event_log]
        self.assertEqual(kinds.count("service_dispatched"), 1)
        self.assertEqual(kinds.count("service_moved"), 2)
        self.assertEqual(kinds.count("incident_resolved"), 1)
        self.assertEqual(kinds.count("simulation_tick"), 2)
        self.assertEqual(
            [event.sequence for event in simulation.world.event_log],
            list(range(1, len(simulation.world.event_log) + 1)),
        )


if __name__ == "__main__":
    unittest.main()
