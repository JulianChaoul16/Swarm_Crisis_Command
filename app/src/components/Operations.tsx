"use client";

import { useEffect, useRef, useState } from "react";
import { REPORT_LABELS, timeAgo } from "./Panels";
import type { ChatMessage, CommsMessage, DispatchProposal } from "@/lib/operations";
import type { AgentReport, Incident, Service, WorldState } from "@/lib/types";

export function AlertBar({ alerts, onAcknowledge }: {
  alerts: Incident[];
  onAcknowledge: (incidentId: string) => void;
}) {
  if (!alerts.length) return null;
  return (
    <div className="alert-bar" role="alert">
      {alerts.map((incident) => (
        <div key={incident.id} className={`alert sev-${incident.severity}`}>
          <span className="alert-sev">{incident.severity}</span>
          <span className="grow">
            <strong>{incident.kind}</strong> · {incident.id} · needs{" "}
            {incident.required_services.join(", ") || "any unit"} · unassigned
          </span>
          <button className="small" onClick={() => onAcknowledge(incident.id)}>
            Acknowledge
          </button>
        </div>
      ))}
    </div>
  );
}

export function AttentionPanel({ world, proposals, reports, acknowledged, onApprove, onReject, onAcknowledge }: {
  world: WorldState;
  proposals: DispatchProposal[];
  reports: AgentReport[];
  acknowledged: Set<string>;
  onApprove: (proposal: DispatchProposal) => void;
  onReject: (proposal: DispatchProposal) => void;
  onAcknowledge: (reportId: string) => void;
}) {
  const incidentsById = new Map(world.incidents.map((incident) => [incident.id, incident]));
  const servicesById = new Map(world.services.map((service) => [service.id, service]));
  const pending = proposals.filter(
    (proposal) =>
      incidentsById.has(proposal.incident_id) && servicesById.get(proposal.service_id)?.available,
  );
  const critical = world.incidents
    .filter((incident) => incident.status !== "resolved" && incident.severity_level >= 3)
    .sort((a, b) => b.severity_level - a.severity_level);
  const reviews = reports.filter(
    (report) => report.type === "needs_review" && !acknowledged.has(report.id),
  );

  return (
    <>
      <h3 className="sub">Dispatch approvals <span className="count">{pending.length}</span></h3>
      {pending.length === 0 && <p className="muted">No dispatches awaiting approval.</p>}
      <ul className="list">
        {pending.map((proposal) => {
          const incident = incidentsById.get(proposal.incident_id)!;
          const service = servicesById.get(proposal.service_id)!;
          return (
            <li key={proposal.id} className="proposal">
              <div className="row">
                <div className="grow">
                  <div className="title">
                    {service.name} → {incident.kind}
                  </div>
                  <div className="meta">
                    {incident.id} · {incident.severity} · ETA {proposal.eta.toFixed(1)}
                  </div>
                </div>
                <button className="small" onClick={() => onApprove(proposal)}>Approve</button>
                <button className="ghost small" onClick={() => onReject(proposal)}>Reject</button>
              </div>
              <div className="meta">{proposal.reason}</div>
            </li>
          );
        })}
      </ul>

      <h3 className="sub">Critical incidents <span className="count">{critical.length}</span></h3>
      <ul className="list">
        {critical.map((incident) => {
          const related = reports.filter((report) => report.related_ids.includes(incident.id));
          const latest = related[0];
          return (
            <li key={incident.id} className={`sev-${incident.severity}`}>
              <div className="row">
                <span className="title grow">
                  {incident.kind} <span className="muted">{incident.id}</span>
                </span>
                <span className={`pill status-${incident.status}`}>{incident.status}</span>
              </div>
              <div className="meta">
                {incident.assigned_service_ids.join(", ") || "No units assigned"} · {related.length} agent reports
              </div>
              {latest && (
                <div className="meta latest">
                  <strong>{latest.agent}:</strong> {latest.message}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {reviews.length > 0 && (
        <>
          <h3 className="sub">Needs your review <span className="count">{reviews.length}</span></h3>
          <ul className="list">
            {reviews.map((report) => (
              <li key={report.id}>
                <div className="meta">
                  <strong>{report.agent}</strong> · {REPORT_LABELS[report.type]} · {timeAgo(report.at)}
                </div>
                <div className="row">
                  <span className="report-msg grow">{report.message}</span>
                  <button className="ghost small" onClick={() => onAcknowledge(report.id)}>Reviewed</button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

export function UnitActions({ services, incidents, onDispatch, onMessage }: {
  services: Service[];
  incidents: Incident[];
  onDispatch: (serviceId: string, incidentId: string) => void;
  onMessage: (serviceId: string) => void;
}) {
  const open = incidents.filter((incident) => incident.status !== "resolved");
  const capabilities = [...new Set(services.flatMap((service) => service.capabilities))].sort();
  return (
    <>
      <div className="cap-summary">
        {capabilities.map((capability) => {
          const units = services.filter((service) => service.capabilities.includes(capability));
          const free = units.filter((service) => service.available).length;
          return (
            <div key={capability} className={`cap ${free === 0 ? "empty" : ""}`}>
              <strong>{free}</strong>/{units.length} <span>{capability}</span>
            </div>
          );
        })}
      </div>
      <ul className="list">
        {services.map((service) => (
          <li key={service.id} className="row">
            <span className={`dot ${service.available ? "unit-idle" : "unit-busy"}`} />
            <div className="grow">
              <div className="title">{service.name}</div>
              <div className="meta">
                {service.capabilities.join(", ")} ·{" "}
                {service.assigned_incident_id ? `→ ${service.assigned_incident_id}` : "available"}
              </div>
            </div>
            {service.available && (
              <select
                className="small"
                value=""
                aria-label={`Dispatch ${service.name}`}
                onChange={(event) => event.target.value && onDispatch(service.id, event.target.value)}
              >
                <option value="">Dispatch…</option>
                {open.map((incident) => (
                  <option key={incident.id} value={incident.id}>
                    {incident.id} · {incident.kind}
                  </option>
                ))}
              </select>
            )}
            <button className="ghost small" onClick={() => onMessage(service.id)}>Message</button>
          </li>
        ))}
      </ul>
    </>
  );
}

export function FieldComms({ services, comms, target, onTarget, onSend }: {
  services: Service[];
  comms: CommsMessage[];
  target: string;
  onTarget: (serviceId: string) => void;
  onSend: (serviceId: string, text: string) => void;
}) {
  const [text, setText] = useState("");
  const names = new Map(services.map((service) => [service.id, service.name]));
  return (
    <div className="comms">
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          if (!target || !text.trim()) return;
          onSend(target, text.trim());
          setText("");
        }}
      >
        <select value={target} onChange={(event) => onTarget(event.target.value)} aria-label="Recipient unit">
          <option value="">To unit…</option>
          {services.map((service) => (
            <option key={service.id} value={service.id}>{service.name}</option>
          ))}
        </select>
        <input value={text} placeholder="Message to field unit" onChange={(event) => setText(event.target.value)} />
        <button disabled={!target || !text.trim()}>Send</button>
      </form>
      <ul className="list">
        {comms.map((message) => (
          <li key={message.id} className={`msg ${message.direction}`}>
            <div className="meta">
              {message.direction === "out" ? `You → ${names.get(message.unit_id) ?? message.unit_id}` : `${names.get(message.unit_id) ?? message.unit_id} → You`}{" "}
              · {timeAgo(message.at)}
            </div>
            <div>{message.text}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

const SUGGESTIONS = ["What are the top priorities?", "Which units are available?", "Any road closures?"];

export function AgentChat({ chat, onAsk }: { chat: ChatMessage[]; onAsk: (question: string) => void }) {
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => end.current?.scrollIntoView({ block: "nearest" }), [chat]);
  const submit = (question: string) => {
    if (!question.trim()) return;
    onAsk(question.trim());
    setText("");
  };
  return (
    <div className="chat">
      <div className="chat-log">
        {chat.length === 0 && (
          <div className="suggestions">
            {SUGGESTIONS.map((suggestion) => (
              <button key={suggestion} className="ghost small" onClick={() => submit(suggestion)}>
                {suggestion}
              </button>
            ))}
          </div>
        )}
        {chat.map((message) => (
          <div key={message.id} className={`bubble ${message.role}`}>
            {message.role === "agent" && <div className="agent">{message.agent} agent</div>}
            {message.text}
          </div>
        ))}
        <div ref={end} />
      </div>
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          submit(text);
        }}
      >
        <input value={text} placeholder="Ask the agents, e.g. status of incident-4" onChange={(event) => setText(event.target.value)} />
        <button disabled={!text.trim()}>Ask</button>
      </form>
    </div>
  );
}
