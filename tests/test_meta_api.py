"""The Meta model hub: record/replay, fallbacks, local redaction and retrieval, and the
swarm and comms features built on it. No network."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crisis_command.agents.model_client import UnavailableModelClient
from crisis_command.database import connection
from crisis_command.meta_api import MUSE_GLIMMER, MUSE_SPARK, SAM, MetaHub
from crisis_command.operations import CommandService
from crisis_command.swarm.board import TRIAGE


class FakeClient:
    model_id = "fake-spark"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete_json(self, *, system, user, schema, timeout):
        self.calls.append(user)
        if self.error:
            raise self.error
        return self.reply(schema) if callable(self.reply) else self.reply


def answer(schema):
    keys = set(schema["properties"])
    if "severity_score" in keys:
        return {"severity_score": 97, "vulnerable": True, "summary": "s", "reasons": ["bus in moving water"]}
    if "tradeoff" in keys:
        return {"why": ["most urgent [Triage Agent]"], "tradeoff": "delays incident-6"}
    return {"english": "Avoid Shockoe Bottom.", "spanish": "Evite Shockoe Bottom."}


class HubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)
        self.logged = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def hub(self, client, mode="live"):
        return MetaHub(client, mode=mode, cache_dir=self.cache,
                       on_fallback=lambda model, text: self.logged.append((model, text)))

    def test_live_call_is_recorded_and_replayed_in_demo_mode(self) -> None:
        facts = {"type": "stranded bus", "people_at_risk": 4}
        live = self.hub(FakeClient(answer)).triage(facts, lambda: {})
        self.assertEqual((live.source, live.value["severity_score"]), ("live", 97))
        replay = self.hub(UnavailableModelClient("offline"), mode="demo").triage(facts, lambda: {})
        self.assertEqual((replay.source, replay.value), ("cache", live.value))

    def test_live_use_never_overwrites_a_recording_unless_re_recording(self) -> None:
        facts = {"type": "stranded bus"}
        self.hub(FakeClient(answer)).triage(facts, lambda: {})
        changed = lambda schema: {**answer(schema), "severity_score": 10}
        self.hub(FakeClient(changed)).triage(facts, lambda: {})
        replay = self.hub(UnavailableModelClient("x"), mode="demo").triage(facts, lambda: {})
        self.assertEqual(replay.value["severity_score"], 97)
        hub = self.hub(FakeClient(changed))
        hub.overwrite = True
        hub.triage(facts, lambda: {})
        self.assertEqual(self.hub(UnavailableModelClient("x"), mode="demo").triage(facts, lambda: {}).value["severity_score"], 10)

    def test_failure_uses_the_cache_and_logs_it(self) -> None:
        facts = {"type": "stranded bus"}
        self.hub(FakeClient(answer)).triage(facts, lambda: {})
        result = self.hub(FakeClient(error=RuntimeError("HTTP 500"))).triage(facts, lambda: {})
        self.assertEqual(result.source, "cache")
        self.assertIn("HTTP 500", self.logged[-1][1])

    def test_failure_without_cache_falls_back_to_rules_and_logs_it(self) -> None:
        result = self.hub(FakeClient(error=TimeoutError("slow"))).triage({"type": "x"}, lambda: {"severity_score": 50})
        self.assertEqual((result.source, result.value), ("fallback", {"severity_score": 50}))
        self.assertEqual(self.logged[-1][0], MUSE_SPARK)

    def test_redaction_runs_before_the_cloud_call(self) -> None:
        client = FakeClient(answer)
        self.hub(client).triage({"text": "My name is Ana Ruiz, call +1 804 555 0142 or ana@x.com"}, lambda: {})
        self.assertNotIn("Ana", client.calls[0])
        self.assertNotIn("555", client.calls[0])
        self.assertIn("[PHONE]", client.calls[0])

    def test_guidance_cites_the_sample_knowledge_base(self) -> None:
        result = self.hub(UnavailableModelClient("offline")).guidance("stranded vehicles rescue water")
        self.assertEqual((result.model, result.source), (MUSE_GLIMMER, "local"))
        self.assertTrue(result.value and "Sample" in result.value[0]["citation"])

    def test_status_reports_only_spark_as_connected(self) -> None:
        rows = {row["model"]: row["status"] for row in self.hub(FakeClient(answer)).status()}
        self.assertEqual(rows[MUSE_SPARK], "live")
        self.assertEqual(rows[SAM], "not connected")


class ServiceModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(connection, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.db.start()
        self.cache = patch("crisis_command.meta_api.CACHE_DIR", Path(self.tmp.name) / "cache")
        self.cache.start()

    def tearDown(self) -> None:
        self.cache.stop()
        self.db.stop()
        self.tmp.cleanup()

    def test_triage_votes_come_from_muse_spark_when_live(self) -> None:
        service = CommandService(client=FakeClient(answer))
        vote = next(iter(service.board.votes.values()))[TRIAGE]
        self.assertEqual((vote.score, vote.source), (97, "Muse Spark · live"))

    def test_offline_votes_say_rules_and_the_fallback_is_logged(self) -> None:
        service = CommandService(client=UnavailableModelClient("offline"))
        vote = next(iter(service.board.votes.values()))[TRIAGE]
        self.assertEqual(vote.source, "rules")
        self.assertTrue(any(a.source == "fallback" for a in service.board.activity))

    def test_recommendation_is_explained_and_approval_writes_a_bilingual_alert(self) -> None:
        service = CommandService(client=FakeClient(answer))
        plan = service.current_plan
        self.assertEqual(service.explanations[plan.id].value["tradeoff"], "delays incident-6")
        service.approve(plan.id, plan.world_version)
        alert = service.alerts[plan.id]
        self.assertEqual(alert["text"].value["spanish"], "Evite Shockoe Bottom.")
        self.assertIsNone(alert["graphic"].value)  # Muse Image not connected: no graphic is faked


if __name__ == "__main__":
    unittest.main()
