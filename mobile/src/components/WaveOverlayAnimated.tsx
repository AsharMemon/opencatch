/**
 * OpenCatch -- Wave Height Overlay (Windy-Quality)
 *
 * Full-screen color heat map covering ocean areas, like Windy's wave view.
 * Fetches a dense grid from the Open-Meteo Marine API and renders each cell
 * as a large semi-transparent blurred circle that blends with neighbors to
 * create a continuous color surface. Wave direction arrows overlaid on top.
 *
 * Color scale (meters):
 *   Deep Blue (0-0.5m) -> Blue (0.5-1m) -> Cyan (1-1.5m) ->
 *   Green (1.5-2m) -> Yellow (2-3m) -> Orange (3-4m) -> Red (4m+)
 *
 * Only visible near the coast.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';

// ── Types ────────────────────────────────────────────────────────────────────

interface WavePoint {
  lat: number;
  lon: number;
  heightM: number;
  periodS: number;
  directionDeg: number;
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

/**
 * Dense grid spacing -- ~15km for smooth heat map fill.
 * Smaller than before (was 0.3) to create continuous coverage.
 */
const GRID_SPACING_DEG = 0.15;

/** Radius of the wave grid in degrees (~2 degrees for wide coverage). */
const GRID_RADIUS_DEG = 2.0;

/** Minimum move distance before refetching (meters). */
const REFRESH_DISTANCE_M = 8000;

/** Cache TTL: 30 minutes. */
const CACHE_TTL_MS = 30 * 60 * 1000;

// ── Color scale (Windy-style rich gradient) ─────────────────────────────────

