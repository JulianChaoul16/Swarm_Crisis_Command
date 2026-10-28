"""Convert domain objects into JSON-ready dictionaries for the dashboard."""

from dataclasses import asdict
from typing import Any

import networkx as nx

from ..model import City, Coordinate, EmergencyService, Incident, IncidentStatus, Road
from ..operations import CommandService, PlanProposal, Report
from ..simulation import WorldEvent
from .geo import to_line, to_lng_lat


def _coordinate(point: Coordinate) -> dict[str, float]:
    return {"x": point.x, "y": point.y}


def serialize_incident(
    incident: Incident,
    services: list[EmergencyService],
    reported_tick: int,
) -> dict[str, Any]:
    return {
        "id": incident.id,
        "kind": incident.kind,
        "severity": incident.severity.name.lower(),
        "severity_level": int(incident.severity),
        "status": incident.status.value,
        "reported_tick": reported_tick,
        "people": incident.people,
        "required_services": sorted(incident.required_services),
        "location": _coordinate(incident.location),
        "position": to_lng_lat(incident.location),
        "assigned_service_ids": sorted(
            service.id for service in services if service.assigned_incident_id == incident.id
        ),
    }


def _current_route(
    service: EmergencyService,
    incidents_by_id: dict[str, Incident],
    city: City | None,
) -> list[list[float]] | None:
    if service.assigned_incident_id is None:
        return None
    target = incidents_by_id[service.assigned_incident_id].location
    if city is None:
        return to_line([service.location, target])
    try:
        return to_line(city.shortest_route(service.location, target))
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def serialize_service(
    service: EmergencyService,
    incidents_by_id: dict[str, Incident],
    city: City | None,
) -> dict[str, Any]:
    return {
        "id": service.id,
        "name": service.name,
        "capabilities": sorted(service.capabilities),
        "speed": service.speed,
        "capacity": service.capacity,
        "available": service.available,
        "assigned_incident_id": service.assigned_incident_id,
        "location": _coordinate(service.location),
        "position": to_lng_lat(service.location),
        "route": _current_route(service, incidents_by_id, city),
    }


def serialize_road(road: Road) -> dict[str, Any]:
    return {
        "id": road.id,
        "is_closed": road.is_closed,
        "length": road.length,
        "path": to_line([road.start, road.end]),
    }


def serialize_proposal(proposal: PlanProposal) -> dict[str, Any]:
    plan = proposal.plan
    return {
        "id": proposal.id,
        "run_id": proposal.run_id,
        "world_version": proposal.world_version,
        "status": proposal.status.value,
        "valid": proposal.valid,
        "created_at": proposal.created_at.isoformat(),
        "previous_plan_id": proposal.previous_plan_id,
        "decided_at": proposal.decided_at.isoformat() if proposal.decided_at else None,
        "decided_by": proposal.decided_by,
        "decision_reason": proposal.decision_reason,
        "assignments": [
            {
                "incident_id": dispatch.incident_id,
                "service_id": dispatch.service_id,
                "estimated_arrival": dispatch.estimated_arrival,
                "route": to_line(dispatch.route),
            }
            for dispatch in plan.assignments
        ],
        "unassigned_incident_ids": list(plan.unassigned_incident_ids),
        "rationale": list(plan.rationale),
        "checks": [asdict(check) for check in proposal.checks],
        "changes": [asdict(change) for change in proposal.changes],
        "invalidated": [asdict(mission) for mission in proposal.invalidated],
        "agents": [
            {
                "agent": finding.agent,
                "status": finding.status.value,
                "summary": finding.summary,
                "findings": list(finding.findings),
                "missing_information": list(finding.missing_information),
                "error": finding.error,
                "model_id": finding.model_id,
                "latency_ms": finding.latency_ms,
                "tools": [{"name": tool.name, "output": tool.output} for tool in finding.tool_results],
            }
            for finding in proposal.agent_findings
        ],
    }


def serialize_report(report: Report) -> dict[str, Any]:
    fields = asdict(report.fields)
    if fields["required_services"] is not None:
        fields["required_services"] = list(fields["required_services"])
    return {
        "id": report.id,
        "content": report.content,
        "source": report.source,
        "received_at": report.received_at.isoformat(),
        "occurred_at": report.occurred_at.isoformat() if report.occurred_at else None,
        "fields": fields,
        "extracted_by": report.extracted_by,
        "extraction_error": report.extraction_error,
        "missing_fields": list(report.missing_fields),
        "validation_errors": list(report.validation_errors),
        "warnings": list(report.warnings),
        "status": report.status.value,
        "linked_incident_id": report.linked_incident_id,
        "linked_road_id": report.linked_road_id,
        "decision_reason": report.decision_reason,
    }


def serialize_event(event: WorldEvent) -> dict[str, Any]:
    return {
        "sequence": event.sequence,
        "tick": event.tick,
        "version": event.version,
        "kind": event.kind,
        "actor": event.actor,
        "recorded_at": event.recorded_at,
        "entity_ids": list(event.entity_ids),
        "reason": event.reason,
        "details": dict(event.details),
    }


def _disruption_in_effect(fields: dict[str, Any], world) -> bool:
    if fields.get("report_type") != "road_status" or world.city is None:
        return False
    road = next((road for road in world.city.roads if road.id == fields.get("road_id")), None)
    return road is not None and road.is_closed == fields.get("road_closed")


def serialize_state(service: CommandService) -> dict[str, Any]:
    world = service.world
    incidents = list(world.incidents)
    services = list(world.services)
    incidents_by_id = {incident.id: incident for incident in incidents}
    reported_ticks = {
        event.entity_ids[0]: event.tick
        for event in world.event_log
        if event.kind == "incident_added"
    }
    current = service.current_plan
    return {
        "simulation": True,
        "run_id": service.run_id,
        "scenario": service.scenario.name,
        "model": service.client.model_id,
        "city": world.city.name if world.city is not None else None,
        "version": world.version,
        "tick": world.tick_count,
        "all_resolved": all(
            incident.status == IncidentStatus.RESOLVED for incident in incidents
        ),
        "incidents": [
            serialize_incident(incident, services, reported_ticks.get(incident.id, 0))
            for incident in incidents
        ],
        "services": [
            serialize_service(service_, incidents_by_id, world.city) for service_ in services
        ],
        "roads": [serialize_road(road) for road in world.city.roads] if world.city else [],
        "places": sorted(service.scenario.places),
        "priority_rule": service.planner.priority_rule,
        "disruptions": [
            {
                "key": key,
                "label": disruption.label,
                "in_effect": _disruption_in_effect(disruption.fields, world),
            }
            for key, disruption in service.scenario.disruptions.items()
        ],
        "current_plan": serialize_proposal(current) if current is not None else None,
        "reports": [serialize_report(report) for report in reversed(service.reports)],
    }
