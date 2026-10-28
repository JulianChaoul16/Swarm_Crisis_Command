"use client";

import { useState } from "react";
import type { Incident, Plan, Road, Service, WorldEvent } from "@/lib/types";

export function Section({ title, count, children }: {
  title: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <section className="section">
      <h2>
        {title}
        {count !== undefined && <span className="count">{count}</span>}
      </h2>
      {children}
    </section>
  );
}

export function IncidentList({ incidents }: { incidents: Incident[] }) {
  const ordered = [...incidents].sort(
    (a, b) =>
      Number(a.status === "resolved") - Number(b.status === "resolved") ||
      b.severity_level - a.severity_level,
  );
  return (
    <ul className="list">
      {ordered.map((incident) => (
        <li key={incident.id} className="row">
          <span className={`dot status-${incident.status}`} />
          <div className="grow">
            <div className="title">
              {incident.kind} <span className="muted">{incident.id}</span>
            </div>
            <div className="meta">
              {incident.severity} · needs {incident.required_services.join(", ") || "any unit"}
              {incident.assigned_service_ids.length > 0 &&
                ` · ${incident.assigned_service_ids.join(", ")}`}
            </div>
          </div>
          <span className={`pill status-${incident.status}`}>{incident.status}</span>
        </li>
      ))}
    </ul>
  );
}

export function UnitList({ services }: { services: Service[] }) {
  return (
    <ul className="list">
      {services.map((service) => (
        <li key={service.id} className="row">
          <span className={`dot ${service.available ? "unit-idle" : "unit-busy"}`} />
          <div className="grow">
            <div className="title">{service.name}</div>
            <div className="meta">
              {service.capabilities.join(", ")} · ({service.location.x.toFixed(1)},{" "}
              {service.location.y.toFixed(1)})
              {service.assigned_incident_id && !service.route && " · no open route"}
            </div>
          </div>
          <span className="pill">
            {service.assigned_incident_id ? `→ ${service.assigned_incident_id}` : "available"}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function RoadControl({ road, busy, onSubmit, onClose }: {
  road: Road;
  busy: boolean;
  onSubmit: (isClosed: boolean, reason: string) => void;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const nextClosed = !road.is_closed;
  const fallback = nextClosed ? "Closed by operations manager." : "Reopened by operations manager.";
  return (
    <div className="road-card">
      <div className="row">
        <div className="grow">
          <div className="title">{road.id}</div>
          <div className="meta">
            {road.is_closed ? "Closed" : "Open"} · length {road.length.toFixed(2)}
          </div>
        </div>
        <button className="ghost" onClick={onClose} aria-label="Deselect road">
          ×
        </button>
      </div>
      <input
        value={reason}
        placeholder={nextClosed ? "Reason, e.g. bridge flooded" : "Reason for reopening"}
        onChange={(event) => setReason(event.target.value)}
      />
      <button
        className={nextClosed ? "danger" : ""}
        disabled={busy}
        onClick={() => {
          onSubmit(nextClosed, reason.trim() || fallback);
          setReason("");
        }}
      >
        {nextClosed ? "Close road" : "Reopen road"}
      </button>
    </div>
  );
}

const EVIDENCE_PREFIX = "Used city_network evidence";

export function PlanSummary({ plan }: { plan: Plan | null }) {
  if (!plan) return <p className="muted">No plan yet. Advance the simulation to plan.</p>;
  const evidence = plan.rationale.filter((line) => line.startsWith(EVIDENCE_PREFIX));
  const decisions = plan.rationale.filter((line) => !line.startsWith(EVIDENCE_PREFIX));
  return (
    <div className="plan">
      <div className="meta">Planned against world v{plan.world_version}</div>
      {plan.assignments.length > 0 && (
        <ul className="list compact">
          {plan.assignments.map((dispatch) => (
            <li key={dispatch.service_id}>
              <strong>{dispatch.service_id}</strong> → {dispatch.incident_id}{" "}
              <span className="muted">ETA {dispatch.estimated_arrival.toFixed(1)}</span>
            </li>
          ))}
        </ul>
      )}
      {plan.unassigned_incident_ids.length > 0 && (
        <p className="warn">Unassigned: {plan.unassigned_incident_ids.join(", ")}</p>
      )}
      <ul className="rationale">
        {decisions.map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>
      {evidence.length > 0 && (
        <details>
          <summary>{evidence.length} road evidence claims</summary>
          <ul className="rationale">
            {evidence.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function EventLog({ events }: { events: WorldEvent[] }) {
  return (
    <ol className="events">
      {events.map((event) => (
        <li key={event.sequence}>
          <div className="row">
            <span className="kind">{event.kind.replaceAll("_", " ")}</span>
            <span className="muted">
              t{event.tick} · v{event.version}
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
