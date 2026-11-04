"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type ReadAs = {
  type: string | null;
  kind: string | null;
  location: string | null;
  people: number | null;
  needs: string[];
  severity: string | null;
};

type Summary = {
  id?: string;
  status: string;
  error?: string | null;
  missing?: string[];
  incident_id?: string | null;
  road_id?: string | null;
  read_as?: ReadAs;
};

type Result = { transcript?: string | null; report: Summary; receipt?: string | null };

/** One of this phone's reports, as the hub tracks it. */
type MyReport = {
  receipt: string;
  id?: string;
  text?: string;
  received_at?: string;
  place?: string | null;
  stage: string;
  step: number;
  steps?: string[];
  label: string;
  units?: string[];
};

type Feed = {
  city: string;
  hazard: string;
  alerts: { plan_id: string; english: string; spanish: string; at: string | null; source: string }[];
  closed_roads: { name: string; evidence: { report_id: string | null; source: string; at: string | null } | null }[];
  gauge: { value?: number; trend?: string; label?: string; site?: string } | null;
  active_incidents: number;
  units_out: number;
  units_total: number;
};

type Tab = "alerts" | "report" | "mine";

const EXAMPLES = [
  "Family of four on the roof of a house on Hull St in Manchester, water up to the second floor.",
  "Lee Bridge is underwater, cars can't get across.",
  "Elderly woman in a wheelchair stuck in her apartment at Riverside Apartments, water coming in.",
];

const STORE = "crisis-command-receipts";
const STEPS = ["Received", "On the command screen", "Help dispatched", "Resolved"];

// Receipts live only in this browser; storage can be blocked, so every access is guarded.
function loadReceipts(): string[] {
  try {
    const raw = window.localStorage.getItem(STORE);
    return raw ? (JSON.parse(raw) as string[]).filter((r) => typeof r === "string") : [];
  } catch {
    return [];
  }
}

function saveReceipts(receipts: string[]) {
  try {
    window.localStorage.setItem(STORE, JSON.stringify(receipts.slice(0, 20)));
  } catch {
    // Not saved: the report still went through; it just won't be tracked after a reload.
  }
}

const clock = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";

/** Downsample float audio to 24 kHz 16-bit mono PCM, the format Muse Voice Transcribe expects. */
function toPcm24k(chunks: Float32Array[], rate: number): ArrayBuffer {
  const length = chunks.reduce((n, c) => n + c.length, 0);
  const input = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    input.set(chunk, offset);
    offset += chunk.length;
  }
  const ratio = rate / 24000;
  const out = new Int16Array(Math.floor(length / ratio));
  for (let i = 0; i < out.length; i++) {
    const position = i * ratio;
    const left = Math.floor(position);
    const right = Math.min(left + 1, length - 1);
    const sample = input[left] + (input[right] - input[left]) * (position - left);
    out[i] = Math.max(-32768, Math.min(32767, Math.round(sample * 32767)));
  }
  return out.buffer;
}

function Progress({ report }: { report: MyReport }) {
  const steps = report.steps ?? STEPS;
  const stopped = ["needs_detail", "received", "not_used", "closed"].includes(report.stage);
  return (
    <ol className="hub-steps" aria-label="Progress">
      {steps.map((step, index) => {
        const state = index < report.step ? "done" : index === report.step ? (stopped ? "stuck" : "now") : "todo";
        return (
          <li key={step} className={`hub-step ${state}`}>
            <span className="hub-dot" aria-hidden>{state === "done" ? "✓" : index + 1}</span>
            <span>{step}</span>
          </li>
        );
      })}
    </ol>
  );
}

