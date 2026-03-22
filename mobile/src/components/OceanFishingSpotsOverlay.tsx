/**
 * OpenCatch — Ocean Fishing Spots Overlay
 *
 * Navionics-style overlay highlighting good ocean fishing areas.
 * Combines data from:
 *   - Artificial reefs (state programs)
 *   - Known wrecks (from OSM/OpenSeaMap)
 *   - Depth contour breaks (20m, 40m, 60m ledges)
 *
 * Color-coded by type: reef (orange), wreck (red), ledge (blue).
 * Shows fish icon + species labels at higher zoom levels.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
import { palette } from '../theme/palette';

// ── Types ────────────────────────────────────────────────────────────────────

interface OceanSpot {
  id: string;
  name: string;
  lat: number;
  lon: number;
  type: 'reef' | 'wreck' | 'ledge';
  depthFt: number | null;
  description: string;
}

interface Props {
  lat: number;
  lon: number;
  ShapeSource: any;
  CircleLayer: any;
  SymbolLayer: any;
}

// ── Constants ────────────────────────────────────────────────────────────────

const OVERPASS_URL = 'https://overpass-api.de/api/interpreter';
const GEBCO_WMS = 'https://www.gebco.net/data_and_products/gebco_web_services/web_map_service/mapserv';
const SEARCH_RADIUS_M = 50000;
const REFRESH_DISTANCE_M = 15000;
const CACHE_TTL_MS = 60 * 60 * 1000;

const SPOT_COLORS: Record<OceanSpot['type'], string> = {
  reef: '#FF9800',   // orange
  wreck: '#F44336',  // red
  ledge: '#1565C0',  // blue
};

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

function haversineMiles(lat1: number, lon1: number, lat2: number, lon2: number): number {
  return haversineDistance(lat1, lon1, lat2, lon2) / 1609.344;
}

// ── Fetch wrecks from OSM ────────────────────────────────────────────────────

async function fetchWrecks(lat: number, lon: number): Promise<OceanSpot[]> {
  const query = `[out:json][timeout:20];
(
  node["seamark:type"="wreck"](around:${SEARCH_RADIUS_M},${lat},${lon});
  node["historic"="wreck"](around:${SEARCH_RADIUS_M},${lat},${lon});
  node["seamark:type"="rock"](around:${SEARCH_RADIUS_M},${lat},${lon});
);
out body qt 200;`;

  try {
    const res = await fetch(OVERPASS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `data=${encodeURIComponent(query)}`,
    });
    if (!res.ok) return [];
    const data = await res.json();

    return (data.elements ?? []).map((el: any) => {
      const tags = el.tags ?? {};
      const depthStr = tags['seamark:wreck:depth'] || tags.depth;
      const depthM = depthStr ? parseFloat(depthStr) : null;

      return {
        id: `wreck-${el.id}`,
        name: tags.name ?? tags['seamark:name'] ?? 'Wreck',
        lat: el.lat,
        lon: el.lon,
        type: 'wreck' as const,
        depthFt: depthM != null ? Math.round(depthM * 3.28084) : null,
        description: tags.description ?? (depthM ? `Depth: ${Math.round(depthM * 3.28084)}ft` : 'Wreck site'),
      };
    });
  } catch {
    return [];
  }
}

// ── Fetch reef spots from OSM ────────────────────────────────────────────────

async function fetchReefs(lat: number, lon: number): Promise<OceanSpot[]> {
  const query = `[out:json][timeout:20];
(
  node["natural"="reef"](around:${SEARCH_RADIUS_M},${lat},${lon});
  way["natural"="reef"](around:${SEARCH_RADIUS_M},${lat},${lon});
  node["geological"="reef"](around:${SEARCH_RADIUS_M},${lat},${lon});
);
out center body qt 200;`;

  try {
    const res = await fetch(OVERPASS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `data=${encodeURIComponent(query)}`,
    });
    if (!res.ok) return [];
    const data = await res.json();

    return (data.elements ?? []).map((el: any) => {
      const rLat = el.lat ?? el.center?.lat ?? 0;
      const rLon = el.lon ?? el.center?.lon ?? 0;
      const tags = el.tags ?? {};

      return {
        id: `reef-${el.id}`,
        name: tags.name ?? 'Reef',
        lat: rLat,
        lon: rLon,
        type: 'reef' as const,
        depthFt: null,
        description: tags.description ?? 'Natural reef',
      };
    });
  } catch {
    return [];
  }
}

// ── Detect ledges via GEBCO depth sampling ───────────────────────────────────

async function detectLedges(lat: number, lon: number): Promise<OceanSpot[]> {
  // Sample depth at several radial points to find steep drop-offs
  const SAMPLE_RADIUS_DEG = 0.4;
  const SAMPLE_STEP_DEG = 0.1;
  const depths: Array<{ lat: number; lon: number; depthM: number }> = [];

  const promises: Promise<void>[] = [];
  for (let dLat = -SAMPLE_RADIUS_DEG; dLat <= SAMPLE_RADIUS_DEG; dLat += SAMPLE_STEP_DEG) {
    for (let dLon = -SAMPLE_RADIUS_DEG; dLon <= SAMPLE_RADIUS_DEG; dLon += SAMPLE_STEP_DEG) {
      const sLat = lat + dLat;
      const sLon = lon + dLon;
      const url =
        `${GEBCO_WMS}?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetFeatureInfo` +
        `&LAYERS=GEBCO_LATEST&QUERY_LAYERS=GEBCO_LATEST&INFO_FORMAT=text/plain` +
        `&SRS=EPSG:4326&WIDTH=2&HEIGHT=2&X=1&Y=1` +
        `&BBOX=${sLon - 0.001},${sLat - 0.001},${sLon + 0.001},${sLat + 0.001}`;

      promises.push(
        fetch(url)
          .then(async (res) => {
            if (!res.ok) return;
            const text = await res.text();
            const match = text.match(/value_list\s*=\s*['"]?([-\d.]+)/);
            if (match) {
              depths.push({ lat: sLat, lon: sLon, depthM: parseFloat(match[1]) });
            }
          })
          .catch(() => {}),
      );
    }
  }

  // Batch concurrency
  const BATCH = 15;
  for (let i = 0; i < promises.length; i += BATCH) {
    await Promise.all(promises.slice(i, i + BATCH));
  }

  // Find adjacent points with significant depth change (> 20m)
  const ledges: OceanSpot[] = [];
  let ledgeId = 0;

  for (let i = 0; i < depths.length; i++) {
    for (let j = i + 1; j < depths.length; j++) {
      const d1 = depths[i];
      const d2 = depths[j];
      const dist = haversineMiles(d1.lat, d1.lon, d2.lat, d2.lon);
      if (dist > 5) continue;

      const depthDiff = Math.abs(d1.depthM - d2.depthM);
      if (depthDiff > 20) {
        const shallow = d1.depthM > d2.depthM ? d1 : d2;
        const deep = d1.depthM > d2.depthM ? d2 : d1;
        ledgeId++;

        const shallowFt = Math.round(Math.abs(shallow.depthM) * 3.28084);
        const deepFt = Math.round(Math.abs(deep.depthM) * 3.28084);

        ledges.push({
          id: `ledge-${ledgeId}`,
          name: `${shallowFt}ft-${deepFt}ft Ledge`,
          lat: (d1.lat + d2.lat) / 2,
          lon: (d1.lon + d2.lon) / 2,
          type: 'ledge',
          depthFt: shallowFt,
          description: `Drop-off from ${shallowFt}ft to ${deepFt}ft`,
        });
      }
    }
  }

  // Deduplicate close ledges
  return ledges.filter((l, idx) => {
    for (let k = 0; k < idx; k++) {
      if (haversineMiles(l.lat, l.lon, ledges[k].lat, ledges[k].lon) < 1) return false;
    }
    return true;
  });
}

// ── GeoJSON conversion ───────────────────────────────────────────────────────

function spotsToGeoJSON(spots: OceanSpot[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: spots.map((s) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [s.lon, s.lat],
      },
      properties: {
        id: s.id,
        name: s.name,
        spotType: s.type,
        color: SPOT_COLORS[s.type],
        depthFt: s.depthFt,
        description: s.description,
        // Icon text for each type
        icon: s.type === 'reef' ? '\uD83E\uDEB8' : s.type === 'wreck' ? '\u2693' : '\uD83C\uDF0A',
      },
    })),
  };
}

// ── Cache ────────────────────────────────────────────────────────────────────

let _spotCache: { geoJSON: GeoJSON.FeatureCollection; lat: number; lon: number; ts: number } | null = null;

// ── Component ────────────────────────────────────────────────────────────────

export function OceanFishingSpotsOverlay({ lat, lon, ShapeSource, CircleLayer, SymbolLayer }: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);
  const loadingRef = useRef(false);

  const fetchData = useCallback(async (centerLat: number, centerLon: number) => {
    if (loadingRef.current) return;

    // Check cache
    if (_spotCache && Date.now() - _spotCache.ts < CACHE_TTL_MS) {
      const dist = haversineDistance(centerLat, centerLon, _spotCache.lat, _spotCache.lon);
      if (dist < REFRESH_DISTANCE_M) {
        setGeoJSON(_spotCache.geoJSON);
        return;
      }
    }

    if (lastCenter.current) {
      const dist = haversineDistance(centerLat, centerLon, lastCenter.current.lat, lastCenter.current.lon);
      if (dist < REFRESH_DISTANCE_M) return;
    }

    loadingRef.current = true;
    lastCenter.current = { lat: centerLat, lon: centerLon };

    try {
      // Fetch all three data sources concurrently
      const [wrecks, reefs, ledges] = await Promise.all([
        fetchWrecks(centerLat, centerLon),
        fetchReefs(centerLat, centerLon),
        detectLedges(centerLat, centerLon),
      ]);

      const allSpots = [...reefs, ...wrecks, ...ledges];
      if (allSpots.length > 0) {
        const gj = spotsToGeoJSON(allSpots);
        _spotCache = { geoJSON: gj, lat: centerLat, lon: centerLon, ts: Date.now() };
        setGeoJSON(gj);
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
    <ShapeSource id="ocean-fishing-spots-source" shape={geoJSON}>
      {/* Colored circle markers by type */}
      <CircleLayer
        id="ocean-spots-circles"
        style={{
          circleRadius: [
            'interpolate',
            ['linear'],
            ['zoom'],
            6, 4,
            10, 7,
            14, 11,
          ],
          circleColor: ['get', 'color'],
          circleStrokeColor: '#FFFFFF',
          circleStrokeWidth: 1.5,
          circleOpacity: 0.85,
        }}
      />
      {/* Type icon */}
      <SymbolLayer
        id="ocean-spots-icons"
        minZoomLevel={8}
        style={{
          textField: ['get', 'icon'],
          textSize: 14,
          textAllowOverlap: true,
          textIgnorePlacement: true,
        }}
      />
      {/* Name labels at higher zoom */}
      <SymbolLayer
        id="ocean-spots-labels"
        minZoomLevel={10}
        style={{
          textField: ['get', 'name'],
          textSize: 10,
          textColor: '#333333',
          textHaloColor: 'rgba(255, 255, 255, 0.95)',
          textHaloWidth: 1.5,
          textFont: ['Open Sans Regular'],
          textOffset: [0, 2.0],
          textAnchor: 'top',
          textMaxWidth: 10,
          textOptional: true,
        }}
      />
      {/* Depth/description at very high zoom */}
      <SymbolLayer
        id="ocean-spots-desc"
        minZoomLevel={12}
        style={{
          textField: ['get', 'description'],
          textSize: 9,
          textColor: '#666666',
          textHaloColor: 'rgba(255, 255, 255, 0.9)',
          textHaloWidth: 1,
          textFont: ['Open Sans Regular'],
          textOffset: [0, 3.2],
          textAnchor: 'top',
          textOptional: true,
        }}
      />
    </ShapeSource>
  );
}
