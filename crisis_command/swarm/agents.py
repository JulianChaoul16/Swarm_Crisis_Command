"""The incident swarm: Triage, Vision and Logistics agents voting on one shared board.

`spawn_incident_swarm(incident_id)` runs the three agents in parallel for one incident. Each
reads a frozen world snapshot and writes its vote to the board. Agents also react to each
other through the bus: a flood-cover change makes Vision and Logistics re-vote, and a road
change makes Logistics re-vote. The coordinator re-plans when the consensus moves.

Phase 2 scoring is rule-based and documented below; each vote records its source. Model
calls (Muse Spark, SAM 3.1, Muse Wonder, Muse Glimmer) replace these rules in Phase 3.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import networkx as nx

from ..model import Incident, IncidentStatus, Severity
from ..simulation import WorldSnapshot
from .board import FLOOD_CHANGED, LOGISTICS, ROAD_CHANGED, TRIAGE, VISION, IncidentBoard

SEVERITY_BASE = {Severity.CRITICAL: 80, Severity.HIGH: 62, Severity.MEDIUM: 42, Severity.LOW: 22}
RULES = "rules"


def triage_vote(incident: Incident) -> tuple[int, tuple[str, ...]]:
    """Severity class, plus people at risk, plus a vulnerability proxy (medical need)."""
    score = SEVERITY_BASE[incident.severity] + min(incident.people, 10) * 1.5
    reasons = [f"{incident.severity.name.lower()} report, {incident.people} people at risk"]
    if "medical" in incident.required_services:
        score += 6
        reasons.append("medical need (vulnerable person)")
    return round(score), tuple(reasons)


def vision_vote(incident: Incident, cover: dict[str, Any] | None) -> tuple[int, tuple[str, ...]]:
    """Flood cover around the incident: more water, more urgent."""
    if cover is None:
        return SEVERITY_BASE[incident.severity], ("no imagery for this location; vote = severity class",)
    score = 30 + 0.7 * cover["percent"]
    return round(score), (f"{cover['percent']}% of the area around it is flooded",)


def logistics_vote(incident: Incident, snapshot: WorldSnapshot, cover: dict[str, Any] | None) -> tuple[int, tuple[str, ...]]:
    """How hard it is to get help there: slow or impossible access raises urgency."""
    score = SEVERITY_BASE[incident.severity]
    reasons = []
    city = snapshot.city
    for capability in sorted(incident.required_services):
        etas = []
        for unit in snapshot.services:
            if capability not in unit.capabilities or unit.assigned_incident_id not in (None, incident.id):
                continue
            try:
                etas.append(city.route_distance(unit.location, incident.location) / unit.speed)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
        if not etas:
            score += 15
            reasons.append(f"no free {capability} unit can reach it over open roads")
        else:
            score += min(min(etas) * 4, 12)
            reasons.append(f"nearest {capability} unit {min(etas):.1f} min away")
    seats = sum(u.capacity for u in snapshot.services if u.available)
    if incident.people > seats:
        score += 10
        reasons.append(f"only {seats} free seats for {incident.people} people")
    if cover is not None and cover["percent"] >= 70:
        score += 5
        reasons.append("water still rising around access routes")
    return round(score), tuple(reasons)


class Swarm:
    """Binds the agents to a board and a source of world snapshots."""

    def __init__(self, board: IncidentBoard, snapshot, place_of, flood_sample: dict[str, dict[str, Any]]):
        self.board = board
        self.snapshot = snapshot  # () -> WorldSnapshot
        self.place_of = place_of  # Coordinate -> place name
        self.flood_sample = flood_sample
        board.bus.subscribe(FLOOD_CHANGED, self._on_flood)
        board.bus.subscribe(ROAD_CHANGED, self._on_road)

    def _open(self, snapshot: WorldSnapshot) -> list[Incident]:
        return [i for i in snapshot.incidents if i.status != IncidentStatus.RESOLVED]

    def _incident(self, snapshot: WorldSnapshot, incident_id: str) -> Incident | None:
        return next((i for i in snapshot.incidents if i.id == incident_id), None)

    # ----- agents ---------------------------------------------------------

    def triage(self, incident_id: str) -> None:
        incident = self._incident(self.snapshot(), incident_id)
        if incident is not None:
            self.board.cast(incident_id, TRIAGE, *triage_vote(incident), source=RULES)

    def vision(self, incident_id: str) -> None:
        incident = self._incident(self.snapshot(), incident_id)
        if incident is None:
            return
        place = self.place_of(incident.location)
        cover = self.board.flood_cover.get(place)
        if cover is None and place in self.flood_sample:
            # Phase 2: flood cover comes from the scenario's sample imagery table.
            self.board.set_flood_cover(place, {**self.flood_sample[place], "place": place}, VISION, "sample data")
            cover = self.board.flood_cover.get(place)
        self.board.cast(incident_id, VISION, *vision_vote(incident, cover),
                        source="sample data" if cover else RULES)

    def logistics(self, incident_id: str) -> None:
        snapshot = self.snapshot()
        incident = self._incident(snapshot, incident_id)
        if incident is not None:
            cover = self.board.flood_cover.get(self.place_of(incident.location))
            self.board.cast(incident_id, LOGISTICS, *logistics_vote(incident, snapshot, cover), source=RULES)

    # ----- spawning and reactions -----------------------------------------

    def spawn_incident_swarm(self, incident_id: str) -> None:
        """Run all three agents for one incident in parallel; returns when all have voted."""
        self.board.log("Coordinator", "swarm", f"Spawned swarm for {incident_id}: Triage, Vision, Logistics", incident_id)
        with ThreadPoolExecutor(max_workers=3) as pool:
            for future in [pool.submit(agent, incident_id) for agent in (self.triage, self.vision, self.logistics)]:
                future.result()

    def _on_flood(self, payload: dict[str, Any]) -> None:
        """Vision updated flood cover: re-vote incidents there; Logistics re-checks access."""
        snapshot = self.snapshot()
        for incident in self._open(snapshot):
            # Only incidents whose first round of votes is complete; a spawn in progress
            # will read the new cover anyway.
            if self.place_of(incident.location) == payload["place"] and len(self.board.votes.get(incident.id, {})) == 3:
                self.board.log(LOGISTICS, RULES, f"Reacting to new flood cover at {payload['place']}", incident.id)
                self.vision(incident.id)
                self.logistics(incident.id)

    def _on_road(self, payload: dict[str, Any]) -> None:
        """A road opened or closed: Logistics re-votes every open incident."""
        self.board.log(LOGISTICS, RULES, f"Reacting to {payload['road_id']} {'closed' if payload['closed'] else 'reopened'}")
        for incident in self._open(self.snapshot()):
            if incident.id in self.board.votes:
                self.logistics(incident.id)
