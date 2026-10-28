"""Plan proposals: lifecycle, validation results, and differences from the prior proposal."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ..agents.specialists import AgentFinding
from ..model import City
from ..planner import ResponsePlan
from .feasibility import Check, closed_roads_on_route


class PlanStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


@dataclass(frozen=True)
class InvalidatedMission:
    """A committed mission released because no open route could complete it."""

    incident_id: str
    service_ids: tuple[str, ...]
    blocked_service_ids: tuple[str, ...]
    cause: str


@dataclass(frozen=True)
class AssignmentChange:
    service_id: str
    incident_id: str | None
    # invalidated | reassigned | replacement | dropped | new | rerouted | unchanged
    change: str
    previous_incident_id: str | None
    explanation: str


@dataclass
class PlanProposal:
    id: str
    run_id: str
    world_version: int
    state_signature: tuple[object, ...]
    plan: ResponsePlan
    checks: tuple[Check, ...]
    changes: tuple[AssignmentChange, ...]
    agent_findings: tuple[AgentFinding, ...]
    created_at: datetime
    previous_plan_id: str | None = None
    invalidated: tuple[InvalidatedMission, ...] = ()
    status: PlanStatus = PlanStatus.PROPOSED
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None

    @property
    def valid(self) -> bool:
        return all(check.passed for check in self.checks)


CHANGE_ORDER = {
    "invalidated": 0,
    "reassigned": 1,
    "replacement": 2,
    "dropped": 3,
    "new": 4,
    "rerouted": 5,
    "unchanged": 6,
}


def _selection_reason(plan: ResponsePlan, service_id: str, incident_id: str) -> str:
    prefix = f"Selected service {service_id} for incident {incident_id}"
    return next((line for line in plan.rationale if line.startswith(prefix)), "")


def _unmet_reason(plan: ResponsePlan, incident_id: str) -> str:
    prefix = f"Incident {incident_id} left unassigned; "
    line = next((line for line in plan.rationale if line.startswith(prefix)), "")
    return line[len(prefix):].rstrip(".")


def diff_plans(
    plan: ResponsePlan,
    previous: ResponsePlan | None,
    city: City | None = None,
    invalidated: tuple[InvalidatedMission, ...] = (),
) -> tuple[AssignmentChange, ...]:
    """Compare a proposal with the previous one and with missions released since then."""
    before = {d.service_id: d for d in previous.assignments} if previous else {}
    after = {d.service_id: d for d in plan.assignments}
    earlier_by_incident: dict[str, list[str]] = {}
    for dispatch in before.values():
        earlier_by_incident.setdefault(dispatch.incident_id, []).append(dispatch.service_id)
    for mission in invalidated:
        earlier_by_incident.setdefault(mission.incident_id, []).extend(mission.service_ids)

    def blocked(service_id: str) -> list[str]:
        dispatch = before.get(service_id)
        return closed_roads_on_route(city, dispatch.route) if dispatch else []

    def outcome(incident_id: str) -> str:
        covering = sorted(s for s, d in after.items() if d.incident_id == incident_id)
        if covering:
            return f"{incident_id} is now covered by {', '.join(covering)}."
        if incident_id in plan.unassigned_incident_ids:
            reason = _unmet_reason(plan, incident_id)
            return f"{incident_id} is now unmet: {reason}." if reason else f"{incident_id} is now unmet."
        return f"{incident_id} no longer needs a new dispatch."

    changes: list[AssignmentChange] = []
    for mission in invalidated:
        for service_id in mission.service_ids:
            cause = (
                mission.cause
                if service_id in mission.blocked_service_ids
                else f"Released with {', '.join(mission.blocked_service_ids)}, which lost its route."
            )
            changes.append(
                AssignmentChange(service_id, None, "invalidated", mission.incident_id, cause)
            )

    for service_id, dispatch in after.items():
        incident_id = dispatch.incident_id
        selection = _selection_reason(plan, service_id, incident_id)
        earlier = before.get(service_id)
        if earlier is not None and earlier.incident_id == incident_id:
            if earlier.route != dispatch.route:
                change = "rerouted"
                explanation = (
                    f"Same mission, new route: ETA {earlier.estimated_arrival:.1f} -> "
                    f"{dispatch.estimated_arrival:.1f}"
                    + (f" (avoids closed {', '.join(blocked(service_id))})." if blocked(service_id) else ".")
                )
            else:
                change, explanation = "unchanged", selection
            changes.append(AssignmentChange(service_id, incident_id, change, incident_id, explanation))
        elif earlier is not None:
            roads = blocked(service_id)
            explanation = (
                f"Moved from {earlier.incident_id} to {incident_id}"
                + (f"; its earlier route used closed {', '.join(roads)}" if roads else "")
                + f". {selection}"
            )
            changes.append(
                AssignmentChange(service_id, incident_id, "reassigned", earlier.incident_id, explanation)
            )
        else:
            replaced = [s for s in earlier_by_incident.get(incident_id, []) if s not in after or after[s].incident_id != incident_id]
            if replaced:
                causes = [f"{s}: route used closed {', '.join(blocked(s))}" for s in replaced if blocked(s)]
                explanation = (
                    f"Replaces {', '.join(replaced)} on {incident_id}"
                    + (f" ({'; '.join(causes)})" if causes else "")
                    + f". {selection}"
                )
                changes.append(AssignmentChange(service_id, incident_id, "replacement", None, explanation))
            else:
                changes.append(AssignmentChange(service_id, incident_id, "new", None, selection))

    for service_id, dispatch in before.items():
        if service_id in after:
            continue
        roads = blocked(service_id)
        explanation = (
            (f"Route to {dispatch.incident_id} used closed {', '.join(roads)}. " if roads else "")
            + outcome(dispatch.incident_id)
        )
        changes.append(AssignmentChange(service_id, None, "dropped", dispatch.incident_id, explanation))

    return tuple(sorted(changes, key=lambda item: (CHANGE_ORDER[item.change], item.service_id)))
