/**
 * OpenCatch — Wind Overlay
 *
 * The old version behaved like a soft heatmap with a few arrows on top.
 * This version flips that: the wind field should read as a dense vector net
 * first, with only a faint speed tint behind it.
 *
 * Data: Open-Meteo forecast API — no API key needed.
 * Updates every 15 minutes while active.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';

// ── Types ────────────────────────────────────────────────────────────────────

interface WindGridPoint {
  lat: number;
  lon: number;
  speedKn: number;
  speedMs: number;
  directionDeg: number;
  gustKn?: number;
}

interface WindViewportBounds {
  minLat: number;
  minLon: number;
  maxLat: number;
  maxLon: number;
}

interface Props {
  lat: number;
  lon: number;
  bounds?: WindViewportBounds | null;
  ShapeSource: any;
  SymbolLayer: any;
  CircleLayer: any;
  /** Optional: also pass RasterSource / RasterLayer for OWM tiles */
  RasterSource?: any;
  RasterLayer?: any;
  /** Called when the overlay begins loading data */
  onLoadStart?: () => void;
  /** Called when the overlay finishes loading data */
  onLoadEnd?: () => void;
  /** Called when fresh or cached wind vectors are ready */
  onDataLoaded?: (payload: { vectorCount: number; center: { lat: number; lon: number } }) => void;
}

// ── Constants ────────────────────────────────────────────────────────────────

const OPEN_METEO_BASE = 'https://api.open-meteo.com/v1/forecast';

const DEFAULT_GRID_RADIUS_DEG = 1.4;
const TARGET_GRID_COLUMNS_NEAR = 18;
const TARGET_GRID_COLUMNS_MEDIUM = 22;
const TARGET_GRID_COLUMNS_FAR = 26;
const TARGET_GRID_ROWS_NEAR = 14;
const TARGET_GRID_ROWS_MEDIUM = 16;
const TARGET_GRID_ROWS_FAR = 18;
const MIN_GRID_SPACING_DEG = 0.025;
const MAX_GRID_SPACING_DEG = 0.9;

/** Refresh interval: 15 minutes. */
const REFRESH_INTERVAL_MS = 15 * 60 * 1000;

/** Minimum move distance before refetching (meters). */
const REFRESH_DISTANCE_M = 8000;

/** Cache TTL: 15 minutes. */
const CACHE_TTL_MS = 15 * 60 * 1000;

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

/** Convert km/h to knots. */
function kmhToKnots(kmh: number): number {
  return kmh * 0.539957;
}

/** Convert km/h to m/s. */
function kmhToMs(kmh: number): number {
  return kmh / 3.6;
}

/**
 * Windy-style color scale for wind speed (knots).
 * blue (calm) -> cyan -> green -> yellow -> orange -> red (strong)
 */
function windyHeatColor(kn: number): string {
  if (kn < 3)  return '#3B82F6';  // blue — calm
  if (kn < 7)  return '#06B6D4';  // cyan — light breeze
  if (kn < 12) return '#22C55E';  // green — gentle breeze
  if (kn < 18) return '#EAB308';  // yellow — moderate
  if (kn < 25) return '#F97316';  // orange — fresh-strong
  if (kn < 35) return '#EF4444';  // red — gale warning
  return '#DC2626';                // deep red — storm
}

/** Badge color for speed labels. */
function badgeColor(kn: number): string {
  if (kn < 12) return '#6366F1'; // indigo
  if (kn < 20) return '#7C3AED'; // purple
  return '#DC2626';               // red
}

function windLabel(kn: number): string {
  if (kn < 5)  return 'Calm';
  if (kn < 10) return 'Light';
  if (kn < 20) return 'Moderate';
  if (kn < 30) return 'Strong';
  return 'Gale';
}

// ── Build grid coordinates ───────────────────────────────────────────────────

