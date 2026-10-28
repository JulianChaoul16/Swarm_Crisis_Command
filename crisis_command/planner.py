from dataclasses import dataclass

import networkx as nx

from .agents.authoritative_state_agent import (
    AuthoritativeStateAgent,
    Claim,
    ClaimResolution,
    EvidenceArbitrator,
    VerificationStatus,
)
from .model import (
    City,
    Coordinate,
    EmergencyService,
    Incident,
    IncidentStatus,
    Severity,
)


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
    unknown_road_ids: tuple[str, ...] = ()


class Planner:
    """Make explainable dispatch decisions using the authoritative state agent."""

    def __init__(
        self,
        evidence_arbitrator: EvidenceArbitrator | None = None,
        state_agent: AuthoritativeStateAgent | None = None,
    ) -> None:
        if evidence_arbitrator is not None and state_agent is not None:
            if evidence_arbitrator is not state_agent.arbitrator:
                raise ValueError(
                    "Planner and authoritative state agent must share one arbitrator."
                )
        self.state_agent = state_agent or AuthoritativeStateAgent(evidence_arbitrator)
        self.evidence_arbitrator = self.state_agent.arbitrator

    def plan(
        self,
        incidents: list[Incident],
        services: list[EmergencyService],
        city: City | None = None,
        world_version: int | None = None,
        claims: tuple[Claim, ...] = (),
    ) -> ResponsePlan:
        planning_version = world_version or max(
            (claim.world_version for claim in claims),
            default=1,
        )
        state_review = self.state_agent.review(claims, city, planning_version)
        effective_city = self.state_agent.city_with_evidence(
            city,
            state_review.resolutions,
        )
        open_incidents = sorted(
            (incident for incident in incidents if incident.status == IncidentStatus.OPEN),
            key=lambda incident: (-incident.severity, incident.id),
        )
        available = {service.id: service for service in services if service.available}
        assignments: list[Dispatch] = []
        unassigned: list[str] = []
        rationale: list[str] = []

        if state_review.ignored_stale_claims:
            references = ", ".join(
                claim.evidence_reference for claim in state_review.ignored_stale_claims
            )
            rationale.append(
                "Ignored stale or superseded claims for planning; retained them "
                f"in the audit record: {references}."
            )
        if not open_incidents:
            rationale.append("No open incidents require a dispatch decision.")
        if not available and open_incidents:
            rationale.append("No emergency services are currently available.")

        for incident in open_incidents:
            rationale.append(
                f"Incident {incident.id} prioritized at "
                f"{incident.severity.name.lower()} severity."
            )
        for resolution in state_review.resolutions:
            if resolution.requires_review:
                rationale.append(
                    f"Review required for {resolution.subject}.{resolution.attribute}; "
                    "conflicting or disputed evidence was recorded."
                )
            if resolution.status_unknown:
                rationale.append(
                    f"Unknown road status for {resolution.subject}; "
                    "the road is blocked pending verification."
                )
            elif resolution.disputed:
                rationale.append(
                    f"Conflicting evidence for {resolution.subject}.{resolution.attribute}; "
                    "the claim is unresolved."
                )
            elif resolution.selected_claim is not None:
                selected = resolution.selected_claim
                rationale.append(
                    f"Used {selected.source} evidence for "
                    f"{resolution.subject}.{resolution.attribute} "
                    f"({selected.verification_status.value}, "
                    f"reliability {self.evidence_arbitrator.source_reliability(selected.source):.2f}, "
                    f"confidence {selected.confidence:.2f}, version "
                    f"{selected.world_version}, evidence {selected.evidence_reference})."
                )
        if state_review.unknown_road_ids:
            rationale.append(
                "Blocked unknown road status for: "
                f"{', '.join(state_review.unknown_road_ids)}."
            )

        def route_for(
            service: EmergencyService,
            incident: Incident,
        ) -> tuple[tuple[Coordinate, ...], float] | None:
            if effective_city is None:
                if incident.severity >= Severity.HIGH:
                    return None
                distance = service.location.distance_to(incident.location)
                return (service.location, incident.location), distance
            try:
                route = tuple(
                    effective_city.shortest_route(service.location, incident.location)
                )
                distance = effective_city.route_distance(
                    service.location,
                    incident.location,
                )
                return route, distance
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return None

        for incident in open_incidents:
            remaining_capabilities = set(incident.required_services)
            selected: list[EmergencyService] = []
            selected_routes: dict[
                str, tuple[tuple[Coordinate, ...], float]
            ] = {}

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
                if effective_city is None and incident.severity >= Severity.HIGH:
                    reason = "safety-critical routing is blocked because road status is unknown"
                else:
                    reason = (
                        "no reachable available services cover "
                        f"{', '.join(sorted(remaining_capabilities))}"
                    )
                rationale.append(f"Incident {incident.id} left unassigned; {reason}.")
                continue

            if not incident.required_services:
                candidate_routes = {
                    service.id: route
                    for service in available.values()
                    if (route := route_for(service, incident)) is not None
                }
                candidates = [
                    service
                    for service in available.values()
                    if service.id in candidate_routes
                ]
                if not candidates:
                    unassigned.append(incident.id)
                    reason = (
                        "safety-critical routing is blocked because road status is unknown"
                        if effective_city is None and incident.severity >= Severity.HIGH
                        else "no available service has a reachable route"
                    )
                    rationale.append(f"Incident {incident.id} left unassigned; {reason}.")
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
            assignments=tuple(assignments),
            unassigned_incident_ids=tuple(unassigned),
            world_version=world_version,
            claims=state_review.claims,
            claim_resolutions=state_review.resolutions,
            rationale=tuple(rationale),
            unknown_road_ids=state_review.unknown_road_ids,
        )


__all__ = [
    "AuthoritativeStateAgent",
    "Claim",
    "ClaimResolution",
    "Dispatch",
    "EvidenceArbitrator",
    "Planner",
    "ResponsePlan",
    "VerificationStatus",
]
