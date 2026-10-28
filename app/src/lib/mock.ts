// Mock data for reviewing the dashboard layout without the Python API, and for the
// specialist agent feed, which the backend does not produce yet.
import type { AgentReport, Incident, LngLat, Road, Service, WorldEvent, WorldState } from "./types";

// Same projection as crisis_command/api/geo.py.
const p = (x: number, y: number): LngLat => [
  +(-77.456 + x * 0.005).toFixed(6),
  +(37.53 + y * 0.004).toFixed(6),
];
const line = (...points: [number, number][]) => points.map(([x, y]) => p(x, y));

const road = (id: string, a: [number, number], b: [number, number], isClosed = false): Road => ({
  id,
  is_closed: isClosed,
  length: Math.hypot(b[0] - a[0], b[1] - a[1]),
  path: line(a, b),
});

const roads: Road[] = [
  road("fire-road-1", [1, 1], [4, 1]),
  road("fire-road-2", [4, 1], [8, 5]),
  road("medical-road-1", [1, 2], [4, 2]),
  road("connector-1", [4, 2], [4, 1]),
  road("connector-2", [4, 2], [4, 4]),
  road("police-road-1", [0, 4], [2, 4]),
  road("collision-road", [2, 4], [4, 4]),
  road("river-bridge", [4, 1], [7, 2], true),
  road("east-avenue", [7, 2], [8, 5]),
  road("south-loop", [4, 1], [6, 0]),
  road("shelter-road", [7, 2], [9, 3]),
  road("station-2-road", [6, 0], [7, 2]),
];

const incident = (
  id: string,
  kind: string,
  [x, y]: [number, number],
  severity: Incident["severity"],
  status: Incident["status"],
  reportedTick: number,
  requiredServices: string[],
  assigned: string[] = [],
): Incident => ({
  id,
  kind,
  severity,
  severity_level: { low: 1, medium: 2, high: 3, critical: 4 }[severity],
  status,
  reported_tick: reportedTick,
  required_services: requiredServices,
  location: { x, y },
  position: p(x, y),
  assigned_service_ids: assigned,
});

const incidents: Incident[] = [
  incident("incident-1", "building fire", [8, 5], "critical", "assigned", 0, ["fire", "medical"], ["unit-fire-1", "unit-medical-1"]),
  incident("incident-2", "road collision", [2, 4], "high", "assigned", 1, ["medical", "police"], ["unit-medical-2", "unit-police-1"]),
  incident("incident-3", "flooded underpass", [6, 0], "high", "open", 6, ["rescue"]),
  incident("incident-4", "trapped residents", [9, 3], "critical", "open", 6, ["rescue", "medical"]),
  incident("incident-5", "downed power line", [7, 2], "medium", "open", 5, ["utility", "police"]),
  incident("incident-6", "gas odor report", [4, 4], "low", "resolved", 2, ["fire"]),
];

const service = (
  id: string,
  name: string,
  [x, y]: [number, number],
  capabilities: string[],
  assignedIncidentId: string | null = null,
  route: LngLat[] | null = null,
): Service => ({
  id,
  name,
  capabilities,
  speed: 2,
  available: assignedIncidentId === null,
  assigned_incident_id: assignedIncidentId,
  location: { x, y },
  position: p(x, y),
  route,
});

const services: Service[] = [
  service("unit-fire-1", "Fire engine 1", [5.5, 2.5], ["fire"], "incident-1", line([5.5, 2.5], [8, 5])),
  service("unit-fire-2", "Fire engine 2", [1, 1], ["fire"]),
  service("unit-medical-1", "Ambulance 1", [4, 1.5], ["medical"], "incident-1", line([4, 1.5], [4, 1], [8, 5])),
  service("unit-medical-2", "Ambulance 2", [3, 4], ["medical"], "incident-2", line([3, 4], [2, 4])),
  service("unit-police-1", "Police unit 1", [1, 4], ["police"], "incident-2", line([1, 4], [2, 4])),
  service("unit-police-2", "Police unit 2", [0, 4], ["police"]),
  service("unit-rescue-1", "Swift water rescue 1", [1, 2], ["rescue"]),
  service("unit-utility-1", "Utility crew 1", [6, 0], ["utility"]),
];

export const mockWorld: WorldState = {
  city: "Mock City",
  version: 14,
  tick: 6,
  all_resolved: false,
  incidents,
  services,
  roads,
  last_plan: {
    world_version: 13,
    assignments: [],
    unassigned_incident_ids: ["incident-3", "incident-4", "incident-5"],
    rationale: [
      "Incident incident-4 prioritized at critical severity.",
      "Incident incident-3 prioritized at high severity.",
      "Incident incident-4 left unassigned; no reachable available services cover medical.",
    ],
    unknown_road_ids: [],
    claims_requiring_review: ["river-bridge.is_closed"],
  },
  notice: null,
};

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

