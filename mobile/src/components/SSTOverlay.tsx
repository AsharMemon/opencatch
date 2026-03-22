/**
 * OpenCatch -- Sea Surface Temperature (SST) Overlay (Windy-Quality)
 *
 * Full-ocean-coverage temperature gradient map. Fetches a dense grid
 * of SST data from Open-Meteo Marine API and renders as a continuous
 * color surface using large blurred circles.
 *
 * Killer feature for offshore fishing: visually shows temperature
 * breaks where fish congregate at boundaries between warm and cold water.
 *
 * Color scale:
 *   Deep Blue (<50F/10C) -> Cyan -> Green -> Yellow -> Orange -> Red (>85F/30C)
 *
 * Only useful near the coast / ocean.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';

// ── Types ────────────────────────────────────────────────────────────────────

interface SSTPoint {
  lat: number;
  lon: number;
  tempC: number;
}

interface Props {
  lat: number;
  lon: number;
  ShapeSource: any;
  CircleLayer: any;
  SymbolLayer: any;
  onLoadStart?: () => void;
  onLoadEnd?: () => void;
}

// ── Constants ────────────────────────────────────────────────────────────────

const MARINE_API_BASE = 'https://marine-api.open-meteo.com/v1/marine';

/** Dense grid for continuous coverage (~15km). */
const GRID_SPACING_DEG = 0.15;

/** Wide coverage radius. */
const GRID_RADIUS_DEG = 2.0;

/** Min move distance before refetch. */
const REFRESH_DISTANCE_M = 8000;

/** Cache TTL: 1 hour (SST changes slowly). */
const CACHE_TTL_MS = 60 * 60 * 1000;

// ── Color scale (ocean temperature) ──────────────────────────────────────────

function sstColor(tempC: number): string {
  if (tempC < 5) return '#0D47A1';   // Deep blue -- very cold
  if (tempC < 10) return '#1565C0';  // Blue
  if (tempC < 14) return '#1E88E5';  // Medium blue
  if (tempC < 17) return '#00ACC1';  // Cyan
  if (tempC < 20) return '#00897B';  // Teal
  if (tempC < 23) return '#43A047';  // Green
  if (tempC < 25) return '#7CB342';  // Light green
  if (tempC < 27) return '#FDD835';  // Yellow
  if (tempC < 29) return '#FF9800';  // Orange
  if (tempC < 31) return '#F4511E';  // Deep orange
  return '#C62828';                   // Red -- tropical
}

function tempLabel(tempC: number): string {
  const tempF = tempC * 9 / 5 + 32;
  return `${Math.round(tempF)}F`;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function haversineDistance(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371000;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ── Grid building ────────────────────────────────────────────────────────────

function buildGrid(centerLat: number, centerLon: number): Array<{ lat: number; lon: number }> {
  const coords: Array<{ lat: number; lon: number }> = [];
  const latMin = centerLat - GRID_RADIUS_DEG;
  const latMax = centerLat + GRID_RADIUS_DEG;
  const lonMin = centerLon - GRID_RADIUS_DEG;
  const lonMax = centerLon + GRID_RADIUS_DEG;

  for (let lat = latMin; lat <= latMax + 1e-9; lat += GRID_SPACING_DEG) {
    for (let lon = lonMin; lon <= lonMax + 1e-9; lon += GRID_SPACING_DEG) {
      coords.push({
        lat: parseFloat(lat.toFixed(4)),
        lon: parseFloat(lon.toFixed(4)),
      });
    }
  }
  return coords;
}

// ── Fetch SST data ───────────────────────────────────────────────────────────

interface MarineHourlyResponse {
  latitude: number;
  longitude: number;
  hourly?: {
    sea_surface_temperature?: number[];
  };
}

async function fetchSSTBatch(coords: Array<{ lat: number; lon: number }>): Promise<SSTPoint[]> {
  const BATCH_SIZE = 20;
  const results: SSTPoint[] = [];

  for (let i = 0; i < coords.length; i += BATCH_SIZE) {
    const batch = coords.slice(i, i + BATCH_SIZE);
    const lats = batch.map((c) => c.lat.toFixed(4)).join(',');
    const lons = batch.map((c) => c.lon.toFixed(4)).join(',');

    // Open-Meteo Marine API -- sea_surface_temperature is available
    // as an hourly marine variable (alias: ocean_temperature)
    const url =
      `${MARINE_API_BASE}?latitude=${lats}&longitude=${lons}` +
      '&hourly=sea_surface_temperature' +
      '&forecast_hours=1';

    try {
      const res = await fetch(url);
      if (!res.ok) continue;

      const json = await res.json();
      const items: MarineHourlyResponse[] = Array.isArray(json) ? json : [json];

      for (let j = 0; j < items.length; j++) {
        const item = items[j];
        const tempC = item.hourly?.sea_surface_temperature?.[0];

        // Only include ocean points that returned valid SST
        if (tempC != null && !isNaN(tempC)) {
          results.push({
            lat: batch[j].lat,
            lon: batch[j].lon,
            tempC,
          });
        }
      }
    } catch {
      // Skip failed batch
    }
  }

  return results;
}

// ── GeoJSON conversion ───────────────────────────────────────────────────────

function sstToGeoJSON(points: SSTPoint[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: points.map((p) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [p.lon, p.lat],
      },
      properties: {
        tempC: Math.round(p.tempC * 10) / 10,
        color: sstColor(p.tempC),
        label: tempLabel(p.tempC),
      },
    })),
  };
}

