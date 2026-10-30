# Evaluation

Scope: synthetic flood exercise only (`flood-exercise` scenario). These results describe the
simulator's deterministic pipeline on authored variants. They are not evidence about real
emergency operations, and they say nothing yet about AI quality.

Reproduce with:

```powershell
python -m evaluation.run_eval
```

Raw results: [`docs/evaluation/results.json`](evaluation/results.json) (generated
2026-10-28 23:08 UTC, Python 3.14.4, Windows 11, 12 variants × 3 runs).

## What was compared

| Configuration | Status |
| --- | --- |
| 1. Deterministic planner given correct structured input | **Run** |
| 2. One AI agent with the same tools | **Blocked**: no model client configured |
| 3. Three coordinated specialists | **Blocked**: no model client configured |

Configuration 1 used operator-entered structured fields. The three specialists still ran
their deterministic tools in every proposal, but with no model configured their analysis is
recorded as `unavailable`. No AI output was estimated, cached, or simulated. Extraction
accuracy and missing-information flag metrics need model-backed extraction and were not run.

## Method

Each variant starts a fresh command service with its own temporary SQLite database, applies
its updates through the same intake/confirm/propose/approve path as the app, and checks the
outcome against an expectation written in the harness before the run. Latency is wall-clock
time from submitting an accepted update to a stored, validated proposal, including the
specialist calls (which return `unavailable` immediately). It excludes human approval time.

Priority-weighted unmet demand sums simulator weights (low 1, medium 2, high 3, critical 4)
over incidents left unassigned in the final proposal. The weights are a documented simulator
setting, not triage doctrine.

## Results

| Variant | Expectation met | Median update→proposal (ms) | Weighted unmet | Unmet incidents |
| --- | --- | --- | --- | --- |
| baseline | 3/3 | — | 0 | none |
| straightforward_intake | 3/3 | 32.0 | 3 | incident-7 (no free boat) |
| missing_field | 3/3 | — | 0 | report held: `people` missing |
| ambiguous_closure | 3/3 | — | 0 | "possible" closure not applied until confirmed |
| capability_mismatch | 3/3 | — | 0 | report held: no `hazmat` resource |
| capacity_shortage | 3/3 | 34.2 | 4 | incident-7 (carry 0 of 12) |
| competing_requests | 3/3 | 65.9 | 7 | incident-8, incident-2 |
| unavailable_resource | 3/3 | — | 3 | incident-2 |
| confirmed_closure | 3/3 | 64.2 | 3 | incident-4, incident-6 |
| update_during_planning | 3/3 | 89.0 | 3 | incident-4, incident-6 |
| closure_after_approval | 3/3 | 42.6 | 5 | incident-3, incident-4 |
| reopen_after_closure | 3/3 | 44.6 | 0 | none |

"—" means the variant has no accepted world update (the report is held or rejected at
intake), so there is no update-to-proposal interval.

Across all 36 runs: 0 proposals failed a hard-constraint check, and 0 invalid plans were
committed. The second figure is an invariant enforced by approval (feasibility is rechecked
at commit and a stale plan is refused), not a measured advantage. The validator's rejection
path is exercised by deliberately constructed invalid plans in the unit tests
(`FeasibilityTests`), not by a naturally occurring failure.

## Observations

- `update_during_planning`: the in-flight proposal was discarded once and recomputed against
  the new snapshot in every run; no stale proposal was stored or approved.
- `competing_requests`: adding two critical rescues moved the north boat away from the
  high-severity incident-2. Both the new unmet critical incident and incident-2 show their
  reasons in the plan.
- `closure_after_approval`: the planner does not preempt committed missions, so
  incident-3 stays unmet while Fire Engine 1 completes a lower-priority committed mission.
  This tradeoff is shown to the operator rather than resolved automatically.

## Limitations

- One synthetic scenario; variants are authored by the developer and were not held out from
  tuning.
- The planner is exact only over available units. It does not preempt committed missions,
  so a plan can be worse than one that reassigned committed units.
- Latency figures are local, single-process, and exclude model latency because no model ran.
- No practitioner review has been obtained.
