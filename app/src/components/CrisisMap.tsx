"use client";

import { useEffect, useRef, useState } from "react";
import type { GeoJSONSource, Map as MapLibreMap, Marker, StyleSpecification } from "maplibre-gl";
import type { Incident, LngLat, Road, Service } from "@/lib/types";

// The road graph renders on a plain background with no network access. The street
// basemap is optional context only and is labeled illustrative in the UI.
const OFFLINE_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#eef2f6" } }],
};
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron";
const OWN = "cc-";
const LABEL_MAX = 24;
const shorten = (text: string) => (text.length > LABEL_MAX ? `${text.slice(0, LABEL_MAX - 1)}…` : text);

export type MapData = {
  incidents: Pick<Incident, "id" | "kind" | "severity" | "severity_level" | "status" | "position">[];
  services: Pick<Service, "id" | "name" | "available" | "position" | "route">[];
  roads: Pick<Road, "id" | "is_closed" | "path">[];
  proposedRoutes?: { service_id: string; route: LngLat[] }[];
  annotations?: { label: string; path: LngLat[] }[];
};

type Props = {
  data: MapData | null;
  basemap: boolean;
  onBasemapError?: () => void;
  selectedRoadId?: string | null;
  onSelectRoad?: (roadId: string | null) => void;
};

type Collection = GeoJSON.FeatureCollection;
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

function toGeoJson(data: MapData): Record<string, Collection> {
  const collection = (features: GeoJSON.Feature[]): Collection => ({ type: "FeatureCollection", features });
  return {
    [`${OWN}annotations`]: collection((data.annotations ?? []).map((item) => line(item.path, { label: item.label }))),
    [`${OWN}roads`]: collection(data.roads.map((road) => line(road.path, { id: road.id, closed: road.is_closed }))),
    [`${OWN}routes`]: collection(
      data.services
        .filter((service) => service.route && service.route.length > 1)
        .map((service) => line(service.route!, { id: service.id })),
    ),
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
    [`${OWN}services`]: collection(
      data.services.map((service) => point(service.position, { id: service.id, available: service.available })),
    ),
  };
}

