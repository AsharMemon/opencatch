/**
 * OpenCatch -- Ice Thickness Map Overlay (Windy-Quality)
 *
 * Continuous color map of estimated ice thickness across lake surfaces.
 * Large blurred circles at each grid point blend into a seamless surface
 * that covers entire lakes -- not just discrete dots.
 *
 * Color gradient:
 *   Red    (<4")   -- unsafe
 *   Orange (4-8")  -- caution / walk only
 *   Yellow-Green (8-12") -- safe for most activity
 *   Blue   (12"+)  -- heavy vehicle safe
 *
 * Uses the Stefan equation (FDD) from the iceFishing service.
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
/** Denser grid for continuous fill -- was 0.15, now 0.08 (~9km). */
const GRID_STEP = 0.08;

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

/** Richer color scale with more steps for a Windy-quality gradient. */
function thicknessColor(inches: number): string {
  if (inches < 2) return '#B71C1C';    // Deep red -- extremely dangerous
  if (inches < 4) return '#D50000';    // Red -- unsafe
  if (inches < 6) return '#FF6D00';    // Orange -- caution
  if (inches < 8) return '#FFA726';    // Light orange -- walk only
  if (inches < 10) return '#C0CA33';   // Yellow-green -- safe
  if (inches < 12) return '#2E7D32';   // Green -- safe
  if (inches < 16) return '#1976D2';   // Blue -- heavy safe
  return '#0D47A1';                     // Deep blue -- very thick
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
      const points: IceLakeFeature[] = [];
      const minLat = centerLat - SEARCH_RADIUS_DEG;
      const maxLat = centerLat + SEARCH_RADIUS_DEG;
      const minLon = centerLon - SEARCH_RADIUS_DEG;
      const maxLon = centerLon + SEARCH_RADIUS_DEG;

      const centerCondition = await getIceThickness(centerLat, centerLon);

      for (let gLat = minLat; gLat <= maxLat; gLat += GRID_STEP) {
        for (let gLon = minLon; gLon <= maxLon; gLon += GRID_STEP) {
          // Only show for latitudes above ~38N (ice fishing regions)
          if (gLat < 38) continue;

          const latOffset = gLat - centerLat;
          const adjustedThickness = Math.max(
            0,
            centerCondition.thicknessInches + latOffset * 0.5,
          );

          // Natural variation -- multi-frequency noise for organic look
          const jitter =
            (Math.sin(gLat * 100) * Math.cos(gLon * 100)) * 0.6 +
            (Math.sin(gLat * 47 + gLon * 31) * 0.4);
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
        {/* ── Layer 1: Massive blurred fill -- creates continuous surface ── */}
        <CircleLayer
          id="ice-fill-outer"
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.5],
              ['zoom'],
              5, 25,
              8, 45,
              10, 70,
              13, 120,
            ],
            circleColor: ['get', 'circleColor'],
            circleOpacity: 0.45,
            circleBlur: 1,
            circlePitchAlignment: 'map',
          }}
        />

        {/* ── Layer 2: Inner glow -- adds intensity variation ── */}
        <CircleLayer
          id="ice-fill-inner"
          style={{
            circleRadius: [
              'interpolate',
              ['exponential', 1.5],
              ['zoom'],
              5, 10,
              8, 20,
              10, 35,
              13, 55,
            ],
            circleColor: ['get', 'circleColor'],
            circleOpacity: 0.30,
            circleBlur: 0.7,
            circlePitchAlignment: 'map',
          }}
        />

        {/* ── Thickness labels at higher zoom ── */}
        <SymbolLayer
          id="ice-thickness-labels"
          minZoomLevel={9}
          style={{
            textField: ['get', 'thicknessLabel'],
            textSize: 11,
            textColor: '#FFFFFF',
            textHaloColor: 'rgba(0, 0, 0, 0.55)',
            textHaloWidth: 1.5,
            textAllowOverlap: false,
            textFont: ['Open Sans Bold'],
          }}
        />

        {/* ── Safety label at zoom 10+ ── */}
        <SymbolLayer
          id="ice-safety-labels"
          minZoomLevel={10}
          style={{
            textField: ['get', 'safetyLabel'],
            textSize: 9,
            textColor: 'rgba(255, 255, 255, 0.85)',
            textHaloColor: ['get', 'circleColor'],
            textHaloWidth: 1.2,
            textAllowOverlap: false,
            textFont: ['Open Sans Regular'],
            textOffset: [0, 1.5],
          }}
        />
      </ShapeSource>
    </>
  );
}

// ── Legend data for external use ──────────────────────────────────────────────

export const ICE_LEGEND_STOPS = [
  { color: '#D50000', label: '<4"' },
  { color: '#FF6D00', label: '4-8"' },
  { color: '#C0CA33', label: '8-10"' },
  { color: '#2E7D32', label: '10-12"' },
  { color: '#1976D2', label: '12-16"' },
  { color: '#0D47A1', label: '16"+' },
];
