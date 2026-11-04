"""Command service: the only writer of authoritative state.

Flow: a confirmed change lands -> the incident swarm (Intake, Vision, Access check) votes on the
shared IncidentBoard, reacting to each other through the event bus -> the exact solver plans
with the swarm's consensus severity as priority -> feasibility checks -> proposal for human
review -> approval bound to plan ID and world version -> atomic in-memory commit plus SQLite
transaction. Agents write only to the board; they never write world state or dispatch.
"""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
from concurrent.futures import ThreadPoolExecutor
from threading import RLock, Thread
from typing import Any, Iterator
from uuid import uuid4

from ..agents.model_client import ModelClient, ModelUnavailableError, model_client_from_env
from ..meta_api import MetaHub, hub_from_env, unsupported_numbers
from ..model import Incident, IncidentStatus, Severity
from ..swarm.agents import SEVERITY_BASE, Swarm
from ..swarm.board import CONSENSUS_CHANGED, ROAD_CHANGED, UNITS_CHANGED, IncidentBoard
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
DEFAULT_AGENT_TIMEOUT_SECONDS = 20.0
SWARM_PRIORITY_RULE = "swarm consensus severity (highest first), then people affected, then incident id"

def _nullable_enum(values) -> dict[str, Any]:
    return {"type": ["string", "null"], "enum": [*sorted(values), None]}


def extraction_schema(context: IntakeContext) -> dict[str, Any]:
    """Strict schema whose enums come from the live scenario, so the model can only name
    places, roads and capabilities that exist. Validation still runs on every result."""
    properties = {
        "report_type": _nullable_enum(REPORT_TYPES),
        "kind": {"type": ["string", "null"]},
        "severity": _nullable_enum(SEVERITIES),
        "required_services": {"type": ["array", "null"],
                              "items": {"type": "string", "enum": sorted(context.capabilities)}},
        "location": _nullable_enum(context.places),
        "road_id": _nullable_enum(context.road_ids),
        "road_closed": {"type": ["boolean", "null"]},
        "certainty": _nullable_enum(("confirmed", "possible")),
        "people": {"type": ["integer", "null"]},
    }
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


CAPABILITY_GUIDE = {
    "rescue": "people trapped by or in water, or stranded",
    "medical": "an injury or medical condition, including vulnerable people at risk",
    "fire": "fire or smoke",
    "utility": "power lines, gas, transformers or other infrastructure hazards",
    "police": "traffic control, security or road hazards needing a cordon",
}

EXTRACTION_RULES = """\
Extract structured fields from one emergency report in a simulated flood exercise. Reports
may be informal, misspelled, in any language, or contain irrelevant numbers.

- report_type: "incident" if people or property need a response; "road_status" if the report
  only says whether a road is open or closed.
- location: the known place the report is about, matching nicknames and misspellings; null if
  no known place matches. Never pick a place the report does not refer to.
- kind: a short lowercase description, e.g. "residents trapped by rising water".
- severity: critical = someone trapped, in water, or in immediate danger to life; high =
  injury, fire, a vulnerable person whose condition is unknown, or water about to reach
  people; medium = displaced or inconvenienced, no immediate danger; low = observations or
  property only.
- required_services: every capability the response needs (guide below).
- people: how many people need rescue, treatment or moving. Add counts ("me and my 2 kids" =
  3); one described person = 1; null if no count is possible ("a family"). House numbers,
  years, distances and times are not people.
- road_id and road_closed: for road_status reports. Closed, blocked, out or under water =
  true; open, passable, reopened or clear = false.
- certainty: "confirmed" if the source states it as fact, "possible" if hedged ("I think",
  "maybe", a question).

Use null for anything the report does not state. Do not guess. Never invent a location, a head
count, a capability, a road status or a confirmation that the report does not give.
"""


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


