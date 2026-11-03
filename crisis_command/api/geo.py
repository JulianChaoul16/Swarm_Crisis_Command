"""Project the scenario's road graph onto Richmond, VA for display.

The planner works on the scenario's simplified graph in abstract grid units. For the
map, each flood-scenario place is pinned to a real Richmond location and each road is
drawn along real streets (`richmond_geo.json`, built by `tools/build_richmond_geo.py`
from OpenStreetMap data). Points part-way along a road, such as a moving unit, are
placed at the same fraction along the street geometry. Points that are not on the flood
graph fall back to a plain linear projection.
"""

import json
from math import cos, hypot, radians
from pathlib import Path

from ..model import Coordinate
from ..operations.scenario import DEMO_EXTRA_ROADS, FLOOD_PLACES, FLOOD_ROADS

ORIGIN_LNG = -77.4560
ORIGIN_LAT = 37.5300
DEGREES_LNG_PER_UNIT = 0.0050
DEGREES_LAT_PER_UNIT = 0.0040
_EPSILON = 1e-6
_LNG_SCALE = cos(radians(37.53))

_GEOMETRY = json.loads(Path(__file__).with_name("richmond_geo.json").read_text(encoding="utf-8"))
_PLACES: dict[Coordinate, list[float]] = {
    point: _GEOMETRY["places"][name] for name, point in FLOOD_PLACES.items()
}
# road id -> (grid start, grid end, street polyline from start to end)
_ROADS: dict[str, tuple[Coordinate, Coordinate, list[list[float]]]] = {
    road_id: (FLOOD_PLACES[start], FLOOD_PLACES[end], _GEOMETRY["roads"][road_id])
    for road_id, start, end, _closed in FLOOD_ROADS + DEMO_EXTRA_ROADS
}
LABELS: dict[str, list[float]] = _GEOMETRY["labels"]


def _linear(point: Coordinate) -> list[float]:
    return [
        round(ORIGIN_LNG + point.x * DEGREES_LNG_PER_UNIT, 6),
        round(ORIGIN_LAT + point.y * DEGREES_LAT_PER_UNIT, 6),
    ]


def _fraction(point: Coordinate, start: Coordinate, end: Coordinate) -> float | None:
    """How far along start -> end the point lies, or None if it is not on that segment."""
    dx, dy = end.x - start.x, end.y - start.y
    t = ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)
    if not -_EPSILON <= t <= 1 + _EPSILON:
        return None
    if hypot(start.x + t * dx - point.x, start.y + t * dy - point.y) > 1e-4:
        return None
    return min(max(t, 0.0), 1.0)


def _cumulative(line: list[list[float]]) -> list[float]:
    totals = [0.0]
    for (lng1, lat1), (lng2, lat2) in zip(line, line[1:]):
        totals.append(totals[-1] + hypot((lng2 - lng1) * _LNG_SCALE, lat2 - lat1))
    return totals


def _at(line: list[list[float]], totals: list[float], distance: float) -> list[float]:
    for index in range(1, len(line)):
        if totals[index] >= distance:
            span = totals[index] - totals[index - 1] or 1.0
            share = (distance - totals[index - 1]) / span
            (lng1, lat1), (lng2, lat2) = line[index - 1], line[index]
            return [round(lng1 + (lng2 - lng1) * share, 6), round(lat1 + (lat2 - lat1) * share, 6)]
    return line[-1]


def _slice(line: list[list[float]], start: float, end: float) -> list[list[float]]:
    """The part of a polyline between two fractions of its length, in travel order."""
    if start > end:
        return list(reversed(_slice(line, end, start)))
    totals = _cumulative(line)
    a, b = start * totals[-1], end * totals[-1]
    inner = [point for point, total in zip(line, totals) if a < total < b]
    return [_at(line, totals, a), *inner, _at(line, totals, b)]


def _on_road(point: Coordinate) -> tuple[list[list[float]], float] | None:
    for start, end, line in _ROADS.values():
        t = _fraction(point, start, end)
        if t is not None:
            return line, t
    return None


def to_lng_lat(point: Coordinate) -> list[float]:
    if point in _PLACES:
        return _PLACES[point]
    found = _on_road(point)
    if found is None:
        return _linear(point)
    line, t = found
    return _at(line, _cumulative(line), t * _cumulative(line)[-1])


def to_line(points: tuple[Coordinate, ...] | list[Coordinate]) -> list[list[float]]:
    """Draw a sequence of graph points, following street geometry between them."""
    points = list(points)
    if len(points) < 2:
        return [to_lng_lat(point) for point in points]
    drawn: list[list[float]] = []
    for a, b in zip(points, points[1:]):
        segment = None
        for start, end, line in _ROADS.values():
            ta, tb = _fraction(a, start, end), _fraction(b, start, end)
            if ta is not None and tb is not None:
                segment = _slice(line, ta, tb)
                break
        if segment is None:
            segment = [to_lng_lat(a), to_lng_lat(b)]
        drawn.extend(segment[1:] if drawn and drawn[-1] == segment[0] else segment)
    return drawn
