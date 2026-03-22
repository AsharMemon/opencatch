/**
 * OpenCatch — Tidal Current Overlay
 *
 * Animated tidal current arrows on the map from NOAA CO-OPS
 * Currents Predictions API. Shows arrow direction, speed-based
 * color coding, and slack/ebb/flood state labels at higher zoom.
 *
 * Only visible near the coast. Refreshes every 30 minutes.
 */

import React, { useEffect, useRef, useState } from 'react';
import { palette } from '../theme/palette';

// ── Types ────────────────────────────────────────────────────────

interface TidalCurrentStation {
  id: string;
  name: string;
  lat: number;
  lon: number;
}

interface TidalCurrentPrediction {
  stationId: string;
  stationName: string;
  lat: number;
  lon: number;
  /** Current speed in knots. */
  speedKnots: number;
  /** Current direction in degrees (0-360, direction current flows toward). */
  directionDeg: number;
  /** Tidal state: slack, flood, or ebb. */
  state: 'slack' | 'flood' | 'ebb';
  /** ISO timestamp of the prediction. */
  time: string;
}

interface Props {
  lat: number;
  lon: number;
  zoom: number;
  ShapeSource: any;
  CircleLayer: any;
  SymbolLayer: any;
  onLoadStart?: () => void;
  onLoadEnd?: () => void;
}

// ── Constants ────────────────────────────────────────────────────

const NOAA_BASE = 'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter';
const NOAA_STATIONS_BASE = 'https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json';
const SEARCH_RADIUS_DEG = 1.0; // ~60 nm
const REFRESH_DISTANCE_DEG = 0.3;
const REFRESH_INTERVAL_MS = 30 * 60 * 1000; // 30 minutes
const REQUEST_TIMEOUT_MS = 12_000;

/** Speed-to-color mapping for current arrows. */
function speedColor(knots: number): string {
  if (knots < 0.3) return '#9E9E9E'; // Slack — gray
  if (knots < 1.0) return '#4CAF50'; // Green
  if (knots < 2.0) return '#FFC107'; // Yellow
  if (knots < 3.0) return '#FF9800'; // Orange
  return '#F44336';                   // Red
}

/** Determine tidal state from speed and type. */
function determineTidalState(speed: number, type?: string): 'slack' | 'flood' | 'ebb' {
  if (speed < 0.1) return 'slack';
  if (type?.toLowerCase().includes('flood')) return 'flood';
  if (type?.toLowerCase().includes('ebb')) return 'ebb';
  return speed > 0 ? 'flood' : 'ebb';
}

// ── Haversine ────────────────────────────────────────────────────

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

// ── Fetch helper ─────────────────────────────────────────────────

async function fetchJSON<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

// ── NOAA API ─────────────────────────────────────────────────────

/** Find tidal current stations near a position. */
async function findNearbyCurrentStations(
  lat: number,
  lon: number,
): Promise<TidalCurrentStation[]> {
  try {
    // Use NOAA metadata API to find current stations
    const url = `${NOAA_STATIONS_BASE}?type=currentpredictions&units=english`;
    const data = await fetchJSON<{
      stations?: Array<{
        id: string;
        name: string;
        lat: number;
        lng: number;
      }>;
    }>(url);

    if (!data.stations) return [];

    // Filter to nearby stations
    return data.stations
      .filter((s) => {
        const dlat = Math.abs(s.lat - lat);
        const dlon = Math.abs(s.lng - lon);
        return dlat < SEARCH_RADIUS_DEG && dlon < SEARCH_RADIUS_DEG;
      })
      .map((s) => ({
        id: s.id,
        name: s.name,
        lat: s.lat,
        lon: s.lng,
      }))
      .slice(0, 20); // Limit to 20 stations
  } catch (err) {
    console.warn('[TidalCurrentOverlay] Station search failed:', err);
    return [];
  }
}

