"""The citizen hub: a private receipt per report, its live status, and the public feed.

A receipt is a random token handed to whoever sent the report, so a phone can follow its own
reports without accounts, and nobody can read someone else's by guessing an id. The public
feed shows only what is meant for everyone: approved public alerts, closed roads and the river
gauge, never other people's reports.
"""

from secrets import token_urlsafe
from threading import Lock
from typing import Any

from ..model import IncidentStatus
from ..operations import CommandService
from ..operations.plans import PlanStatus
from ..operations.reports import ReportStatus
from .serializers import _road_evidence

STEPS = ("Received", "On the command screen", "Help dispatched", "Resolved")
# Report fields in the words a resident would use.
FIELD_WORDS = {"kind": "what is happening", "location": "where it is", "people": "how many people",
               "severity": "how serious it is", "required_services": "what help is needed",
               "road_id": "which road", "road_closed": "whether the road is closed", "report_type": "what it is about"}

_lock = Lock()
_receipts: dict[str, tuple[str, str]] = {}  # token -> (run id, report id)


def issue(service: CommandService, report_id: str) -> str:
    token = token_urlsafe(16)
    with _lock:
        _receipts[token] = (service.run_id, report_id)
    return token


def _units_on(service: CommandService, incident_id: str) -> list[str]:
    return [s.name for s in service.world.services if s.assigned_incident_id == incident_id]


def _status(service: CommandService, report) -> dict[str, Any]:
    """Where a report stands, in plain words, and how far along the steps it is."""
    if report.status == ReportStatus.REJECTED:
        return {"stage": "not_used", "step": 0, "label": f"Not used: {report.decision_reason or 'set aside by a dispatcher'}."}
    if report.status == ReportStatus.NEEDS_CLARIFICATION:
        missing = ", ".join(FIELD_WORDS.get(f, f.replace("_", " ")) for f in report.missing_fields) or "some details"
        return {"stage": "needs_detail", "step": 0,
                "label": f"Received, but we need more detail ({missing}). Please send another report with it."}
    if report.status != ReportStatus.CONFIRMED:
        return {"stage": "received", "step": 0,
                "label": "Received, but it could not be read automatically. Please send it again with the street "
                         "and what you can see."}
    if report.linked_road_id:
        road = next((r for r in service.world.city.roads if r.id == report.linked_road_id), None)
        name = service.scenario.road_names.get(report.linked_road_id, report.linked_road_id)
        state = "closed" if road is not None and road.is_closed else "open"
        return {"stage": "road", "step": 3, "label": f"Road update applied: {name} is now marked {state} on the map."}

    incident = next((i for i in service.world.incidents if i.id == report.linked_incident_id), None)
    if incident is None:
        return {"stage": "received", "step": 0, "label": "Received."}
    if incident.status == IncidentStatus.RESOLVED:
        how = next((r for r in reversed(service.resolutions) if r["incident_id"] == incident.id), None)
        detail = f" ({how['text']})" if how and how.get("text") else ""
        return {"stage": "resolved", "step": 3, "label": f"Resolved{detail}. Thank you for reporting."}
    if incident.status == IncidentStatus.ASSIGNED:
        units = _units_on(service, incident.id)
        return {"stage": "dispatched", "step": 2, "units": units,
                "label": f"Help is on the way: {', '.join(units)}."}
    plan = service.current_plan
    arranging = (plan is not None and plan.status == PlanStatus.PROPOSED
                 and any(d.incident_id == incident.id for d in plan.plan.assignments))
    if arranging:
        return {"stage": "arranging", "step": 1,
                "label": "Help is being arranged. A dispatcher is reviewing the plan now."}
    prefix = f"Incident {incident.id} left unassigned; "
    reason = next((line[len(prefix):].rstrip(".") for line in (plan.plan.rationale if plan else ())
                   if line.startswith(prefix)), None)
    return {"stage": "waiting", "step": 1,
            "label": "On the command screen and ranked by urgency, but no crew can be sent yet"
                     + (f": {reason}." if reason else ".") + " Dispatchers can see it and it stays in the queue."}


def reports(service: CommandService, tokens: list[str]) -> list[dict[str, Any]]:
    by_id = {r.id: r for r in service.reports}
    out = []
    for token in tokens[:20]:
        with _lock:
            held = _receipts.get(token)
        if held is None:
            continue
        run_id, report_id = held
        report = by_id.get(report_id) if run_id == service.run_id else None
        if report is None:
            out.append({"receipt": token, "stage": "closed", "step": 0,
                        "label": "The command board was reset, so this report is no longer on it."})
            continue
        out.append({"receipt": token, "id": report.id, "text": report.content,
                    "received_at": report.received_at.isoformat(), "place": report.fields.location,
                    "steps": list(STEPS), **_status(service, report)})
    return out


def feed(service: CommandService) -> dict[str, Any]:
    plans = service._plans
    alerts = []
    for plan_id, alert in service.alerts.items():
        text = alert["text"].value if alert.get("text") is not None else None
        if not text:
            continue
        plan = plans.get(plan_id)
        alerts.append({"plan_id": plan_id, "english": text.get("english"), "spanish": text.get("spanish"),
                       "at": (plan.decided_at or plan.created_at).isoformat() if plan else None,
                       "source": alert["text"].label})
    alerts.sort(key=lambda a: a["at"] or "", reverse=True)
    closed = [{"name": service.scenario.road_names.get(r.id, r.id), "evidence": _road_evidence(service, r)}
              for r in service.world.city.roads if r.is_closed]
    active = [i for i in service.world.incidents if i.status != IncidentStatus.RESOLVED]
    return {
        "city": service.scenario.city_short,
        "hazard": service.scenario.hazard,
        "alerts": alerts,
        "closed_roads": closed,
        "gauge": dict(service.board.gauge) or None,
        "active_incidents": len(active),
        "units_out": sum(1 for s in service.world.services if s.assigned_incident_id),
        "units_total": len(service.world.services),
    }
