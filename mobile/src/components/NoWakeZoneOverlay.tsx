/**
 * OpenCatch — No-Wake Zone Overlay
 *
 * Queries OSM Overpass for restricted areas and speed zones near water.
 * Renders as yellow/orange shaded polygons with speed limit labels.
 */

import React, { useEffect, useRef, useState } from 'react';
import { palette } from '../theme/palette';

// ── Types ────────────────────────────────────────────────────────────────────

interface NoWakeZone {
  id: string;
  name: string;
  speedLimit: string | null;
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon;
}

interface Props {
  lat: number;
  lon: number;
  ShapeSource: any;
  FillLayer: any;
  SymbolLayer: any;
  LineLayer: any;
}

// ── Overpass query ───────────────────────────────────────────────────────────

const OVERPASS_API = 'https://overpass-api.de/api/interpreter';
const SEARCH_RADIUS_M = 25000; // 25km
const REFRESH_DISTANCE_M = 5000;

function buildOverpassQuery(lat: number, lon: number): string {
  return `[out:json][timeout:15];
(
  way["seamark:type"="restricted_area"](around:${SEARCH_RADIUS_M},${lat},${lon});
  relation["seamark:type"="restricted_area"](around:${SEARCH_RADIUS_M},${lat},${lon});
  way["maxspeed:type"="zone"](around:${SEARCH_RADIUS_M},${lat},${lon});
  way["seamark:restricted_area:category"="no_wake"](around:${SEARCH_RADIUS_M},${lat},${lon});
  way["maxspeed"]["waterway"](around:${SEARCH_RADIUS_M},${lat},${lon});
  way["seamark:type"="speed_limit"](around:${SEARCH_RADIUS_M},${lat},${lon});
);
out body;
>;
out skel qt;`;
}

function haversineDistance(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371000;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ── Parse OSM response ──────────────────────────────────────────────────────

function parseOverpassResponse(data: any): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  if (!data?.elements) return { type: 'FeatureCollection', features };

  const nodes = new Map<number, [number, number]>();
  for (const el of data.elements) {
    if (el.type === 'node' && el.lat != null && el.lon != null) {
      nodes.set(el.id, [el.lon, el.lat]);
    }
  }

  for (const el of data.elements) {
    if (el.type !== 'way' || !el.nodes) continue;
    const coords: [number, number][] = [];
    for (const nodeId of el.nodes) {
      const c = nodes.get(nodeId);
      if (c) coords.push(c);
    }
    if (coords.length < 3) continue;

    // Close ring if needed
    const first = coords[0];
    const last = coords[coords.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) {
      coords.push([...first] as [number, number]);
    }

    const tags = el.tags || {};
    const name = tags.name || tags['seamark:name'] || 'No-Wake Zone';
    const speedLimit =
      tags.maxspeed ||
      tags['seamark:restricted_area:restriction'] ||
      tags['seamark:speed_limit:speed'] ||
      null;

    // Compute centroid for label placement
    const cx = coords.reduce((s, c) => s + c[0], 0) / coords.length;
    const cy = coords.reduce((s, c) => s + c[1], 0) / coords.length;

    // Zone polygon
    features.push({
      type: 'Feature',
      properties: {
        id: `nwz-${el.id}`,
        name,
        speedLimit: speedLimit ? `${speedLimit}` : 'No Wake',
        isLabel: false,
      },
      geometry: {
        type: 'Polygon',
        coordinates: [coords],
      },
    });

    // Label point
    features.push({
      type: 'Feature',
      properties: {
        id: `nwz-label-${el.id}`,
        name,
        speedLimit: speedLimit ? `${speedLimit}` : 'No Wake',
        isLabel: true,
      },
      geometry: {
        type: 'Point',
        coordinates: [cx, cy],
      },
    });
  }

  return { type: 'FeatureCollection', features };
}

// ── Component ────────────────────────────────────────────────────────────────

export function NoWakeZoneOverlay({ lat, lon, ShapeSource, FillLayer, SymbolLayer, LineLayer }: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection>({ type: 'FeatureCollection', features: [] });
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);

  useEffect(() => {
    if (lastCenter.current && haversineDistance(lastCenter.current.lat, lastCenter.current.lon, lat, lon) < REFRESH_DISTANCE_M) {
      return;
    }
    lastCenter.current = { lat, lon };

    const controller = new AbortController();
    (async () => {
      try {
        const query = buildOverpassQuery(lat, lon);
        const res = await fetch(OVERPASS_API, {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: `data=${encodeURIComponent(query)}`,
          signal: controller.signal,
        });
        if (!res.ok) return;
        const data = await res.json();
        setGeoJSON(parseOverpassResponse(data));
      } catch {
        // Network error or aborted
      }
    })();

    return () => controller.abort();
  }, [lat, lon]);

  if (geoJSON.features.length === 0) return null;

  return (
    <>
      <ShapeSource id="no-wake-zones-source" shape={geoJSON}>
        {/* Fill for zone polygons */}
        <FillLayer
          id="no-wake-zones-fill"
          filter={['==', ['get', 'isLabel'], false]}
          style={{
            fillColor: 'rgba(255, 165, 0, 0.25)',
            fillOutlineColor: 'rgba(255, 140, 0, 0.7)',
          }}
        />
        {/* Border for zone polygons */}
        <LineLayer
          id="no-wake-zones-border"
          filter={['==', ['get', 'isLabel'], false]}
          style={{
            lineColor: 'rgba(255, 140, 0, 0.8)',
            lineWidth: 2,
            lineDasharray: [4, 2],
          }}
        />
        {/* Speed limit labels */}
        <SymbolLayer
          id="no-wake-zones-labels"
          filter={['==', ['get', 'isLabel'], true]}
          style={{
            textField: ['get', 'speedLimit'],
            textSize: 12,
            textColor: '#CC6600',
            textHaloColor: 'rgba(255, 255, 255, 0.9)',
            textHaloWidth: 1.5,
            textFont: ['Open Sans Bold'],
            textAllowOverlap: true,
          }}
        />
      </ShapeSource>
    </>
  );
}
