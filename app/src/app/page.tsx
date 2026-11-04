"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import CrisisMap, { type MapData } from "@/components/CrisisMap";
import { ApiError, api } from "@/lib/api";
import type { Agent, Incident, Proposal, Swarm, WorldState } from "@/lib/types";

// One screen, three panels: incident queue | map | recommendation + activity.
// Workflow: Detect -> Prioritize -> Decide -> Dispatch -> Communicate.

// Ranked by the swarm's consensus severity (the same order the solver plans in).
const rank = (incidents: Incident[], swarm: Swarm) => {
  const final = (incident: Incident) => swarm.incidents[incident.id]?.final ?? -1;
  return incidents
    .filter((incident) => incident.status !== "resolved")
    .sort((a, b) => final(b) - final(a) || b.people - a.people || a.id.localeCompare(b.id));
};

// Two agents vote, plus a deterministic access check (code, not an agent).
const AGENTS: [string, string][] = [["Intake Agent", "Intake"], ["Vision Agent", "Vision"], ["Access check", "Access"]];

/** A model pill: the model's name and where this output really came from. */
function Pill({ label, title }: { label?: string | null; title?: string }) {
  // Labels come from the API; tolerate a missing one (e.g. while the app and API update).
  if (!label) return null;
  const [model, source] = label.split(" · ");
  const kind = source === "live" ? "live" : source === "cache" ? "cache" : source === "local" ? "local" : source ? "sample" : "rules";
  return (
    <span className={`pill pill-${kind}`} title={title}>
      [{model}]{source ? <span className="pill-src"> {source}</span> : null}
    </span>
  );
}

const ago = (iso: string) => {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return seconds < 60 ? `${seconds}s ago` : `${Math.round(seconds / 60)}m ago`;
};

/** The named agents; click one to see what it is working on. */
function AgentStrip({ agents, open, onOpen }: { agents: Agent[]; open: string | null; onOpen: (name: string | null) => void }) {
  return (
    <div className="agent-strip">
      {agents.map((agent) => (
        <button key={agent.name} className={`agent-chip ${open === agent.name ? "on" : ""}`}
          onClick={() => onOpen(open === agent.name ? null : agent.name)} title={agent.role}>
          <b>{agent.name}</b>
          <span className="muted"> {agent.last ? `· ${agent.actions} actions · ${ago(agent.last.at)}` : "· idle"}</span>
        </button>
      ))}
    </div>
  );
}

