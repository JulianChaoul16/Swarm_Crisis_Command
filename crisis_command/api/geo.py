"""Project the synthetic city grid onto map coordinates for display.

The simulation works in abstract grid units. The dashboard places that grid over a
Richmond-inspired area purely for visual context; positions are not real locations.
"""

from ..model import Coordinate

ORIGIN_LNG = -77.4560
ORIGIN_LAT = 37.5300
DEGREES_LNG_PER_UNIT = 0.0050
DEGREES_LAT_PER_UNIT = 0.0040


def to_lng_lat(point: Coordinate) -> list[float]:
    return [
        round(ORIGIN_LNG + point.x * DEGREES_LNG_PER_UNIT, 6),
        round(ORIGIN_LAT + point.y * DEGREES_LAT_PER_UNIT, 6),
    ]


def to_line(points: tuple[Coordinate, ...] | list[Coordinate]) -> list[list[float]]:
    return [to_lng_lat(point) for point in points]
