"use client";

import { useEffect, useRef, useState } from "react";
import type { GeoJSONSource, Map as MapLibreMap, Marker, StyleSpecification } from "maplibre-gl";
import type { GisFrame, Incident, LngLat, Road, Service, Zone } from "@/lib/types";

// Without network access the road graph renders on a plain background. With it, the
// basemap is Esri World Imagery satellite tiles of Richmond, dimmed so overlays read.
const OFFLINE_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#0d1424" } }],
};
const BASEMAP_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    satellite: {
      type: "raster",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
      maxzoom: 19,
      attribution: "Imagery © Esri, Maxar, Earthstar Geographics · Streets © OpenStreetMap contributors",
    },
  },
  layers: [
    { id: "background", type: "background", paint: { "background-color": "#0d1424" } },
    { id: "satellite", type: "raster", source: "satellite", paint: { "raster-brightness-max": 0.7, "raster-saturation": -0.2 } },
  ],
};
const OWN = "cc-";
const LABEL_MAX = 24;
const shorten = (text: string) => (text.length > LABEL_MAX ? `${text.slice(0, LABEL_MAX - 1)}…` : text);

// Unit colors by service type, shared with the legend.
export const UNIT_COLORS: Record<string, string> = {
  fire: "#ef4444",
  police: "#3b82f6",
  medical: "#22c55e",
  rescue: "#f97316",
  utility: "#eab308",
};
const unitKind = (capabilities: string[]) => capabilities.find((c) => c in UNIT_COLORS) ?? "other";
const OUT_OF_SERVICE = "#64748b";
const KIND_COLOR = ["match", ["get", "kind"], ...Object.entries(UNIT_COLORS).flat(), "out", OUT_OF_SERVICE, "#cbd5e1"] as unknown as string;

// Dispatched units drive their route on the map. The pace is time-compressed for display;
// arrival times in the plan come from the planner, not from this animation.
const DISPLAY_METRES_PER_SECOND = 140;
const ZONE_COLOR = [
  "match", ["get", "type"],
  "fire", ["match", ["get", "level"], "critical", "#dc2626", "high", "#ea580c", "#f59e0b"],
  ["match", ["get", "level"], "severe", "#1d4ed8", "moderate", "#3b82f6", "#93c5fd"],
] as unknown as string;

export type MapData = {
  incidents: Pick<Incident, "id" | "kind" | "severity" | "severity_level" | "status" | "position">[];
  services: (Pick<Service, "id" | "name" | "capabilities" | "available" | "position" | "route"> & { out_of_service?: boolean })[];
  roads: Pick<Road, "id" | "is_closed" | "path">[];
  proposedRoutes?: { service_id: string; route: LngLat[] }[];
  annotations?: { label: string; path: LngLat[] }[];
  // Flood-water masks (GeoJSON features) drawn as a blue overlay.
  floodMasks?: GeoJSON.Feature[];
  // Affected-area zones (flood by percent, fire by severity); projected ones are dashed.
  zones?: Zone[];
  outages?: GisFrame["outages"];
  facilities?: GisFrame["facilities"];
};

type Props = {
  data: MapData | null;
  basemap: boolean;
  onBasemapError?: () => void;
  selectedRoadId?: string | null;
  onSelectRoad?: (roadId: string | null) => void;
};

type Collection = GeoJSON.FeatureCollection;
type Trip = { key: string; route: LngLat[]; totals: number[]; started: number };

const line = (coordinates: LngLat[], properties: Record<string, unknown>): GeoJSON.Feature => ({
  type: "Feature",
  properties,
  geometry: { type: "LineString", coordinates },
});
const point = (coordinates: LngLat, properties: Record<string, unknown>): GeoJSON.Feature => ({
  type: "Feature",
  properties,
  geometry: { type: "Point", coordinates },
});
const collection = (features: GeoJSON.Feature[]): Collection => ({ type: "FeatureCollection", features });

function metres([lng1, lat1]: LngLat, [lng2, lat2]: LngLat) {
  const dx = (lng2 - lng1) * 111320 * Math.cos((lat1 * Math.PI) / 180);
  const dy = (lat2 - lat1) * 110540;
  return Math.hypot(dx, dy);
}

