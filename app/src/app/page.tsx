"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import CrisisMap, { type MapData } from "@/components/CrisisMap";
import { ApiError, api } from "@/lib/api";
import type { Incident, Proposal, WorldEvent, WorldState } from "@/lib/types";

// One screen, three panels: incident queue | map | recommendation + activity.
// Workflow: Detect -> Prioritize -> Decide -> Dispatch -> Communicate.

const rank = (incidents: Incident[]) =>
  incidents
    .filter((incident) => incident.status !== "resolved")
    .sort((a, b) => b.severity_level - a.severity_level || b.people - a.people || a.id.localeCompare(b.id));

const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function IncidentQueue({ incidents, focus }: { incidents: Incident[]; focus: string | null }) {
  if (!incidents.length) return <p className="empty">No open incidents.</p>;
  return (
    <ol className="queue">
      {incidents.map((incident, index) => (
        <li key={incident.id} className={`card sev-${incident.severity} ${incident.id === focus ? "focus" : ""}`}>
          <div className="card-top">
            <span className="rank">#{index + 1}</span>
            <span className={`sev sev-${incident.severity}`}>{incident.severity}</span>
            <span className="muted">{incident.id}</span>
            <span className={`status st-${incident.status}`}>{incident.status === "assigned" ? "dispatched" : "needs units"}</span>
          </div>
          <div className="place">{incident.place ?? "Unknown location"}</div>
          <div className="kind">{incident.kind}</div>
          <div className="meta">
            <b>{incident.people}</b> people at risk · needs {incident.required_services.join(", ") || "none"}
          </div>
        </li>
      ))}
    </ol>
  );
}

