"""The shared blackboard: one `IncidentBoard` all agents read and write, and an `EventBus`.

There is no fixed pipeline. Each agent writes its vote or finding to the board; the board
publishes what changed on the bus; any agent (or the coordinator) subscribed to that topic
reacts, for example by re-voting. Final severity is a consensus of the agents' votes, never
one agent's output. Every agent action is written to the activity log shown in the UI.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median
from threading import RLock
from typing import Any, Callable

TRIAGE = "Intake Agent"
VISION = "Vision Agent"
# Not an agent: deterministic code that checks how fast units can reach an incident.
LOGISTICS = "Access check"
VOTERS = (TRIAGE, VISION, LOGISTICS)

# Topics published on the bus.
INCIDENT_NEW = "incident.new"
VOTE_CAST = "vote.cast"
CONSENSUS_CHANGED = "consensus.changed"
ROAD_CHANGED = "road.changed"
FLOOD_CHANGED = "flood.changed"
GAUGE_CHANGED = "gauge.changed"
UNITS_CHANGED = "units.changed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Vote:
    agent: str
    score: int
    reasons: tuple[str, ...]
    # Where the vote came from, e.g. "rules", "Muse Spark", "sample data". Shown in the UI.
    source: str
    cast_at: datetime


@dataclass(frozen=True)
class Activity:
    at: datetime
    agent: str
    source: str
    text: str
    incident_id: str | None = None


def consensus(votes: dict[str, Vote]) -> int | None:
    """Final severity: the median vote, pulled halfway toward the highest one.

    One alarmed agent raises the priority but cannot set it alone; e.g. 82, 96, 91 -> 94.
    None until every voter has voted.
    """
    if any(agent not in votes for agent in VOTERS):
        return None
    scores = [votes[agent].score for agent in VOTERS]
    middle = median(scores)
    return round(middle + (max(scores) - middle) / 2)


def agreement(votes: dict[str, Vote]) -> int | None:
    """How closely the votes agree, 0-100 (100 = identical). Not a calibrated probability."""
    if any(agent not in votes for agent in VOTERS):
        return None
    scores = [votes[agent].score for agent in VOTERS]
    return max(0, 100 - (max(scores) - min(scores)))


class EventBus:
    """Synchronous publish/subscribe. Handlers run in the publisher's thread, in order."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Callable[[dict[str, Any]], None]) -> None:
        self._handlers[topic].append(handler)

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        for handler in list(self._handlers[topic]):
            handler(payload)


@dataclass
class IncidentBoard:
    run_id: str
    bus: EventBus = field(default_factory=EventBus)
    votes: dict[str, dict[str, Vote]] = field(default_factory=dict)
    finals: dict[str, int] = field(default_factory=dict)
    # Shared observations agents publish for each other, e.g. flood cover per place.
    flood_cover: dict[str, dict[str, Any]] = field(default_factory=dict)
    # River gauge reading (scenario value) and imagery results (masks, estimates) for the map.
    gauge: dict[str, Any] = field(default_factory=dict)
    imagery: list[dict[str, Any]] = field(default_factory=list)
    transcripts: list[dict[str, Any]] = field(default_factory=list)
    activity: list[Activity] = field(default_factory=list)
    _lock: RLock = field(default_factory=RLock)

    def log(self, agent: str, source: str, text: str, incident_id: str | None = None) -> None:
        with self._lock:
            self.activity.append(Activity(_now(), agent, source, text, incident_id))

    def cast(self, incident_id: str, agent: str, score: int, reasons: tuple[str, ...], source: str) -> None:
        """Record one agent's vote; publishes the vote and, if it moved, the new consensus."""
        score = max(0, min(100, int(score)))
        with self._lock:
            previous = self.votes.get(incident_id, {}).get(agent)
            self.votes.setdefault(incident_id, {})[agent] = Vote(agent, score, reasons, source, _now())
            before = self.finals.get(incident_id)
            final = consensus(self.votes[incident_id])
            if final is not None:
                self.finals[incident_id] = final
        change = f"{previous.score} -> {score}" if previous and previous.score != score else f"{score}"
        if previous is None or previous.score != score:
            self.log(agent, source, f"{incident_id} severity vote {change}: {reasons[0] if reasons else ''}", incident_id)
        self.bus.publish(VOTE_CAST, {"incident_id": incident_id, "agent": agent, "score": score})
        if final is not None and final != before:
            self.log("Swarm consensus", "rule: median pulled halfway to max",
                     f"{incident_id} final severity {before if before is not None else '-'} -> {final}", incident_id)
            self.bus.publish(CONSENSUS_CHANGED, {"incident_id": incident_id, "final": final, "previous": before})

    def set_flood_cover(self, place: str, cover: dict[str, Any], agent: str, source: str) -> None:
        with self._lock:
            before = self.flood_cover.get(place, {}).get("percent")
            self.flood_cover[place] = cover
        if before != cover.get("percent"):
            self.log(agent, source, f"{place} now {cover['percent']}% flooded")
            self.bus.publish(FLOOD_CHANGED, {"place": place, "cover": cover})

    def set_gauge(self, reading: dict[str, Any], source: str) -> None:
        with self._lock:
            before = self.gauge.get("value")
            self.gauge = reading
        if before != reading.get("value"):
            self.log("River gauge", source, f"James River at Westham {reading['value']} ft, {reading.get('trend', '')}")
            self.bus.publish(GAUGE_CHANGED, {"gauge": reading})

    def final(self, incident_id: str) -> int | None:
        with self._lock:
            return self.finals.get(incident_id)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "votes": {iid: dict(v) for iid, v in self.votes.items()},
                "finals": dict(self.finals),
                "flood_cover": dict(self.flood_cover),
                "gauge": dict(self.gauge),
                "imagery": list(self.imagery),
                "transcripts": list(self.transcripts),
                "activity": list(self.activity),
            }
