import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crisis_command.database import connection, repository


class DatabaseRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.db"
        self.path_patcher = patch.object(connection, "DB_PATH", self.database_path)
        self.path_patcher.start()
        connection.initialize_database()

    def tearDown(self) -> None:
        self.path_patcher.stop()
        self.temp_dir.cleanup()

    def test_incident_task_and_events_share_a_consistent_schema(self) -> None:
        incident_id = repository.create_incident(
            "Flood response",
            "Water entered the lower level.",
            "high",
        )
        task_id = repository.create_task(
            incident_id,
            "research",
            "Assess affected roads.",
        )

        tasks = repository.get_tasks(incident_id)
        events = repository.get_events(incident_id)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], task_id)
        self.assertEqual(tasks[0]["status"], "pending")
        self.assertEqual([event["event_type"] for event in events], [
            "incident_created",
            "task_created",
        ])
        self.assertEqual(events[1]["task_id"], task_id)
        self.assertEqual(events[0]["message"], "Flood response")
        self.assertTrue(events[0]["occurred_at"])
        self.assertEqual([event["world_version"] for event in events], [2, 3])
        self.assertEqual(repository.get_world_version(), 3)

    def test_task_creation_for_unknown_incident_fails(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            repository.create_task(999, "research", "Investigate")
        self.assertEqual(repository.get_world_version(), 1)

    def test_initialize_database_is_repeatable(self) -> None:
        connection.initialize_database()
        self.assertEqual(repository.get_world_version(), 1)
        incident_id = repository.create_incident("Fire", "Smoke reported.", "critical")
        self.assertEqual(len(repository.get_events(incident_id)), 1)
        self.assertEqual(repository.get_world_version(), 2)

    def test_initialization_migrates_existing_events_table(self) -> None:
        self.database_path.unlink()
        conn = sqlite3.connect(self.database_path)
        try:
            conn.executescript(
                """
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    resource_id TEXT,
                    incident_id TEXT,
                    world_version INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )
        finally:
            conn.close()

        connection.initialize_database()
        incident_id = repository.create_incident("Road closure", "Bridge closed.", "medium")
        task_id = repository.create_task(incident_id, "resource", "Find detour.")

        self.assertEqual(len(repository.get_tasks(incident_id)), 1)
        events = repository.get_events(incident_id)
        self.assertEqual(events[1]["task_id"], task_id)
        self.assertEqual([event["world_version"] for event in events], [2, 3])


if __name__ == "__main__":
    unittest.main()
