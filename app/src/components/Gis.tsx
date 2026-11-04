"use client";

import type { GisFrame, Projection } from "@/lib/types";

export const LAYERS = [
  ["flood", "Flood zones"],
  ["projection", "Projected flood (trend)"],
  ["fire", "Fire areas"],
  ["units", "Units (simulated GPS)"],
  ["roads", "Closed roads"],
  ["power", "Power outages"],
  ["hospitals", "Hospitals & shelters"],
] as const;

export type LayerKey = (typeof LAYERS)[number][0];
export type Layers = Record<LayerKey, boolean>;
export const ALL_LAYERS: Layers = { flood: true, projection: true, fire: true, units: true, roads: true, power: true, hospitals: true };

/** Where the map is in time: live, a recorded frame, or a projection hour. */
export type Scrub = { kind: "live" } | { kind: "past"; index: number } | { kind: "future"; hours: number };

const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function since(iso: string) {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes} min ago` : `${Math.floor(minutes / 60)} h ${minutes % 60} min ago`;
}

export function LayerControl({ layers, onChange, sources }: {
  layers: Layers;
  onChange: (layers: Layers) => void;
  sources: Record<string, string>;
}) {
  return (
    <details className="layers">
      <summary>Layers ({LAYERS.filter(([key]) => layers[key]).length}/{LAYERS.length})</summary>
      {LAYERS.map(([key, label]) => (
        <label key={key} title={sources[key === "hospitals" ? "hospitals" : key] ?? ""}>
          <input type="checkbox" checked={layers[key]} onChange={(e) => onChange({ ...layers, [key]: e.target.checked })} />
          <i className={`swatch swatch-${key}`} /> {label}
        </label>
      ))}
    </details>
  );
}

/** One slider: recorded frames on the left, LIVE in the middle, +1..+4 h projection on the right. */
export function Scrubber({ frames, projection, scrub, onScrub, onOpen }: {
  frames: GisFrame[];
  projection: Projection;
  scrub: Scrub;
  onScrub: (scrub: Scrub) => void;
  onOpen: () => void;
}) {
  const past = frames.length;
  const future = projection.available ? projection.frames.length : 0;
  const value = scrub.kind === "live" ? past : scrub.kind === "past" ? scrub.index : past + scrub.hours;
  const toScrub = (position: number): Scrub =>
    position < past ? { kind: "past", index: position } : position === past ? { kind: "live" } : { kind: "future", hours: position - past };
  return (
    <div className="scrubber">
      <div className="scrub-ends">
        <span>{past ? `◀ ${clock(frames[0].at)} (${since(frames[0].at)})` : "◀ no recorded history yet"}</span>
        <button className={scrub.kind === "live" ? "live-btn on" : "live-btn"} onClick={() => onScrub({ kind: "live" })}>● LIVE</button>
        <span>{future ? `+${future} h trend projection ▶` : "no projection yet ▶"}</span>
      </div>
      <input
        type="range"
        min={0}
        max={past + future}
        step={1}
        value={value}
        onPointerDown={onOpen}
        onFocus={onOpen}
        onChange={(e) => onScrub(toScrub(Number(e.target.value)))}
        style={{ ["--live" as string]: `${((past) / Math.max(past + future, 1)) * 100}%` }}
        aria-label="Timeline: recorded history to the left of LIVE, projection to the right"
      />
    </div>
  );
}

export function ScrubBanner({ scrub, frames, projection }: { scrub: Scrub; frames: GisFrame[]; projection: Projection }) {
  if (scrub.kind === "live") return null;
  if (scrub.kind === "past") {
    const frame = frames[scrub.index];
    if (!frame) return null;
    return (
      <div className="scrub-banner past">
        ⏪ Recorded state at <b>{clock(frame.at)}</b> ({since(frame.at)}). Map shows what was known then.
      </div>
    );
  }
  const frame = projection.frames.find((f) => f.hours === scrub.hours);
  if (!frame) return null;
  return (
    <div className="scrub-banner future" title={projection.note}>
      ⏩ <b>PROJECTION +{frame.hours} h</b>: gauge {frame.gauge_ft.toFixed(1)} ft
      {frame.above_trigger && projection.trigger_ft ? ` (above ${projection.trigger_ft} ft trigger)` : ""} · dashed = projected
      flood · <u>trend, not a forecast</u> <span className="muted">(hover for method)</span>
    </div>
  );
}

export function AffectedCard({ frame, label }: { frame: GisFrame; label: string }) {
  const { people_reported, incidents, incidents_without_count } = frame.affected;
  const listed = frame.incidents.filter((i) => i.people);
  return (
    <div className="affected">
      <div className="rec-label">People affected · {label}</div>
      <div className="affected-big">
        <b>{people_reported}</b> people reported at risk
        <span className="muted"> across {incidents} open incident{incidents === 1 ? "" : "s"}</span>
      </div>
      {incidents_without_count > 0 && (
        <div className="muted small">
          {incidents_without_count} incident{incidents_without_count === 1 ? " has" : "s have"} no head count reported; the true total may be higher.
        </div>
      )}
      {listed.length > 0 && (
        <ul className="affected-list">
          {listed.map((i) => (
            <li key={i.id}><b>{i.people}</b> · {i.place ?? i.id} <span className="muted">({i.kind}, {i.status})</span></li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Always on screen: where the gauge trend points over the next hours, and which hour the
 *  dashed "projected flood" layer shows. Says why when there is no projection yet. */
export function ProjectionCard({ projection, horizon, onHorizon, shown }: {
  projection: Projection;
  horizon: number;
  onHorizon: (hours: number) => void;
  shown: boolean;
}) {
  if (!projection.available) {
    return (
      <div className="projection" title={projection.note}>
        <span className="rec-label">Flood trend projection</span>
        <span className="muted">{projection.reason ?? "No projection yet."}</span>
      </div>
    );
  }
  const rate = projection.rate_ft_per_h ?? 0;
  return (
    <div className="projection" title={projection.note}>
      <span className="rec-label">Flood trend projection</span>
      <span className="muted">
        {rate > 0 ? "rising" : rate < 0 ? "falling" : "steady"} {Math.abs(rate).toFixed(2)} ft/h · trend, not a forecast
      </span>
      <div className="projection-hours" role="group" aria-label="Projection hour shown on the map">
        {projection.frames.map((frame) => (
          <button
            key={frame.hours}
            className={`${frame.hours === horizon ? "on" : ""} ${frame.above_trigger ? "over" : ""}`}
            onClick={() => onHorizon(frame.hours)}
            title={frame.above_trigger && projection.trigger_ft ? `Above the ${projection.trigger_ft} ft sample trigger` : undefined}
          >
            +{frame.hours} h <b>{frame.gauge_ft.toFixed(1)} ft</b>{frame.above_trigger ? " ⚠" : ""}
          </button>
        ))}
      </div>
      <span className="muted small">{shown ? `Dashed on the map: +${horizon} h` : "Turn on “Projected flood” in Layers to see it on the map"}</span>
    </div>
  );
}