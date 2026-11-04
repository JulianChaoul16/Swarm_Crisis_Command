"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import CrisisMap, { type MapData } from "@/components/CrisisMap";
import { ALL_LAYERS, AffectedCard, LayerControl, ScrubBanner, Scrubber, type Layers, type Scrub } from "@/components/Gis";
import { ApiError, api } from "@/lib/api";
import type { Agent, GisFrame, Incident, Proposal, Swarm, WorldState, DemoStatus } from "@/lib/types";

// Three tabs. Operations map: incident queue | multi-layer map with a time scrubber |
// recommendation + activity. Personnel: the roster and the orders sent to each unit.
// Agents: each agent's decisions and the swarm's discussion per incident.
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

function AgentPanel({ agent, onClose }: { agent: Agent; onClose?: () => void }) {
  return (
    <div className={onClose ? "agent-panel" : "agent-panel inline"} role={onClose ? "dialog" : undefined} aria-label={agent.name}>
      <div className="agent-head">
        <h3>{agent.name}</h3>
        {onClose && <button className="ghost" onClick={onClose}>Close</button>}
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

function IncidentQueue({ incidents, focus, swarm, busy, onResolve }: {
  incidents: Incident[];
  focus: string | null;
  swarm: Swarm;
  busy: boolean;
  onResolve: (id: string) => void;
}) {
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
          {incident.status === "assigned" && (
            <button className="ghost resolve" disabled={busy} onClick={() => onResolve(incident.id)}
              title="The crew reports this incident done: free its units and re-plan.">
              Mark resolved
            </button>
          )}
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
  // A unit already on another mission is being rerouted, not newly dispatched.
  const movedFrom = new Map(world.services.flatMap((service) => {
    const current = service.assigned_incident_id;
    return current ? [[service.id, incidents.get(current)?.place ?? current] as const] : [];
  }));
  const unitLabel = ({ service_id: id, incident_id }: { service_id: string; incident_id: string }) =>
    `${units.get(id)}${movedFrom.has(id) && movedFrom.get(id) !== (incidents.get(incident_id)?.place ?? incident_id) ? ` (rerouted from ${movedFrom.get(id)})` : ""}`;
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
            Dispatch {leadDispatches.map(unitLabel).join(" + ")} to {lead.place ?? lead.id}: {lead.kind}
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
                const names = plan.assignments.filter((d) => d.incident_id === incident.id).map(unitLabel);
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
        <div className="rec-action">
          {unmet.length
            ? `No free unit can take the ${unmet.length} waiting incident${unmet.length > 1 ? "s" : ""}. Mark a dispatched incident resolved to free its units.`
            : "No new dispatches needed."}
        </div>
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

/** Small demo control in the nav: START, then ▶ plays one step (also the → key). */
function DemoStepper({ demo, busy, run }: {
  demo: DemoStatus;
  busy: boolean;
  run: (action: () => Promise<unknown>) => void;
}) {
  if (!demo.running && !demo.finished)
    return (
      <button className="demo-start" disabled={busy} onClick={() => run(api.startDemo)}
        title="Reset to the seeded Richmond flood. Then press ▶ (or →) to bring in each report.">
        Demo
      </button>
    );
  return (
    <div className="demo-stepper">
      <span className="muted">Demo {demo.step}/{demo.total}</span>
      <button className="step-btn" disabled={busy || !demo.can_step} onClick={() => run(api.stepDemo)}
        title={demo.can_step ? "Next step (→)" : demo.blocked ?? ""} aria-label="Next demo step">
        {demo.busy ? "…" : "▶"}
      </button>
      <button className="ghost icon" disabled={busy} onClick={() => run(api.startDemo)} title="Restart the demo">↺</button>
    </div>
  );
}

/** Presenter note for the current demo step, small, over the bottom of the map. */
function DemoNote({ demo }: { demo: DemoStatus }) {
  const [hidden, setHidden] = useState(false);
  const text = demo.guide?.now;
  useEffect(() => setHidden(false), [text]);
  if (!demo.guide || hidden) return null;
  return (
    <div className={`demo-note ${demo.guide.your_turn ? "your-turn" : ""}`}>
      <button className="ghost icon close" onClick={() => setHidden(true)} aria-label="Hide note">×</button>
      <b>{demo.guide.your_turn ? "Your turn" : `Step ${demo.step}/${demo.total}`}:</b> {demo.guide.now}
      {demo.guide.next && <div className="muted small">Next (▶): {demo.guide.next}{demo.blocked && !demo.busy ? ` · ${demo.blocked}` : ""}</div>}
      {demo.errors.length > 0 && <div className="bad small">{demo.errors.join("; ")}</div>}
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

/** Live intake mode: which inputs are connected. */
function LiveIntakeBadge({ mode }: { mode: WorldState["mode"] }) {
  const feeds = mode?.connectors ?? [];
  const detail = ["report page", "intake API", ...feeds.map((f) => `${f.name}: ${f.error ? `error (${f.error})` : `${f.received} received`}`)]
    .join(" · ") + (feeds.length ? "" : " · no database connector configured");
  return (
    <span className={`live-intake ${feeds.some((f) => f.error) ? "bad" : ""}`} title={detail}>
      <span className="live-dot" /> Live intake
    </span>
  );
}

/** Where to send a report from: this computer, or a phone on the same Wi-Fi. */
function ReportLink() {
  const [lanIp, setLanIp] = useState<string | null>(null);
  const [port, setPort] = useState("3000");
  useEffect(() => {
    setPort(window.location.port || "80");
    fetch("/api/connect").then((r) => r.json()).then((d) => setLanIp(d.lan_ip ?? null)).catch(() => setLanIp(null));
  }, []);
  return (
    <details className="report-link">
      <summary>Send a report</summary>
      <div className="report-pop">
        <a href="/report" target="_blank" rel="noreferrer">http://localhost:{port}/report</a>
        {lanIp && <div className="muted small">Phone on the same Wi-Fi: <b>http://{lanIp}:{port}/report</b></div>}
      </div>
    </details>
  );
}

/** River gauge, shown over the top of the map. */
function GaugeChip({ swarm, demo }: { swarm: Swarm; demo: boolean }) {
  const gauge = swarm.gauge;
  return (
    <div className="gauge-chip" title={gauge.site}>
      James River: {gauge.value !== undefined
        ? <><b>{gauge.value.toFixed(1)} ft {gauge.trend}</b> <span className="muted">({gauge.label})</span></>
        : <span className="muted">{demo ? "no reading (press Demo)" : "no gauge feed connected"}</span>}
    </div>
  );
}

/** Personnel: every unit, its status, and the orders dispatch has sent it. */
function PersonnelTab({ world }: { world: WorldState }) {
  const { units, broadcasts, note } = world.roster;
  return (
    <main className="tab-page">
      <p className="muted">{note}</p>
      <div className="roster">
        {units.map((unit) => (
          <article key={unit.id} className={`unit-card status-${unit.status.replace(/\s+/g, "-")}`}>
            <header>
              <i className={`dot kind-${unit.kind}`} /> <b>{unit.name}</b>
              <span className={`unit-status ${unit.status.replace(/\s+/g, "-")}`}>{unit.status}</span>
            </header>
            <div className="muted small">
              {unit.capabilities.join(", ")}{unit.capacity ? ` · carries up to ${unit.capacity}` : ""} · at {unit.location ?? "en route"}
              <span title={unit.position_source}> (simulated position)</span>
            </div>
            {unit.assignment && (
              <div className="unit-assignment">Assigned: <b>{unit.assignment.place ?? unit.assignment.incident_id}</b> · {unit.assignment.kind}</div>
            )}
            <div className="rec-label">Orders received ({unit.messages.length})</div>
            <ol className="feed">
              {unit.messages.map((m, i) => (
                <li key={`${m.plan_id}${i}`}>
                  <span className="t">[{time(m.at)}]</span> <span className="src">{m.plan_id} · {m.from}</span> {m.text}
                </li>
              ))}
              {!unit.messages.length && <li className="muted">No orders yet. Orders appear when a dispatcher approves a plan.</li>}
            </ol>
          </article>
        ))}
      </div>
      <h2>Public broadcasts</h2>
      {broadcasts.length ? broadcasts.map((b) => (
        <div key={b.plan_id} className="alert">
          <div className="rec-label">{b.plan_id} <Pill label={b.source} /></div>
          <p><b>EN</b> {b.english}</p>
          <p><b>ES</b> {b.spanish}</p>
        </div>
      )) : <p className="muted">No public alerts yet.</p>}
    </main>
  );
}

/** Agents: what each agent decided, and how the swarm argued each incident's severity. */
function AgentsTab({ world, queue, focus }: { world: WorldState; queue: Incident[]; focus: string | null }) {
  const agents = focus ? world.agents.filter((a) => a.name === focus) : world.agents;
  return (
    <main className="tab-page">
      <h2>Swarm discussion by incident</h2>
      <p className="muted">
        Each agent scores every incident independently (two AI estimates and one computed check); the consensus is the
        median pulled halfway toward the highest score. A dispatcher approves every plan.
      </p>
      <div className="discussion">
        {queue.map((incident) => {
          const entry = world.swarm.incidents[incident.id];
          return (
            <article key={incident.id} className={`card sev-${incident.severity}`}>
              <header>
                <b>{incident.place ?? incident.id}</b> · {incident.kind}
                <span className="muted"> · {incident.id}</span>
                {entry?.final != null && <span className="final"> = consensus <b>{entry.final}</b> est.{entry.agreement != null ? ` · ${Math.round(entry.agreement)}% agreement` : ""}</span>}
              </header>
              {AGENTS.map(([name, label]) => {
                const vote = entry?.votes[name];
                return (
                  <div key={name} className="argument">
                    <span className="argument-who">{label}</span>
                    <b className="argument-score">{vote ? vote.score : "…"}</b>
                    <span>
                      {vote ? vote.reasons.join(" · ") : "waiting"}
                      {vote && <span className="muted small"> ({vote.source})</span>}
                    </span>
                  </div>
                );
              })}
            </article>
          );
        })}
        {!queue.length && <p className="muted">No open incidents to discuss.</p>}
      </div>
      <h2>{focus ? focus : "All agents"}</h2>
      <div className="agent-grid">
        {agents.map((agent) => <AgentPanel key={agent.name} agent={agent} />)}
      </div>
      <h2>Swarm activity</h2>
      <ActivityFeed activity={world.swarm.activity} />
    </main>
  );
}

export default function CommandScreen() {
  const [world, setWorld] = useState<WorldState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [agentOpen, setAgentOpen] = useState<string | null>(null);
  const [tab, setTab] = useState<"map" | "personnel" | "agents">("map");
  const [layers, setLayers] = useState<Layers>(ALL_LAYERS);
  const [scrub, setScrub] = useState<Scrub>({ kind: "live" });
  const [frames, setFrames] = useState<GisFrame[]>([]);
  const loadHistory = useCallback(() => {
    api.gisHistory().then((h) => setFrames(h.frames)).catch(() => null);
  }, []);

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

  const demoRunning = Boolean(world?.demo?.running || world?.demo?.busy);
  const liveIntake = world ? !world.mode?.demo_enabled : false;
  useEffect(() => {
    // Poll while the demo plays, and always in live intake (reports arrive from outside).
    if (!demoRunning && !liveIntake) return;
    const timer = setInterval(() => refresh().catch(() => null), demoRunning ? 1500 : 3000);
    return () => clearInterval(timer);
  }, [demoRunning, liveIntake, refresh]);

  const queue = useMemo(() => (world ? rank(world.incidents, world.swarm) : []), [world]);
  // What the map shows: live state, a recorded frame (scrubbed back) or a projection hour.
  const pastFrame = scrub.kind === "past" ? frames[scrub.index] ?? null : null;
  const futureFrame = scrub.kind === "future" && world ? world.gis.projection.frames.find((f) => f.hours === scrub.hours) ?? null : null;
  const shownFrame: GisFrame | null = pastFrame ?? world?.gis ?? null;
  const mapData: MapData | null = useMemo(() => {
    if (!world) return null;
    const zones = (futureFrame?.zones ?? shownFrame?.zones ?? []).filter((z) => (z.type === "flood" ? layers.flood : layers.fire));
    const closed = new Set((shownFrame?.closed_roads ?? []).map((r) => r.id));
    const base: MapData = pastFrame
      ? {
          incidents: pastFrame.incidents.map((i) => ({
            id: i.id, kind: i.place ? `${i.place}: ${i.kind}` : i.kind, severity: i.severity as Incident["severity"],
            severity_level: i.severity_level, status: i.status as Incident["status"], position: i.position,
          })),
          services: pastFrame.units.map((u) => ({
            id: u.id, name: u.name, capabilities: [u.kind], available: u.available, position: u.position, route: null,
          })),
          roads: world.roads.map((r) => ({ ...r, is_closed: closed.has(r.id) })),
          annotations: world.annotations,
          proposedRoutes: [],
        }
      : {
          incidents: queue.map((incident) => ({ ...incident, kind: incident.place ? `${incident.place}: ${incident.kind}` : incident.kind })),
          services: world.services,
          roads: world.roads,
          annotations: world.annotations,
          proposedRoutes: !futureFrame && world.current_plan?.status === "proposed" ? world.current_plan.assignments : [],
          floodMasks: layers.flood && !futureFrame ? world.swarm.imagery.flatMap((image) => image.map_features ?? []) : [],
        };
    return {
      ...base,
      services: layers.units ? base.services : [],
      proposedRoutes: layers.units ? base.proposedRoutes : [],
      roads: layers.roads ? base.roads : base.roads.map((r) => ({ ...r, is_closed: false })),
      zones,
      outages: layers.power ? shownFrame?.outages ?? [] : [],
      facilities: layers.hospitals ? shownFrame?.facilities ?? [] : [],
    };
  }, [world, queue, layers, pastFrame, futureFrame, shownFrame]);

  // The → key is the presenter's ▶ (ignored while typing in a field).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key !== "ArrowRight" || target?.closest("input, textarea, select, [contenteditable]")) return;
      if (world?.demo?.can_step && !busy) {
        event.preventDefault();
        run(api.stepDemo);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [world?.demo?.can_step, busy, run]);

  // Keep the scrubber's history fresh while the map tab is open.
  useEffect(() => {
    if (tab !== "map") return;
    loadHistory();
    const timer = setInterval(loadHistory, 5000);
    return () => clearInterval(timer);
  }, [tab, loadHistory]);

  return (
    <div className="screen">
      <header className="nav">
        <div className="brand">
          <span className="brand-mark">◆</span> Crisis Command <span className="sim">SIMULATION</span>
        </div>
        {world && (
          <nav className="tabs" role="tablist">
            {([["map", "Operations map"], ["personnel", `Personnel (${world.roster.units.length})`], ["agents", "Agents & decisions"]] as const).map(([key, label]) => (
              <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "tab on" : "tab"} onClick={() => setTab(key)}>
                {label}
              </button>
            ))}
          </nav>
        )}
        <div className="nav-right">
          {world?.demo ? <DemoStepper demo={world.demo} busy={busy} run={run} /> : world && <LiveIntakeBadge mode={world.mode} />}
          <ReportLink />
          <span className="org">City of Richmond Operations</span>
        </div>
      </header>
      {error && <div className="banner">{error}</div>}

      {world && tab === "personnel" && <PersonnelTab world={world} />}
      {world && tab === "agents" && (
        <>
          <AgentStrip agents={world.agents} open={agentOpen} onOpen={setAgentOpen} />
          <AgentsTab world={world} queue={queue} focus={agentOpen} />
        </>
      )}

      {world && tab === "map" && (
        <main className="panels">
          <section className="panel left">
            <h2>Swarm Incident Queue</h2>
            {shownFrame && <AffectedCard frame={shownFrame} label={pastFrame ? "recorded" : "now"} />}
            {!world.demo && world.incidents.length === 0 && (
              <p className="empty">
                Waiting for reports: from the report page, <code>POST /api/intake</code>, or a database connector in
                <code> .env</code>. Each is read by the Intake Agent, scored by the swarm and planned; a dispatcher approves every plan.
              </p>
            )}
            <Transcripts swarm={world.swarm} />
            <PhotoCard swarm={world.swarm} />
            <IncidentQueue incidents={queue} focus={queue[0]?.id ?? null} swarm={world.swarm} busy={busy}
              onResolve={(id) => run(() => api.resolveIncident(id))} />
          </section>

          <section className="panel center">
            <div className="map-wrap">
              <CrisisMap data={mapData} basemap />
              <LayerControl layers={layers} onChange={setLayers} sources={world.gis.layer_sources} />
              <ScrubBanner scrub={scrub} frames={frames} projection={world.gis.projection} />
              <GaugeChip swarm={world.swarm} demo={!!world.demo} />
              {world.demo && scrub.kind === "live" && <DemoNote demo={world.demo} />}
            </div>
            <Scrubber frames={frames} projection={world.gis.projection} scrub={scrub} onScrub={setScrub} onOpen={loadHistory} />
            <VisionBadge swarm={world.swarm} />
            <div className="legend">
              <span><i className="ring open" /> Needs units</span>
              <span><i className="ring assigned" /> Dispatched</span>
              <span><i className="line proposed" /> Recommended route</span>
              <span><i className="line closed" /> Road closed</span>
              <span><i className="swatch swatch-flood" /> Flood (darker = more cover)</span>
              <span><i className="swatch swatch-fire" /> Fire area</span>
              <span><i className="dot outage" /> Outage reported</span>
              <span><i className="dot hospital" /> Hospital (beds: no feed)</span>
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
