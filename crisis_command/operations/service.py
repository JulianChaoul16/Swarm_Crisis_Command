"""Command service: the only writer of authoritative state.

Flow (handoff §6): snapshot -> specialists in parallel -> deterministic plan -> feasibility
checks -> proposal for human review -> approval bound to plan ID and world version ->
atomic in-memory commit plus SQLite transaction. Agents and planners never write state.
"""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4

from ..agents.model_client import ModelClient, ModelUnavailableError, model_client_from_env
from ..agents.specialists import (
    DEFAULT_AGENT_TIMEOUT_SECONDS,
    AgentStatus,
    run_specialists,
)
from ..model import Incident, IncidentStatus, Severity
from ..planner import Planner
from ..simulation import Simulation, World
from .feasibility import all_passed, check_plan
from .plans import InvalidatedMission, PlanProposal, PlanStatus, diff_plans
from .reports import (
    REPORT_TYPES,
    SEVERITIES,
    IntakeContext,
    Report,
    ReportFields,
    ReportStatus,
    evaluate,
)
from .scenario import FLOOD_SCENARIO, Scenario
from .store import OperationsStore

MAX_PROPOSAL_ATTEMPTS = 3

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "report_type": {"type": ["string", "null"], "enum": [*REPORT_TYPES, None]},
        "kind": {"type": ["string", "null"]},
        "severity": {"type": ["string", "null"], "enum": [*SEVERITIES, None]},
        "required_services": {"type": ["array", "null"], "items": {"type": "string"}},
        "location": {"type": ["string", "null"]},
        "road_id": {"type": ["string", "null"]},
        "road_closed": {"type": ["boolean", "null"]},
        "certainty": {"type": ["string", "null"], "enum": ["confirmed", "possible", None]},
        "people": {"type": ["integer", "null"]},
    },
    "required": ["report_type"],
}


class CommandError(RuntimeError):
    status_code = 400


class NotFoundError(CommandError):
    status_code = 404


class ConflictError(CommandError):
    status_code = 409


