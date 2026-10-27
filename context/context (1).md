# Crisis Command — Hackathon Grind Context

_Last updated: October 27, 2026_

## Purpose of this document

This file captures the design decisions, architecture lessons, hackathon strategy, MVP, example code, and next steps developed in our **Hackathon Grind / Crisis Command** conversations. It is intended as context for resuming development or providing to another coding assistant. Examples are illustrative and not evidence that the corresponding functionality is implemented.

## 1. Project and product vision

**Project name:** Crisis Command  
**Workspace/theme:** Hackathon Grind  
**Hackathon:** Meta AI Developer Hackathon (planning reference: submission deadline November 4, 2026, 9 PM ET; verify with official rules before submission).  
**Chosen direction:** Option B — Autonomous Multi-Agent Command Center, demonstrated safely through a simulated emergency environment (option C as the testing/simulation foundation).

**Tagline:** Adaptive AI coordination for a changing world.

**Product thesis:** An AI-powered command center coordinates specialized AI agents to transform changing crisis reports into feasible, auditable, resource-constrained response plans. Agents can investigate, propose, and replan; humans approve consequential real-world actions. The initial product is a **simulation / decision-support prototype**, not a live dispatch platform or certified emergency service.

**Proposed target users:** municipal emergency planning and operations teams, campus emergency-management staff, and disaster-response training organizations.

**Chosen starter domain:** synthetic flood / storm emergency response, optionally presented in a Richmond-inspired simulated map; **no actual emergency services or real-world dispatch actions**.

**Differentiating capability:** **adaptive multi-agent coordination and dependency-aware replanning**. When conditions change, identify which planned assignments depend on the changed roads/resources, revalidate the full plan, and replan affected work where possible rather than blindly restart all analysis. The safe MVP starts with full recomputation on any world-version mismatch; selective replanning is a follow-on optimization.

## 2. Judging criteria and proposed scoring strategy

| Criterion | Weight | What to demonstrate |
|---|---:|---|
| Impact | 30% | Specific users, credible use case, measurable gains in planning speed, feasibility, and coverage; no unsupported real-world safety claims |
| Use of Meta AI Tooling | 25% | Muse-driven report interpretation, model-driven delegation, structured outputs, parallel tool calls, synthesis and replanning; more than a superficial chatbot integration |
| Technical Depth & Execution | 20% | Sound module boundaries, parallel orchestration, graph algorithms, constrained optimization, validation, versioning, transactional state changes, fault handling, tests |
| Demo | 25% | Working command dashboard, visible parallel-agent activity, routes and assignment visualization, striking road-closure/replanning moment, benchmark evidence |

Previously considered **aspirational** targets (not forecast judge scores): Impact 9/10, Meta Tooling 9.5/10, Technical Depth 9/10, Demo 9.5/10, weighted 92.5/100.

**Strategic principle:** Build a small but complete and demonstrably reliable vertical slice, not a large collection of features. Dynamic replanning serves all four judging categories.

## 3. Concrete MVP

Starter sample dataset: **6 incidents, 8 resources, 3 specialist agents** (adjust for development convenience).

User flow:
1. Operator enters an emergency report in natural language.
2. Meta model parses it into a validated structured incident.
3. Specialist agents work concurrently, e.g. **Situation**, **Logistics/Resources**, and **Routing**.
4. Deterministic planner assigns compatible, available resources along feasible graph routes.
5. Dashboard shows incidents, resources, agent progress, recommendations and routes.
6. A simulated bridge/road closure modifies the world, invalidates dependent proposals, and triggers replanning.
7. Operator can inspect rationale, event history, validation/benchmark results, and approve a proposal. No live dispatch in MVP.

**Acceptance criteria:**
- Natural-language incident intake and structured validation.
- Parallel specialist-agent execution with observable results.
- Hard constraint validation for resource availability, capability, capacity, and traversable roads.
- Road closure event produces an updated feasible plan.
- Event trail records what changed and why.
- Human review and approval before any dispatch-like state transition.

**Defer:** real dispatch or external emergency integrations, user authentication, sophisticated GIS, weather forecasting models, autonomous real-world execution, microservices, and speculative infrastructure.

## 4. Demo story

