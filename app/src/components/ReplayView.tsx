"use client";

import { useEffect, useMemo, useState } from "react";
import CrisisMap, { type MapData } from "./CrisisMap";
import { label, time } from "./Panels";
import { api } from "@/lib/api";
import type { ReplayData, RunSummary } from "@/lib/types";

const REPLAY_INTERVAL_MS = 700;

export default function ReplayView({ basemap, currentRunId, annotations }: {
  basemap: boolean;
  currentRunId: string;
  annotations: MapData["annotations"];
}) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runId, setRunId] = useState<string>("");
  const [replay, setReplay] = useState<ReplayData | null>(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.runs()
      .then((list) => {
        setRuns(list);
        const pick = list.find((run) => run.id !== currentRunId && run.event_count > 5) ?? list[0];
        if (pick) setRunId(pick.id);
      })
      .catch((caught) => setError(String(caught)));
  }, [currentRunId]);

  useEffect(() => {
    if (!runId) return;
    setPlaying(false);
    setCursor(0);
    api.replay(runId).then(setReplay).catch((caught) => setError(String(caught)));
  }, [runId]);

  useEffect(() => {
    if (!playing || !replay) return;
    if (cursor >= replay.events.length - 1) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => setCursor((value) => value + 1), REPLAY_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [playing, cursor, replay]);

  const event = replay?.events[cursor];
  const snapshot = useMemo(() => {
    if (!replay || !event) return null;
    return [...replay.snapshots].reverse().find((item) => item.version <= event.version) ?? replay.snapshots[0] ?? null;
  }, [replay, event]);
  const mapData: MapData | null = snapshot ? { ...snapshot, annotations } : null;

  return (
    <div className="replay">
      <div className="replay-banner" role="status">
        RECORDED REPLAY · read back from the SQLite run log · no live inference or planning
      </div>
      {error && <div className="banner error">{error}</div>}
      <div className="replay-controls">
        <select value={runId} onChange={(e) => setRunId(e.target.value)} aria-label="Recorded run">
          {runs.map((run) => (
            <option key={run.id} value={run.id}>
              {run.id} · {run.scenario} · {run.event_count} events · {time(run.started_at)}
              {run.id === currentRunId ? " (current live run)" : ""}
            </option>
          ))}
        </select>
        <button className="ghost small" disabled={!replay} onClick={() => setCursor(0)}>⏮</button>
        <button className="small" disabled={!replay} onClick={() => setPlaying((value) => !value)}>
          {playing ? "Pause replay" : "Play replay"}
        </button>
        <input
          type="range"
          min={0}
          max={Math.max(0, (replay?.events.length ?? 1) - 1)}
          value={cursor}
          onChange={(e) => setCursor(Number(e.target.value))}
          aria-label="Replay position"
        />
        <span className="meta nowrap">
          {replay ? `${cursor + 1}/${replay.events.length}` : "–"} · world v{snapshot?.version ?? "–"}
        </span>
      </div>

      <div className="replay-body">
        <div className="tile replay-map">
          <div className="map-wrap">
            <CrisisMap data={mapData} basemap={basemap} />
            {!snapshot?.roads.length && replay && (
              <div className="map-overlay road-card">
                <span className="meta">Road geometry unavailable: this run used a different scenario.</span>
              </div>
            )}
          </div>
        </div>
        <ol className="tile replay-events events">
          {replay?.events.map((item, index) => (
            <li
              key={item.sequence}
              className={`${index === cursor ? "current" : ""} ${index > cursor ? "future" : ""}`}
              onClick={() => setCursor(index)}
            >
              <div className="row">
                <span className="kind">{label(item.kind)}</span>
                <span className="muted">{item.actor} · v{item.version} · {time(item.recorded_at)}</span>
              </div>
              <div className="meta">
                {item.entity_ids.length > 0 && <strong>{item.entity_ids.join(", ")} </strong>}
                {item.reason}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