function newTrip(route: LngLat[], started: number): Trip {
  const totals = [0];
  for (let i = 1; i < route.length; i++) totals.push(totals[i - 1] + metres(route[i - 1], route[i]));
  return { key: `${route[0]}|${route[route.length - 1]}|${route.length}`, route, totals, started };
}

/** Where a trip is now, and the part of its route still ahead. */
function tripState(trip: Trip, now: number): { at: LngLat; ahead: LngLat[] } {
  const length = trip.totals[trip.totals.length - 1];
  const travelled = Math.min(length, ((now - trip.started) / 1000) * DISPLAY_METRES_PER_SECOND);
  let index = 1;
  while (index < trip.route.length - 1 && trip.totals[index] < travelled) index++;
  const [a, b] = [trip.route[index - 1], trip.route[index]];
  const span = trip.totals[index] - trip.totals[index - 1] || 1;
  const share = Math.min(1, Math.max(0, (travelled - trip.totals[index - 1]) / span));
  const at: LngLat = [a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share];
  return { at, ahead: [at, ...trip.route.slice(index)] };
}

function staticGeoJson(data: MapData): Record<string, Collection> {
  return {
    [`${OWN}annotations`]: collection((data.annotations ?? []).map((item) => line(item.path, { label: item.label }))),
    [`${OWN}masks`]: collection(data.floodMasks ?? []),
    [`${OWN}zones`]: collection((data.zones ?? []).map((zone) => ({
      type: "Feature" as const,
      geometry: { type: "Polygon" as const, coordinates: [zone.polygon] },
      properties: { id: zone.id, type: zone.type, level: zone.level, projected: zone.projected },
    }))),
    [`${OWN}outages`]: collection((data.outages ?? []).map((o) => point(o.position, { id: o.incident_id }))),
    [`${OWN}facilities`]: collection((data.facilities ?? []).map((f) => point(f.position, { name: f.name, type: f.type }))),
    [`${OWN}roads`]: collection(data.roads.map((road) => line(road.path, { id: road.id, closed: road.is_closed }))),
    [`${OWN}proposed`]: collection(
      (data.proposedRoutes ?? [])
        .filter((item) => item.route.length > 1)
        .map((item) => line(item.route, { id: item.service_id })),
    ),
    [`${OWN}incidents`]: collection(
      data.incidents.map((incident) =>
        point(incident.position, { id: incident.id, status: incident.status, severity: incident.severity_level }),
      ),
    ),
  };
}

