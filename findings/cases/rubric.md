# Rubric

Every arm (human, algorithms, AI agent) gets this rubric and `town.json`. The answer keys in
`answers/` apply these rules exactly.

## Task 1: Reports

For each report, give:

- **kind**: one of
  - `flood_rescue`: people trapped by or in water
  - `evacuation`: people need moving out of a threatened area but are not trapped
  - `fire`
  - `medical`
  - `road_status`: the report is only about roads or bridges
  - `property_damage`
  - `other`
- **place**: the place id from `town.json` that the report is about (the first place if several
  are named). Use `null` for `road_status` reports and when no gazetteer place is named.
- **people**: how many people need help (rescue, treatment or moving).
  - Add counts together ("me and my 2 kids" = 3).
  - "a dozen" = 12, "50+" = 50, "like 30" = 30.
  - One described person ("an old man", "the driver") = 1.
  - Use `null` when no count is possible ("family", "everyone").
  - House numbers, years, distances and times are not people.
- **severity**:
  - `critical`: someone trapped, in the water, or in immediate danger to life.
  - `high`: injury, fire, a vulnerable person whose condition is unknown, or water about to
    reach people.
  - `medium`: displaced or inconvenienced people with no immediate danger.
  - `low`: road information, property damage, observations.
- **needs**: the set of unit capabilities required.
  - `boat`: people trapped by or in water.
  - `medic`: an injury or medical condition.
  - `fire`: fire or smoke.
  - `transport`: 10 or more people need moving.
  - Use `[]` if none apply.
- **road_claims**: every road whose status the report states as fact, as
  `{"road_id": ..., "status": "open" | "closed"}`.
  - Use road ids from `town.json`.
  - "Closed", "blocked", "out", "under water" and "water over road" mean `closed`.
  - "Open", "passable", "reopened" and "clear" mean `open`.
  - Ignore hedged statements ("I think", "maybe", a question mark).

## Task 2: Conflicting road reports

Each set holds several claims about one road. Resolve its status as follows:

1. Drop claims made more than 20 minutes before the newest claim in the set.
2. Ignore hedged claims.
3. Keep the highest source tier left: `official` > `responder` > `public`.
4. Within that tier, the newest claim wins.
5. If claims at that same newest time disagree, the answer is `unknown`.

Answer `open`, `closed` or `unknown`.

## Tasks 3–6: Scenarios

Distances are road lengths in km. A unit's ETA in minutes is its shortest open-road distance
divided by its speed (km per minute).

- **Routing**: the shortest total road length from A to B using only open roads. Answer a
  list of place ids from A to B, or `null` if B is unreachable.
- **Triage**: order the incidents by:
  1. severity, critical first
  2. people, more first
  3. reported minute, earlier first
  4. id
- **Assignment**: each incident has one *slot* per entry in its `needs`, with slot ids like
  `inc-2#0`, `inc-2#1`.
  - Fill each slot with at most one unit that has that capability.
  - Each unit fills at most one slot.
  - The unit must be able to reach the incident over open roads.
  - A filled slot costs severity weight × ETA. An unfilled slot costs severity weight × 100.
  - Weights: critical 8, high 4, medium 2, low 1.
  - Minimize the total cost.
  - Answer a list of `{"slot": ..., "unit": ...}` and leave unfilled slots out.
- **Replanning**: after the disruption closes one more road, give a new assignment under the
  same rules. Changes from the prior plan are reported as churn but do not affect the score.

Units start at their listed location. Nobody is en route.
