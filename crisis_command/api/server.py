"""FastAPI routes for the emergency operations dashboard.

Run from the repository root:
    python -m uvicorn crisis_command.api.server:app --reload --port 8000
"""

from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ..operations import CommandError, StaleApprovalError
from .serializers import serialize_event, serialize_state
from ..meta_api import ROOT, hub_from_env, setting
from .session import DEMO_ENABLED, connectors, demo, service

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
        return {**serialize_state(service), "demo": demo.status() if demo else None,
                "mode": {"demo_enabled": DEMO_ENABLED,
                         "connectors": [{"name": name, **status} for name, status in connectors.status.items()]
                                       or [{"name": c.name, "received": 0, "error": None} for c in connectors.connectors]}}


def _require_demo():
    if demo is None:
        raise HTTPException(404, "Demo is disabled (set DEMO_ENABLED=true in .env to enable it).")
    return demo


@app.on_event("startup")
def _start_connectors() -> None:
    connectors.start()


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    return _state()


@app.get("/api/events")
def get_events(limit: int = 200) -> list[dict[str, Any]]:
    events = service.world.event_log[-limit:] if limit > 0 else ()
    return [serialize_event(event) for event in reversed(events)]


class PublicReport(BaseModel):
    text: str = Field(min_length=3, max_length=2000)
    source: str = Field(default="public report page", max_length=80)


def _report_summary(report) -> dict[str, Any]:
    if report is None:
        return {"status": "not_read"}
    fields = report.fields
    return {"id": report.id, "status": report.status.value, "error": report.extraction_error,
            "missing": list(report.missing_fields), "incident_id": report.linked_incident_id,
            "road_id": report.linked_road_id,
            "read_as": {"type": fields.report_type, "kind": fields.kind, "location": fields.location,
                        "people": fields.people, "needs": list(fields.required_services or []),
                        "severity": fields.severity}}


class ExternalReport(BaseModel):
    text: str = Field(min_length=3, max_length=4000)
    source: str = Field(min_length=1, max_length=80)
    external_id: str | None = Field(default=None, max_length=120)


@app.post("/api/intake")
def intake_external(payload: ExternalReport, request: Request) -> dict[str, Any]:
    """For other systems (911/CAD, radio logs): one report per call. If INTAKE_TOKEN is set in
    .env, send it as `Authorization: Bearer <token>`."""
    token = setting("INTAKE_TOKEN")
    if token and request.headers.get("authorization", "") != f"Bearer {token}":
        raise HTTPException(401, "Missing or wrong intake token.")
    source = payload.source.strip() + (f" [{payload.external_id}]" if payload.external_id else "")
    return {"report": _report_summary(service.intake_message(payload.text.strip(), source))}


@app.post("/api/intake/text")
def intake_text(payload: PublicReport) -> dict[str, Any]:
    """The report page: a typed incident report through normal intake."""
    return {"report": _report_summary(service.intake_message(payload.text.strip(), payload.source.strip() or "public report page"))}


@app.post("/api/intake/audio")
async def intake_audio(request: Request, source: str = "public report page (voice)") -> dict[str, Any]:
    """The report page: 24 kHz 16-bit mono PCM from the browser microphone."""
    pcm = await request.body()
    if len(pcm) < 24000 or len(pcm) > 24000 * 2 * 120:
        raise HTTPException(422, "Recording must be between 0.5 s and 2 minutes.")
    from starlette.concurrency import run_in_threadpool
    text, report = await run_in_threadpool(service.intake_audio, pcm, source)
    return {"transcript": text, "report": _report_summary(report)}


@app.get("/api/connect")
def connect_info() -> dict[str, Any]:
    """This machine's network address, so a phone on the same Wi-Fi can open the report page."""
    import socket
    address = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))  # no packet is sent; this only picks the outgoing interface
        address = probe.getsockname()[0]
        probe.close()
    except OSError:
        pass
    return {"lan_ip": address}


MEDIA_DIRS = {"images", "audio"}


@app.get("/api/media/{folder}/{name}")
def media(folder: str, name: str) -> FileResponse:
    """Test media (AI-generated images, synthesized audio) for the dashboard."""
    path = (ROOT / "test_media" / folder / name).resolve()
    if folder not in MEDIA_DIRS or path.parent != (ROOT / "test_media" / folder).resolve() or not path.is_file():
        raise HTTPException(404, "Not found.")
    return FileResponse(path)


@app.post("/api/demo/start")
def start_demo(record: bool = False) -> dict[str, Any]:
    """START DEMO SCENARIO: reset to the seed and play the timeline through the pipeline,
    replaying recorded model responses (`?record=true` calls the live model and re-records)."""
    _require_demo().start(record=record)
    return _state()


@app.post("/api/demo/pause")
def pause_demo() -> dict[str, Any]:
    _require_demo().pause()
    return _state()


@app.post("/api/demo/resume")
def resume_demo() -> dict[str, Any]:
    _require_demo().resume()
    return _state()


@app.post("/api/reset")
def reset() -> dict[str, Any]:
    if demo:
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
