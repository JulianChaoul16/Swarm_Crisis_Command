# Contributing to Crisis Command

Thanks for helping. Crisis Command is meant to work for any city and any hazard, so the most
useful contributions make it easier to bring a new place online or connect a real feed.

## Set up

Requires Python 3.12+ and Node 20+.

```powershell
pip install -r requirements.txt
cd app; npm install; cd ..
copy .env.example .env        # META_API_KEY is optional; without it the agents fall back to rules
python -m uvicorn crisis_command.api.server:app --port 8000
cd app; npm run dev           # http://localhost:3000
```

## Before you open a pull request

```powershell
python -m unittest discover -s tests   # all tests run offline; none call the network
cd app; npm run typecheck
```

- Keep the rules in the README's **Trust** section: the LLM never writes world state, never
  sets the plan, and never states a number that isn't in its input.
- Code makes the exact decisions (routing, assignment, capacity, feasibility); models read,
  look, vote and explain.
- Don't commit keys, `.env` files, recordings from a private run, or real people's reports.
- If you change a Muse Spark prompt, re-record the demo (`python tools/record_demo.py`) so the
  replayed answers still match.

## Good places to start

| Area | What's needed |
|---|---|
| **A new city** | A `Scenario` in `crisis_command/operations/scenario.py` (places, roads, crews, name, hazard), a street-geometry file, and its plan documents. `crisis_command/api/geo.py` reads Richmond's places today; making it read geometry per scenario is the first step. |
| **Real feeds** | River gauges (e.g. USGS), CAD, unit GPS, hospital beds. `crisis_command/connectors.py` shows the pattern. |
| **Vision** | Decode SAM 3.1 masks, and georeference images instead of the assumed footprint. |
| **Other hazards** | Fire, storm or heat layers alongside the flood gauge and projection. |
| **Citizen hub** | Notifications and more languages for the public alert. |

## Reporting problems

Open a GitHub issue with what you did, what you expected and what happened. For anything
security-related, follow [SECURITY.md](SECURITY.md) instead.

By contributing, you agree that your work is released under the [MIT License](LICENSE).
