"""Run the synthetic scenario variants through the real command service.

    python -m evaluation.run_eval

Configuration 1 (deterministic planner given correct structured input) runs here.
Configurations 2 (one AI agent) and 3 (three specialists) need a model client and are
recorded as blocked until one is configured; no AI results are estimated or simulated.
Results are written to docs/evaluation/results.json.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import statistics
import tempfile
from time import perf_counter
from typing import Callable
from unittest.mock import patch

from crisis_command.agents.model_client import UnavailableModelClient
from crisis_command.database import connection
from crisis_command.operations import CommandService, PlanStatus, ReportStatus
from crisis_command.operations.feasibility import all_passed, check_plan
from crisis_command.operations.scenario import FLOOD_SCENARIO, build_flood_world

RUNS_PER_VARIANT = 3
SEVERITY_WEIGHT = {1: 1, 2: 2, 3: 3, 4: 4}
RESULTS_PATH = Path(__file__).resolve().parents[1] / "docs" / "evaluation" / "results.json"


@dataclass
class Outcome:
    passed: bool
    observations: dict
    update_to_proposal_ms: float | None = None


def _rescue(location: str, people: int, severity: str = "critical") -> dict:
    return {
        "report_type": "incident",
        "kind": "rescue",
        "severity": severity,
        "location": location,
        "required_services": ["rescue"],
        "people": people,
    }


def _timed(action: Callable[[], object]) -> float:
    started = perf_counter()
    action()
    return (perf_counter() - started) * 1000


def _unmet(service: CommandService) -> list[str]:
    return list(service.current_plan.plan.unassigned_incident_ids)


def _weighted_unmet(service: CommandService) -> int:
    incidents = {incident.id: incident for incident in service.world.incidents}
    return sum(SEVERITY_WEIGHT[int(incidents[i].severity)] for i in _unmet(service))


# ----- variants: each returns an Outcome checked against authored expectations -----

def baseline(service: CommandService) -> Outcome:
    plan = service.current_plan
    return Outcome(plan.valid and not _unmet(service), {"assignments": len(plan.plan.assignments)})


def straightforward_intake(service: CommandService) -> Outcome:
    report = service.submit_report("Two people on a car roof at East Junction", "patrol",
                                   fields=_rescue("East Junction", 2, "high"))
    ms = _timed(lambda: service.confirm_report(report.id))
    incident_ids = {incident.id for incident in service.world.incidents}
    return Outcome(
        report.status == ReportStatus.READY and len(incident_ids) == 7 and service.current_plan.valid,
        {"unmet": _unmet(service)},
        ms,
    )


def missing_field(service: CommandService) -> Outcome:
    fields = _rescue("Shockoe Bottom", 0)
    del fields["people"]
    version = service.world.version
    report = service.submit_report("People stuck near Shockoe Bottom", "caller", fields=fields)
    return Outcome(
        report.status == ReportStatus.NEEDS_CLARIFICATION
        and report.missing_fields == ("people",)
        and service.world.version == version,
        {"missing": list(report.missing_fields)},
    )


def ambiguous_closure(service: CommandService) -> Outcome:
    report = service.submit_report("Someone says the bridge might be flooded", "social media", fields={
        "report_type": "road_status", "road_id": "main-street-bridge", "road_closed": True, "certainty": "possible",
    })
    bridge = next(road for road in service.world.city.roads if road.id == "main-street-bridge")
    return Outcome(
        report.status == ReportStatus.READY and bool(report.warnings) and not bridge.is_closed,
        {"warnings": list(report.warnings), "bridge_closed": bridge.is_closed},
    )


def capability_mismatch(service: CommandService) -> Outcome:
    report = service.submit_report("Chemical smell at Manchester", "caller", fields={
        "report_type": "incident", "kind": "chemical leak", "severity": "high",
        "location": "Manchester", "required_services": ["hazmat"],
    })
    return Outcome(
        report.status == ReportStatus.NEEDS_CLARIFICATION and any("hazmat" in e for e in report.validation_errors),
        {"errors": list(report.validation_errors)},
    )


def capacity_shortage(service: CommandService) -> Outcome:
    report = service.submit_report("Twelve residents stranded at High School Shelter", "caller",
                                   fields=_rescue("High School Shelter", 12))
    ms = _timed(lambda: service.confirm_report(report.id))
    reasons = [line for line in service.current_plan.plan.rationale if "people" in line and "left unassigned" in line]
    return Outcome(bool(reasons) and service.current_plan.valid, {"reasons": reasons}, ms)


def competing_requests(service: CommandService) -> Outcome:
    ms = 0.0
    for location in ("East Junction", "West End"):
        report = service.submit_report(f"Family on a roof at {location}", "caller", fields=_rescue(location, 3))
        ms += _timed(lambda: service.confirm_report(report.id))
    reasons = [line for line in service.current_plan.plan.rationale if "committed to higher-priority" in line]
    return Outcome(bool(reasons) and service.current_plan.valid, {"tradeoffs": reasons}, ms)


def unavailable_resource(service: CommandService) -> Outcome:
    # Seed variant: Rescue Boat 1 is out of service before the run starts.
    return Outcome(
        "incident-2" in _unmet(service) and service.current_plan.valid,
        {"unmet": _unmet(service)},
    )


def confirmed_closure(service: CommandService) -> Outcome:
    ms = _timed(lambda: service.inject_disruption("bridge_closure"))
    changed = sorted(c.service_id for c in service.current_plan.changes if c.change in ("reassigned", "dropped"))
    return Outcome(
        changed == ["fire-1", "fire-2", "medic-1"] and _unmet(service) == ["incident-4", "incident-6"]
        and service.current_plan.valid,
        {"changed": changed, "unmet": _unmet(service)},
        ms,
    )


def update_during_planning(service: CommandService) -> Outcome:
    original = service.planner.plan
    calls = {"n": 0}

    def plan_while_world_changes(*args, **kwargs):
        result = original(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:  # a closure lands while the first plan is being computed
            service.world.city.set_road_closed("main-street-bridge", True, "Evaluation: change during planning.")
        return result

    with patch.object(service.planner, "plan", side_effect=plan_while_world_changes):
        ms = _timed(service.propose)
    kinds = [event.kind for event in service.world.event_log]
    plan = service.current_plan
    return Outcome(
        "proposal_discarded" in kinds and plan.world_version == service.world.version and plan.valid,
        {"discarded": kinds.count("proposal_discarded"), "final_version": plan.world_version},
        ms,
    )


def closure_after_approval(service: CommandService) -> Outcome:
    plan = service.current_plan
    service.approve(plan.id, plan.world_version)
    ms = _timed(lambda: service.inject_disruption("bridge_closure"))
    released = sorted(m.incident_id for m in service.current_plan.invalidated)
    return Outcome(released == ["incident-3", "incident-4"], {"released": released}, ms)


def reopen_after_closure(service: CommandService) -> Outcome:
    service.inject_disruption("bridge_closure")
    report = service.submit_report("Bridge inspected and reopened", "road authority", fields={
        "report_type": "road_status", "road_id": "main-street-bridge", "road_closed": False, "certainty": "confirmed",
    })
    ms = _timed(lambda: service.confirm_report(report.id))
    return Outcome(not _unmet(service) and service.current_plan.valid, {"unmet": _unmet(service)}, ms)


def _without_boat_1():
    world = build_flood_world()
    services = [service for service in world.services if service.id != "boat-1"]
    world.services.clear()
    list.extend(world.services, services)
    return world


VARIANTS: list[tuple[str, Callable[[CommandService], Outcome], object]] = [
    ("baseline", baseline, FLOOD_SCENARIO),
    ("straightforward_intake", straightforward_intake, FLOOD_SCENARIO),
    ("missing_field", missing_field, FLOOD_SCENARIO),
    ("ambiguous_closure", ambiguous_closure, FLOOD_SCENARIO),
    ("capability_mismatch", capability_mismatch, FLOOD_SCENARIO),
    ("capacity_shortage", capacity_shortage, FLOOD_SCENARIO),
    ("competing_requests", competing_requests, FLOOD_SCENARIO),
    ("unavailable_resource", unavailable_resource, replace(FLOOD_SCENARIO, build_world=_without_boat_1)),
    ("confirmed_closure", confirmed_closure, FLOOD_SCENARIO),
    ("update_during_planning", update_during_planning, FLOOD_SCENARIO),
    ("closure_after_approval", closure_after_approval, FLOOD_SCENARIO),
    ("reopen_after_closure", reopen_after_closure, FLOOD_SCENARIO),
]


def _invariants(service: CommandService) -> dict:
    """Count proposals that failed checks, and approved plans that were invalid when committed."""
    plans = list(service._plans.values())
    invalid_proposals = sum(1 for plan in plans if not plan.valid)
    approved = [plan for plan in plans if plan.status == PlanStatus.APPROVED]
    # Approval re-checks feasibility at commit; record that every approved plan passed it.
    invalid_committed = sum(1 for plan in approved if not all_passed(plan.checks))
    return {
        "proposals": len(plans),
        "invalid_proposals": invalid_proposals,
        "approved_plans": len(approved),
        "invalid_committed_plans": invalid_committed,
        "final_plan_checks_pass": all_passed(
            check_plan(service.current_plan.plan, service.world.incidents, service.world.services, service.world.city)
        ) if service.current_plan and service.current_plan.status == PlanStatus.PROPOSED else None,
    }


def run() -> dict:
    # Configuration 1 is the deterministic pipeline; it never calls a model, even when one
    # is configured. Model configurations are measured separately (findings/ branch).
    client = UnavailableModelClient("configuration 1 runs without a model")
    results = []
    for name, variant, scenario in VARIANTS:
        runs = []
        for run_index in range(RUNS_PER_VARIANT):
            with tempfile.TemporaryDirectory() as tmp, patch.object(connection, "DB_PATH", Path(tmp) / "eval.db"):
                service = CommandService(scenario=scenario, client=client, agent_timeout=5)
                outcome = variant(service)
                runs.append(
                    {
                        "run": run_index + 1,
                        "expectation_met": outcome.passed,
                        "update_to_proposal_ms": None if outcome.update_to_proposal_ms is None else round(outcome.update_to_proposal_ms, 2),
                        "priority_weighted_unmet": _weighted_unmet(service),
                        "unmet_incidents": _unmet(service),
                        "agent_statuses": service.agent_statuses(),
                        "observations": outcome.observations,
                        **_invariants(service),
                    }
                )
        latencies = [r["update_to_proposal_ms"] for r in runs if r["update_to_proposal_ms"] is not None]
        results.append(
            {
                "variant": name,
                "runs": runs,
                "expectation_met_runs": sum(r["expectation_met"] for r in runs),
                "median_update_to_proposal_ms": round(statistics.median(latencies), 2) if latencies else None,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "scenario": FLOOD_SCENARIO.name,
        "runs_per_variant": RUNS_PER_VARIANT,
        "priority_weights": SEVERITY_WEIGHT,
        "configurations": {
            "1_deterministic_planner_structured_input": {
                "status": "run",
                "note": "Operator-entered structured fields; specialists ran with model client "
                f"'{client.model_id}', so agent analysis is reported as unavailable, not completed.",
            },
            "2_single_ai_agent": {"status": "blocked", "reason": "No model client configured (META_API_KEY)."},
            "3_three_specialists": {"status": "blocked", "reason": "No model client configured (META_API_KEY)."},
        },
        "extraction_metrics": {"status": "not run", "reason": "Requires model-backed extraction."},
        "variants": results,
    }


def main() -> None:
    report = run()
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(report, indent=2))
    for variant in report["variants"]:
        first = variant["runs"][0]
        print(
            f"{variant['variant']:<24} expectation met {variant['expectation_met_runs']}/{RUNS_PER_VARIANT}  "
            f"median ms {variant['median_update_to_proposal_ms']!s:<7} weighted unmet {first['priority_weighted_unmet']}  "
            f"invalid proposals {first['invalid_proposals']}  invalid committed {first['invalid_committed_plans']}"
        )
    print(f"Wrote {RESULTS_PATH}")


if __name__ == "__main__":
    main()
