from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from typing import Mapping

import networkx as nx

from .model import City, Coordinate, EmergencyService, Incident, IncidentStatus, Road


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    DISPUTED = "disputed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Claim:
    subject: str
    attribute: str
    value: object
    source: str
    timestamp: datetime
    world_version: int
    verification_status: VerificationStatus
    confidence: float
    evidence_reference: str

    def __post_init__(self) -> None:
        for field_name in ("subject", "attribute", "source", "evidence_reference"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"Claim {field_name} cannot be empty.")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Claim timestamp must include a timezone.")
        if self.world_version < 1:
            raise ValueError("Claim world version must be positive.")
        if not isinstance(self.verification_status, VerificationStatus):
            raise ValueError("Claim verification status is invalid.")
        if not isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("Claim confidence must be between zero and one.")


@dataclass(frozen=True)
class ClaimResolution:
    subject: str
    attribute: str
    claims: tuple[Claim, ...]
    selected_claim: Claim | None
    disputed: bool

    @property
    def value(self) -> object | None:
        return self.selected_claim.value if self.selected_claim is not None else None


class EvidenceArbitrator:
    def __init__(self, source_reliability: Mapping[str, float] | None = None) -> None:
        self._source_reliability = {"city_network": 0.5}
        self._source_reliability.update(source_reliability or {})
        for source, reliability in self._source_reliability.items():
            if not source.strip() or not isfinite(reliability) or not 0 <= reliability <= 1:
                raise ValueError("Source reliability must be between zero and one.")

    def source_reliability(self, source: str) -> float:
        return self._source_reliability.get(source, 0.5)

    def resolve(self, claims: tuple[Claim, ...]) -> tuple[ClaimResolution, ...]:
        grouped: dict[tuple[str, str], list[Claim]] = {}
        for claim in claims:
            grouped.setdefault((claim.subject, claim.attribute), []).append(claim)

        resolutions: list[ClaimResolution] = []
        for (subject, attribute), group in sorted(grouped.items()):
            eligible = [
                claim
                for claim in group
                if claim.verification_status != VerificationStatus.REJECTED
            ]
            if not eligible:
                resolutions.append(
                    ClaimResolution(subject, attribute, tuple(group), None, True)
                )
                continue

            def evidence_rank(claim: Claim) -> tuple[int, float, float, float]:
                verification = {
                    VerificationStatus.VERIFIED: 2,
                    VerificationStatus.UNVERIFIED: 1,
                    VerificationStatus.DISPUTED: 0,
                    VerificationStatus.REJECTED: -1,
                }[claim.verification_status]
                reliability = self.source_reliability(claim.source)
                return (
                    verification,
                    reliability,
                    claim.confidence,
                    claim.timestamp.timestamp(),
                )

            best_rank = max(evidence_rank(claim) for claim in eligible)
            strongest = [
                claim for claim in eligible if evidence_rank(claim) == best_rank
            ]
            values = [claim.value for claim in strongest]
            disputed = (
                any(value != values[0] for value in values[1:])
                or all(
                    claim.verification_status == VerificationStatus.DISPUTED
                    for claim in strongest
                )
            )
            selected = None if disputed else min(
                strongest,
                key=lambda claim: (claim.source, claim.evidence_reference),
            )
            resolutions.append(
                ClaimResolution(subject, attribute, tuple(group), selected, disputed)
            )
        return tuple(resolutions)


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
    world_version: int | None = None
    claims: tuple[Claim, ...] = ()
    claim_resolutions: tuple[ClaimResolution, ...] = ()
    rationale: tuple[str, ...] = ()


