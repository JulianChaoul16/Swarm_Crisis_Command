import json
from dataclasses import dataclass, field

from ..agents.authoritative_state_agent import AuthoritativeStateAgent, Claim
from ..planner import Planner, ResponsePlan
from ..simulation import Simulation, World


class UnserviceableIncidentsError(RuntimeError):
    def __init__(self, incident_ids: tuple[str, ...]) -> None:
        self.incident_ids = incident_ids
        joined_ids = ", ".join(incident_ids)
        super().__init__(f"No available services can handle incidents: {joined_ids}.")


@dataclass
class Coordinator:
    world: World
    planner: Planner = field(default_factory=Planner)
    state_agent: AuthoritativeStateAgent | None = None
    _simulation: Simulation = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.state_agent is None:
            self.state_agent = self.planner.state_agent
        elif self.planner.state_agent is not self.state_agent:
            self.planner = Planner(state_agent=self.state_agent)
        self._simulation = Simulation(self.world, self.planner)

    def step(self, claims: tuple[Claim, ...] = ()) -> ResponsePlan:
        preview = self.world.snapshot()
        proposed_plan = self.planner.plan(
            list(preview.incidents),
            list(preview.services),
            preview.city,
            world_version=preview.version,
            claims=claims,
        )
        if (
            proposed_plan.unassigned_incident_ids
            and not proposed_plan.assignments
            and all(service.available for service in preview.services)
        ):
            with self.world._lock:
                details = self._simulation._plan_audit_details(proposed_plan)
                self.world._append_event(
                    "plan_blocked",
                    proposed_plan.unassigned_incident_ids,
                    "No safety-approved dispatch could be produced.",
                    details
                    + (
                        (
                            "blocked_incident_ids",
                            json.dumps(proposed_plan.unassigned_incident_ids),
                        ),
                    ),
                )
            raise UnserviceableIncidentsError(proposed_plan.unassigned_incident_ids)

        return self._simulation.step(
            snapshot=preview,
            plan=proposed_plan,
            claims=claims,
        )