class StaleApprovalError(ConflictError):
    def __init__(self, message: str, replacement_plan_id: str | None) -> None:
        super().__init__(message)
        self.replacement_plan_id = replacement_plan_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CommandService:
    def __init__(
        self,
        scenario: Scenario = FLOOD_SCENARIO,
        client: ModelClient | None = None,
        store: OperationsStore | None = None,
        agent_timeout: float = DEFAULT_AGENT_TIMEOUT_SECONDS,
    ) -> None:
        self.scenario = scenario
        self.client = client or model_client_from_env()
        self.store = store or OperationsStore()
        self.agent_timeout = agent_timeout
        self._lock = RLock()
        self.reset()

    # ----- run lifecycle -------------------------------------------------

    def reset(self) -> None:
        """Start a new run from the exact seed; late results from older runs are discarded."""
        with self._lock:
            self.run_id = f"run-{uuid4().hex[:12]}"
            self.world: World = self.scenario.build_world()
            self.planner = Planner()
            self.simulation = Simulation(self.world, self.planner)
            self._reports: dict[str, Report] = {}
            self._plans: dict[str, PlanProposal] = {}
            self.current_plan_id: str | None = None
            # Missions released since the last proposal; explained in its diff.
            self._invalidated: list[InvalidatedMission] = []
            # Road id -> report that last changed it, cited as evidence in plan diffs.
            self.road_evidence: dict[str, str] = {}
            self._persisted_events = 0
            self.store.start_run(self.run_id, self.scenario.name, _now().isoformat())
            self.world._append_event(
                "run_started",
                (self.run_id,),
                f"Scenario {self.scenario.name!r} loaded from seed.",
                actor="operator",
            )
            self._commit()
        self.propose()

    @property
    def reports(self) -> tuple[Report, ...]:
        return tuple(self._reports.values())

    @property
    def current_plan(self) -> PlanProposal | None:
        return self._plans.get(self.current_plan_id) if self.current_plan_id else None

    def intake_context(self) -> IntakeContext:
        return IntakeContext(
            places=frozenset(self.scenario.places),
            road_ids=frozenset(road.id for road in self.world.city.roads) if self.world.city else frozenset(),
            capabilities=frozenset(
                capability for service in self.world.services for capability in service.capabilities
            ),
        )

    # ----- persistence ---------------------------------------------------

    def _commit(self, *, reports=(), plans=(), approval=None) -> None:
        events = self.world.event_log[self._persisted_events:]
        self.store.persist(self.run_id, self.world, events, reports, plans, approval)
        self._persisted_events += len(events)

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        """Undo in-memory changes if anything, including the SQLite write, fails."""
        with self._lock:
            checkpoint = self.world.checkpoint()
            reports = dict(self._reports)
            plans = {plan_id: replace(plan) for plan_id, plan in self._plans.items()}
            current = self.current_plan_id
            invalidated = list(self._invalidated)
            road_evidence = dict(self.road_evidence)
            try:
                yield
            except Exception:
                self.world.rollback(checkpoint)
                self._reports, self._plans, self.current_plan_id = reports, plans, current
                self._invalidated = invalidated
                self.road_evidence = road_evidence
                raise

    # ----- intake --------------------------------------------------------

    def submit_report(
        self,
        content: str,
        source: str,
        occurred_at: datetime | None = None,
        fields: dict[str, Any] | None = None,
    ) -> Report:
        if not content.strip() or not source.strip():
            raise CommandError("Report content and source are required.")
        report = Report(
            id=f"R-{len(self._reports) + 1:03d}",
            content=content.strip(),
            source=source.strip(),
            received_at=_now(),
            occurred_at=occurred_at,
        )
        if fields is not None:
            report = replace(report, fields=ReportFields.from_mapping(fields), extracted_by="operator")
        else:
            report = self._extract(report)
        with self._atomic():
            report = evaluate(report, self.intake_context())
            self._reports[report.id] = report
            self.world._append_event(
                "report_received",
                (report.id,),
                f"Report from {report.source}: {report.status.value.replace('_', ' ')}.",
                (("extracted_by", report.extracted_by or "none"),),
                actor=report.source,
            )
            self._commit(reports=(report,))
        return report

    def _extract(self, report: Report) -> Report:
        try:
            output = self.client.complete_json(
                system=(
                    "Extract structured fields from an emergency report in a simulated exercise. "
                    "Use null for anything the report does not state. Known places: "
                    + ", ".join(sorted(self.scenario.places))
                    + ". Known roads: "
                    + ", ".join(sorted(self.intake_context().road_ids))
                    + "."
                ),
                user=report.content,
                schema=EXTRACTION_SCHEMA,
                timeout=self.agent_timeout,
            )
        except ModelUnavailableError as error:
            return replace(report, extraction_error=str(error))
        except Exception as error:  # noqa: BLE001 - surfaced to the operator, never hidden
            return replace(report, extraction_error=f"Extraction failed: {error}")
        return replace(
            report,
            fields=ReportFields.from_mapping(output),
            extracted_by=f"model:{self.client.model_id}",
        )

    def clarify_report(self, report_id: str, fields: dict[str, Any], actor: str = "operator") -> Report:
        with self._atomic():
            report = self._open_report(report_id)
            report = evaluate(
                replace(
                    report,
                    fields=report.fields.merged(ReportFields.from_mapping(fields)),
                    extracted_by=report.extracted_by or actor,
                ),
                self.intake_context(),
            )
            self._reports[report.id] = report
            self.world._append_event(
                "report_clarified",
                (report.id,),
                f"Fields updated by {actor}; now {report.status.value.replace('_', ' ')}.",
                (("fields", json.dumps(fields, default=str)),),
                actor=actor,
            )
            self._commit(reports=(report,))
        return report

    def confirm_report(self, report_id: str, actor: str = "operator") -> Report:
        with self._atomic():
            report = self._open_report(report_id)
            if report.status != ReportStatus.READY:
                raise ConflictError(
                    f"Report {report_id} is {report.status.value}; resolve missing fields first."
                )
            fields = report.fields
            reason = f"Confirmed by {actor} from report {report.id} ({report.source})."
            if fields.report_type == "incident":
                incident = Incident(
                    id=self._next_incident_id(),
                    kind=fields.kind,
                    location=self.scenario.places[fields.location],
                    severity=Severity[fields.severity.upper()],
                    required_services=frozenset(fields.required_services or ()),
                    people=fields.people or 0,
                )
                self.world.add_incident(incident, reason)
                report = replace(report, linked_incident_id=incident.id)
            else:
                self.world.city.set_road_closed(fields.road_id, fields.road_closed, reason)
                report = replace(report, linked_road_id=fields.road_id)
                self.road_evidence[fields.road_id] = report.id
                cause = (
                    f"{fields.road_id} {'closed' if fields.road_closed else 'reopened'} "
                    f"(report {report.id}, {report.source})."
                )
                for incident_id, service_ids, blocked in self.simulation.release_unreachable_missions(cause):
                    self._invalidated.append(
                        InvalidatedMission(
                            incident_id,
                            service_ids,
                            blocked,
                            f"Mission to {incident_id} has no open route after {cause}",
                        )
                    )
            report = replace(report, status=ReportStatus.CONFIRMED)
            self._reports[report.id] = report
            self.world._append_event(
                "report_confirmed",
                (report.id, report.linked_incident_id or report.linked_road_id),
                reason,
                actor=actor,
            )
            self._mark_stale_locked()
            self._commit(reports=(report,), plans=self._plans.values())
        self.propose()
        return report

    def reject_report(self, report_id: str, reason: str, actor: str = "operator") -> Report:
        with self._atomic():
            report = replace(
                self._open_report(report_id),
                status=ReportStatus.REJECTED,
                decision_reason=reason,
            )
            self._reports[report.id] = report
            self.world._append_event("report_rejected", (report.id,), reason, actor=actor)
            self._commit(reports=(report,))
        return report

    def inject_disruption(self, key: str, actor: str = "operator") -> Report:
        """Scenario control: file the scripted report and confirm it through normal intake."""
        disruption = self.scenario.disruptions.get(key)
        if disruption is None:
            raise NotFoundError(f"Scenario {self.scenario.name!r} has no disruption {key!r}.")
        report = self.submit_report(disruption.content, disruption.source, fields=disruption.fields)
        return self.confirm_report(report.id, actor=actor)

    def _open_report(self, report_id: str) -> Report:
        report = self._reports.get(report_id)
        if report is None:
            raise NotFoundError(f"Unknown report {report_id}.")
        if report.status in (ReportStatus.CONFIRMED, ReportStatus.REJECTED):
            raise ConflictError(f"Report {report_id} is already {report.status.value}.")
        return report

    def _next_incident_id(self) -> str:
        existing = {incident.id for incident in self.world.incidents}
        number = len(existing) + 1
        while f"incident-{number}" in existing:
            number += 1
        return f"incident-{number}"

    # ----- planning and approval ----------------------------------------

    def propose(self) -> PlanProposal | None:
        """Plan against an immutable snapshot; discard and retry if the world moved meanwhile."""
        for _ in range(MAX_PROPOSAL_ATTEMPTS):
            with self._lock:
                run_id = self.run_id
                snapshot = self.world.snapshot()
            findings = run_specialists(snapshot, self.client, self.agent_timeout)
            plan = self.planner.plan(
                list(snapshot.incidents),
                list(snapshot.services),
                snapshot.city,
                world_version=snapshot.version,
            )
            with self._atomic():
                if run_id != self.run_id:
                    return None  # a reset happened; this run's results must not touch the new world
                if (
                    self.world.version != snapshot.version
                    or self.world._state_signature() != snapshot.state_signature
                ):
                    self.world._append_event(
                        "proposal_discarded",
                        (),
                        f"World changed from v{snapshot.version} to v{self.world.version} "
                        "while planning; recomputing.",
                        actor="coordinator",
                    )
                    self._commit()
                    continue
                return self._store_proposal(plan, snapshot, findings)

        with self._lock:
            self.world._append_event(
                "proposal_pending",
                (),
                f"World kept changing; no proposal after {MAX_PROPOSAL_ATTEMPTS} attempts.",
                actor="coordinator",
            )
            self._commit()
        return None

    def _store_proposal(self, plan, snapshot, findings) -> PlanProposal:
        previous = self.current_plan
        # Approved dispatches are now committed missions, not alternatives to compare against.
        comparable = previous if previous is not None and previous.status != PlanStatus.APPROVED else None
        checks = check_plan(plan, self.world.incidents, self.world.services, self.world.city)
        proposal = PlanProposal(
            id=f"P-{len(self._plans) + 1:03d}",
            run_id=self.run_id,
            world_version=snapshot.version,
            state_signature=snapshot.state_signature,
            plan=plan,
            checks=checks,
            changes=diff_plans(
                plan,
                comparable.plan if comparable else None,
                self.world.city,
                tuple(self._invalidated),
                self.road_evidence,
            ),
            invalidated=tuple(self._invalidated),
            agent_findings=findings,
            created_at=_now(),
            previous_plan_id=previous.id if previous else None,
            status=PlanStatus.PROPOSED if all_passed(checks) else PlanStatus.INVALID,
        )
        self._invalidated = []
        if previous is not None and previous.status == PlanStatus.PROPOSED:
            previous.status = PlanStatus.SUPERSEDED
        self._plans[proposal.id] = proposal
        self.current_plan_id = proposal.id
        agent_states = ", ".join(f"{f.agent}: {f.status.value}" for f in findings)
        self.world._append_event(
            "agents_reported",
            tuple(f.agent for f in findings),
            f"Specialists finished ({agent_states}).",
            tuple((f.agent, f.error or f.summary or f.status.value) for f in findings),
            actor="coordinator",
        )
        self.world._append_event(
            "plan_proposed" if proposal.valid else "plan_invalid",
            (proposal.id,),
            f"{len(plan.assignments)} dispatches, {len(plan.unassigned_incident_ids)} unmet "
            f"against v{proposal.world_version}.",
            tuple((check.rule, check.detail) for check in checks if not check.passed),
            actor="coordinator",
        )
        self._commit(plans=[p for p in (previous, proposal) if p is not None])
        return proposal

    def _mark_stale_locked(self) -> None:
        plan = self.current_plan
        if plan is not None and plan.status == PlanStatus.PROPOSED and plan.world_version != self.world.version:
            plan.status = PlanStatus.STALE
            self.world._append_event(
                "plan_stale",
                (plan.id,),
                f"World moved from v{plan.world_version} to v{self.world.version}; approval blocked.",
                actor="coordinator",
            )

    def approve(self, plan_id: str, world_version: int, actor: str = "operator") -> PlanProposal:
        stale = False
        with self._atomic():
            proposal = self._plans.get(plan_id)
            if proposal is None:
                raise NotFoundError(f"Unknown plan {plan_id}.")
            if plan_id != self.current_plan_id:
                if proposal.status in (PlanStatus.STALE, PlanStatus.SUPERSEDED):
                    raise StaleApprovalError(
                        f"{plan_id} is {proposal.status.value} (planned against "
                        f"v{proposal.world_version}; world is v{self.world.version}). "
                        f"Review {self.current_plan_id} instead.",
                        self.current_plan_id,
                    )
                raise ConflictError(f"{plan_id} is not the current proposal ({self.current_plan_id}).")
            if world_version != proposal.world_version:
                raise ConflictError(
                    f"Approval names v{world_version}, but {plan_id} was planned against "
                    f"v{proposal.world_version}."
                )
            if (
                self.world.version != proposal.world_version
                or self.world._state_signature() != proposal.state_signature
            ):
                proposal.status = PlanStatus.STALE
                self.world._append_event(
                    "approval_refused",
                    (plan_id,),
                    f"{plan_id} was planned against v{proposal.world_version}; world is now "
                    f"v{self.world.version}.",
                    actor=actor,
                )
                self._commit(plans=(proposal,))
                stale = True
            else:
                if proposal.status != PlanStatus.PROPOSED:
                    raise ConflictError(f"{plan_id} is {proposal.status.value} and cannot be approved.")
                if not proposal.plan.assignments:
                    raise ConflictError(f"{plan_id} contains no dispatches to approve.")
                checks = check_plan(
                    proposal.plan, self.world.incidents, self.world.services, self.world.city
                )
                if not all_passed(checks):
                    raise ConflictError("Plan failed feasibility checks at commit time.")
                decided_at = _now()
                self.simulation.apply_dispatches(
                    proposal.plan,
                    (("plan_id", plan_id), ("approved_by", actor)),
                )
                proposal.status = PlanStatus.APPROVED
                proposal.decided_at = decided_at
                proposal.decided_by = actor
                self.world._append_event(
                    "plan_approved",
                    (plan_id,),
                    f"{actor} approved {plan_id} ({len(proposal.plan.assignments)} dispatches).",
                    actor=actor,
                )
                self._commit(
                    plans=(proposal,),
                    approval={
                        "plan_id": plan_id,
                        "world_version": proposal.world_version,
                        "decision": "approved",
                        "actor": actor,
                        "decided_at": decided_at.isoformat(),
                    },
                )

        replacement = self.propose()
        if stale:
            raise StaleApprovalError(
                f"{plan_id} is stale; a new proposal was computed from the latest snapshot.",
                replacement.id if replacement else None,
            )
        return proposal

    def reject_plan(self, plan_id: str, reason: str, actor: str = "operator") -> PlanProposal:
        with self._atomic():
            proposal = self._plans.get(plan_id)
            if proposal is None:
                raise NotFoundError(f"Unknown plan {plan_id}.")
            if proposal.status not in (PlanStatus.PROPOSED, PlanStatus.INVALID):
                raise ConflictError(f"{plan_id} is {proposal.status.value}.")
            decided_at = _now()
            proposal.status = PlanStatus.REJECTED
            proposal.decided_at = decided_at
            proposal.decided_by = actor
            proposal.decision_reason = reason
            self.world._append_event("plan_rejected", (plan_id,), reason, actor=actor)
            self._commit(
                plans=(proposal,),
                approval={
                    "plan_id": plan_id,
                    "world_version": proposal.world_version,
                    "decision": "rejected",
                    "actor": actor,
                    "decided_at": decided_at.isoformat(),
                    "reason": reason,
                },
            )
        return proposal

    # ----- simulated time ------------------------------------------------

    def advance_clock(self) -> None:
        with self._atomic():
            self.simulation.advance_clock()
            self._mark_stale_locked()
            self._commit(plans=self._plans.values())
        if any(incident.status == IncidentStatus.OPEN for incident in self.world.incidents):
            self.propose()

    def agent_statuses(self) -> dict[str, str]:
        plan = self.current_plan
        if plan is None:
            return {}
        return {finding.agent: finding.status.value for finding in plan.agent_findings}


__all__ = [
    "AgentStatus",
    "CommandError",
    "CommandService",
    "ConflictError",
    "NotFoundError",
    "StaleApprovalError",
]
