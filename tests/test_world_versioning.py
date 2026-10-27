import unittest

from model import City, Coordinate, EmergencyService, Incident, IncidentStatus, Road, Severity
from planner import Planner, ResponsePlan
from simulation import Simulation, StalePlanError, World


class WorldVersioningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = Coordinate(0, 0)
        self.end = Coordinate(4, 0)
        self.city = City("Test City")
        self.city.add_road(Road("main-road", self.start, self.end))
        self.incident = Incident(
            "i1",
            "medical emergency",
            self.end,
            Severity.HIGH,
            frozenset({"medical"}),
        )
        self.service = EmergencyService(
            "s1",
            "Ambulance",
            self.start,
            frozenset({"medical"}),
            speed=2,
        )
        self.world = World([self.incident], [self.service], city=self.city)

    def test_entity_and_road_changes_increment_version(self) -> None:
        initial_version = self.world.version
        self.world.add_incident(
            Incident("i2", "fire", self.end, Severity.LOW)
        )
        self.assertEqual(self.world.version, initial_version + 1)

        self.world.add_emergency_service(
            EmergencyService("s2", "Fire engine", self.start, frozenset({"fire"}))
        )
        self.assertEqual(self.world.version, initial_version + 2)

        self.world.city.set_road_closed("main-road", True)
        self.assertEqual(self.world.version, initial_version + 3)

    def test_snapshot_is_detached_from_later_world_changes(self) -> None:
        snapshot = self.world.snapshot()
        self.world.city.set_road_closed("main-road", True)
        self.world.services[0].location = Coordinate(1, 0)

        self.assertEqual(snapshot.version + 1, self.world.version)
        self.assertFalse(snapshot.city.roads[0].is_closed)
        self.assertEqual(snapshot.services[0].location, self.start)

    def test_stale_plan_is_rejected_without_committing_assignments(self) -> None:
        world = self.world

        class ChangingPlanner(Planner):
            def plan(
                self,
                incidents: list[Incident],
                services: list[EmergencyService],
                city: City | None = None,
                world_version: int | None = None,
            ) -> ResponsePlan:
                plan = super().plan(incidents, services, city, world_version)
                world.add_incident(
                    Incident(
                        "new-incident",
                        "flood",
                        world.incidents[0].location,
                        Severity.MEDIUM,
                    )
                )
                return plan

        simulation = Simulation(world, ChangingPlanner())
        with self.assertRaises(StalePlanError):
            simulation.step()

        self.assertEqual(self.incident.status, IncidentStatus.OPEN)
        self.assertTrue(self.service.available)
        self.assertEqual(world.tick_count, 0)

    def test_successful_step_advances_version_and_tags_plan(self) -> None:
        initial_version = self.world.version

        plan = Simulation(self.world).step()

        self.assertEqual(plan.world_version, initial_version)
        self.assertEqual(self.world.version, initial_version + 1)
        self.assertEqual(self.world.tick_count, 1)

    def test_unversioned_entity_mutation_still_invalidates_plan(self) -> None:
        world = self.world

        class MutatingPlanner(Planner):
            def plan(
                self,
                incidents: list[Incident],
                services: list[EmergencyService],
                city: City | None = None,
                world_version: int | None = None,
            ) -> ResponsePlan:
                plan = super().plan(incidents, services, city, world_version)
                world.services[0].location = Coordinate(1, 0)
                return plan

        with self.assertRaises(StalePlanError):
            Simulation(world, MutatingPlanner()).step()

        self.assertEqual(world.tick_count, 0)
        self.assertTrue(self.service.available)


if __name__ == "__main__":
    unittest.main()