function normalizeBounds(
  centerLat: number,
  centerLon: number,
  bounds?: WindViewportBounds | null,
): WindViewportBounds {
  if (
    bounds &&
    Number.isFinite(bounds.minLat) &&
    Number.isFinite(bounds.minLon) &&
    Number.isFinite(bounds.maxLat) &&
    Number.isFinite(bounds.maxLon) &&
    bounds.maxLat > bounds.minLat &&
    bounds.maxLon > bounds.minLon
  ) {
    return bounds;
  }

  return {
    minLat: centerLat - DEFAULT_GRID_RADIUS_DEG,
    maxLat: centerLat + DEFAULT_GRID_RADIUS_DEG,
    minLon: centerLon - DEFAULT_GRID_RADIUS_DEG,
    maxLon: centerLon + DEFAULT_GRID_RADIUS_DEG,
  };
}

function boundsKey(bounds: WindViewportBounds): string {
  return [
    bounds.minLat.toFixed(2),
    bounds.minLon.toFixed(2),
    bounds.maxLat.toFixed(2),
    bounds.maxLon.toFixed(2),
  ].join(':');
}

function buildGrid(
  centerLat: number,
  centerLon: number,
  bounds?: WindViewportBounds | null,
): Array<{ lat: number; lon: number }> {
  const coords: Array<{ lat: number; lon: number }> = [];
  const seen = new Set<string>();
  const frame = normalizeBounds(centerLat, centerLon, bounds);
  const latSpan = Math.max(frame.maxLat - frame.minLat, 0.2);
  const lonSpan = Math.max(frame.maxLon - frame.minLon, 0.2);
  const maxSpan = Math.max(latSpan, lonSpan);
  const targetColumns =
    maxSpan > 10 ? TARGET_GRID_COLUMNS_FAR : maxSpan > 4 ? TARGET_GRID_COLUMNS_MEDIUM : TARGET_GRID_COLUMNS_NEAR;
  const targetRows =
    maxSpan > 10 ? TARGET_GRID_ROWS_FAR : maxSpan > 4 ? TARGET_GRID_ROWS_MEDIUM : TARGET_GRID_ROWS_NEAR;
  const latSpacing = Math.min(MAX_GRID_SPACING_DEG, Math.max(MIN_GRID_SPACING_DEG, latSpan / targetRows));
  const lonSpacing = Math.min(MAX_GRID_SPACING_DEG, Math.max(MIN_GRID_SPACING_DEG, lonSpan / targetColumns));

  let rowIndex = 0;
  for (let lat = frame.minLat; lat <= frame.maxLat + 1e-9; lat += latSpacing) {
    const stagger = rowIndex % 2 === 0 ? 0 : lonSpacing / 2;
    for (let lon = frame.minLon - stagger; lon <= frame.maxLon + lonSpacing + 1e-9; lon += lonSpacing) {
      const clampedLon = Math.min(frame.maxLon, Math.max(frame.minLon, lon));
      const point = {
        lat: parseFloat(lat.toFixed(4)),
        lon: parseFloat(clampedLon.toFixed(4)),
      };
      const key = `${point.lat}:${point.lon}`;
      if (!seen.has(key)) {
        seen.add(key);
        coords.push(point);
      }
    }
    rowIndex += 1;
  }
  return coords;
}

// ── Fetch wind for a batch of coordinates using Open-Meteo multi-location ───

interface OpenMeteoHourlyResponse {
  latitude: number;
  longitude: number;
  hourly?: {
    windspeed_10m?: number[];
    winddirection_10m?: number[];
    windgusts_10m?: number[];
  };
}

async function fetchWindBatch(coords: Array<{ lat: number; lon: number }>): Promise<WindGridPoint[]> {
  const BATCH_SIZE = 40;
  const results: WindGridPoint[] = [];

  for (let i = 0; i < coords.length; i += BATCH_SIZE) {
    const batch = coords.slice(i, i + BATCH_SIZE);
    const lats = batch.map((c) => c.lat.toFixed(4)).join(',');
    const lons = batch.map((c) => c.lon.toFixed(4)).join(',');

    const url =
      `${OPEN_METEO_BASE}?latitude=${lats}&longitude=${lons}` +
      '&hourly=windspeed_10m,winddirection_10m,windgusts_10m' +
      '&forecast_hours=1&wind_speed_unit=kmh';

    try {
      const res = await fetch(url);
      if (!res.ok) continue;

      const json = await res.json();
      const items: OpenMeteoHourlyResponse[] = Array.isArray(json) ? json : [json];

      for (let j = 0; j < items.length; j++) {
        const item = items[j];
        const speedKmh = item.hourly?.windspeed_10m?.[0] ?? 0;
        const dirDeg = item.hourly?.winddirection_10m?.[0] ?? 0;
        const gustKmh = item.hourly?.windgusts_10m?.[0];

        results.push({
          lat: batch[j].lat,
          lon: batch[j].lon,
          speedKn: kmhToKnots(speedKmh),
          speedMs: kmhToMs(speedKmh),
          directionDeg: dirDeg,
          gustKn: gustKmh != null ? kmhToKnots(gustKmh) : undefined,
        });
      }
    } catch {
      // Skip failed batch
    }
  }

  return results;
}

