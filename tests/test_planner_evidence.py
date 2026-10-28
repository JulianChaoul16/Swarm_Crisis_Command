import json
import unittest
from datetime import datetime, timezone

from crisis_command.model import (
    City,
    Coordinate,
    EmergencyService,
    Incident,
    Road,
    Severity,
)
from crisis_command.coordinator import Coordinator, UnserviceableIncidentsError
from crisis_command.planner import (
    Claim,
    EvidenceArbitrator,
    Planner,
    VerificationStatus,
)
from crisis_command.simulation import Simulation, World


class PlannerEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = Coordinate(0, 0)
        self.end = Coordinate(5, 0)
        self.city = City("Evidence City")
        self.city.add_road(Road("bridge", self.start, self.end))
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
        )
        self.timestamp = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)

    def make_claim(
        self,
        *,
        source: str,
        value: bool,
        confidence: float,
        evidence_reference: str,
        timestamp: datetime | None = None,
        world_version: int = 1,
        verification_status: VerificationStatus = VerificationStatus.VERIFIED,
    ) -> Claim:
        return Claim(
            subject="bridge",
            attribute="is_closed",
            value=value,
            source=source,
            timestamp=timestamp or self.timestamp,
            world_version=world_version,
            verification_status=verification_status,
            confidence=confidence,
            evidence_reference=evidence_reference,
        )

    def test_source_reliability_breaks_conflict_before_recency(self) -> None:
        older_reliable_report = self.make_claim(
            source="road_authority",
            value=False,
            confidence=0.8,
            evidence_reference="sensor-record-17",
        )
        newer_agent_report = self.make_claim(
            source="field_agent",
            value=True,
            confidence=0.8,
            evidence_reference="agent-observation-21",
            timestamp=datetime(2026, 10, 27, 13, tzinfo=timezone.utc),
        )
        planner = Planner(
            EvidenceArbitrator(
                {
                    "city_network": 0.1,
                    "road_authority": 0.95,
                    "field_agent": 0.7,
                }
            )
        )

        plan = planner.plan(
            [self.incident],
            [self.service],
            self.city,
            world_version=1,
            claims=(older_reliable_report, newer_agent_report),
        )

        resolution = plan.claim_resolutions[0]
        self.assertFalse(resolution.disputed)
        self.assertEqual(resolution.selected_claim, older_reliable_report)
        self.assertTrue(plan.assignments)
        self.assertTrue(any("sensor-record-17" in text for text in plan.rationale))

    def test_confident_verified_road_claim_can_update_network_status(self) -> None:
        closed_report = self.make_claim(
            source="field_agent",
            value=True,
            confidence=0.9,
            evidence_reference="bridge-closure-photo",
        )

        plan = Planner().plan(
            [self.incident],
            [self.service],
            self.city,
            world_version=1,
            claims=(closed_report,),
        )

        self.assertEqual(
            plan.claim_resolutions[0].selected_claim,
            closed_report,
        )
        self.assertFalse(plan.assignments)
        self.assertEqual(plan.unassigned_incident_ids, (self.incident.id,))

    def test_tied_conflicting_road_claims_are_conservatively_closed(self) -> None:
        claims = (
            self.make_claim(
                source="agent_a",
                value=False,
                confidence=0.8,
                evidence_reference="observation-a",
            ),
            self.make_claim(
                source="agent_b",
                value=True,
                confidence=0.8,
                evidence_reference="observation-b",
            ),
        )

        plan = Planner(
            EvidenceArbitrator(
                {"city_network": 0.1, "agent_a": 0.7, "agent_b": 0.7}
            )
        ).plan(
            [self.incident],
            [self.service],
            self.city,
            world_version=1,
            claims=claims,
        )

        self.assertTrue(plan.claim_resolutions[0].disputed)
        self.assertFalse(plan.assignments)
        self.assertEqual(plan.unassigned_incident_ids, (self.incident.id,))

    def test_unverified_conflict_is_flagged_even_when_verified_claim_wins(self) -> None:
        verified_open = self.make_claim(
            source="verified_source",
            value=False,
            confidence=0.4,
            evidence_reference="verified-observation",
        )
        unverified_closed = self.make_claim(
            source="highly_reliable_but_unverified",
            value=True,
            confidence=1.0,
            evidence_reference="unverified-observation",
            verification_status=VerificationStatus.UNVERIFIED,
        )
        planner = Planner(
            EvidenceArbitrator(
                {
                    "city_network": 0.1,
                    "verified_source": 0.2,
                    "highly_reliable_but_unverified": 1.0,
                }
            )
        )

        plan = planner.plan(
            [self.incident],
            [self.service],
            self.city,
            world_version=1,
            claims=(verified_open, unverified_closed),
        )

        self.assertEqual(plan.claim_resolutions[0].selected_claim, verified_open)
        self.assertTrue(plan.claim_resolutions[0].requires_review)
        self.assertTrue(plan.claim_resolutions[0].status_unknown)
        self.assertFalse(plan.assignments)
        self.assertEqual(plan.unassigned_incident_ids, (self.incident.id,))

    def test_claim_from_older_world_cannot_override_current_road_state(self) -> None:
        self.city.set_road_closed("bridge", True)
        old_open_report = self.make_claim(
            source="road_authority",
            value=False,
            confidence=1.0,
            evidence_reference="old-open-report",
        )

        plan = Planner().plan(
            [self.incident],
            [self.service],
            self.city,
            world_version=2,
            claims=(old_open_report,),
        )

        self.assertFalse(plan.assignments)
        self.assertTrue(
            any("old-open-report" in rationale for rationale in plan.rationale)
        )

    def test_verified_authoritative_closure_overrides_stale_road_snapshot(self) -> None:
        authoritative_closure = self.make_claim(
            source="road_authority",
            value=True,
            confidence=0.7,
            evidence_reference="authority-closure-order",
            world_version=1,
        )

        plan = Planner().plan(
            [self.incident],
            [self.service],
            self.city,
            world_version=2,
            claims=(authoritative_closure,),
        )

        self.assertEqual(
            plan.claim_resolutions[0].selected_claim,
            authoritative_closure,
        )
        self.assertFalse(plan.assignments)
        self.assertEqual(plan.unassigned_incident_ids, (self.incident.id,))

    def test_newer_authoritative_open_report_supersedes_stale_closure(self) -> None:
        old_closure = self.make_claim(
            source="road_authority",
            value=True,
            confidence=0.9,
            evidence_reference="old-closure-order",
            world_version=1,
        )
        current_open_report = self.make_claim(
            source="road_authority",
            value=False,
            confidence=0.9,
            evidence_reference="current-reopening-order",
            world_version=2,
        )

        plan = Planner().plan(
            [self.incident],
            [self.service],
            self.city,
            world_version=2,
            claims=(old_closure, current_open_report),
        )

        self.assertEqual(
            plan.claim_resolutions[0].selected_claim,
            current_open_report,
        )
        self.assertTrue(plan.assignments)

    def test_unverified_road_conflict_triggers_review_and_blocks_road(self) -> None:
        unverified_closure = self.make_claim(
            source="field_agent",
            value=True,
            confidence=1.0,
            evidence_reference="unverified-closure-report",
            verification_status=VerificationStatus.UNVERIFIED,
        )

        plan = Planner().plan(
            [self.incident],
            [self.service],
            self.city,
            world_version=1,
            claims=(unverified_closure,),
        )

        resolution = plan.claim_resolutions[0]
        self.assertTrue(resolution.requires_review)
        self.assertTrue(resolution.status_unknown)
        self.assertEqual(plan.unknown_road_ids, ("bridge",))
        self.assertFalse(plan.assignments)

    def test_unknown_road_status_blocks_safety_critical_route(self) -> None:
        unknown_status = Claim(
            subject="bridge",
            attribute="is_closed",
            value=None,
            source="road_authority",
            timestamp=datetime(2026, 10, 27, 14, tzinfo=timezone.utc),
            world_version=1,
            verification_status=VerificationStatus.VERIFIED,
            confidence=1.0,
            evidence_reference="status-unavailable",
        )

        plan = Planner().plan(
            [self.incident],
            [self.service],
            self.city,
            world_version=1,
            claims=(unknown_status,),
        )

        self.assertEqual(plan.unknown_road_ids, ("bridge",))
        self.assertFalse(plan.assignments)
        self.assertEqual(plan.unassigned_incident_ids, (self.incident.id,))

    def test_safety_critical_incident_requires_a_road_network(self) -> None:
        plan = Planner().plan(
            [self.incident],
            [self.service],
            world_version=1,
        )

        self.assertFalse(plan.assignments)
        self.assertEqual(plan.unassigned_incident_ids, (self.incident.id,))
        self.assertTrue(any("road status is unknown" in text for text in plan.rationale))

    def test_unknown_road_claim_is_rejected(self) -> None:
        claim = Claim(
            subject="missing-road",
            attribute="is_closed",
            value=True,
            source="field_agent",
            timestamp=self.timestamp,
            world_version=1,
            verification_status=VerificationStatus.VERIFIED,
            confidence=0.9,
            evidence_reference="unknown-road-report",
        )

        with self.assertRaisesRegex(ValueError, "unknown roads"):
            Planner().plan(
                [self.incident],
                [self.service],
                self.city,
                world_version=1,
                claims=(claim,),
            )

    def test_plan_decision_event_preserves_claim_provenance(self) -> None:
        claim = self.make_claim(
            source="road_authority",
            value=False,
            confidence=0.91,
            evidence_reference="sensor-record-42",
        )
        world = World([self.incident], [self.service], city=self.city)

        planner = Planner(
            EvidenceArbitrator({"city_network": 0.1, "road_authority": 0.9})
        )
        Simulation(world, planner).step(claims=(claim,))

        decision = next(
            event for event in world.event_log if event.kind == "plan_decision"
        )
        details = dict(decision.details)
        recorded_claims = json.loads(details["claims"])
        self.assertEqual(recorded_claims[0]["source"], "road_authority")
        self.assertEqual(recorded_claims[0]["evidence_reference"], "sensor-record-42")
        self.assertEqual(recorded_claims[0]["verification_status"], "verified")
        self.assertEqual(recorded_claims[0]["confidence"], 0.91)
        self.assertEqual(recorded_claims[0]["world_version"], 1)

    def test_blocked_decision_records_supporting_evidence(self) -> None:
        claim = Claim(
            subject="bridge",
            attribute="is_closed",
            value=None,
            source="road_authority",
            timestamp=datetime(2026, 10, 27, 14, tzinfo=timezone.utc),
            world_version=1,
            verification_status=VerificationStatus.VERIFIED,
            confidence=1.0,
            evidence_reference="missing-road-status-report",
        )
        world = World([self.incident], [self.service], city=self.city)

        with self.assertRaises(UnserviceableIncidentsError):
            Coordinator(world).step(claims=(claim,))

        blocked_event = next(
            event for event in world.event_log if event.kind == "plan_blocked"
        )
        details = dict(blocked_event.details)
        recorded_claims = json.loads(details["claims"])
        self.assertEqual(
            recorded_claims[0]["evidence_reference"],
            "missing-road-status-report",
        )
        self.assertEqual(json.loads(details["unknown_road_ids"]), ["bridge"])

    def test_future_version_claim_is_rejected(self) -> None:
        claim = self.make_claim(
            source="agent",
            value=True,
            confidence=0.5,
            evidence_reference="future-observation",
            world_version=2,
        )

        with self.assertRaises(ValueError):
            Planner().plan(
                [self.incident],
                [self.service],
                self.city,
                world_version=1,
                claims=(claim,),
            )

    def test_claim_requires_timezone_and_valid_confidence(self) -> None:
        with self.assertRaises(ValueError):
            self.make_claim(
                source="agent",
                value=False,
                confidence=1.1,
                evidence_reference="bad-confidence",
            )

        with self.assertRaises(ValueError):
            self.make_claim(
                source="agent",
                value=False,
                confidence=0.5,
                evidence_reference="naive-time",
                timestamp=datetime(2026, 10, 27, 12),
            )


if __name__ == "__main__":
    unittest.main()
