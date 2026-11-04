// Mirrors crisis_command/api/serializers.py.

export type LngLat = [number, number];

export type Evidence = { report_id: string | null; source: string; at: string | null };

export type Incident = {
  id: string;
  evidence?: Evidence[];
  place: string | null;
  kind: string;
  severity: "low" | "medium" | "high" | "critical";
  severity_level: number;
  status: "open" | "assigned" | "resolved";
  reported_tick: number;
  people: number;
  required_services: string[];
  location: { x: number; y: number };
  position: LngLat;
  assigned_service_ids: string[];
};

export type Service = {
  id: string;
  name: string;
  place: string | null;
  capabilities: string[];
  speed: number;
  capacity: number;
  available: boolean;
  assigned_incident_id: string | null;
  location: { x: number; y: number };
  position: LngLat;
  route: LngLat[] | null;
};

export type Road = {
  id: string;
  name?: string;
  evidence?: Evidence | null;
  is_closed: boolean;
  length: number;
  path: LngLat[];
};

export type Check = { rule: string; passed: boolean; detail: string };

export type Change = {
  service_id: string;
  incident_id: string | null;
  change: "invalidated" | "reassigned" | "replacement" | "dropped" | "new" | "rerouted" | "unchanged";
  previous_incident_id: string | null;
  explanation: string;
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
  assignments: { incident_id: string; service_id: string; estimated_arrival: number; route: LngLat[]; via: string[] }[];
  unassigned_incident_ids: string[];
  rationale: string[];
  checks: Check[];
  changes: Change[];
  invalidated: { incident_id: string; service_ids: string[]; blocked_service_ids: string[]; cause: string }[];
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
  people: number | null;
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

export type Zone = {
  id: string;
  type: "flood" | "fire";
  place: string | null;
  percent?: number;
  level: string;
  source: string;
  projected: boolean;
  polygon: LngLat[];
  incident_id?: string;
};

export type GisFrame = {
  at: string;
  run_id: string;
  zones: Zone[];
  outages: { incident_id: string; kind: string; place: string | null; position: LngLat; source: string }[];
  facilities: {
    name: string;
    type: "hospital" | "shelter";
    position: LngLat;
    beds_available?: number | null;
    beds_source?: string;
    capacity?: number | null;
    capacity_source?: string;
  }[];
  incidents: {
    id: string;
    kind: string;
    place: string | null;
    position: LngLat;
    status: string;
    severity: string;
    severity_level: number;
    people: number | null;
  }[];
  units: {
    id: string;
    name: string;
    kind: string;
    position: LngLat;
    available: boolean;
    assigned_incident_id: string | null;
    position_source: string;
  }[];
  closed_roads: { id: string; name: string; path: LngLat[] }[];
  gauge: { value?: number; trend?: string; label?: string; scenario_hour?: number } | null;
  affected: { people_reported: number; incidents: number; incidents_without_count: number };
  layer_sources: Record<string, string>;
};

export type Projection = {
  available: boolean;
  reason?: string;
  note: string;
  rate_ft_per_h?: number;
  basis?: string;
  trigger_ft?: number;
  frames: { hours: number; gauge_ft: number; above_trigger: boolean; zones: Zone[] }[];
};

export type Roster = {
  units: {
    id: string;
    name: string;
    kind: string;
    capabilities: string[];
    capacity: number | null;
    status: string;
    assignment: { incident_id: string; kind: string; place: string | null } | null;
    location: string | null;
    position: LngLat;
    position_source: string;
    messages: { at: string; plan_id: string; from: string; text: string }[];
  }[];
  broadcasts: { plan_id: string; english: string; spanish: string; source: string }[];
  note: string;
};

export type DemoStatus = {
  running: boolean;
  paused: boolean;
  finished: boolean;
  elapsed_s: number | null;
  events: { at: number; label: string; done: boolean }[];
  guide: { now: string; your_turn: boolean; next?: string | null; next_in_s?: number | null } | null;
  errors: string[];
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
  priority_rule: string;
  annotations: { label: string; path: LngLat[] }[];
  disruptions: { key: string; label: string; in_effect: boolean }[];
  current_plan: Proposal | null;
  reports: Report[];
  swarm: Swarm;
  models: { model: string; status: string; detail: string }[];
  agents: Agent[];
  demo: DemoStatus | null;
  gis: GisFrame & { projection: Projection };
  roster: Roster;
  mode: { demo_enabled: boolean; connectors: { name: string; received: number; error: string | null }[] };
  explanation: (ModelResult<{ why: string[]; tradeoff: string }> & { unverified: string[] }) | null;
  alerts: Record<string, {
    text: ModelResult<{ english: string; spanish: string }> | null;
    graphic: ModelResult<unknown> | null;
    unverified: string[];
  }>;
};

export type Agent = {
  name: string;
  role: string;
  models: { model: string; status: string }[];
  actions: number;
  last: { at: string; text: string } | null;
  activity: { at: string; source: string; text: string; incident_id: string | null }[];
  votes: { incident_id: string; kind: string; score: number; reasons: string[]; source: string }[];
};

export type ModelResult<T> = { value: T; model: string; source: string; note: string; label: string };

export type SwarmVote = { score: number; reasons: string[]; source: string };

export type Swarm = {
  incidents: Record<string, { votes: Record<string, SwarmVote>; final: number | null; agreement: number | null }>;
  flood_cover: Record<string, { percent: number; note?: string }>;
  gauge: { value?: number; trend?: string; site?: string; label?: string };
  imagery: {
    image_id: string;
    place: string;
    road_id: string;
    image_url: string;
    size: [number, number];
    objects: Record<string, [number, number, number, number][]>;
    boxes_source: string;
    map_features: GeoJSON.Feature[];
    estimate: {
      water_on_road: boolean;
      depth_in_low: number;
      depth_in_high: number;
      passable_for_cars: boolean;
      fire_visible: boolean;
      confidence: number;
      reason: string;
    };
    estimate_source: string;
    note: string;
  }[];
  transcripts: { audio_id: string; text: string; source: string; note: string }[];
  activity: { at: string; agent: string; source: string; text: string; incident_id: string | null }[];
  consensus_rule: string;
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
