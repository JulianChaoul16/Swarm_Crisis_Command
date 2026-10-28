"""Plan proposals: lifecycle, validation results, and differences from the prior proposal."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ..agents.specialists import AgentFinding
from ..planner import ResponsePlan
from .feasibility import Check


class PlanStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


@dataclass(frozen=True)
class AssignmentChange:
    service_id: str
    incident_id: str | None
    change: str  # new | unchanged | reassigned | dropped
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
    status: PlanStatus = PlanStatus.PROPOSED
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None

    @property
    def valid(self) -> bool:
        return all(check.passed for check in self.checks)


def _selection_reason(plan: ResponsePlan, service_id: str, incident_id: str) -> str:
    prefix = f"Selected service {service_id} for incident {incident_id}"
    return next((line for line in plan.rationale if line.startswith(prefix)), "")


def diff_plans(plan: ResponsePlan, previous: ResponsePlan | None) -> tuple[AssignmentChange, ...]:
    before = {dispatch.service_id: dispatch.incident_id for dispatch in previous.assignments} if previous else {}
    after = {dispatch.service_id: dispatch.incident_id for dispatch in plan.assignments}
    changes: list[AssignmentChange] = []

    for service_id, incident_id in after.items():
        previous_incident = before.get(service_id)
        if previous_incident == incident_id:
            change = "unchanged"
        elif previous_incident is None:
            change = "new"
        else:
            change = "reassigned"
        changes.append(
            AssignmentChange(
                service_id,
                incident_id,
                change,
                previous_incident,
                _selection_reason(plan, service_id, incident_id),
            )
        )

    for service_id, incident_id in before.items():
        if service_id in after:
            continue
        if incident_id in plan.unassigned_incident_ids:
            explanation = f"{incident_id} can no longer be fully served; see unmet needs."
        elif any(dispatch.incident_id == incident_id for dispatch in plan.assignments):
            explanation = f"{incident_id} is now covered by other services."
        else:
            explanation = f"{incident_id} no longer needs a new dispatch."
        changes.append(AssignmentChange(service_id, None, "dropped", incident_id, explanation))

    order = {"reassigned": 0, "new": 1, "dropped": 2, "unchanged": 3}
    return tuple(sorted(changes, key=lambda item: (order[item.change], item.service_id)))
