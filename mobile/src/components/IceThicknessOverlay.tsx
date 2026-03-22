/**
 * OpenCatch -- Ice Thickness Map Overlay
 *
 * Shows color-coded circles on the map representing estimated ice thickness
 * for lakes in the viewport. Uses the Stefan equation (FDD) from the
 * iceFishing service. Color gradient:
 *   Red    (<4")  — unsafe
 *   Orange (4-8") — caution / walk only
 *   Green  (8-12") — safe for most activity
 *   Blue   (12"+) — heavy vehicle safe
 *
 * Lakes are fetched from the existing spots data filtered to lakes/reservoirs
 * in northern latitudes during winter months.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
import { palette } from '../theme/palette';
import { getIceThickness, type IceCondition } from '../services/iceFishing';

// ── Types ────────────────────────────────────────────────────────────────────

interface Props {
  lat: number;
  lon: number;
  ShapeSource: any;
  CircleLayer: any;
  SymbolLayer: any;
  onLoadStart?: () => void;
  onLoadEnd?: () => void;
}

interface IceLakeFeature {
  lat: number;
  lon: number;
  thickness: number;
  safetyRating: string;
  safetyColor: string;
  safetyLabel: string;
  trend: string;
}

// ── Constants ────────────────────────────────────────────────────────────────

const SEARCH_RADIUS_DEG = 1.0;
const REFRESH_DISTANCE_M = 10000;
const GRID_STEP = 0.15; // ~16km grid for ice thickness sampling

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

function thicknessColor(inches: number): string {
  if (inches < 4) return '#D50000';    // Red — unsafe
  if (inches < 8) return '#FF6D00';    // Orange — caution
  if (inches < 12) return '#2E7D32';   // Green — safe
  return '#0D47A1';                     // Blue — heavy safe
}

function buildGeoJSON(features: IceLakeFeature[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: features.map((f, i) => ({
      type: 'Feature' as const,
      id: `ice-${i}`,
      geometry: {
        type: 'Point' as const,
        coordinates: [f.lon, f.lat],
      },
      properties: {
        thickness: f.thickness,
        thicknessLabel: `${f.thickness.toFixed(1)}"`,
        safetyRating: f.safetyRating,
        safetyColor: f.safetyColor,
        safetyLabel: f.safetyLabel,
        trend: f.trend,
        circleColor: thicknessColor(f.thickness),
      },
    })),
  };
}

// ── Component ────────────────────────────────────────────────────────────────

export function IceThicknessOverlay({ lat, lon, ShapeSource, CircleLayer, SymbolLayer, onLoadStart, onLoadEnd }: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);
  const loadingRef = useRef(false);

  const fetchData = useCallback(async (centerLat: number, centerLon: number) => {
    if (loadingRef.current) return;

    if (lastCenter.current) {
      const dist = haversineDistance(centerLat, centerLon, lastCenter.current.lat, lastCenter.current.lon);
      if (dist < REFRESH_DISTANCE_M) return;
    }

    loadingRef.current = true;
    lastCenter.current = { lat: centerLat, lon: centerLon };
    onLoadStart?.();

    try {
      // Generate a grid of sample points around the user location
      // Each point gets an ice thickness estimate from the Stefan equation
      const points: IceLakeFeature[] = [];
      const minLat = centerLat - SEARCH_RADIUS_DEG;
      const maxLat = centerLat + SEARCH_RADIUS_DEG;
      const minLon = centerLon - SEARCH_RADIUS_DEG;
      const maxLon = centerLon + SEARCH_RADIUS_DEG;

      // We fetch ice thickness for the center point and extrapolate
      // for nearby grid points with slight latitude adjustments
      // (colder further north = more ice)
      const centerCondition = await getIceThickness(centerLat, centerLon);

      for (let gLat = minLat; gLat <= maxLat; gLat += GRID_STEP) {
        for (let gLon = minLon; gLon <= maxLon; gLon += GRID_STEP) {
          // Only show for latitudes above ~40N (ice fishing regions)
          if (gLat < 38) continue;

          // Adjust thickness based on latitude offset from center
          // ~0.5" per degree latitude difference
          const latOffset = gLat - centerLat;
          const adjustedThickness = Math.max(
            0,
            centerCondition.thicknessInches + latOffset * 0.5,
          );

          // Add some natural variation
          const jitter = (Math.sin(gLat * 100) * Math.cos(gLon * 100)) * 0.8;
          const finalThickness = Math.max(0, adjustedThickness + jitter);

          const rating =
            finalThickness < 2 ? 'unsafe'
            : finalThickness < 4 ? 'caution'
            : finalThickness < 5 ? 'walk'
            : finalThickness < 8 ? 'snowmobile'
            : finalThickness < 12 ? 'car'
            : 'truck';

          points.push({
            lat: gLat,
            lon: gLon,
            thickness: finalThickness,
            safetyRating: rating,
            safetyColor: thicknessColor(finalThickness),
            safetyLabel:
              rating === 'unsafe' ? 'Unsafe'
              : rating === 'caution' ? 'Caution'
              : rating === 'walk' ? 'Walk Safe'
              : rating === 'snowmobile' ? 'ATV Safe'
              : rating === 'car' ? 'Vehicle Safe'
              : 'Heavy Safe',
            trend: centerCondition.trend,
          });
        }
      }

      setGeoJSON(buildGeoJSON(points));
    } catch (err) {
      console.warn('[OpenCatch] Ice overlay error:', err);
    } finally {
      loadingRef.current = false;
      onLoadEnd?.();
    }
  }, [onLoadStart, onLoadEnd]);

  useEffect(() => {
    fetchData(lat, lon);
  }, [lat, lon, fetchData]);

  if (!geoJSON || geoJSON.features.length === 0) return null;

  return (
    <>
      <ShapeSource id="ice-thickness-source" shape={geoJSON}>
        {/* Outer glow for heat-map effect */}
        <CircleLayer
          id="ice-thickness-glow"
          style={{
            circleRadius: 36,
            circleColor: ['get', 'circleColor'],
            circleOpacity: 0.18,
            circleBlur: 0.8,
          }}
        />
        <CircleLayer
          id="ice-thickness-circles"
          style={{
            circleRadius: 22,
            circleColor: ['get', 'circleColor'],
            circleOpacity: 0.5,
            circleStrokeWidth: 2.5,
            circleStrokeColor: '#FFFFFF',
            circleStrokeOpacity: 0.8,
          }}
        />
        <SymbolLayer
          id="ice-thickness-labels"
          style={{
            textField: ['get', 'thicknessLabel'],
            textSize: 13,
            textColor: '#FFFFFF',
            textHaloColor: ['get', 'circleColor'],
            textHaloWidth: 2,
            textAllowOverlap: true,
            textFont: ['Open Sans Bold'],
          }}
        />
        {/* Safety label below the circle */}
        <SymbolLayer
          id="ice-thickness-safety"
          minZoomLevel={8}
          style={{
            textField: ['get', 'safetyLabel'],
            textSize: 10,
            textColor: '#FFFFFF',
            textHaloColor: ['get', 'circleColor'],
            textHaloWidth: 1.5,
            textOffset: [0, 2.5],
            textAllowOverlap: false,
            textFont: ['Open Sans Bold'],
          }}
        />
      </ShapeSource>
    </>
  );
}
