"""Deterministic hard-constraint checks for a proposed response plan."""

from dataclasses import dataclass
from typing import Iterable

from ..model import City, Coordinate, EmergencyService, Incident, IncidentStatus
from ..planner import ResponsePlan


@dataclass(frozen=True)
class Check:
    rule: str
    passed: bool
    detail: str


def _on_open_road(city: City, a: Coordinate, b: Coordinate) -> bool:
    for road in city.roads:
        if road.is_closed:
            continue
        if all(
            point in (road.start, road.end) or City._road_projection(point, road) is not None
            for point in (a, b)
        ):
            return True
    return False


def check_plan(
    plan: ResponsePlan,
    incidents: Iterable[Incident],
    services: Iterable[EmergencyService],
    city: City | None,
) -> tuple[Check, ...]:
    incidents_by_id = {incident.id: incident for incident in incidents}
    services_by_id = {service.id: service for service in services}
    problems: dict[str, list[str]] = {
        rule: []
        for rule in (
            "references_exist",
            "incidents_open",
            "resources_available",
            "no_double_booking",
            "capabilities_cover",
            "routes_open",
            "unmet_needs_reported",
        )
    }

    seen: set[str] = set()
    coverage: dict[str, set[str]] = {}
    for dispatch in plan.assignments:
        incident = incidents_by_id.get(dispatch.incident_id)
        service = services_by_id.get(dispatch.service_id)
        if incident is None or service is None:
            problems["references_exist"].append(
                f"{dispatch.service_id} -> {dispatch.incident_id} references an unknown entity"
            )
            continue
        if dispatch.service_id in seen:
            problems["no_double_booking"].append(f"{service.id} is assigned more than once")
        seen.add(service.id)
        if incident.status != IncidentStatus.OPEN:
            problems["incidents_open"].append(f"{incident.id} is {incident.status.value}")
        if not service.available:
            problems["resources_available"].append(
                f"{service.id} is already on {service.assigned_incident_id}"
            )
        coverage.setdefault(incident.id, set()).update(service.capabilities)

        route = dispatch.route
        if not route or route[0] != service.location or route[-1] != incident.location:
            problems["routes_open"].append(
                f"{service.id} route does not run from the unit to {incident.id}"
            )
        elif city is not None and not all(
            _on_open_road(city, a, b) for a, b in zip(route, route[1:])
        ):
            problems["routes_open"].append(f"{service.id} route uses a closed or unknown road")

    for incident_id, capabilities in coverage.items():
        missing = incidents_by_id[incident_id].required_services - capabilities
        if missing:
            problems["capabilities_cover"].append(
                f"{incident_id} still needs {', '.join(sorted(missing))}"
            )

    open_ids = {
        incident.id for incident in incidents_by_id.values() if incident.status == IncidentStatus.OPEN
    }
    unaccounted = open_ids - set(coverage) - set(plan.unassigned_incident_ids)
    if unaccounted:
        problems["unmet_needs_reported"].append(
            f"open incidents missing from the plan: {', '.join(sorted(unaccounted))}"
        )

    return tuple(
        Check(rule, not details, "; ".join(details) if details else "ok")
        for rule, details in problems.items()
    )


def all_passed(checks: Iterable[Check]) -> bool:
    return all(check.passed for check in checks)