1. Crisis begins on an interactive synthetic map.
2. Agents work simultaneously; show useful tool calls/results, not fake animation.
3. A resource-feasible plan appears with route lines and explanations.
4. A key bridge closes; invalid routes are highlighted.
5. System detects stale/dependent assignments, recomputes, preserves unaffected assignments where safe, and shows updated plan.
6. Compare latency, plan feasibility, replanning effort, and scenario coverage against baselines.

Use a deterministic, preloaded demo scenario and recording fallback, while keeping the core experience live and functional.

## 5. Benchmark plan

Compare on the same simulated scenarios:
- **A:** greedy deterministic resource allocation.
- **B:** a single Muse agent with appropriate planning tools.
- **C:** Crisis Command specialized multi-agent architecture with common deterministic constraint validation and selective replanning (when implemented).

Metrics: time to valid plan, percentage of proposals passing hard constraints, unaddressed incidents/coverage within scenario targets, percentage of replanning work avoided, timeout/failure recovery. **Do not assume parallel agents are faster; measure actual performance and model-call overhead.**

## 6. Architecture: current direction

```text
Dashboard (proposed: Next.js / React + MapLibre)
    |
FastAPI Orchestrator (Python, asyncio; Meta Muse integrations)
    |--- Situation agent: interpret incident data
    |--- Logistics agent: assess resource requirements / capabilities
    |--- Routing agent: analyze accessibility
    |
Deterministic Planning Engine
    |--- NetworkX road graph and shortest routes
    |--- Resource assignment and explicit constraints
    |--- Plan validation and dependency tracking
    |
Authoritative State Manager / persistence
    |--- Current world state and version
    |--- Structured event log / audit trail
    |--- Atomic commit of valid assignments
    |
Realtime dashboard updates (later WebSockets)
```

**Meta tooling plan:** use the hackathon-eligible Muse model, structured outputs and tool calls for interpretation/delegation/synthesis. Verify current supported model/API names and capabilities from official docs before implementation. Model choice may evolve. Optional aerial imagery/SAM integration only if it genuinely drives planning decisions; not necessary for MVP.

**Deterministic vs AI responsibilities:** AI interprets language, identifies information gaps, proposes priorities, and explains options; deterministic software validates hard constraints, computes graph routes and assignments, and commits authoritative state. An LLM recommendation is not itself proof of feasibility.

**Planning:** graph nodes represent locations, graph edges roads with travel-time weights and blocked status. First build nearest *eligible* available resource by shortest feasible route. Later introduce global assignment optimizer (e.g. OR-Tools), explicit unserved-incident penalties, priorities, workload and resource constraints. An illustrative objective discussed was `C(r,i) = alpha * travel_time + beta * priority_cost + gamma * workload`, with priority cost defined to favor urgent incidents; feasibility stays a hard constraint, not merely a penalty.

## 7. Repository organization — and why

Initial *small prototype* structure:

```text
crisis-command/
├── app/
│   ├── __init__.py
│   ├── models.py        # domain objects/schemas
│   ├── simulation.py    # owns simulated world mutation
│   ├── planner.py       # computes proposed feasible assignments/routes
│   └── main.py          # entrypoint / coordination
├── tests/
│   └── test_planner.py
├── requirements.txt
└── README.md
```

Later, if needed, refactor into grouped responsibilities:

```text
app/
├── domain/
│   ├── models.py
│   └── events.py
├── planning/
│   ├── optimizer.py
│   └── routing.py
├── agents/
│   ├── orchestrator.py
│   └── specialists.py
├── simulation/
│   └── world.py
└── api/
    └── routes.py
```

**Why:** organize around responsibilities rather than arbitrary folder conventions. High cohesion, low coupling. Example: switching route algorithms should mostly affect planning code, not API, simulation or entrypoint. Files/folders follow real boundaries; they do not create good architecture by themselves. Avoid premature microservices.

Four meanings of scalability discussed: code extensibility, runtime performance, team development, and operational reliability. For MVP prioritize maintainability, correctness, and enough concurrency to demonstrate the product.

## 8. Architecture training approach

User wants to improve at **architecture, structuring code, scaling systems and making design decisions independently**, not just copy code. Assistant should act as senior engineer/technical lead: ask the user to reason first, review tradeoffs honestly, explain concepts, then implement.

