# Crisis Command

Adaptive AI coordination for a changing world. A swarm of specialist agents helps emergency
operations managers see how a disruption breaks a response plan, then proposes a feasible
alternative for human approval. Submission for the Meta AI Developer Hackathon.

> **Simulation only.** Crisis Command is an exercise and decision-support prototype. All
> incidents, resources, roads, and outcomes are synthetic. The map is an illustrative graph on
> a Richmond-inspired grid, not a validated representation of Richmond roads or emergency
> services. It performs no real dispatch and is not a certified emergency system.

## The problem

Municipal emergency planners, campus emergency-management teams, and exercise organizers
juggle several missions at once. When a bridge closes, they need to know which missions it
breaks, what the feasible alternatives are, and which needs can no longer be met, before
they commit anyone.

## What it does

- **Report intake with provenance.** Each report keeps its source, receipt time, extracted
  fields, and explicit unknowns. Incomplete or invalid reports are held for clarification,
  and nothing changes the world until an operator confirms a complete report. A report that a
  bridge *might* be closed cannot close it silently.
- **Three specialists, one authority.** Situation, Resources, and Routing specialists run
  concurrently over an immutable world snapshot with a shared timeout. Their graph and
  inventory tools are deterministic. Agents return findings; only the command service writes
  state.
- **Deterministic planning and feasibility.** Routes are graph shortest paths over open
  roads. Assignments respect capability, capacity, availability, and one active mission per
  resource, served in a stated, configurable priority order. Every proposal records
  per-rule constraint checks, and every unmet need explains its tradeoff.
- **Closure-driven replanning.** A confirmed closure releases committed missions that lost
  their route and recomputes the whole plan. The diff explains each change and cites the
  report behind it.
- **Human approval bound to a version.** Approval must name the current plan and the world
  version it was computed against. A stale plan is refused and recomputed. Each commit writes
  events, plan and approval records, and a world snapshot to SQLite in one transaction.
- **Seeded reset and recorded replay.** Reset restores the exact seed under a new run
  identity and discards in-flight results. Replay reads a past run back from the SQLite log
  under a RECORDED REPLAY banner.

The AI interprets and explains. Deterministic software computes routes, assignments, and
feasibility, and commits state. Humans approve dispatch-like transitions. See
[docs/architecture.md](docs/architecture.md).

## Model status

No Meta model client is implemented yet. Report extraction and specialist analysis report
`unavailable` in the UI and in every plan record; no canned or simulated AI output is shown.
Operators enter report fields directly in the meantime. To prepare for the model, copy
[`.env.example`](.env.example) and set `META_API_KEY` and `META_MODEL` in your shell.

## Quickstart

Requirements: Python 3.10+ and Node.js 20+. From the repository root:

```powershell
python -m pip install -r requirements.txt
python -m uvicorn crisis_command.api.server:app --port 8000
```

In a second terminal:

```powershell
cd app
npm install
npm run dev
```

Open http://localhost:3000. The dashboard proxies `/api/*` to `http://127.0.0.1:8000`; set
`CRISIS_API_ORIGIN` to point it elsewhere. Restart the API after Python changes, because
`--reload` can hang on Windows with this app. The npm scripts use webpack because Windows
Application Control can block Next.js's native Turbopack bindings. Runtime data is written
to `crisis_command/data/crisis_command.db` (ignored by git).

## Demo

The canonical scenario is a synthetic flood with 6 incidents, 8 resources, and one open river
crossing. Press **Confirm main street bridge closure**, review the revised plan, and approve
it. The full script, expected results, and reset/replay steps are in
[docs/demo.md](docs/demo.md).

## Tests and evaluation

```powershell
python -B -m unittest discover -s tests
python -m evaluation.run_eval
```

The evaluation runs 12 authored variants, 3 times each, through the real pipeline. Results
and their limits are in [docs/evaluation.md](docs/evaluation.md). The AI comparison
configurations are blocked until a model client exists.

## Repository layout

- `crisis_command/model.py`: incidents, resources (capability, speed, capacity), roads, and
  `City`, a NetworkX multigraph that routes around closed roads.
- `crisis_command/planner.py`: deterministic priority-order assignment with rationale and
  explicit unmet-need reasons. The `AuthoritativeStateAgent`
  (`crisis_command/agents/authoritative_state_agent.py`) arbitrates conflicting road claims by
  verification, authority, reliability, confidence, and recency.
- `crisis_command/simulation.py`: the versioned world with an ordered event log, snapshots,
  checkpoint/rollback, dispatch, clock advance, and mission invalidation.
- `crisis_command/operations/`: the command service (intake, proposals, approval, reset),
  feasibility checks, plan diffs, scenarios, and SQLite persistence.
- `crisis_command/agents/`: the model client interface and the three specialists.
- `crisis_command/database/`: SQLite schema and connection (with migrations for earlier
  schemas).
- `crisis_command/api/`: FastAPI routes and serializers.
- `app/`: the Next.js + MapLibre dashboard with Map, Intake, Review, Incidents, Resources, and
  Timeline surfaces, grid or table layout, and replay mode.
- `evaluation/`: the evaluation harness. `docs/`: architecture, demo, evaluation, submission
  draft, and the progress checklist.
- `crisis_command/main.py`: the original command-line demo (`python -m crisis_command.main`).

## Known limitations

- No model integration yet (see Model status).
- Assignment is solved exactly for the served set and its units, but committed missions are
  not preempted for later, higher-priority needs.
- One active mission per resource; transport to shelters and destination capacity are not
  modeled.
- One scenario; evaluation variants were authored by the developer.
- No authentication; intended for local use. Any public deployment needs a separate access
  and security review.
