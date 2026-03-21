/**
 * Precipitation Radar Service for OpenCatch.
 *
 * Uses the RainViewer API (free, global) to fetch real-time precipitation
 * radar tiles. Tiles are standard z/x/y raster tiles compatible with MapLibre
 * RasterSource. The API provides historical frames for animation and
 * forecast frames for short-term precipitation prediction.
 *
 * RainViewer API docs: https://www.rainviewer.com/api.html
 */

// ── Types ────────────────────────────────────────────────────────

/** A single radar frame (snapshot in time). */
export interface RadarFrame {
  /** Unix timestamp (seconds) when the radar was captured. */
  time: number;
  /** ISO 8601 formatted timestamp. */
  timeISO: string;
  /** Tile URL path — append to host + add /{z}/{x}/{y}/2/1_1.png */
  path: string;
}

/** A radar timestamp with human-readable label. */
export interface RadarTimestamp {
  /** Unix timestamp (seconds). */
  time: number;
  /** Human-readable label, e.g. "12:30 PM" or "5 min ago". */
  label: string;
  /** Whether this frame is a forecast (vs. observed). */
  isForecast: boolean;
}

/** Full radar data from RainViewer. */
export interface RadarData {
  /** Base host for tile URLs. */
  host: string;
  /** Past radar frames (observed). */
  past: RadarFrame[];
  /** Future radar frames (nowcast/forecast). */
  nowcast: RadarFrame[];
  /** Generated unix timestamp. */
  generated: number;
}

/** Color legend entry for radar intensity. */
export interface RadarLegendEntry {
  label: string;
  color: string;
  /** dBZ range min. */
  dbzMin: number;
  /** dBZ range max (inclusive). */
  dbzMax: number;
}

// ── Constants ────────────────────────────────────────────────────

const RAINVIEWER_API = 'https://api.rainviewer.com/public/weather-maps.json';

/** Cache TTL: 5 minutes — radar updates every 10 min. */
const CACHE_TTL_MS = 5 * 60 * 1000;

const REQUEST_TIMEOUT_MS = 10_000;

/** Default tile size: 256 for standard, 512 for retina. */
const TILE_SIZE = 256;

/**
 * Tile URL options:
 * - Color scheme: 1 = original, 2 = universal blue, 4 = titan, 6 = NEXRAD
 * - Smooth: 1 = smooth, 0 = raw
 * - Snow: 1 = show snow
 * Full: /{path}/{size}/{z}/{x}/{y}/{color}/{smooth}_{snow}.png
 */
const TILE_COLOR_SCHEME = 6; // NEXRAD-style colors
const TILE_SMOOTH = 1;
const TILE_SNOW = 1;

// ── Radar color legend ───────────────────────────────────────────

export const RADAR_LEGEND: RadarLegendEntry[] = [
  { label: 'Light Rain', color: '#00C853', dbzMin: 5, dbzMax: 20 },
  { label: 'Moderate', color: '#FFD600', dbzMin: 20, dbzMax: 35 },
  { label: 'Heavy Rain', color: '#FF6D00', dbzMin: 35, dbzMax: 50 },
  { label: 'Intense', color: '#D50000', dbzMin: 50, dbzMax: 60 },
  { label: 'Extreme / Hail', color: '#AA00FF', dbzMin: 60, dbzMax: 75 },
];

// ── Cache ────────────────────────────────────────────────────────

let cachedData: RadarData | null = null;
let cachedAt = 0;

// ── HTTP helper ──────────────────────────────────────────────────

