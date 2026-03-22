/**
 * OpenCatch — Marine Gas Station Overlay
 *
 * Shows marine fuel docks on the map as gas pump icons.
 * Tap shows name, fuel types, and distance from current location.
 * Uses the marineGasStations service to fetch data from OSM.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
import { palette } from '../theme/palette';
import {
  getMarineGasStations,
  marineGasToGeoJSON,
  type MarineGasBBox,
} from '../services/marineGasStations';

// ── Types ────────────────────────────────────────────────────────────────────

interface Props {
  lat: number;
  lon: number;
  ShapeSource: any;
  CircleLayer: any;
  SymbolLayer: any;
}

// ── Constants ────────────────────────────────────────────────────────────────

const SEARCH_RADIUS_DEG = 0.5;
const REFRESH_DISTANCE_M = 5000;

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

// ── Component ────────────────────────────────────────────────────────────────

export function MarineGasOverlay({ lat, lon, ShapeSource, CircleLayer, SymbolLayer }: Props) {
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

    try {
      const bbox: MarineGasBBox = {
        south: centerLat - SEARCH_RADIUS_DEG,
        north: centerLat + SEARCH_RADIUS_DEG,
        west: centerLon - SEARCH_RADIUS_DEG,
        east: centerLon + SEARCH_RADIUS_DEG,
      };
      const stations = await getMarineGasStations(bbox);
      if (stations.length > 0) {
        setGeoJSON(marineGasToGeoJSON(stations));
      } else {
        setGeoJSON(null);
      }
    } catch {
      // Keep previous data
    } finally {
      loadingRef.current = false;
    }
  }, []);

  useEffect(() => {
    fetchData(lat, lon);
  }, [lat, lon, fetchData]);

  if (!geoJSON || geoJSON.features.length === 0) return null;

  return (
    <ShapeSource id="marine-gas-source" shape={geoJSON}>
      {/* Fuel dock marker — orange circle with white border */}
      <CircleLayer
        id="marine-gas-circles"
        style={{
          circleRadius: [
            'interpolate',
            ['linear'],
            ['zoom'],
            8, 4,
            12, 7,
            16, 10,
          ],
          circleColor: '#E65100',
          circleStrokeColor: '#FFFFFF',
          circleStrokeWidth: 1.5,
          circleOpacity: 0.9,
        }}
      />
      {/* Gas pump icon text (unicode) at the marker */}
      <SymbolLayer
        id="marine-gas-icon"
        style={{
          textField: '\u26FD',
          textSize: [
            'interpolate',
            ['linear'],
            ['zoom'],
            8, 8,
            12, 12,
            16, 16,
          ],
          textAllowOverlap: true,
          textIgnorePlacement: true,
        }}
      />
      {/* Name labels at higher zoom */}
      <SymbolLayer
        id="marine-gas-labels"
        minZoomLevel={11}
        style={{
          textField: ['get', 'name'],
          textSize: 10,
          textColor: '#333333',
          textHaloColor: 'rgba(255, 255, 255, 0.95)',
          textHaloWidth: 1.5,
          textFont: ['Open Sans Regular'],
          textOffset: [0, 1.8],
          textAnchor: 'top',
          textMaxWidth: 10,
          textOptional: true,
        }}
      />
      {/* Fuel type labels at very high zoom */}
      <SymbolLayer
        id="marine-gas-fuel-labels"
        minZoomLevel={13}
        style={{
          textField: ['get', 'fuelTypes'],
          textSize: 9,
          textColor: '#666666',
          textHaloColor: 'rgba(255, 255, 255, 0.9)',
          textHaloWidth: 1,
          textFont: ['Open Sans Regular'],
          textOffset: [0, 3.0],
          textAnchor: 'top',
          textOptional: true,
        }}
      />
    </ShapeSource>
  );
}
