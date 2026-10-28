import json
from datetime import datetime, timezone
import sqlite3
from typing import Any

from .connection import get_connection


def _advance_world_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO world_metadata (id, version) VALUES (1, 1)"
    )
    conn.execute("UPDATE world_metadata SET version = version + 1 WHERE id = 1")
    row = conn.execute(
        "SELECT version FROM world_metadata WHERE id = 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("World version metadata could not be initialized.")
    return row["version"]


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} cannot be empty.")


def _event_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_incident(title: str, description: str, severity: str) -> int:
    _require_text(title, "Incident title")
    _require_text(description, "Incident description")
    _require_text(severity, "Incident severity")

    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO incidents (title, description, severity)
                VALUES (?, ?, ?)
                """,
                (title, description, severity),
            )
            incident_id = cursor.lastrowid
            world_version = _advance_world_version(conn)
            conn.execute(
                """
                INSERT INTO events
                    (
                        incident_id, event_type, message, world_version,
                        occurred_at, payload_json
                    )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    "incident_created",
                    title,
                    world_version,
                    _event_timestamp(),
                    json.dumps({"severity": severity, "description": description}),
                ),
            )
        return incident_id
    finally:
        conn.close()


def create_task(incident_id: int, agent_type: str, description: str) -> int:
    if incident_id <= 0:
        raise ValueError("Incident id must be greater than zero.")
    _require_text(agent_type, "Agent type")
    _require_text(description, "Task description")

    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO tasks (incident_id, agent_type, description)
                VALUES (?, ?, ?)
                """,
                (incident_id, agent_type, description),
            )
            task_id = cursor.lastrowid
            world_version = _advance_world_version(conn)
            conn.execute(
                """
                INSERT INTO events
                    (
                        incident_id, task_id, event_type, message,
                        world_version, occurred_at, payload_json
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    task_id,
                    "task_created",
                    description,
                    world_version,
                    _event_timestamp(),
                    json.dumps({"agent_type": agent_type}),
                ),
            )
        return task_id
    finally:
        conn.close()


def get_tasks(incident_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE incident_id = ? ORDER BY id",
            (incident_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_events(incident_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE incident_id = ? ORDER BY id",
            (incident_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_world_version() -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT version FROM world_metadata WHERE id = 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("World version metadata is not initialized.")
        return row["version"]
    finally:
        conn.close()
