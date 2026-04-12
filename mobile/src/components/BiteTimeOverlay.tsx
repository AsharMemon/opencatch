/**
 * OpenCatch -- Fishability Overlay
 *
 * Calm, chart-friendly rendering for "best place to fish" guidance.
 * When a lake-wide field is available, we bias toward soft overlapping
 * zones rather than loud glowing dots so the bathymetry stays readable.
 */

import React, { useEffect, useState } from 'react';
import {
  getDailyBiteForecast,
} from '../services/bestTimeWindows';

// ── Types ────────────────────────────────────────────────────────────────────

interface Props {
  lat: number;
  lon: number;
  spots: Array<{ id: string; lat: number; lon: number; name: string }>;
  predictionGeoJSON?: GeoJSON.FeatureCollection | null;
  presentation?: 'spots' | 'field';
  ShapeSource: any;
  CircleLayer: any;
  SymbolLayer: any;
  onLoadStart?: () => void;
  onLoadEnd?: () => void;
}

// ── Color scale (soft chart-friendly palette) ────────────────────────────────

function biteColor(score: number): string {
  if (score >= 82) return '#C9884C';   // warm copper -- prime edge
  if (score >= 68) return '#E2B875';   // sandy gold -- strong
  if (score >= 54) return '#7FAE9D';   // sage teal -- good
  if (score >= 42) return '#99C3C8';   // sea mist -- fair
  if (score >= 30) return '#B5CAD9';   // pale steel -- slow
  if (score >= 18) return '#CCD8E2';   // silver blue -- weak
  return '#E2EAF0';                    // mist -- cold
}

function biteLabel(score: number): string {
  if (score >= 80) return 'Prime';
  if (score >= 55) return 'Good';
  if (score >= 40) return 'Fair';
  if (score >= 25) return 'Slow';
  return 'Cold';
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
  predictionGeoJSON,
  presentation = 'spots',
  ShapeSource,
  CircleLayer,
  SymbolLayer,
  onLoadStart,
  onLoadEnd,
}: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const isField = presentation === 'field';

  useEffect(() => {
    if (predictionGeoJSON) {
      onLoadStart?.();
      setGeoJSON(predictionGeoJSON);
      onLoadEnd?.();
      return;
    }
    if (spots.length === 0) return;
    onLoadStart?.();
    setGeoJSON(buildGeoJSON(spots));
    onLoadEnd?.();
  }, [spots, lat, lon, onLoadStart, onLoadEnd, predictionGeoJSON]);

  // Refresh every 15 minutes (bite score changes hourly)
  useEffect(() => {
    if (predictionGeoJSON) return;
    const interval = setInterval(() => {
      if (spots.length > 0) setGeoJSON(buildGeoJSON(spots));
    }, 15 * 60 * 1000);
    return () => clearInterval(interval);
  }, [spots, predictionGeoJSON]);

  if (!geoJSON || geoJSON.features.length === 0) return null;

  return (
    <>
      <ShapeSource id="bite-time-source" shape={geoJSON}>
        <CircleLayer
          id="bite-soft-wash"
          filter={isField ? ['>=', ['get', 'score'], 24] : ['>=', ['get', 'score'], 48]}
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.35],
              ['zoom'],
              5, isField ? 14 : 26,
              8, isField ? 20 : 38,
              11, isField ? 30 : 56,
              14, isField ? 44 : 82,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: [
              'interpolate',
              ['linear'],
              ['get', 'score'],
              20, isField ? 0.08 : 0.07,
              60, isField ? 0.14 : 0.13,
              90, isField ? 0.22 : 0.2,
            ],
            circleBlur: isField ? 0.95 : 0.8,
            circlePitchAlignment: 'map',
          }}
        />

        <CircleLayer
          id="bite-soft-core"
          filter={isField ? ['>=', ['get', 'score'], 34] : ['>=', ['get', 'score'], 24]}
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.3],
              ['zoom'],
              5, isField ? 6 : 10,
              8, isField ? 10 : 16,
              11, isField ? 14 : 24,
              14, isField ? 20 : 34,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: [
              'interpolate',
              ['linear'],
              ['get', 'score'],
              25, isField ? 0.16 : 0.14,
              60, isField ? 0.28 : 0.25,
              90, isField ? 0.42 : 0.38,
            ],
            circleBlur: isField ? 0.45 : 0.35,
            circlePitchAlignment: 'map',
          }}
        />

        <CircleLayer
          id="bite-focus-pip"
          minZoomLevel={isField ? 10 : 8}
          filter={['>=', ['get', 'score'], 64]}
          style={{
            circleRadius: [
              'interpolate', ['linear'], ['get', 'score'],
              64, isField ? 2.2 : 3.5,
              80, isField ? 3.4 : 5.5,
              100, isField ? 4.5 : 7,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: isField ? 0.78 : 0.82,
            circleStrokeWidth: isField ? 1.2 : 1.6,
            circleStrokeColor: 'rgba(255,255,255,0.92)',
            circleStrokeOpacity: 0.95,
          }}
        />

        <SymbolLayer
          id="bite-labels"
          minZoomLevel={isField ? 11 : 8}
          filter={isField ? ['>=', ['get', 'score'], 72] : ['>=', ['get', 'score'], 58]}
          style={{
            textField: isField ? ['get', 'label'] : ['get', 'label'],
            textSize: [
              'interpolate', ['linear'], ['get', 'score'],
              0, isField ? 8 : 9,
              70, isField ? 10 : 11,
              100, isField ? 11 : 13,
            ],
            textColor: isField ? '#18354C' : '#14324A',
            textHaloColor: 'rgba(255,255,255,0.92)',
            textHaloWidth: isField ? 1.2 : 1.5,
            textAllowOverlap: false,
            textFont: ['Open Sans Bold'],
            textOffset: [0, isField ? -1.1 : -1.7],
          }}
        />

        <SymbolLayer
          id="bite-score-text"
          minZoomLevel={12}
          filter={isField ? ['>=', ['get', 'score'], 78] : ['>=', ['get', 'score'], 68]}
          style={{
            textField: isField ? ['get', 'depthText'] : ['get', 'scoreText'],
            textSize: isField ? 8 : 9,
            textColor: '#35566D',
            textHaloColor: 'rgba(255,255,255,0.9)',
            textHaloWidth: 1.1,
            textAllowOverlap: false,
            textFont: ['Open Sans Regular'],
            textOffset: [0, 1.4],
          }}
        />
      </ShapeSource>
    </>
  );
}

// ── Legend data for external use ──────────────────────────────────────────────

export const BITE_LEGEND_STOPS = [
  { color: '#E2EAF0', label: 'Cold' },
  { color: '#B5CAD9', label: 'Slow' },
  { color: '#99C3C8', label: 'Fair' },
  { color: '#7FAE9D', label: 'Good' },
  { color: '#C9884C', label: 'Prime' },
];
