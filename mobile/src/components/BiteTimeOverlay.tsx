/**
 * OpenCatch -- Bite Time Map Overlay
 *
 * Color-codes fishing spots on the map by their current bite score.
 * Green = hot bite right now, yellow = moderate, red/gray = slow.
 * Uses the bestTimeWindows service to score each spot.
 *
 * This answers the key angler question: "Where should I go RIGHT NOW?"
 */

import React, { useEffect, useState } from 'react';
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

// ── Helpers ──────────────────────────────────────────────────────────────────

function biteColor(score: number): string {
  if (score >= 75) return '#2E7D32';   // Hot -- go now
  if (score >= 55) return '#66BB6A';   // Good
  if (score >= 40) return '#FFA726';   // Moderate
  if (score >= 25) return '#EF5350';   // Slow
  return '#78909C';                     // Dead
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
      // Get a bite forecast score for this spot's location
      // We use the spot lat/lon with some deterministic variation
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
        {/* Outer glow for all spots */}
        <CircleLayer
          id="bite-glow"
          style={{
            circleRadius: [
              'interpolate', ['linear'], ['get', 'score'],
              0, 24,
              50, 32,
              100, 44,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: [
              'interpolate', ['linear'], ['get', 'score'],
              0, 0.1,
              50, 0.15,
              100, 0.25,
            ],
            circleBlur: 0.7,
          }}
        />
        {/* Main bite score circle */}
        <CircleLayer
          id="bite-dots"
          style={{
            circleRadius: 16,
            circleColor: ['get', 'color'],
            circleOpacity: 0.7,
            circleStrokeWidth: 2.5,
            circleStrokeColor: '#FFFFFF',
            circleStrokeOpacity: 0.9,
          }}
        />
        {/* Bite status label */}
        <SymbolLayer
          id="bite-labels"
          style={{
            textField: ['get', 'label'],
            textSize: 11,
            textColor: '#FFFFFF',
            textHaloColor: ['get', 'color'],
            textHaloWidth: 2,
            textAllowOverlap: true,
            textFont: ['Open Sans Bold'],
          }}
        />
      </ShapeSource>
    </>
  );
}