// ── GeoJSON conversion — heat map layer ─────────────────────────────────────

function windGridToHeatGeoJSON(points: WindGridPoint[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: points.map((p) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [p.lon, p.lat],
      },
      properties: {
        speedKn: Math.round(p.speedKn * 10) / 10,
        color: windyHeatColor(p.speedKn),
      },
    })),
  };
}

// ── GeoJSON conversion — arrow layer (white directional arrows) ─────────────

function windGridToArrowGeoJSON(points: WindGridPoint[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: points.map((p) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [p.lon, p.lat],
      },
      properties: {
        speedKn: Math.round(p.speedKn * 10) / 10,
        speedMs: Math.round(p.speedMs * 10) / 10,
        directionDeg: p.directionDeg,
        iconRotation: (p.directionDeg + 180) % 360,
        label: `${Math.round(p.speedMs)}`,
        badgeColor: badgeColor(p.speedKn),
        arrowColor: windyHeatColor(p.speedKn),
        description: windLabel(p.speedKn),
      },
    })),
  };
}

// ── Cache ────────────────────────────────────────────────────────────────────

let _cache: {
  heatGeoJSON: GeoJSON.FeatureCollection;
  arrowGeoJSON: GeoJSON.FeatureCollection;
  lat: number;
  lon: number;
  extentKey: string;
  ts: number;
} | null = null;

// ── Component ────────────────────────────────────────────────────────────────

