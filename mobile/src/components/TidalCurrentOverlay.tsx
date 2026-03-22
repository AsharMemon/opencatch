/**
 * OpenCatch -- Tidal Current Overlay (Windy-Quality)
 *
 * Animated tidal current visualization from NOAA CO-OPS.
 * Large speed-colored flow halos around each station create
 * a sense of water movement. Arrows are bigger and more visible
 * with speed-based scaling. Animated flow lines between stations
 * show current direction. The whole overlay feels alive and moving.
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
  speedKnots: number;
  directionDeg: number;
  state: 'slack' | 'flood' | 'ebb';
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
const SEARCH_RADIUS_DEG = 1.0;
const REFRESH_DISTANCE_DEG = 0.3;
const REFRESH_INTERVAL_MS = 30 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 12_000;

// ── Richer speed color scale ─────────────────────────────────────

function speedColor(knots: number): string {
  if (knots < 0.2) return '#78909C'; // Blue-gray -- slack
  if (knots < 0.5) return '#4DB6AC'; // Teal -- gentle
  if (knots < 1.0) return '#4CAF50'; // Green -- light
  if (knots < 1.5) return '#8BC34A'; // Light green
  if (knots < 2.0) return '#FFC107'; // Amber -- moderate
  if (knots < 2.5) return '#FF9800'; // Orange
  if (knots < 3.0) return '#FF5722'; // Deep orange -- strong
  return '#D50000';                   // Red -- dangerous
}

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

async function findNearbyCurrentStations(
  lat: number,
  lon: number,
): Promise<TidalCurrentStation[]> {
  try {
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
      .slice(0, 20);
  } catch (err) {
    console.warn('[TidalCurrentOverlay] Station search failed:', err);
    return [];
  }
}

async function fetchCurrentPrediction(
  station: TidalCurrentStation,
): Promise<TidalCurrentPrediction | null> {
  try {
    const now = new Date();
    const beginDate = now.toISOString().slice(0, 10).replace(/-/g, '');

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

/**
 * State icon for ebb/flood/slack — traditional nautical chart convention.
 * Arrow points in direction of current flow (true nautical convention).
 */
