import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crisis_command.database import connection
from crisis_command.model import City, Coordinate, EmergencyService, Incident, Road, Severity
from crisis_command.operations import CommandService, PlanStatus, ReportStatus
from crisis_command.operations.feasibility import closed_roads_on_route
from crisis_command.operations.scenario import FLOOD_SCENARIO, build_flood_world
from crisis_command.planner import Planner


def assignments(plan) -> dict[str, str]:
    return {dispatch.service_id: dispatch.incident_id for dispatch in plan.plan.assignments}


def changes(plan) -> dict[str, tuple[str, str]]:
    return {change.service_id: (change.change, change.explanation) for change in plan.changes}


class FloodScenarioTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path_patcher = patch.object(connection, "DB_PATH", Path(self.temp_dir.name) / "test.db")
        self.path_patcher.start()
        self.service = CommandService(scenario=FLOOD_SCENARIO, agent_timeout=2)

    def tearDown(self) -> None:
        self.path_patcher.stop()
        self.temp_dir.cleanup()


class SeedTests(FloodScenarioTestCase):
    def test_seed_has_six_incidents_eight_resources_and_a_complete_initial_plan(self) -> None:
        world = self.service.world
        plan = self.service.current_plan

        self.assertEqual(len(world.incidents), 6)
        self.assertEqual(len(world.services), 8)
        self.assertTrue(plan.valid)
        self.assertEqual(plan.plan.unassigned_incident_ids, ())
        crossing = {
            dispatch.service_id
            for dispatch in plan.plan.assignments
            if "main-street-bridge" in self._roads_used(dispatch.route)
        }
        self.assertEqual(crossing, {"fire-2", "medic-1"})

    def _roads_used(self, route):
        city = self.service.world.city
        city.set_road_closed("main-street-bridge", True, "probe")
        used = closed_roads_on_route(city, route)
        city.set_road_closed("main-street-bridge", False, "probe")
        return used

    def test_seed_is_reproducible_and_reset_restores_it(self) -> None:
        first = assignments(self.service.current_plan)
        self.service.inject_disruption("bridge_closure")
        self.service.reset()

        bridge = next(r for r in self.service.world.city.roads if r.id == "main-street-bridge")
        upriver = next(r for r in self.service.world.city.roads if r.id == "upriver-bridge")
        self.assertFalse(bridge.is_closed)
        self.assertTrue(upriver.is_closed)
        self.assertEqual(assignments(self.service.current_plan), first)


class BridgeClosureTests(FloodScenarioTestCase):
    def test_closure_before_approval_replans_with_explained_changes_and_unmet_needs(self) -> None:
        before = self.service.current_plan
        report = self.service.inject_disruption("bridge_closure")
        after = self.service.current_plan

        self.assertEqual(report.status, ReportStatus.CONFIRMED)
        self.assertEqual(before.status, PlanStatus.STALE)
        self.assertTrue(after.valid)
        self.assertEqual(after.plan.unassigned_incident_ids, ("incident-4", "incident-6"))

        diff = changes(after)
        self.assertEqual(diff["fire-1"][0], "reassigned")
        self.assertEqual(diff["fire-2"][0], "dropped")
        self.assertIn("main-street-bridge", diff["fire-2"][1])
        self.assertEqual(diff["medic-1"][0], "dropped")
        self.assertIn("main-street-bridge", diff["medic-1"][1])
        self.assertIn("higher-priority incident-1", diff["medic-1"][1])

        tradeoffs = [line for line in after.plan.rationale if "left unassigned" in line]
        self.assertTrue(any("committed to higher-priority incident-3" in line for line in tradeoffs))

        self.service.approve(after.id, after.world_version)
        self.assertEqual(after.status, PlanStatus.APPROVED)

    def test_closure_after_approval_invalidates_committed_missions(self) -> None:
        plan = self.service.current_plan
        self.service.approve(plan.id, plan.world_version)
        self.service.inject_disruption("bridge_closure")

        kinds = [event.kind for event in self.service.world.event_log]
        self.assertEqual(kinds.count("mission_invalidated"), 2)
        released = {mission.incident_id for mission in self.service.current_plan.invalidated}
        self.assertEqual(released, {"incident-3", "incident-4"})
        invalidated = [c for c in self.service.current_plan.changes if c.change == "invalidated"]
        self.assertEqual({c.service_id for c in invalidated}, {"fire-2", "utility-1", "medic-1"})

        medic = next(s for s in self.service.world.services if s.id == "medic-1")
        self.assertTrue(medic.available)
        unmet = self.service.current_plan.plan.unassigned_incident_ids
        self.assertIn("incident-4", unmet)
        self.assertIn("incident-3", unmet)  # the only reachable fire engine is already committed
        self.assertTrue(
            any("already on incident-6" in line for line in self.service.current_plan.plan.rationale)
        )


class CapacityAndPriorityTests(FloodScenarioTestCase):
    def test_rescue_report_without_head_count_needs_clarification(self) -> None:
        report = self.service.submit_report(
            "People on a roof near Shockoe Bottom",
            "caller",
            fields={
                "report_type": "incident",
                "kind": "rooftop rescue",
                "severity": "high",
                "location": "Shockoe Bottom",
                "required_services": ["rescue"],
            },
        )
        self.assertEqual(report.status, ReportStatus.NEEDS_CLARIFICATION)
        self.assertIn("people", report.missing_fields)

        report = self.service.clarify_report(report.id, {"people": 3})
        self.assertEqual(report.status, ReportStatus.READY)

    def test_capacity_shortage_is_an_explicit_unmet_need(self) -> None:
        start, end = Coordinate(0, 0), Coordinate(1, 0)
        city = City("Capacity test")
        city.add_road(Road("only-road", start, end))
        incident = Incident("big", "evacuation", end, Severity.HIGH, frozenset({"rescue"}), people=10)
        boat = EmergencyService("boat", "Boat", start, frozenset({"rescue"}), capacity=4)
        plan = Planner().plan([incident], [boat], city, world_version=1)

        self.assertEqual(plan.unassigned_incident_ids, ("big",))
        self.assertTrue(any("carry 4 of 10 people" in line for line in plan.rationale))

    def test_priority_rule_is_configurable_and_stated(self) -> None:
        world = build_flood_world()
        by_id_desc = Planner(
            priority_key=lambda incident: tuple(-ord(ch) for ch in incident.id),
            priority_rule="reverse incident id",
        )
        plan = by_id_desc.plan(list(world.incidents), list(world.services), world.city, world_version=1)

        self.assertEqual(plan.rationale[0], "Priority rule: reverse incident id.")
        self.assertEqual(plan.assignments[0].incident_id, "incident-6")


if __name__ == "__main__":
    unittest.main()
