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
}: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);

  useEffect(() => {
    if (spots.length === 0) return;
    setGeoJSON(buildGeoJSON(spots));
  }, [spots, lat, lon]);

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
        {/* Pulsing outer ring for hot spots */}
        <CircleLayer
          id="bite-glow"
          filter={['>=', ['get', 'score'], 70]}
          style={{
            circleRadius: 22,
            circleColor: ['get', 'color'],
            circleOpacity: 0.2,
            circleBlur: 0.6,
          }}
        />
        {/* Main bite score circle */}
        <CircleLayer
          id="bite-dots"
          style={{
            circleRadius: 14,
            circleColor: ['get', 'color'],
            circleOpacity: 0.55,
            circleStrokeWidth: 2,
            circleStrokeColor: ['get', 'color'],
            circleStrokeOpacity: 0.85,
          }}
        />
        {/* Bite status label */}
        <SymbolLayer
          id="bite-labels"
          style={{
            textField: ['get', 'label'],
            textSize: 10,
            textColor: '#FFFFFF',
            textHaloColor: ['get', 'color'],
            textHaloWidth: 1.3,
            textAllowOverlap: false,
            textFont: ['Open Sans Bold'],
          }}
        />
      </ShapeSource>
    </>
  );
}
