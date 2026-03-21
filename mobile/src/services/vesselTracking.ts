/**
 * Vessel Tracking Service for OpenCatch.
 *
 * Provides AIS (Automatic Identification System) vessel tracking
 * data for marine situational awareness. Uses AISHub community API
 * for nearby vessel positions and static vessel information.
 *
 * Also includes location sharing via the native share sheet.
 *
 * AISHub:  https://www.aishub.net/api
 */

import { Share, Platform } from 'react-native';

// ── Types ────────────────────────────────────────────────────────

/** An AIS vessel position report. */
export interface AISPosition {
  /** Maritime Mobile Service Identity (9-digit number). */
  mmsi: string;
  /** Latitude in decimal degrees. */
  lat: number;
  /** Longitude in decimal degrees. */
  lon: number;
  /** Course over ground in degrees (0-360). */
  cogDeg: number;
  /** Speed over ground in knots. */
  sogKnots: number;
  /** True heading in degrees, or null if unavailable. */
  headingDeg: number | null;
  /** Navigation status code (0=underway, 1=anchored, 5=moored, etc.). */
  navStatus: number;
  /** ISO-8601 UTC timestamp of the position report. */
  timestamp: string;
}

/** Static vessel information from AIS Class A/B. */
export interface Vessel {
  /** Maritime Mobile Service Identity. */
  mmsi: string;
  /** Vessel name. */
  name: string;
  /** IMO number, if available. */
  imo?: string;
  /** Call sign. */
  callSign?: string;
  /** AIS ship type code. */
  shipType: number;
  /** Human-readable ship type. */
  shipTypeLabel: string;
  /** Vessel length in meters. */
  lengthM: number | null;
  /** Vessel beam (width) in meters. */
  beamM: number | null;
  /** Vessel draft in meters. */
  draftM: number | null;
  /** Destination, if reported. */
  destination?: string;
  /** ETA as ISO-8601 string, if reported. */
  eta?: string;
  /** Flag state country code. */
  flag?: string;
  /** Latest known position. */
  lastPosition: AISPosition | null;
}

/** A nearby vessel with distance from the queried position. */
export interface NearbyVessel extends Vessel {
  /** Distance from the queried coordinates in nautical miles. */
  distanceNm: number;
}

// ── Constants ────────────────────────────────────────────────────

/**
 * AISHub community data sharing API. Users should register for a free
 * API key at aishub.net. The key is optional — we fall back gracefully.
 */
const AISHUB_BASE = 'https://data.aishub.net/ws.php';

const USER_AGENT = 'OpenCatch/1.0 (contact@opencatch.app)';
const REQUEST_TIMEOUT_MS = 15_000;

/** Cache TTL: 5 minutes for vessel positions. */
const CACHE_TTL_MS = 5 * 60 * 1000;

/** Default search radius in degrees (~30 nautical miles). */
const DEFAULT_RADIUS_DEG = 0.5;

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

const cache = new Map<string, CacheEntry<any>>();

function getCached<T>(key: string): T | null {
  const entry = cache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    cache.delete(key);
    return null;
  }
  return entry.data as T;
}

function setCache<T>(key: string, data: T): void {
  cache.set(key, { data, timestamp: Date.now() });
}

// ── Fetch helper ─────────────────────────────────────────────────

async function fetchJSON<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      headers: { 'User-Agent': USER_AGENT },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

// ── Haversine distance (nautical miles) ──────────────────────────

function haversineNm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 3440.065; // Earth radius in nautical miles
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ── Ship type decoder ────────────────────────────────────────────

function decodeShipType(code: number): string {
  if (code >= 20 && code <= 29) return 'Wing in ground';
  if (code === 30) return 'Fishing';
  if (code === 31 || code === 32) return 'Towing';
  if (code === 33) return 'Dredger';
  if (code === 34) return 'Diving ops';
  if (code === 35) return 'Military';
  if (code === 36) return 'Sailing';
  if (code === 37) return 'Pleasure craft';
  if (code >= 40 && code <= 49) return 'High-speed craft';
  if (code === 50) return 'Pilot vessel';
  if (code === 51) return 'Search & rescue';
  if (code === 52) return 'Tug';
  if (code === 53) return 'Port tender';
  if (code === 55) return 'Law enforcement';
  if (code >= 60 && code <= 69) return 'Passenger';
  if (code >= 70 && code <= 79) return 'Cargo';
  if (code >= 80 && code <= 89) return 'Tanker';
  return 'Other';
}

