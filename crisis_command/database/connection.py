
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "crisis_command.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    return conn

def initialize_database():
    conn = get_connection()

    try:
        with conn:
            conn.executescript(SCHEMA_PATH.read_text())
    finally:
        conn.close()
