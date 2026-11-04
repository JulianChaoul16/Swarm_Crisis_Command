"use client";

import { useRef, useState } from "react";

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

type Result = { transcript?: string | null; report: Summary };

const EXAMPLES = [
  "Family of four on the roof of a house on Hull St in Manchester, water up to the second floor.",
  "Lee Bridge is underwater, cars can't get across.",
  "Elderly woman in a wheelchair stuck in her apartment at Riverside Apartments, water coming in.",
];

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

export default function ReportPage() {
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const capture = useRef<{ stop: () => Promise<ArrayBuffer> } | null>(null);

  const source = name.trim() ? `report page (${name.trim()})` : "public report page";
  const canRecord = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

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
  return (
    <main className="report-page">
      <span className="sim">SIMULATION</span>
      <h1>Report an incident</h1>
      <p className="muted">
        Richmond flood exercise. Your report goes to the Crisis Command screen: the Intake Agent reads it, the
        agents score it, and the planner includes it. A person still approves every dispatch.
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
                  {report.incident_id && <li>Incident: {report.incident_id}</li>}
                  {report.road_id && <li>Road: {report.road_id}</li>}
                </ul>
              )}
              {!!report.missing?.length && (
                <p className="report-error">Missing: {report.missing.join(", ")}. Send another report with those details.</p>
              )}
            </>
          )}
        </section>
      )}
    </main>
  );
}
