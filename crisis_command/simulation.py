import json
from copy import deepcopy
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Callable, Generic, Iterable, TypeVar, cast, overload
from _thread import RLock as RLockType

MAX_PLAN_ATTEMPTS = 3

import networkx as nx

from .model import City, Coordinate, EmergencyService, Incident, IncidentStatus, Road
from .planner import Claim, Planner, ResponsePlan


T = TypeVar("T")


class _VersionedList(list[T], Generic[T]):
    def __init__(
        self,
        values: Iterable[T],
        validate: Callable[[T], None],
        on_add: Callable[[T, str], None],
        lock: RLockType,
    ) -> None:
        super().__init__(values)
        self._validate = validate
        self._on_add = on_add
        self._lock = lock

    def append(self, value: T) -> None:
        self.append_with_reason(value, "Added directly to world state.")

    def append_with_reason(self, value: T, reason: str) -> None:
        if not reason.strip():
            raise ValueError("World update reason cannot be empty.")
        with self._lock:
            self._validate(value)
            super().append(value)
            self._on_add(value, reason)

    def extend(self, values: Iterable[T]) -> None:
        with self._lock:
            for value in values:
                self.append(value)

    def insert(self, index: int, value: T) -> None:
        with self._lock:
            self._validate(value)
            super().insert(index, value)
            self._on_add(value, "Added directly to world state.")

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
                for item in values:
                    self._on_add(item, "Added or replaced in world state.")
                return

            item = cast(T, value)
            self._validate(item)
            super().__setitem__(index, item)
            self._on_add(item, "Added or replaced in world state.")


@dataclass(frozen=True)
class WorldSnapshot:
    version: int
    incidents: tuple[Incident, ...]
    services: tuple[EmergencyService, ...]
    city: City | None
    state_signature: tuple[object, ...]


@dataclass(frozen=True)
class WorldEvent:
    sequence: int
    tick: int
    version: int
    kind: str
    entity_ids: tuple[str, ...]
    reason: str
    details: tuple[tuple[str, str], ...] = ()


class StalePlanError(RuntimeError):
    pass


