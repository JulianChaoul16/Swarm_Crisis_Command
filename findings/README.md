# Findings: what should our AI agents automate?

A test, separate from the main app, of which parts of Crisis Command work best when done by:

1. **a person** (the project author),
2. **algorithms** (rules, Dijkstra, A*, greedy, Hungarian), or
3. **an AI agent**.

Every arm gets the same cases, the same `cases/rubric.md` and the same `cases/town.json`, and is
scored by the same `score.py`.

## Tasks

| # | Task | Question |
|---|---|---|
| 1 | Reports | Turn a messy text report into kind, place, people, severity, needs and road claims |
| 2 | Conflicts | Resolve contradictory road reports by source tier, recency and hedging |
| 3 | Routing | Find the shortest open route between two places |
| 4 | Triage | Order incidents by the rubric |
| 5 | Assignment | Match units to incident needs at the lowest severity-weighted ETA |
| 6 | Replanning | Re-assign after a road on the committed plan closes |

## Layout

```
cases/
  rubric.md          the rules every arm follows
  town.json          fictional town: 15 places, 21 roads, three river crossings
  reports.json       20 hand-written reports
  conflicts.json     6 conflict sets
  scenarios.json     20 generated scenarios (seed 2026), 10 with a disruption
  answers/           answer keys; the human tester must not open this folder
arms/
  algorithms.py      algorithm arm
  human.py           (to do) terminal session that times the tester and saves answers
  ai_agent.py        (to do) model arm
build_cases.py       writes town.json, scenarios.json and answers/scenarios.json
oracle.py            brute-force answer keys (enumerates every path and every assignment)
common.py            shared rules: costs, slots, triage order, plan validation
score.py             scores every results/*.json, writes results/summary.md
results/
```

## Running

From the repo root:

```
python -m findings.build_cases        # only needed if the generator changes
python -m findings.arms.algorithms
python -m findings.score
```

## How answers are checked

- **Reports and conflicts**: hand-written answer keys that follow the rubric.
- **Routing and assignment**: the oracle enumerates every simple path and every slot-to-unit
  choice. It shares no search code with the arms, so Dijkstra and Hungarian are checked against
  an independent exhaustive search, not against themselves.
- **Triage**: the rubric's sort order. This is a definition, so the rule scores 100% by
  construction. The task measures whether a person or an AI applies a fixed rule reliably.

## Results so far

Algorithm arm only. Full table: `results/summary.md`.

- **Routing**: Dijkstra and A* found the shortest route on all 60 queries. Fewest-hops search
  found it on 83%.
- **Assignment**:
  - Hungarian found the best plan in 20 of 20 scenarios.
  - Greedy in priority order (the current app's approach) found it in 80% of scenarios. Its
    average cost was 16% above the best, and 224% above in the worst case.
  - Cheapest-pair-first greedy found it in 40%.
- **Replanning**:
  - Hungarian and priority greedy found the best plan in all 10.
  - The "sticky" Hungarian variant, which prefers keeping already-committed units when costs are
    close, also found the best plan in all 10. It changed fewer committed assignments: 0.1 per
    scenario, against 0.3.
- **Reports and conflicts**: the rules scored 100%. **Read this as a best case, not a finding.**
  The same author wrote the reports, the answer keys and the rules, so the rules fit these cases.
  A fair score needs reports the rules have never seen (see "Next steps").

## Changes made after seeing results

- **Scenario generator, version 2.**
  - **What changed:** in version 1, disruptions closed a random road, and in all 10 cases that
    road did not affect the optimal plan, so replanning tested nothing. Version 2 closes a road
    on the committed plan's routes.
  - **Side effect:** removing the old random draw shifted the seeded stream, so version 2's
    scenarios differ from version 1's.
  - **Version 1 results:**
    - Priority greedy found the best plan in 85% of scenarios, 3% above at worst.
    - Cheapest-pair-first greedy found it in 30%.
    - Hungarian found it in 100%.
  - **What the gap shows:** greedy's numbers depend on which 20 scenarios are drawn.

## Limitations

- One human tester, who is also the project author.
- Small case counts: 20 reports, 6 conflict sets, 20 scenarios.
- The town and all cases are fictional.
- Report answer keys involve judgement. The rubric narrows it but does not remove it.
- The cost model is a simplification:
  - Units start at their base and are not en route.
  - Each unit fills one slot.
  - There are no shelter limits.

## Next steps

1. Human arm: `arms/human.py`.
2. AI arm: `arms/ai_agent.py`.
   - Same rubric and cases; JSON output.
   - Three runs per case, to measure consistency.
3. Holdout reports for the AI-versus-rules comparison:
   - Freeze the rules first.
   - Then someone other than the rule author writes about 10 new reports with answer keys.
   - The rules and the AI are scored on them.
   - If the project author writes them, they can't also take them as the human tester.