function addOwnLayers(map: MapLibreMap) {
  for (const name of ["zones", "masks", "annotations", "roads", "routes", "proposed", "incidents", "outages", "facilities", "services"]) {
    if (!map.getSource(`${OWN}${name}`)) map.addSource(`${OWN}${name}`, { type: "geojson", data: collection([]) });
  }
  const layers: Parameters<MapLibreMap["addLayer"]>[0][] = [
    // Affected areas: flood zones (blue by depth of cover) and fire zones (orange/red).
    {
      id: `${OWN}zones`,
      type: "fill",
      source: `${OWN}zones`,
      paint: {
        "fill-color": ZONE_COLOR,
        "fill-opacity": ["case", ["get", "projected"], 0.2, 0.36],
      },
    },
    {
      id: `${OWN}zones-edge`,
      type: "line",
      source: `${OWN}zones`,
      filter: ["==", ["get", "projected"], false],
      paint: { "line-color": ZONE_COLOR, "line-width": 1.5, "line-opacity": 0.9 },
    },
    {
      id: `${OWN}zones-projected-edge`,
      type: "line",
      source: `${OWN}zones`,
      filter: ["==", ["get", "projected"], true],
      paint: { "line-color": "#e0f2fe", "line-width": 2, "line-dasharray": [2, 2] },
    },
    {
      id: `${OWN}masks`,
      type: "fill",
      source: `${OWN}masks`,
      paint: { "fill-color": "#3b82f6", "fill-opacity": 0.45, "fill-outline-color": "#93c5fd" },
    },
    {
      id: `${OWN}annotations`,
      type: "line",
      source: `${OWN}annotations`,
      layout: { "line-cap": "round" },
      paint: { "line-color": "#1d4ed8", "line-width": 18, "line-opacity": 0.35 },
    },
    // The scenario's road network, faint so the imagery shows through.
    {
      id: `${OWN}roads`,
      type: "line",
      source: `${OWN}roads`,
      filter: ["==", ["get", "closed"], false],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#e2e8f0", "line-width": 2.5, "line-opacity": 0.45 },
    },
    // Closed roads: blacked out, with a pale edge so they read on dark imagery.
    {
      id: `${OWN}roads-closed-edge`,
      type: "line",
      source: `${OWN}roads`,
      filter: ["==", ["get", "closed"], true],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#f8fafc", "line-width": 10, "line-opacity": 0.75 },
    },
    {
      id: `${OWN}roads-closed`,
      type: "line",
      source: `${OWN}roads`,
      filter: ["==", ["get", "closed"], true],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#000000", "line-width": 7 },
    },
    {
      id: `${OWN}roads-selected`,
      type: "line",
      source: `${OWN}roads`,
      filter: ["==", ["get", "id"], ""],
      paint: { "line-color": "#111827", "line-width": 10, "line-opacity": 0.25 },
    },
    {
      id: `${OWN}roads-hit`,
      type: "line",
      source: `${OWN}roads`,
      paint: { "line-color": "#000", "line-width": 18, "line-opacity": 0 },
    },
    // Proposed routes: dashed white until a person approves the plan.
    {
      id: `${OWN}proposed-casing`,
      type: "line",
      source: `${OWN}proposed`,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#0a0f1c", "line-width": 7, "line-opacity": 0.7 },
    },
    {
      id: `${OWN}proposed`,
      type: "line",
      source: `${OWN}proposed`,
      layout: { "line-join": "round" },
      paint: { "line-color": "#f8fafc", "line-width": 3.5, "line-dasharray": [2, 1.2] },
    },
    // Dispatched routes: the road still ahead of each moving unit, in the unit's color.
    {
      id: `${OWN}routes-casing`,
      type: "line",
      source: `${OWN}routes`,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#0a0f1c", "line-width": 8, "line-opacity": 0.8 },
    },
    {
      id: `${OWN}routes`,
      type: "line",
      source: `${OWN}routes`,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": KIND_COLOR, "line-width": 4.5 },
    },
    // Incidents: rings, so they never read as a unit.
    {
      id: `${OWN}incidents`,
      type: "circle",
      source: `${OWN}incidents`,
      paint: {
        "circle-radius": ["+", 7, ["*", 2, ["get", "severity"]]],
        "circle-color": ["match", ["get", "status"], "open", "#ef4444", "assigned", "#f59e0b", "#94a3b8"],
        "circle-opacity": 0.25,
        "circle-stroke-color": ["match", ["get", "status"], "open", "#ef4444", "assigned", "#f59e0b", "#94a3b8"],
        "circle-stroke-width": 3,
      },
    },
    // Reported power outages and facilities (hospitals, shelters).
    {
      id: `${OWN}outages`,
      type: "circle",
      source: `${OWN}outages`,
      paint: { "circle-radius": 8, "circle-color": "#facc15", "circle-stroke-color": "#111827", "circle-stroke-width": 2.5 },
    },
    {
      id: `${OWN}facilities`,
      type: "circle",
      source: `${OWN}facilities`,
      paint: {
        "circle-radius": 9,
        "circle-color": ["match", ["get", "type"], "hospital", "#f8fafc", "#a7f3d0"],
        "circle-stroke-color": ["match", ["get", "type"], "hospital", "#dc2626", "#047857"],
        "circle-stroke-width": 3,
      },
    },
    // Units: solid dots colored by type; a white ring marks one out on a mission.
    {
      id: `${OWN}services`,
      type: "circle",
      source: `${OWN}services`,
      paint: {
        "circle-radius": 7,
        "circle-color": KIND_COLOR,
        "circle-stroke-color": ["case", ["get", "available"], "#0a0f1c", "#ffffff"],
        "circle-stroke-width": ["case", ["get", "available"], 2, 3],
      },
    },
  ];
  for (const layer of layers) if (!map.getLayer(layer.id)) map.addLayer(layer);
}

