"""Single in-memory command session shared by API requests."""

from threading import Lock

from ..coordinator import Coordinator, UnserviceableIncidentsError
from ..main import build_demo_world
from ..model import Incident, IncidentStatus
from ..planner import ResponsePlan
from ..simulation import StalePlanError


class CommandSession:
    """Owns the coordinator and serializes requests that mutate the world."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.coordinator = Coordinator(build_demo_world())
            self.last_plan: ResponsePlan | None = None
            self.notice: str | None = None

    @property
    def world(self):
        return self.coordinator.world

    def step(self) -> None:
        with self._lock:
            if all(
                incident.status == IncidentStatus.RESOLVED
                for incident in self.world.incidents
            ):
                self.notice = "All incidents are resolved."
                return
            try:
                self.last_plan = self.coordinator.step()
                self.notice = None
            except (UnserviceableIncidentsError, StalePlanError) as error:
                self.notice = str(error)

    def set_road_closed(self, road_id: str, is_closed: bool, reason: str) -> None:
        with self._lock:
            if self.world.city is None:
                raise KeyError(road_id)
            self.world.city.set_road_closed(road_id, is_closed, reason)

    def add_incident(self, incident: Incident, reason: str) -> None:
        with self._lock:
            self.world.add_incident(incident, reason)


session = CommandSession()