/** Fetch current predictions for a station. */
async function fetchCurrentPrediction(
  station: TidalCurrentStation,
): Promise<TidalCurrentPrediction | null> {
  try {
    const now = new Date();
    const beginDate = now.toISOString().slice(0, 10).replace(/-/g, '');
    const begin = `${beginDate} ${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`;

    // Fetch next hour of predictions
    const endDate = new Date(now.getTime() + 3600_000);
    const end = `${endDate.toISOString().slice(0, 10).replace(/-/g, '')} ${endDate.getHours().toString().padStart(2, '0')}:${endDate.getMinutes().toString().padStart(2, '0')}`;

    const params = new URLSearchParams({
      product: 'currents_predictions',
      begin_date: beginDate,
      range: '1',
      station: station.id,
      time_zone: 'gmt',
      interval: '6',
      units: 'english',
      format: 'json',
    });

    const url = `${NOAA_BASE}?${params.toString()}`;
    const data = await fetchJSON<{
      current_predictions?: {
        cp?: Array<{
          Time: string;
          Speed: number;
          Direction?: string;
          Bin?: string;
          Depth?: string;
          Mean_Flood_Dir?: number;
          Mean_Ebb_Dir?: number;
          Type?: string;
        }>;
      };
    }>(url);

    const predictions = data.current_predictions?.cp;
    if (!predictions || predictions.length === 0) return null;

    // Find the prediction closest to current time
    const nowMs = now.getTime();
    let closest = predictions[0];
    let minDiff = Math.abs(new Date(closest.Time).getTime() - nowMs);

    for (const p of predictions) {
      const diff = Math.abs(new Date(p.Time).getTime() - nowMs);
      if (diff < minDiff) {
        minDiff = diff;
        closest = p;
      }
    }

    const speed = Math.abs(closest.Speed ?? 0);
    const directionStr = closest.Direction;
    let direction = 0;

    if (directionStr != null && !isNaN(Number(directionStr))) {
      direction = Number(directionStr);
    } else if (closest.Mean_Flood_Dir != null && speed > 0.1) {
      direction = closest.Type?.toLowerCase().includes('ebb')
        ? (closest.Mean_Ebb_Dir ?? closest.Mean_Flood_Dir + 180) % 360
        : closest.Mean_Flood_Dir;
    }

    return {
      stationId: station.id,
      stationName: station.name,
      lat: station.lat,
      lon: station.lon,
      speedKnots: speed,
      directionDeg: direction,
      state: determineTidalState(speed, closest.Type),
      time: closest.Time,
    };
  } catch (err) {
    console.warn(`[TidalCurrentOverlay] Prediction fetch failed for ${station.id}:`, err);
    return null;
  }
}

// ── Build GeoJSON ────────────────────────────────────────────────

function buildGeoJSON(predictions: TidalCurrentPrediction[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: predictions.map((p) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [p.lon, p.lat],
      },
      properties: {
        stationId: p.stationId,
        stationName: p.stationName,
        speedKnots: p.speedKnots,
        speedLabel: `${p.speedKnots.toFixed(1)} kn`,
        directionDeg: p.directionDeg,
        state: p.state,
        stateLabel: p.state.charAt(0).toUpperCase() + p.state.slice(1),
        color: speedColor(p.speedKnots),
        // Arrow size scales with speed
        arrowSize: Math.max(0.6, Math.min(1.5, p.speedKnots / 2)),
      },
    })),
  };
}

// ── Component ────────────────────────────────────────────────────