export const mockAgentReports: AgentReport[] = [
  { id: "r9", agent: "Situation", type: "warning", message: "Caller reports 3 residents on a second floor with water rising at 9th & Shelter Rd.", related_ids: ["incident-4"], confidence: 0.82, at: minutesAgo(1) },
  { id: "r8", agent: "Routing", type: "warning", message: "River bridge closed by road authority; 2 planned routes depended on it and were recalculated (+4.1 min average).", related_ids: ["river-bridge", "unit-fire-1"], confidence: 0.97, at: minutesAgo(2) },
  { id: "r7", agent: "Logistics", type: "recommendation", message: "Send Swift water rescue 1 to incident-4 before incident-3; only one rescue-capable unit is available.", related_ids: ["incident-4", "incident-3", "unit-rescue-1"], confidence: 0.74, at: minutesAgo(3) },
  { id: "r6", agent: "State", type: "needs_review", message: "Conflicting reports on river-bridge: verified closure (road authority) vs. unverified 'passable' (social media). Treated as closed.", related_ids: ["river-bridge"], confidence: null, at: minutesAgo(4) },
  { id: "r5", agent: "Situation", type: "finding", message: "Downed power line reported near the east avenue junction; no injuries reported.", related_ids: ["incident-5"], confidence: 0.68, at: minutesAgo(6) },
  { id: "r4", agent: "Logistics", type: "warning", message: "No medical units available for incident-4 until Ambulance 2 clears incident-2 (est. 6 min).", related_ids: ["incident-4", "unit-medical-2"], confidence: 0.88, at: minutesAgo(7) },
  { id: "r3", agent: "Routing", type: "finding", message: "Underpass at South Loop flooded; route is still passable for high-clearance vehicles only.", related_ids: ["incident-3", "south-loop"], confidence: 0.61, at: minutesAgo(11) },
  { id: "r2", agent: "Planner", type: "recommendation", message: "Dispatched Ambulance 2 and Police unit 1 to the road collision (ETA 1.5).", related_ids: ["incident-2"], confidence: null, at: minutesAgo(14) },
  { id: "r1", agent: "Situation", type: "finding", message: "Gas odor at 4th & Main confirmed as false alarm by Fire engine 2.", related_ids: ["incident-6"], confidence: 0.93, at: minutesAgo(20) },
];

export const mockEvents: WorldEvent[] = [
  { sequence: 9, tick: 6, version: 14, kind: "incident_added", entity_ids: ["incident-4"], reason: "Trapped residents reported by 911 caller.", details: {} },
  { sequence: 8, tick: 6, version: 13, kind: "incident_added", entity_ids: ["incident-3"], reason: "Flooded underpass reported by patrol.", details: {} },
  { sequence: 7, tick: 5, version: 12, kind: "road_closed", entity_ids: ["river-bridge"], reason: "Bridge flooded.", details: {} },
  { sequence: 6, tick: 5, version: 11, kind: "incident_added", entity_ids: ["incident-5"], reason: "Downed power line reported.", details: {} },
  { sequence: 5, tick: 4, version: 10, kind: "service_moved", entity_ids: ["unit-fire-1", "incident-1"], reason: "Simulation advanced the service along its current route.", details: {} },
  { sequence: 4, tick: 2, version: 6, kind: "incident_resolved", entity_ids: ["incident-6"], reason: "All assigned services arrived at the incident.", details: {} },
  { sequence: 3, tick: 1, version: 4, kind: "service_dispatched", entity_ids: ["unit-medical-2", "incident-2"], reason: "Planner selected an available service that meets incident requirements.", details: {} },
  { sequence: 2, tick: 0, version: 2, kind: "plan_decision", entity_ids: ["incident-1"], reason: "Planner decision accepted for execution.", details: {} },
  { sequence: 1, tick: 0, version: 1, kind: "world_initialized", entity_ids: [], reason: "Initial world state loaded.", details: {} },
];

// With the live API, surface the real planner output as agent reports.
export function reportsFromWorld(world: WorldState): AgentReport[] {
  const now = new Date().toISOString();
  const plan = world.last_plan;
  const reports: AgentReport[] = [];
  if (world.notice) {
    reports.push({ id: "notice", agent: "Planner", type: "warning", message: world.notice, related_ids: [], confidence: null, at: now });
  }
  for (const review of plan?.claims_requiring_review ?? []) {
    reports.push({ id: `review-${review}`, agent: "State", type: "needs_review", message: `Conflicting evidence for ${review}; treated as unknown.`, related_ids: [review.split(".")[0]], confidence: null, at: now });
  }
  for (const dispatch of plan?.assignments ?? []) {
    reports.push({ id: `dispatch-${dispatch.service_id}`, agent: "Planner", type: "recommendation", message: `Dispatch ${dispatch.service_id} to ${dispatch.incident_id} (ETA ${dispatch.estimated_arrival.toFixed(1)}).`, related_ids: [dispatch.service_id, dispatch.incident_id], confidence: null, at: now });
  }
  for (const id of plan?.unassigned_incident_ids ?? []) {
    reports.push({ id: `unassigned-${id}`, agent: "Planner", type: "warning", message: `${id} could not be assigned with current units and roads.`, related_ids: [id], confidence: null, at: now });
  }
  return reports;
}
