"""Situation, Resources, and Routing specialists.

Each specialist runs deterministic tools over an immutable world snapshot, then asks the
model to interpret those tool results. Tool results are software output and are always
reported; model analysis is reported only when a model call actually succeeds. Agents
return findings; they never write world state.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from time import perf_counter
from typing import Any

import networkx as nx

from ..model import IncidentStatus
from ..planner import DEFAULT_PRIORITY_RULE, default_priority
from .model_client import ModelClient, ModelUnavailableError

DEFAULT_AGENT_TIMEOUT_SECONDS = 20.0
# Backstop for clients that ignore their own timeout argument.
AGENT_TIMEOUT_GRACE_SECONDS = 0.5

FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "missing_information": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "findings", "missing_information"],
    "additionalProperties": False,
}


class AgentStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ToolResult:
    name: str
    output: dict[str, Any]


@dataclass(frozen=True)
class AgentFinding:
    agent: str
    status: AgentStatus
    tool_results: tuple[ToolResult, ...]
    summary: str | None = None
    findings: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()
    error: str | None = None
    model_id: str | None = None
    latency_ms: int = 0
    finished_at: str = ""


def _open_incidents(snapshot) -> list:
    return sorted(
        (incident for incident in snapshot.incidents if incident.status != IncidentStatus.RESOLVED),
        key=default_priority,
    )


def situation_tools(snapshot) -> tuple[ToolResult, ...]:
    return (
        ToolResult(
            "incident_overview",
            {
                "world_version": snapshot.version,
                "priority_rule": DEFAULT_PRIORITY_RULE,
                "incidents": [
                    {
                        "id": incident.id,
                        "kind": incident.kind,
                        "severity": incident.severity.name.lower(),
                        "status": incident.status.value,
                        "required_services": sorted(incident.required_services),
                        "people": incident.people,
                    }
                    for incident in _open_incidents(snapshot)
                ],
            },
        ),
    )


def resources_tools(snapshot) -> tuple[ToolResult, ...]:
    available = [service for service in snapshot.services if service.available]
    coverage = []
    for incident in _open_incidents(snapshot):
        if incident.status != IncidentStatus.OPEN:
            continue
        missing = sorted(
            capability
            for capability in incident.required_services
            if not any(capability in service.capabilities for service in available)
        )
        # Seats matter as much as capabilities: an incident is only served if everyone fits.
        seats = sum(service.capacity for service in available)
        coverage.append({
            "incident_id": incident.id,
            "uncovered_capabilities": missing,
            "people": incident.people,
            "available_seats_all_units": seats,
            "seat_shortfall": max(0, incident.people - seats),
        })
    return (
        ToolResult(
            "resource_inventory",
            {
                "services": [
                    {
                        "id": service.id,
                        "capabilities": sorted(service.capabilities),
                        "capacity": service.capacity,
                        "available": service.available,
                        "assigned_incident_id": service.assigned_incident_id,
                    }
                    for service in snapshot.services
                ],
            },
        ),
        ToolResult("capability_coverage", {"open_incidents": coverage}),
    )


def routing_tools(snapshot) -> tuple[ToolResult, ...]:
    city = snapshot.city
    closed = sorted(road.id for road in city.roads if road.is_closed) if city else []
    reachability = []
    for incident in _open_incidents(snapshot):
        for service in snapshot.services:
            if not service.available and service.assigned_incident_id != incident.id:
                continue
            if city is None:
                reachability.append(
                    {"service_id": service.id, "incident_id": incident.id, "reachable": None}
                )
                continue
            try:
                distance = city.route_distance(service.location, incident.location)
                reachability.append(
                    {
                        "service_id": service.id,
                        "incident_id": incident.id,
                        "reachable": True,
                        "distance": round(distance, 3),
                    }
                )
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                reachability.append(
                    {"service_id": service.id, "incident_id": incident.id, "reachable": False}
                )
    return (
        ToolResult("closed_roads", {"road_ids": closed}),
        ToolResult("reachability", {"pairs": reachability}),
    )


@dataclass(frozen=True)
class Specialist:
    name: str
    role: str
    tools: Any

    def run(self, snapshot, client: ModelClient, timeout: float) -> AgentFinding:
        started = perf_counter()
        tool_results = self.tools(snapshot)

        def finish(status: AgentStatus, **fields: Any) -> AgentFinding:
            return AgentFinding(
                agent=self.name,
                status=status,
                tool_results=tool_results,
                model_id=client.model_id,
                latency_ms=round((perf_counter() - started) * 1000),
                finished_at=datetime.now(timezone.utc).isoformat(),
                **fields,
            )

        try:
            output = client.complete_json(
                system=(
                    f"You are the {self.name} specialist in a simulated emergency-coordination "
                    f"exercise. {self.role} Use only the provided tool results. Do not invent "
                    "resources, roads, or routes. Flag missing information explicitly."
                ),
                user=json.dumps({r.name: r.output for r in tool_results}),
                schema=FINDINGS_SCHEMA,
                timeout=timeout,
            )
        except ModelUnavailableError as error:
            return finish(AgentStatus.UNAVAILABLE, error=str(error))
        except TimeoutError as error:
            return finish(AgentStatus.TIMED_OUT, error=str(error) or "Model call timed out.")
        except Exception as error:  # noqa: BLE001 - any model/tool failure is reported, not hidden
            return finish(AgentStatus.FAILED, error=f"{type(error).__name__}: {error}")

        if (
            not isinstance(output.get("summary"), str)
            or not isinstance(output.get("findings"), list)
            or not isinstance(output.get("missing_information"), list)
        ):
            return finish(AgentStatus.FAILED, error="Model output did not match the findings schema.")
        return finish(
            AgentStatus.COMPLETED,
            summary=output["summary"],
            findings=tuple(str(item) for item in output["findings"]),
            missing_information=tuple(str(item) for item in output["missing_information"]),
        )


SPECIALISTS = (
    Specialist(
        "Situation",
        "Identify affected incidents, uncertainty, and why each matters under the declared priority rule.",
        situation_tools,
    ),
    Specialist(
        "Resources",
        "Identify capability shortages and candidate resources from the authoritative inventory.",
        resources_tools,
    ),
    Specialist(
        "Routing",
        "Explain reachability and closure consequences from the deterministic graph results.",
        routing_tools,
    ),
)


def run_specialists(
    snapshot,
    client: ModelClient,
    timeout: float = DEFAULT_AGENT_TIMEOUT_SECONDS,
    specialists: tuple[Specialist, ...] = SPECIALISTS,
) -> tuple[AgentFinding, ...]:
    """Run specialists concurrently; a specialist that overruns `timeout` is reported as timed out."""
    executor = ThreadPoolExecutor(max_workers=len(specialists))
    try:
        futures = [
            (specialist, executor.submit(specialist.run, snapshot, client, timeout))
            for specialist in specialists
        ]
        # One shared deadline: specialists run concurrently, so waits must not add up.
        deadline = perf_counter() + timeout + AGENT_TIMEOUT_GRACE_SECONDS
        findings = []
        for specialist, future in futures:
            try:
                findings.append(future.result(timeout=max(0.0, deadline - perf_counter())))
            except FutureTimeout:
                findings.append(
                    AgentFinding(
                        agent=specialist.name,
                        status=AgentStatus.TIMED_OUT,
                        tool_results=(),
                        error=f"No result within {timeout:.0f}s.",
                        model_id=client.model_id,
                        finished_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
        return tuple(findings)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