class Planner:
    """Prioritize incidents and allocate the closest available capable services."""

    def __init__(self, evidence_arbitrator: EvidenceArbitrator | None = None) -> None:
        self.evidence_arbitrator = evidence_arbitrator or EvidenceArbitrator()

    def plan(
        self,
        incidents: list[Incident],
        services: list[EmergencyService],
        city: City | None = None,
        world_version: int | None = None,
        claims: tuple[Claim, ...] = (),
    ) -> ResponsePlan:
        if world_version is not None and any(
            claim.world_version > world_version for claim in claims
        ):
            raise ValueError("A claim cannot come from a future world version.")
        road_state_claims = self._road_state_claims(city, world_version)
        all_claims = claims + road_state_claims
        current_claims = tuple(
            claim for claim in all_claims
            if world_version is None or claim.world_version == world_version
        )
        stale_claims = tuple(
            claim
            for claim in all_claims
            if world_version is not None and claim.world_version != world_version
        )
        claim_resolutions = self.evidence_arbitrator.resolve(current_claims)
        city = self.city_with_evidence(city, claim_resolutions)
        open_incidents = sorted(
            (incident for incident in incidents if incident.status == IncidentStatus.OPEN),
            key=lambda incident: (-incident.severity, incident.id),
        )
        available = {service.id: service for service in services if service.available}
        assignments: list[Dispatch] = []
        unassigned: list[str] = []
        rationale: list[str] = []
        if stale_claims:
            rationale.append(
                f"Excluded {len(stale_claims)} claim(s) analyzed against an older world version."
            )
        if stale_claims:
            stale_references = ", ".join(
                claim.evidence_reference for claim in stale_claims
            )
            rationale.append(f"Excluded stale evidence references: {stale_references}.")
        for incident in open_incidents:
            rationale.append(
                f"Incident {incident.id} prioritized at {incident.severity.name.lower()} severity."
            )
        for resolution in claim_resolutions:
            if resolution.disputed:
                outcome = "treated conservatively as closed" if (
                    resolution.attribute == "is_closed"
                ) else "left unresolved"
                rationale.append(
                    f"Unresolved evidence for {resolution.subject}.{resolution.attribute}; {outcome}."
                )
            elif resolution.selected_claim is not None:
                action = (
                    "Used"
                    if resolution.attribute == "is_closed"
                    else "Recorded"
                )
                rationale.append(
                    f"{action} {resolution.selected_claim.source} evidence for "
                    f"{resolution.subject}.{resolution.attribute} "
                    f"({resolution.selected_claim.verification_status.value}, "
                    f"source reliability "
                    f"{self.evidence_arbitrator.source_reliability(resolution.selected_claim.source):.2f}, "
                    f"confidence {resolution.selected_claim.confidence:.2f}, "
                    f"version {resolution.selected_claim.world_version}, "
                    f"evidence {resolution.selected_claim.evidence_reference})."
                )

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
                rationale.append(
                    f"Incident {incident.id} left unassigned; no reachable available "
                    f"services cover {', '.join(sorted(remaining_capabilities))}."
                )
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
                    rationale.append(
                        f"Incident {incident.id} left unassigned; no available service "
                        "has a reachable route."
                    )
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
                rationale.append(
                    f"Selected service {service.id} for incident {incident.id}: "
                    f"ETA {eta:.3f}, route distance {distance:.3f}, "
                    f"capabilities {', '.join(sorted(service.capabilities)) or 'none'}."
                )
                del available[service.id]

        return ResponsePlan(
            tuple(assignments),
            tuple(unassigned),
            world_version=world_version,
            claims=all_claims,
            claim_resolutions=claim_resolutions,
            rationale=tuple(rationale),
        )

    @staticmethod
    def _road_state_claims(
        city: City | None,
        world_version: int | None,
    ) -> tuple[Claim, ...]:
        if city is None:
            return ()
        observed_at = datetime.now(timezone.utc)
        version = world_version or 1
        return tuple(
            Claim(
                subject=road.id,
                attribute="is_closed",
                value=road.is_closed,
                source="city_network",
                timestamp=observed_at,
                world_version=version,
                verification_status=VerificationStatus.VERIFIED,
                confidence=0.75,
                evidence_reference=f"city:{city.name}:road:{road.id}",
            )
            for road in city.roads
        )

    @staticmethod
    def city_with_evidence(
        city: City | None,
        resolutions: tuple[ClaimResolution, ...],
    ) -> City | None:
        road_updates = {
            resolution.subject: resolution
            for resolution in resolutions
            if resolution.attribute == "is_closed"
        }
        if city is None:
            if road_updates:
                raise ValueError("Road status claims require a city road network.")
            return None
        if not road_updates:
            return city
        road_ids = {road.id for road in city.roads}
        unknown_roads = set(road_updates) - road_ids
        if unknown_roads:
            raise ValueError(
                f"Road status claims reference unknown roads: {', '.join(sorted(unknown_roads))}."
            )
        updated_city = City(city.name)
        for road in city.roads:
            resolution = road_updates.get(road.id)
            is_closed = road.is_closed
            if resolution is not None:
                if (
                    not resolution.disputed
                    and resolution.selected_claim is not None
                    and not isinstance(resolution.value, bool)
                ):
                    raise ValueError(
                        f"Road closure claim for {road.id!r} must be boolean."
                    )
                is_closed = (
                    True
                    if resolution.disputed
                    else bool(resolution.value)
                )
            updated_city.add_road(
                Road(road.id, road.start, road.end, is_closed=is_closed)
            )
        return updated_city