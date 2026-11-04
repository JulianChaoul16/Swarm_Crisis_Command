"""The citizen hub: a sender follows their own report by receipt, and sees only public facts."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crisis_command.agents.model_client import UnavailableModelClient
from crisis_command.api import citizens
from crisis_command.database import connection
from crisis_command.operations import LIVE_RICHMOND_SCENARIO, CommandService

OFFLINE = UnavailableModelClient("no network in tests")


class CitizenHubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(connection, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.db.start()
        self.service = CommandService(scenario=LIVE_RICHMOND_SCENARIO, client=OFFLINE, agent_timeout=2)

    def tearDown(self) -> None:
        self.db.stop()
        self.tmp.cleanup()

    def send(self, fields: dict) -> str:
        report = self.service.submit_report("Family trapped by water on Hull St", "report page", fields=fields)
        self.service.confirm_report(report.id)
        return citizens.issue(self.service, report.id)

    def stage(self, receipt: str) -> dict:
        (status,) = citizens.reports(self.service, [receipt])
        return status

    def test_a_report_moves_from_arranging_to_dispatched_to_resolved(self) -> None:
        receipt = self.send({"report_type": "incident", "kind": "family trapped", "severity": "critical",
                             "location": "Manchester", "required_services": ["rescue"], "people": 4})
        status = self.stage(receipt)
        self.assertEqual(status["stage"], "arranging")
        self.assertEqual(status["text"], "Family trapped by water on Hull St")

        plan = self.service.current_plan
        self.service.approve(plan.id, plan.world_version)
        status = self.stage(receipt)
        self.assertEqual(status["stage"], "dispatched")
        self.assertTrue(status["units"])

        incident = self.service.reports[-1].linked_incident_id
        self.service.resolve_incident(incident, "operator", "everyone out safely")
        status = self.stage(receipt)
        self.assertEqual((status["stage"], status["step"]), ("resolved", 3))
        self.assertIn("everyone out safely", status["label"])

    def test_receipts_are_private_and_end_with_the_run(self) -> None:
        receipt = self.send({"report_type": "incident", "kind": "car stuck", "severity": "high",
                             "location": "Shockoe Bottom", "required_services": ["rescue"], "people": 1})
        self.assertEqual(citizens.reports(self.service, ["made-up-receipt"]), [])
        self.service.reset()
        self.assertEqual(self.stage(receipt)["stage"], "closed")

    def test_the_feed_shows_closed_roads_but_no_one_elses_reports(self) -> None:
        self.send({"report_type": "road_status", "road_id": "hull-street", "road_closed": True,
                   "certainty": "confirmed"})
        feed = citizens.feed(self.service)
        self.assertIn("Hull St", [road["name"] for road in feed["closed_roads"]])
        self.assertNotIn("reports", feed)
        self.assertEqual(feed["units_total"], len(self.service.world.services))


if __name__ == "__main__":
    unittest.main()
