# Swarm_Crisis_Command-
A Swarm of Agents that help  Emergency Operation Managers make the best decisions during crisises. Submission for Meta AI Devleoper Hackathon



## Biggest Takeaway from Demo 
- dependency-aware multi-agent replanning

## Application structure
- `crisis_command/main.py` runs the command-line demonstration.
- `crisis_command/model.py` defines incidents, coordinates, service capabilities, severity, and status.
- `City` in `crisis_command/model.py` stores road segments in a NetworkX multigraph, including
  parallel roads, and calculates shortest routes and distances without using closed roads.
- `crisis_command/planner.py` uses road routes for dispatch ETAs, and `crisis_command/simulation.py` moves services
  along those roads rather than directly toward incidents.
- The world can be copied into a planning snapshot; each plan carries its source
  version. If state changes during planning, the simulation independently checks
  whether the proposal remains safe and feasible; it executes a valid proposal before
  replanning, or rejects it and recalculates against the latest snapshot.
- The world exposes an ordered event log for initial state, incident/resource updates,
  road changes, accepted/rejected plans, dispatch, movement, resolution, and simulation
  ticks. Each plan records its rationale and evidence-backed claims.
- The `AuthoritativeStateAgent` is the planner's head-of-state preset. It resolves
  evidence by verification status, authority, configured source reliability, confidence,
  and recency. Verified authoritative road closures override an outdated road snapshot;
  `road_authority` and `authoritative_state_agent` are the preset authoritative sources.
  Unverified conflicts are flagged for review, and unknown road status blocks that road
  from route approval. Safety-critical incidents require a known city road network.
  Stale claims remain in the audit record but cannot override current state unless they
  are verified authoritative closures.
- Claims retain their source, timezone-aware timestamp, analyzed world version,
  verification status, confidence, and evidence reference. The current city-network
  road state is included as evidence; unknown sources have a neutral reliability score
  until configured. Approved and blocked decisions record their supporting claims,
  evidence references, version, and rationale.
- `crisis_command/agents/agent_models.py` contains the current role and agent data models.
- `crisis_command/database/` contains SQLite connection and repository code; the schema is in
  `crisis_command/database/schema.sql` and runtime database files are stored under ignored
  `crisis_command/data/`.
- The SQLite schema includes incidents, tasks, resources, world metadata, and events.
  Repository helpers create incidents/tasks and record matching events in one transaction;
  each change advances the persisted world version recorded on its event. Database
  initialization also upgrades the earlier events-table schema when needed.
- `crisis_command/coordinator/` coordinates planning and simulation and reports incidents
  that no available service can handle.
- `crisis_command/operations/` is the command service, the only writer of authoritative
  state. Reports keep their source, receipt time, extracted fields, missing fields, and
  status; nothing changes the world until an operator confirms a complete report. Each
  plan proposal records the world version it was computed against, per-rule feasibility
  checks, changes from the previous proposal, and specialist results. Approval must name
  the current plan and its world version; a stale plan is refused and recomputed. Every
  commit writes events, plan/approval records, and a world snapshot to SQLite in one
  transaction, and in-memory state is rolled back if that write fails.
- `crisis_command/agents/specialists.py` runs the Situation, Resources, and Routing
  specialists concurrently with a timeout. Their graph and inventory tools are
  deterministic; model analysis is reported only when a model call succeeds. No Meta model
  client is configured yet, so specialists and report extraction report `unavailable`.
- `crisis_command/operations/scenario.py` seeds the default synthetic flood exercise on a
  Richmond-inspired grid: 6 incidents, 8 resources, and one open river crossing. The
  "Confirm main street bridge closure" control files and confirms an ordinary report; committed
  missions that lose their route are released and the plan is recomputed in full. Incidents
  can require carrying people, and boats and ambulances have capacity. The planner serves
  incidents greedily in a stated, configurable priority order (a simulator setting, not
  triage doctrine) and explains every unmet need.
- `crisis_command/api/` exposes the command service over HTTP with FastAPI. Grid
  coordinates are projected onto a Richmond-inspired map area for display only.
- The dashboard map draws the road graph without network access; the street basemap is an
  opt-in toggle and is illustrative only. Replay mode reads a recorded run's events and world
  snapshots back from SQLite under a RECORDED REPLAY banner; it performs no inference.
- `app/` is the dashboard (Next.js, React, MapLibre) with a persistent SIMULATION label:
  map, report intake and clarification, plan review and approval, incidents, resources,
  and the event timeline, in a grid or table layout.
- The planner prioritizes open incidents by severity and chooses reachable services
  based on capability coverage and estimated arrival, recording rationale and claim
  arbitration with each version-tagged decision.
- The simulation advances the world one time step at a time, moving dispatched
  services and resolving incidents when all assigned services arrive.
- The database repository is usable on its own but is not yet wired to persist the
  coordinator's in-memory simulation state.

Install dependencies and run the demo from the repository root with Python 3.10 or later:

```powershell
python -m pip install -r requirements.txt
python -m crisis_command.main
```

Run the dashboard with two terminals from the repository root (Node.js 20 or later):

```powershell
python -m uvicorn crisis_command.api.server:app --port 8000
```

```powershell
cd app
npm install
npm run dev
```

Then open http://localhost:3000. The dashboard proxies `/api/*` to `http://127.0.0.1:8000`;
set `CRISIS_API_ORIGIN` to point it elsewhere. Restart the API after Python changes; `--reload` can hang on Windows with this app. Scripts use webpack because Windows
Application Control can block Next.js's native Turbopack bindings.

Run the unit tests with:

```powershell
python -B -m unittest discover -s tests
```