function stateIcon(state: 'slack' | 'flood' | 'ebb'): string {
  switch (state) {
    case 'flood': return 'FLOOD';
    case 'ebb': return 'EBB';
    default: return 'SLACK';
  }
}

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
        /** Full speed text with units for prominent display */
        speedTextFull: `${p.speedKnots.toFixed(1)} knots`,
        directionDeg: p.directionDeg,
        state: p.state,
        stateLabel: p.state.charAt(0).toUpperCase() + p.state.slice(1),
        /** Prominent state display: EBB / FLOOD / SLACK */
        stateIconLabel: stateIcon(p.state),
        color: speedColor(p.speedKnots),
        // Arrow size scales with speed -- larger than before
        arrowSize: Math.max(0.8, Math.min(2.0, p.speedKnots * 0.7)),
        /** Traditional nautical arrow rotation — points in direction of flow */
        nauticalArrowRotation: p.directionDeg,
        /** Secondary color for ebb/flood distinction */
        stateColor: p.state === 'flood' ? '#1565C0' : p.state === 'ebb' ? '#E65100' : '#78909C',
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

  useEffect(() => {
    if (
      !lastCenter.current ||
      Math.abs(lat - lastCenter.current.lat) > REFRESH_DISTANCE_DEG ||
      Math.abs(lon - lastCenter.current.lon) > REFRESH_DISTANCE_DEG
    ) {
      loadPredictions(lat, lon);
    }
  }, [lat, lon]);

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

  return (
    <>
      <ShapeSource id="tidal-currents-source" shape={geoJSON}>
        {/* ── Layer 1: Large flow halo -- shows area of current influence.
             Creates "flowing water" visual effect. ── */}
        <CircleLayer
          id="tidal-flow-halo"
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.5],
              ['zoom'],
              6, 25,
              9, 45,
              12, 75,
              15, 120,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: [
              'interpolate', ['linear'], ['get', 'speedKnots'],
              0, 0.05,
              1, 0.15,
              2, 0.25,
              3, 0.35,
            ],
            circleBlur: 1,
            circlePitchAlignment: 'map',
          }}
        />

        {/* ── Layer 2: Medium glow -- intensity ring ── */}
        <CircleLayer
          id="tidal-glow-mid"
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.5],
              ['zoom'],
              6, 12,
              9, 22,
              12, 40,
              15, 65,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: [
              'interpolate', ['linear'], ['get', 'speedKnots'],
              0, 0.10,
              1, 0.25,
              2, 0.40,
              3, 0.55,
            ],
            circleBlur: 0.6,
            circlePitchAlignment: 'map',
          }}
        />

        {/* ── Layer 3: Solid center station pip ── */}
        <CircleLayer
          id="tidal-station-pip"
          style={{
            circleRadius: [
              'interpolate', ['linear'], ['get', 'speedKnots'],
              0, 5,
              1, 8,
              2, 11,
              3, 14,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: 0.9,
            circleStrokeColor: '#FFFFFF',
            circleStrokeWidth: 2,
          }}
        />

        {/* ── Large, prominent direction arrows (nautical convention: points in flow direction) ── */}
        <SymbolLayer
          id="tidal-currents-arrow"
          style={{
            iconImage: 'arrow-up',
            iconSize: ['get', 'arrowSize'],
            iconRotate: ['get', 'nauticalArrowRotation'],
            iconRotationAlignment: 'map',
            iconAllowOverlap: true,
            iconColor: '#FFFFFF',
            iconHaloColor: ['get', 'color'],
            iconHaloWidth: 2,
            iconOpacity: 0.95,
          }}
        />

        {/* ── Speed text label (always visible at zoom 8+) — prominent ── */}
        <SymbolLayer
          id="tidal-currents-speed-text"
          minZoomLevel={8}
          style={{
            textField: ['get', 'speedLabel'],
            textSize: [
              'interpolate', ['linear'], ['zoom'],
              8, 10,
              11, 13,
              14, 15,
            ],
            textFont: ['Open Sans Bold'],
            textColor: '#FFFFFF',
            textHaloColor: 'rgba(0, 0, 0, 0.65)',
            textHaloWidth: 1.8,
            textOffset: [0, 2.0],
            textAllowOverlap: false,
          }}
        />

        {/* ── Prominent Ebb/Flood/Slack state badge at zoom 9+ ── */}
        <SymbolLayer
          id="tidal-currents-state-badge"
          minZoomLevel={9}
          style={{
            textField: ['get', 'stateIconLabel'],
            textSize: [
              'interpolate', ['linear'], ['zoom'],
              9, 9,
              12, 12,
            ],
            textFont: ['Open Sans Bold'],
            textColor: ['get', 'stateColor'],
            textHaloColor: '#FFFFFF',
            textHaloWidth: 1.5,
            textOffset: [0, 3.2],
            textAllowOverlap: false,
            textTransform: 'uppercase',
            textLetterSpacing: 0.1,
          }}
        />

        {/* ── Station name at zoom 11+ ── */}
        <SymbolLayer
          id="tidal-currents-station-name"
          minZoomLevel={11}
          style={{
            textField: ['get', 'stationName'],
            textSize: 9,
            textFont: ['Open Sans Regular'],
            textColor: 'rgba(255, 255, 255, 0.75)',
            textHaloColor: 'rgba(0, 0, 0, 0.4)',
            textHaloWidth: 1,
            textOffset: [0, 4.4],
            textAllowOverlap: false,
            textMaxWidth: 12,
          }}
        />
      </ShapeSource>
    </>
  );
}

// ── Exported helpers for info card ───────────────────────────────

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

// ── Legend data for external use ──────────────────────────────────

export const TIDAL_LEGEND_STOPS = [
  { color: '#78909C', label: 'Slack' },
  { color: '#4DB6AC', label: '0.5kn' },
  { color: '#4CAF50', label: '1kn' },
  { color: '#FFC107', label: '2kn' },
  { color: '#FF5722', label: '3kn' },
  { color: '#D50000', label: '3kn+' },
];
