from dataclasses import dataclass

from model import EmergencyService, Incident, IncidentStatus


@dataclass(frozen=True)
class Dispatch:
    incident_id: str
    service_id: str
    estimated_arrival: float


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
    ) -> ResponsePlan:
        open_incidents = sorted(
            (incident for incident in incidents if incident.status == IncidentStatus.OPEN),
            key=lambda incident: (-incident.severity, incident.id),
        )
        available = {service.id: service for service in services if service.available}
        assignments: list[Dispatch] = []
        unassigned: list[str] = []

        for incident in open_incidents:
            remaining_capabilities = set(incident.required_services)
            selected: list[EmergencyService] = []

            while remaining_capabilities:
                candidates = [
                    service
                    for service in available.values()
                    if service.capabilities & remaining_capabilities
                ]
                if not candidates:
                    break

                service = min(
                    candidates,
                    key=lambda candidate: (
                        -len(candidate.capabilities & remaining_capabilities),
                        incident.location.distance_to(candidate.location) / candidate.speed,
                        candidate.id,
                    ),
                )
                selected.append(service)
                remaining_capabilities.difference_update(service.capabilities)

            if remaining_capabilities:
                unassigned.append(incident.id)
                continue

            if not incident.required_services:
                candidates = list(available.values())
                if not candidates:
                    unassigned.append(incident.id)
                    continue
                selected = [
                    min(
                        candidates,
                        key=lambda candidate: (
                            incident.location.distance_to(candidate.location) / candidate.speed,
                            candidate.id,
                        ),
                    )
                ]

            for service in selected:
                eta = incident.location.distance_to(service.location) / service.speed
                assignments.append(Dispatch(incident.id, service.id, eta))
                del available[service.id]

        return ResponsePlan(tuple(assignments), tuple(unassigned))