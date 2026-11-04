"""Live intake: the database connector, the intake API token, and the empty live world."""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from crisis_command.connectors import ConnectorRunner, SqlTableConnector
from crisis_command.operations import LIVE_RICHMOND_SCENARIO


class SqlConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "feed.db"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE incoming_reports (id INTEGER PRIMARY KEY, text TEXT, source TEXT)")
            db.execute("INSERT INTO incoming_reports (text, source) VALUES ('old report', 'cad')")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def add(self, text: str, source: str = "cad") -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO incoming_reports (text, source) VALUES (?, ?)", (text, source))

    def test_reads_only_new_rows_once_each(self) -> None:
        connector = SqlTableConnector(f"sqlite:///{self.path}", "incoming_reports")
        self.assertEqual(connector.poll(), [])  # starts after the rows already present
        self.add("Family on roof at 7th and Bainbridge")
        self.add("   ")  # blank rows are skipped
        reports = connector.poll()
        self.assertEqual([r.text for r in reports], ["Family on roof at 7th and Bainbridge"])
        self.assertEqual(reports[0].source, "cad")
        self.assertEqual(connector.poll(), [])

    def test_runner_delivers_and_reports_errors_without_stopping(self) -> None:
        good = SqlTableConnector(f"sqlite:///{self.path}", "incoming_reports")
        bad = SqlTableConnector(f"sqlite:///{self.path}", "missing_table")
        bad.name = "broken"
        delivered, errors = [], []
        runner = ConnectorRunner([bad, good], delivered.append, on_error=lambda n, e: errors.append(n))
        runner.poll_once()
        self.add("Lee Bridge underwater")
        self.assertEqual(runner.poll_once(), 1)
        self.assertEqual([r.text for r in delivered], ["Lee Bridge underwater"])
        self.assertEqual(errors[0], "broken")
        self.assertIsNotNone(runner.status["broken"]["error"])

    def test_rejects_unsafe_table_names(self) -> None:
        with self.assertRaises(ValueError):
            SqlTableConnector(f"sqlite:///{self.path}", "reports; DROP TABLE x")


class LiveModeTests(unittest.TestCase):
    def test_live_world_has_units_and_roads_but_no_incidents(self) -> None:
        world = LIVE_RICHMOND_SCENARIO.build_world()
        self.assertEqual(world.incidents, [])
        self.assertTrue(world.services and world.city.roads)
        self.assertEqual(LIVE_RICHMOND_SCENARIO.flood_sample, {})

    def test_intake_api_requires_the_token_when_set(self) -> None:
        from fastapi import HTTPException

        from crisis_command.api import server
        request = type("R", (), {"headers": {"authorization": "Bearer wrong"}})()
        payload = server.ExternalReport(text="Tree down on Main St", source="cad")
        with patch.object(server, "setting", lambda name: "secret" if name == "INTAKE_TOKEN" else ""):
            with self.assertRaises(HTTPException) as caught:
                server.intake_external(payload, request)
        self.assertEqual(caught.exception.status_code, 401)

    def test_demo_endpoints_404_when_demo_is_disabled(self) -> None:
        from fastapi import HTTPException

        from crisis_command.api import server
        with patch.object(server, "demo", None), self.assertRaises(HTTPException) as caught:
            server.start_demo()
        self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
