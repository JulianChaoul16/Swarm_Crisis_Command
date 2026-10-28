"""SQLite persistence for the command service. Each call is one transaction."""

from dataclasses import asdict
import json
from typing import Any, Iterable

from ..database.connection import get_connection, initialize_database
from ..simulation import World, WorldEvent
from .plans import PlanProposal
from .reports import Report


def world_snapshot(world: World) -> dict[str, Any]:
    return {
        "version": world.version,
        "tick": world.tick_count,
        "incidents": [
            {
                "id": incident.id,
                "kind": incident.kind,
                "severity": incident.severity.name.lower(),
                "status": incident.status.value,
                "required_services": sorted(incident.required_services),
                "location": [incident.location.x, incident.location.y],
            }
            for incident in world.incidents
        ],
        "services": [
            {
                "id": service.id,
                "name": service.name,
                "capabilities": sorted(service.capabilities),
                "assigned_incident_id": service.assigned_incident_id,
                "location": [service.location.x, service.location.y],
            }
            for service in world.services
        ],
        "roads": [
            {"id": road.id, "is_closed": road.is_closed}
            for road in (world.city.roads if world.city else ())
        ],
    }


def plan_json(proposal: PlanProposal) -> str:
    plan = proposal.plan
    return json.dumps(
        {
            "assignments": [
                {
                    "incident_id": dispatch.incident_id,
                    "service_id": dispatch.service_id,
                    "estimated_arrival": dispatch.estimated_arrival,
                    "route": [[point.x, point.y] for point in dispatch.route],
                }
                for dispatch in plan.assignments
            ],
            "unassigned_incident_ids": list(plan.unassigned_incident_ids),
            "rationale": list(plan.rationale),
            "changes": [asdict(change) for change in proposal.changes],
            "agents": [
                {
                    "agent": finding.agent,
                    "status": finding.status.value,
                    "summary": finding.summary,
                    "error": finding.error,
                }
                for finding in proposal.agent_findings
            ],
        }
    )


class OperationsStore:
    def __init__(self) -> None:
        initialize_database()

    def start_run(self, run_id: str, scenario: str, started_at: str) -> None:
        conn = get_connection()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO runs (id, scenario, started_at) VALUES (?, ?, ?)",
                    (run_id, scenario, started_at),
                )
        finally:
            conn.close()

    def persist(
        self,
        run_id: str,
        world: World,
        events: Iterable[WorldEvent],
        reports: Iterable[Report] = (),
        plans: Iterable[PlanProposal] = (),
        approval: dict[str, Any] | None = None,
    ) -> None:
        conn = get_connection()
        try:
            with conn:
                for report in reports:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO reports (
                            id, run_id, content, source, received_at, occurred_at, status,
                            extracted_by, fields_json, missing_fields_json,
                            linked_incident_id, linked_road_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            report.id,
                            run_id,
                            report.content,
                            report.source,
                            report.received_at.isoformat(),
                            report.occurred_at.isoformat() if report.occurred_at else None,
                            report.status.value,
                            report.extracted_by,
                            json.dumps(asdict(report.fields)),
                            json.dumps(report.missing_fields),
                            report.linked_incident_id,
                            report.linked_road_id,
                        ),
                    )
                for proposal in plans:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO plans (
                            id, run_id, world_version, status, created_at, plan_json,
                            checks_json, decided_at, decided_by, decision_reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            proposal.id,
                            run_id,
                            proposal.world_version,
                            proposal.status.value,
                            proposal.created_at.isoformat(),
                            plan_json(proposal),
                            json.dumps([asdict(check) for check in proposal.checks]),
                            proposal.decided_at.isoformat() if proposal.decided_at else None,
                            proposal.decided_by,
                            proposal.decision_reason,
                        ),
                    )
                if approval is not None:
                    conn.execute(
                        """
                        INSERT INTO approvals (
                            plan_id, run_id, world_version, decision, actor, decided_at, reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            approval["plan_id"],
                            run_id,
                            approval["world_version"],
                            approval["decision"],
                            approval["actor"],
                            approval["decided_at"],
                            approval.get("reason"),
                        ),
                    )
                for event in events:
                    conn.execute(
                        """
                        INSERT INTO events (
                            event_type, message, world_version, occurred_at, payload_json,
                            run_id, sequence, actor, entity_ids_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.kind,
                            event.reason,
                            event.version,
                            event.recorded_at,
                            json.dumps(dict(event.details)),
                            run_id,
                            event.sequence,
                            event.actor,
                            json.dumps(event.entity_ids),
                        ),
                    )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO world_snapshots (run_id, world_version, tick, snapshot_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (run_id, world.version, world.tick_count, json.dumps(world_snapshot(world))),
                )
                conn.execute(
                    "UPDATE world_metadata SET version = ? WHERE id = 1",
                    (world.version,),
                )
        finally:
            conn.close()

    def events(self, run_id: str) -> list[dict[str, Any]]:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def snapshots(self, run_id: str) -> list[dict[str, Any]]:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT world_version, tick, snapshot_json FROM world_snapshots "
                "WHERE run_id = ? ORDER BY world_version",
                (run_id,),
            ).fetchall()
            return [
                {"version": row["world_version"], "tick": row["tick"], "world": json.loads(row["snapshot_json"])}
                for row in rows
            ]
        finally:
            conn.close()

    def runs(self) -> list[dict[str, Any]]:
        conn = get_connection()
        try:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT runs.*, COUNT(events.id) AS event_count
                    FROM runs LEFT JOIN events ON events.run_id = runs.id
                    GROUP BY runs.id ORDER BY runs.started_at DESC
                    """
                )
            ]
        finally:
            conn.close()
