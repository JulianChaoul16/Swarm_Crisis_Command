"""Convert domain objects into JSON-ready dictionaries for the dashboard."""

from dataclasses import asdict
import json
from typing import Any

import networkx as nx

from ..model import City, Coordinate, EmergencyService, Incident, IncidentStatus, Road
from ..operations import CommandService, PlanProposal, Report
from ..simulation import WorldEvent
from ..operations.scenario import FLOOD_PLACES
from ..swarm.board import IncidentBoard, agreement
from .geo import LABELS, to_line, to_lng_lat


def _coordinate(point: Coordinate) -> dict[str, float]:
    return {"x": point.x, "y": point.y}


def serialize_incident(
    incident: Incident,
    services: list[EmergencyService],
    reported_tick: int,
    place: str | None = None,
) -> dict[str, Any]:
    return {
        "id": incident.id,
        "place": place,
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
    place: str | None = None,
) -> dict[str, Any]:
    return {
        "id": service.id,
        "name": service.name,
        "place": place,
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


def roads_on(route, city: City | None, names: dict[str, str]) -> list[str]:
    """Readable names of the roads a route uses, in order."""
    if city is None:
        return []
    found: list[str] = []
    for a, b in zip(route, route[1:]):
        road = next((r for r in city.roads if {r.start, r.end} == {a, b}), None)
        if road is not None:
            name = names.get(road.id, road.id)
            if not found or found[-1] != name:
                found.append(name)
    return found


def serialize_proposal(proposal: PlanProposal, city: City | None = None,
                       road_names: dict[str, str] | None = None) -> dict[str, Any]:
    road_names = road_names or {}
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
                "via": roads_on(dispatch.route, city, road_names),
            }
            for dispatch in plan.assignments
        ],
        "unassigned_incident_ids": list(plan.unassigned_incident_ids),
        "rationale": list(plan.rationale),
        "checks": [asdict(check) for check in proposal.checks],
        "changes": [asdict(change) for change in proposal.changes],
        "invalidated": [asdict(mission) for mission in proposal.invalidated],
    }


def _result(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    return {"value": result.value, "model": result.model, "source": result.source,
            "note": result.note, "label": result.label}


# The named agents shown in the UI: role, the models behind each, and which activity-log
# names belong to it.
AGENT_ROSTER = (
    ("Listener Agent", "Turns 911 and radio audio into text.", ("Muse Voice Transcribe",), ("Listener Agent",)),
    ("Intake Agent", "Reads every report (text or transcript) into structured fields and votes how urgent "
     "it is (0-100). Personal details are redacted on this machine first.",
     ("Muse Spark",), ("Intake Agent",)),
    ("Vision Agent", "Reads citizen photos: where the water is and whether a road is passable. Votes on flood exposure.",
     ("SAM 3.1", "Muse Wonder"), ("Vision Agent",)),
    ("Command Agent", "Explains the solver's recommendation (why, what it delays) and, after approval, writes "
     "the public alert in English and Spanish.", ("Muse Spark", "Muse Image"), ("Command Agent",)),
)
VOTERS = {"Intake Agent", "Vision Agent"}
MODEL_STATUS_LABEL = {"live": "live", "cache (demo mode)": "recorded answers (demo)", "not connected": "not connected"}
STAND_IN = {
    "SAM 3.1": "sample data",
    "Muse Wonder": "sample data",
    "Muse Voice Transcribe": "sample data",
    "Muse Image": "not connected: no graphic is generated",
}


def serialize_agents(service: CommandService) -> list[dict[str, Any]]:
    status = {row["model"]: row["status"] for row in service.hub.status()}
    data = service.board.snapshot()
    incidents = {i.id: i for i in service.world.incidents if i.status != IncidentStatus.RESOLVED}
    agents = []
    for name, role, models, log_names in AGENT_ROSTER:
        activity = [a for a in data["activity"] if a.agent in log_names]
        votes = []
        if name in VOTERS:
            for incident_id, by_agent in data["votes"].items():
                vote = by_agent.get(name)
                if vote is not None and incident_id in incidents:
                    votes.append({"incident_id": incident_id, "kind": incidents[incident_id].kind,
                                  "score": vote.score, "reasons": list(vote.reasons), "source": vote.source})
            votes.sort(key=lambda v: -v["score"])
        agents.append({
            "name": name,
            "role": role,
            "models": [{"model": m, "status": MODEL_STATUS_LABEL.get(status.get(m, ""), "") if m == "Muse Spark"
                        else STAND_IN.get(m, "") if status.get(m) == "not connected" else status.get(m, "")}
                       for m in models]
                      or [{"model": "Exact solver (code)", "status": "deterministic"}],
            "actions": len(activity),
            "last": ({"at": activity[-1].at.isoformat(), "text": activity[-1].text} if activity else None),
            "activity": [{"at": a.at.isoformat(), "source": a.source, "text": a.text, "incident_id": a.incident_id}
                         for a in reversed(activity[-40:])],
            "votes": votes,
        })
    return agents


def serialize_swarm(board: IncidentBoard) -> dict[str, Any]:
    data = board.snapshot()
    return {
        "incidents": {
            incident_id: {
                "votes": {agent: {"score": vote.score, "reasons": list(vote.reasons), "source": vote.source}
                          for agent, vote in votes.items()},
                "final": data["finals"].get(incident_id),
                "agreement": agreement(votes),
            }
            for incident_id, votes in data["votes"].items()
        },
        "flood_cover": data["flood_cover"],
        "gauge": data["gauge"],
        "imagery": data["imagery"],
        "transcripts": data["transcripts"],
        # Newest first, capped for the UI.
        "activity": [
            {"at": item.at.isoformat(), "agent": item.agent, "source": item.source,
             "text": item.text, "incident_id": item.incident_id}
            for item in reversed(data["activity"][-120:])
        ],
        "consensus_rule": "Final = median vote, pulled halfway toward the highest vote.",
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
    place_names = {point: name for name, point in service.scenario.places.items()}
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
            serialize_incident(incident, services, reported_ticks.get(incident.id, 0),
                               place_names.get(incident.location))
            for incident in incidents
        ],
        "services": [
            serialize_service(service_, incidents_by_id, world.city, place_names.get(service_.location))
            for service_ in services
        ],
        "roads": [{**serialize_road(road), "name": service.scenario.road_names.get(road.id, road.id)}
                  for road in world.city.roads] if world.city else [],
        "places": sorted(service.scenario.places),
        "priority_rule": service.planner.priority_rule,
        # On the satellite map the real river is visible; only its name is drawn.
        "annotations": [
            {"label": label, "path": [point]} for label, point in LABELS.items()
        ] if service.scenario.places is FLOOD_PLACES else [
            {"label": label, "path": to_line(points)}
            for label, points in service.scenario.annotations
        ],
        "disruptions": [
            {
                "key": key,
                "label": disruption.label,
                "in_effect": _disruption_in_effect(disruption.fields, world),
            }
            for key, disruption in service.scenario.disruptions.items()
        ],
        "current_plan": serialize_proposal(current, world.city, service.scenario.road_names)
        if current is not None else None,
        "swarm": serialize_swarm(service.board),
        "models": service.hub.status(),
        "agents": serialize_agents(service),
        "explanation": _result(service.explanations.get(current.id)) if current is not None else None,
        "alerts": {plan_id: {"text": _result(alert["text"]), "graphic": _result(alert["graphic"])}
                   for plan_id, alert in service.alerts.items()},
        "reports": [serialize_report(report) for report in reversed(service.reports)],
    }
