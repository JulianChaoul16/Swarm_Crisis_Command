"use client";

import { useCallback, useEffect, useState } from "react";
import CrisisMap from "@/components/CrisisMap";
import {
  EventLog,
  IncidentList,
  PlanSummary,
  RoadControl,
  Section,
  UnitList,
} from "@/components/Panels";
import { api } from "@/lib/api";
import type { WorldEvent, WorldState } from "@/lib/types";

const PLAY_INTERVAL_MS = 1200;

export default function Dashboard() {
  const [world, setWorld] = useState<WorldState | null>(null);
  const [events, setEvents] = useState<WorldEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [selectedRoadId, setSelectedRoadId] = useState<string | null>(null);

  const run = useCallback(async (action: () => Promise<WorldState>) => {
    setBusy(true);
    try {
      const next = await action();
      setWorld(next);
      setEvents(await api.events(40));
      setError(null);
      return next;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setPlaying(false);
      return null;
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    run(api.state);
  }, [run]);

  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(async () => {
      const next = await run(api.step);
      if (!next || next.all_resolved || next.notice) setPlaying(false);
    }, PLAY_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [playing, run]);

  const selectedRoad = world?.roads.find((road) => road.id === selectedRoadId) ?? null;
  const openCount = world?.incidents.filter((incident) => incident.status !== "resolved").length;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          Crisis Command
          {world?.city && <span className="muted"> · {world.city}</span>}
        </div>
        <div className="stats">
          <span>Tick {world?.tick ?? "–"}</span>
          <span>World v{world?.version ?? "–"}</span>
          <span>{openCount ?? "–"} active</span>
        </div>
        <div className="actions">
          <button disabled={busy || playing || world?.all_resolved} onClick={() => run(api.step)}>
            Step
          </button>
          <button
            disabled={!world || world.all_resolved}
            onClick={() => setPlaying((value) => !value)}
          >
            {playing ? "Pause" : "Run"}
          </button>
          <button
            className="ghost"
            disabled={busy}
            onClick={() => {
              setPlaying(false);
              setSelectedRoadId(null);
              run(api.reset);
            }}
          >
            Reset
          </button>
        </div>
      </header>

      <main className="body">
        <div className="map-wrap">
          <CrisisMap world={world} selectedRoadId={selectedRoadId} onSelectRoad={setSelectedRoadId} />
          <div className="legend">
            <span><i className="dot status-open" /> Open</span>
            <span><i className="dot status-assigned" /> Assigned</span>
            <span><i className="dot status-resolved" /> Resolved</span>
            <span><i className="dot unit-busy" /> Unit en route</span>
            <span><i className="swatch closed" /> Closed road</span>
            <span className="muted">Click a road to close or reopen it</span>
          </div>
        </div>

        <aside className="sidebar">
          {error && <div className="banner error">API error: {error}</div>}
          {world?.notice && <div className="banner">{world.notice}</div>}
          {world?.all_resolved && <div className="banner ok">All incidents resolved.</div>}

          {selectedRoad && (
            <RoadControl
              key={selectedRoad.id}
              road={selectedRoad}
              busy={busy}
              onClose={() => setSelectedRoadId(null)}
              onSubmit={(isClosed, reason) =>
                run(() => api.setRoadClosed(selectedRoad.id, isClosed, reason))
              }
            />
          )}

          <Section title="Incidents" count={world?.incidents.length}>
            <IncidentList incidents={world?.incidents ?? []} />
          </Section>
          <Section title="Units" count={world?.services.length}>
            <UnitList services={world?.services ?? []} />
          </Section>
          <Section title="Latest plan">
            <PlanSummary plan={world?.last_plan ?? null} />
          </Section>
          <Section title="Event log">
            <EventLog events={events} />
          </Section>
        </aside>
      </main>
    </div>
  );
}
