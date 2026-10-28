# Crisis Command — Progress Checklist

Tracks work against `context/Crisis_Command_Hackathon_Handoff.md`. Items are marked done only
with evidence (command + result). Branch: `feature/hackathon-mvp`, one commit per verified gate.

## Decisions (2026-10-28)

- Meta model credentials are not available yet. Model-backed steps run behind a client
  interface and report an explicit `model_unavailable` state; nothing substitutes canned output.
- Dashboard is cut to the MVP surfaces: Map, Intake, Plan diff, Review, Timeline.
- Map renders the road graph without network access; the street basemap is optional and
  labeled illustrative.
- One active mission per resource (documented simplification, per handoff §7).
- Simulation clock advances only on explicit Advance / Run. Any clock tick changes the world
  version, so a pending proposal becomes stale and is recomputed (handoff §6.6–6.7).

## Audit (start of Gate A)

- Stack: Python 3.14, NetworkX, FastAPI (`crisis_command/api`), SQLite (`crisis_command/database`),
  Next.js 16 + MapLibre (`app/`, webpack because Windows Application Control blocks Turbopack).
- Present: graph routing over open roads, deterministic planner with evidence arbitration,
  versioned world with OCC checks and in-memory event log, 36 passing unit tests.
- Gaps vs. handoff: no report intake/provenance; plan and dispatch happen in one step (no human
  approval); SQLite not connected to the world; no plan IDs/lifecycle; no specialist agents or
  model client; demo world is 2 incidents / 3 units with no bridge; no capacity; external map
  tiles; no replay; no evaluation; no docs beyond README.

## Gate A — core vertical slice

- [x] Report intake with provenance, validation, missing fields, operator confirmation
  (`crisis_command/operations/reports.py`, `service.py`; tests `IntakeTests`)
- [x] Propose / approve / reject split; approval bound to plan ID and world version
  (`CommandService.propose/approve/reject_plan`; `Simulation.apply_dispatches`)
- [x] Stale approval refused and proposal recomputed (`test_world_change_after_proposal_*`,
  `test_superseded_plan_cannot_be_approved`)
- [x] Commits and events persisted to SQLite in one transaction, in-memory rollback on
  failure (`operations/store.py`; `test_failed_persistence_rolls_back_the_commit`)
- [x] Specialist interface with honest `unavailable` / `failed` / `timed_out` states
  (`agents/specialists.py`, `agents/model_client.py`; `SpecialistTests`)
- [x] Reset during planning discards the old run's result (`test_reset_during_planning_*`)
- [x] Dashboard wired to the new flow; out-of-scope mock surfaces removed
- [x] Evidence: `python -B -m unittest discover -s tests` → 57 tests OK (2026-10-28).
  Browser smoke test: report R-001 filed with a missing field → clarified → confirmed as
  incident-3 → P-001 marked stale → P-002 approved at v2 → units en route; SQLite run
  shows the matching events, approval (P-002, v2, operator), and report link.

Known limitations after Gate A:
- No model client yet; extraction and specialist analysis are `unavailable`.
- `incident_added` events carry actor `simulation`; the confirming operator is recorded on
  the paired `report_confirmed` event.
- Committed missions blocked by a later closure are released on the next clock tick
  (Gate B will invalidate them immediately).
- Map still loads external tiles (Gate C).

## Gate B — adaptation

- [x] Seeded flood scenario: 6 incidents, 8 resources, river crossing only at the main street
  bridge (upriver bridge closed at seed) (`operations/scenario.py`)
- [x] Capacity modeled: incident `people`, unit `capacity`; planner adds carriers and reports
  "can carry X of Y people"; feasibility rule `capacity_sufficient`. Rescue reports without a
  head count are held for clarification. Destination (shelter) capacity is not modeled.
- [x] Priority rule explicit and configurable (`Planner(priority_key=..., priority_rule=...)`),
  stated in every plan's rationale and in the Review panel
- [x] Confirmed closure releases committed missions with no open route immediately
  (`Simulation.release_unreachable_missions`, `mission_invalidated` events)
- [x] Full recompute after the change; diff explains each change (closed road on the earlier
  route, replacement, reassignment, reroute) and unmet needs name the higher-priority tradeoff
- [x] Bridge closure is a scenario control that files and confirms an ordinary report
  (`CommandService.inject_disruption`, `POST /api/scenario/disruptions/bridge_closure`)
- [x] Evidence: 64 tests OK (`tests/test_flood_scenario.py` adds 7). Browser: P-001 covers all
  6 incidents → bridge closure → P-001 stale, P-002 at v2 moves fire-1 to incident-3, drops
  fire-2 and medic-1 (route used main-street-bridge), leaves incident-4 and incident-6 unmet
  with tradeoffs → P-002 approved → P-003 shows the remaining unmet needs.

Known limitations after Gate B:
- Greedy priority-order assignment, not a global optimizer; committed missions are not
  preempted for higher-priority needs (shown as "already on incident-N").
- One active mission per resource; units return to available on arrival (no transport to
  a destination is simulated).

## Gate C — presentation
## Gate D — proof
## Gate E — submission package
