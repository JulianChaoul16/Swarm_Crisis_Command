"""Incoming reports: provenance, extracted fields, validation, and lifecycle.

Reports are unverified until an operator confirms them. Only the command service turns a
confirmed report into an authoritative world change.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

SEVERITIES = ("low", "medium", "high", "critical")
REPORT_TYPES = ("incident", "road_status")
CERTAINTIES = ("confirmed", "possible")


class ReportStatus(str, Enum):
    NEEDS_EXTRACTION = "needs_extraction"
    NEEDS_CLARIFICATION = "needs_clarification"
    READY = "ready"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ReportFields:
    """Structured fields; None means explicitly unknown, never a guessed default."""

    report_type: str | None = None
    kind: str | None = None
    severity: str | None = None
    required_services: tuple[str, ...] | None = None
    location: str | None = None
    road_id: str | None = None
    road_closed: bool | None = None
    certainty: str | None = None
    people: int | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ReportFields":
        services = data.get("required_services")
        return cls(
            report_type=_clean(data.get("report_type")),
            kind=_clean(data.get("kind")),
            severity=_clean(data.get("severity")),
            required_services=(
                tuple(sorted({str(item).strip() for item in services if str(item).strip()}))
                if services is not None
                else None
            ),
            location=_clean(data.get("location")),
            road_id=_clean(data.get("road_id")),
            road_closed=data.get("road_closed") if isinstance(data.get("road_closed"), bool) else None,
            certainty=_clean(data.get("certainty")),
            people=_count(data.get("people")),
        )

    def merged(self, update: "ReportFields") -> "ReportFields":
        return replace(
            self,
            **{
                name: value
                for name, value in vars(update).items()
                if value is not None
            },
        )


def _count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class IntakeContext:
    places: frozenset[str]
    road_ids: frozenset[str]
    capabilities: frozenset[str]


@dataclass(frozen=True)
class Report:
    id: str
    content: str
    source: str
    received_at: datetime
    occurred_at: datetime | None
    fields: ReportFields = field(default_factory=ReportFields)
    extracted_by: str | None = None
    extraction_error: str | None = None
    missing_fields: tuple[str, ...] = ()
    validation_errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    status: ReportStatus = ReportStatus.NEEDS_EXTRACTION
    linked_incident_id: str | None = None
    linked_road_id: str | None = None
    decision_reason: str | None = None


def validate_fields(
    fields: ReportFields,
    context: IntakeContext,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return (missing fields, validation errors, warnings)."""
    missing: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []

    if fields.report_type is None:
        return ("report_type",), (), ()
    if fields.report_type not in REPORT_TYPES:
        return (), (f"Unknown report type {fields.report_type!r}.",), ()

    if fields.report_type == "incident":
        for name in ("kind", "severity", "location"):
            if getattr(fields, name) is None:
                missing.append(name)
        if not fields.required_services:
            missing.append("required_services")
        if "rescue" in (fields.required_services or ()) and fields.people is None:
            missing.append("people")
        if fields.people is not None and fields.people < 0:
            errors.append("People affected cannot be negative.")
        if fields.severity is not None and fields.severity not in SEVERITIES:
            errors.append(f"Severity must be one of {', '.join(SEVERITIES)}.")
        if fields.location is not None and fields.location not in context.places:
            errors.append(f"Location {fields.location!r} does not map to a known place.")
        unknown = sorted(set(fields.required_services or ()) - context.capabilities)
        if unknown:
            errors.append(f"No resource type provides: {', '.join(unknown)}.")
    else:
        if fields.road_id is None:
            missing.append("road_id")
        if fields.road_closed is None:
            missing.append("road_closed")
        if fields.road_id is not None and fields.road_id not in context.road_ids:
            errors.append(f"Road {fields.road_id!r} is not in the road network.")
        if fields.certainty is not None and fields.certainty not in CERTAINTIES:
            errors.append(f"Certainty must be one of {', '.join(CERTAINTIES)}.")
        if fields.certainty in (None, "possible"):
            warnings.append(
                "Road status is not confirmed by the source; confirming applies it as an "
                "authoritative change."
            )
    return tuple(missing), tuple(errors), tuple(warnings)


def evaluate(report: Report, context: IntakeContext) -> Report:
    """Recompute validation results and the resulting status for an open report."""
    if report.status in (ReportStatus.CONFIRMED, ReportStatus.REJECTED):
        return report
    if report.extracted_by is None:
        return replace(report, status=ReportStatus.NEEDS_EXTRACTION)
    missing, errors, warnings = validate_fields(report.fields, context)
    status = (
        ReportStatus.READY
        if not missing and not errors
        else ReportStatus.NEEDS_CLARIFICATION
    )
    return replace(
        report,
        missing_fields=missing,
        validation_errors=errors,
        warnings=warnings,
        status=status,
    )
