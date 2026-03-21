/**
 * OpenCatch — Artificial Reef Overlay
 *
 * Displays artificial reef locations from coastal state reef programs
 * on the map. Uses the coastalCharts service to fetch reef data and
 * renders colored markers with depth and material info on tap.
 */

import React, { useEffect, useRef, useState } from 'react';
import { palette } from '../theme/palette';
import {
  getArtificialReefs,
  reefsToGeoJSON,
  type ArtificialReef,
} from '../services/coastalCharts';

// ── Types ────────────────────────────────────────────────────────────────────

interface Props {
  lat: number;
  lon: number;
  ShapeSource: any;
  CircleLayer: any;
  SymbolLayer: any;
}

// ── State detection ──────────────────────────────────────────────────────────

/** Map approximate lat/lon to the nearest coastal state with a reef program. */
function detectCoastalState(lat: number, lon: number): string | null {
  // Gulf states
  if (lat >= 24.5 && lat <= 30.5 && lon >= -87.6 && lon <= -80.0) return 'FL';
  if (lat >= 25.5 && lat <= 31.0 && lon >= -98.0 && lon <= -87.6) return 'TX';
  if (lat >= 28.5 && lat <= 33.0 && lon >= -94.0 && lon <= -88.8) return 'LA';
  if (lat >= 30.0 && lat <= 31.0 && lon >= -88.8 && lon <= -88.0) return 'MS';
  if (lat >= 29.9 && lat <= 31.0 && lon >= -88.5 && lon <= -87.5) return 'AL';

  // Southeast Atlantic
  if (lat >= 30.3 && lat <= 35.3 && lon >= -82.0 && lon <= -75.5) {
    if (lat < 32.0) return 'GA';
    if (lat < 33.8) return 'SC';
    return 'NC';
  }

  // Mid-Atlantic / Northeast
  if (lat >= 38.5 && lat <= 41.5 && lon >= -75.6 && lon <= -73.7) return 'NJ';
  if (lat >= 38.4 && lat <= 39.8 && lon >= -75.8 && lon <= -75.0) return 'DE';

  return null;
}

const SEARCH_RADIUS_DEG = 2.0;
const REFRESH_DISTANCE_M = 10000;

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

// ── Component ────────────────────────────────────────────────────────────────

export function ArtificialReefOverlay({
  lat,
  lon,
  ShapeSource,
  CircleLayer,
  SymbolLayer,
}: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);
  const loadingRef = useRef(false);

  useEffect(() => {
    if (loadingRef.current) return;

    // Skip refresh if the user hasn't moved significantly.
    if (lastCenter.current) {
      const dist = haversineDistance(lat, lon, lastCenter.current.lat, lastCenter.current.lon);
      if (dist < REFRESH_DISTANCE_M) return;
    }

    const state = detectCoastalState(lat, lon);
    if (!state) {
      setGeoJSON(null);
      return;
    }

    loadingRef.current = true;
    lastCenter.current = { lat, lon };

    getArtificialReefs(state)
      .then((reefs) => {
        if (reefs.length > 0) {
          setGeoJSON(reefsToGeoJSON(reefs));
        } else {
          setGeoJSON(null);
        }
      })
      .catch(() => setGeoJSON(null))
      .finally(() => {
        loadingRef.current = false;
      });
  }, [lat, lon]);

  if (!geoJSON || !geoJSON.features.length) return null;

  return (
    <>
      <ShapeSource
        id="artificial-reef-source"
        shape={geoJSON}
      >
        <CircleLayer
          id="artificial-reef-dots"
          style={{
            circleRadius: 6,
            circleColor: palette.warning,
            circleStrokeColor: '#FFFFFF',
            circleStrokeWidth: 1.5,
            circleOpacity: 0.85,
          }}
        />
        <SymbolLayer
          id="artificial-reef-labels"
          minZoomLevel={10}
          style={{
            textField: ['get', 'name'],
            textSize: 11,
            textFont: ['Open Sans Regular'],
            textOffset: [0, 1.5],
            textAnchor: 'top',
            textColor: palette.text,
            textHaloColor: '#FFFFFF',
            textHaloWidth: 1,
            textMaxWidth: 10,
            textOptional: true,
          }}
        />
      </ShapeSource>
    </>
  );
}