def _same_decisions(a, b) -> bool:
    """Two plans make the same decisions for the same reasons, priority order included."""
    return (a.assignments == b.assignments and set(a.kept) == set(b.kept)
            and a.unassigned_incident_ids == b.unassigned_incident_ids and a.rationale == b.rationale)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CommandService:
    def __init__(
        self,
        scenario: Scenario = FLOOD_SCENARIO,
        client: ModelClient | None = None,
        store: OperationsStore | None = None,
        agent_timeout: float = DEFAULT_AGENT_TIMEOUT_SECONDS,
        background_models: bool = False,
    ) -> None:
        self.scenario = scenario
        self.client = client or model_client_from_env()
        self.hub: MetaHub = hub_from_env(self.client)
        self.hub.timeout = agent_timeout
        self.hub.city, self.hub.city_short, self.hub.hazard = scenario.city, scenario.city_short, scenario.hazard
        # When on, the recommendation text and public alert are written in a background
        # thread and attach when ready; the plan itself never waits for them.
        self.background_models = background_models
        # Called with the approved proposal (the demo uses it to continue the timeline).
        self.approval_listeners: list = []
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
            # The shared blackboard for this run; a reset replaces it, so late writes from
            # the old run's agents land on a board nobody reads.
            self.board = IncidentBoard(self.run_id)
            self.board.bus.subscribe(CONSENSUS_CHANGED, self._on_consensus)
            place_names = {point: name for name, point in self.scenario.places.items()}
            self.hub.on_fallback = lambda model, text: self.board.log(model, "fallback", text)
            self.swarm = Swarm(self.board, self._snapshot, lambda point: place_names.get(point, "unknown"),
                               self.scenario.flood_sample, self.hub, self._report_text)
            # Plan id -> model-written explanation, and approved plan id -> public alert.
            self.explanations: dict[str, Any] = {}
            # Plan id -> numbers in the model's explanation that are not in its facts.
            self.unverified: dict[str, list[str]] = {}
            self.alerts: dict[str, Any] = {}
            # Resolved incidents, oldest first: who closed them and which units they freed.
            self.resolutions: list[dict[str, Any]] = []
            self.planner = Planner(priority_key=self._swarm_priority, priority_rule=SWARM_PRIORITY_RULE,
                                   reroute=self.scenario.reroute)
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
            seeds = [i.id for i in self.world.incidents if i.status != IncidentStatus.RESOLVED]
        with ThreadPoolExecutor(max_workers=max(1, len(seeds))) as pool:
            list(pool.map(self.swarm.spawn_incident_swarm, seeds))
        self.propose()

    def _report_text(self, incident_id: str) -> str:
        with self._lock:
            return " / ".join(r.content for r in self._reports.values() if r.linked_incident_id == incident_id)

    def _snapshot(self):
        with self._lock:
            return self.world.snapshot()

    def _swarm_priority(self, incident: Incident) -> tuple[object, ...]:
        """Plan in the swarm's consensus order; before the swarm has voted, by severity class."""
        final = self.board.final(incident.id)
        return (-(final if final is not None else SEVERITY_BASE[incident.severity]), -incident.people, incident.id)

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
        entered_by: str = "operator",
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
            report = replace(report, fields=ReportFields.from_mapping(fields), extracted_by=entered_by)
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
        """Muse Spark reads the report (through the hub: redacted, recorded, replayable)."""
        context = self.intake_context()
        guide = "\n".join(
            f"- {capability}: {CAPABILITY_GUIDE.get(capability, 'as named')}"
            for capability in sorted(context.capabilities)
        )
        notes = self.scenario.place_notes
        places = ", ".join(f"{name} ({notes[name]})" if name in notes else name for name in sorted(context.places))
        # Name each road as people say it (e.g. upriver-bridge = Lee Bridge), plus any note.
        names = self.scenario.road_names
        roads = ", ".join(
            f"{road} ({'; '.join(p for p in (names.get(road), notes.get(road)) if p)})" if road in names or road in notes else road
            for road in sorted(context.road_ids)
        )
        result = self.hub.extract(
            report.source,
            report.content,
            f"{EXTRACTION_RULES}\nCapabilities:\n{guide}\n\nKnown places: {places}.\nKnown roads: {roads}.",
            extraction_schema(context),
        )
        if result.value is None:
            return replace(report, extraction_error=f"No reading: {result.note}")
        return replace(
            report,
            fields=ReportFields.from_mapping(result.value),
            extracted_by=f"model:{result.model} ({result.source})",
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
        if report.linked_incident_id:
            self.swarm.spawn_incident_swarm(report.linked_incident_id)
        elif report.linked_road_id:
            self.board.bus.publish(ROAD_CHANGED, {"road_id": fields.road_id, "closed": fields.road_closed})
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
        """One more than the highest number in use (seeded incidents may not start at 1)."""
        numbers = [int(i.id.split("-")[-1]) for i in self.world.incidents if i.id.split("-")[-1].isdigit()]
        return f"incident-{max(numbers, default=0) + 1}"

    # ----- planning and approval ----------------------------------------

    def propose(self) -> PlanProposal | None:
        """Plan against an immutable snapshot; discard and retry if the world moved meanwhile."""
        for _ in range(MAX_PROPOSAL_ATTEMPTS):
            with self._lock:
                run_id = self.run_id
                snapshot = self.world.snapshot()
            findings = ()
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
                current = self.current_plan
                if (current is not None and current.status == PlanStatus.PROPOSED
                        and current.state_signature == snapshot.state_signature
                        and _same_decisions(current.plan, plan)):
                    return current  # nothing would change; keep the plan (and its explanation)
                proposal = self._store_proposal(plan, snapshot, findings)
            self._explain(proposal)
            return proposal

    def _on_consensus(self, payload: dict[str, Any]) -> None:
        """An agent re-voted and the consensus moved: priorities changed, so re-plan. A first
        consensus (a new incident) is planned by whoever filed it."""
        if payload.get("previous") is not None and self.current_plan_id is not None:
            self.propose()

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
        self.board.log("Coordinator", "exact solver",
                       f"Plan {proposal.id}: {len(plan.assignments)} dispatches, "
                       f"{len(plan.unassigned_incident_ids)} unmet, ranked by swarm consensus")
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

        if not stale:
            self.board.bus.publish(UNITS_CHANGED, {"why": f"units dispatched by {plan_id}"})
        replacement = self.propose()
        if not stale:
            self._announce(proposal)
            for listener in list(self.approval_listeners):
                listener(proposal)
        if stale:
            raise StaleApprovalError(
                f"{plan_id} is stale; a new proposal was computed from the latest snapshot.",
                replacement.id if replacement else None,
            )
        return proposal

    def intake_message(self, text: str, source: str) -> Report:
        """A report from outside (the report page): read by the Intake Agent, applied if it is
        complete, then the swarm votes and the solver re-plans. Dispatch still needs approval."""
        report = self.submit_report(text, source)
        if report.extraction_error:
            self.board.log("Intake Agent", "intake", f"{report.id} could not be read: {report.extraction_error}")
            return report
        self.board.log("Intake Agent", report.extracted_by or "intake",
                       f"{report.id} from {source} read as {report.fields.kind} at {report.fields.location}, "
                       f"{report.fields.people} people ({report.status.value})")
        if report.status == ReportStatus.READY:
            report = self.confirm_report(report.id, actor="auto-apply (complete report)")
        return report

    def intake_audio(self, pcm: bytes, source: str) -> tuple[str | None, Report | None]:
        """A spoken report: the Listener Agent transcribes it, then it goes through intake."""
        self.board.log("Listener Agent", "Muse Voice Transcribe", f"Listening to a report from {source}")
        result = self.hub.transcribe(pcm)
        if result.value is None:
            self.board.log("Listener Agent", result.label, f"No transcript: {result.note}")
            return None, None
        text = result.value["text"]
        self.board.transcripts.append({"audio_id": f"report-{len(self.board.transcripts) + 1}", "text": text,
                                       "source": result.label, "note": f"Spoken report from {source}"})
        self.board.log("Listener Agent", result.label, f"Transcribed: \"{text[:90]}\"")
        return text, self.intake_message(text, f"{source} (transcribed)")

    def resolve_incident(self, incident_id: str, reported_by: str, text: str) -> tuple[str, ...]:
        """An incident is resolved (field unit or operator): close it, free its units, tell
        every agent and the freed crews, re-plan."""
        with self._atomic():
            incident = next((i for i in self.world.incidents if i.id == incident_id), None)
            if incident is None:
                raise NotFoundError(f"Unknown incident {incident_id}.")
            if incident.status == IncidentStatus.RESOLVED:
                raise ConflictError(f"{incident_id} is already resolved.")
            try:
                freed = self.world.resolve_incident(incident_id, f"{reported_by}: {text}")
            except ValueError as error:
                raise ConflictError(str(error)) from error
            self._mark_stale_locked()
            self._commit(plans=self._plans.values())
            self.resolutions.append({"at": _now(), "incident_id": incident_id, "by": reported_by,
                                     "text": text, "freed": freed})
        names = {s.id: s.name for s in self.world.services}
        self.board.retire(incident_id, reported_by)
        self.board.bus.publish(UNITS_CHANGED, {"why": f"{', '.join(names[f] for f in freed) or 'no units'} freed by {incident_id} resolving"})
        self.board.log("Coordinator", "field report",
                       f"{incident_id} resolved ({reported_by}); free again: "
                       f"{', '.join(names[f] for f in freed) or 'none'}", incident_id)
        self.propose()
        return freed

    # ----- command & comms (model-written text around the solver's decision) --

    def _in_background(self, work) -> None:
        if self.background_models:
            Thread(target=work, daemon=True).start()
        else:
            work()

    def _lead(self, proposal: PlanProposal) -> Incident | None:
        served = {d.incident_id for d in proposal.plan.assignments}
        candidates = [i for i in self.world.incidents if i.id in served]
        return min(candidates, key=self._swarm_priority) if candidates else None

    def _explain(self, proposal: PlanProposal) -> None:
        """Muse Spark explains the solver's recommendation from the swarm's evidence."""
        with self._lock:
            lead = self._lead(proposal)
            if lead is None:
                return
            names = {s.id: s.name for s in self.world.services}
            places = {point: name for name, point in self.scenario.places.items()}
            votes = self.board.votes.get(lead.id, {})
            unmet = [line.replace("Incident ", "", 1) for line in proposal.plan.rationale if "left unassigned" in line]
            facts = {
                "recommended": {"incident": lead.id, "type": lead.kind, "location": places.get(lead.location),
                                "people_at_risk": lead.people,
                                "units": [f"{names[d.service_id]} (ETA {d.estimated_arrival:.1f} min)"
                                          for d in proposal.plan.assignments if d.incident_id == lead.id]},
                # Sources (live/cache) are left out so a replayed run asks the same question.
                "swarm_votes": {agent: {"score": v.score, "reasons": list(v.reasons)}
                                for agent, v in votes.items()},
                "consensus": self.board.final(lead.id),
                "unmet": unmet,
            }
            run_id = self.run_id

        def fallback() -> dict[str, Any]:
            why = [f"{v['reasons'][0]} [{agent}]" for agent, v in facts["swarm_votes"].items() if v["reasons"]][:3]
            return {"why": why, "tradeoff": "; ".join(unmet) or "No other incident is left waiting."}

        def work() -> None:
            result = self.hub.recommend(facts, fallback)
            with self._lock:
                if run_id == self.run_id:
                    self.explanations[proposal.id] = result
                    self.unverified[proposal.id] = unsupported_numbers(result.value, facts)
                    self.board.log("Command Agent", result.label, f"Recommendation for {proposal.id} written")

        self._in_background(work)

    def _announce(self, proposal: PlanProposal) -> None:
        """After approval: a public alert in English and Spanish (Muse Spark), plus the graphic slot."""
        with self._lock:
            places = {point: name for name, point in self.scenario.places.items()}
            incidents = {i.id: i for i in self.world.incidents}
            served = sorted({d.incident_id for d in proposal.plan.assignments})
            facts = {
                "responding_to": [{"type": incidents[i].kind, "location": places.get(incidents[i].location)}
                                  for i in served if i in incidents],
                "closed_roads": sorted(self.scenario.road_names.get(r.id, r.id)
                                       for r in self.world.city.roads if r.is_closed),
            }
            run_id = self.run_id
        areas = sorted({item["location"] for item in facts["responding_to"] if item["location"]})

        def fallback() -> dict[str, Any]:
            where = ", ".join(areas) or "the affected areas"
            city = self.scenario.city_short
            if self.scenario.hazard == "flood":
                return {
                    "english": f"Flooding in {city}. Stay away from {where}. Do not drive through water; follow official instructions.",
                    "spanish": f"Inundaciones en {city}. Manténgase lejos de {where}. No maneje por el agua; siga las instrucciones oficiales.",
                }
            return {
                "english": f"Emergency in {city}. Stay away from {where}; follow official instructions.",
                "spanish": f"Emergencia en {city}. Manténgase lejos de {where}; siga las instrucciones oficiales.",
            }

        def work() -> None:
            text = self.hub.public_alert(facts, fallback)
            graphic = self.hub.alert_graphic(facts)
            with self._lock:
                if run_id == self.run_id:
                    self.alerts[proposal.id] = {"text": text, "graphic": graphic,
                                                "unverified": unsupported_numbers(text.value, facts)}
                    self.board.log("Command Agent", text.label, f"Public alert for {proposal.id} written (EN/ES)")

        self._in_background(work)

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


__all__ = [
    "CommandError",
    "CommandService",
    "ConflictError",
    "NotFoundError",
    "StaleApprovalError",
]
