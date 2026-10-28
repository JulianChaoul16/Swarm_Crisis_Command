"use client";

import { useState } from "react";
import { REPORT_LABELS, isNew, orderEmergencies, timeAgo } from "./Panels";
import type { AgentReport, WorldEvent, WorldState } from "@/lib/types";

const TABS = ["Emergencies", "Units", "Agent reports", "Roads", "Event log"] as const;
type Tab = (typeof TABS)[number];

export default function TableView({ world, reports, events, acknowledged, onAcknowledge }: {
  world: WorldState;
  reports: AgentReport[];
  events: WorldEvent[];
  acknowledged: Set<string>;
  onAcknowledge: (id: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("Emergencies");
  const counts: Record<Tab, number> = {
    Emergencies: world.incidents.length,
    Units: world.services.length,
    "Agent reports": reports.filter((report) => !acknowledged.has(report.id)).length,
    Roads: world.roads.length,
    "Event log": events.length,
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
        {tab === "Emergencies" && (
          <table>
            <thead>
              <tr><th>Severity</th><th>Emergency</th><th>ID</th><th>Status</th><th>Needs</th><th>Assigned units</th><th>Reported</th><th>Grid</th></tr>
            </thead>
            <tbody>
              {orderEmergencies(world.incidents).map((incident) => (
                <tr key={incident.id} className={`sev-${incident.severity}`}>
                  <td><span className={`sev sev-${incident.severity}`}>{incident.severity}</span></td>
                  <td>{incident.kind} {isNew(incident, world.tick) && <span className="badge-new">NEW</span>}</td>
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
        {tab === "Units" && (
          <table>
            <thead>
              <tr><th>Unit</th><th>ID</th><th>Capabilities</th><th>Status</th><th>Assignment</th><th>Grid</th><th>Route</th></tr>
            </thead>
            <tbody>
              {world.services.map((service) => (
                <tr key={service.id}>
                  <td>{service.name}</td>
                  <td className="mono">{service.id}</td>
                  <td>{service.capabilities.join(", ")}</td>
                  <td>{service.available ? "Available" : "En route"}</td>
                  <td>{service.assigned_incident_id ?? "—"}</td>
                  <td className="mono">{service.location.x.toFixed(1)}, {service.location.y.toFixed(1)}</td>
                  <td>{service.assigned_incident_id ? (service.route ? `${service.route.length - 1} segments` : "No open route") : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "Agent reports" && (
          <table>
            <thead>
              <tr><th>Time</th><th>Agent</th><th>Type</th><th>Report</th><th>Related</th><th>Confidence</th><th /></tr>
            </thead>
            <tbody>
              {reports.map((report) => (
                <tr key={report.id} className={acknowledged.has(report.id) ? "done" : ""}>
                  <td>{timeAgo(report.at)}</td>
                  <td>{report.agent}</td>
                  <td><span className={`tag type-${report.type}`}>{REPORT_LABELS[report.type]}</span></td>
                  <td className="wrap">{report.message}</td>
                  <td className="mono">{report.related_ids.join(", ")}</td>
                  <td>{report.confidence === null ? "—" : `${Math.round(report.confidence * 100)}%`}</td>
                  <td>
                    {!acknowledged.has(report.id) && (
                      <button className="ghost small" onClick={() => onAcknowledge(report.id)}>Acknowledge</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
        {tab === "Event log" && (
          <table>
            <thead>
              <tr><th>#</th><th>Tick</th><th>Version</th><th>Event</th><th>Entities</th><th>Reason</th></tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.sequence}>
                  <td>{event.sequence}</td>
                  <td>{event.tick}</td>
                  <td>v{event.version}</td>
                  <td>{event.kind.replaceAll("_", " ")}</td>
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