function waveColor(heightM: number): string {
  if (heightM < 0.25) return '#1A237E'; // Deep navy
  if (heightM < 0.5) return '#1565C0';  // Dark blue
  if (heightM < 1.0) return '#2196F3';  // Blue
  if (heightM < 1.5) return '#00BCD4';  // Cyan
  if (heightM < 2.0) return '#4CAF50';  // Green
  if (heightM < 2.5) return '#8BC34A';  // Light green
  if (heightM < 3.0) return '#FFC107';  // Amber
  if (heightM < 3.5) return '#FF9800';  // Orange
  if (heightM < 4.0) return '#FF5722';  // Deep orange
  return '#D50000';                      // Red
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

// ── Fetch wave data ──────────────────────────────────────────────────────────

interface MarineHourlyResponse {
  latitude: number;
  longitude: number;
  hourly?: {
    wave_height?: number[];
    wave_period?: number[];
    wave_direction?: number[];
  };
}

async function fetchWaveBatch(coords: Array<{ lat: number; lon: number }>): Promise<WavePoint[]> {
  const BATCH_SIZE = 20;
  const results: WavePoint[] = [];

  for (let i = 0; i < coords.length; i += BATCH_SIZE) {
    const batch = coords.slice(i, i + BATCH_SIZE);
    const lats = batch.map((c) => c.lat.toFixed(4)).join(',');
    const lons = batch.map((c) => c.lon.toFixed(4)).join(',');

    const url =
      `${MARINE_API_BASE}?latitude=${lats}&longitude=${lons}` +
      '&hourly=wave_height,wave_period,wave_direction' +
      '&forecast_hours=1';

    try {
      const res = await fetch(url);
      if (!res.ok) continue;

      const json = await res.json();
      const items: MarineHourlyResponse[] = Array.isArray(json) ? json : [json];

      for (let j = 0; j < items.length; j++) {
        const item = items[j];
        const heightM = item.hourly?.wave_height?.[0];
        const periodS = item.hourly?.wave_period?.[0] ?? 0;
        const dirDeg = item.hourly?.wave_direction?.[0] ?? 0;

        if (heightM != null && heightM >= 0) {
          results.push({
            lat: batch[j].lat,
            lon: batch[j].lon,
            heightM,
            periodS,
            directionDeg: dirDeg,
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

function waveGridToGeoJSON(points: WavePoint[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: points.map((p) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [p.lon, p.lat],
      },
      properties: {
        heightM: Math.round(p.heightM * 10) / 10,
        periodS: Math.round(p.periodS),
        directionDeg: p.directionDeg,
        iconRotation: (p.directionDeg + 180) % 360,
        color: waveColor(p.heightM),
        label: `${p.heightM.toFixed(1)}m`,
      },
    })),
  };
}

// ── Cache ────────────────────────────────────────────────────────────────────

let _waveCache: { geoJSON: GeoJSON.FeatureCollection; lat: number; lon: number; ts: number } | null = null;

// ── Component ────────────────────────────────────────────────────────────────

export function WaveOverlayAnimated({ lat, lon, ShapeSource, CircleLayer, SymbolLayer, onLoadStart, onLoadEnd }: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);
  const loadingRef = useRef(false);

  const fetchData = useCallback(async (centerLat: number, centerLon: number) => {
    if (loadingRef.current) return;

    // Check cache
    if (_waveCache && Date.now() - _waveCache.ts < CACHE_TTL_MS) {
      const dist = haversineDistance(centerLat, centerLon, _waveCache.lat, _waveCache.lon);
      if (dist < REFRESH_DISTANCE_M) {
        setGeoJSON(_waveCache.geoJSON);
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
      const points = await fetchWaveBatch(grid);
      if (points.length > 0) {
        const gj = waveGridToGeoJSON(points);
        _waveCache = { geoJSON: gj, lat: centerLat, lon: centerLon, ts: Date.now() };
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
    <ShapeSource id="wave-overlay-source" shape={geoJSON}>
      {/* ── Heat map fill: large blurred circles that overlap to form a
           continuous color surface across the ocean ── */}
      <CircleLayer
        id="wave-heatmap-fill"
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
          circleOpacity: 0.55,
          circleBlur: 1,
          circlePitchAlignment: 'map',
          circleTranslate: [0, 0],
        }}
      />

      {/* ── Inner intensity core: brighter center for depth ── */}
      <CircleLayer
        id="wave-heatmap-core"
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
          circleOpacity: 0.35,
          circleBlur: 0.8,
          circlePitchAlignment: 'map',
        }}
      />

      {/* ── Wave direction arrows at medium-high zoom ── */}
      <SymbolLayer
        id="wave-overlay-arrows"
        minZoomLevel={6}
        style={{
          iconImage: 'triangle-11',
          iconSize: [
            'interpolate',
            ['linear'],
            ['zoom'],
            6, 0.5,
            9, 0.8,
            12, 1.1,
          ],
          iconRotate: ['get', 'iconRotation'],
          iconAllowOverlap: true,
          iconIgnorePlacement: true,
          iconColor: '#FFFFFF',
          iconOpacity: 0.7,
          iconHaloColor: ['get', 'color'],
          iconHaloWidth: 1,
        }}
      />

      {/* ── Height labels at higher zoom ── */}
      <SymbolLayer
        id="wave-overlay-labels"
        minZoomLevel={9}
        style={{
          textField: ['get', 'label'],
          textSize: 11,
          textColor: '#FFFFFF',
          textHaloColor: 'rgba(0, 0, 0, 0.6)',
          textHaloWidth: 1.5,
          textFont: ['Open Sans Bold'],
          textOffset: [0, 2.2],
          textAnchor: 'top',
          textOptional: true,
          textAllowOverlap: false,
        }}
      />
    </ShapeSource>
  );
}

// ── Legend data for external use ──────────────────────────────────────────────

export const WAVE_LEGEND_STOPS = [
  { color: '#1A237E', label: '0m' },
  { color: '#2196F3', label: '1m' },
  { color: '#00BCD4', label: '1.5m' },
  { color: '#4CAF50', label: '2m' },
  { color: '#FFC107', label: '3m' },
  { color: '#FF5722', label: '4m' },
  { color: '#D50000', label: '4m+' },
];
