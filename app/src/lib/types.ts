// Mirrors crisis_command/api/serializers.py.

export type LngLat = [number, number];

export type Incident = {
  id: string;
  kind: string;
  severity: "low" | "medium" | "high" | "critical";
  severity_level: number;
  status: "open" | "assigned" | "resolved";
  reported_tick: number;
  required_services: string[];
  location: { x: number; y: number };
  position: LngLat;
  assigned_service_ids: string[];
};

export type Service = {
  id: string;
  name: string;
  capabilities: string[];
  speed: number;
  available: boolean;
  assigned_incident_id: string | null;
  location: { x: number; y: number };
  position: LngLat;
  route: LngLat[] | null;
};

export type Road = {
  id: string;
  is_closed: boolean;
  length: number;
  path: LngLat[];
};

export type Plan = {
  world_version: number | null;
  assignments: {
    incident_id: string;
    service_id: string;
    estimated_arrival: number;
    route: LngLat[];
  }[];
  unassigned_incident_ids: string[];
  rationale: string[];
  unknown_road_ids: string[];
  claims_requiring_review: string[];
};

export type WorldState = {
  city: string | null;
  version: number;
  tick: number;
  all_resolved: boolean;
  incidents: Incident[];
  services: Service[];
  roads: Road[];
  last_plan: Plan | null;
  notice: string | null;
};

// Not served by the API yet: specialist agents are mocked in lib/mock.ts.
export type AgentReport = {
  id: string;
  agent: "Situation" | "Logistics" | "Routing" | "State" | "Planner";
  type: "finding" | "recommendation" | "warning" | "needs_review";
  message: string;
  related_ids: string[];
  confidence: number | null;
  at: string;
};

export type WorldEvent = {
  sequence: number;
  tick: number;
  version: number;
  kind: string;
  entity_ids: string[];
  reason: string;
  details: Record<string, string>;
};
