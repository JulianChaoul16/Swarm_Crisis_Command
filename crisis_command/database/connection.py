
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "crisis_command.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database() -> None:
    conn = get_connection()

    try:
        _migrate_events_table(conn)
        with conn:
            conn.executescript(SCHEMA_PATH.read_text())
    finally:
        conn.close()


def _migrate_events_table(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(events)").fetchall()
    }
    if not columns:
        return
    if "message" not in columns:
        conn.execute(
            "ALTER TABLE events ADD COLUMN message TEXT NOT NULL DEFAULT ''"
        )
    if "task_id" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN task_id INTEGER")
