from dataclasses import dataclass, field
from typing import Callable, Generic, Iterable, TypeVar, cast, overload

import networkx as nx

from model import City, Coordinate, EmergencyService, Incident, IncidentStatus
from planner import Planner, ResponsePlan


T = TypeVar("T")


class _VersionedList(list[T], Generic[T]):
    def __init__(
        self,
        values: Iterable[T],
        validate: Callable[[T], None],
        on_add: Callable[[], None],
    ) -> None:
        super().__init__(values)
        self._validate = validate
        self._on_add = on_add

    def append(self, value: T) -> None:
        self._validate(value)
        super().append(value)
        self._on_add()

    def extend(self, values: Iterable[T]) -> None:
        for value in values:
            self.append(value)

    def insert(self, index: int, value: T) -> None:
        self._validate(value)
        super().insert(index, value)
        self._on_add()

    def __iadd__(self, values: Iterable[T]) -> _VersionedList[T]:
        self.extend(values)
        return self

    @overload
    def __setitem__(self, index: int, value: T) -> None: ...

    @overload
    def __setitem__(self, index: slice, value: Iterable[T]) -> None: ...

    def __setitem__(self, index: int | slice, value: T | Iterable[T]) -> None:
        if isinstance(index, slice):
            values = list(cast(Iterable[T], value))
            for item in values:
                self._validate(item)
            super().__setitem__(index, values)
            for _ in values:
                self._on_add()
            return

        item = cast(T, value)
        self._validate(item)
        super().__setitem__(index, item)
        self._on_add()


@dataclass
class World:
    incidents: list[Incident] = field(default_factory=list)
    services: list[EmergencyService] = field(default_factory=list)
    tick_count: int = 0
    version: int = 1
    city: City | None = None

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
        self.incidents = _VersionedList(
            self.incidents,
            self._validate_incident,
            self._increment_version,
        )
        self.services = _VersionedList(
            self.services,
            self._validate_service,
            self._increment_version,
        )

    def _increment_version(self) -> None:
        self.version += 1

    def _validate_incident(self, incident: Incident) -> None:
        if any(existing.id == incident.id for existing in self.incidents):
            raise ValueError(f"Incident id {incident.id!r} already exists in this world.")

    def _validate_service(self, service: EmergencyService) -> None:
        if any(existing.id == service.id for existing in self.services):
            raise ValueError(f"Service id {service.id!r} already exists in this world.")
        if (
            service.assigned_incident_id is not None
            and all(incident.id != service.assigned_incident_id for incident in self.incidents)
        ):
            raise ValueError("Service must reference an incident in this world.")

    def add_incident(self, incident: Incident) -> None:
        self.incidents.append(incident)

    def add_emergency_service(self, service: EmergencyService) -> None:
        self.services.append(service)


@dataclass
class Simulation:
    world: World
    planner: Planner = field(default_factory=Planner)

    def step(self) -> ResponsePlan:
        plan = self.planner.plan(
            self.world.incidents,
            self.world.services,
            self.world.city,
        )
        incidents_by_id = {incident.id: incident for incident in self.world.incidents}
        services_by_id = {service.id: service for service in self.world.services}

        for dispatch in plan.assignments:
            service = services_by_id[dispatch.service_id]
            service.assigned_incident_id = dispatch.incident_id
            incidents_by_id[dispatch.incident_id].status = IncidentStatus.ASSIGNED

        planned_routes = {
            dispatch.service_id: dispatch.route for dispatch in plan.assignments
        }
        for service in self.world.services:
            if service.assigned_incident_id is None:
                continue
            incident = incidents_by_id[service.assigned_incident_id]
            try:
                route = planned_routes.get(service.id)
                if route is None:
                    route = (
                        self.world.city.shortest_route(service.location, incident.location)
                        if self.world.city is not None
                        else [service.location, incident.location]
                    )
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                incident.status = IncidentStatus.OPEN
                for assigned in self.world.services:
                    if assigned.assigned_incident_id == incident.id:
                        assigned.assigned_incident_id = None
                continue

            remaining_distance = service.speed
            current = service.location
            for waypoint in route[1:]:
                segment_distance = current.distance_to(waypoint)
                if remaining_distance >= segment_distance:
                    current = waypoint
                    remaining_distance -= segment_distance
                    continue
                if segment_distance:
                    scale = remaining_distance / segment_distance
                    current = Coordinate(
                        current.x + (waypoint.x - current.x) * scale,
                        current.y + (waypoint.y - current.y) * scale,
                    )
                break
            service.location = current

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
