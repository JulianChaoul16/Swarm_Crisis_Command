from dataclasses import dataclass
from enum import Enum, IntEnum
from math import hypot, isfinite
from typing import Callable

import networkx as nx


@dataclass(frozen=True)
class Coordinate:
    x: float
    y: float

    def __post_init__(self) -> None:
        if not isfinite(self.x) or not isfinite(self.y):
            raise ValueError("Coordinate values must be finite.")

    def distance_to(self, other: Coordinate) -> float:
        return hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class Road:
    id: str
    start: Coordinate
    end: Coordinate
    is_closed: bool = False

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Road id cannot be empty.")
        if self.start == self.end:
            raise ValueError("Road endpoints must be different.")

    @property
    def length(self) -> float:
        return self.start.distance_to(self.end)


class City:
    def __init__(self, name: str) -> None:
        if not name.strip():
            raise ValueError("City name cannot be empty.")
        self.name = name
        self.graph = nx.MultiGraph()
        self._roads: dict[str, Road] = {}
        self._on_change: Callable[[], None] | None = None

    def set_change_callback(self, callback: Callable[[], None] | None) -> None:
        self._on_change = callback

    @property
    def roads(self) -> tuple[Road, ...]:
        return tuple(self._roads.values())

    def add_road(self, road: Road) -> None:
        if road.id in self._roads:
            raise ValueError(f"Road id {road.id!r} already exists in {self.name}.")

        self.graph.add_edge(
            road.start,
            road.end,
            key=road.id,
            road_id=road.id,
            length=road.length,
            is_closed=road.is_closed,
        )
        self._roads[road.id] = road
        if self._on_change is not None:
            self._on_change()

    def set_road_closed(self, road_id: str, is_closed: bool) -> None:
        road = self._roads[road_id]
        if road.is_closed == is_closed:
            return
        self.graph[road.start][road.end][road.id]["is_closed"] = is_closed
        self._roads[road_id] = Road(
            id=road.id,
            start=road.start,
            end=road.end,
            is_closed=is_closed,
        )
        if self._on_change is not None:
            self._on_change()

    @staticmethod
    def _road_projection(point: Coordinate, road: Road) -> float | None:
        dx = road.end.x - road.start.x
        dy = road.end.y - road.start.y
        length_squared = dx * dx + dy * dy
        projection = (
            (point.x - road.start.x) * dx + (point.y - road.start.y) * dy
        ) / length_squared
        if projection < 0 or projection > 1:
            return None
        projected = Coordinate(
            road.start.x + projection * dx,
            road.start.y + projection * dy,
        )
        return projection if point.distance_to(projected) <= 1e-9 else None

    def _open_graph(
        self,
        start: Coordinate | None = None,
        end: Coordinate | None = None,
    ) -> nx.MultiGraph:
        open_roads = self.graph.copy()
        closed_edges = [
            (u, v, key)
            for u, v, key, data in open_roads.edges(keys=True, data=True)
            if data["is_closed"]
        ]
        open_roads.remove_edges_from(closed_edges)

        for road in self._roads.values():
            if road.is_closed:
                continue
            split_points = [(0.0, road.start), (1.0, road.end)]
            for point in (start, end):
                if point is None or point in (road.start, road.end):
                    continue
                projection = self._road_projection(point, road)
                if projection is not None:
                    split_points.append((projection, point))
            if len(split_points) == 2:
                continue

            edge_data = open_roads[road.start][road.end][road.id].copy()
            open_roads.remove_edge(road.start, road.end, road.id)
            split_points.sort(key=lambda item: item[0])
            for index, ((_, left), (_, right)) in enumerate(
                zip(split_points, split_points[1:])
            ):
                open_roads.add_edge(
                    left,
                    right,
                    key=f"{road.id}:segment:{index}",
                    **{**edge_data, "length": left.distance_to(right)},
                )
        return open_roads

    def shortest_route(self, start: Coordinate, end: Coordinate) -> list[Coordinate]:
        if start == end:
            return [start]
        return nx.shortest_path(self._open_graph(start, end), start, end, weight="length")

    def route_distance(self, start: Coordinate, end: Coordinate) -> float:
        if start == end:
            return 0.0
        return nx.shortest_path_length(
            self._open_graph(start, end),
            start,
            end,
            weight="length",
        )


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