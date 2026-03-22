/**
 * OpenCatch -- Maritime Boundaries Overlay
 *
 * Safety-critical overlay showing:
 *   - Shipping lanes as purple/magenta dashed lines with direction arrows
 *   - Traffic separation schemes as hatched zones
 *   - Restricted areas as RED shaded polygons with warning icons
 *   - Marine sanctuaries as GREEN shaded polygons
 *   - Military zones as RED with warning icons
 *
 * MUST be highly visible — these are safety-critical features.
 * Tap restricted area for name, type, restrictions, authority.
 */

import React, { useEffect, useRef, useState } from 'react';
import {
  getShippingLanes,
  getRestrictedAreas,
  getTrafficSeparationSchemes,
  shippingLanesToGeoJSON,
  restrictedAreasToGeoJSON,
  tssToGeoJSON,
  type BBox,
} from '../services/maritimeRoutes';

// ── Types ────────────────────────────────────────────────────────

interface Props {
  lat: number;
  lon: number;
  zoom: number;
  ShapeSource: any;
  LineLayer: any;
  FillLayer: any;
  SymbolLayer: any;
  CircleLayer: any;
  onLoadStart?: () => void;
  onLoadEnd?: () => void;
}

// ── Constants ────────────────────────────────────────────────────

const SEARCH_RADIUS_DEG = 0.3;
const REFRESH_DISTANCE_DEG = 0.15;

// ── Component ────────────────────────────────────────────────────

