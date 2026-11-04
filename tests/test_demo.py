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
        demo.play_all_now()
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
        self.assertEqual(service.board.votes["incident-14"]["Triage Agent"].source, "Muse Spark · cache")

    def test_approving_the_demo_plan_replays_the_recorded_bilingual_alert(self) -> None:
        service = self.run_demo()
        plan = service.current_plan
        self.assertEqual(service.explanations[plan.id].source, "cache")
        service.approve(plan.id, plan.world_version)
        alert = service.alerts[plan.id]["text"]
        self.assertEqual(alert.source, "cache")
        self.assertTrue(alert.value["english"] and alert.value["spanish"])

    def test_pause_stops_the_clock_and_resume_continues(self) -> None:
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
        self.assertTrue(all(e["done"] for e in status["events"]))
        self.assertTrue(status["finished"] and status["guide"]["your_turn"])
        demo.stop()

    def test_sample_imagery_and_transcript_are_labelled_as_samples(self) -> None:
        state = serialize_state(self.run_demo())
        image = state["swarm"]["imagery"][0]
        self.assertEqual((image["mask_source"], image["estimate_source"]), ("SAM 3.1 · sample", "Muse Wonder · sample"))
        self.assertIn("Not SAM 3.1 output", image["mask"]["properties"]["note"])
        self.assertEqual(state["swarm"]["transcripts"][0]["source"], "Muse Voice Transcribe · sample")

    def test_without_recordings_the_call_is_not_invented(self) -> None:
        with patch("crisis_command.meta_api.CACHE_DIR", Path(self.tmp.name) / "empty"):
            service = self.run_demo()
        self.assertNotIn("incident-14", [i.id for i in service.world.incidents])
        self.assertTrue(any("could not be read" in a.text for a in service.board.activity))


if __name__ == "__main__":
    unittest.main()
