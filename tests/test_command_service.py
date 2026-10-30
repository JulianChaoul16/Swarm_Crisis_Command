import sqlite3
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from crisis_command.agents.model_client import UnavailableModelClient
from crisis_command.agents.specialists import AgentStatus, run_specialists
from crisis_command.database import connection
from crisis_command.model import IncidentStatus
from crisis_command.operations import (
    DEMO_SCENARIO,
    CommandService,
    ConflictError,
    PlanStatus,
    ReportStatus,
    StaleApprovalError,
)
from crisis_command.operations.feasibility import check_plan

# Tests never call a live model, even when .env holds a key.
OFFLINE = UnavailableModelClient("No model configured (tests run offline).")

INCIDENT_FIELDS = {
    "report_type": "incident",
    "kind": "flooded basement",
    "severity": "high",
    "location": "Junction C",
    "required_services": ["medical"],
}


class CommandServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.db"
        self.path_patcher = patch.object(connection, "DB_PATH", self.database_path)
        self.path_patcher.start()
        self.service = CommandService(scenario=DEMO_SCENARIO, client=OFFLINE, agent_timeout=2)

    def tearDown(self) -> None:
        self.path_patcher.stop()
        self.temp_dir.cleanup()

    def query(self, sql: str, *params) -> list[tuple]:
        conn = sqlite3.connect(self.database_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


class IntakeTests(CommandServiceTestCase):
    def test_report_without_model_is_flagged_not_guessed(self) -> None:
        report = self.service.submit_report("Water rising at Junction C", "911 caller")

        self.assertEqual(report.status, ReportStatus.NEEDS_EXTRACTION)
        self.assertIsNone(report.extracted_by)
        self.assertIn("No model configured", report.extraction_error)
        self.assertEqual(report.fields.kind, None)

    def test_missing_fields_block_confirmation_until_clarified(self) -> None:
        fields = {key: value for key, value in INCIDENT_FIELDS.items() if key != "required_services"}
        report = self.service.submit_report("Basement flooding", "patrol", fields=fields)

        self.assertEqual(report.status, ReportStatus.NEEDS_CLARIFICATION)
        self.assertEqual(report.missing_fields, ("required_services",))
        with self.assertRaises(ConflictError):
            self.service.confirm_report(report.id)

        report = self.service.clarify_report(report.id, {"required_services": ["medical"]})
        self.assertEqual(report.status, ReportStatus.READY)

    def test_unknown_location_and_capability_are_validation_errors(self) -> None:
        report = self.service.submit_report(
            "Fire at the mall",
            "caller",
            fields={**INCIDENT_FIELDS, "location": "Nowhere Mall", "required_services": ["hazmat"]},
        )

        self.assertEqual(report.status, ReportStatus.NEEDS_CLARIFICATION)
        self.assertEqual(len(report.validation_errors), 2)

    def test_confirmed_report_creates_incident_and_triggers_replanning(self) -> None:
        before = self.service.current_plan
        report = self.service.submit_report("Basement flooding", "patrol", fields=INCIDENT_FIELDS)
        report = self.service.confirm_report(report.id)

        incident = next(i for i in self.service.world.incidents if i.id == report.linked_incident_id)
        self.assertEqual(incident.kind, "flooded basement")
        self.assertEqual(before.status, PlanStatus.STALE)
        self.assertNotEqual(self.service.current_plan.id, before.id)
        self.assertEqual(self.service.current_plan.world_version, self.service.world.version)

    def test_possible_road_closure_needs_explicit_confirmation(self) -> None:
        report = self.service.submit_report(
            "Someone said connector 1 might be under water",
            "social media",
            fields={"report_type": "road_status", "road_id": "connector-1", "road_closed": True, "certainty": "possible"},
        )
        road = next(r for r in self.service.world.city.roads if r.id == "connector-1")

        self.assertEqual(report.status, ReportStatus.READY)
        self.assertTrue(report.warnings)
        self.assertFalse(road.is_closed)

        self.service.confirm_report(report.id)
        road = next(r for r in self.service.world.city.roads if r.id == "connector-1")
        self.assertTrue(road.is_closed)

    def test_rejected_report_changes_nothing(self) -> None:
        version = self.service.world.version
        report = self.service.submit_report("Hoax call", "caller", fields=INCIDENT_FIELDS)
        report = self.service.reject_report(report.id, "Duplicate of an existing call.")

        self.assertEqual(report.status, ReportStatus.REJECTED)
        self.assertEqual(self.service.world.version, version)


class ApprovalTests(CommandServiceTestCase):
    def test_initial_proposal_is_reviewable_and_nothing_is_dispatched(self) -> None:
        plan = self.service.current_plan

        self.assertEqual(plan.status, PlanStatus.PROPOSED)
        self.assertTrue(plan.plan.assignments)
        self.assertTrue(all(check.passed for check in plan.checks))
        self.assertTrue(all(service.available for service in self.service.world.services))

    def test_unavailable_model_is_never_reported_as_completed_analysis(self) -> None:
        findings = self.service.current_plan.agent_findings

        self.assertEqual({f.agent for f in findings}, {"Situation", "Resources", "Routing"})
        self.assertTrue(all(f.status == AgentStatus.UNAVAILABLE for f in findings))
        self.assertTrue(all(f.summary is None and f.tool_results for f in findings))

    def test_approval_commits_dispatches_and_persists_atomically(self) -> None:
        plan = self.service.current_plan
        self.service.approve(plan.id, plan.world_version)

        assigned = {
            service.id: service.assigned_incident_id
            for service in self.service.world.services
            if not service.available
        }
        self.assertEqual(
            assigned,
            {dispatch.service_id: dispatch.incident_id for dispatch in plan.plan.assignments},
        )
        self.assertEqual(plan.status, PlanStatus.APPROVED)
        self.assertEqual(
            self.query("SELECT plan_id, decision, world_version FROM approvals"),
            [(plan.id, "approved", plan.world_version)],
        )
        persisted = self.query(
            "SELECT COUNT(*) FROM events WHERE run_id = ?", self.service.run_id
        )[0][0]
        self.assertEqual(persisted, len(self.service.world.event_log))

    def test_proposal_after_approval_does_not_report_committed_missions_as_dropped(self) -> None:
        plan = self.service.current_plan
        self.service.approve(plan.id, plan.world_version)

        follow_up = self.service.current_plan
        self.assertNotEqual(follow_up.id, plan.id)
        self.assertFalse([c for c in follow_up.changes if c.change == "dropped"])

    def test_approval_must_name_the_plans_world_version(self) -> None:
        plan = self.service.current_plan
        with self.assertRaises(ConflictError):
            self.service.approve(plan.id, plan.world_version + 1)
        self.assertTrue(all(service.available for service in self.service.world.services))

    def test_superseded_plan_cannot_be_approved(self) -> None:
        old = self.service.current_plan
        self.service.advance_clock()  # tick changes the world version and recomputes

        with self.assertRaises(StaleApprovalError) as raised:
            self.service.approve(old.id, old.world_version)
        self.assertEqual(raised.exception.replacement_plan_id, self.service.current_plan.id)
        self.assertTrue(all(service.available for service in self.service.world.services))

    def test_world_change_after_proposal_blocks_approval_and_recomputes(self) -> None:
        plan = self.service.current_plan
        # A change that bypasses the service, e.g. one landing between review and approval.
        self.service.world.city.set_road_closed("collision-road", True, "Test change.")

        with self.assertRaises(StaleApprovalError) as raised:
            self.service.approve(plan.id, plan.world_version)

        self.assertEqual(plan.status, PlanStatus.STALE)
        replacement = self.service.current_plan
        self.assertEqual(replacement.id, raised.exception.replacement_plan_id)
        self.assertEqual(replacement.world_version, self.service.world.version)
        self.assertTrue(all(service.available for service in self.service.world.services))

    def test_failed_persistence_rolls_back_the_commit(self) -> None:
        plan = self.service.current_plan
        version = self.service.world.version
        event_count = len(self.service.world.event_log)

        with patch.object(self.service.store, "persist", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.service.approve(plan.id, plan.world_version)

        self.assertEqual(self.service.world.version, version)
        self.assertEqual(len(self.service.world.event_log), event_count)
        self.assertEqual(self.service.current_plan.status, PlanStatus.PROPOSED)
        self.assertTrue(all(service.available for service in self.service.world.services))
        self.assertTrue(
            all(i.status == IncidentStatus.OPEN for i in self.service.world.incidents)
        )

    def test_rejected_plan_is_recorded_and_not_executed(self) -> None:
        plan = self.service.current_plan
        self.service.reject_plan(plan.id, "Hold units for a briefing.")

        self.assertEqual(plan.status, PlanStatus.REJECTED)
        self.assertTrue(all(service.available for service in self.service.world.services))
        self.assertEqual(
            self.query("SELECT decision, reason FROM approvals"),
            [("rejected", "Hold units for a briefing.")],
        )
        with self.assertRaises(ConflictError):
            self.service.approve(plan.id, plan.world_version)

    def test_reset_during_planning_discards_the_old_runs_result(self) -> None:
        old_run = self.service.run_id
        original_plan = self.service.planner.plan

        def plan_then_reset(*args, **kwargs):
            result = original_plan(*args, **kwargs)
            self.service.reset()
            return result

        with patch.object(self.service.planner, "plan", side_effect=plan_then_reset):
            self.assertIsNone(self.service.propose())

        self.assertNotEqual(self.service.run_id, old_run)
        self.assertEqual(self.service.current_plan.run_id, self.service.run_id)
        self.assertEqual(
            [e.kind for e in self.service.world.event_log if e.kind == "plan_proposed"],
            ["plan_proposed"],
        )


class FakeClient:
    def __init__(self, behaviour):
        self.model_id = "fake-model"
        self.behaviour = behaviour

    def complete_json(self, *, system, user, schema, timeout):
        return self.behaviour()


class SpecialistTests(CommandServiceTestCase):
    def snapshot(self):
        return self.service.world.snapshot()

    def test_valid_model_output_is_completed(self) -> None:
        client = FakeClient(lambda: {"summary": "ok", "findings": ["a"], "missing_information": []})
        findings = run_specialists(self.snapshot(), client, timeout=1)
        self.assertTrue(all(f.status == AgentStatus.COMPLETED for f in findings))

    def test_invalid_output_and_errors_are_failures(self) -> None:
        findings = run_specialists(self.snapshot(), FakeClient(lambda: {"summary": 3}), timeout=1)
        self.assertTrue(all(f.status == AgentStatus.FAILED for f in findings))

        def boom():
            raise RuntimeError("HTTP 500")

        findings = run_specialists(self.snapshot(), FakeClient(boom), timeout=1)
        self.assertTrue(all(f.status == AgentStatus.FAILED and "HTTP 500" in f.error for f in findings))

    def test_slow_specialist_is_reported_as_timed_out(self) -> None:
        def slow():
            time.sleep(3)
            return {"summary": "late", "findings": [], "missing_information": []}

        findings = run_specialists(self.snapshot(), FakeClient(slow), timeout=0.2)
        self.assertTrue(all(f.status == AgentStatus.TIMED_OUT for f in findings))

    def test_unavailable_client(self) -> None:
        findings = run_specialists(self.snapshot(), UnavailableModelClient("no key"), timeout=1)
        self.assertTrue(all(f.status == AgentStatus.UNAVAILABLE for f in findings))


class FeasibilityTests(CommandServiceTestCase):
    def test_closed_road_and_double_booking_fail_checks(self) -> None:
        plan = self.service.current_plan.plan
        world = self.service.world
        first = plan.assignments[0]
        doubled = replace(plan, assignments=plan.assignments + (first,))
        for road in world.city.roads:
            world.city.set_road_closed(road.id, True, "Test closure.")

        failed = {check.rule for check in check_plan(doubled, world.incidents, world.services, world.city) if not check.passed}

        self.assertIn("no_double_booking", failed)
        self.assertIn("routes_open", failed)


if __name__ == "__main__":
    unittest.main()
