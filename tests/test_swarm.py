"""The shared incident board, event bus, consensus vote and swarm reactions."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crisis_command.agents.model_client import UnavailableModelClient
from crisis_command.database import connection
from crisis_command.operations import CommandService
from crisis_command.swarm.board import (
    CONSENSUS_CHANGED,
    LOGISTICS,
    TRIAGE,
    VISION,
    EventBus,
    IncidentBoard,
    agreement,
    consensus,
)

OFFLINE = UnavailableModelClient("tests run offline")


class ConsensusTests(unittest.TestCase):
    def test_median_pulled_halfway_to_the_highest_vote(self) -> None:
        board = IncidentBoard("run")
        for agent, score in ((TRIAGE, 82), (VISION, 96), (LOGISTICS, 91)):
            board.cast("incident-14", agent, score, ("x",), "rules")
        self.assertEqual(board.final("incident-14"), 94)
        self.assertEqual(agreement(board.votes["incident-14"]), 86)

    def test_no_final_until_every_agent_has_voted(self) -> None:
        board = IncidentBoard("run")
        board.cast("incident-1", TRIAGE, 90, ("x",), "rules")
        self.assertIsNone(board.final("incident-1"))
        self.assertIsNone(consensus(board.votes["incident-1"]))

    def test_one_alarmed_agent_raises_but_does_not_decide(self) -> None:
        votes = IncidentBoard("run")
        for agent, score in ((TRIAGE, 20), (VISION, 100), (LOGISTICS, 20)):
            votes.cast("i", agent, score, ("x",), "rules")
        self.assertEqual(votes.final("i"), 60)

    def test_consensus_change_is_published_and_logged(self) -> None:
        board = IncidentBoard("run")
        seen = []
        board.bus.subscribe(CONSENSUS_CHANGED, seen.append)
        for agent in (TRIAGE, VISION, LOGISTICS):
            board.cast("i", agent, 50, ("x",), "rules")
        board.cast("i", VISION, 90, ("water rising",), "rules")
        self.assertEqual([p["final"] for p in seen], [50, 70])
        self.assertTrue(any("50 -> 70" in a.text for a in board.activity))

    def test_bus_delivers_to_every_subscriber(self) -> None:
        bus, got = EventBus(), []
        bus.subscribe("t", lambda p: got.append(("a", p["x"])))
        bus.subscribe("t", lambda p: got.append(("b", p["x"])))
        bus.publish("t", {"x": 1})
        self.assertEqual(got, [("a", 1), ("b", 1)])


class SwarmServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(connection, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.db.start()
        self.service = CommandService(client=OFFLINE)

    def tearDown(self) -> None:
        self.db.stop()
        self.tmp.cleanup()

    def test_new_incident_spawns_a_swarm_and_enters_the_plan_by_consensus(self) -> None:
        report = self.service.submit_report("bus taking on water", "caller", fields={
            "report_type": "incident", "kind": "stranded bus", "severity": "critical",
            "location": "Shockoe Bottom", "required_services": ["rescue"], "people": 4})
        report = self.service.confirm_report(report.id)
        incident_id = report.linked_incident_id
        self.assertEqual(len(self.service.board.votes[incident_id]), 3)
        self.assertTrue(any(f"Spawned swarm for {incident_id}" in a.text for a in self.service.board.activity))
        self.assertIn("swarm consensus", self.service.current_plan.plan.rationale[0])

    def test_road_closure_makes_logistics_re_vote(self) -> None:
        before = {i: v[LOGISTICS].score for i, v in self.service.board.votes.items()}
        report = self.service.submit_report("bridge closed", "road authority", fields={
            "report_type": "road_status", "road_id": "main-street-bridge", "road_closed": True,
            "certainty": "confirmed"})
        self.service.confirm_report(report.id)
        after = {i: v[LOGISTICS].score for i, v in self.service.board.votes.items()}
        self.assertNotEqual(before, after)
        self.assertTrue(any("Reacting to main-street-bridge closed" in a.text for a in self.service.board.activity))

    def test_flood_cover_change_makes_vision_and_logistics_re_vote(self) -> None:
        board = self.service.board
        incident = next(i for i in self.service.world.incidents
                        if board.flood_cover.get("Shockoe Bottom") and i.id in board.votes
                        and self.service.scenario.places["Shockoe Bottom"] == i.location)
        before = board.votes[incident.id][VISION].score
        board.set_flood_cover("Shockoe Bottom", {"percent": 100, "place": "Shockoe Bottom"}, VISION, "test")
        self.assertGreater(board.votes[incident.id][VISION].score, before)

    def test_plan_order_follows_the_consensus(self) -> None:
        board = self.service.board
        low = min(board.finals, key=board.finals.get)
        for agent in (TRIAGE, VISION, LOGISTICS):
            board.cast(low, agent, 100, ("operator test",), "test")
        self.service.propose()
        ranked = [line.split()[1] for line in self.service.current_plan.plan.rationale
                  if line.startswith("Incident ") and "prioritized" in line]
        self.assertEqual(ranked[0], low)

    def test_a_re_vote_re_plans_by_itself(self) -> None:
        board = self.service.board
        before = self.service.current_plan_id
        low = min(board.finals, key=board.finals.get)
        for agent in (TRIAGE, VISION, LOGISTICS):
            board.cast(low, agent, 100, ("operator test",), "test")
        self.assertNotEqual(self.service.current_plan_id, before)
        ranked = [line.split()[1] for line in self.service.current_plan.plan.rationale
                  if line.startswith("Incident ") and "prioritized" in line]
        self.assertEqual(ranked[0], low)

    def test_reset_starts_a_fresh_board(self) -> None:
        old = self.service.board
        self.service.reset()
        self.assertIsNot(self.service.board, old)
        self.assertEqual(self.service.board.run_id, self.service.run_id)


if __name__ == "__main__":
    unittest.main()
