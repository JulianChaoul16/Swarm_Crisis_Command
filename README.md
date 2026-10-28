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
  version and is rejected if the world changes before assignments are committed.
- The world exposes an ordered event log for initial state, incident/resource updates,
  road changes, dispatch, movement, resolution, and simulation ticks.
- `crisis_command/agents/agent_models.py` contains the current role and agent data models.
- `crisis_command/database/` contains SQLite connection and repository code; the schema is in
  `crisis_command/database/schema.sql` and runtime database files are stored under ignored
  `crisis_command/data/`.
- `crisis_command/coordinator/` coordinates planning and simulation and reports incidents
  that no available service can handle. The dashboard package is a boundary for future UI
  work; no dashboard implementation is present yet.
- The planner prioritizes open incidents and assigns the nearest available services,
  covering every service capability an incident requires.
- The simulation advances the world one time step at a time, moving dispatched
  services and resolving incidents when all assigned services arrive.
- Database modules are currently a scaffold and are not yet wired to the in-memory
  simulation state; integrate persistence after the coordinator interfaces settle.

Install dependencies and run the demo from the repository root with Python 3.10 or later:

```powershell
python -m pip install -r requirements.txt
python -m crisis_command.main
```

Run the unit tests with:

```powershell
python -B -m unittest discover -s tests
```
