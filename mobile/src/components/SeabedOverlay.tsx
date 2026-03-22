/**
 * OpenCatch -- Seabed Characteristics Overlay
 *
 * Shows seabed type as colored markers on the map using standard
 * nautical abbreviations. Color-coded by anchoring suitability:
 *   Green = good (sand, mud, clay)
 *   Yellow = fair (gravel, shells, mixed)
 *   Red = poor (rock, coral, weed)
 *
 * Only visible at zoom 10+ (too noisy otherwise).
 * Tap a point for popup with full description + anchoring advice.
 */

import React, { useEffect, useRef, useState } from 'react';
import {
  getSeabedInArea,
  seabedToGeoJSON,
  type SeabedPoint,
  type BBox,
} from '../services/seabedCharacteristics';

// ── Types ────────────────────────────────────────────────────────

interface Props {
  lat: number;
  lon: number;
  zoom: number;
  ShapeSource: any;
  CircleLayer: any;
  SymbolLayer: any;
  onLoadStart?: () => void;
  onLoadEnd?: () => void;
  onPointPress?: (point: SeabedPoint) => void;
}

// ── Constants ────────────────────────────────────────────────────

const MIN_ZOOM = 10;
const SEARCH_RADIUS_DEG = 0.15;
const REFRESH_DISTANCE_DEG = 0.08;

// ── Component ────────────────────────────────────────────────────

export function SeabedOverlay({
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

  const loadData = async (centerLat: number, centerLon: number) => {
    onLoadStart?.();
    try {
      const bbox: BBox = {
        minLat: centerLat - SEARCH_RADIUS_DEG,
        maxLat: centerLat + SEARCH_RADIUS_DEG,
        minLon: centerLon - SEARCH_RADIUS_DEG,
        maxLon: centerLon + SEARCH_RADIUS_DEG,
      };
      const points = await getSeabedInArea(bbox);
      if (points.length > 0) {
        setGeoJSON(seabedToGeoJSON(points));
      } else {
        setGeoJSON(null);
      }
      lastCenter.current = { lat: centerLat, lon: centerLon };
    } catch (err) {
      console.warn('[SeabedOverlay] Load failed:', err);
    } finally {
      onLoadEnd?.();
    }
  };

  useEffect(() => {
    if (zoom < MIN_ZOOM) return;
    if (
      !lastCenter.current ||
      Math.abs(lat - lastCenter.current.lat) > REFRESH_DISTANCE_DEG ||
      Math.abs(lon - lastCenter.current.lon) > REFRESH_DISTANCE_DEG
    ) {
      loadData(lat, lon);
    }
  }, [lat, lon, zoom]);

  // Don't render below min zoom
  if (zoom < MIN_ZOOM || !geoJSON || !ShapeSource || !CircleLayer || !SymbolLayer) {
    return null;
  }

  return (
    <>
      <ShapeSource id="seabed-chars-source" shape={geoJSON}>
        {/* Colored circle base — anchoring suitability color */}
        <CircleLayer
          id="seabed-circle"
          style={{
            circleRadius: [
              'interpolate',
              ['linear'],
              ['zoom'],
              10, 8,
              13, 14,
              16, 20,
            ],
            circleColor: ['get', 'color'],
            circleOpacity: 0.75,
            circleStrokeColor: '#FFFFFF',
            circleStrokeWidth: 1.5,
          }}
        />

        {/* Nautical abbreviation label inside circle */}
        <SymbolLayer
          id="seabed-abbrev-label"
          style={{
            textField: ['get', 'abbreviation'],
            textSize: [
              'interpolate',
              ['linear'],
              ['zoom'],
              10, 8,
              13, 11,
              16, 14,
            ],
            textFont: ['Open Sans Bold'],
            textColor: '#FFFFFF',
            textHaloColor: 'rgba(0, 0, 0, 0.3)',
            textHaloWidth: 0.5,
            textAllowOverlap: true,
            textIgnorePlacement: false,
          }}
        />

        {/* Full description label at higher zoom */}
        <SymbolLayer
          id="seabed-desc-label"
          minZoomLevel={13}
          style={{
            textField: ['get', 'type'],
            textSize: 9,
            textFont: ['Open Sans Regular'],
            textColor: ['get', 'color'],
            textHaloColor: '#FFFFFF',
            textHaloWidth: 1,
            textOffset: [0, 2.0],
            textAllowOverlap: false,
            textTransform: 'uppercase',
          }}
        />
      </ShapeSource>
    </>
  );
}
