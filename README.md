# Swarm_Crisis_Command-
A Swarm of Agents that help  Emergency Operation Managers make the best decisions during crisises. Submission for Meta AI Devleoper Hackathon



## Biggest Takeaway from Demo 
- dependency-aware multi-agent replanning

## Crisis response prototype
- `model.py` defines incidents, coordinates, service capabilities, severity, and status.
- `City` in `model.py` stores road segments in a NetworkX multigraph, including
  parallel roads, and calculates shortest routes and distances without using closed roads.
- The planner uses road routes for dispatch ETAs, and the simulation moves services
  along those roads rather than directly toward incidents.
- The world can be copied into a planning snapshot; each plan carries its source
  version and is rejected if the world changes before assignments are committed.
- The world exposes an ordered event log for initial state, incident/resource updates,
  road changes, dispatch, movement, resolution, and simulation ticks.
- `planner.py` prioritizes open incidents and assigns the nearest available services,
  covering every service capability an incident requires.
- `simulation.py` advances the world one time step at a time, moving dispatched
  services and resolving incidents when all assigned services arrive.
- `main.py` runs a small command-line demonstration.

Install dependencies and run the demo with Python 3.10 or later:

```powershell
python -m pip install -r requirements.txt
python main.py
```

Run the unit tests with:

```powershell
python -B -m unittest discover -s tests
```