function addOwnLayers(map: MapLibreMap) {
  const empty: Collection = { type: "FeatureCollection", features: [] };
  for (const name of ["annotations", "roads", "routes", "proposed", "incidents", "services"]) {
    if (!map.getSource(`${OWN}${name}`)) map.addSource(`${OWN}${name}`, { type: "geojson", data: empty });
  }
  const layers: Parameters<MapLibreMap["addLayer"]>[0][] = [
    {
      id: `${OWN}annotations`,
      type: "line",
      source: `${OWN}annotations`,
      layout: { "line-cap": "round" },
      paint: { "line-color": "#93c5fd", "line-width": 18, "line-opacity": 0.55 },
    },
    {
      id: `${OWN}roads`,
      type: "line",
      source: `${OWN}roads`,
      filter: ["==", ["get", "closed"], false],
      layout: { "line-cap": "round" },
      paint: { "line-color": "#64748b", "line-width": 5 },
    },
    {
      id: `${OWN}roads-closed`,
      type: "line",
      source: `${OWN}roads`,
      filter: ["==", ["get", "closed"], true],
      paint: { "line-color": "#dc2626", "line-width": 5, "line-dasharray": [1.2, 1] },
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
    {
      id: `${OWN}routes`,
      type: "line",
      source: `${OWN}routes`,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#2563eb", "line-width": 3.5, "line-opacity": 0.9 },
    },
    {
      id: `${OWN}proposed`,
      type: "line",
      source: `${OWN}proposed`,
      layout: { "line-join": "round" },
      paint: { "line-color": "#7c3aed", "line-width": 3, "line-dasharray": [2, 1.5], "line-opacity": 0.85 },
    },
    {
      id: `${OWN}incidents`,
      type: "circle",
      source: `${OWN}incidents`,
      paint: {
        "circle-radius": ["+", 5, ["*", 2, ["get", "severity"]]],
        "circle-color": ["match", ["get", "status"], "open", "#dc2626", "assigned", "#d97706", "#16a34a"],
        "circle-opacity": 0.9,
        "circle-stroke-color": "#fff",
        "circle-stroke-width": 2,
      },
    },
    {
      id: `${OWN}services`,
      type: "circle",
      source: `${OWN}services`,
      paint: {
        "circle-radius": 6,
        "circle-color": ["case", ["get", "available"], "#0f172a", "#2563eb"],
        "circle-stroke-color": "#fff",
        "circle-stroke-width": 2,
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
  const fitted = useRef(false);
  const callbacks = useRef({ onSelectRoad, onBasemapError });
  const basemapRef = useRef(basemap);
  const [ready, setReady] = useState(0);
  callbacks.current = { onSelectRoad, onBasemapError };
  basemapRef.current = basemap;

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
        style: OFFLINE_STYLE,
        center: [-77.43, 37.545],
        zoom: 13.5,
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

  // Switch between the offline style and the optional basemap, keeping our layers.
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

  useEffect(() => {
    const map = mapRef.current;
    const lib = libRef.current;
    if (!ready || !map || !lib || !data) return;
    for (const [id, collection] of Object.entries(toGeoJson(data))) {
      (map.getSource(id) as GeoJSONSource | undefined)?.setData(collection);
    }

    // HTML labels need no remote font glyphs, so they work offline.
    const wanted = new Map<string, { at: LngLat; text: string; kind: string; title?: string }>();
    for (const incident of data.incidents) {
      wanted.set(`i:${incident.id}`, {
        at: incident.position,
        text: shorten(incident.kind),
        kind: "incident",
        title: `${incident.id} · ${incident.kind} · ${incident.severity} · ${incident.status}`,
      });
    }
    // Units sharing a position (e.g. one station) share a label so none is hidden.
    const unitsAt = new Map<string, string[]>();
    for (const service of data.services) {
      const key = service.position.join(",");
      unitsAt.set(key, [...(unitsAt.get(key) ?? []), service.name]);
    }
    for (const [key, names] of unitsAt) {
      const at = key.split(",").map(Number) as LngLat;
      wanted.set(`s:${key}`, { at, text: names.join(" · "), kind: "unit" });
    }
    for (const annotation of data.annotations ?? []) {
      const middle = annotation.path[Math.floor(annotation.path.length / 2)] ?? annotation.path[0];
      const at: LngLat = annotation.path.length === 2
        ? [(annotation.path[0][0] * 3 + annotation.path[1][0]) / 4, annotation.path[0][1]]
        : middle;
      wanted.set(`a:${annotation.label}`, { at, text: annotation.label, kind: "annotation" });
    }
    for (const [key, marker] of markers.current) {
      if (!wanted.has(key)) {
        marker.remove();
        markers.current.delete(key);
      }
    }
    for (const [key, label] of wanted) {
      let marker = markers.current.get(key);
      if (!marker) {
        const element = document.createElement("div");
        element.className = `map-label map-label-${label.kind}`;
        marker = new lib.Marker({
          element,
          anchor: label.kind === "unit" ? "bottom" : label.kind === "incident" ? "top" : "center",
          offset: label.kind === "unit" ? [0, -8] : label.kind === "incident" ? [0, 12] : [0, 0],
        });
        marker.setLngLat(label.at).addTo(map);
        markers.current.set(key, marker);
      }
      marker.getElement().textContent = label.text;
      marker.getElement().title = label.title ?? label.text;
      marker.setLngLat(label.at);
    }

    if (!fitted.current && data.roads.length) {
      const points = data.roads.flatMap((road) => road.path);
      const lngs = points.map((p) => p[0]);
      const lats = points.map((p) => p[1]);
      map.fitBounds(
        [
          [Math.min(...lngs), Math.min(...lats)],
          [Math.max(...lngs), Math.max(...lats)],
        ],
        { padding: 70, duration: 0 },
      );
      fitted.current = true;
    }
  }, [ready, data]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map?.getLayer(`${OWN}roads-selected`)) return;
    map.setFilter(`${OWN}roads-selected`, ["==", ["get", "id"], selectedRoadId ?? ""]);
  }, [ready, selectedRoadId]);

  return <div ref={container} className="map" />;
}
