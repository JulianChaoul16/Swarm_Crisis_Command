"use client";

import { useEffect, useRef, useState } from "react";
import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";
import type { WorldState } from "@/lib/types";

const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron";

type Props = {
  world: WorldState | null;
  selectedRoadId: string | null;
  onSelectRoad: (roadId: string | null) => void;
};

type Collection = GeoJSON.FeatureCollection;

function toGeoJson(world: WorldState): Record<string, Collection> {
  return {
    roads: {
      type: "FeatureCollection",
      features: world.roads.map((road) => ({
        type: "Feature",
        properties: { id: road.id, closed: road.is_closed },
        geometry: { type: "LineString", coordinates: road.path },
      })),
    },
    routes: {
      type: "FeatureCollection",
      features: world.services
        .filter((service) => service.route && service.route.length > 1)
        .map((service) => ({
          type: "Feature",
          properties: { id: service.id },
          geometry: { type: "LineString", coordinates: service.route! },
        })),
    },
    incidents: {
      type: "FeatureCollection",
      features: world.incidents.map((incident) => ({
        type: "Feature",
        properties: {
          id: incident.id,
          label: `${incident.kind} · ${incident.severity}`,
          status: incident.status,
          severity: incident.severity_level,
        },
        geometry: { type: "Point", coordinates: incident.position },
      })),
    },
    services: {
      type: "FeatureCollection",
      features: world.services.map((service) => ({
        type: "Feature",
        properties: { id: service.id, label: service.name, available: service.available },
        geometry: { type: "Point", coordinates: service.position },
      })),
    },
  };
}

export default function CrisisMap({ world, selectedRoadId, onSelectRoad }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const fitted = useRef(false);
  const onSelectRoadRef = useRef(onSelectRoad);
  const [ready, setReady] = useState(false);
  onSelectRoadRef.current = onSelectRoad;

  useEffect(() => {
    let cancelled = false;
    let map: MapLibreMap | undefined;

    import("maplibre-gl").then((maplibregl) => {
      if (cancelled || !container.current) return;
      // MapLibre v6 locates its worker via import.meta.url, which webpack rewrites;
      // emit the worker as a bundled asset and point MapLibre at it explicitly.
      maplibregl.setWorkerUrl(
        new URL("maplibre-gl/dist/maplibre-gl-worker.mjs", import.meta.url).href,
      );
      const instance = new maplibregl.Map({
        container: container.current,
        style: BASEMAP_STYLE,
        center: [-77.436, 37.54],
        zoom: 14,
        attributionControl: { compact: true },
      });
      instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      map = instance;
      mapRef.current = instance;

      instance.on("load", () => {
        const empty: Collection = { type: "FeatureCollection", features: [] };
        for (const id of ["roads", "routes", "incidents", "services"]) {
          instance.addSource(id, { type: "geojson", data: empty });
        }

        instance.addLayer({
          id: "roads",
          type: "line",
          source: "roads",
          filter: ["==", ["get", "closed"], false],
          layout: { "line-cap": "round" },
          paint: { "line-color": "#64748b", "line-width": 5 },
        });
        instance.addLayer({
          id: "roads-closed",
          type: "line",
          source: "roads",
          filter: ["==", ["get", "closed"], true],
          paint: { "line-color": "#dc2626", "line-width": 5, "line-dasharray": [1.2, 1] },
        });
        instance.addLayer({
          id: "roads-selected",
          type: "line",
          source: "roads",
          filter: ["==", ["get", "id"], ""],
          paint: { "line-color": "#111827", "line-width": 9, "line-opacity": 0.25 },
        });
        instance.addLayer({
          id: "roads-hit",
          type: "line",
          source: "roads",
          paint: { "line-color": "#000", "line-width": 18, "line-opacity": 0 },
        });
        instance.addLayer({
          id: "routes",
          type: "line",
          source: "routes",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": "#2563eb", "line-width": 3, "line-opacity": 0.85 },
        });
        instance.addLayer({
          id: "incidents",
          type: "circle",
          source: "incidents",
          paint: {
            "circle-radius": ["+", 5, ["*", 2, ["get", "severity"]]],
            "circle-color": [
              "match",
              ["get", "status"],
              "open", "#dc2626",
              "assigned", "#d97706",
              "#16a34a",
            ],
            "circle-opacity": 0.85,
            "circle-stroke-color": "#fff",
            "circle-stroke-width": 2,
          },
        });
        instance.addLayer({
          id: "services",
          type: "circle",
          source: "services",
          paint: {
            "circle-radius": 6,
            "circle-color": ["case", ["get", "available"], "#0f172a", "#2563eb"],
            "circle-stroke-color": "#fff",
            "circle-stroke-width": 2,
          },
        });
        instance.addLayer({
          id: "labels",
          type: "symbol",
          source: "incidents",
          layout: {
            "text-field": ["get", "label"],
            "text-font": ["Noto Sans Regular"],
            "text-size": 12,
            "text-offset": [0, 1.6],
            "text-anchor": "top",
          },
          paint: { "text-color": "#111827", "text-halo-color": "#fff", "text-halo-width": 1.5 },
        });
        instance.addLayer({
          id: "service-labels",
          type: "symbol",
          source: "services",
          layout: {
            "text-field": ["get", "label"],
            "text-font": ["Noto Sans Regular"],
            "text-size": 11,
            "text-offset": [0, -1.3],
            "text-anchor": "bottom",
          },
          paint: { "text-color": "#334155", "text-halo-color": "#fff", "text-halo-width": 1.5 },
        });

        instance.on("click", "roads-hit", (event) => {
          const id = event.features?.[0]?.properties?.id;
          if (typeof id === "string") onSelectRoadRef.current(id);
        });
        instance.on("mouseenter", "roads-hit", () => (instance.getCanvas().style.cursor = "pointer"));
        instance.on("mouseleave", "roads-hit", () => (instance.getCanvas().style.cursor = ""));
        setReady(true);
      });
    });

    return () => {
      cancelled = true;
      map?.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !world) return;
    const data = toGeoJson(world);
    for (const [id, collection] of Object.entries(data)) {
      (map.getSource(id) as GeoJSONSource | undefined)?.setData(collection);
    }
    if (!fitted.current && world.roads.length) {
      const points = world.roads.flatMap((road) => road.path);
      const lngs = points.map((point) => point[0]);
      const lats = points.map((point) => point[1]);
      map.fitBounds(
        [
          [Math.min(...lngs), Math.min(...lats)],
          [Math.max(...lngs), Math.max(...lats)],
        ],
        { padding: 80, duration: 0 },
      );
      fitted.current = true;
    }
  }, [ready, world]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    map.setFilter("roads-selected", ["==", ["get", "id"], selectedRoadId ?? ""]);
  }, [ready, selectedRoadId]);

  return <div ref={container} className="map" />;
}
