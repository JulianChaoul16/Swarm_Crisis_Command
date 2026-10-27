from dataclasses import dataclass, field

from model import Coordinate, EmergencyService, Incident, IncidentStatus
from planner import Planner, ResponsePlan


@dataclass
class World:
    incidents: list[Incident] = field(default_factory=list)
    services: list[EmergencyService] = field(default_factory=list)
    tick_count: int = 0

    def __post_init__(self) -> None:
        incident_ids = [incident.id for incident in self.incidents]
        service_ids = [service.id for service in self.services]
        if len(incident_ids) != len(set(incident_ids)):
            raise ValueError("Incident ids must be unique.")
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("Emergency service ids must be unique.")
        known_incidents = set(incident_ids)
        if any(
            service.assigned_incident_id not in known_incidents
            for service in self.services
            if service.assigned_incident_id is not None
        ):
            raise ValueError("Services must reference an incident in this world.")


@dataclass
class Simulation:
    world: World
    planner: Planner = field(default_factory=Planner)

    def step(self) -> ResponsePlan:
        plan = self.planner.plan(self.world.incidents, self.world.services)
        incidents_by_id = {incident.id: incident for incident in self.world.incidents}
        services_by_id = {service.id: service for service in self.world.services}

        for dispatch in plan.assignments:
            service = services_by_id[dispatch.service_id]
            service.assigned_incident_id = dispatch.incident_id
            incidents_by_id[dispatch.incident_id].status = IncidentStatus.ASSIGNED

        for service in self.world.services:
            if service.assigned_incident_id is None:
                continue
            incident = incidents_by_id[service.assigned_incident_id]
            distance = service.location.distance_to(incident.location)
            travel_distance = min(distance, service.speed)
            if distance:
                scale = travel_distance / distance
                service.location = Coordinate(
                    service.location.x + (incident.location.x - service.location.x) * scale,
                    service.location.y + (incident.location.y - service.location.y) * scale,
                )

        for incident in self.world.incidents:
            if incident.status != IncidentStatus.ASSIGNED:
                continue
            assigned_services = [
                service
                for service in self.world.services
                if service.assigned_incident_id == incident.id
            ]
            if assigned_services and all(
                service.location.distance_to(incident.location) == 0
                for service in assigned_services
            ):
                incident.status = IncidentStatus.RESOLVED
                for service in assigned_services:
                    service.assigned_incident_id = None

        self.world.tick_count += 1
        return plan

    def run(self, max_steps: int = 100) -> int:
        if max_steps < 0:
            raise ValueError("max_steps cannot be negative.")
        for steps in range(max_steps + 1):
            if all(
                incident.status == IncidentStatus.RESOLVED
                for incident in self.world.incidents
            ):
                return steps
            if steps == max_steps:
                break
            self.step()
        return max_steps
