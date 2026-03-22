/**
 * OpenCatch — Animated Wind Overlay
 *
 * Windy-style animated wind visualization on the map.
 * Fetches wind data from Open-Meteo and displays color-coded
 * directional arrows at a regular grid (~20km spacing).
 *
 * Color scale (knots): green (0-10), yellow (10-20),
 *   orange (20-30), red (30+).
 *
 * Updates every 15 minutes while active.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';

// ── Types ────────────────────────────────────────────────────────────────────

interface WindGridPoint {
  lat: number;
  lon: number;
  speedKn: number;
  directionDeg: number;
  gustKn?: number;
}

interface Props {
  lat: number;
  lon: number;
  ShapeSource: any;
  SymbolLayer: any;
  CircleLayer: any;
}

// ── Constants ────────────────────────────────────────────────────────────────

const OPEN_METEO_BASE = 'https://api.open-meteo.com/v1/forecast';

/** Grid spacing in degrees — approx 20km at mid-latitudes. */
const GRID_SPACING_DEG = 0.18;

/** Radius of the grid around center in degrees. */
const GRID_RADIUS_DEG = 1.0;

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

function windSpeedColorKn(kn: number): string {
  if (kn < 10) return '#4CAF50';   // green
  if (kn < 20) return '#FFC107';   // yellow
  if (kn < 30) return '#FF9800';   // orange
  return '#F44336';                 // red
}

function windLabel(kn: number): string {
  if (kn < 5) return 'Calm';
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
  };
}

async function fetchWindBatch(coords: Array<{ lat: number; lon: number }>): Promise<WindGridPoint[]> {
  // Open-Meteo supports comma-separated lat/lon for multiple locations
  // But let's batch in groups of ~25 to avoid URL length limits
  const BATCH_SIZE = 25;
  const results: WindGridPoint[] = [];

  for (let i = 0; i < coords.length; i += BATCH_SIZE) {
    const batch = coords.slice(i, i + BATCH_SIZE);
    const lats = batch.map((c) => c.lat.toFixed(4)).join(',');
    const lons = batch.map((c) => c.lon.toFixed(4)).join(',');

    const url =
      `${OPEN_METEO_BASE}?latitude=${lats}&longitude=${lons}` +
      '&hourly=windspeed_10m,winddirection_10m' +
      '&forecast_hours=1&wind_speed_unit=kmh';

    try {
      const res = await fetch(url);
      if (!res.ok) continue;

      const json = await res.json();

      // Open-Meteo returns an array when multiple locations, single object for one
      const items: OpenMeteoHourlyResponse[] = Array.isArray(json) ? json : [json];

      for (let j = 0; j < items.length; j++) {
        const item = items[j];
        const speedKmh = item.hourly?.windspeed_10m?.[0] ?? 0;
        const dirDeg = item.hourly?.winddirection_10m?.[0] ?? 0;

        results.push({
          lat: batch[j].lat,
          lon: batch[j].lon,
          speedKn: kmhToKnots(speedKmh),
          directionDeg: dirDeg,
        });
      }
    } catch {
      // Skip failed batch
    }
  }

  return results;
}

// ── GeoJSON conversion ───────────────────────────────────────────────────────

function windGridToGeoJSON(points: WindGridPoint[]): GeoJSON.FeatureCollection {
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
        directionDeg: p.directionDeg,
        // Arrow points in the direction wind blows TO (add 180 to meteorological "from")
        iconRotation: (p.directionDeg + 180) % 360,
        color: windSpeedColorKn(p.speedKn),
        label: `${Math.round(p.speedKn)} kn`,
        description: windLabel(p.speedKn),
      },
    })),
  };
}

// ── Cache ────────────────────────────────────────────────────────────────────

let _cache: { geoJSON: GeoJSON.FeatureCollection; lat: number; lon: number; ts: number } | null = null;

// ── Component ────────────────────────────────────────────────────────────────

export function WindOverlayAnimated({ lat, lon, ShapeSource, SymbolLayer, CircleLayer }: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const loadingRef = useRef(false);

  const fetchData = useCallback(async (centerLat: number, centerLon: number, force = false) => {
    if (loadingRef.current) return;

    // Check cache
    if (!force && _cache && Date.now() - _cache.ts < CACHE_TTL_MS) {
      const dist = haversineDistance(centerLat, centerLon, _cache.lat, _cache.lon);
      if (dist < REFRESH_DISTANCE_M) {
        setGeoJSON(_cache.geoJSON);
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

    try {
      const grid = buildGrid(centerLat, centerLon);
      const points = await fetchWindBatch(grid);
      if (points.length > 0) {
        const gj = windGridToGeoJSON(points);
        _cache = { geoJSON: gj, lat: centerLat, lon: centerLon, ts: Date.now() };
        setGeoJSON(gj);
      }
    } catch {
      // Keep previous data
    } finally {
      loadingRef.current = false;
    }
  }, []);

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

  if (!geoJSON || geoJSON.features.length === 0) return null;

  return (
    <ShapeSource id="wind-animated-source" shape={geoJSON}>
      {/* Base dot at each grid point */}
      <CircleLayer
        id="wind-animated-dots"
        style={{
          circleRadius: 3,
          circleColor: ['get', 'color'],
          circleOpacity: 0.4,
        }}
      />
      {/* Directional arrows — color-coded by speed */}
      <SymbolLayer
        id="wind-animated-arrows"
        style={{
          iconImage: 'triangle-11',
          iconSize: [
            'interpolate',
            ['linear'],
            ['get', 'speedKn'],
            0, 0.8,
            10, 1.0,
            20, 1.3,
            30, 1.6,
          ],
          iconRotate: ['get', 'iconRotation'],
          iconAllowOverlap: true,
          iconIgnorePlacement: true,
          iconColor: ['get', 'color'],
          iconOpacity: 0.85,
        }}
      />
      {/* Speed labels at higher zoom */}
      <SymbolLayer
        id="wind-animated-labels"
        minZoomLevel={9}
        style={{
          textField: ['get', 'label'],
          textSize: 9,
          textColor: ['get', 'color'],
          textHaloColor: 'rgba(255, 255, 255, 0.9)',
          textHaloWidth: 1,
          textFont: ['Open Sans Regular'],
          textOffset: [0, 1.8],
          textAnchor: 'top',
          textOptional: true,
          textAllowOverlap: false,
        }}
      />
    </ShapeSource>
  );
}