export function MaritimeBoundariesOverlay({
  lat,
  lon,
  zoom,
  ShapeSource,
  LineLayer,
  FillLayer,
  SymbolLayer,
  CircleLayer,
  onLoadStart,
  onLoadEnd,
}: Props) {
  const [lanesGeoJSON, setLanesGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const [restrictedGeoJSON, setRestrictedGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const [tssGeoJSON, setTssGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
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

      const [lanes, restricted, tss] = await Promise.all([
        getShippingLanes(bbox),
        getRestrictedAreas(bbox),
        getTrafficSeparationSchemes(bbox),
      ]);

      setLanesGeoJSON(lanes.length > 0 ? shippingLanesToGeoJSON(lanes) : null);
      setRestrictedGeoJSON(restricted.length > 0 ? restrictedAreasToGeoJSON(restricted) : null);
      setTssGeoJSON(tss.length > 0 ? tssToGeoJSON(tss) : null);

      lastCenter.current = { lat: centerLat, lon: centerLon };
    } catch (err) {
      console.warn('[MaritimeBoundariesOverlay] Load failed:', err);
    } finally {
      onLoadEnd?.();
    }
  };

  useEffect(() => {
    if (
      !lastCenter.current ||
      Math.abs(lat - lastCenter.current.lat) > REFRESH_DISTANCE_DEG ||
      Math.abs(lon - lastCenter.current.lon) > REFRESH_DISTANCE_DEG
    ) {
      loadData(lat, lon);
    }
  }, [lat, lon]);

  if (!ShapeSource || !LineLayer || !FillLayer || !SymbolLayer) return null;

  const hasLanes = lanesGeoJSON && lanesGeoJSON.features.length > 0;
  const hasRestricted = restrictedGeoJSON && restrictedGeoJSON.features.length > 0;
  const hasTSS = tssGeoJSON && tssGeoJSON.features.length > 0;

  if (!hasLanes && !hasRestricted && !hasTSS) return null;

  return (
    <>
      {/* ── Shipping Lanes — purple/magenta dashed lines ── */}
      {hasLanes && (
        <ShapeSource id="maritime-lanes-source" shape={lanesGeoJSON}>
          {/* Lane outline (wider, semi-transparent) */}
          <LineLayer
            id="maritime-lanes-outline"
            style={{
              lineColor: '#9C27B0',
              lineWidth: 6,
              lineOpacity: 0.25,
              lineCap: 'round',
              lineJoin: 'round',
            }}
          />
          {/* Lane dashed line */}
          <LineLayer
            id="maritime-lanes-dash"
            style={{
              lineColor: '#AB47BC',
              lineWidth: 2.5,
              lineOpacity: 0.85,
              lineDasharray: [6, 4],
              lineCap: 'butt',
              lineJoin: 'round',
            }}
          />
          {/* Direction arrows along lane */}
          <SymbolLayer
            id="maritime-lanes-arrows"
            style={{
              symbolPlacement: 'line',
              symbolSpacing: 80,
              iconImage: 'arrow-up',
              iconSize: 0.6,
              iconRotate: 90, // Along the line
              iconRotationAlignment: 'map',
              iconAllowOverlap: true,
              iconColor: '#9C27B0',
              iconOpacity: 0.85,
            }}
          />
          {/* Lane name label */}
          <SymbolLayer
            id="maritime-lanes-label"
            minZoomLevel={10}
            style={{
              symbolPlacement: 'line',
              textField: ['get', 'name'],
              textSize: 10,
              textFont: ['Open Sans Bold'],
              textColor: '#7B1FA2',
              textHaloColor: '#FFFFFF',
              textHaloWidth: 1.5,
              textAllowOverlap: false,
            }}
          />
        </ShapeSource>
      )}

      {/* ── Traffic Separation Schemes — hatched zones ── */}
      {hasTSS && (
        <ShapeSource id="maritime-tss-source" shape={tssGeoJSON}>
          <FillLayer
            id="maritime-tss-fill"
            style={{
              fillColor: '#CE93D8',
              fillOpacity: 0.2,
            }}
          />
          <LineLayer
            id="maritime-tss-outline"
            style={{
              lineColor: '#9C27B0',
              lineWidth: 2,
              lineOpacity: 0.6,
              lineDasharray: [3, 3],
            }}
          />
          <SymbolLayer
            id="maritime-tss-label"
            minZoomLevel={9}
            style={{
              textField: ['get', 'name'],
              textSize: 10,
              textFont: ['Open Sans Regular'],
              textColor: '#7B1FA2',
              textHaloColor: '#FFFFFF',
              textHaloWidth: 1.5,
              textAllowOverlap: false,
            }}
          />
        </ShapeSource>
      )}

      {/* ── Restricted Areas — colored polygons with icons ── */}
      {hasRestricted && (
        <ShapeSource id="maritime-restricted-source" shape={restrictedGeoJSON}>
          {/* Area fill — color based on type */}
          <FillLayer
            id="maritime-restricted-fill"
            style={{
              fillColor: ['get', 'color'],
              fillOpacity: [
                'match',
                ['get', 'severity'],
                'danger', 0.35,
                'caution', 0.25,
                'warning', 0.2,
                0.15,
              ],
            }}
          />
          {/* Area outline — solid, thick for visibility */}
          <LineLayer
            id="maritime-restricted-outline"
            style={{
              lineColor: ['get', 'color'],
              lineWidth: [
                'match',
                ['get', 'severity'],
                'danger', 3,
                'caution', 2.5,
                2,
              ],
              lineOpacity: 0.9,
            }}
          />
          {/* Warning icon at centroid */}
          <SymbolLayer
            id="maritime-restricted-icon"
            style={{
              iconImage: 'warning',
              iconSize: [
                'match',
                ['get', 'severity'],
                'danger', 1.2,
                'caution', 1.0,
                0.8,
              ],
              iconAllowOverlap: true,
              iconColor: ['get', 'color'],
              iconOpacity: 0.9,
            }}
          />
          {/* Name label */}
          <SymbolLayer
            id="maritime-restricted-label"
            minZoomLevel={9}
            style={{
              textField: ['get', 'name'],
              textSize: 11,
              textFont: ['Open Sans Bold'],
              textColor: ['get', 'color'],
              textHaloColor: '#FFFFFF',
              textHaloWidth: 2,
              textOffset: [0, 2.5],
              textAllowOverlap: false,
            }}
          />
          {/* Type sublabel at higher zoom */}
          <SymbolLayer
            id="maritime-restricted-type"
            minZoomLevel={11}
            style={{
              textField: [
                'match',
                ['get', 'areaType'],
                'military', 'MILITARY ZONE',
                'marine_sanctuary', 'MARINE SANCTUARY',
                'nature_reserve', 'NATURE RESERVE',
                'security_zone', 'SECURITY ZONE',
                'anchoring_prohibited', 'NO ANCHORING',
                'fishing_prohibited', 'NO FISHING',
                'speed_restricted', 'SPEED RESTRICTED',
                'RESTRICTED',
              ],
              textSize: 9,
              textFont: ['Open Sans Bold'],
              textColor: ['get', 'color'],
              textHaloColor: '#FFFFFF',
              textHaloWidth: 1.5,
              textOffset: [0, 3.8],
              textAllowOverlap: false,
              textTransform: 'uppercase',
            }}
          />
        </ShapeSource>
      )}
    </>
  );
}