async function fetchJSON<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) {
      throw new Error(`RainViewer API ${response.status}: ${url}`);
    }
    return response.json() as Promise<T>;
  } catch (err: any) {
    if (err.name === 'AbortError') {
      throw new Error(`RainViewer API timed out after ${REQUEST_TIMEOUT_MS}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

// ── RainViewer response shape ────────────────────────────────────

interface RainViewerResponse {
  version: string;
  generated: number;
  host: string;
  radar: {
    past: Array<{ time: number; path: string }>;
    nowcast: Array<{ time: number; path: string }>;
  };
  satellite?: {
    infrared: Array<{ time: number; path: string }>;
  };
}

// ── Internal helpers ─────────────────────────────────────────────

function parseFrame(raw: { time: number; path: string }): RadarFrame {
  return {
    time: raw.time,
    timeISO: new Date(raw.time * 1000).toISOString(),
    path: raw.path,
  };
}

function formatTimestamp(unixSeconds: number, isForecast: boolean): RadarTimestamp {
  const date = new Date(unixSeconds * 1000);
  const now = Date.now();
  const diffMinutes = Math.round((now - unixSeconds * 1000) / 60_000);

  let label: string;
  if (isForecast) {
    const fwdMin = Math.round((unixSeconds * 1000 - now) / 60_000);
    label = fwdMin <= 0 ? 'Now' : `+${fwdMin} min`;
  } else if (diffMinutes < 1) {
    label = 'Now';
  } else if (diffMinutes < 60) {
    label = `${diffMinutes} min ago`;
  } else {
    label = date.toLocaleTimeString('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    });
  }

  return { time: unixSeconds, label, isForecast };
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Fetch the current radar data from RainViewer.
 * Results are cached for 5 minutes.
 */
export async function fetchRadarData(): Promise<RadarData> {
  if (cachedData && Date.now() - cachedAt < CACHE_TTL_MS) {
    return cachedData;
  }

  try {
    const raw = await fetchJSON<RainViewerResponse>(RAINVIEWER_API);

    const data: RadarData = {
      host: raw.host,
      past: (raw.radar?.past ?? []).map(parseFrame),
      nowcast: (raw.radar?.nowcast ?? []).map(parseFrame),
      generated: raw.generated,
    };

    cachedData = data;
    cachedAt = Date.now();
    return data;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch radar data:', err);
    // Return stale cache if available
    if (cachedData) return cachedData;
    throw err;
  }
}

/**
 * Build a tile URL for a specific radar frame.
 *
 * Returns a URL template with {z}/{x}/{y} placeholders suitable
 * for MapLibre RasterSource tileUrlTemplates.
 *
 * @param host - The base host from RadarData.
 * @param framePath - The path from a RadarFrame.
 * @returns Tile URL template string.
 */
export function buildRadarTileUrl(host: string, framePath: string): string {
  return `${host}${framePath}/${TILE_SIZE}/{z}/{x}/{y}/${TILE_COLOR_SCHEME}/${TILE_SMOOTH}_${TILE_SNOW}.png`;
}

/**
 * Get tile URLs for the last N radar frames (for animation).
 *
 * @param count - Number of past frames to return (default: 6).
 * @returns Array of tile URL templates, oldest first.
 */
export async function getRadarFrames(count: number = 6): Promise<{
  frames: Array<{ tileUrl: string; timestamp: RadarTimestamp }>;
  nowcast: Array<{ tileUrl: string; timestamp: RadarTimestamp }>;
}> {
  const data = await fetchRadarData();
  const pastFrames = data.past.slice(-count);

  return {
    frames: pastFrames.map((frame) => ({
      tileUrl: buildRadarTileUrl(data.host, frame.path),
      timestamp: formatTimestamp(frame.time, false),
    })),
    nowcast: data.nowcast.map((frame) => ({
      tileUrl: buildRadarTileUrl(data.host, frame.path),
      timestamp: formatTimestamp(frame.time, true),
    })),
  };
}

/**
 * Get the most recent radar tile URL.
 *
 * @returns The latest available radar tile URL template, or null on failure.
 */
export async function getLatestRadarTile(): Promise<string | null> {
  try {
    const data = await fetchRadarData();
    const latest = data.past[data.past.length - 1];
    if (!latest) return null;
    return buildRadarTileUrl(data.host, latest.path);
  } catch {
    return null;
  }
}

/**
 * Get all radar timestamps (past + forecast) with labels.
 */
export async function getRadarTimestamps(): Promise<RadarTimestamp[]> {
  const data = await fetchRadarData();
  const pastTs = data.past.map((f) => formatTimestamp(f.time, false));
  const nowcastTs = data.nowcast.map((f) => formatTimestamp(f.time, true));
  return [...pastTs, ...nowcastTs];
}

/** Clear the radar data cache. */
export function clearRadarCache(): void {
  cachedData = null;
  cachedAt = 0;
}