function AlertsTab({ feed, spanish, onLanguage }: { feed: Feed | null; spanish: boolean; onLanguage: (es: boolean) => void }) {
  if (!feed) return <p className="muted">Loading…</p>;
  return (
    <>
      <div className="hub-row">
        <h2>Public alerts</h2>
        <div className="hub-lang" role="group" aria-label="Language">
          <button className={spanish ? "" : "on"} onClick={() => onLanguage(false)}>English</button>
          <button className={spanish ? "on" : ""} onClick={() => onLanguage(true)}>Español</button>
        </div>
      </div>
      {feed.alerts.length ? (
        <ul className="hub-list">
          {feed.alerts.map((alert, index) => (
            <li key={alert.plan_id} className={`hub-card ${index === 0 ? "hub-latest" : ""}`}>
              <div className="hub-meta">{index === 0 ? "Latest · " : ""}{clock(alert.at)} · written by {alert.source}, approved by a dispatcher</div>
              <p>{spanish ? alert.spanish : alert.english}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No public alerts yet. They appear here when a dispatcher approves a response.</p>
      )}

      <h2>Roads closed now</h2>
      {feed.closed_roads.length ? (
        <ul className="hub-list">
          {feed.closed_roads.map((road) => (
            <li key={road.name} className="hub-card hub-road">
              <b>{road.name}</b>
              <span className="hub-meta">
                {road.evidence ? `${road.evidence.report_id ? `${road.evidence.report_id} · ` : ""}${road.evidence.source}${road.evidence.at ? ` · ${clock(road.evidence.at)}` : ""}` : "source unknown"}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No roads reported closed.</p>
      )}
    </>
  );
}

function MineTab({ reports, onReport }: { reports: MyReport[]; onReport: () => void }) {
  if (!reports.length) {
    return (
      <div className="hub-empty">
        <p>You haven't sent a report from this phone yet.</p>
        <button className="approve" onClick={onReport}>Report something</button>
      </div>
    );
  }
  return (
    <ul className="hub-list">
      {reports.map((report) => (
        <li key={report.receipt} className={`hub-card hub-mine st-${report.stage}`}>
          <div className="hub-meta">
            {report.id ? `${report.id} · ` : ""}sent {clock(report.received_at)}{report.place ? ` · ${report.place}` : ""}
          </div>
          {report.text && <p className="hub-quote">“{report.text}”</p>}
          {report.stage !== "closed" && report.stage !== "road" && <Progress report={report} />}
          <p className="hub-status">{report.label}</p>
        </li>
      ))}
    </ul>
  );
}

export default function HubPage() {
  const [tab, setTab] = useState<Tab>("report");
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [feed, setFeed] = useState<Feed | null>(null);
  const [mine, setMine] = useState<MyReport[]>([]);
  const [receipts, setReceipts] = useState<string[]>([]);
  const [spanish, setSpanish] = useState(false);
  const capture = useRef<{ stop: () => Promise<ArrayBuffer> } | null>(null);

  const source = name.trim() ? `report page (${name.trim()})` : "public report page";
  const canRecord = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

  useEffect(() => {
    setReceipts(loadReceipts());
    try {
      setSpanish(window.localStorage.getItem("crisis-command-lang") === "es");
    } catch {
      // Language falls back to English.
    }
  }, []);

  const refresh = useCallback(async () => {
    const [feedResponse, mineResponse] = await Promise.all([
      fetch("/api/public/feed", { cache: "no-store" }),
      receipts.length ? fetch(`/api/public/reports?receipts=${receipts.join(",")}`, { cache: "no-store" }) : null,
    ]);
    if (feedResponse.ok) setFeed(await feedResponse.json());
    if (mineResponse?.ok) {
      const body = (await mineResponse.json()) as { reports: MyReport[] };
      const order = new Map(receipts.map((r, i) => [r, i]));
      setMine(body.reports.sort((a, b) => (order.get(a.receipt) ?? 0) - (order.get(b.receipt) ?? 0)));
    } else if (!receipts.length) {
      setMine([]);
    }
  }, [receipts]);

  useEffect(() => {
    refresh().catch(() => null);
    const timer = setInterval(() => refresh().catch(() => null), 4000);
    return () => clearInterval(timer);
  }, [refresh]);

  function chooseLanguage(es: boolean) {
    setSpanish(es);
    try {
      window.localStorage.setItem("crisis-command-lang", es ? "es" : "en");
    } catch {
      // Not remembered; the choice still applies now.
    }
  }

  async function send(request: () => Promise<Response>, label: string) {
    setBusy(label);
    setError(null);
    setResult(null);
    try {
      const response = await request();
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
      setResult(body);
      if (label === "Sending") setText("");
      if (body.receipt) {
        const next = [body.receipt as string, ...receipts.filter((r) => r !== body.receipt)];
        setReceipts(next);
        saveReceipts(next);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  function submitText() {
    if (text.trim().length < 3) return;
    send(
      () => fetch("/api/intake/text", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.trim(), source }),
      }),
      "Sending",
    );
  }

  async function startRecording() {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true } });
      const context = new AudioContext();
      const node = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4096, 1, 1);
      const chunks: Float32Array[] = [];
      processor.onaudioprocess = (event) => chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      node.connect(processor);
      processor.connect(context.destination);
      capture.current = {
        stop: async () => {
          processor.disconnect();
          node.disconnect();
          stream.getTracks().forEach((track) => track.stop());
          const rate = context.sampleRate;
          await context.close();
          return toPcm24k(chunks, rate);
        },
      };
      setRecording(true);
    } catch (e) {
      setError(`Microphone unavailable: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function stopRecording() {
    const current = capture.current;
    capture.current = null;
    setRecording(false);
    if (!current) return;
    const pcm = await current.stop();
    send(
      () => fetch(`/api/intake/audio?source=${encodeURIComponent(`${source} (voice)`)}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: pcm,
      }),
      "Transcribing",
    );
  }

  const report = result?.report;
  const latest = feed?.alerts[0];
  const open = mine.filter((r) => !["resolved", "closed", "road", "not_used"].includes(r.stage)).length;
  return (
    <main className="report-page hub">
      <header className="hub-head">
        <span className="sim">SIMULATION</span>
        <h1>{feed ? `${feed.city} ${feed.hazard[0].toUpperCase()}${feed.hazard.slice(1)} Help Hub` : "Help Hub"}</h1>
        <p className="muted small">Emergency exercise, not a live service. In a real emergency, call 911.</p>
      </header>

      {feed && (
        <div className="hub-strip">
          {feed.gauge?.value != null && (
            <span title={feed.gauge.label}>River <b>{feed.gauge.value} ft</b> {feed.gauge.trend ?? ""} <span className="muted">(scenario value)</span></span>
          )}
          <span><b>{feed.active_incidents}</b> active calls</span>
          <span><b>{feed.units_out}</b> of {feed.units_total} crews out</span>
          <span><b>{feed.closed_roads.length}</b> roads closed</span>
        </div>
      )}

      {latest && tab !== "alerts" && (
        <button className="hub-banner" onClick={() => setTab("alerts")}>
          <b>Alert {clock(latest.at)}:</b> {(spanish ? latest.spanish : latest.english).slice(0, 140)}
          {(spanish ? latest.spanish : latest.english).length > 140 ? "…" : ""} <span className="hub-more">More</span>
        </button>
      )}

      <nav className="hub-tabs" role="tablist">
        {([["alerts", `Alerts${feed?.alerts.length ? ` (${feed.alerts.length})` : ""}`], ["report", "Report"],
           ["mine", `My reports${mine.length ? ` (${open} open)` : ""}`]] as const).map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "on" : ""} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </nav>

      {tab === "alerts" && <AlertsTab feed={feed} spanish={spanish} onLanguage={chooseLanguage} />}
      {tab === "mine" && <MineTab reports={mine} onReport={() => setTab("report")} />}

      {tab === "report" && (
        <>
          <p className="muted">
            Your report goes to the Crisis Command screen: the Intake Agent reads it, the agents score it, and the
            planner includes it. A person approves every dispatch. You can follow it under <b>My reports</b>.
          </p>

          <label className="report-field">
            Your name or role (optional)
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Officer Diaz, resident" maxLength={60} />
          </label>

          <label className="report-field">
            What is happening, and where?
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={5}
              maxLength={2000}
              placeholder="Who needs help, how many people, the street or landmark, and what you can see."
            />
          </label>
          <div className="report-examples">
            Try:{" "}
            {EXAMPLES.map((example) => (
              <button key={example} className="link" onClick={() => setText(example)}>{example.split(",")[0]}</button>
            ))}
          </div>

          <div className="report-actions">
            <button className="approve" disabled={!!busy || recording || text.trim().length < 3} onClick={submitText}>
              {busy === "Sending" ? "Sending…" : "Send report"}
            </button>
            {canRecord ? (
              <button className={recording ? "start pause" : "start"} disabled={!!busy} onClick={recording ? stopRecording : startRecording}>
                {recording ? "■ Stop and send" : busy === "Transcribing" ? "Transcribing…" : "🎤 Speak a report"}
              </button>
            ) : (
              <span className="muted small">Voice needs this page opened on the computer (localhost) or over https.</span>
            )}
          </div>
          {recording && <p className="recording">● Recording. Say what is happening and where, then press Stop.</p>}

          {error && <p className="report-error">{error}</p>}

          {result && (
            <section className="report-result">
              {result.transcript && <p><b>Muse Voice Transcribe heard:</b> “{result.transcript}”</p>}
              {report?.status === "not_read" && <p className="report-error">The recording could not be transcribed. Try again or type it.</p>}
              {report?.id && (
                <>
                  <p>
                    <b>{report.id}</b> received:{" "}
                    {report.error ? <span className="report-error">could not be read ({report.error})</span>
                      : report.status === "confirmed" ? "on the command screen now; the agents are scoring it and a dispatcher will approve the plan"
                      : report.status === "needs_clarification" ? "needs more detail before it can be used"
                      : report.status}
                  </p>
                  {report.read_as && !report.error && (
                    <ul className="muted small">
                      <li>Read as: {report.read_as.type === "road_status" ? "road status" : report.read_as.kind ?? "unknown type"}</li>
                      <li>Where: {report.read_as.location ?? "not understood"}</li>
                      {report.read_as.type !== "road_status" && <li>People: {report.read_as.people ?? "not stated"}</li>}
                      {report.read_as.needs.length > 0 && <li>Needs: {report.read_as.needs.join(", ")}</li>}
                    </ul>
                  )}
                  {!!report.missing?.length && (
                    <p className="report-error">Missing: {report.missing.join(", ")}. Send another report with those details.</p>
                  )}
                  {result.receipt && (
                    <button className="link hub-follow" onClick={() => setTab("mine")}>Follow this report under My reports →</button>
                  )}
                </>
              )}
            </section>
          )}
        </>
      )}
    </main>
  );
}
