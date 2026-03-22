/**
 * OpenCatch — Wave Height Overlay
 *
 * Visualizes wave height for coastal areas using the Open-Meteo Marine API.
 * Colored circles at grid points sized proportionally to wave height,
 * with small directional arrows for wave direction.
 *
 * Color scale (meters): blue (0-1m), green (1-2m), yellow (2-3m),
 *   orange (3-4m), red (4m+).
 *
 * Only visible when user is near coast.
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
}

// ── Constants ────────────────────────────────────────────────────────────────

const MARINE_API_BASE = 'https://marine-api.open-meteo.com/v1/marine';

/** Grid spacing in degrees (~30km for open ocean). */
const GRID_SPACING_DEG = 0.3;

/** Radius of the wave grid in degrees. */
const GRID_RADIUS_DEG = 1.5;

/** Minimum move distance before refetching (meters). */
const REFRESH_DISTANCE_M = 10000;

/** Cache TTL: 30 minutes. */
const CACHE_TTL_MS = 30 * 60 * 1000;

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

function waveColor(heightM: number): string {
  if (heightM < 1) return '#2196F3';  // blue
  if (heightM < 2) return '#4CAF50';  // green
  if (heightM < 3) return '#FFC107';  // yellow
  if (heightM < 4) return '#FF9800';  // orange
  return '#F44336';                    // red
}

function waveLabel(heightM: number): string {
  if (heightM < 0.5) return 'Flat';
  if (heightM < 1) return 'Calm';
  if (heightM < 2) return 'Moderate';
  if (heightM < 3) return 'Rough';
  if (heightM < 4) return 'Very Rough';
  return 'High';
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

        // Only include points that returned valid wave data (ocean points)
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
        description: waveLabel(p.heightM),
        // Circle radius proportional to wave height (min 4, max 16)
        radius: Math.min(16, Math.max(4, p.heightM * 4)),
      },
    })),
  };
}

// ── Cache ────────────────────────────────────────────────────────────────────

let _waveCache: { geoJSON: GeoJSON.FeatureCollection; lat: number; lon: number; ts: number } | null = null;

// ── Component ────────────────────────────────────────────────────────────────

export function WaveOverlayAnimated({ lat, lon, ShapeSource, CircleLayer, SymbolLayer }: Props) {
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
    }
  }, []);

  useEffect(() => {
    fetchData(lat, lon);
  }, [lat, lon, fetchData]);

  if (!geoJSON || geoJSON.features.length === 0) return null;

  return (
    <ShapeSource id="wave-overlay-source" shape={geoJSON}>
      {/* Colored circles — size proportional to wave height */}
      <CircleLayer
        id="wave-overlay-circles"
        style={{
          circleRadius: [
            'interpolate',
            ['linear'],
            ['get', 'heightM'],
            0, 4,
            1, 6,
            2, 9,
            3, 12,
            4, 15,
          ],
          circleColor: ['get', 'color'],
          circleStrokeColor: '#FFFFFF',
          circleStrokeWidth: 1,
          circleOpacity: 0.65,
        }}
      />
      {/* Wave direction arrows */}
      <SymbolLayer
        id="wave-overlay-arrows"
        minZoomLevel={7}
        style={{
          iconImage: 'triangle-11',
          iconSize: 0.7,
          iconRotate: ['get', 'iconRotation'],
          iconAllowOverlap: true,
          iconIgnorePlacement: true,
          iconColor: ['get', 'color'],
          iconOpacity: 0.7,
          iconOffset: [0, -20],
        }}
      />
      {/* Height labels at higher zoom */}
      <SymbolLayer
        id="wave-overlay-labels"
        minZoomLevel={8}
        style={{
          textField: ['get', 'label'],
          textSize: 10,
          textColor: ['get', 'color'],
          textHaloColor: 'rgba(255, 255, 255, 0.95)',
          textHaloWidth: 1.5,
          textFont: ['Open Sans Regular'],
          textOffset: [0, 2.0],
          textAnchor: 'top',
          textOptional: true,
          textAllowOverlap: false,
        }}
      />
    </ShapeSource>
  );
}