@dataclass
class World:
    incidents: list[Incident] = field(default_factory=list)
    services: list[EmergencyService] = field(default_factory=list)
    tick_count: int = 0
    version: int = 1
    city: City | None = None
    _lock: RLockType = field(
        default_factory=RLock,
        init=False,
        repr=False,
        compare=False,
    )
    _events: list[WorldEvent] = field(default_factory=list, init=False, repr=False)

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
            self._record_entity_added,
            self._lock,
        )
        self.services = _VersionedList(
            self.services,
            self._validate_service,
            self._record_entity_added,
            self._lock,
        )
        if self.city is not None:
            self.city.set_change_callback(self._record_road_change)
        self._append_event(
            "world_initialized",
            (),
            "Initial world state loaded.",
            (
                ("incident_count", str(len(self.incidents))),
                ("service_count", str(len(self.services))),
                ("road_count", str(len(self.city.roads) if self.city else 0)),
            ),
        )

    @property
    def event_log(self) -> tuple[WorldEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def _append_event(
        self,
        kind: str,
        entity_ids: tuple[str, ...],
        reason: str,
        details: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self._events.append(
            WorldEvent(
                sequence=len(self._events) + 1,
                tick=self.tick_count,
                version=self.version,
                kind=kind,
                entity_ids=entity_ids,
                reason=reason,
                details=details,
            )
        )

    def _record_entity_added(
        self,
        entity: Incident | EmergencyService,
        reason: str,
    ) -> None:
        with self._lock:
            self.version += 1
            if isinstance(entity, Incident):
                kind = "incident_added"
            elif isinstance(entity, EmergencyService):
                kind = "service_added"
            else:
                raise TypeError(f"Unsupported world entity: {type(entity).__name__}.")
            self._append_event(kind, (entity.id,), reason)

    def _record_road_change(self, kind: str, road: Road, reason: str) -> None:
        with self._lock:
            self.version += 1
            self._append_event(
                kind,
                (road.id,),
                reason,
                (
                    ("start", f"({road.start.x}, {road.start.y})"),
                    ("end", f"({road.end.x}, {road.end.y})"),
                    ("is_closed", str(road.is_closed).lower()),
                ),
            )

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

    def add_incident(
        self,
        incident: Incident,
        reason: str = "New incident reported.",
    ) -> None:
        if not reason.strip():
            raise ValueError("Incident addition reason cannot be empty.")
        self.incidents.append_with_reason(incident, reason)

    def add_emergency_service(
        self,
        service: EmergencyService,
        reason: str = "Emergency service added to the world.",
    ) -> None:
        if not reason.strip():
            raise ValueError("Service addition reason cannot be empty.")
        self.services.append_with_reason(service, reason)

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

    def step(
        self,
        snapshot: WorldSnapshot | None = None,
        plan: ResponsePlan | None = None,
        claims: tuple[Claim, ...] = (),
    ) -> ResponsePlan:
        proposal_snapshot = snapshot or self.world.snapshot()
        proposal = plan
        last_rejection = "No proposal could be validated."

        for _ in range(MAX_PLAN_ATTEMPTS):
            if proposal is None:
                proposal = self.planner.plan(
                    list(proposal_snapshot.incidents),
                    list(proposal_snapshot.services),
                    proposal_snapshot.city,
                    world_version=proposal_snapshot.version,
                    claims=claims,
                )
            with self.world._lock:
                if proposal.world_version != proposal_snapshot.version:
                    raise StalePlanError(
                        "Planner returned a plan for a different world version."
                    )
                if (
                    self.world.version == proposal_snapshot.version
                    and self.world._state_signature() == proposal_snapshot.state_signature
                ):
                    self._validate_plan(proposal)
                    return self._advance_world(proposal)

                rebased_plan, rejection_reason = self._revalidate_stale_plan(proposal)
                if rebased_plan is not None:
                    self.world._append_event(
                        "stale_plan_accepted",
                        tuple(dispatch.incident_id for dispatch in rebased_plan.assignments),
                        "Independent validation confirmed the older proposal remains safe and feasible.",
                        (
                            ("proposal_version", str(proposal_snapshot.version)),
                            ("current_version", str(self.world.version)),
                            ("decision", "execute_before_replanning"),
                        ),
                    )
                    return self._advance_world(rebased_plan)

                last_rejection = rejection_reason
                self.world._append_event(
                    "stale_plan_rejected",
                    (),
                    rejection_reason,
                    (
                        ("proposal_version", str(proposal_snapshot.version)),
                        ("current_version", str(self.world.version)),
                        ("decision", "replan"),
                        (
                            "proposal_audit",
                            json.dumps(dict(self._plan_audit_details(proposal))),
                        ),
                    ),
                )
                proposal_snapshot = self.world.snapshot()
            proposal = None

        raise StalePlanError(
            "World state kept changing while planning; no proposal was executed. "
            f"Last validation result: {last_rejection}"
        )

    def _revalidate_stale_plan(
        self,
        plan: ResponsePlan,
    ) -> tuple[ResponsePlan | None, str]:
        try:
            self._validate_assignments(plan)
        except ValueError as error:
            return None, f"Older proposal is no longer valid: {error}"

        incidents_by_id = {incident.id: incident for incident in self.world.incidents}
        services_by_id = {service.id: service for service in self.world.services}
        authoritative_closures = tuple(
            resolution
            for resolution in plan.claim_resolutions
            if resolution.selected_claim is not None
            and self.planner.state_agent.arbitrator.is_authoritative_closure(
                resolution.selected_claim
            )
        )
        effective_city = self.planner.state_agent.city_with_evidence(
            self.world.city,
            authoritative_closures,
        )
        updated_dispatches = []
        for dispatch in plan.assignments:
            incident = incidents_by_id[dispatch.incident_id]
            service = services_by_id[dispatch.service_id]
            try:
                route = (
                    effective_city.shortest_route(service.location, incident.location)
                    if effective_city is not None
                    else [service.location, incident.location]
                )
                distance = (
                    effective_city.route_distance(service.location, incident.location)
                    if effective_city is not None
                    else service.location.distance_to(incident.location)
                )
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return (
                    None,
                    f"Older proposal is no longer feasible: service {service.id!r} "
                    f"cannot reach incident {incident.id!r}.",
                )
            updated_dispatches.append(
                replace(
                    dispatch,
                    estimated_arrival=distance / service.speed,
                    route=tuple(route),
                )
            )
        return replace(plan, assignments=tuple(updated_dispatches)), ""

    def _validate_plan(self, plan: ResponsePlan) -> None:
        self._validate_assignments(plan)
        effective_city = self.planner.state_agent.city_with_evidence(
            self.world.city,
            plan.claim_resolutions,
        )
        incidents_by_id = {incident.id: incident for incident in self.world.incidents}
        services_by_id = {service.id: service for service in self.world.services}
        for dispatch in plan.assignments:
            service = services_by_id[dispatch.service_id]
            incident = incidents_by_id[dispatch.incident_id]
            try:
                expected_route = (
                    effective_city.shortest_route(service.location, incident.location)
                    if effective_city is not None
                    else [service.location, incident.location]
                )
            except (nx.NetworkXNoPath, nx.NodeNotFound) as error:
                raise ValueError(
                    f"Service {service.id!r} has no route to incident {incident.id!r}."
                ) from error
            if dispatch.route != tuple(expected_route):
                raise ValueError(
                    f"Dispatch route for service {service.id!r} does not match "
                    "the current road state."
                )

    def _validate_assignments(self, plan: ResponsePlan) -> None:
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
        applicable_resolutions = plan.claim_resolutions
        if plan.world_version != self.world.version:
            applicable_resolutions = tuple(
                resolution
                for resolution in plan.claim_resolutions
                if resolution.selected_claim is not None
                and self.planner.state_agent.arbitrator.is_authoritative_closure(
                    resolution.selected_claim
                )
            )
        effective_city = self.planner.state_agent.city_with_evidence(
            self.world.city,
            applicable_resolutions,
        )
        pending_events: list[
            tuple[str, tuple[str, ...], str, tuple[tuple[str, str], ...]]
        ] = []
        pending_events.append(
            (
                "plan_decision",
                tuple(dispatch.incident_id for dispatch in plan.assignments),
                "Planner decision accepted for execution.",
                self._plan_audit_details(plan),
            )
        )

        for dispatch in plan.assignments:
            service = services_by_id[dispatch.service_id]
            service.assigned_incident_id = dispatch.incident_id
            incidents_by_id[dispatch.incident_id].status = IncidentStatus.ASSIGNED
            pending_events.append(
                (
                    "service_dispatched",
                    (service.id, dispatch.incident_id),
                    "Planner selected an available service that meets incident requirements.",
                    (
                        ("estimated_arrival", f"{dispatch.estimated_arrival:.3f}"),
                        (
                            "route",
                            " -> ".join(
                                f"({point.x}, {point.y})" for point in dispatch.route
                            ),
                        ),
                    ),
                )
            )

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
                        effective_city.shortest_route(service.location, incident.location)
                        if effective_city is not None
                        else [service.location, incident.location]
                    )
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                incident.status = IncidentStatus.OPEN
                for assigned in self.world.services:
                    if assigned.assigned_incident_id == incident.id:
                        assigned.assigned_incident_id = None
                pending_events.append(
                    (
                        "dispatch_released",
                        (incident.id,),
                        "No open route remained; services were released for replanning.",
                        (),
                    )
                )
                continue

            previous_location = service.location
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
            if service.location != previous_location:
                pending_events.append(
                    (
                        "service_moved",
                        (service.id, incident.id),
                        "Simulation advanced the service along its current route.",
                        (
                            (
                                "from",
                                f"({previous_location.x}, {previous_location.y})",
                            ),
                            ("to", f"({service.location.x}, {service.location.y})"),
                        ),
                    )
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
                pending_events.append(
                    (
                        "incident_resolved",
                        (incident.id,),
                        "All assigned services arrived at the incident.",
                        (),
                    )
                )
                for service in assigned_services:
                    service.assigned_incident_id = None

        self.world.tick_count += 1
        self.world.version += 1
        for kind, entity_ids, reason, details in pending_events:
            self.world._append_event(kind, entity_ids, reason, details)
        self.world._append_event(
            "simulation_tick",
            (),
            "Simulation advanced by one time step.",
        )
        return plan

    @staticmethod
    def _plan_audit_details(plan: ResponsePlan) -> tuple[tuple[str, str], ...]:
        claims = [
            {
                "subject": claim.subject,
                "attribute": claim.attribute,
                "value": claim.value,
                "source": claim.source,
                "timestamp": claim.timestamp.isoformat(),
                "world_version": claim.world_version,
                "verification_status": claim.verification_status.value,
                "confidence": claim.confidence,
                "evidence_reference": claim.evidence_reference,
            }
            for claim in plan.claims
        ]
        resolutions = [
            {
                "subject": resolution.subject,
                "attribute": resolution.attribute,
                "value": resolution.value,
                "disputed": resolution.disputed,
                "requires_review": resolution.requires_review,
                "status_unknown": resolution.status_unknown,
                "selected_evidence_reference": (
                    resolution.selected_claim.evidence_reference
                    if resolution.selected_claim is not None
                    else None
                ),
            }
            for resolution in plan.claim_resolutions
        ]
        return (
            ("proposal_version", str(plan.world_version)),
            ("rationale", json.dumps(plan.rationale)),
            ("claims", json.dumps(claims, default=str)),
            ("claim_resolutions", json.dumps(resolutions)),
            ("unknown_road_ids", json.dumps(plan.unknown_road_ids)),
        )

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
