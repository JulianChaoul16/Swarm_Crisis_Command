"use client";

import { useState } from "react";
import { label, orderIncidents, time } from "./Panels";
import type { WorldEvent, WorldState } from "@/lib/types";

const TABS = ["Incidents", "Resources", "Reports", "Plan", "Roads", "Timeline"] as const;
type Tab = (typeof TABS)[number];

export default function TableView({ world, events }: { world: WorldState; events: WorldEvent[] }) {
  const [tab, setTab] = useState<Tab>("Incidents");
  const plan = world.current_plan;
  const counts: Record<Tab, number> = {
    Incidents: world.incidents.length,
    Resources: world.services.length,
    Reports: world.reports.length,
    Plan: plan?.assignments.length ?? 0,
    Roads: world.roads.length,
    Timeline: events.length,
  };

  return (
    <div className="table-view">
      <nav className="tabs">
        {TABS.map((name) => (
          <button key={name} className={name === tab ? "tab active" : "tab"} onClick={() => setTab(name)}>
            {name} <span className="count">{counts[name]}</span>
          </button>
        ))}
      </nav>
      <div className="table-wrap">
        {tab === "Incidents" && (
          <table>
            <thead>
              <tr><th>Severity</th><th>Incident</th><th>ID</th><th>Status</th><th>Needs</th><th>Assigned</th><th>Reported</th><th>Grid</th></tr>
            </thead>
            <tbody>
              {orderIncidents(world.incidents).map((incident) => (
                <tr key={incident.id} className={`sev-${incident.severity}`}>
                  <td><span className={`sev sev-${incident.severity}`}>{incident.severity}</span></td>
                  <td>{incident.kind}</td>
                  <td className="mono">{incident.id}</td>
                  <td><span className={`pill status-${incident.status}`}>{incident.status}</span></td>
                  <td>{incident.required_services.join(", ") || "any"}</td>
                  <td>{incident.assigned_service_ids.join(", ") || "—"}</td>
                  <td>tick {incident.reported_tick}</td>
                  <td className="mono">{incident.location.x}, {incident.location.y}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "Resources" && (
          <table>
            <thead>
              <tr><th>Resource</th><th>ID</th><th>Capabilities</th><th>Status</th><th>Mission</th><th>Grid</th></tr>
            </thead>
            <tbody>
              {world.services.map((service) => (
                <tr key={service.id}>
                  <td>{service.name}</td>
                  <td className="mono">{service.id}</td>
                  <td>{service.capabilities.join(", ")}</td>
                  <td>{service.available ? "Available" : service.route ? "En route" : "No open route"}</td>
                  <td>{service.assigned_incident_id ?? "—"}</td>
                  <td className="mono">{service.location.x.toFixed(1)}, {service.location.y.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "Reports" && (
          <table>
            <thead>
              <tr><th>ID</th><th>Received</th><th>Source</th><th>Report</th><th>Status</th><th>Missing / errors</th><th>Fields by</th><th>Linked</th></tr>
            </thead>
            <tbody>
              {world.reports.map((report) => (
                <tr key={report.id}>
                  <td className="mono">{report.id}</td>
                  <td>{time(report.received_at)}</td>
                  <td>{report.source}</td>
                  <td className="wrap">{report.content}</td>
                  <td><span className={`tag report-${report.status}`}>{label(report.status)}</span></td>
                  <td className="wrap">{[...report.missing_fields.map(label), ...report.validation_errors].join("; ") || "—"}</td>
                  <td>{report.extracted_by ?? "—"}</td>
                  <td className="mono">{report.linked_incident_id ?? report.linked_road_id ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "Plan" && (
          plan ? (
            <table>
              <thead>
                <tr><th>Change</th><th>Resource</th><th>Incident</th><th>Previous</th><th>ETA</th><th>Explanation</th></tr>
              </thead>
              <tbody>
                {plan.changes.map((change) => (
                  <tr key={`${change.service_id}-${change.change}`}>
                    <td><span className={`tag change-${change.change}`}>{change.change}</span></td>
                    <td className="mono">{change.service_id}</td>
                    <td className="mono">{change.incident_id ?? "—"}</td>
                    <td className="mono">{change.previous_incident_id ?? "—"}</td>
                    <td>{plan.assignments.find((d) => d.service_id === change.service_id)?.estimated_arrival.toFixed(1) ?? "—"}</td>
                    <td className="wrap">{change.explanation}</td>
                  </tr>
                ))}
                {plan.unassigned_incident_ids.map((id) => (
                  <tr key={id} className="sev-critical">
                    <td><span className="tag change-dropped">unmet</span></td>
                    <td>—</td>
                    <td className="mono">{id}</td>
                    <td>—</td>
                    <td>—</td>
                    <td className="wrap">{plan.rationale.find((line) => line.startsWith(`Incident ${id} left unassigned`)) ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted pad">No current proposal.</p>
          )
        )}
        {tab === "Roads" && (
          <table>
            <thead>
              <tr><th>Road</th><th>Status</th><th>Length</th></tr>
            </thead>
            <tbody>
              {world.roads.map((road) => (
                <tr key={road.id}>
                  <td className="mono">{road.id}</td>
                  <td>{road.is_closed ? <span className="pill status-open">closed</span> : "open"}</td>
                  <td>{road.length.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "Timeline" && (
          <table>
            <thead>
              <tr><th>#</th><th>Time</th><th>Tick</th><th>Version</th><th>Event</th><th>Actor</th><th>Entities</th><th>Reason</th></tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.sequence}>
                  <td>{event.sequence}</td>
                  <td>{time(event.recorded_at)}</td>
                  <td>{event.tick}</td>
                  <td>v{event.version}</td>
                  <td>{label(event.kind)}</td>
                  <td>{event.actor}</td>
                  <td className="mono">{event.entity_ids.join(", ")}</td>
                  <td className="wrap">{event.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
