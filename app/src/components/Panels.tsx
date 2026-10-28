"use client";

import { useState } from "react";
import type { AgentReport, Incident, Road, WorldState } from "@/lib/types";

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

export function timeAgo(iso: string) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  return minutes < 1 ? "now" : `${minutes}m ago`;
}

export const isNew = (incident: Incident, tick: number) =>
  incident.status === "open" && incident.reported_tick >= tick - 1;

export function orderEmergencies(incidents: Incident[]) {
  const rank = { open: 0, assigned: 1, resolved: 2 };
  return [...incidents].sort(
    (a, b) =>
      rank[a.status] - rank[b.status] ||
      b.severity_level - a.severity_level ||
      b.reported_tick - a.reported_tick,
  );
}

export function KpiStrip({ world, pendingReviews }: { world: WorldState; pendingReviews: number }) {
  const active = world.incidents.filter((incident) => incident.status !== "resolved");
  const kpis = [
    { label: "Active emergencies", value: active.length },
    { label: "Critical", value: active.filter((incident) => incident.severity === "critical").length, alert: true },
    { label: "Unassigned", value: world.incidents.filter((incident) => incident.status === "open").length, alert: true },
    { label: "Units available", value: `${world.services.filter((service) => service.available).length}/${world.services.length}` },
    { label: "Roads closed", value: world.roads.filter((road) => road.is_closed).length },
    { label: "Agent items to review", value: pendingReviews, alert: true },
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

export function EmergencyFeed({ incidents, tick }: { incidents: Incident[]; tick: number }) {
  return (
    <ul className="list">
      {orderEmergencies(incidents).map((incident) => (
        <li key={incident.id} className={`row sev-${incident.severity}`}>
          <span className={`dot status-${incident.status}`} />
          <div className="grow">
            <div className="title">
              {incident.kind} {isNew(incident, tick) && <span className="badge-new">NEW</span>}
            </div>
            <div className="meta">
              {incident.id} · {incident.severity} · needs {incident.required_services.join(", ") || "any unit"}
              {incident.assigned_service_ids.length > 0 && ` · ${incident.assigned_service_ids.join(", ")}`}
            </div>
          </div>
          <span className={`pill status-${incident.status}`}>{incident.status}</span>
        </li>
      ))}
    </ul>
  );
}

export const REPORT_LABELS: Record<AgentReport["type"], string> = {
  finding: "Finding",
  recommendation: "Recommendation",
  warning: "Warning",
  needs_review: "Needs review",
};

export function AgentFeed({ reports, acknowledged, onAcknowledge }: {
  reports: AgentReport[];
  acknowledged: Set<string>;
  onAcknowledge: (id: string) => void;
}) {
  if (!reports.length) return <p className="muted">No agent reports yet.</p>;
  return (
    <ul className="list">
      {reports.map((report) => {
        const done = acknowledged.has(report.id);
        return (
          <li key={report.id} className={`report type-${report.type} ${done ? "done" : ""}`}>
            <div className="row">
              <span className="agent">{report.agent}</span>
              <span className={`tag type-${report.type}`}>{REPORT_LABELS[report.type]}</span>
              <span className="muted grow right">{timeAgo(report.at)}</span>
            </div>
            <div className="report-msg">{report.message}</div>
            <div className="row">
              <span className="meta grow">
                {report.related_ids.join(", ")}
                {report.confidence !== null && ` · confidence ${Math.round(report.confidence * 100)}%`}
              </span>
              {!done && (
                <button className="ghost small" onClick={() => onAcknowledge(report.id)}>
                  Acknowledge
                </button>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function RoadControl({ road, busy, disabled, onSubmit, onClose }: {
  road: Road;
  busy: boolean;
  disabled: boolean;
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
        <button className="ghost small" onClick={onClose} aria-label="Deselect road">
          ×
        </button>
      </div>
      <input
        value={reason}
        disabled={disabled}
        placeholder={nextClosed ? "Reason, e.g. bridge flooded" : "Reason for reopening"}
        onChange={(event) => setReason(event.target.value)}
      />
      <button
        className={nextClosed ? "danger" : ""}
        disabled={busy || disabled}
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
