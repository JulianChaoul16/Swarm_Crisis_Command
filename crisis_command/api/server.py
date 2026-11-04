"""FastAPI routes for the emergency operations dashboard.

Run from the repository root:
    python -m uvicorn crisis_command.api.server:app --reload --port 8000
"""

from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..operations import CommandError, StaleApprovalError
from .serializers import serialize_event, serialize_state
from ..meta_api import hub_from_env
from .session import demo, service

app = FastAPI(title="Crisis Command API")


class ReportCreate(BaseModel):
    content: str = Field(min_length=1)
    source: str = Field(min_length=1)
    occurred_at: datetime | None = None
    # Operator-entered fields; omit to request model extraction.
    fields: dict[str, Any] | None = None



class Decision(BaseModel):
    reason: str = Field(min_length=1)


class Approval(BaseModel):
    world_version: int


@app.exception_handler(StaleApprovalError)
def stale_approval(_: Request, error: StaleApprovalError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": str(error), "replacement_plan_id": error.replacement_plan_id},
    )


@app.exception_handler(CommandError)
def command_error(_: Request, error: CommandError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content={"detail": str(error)})


@app.exception_handler(ValueError)
def value_error(_: Request, error: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(error)})


def _state() -> dict[str, Any]:
    with service._lock:
        return {**serialize_state(service), "demo": demo.status()}


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    return _state()


@app.get("/api/events")
def get_events(limit: int = 200) -> list[dict[str, Any]]:
    events = service.world.event_log[-limit:] if limit > 0 else ()
    return [serialize_event(event) for event in reversed(events)]


@app.post("/api/demo/start")
def start_demo(record: bool = False) -> dict[str, Any]:
    """START DEMO SCENARIO: reset to the seed and play the timeline through the pipeline,
    replaying recorded model responses (`?record=true` calls the live model and re-records)."""
    demo.start(record=record)
    return _state()


@app.post("/api/demo/pause")
def pause_demo() -> dict[str, Any]:
    demo.pause()
    return _state()


@app.post("/api/demo/resume")
def resume_demo() -> dict[str, Any]:
    demo.resume()
    return _state()


@app.post("/api/reset")
def reset() -> dict[str, Any]:
    demo.stop()
    service.hub.mode = hub_from_env(service.client).mode
    service.reset()
    return _state()


@app.post("/api/reports")
def submit_report(payload: ReportCreate) -> dict[str, Any]:
    service.submit_report(payload.content, payload.source, payload.occurred_at, payload.fields)
    return _state()


@app.post("/api/reports/{report_id}/confirm")
def confirm_report(report_id: str) -> dict[str, Any]:
    service.confirm_report(report_id)
    return _state()


@app.post("/api/reports/{report_id}/reject")
def reject_report(report_id: str, payload: Decision) -> dict[str, Any]:
    service.reject_report(report_id, payload.reason)
    return _state()


@app.post("/api/plans/propose")
def propose() -> dict[str, Any]:
    if service.propose() is None:
        raise HTTPException(409, "No proposal: the world kept changing or the run was reset.")
    return _state()


@app.post("/api/plans/{plan_id}/approve")
def approve(plan_id: str, payload: Approval) -> dict[str, Any]:
    service.approve(plan_id, payload.world_version)
    return _state()


@app.post("/api/plans/{plan_id}/reject")
def reject_plan(plan_id: str, payload: Decision) -> dict[str, Any]:
    service.reject_plan(plan_id, payload.reason)
    return _state()
