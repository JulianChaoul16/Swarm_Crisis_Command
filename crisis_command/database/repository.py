
from .connection import get_connection

def create_incident(title, description, severity):
    conn = get_connection()

    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO incidents
                    (title, description, severity)
                VALUES (?, ?, ?)
                """,
                (title, description, severity)
            )

            incident_id = cursor.lastrowid

            conn.execute(
                """
                INSERT INTO events
                    (incident_id, event_type, message)
                VALUES (?, ?, ?)
                """,
                (incident_id, "incident_created", title)
            )

        return incident_id
    finally:
        conn.close()


def create_task(incident_id, agent_type, description):
    conn = get_connection()

    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO tasks
                    (incident_id, agent_type, description)
                VALUES (?, ?, ?)
                """,
                (incident_id, agent_type, description)
            )

            task_id = cursor.lastrowid

            conn.execute(
                """
                INSERT INTO events
                    (incident_id, task_id, event_type, message)
                VALUES (?, ?, ?, ?)
                """,
                (
                    incident_id, task_id,
                    "task_created", description
                )
            )

        return task_id
    finally:
        conn.close()


def get_tasks(incident_id):
    conn = get_connection()

    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE incident_id = ?",
            (incident_id,)
        ).fetchall()

        return [dict(row) for row in rows]
    finally:
        conn.close()
