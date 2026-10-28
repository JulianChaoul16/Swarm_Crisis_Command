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
## Gate C — presentation
## Gate D — proof
## Gate E — submission package