export function WindOverlayAnimated({
  lat,
  lon,
  bounds = null,
  ShapeSource,
  SymbolLayer,
  CircleLayer,
  onLoadStart,
  onLoadEnd,
  onDataLoaded,
}: Props) {
  const [heatGeoJSON, setHeatGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const [arrowGeoJSON, setArrowGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);
  const lastExtentKey = useRef<string | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const loadingRef = useRef(false);

  const fetchData = useCallback(async (
    centerLat: number,
    centerLon: number,
    viewportBounds?: WindViewportBounds | null,
    force = false,
  ) => {
    if (loadingRef.current) return;
    const normalizedBounds = normalizeBounds(centerLat, centerLon, viewportBounds);
    const extent = boundsKey(normalizedBounds);

    // Check cache
    if (!force && _cache && Date.now() - _cache.ts < CACHE_TTL_MS) {
      const dist = haversineDistance(centerLat, centerLon, _cache.lat, _cache.lon);
      if (dist < REFRESH_DISTANCE_M && _cache.extentKey === extent) {
        setHeatGeoJSON(_cache.heatGeoJSON);
        setArrowGeoJSON(_cache.arrowGeoJSON);
        onDataLoaded?.({
          vectorCount: _cache.arrowGeoJSON.features.length,
          center: { lat: _cache.lat, lon: _cache.lon },
        });
        return;
      }
    }

    // Check if we moved enough
    if (!force && lastCenter.current) {
      const dist = haversineDistance(centerLat, centerLon, lastCenter.current.lat, lastCenter.current.lon);
      if (dist < REFRESH_DISTANCE_M && lastExtentKey.current === extent) return;
    }

    loadingRef.current = true;
    lastCenter.current = { lat: centerLat, lon: centerLon };
    lastExtentKey.current = extent;
    onLoadStart?.();

    try {
      const grid = buildGrid(centerLat, centerLon, normalizedBounds);
      const points = await fetchWindBatch(grid);
      if (points.length > 0) {
        const heat = windGridToHeatGeoJSON(points);
        const arrows = windGridToArrowGeoJSON(points);
        _cache = {
          heatGeoJSON: heat,
          arrowGeoJSON: arrows,
          lat: centerLat,
          lon: centerLon,
          extentKey: extent,
          ts: Date.now(),
        };
        setHeatGeoJSON(heat);
        setArrowGeoJSON(arrows);
        onDataLoaded?.({
          vectorCount: arrows.features.length,
          center: { lat: centerLat, lon: centerLon },
        });
      }
    } catch {
      // Keep previous data
    } finally {
      loadingRef.current = false;
      onLoadEnd?.();
    }
  }, [onDataLoaded, onLoadStart, onLoadEnd]);

  // Initial fetch and re-fetch on location change
  useEffect(() => {
    fetchData(lat, lon, bounds);
  }, [bounds, lat, lon, fetchData]);

  // Auto-refresh every 15 minutes
  useEffect(() => {
    refreshTimer.current = setInterval(() => {
      if (lastCenter.current) {
        fetchData(lastCenter.current.lat, lastCenter.current.lon, bounds, true);
      }
    }, REFRESH_INTERVAL_MS);

    return () => {
      if (refreshTimer.current) clearInterval(refreshTimer.current);
    };
  }, [bounds, fetchData]);

  if ((!arrowGeoJSON || arrowGeoJSON.features.length === 0) && (!heatGeoJSON || heatGeoJSON.features.length === 0)) {
    return null;
  }

  return (
    <>
      {heatGeoJSON ? (
        <ShapeSource id="wind-heat-source" shape={heatGeoJSON}>
          <CircleLayer
            id="wind-heat-field"
            style={{
              circleRadius: [
                'interpolate',
                ['exponential', 1.22],
                ['zoom'],
                4, 8,
                7, 11,
                10, 14,
                13, 17,
              ],
              circleColor: ['get', 'color'],
              circleOpacity: [
                'interpolate',
                ['linear'],
                ['get', 'speedKn'],
                0, 0.003,
                10, 0.006,
                20, 0.009,
                35, 0.012,
              ],
              circleBlur: 0.95,
            }}
          />
        </ShapeSource>
      ) : null}

      {arrowGeoJSON && (
        <ShapeSource id="wind-arrow-source" shape={arrowGeoJSON}>
          <SymbolLayer
            id="wind-flow-arrows-backdrop"
            style={{
              iconImage: 'triangle-11',
              iconSize: [
                'interpolate',
                ['linear'],
                ['zoom'],
                3, 0.22,
                6, 0.26,
                9, 0.31,
                12, 0.38,
                14, 0.44,
              ],
              iconRotate: ['get', 'iconRotation'],
              iconAllowOverlap: true,
              iconIgnorePlacement: true,
              iconColor: 'rgba(255,255,255,0.74)',
              iconOpacity: 0.32,
              iconPitchAlignment: 'map',
              iconRotationAlignment: 'map',
            }}
          />

          <SymbolLayer
            id="wind-flow-arrows"
            style={{
              iconImage: 'triangle-11',
              iconSize: [
                'interpolate',
                ['linear'],
                ['zoom'],
                3, 0.18,
                6, 0.22,
                9, 0.28,
                12, 0.34,
                14, 0.4,
              ],
              iconRotate: ['get', 'iconRotation'],
              iconAllowOverlap: true,
              iconIgnorePlacement: true,
              iconColor: ['get', 'arrowColor'],
              iconOpacity: 0.95,
              iconHaloColor: 'rgba(7, 27, 39, 0.08)',
              iconHaloWidth: 0.2,
              iconPitchAlignment: 'map',
              iconRotationAlignment: 'map',
            }}
          />

          <SymbolLayer
            id="wind-speed-badges"
            minZoomLevel={13.6}
            style={{
              textField: ['concat', ['get', 'label'], ' m/s'],
              textSize: [
                'interpolate',
                ['linear'],
                ['zoom'],
                12.8, 9,
                14, 10.5,
              ],
              textColor: '#FFFFFF',
              textHaloColor: ['get', 'badgeColor'],
              textHaloWidth: 2.1,
              textFont: ['Open Sans Bold'],
              textOffset: [0, 1.45],
              textAnchor: 'top',
              textOptional: true,
              textAllowOverlap: false,
              textPadding: 8,
            }}
          />
        </ShapeSource>
      )}
    </>
  );
}
