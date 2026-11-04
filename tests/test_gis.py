"""Operations-map layers, history, the labelled projection, and the personnel roster."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crisis_command.agents.model_client import UnavailableModelClient
from crisis_command.api.gis import NO_FEED, GisHistory, gis_frame, roster
from crisis_command.database import connection
from crisis_command.operations import DEMO_FLOOD_SCENARIO, LIVE_RICHMOND_SCENARIO, CommandService
from crisis_command.operations.demo import DemoRunner

OFFLINE = UnavailableModelClient("no network in tests")


class GisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(connection, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.db.start()

    def tearDown(self) -> None:
        self.db.stop()
        self.tmp.cleanup()

    def played(self) -> tuple[CommandService, GisHistory]:
        service = CommandService(scenario=DEMO_FLOOD_SCENARIO, client=OFFLINE)
        demo, history = DemoRunner(service), GisHistory(service)
        service.hub.mode = "demo"
        demo.prepare()
        history.record()
        stage_one = [e for e in demo.timeline["events"] if e.get("stage", 1) == 1]
        for event in stage_one:
            getattr(demo, f"_{event['type']}")(event)
            history.record()
        demo._approve_current()
        history.record()
        return service, history

    def test_layers_never_invent_capacity(self) -> None:
        frame = gis_frame(CommandService(scenario=LIVE_RICHMOND_SCENARIO, client=OFFLINE))
        hospital = next(f for f in frame["facilities"] if f["type"] == "hospital")
        self.assertIsNone(hospital["beds_available"])
        self.assertEqual(hospital["beds_source"], NO_FEED)
        self.assertEqual(frame["zones"], [])
        self.assertTrue(all("simulated" in u["position_source"] for u in frame["units"]))

    def test_zones_people_and_outages_come_from_the_board(self) -> None:
        service, _ = self.played()
        frame = gis_frame(service)
        types = {z["type"] for z in frame["zones"]}
        self.assertEqual(types, {"flood", "fire"})
        self.assertTrue(frame["outages"])
        self.assertEqual(frame["affected"]["people_reported"],
                         sum(i.people for i in service.world.incidents if i.status.value != "resolved"))
        self.assertIn("dock-street", [r["id"] for r in frame["closed_roads"]])

    def test_history_records_changes_and_projection_is_labelled(self) -> None:
        service, history = self.played()
        self.assertGreaterEqual(len(history.history()), 3)
        projection = history.projection(gis_frame(service))
        self.assertTrue(projection["available"])
        self.assertIn("not a hydrologic forecast", projection["note"])
        self.assertEqual([f["hours"] for f in projection["frames"]], [1, 2, 3, 4])
        self.assertAlmostEqual(projection["frames"][0]["gauge_ft"], 12.4)
        self.assertTrue(all(z["projected"] for f in projection["frames"] for z in f["zones"] if z["type"] == "flood"))

    def test_no_projection_from_a_single_reading(self) -> None:
        service = CommandService(scenario=DEMO_FLOOD_SCENARIO, client=OFFLINE)
        history = GisHistory(service)
        DemoRunner(service).prepare()
        history.record()
        projection = history.projection(gis_frame(service))
        self.assertFalse(projection["available"])
        self.assertIn("needs two gauge readings", projection["reason"])

    def test_roster_lists_orders_from_approved_plans(self) -> None:
        service, _ = self.played()
        units = {u["id"]: u for u in roster(service)["units"]}
        ordered = [u for u in units.values() if u["messages"]]
        self.assertTrue(ordered)
        self.assertTrue(all(m["text"].startswith(("Proceed", "Reassigned")) for u in ordered for m in u["messages"]))


if __name__ == "__main__":
    unittest.main()