Five design questions, in order:
1. What must the system do? (behaviors before technologies)
2. What data and entities exist?
3. What responsibilities exist, and which module owns each one?
4. How do modules communicate? (explicit interfaces)
5. What is most likely to change? (protect the right boundaries)

Architecture practice loop: spend ~10 minutes designing, implement smallest end-to-end slice, introduce one change, observe how many modules it touches, refactor, and explain tradeoffs. Suggested book: *A Philosophy of Software Design* (John Ousterhout).

Example clean API:

```python
def create_plan(snapshot: WorldState) -> Plan:
    """Pure-ish deterministic calculation, no hidden DB writes or AI calls."""
    ...
```

Distinguish **proposed plan** from **committed, authoritative state**.

## 9. Architecture challenge history and decisions

### Challenge #1 — Which module blocks a road?
- **User chose B: `simulation.py`. Correct.**
- World mutation belongs with simulation/state; planner reads state and computes a route; main/orchestrator coordinates work. Models define data but don't necessarily coordinate mutation.
- Example:

```python
class Simulation:
    def __init__(self, graph):
        self.graph = graph

    def block_road(self, a, b):
        self.graph[a][b]["blocked"] = True
```

- Planner must exclude blocked edges before finding shortest path. Prefer immutable world snapshots once concurrency matters.
- Principles: separation of concerns, high cohesion, low coupling.

### Challenge #2 — Who chooses the resource assignment?
- **User chose B: Planning Engine. Correct.**
- User's reasoning: planner connects incident ID to a vehicle. Refined: planner evaluates hard constraints and optimization criteria; AI adds incident interpretation/recommendations, simulation provides facts, orchestrator coordinates.
- An important invariant: each resource has no more than one **active** assignment, assuming our V1 single-assignment resource model.
- Critical nuance: planning may propose a feasible allocation, but the authoritative manager must **atomically revalidate and commit** availability because the world may change between planning and commit.

### Challenge #3 — Two concurrent agents choose same ambulance
- **User chose A: let AI agents talk to each other. Not sufficient; C is best: authoritative state manager.**
- Agent coordination improves the *quality of proposals* but doesn't guarantee consistency because both could read an old state and submit conflicting actions (race condition / time-of-check to time-of-use).
- State manager commits under a lock/transaction; only one conflicting assignment succeeds.
- Illustrative process-local coroutine coordination:

```python
class StateManager:
    def __init__(self):
        self.assignments = {}
        self.lock = asyncio.Lock()

    async def commit_assignment(self, resource_id, incident_id):
        async with self.lock:
            if resource_id in self.assignments:
                return False
            self.assignments[resource_id] = incident_id
            return True
```

- A local `asyncio.Lock` guards only coroutines using the same instance/event loop; distributed/multiprocess deployments require shared transactional coordination (e.g. DB). Agents can still communicate for better reasoning.

### Challenge #4 — Road changes while planner computes
- **User chose C: versioned world state. Correct.**
- User's reasoning: planning can continue while new updates happen, and new versions lead to updated calculations. Key refinement: versioning does **not** automatically revise a running calculation; it lets us detect stale proposals and decide whether to reject/revalidate/recompute.
- Concept: **Optimistic Concurrency Control (OCC)**: read consistent versioned snapshot → compute without a long lock → atomically compare version and validate at commit.
- Example:

```python
def create_plan(snapshot: WorldState) -> Plan:
    return Plan(
        world_version=snapshot.version,
        assignments=calculate_assignments(snapshot)
    )

def commit_plan(plan: Plan, state: StateManager):
    with state.lock:
        if plan.world_version != state.version:
            raise StalePlanError()
        validate_assignments(plan.assignments, state.world)
        state.apply_plan(plan)
        state.version += 1
```

- Must check version and apply atomically. A coarse global-version check is safe but can cause unnecessary retries. Later optimize with dependency-aware revalidation on road edges, reservations and assignments; shared resource constraints can propagate impact beyond direct road dependencies.
- Tradeoffs: locking entire world hurts responsiveness; restarting for every change wastes work; trusting AI cannot guarantee correctness.