// ── Cache ────────────────────────────────────────────────────────────────────

let _sstCache: { geoJSON: GeoJSON.FeatureCollection; lat: number; lon: number; ts: number } | null = null;

// ── Component ────────────────────────────────────────────────────────────────

export function SSTOverlay({ lat, lon, ShapeSource, CircleLayer, SymbolLayer, onLoadStart, onLoadEnd }: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);
  const loadingRef = useRef(false);

  const fetchData = useCallback(async (centerLat: number, centerLon: number) => {
    if (loadingRef.current) return;

    // Check cache
    if (_sstCache && Date.now() - _sstCache.ts < CACHE_TTL_MS) {
      const dist = haversineDistance(centerLat, centerLon, _sstCache.lat, _sstCache.lon);
      if (dist < REFRESH_DISTANCE_M) {
        setGeoJSON(_sstCache.geoJSON);
        return;
      }
    }

    // Check movement threshold
    if (lastCenter.current) {
      const dist = haversineDistance(centerLat, centerLon, lastCenter.current.lat, lastCenter.current.lon);
      if (dist < REFRESH_DISTANCE_M) return;
    }

    loadingRef.current = true;
    lastCenter.current = { lat: centerLat, lon: centerLon };
    onLoadStart?.();

    try {
      const grid = buildGrid(centerLat, centerLon);
      const points = await fetchSSTBatch(grid);
      if (points.length > 0) {
        const gj = sstToGeoJSON(points);
        _sstCache = { geoJSON: gj, lat: centerLat, lon: centerLon, ts: Date.now() };
        setGeoJSON(gj);
      } else {
        setGeoJSON(null);
      }
    } catch {
      // Keep previous data
    } finally {
      loadingRef.current = false;
      onLoadEnd?.();
    }
  }, [onLoadStart, onLoadEnd]);

  useEffect(() => {
    fetchData(lat, lon);
  }, [lat, lon, fetchData]);

  if (!geoJSON || geoJSON.features.length === 0) return null;

  return (
    <ShapeSource id="sst-overlay-source" shape={geoJSON}>
      {/* ── Full-coverage heat map fill ── */}
      <CircleLayer
        id="sst-heatmap-fill"
        style={{
          circleRadius: [
            'interpolate',
            ['exponential', 1.5],
            ['zoom'],
            4, 30,
            7, 50,
            10, 80,
            13, 140,
          ],
          circleColor: ['get', 'color'],
          circleOpacity: 0.50,
          circleBlur: 1,
          circlePitchAlignment: 'map',
        }}
      />

      {/* ── Inner intensity core ── */}
      <CircleLayer
        id="sst-heatmap-core"
        style={{
          circleRadius: [
            'interpolate',
            ['exponential', 1.5],
            ['zoom'],
            4, 10,
            7, 18,
            10, 30,
            13, 55,
          ],
          circleColor: ['get', 'color'],
          circleOpacity: 0.30,
          circleBlur: 0.7,
          circlePitchAlignment: 'map',
        }}
      />

      {/* ── Temperature labels at higher zoom ── */}
      <SymbolLayer
        id="sst-temp-labels"
        minZoomLevel={8}
        style={{
          textField: ['get', 'label'],
          textSize: 11,
          textColor: '#FFFFFF',
          textHaloColor: 'rgba(0, 0, 0, 0.55)',
          textHaloWidth: 1.5,
          textFont: ['Open Sans Bold'],
          textAllowOverlap: false,
        }}
      />
    </ShapeSource>
  );
}

// ── Legend data for external use ──────────────────────────────────────────────

export const SST_LEGEND_STOPS = [
  { color: '#0D47A1', label: '<50F' },
  { color: '#1E88E5', label: '57F' },
  { color: '#00897B', label: '64F' },
  { color: '#43A047', label: '70F' },
  { color: '#FDD835', label: '77F' },
  { color: '#FF9800', label: '82F' },
  { color: '#C62828', label: '85F+' },
];
