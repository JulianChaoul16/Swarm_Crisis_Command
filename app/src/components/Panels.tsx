"use client";

import { useState } from "react";
import type {
  Incident,
  Proposal,
  Report,
  ReportFields,
  Service,
  WorldEvent,
  WorldState,
} from "@/lib/types";

export function Tile({ title, count, actions, className = "", children }: {
  title: string;
  count?: number;
  actions?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={`tile ${className}`}>
      <header className="tile-head">
        <h2>
          {title}
          {count !== undefined && <span className="count">{count}</span>}
        </h2>
        {actions}
      </header>
      <div className="tile-body">{children}</div>
    </section>
  );
}

export const label = (value: string) => value.replaceAll("_", " ");

export function time(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function orderIncidents(incidents: Incident[]) {
  const rank = { open: 0, assigned: 1, resolved: 2 };
  return [...incidents].sort(
    (a, b) => rank[a.status] - rank[b.status] || b.severity_level - a.severity_level || a.id.localeCompare(b.id),
  );
}

export function KpiStrip({ world }: { world: WorldState }) {
  const active = world.incidents.filter((incident) => incident.status !== "resolved");
  const openReports = world.reports.filter((report) => !["confirmed", "rejected"].includes(report.status));
  const kpis = [
    { label: "Active incidents", value: active.length },
    { label: "Unmet needs", value: world.current_plan?.unassigned_incident_ids.length ?? 0, alert: true },
    { label: "Resources available", value: `${world.services.filter((s) => s.available).length}/${world.services.length}` },
    { label: "Roads closed", value: world.roads.filter((road) => road.is_closed).length, alert: true },
    { label: "Reports to review", value: openReports.length, alert: true },
    { label: "Plan awaiting approval", value: world.current_plan?.status === "proposed" && world.current_plan.assignments.length ? "Yes" : "No" },
  ];
  return (
    <div className="kpis">
      {kpis.map((kpi) => (
        <div key={kpi.label} className={`kpi ${kpi.alert && kpi.value ? "alert" : ""}`}>
          <div className="kpi-value">{kpi.value}</div>
          <div className="kpi-label">{kpi.label}</div>
        </div>
      ))}
    </div>
  );
}

export function IncidentList({ incidents }: { incidents: Incident[] }) {
  return (
    <ul className="list">
      {orderIncidents(incidents).map((incident) => (
        <li key={incident.id} className={`row sev-${incident.severity}`}>
          <span className={`dot status-${incident.status}`} />
          <div className="grow">
            <div className="title">
              {incident.kind} <span className="muted">{incident.id}</span>
            </div>
            <div className="meta">
              {incident.severity} · needs {incident.required_services.join(", ") || "any unit"}
              {incident.people > 0 && ` · ${incident.people} people`}
              {incident.assigned_service_ids.length > 0 && ` · ${incident.assigned_service_ids.join(", ")}`}
            </div>
          </div>
          <span className={`pill status-${incident.status}`}>{incident.status}</span>
        </li>
      ))}
    </ul>
  );
}

export function ResourceList({ services }: { services: Service[] }) {
  return (
    <ul className="list">
      {services.map((service) => (
        <li key={service.id} className="row">
          <span className={`dot ${service.available ? "unit-idle" : "unit-busy"}`} />
          <div className="grow">
            <div className="title">{service.name}</div>
            <div className="meta">
              {service.capabilities.join(", ")}
              {service.capacity > 0 && ` · carries ${service.capacity}`} · grid ({service.location.x.toFixed(1)},{" "}
              {service.location.y.toFixed(1)})
              {service.assigned_incident_id && !service.route && " · no open route"}
            </div>
          </div>
          <span className="pill">{service.assigned_incident_id ? `→ ${service.assigned_incident_id}` : "available"}</span>
        </li>
      ))}
    </ul>
  );
}

// ---------- Intake ----------

type Options = { places: string[]; roads: string[]; capabilities: string[] };

const EMPTY_FIELDS: ReportFields = {
  report_type: null,
  kind: null,
  severity: null,
  required_services: null,
  location: null,
  road_id: null,
  road_closed: null,
  certainty: null,
  people: null,
};

function FieldsEditor({ value, onChange, options }: {
  value: ReportFields;
  onChange: (fields: ReportFields) => void;
  options: Options;
}) {
  const set = (patch: Partial<ReportFields>) => onChange({ ...value, ...patch });
  const pick = (raw: string) => raw || null;
  const services = value.required_services ?? [];
  return (
    <div className="fields">
      <label>
        Type
        <select value={value.report_type ?? ""} onChange={(e) => set({ report_type: pick(e.target.value) as ReportFields["report_type"] })}>
          <option value="">unknown</option>
          <option value="incident">incident</option>
          <option value="road_status">road status</option>
        </select>
      </label>
      {value.report_type === "incident" && (
        <>
          <label>
            Kind
            <input value={value.kind ?? ""} placeholder="e.g. trapped residents" onChange={(e) => set({ kind: pick(e.target.value) })} />
          </label>
          <label>
            Severity
            <select value={value.severity ?? ""} onChange={(e) => set({ severity: pick(e.target.value) as ReportFields["severity"] })}>
              <option value="">unknown</option>
              {["low", "medium", "high", "critical"].map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <label>
            Location
            <select value={value.location ?? ""} onChange={(e) => set({ location: pick(e.target.value) })}>
              <option value="">unknown</option>
              {options.places.map((place) => <option key={place}>{place}</option>)}
            </select>
          </label>
          <label>
            People to move
            <input
              type="number"
              min={0}
              value={value.people ?? ""}
              placeholder="unknown"
              onChange={(e) => set({ people: e.target.value === "" ? null : Number(e.target.value) })}
            />
          </label>
          <fieldset>
            <legend>Needs</legend>
            {options.capabilities.map((capability) => (
              <label key={capability} className="check">
                <input
                  type="checkbox"
                  checked={services.includes(capability)}
                  onChange={(e) =>
                    set({
                      required_services: e.target.checked
                        ? [...services, capability]
                        : services.filter((item) => item !== capability),
                    })
                  }
                />
                {capability}
              </label>
            ))}
          </fieldset>
        </>
      )}
      {value.report_type === "road_status" && (
        <>
          <label>
            Road
            <select value={value.road_id ?? ""} onChange={(e) => set({ road_id: pick(e.target.value) })}>
              <option value="">unknown</option>
              {options.roads.map((road) => <option key={road}>{road}</option>)}
            </select>
          </label>
          <label>
            Status
            <select
              value={value.road_closed === null ? "" : value.road_closed ? "closed" : "open"}
              onChange={(e) => set({ road_closed: e.target.value === "" ? null : e.target.value === "closed" })}
            >
              <option value="">unknown</option>
              <option value="closed">closed</option>
              <option value="open">open</option>
            </select>
          </label>
          <label>
            Source certainty
            <select value={value.certainty ?? ""} onChange={(e) => set({ certainty: pick(e.target.value) as ReportFields["certainty"] })}>
              <option value="">unknown</option>
              <option value="confirmed">confirmed</option>
              <option value="possible">possible</option>
            </select>
          </label>
        </>
      )}
    </div>
  );
}

export function IntakePanel({ world, busy, onSubmit, onClarify, onConfirm, onReject }: {
  world: WorldState;
  busy: boolean;
  onSubmit: (content: string, source: string, fields: ReportFields | undefined) => void;
  onClarify: (id: string, fields: ReportFields) => void;
  onConfirm: (id: string) => void;
  onReject: (id: string, reason: string) => void;
}) {
  const options: Options = {
    places: world.places,
    roads: world.roads.map((road) => road.id),
    capabilities: [...new Set(world.services.flatMap((service) => service.capabilities))].sort(),
  };
  const [content, setContent] = useState("");
  const [source, setSource] = useState("");
  const [manual, setManual] = useState(true);
  const [fields, setFields] = useState<ReportFields>(EMPTY_FIELDS);
  const open = world.reports.filter((report) => !["confirmed", "rejected"].includes(report.status));
  const closed = world.reports.filter((report) => ["confirmed", "rejected"].includes(report.status));

  return (
    <div className="intake">
      <form
        className="intake-form"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(content.trim(), source.trim(), manual ? fields : undefined);
          setContent("");
          setFields(EMPTY_FIELDS);
        }}
      >
        <textarea value={content} rows={2} placeholder="Report text, as received" onChange={(e) => setContent(e.target.value)} />
        <div className="row">
          <input value={source} placeholder="Source (e.g. 911 caller, patrol)" onChange={(e) => setSource(e.target.value)} />
          <label className="check nowrap">
            <input type="checkbox" checked={manual} onChange={(e) => setManual(e.target.checked)} />
            Enter fields
          </label>
        </div>
        {manual ? (
          <FieldsEditor value={fields} onChange={setFields} options={options} />
        ) : (
          <p className="meta">Fields will be requested from the model ({world.model}).</p>
        )}
        <button disabled={busy || !content.trim() || !source.trim()}>Submit report</button>
      </form>

      <ul className="list">
        {open.map((report) => (
          <ReportCard key={report.id} report={report} options={options} busy={busy} onClarify={onClarify} onConfirm={onConfirm} onReject={onReject} />
        ))}
      </ul>
      {closed.length > 0 && (
        <details>
          <summary>{closed.length} closed reports</summary>
          <ul className="list">
            {closed.map((report) => (
              <li key={report.id}>
                <div className="row">
                  <span className="title grow">{report.id} · {report.source}</span>
                  <span className={`tag report-${report.status}`}>{label(report.status)}</span>
                </div>
                <div className="meta">
                  “{report.content}”
                  {report.linked_incident_id && ` → ${report.linked_incident_id}`}
                  {report.linked_road_id && ` → ${report.linked_road_id}`}
                  {report.decision_reason && ` · ${report.decision_reason}`}
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function ReportCard({ report, options, busy, onClarify, onConfirm, onReject }: {
  report: Report;
  options: Options;
  busy: boolean;
  onClarify: (id: string, fields: ReportFields) => void;
  onConfirm: (id: string) => void;
  onReject: (id: string, reason: string) => void;
}) {
  const [fields, setFields] = useState<ReportFields>(report.fields);
  const [editing, setEditing] = useState(report.status !== "ready");
  return (
    <li className="report-card">
      <div className="row">
        <span className="title grow">{report.id} · {report.source}</span>
        <span className={`tag report-${report.status}`}>{label(report.status)}</span>
      </div>
      <div className="quote">“{report.content}”</div>
      <div className="meta">
        Received {time(report.received_at)} · event time {report.occurred_at ? time(report.occurred_at) : "unknown"} · fields by{" "}
        {report.extracted_by ?? "nobody yet"}
      </div>
      {report.extraction_error && <div className="note warn">Extraction: {report.extraction_error}</div>}
      {report.missing_fields.length > 0 && <div className="note warn">Missing: {report.missing_fields.map(label).join(", ")}</div>}
      {report.validation_errors.map((error) => <div key={error} className="note error">{error}</div>)}
      {report.warnings.map((warning) => <div key={warning} className="note warn">{warning}</div>)}

      {editing ? (
        <>
          <FieldsEditor value={fields} onChange={setFields} options={options} />
          <div className="row">
            <button className="small" disabled={busy} onClick={() => { onClarify(report.id, fields); setEditing(false); }}>
              Save fields
            </button>
            <button className="ghost small" disabled={busy} onClick={() => onReject(report.id, "Rejected during intake review.")}>
              Reject report
            </button>
          </div>
        </>
      ) : (
        <div className="row">
          <button className="small" disabled={busy || report.status !== "ready"} onClick={() => onConfirm(report.id)}>
            Confirm as authoritative
          </button>
          <button className="ghost small" onClick={() => setEditing(true)}>Edit</button>
          <button className="ghost small" disabled={busy} onClick={() => onReject(report.id, "Rejected during intake review.")}>
            Reject
          </button>
        </div>
      )}
    </li>
  );
}

// ---------- Review ----------

const CHANGE_LABEL: Record<Proposal["changes"][number]["change"], string> = {
  invalidated: "Invalidated",
  reassigned: "Changed",
  replacement: "Replacement",
  dropped: "Dropped",
  new: "New",
  rerouted: "Rerouted",
  unchanged: "Kept",
};

export function ReviewPanel({ plan, worldVersion, priorityRule, busy, onApprove, onReject, onRecompute }: {
  plan: Proposal | null;
  worldVersion: number;
  priorityRule: string;
  busy: boolean;
  onApprove: (plan: Proposal) => void;
  onReject: (plan: Proposal, reason: string) => void;
  onRecompute: () => void;
}) {
  const [reason, setReason] = useState("");
  if (!plan) {
    return (
      <div>
        <p className="muted">No current proposal.</p>
        <button className="small" disabled={busy} onClick={onRecompute}>Compute proposal</button>
      </div>
    );
  }
  const stale = plan.world_version !== worldVersion;
  const approvable = plan.status === "proposed" && plan.valid && !stale && plan.assignments.length > 0;
  const failed = plan.checks.filter((check) => !check.passed);

  return (
    <div className="review">
      <div className="row">
        <strong className="grow">{plan.id}</strong>
        <span className={`tag plan-${plan.status}`}>{plan.status}</span>
      </div>
      <div className="meta">
        Planned against world v{plan.world_version}
        {stale ? <span className="error-text"> · world is now v{worldVersion} (stale)</span> : " (current)"} ·{" "}
        {plan.valid ? "all hard constraints pass" : `${failed.length} constraint checks failed`}
      </div>
      <div className="meta">Priority rule (simulator setting): {priorityRule}</div>
      {plan.invalidated.length > 0 && (
        <div className="unmet">
          <strong>Missions invalidated by the latest change:</strong>
          <ul className="rationale">
            {plan.invalidated.map((mission) => (
              <li key={mission.incident_id}>
                {mission.incident_id} ({mission.service_ids.join(", ")}): {mission.cause}
              </li>
            ))}
          </ul>
        </div>
      )}

      <h3 className="sub">Specialists</h3>
      <div className="agents">
        {plan.agents.map((agent) => (
          <details key={agent.agent} className={`agent-chip agent-${agent.status}`}>
            <summary>
              {agent.agent}: {label(agent.status)}
            </summary>
            {agent.summary && <p>{agent.summary}</p>}
            {agent.findings.map((finding) => <p key={finding}>• {finding}</p>)}
            {agent.error && <p className="meta">{agent.error}</p>}
            <p className="meta">Tool results (deterministic): {agent.tools.map((tool) => tool.name).join(", ")}</p>
          </details>
        ))}
      </div>

      <h3 className="sub">Dispatches <span className="count">{plan.assignments.length}</span></h3>
      {plan.assignments.length === 0 && <p className="muted">No new dispatches proposed.</p>}
      <ul className="list">
        {plan.changes.map((change) => {
          const dispatch = change.incident_id
            ? plan.assignments.find((item) => item.service_id === change.service_id)
            : undefined;
          return (
            <li key={`${change.service_id}-${change.change}`} className={`change change-${change.change}`}>
              <div className="row">
                <span className={`tag change-${change.change}`}>{CHANGE_LABEL[change.change]}</span>
                <span className="grow">
                  <strong>{change.service_id}</strong> → {change.incident_id ?? <s>{change.previous_incident_id}</s>}
                  {change.change === "reassigned" && <span className="muted"> (was {change.previous_incident_id})</span>}
                </span>
                {dispatch && <span className="muted">ETA {dispatch.estimated_arrival.toFixed(1)}</span>}
              </div>
              {change.explanation && <div className="meta">{change.explanation}</div>}
            </li>
          );
        })}
      </ul>

      {plan.unassigned_incident_ids.length > 0 && (
        <div className="unmet">
          <strong>Unmet needs:</strong> {plan.unassigned_incident_ids.join(", ")}
          <ul className="rationale">
            {plan.rationale.filter((line) => line.includes("left unassigned")).map((line) => <li key={line}>{line}</li>)}
          </ul>
        </div>
      )}

      <details>
        <summary>Constraint checks ({plan.checks.length - failed.length}/{plan.checks.length} pass)</summary>
        <ul className="checks">
          {plan.checks.map((check) => (
            <li key={check.rule} className={check.passed ? "pass" : "fail"}>
              {check.passed ? "✓" : "✕"} {label(check.rule)} <span className="meta">{check.passed ? "" : check.detail}</span>
            </li>
          ))}
        </ul>
      </details>
      <details>
        <summary>Planner rationale</summary>
        <ul className="rationale">
          {plan.rationale.map((line) => <li key={line}>{line}</li>)}
        </ul>
      </details>

      {plan.status === "proposed" || plan.status === "invalid" ? (
        <div className="decide">
          <button disabled={busy || !approvable} onClick={() => onApprove(plan)}>
            Approve {plan.id} (v{plan.world_version})
          </button>
          <input value={reason} placeholder="Reason to reject" onChange={(e) => setReason(e.target.value)} />
          <button className="ghost" disabled={busy || !reason.trim()} onClick={() => { onReject(plan, reason.trim()); setReason(""); }}>
            Reject
          </button>
        </div>
      ) : (
        <div className="decide">
          <span className="meta grow">
            {plan.decided_by ? `${plan.status} by ${plan.decided_by}` : plan.status}
            {plan.decision_reason && ` · ${plan.decision_reason}`}
          </span>
          <button className="ghost small" disabled={busy} onClick={onRecompute}>Recompute</button>
        </div>
      )}
    </div>
  );
}

// ---------- Timeline ----------

export function Timeline({ events }: { events: WorldEvent[] }) {
  return (
    <ol className="events">
      {events.map((event) => (
        <li key={event.sequence} className={`event-${event.kind}`}>
          <div className="row">
            <span className="kind">{label(event.kind)}</span>
            <span className="muted">
              {event.actor} · t{event.tick} · v{event.version} · {time(event.recorded_at)}
            </span>
          </div>
          <div className="meta">
            {event.entity_ids.length > 0 && <strong>{event.entity_ids.join(", ")} </strong>}
            {event.reason}
          </div>
        </li>
      ))}
    </ol>
  );
}