### Challenge #5 — Where/how to store current state and historical decisions?
- **User chose C: current authoritative state plus structured event log. Correct.**
- User's reasoning: frequent queries need fast access to current state, while older data supports extensive searches. Strong systems-thinking about **access patterns**.
- Refined: consistency between current state and history matters; persist both within a single transaction. Indexed event searching may be needed at larger scale.
- **Event logging**: current state is authoritative; log records what happened for traceability/debugging. Recommended for Crisis Command V1.
- **Event sourcing**: ordered immutable event history is authoritative, and current state is a derived projection/replay (possibly accelerated by snapshots). Enables historical reconstruction but adds event versioning, ordering, replay correctness and side effect complexities. Not needed for MVP.
- Suggested starter SQLite schema:

```sql
CREATE TABLE resources (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    incident_id TEXT
);
CREATE TABLE world_metadata (
    id INTEGER PRIMARY KEY,
    version INTEGER NOT NULL
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    resource_id TEXT,
    incident_id TEXT,
    world_version INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
```

- Transaction path: validate current state and expected version → update current state → increment version → append structured event with cause and metadata → commit; rollback all on failure. Add idempotency if retrying changes.

### Challenge #6 — Pending unanswered challenge: routing agent fails

Scenario: three parallel agents run; Situation Agent and Resource Agent finish, but Routing Agent times out. Routing is needed to validate accessibility and produce safe route recommendations.

Choices previously offered:
- **A.** Restart entire workflow, discarding successful results.
- **B.** Continue without routing and let LLM estimate route.
- **C.** Retain valid results, retry failed routing with bounded timeout, use deterministic routing fallback if possible, and refuse to approve affected assignments if feasibility remains unknown.
- **D.** Wait indefinitely.

**Expected best answer: C**, with attention to **partial failure**, bounded retries, timeouts, graceful degradation, fallback correctness, and circuit breakers if expanded. The user has not yet answered this challenge. On resumption, allow them to respond rather than assuming they've completed it.

## 10. First implementation sprint — user can begin here

**Goal:** A single simulated medical incident, a resource, and a road closure leading to a revised feasible route. No AI/API/dashboard yet.

Suggested first session (~60–90 min):
1. Create Python repository and virtual environment; install Pydantic, NetworkX, pytest.
2. Build domain models: `Incident(id, location, severity, required_capability)`, `Resource(id, location, capabilities, available)`, `Road(source, destination, travel_time, blocked)`.
3. Make small synthetic node/edge graph A–F; resource at A, incident at F.
4. Compute shortest route among eligible available resources.
5. Block C–F, calculate new route without blocked edges.
6. Add tests (valid route, blocked edge avoided, unavailable/mismatched resources rejected) and make first commit.

Example **illustrative, not computed** CLI behavior:

```text
=== CRISIS COMMAND SIMULATION ===
Incident: Medical emergency at F
Available resource: Ambulance-01 at A
Initial route: A → B → C → F
Travel time: 12 minutes
EVENT: Road C-F is blocked!
Recalculating route...
Updated route: A → B → E → F
Travel time: 17 minutes
SUCCESS: New route avoids blocked road.
```

First commit suggestion: `feat: implement simulated crisis environment and basic route replanning`.

Then Sprint 2: integrate Muse for structured incident parsing and delegated tool calls. Only after that progressively build orchestration, UI, and full replanning.

## 11. Proposed schedule / prioritization

Initial hackathon plan:

| Dates (2026) | Focus |
|---|---|
| Oct 26–27 | Domain models, simulation, Muse integration foundation |
| Oct 28–29 | Concurrent agents, routing, valid constrained allocations |
| Oct 30–31 | Command dashboard, realtime updates |
| Nov 1 | World-change and dependency-aware replanning |
| Nov 2 | Benchmarks / baselines |
| Nov 3–4 | Reliability tests, video, submission |

Schedule is aspirational; focus on end-to-end reliability. If selective replanning is not ready, **full-plan recomputation is the fallback**. If frontend is delayed, keep a working backend demo.

## 12. Status at end of conversation

- Product positioning, judging strategy, MVP, architecture and implementation order are **planned**, not confirmed implemented.
- User has answered Architecture Challenges #1–5 (correct answers B, B, A [reviewed as C], C, C).
- Challenge #6 (fault tolerance / agent timeout) is currently the next exercise.
- Next practical engineering action: create models, graph simulation, deterministic planner and tests, then integrate AI.
- Ongoing teaching style: solicit user design choice and reasoning, review like a senior engineer, explain counterexamples/tradeoffs, and only then implement.

