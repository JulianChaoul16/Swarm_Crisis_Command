from dataclasses import dataclass

import networkx as nx

from model import City, Coordinate, EmergencyService, Incident, IncidentStatus


@dataclass(frozen=True)
class Dispatch:
    incident_id: str
    service_id: str
    estimated_arrival: float
    route: tuple[Coordinate, ...]


@dataclass(frozen=True)
class ResponsePlan:
    assignments: tuple[Dispatch, ...]
    unassigned_incident_ids: tuple[str, ...]


class Planner:
    """Prioritize incidents and allocate the closest available capable services."""

    def plan(
        self,
        incidents: list[Incident],
        services: list[EmergencyService],
        city: City | None = None,
    ) -> ResponsePlan:
        open_incidents = sorted(
            (incident for incident in incidents if incident.status == IncidentStatus.OPEN),
            key=lambda incident: (-incident.severity, incident.id),
        )
        available = {service.id: service for service in services if service.available}
        assignments: list[Dispatch] = []
        unassigned: list[str] = []

        def route_for(service: EmergencyService, incident: Incident) -> tuple[tuple[Coordinate, ...], float] | None:
            if city is None:
                distance = service.location.distance_to(incident.location)
                return (service.location, incident.location), distance
            try:
                route = tuple(city.shortest_route(service.location, incident.location))
                return route, city.route_distance(service.location, incident.location)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return None

        for incident in open_incidents:
            remaining_capabilities = set(incident.required_services)
            selected: list[EmergencyService] = []
            selected_routes: dict[str, tuple[tuple[Coordinate, ...], float]] = {}

            while remaining_capabilities:
                candidate_routes = {
                    service.id: route
                    for service in available.values()
                    if service.capabilities & remaining_capabilities
                    if (route := route_for(service, incident)) is not None
                }
                candidates = [
                    service
                    for service in available.values()
                    if service.id in candidate_routes
                ]
                if not candidates:
                    break

                service = min(
                    candidates,
                    key=lambda candidate: (
                        -len(candidate.capabilities & remaining_capabilities),
                        candidate_routes[candidate.id][1] / candidate.speed,
                        candidate.id,
                    ),
                )
                selected.append(service)
                selected_routes[service.id] = candidate_routes[service.id]
                remaining_capabilities.difference_update(service.capabilities)

            if remaining_capabilities:
                unassigned.append(incident.id)
                continue

            if not incident.required_services:
                candidate_routes = {
                    service.id: route
                    for service in available.values()
                    if (route := route_for(service, incident)) is not None
                }
                candidates = [
                    service for service in available.values() if service.id in candidate_routes
                ]
                if not candidates:
                    unassigned.append(incident.id)
                    continue
                selected = [
                    min(
                        candidates,
                        key=lambda candidate: (
                            candidate_routes[candidate.id][1] / candidate.speed,
                            candidate.id,
                        ),
                    )
                ]
                selected_routes[selected[0].id] = candidate_routes[selected[0].id]

            for service in selected:
                route, distance = selected_routes[service.id]
                eta = distance / service.speed
                assignments.append(Dispatch(incident.id, service.id, eta, route))
                del available[service.id]

        return ResponsePlan(tuple(assignments), tuple(unassigned))