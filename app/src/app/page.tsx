"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import CrisisMap from "@/components/CrisisMap";
import TableView from "@/components/TableView";
import {
  AgentChat,
  AlertBar,
  AttentionPanel,
  FieldComms,
  UnitActions,
} from "@/components/Operations";
import { AgentFeed, EmergencyFeed, KpiStrip, RoadControl, Tile } from "@/components/Panels";
import { api } from "@/lib/api";
import { mockAgentReports, mockEvents, mockWorld, reportsFromWorld } from "@/lib/mock";
import { useOperations } from "@/lib/operations";
import type { WorldEvent, WorldState } from "@/lib/types";

const PLAY_INTERVAL_MS = 1200;
const VIEW_KEY = "crisis-command:view";
type View = "grid" | "table";

export default function Dashboard() {
  const [world, setWorld] = useState<WorldState | null>(null);
  const [events, setEvents] = useState<WorldEvent[]>([]);
  const [mode, setMode] = useState<"live" | "mock" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [view, setView] = useState<View>("grid");
  const [mapExpanded, setMapExpanded] = useState(false);
  const [selectedRoadId, setSelectedRoadId] = useState<string | null>(null);
  const [commsTarget, setCommsTarget] = useState("");

  const run = useCallback(async (action: () => Promise<WorldState>) => {
    setBusy(true);
    try {
      const next = await action();
      setWorld(next);
      setEvents(await api.events(60));
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
    try {
      const saved = localStorage.getItem(VIEW_KEY);
      if (saved === "grid" || saved === "table") setView(saved);
    } catch {}
    // Fall back to mock data when the Python API is not running.
    api.state()
      .then(async (state) => {
        setWorld(state);
        setEvents(await api.events(60));
        setMode("live");
      })
      .catch(() => {
        setWorld(mockWorld);
        setEvents(mockEvents);
        setMode("mock");
      });
  }, []);

  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(async () => {
      const next = await run(api.step);
      if (!next || next.all_resolved || next.notice) setPlaying(false);
    }, PLAY_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [playing, run]);

  useEffect(() => {
    if (!mapExpanded) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setMapExpanded(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mapExpanded]);

  const changeView = (next: View) => {
    setView(next);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {}
  };

  const reports = useMemo(
    () => (mode === "mock" ? mockAgentReports : world ? reportsFromWorld(world) : []),
    [mode, world],
  );
  const ops = useOperations(world, setWorld);
  const { acknowledged, acknowledge } = ops;
  const pendingReviews = reports.filter(
    (report) => report.type === "needs_review" && !acknowledged.has(report.id),
  ).length;

  const live = mode === "live";
  // Manager dispatch only updates the local mock world until the API has an approval gate.
  const guardMock = <T extends unknown[]>(action: (...args: T) => void) =>
    (...args: T) =>
      live ? setError("Manual dispatch and approvals are not in the API yet; mock mode only.") : action(...args);
  const selectedRoad = world?.roads.find((road) => road.id === selectedRoadId) ?? null;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          Crisis Command
          {world?.city && <span className="muted"> · {world.city}</span>}
        </div>
        {mode === "mock" && <span className="mode-badge">Mock data · API offline</span>}
        <div className="stats">
          <span>Tick {world?.tick ?? "–"}</span>
          <span>World v{world?.version ?? "–"}</span>
        </div>
        <div className="segmented" role="tablist" aria-label="Layout">
          <button className={view === "grid" ? "active" : ""} onClick={() => changeView("grid")}>
            Grid
          </button>
          <button className={view === "table" ? "active" : ""} onClick={() => changeView("table")}>
            Table
          </button>
        </div>
        <div className="actions">
          <button disabled={!live || busy || playing || world?.all_resolved} onClick={() => run(api.step)}>
            Step
          </button>
          <button disabled={!live || world?.all_resolved} onClick={() => setPlaying((value) => !value)}>
            {playing ? "Pause" : "Run"}
          </button>
          <button
            className="ghost"
            disabled={!live || busy}
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

      {(error || world?.notice) && (
        <div className={`banner ${error ? "error" : ""}`}>
          {error ? `API error: ${error}` : world?.notice}
        </div>
      )}

      <AlertBar alerts={ops.alerts} onAcknowledge={(id) => acknowledge(`incident:${id}`)} />

      {world && (
        <main className="content">
          <KpiStrip world={world} pendingReviews={pendingReviews} />

          {view === "table" ? (
            <TableView
              world={world}
              reports={reports}
              events={events}
              acknowledged={acknowledged}
              onAcknowledge={acknowledge}
            />
          ) : null}

          {/* The map stays mounted in both views so it does not reload on toggle. */}
          <div className={view === "grid" ? "bento" : "bento hidden"}>
            <Tile
              title="Operations map"
              className={`t-map ${mapExpanded ? "expanded" : ""}`}
              actions={
                <button className="ghost small" onClick={() => setMapExpanded((value) => !value)}>
                  {mapExpanded ? "Close ✕" : "Expand ⤢"}
                </button>
              }
            >
              <div className="map-wrap">
                <CrisisMap world={world} selectedRoadId={selectedRoadId} onSelectRoad={setSelectedRoadId} />
                {selectedRoad && (
                  <div className="map-overlay">
                    <RoadControl
                      key={selectedRoad.id}
                      road={selectedRoad}
                      busy={busy}
                      disabled={!live}
                      onClose={() => setSelectedRoadId(null)}
                      onSubmit={(isClosed, reason) =>
                        run(() => api.setRoadClosed(selectedRoad.id, isClosed, reason))
                      }
                    />
                  </div>
                )}
                <div className="legend">
                  <span><i className="dot status-open" /> Open</span>
                  <span><i className="dot status-assigned" /> Assigned</span>
                  <span><i className="dot status-resolved" /> Resolved</span>
                  <span><i className="dot unit-busy" /> Unit en route</span>
                  <span><i className="dot unit-idle" /> Unit available</span>
                  <span><i className="swatch closed" /> Closed road</span>
                </div>
              </div>
            </Tile>

            <Tile title="Needs attention" className="t-attention">
              <AttentionPanel
                world={world}
                proposals={ops.proposals}
                reports={reports}
                acknowledged={acknowledged}
                onApprove={guardMock(ops.approve)}
                onReject={ops.reject}
                onAcknowledge={acknowledge}
              />
            </Tile>

            <Tile title="Emergencies" count={world.incidents.length} className="t-emergencies">
              <EmergencyFeed incidents={world.incidents} tick={world.tick} />
            </Tile>

            <Tile
              title="Agent reports"
              count={reports.filter((report) => !acknowledged.has(report.id)).length}
              className="t-agents"
            >
              <AgentFeed reports={reports} acknowledged={acknowledged} onAcknowledge={acknowledge} />
            </Tile>

            <Tile title="Units" count={world.services.length} className="t-units">
              <UnitActions
                services={world.services}
                incidents={world.incidents}
                onDispatch={guardMock(ops.dispatch)}
                onMessage={setCommsTarget}
              />
            </Tile>

            <Tile title="Ask the agents" className="t-chat">
              <AgentChat chat={ops.chat} onAsk={ops.ask} />
            </Tile>

            <Tile title="Field comms" className="t-comms">
              <FieldComms
                services={world.services}
                comms={ops.comms}
                target={commsTarget}
                onTarget={setCommsTarget}
                onSend={ops.sendMessage}
              />
            </Tile>
          </div>
        </main>
      )}
    </div>
  );
}
