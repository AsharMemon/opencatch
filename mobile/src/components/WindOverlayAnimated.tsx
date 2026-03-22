/**
 * OpenCatch — Animated Wind Overlay (Windy-Style)
 *
 * Full-screen wind visualization inspired by Windy.app:
 *   1. Dense color heat map background via large blurred circles
 *      (blue=calm, cyan, green, yellow, orange, red=gale)
 *   2. Small white directional arrows simulating particle flow
 *   3. Speed badges at city-scale zoom (purple circles with "X m/s")
 *
 * Data: Open-Meteo forecast API — no API key needed.
 * Grid: ~0.10° spacing (~10 km) for heat map, denser than before.
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

interface Props {
  lat: number;
  lon: number;
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
}

// ── Constants ────────────────────────────────────────────────────────────────

const OPEN_METEO_BASE = 'https://api.open-meteo.com/v1/forecast';

/** Dense grid spacing for heat map (~10 km at mid-latitudes). */
const GRID_SPACING_DEG = 0.10;

/** Radius of the grid around center in degrees. */
const GRID_RADIUS_DEG = 1.2;

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
  const BATCH_SIZE = 25;
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
  ts: number;
} | null = null;

// ── Component ────────────────────────────────────────────────────────────────

export function WindOverlayAnimated({
  lat,
  lon,
  ShapeSource,
  SymbolLayer,
  CircleLayer,
  onLoadStart,
  onLoadEnd,
}: Props) {
  const [heatGeoJSON, setHeatGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const [arrowGeoJSON, setArrowGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const loadingRef = useRef(false);

  const fetchData = useCallback(async (centerLat: number, centerLon: number, force = false) => {
    if (loadingRef.current) return;

    // Check cache
    if (!force && _cache && Date.now() - _cache.ts < CACHE_TTL_MS) {
      const dist = haversineDistance(centerLat, centerLon, _cache.lat, _cache.lon);
      if (dist < REFRESH_DISTANCE_M) {
        setHeatGeoJSON(_cache.heatGeoJSON);
        setArrowGeoJSON(_cache.arrowGeoJSON);
        return;
      }
    }

    // Check if we moved enough
    if (!force && lastCenter.current) {
      const dist = haversineDistance(centerLat, centerLon, lastCenter.current.lat, lastCenter.current.lon);
      if (dist < REFRESH_DISTANCE_M) return;
    }

    loadingRef.current = true;
    lastCenter.current = { lat: centerLat, lon: centerLon };
    onLoadStart?.();

    try {
      const grid = buildGrid(centerLat, centerLon);
      const points = await fetchWindBatch(grid);
      if (points.length > 0) {
        const heat = windGridToHeatGeoJSON(points);
        const arrows = windGridToArrowGeoJSON(points);
        _cache = {
          heatGeoJSON: heat,
          arrowGeoJSON: arrows,
          lat: centerLat,
          lon: centerLon,
          ts: Date.now(),
        };
        setHeatGeoJSON(heat);
        setArrowGeoJSON(arrows);
      }
    } catch {
      // Keep previous data
    } finally {
      loadingRef.current = false;
      onLoadEnd?.();
    }
  }, [onLoadStart, onLoadEnd]);

  // Initial fetch and re-fetch on location change
  useEffect(() => {
    fetchData(lat, lon);
  }, [lat, lon, fetchData]);

  // Auto-refresh every 15 minutes
  useEffect(() => {
    refreshTimer.current = setInterval(() => {
      if (lastCenter.current) {
        fetchData(lastCenter.current.lat, lastCenter.current.lon, true);
      }
    }, REFRESH_INTERVAL_MS);

    return () => {
      if (refreshTimer.current) clearInterval(refreshTimer.current);
    };
  }, [fetchData]);

  if (!heatGeoJSON || heatGeoJSON.features.length === 0) return null;

  return (
    <>
      {/* Layer 1: Full-screen color heat map background */}
      <ShapeSource id="wind-heat-source" shape={heatGeoJSON}>
        {/* Large, heavily blurred circles create a continuous color field */}
        <CircleLayer
          id="wind-heat-field"
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.5],
              ['zoom'],
              4, 40,
              7, 60,
              10, 80,
              13, 120,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: 0.35,
            circleBlur: 1.0,
          }}
        />
        {/* Secondary smaller circles for color intensity at grid centers */}
        <CircleLayer
          id="wind-heat-core"
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.5],
              ['zoom'],
              4, 15,
              7, 25,
              10, 35,
              13, 50,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: 0.25,
            circleBlur: 0.6,
          }}
        />
      </ShapeSource>

      {/* Layer 2: White directional arrows (particle-flow look) */}
      {arrowGeoJSON && (
        <ShapeSource id="wind-arrow-source" shape={arrowGeoJSON}>
          <SymbolLayer
            id="wind-flow-arrows"
            style={{
              iconImage: 'triangle-11',
              iconSize: [
                'interpolate',
                ['linear'],
                ['get', 'speedKn'],
                0, 0.5,
                10, 0.7,
                20, 0.9,
                30, 1.1,
              ],
              iconRotate: ['get', 'iconRotation'],
              iconAllowOverlap: true,
              iconIgnorePlacement: true,
              iconColor: '#FFFFFF',
              iconOpacity: 0.85,
              iconHaloColor: 'rgba(0,0,0,0.3)',
              iconHaloWidth: 1,
            }}
          />

          {/* Layer 3: Speed badges at higher zoom (purple circles like Windy) */}
          <SymbolLayer
            id="wind-speed-badges"
            minZoomLevel={8}
            style={{
              textField: ['concat', ['get', 'label'], ' m/s'],
              textSize: [
                'interpolate',
                ['linear'],
                ['zoom'],
                8, 9,
                11, 11,
                14, 13,
              ],
              textColor: '#FFFFFF',
              textHaloColor: ['get', 'badgeColor'],
              textHaloWidth: 2.5,
              textFont: ['Open Sans Bold'],
              textOffset: [0, 1.8],
              textAnchor: 'top',
              textOptional: true,
              textAllowOverlap: false,
              textPadding: 12,
            }}
          />
        </ShapeSource>
      )}
    </>
  );
}
