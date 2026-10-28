"""FastAPI routes for the emergency operations dashboard.

Run from the repository root:
    python -m uvicorn crisis_command.api.server:app --reload --port 8000
"""

from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..model import Coordinate, Incident, Severity
from .serializers import serialize_event, serialize_world
from .session import session

app = FastAPI(title="Crisis Command API")


class RoadUpdate(BaseModel):
    is_closed: bool
    reason: str = Field(min_length=1)


class IncidentCreate(BaseModel):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    x: float
    y: float
    severity: Literal["low", "medium", "high", "critical"]
    required_services: list[str] = []
    reason: str = "New incident reported by operations manager."


def _state() -> dict[str, Any]:
    return serialize_world(session.world, session.last_plan, session.notice)


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    return _state()


@app.get("/api/events")
def get_events(limit: int = 100) -> list[dict[str, Any]]:
    events = session.world.event_log[-limit:] if limit > 0 else ()
    return [serialize_event(event) for event in reversed(events)]


@app.post("/api/step")
def step() -> dict[str, Any]:
    session.step()
    return _state()


@app.post("/api/reset")
def reset() -> dict[str, Any]:
    session.reset()
    return _state()


@app.post("/api/roads/{road_id}")
def update_road(road_id: str, update: RoadUpdate) -> dict[str, Any]:
    try:
        session.set_road_closed(road_id, update.is_closed, update.reason)
    except KeyError as error:
        raise HTTPException(404, f"Unknown road {road_id!r}.") from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return _state()


@app.post("/api/incidents")
def create_incident(payload: IncidentCreate) -> dict[str, Any]:
    try:
        incident = Incident(
            id=payload.id,
            kind=payload.kind,
            location=Coordinate(payload.x, payload.y),
            severity=Severity[payload.severity.upper()],
            required_services=frozenset(payload.required_services),
        )
        session.add_incident(incident, payload.reason)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return _state()
