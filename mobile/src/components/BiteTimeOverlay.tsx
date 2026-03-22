/**
 * OpenCatch -- Bite Activity Map Overlay (Windy-Quality)
 *
 * Dramatic, high-contrast visualization of bite activity across spots.
 * Hot spots get massive pulsing glow halos with intense color cores.
 * Cold/dead spots are visually muted. The contrast makes it instantly
 * obvious where the fish are biting.
 *
 * Green = HOT bite -> Yellow = Fair -> Orange = Slow -> Gray = Dead
 */

import React, { useEffect, useState, useRef } from 'react';
import { palette } from '../theme/palette';
import {
  getDailyBiteForecast,
  type DailyBiteForecast,
} from '../services/bestTimeWindows';

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

// ── Color scale (high contrast for immediate readability) ────────────────────

function biteColor(score: number): string {
  if (score >= 80) return '#00C853';   // Bright green -- HOT
  if (score >= 65) return '#2E7D32';   // Green -- Good
  if (score >= 50) return '#8BC34A';   // Light green -- Fair+
  if (score >= 40) return '#FFC107';   // Amber -- Fair
  if (score >= 25) return '#FF9800';   // Orange -- Slow
  if (score >= 15) return '#EF5350';   // Red -- Poor
  return '#546E7A';                     // Blue-gray -- Dead
}

function biteLabel(score: number): string {
  if (score >= 75) return 'HOT';
  if (score >= 55) return 'Good';
  if (score >= 40) return 'Fair';
  if (score >= 25) return 'Slow';
  return 'Dead';
}

function buildGeoJSON(
  spots: Array<{ id: string; lat: number; lon: number; name: string }>,
): GeoJSON.FeatureCollection {
  const currentHour = new Date().getHours();

  return {
    type: 'FeatureCollection',
    features: spots.map((spot) => {
      const forecast = getDailyBiteForecast(spot.lat, spot.lon);
      const hourlyScore = forecast.hourlyScores[currentHour] ?? forecast.overallRating;

      const color = biteColor(hourlyScore);
      const label = biteLabel(hourlyScore);

      return {
        type: 'Feature' as const,
        id: `bite-${spot.id}`,
        geometry: {
          type: 'Point' as const,
          coordinates: [spot.lon, spot.lat],
        },
        properties: {
          score: hourlyScore,
          color,
          label,
          name: spot.name,
          scoreText: `${hourlyScore}`,
          overallRating: forecast.overallRating,
          ratingLabel: forecast.ratingLabel,
          // Pre-computed flags for MapLibre expression filters
          isHot: hourlyScore >= 70 ? 1 : 0,
          isGood: hourlyScore >= 50 ? 1 : 0,
        },
      };
    }),
  };
}

// ── Component ────────────────────────────────────────────────────────────────

export function BiteTimeOverlay({
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

  // Refresh every 15 minutes (bite score changes hourly)
  useEffect(() => {
    const interval = setInterval(() => {
      if (spots.length > 0) setGeoJSON(buildGeoJSON(spots));
    }, 15 * 60 * 1000);
    return () => clearInterval(interval);
  }, [spots]);

  if (!geoJSON || geoJSON.features.length === 0) return null;

  return (
    <>
      <ShapeSource id="bite-time-source" shape={geoJSON}>
        {/* ── Layer 1: Massive outer shimmer halo for HOT spots.
             Creates dramatic "heat shimmer" visible from far away. ── */}
        <CircleLayer
          id="bite-shimmer-outer"
          filter={['>=', ['get', 'score'], 65]}
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.5],
              ['zoom'],
              5, 40,
              8, 65,
              11, 100,
              14, 160,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: [
              'interpolate', ['linear'], ['get', 'score'],
              65, 0.10,
              80, 0.18,
              100, 0.25,
            ],
            circleBlur: 1,
            circlePitchAlignment: 'map',
          }}
        />

        {/* ── Layer 2: Medium glow ring -- visible for all above-average spots ── */}
        <CircleLayer
          id="bite-glow-mid"
          filter={['>=', ['get', 'score'], 40]}
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.5],
              ['zoom'],
              5, 20,
              8, 35,
              11, 55,
              14, 85,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: [
              'interpolate', ['linear'], ['get', 'score'],
              40, 0.15,
              70, 0.30,
              100, 0.45,
            ],
            circleBlur: 0.7,
            circlePitchAlignment: 'map',
          }}
        />

        {/* ── Layer 3: Inner intensity core -- bright center ── */}
        <CircleLayer
          id="bite-core"
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.5],
              ['zoom'],
              5, 8,
              8, 14,
              11, 22,
              14, 35,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: [
              'interpolate', ['linear'], ['get', 'score'],
              0, 0.30,
              40, 0.50,
              70, 0.70,
              100, 0.85,
            ],
            circleBlur: 0.3,
            circlePitchAlignment: 'map',
          }}
        />

        {/* ── Layer 4: Center pip with strong white stroke ── */}
        <CircleLayer
          id="bite-pip"
          minZoomLevel={7}
          style={{
            circleRadius: [
              'interpolate', ['linear'], ['get', 'score'],
              0, 5,
              50, 8,
              80, 12,
              100, 14,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: 0.9,
            circleStrokeWidth: [
              'interpolate', ['linear'], ['get', 'score'],
              0, 1,
              70, 2,
              100, 3,
            ],
            circleStrokeColor: '#FFFFFF',
            circleStrokeOpacity: 0.95,
          }}
        />

        {/* ── Bite status label ── */}
        <SymbolLayer
          id="bite-labels"
          minZoomLevel={8}
          style={{
            textField: ['get', 'label'],
            textSize: [
              'interpolate', ['linear'], ['get', 'score'],
              0, 9,
              70, 12,
              100, 14,
            ],
            textColor: '#FFFFFF',
            textHaloColor: 'rgba(0, 0, 0, 0.6)',
            textHaloWidth: 1.5,
            textAllowOverlap: false,
            textFont: ['Open Sans Bold'],
            textOffset: [0, -2],
          }}
        />

        {/* ── Score number at high zoom ── */}
        <SymbolLayer
          id="bite-score-text"
          minZoomLevel={10}
          style={{
            textField: ['get', 'scoreText'],
            textSize: 9,
            textColor: 'rgba(255, 255, 255, 0.8)',
            textHaloColor: ['get', 'color'],
            textHaloWidth: 1,
            textAllowOverlap: false,
            textFont: ['Open Sans Regular'],
            textOffset: [0, 1.8],
          }}
        />
      </ShapeSource>
    </>
  );
}

// ── Legend data for external use ──────────────────────────────────────────────

export const BITE_LEGEND_STOPS = [
  { color: '#546E7A', label: 'Dead' },
  { color: '#EF5350', label: 'Slow' },
  { color: '#FFC107', label: 'Fair' },
  { color: '#8BC34A', label: 'Good' },
  { color: '#00C853', label: 'HOT' },
];
