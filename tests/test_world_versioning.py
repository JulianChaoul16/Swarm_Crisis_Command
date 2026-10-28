from dataclasses import replace
import unittest

from crisis_command.model import City, Coordinate, EmergencyService, Incident, IncidentStatus, Road, Severity
from crisis_command.planner import Claim, Planner, ResponsePlan
from crisis_command.simulation import Simulation, World


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

    def test_safe_stale_plan_executes_before_replanning_new_incident(self) -> None:
        world = self.world

        class ChangingPlanner(Planner):
            def plan(
                self,
                incidents: list[Incident],
                services: list[EmergencyService],
                city: City | None = None,
                world_version: int | None = None,
                claims: tuple[Claim, ...] = (),
            ) -> ResponsePlan:
                plan = super().plan(incidents, services, city, world_version, claims)
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
        plan = simulation.step()

        self.assertEqual(plan.assignments[0].incident_id, self.incident.id)
        self.assertEqual(self.incident.status, IncidentStatus.ASSIGNED)
        self.assertFalse(self.service.available)
        self.assertEqual(world.tick_count, 1)
        self.assertIn("stale_plan_accepted", [event.kind for event in world.event_log])

    def test_stale_plan_recalculates_when_road_closure_makes_it_unsafe(self) -> None:
        world = self.world

        class ClosingPlanner(Planner):
            def __init__(self) -> None:
                super().__init__()
                self.closed_road = False

            def plan(
                self,
                incidents: list[Incident],
                services: list[EmergencyService],
                city: City | None = None,
                world_version: int | None = None,
                claims: tuple[Claim, ...] = (),
            ) -> ResponsePlan:
                plan = super().plan(incidents, services, city, world_version, claims)
                if not self.closed_road:
                    self.closed_road = True
                    world.city.set_road_closed("main-road", True)
                return plan

        simulation = Simulation(world, ClosingPlanner())
        plan = simulation.step()

        self.assertFalse(plan.assignments)
        self.assertEqual(plan.unassigned_incident_ids, (self.incident.id,))
        self.assertEqual(plan.world_version, world.version - 1)
        self.assertIn("stale_plan_rejected", [event.kind for event in world.event_log])
        self.assertEqual(world.tick_count, 1)
        self.assertTrue(self.service.available)

    def test_successful_step_advances_version_and_tags_plan(self) -> None:
        initial_version = self.world.version

        plan = Simulation(self.world).step()

        self.assertEqual(plan.world_version, initial_version)
        self.assertEqual(self.world.version, initial_version + 1)
        self.assertEqual(self.world.tick_count, 1)

    def test_invalid_route_is_rejected_before_dispatch(self) -> None:
        snapshot = self.world.snapshot()
        plan = Planner().plan(
            list(snapshot.incidents),
            list(snapshot.services),
            snapshot.city,
            world_version=snapshot.version,
        )
        invalid_dispatch = replace(
            plan.assignments[0],
            route=(self.start, Coordinate(2, 1), self.end),
        )
        invalid_plan = replace(plan, assignments=(invalid_dispatch,))

        with self.assertRaisesRegex(ValueError, "does not match"):
            Simulation(self.world).step(snapshot=snapshot, plan=invalid_plan)

        self.assertEqual(self.world.tick_count, 0)
        self.assertTrue(self.service.available)

    def test_unversioned_entity_mutation_still_invalidates_plan(self) -> None:
        world = self.world

        class MutatingPlanner(Planner):
            def plan(
                self,
                incidents: list[Incident],
                services: list[EmergencyService],
                city: City | None = None,
                world_version: int | None = None,
                claims: tuple[Claim, ...] = (),
            ) -> ResponsePlan:
                plan = super().plan(incidents, services, city, world_version, claims)
                world.services[0].location = Coordinate(1, 0)
                return plan

        plan = Simulation(world, MutatingPlanner()).step()

        self.assertEqual(plan.assignments[0].route[0], Coordinate(1, 0))
        self.assertEqual(world.tick_count, 1)
        self.assertFalse(self.service.available)


if __name__ == "__main__":
    unittest.main()
