from dataclasses import dataclass
from enum import Enum, IntEnum
from math import hypot, isfinite


@dataclass(frozen=True)
class Coordinate:
    x: float
    y: float

    def __post_init__(self) -> None:
        if not isfinite(self.x) or not isfinite(self.y):
            raise ValueError("Coordinate values must be finite.")

    def distance_to(self, other: Coordinate) -> float:
        return hypot(self.x - other.x, self.y - other.y)


class Severity(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class IncidentStatus(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    RESOLVED = "resolved"


@dataclass
class Incident:
    id: str
    kind: str
    location: Coordinate
    severity: Severity
    required_services: frozenset[str] = frozenset()
    status: IncidentStatus = IncidentStatus.OPEN

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Incident id cannot be empty.")
        if not self.kind.strip():
            raise ValueError("Incident kind cannot be empty.")
        if any(not service.strip() for service in self.required_services):
            raise ValueError("Required service names cannot be empty.")


@dataclass
class EmergencyService:
    id: str
    name: str
    location: Coordinate
    capabilities: frozenset[str]
    speed: float = 1.0
    assigned_incident_id: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Emergency service id cannot be empty.")
        if not self.name.strip():
            raise ValueError("Emergency service name cannot be empty.")
        if not isfinite(self.speed) or self.speed <= 0:
            raise ValueError("Emergency service speed must be finite and greater than zero.")
        if any(not capability.strip() for capability in self.capabilities):
            raise ValueError("Capability names cannot be empty.")

    @property
    def available(self) -> bool:
        return self.assigned_incident_id is None