function Recommendation({
  plan,
  world,
  busy,
  onApprove,
  onReject,
}: {
  plan: Proposal | null;
  world: WorldState;
  busy: boolean;
  onApprove: (plan: Proposal) => void;
  onReject: (plan: Proposal) => void;
}) {
  if (!plan) return <p className="empty">No recommendation yet.</p>;
  const incidents = new Map(world.incidents.map((incident) => [incident.id, incident]));
  const units = new Map(world.services.map((service) => [service.id, service.name]));
  // Lead with the most urgent incident the plan serves.
  const served = rank(plan.assignments.map((d) => incidents.get(d.incident_id)!).filter(Boolean))
    .filter((incident, index, all) => all.findIndex((other) => other.id === incident.id) === index);
  const lead = served[0];
  const leadDispatches = lead ? plan.assignments.filter((d) => d.incident_id === lead.id) : [];
  const others = served.slice(1);
  const reasons = leadDispatches.map(
    (d) => `${units.get(d.service_id)}: ETA ${d.estimated_arrival.toFixed(1)} min over open roads`,
  );
  const unmet = plan.unassigned_incident_ids.map((id) => {
    const reason = plan.rationale.find((r) => r.startsWith(`Incident ${id} left unassigned`))?.split("; ")[1];
    const incident = incidents.get(id);
    return `${incident?.place ?? id} (${incident?.kind ?? ""}) waits: ${reason ?? "no units available"}`;
  });
  const live = plan.status === "proposed";

  return (
    <div className={`rec ${live ? "" : "rec-done"}`}>
      <div className="rec-head">
        <span className="pill pill-solver">Exact solver</span>
        <span className="muted">Plan {plan.id} · world v{plan.world_version} · {plan.status}</span>
      </div>
      {lead ? (
        <>
          <div className="rec-label">Recommend</div>
          <div className="rec-action">
            Dispatch {leadDispatches.map((d) => units.get(d.service_id)).join(" + ")} to {lead.place ?? lead.id}: {lead.kind}
          </div>
          {others.length > 0 && (
            <div className="rec-also">
              Also: {others.map((incident) => {
                const names = plan.assignments.filter((d) => d.incident_id === incident.id).map((d) => units.get(d.service_id));
                return `${names.join(" + ")} → ${incident.place ?? incident.id}`;
              }).join(" · ")}
            </div>
          )}
          <div className="rec-label">Why</div>
          <ul className="rec-why">
            <li>Most urgent open incident: {lead.severity}, {lead.people} people at risk</li>
            {reasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        </>
      ) : (
        <div className="rec-action">No new dispatches needed.</div>
      )}
      <div className="rec-label">Tradeoff</div>
      <ul className="rec-why">
        {unmet.length ? unmet.map((line) => <li key={line} className="bad">{line}</li>) : <li>No incident left waiting.</li>}
      </ul>
      {live && (
        <div className="rec-buttons">
          <button className="approve" disabled={busy || !plan.valid || !plan.assignments.length} onClick={() => onApprove(plan)}>
            Approve &amp; Dispatch
          </button>
          <button className="ghost" disabled title="Arrives with the swarm (Phase 2-3)">Simulate Alternative</button>
          <button className="ghost" disabled={busy} onClick={() => onReject(plan)}>Reject</button>
        </div>
      )}
      {plan.status === "approved" && <p className="approved">✓ Approved by {plan.decided_by}: units dispatched (simulated)</p>}
    </div>
  );
}

function ActivityFeed({ events }: { events: WorldEvent[] }) {
  if (!events.length) return <p className="empty">No activity yet.</p>;
  return (
    <ol className="feed">
      {events.map((event) => (
        <li key={event.sequence}>
          <span className="t">[{time(event.recorded_at)}]</span> <b>{event.actor}</b>: {event.reason}
        </li>
      ))}
    </ol>
  );
}

export default function CommandScreen() {
  const [world, setWorld] = useState<WorldState | null>(null);
  const [events, setEvents] = useState<WorldEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const [state, log] = await Promise.all([api.state(), api.events(60)]);
    setWorld(state);
    setEvents(log);
  }, []);

  const run = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true);
      try {
        await action();
        setError(null);
      } catch (caught) {
        setError(caught instanceof ApiError || caught instanceof Error ? caught.message : String(caught));
      } finally {
        setBusy(false);
        await refresh().catch(() => null);
      }
    },
    [refresh],
  );

  useEffect(() => {
    refresh().catch((caught) =>
      setError(`Cannot reach the API (${caught instanceof Error ? caught.message : caught}). Start it with: python -m uvicorn crisis_command.api.server:app --port 8000`),
    );
  }, [refresh]);

  const queue = useMemo(() => (world ? rank(world.incidents) : []), [world]);
  const mapData: MapData | null = useMemo(
    () =>
      world && {
        incidents: queue.map((incident) => ({ ...incident, kind: incident.place ? `${incident.place}: ${incident.kind}` : incident.kind })),
        services: world.services,
        roads: world.roads,
        annotations: world.annotations,
        proposedRoutes: world.current_plan?.status === "proposed" ? world.current_plan.assignments : [],
      },
    [world, queue],
  );

  return (
    <div className="screen">
      <header className="topbar">
        <span className="sim">SIMULATION</span>
        <h1>Crisis Command Swarm <span className="muted">· Richmond Flood</span></h1>
        <button className="start" disabled={busy} onClick={() => run(api.reset)} title="Resets the seeded scenario. The timed demo arrives in Phase 4.">
          ▶ START DEMO SCENARIO
        </button>
        <div className="gauge" title="Scenario gauge value arrives with the seeded demo (Phase 4).">
          James River gauge (Westham): <b>—</b> <span className="muted">scenario value pending</span>
        </div>
      </header>

      {error && <div className="banner">{error}</div>}

      {world && (
        <main className="panels">
          <section className="panel left">
            <h2>Swarm Incident Queue</h2>
            <IncidentQueue incidents={queue} focus={queue[0]?.id ?? null} />
          </section>

          <section className="panel center">
            <CrisisMap data={mapData} basemap />
            <div className="legend">
              <span><i className="ring open" /> Needs units</span>
              <span><i className="ring assigned" /> Dispatched</span>
              <span><i className="line proposed" /> Recommended route</span>
              <span><i className="line closed" /> Road closed</span>
            </div>
          </section>

          <section className="panel right">
            <h2>Swarm Recommendation</h2>
            <Recommendation
              plan={world.current_plan}
              world={world}
              busy={busy}
              onApprove={(plan) => run(() => api.approve(plan.id, plan.world_version))}
              onReject={(plan) => run(() => api.rejectPlan(plan.id, "Rejected by operator."))}
            />
            <h2>Swarm Activity</h2>
            <ActivityFeed events={events} />
          </section>
        </main>
      )}
    </div>
  );
}
