# Demo script

Everything shown is simulated. Keep the SIMULATION label visible in every shot. Verify the
permitted video length in the official rules before editing; the narrative below targets
about three minutes.

## Setup

1. Start the API: `python -m uvicorn crisis_command.api.server:app --port 8000`
2. Start the dashboard: `cd app; npm run dev`, then open http://localhost:3000.
3. Press **Reset** so the run starts from the seed. Leave **Street basemap** off: the demo
   needs no network access for map data.

## Canonical scenario (live)

| Step | Action | Expected result |
| --- | --- | --- |
| 1 | Open the dashboard | 6 incidents, 8/8 resources, 1 road closed (upriver bridge). Plan P-001 covers all 6 incidents and all 8 constraint checks pass; proposed routes are dashed purple. |
| 2 | In **Intake**, enter text "People on a roof at East Junction", source "patrol", type incident, kind "rooftop rescue", severity high, location East Junction, needs rescue. Leave **People to move** empty and submit. | Report held as **needs clarification**, missing: people. World version unchanged. |
| 3 | Set People to move to 2 and **Save fields**, then **Confirm as authoritative**. | New incident added; P-001 marked stale; a new proposal shows the rescue as unmet because both boats are on higher-priority incidents. |
| 4 | Press **Confirm main street bridge closure**. | Report R-00n from "road authority" is filed and confirmed through intake. Bridge turns red dashed. |
| 5 | Read **Review**. | Fire Engine 1 **Changed** to the transformer fire; Fire Engine 2 and Ambulance 1 **Dropped** with "route used closed main-street-bridge (R-00n)"; incident-4, incident-6, and the rooftop rescue from step 3 listed as unmet, each naming the higher-priority incident holding the reachable units. Specialists show **unavailable** (no model configured). |
| 6 | Click the **R-00n** link in an explanation. | The confirming report opens in Intake. |
| 7 | Press **Approve P-00n (vN)**. | Plan approved; units turn blue (en route); timeline shows `plan_decision`, `service_dispatched`, `plan_approved`. A follow-up proposal lists remaining unmet needs. |
| 8 | Press **Run** (or **Advance clock**). | Units move along committed routes; incidents resolve as all assigned units arrive. Run pauses automatically if a proposal with dispatches awaits review. |

Without step 2–3 the closure produces exactly: fire-1 reassigned, fire-2 and medic-1 dropped,
incident-4 and incident-6 unmet. This is asserted in `tests/test_flood_scenario.py` and the
`confirmed_closure` evaluation variant.

## Stale-state rejection (technical example)

Shown by tests rather than the UI, because it requires a change landing between review and
approval:

- `tests/test_command_service.py::ApprovalTests::test_world_change_after_proposal_blocks_approval_and_recomputes`
  changes a road after a proposal is made, then approves the old plan: approval is refused
  with HTTP 409 semantics, the plan is marked stale, and a replacement is computed.
- `evaluation/run_eval.py` variant `update_during_planning` lands a closure while the planner
  is running: the in-flight proposal is discarded and recomputed.

In the UI, approving a plan after **Advance clock** changed the world shows the refusal
banner and the replacement plan ID.

## Reset

**Reset** restores the exact seed (bridge open, upriver bridge closed, P-001 assignments) under
a new run ID. Results computed for the previous run are discarded.

## Replay

Switch to **Replay**, pick a recorded run, and play or scrub. The dark **RECORDED REPLAY**
banner stays visible: states and events are read back from SQLite and no inference or
planning runs. Use replay footage only with that label in frame.

## Invariants to point out

- Nothing dispatches without an approval naming the plan and its world version.
- Every proposal lists constraint checks; unmet needs never disappear.
- Agent states are shown as reported (`unavailable`, `failed`, `timed_out`, `completed`).
