from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
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
        lock: RLock,
    ) -> None:
        super().__init__(values)
        self._validate = validate
        self._on_add = on_add
        self._lock = lock

    def append(self, value: T) -> None:
        with self._lock:
            self._validate(value)
            super().append(value)
            self._on_add()

    def extend(self, values: Iterable[T]) -> None:
        with self._lock:
            for value in values:
                self.append(value)

    def insert(self, index: int, value: T) -> None:
        with self._lock:
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
        with self._lock:
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


@dataclass(frozen=True)
class WorldSnapshot:
    version: int
    incidents: tuple[Incident, ...]
    services: tuple[EmergencyService, ...]
    city: City | None
    state_signature: tuple[object, ...]


class StalePlanError(RuntimeError):
    pass


@dataclass
class World:
    incidents: list[Incident] = field(default_factory=list)
    services: list[EmergencyService] = field(default_factory=list)
    tick_count: int = 0
    version: int = 1
    city: City | None = None
    _lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)

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
            self._lock,
        )
        self.services = _VersionedList(
            self.services,
            self._validate_service,
            self._increment_version,
            self._lock,
        )
        if self.city is not None:
            self.city.set_change_callback(self._increment_version)

    def _increment_version(self) -> None:
        with self._lock:
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

    def snapshot(self) -> WorldSnapshot:
        with self._lock:
            city_snapshot = None
            if self.city is not None:
                city_snapshot = City(self.city.name)
                for road in self.city.roads:
                    city_snapshot.add_road(deepcopy(road))
            return WorldSnapshot(
                version=self.version,
                incidents=tuple(deepcopy(incident) for incident in self.incidents),
                services=tuple(deepcopy(service) for service in self.services),
                city=city_snapshot,
                state_signature=self._state_signature(),
            )

    def _state_signature(self) -> tuple[object, ...]:
        incidents = tuple(
            (
                incident.id,
                incident.kind,
                incident.location,
                incident.severity,
                incident.required_services,
                incident.status,
            )
            for incident in self.incidents
        )
        services = tuple(
            (
                service.id,
                service.name,
                service.location,
                service.capabilities,
                service.speed,
                service.assigned_incident_id,
            )
            for service in self.services
        )
        roads = (
            self.city.name,
            tuple(
                (road.id, road.start, road.end, road.is_closed)
                for road in self.city.roads
            ),
        ) if self.city is not None else None
        return self.tick_count, incidents, services, roads


@dataclass
class Simulation:
    world: World
    planner: Planner = field(default_factory=Planner)

    def step(self) -> ResponsePlan:
        snapshot = self.world.snapshot()
        plan = self.planner.plan(
            list(snapshot.incidents),
            list(snapshot.services),
            snapshot.city,
            world_version=snapshot.version,
        )
        with self.world._lock:
            if (
                self.world.version != snapshot.version
                or self.world._state_signature() != snapshot.state_signature
            ):
                raise StalePlanError(
                    f"Plan used world version {snapshot.version}, "
                    f"but current world is version {self.world.version}."
                )
            if plan.world_version != snapshot.version:
                raise StalePlanError("Planner returned a plan for a different world version.")
            self._validate_plan(plan)
            return self._advance_world(plan)

    def _validate_plan(self, plan: ResponsePlan) -> None:
        incidents_by_id = {incident.id: incident for incident in self.world.incidents}
        services_by_id = {service.id: service for service in self.world.services}
        assigned_service_ids: set[str] = set()
        capabilities_by_incident: dict[str, set[str]] = {}

        for dispatch in plan.assignments:
            if dispatch.incident_id not in incidents_by_id:
                raise ValueError(f"Unknown incident in dispatch: {dispatch.incident_id!r}.")
            if dispatch.service_id not in services_by_id:
                raise ValueError(f"Unknown service in dispatch: {dispatch.service_id!r}.")
            if dispatch.service_id in assigned_service_ids:
                raise ValueError(f"Service {dispatch.service_id!r} is assigned more than once.")
            incident = incidents_by_id[dispatch.incident_id]
            service = services_by_id[dispatch.service_id]
            if incident.status != IncidentStatus.OPEN:
                raise ValueError(f"Incident {incident.id!r} is not open.")
            if not service.available:
                raise ValueError(f"Service {service.id!r} is not available.")
            assigned_service_ids.add(service.id)
            capabilities_by_incident.setdefault(incident.id, set()).update(
                service.capabilities
            )

        for incident_id, capabilities in capabilities_by_incident.items():
            required = incidents_by_id[incident_id].required_services
            if not required.issubset(capabilities):
                raise ValueError(f"Dispatch does not cover all requirements for {incident_id!r}.")

    def _advance_world(self, plan: ResponsePlan) -> ResponsePlan:
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
        self.world.version += 1
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
