from dataclasses import dataclass, field

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
    _simulation: Simulation = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._simulation = Simulation(self.world, self.planner)

    def step(self) -> ResponsePlan:
        preview = self.world.snapshot()
        proposed_plan = self.planner.plan(
            list(preview.incidents),
            list(preview.services),
            preview.city,
            world_version=preview.version,
        )
        if (
            proposed_plan.unassigned_incident_ids
            and not proposed_plan.assignments
            and all(service.available for service in preview.services)
        ):
            raise UnserviceableIncidentsError(proposed_plan.unassigned_incident_ids)

        return self._simulation.step(snapshot=preview, plan=proposed_plan)