function AgentPanel({ agent, onClose }: { agent: Agent; onClose: () => void }) {
  return (
    <div className="agent-panel" role="dialog" aria-label={agent.name}>
      <div className="agent-head">
        <h3>{agent.name}</h3>
        <button className="ghost" onClick={onClose}>Close</button>
      </div>
      <p>{agent.role}</p>
      <div className="rec-label">Models</div>
      <ul className="agent-models">
        {agent.models.map((m) => <li key={m.model}><b>{m.model}</b> <span className="muted">{m.status}</span></li>)}
      </ul>
      {agent.last && (
        <>
          <div className="rec-label">Working on</div>
          <p className="agent-now">{agent.last.text} <span className="muted">({ago(agent.last.at)})</span></p>
        </>
      )}
      {agent.votes.length > 0 && (
        <>
          <div className="rec-label">Current votes</div>
          <table className="agent-votes">
            <tbody>
              {agent.votes.map((v) => (
                <tr key={v.incident_id}>
                  <td><b>{v.score}</b></td>
                  <td>{v.incident_id} · {v.kind}<div className="muted">{v.reasons[0]}</div></td>
                  <td className="muted">{v.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <div className="rec-label">Activity ({agent.actions})</div>
      <ol className="feed">
        {agent.activity.map((item, index) => (
          <li key={`${item.at}${index}`}>
            <span className="t">[{time(item.at)}]</span> <span className="src">({item.source})</span> {item.text}
          </li>
        ))}
        {!agent.activity.length && <li className="muted">Nothing yet this run.</li>}
      </ol>
    </div>
  );
}

/** Where a fact came from, and when. */
function Source({ evidence }: { evidence?: { report_id: string | null; source: string; at: string | null }[] | null }) {
  if (!evidence?.length) return null;
  return (
    <div className="source">
      Source:{" "}
      {evidence.map((e, i) => (
        <span key={i}>{e.report_id ? `${e.report_id} · ` : ""}{e.source}{e.at ? ` · ${time(e.at)}` : ""}{i < evidence.length - 1 ? "; " : ""}</span>
      ))}
    </div>
  );
}

function Unverified({ numbers, what }: { numbers?: string[]; what: string }) {
  if (!numbers?.length) return null;
  return (
    <p className="unverified" title="These numbers appear in the model's text but not in the facts it was given.">
      ⚠ Unverified in {what}: {numbers.join(", ")}. Not in the facts given to the model; check before using.
    </p>
  );
}

function Votes({ entry }: { entry: Swarm["incidents"][string] | undefined }) {
  if (!entry) return <div className="votes muted">Swarm voting…</div>;
  return (
    <div className="votes">
      {AGENTS.map(([agent, label]) => {
        const vote = entry.votes[agent];
        return (
          <span key={agent} className="vote" title={vote ? `${vote.reasons.join("; ")} (source: ${vote.source})` : "waiting"}>
            {label}: <b>{vote ? vote.score : "…"}</b>
            {vote && <span className="vote-src"> {vote.source.replace(" · ", " ")}</span>}
          </span>
        );
      })}
      <span className="final" title="Consensus of three estimates (two AI, one computed); not a measured value.">= Final: <b>{entry.final ?? "…"}</b> <span className="muted">est.</span></span>
    </div>
  );
}

const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function IncidentQueue({ incidents, focus, swarm }: { incidents: Incident[]; focus: string | null; swarm: Swarm }) {
  if (!incidents.length) return <p className="empty">No open incidents.</p>;
  return (
    <ol className="queue">
      {incidents.map((incident, index) => (
        <li key={incident.id} className={`card sev-${incident.severity} ${incident.id === focus ? "focus" : ""}`}>
          <div className="card-top">
            <span className="rank">#{index + 1}</span>
            <span className="score">{swarm.incidents[incident.id]?.final ?? "…"}</span>
            <span className={`sev sev-${incident.severity}`}>{incident.severity}</span>
            <span className="muted">{incident.id}</span>
            <span className={`status st-${incident.status}`}>{incident.status === "assigned" ? "dispatched" : "needs units"}</span>
          </div>
          <div className="place">{incident.place ?? "Unknown location"}</div>
          <div className="kind">{incident.kind}</div>
          <div className="meta">
            <b>{incident.people}</b> people at risk · needs {incident.required_services.join(", ") || "none"}
          </div>
          <Votes entry={swarm.incidents[incident.id]} />
          <Source evidence={incident.evidence} />
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
  const served = rank(plan.assignments.map((d) => incidents.get(d.incident_id)!).filter(Boolean), world.swarm)
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
          {leadDispatches.some((d) => d.via.length) && (
            <ul className="rec-route">
              {leadDispatches.map((d) => (
                <li key={d.service_id}>
                  <b>{units.get(d.service_id)}</b> via {d.via.join(" → ") || "its current location"}
                </li>
              ))}
              {world.roads.some((r) => r.is_closed) && (
                <li className="muted">
                  Avoiding closed:{" "}
                  {world.roads.filter((r) => r.is_closed).map((r) =>
                    `${r.name ?? r.id} [${r.evidence ? `${r.evidence.report_id ? `${r.evidence.report_id}, ` : ""}${r.evidence.source}${r.evidence.at ? `, ${time(r.evidence.at)}` : ""}` : "source unknown"}]`,
                  ).join("; ")}
                </li>
              )}
            </ul>
          )}
          {others.length > 0 && (
            <div className="rec-also">
              Also: {others.map((incident) => {
                const names = plan.assignments.filter((d) => d.incident_id === incident.id).map((d) => units.get(d.service_id));
                return `${names.join(" + ")} → ${incident.place ?? incident.id}`;
              }).join(" · ")}
            </div>
          )}
          <div className="rec-label">
            Why {world.explanation ? <Pill label={world.explanation.label} title={world.explanation.note} /> : <span className="muted">(writing…)</span>}
          </div>
          <ul className="rec-why">
            <li>
              Highest swarm consensus: {world.swarm.incidents[lead.id]?.final ?? "?"}, {lead.people} people at risk
            </li>
            {(world.explanation?.value.why.length ? world.explanation.value.why : AGENTS.map(([agent, label]) => {
              const vote = world.swarm.incidents[lead.id]?.votes[agent];
              return vote ? `${vote.reasons[0]} [${label}]` : "";
            }).filter(Boolean)).map((line) => <li key={line}>{line}</li>)}
            {reasons.map((reason) => <li key={reason}>{reason} [Exact solver]</li>)}
          </ul>
          <Unverified numbers={world.explanation?.unverified} what="the explanation" />
          <div className="rec-label">Confidence</div>
          <div className="rec-conf" title="How closely the three agents' votes agree (100 = identical). Not a calibrated probability.">
            {world.swarm.incidents[lead.id]?.agreement ?? "?"}% <span className="muted">vote agreement</span>
          </div>
        </>
      ) : (
        <div className="rec-action">No new dispatches needed.</div>
      )}
      <div className="rec-label">Tradeoff</div>
      <ul className="rec-why">
        {world.explanation?.value.tradeoff && <li>{world.explanation.value.tradeoff}</li>}
        {unmet.length ? unmet.map((line) => <li key={line} className="bad">{line}</li>) : !world.explanation && <li>No incident left waiting.</li>}
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
      <Alert alert={world.alerts[plan.id] ?? latestAlert(world)} />
    </div>
  );
}

function DemoButton({ demo, busy, run }: {
  demo: WorldState["demo"] | undefined;
  busy: boolean;
  run: (action: () => Promise<unknown>) => void;
}) {
  if (demo?.running && !demo.paused)
    return <button className="start pause" disabled={busy} onClick={() => run(api.pauseDemo)}>⏸ PAUSE DEMO</button>;
  if (demo?.running && demo.paused)
    return (
      <>
        <button className="start" disabled={busy} onClick={() => run(api.resumeDemo)}>▶ RESUME DEMO</button>
        <button className="ghost" disabled={busy} onClick={() => run(api.startDemo)}>Restart</button>
      </>
    );
  return (
    <button className="start" disabled={busy} onClick={() => run(api.startDemo)}
      title="Plays a 50-second seeded Richmond flood through the swarm, using recorded model responses (works offline).">
      ▶ {demo?.finished ? "RESTART DEMO" : "START DEMO SCENARIO"}
    </button>
  );
}

/** Plain-language narration of what the demo is doing now, and what to do. */
function DemoGuide({ demo }: { demo: WorldState["demo"] }) {
  if (!demo.guide)
    return (
      <div className="guide">
        <b>How to use the demo:</b> press <b>START DEMO SCENARIO</b>. For about 50 seconds a flash flood
        unfolds on its own: a 911 call, a photo of a flooded street, a rising river. Watch the queue
        (left), map (center) and recommendation (right) change as the agents react. When it ends, it is
        your turn to approve the recommended dispatch. You can pause at any time.
      </div>
    );
  return (
    <div className={`guide ${demo.guide.your_turn ? "your-turn" : ""} ${demo.paused ? "paused" : ""}`}>
      <b>{demo.paused ? "Paused" : demo.guide.your_turn ? "Your turn" : "Now"}:</b> {demo.guide.now}
      {!demo.guide.your_turn && demo.guide.next && (
        <span className="muted"> · Next: {demo.guide.next}{demo.paused ? " (paused)" : ` in ${demo.guide.next_in_s}s`}</span>
      )}
    </div>
  );
}

function Timeline({ demo }: { demo: WorldState["demo"] }) {
  if (demo.elapsed_s === null) return null;
  return (
    <div className="timeline" title={demo.errors.join("\n")}>
      <span className="muted">T+{Math.floor(demo.elapsed_s)}s</span>
      {demo.events.map((event) => (
        <span key={event.at} className={`tl ${event.done ? "done" : ""}`}>T+{event.at}s {event.label}</span>
      ))}
      {demo.errors.length > 0 && <span className="bad">{demo.errors.length} error(s)</span>}
    </div>
  );
}

function Transcripts({ swarm }: { swarm: Swarm }) {
  if (!swarm.transcripts.length) return null;
  const last = swarm.transcripts[swarm.transcripts.length - 1];
  return (
    <div className="transcript">
      <div className="rec-label">Listener Agent · transcript <Pill label={last.source} title={last.note} /></div>
      <p>“{last.text}”</p>
    </div>
  );
}

function VisionBadge({ swarm }: { swarm: Swarm }) {
  if (!swarm.imagery.length) return null;
  const image = swarm.imagery[swarm.imagery.length - 1];
  if (!image.objects || !image.estimate) return null;
  const counts = Object.entries(image.objects).map(([prompt, boxes]) => `${boxes.length} × ${prompt}`).join(", ");
  return (
    <div className="vision-badge" title={image.note}>
      <Pill label={image.boxes_source} /> {counts}
      <span className="sep">|</span>
      <Pill label={image.estimate_source} /> <b>ESTIMATE:</b> {image.estimate.depth_in_low}–{image.estimate.depth_in_high} in,{" "}
      <b>{image.estimate.passable_for_cars ? "passable" : "impassable"}</b> ({Math.round(image.estimate.confidence * 100)}% model-stated confidence, not calibrated)
    </div>
  );
}

const BOX_COLORS = ["#3b82f6", "#facc15", "#f97316", "#22c55e"];

/** The citizen photo with SAM 3.1's boxes drawn on it. */
function PhotoCard({ swarm }: { swarm: Swarm }) {
  const [open, setOpen] = useState(false);
  if (!swarm.imagery.length) return null;
  const image = swarm.imagery[swarm.imagery.length - 1];
  if (!image.objects || !image.size || !image.estimate) return null;
  const [width, height] = image.size;
  const prompts = Object.keys(image.objects);
  return (
    <div className={`photo ${open ? "photo-open" : ""}`}>
      <div className="rec-label">
        Vision Agent · citizen photo <Pill label={image.boxes_source} title={image.note} />{" "}
        <Pill label={image.estimate_source} />
      </div>
      <button className="photo-frame" onClick={() => setOpen(!open)} title={open ? "Click to shrink" : "Click to enlarge"}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={image.image_url} alt={`Photo of ${image.place}`} />
        <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
          {prompts.map((prompt, index) =>
            image.objects[prompt].map(([x1, y1, x2, y2], i) => (
              <rect key={`${prompt}${i}`} x={x1} y={y1} width={x2 - x1} height={y2 - y1}
                fill={index === 0 ? "rgba(59,130,246,0.18)" : "none"} stroke={BOX_COLORS[index % BOX_COLORS.length]}
                strokeWidth={index === 0 ? 10 : 6} />
            )),
          )}
        </svg>
      </button>
      <div className="photo-legend">
        {prompts.map((prompt, index) => (
          <span key={prompt}><i style={{ borderColor: BOX_COLORS[index % BOX_COLORS.length] }} /> {image.objects[prompt].length} × {prompt}</span>
        ))}
      </div>
      <p className="photo-reason"><b>Wonder ESTIMATE:</b> {image.estimate.depth_in_low}–{image.estimate.depth_in_high} in,{" "}
        {image.estimate.passable_for_cars ? "passable" : "impassable"} ({Math.round(image.estimate.confidence * 100)}%, model-stated). “{image.estimate.reason}”</p>
      <p className="muted photo-note">AI-generated test image (Muse Image), not a real photo. Map position is an assumed footprint.</p>
    </div>
  );
}

function latestAlert(world: WorldState) {
  const ids = Object.keys(world.alerts).sort();
  return ids.length ? world.alerts[ids[ids.length - 1]] : undefined;
}

function Alert({ alert }: { alert: WorldState["alerts"][string] | undefined }) {
  if (!alert) return null;
  return (
    <div className="alert">
      <div className="rec-label">Public alert {alert.text && <Pill label={alert.text.label} title={alert.text.note} />}</div>
      {alert.text ? (
        <>
          <p><b>EN</b> {alert.text.value.english}</p>
          <p><b>ES</b> {alert.text.value.spanish}</p>
          <Unverified numbers={alert.unverified} what="the alert" />
        </>
      ) : (
        <p className="muted">Writing…</p>
      )}
      {alert.graphic && !alert.graphic.value && (
        <p className="muted">Alert graphic: <Pill label={alert.graphic.label} title={alert.graphic.note} /> {alert.graphic.note}</p>
      )}
    </div>
  );
}

function ActivityFeed({ activity }: { activity: Swarm["activity"] }) {
  if (!activity.length) return <p className="empty">No activity yet.</p>;
  return (
    <ol className="feed">
      {activity.map((item, index) => (
        <li key={`${item.at}${index}`}>
          <span className="t">[{time(item.at)}]</span> <b>{item.agent}</b> <span className="src">({item.source})</span>: {item.text}
        </li>
      ))}
    </ol>
  );
}

/** Where to send a report from: this computer, or a phone on the same Wi-Fi. */
function ConnectCorner() {
  const [lanIp, setLanIp] = useState<string | null>(null);
  const [port, setPort] = useState("3000");
  useEffect(() => {
    setPort(window.location.port || "80");
    fetch("/api/connect").then((r) => r.json()).then((d) => setLanIp(d.lan_ip ?? null)).catch(() => setLanIp(null));
  }, []);
  const local = `http://localhost:${port}/report`;
  const network = lanIp ? `http://${lanIp}:${port}/report` : null;
  return (
    <div className="connect" title="Open this address to send an incident report into the swarm">
      <span className="connect-label">Send a report</span>
      <a href="/report" target="_blank" rel="noreferrer">{local}</a>
      {network && <span className="connect-lan">Phone on same Wi-Fi: <b>{network}</b></span>}
    </div>
  );
}

export default function CommandScreen() {
  const [world, setWorld] = useState<WorldState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [agentOpen, setAgentOpen] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setWorld(await api.state());
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
        // Model-written text attaches a few seconds after the plan; pick it up.
        for (const delay of [2500, 6000, 12000]) setTimeout(() => refresh().catch(() => null), delay);
      }
    },
    [refresh],
  );

  useEffect(() => {
    refresh().catch((caught) =>
      setError(`Cannot reach the API (${caught instanceof Error ? caught.message : caught}). Start it with: python -m uvicorn crisis_command.api.server:app --port 8000`),
    );
  }, [refresh]);

  const demoRunning = Boolean(world?.demo.running);
  useEffect(() => {
    if (!demoRunning) return;
    const timer = setInterval(() => refresh().catch(() => null), 1500);
    return () => clearInterval(timer);
  }, [demoRunning, refresh]);

  const queue = useMemo(() => (world ? rank(world.incidents, world.swarm) : []), [world]);
  const mapData: MapData | null = useMemo(
    () =>
      world && {
        incidents: queue.map((incident) => ({ ...incident, kind: incident.place ? `${incident.place}: ${incident.kind}` : incident.kind })),
        services: world.services,
        roads: world.roads,
        annotations: world.annotations,
        proposedRoutes: world.current_plan?.status === "proposed" ? world.current_plan.assignments : [],
        floodMasks: world.swarm.imagery.flatMap((image) => image.map_features ?? []),
      },
    [world, queue],
  );

  return (
    <div className="screen">
      <header className="topbar">
        <span className="sim">SIMULATION</span>
        <h1>Crisis Command Swarm <span className="muted">· Richmond Flood</span></h1>
        <DemoButton demo={world?.demo} busy={busy} run={run} />
        {world && <Timeline demo={world.demo} />}
        <div className="gauge" title={world?.swarm.gauge.site}>
          James River gauge: {world?.swarm.gauge.value !== undefined
            ? <><b>{world.swarm.gauge.value.toFixed(1)} ft {world.swarm.gauge.trend}</b> <span className="muted">({world.swarm.gauge.label})</span></>
            : <span className="muted">no reading yet (start the demo)</span>}
        </div>
        <ConnectCorner />
      </header>

      {world && <DemoGuide demo={world.demo} />}
      {world && <AgentStrip agents={world.agents} open={agentOpen} onOpen={setAgentOpen} />}
      {world && agentOpen && world.agents.find((a) => a.name === agentOpen) && (
        <AgentPanel agent={world.agents.find((a) => a.name === agentOpen)!} onClose={() => setAgentOpen(null)} />
      )}
      {error && <div className="banner">{error}</div>}

      {world && (
        <main className="panels">
          <section className="panel left">
            <h2>Swarm Incident Queue</h2>
            <Transcripts swarm={world.swarm} />
            <PhotoCard swarm={world.swarm} />
            <IncidentQueue incidents={queue} focus={queue[0]?.id ?? null} swarm={world.swarm} />
          </section>

          <section className="panel center">
            <CrisisMap data={mapData} basemap />
            <VisionBadge swarm={world.swarm} />
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
            <ActivityFeed activity={world.swarm.activity} />
          </section>
        </main>
      )}
    </div>
  );
}
