/**
 * OpenCatch — Navigation Aids Overlay
 *
 * Queries OSM/OpenSeaMap for buoys, lights, and channel markers.
 * Renders colored markers (red right returning, green left) with
 * light characteristics shown on tap.
 */

import React, { useEffect, useRef, useState } from 'react';
import { palette } from '../theme/palette';

// ── Types ────────────────────────────────────────────────────────────────────

interface NavigationAid {
  id: string;
  type: 'buoy_lateral' | 'buoy_cardinal' | 'light' | 'beacon' | 'daymark' | 'other';
  name: string;
  color: string;
  lightCharacter: string | null; // e.g. "Fl(2) 6s"
  lat: number;
  lon: number;
}

interface Props {
  lat: number;
  lon: number;
  ShapeSource: any;
  CircleLayer: any;
  SymbolLayer: any;
}

// ── Overpass query ───────────────────────────────────────────────────────────

const OVERPASS_API = 'https://overpass-api.de/api/interpreter';
const SEARCH_RADIUS_M = 25000;
const REFRESH_DISTANCE_M = 5000;

function buildOverpassQuery(lat: number, lon: number): string {
  return `[out:json][timeout:15];
(
  node["seamark:type"="buoy_lateral"](around:${SEARCH_RADIUS_M},${lat},${lon});
  node["seamark:type"="buoy_cardinal"](around:${SEARCH_RADIUS_M},${lat},${lon});
  node["seamark:type"="buoy_safe_water"](around:${SEARCH_RADIUS_M},${lat},${lon});
  node["seamark:type"="buoy_isolated_danger"](around:${SEARCH_RADIUS_M},${lat},${lon});
  node["seamark:type"="buoy_special_purpose"](around:${SEARCH_RADIUS_M},${lat},${lon});
  node["seamark:type"="light"](around:${SEARCH_RADIUS_M},${lat},${lon});
  node["seamark:type"="beacon_lateral"](around:${SEARCH_RADIUS_M},${lat},${lon});
  node["seamark:type"="light_major"](around:${SEARCH_RADIUS_M},${lat},${lon});
  node["seamark:type"="light_minor"](around:${SEARCH_RADIUS_M},${lat},${lon});
);
out body;`;
}

function haversineDistance(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371000;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ── Color mapping (IALA Region B — US standard) ──────────────────────────────

function getAidColor(tags: Record<string, string>): string {
  const seamarkType = tags['seamark:type'] || '';
  const colour = tags['seamark:buoy_lateral:colour'] ||
    tags['seamark:beacon_lateral:colour'] ||
    tags['seamark:colour'] || '';
  const category = tags['seamark:buoy_lateral:category'] ||
    tags['seamark:beacon_lateral:category'] || '';

  // Red right returning (starboard in IALA B)
  if (colour.includes('red') || category === 'starboard') return '#E53935';
  // Green (port in IALA B)
  if (colour.includes('green') || category === 'port') return '#2E7D32';
  // Cardinal buoys
  if (seamarkType.includes('cardinal')) {
    if (tags['seamark:buoy_cardinal:category'] === 'north') return '#212121';
    if (tags['seamark:buoy_cardinal:category'] === 'south') return '#212121';
    if (tags['seamark:buoy_cardinal:category'] === 'east') return '#212121';
    if (tags['seamark:buoy_cardinal:category'] === 'west') return '#212121';
    return '#FFC107'; // yellow/black
  }
  // Safe water
  if (seamarkType.includes('safe_water')) return '#E53935';
  // Isolated danger
  if (seamarkType.includes('isolated_danger')) return '#212121';
  // Special purpose
  if (seamarkType.includes('special_purpose')) return '#FFC107';
  // Lights
  if (seamarkType.includes('light')) return '#FFD600';

  return '#78909C'; // default gray
}

function getLightCharacter(tags: Record<string, string>): string | null {
  const character = tags['seamark:light:character'];
  const period = tags['seamark:light:period'];
  const colour = tags['seamark:light:colour'];

  if (!character) return null;

  let result = character;
  if (period) result += ` ${period}s`;
  if (colour) result += ` ${colour}`;
  return result;
}

function getAidType(tags: Record<string, string>): string {
  const seamarkType = tags['seamark:type'] || '';
  if (seamarkType.includes('buoy_lateral')) return 'buoy_lateral';
  if (seamarkType.includes('buoy_cardinal')) return 'buoy_cardinal';
  if (seamarkType.includes('light')) return 'light';
  if (seamarkType.includes('beacon')) return 'beacon';
  return 'other';
}

// ── Parse response ──────────────────────────────────────────────────────────

function parseOverpassResponse(data: any): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  if (!data?.elements) return { type: 'FeatureCollection', features };

  for (const el of data.elements) {
    if (el.type !== 'node' || el.lat == null || el.lon == null) continue;

    const tags = el.tags || {};
    const name = tags.name || tags['seamark:name'] || tags['seamark:type']?.replace(/_/g, ' ') || 'Nav Aid';
    const color = getAidColor(tags);
    const lightChar = getLightCharacter(tags);
    const aidType = getAidType(tags);

    features.push({
      type: 'Feature',
      properties: {
        id: `navaid-${el.id}`,
        name,
        color,
        lightCharacter: lightChar || '',
        aidType,
        hasLight: lightChar ? 'true' : 'false',
      },
      geometry: {
        type: 'Point',
        coordinates: [el.lon, el.lat],
      },
    });
  }

  return { type: 'FeatureCollection', features };
}

