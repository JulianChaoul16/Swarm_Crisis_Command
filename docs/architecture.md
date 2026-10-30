# Architecture

```text
Dashboard (Next.js + MapLibre, app/)
   │  HTTP /api/* (proxied by Next.js)
FastAPI (crisis_command/api/)
   │
Command service (crisis_command/operations/service.py)  ← only writer of authoritative state
   ├─ Intake: reports → validation → operator confirmation
   ├─ Coordinator: snapshot → specialists ∥ → planner → feasibility checks → proposal
   ├─ Approval: plan ID + world version → recheck → commit
   └─ Store: one SQLite transaction per commit (events, plans, approvals, snapshot)
        │
World (crisis_command/simulation.py): versioned state, event log, checkpoint/rollback
Planner (crisis_command/planner.py): deterministic routing and assignment
Specialists (crisis_command/agents/specialists.py): deterministic tools + model analysis
```

## Responsibilities

| Concern | Owner | AI involvement |
| --- | --- | --- |
| Interpret a free-text report | Model extraction (when configured) or operator | Proposes fields; never applies them |
| Validate report fields | `reports.validate_fields` | None |
| Confirm a report as fact | Operator, via the command service | None |
| Explain the situation, shortages, reachability | Situation / Resources / Routing specialists | Model summarizes deterministic tool results |
| Routes and assignments | `Planner` (NetworkX shortest paths, priority order) | None |
| Hard constraints | `feasibility.check_plan` | None |
| Approve dispatch | Operator | None |
| Write state and history | `CommandService` + `OperationsStore` | None |

An LLM recommendation is never treated as proof of feasibility. The model does not compute
graph paths, invent resources, or commit state.

## State, version, and approval flow

1. **Snapshot.** The coordinator copies the world (`World.snapshot`) under the service lock
   and records the run ID, world version, and a state signature.
2. **Analyze and plan without the lock.** Specialists run concurrently with a shared timeout,
   and the planner computes a proposal from the snapshot. Model latency never blocks other
   requests.
3. **Store or retry.** Under the lock again: if a reset happened, the result is discarded. If
   the world version or signature moved, a `proposal_discarded` event is recorded and planning
   retries (at most 3 attempts, then an explicit `proposal_pending` event). Otherwise the
   proposal is stored with its checks, its diff from the previous unexecuted proposal, and the
   specialist findings. The previous proposal is marked superseded.
4. **Approve.** The operator approves a plan ID together with the world version shown. The
   service refuses approval if the plan is not current, is stale, names the wrong version, or
   fails a feasibility recheck against the live world. A stale plan triggers a recompute.
5. **Commit atomically.** The world is checkpointed, dispatches are applied, and new events,
   plan and approval rows, and a world snapshot are written in one SQLite transaction. If
   anything fails, the in-memory world, reports, and plans are rolled back to the checkpoint.

World changes (confirmed reports, clock ticks, mission invalidation) increment the world
version, so any outstanding proposal becomes stale and is recomputed in full. Selective
recomputation is deliberately out of scope.

## Feasibility rules

`references_exist`, `incidents_open`, `resources_available`, `no_double_booking`,
`capabilities_cover`, `capacity_sufficient`, `routes_open` (every route segment lies on an
open road), and `unmet_needs_reported` (every open incident is either served or listed as
unmet). If no complete solution exists, the plan is partial and lists unmet needs with reasons.

## Allocation objective

Assignment is solved exactly as a small integer program (scipy's HiGHS solver):

1. **Served set**: incidents are taken in priority order (default: severity, then people
   affected, then incident ID). Each one is served if that is still feasible alongside every
   higher-priority choice.
2. **Units**: for that set, units are chosen to minimize severity-weighted ETA.

An incident counts as served only when its units cover every required capability and can
carry all its people. Each unit serves at most one incident. Incidents that cannot be fully
served receive no partial assignment and are reported as unmet, with the reason.

`tests/test_planner_optimal.py` checks the result against an exhaustive search. The earlier
greedy pass was compared on 500 random instances:
- In 46 it served a worse set by priority.
- In 58 it served the same set with a higher weighted ETA.

See also `findings/` on the `findings` branch.

## Failure handling

- Specialist model errors, invalid output, and timeouts are recorded as `failed` or
  `timed_out` and never displayed as completed analysis. A missing model is `unavailable`.
- Reset starts a new run ID. Results computed for an older run are discarded before they can
  touch the new world.
- SQLite write failures roll back in-memory state (tested).

## Persistence

SQLite tables: the original `incidents`, `tasks`, `resources`, and `world_metadata`; `events`
extended with run, sequence, actor, and entity references; plus `runs`, `reports`, `plans`,
`approvals`, and `world_snapshots`. Replay reads `events` and `world_snapshots` for a run.
Model reasoning and secrets are not persisted.
