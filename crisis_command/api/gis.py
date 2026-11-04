"""GIS layers for the operations map, their history (scrub back), a labelled trend
projection (scrub forward), and the personnel roster with the orders each unit received.

Nothing here invents data. Layers without a feed (hospital beds, shelter capacity, live
GPS, power-grid telemetry) are returned with ``None`` values and a "no feed connected" note.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import cos, pi, radians, sin
from threading import Event, Lock, Thread
from typing import Any

from ..model import IncidentStatus
from ..operations import CommandService
from ..operations.plans import PlanStatus
from .geo import to_line, to_lng_lat
from .serializers import serialize_proposal

NO_FEED = "no feed connected"
SIMULATED_POSITION = "simulated position (GPS feed not connected)"
HISTORY_WINDOW = timedelta(hours=2)
MAX_FRAMES = 900
PROJECTION_HOURS = (1, 2, 3, 4)
# Rule of thumb for the projection: each foot of projected rise adds this many points of
# flooded area to every place that is already flooded. Not a hydrologic model.
POINTS_PER_FOOT = 15
FLOOD_TRIGGER_FT = 12.0  # the sample trigger used by the Access check


def _circle(center: list[float], radius_m: float, steps: int = 40) -> list[list[float]]:
    lng, lat = center
    dlat = radius_m / 111_320
    dlng = radius_m / (111_320 * max(cos(radians(lat)), 0.01))
    ring = [[round(lng + dlng * cos(2 * pi * i / steps), 6), round(lat + dlat * sin(2 * pi * i / steps), 6)]
            for i in range(steps)]
    return ring + [ring[0]]


def _flood_zone(place: str, center: list[float], percent: int, source: str, projected: bool = False) -> dict[str, Any]:
    level = "severe" if percent >= 60 else "moderate" if percent >= 30 else "minor"
    return {"id": f"flood:{place}", "type": "flood", "place": place, "percent": percent, "level": level,
            "source": source, "projected": projected,
            "polygon": _circle(center, 120 + 6 * percent)}


def _service_kind(capabilities: frozenset[str]) -> str:
    for kind in ("rescue", "medical", "fire", "police", "utility"):
        if kind in capabilities:
            return kind
    return next(iter(sorted(capabilities)), "unit")


def gis_frame(service: CommandService) -> dict[str, Any]:
    """The map's layers right now."""
    world, board, scenario = service.world, service.board, service.scenario
    places = scenario.places
    place_of = {point: name for name, point in places.items()}
    incidents = [i for i in world.incidents if i.status != IncidentStatus.RESOLVED]

    zones = []
    for place, cover in sorted(board.flood_cover.items()):
        percent = int(cover.get("percent") or 0)
        if percent > 0 and place in places:
            zones.append(_flood_zone(place, to_lng_lat(places[place]), percent, str(cover.get("source", "Vision Agent"))))
    for incident in incidents:
        if "fire" in incident.required_services or "fire" in incident.kind.lower():
            zones.append({"id": f"fire:{incident.id}", "type": "fire", "place": place_of.get(incident.location),
                          "incident_id": incident.id, "level": incident.severity.name.lower(),
                          "source": "incident report (approximate area, not a mapped perimeter)", "projected": False,
                          "polygon": _circle(to_lng_lat(incident.location), 90 + 45 * int(incident.severity))})

    outages = [
        {"incident_id": i.id, "kind": i.kind, "place": place_of.get(i.location), "position": to_lng_lat(i.location),
         "source": "reported in incident (no grid feed connected)"}
        for i in incidents
        if "utility" in i.required_services or any(w in i.kind.lower() for w in ("power", "transformer", "outage", "line"))
    ]

    facilities = []
    for name, point in places.items():
        lowered = name.lower()
        if "hospital" in lowered:
            facilities.append({"name": name, "type": "hospital", "position": to_lng_lat(point),
                               "beds_available": None, "beds_source": NO_FEED})
        elif "shelter" in lowered:
            facilities.append({"name": name, "type": "shelter", "position": to_lng_lat(point),
                               "capacity": None, "capacity_source": NO_FEED})

    people_counts = [i.people for i in incidents if i.people]
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "run_id": service.run_id,
        "zones": zones,
        "outages": outages,
        "facilities": facilities,
        "incidents": [
            {"id": i.id, "kind": i.kind, "place": place_of.get(i.location), "position": to_lng_lat(i.location),
             "status": i.status.value, "severity": i.severity.name.lower(), "severity_level": int(i.severity),
             "people": i.people or None}
            for i in incidents
        ],
        "units": [
            {"id": s.id, "name": s.name, "kind": _service_kind(s.capabilities), "position": to_lng_lat(s.location),
             "available": s.available, "assigned_incident_id": s.assigned_incident_id,
             "position_source": SIMULATED_POSITION}
            for s in world.services
        ],
        "closed_roads": [
            {"id": r.id, "name": scenario.road_names.get(r.id, r.id), "path": to_line([r.start, r.end])}
            for r in (world.city.roads if world.city else []) if r.is_closed
        ],
        "gauge": dict(board.gauge) if board.gauge else None,
        "affected": {
            "people_reported": sum(people_counts),
            "incidents": len(incidents),
            "incidents_without_count": len(incidents) - len(people_counts),
        },
        "layer_sources": {
            "flood": "Vision Agent flood cover per place (SAM 3.1 or labelled sample)",
            "fire": "fire incidents; circle is an approximate area, not a mapped perimeter",
            "units": SIMULATED_POSITION,
            "roads": "road closures from confirmed reports",
            "power": "outages reported in incidents; " + NO_FEED + " for grid status",
            "hospitals": "bed capacity: " + NO_FEED,
        },
    }