// ── Component ────────────────────────────────────────────────────────────────

export function NavigationAidsOverlay({ lat, lon, ShapeSource, CircleLayer, SymbolLayer }: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection>({ type: 'FeatureCollection', features: [] });
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);

  useEffect(() => {
    if (lastCenter.current && haversineDistance(lastCenter.current.lat, lastCenter.current.lon, lat, lon) < REFRESH_DISTANCE_M) {
      return;
    }
    lastCenter.current = { lat, lon };

    const controller = new AbortController();
    (async () => {
      try {
        const query = buildOverpassQuery(lat, lon);
        const res = await fetch(OVERPASS_API, {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: `data=${encodeURIComponent(query)}`,
          signal: controller.signal,
        });
        if (!res.ok) return;
        const data = await res.json();
        setGeoJSON(parseOverpassResponse(data));
      } catch {
        // Network error or aborted
      }
    })();

    return () => controller.abort();
  }, [lat, lon]);

  if (geoJSON.features.length === 0) return null;

  return (
    <ShapeSource id="nav-aids-source" shape={geoJSON}>
      {/* Colored circle markers */}
      <CircleLayer
        id="nav-aids-circles"
        style={{
          circleRadius: [
            'interpolate', ['linear'], ['zoom'],
            8, 3,
            12, 6,
            16, 10,
          ],
          circleColor: ['get', 'color'],
          circleStrokeColor: '#FFFFFF',
          circleStrokeWidth: 1.5,
          circleOpacity: 0.9,
        }}
      />

      {/* Light character labels — shown at higher zoom */}
      <SymbolLayer
        id="nav-aids-labels"
        minZoomLevel={12}
        style={{
          textField: ['case',
            ['==', ['get', 'hasLight'], 'true'],
            ['get', 'lightCharacter'],
            ['get', 'name'],
          ],
          textSize: 10,
          textColor: '#333333',
          textHaloColor: 'rgba(255, 255, 255, 0.95)',
          textHaloWidth: 1.5,
          textFont: ['Open Sans Regular'],
          textOffset: [0, 1.2],
          textAnchor: 'top',
          textMaxWidth: 8,
          textOptional: true,
        }}
      />

      {/* Name labels at even higher zoom */}
      <SymbolLayer
        id="nav-aids-names"
        minZoomLevel={14}
        style={{
          textField: ['get', 'name'],
          textSize: 9,
          textColor: '#666666',
          textHaloColor: 'rgba(255, 255, 255, 0.9)',
          textHaloWidth: 1,
          textFont: ['Open Sans Regular'],
          textOffset: [0, 2.4],
          textAnchor: 'top',
          textOptional: true,
        }}
      />
    </ShapeSource>
  );
}
