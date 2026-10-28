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
- Planner claims retain their source, timezone-aware timestamp, analyzed world version,
  verification status, confidence, and evidence reference. Conflicts are resolved by
  verification status, configured source reliability, confidence, and recency; tied
  conflicting road reports are treated conservatively as closed. Claims from older
  world versions remain in the audit record but cannot override current road state.
  The current city-network road state is included as verified evidence; unknown sources
  have a neutral reliability score until configured.
- `crisis_command/agents/agent_models.py` contains the current role and agent data models.
- `crisis_command/database/` contains SQLite connection and repository code; the schema is in
  `crisis_command/database/schema.sql` and runtime database files are stored under ignored
  `crisis_command/data/`.
- The SQLite schema includes incidents, tasks, resources, world metadata, and events.
  Repository helpers create incidents/tasks and record matching events in one transaction;
  each change advances the persisted world version recorded on its event. Database
  initialization also upgrades the earlier events-table schema when needed.
- `crisis_command/coordinator/` coordinates planning and simulation and reports incidents
  that no available service can handle. The dashboard package is a boundary for future UI
  work; no dashboard implementation is present yet.
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

Run the unit tests with:

```powershell
python -B -m unittest discover -s tests
```