export default function CrisisMap({ data, basemap, onBasemapError, selectedRoadId = null, onSelectRoad }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const libRef = useRef<typeof import("maplibre-gl") | null>(null);
  const markers = useRef(new Map<string, Marker>());
  const trips = useRef(new Map<string, Trip>());
  const dataRef = useRef<MapData | null>(data);
  const fitted = useRef(false);
  const callbacks = useRef({ onSelectRoad, onBasemapError });
  const basemapRef = useRef(basemap);
  const [ready, setReady] = useState(0);
  callbacks.current = { onSelectRoad, onBasemapError };
  basemapRef.current = basemap;
  dataRef.current = data;

  useEffect(() => {
    let cancelled = false;
    let map: MapLibreMap | undefined;
    let resizeObserver: ResizeObserver | undefined;

    import("maplibre-gl").then((maplibregl) => {
      if (cancelled || !container.current) return;
      libRef.current = maplibregl;
      // MapLibre v6 locates its worker via import.meta.url, which webpack rewrites;
      // emit the worker as a bundled asset and point MapLibre at it explicitly.
      maplibregl.setWorkerUrl(new URL("maplibre-gl/dist/maplibre-gl-worker.mjs", import.meta.url).href);
      const instance = new maplibregl.Map({
        container: container.current,
        style: basemapRef.current ? BASEMAP_STYLE : OFFLINE_STYLE,
        center: [-77.435, 37.533],
        zoom: 14,
        attributionControl: { compact: true },
      });
      instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      map = instance;
      mapRef.current = instance;
      // Keep the canvas sized to its tile when the map is expanded or collapsed.
      resizeObserver = new ResizeObserver(() => instance.resize());
      resizeObserver.observe(container.current);

      instance.on("style.load", () => {
        addOwnLayers(instance);
        setReady((count) => count + 1);
      });
      instance.on("error", (event) => {
        const sourceId = (event as { sourceId?: string }).sourceId ?? "";
        if (basemapRef.current && !sourceId.startsWith(OWN)) callbacks.current.onBasemapError?.();
      });
      instance.on("click", `${OWN}roads-hit`, (event) => {
        const id = event.features?.[0]?.properties?.id;
        if (typeof id === "string") callbacks.current.onSelectRoad?.(id);
      });
      instance.on("mouseenter", `${OWN}roads-hit`, () => {
        if (callbacks.current.onSelectRoad) instance.getCanvas().style.cursor = "pointer";
      });
      instance.on("mouseleave", `${OWN}roads-hit`, () => (instance.getCanvas().style.cursor = ""));
    });

    return () => {
      cancelled = true;
      resizeObserver?.disconnect();
      markers.current.forEach((marker) => marker.remove());
      markers.current.clear();
      map?.remove();
      mapRef.current = null;
    };
  }, []);

  // Switch between the offline style and the satellite basemap, keeping our layers.
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    map.setStyle(basemap ? BASEMAP_STYLE : OFFLINE_STYLE, {
      transformStyle: (previous, next) => ({
        ...next,
        sources: {
          ...next.sources,
          ...Object.fromEntries(Object.entries(previous?.sources ?? {}).filter(([id]) => id.startsWith(OWN))),
        },
        layers: [...next.layers, ...(previous?.layers ?? []).filter((layer) => layer.id.startsWith(OWN))],
      }),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [basemap]);

  // Place or move an HTML label (no remote font glyphs needed, so it works offline).
  const label = (key: string, at: LngLat, text: string, kind: string, title?: string) => {
    const map = mapRef.current;
    const lib = libRef.current;
    if (!map || !lib) return;
    let marker = markers.current.get(key);
    if (!marker) {
      const element = document.createElement("div");
      element.className = `map-label map-label-${kind}`;
      marker = new lib.Marker({
        element,
        anchor: kind === "incident" ? "top" : kind === "annotation" ? "center" : "bottom",
        offset: kind === "incident" ? [0, 14] : kind === "annotation" ? [0, 0] : [0, -9],
      });
      marker.setLngLat(at).addTo(map);
      markers.current.set(key, marker);
    }
    marker.getElement().textContent = text;
    marker.getElement().title = title ?? text;
    marker.setLngLat(at);
  };

  // Draw units and their remaining routes at a moment in time; returns whether any still move.
  const drawUnits = (now: number) => {
    const map = mapRef.current;
    const current = dataRef.current;
    if (!map || !current) return false;
    let moving = false;
    const units: GeoJSON.Feature[] = [];
    const routes: GeoJSON.Feature[] = [];
    for (const service of current.services) {
      const kind = service.out_of_service ? "out" : unitKind(service.capabilities);
      const trip = trips.current.get(service.id);
      let at = service.position;
      if (trip) {
        const state = tripState(trip, now);
        at = state.at;
        if (state.ahead.length > 1 && metres(state.at, trip.route[trip.route.length - 1]) > 1) {
          moving = true;
          routes.push(line(state.ahead, { id: service.id, kind }));
        }
        label(`m:${service.id}`, at, service.name, "unit");
      }
      units.push(point(at, { id: service.id, available: service.available, kind }));
    }
    (map.getSource(`${OWN}services`) as GeoJSONSource | undefined)?.setData(collection(units));
    (map.getSource(`${OWN}routes`) as GeoJSONSource | undefined)?.setData(collection(routes));
    return moving;
  };

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !data) return;
    for (const [id, layerData] of Object.entries(staticGeoJson(data))) {
      (map.getSource(id) as GeoJSONSource | undefined)?.setData(layerData);
    }

    // Start a trip when a unit gets a new route; keep running trips untouched.
    const now = performance.now();
    const active = new Set<string>();
    for (const service of data.services) {
      if (service.available || !service.route || service.route.length < 2) continue;
      active.add(service.id);
      const trip = newTrip(service.route, now);
      if (trips.current.get(service.id)?.key !== trip.key) trips.current.set(service.id, trip);
    }
    for (const id of [...trips.current.keys()]) if (!active.has(id)) trips.current.delete(id);

    const wanted = new Set<string>();
    for (const incident of data.incidents) {
      wanted.add(`i:${incident.id}`);
      label(`i:${incident.id}`, incident.position, shorten(incident.kind), "incident",
        `${incident.id} · ${incident.kind} · ${incident.severity} · ${incident.status}`);
    }
    // Units parked at the same spot (e.g. one station) share a label so none is hidden.
    const unitsAt = new Map<string, string[]>();
    for (const service of data.services) {
      if (trips.current.has(service.id)) {
        wanted.add(`m:${service.id}`);
        continue;
      }
      const key = service.position.join(",");
      unitsAt.set(key, [...(unitsAt.get(key) ?? []), service.name]);
    }
    for (const [key, names] of unitsAt) {
      wanted.add(`s:${key}`);
      label(`s:${key}`, key.split(",").map(Number) as LngLat, names.join(" · "), "unit");
    }
    for (const facility of data.facilities ?? []) {
      const key = `f:${facility.name}`;
      wanted.add(key);
      const detail = facility.type === "hospital"
        ? `beds: ${facility.beds_available ?? facility.beds_source ?? "unknown"}`
        : `capacity: ${facility.capacity ?? facility.capacity_source ?? "unknown"}`;
      label(key, facility.position, `${facility.type === "hospital" ? "✚" : "⌂"} ${facility.name} · ${detail}`, "facility");
    }
    for (const outage of data.outages ?? []) {
      const key = `o:${outage.incident_id}`;
      wanted.add(key);
      label(key, outage.position, "⚡ outage reported", "outage", `${outage.kind} (${outage.source})`);
    }
    for (const annotation of data.annotations ?? []) {
      const middle = annotation.path[Math.floor(annotation.path.length / 2)] ?? annotation.path[0];
      const at: LngLat = annotation.path.length === 2
        ? [(annotation.path[0][0] * 3 + annotation.path[1][0]) / 4, annotation.path[0][1]]
        : middle;
      wanted.add(`a:${annotation.label}`);
      label(`a:${annotation.label}`, at, annotation.label, "annotation");
    }
    for (const [key, marker] of markers.current) {
      if (!wanted.has(key)) {
        marker.remove();
        markers.current.delete(key);
      }
    }

    // Animate until every dispatched unit has reached its destination.
    let frame = 0;
    const tick = () => {
      if (drawUnits(performance.now())) frame = requestAnimationFrame(tick);
    };
    tick();

    if (!fitted.current && data.roads.length) {
      const points = data.roads.flatMap((road) => road.path);
      const lngs = points.map((p) => p[0]);
      const lats = points.map((p) => p[1]);
      map.fitBounds(
        [
          [Math.min(...lngs), Math.min(...lats)],
          [Math.max(...lngs), Math.max(...lats)],
        ],
        { padding: 50, duration: 0 },
      );
      fitted.current = true;
    }
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, data]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map?.getLayer(`${OWN}roads-selected`)) return;
    map.setFilter(`${OWN}roads-selected`, ["==", ["get", "id"], selectedRoadId ?? ""]);
  }, [ready, selectedRoadId]);

  return <div ref={container} className="map" />;
}
