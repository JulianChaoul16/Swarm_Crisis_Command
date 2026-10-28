// Manager-side operations: dispatch approvals, field comms, agent chat, alert
// acknowledgement. Mock-only for now; the API executes plans without an approval gate
// and has no comms or chat endpoints yet.
"use client";

import { useCallback, useState } from "react";
import type { AgentReport, Incident, WorldState } from "./types";

export type DispatchProposal = {
  id: string;
  incident_id: string;
  service_id: string;
  eta: number;
  reason: string;
};

export type CommsMessage = {
  id: string;
  unit_id: string;
  direction: "out" | "in";
  text: string;
  at: string;
};

export type ChatMessage = {
  id: string;
  role: "manager" | "agent";
  agent?: AgentReport["agent"];
  text: string;
  at: string;
};

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
let sequence = 0;
const nextId = (prefix: string) => `${prefix}-${Date.now()}-${sequence++}`;

export const mockProposals: DispatchProposal[] = [
  { id: "p1", incident_id: "incident-4", service_id: "unit-rescue-1", eta: 3.2, reason: "Only available rescue-capable unit; incident-4 is critical with residents trapped." },
  { id: "p2", incident_id: "incident-5", service_id: "unit-utility-1", eta: 1.1, reason: "Utility crew is closest and covers the 'utility' requirement." },
  { id: "p3", incident_id: "incident-5", service_id: "unit-police-2", eta: 4.6, reason: "Covers 'police' for scene control around the downed line." },
];

const mockComms: CommsMessage[] = [
  { id: "c3", unit_id: "unit-fire-1", direction: "in", text: "Detouring via east avenue, bridge closed. ETA 4 min.", at: minutesAgo(2) },
  { id: "c2", unit_id: "unit-medical-2", direction: "in", text: "On scene at collision, 2 patients, 1 transport likely.", at: minutesAgo(5) },
  { id: "c1", unit_id: "unit-medical-2", direction: "out", text: "Proceed to road collision at Police Rd & 2nd.", at: minutesAgo(14) },
];

const isCritical = (incident: Incident) =>
  incident.status !== "resolved" && incident.severity_level >= 3;

// Answers built from current world state so replies stay grounded until Muse is wired in.
function mockAgentReply(question: string, world: WorldState): ChatMessage {
  const text = question.toLowerCase();
  const reply = (agent: AgentReport["agent"], body: string): ChatMessage => ({
    id: nextId("chat"),
    role: "agent",
    agent,
    text: body,
    at: new Date().toISOString(),
  });

  const incident = world.incidents.find((item) => text.includes(item.id));
  if (incident) {
    const units = incident.assigned_service_ids.join(", ") || "no units yet";
    return reply(
      "Situation",
      `${incident.id} is a ${incident.severity} ${incident.kind}, status ${incident.status}. ` +
        `Needs ${incident.required_services.join(", ") || "any unit"}; assigned: ${units}.`,
    );
  }
  const unit = world.services.find((item) => text.includes(item.id) || text.includes(item.name.toLowerCase()));
  if (unit) {
    return reply(
      "Logistics",
      `${unit.name} (${unit.capabilities.join(", ")}) is ${unit.available ? "available" : `en route to ${unit.assigned_incident_id}`} ` +
        `at grid ${unit.location.x.toFixed(1)}, ${unit.location.y.toFixed(1)}.`,
    );
  }
  if (text.includes("available") || text.includes("free")) {
    const free = world.services.filter((item) => item.available).map((item) => item.name);
    return reply("Logistics", free.length ? `Available now: ${free.join(", ")}.` : "No units are available right now.");
  }
  if (text.includes("road") || text.includes("route") || text.includes("bridge")) {
    const closed = world.roads.filter((road) => road.is_closed).map((road) => road.id);
    return reply("Routing", closed.length ? `Closed roads: ${closed.join(", ")}. Routes avoid them.` : "All roads are open.");
  }
  if (text.includes("critical") || text.includes("priority")) {
    const critical = world.incidents.filter(isCritical).map((item) => `${item.id} (${item.kind})`);
    return reply("Situation", critical.length ? `Top priorities: ${critical.join("; ")}.` : "No critical or high incidents active.");
  }
  return reply(
    "Situation",
    "Mock agent: ask about an incident id, a unit, available units, roads, or priorities. Free-form answers arrive with the Muse integration.",
  );
}

export function useOperations(
  world: WorldState | null,
  setWorld: (world: WorldState) => void,
) {
  const [proposals, setProposals] = useState<DispatchProposal[]>(mockProposals);
  const [comms, setComms] = useState<CommsMessage[]>(mockComms);
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set());

  const acknowledge = useCallback(
    (key: string) => setAcknowledged((current) => new Set(current).add(key)),
    [],
  );

  const sendMessage = useCallback((unitId: string, text: string) => {
    setComms((current) => [
      { id: nextId("comms"), unit_id: unitId, direction: "out", text, at: new Date().toISOString() },
      ...current,
    ]);
  }, []);

  // Applies a dispatch to the local mock world and notifies the unit.
  const dispatch = useCallback(
    (serviceId: string, incidentId: string) => {
      if (!world) return;
      const incident = world.incidents.find((item) => item.id === incidentId);
      const unit = world.services.find((item) => item.id === serviceId);
      if (!incident || !unit || !unit.available) return;
      setWorld({
        ...world,
        services: world.services.map((item) =>
          item.id === serviceId
            ? { ...item, available: false, assigned_incident_id: incidentId, route: [item.position, incident.position] }
            : item,
        ),
        incidents: world.incidents.map((item) =>
          item.id === incidentId
            ? { ...item, status: "assigned", assigned_service_ids: [...item.assigned_service_ids, serviceId] }
            : item,
        ),
      });
      sendMessage(serviceId, `Dispatched to ${incident.kind} (${incident.id}). Proceed and report on arrival.`);
      setProposals((current) => current.filter((item) => item.service_id !== serviceId));
    },
    [world, setWorld, sendMessage],
  );

  const approve = useCallback(
    (proposal: DispatchProposal) => dispatch(proposal.service_id, proposal.incident_id),
    [dispatch],
  );

  const reject = useCallback((proposal: DispatchProposal) => {
    setProposals((current) => current.filter((item) => item.id !== proposal.id));
  }, []);

  const ask = useCallback(
    (question: string) => {
      if (!world) return;
      const asked: ChatMessage = { id: nextId("chat"), role: "manager", text: question, at: new Date().toISOString() };
      setChat((current) => [...current, asked, mockAgentReply(question, world)]);
    },
    [world],
  );

  const alerts = (world?.incidents ?? []).filter(
    (incident) =>
      incident.status === "open" && incident.severity_level >= 3 && !acknowledged.has(`incident:${incident.id}`),
  );

  return { proposals, approve, reject, comms, sendMessage, dispatch, chat, ask, acknowledged, acknowledge, alerts };
}

export type Operations = ReturnType<typeof useOperations>;
