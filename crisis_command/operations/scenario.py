"""Seeded scenarios loaded through the command service."""

from dataclasses import dataclass
from typing import Callable

from ..main import build_demo_world
from ..model import Coordinate
from ..simulation import World


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    build_world: Callable[[], World]
    places: dict[str, Coordinate]


DEMO_SCENARIO = Scenario(
    name="demo-city",
    description="Two-incident demo world from crisis_command.main.",
    build_world=build_demo_world,
    places={
        "Fire Station 1": Coordinate(1, 1),
        "Ambulance Station": Coordinate(1, 2),
        "Police Station": Coordinate(0, 4),
        "Junction A": Coordinate(4, 1),
        "Junction B": Coordinate(4, 2),
        "Junction C": Coordinate(4, 4),
        "Warehouse District": Coordinate(8, 5),
        "Collision Corner": Coordinate(2, 4),
    },
)