// ── Nav status decoder ───────────────────────────────────────────

/** Decode AIS navigation status code to human-readable string. */
export function decodeNavStatus(code: number): string {
  switch (code) {
    case 0: return 'Under way (engine)';
    case 1: return 'At anchor';
    case 2: return 'Not under command';
    case 3: return 'Restricted maneuverability';
    case 4: return 'Constrained by draft';
    case 5: return 'Moored';
    case 6: return 'Aground';
    case 7: return 'Engaged in fishing';
    case 8: return 'Under way (sailing)';
    case 14: return 'AIS-SART active';
    default: return 'Unknown';
  }
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Fetch nearby vessels via AIS data within approximately 30nm of
 * the given position. Returns vessels sorted by distance.
 *
 * @param lat — Latitude in decimal degrees.
 * @param lon — Longitude in decimal degrees.
 * @param apiKey — Optional AISHub API key. Without it, results may be limited.
 */
export async function getNearbyVessels(
  lat: number,
  lon: number,
  apiKey?: string,
): Promise<NearbyVessel[]> {
  const key = `vessels:${lat.toFixed(2)},${lon.toFixed(2)}`;
  const cached = getCached<NearbyVessel[]>(key);
  if (cached) return cached;

  const minLat = lat - DEFAULT_RADIUS_DEG;
  const maxLat = lat + DEFAULT_RADIUS_DEG;
  const minLon = lon - DEFAULT_RADIUS_DEG;
  const maxLon = lon + DEFAULT_RADIUS_DEG;

  // Build AISHub URL. The format parameter requests JSON output.
  const params = new URLSearchParams({
    username: apiKey ?? '',
    format: '1', // JSON
    output: 'json',
    compress: '0',
    latmin: minLat.toFixed(4),
    latmax: maxLat.toFixed(4),
    lonmin: minLon.toFixed(4),
    lonmax: maxLon.toFixed(4),
  });

  const url = `${AISHUB_BASE}?${params.toString()}`;

  try {
    const data = await fetchJSON<Array<{
      MMSI: number;
      NAME?: string;
      IMO?: number;
      CALLSIGN?: string;
      SHIP_TYPE?: number;
      LENGTH?: number;
      WIDTH?: number;
      DRAUGHT?: number;
      DESTINATION?: string;
      ETA?: string;
      LATITUDE: number;
      LONGITUDE: number;
      COG: number;
      SOG: number;
      HEADING?: number;
      NAVSTAT?: number;
      TIME?: string;
      FLAG?: string;
    }> | { ERROR?: string }>(url);

    // AISHub returns an error object if key is invalid.
    if (!Array.isArray(data)) {
      console.warn('[vesselTracking] AISHub error:', (data as any).ERROR);
      return [];
    }

    const vessels: NearbyVessel[] = data.map((v) => {
      const shipType = v.SHIP_TYPE ?? 0;
      const position: AISPosition = {
        mmsi: String(v.MMSI),
        lat: v.LATITUDE,
        lon: v.LONGITUDE,
        cogDeg: v.COG ?? 0,
        sogKnots: v.SOG ?? 0,
        headingDeg: v.HEADING != null && v.HEADING < 511 ? v.HEADING : null,
        navStatus: v.NAVSTAT ?? 15,
        timestamp: v.TIME ?? new Date().toISOString(),
      };

      return {
        mmsi: String(v.MMSI),
        name: v.NAME?.trim() ?? `MMSI ${v.MMSI}`,
        imo: v.IMO ? String(v.IMO) : undefined,
        callSign: v.CALLSIGN?.trim() ?? undefined,
        shipType,
        shipTypeLabel: decodeShipType(shipType),
        lengthM: v.LENGTH ?? null,
        beamM: v.WIDTH ?? null,
        draftM: v.DRAUGHT != null ? v.DRAUGHT / 10 : null,
        destination: v.DESTINATION?.trim() ?? undefined,
        eta: v.ETA ?? undefined,
        flag: v.FLAG ?? undefined,
        lastPosition: position,
        distanceNm: haversineNm(lat, lon, v.LATITUDE, v.LONGITUDE),
      };
    });

    // Sort by distance.
    vessels.sort((a, b) => a.distanceNm - b.distanceNm);

    setCache(key, vessels);
    return vessels;
  } catch (err) {
    console.warn('[vesselTracking] getNearbyVessels failed:', err);
    return [];
  }
}

/**
 * Get detailed vessel information by MMSI. Uses the MarineTraffic
 * free vessel search as a fallback for vessel details.
 */
export async function getVesselInfo(mmsi: string): Promise<Vessel | null> {
  const key = `vessel:${mmsi}`;
  const cached = getCached<Vessel>(key);
  if (cached) return cached;

  // Try AISHub single-vessel lookup.
  const url = `${AISHUB_BASE}?username=&format=1&output=json&mmsi=${mmsi}`;

  try {
    const data = await fetchJSON<any>(url);
    if (!data || (data.ERROR)) return null;

    const v = Array.isArray(data) ? data[0] : data;
    if (!v?.MMSI) return null;

    const shipType = v.SHIP_TYPE ?? 0;
    const vessel: Vessel = {
      mmsi: String(v.MMSI),
      name: v.NAME?.trim() ?? `MMSI ${mmsi}`,
      imo: v.IMO ? String(v.IMO) : undefined,
      callSign: v.CALLSIGN?.trim() ?? undefined,
      shipType,
      shipTypeLabel: decodeShipType(shipType),
      lengthM: v.LENGTH ?? null,
      beamM: v.WIDTH ?? null,
      draftM: v.DRAUGHT != null ? v.DRAUGHT / 10 : null,
      destination: v.DESTINATION?.trim() ?? undefined,
      eta: v.ETA ?? undefined,
      flag: v.FLAG ?? undefined,
      lastPosition: v.LATITUDE
        ? {
            mmsi: String(v.MMSI),
            lat: v.LATITUDE,
            lon: v.LONGITUDE,
            cogDeg: v.COG ?? 0,
            sogKnots: v.SOG ?? 0,
            headingDeg: v.HEADING != null && v.HEADING < 511 ? v.HEADING : null,
            navStatus: v.NAVSTAT ?? 15,
            timestamp: v.TIME ?? new Date().toISOString(),
          }
        : null,
    };

    setCache(key, vessel);
    return vessel;
  } catch (err) {
    console.warn('[vesselTracking] getVesselInfo failed:', err);
    return null;
  }
}

/**
 * Share the user's current location with contacts via the native
 * share sheet. Generates a Google Maps link for universal compatibility.
 *
 * @param lat — Current latitude.
 * @param lon — Current longitude.
 * @param name — Optional label for the shared location.
 */
export async function shareMyLocation(
  lat: number,
  lon: number,
  name?: string,
): Promise<boolean> {
  const label = name ?? 'My Fishing Spot';
  const mapsUrl = `https://maps.google.com/maps?q=${lat},${lon}`;
  const message = `${label}\n${lat.toFixed(6)}, ${lon.toFixed(6)}\n${mapsUrl}`;

  try {
    const result = await Share.share(
      Platform.OS === 'ios'
        ? { message, url: mapsUrl }
        : { message },
      { dialogTitle: 'Share Location' },
    );
    return result.action !== Share.dismissedAction;
  } catch (err) {
    console.warn('[vesselTracking] shareMyLocation failed:', err);
    return false;
  }
}

// ── GeoJSON helper ───────────────────────────────────────────────

/** Convert NearbyVessel array to GeoJSON FeatureCollection for map display. */
export function vesselsToGeoJSON(vessels: NearbyVessel[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: vessels
      .filter((v) => v.lastPosition)
      .map((v) => ({
        type: 'Feature' as const,
        geometry: {
          type: 'Point' as const,
          coordinates: [v.lastPosition!.lon, v.lastPosition!.lat],
        },
        properties: {
          mmsi: v.mmsi,
          name: v.name,
          type: v.shipTypeLabel,
          speed: v.lastPosition!.sogKnots,
          heading: v.lastPosition!.headingDeg ?? v.lastPosition!.cogDeg,
          status: decodeNavStatus(v.lastPosition!.navStatus),
          distanceNm: v.distanceNm,
        },
      })),
  };
}
