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
from .session import service

app = FastAPI(title="Crisis Command API")


class ReportCreate(BaseModel):
    content: str = Field(min_length=1)
    source: str = Field(min_length=1)
    occurred_at: datetime | None = None
    # Operator-entered fields; omit to request model extraction.
    fields: dict[str, Any] | None = None


class ReportClarify(BaseModel):
    fields: dict[str, Any]


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
    return serialize_state(service)


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    return _state()


@app.get("/api/events")
def get_events(limit: int = 200) -> list[dict[str, Any]]:
    events = service.world.event_log[-limit:] if limit > 0 else ()
    return [serialize_event(event) for event in reversed(events)]


@app.post("/api/reset")
def reset() -> dict[str, Any]:
    service.reset()
    return _state()


@app.post("/api/clock/advance")
def advance_clock() -> dict[str, Any]:
    service.advance_clock()
    return _state()


@app.post("/api/reports")
def submit_report(payload: ReportCreate) -> dict[str, Any]:
    service.submit_report(payload.content, payload.source, payload.occurred_at, payload.fields)
    return _state()


@app.post("/api/reports/{report_id}/clarify")
def clarify_report(report_id: str, payload: ReportClarify) -> dict[str, Any]:
    service.clarify_report(report_id, payload.fields)
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
