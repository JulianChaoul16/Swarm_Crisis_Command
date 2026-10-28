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

export type Check = { rule: string; passed: boolean; detail: string };

export type Change = {
  service_id: string;
  incident_id: string | null;
  change: "new" | "unchanged" | "reassigned" | "dropped";
  previous_incident_id: string | null;
  explanation: string;
};

export type AgentRun = {
  agent: string;
  status: "completed" | "failed" | "timed_out" | "unavailable";
  summary: string | null;
  findings: string[];
  missing_information: string[];
  error: string | null;
  model_id: string | null;
  latency_ms: number;
  tools: { name: string; output: Record<string, unknown> }[];
};

export type Proposal = {
  id: string;
  run_id: string;
  world_version: number;
  status: "proposed" | "approved" | "rejected" | "stale" | "superseded" | "invalid";
  valid: boolean;
  created_at: string;
  previous_plan_id: string | null;
  decided_at: string | null;
  decided_by: string | null;
  decision_reason: string | null;
  assignments: { incident_id: string; service_id: string; estimated_arrival: number; route: LngLat[] }[];
  unassigned_incident_ids: string[];
  rationale: string[];
  checks: Check[];
  changes: Change[];
  agents: AgentRun[];
};

export type ReportFields = {
  report_type: "incident" | "road_status" | null;
  kind: string | null;
  severity: Incident["severity"] | null;
  required_services: string[] | null;
  location: string | null;
  road_id: string | null;
  road_closed: boolean | null;
  certainty: "confirmed" | "possible" | null;
};

export type Report = {
  id: string;
  content: string;
  source: string;
  received_at: string;
  occurred_at: string | null;
  fields: ReportFields;
  extracted_by: string | null;
  extraction_error: string | null;
  missing_fields: string[];
  validation_errors: string[];
  warnings: string[];
  status: "needs_extraction" | "needs_clarification" | "ready" | "confirmed" | "rejected";
  linked_incident_id: string | null;
  linked_road_id: string | null;
  decision_reason: string | null;
};

export type WorldState = {
  simulation: true;
  run_id: string;
  scenario: string;
  model: string;
  city: string | null;
  version: number;
  tick: number;
  all_resolved: boolean;
  incidents: Incident[];
  services: Service[];
  roads: Road[];
  places: string[];
  current_plan: Proposal | null;
  reports: Report[];
};

export type WorldEvent = {
  sequence: number;
  tick: number;
  version: number;
  kind: string;
  actor: string;
  recorded_at: string;
  entity_ids: string[];
  reason: string;
  details: Record<string, string>;
};
