# Submission draft — Meta AI Developer Hackathon

Status: **draft, not submitted.** Final submission, publishing, and any outreach require
Julian's explicit authorization.

## One-line description

Crisis Command is an emergency-coordination simulator in which specialist agents and a
deterministic planner show how a disruption breaks a multi-mission response plan, propose a
feasible revision with explicit tradeoffs, and wait for human approval bound to the exact
world version.

## Description (draft)

Emergency planners and exercise teams must understand how a changing situation affects many
missions at once. In Crisis Command, a synthetic flood exercise runs with six incidents and
eight resources. When the operator confirms that the main river bridge has closed, the system
releases missions that lost their route, recomputes the full plan from a fresh snapshot, and
explains every change. One fire engine is reassigned, two units are dropped because their
routes used the closed bridge (each citing the report that closed it), and two needs are left
visibly unmet, each naming the higher-priority incident holding the reachable units. The
operator approves the revised plan, and approval is refused if the world has changed since the
plan was computed.

AI interprets reports and explains the situation. Deterministic software computes routes,
assignments, and feasibility, and is the only writer of state. Every transition is recorded
in SQLite and can be replayed under a RECORDED REPLAY label. All data is synthetic, and the
system performs no real dispatch.

## Use of Meta AI tooling

**Current status: not yet integrated.** The model client interface, structured-output
schemas (report extraction and specialist findings), concurrent specialist execution with
timeouts, and honest `unavailable` / `failed` / `timed_out` states are implemented and tested.
No Meta model call is made yet because credentials were not available at the time of writing.
Do not describe Muse/Llama usage in the submission until the client is implemented and the
evaluation's AI configurations have actually been run.

Planned once credentials exist: schema-constrained report extraction, and the Situation,
Resources, and Routing specialists interpreting deterministic tool results, compared against
a single agent under the same tools (see docs/evaluation.md).

## Differentiation

- Closure-aware replanning across several simultaneous missions, with a diff that cites
  evidence, rather than single-incident routing.
- Hard separation between AI interpretation and authoritative state: version-bound approval,
  optimistic concurrency, transactional commits, and rollback.
- Unmet needs and tradeoffs stay visible; nothing is marked complete on agent failure.

## Evidence

- 66 automated tests (`python -B -m unittest discover -s tests`).
- Evaluation of the deterministic pipeline: 12 variants × 3 runs, all expectations met, 0
  failed-constraint proposals, 0 invalid commits (an enforced invariant). See
  docs/evaluation.md. AI configurations: blocked.
- Demo script with verified expected results: docs/demo.md.

## Known limitations

No model integration yet; planner exact over available units but never preempts committed missions; one synthetic scenario; one mission
per resource; no destination or shelter capacity; no authentication; no practitioner review
obtained.

## Users and next validation step

Initial users: municipal emergency-planning teams, campus emergency management, and exercise
organizers. Next step: with authorization, ask an EOC practitioner (for example, a campus
emergency-management team that runs exercises) what is unrealistic, what information is
missing before approval, and which resource constraint is absent. No such review has been
obtained, and none should be implied.

## Required links

| Item | Status |
| --- | --- |
| Repository URL and visibility | Needs Julian: confirm repo and visibility settings |
| Demo video | **Not recorded.** No recording was made in this environment; follow docs/demo.md |
| Live deployment | None (local quickstart only; deployment would need an access/security review) |

## Official requirements checklist

None of the following has been verified against live organizer materials. Each must be
checked on the event pages before submitting:

- [ ] Track eligibility and theme
- [ ] Submission portal and required fields
- [ ] Allowed prior work (this repository predates parts of the event window)
- [ ] Required Meta model/tool usage — **likely blocking until the model client exists**
- [ ] Video length and format
- [ ] Repository visibility and access for judges
- [ ] Deadline and time zone (planning reference: November 4, 2026, 9:00 p.m. America/New_York)
- [ ] Judging criteria and weights (secondary reports cite 30/25/25/20; unverified)
