"""START DEMO SCENARIO end to end, offline, from the committed recordings."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crisis_command.agents.model_client import UnavailableModelClient
from crisis_command.api.serializers import serialize_state
from crisis_command.database import connection
from crisis_command.operations import DEMO_FLOOD_SCENARIO, CommandService
from crisis_command.operations.demo import DemoRunner

OFFLINE = UnavailableModelClient("no network in tests")


class DemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(connection, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.db.start()

    def tearDown(self) -> None:
        self.db.stop()
        self.tmp.cleanup()

    def run_demo(self) -> CommandService:
        service = CommandService(scenario=DEMO_FLOOD_SCENARIO, client=OFFLINE)
        demo = DemoRunner(service)
        service.hub.mode = "demo"
        demo.prepare()
        for event in demo.timeline["events"]:
            if event.get("stage", 1) == 1:
                getattr(demo, f"_{event['type']}")(event)
        return service

    def test_offline_replay_produces_the_seeded_story_with_no_fallbacks(self) -> None:
        service = self.run_demo()
        state = serialize_state(service)
        incident = next(i for i in service.world.incidents if i.id == "incident-14")
        self.assertEqual(incident.location, DEMO_FLOOD_SCENARIO.places["Shockoe Bottom"])
        self.assertIn("dock-street", [r.id for r in service.world.city.roads if r.is_closed])
        ranked = max(state["swarm"]["incidents"].items(), key=lambda kv: kv[1]["final"] or 0)
        self.assertEqual(ranked[0], "incident-14")
        via = [a["via"] for a in state["current_plan"]["assignments"]
               if a["incident_id"] == "incident-14" and a["service_id"].startswith("swift")]
        self.assertIn("14th St (Mayo) Bridge", via[0])
        self.assertNotIn("Dock St", via[0])
        self.assertEqual([a.text for a in service.board.activity if a.source == "fallback"], [])
        self.assertEqual(service.board.votes["incident-14"]["Intake Agent"].source, "Muse Spark · cache")

    def test_approving_the_demo_plan_replays_the_recorded_bilingual_alert(self) -> None:
        service = self.run_demo()
        plan = service.current_plan
        self.assertEqual(service.explanations[plan.id].source, "cache")
        service.approve(plan.id, plan.world_version)
        alert = service.alerts[plan.id]["text"]
        self.assertEqual(alert.source, "cache")
        self.assertTrue(alert.value["english"] and alert.value["spanish"])

    def test_pause_stops_the_clock_and_the_demo_continues_after_each_approval(self) -> None:
        import time
        service = CommandService(scenario=DEMO_FLOOD_SCENARIO, client=OFFLINE)
        demo = DemoRunner(service, speed=20)  # T+15s arrives after 0.75 s
        demo.start()
        demo.pause()
        time.sleep(1.2)
        status = demo.status()
        self.assertTrue(status["paused"])
        self.assertFalse(any(e["done"] for e in status["events"]))
        self.assertIn("Five incidents", status["guide"]["now"])
        demo.resume()
        time.sleep(3)
        status = demo.status()
        stage1 = [e["done"] for e in status["events"] if e["stage"] == 1]
        stage2 = [e["done"] for e in status["events"] if e["stage"] == 2]
        self.assertTrue(all(stage1) and not any(stage2))  # stage 2 waits for the operator
        self.assertTrue(status["guide"]["your_turn"] and not status["finished"])

        plan = service.current_plan
        service.approve(plan.id, plan.world_version)
        time.sleep(2.5)
        status = demo.status()
        self.assertTrue(all(e["done"] for e in status["events"]))
        self.assertTrue(status["guide"]["your_turn"])  # a freed team can take the new call
        plan = service.current_plan
        service.approve(plan.id, plan.world_version)
        status = demo.status()
        self.assertTrue(status["finished"])
        self.assertIn("full loop", status["guide"]["now"])
        demo.stop()

    def test_full_loop_replays_offline_and_the_freed_team_takes_the_new_call(self) -> None:
        service = CommandService(scenario=DEMO_FLOOD_SCENARIO, client=OFFLINE)
        demo = DemoRunner(service)
        service.hub.mode = "demo"
        demo.prepare()
        demo.play_all_now(approve=True)
        approved = [p for p in service._plans.values() if p.status.value == "approved"]
        self.assertEqual(len(approved), 2)
        last = {(d.service_id, d.incident_id) for d in approved[-1].plan.assignments}
        self.assertIn(("swift-1", "incident-15"), last)
        self.assertEqual(next(i for i in service.world.incidents if i.id == "incident-9").status.value, "resolved")
        self.assertEqual([a.text for a in service.board.activity if a.source == "fallback"], [])

    def test_photo_is_read_by_recorded_sam_and_wonder_and_labelled_honestly(self) -> None:
        state = serialize_state(self.run_demo())
        image = state["swarm"]["imagery"][0]
        self.assertEqual((image["boxes_source"], image["estimate_source"]), ("SAM 3.1 · cache", "Muse Wonder · cache"))
        self.assertTrue(image["objects"]["flooded street"] and image["map_features"])
        self.assertFalse(image["estimate"]["passable_for_cars"])
        self.assertIn("AI-generated test image", image["note"])
        self.assertIn("assumed footprint", image["note"])
        self.assertEqual(state["swarm"]["transcripts"][0]["source"], "Muse Voice Transcribe · sample")

    def test_without_recordings_the_call_is_not_invented(self) -> None:
        with patch("crisis_command.meta_api.CACHE_DIR", Path(self.tmp.name) / "empty"):
            service = self.run_demo()
        self.assertNotIn("incident-14", [i.id for i in service.world.incidents])
        self.assertTrue(any("could not be read" in a.text for a in service.board.activity))


if __name__ == "__main__":
    unittest.main()
