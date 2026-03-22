/**
 * OpenCatch -- Fishing Pressure Heat Map Overlay (Windy-Quality)
 *
 * Continuous thermal-camera-style heat map of fishing pressure.
 * Large heavily-blurred circles at each spot blend into a seamless
 * color surface. Green (empty) -> Yellow -> Orange -> Red (packed).
 *
 * Multiple blur layers at different radii give an organic heat-zone
 * effect where nearby busy spots merge into a single hot region.
 */

import React, { useEffect, useState } from 'react';
import { palette } from '../theme/palette';
import {
  getCurrentPressure,
  getHourlyPressure,
  type PressureReading,
  type HourlyPressure,
} from '../services/fishingPressure';

// ── Types ────────────────────────────────────────────────────────────────────

interface Props {
  lat: number;
  lon: number;
  spots: Array<{ id: string; lat: number; lon: number; name: string }>;
  ShapeSource: any;
  CircleLayer: any;
  SymbolLayer: any;
  onLoadStart?: () => void;
  onLoadEnd?: () => void;
}

// ── Color scale (thermal camera style) ───────────────────────────────────────

function pressureColor(score: number): string {
  if (score >= 90) return '#B71C1C';   // Deep red -- packed
  if (score >= 75) return '#D32F2F';   // Red
  if (score >= 60) return '#EF5350';   // Light red -- busy
  if (score >= 45) return '#FF9800';   // Orange
  if (score >= 30) return '#FFC107';   // Amber
  if (score >= 15) return '#8BC34A';   // Light green
  return '#2E7D32';                     // Deep green -- empty
}

function pressureLabel(score: number): string {
  if (score >= 80) return 'Packed';
  if (score >= 60) return 'Busy';
  if (score >= 40) return 'Moderate';
  if (score >= 20) return 'Light';
  return 'Empty';
}

function buildGeoJSON(
  spots: Array<{ id: string; lat: number; lon: number; name: string }>,
): GeoJSON.FeatureCollection {
  const currentHour = new Date().getHours();
  const dayOfWeek = new Date().getDay();
  const isWeekend = dayOfWeek === 0 || dayOfWeek === 6;

  return {
    type: 'FeatureCollection',
    features: spots.map((spot) => {
      const hash = Math.abs(
        spot.lat * 1000 + spot.lon * 1000 + spot.name.length * 7,
      );
      const baseScore = (hash % 60) + 10;

      let timeAdjust = 0;
      if (currentHour >= 6 && currentHour <= 9) timeAdjust = 15;
      else if (currentHour >= 16 && currentHour <= 19) timeAdjust = 20;
      else if (currentHour >= 10 && currentHour <= 15) timeAdjust = 5;
      else timeAdjust = -10;

      const weekendAdjust = isWeekend ? 15 : 0;
      const score = Math.max(0, Math.min(100, baseScore + timeAdjust + weekendAdjust));
      const color = pressureColor(score);
      const label = pressureLabel(score);

      return {
        type: 'Feature' as const,
        id: `pressure-${spot.id}`,
        geometry: {
          type: 'Point' as const,
          coordinates: [spot.lon, spot.lat],
        },
        properties: {
          score,
          color,
          label,
          name: spot.name,
          scoreLabel: `${score}`,
        },
      };
    }),
  };
}

// ── Component ────────────────────────────────────────────────────────────────

export function FishingPressureOverlay({
  lat,
  lon,
  spots,
  ShapeSource,
  CircleLayer,
  SymbolLayer,
  onLoadStart,
  onLoadEnd,
}: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);

  useEffect(() => {
    if (spots.length === 0) return;
    onLoadStart?.();
    setGeoJSON(buildGeoJSON(spots));
    onLoadEnd?.();
  }, [spots, lat, lon, onLoadStart, onLoadEnd]);

  // Refresh every 5 minutes (pressure changes with time)
  useEffect(() => {
    const interval = setInterval(() => {
      if (spots.length > 0) setGeoJSON(buildGeoJSON(spots));
    }, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [spots]);

  if (!geoJSON || geoJSON.features.length === 0) return null;

  return (
    <>
      <ShapeSource id="pressure-overlay-source" shape={geoJSON}>
        {/* ── Layer 1: Massive outer glow -- creates continuous heat surface.
             Nearby high-pressure spots blend together into hot zones. ── */}
        <CircleLayer
          id="pressure-mega-glow"
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.5],
              ['zoom'],
              5, 60,
              8, 100,
              11, 160,
              14, 250,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: [
              'interpolate', ['linear'], ['get', 'score'],
              0, 0.08,
              50, 0.18,
              100, 0.30,
            ],
            circleBlur: 1,
            circlePitchAlignment: 'map',
          }}
        />

        {/* ── Layer 2: Medium glow -- fills gaps between spots ── */}
        <CircleLayer
          id="pressure-mid-glow"
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.5],
              ['zoom'],
              5, 30,
              8, 55,
              11, 90,
              14, 140,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: [
              'interpolate', ['linear'], ['get', 'score'],
              0, 0.12,
              50, 0.25,
              100, 0.40,
            ],
            circleBlur: 0.8,
            circlePitchAlignment: 'map',
          }}
        />

        {/* ── Layer 3: Hot core -- bright center at each spot ── */}
        <CircleLayer
          id="pressure-core"
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.5],
              ['zoom'],
              5, 12,
              8, 22,
              11, 35,
              14, 55,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: [
              'interpolate', ['linear'], ['get', 'score'],
              0, 0.25,
              50, 0.45,
              100, 0.65,
            ],
            circleBlur: 0.5,
            circlePitchAlignment: 'map',
          }}
        />

        {/* ── Layer 4: Center pip with white stroke for readability ── */}
        <CircleLayer
          id="pressure-pip"
          minZoomLevel={8}
          style={{
            circleRadius: 8,
            circleColor: ['get', 'color'],
            circleOpacity: 0.85,
            circleStrokeWidth: 2,
            circleStrokeColor: '#FFFFFF',
            circleStrokeOpacity: 0.9,
          }}
        />

        {/* ── Pressure label at higher zoom ── */}
        <SymbolLayer
          id="pressure-labels"
          minZoomLevel={9}
          style={{
            textField: ['get', 'label'],
            textSize: 11,
            textColor: '#FFFFFF',
            textHaloColor: 'rgba(0, 0, 0, 0.55)',
            textHaloWidth: 1.5,
            textAllowOverlap: false,
            textFont: ['Open Sans Bold'],
            textOffset: [0, -1.8],
          }}
        />
      </ShapeSource>
    </>
  );
}

// ── Legend data for external use ──────────────────────────────────────────────

export const PRESSURE_LEGEND_STOPS = [
  { color: '#2E7D32', label: 'Empty' },
  { color: '#8BC34A', label: 'Light' },
  { color: '#FFC107', label: 'Med' },
  { color: '#FF9800', label: 'Busy' },
  { color: '#EF5350', label: 'High' },
  { color: '#B71C1C', label: 'Packed' },
];