def _fingerprint(frame: dict[str, Any]) -> str:
    import json
    return json.dumps({k: v for k, v in frame.items() if k != "at"}, sort_keys=True, default=str)


class GisHistory:
    """Records a frame whenever the map changes (polled every few seconds), for 2 hours."""

    def __init__(self, service: CommandService, interval_s: float = 2.0) -> None:
        self.service = service
        self.interval_s = interval_s
        self.frames: list[dict[str, Any]] = []
        self.gauge_readings: list[dict[str, Any]] = []
        self._last = ""
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None

    def record(self) -> None:
        with self.service._lock:
            frame = gis_frame(self.service)
        fingerprint = _fingerprint(frame)
        with self._lock:
            if self.frames and self.frames[-1]["run_id"] != frame["run_id"]:
                self.frames, self.gauge_readings, self._last = [], [], ""
            gauge = frame["gauge"]
            if gauge and gauge.get("value") is not None and (
                    not self.gauge_readings or self.gauge_readings[-1]["value"] != gauge["value"]):
                self.gauge_readings.append({"value": gauge["value"], "scenario_hour": gauge.get("scenario_hour"),
                                            "at": frame["at"]})
            if fingerprint == self._last:
                return
            self._last = fingerprint
            self.frames.append(frame)
            cutoff = datetime.now(timezone.utc) - HISTORY_WINDOW
            self.frames = [f for f in self.frames if datetime.fromisoformat(f["at"]) >= cutoff]
            if len(self.frames) > MAX_FRAMES:  # thin the oldest half, keep recent detail
                half = len(self.frames) // 2
                self.frames = self.frames[:half:2] + self.frames[half:]

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.frames)

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.record()
                except Exception:  # noqa: BLE001 - a bad frame must not stop recording
                    pass
                self._stop.wait(self.interval_s)

        self._thread = Thread(target=loop, name="gis-history", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ----- projection --------------------------------------------------------

    def gauge_rate(self) -> tuple[float | None, str]:
        """Feet per hour from the gauge readings, or None with the reason."""
        with self._lock:
            readings = list(self.gauge_readings)
        timed = [r for r in readings if r.get("scenario_hour") is not None]
        if len(timed) >= 2:
            first, last = timed[0], timed[-1]
            hours = last["scenario_hour"] - first["scenario_hour"]
            if hours > 0:
                return (last["value"] - first["value"]) / hours, f"{len(timed)} gauge readings (scenario time)"
        if len(readings) >= 2:
            first, last = readings[0], readings[-1]
            hours = (datetime.fromisoformat(last["at"]) - datetime.fromisoformat(first["at"])).total_seconds() / 3600
            if hours >= 0.25:
                return (last["value"] - first["value"]) / hours, f"{len(readings)} gauge readings"
        return None, "needs two gauge readings at least 15 minutes apart"

    def projection(self, frame: dict[str, Any]) -> dict[str, Any]:
        rate, basis = self.gauge_rate()
        gauge = frame.get("gauge") or {}
        note = (f"Trend projection, not a hydrologic forecast: the gauge rate ({basis}) is extended "
                f"linearly, and each flooded place grows {POINTS_PER_FOOT} points per foot of rise. "
                f"Incidents and units are not projected.")
        if rate is None or gauge.get("value") is None:
            return {"available": False, "reason": f"No projection: {basis}.", "note": note, "frames": []}
        places = self.service.scenario.places
        frames = []
        for hours in PROJECTION_HOURS:
            rise = max(rate * hours, 0.0)
            value = round(gauge["value"] + rate * hours, 2)
            zones = []
            for zone in frame["zones"]:
                if zone["type"] == "flood" and zone["place"] in places:
                    percent = min(100, round(zone["percent"] + POINTS_PER_FOOT * rise))
                    zones.append(_flood_zone(zone["place"], to_lng_lat(places[zone["place"]]), percent,
                                             "trend projection", projected=True))
                else:
                    zones.append(zone)
            frames.append({"hours": hours, "gauge_ft": value, "above_trigger": value >= FLOOD_TRIGGER_FT,
                           "zones": zones})
        return {"available": True, "rate_ft_per_h": round(rate, 3), "basis": basis, "note": note,
                "trigger_ft": FLOOD_TRIGGER_FT, "frames": frames}


def roster(service: CommandService) -> dict[str, Any]:
    """Each unit, its status, and the orders sent to it (from approved plans), oldest first."""
    world, scenario = service.world, service.scenario
    place_of = {point: name for name, point in scenario.places.items()}
    incidents = {i.id: i for i in world.incidents}
    approved = sorted((p for p in service._plans.values() if p.status == PlanStatus.APPROVED),
                      key=lambda p: p.decided_at or p.created_at)
    messages: dict[str, list[dict[str, Any]]] = {s.id: [] for s in world.services}
    current_order: dict[str, str] = {}
    for proposal in approved:
        plan = serialize_proposal(proposal, world.city, scenario.road_names)
        assigned = {a["service_id"]: a for a in plan["assignments"]}
        when = (proposal.decided_at or proposal.created_at).isoformat()
        by = proposal.decided_by or "operator"
        for unit_id, assignment in assigned.items():
            incident = incidents.get(assignment["incident_id"])
            where = place_of.get(incident.location) if incident else None
            text = (f"Proceed to {where or assignment['incident_id']} ({incident.kind if incident else 'incident'}), "
                    f"ETA {assignment['estimated_arrival']:.1f} min"
                    + (f" via {', '.join(assignment['via'])}" if assignment.get("via") else ""))
            if current_order.get(unit_id) not in (None, assignment["incident_id"]):
                text = "Reassigned. " + text
            current_order[unit_id] = assignment["incident_id"]
            messages.setdefault(unit_id, []).append({"at": when, "plan_id": proposal.id, "from": f"Dispatch ({by})",
                                                     "text": text})
    units = []
    for s in world.services:
        incident = incidents.get(s.assigned_incident_id) if s.assigned_incident_id else None
        if incident is not None and incident.status == IncidentStatus.RESOLVED:
            status = "back in service"
        elif incident is not None:
            status = "assigned"
        else:
            status = "available"
        units.append({
            "id": s.id, "name": s.name, "kind": _service_kind(s.capabilities), "capabilities": sorted(s.capabilities),
            "capacity": s.capacity or None, "status": status,
            "assignment": ({"incident_id": incident.id, "kind": incident.kind, "place": place_of.get(incident.location)}
                           if incident else None),
            "location": place_of.get(s.location), "position": to_lng_lat(s.location),
            "position_source": SIMULATED_POSITION,
            "messages": messages.get(s.id, []),
        })
    broadcasts = [
        {"plan_id": plan_id, "english": alert["text"].value.get("english"), "spanish": alert["text"].value.get("spanish"),
         "source": alert["text"].label}
        for plan_id, alert in service.alerts.items()
        if alert.get("text") is not None and getattr(alert["text"], "value", None)
    ]
    return {"units": units, "broadcasts": broadcasts,
            "note": "Orders are the dispatches in approved plans. Delivery to crews (radio/CAD) is not connected; "
                    "nothing here confirms a crew received an order."}
