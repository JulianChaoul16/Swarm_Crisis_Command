"""Convert domain objects into JSON-ready dictionaries for the dashboard."""

from typing import Any

import networkx as nx

from ..model import City, Coordinate, EmergencyService, Incident, IncidentStatus, Road
from ..planner import ResponsePlan
from ..simulation import World, WorldEvent
from .geo import to_line, to_lng_lat


def _coordinate(point: Coordinate) -> dict[str, float]:
    return {"x": point.x, "y": point.y}


def serialize_incident(incident: Incident, services: list[EmergencyService]) -> dict[str, Any]:
    return {
        "id": incident.id,
        "kind": incident.kind,
        "severity": incident.severity.name.lower(),
        "severity_level": int(incident.severity),
        "status": incident.status.value,
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


def serialize_plan(plan: ResponsePlan) -> dict[str, Any]:
    return {
        "world_version": plan.world_version,
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
        "unknown_road_ids": list(plan.unknown_road_ids),
        "claims_requiring_review": [
            f"{resolution.subject}.{resolution.attribute}"
            for resolution in plan.claim_resolutions
            if resolution.requires_review
        ],
    }


def serialize_event(event: WorldEvent) -> dict[str, Any]:
    return {
        "sequence": event.sequence,
        "tick": event.tick,
        "version": event.version,
        "kind": event.kind,
        "entity_ids": list(event.entity_ids),
        "reason": event.reason,
        "details": dict(event.details),
    }


def serialize_world(
    world: World,
    last_plan: ResponsePlan | None,
    notice: str | None,
) -> dict[str, Any]:
    incidents = list(world.incidents)
    services = list(world.services)
    incidents_by_id = {incident.id: incident for incident in incidents}
    return {
        "city": world.city.name if world.city is not None else None,
        "version": world.version,
        "tick": world.tick_count,
        "all_resolved": all(
            incident.status == IncidentStatus.RESOLVED for incident in incidents
        ),
        "incidents": [serialize_incident(incident, services) for incident in incidents],
        "services": [
            serialize_service(service, incidents_by_id, world.city) for service in services
        ],
        "roads": [serialize_road(road) for road in world.city.roads] if world.city else [],
        "last_plan": serialize_plan(last_plan) if last_plan is not None else None,
        "notice": notice,
    }
