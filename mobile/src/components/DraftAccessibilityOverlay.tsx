/**
 * OpenCatch — Draft Accessibility Overlay
 *
 * Highlights areas as safe (green), caution (orange), or danger (red)
 * based on depth vs. the user's boat draft setting.
 * Uses NOAA depth soundings with IDW interpolation.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  getDraftAccessibility,
  feetToMeters,
  type DraftAccessibilityResult,
} from '../services/draftAccessibility';

// ── Types ────────────────────────────────────────────────────────────────────

interface Props {
  lat: number;
  lon: number;
  /** Boat draft in feet */
  draftFt: number;
  zoom: number;
  ShapeSource: any;
  CircleLayer: any;
  SymbolLayer: any;
}

// ── Constants ────────────────────────────────────────────────────────────────

const REFRESH_DISTANCE_M = 3000;
const MIN_ZOOM = 10;

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

function resultToGeoJSON(result: DraftAccessibilityResult): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: result.cells
      .filter((c) => c.accessibility !== 'unknown')
      .map((cell, i) => ({
        type: 'Feature' as const,
        id: i,
        geometry: {
          type: 'Point' as const,
          coordinates: [cell.lon, cell.lat],
        },
        properties: {
          color: cell.color,
          opacity: cell.opacity,
          accessibility: cell.accessibility,
          depth: cell.depthM != null ? cell.depthM.toFixed(1) : '',
        },
      })),
  };
}

// ── Component ────────────────────────────────────────────────────────────────

export function DraftAccessibilityOverlay({
  lat,
  lon,
  draftFt,
  zoom,
  ShapeSource,
  CircleLayer,
  SymbolLayer,
}: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);
  const loadingRef = useRef(false);

  const fetchData = useCallback(
    async (centerLat: number, centerLon: number) => {
      if (loadingRef.current) return;
      if (draftFt <= 0) return;

      if (lastCenter.current) {
        const dist = haversineDistance(
          centerLat,
          centerLon,
          lastCenter.current.lat,
          lastCenter.current.lon,
        );
        if (dist < REFRESH_DISTANCE_M) return;
      }

      loadingRef.current = true;
      lastCenter.current = { lat: centerLat, lon: centerLon };

      try {
        const span = 0.08; // ~8km grid
        const result = await getDraftAccessibility(
          {
            south: centerLat - span,
            north: centerLat + span,
            west: centerLon - span,
            east: centerLon + span,
          },
          feetToMeters(draftFt),
        );
        if (result.cells.length > 0) {
          setGeoJSON(resultToGeoJSON(result));
        }
      } catch {
        // Keep previous data
      } finally {
        loadingRef.current = false;
      }
    },
    [draftFt],
  );

  useEffect(() => {
    if (zoom >= MIN_ZOOM) {
      fetchData(lat, lon);
    } else {
      setGeoJSON(null);
    }
  }, [lat, lon, zoom, fetchData]);

  if (!geoJSON || geoJSON.features.length === 0 || zoom < MIN_ZOOM) return null;

  return (
    <ShapeSource id="draft-access-source" shape={geoJSON}>
      {/* Colored grid cells: green=safe, orange=caution, red=danger */}
      <CircleLayer
        id="draft-access-circles"
        style={{
          circleRadius: [
            'interpolate',
            ['linear'],
            ['zoom'],
            10, 6,
            13, 14,
            16, 22,
          ],
          circleColor: ['get', 'color'],
          circleOpacity: ['get', 'opacity'],
          circleStrokeWidth: 0,
        }}
      />
      {/* Depth labels at higher zoom */}
      <SymbolLayer
        id="draft-access-labels"
        minZoomLevel={13}
        style={{
          textField: ['get', 'depth'],
          textSize: 9,
          textColor: '#333333',
          textHaloColor: 'rgba(255, 255, 255, 0.9)',
          textHaloWidth: 1,
          textFont: ['Open Sans Regular'],
          textAllowOverlap: false,
          textOptional: true,
        }}
      />
    </ShapeSource>
  );
}
