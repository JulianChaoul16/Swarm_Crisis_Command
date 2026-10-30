from dataclasses import dataclass
from typing import Callable

import networkx as nx
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

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


def default_priority(incident: Incident) -> tuple[object, ...]:
    return (-incident.severity, -incident.people, incident.id)


DEFAULT_PRIORITY_RULE = "severity (critical first), then people affected, then incident id"
SEVERITY_WEIGHT = {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 4, Severity.CRITICAL: 8}
# Tie-breaker cost per dispatched unit, so a unit with zero ETA is not sent when unneeded.
UNIT_COST = 1e-3


class Planner:
    """Make explainable dispatch decisions using the authoritative state agent.

    Assignment is solved exactly (see `_optimize`): the served set follows the priority order,
    then units are chosen to minimize severity-weighted ETA. The findings test showed the
    earlier greedy pass missed the best plan in 20% of scenarios. The priority rule is a
    simulator setting, not validated triage doctrine; pass `priority_key` and `priority_rule`.
    """

    def __init__(
        self,
        evidence_arbitrator: EvidenceArbitrator | None = None,
        state_agent: AuthoritativeStateAgent | None = None,
        priority_key: Callable[[Incident], tuple[object, ...]] = default_priority,
        priority_rule: str = DEFAULT_PRIORITY_RULE,
    ) -> None:
        self.priority_key = priority_key
        self.priority_rule = priority_rule
        if evidence_arbitrator is not None and state_agent is not None:
            if evidence_arbitrator is not state_agent.arbitrator:
                raise ValueError(
                    "Planner and authoritative state agent must share one arbitrator."
                )
        self.state_agent = state_agent or AuthoritativeStateAgent(evidence_arbitrator)
        self.evidence_arbitrator = self.state_agent.arbitrator

    @staticmethod
    def _relevant(service: EmergencyService, incident: Incident) -> bool:
        if service.capabilities & incident.required_services:
            return True
        if incident.people > 0 and service.capacity > 0:
            return True
        return not incident.required_services and incident.people == 0

    def _optimize(self, incidents, available, route_for) -> dict:
        """Exact assignment for incidents already sorted by priority.

        An incident is served only when its units cover every required capability and can carry
        all its people; each unit serves at most one incident. Stage 1 fixes the served set
        lexicographically: each incident in priority order is served if that stays feasible
        with every higher-priority choice. Stage 2 minimizes severity-weighted ETA for that set.
        Returns {incident_id: [(service, (route, distance)), ...]} for served incidents.
        """
        if not incidents:
            return {}
        pairs = []  # (incident index, service, route, distance, eta)
        for index, incident in enumerate(incidents):
            for service in available.values():
                if not self._relevant(service, incident):
                    continue
                found = route_for(service, incident)
                if found is not None:
                    pairs.append((index, service, found[0], found[1], found[1] / service.speed))

        pair_count = len(pairs)
        size = pair_count + len(incidents)
        rows, lower, upper = [], [], []

        def constrain(coefficients: dict[int, float], low: float, high: float) -> None:
            row = np.zeros(size)
            for column, value in coefficients.items():
                row[column] += value
            rows.append(row)
            lower.append(low)
            upper.append(high)

        for service_id in {pair[1].id for pair in pairs}:
            constrain({p: 1 for p, pair in enumerate(pairs) if pair[1].id == service_id}, 0, 1)
        for p, pair in enumerate(pairs):
            constrain({p: 1, pair_count + pair[0]: -1}, -np.inf, 0)
        for index, incident in enumerate(incidents):
            served = pair_count + index
            own = [p for p, pair in enumerate(pairs) if pair[0] == index]
            for capability in incident.required_services:
                constrain({**{p: 1 for p in own if capability in pairs[p][1].capabilities},
                           served: -1}, 0, np.inf)
            if incident.people > 0:
                constrain({**{p: pairs[p][1].capacity for p in own}, served: -incident.people},
                          0, np.inf)
            if not incident.required_services and incident.people == 0:
                constrain({**{p: 1 for p in own}, served: -1}, 0, np.inf)
        constraints = LinearConstraint(np.array(rows), lower, upper)

        def solve(served_bounds: list[tuple[int, int]], cost: np.ndarray):
            low, high = np.zeros(size), np.ones(size)
            for index, (floor, ceiling) in enumerate(served_bounds):
                low[pair_count + index], high[pair_count + index] = floor, ceiling
            result = milp(cost, constraints=constraints, integrality=np.ones(size),
                          bounds=Bounds(low, high))
            return result.x if result.success else None

        bounds: list[tuple[int, int]] = [(0, 0)] * len(incidents)
        for index in range(len(incidents)):
            trial = [*bounds[:index], (1, 1), *bounds[index + 1:]]
            if solve(trial, np.zeros(size)) is not None:
                bounds = trial

        cost = np.zeros(size)
        for p, (index, _, _, _, eta) in enumerate(pairs):
            cost[p] = SEVERITY_WEIGHT[incidents[index].severity] * eta + UNIT_COST
        values = solve(bounds, cost)
        if values is None:
            raise RuntimeError("Assignment solver failed on a feasible served set.")

        solution: dict[str, list] = {}
        for p, (index, service, route, distance, eta) in enumerate(pairs):
            if values[p] > 0.5:
                solution.setdefault(incidents[index].id, []).append(
                    (eta, service.id, service, (route, distance)))
        return {
            incident_id: [(service, found) for _, _, service, found in sorted(chosen, key=lambda c: c[:2])]
            for incident_id, chosen in solution.items()
        }

    def _unmet_reason(self, incident, services, solution, higher, route_for, effective_city) -> str:
        if effective_city is None and incident.severity >= Severity.HIGH:
            return "safety-critical routing is blocked because road status is unknown"
        planned = {service.id: incident_id for incident_id, chosen in solution.items()
                   for service, _ in chosen}
        reachable = [service for service in services
                     if service.available and route_for(service, incident) is not None]
        if not incident.required_services and incident.people == 0 and not reachable:
            return "no available service has a reachable route"

        def missing_from(units) -> list[str]:
            return sorted(capability for capability in incident.required_services
                          if not any(capability in unit.capabilities for unit in units))

        def shortage(capabilities) -> str:
            return "; ".join(
                self._shortage_reason(capability, incident, services, planned, higher, route_for)
                for capability in capabilities
            )

        # First: could it be served even if no other incident needed anything?
        if missing := missing_from(reachable):
            return shortage(missing)
        carried = sum(service.capacity for service in reachable)
        if carried < incident.people:
            return f"reachable units can carry {carried} of {incident.people} people"
        # Otherwise other incidents took the units it needed.
        free = [service for service in reachable if service.id not in planned]
        if missing := missing_from(free):
            return shortage(missing)
        carriers = sorted({planned[service.id] for service in reachable
                           if service.capacity > 0 and planned.get(service.id) in higher})
        if carriers:
            return (f"units that could carry its {incident.people} people are committed to "
                    f"higher-priority {', '.join(carriers)}")
        return "the units that could cover it are needed together for higher-priority incidents"

    @staticmethod
    def _shortage_reason(capability, incident, services, planned, higher, route_for) -> str:
        """Explain why no unit covers `capability`, naming any higher-priority tradeoff."""
        holders = [service for service in services if capability in service.capabilities]
        if not holders:
            return f"no resource provides {capability}"
        reachable = [service for service in holders if route_for(service, incident) is not None]
        competing = sorted(
            {planned[service.id] for service in reachable if planned.get(service.id) in higher}
        )
        if competing:
            return (
                f"{capability} units that can reach it are committed to higher-priority "
                f"{', '.join(competing)}"
            )
        busy = sorted(
            {service.assigned_incident_id for service in reachable if not service.available}
        )
        if busy:
            return f"{capability} units that can reach it are already on {', '.join(busy)}"
        if any(service.id in planned for service in reachable):
            return (f"{capability} units that can reach it are needed together for "
                    "higher-priority incidents")
        return f"no {capability} unit can reach it over open roads"

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
            key=self.priority_key,
        )
        available = {service.id: service for service in services if service.available}
        assignments: list[Dispatch] = []
        unassigned: list[str] = []
        rationale: list[str] = [f"Priority rule: {self.priority_rule}."]

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

        solution = self._optimize(open_incidents, available, route_for)
        if open_incidents and available:
            rationale.append(
                "Assignment solved exactly as an integer program (scipy HiGHS): serve as many "
                "incidents as possible in priority order, then minimize severity-weighted ETA."
            )
        higher: set[str] = set()
        for incident in open_incidents:
            chosen = solution.get(incident.id)
            if chosen is None:
                unassigned.append(incident.id)
                reason = self._unmet_reason(
                    incident, services, solution, higher, route_for, effective_city
                )
                rationale.append(f"Incident {incident.id} left unassigned; {reason}.")
            else:
                for service, (route, distance) in chosen:
                    eta = distance / service.speed
                    assignments.append(Dispatch(incident.id, service.id, eta, route))
                    rationale.append(
                        f"Selected service {service.id} for incident {incident.id}: "
                        f"ETA {eta:.3f}, route distance {distance:.3f}, "
                        f"capabilities {', '.join(sorted(service.capabilities)) or 'none'}."
                    )
            higher.add(incident.id)

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
    "DEFAULT_PRIORITY_RULE",
    "default_priority",
    "AuthoritativeStateAgent",
    "Claim",
    "ClaimResolution",
    "Dispatch",
    "EvidenceArbitrator",
    "Planner",
    "ResponsePlan",
    "VerificationStatus",
]
