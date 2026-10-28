"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import CrisisMap, { type MapData } from "@/components/CrisisMap";
import ReplayView from "@/components/ReplayView";
import TableView from "@/components/TableView";
import {
  IncidentList,
  IntakePanel,
  KpiStrip,
  ResourceList,
  ReviewPanel,
  Tile,
  Timeline,
} from "@/components/Panels";
import { ApiError, api } from "@/lib/api";
import type { WorldEvent, WorldState } from "@/lib/types";

const PLAY_INTERVAL_MS = 1500;
const VIEW_KEY = "crisis-command:view";
const BASEMAP_KEY = "crisis-command:basemap";
type View = "grid" | "table";

export default function Dashboard() {
  const [world, setWorld] = useState<WorldState | null>(null);
  const [events, setEvents] = useState<WorldEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [view, setView] = useState<View>("grid");
  const [mapExpanded, setMapExpanded] = useState(false);
  const [selectedRoadId, setSelectedRoadId] = useState<string | null>(null);
  const [mode, setMode] = useState<"live" | "replay">("live");
  const [basemap, setBasemap] = useState(false);

  const refresh = useCallback(async () => {
    const [state, log] = await Promise.all([api.state(), api.events()]);
    setWorld(state);
    setEvents(log);
    return state;
  }, []);

  const run = useCallback(
    async (action: () => Promise<unknown>, success?: string) => {
      setBusy(true);
      try {
        await action();
        setError(null);
        setNotice(success ?? null);
        return await refresh();
      } catch (caught) {
        setPlaying(false);
        if (caught instanceof ApiError && caught.replacementPlanId) {
          setNotice(`${caught.message}`);
          setError(null);
        } else {
          setError(caught instanceof Error ? caught.message : String(caught));
        }
        return await refresh().catch(() => null);
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  useEffect(() => {
    try {
      const saved = localStorage.getItem(VIEW_KEY);
      if (saved === "grid" || saved === "table") setView(saved);
      setBasemap(localStorage.getItem(BASEMAP_KEY) === "on");
    } catch {}
    refresh().catch((caught) =>
      setError(`Cannot reach the Crisis Command API (${caught instanceof Error ? caught.message : caught}). Start it with: python -m uvicorn crisis_command.api.server:app --port 8000`),
    );
  }, [refresh]);

  // The clock holds while a proposal with dispatches awaits review.
  const awaitingReview = Boolean(
    world?.current_plan?.status === "proposed" && world.current_plan.assignments.length,
  );
  useEffect(() => {
    if (!playing) return;
    if (awaitingReview || world?.all_resolved) {
      setPlaying(false);
      if (awaitingReview) setNotice("Simulation paused: a proposal is awaiting review.");
      return;
    }
    const timer = setTimeout(() => run(api.advance), PLAY_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [playing, awaitingReview, world, run]);

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

  const toggleBasemap = (on: boolean) => {
    setBasemap(on);
    try {
      localStorage.setItem(BASEMAP_KEY, on ? "on" : "off");
    } catch {}
  };

  const mapData: MapData | null = useMemo(
    () =>
      world && {
        incidents: world.incidents,
        services: world.services,
        roads: world.roads,
        annotations: world.annotations,
        proposedRoutes: world.current_plan?.status === "proposed" ? world.current_plan.assignments : [],
      },
    [world],
  );

  const selectedRoad = world?.roads.find((road) => road.id === selectedRoadId) ?? null;

  return (
    <div className="shell">
      <header className="topbar">
        <span className="sim-badge" title="All data and outcomes are simulated.">SIMULATION</span>
        <div className="brand">
          Crisis Command
          {world && <span className="muted"> · {world.scenario} · {world.run_id}</span>}
        </div>
        <div className="stats">
          <span>Tick {world?.tick ?? "–"}</span>
          <span>World v{world?.version ?? "–"}</span>
          <span className={world?.model === "unavailable" ? "error-text" : ""}>Model: {world?.model ?? "–"}</span>
          <span>Clock: {playing ? "running" : "paused"}</span>
          <span className={busy ? "busy" : ""}>{busy ? "Request in flight (planning/model calls)…" : "No requests in flight"}</span>
        </div>
        <div className="segmented" role="tablist" aria-label="Mode">
          <button className={mode === "live" ? "active" : ""} onClick={() => { setNotice(null); setMode("live"); }}>Live</button>
          <button className={mode === "replay" ? "active" : ""} onClick={() => { setPlaying(false); setNotice(null); setMode("replay"); }}>
            Replay
          </button>
        </div>
        <label className="check nowrap" title="Street tiles from OpenFreeMap; illustrative context only. The road graph works without them.">
          <input type="checkbox" checked={basemap} onChange={(e) => toggleBasemap(e.target.checked)} />
          Street basemap (illustrative)
        </label>
        {mode === "live" && (
        <div className="segmented" role="tablist" aria-label="Layout">
          <button className={view === "grid" ? "active" : ""} onClick={() => changeView("grid")}>Grid</button>
          <button className={view === "table" ? "active" : ""} onClick={() => changeView("table")}>Table</button>
        </div>
        )}
        {mode === "live" && (
        <div className="actions">
          {world?.disruptions.map((disruption) => (
            <button
              key={disruption.key}
              className="danger"
              title="Files the scripted report and confirms it through normal intake."
              disabled={busy || disruption.in_effect}
              onClick={() => run(() => api.injectDisruption(disruption.key), `${disruption.label}: confirmed through intake; plan recomputed.`)}
            >
              {disruption.label}
            </button>
          ))}
          <button disabled={!world || busy || playing || world.all_resolved} onClick={() => run(api.advance)}>
            Advance clock
          </button>
          <button disabled={!world || world.all_resolved} onClick={() => setPlaying((value) => !value)}>
            {playing ? "Pause" : "Run"}
          </button>
          <button
            className="ghost"
            disabled={busy}
            onClick={() => {
              setPlaying(false);
              setSelectedRoadId(null);
              run(api.reset, "Scenario reset to its seed; new run started.");
            }}
          >
            Reset
          </button>
        </div>
        )}
      </header>

      {error && <div className="banner error">{error}</div>}
      {notice && <div className="banner">{notice}</div>}

      {world && mode === "replay" && (
        <main className="content">
          <ReplayView basemap={basemap} currentRunId={world.run_id} annotations={world.annotations} />
        </main>
      )}

      {world && mode === "live" && (
        <main className="content">
          <KpiStrip world={world} />

          {view === "table" && <TableView world={world} events={events} />}

          {/* The map stays mounted in both views so it does not reload on toggle. */}
          <div className={view === "grid" ? "bento" : "bento hidden"}>
            <Tile
              title="Map"
              className={`t-map ${mapExpanded ? "expanded" : ""}`}
              actions={
                <button className="ghost small" onClick={() => setMapExpanded((value) => !value)}>
                  {mapExpanded ? "Close ✕" : "Expand ⤢"}
                </button>
              }
            >
              <div className="map-wrap">
                <CrisisMap
                  data={mapData}
                  basemap={basemap}
                  onBasemapError={() => {
                    toggleBasemap(false);
                    setNotice("Street basemap unavailable; showing the offline road graph.");
                  }}
                  selectedRoadId={selectedRoadId}
                  onSelectRoad={setSelectedRoadId}
                />
                {selectedRoad && (
                  <div className="map-overlay road-card">
                    <div className="row">
                      <div className="grow">
                        <div className="title">{selectedRoad.id}</div>
                        <div className="meta">{selectedRoad.is_closed ? "Closed" : "Open"} · length {selectedRoad.length.toFixed(2)}</div>
                      </div>
                      <button className="ghost small" onClick={() => setSelectedRoadId(null)} aria-label="Deselect road">×</button>
                    </div>
                    <p className="meta">Road changes go through intake: file a report, then confirm it.</p>
                    <button
                      className={selectedRoad.is_closed ? "small" : "small danger"}
                      disabled={busy}
                      onClick={() =>
                        run(
                          () =>
                            api.submitReport(
                              `Operator observed ${selectedRoad.id} ${selectedRoad.is_closed ? "reopened" : "closed"}.`,
                              "operator",
                              { report_type: "road_status", road_id: selectedRoad.id, road_closed: !selectedRoad.is_closed, certainty: "confirmed" },
                            ),
                          `Road report filed for ${selectedRoad.id}; confirm it in Intake.`,
                        )
                      }
                    >
                      File {selectedRoad.is_closed ? "reopening" : "closure"} report
                    </button>
                  </div>
                )}
                <div className="legend">
                  <span><i className="dot status-open" /> Open</span>
                  <span><i className="dot status-assigned" /> Assigned</span>
                  <span><i className="dot status-resolved" /> Resolved</span>
                  <span><i className="dot unit-busy" /> En route</span>
                  <span><i className="dot unit-idle" /> Available</span>
                  <span><i className="swatch closed" /> Closed road</span>
                  <span><i className="swatch committed" /> Committed route</span>
                  <span><i className="swatch proposed" /> Proposed route</span>
                  {basemap && <span className="muted">Basemap illustrative only</span>}
                </div>
              </div>
            </Tile>

            <Tile title="Review" className="t-review">
              <ReviewPanel
                plan={world.current_plan}
                worldVersion={world.version}
                priorityRule={world.priority_rule}
                busy={busy}
                onApprove={(plan) => run(() => api.approve(plan.id, plan.world_version), `${plan.id} approved and committed.`)}
                onReject={(plan, reason) => run(() => api.rejectPlan(plan.id, reason), `${plan.id} rejected.`)}
                onRecompute={() => run(api.propose)}
              />
            </Tile>

            <Tile
              title="Intake"
              count={world.reports.filter((r) => !["confirmed", "rejected"].includes(r.status)).length}
              className="t-intake"
            >
              <IntakePanel
                world={world}
                busy={busy}
                onSubmit={(content, source, fields) => run(() => api.submitReport(content, source, fields))}
                onClarify={(id, fields) => run(() => api.clarifyReport(id, fields))}
                onConfirm={(id) => run(() => api.confirmReport(id), `${id} confirmed; plan recomputed.`)}
                onReject={(id, reason) => run(() => api.rejectReport(id, reason))}
              />
            </Tile>

            <Tile title="Incidents" count={world.incidents.length} className="t-incidents">
              <IncidentList incidents={world.incidents} />
            </Tile>

            <Tile title="Resources" count={world.services.length} className="t-resources">
              <ResourceList services={world.services} />
            </Tile>

            <Tile title="Timeline" count={events.length} className="t-timeline">
              <Timeline events={events} />
            </Tile>
          </div>
        </main>
      )}
    </div>
  );
}