export function TidalCurrentOverlay({
  lat,
  lon,
  zoom,
  ShapeSource,
  CircleLayer,
  SymbolLayer,
  onLoadStart,
  onLoadEnd,
}: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadPredictions = async (centerLat: number, centerLon: number) => {
    onLoadStart?.();
    try {
      const stations = await findNearbyCurrentStations(centerLat, centerLon);
      if (stations.length === 0) {
        setGeoJSON(null);
        return;
      }

      // Fetch predictions for all stations in parallel
      const results = await Promise.all(stations.map(fetchCurrentPrediction));
      const valid = results.filter((r): r is TidalCurrentPrediction => r !== null);

      if (valid.length > 0) {
        setGeoJSON(buildGeoJSON(valid));
      } else {
        setGeoJSON(null);
      }

      lastCenter.current = { lat: centerLat, lon: centerLon };
    } catch (err) {
      console.warn('[TidalCurrentOverlay] Load failed:', err);
    } finally {
      onLoadEnd?.();
    }
  };

  // Initial load and refresh on significant position change
  useEffect(() => {
    if (
      !lastCenter.current ||
      Math.abs(lat - lastCenter.current.lat) > REFRESH_DISTANCE_DEG ||
      Math.abs(lon - lastCenter.current.lon) > REFRESH_DISTANCE_DEG
    ) {
      loadPredictions(lat, lon);
    }
  }, [lat, lon]);

  // Periodic refresh every 30 minutes
  useEffect(() => {
    refreshTimer.current = setInterval(() => {
      if (lastCenter.current) {
        loadPredictions(lastCenter.current.lat, lastCenter.current.lon);
      }
    }, REFRESH_INTERVAL_MS);

    return () => {
      if (refreshTimer.current) clearInterval(refreshTimer.current);
    };
  }, []);

  if (!geoJSON || !ShapeSource || !CircleLayer || !SymbolLayer) return null;

  const showLabels = zoom >= 10;

  return (
    <>
      <ShapeSource
        id="tidal-currents-source"
        shape={geoJSON}
      >
        {/* Outer glow for visibility */}
        <CircleLayer
          id="tidal-currents-glow"
          style={{
            circleRadius: [
              'interpolate', ['linear'], ['get', 'speedKnots'],
              0, 20,
              1, 28,
              2, 36,
              3, 44,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: 0.15,
            circleBlur: 0.7,
          }}
        />

        {/* Base circle at each station */}
        <CircleLayer
          id="tidal-currents-circle"
          style={{
            circleRadius: [
              'interpolate', ['linear'], ['get', 'speedKnots'],
              0, 8,
              1, 12,
              2, 16,
              3, 20,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: 0.85,
            circleStrokeColor: '#FFFFFF',
            circleStrokeWidth: 2,
          }}
        />

        {/* Arrow symbol showing current direction */}
        <SymbolLayer
          id="tidal-currents-arrow"
          style={{
            iconImage: 'triangle-11',
            iconSize: [
              'interpolate', ['linear'], ['get', 'speedKnots'],
              0, 0.8,
              1, 1.0,
              2, 1.3,
              3, 1.6,
            ],
            iconRotate: ['get', 'directionDeg'],
            iconRotationAlignment: 'map',
            iconAllowOverlap: true,
            iconColor: '#FFFFFF',
            iconOpacity: 0.9,
          }}
        />

        {/* Speed label at zoom 9+ */}
        {showLabels && (
          <SymbolLayer
            id="tidal-currents-label"
            style={{
              textField: ['get', 'speedLabel'],
              textSize: 12,
              textFont: ['Open Sans Bold'],
              textColor: '#FFFFFF',
              textHaloColor: ['get', 'color'],
              textHaloWidth: 2,
              textOffset: [0, 2.0],
              textAllowOverlap: false,
            }}
          />
        )}

        {/* State label (Slack/Ebb/Flood) at zoom 11+ */}
        {zoom >= 11 && (
          <SymbolLayer
            id="tidal-currents-state"
            style={{
              textField: ['get', 'stateLabel'],
              textSize: 10,
              textFont: ['Open Sans Bold'],
              textColor: palette.textSecondary,
              textHaloColor: '#FFFFFF',
              textHaloWidth: 1.5,
              textOffset: [0, 3.0],
              textAllowOverlap: false,
            }}
          />
        )}
      </ShapeSource>
    </>
  );
}

// ── Exported helpers for info card ───────────────────────────────

/** Get a summary of tidal current conditions for the info card. */
export async function getTidalCurrentSummary(
  lat: number,
  lon: number,
): Promise<{ stationCount: number; maxSpeed: number; dominantState: string } | null> {
  try {
    const stations = await findNearbyCurrentStations(lat, lon);
    if (stations.length === 0) return null;

    const results = await Promise.all(stations.slice(0, 5).map(fetchCurrentPrediction));
    const valid = results.filter((r): r is TidalCurrentPrediction => r !== null);
    if (valid.length === 0) return null;

    const maxSpeed = Math.max(...valid.map((p) => p.speedKnots));
    const states = valid.map((p) => p.state);
    const stateCount = { slack: 0, flood: 0, ebb: 0 };
    for (const s of states) stateCount[s]++;
    const dominantState = (Object.entries(stateCount) as [string, number][])
      .sort((a, b) => b[1] - a[1])[0][0];

    return { stationCount: valid.length, maxSpeed, dominantState };
  } catch {
    return null;
  }
}
