/**
 * Fishing spot discovery service for OpenCatch.
 *
 * Dynamically discovers fishing spots (lakes, rivers, ponds, reservoirs)
 * via the OpenStreetMap Overpass API based on the visible map bounding box.
 *
 * Features:
 * - Zoom-dependent filtering: country level shows only major lakes,
 *   state level shows medium water bodies, county level shows everything
 * - Grid-cell caching with 1-hour TTL to avoid redundant queries
 * - Request cancellation on map movement
 * - Returns objects compatible with the FishingLocation interface
 */

import type { FishingLocation } from '../types/models';

// ── Types ────────────────────────────────────────────────────────

export interface BoundingBox {
  south: number; // min lat
  west: number;  // min lon
  north: number; // max lat
  east: number;  // max lon
}

export type WaterBodySize = 'major' | 'medium' | 'small';

export interface DiscoveredSpot {
  id: string;
  name: string;
  lat: number;
  lon: number;
  waterBodyType: 'lake' | 'river' | 'pond' | 'reservoir' | 'stream' | 'wetland' | 'water';
  size: WaterBodySize;
  areaKm2?: number;
  /** Converted to a minimal FishingLocation for map rendering */
  asFishingLocation: FishingLocation;
}

// ── Constants ────────────────────────────────────────────────────

const OVERPASS_URL = 'https://overpass-api.de/api/interpreter';
const CACHE_TTL_MS = 4 * 60 * 60 * 1000; // 4 hours — aggressive caching since water bodies don't change
const MAX_BBOX_AREA_DEG2 = 100; // Don't query more than ~10x10 degree area at once
const GRID_CELL_SIZE_DEFAULT = 2; // degrees — divide world into 2x2 degree cells for caching
const GRID_CELL_SIZE_ZOOMED = 1; // degrees — smaller cells at high zoom to reduce boundary misses
const ZOOM_SMALL_GRID = 10; // zoom threshold for switching to smaller grid cells
const REQUEST_TIMEOUT_MS = 8_000;
const ADJACENT_PREFETCH_DELAY_MS = 3_000; // delay before prefetching adjacent cells

// Zoom thresholds for water body size filtering
const ZOOM_COUNTRY = 5;   // zoom <= 5: only major lakes (area > 10 km2)
const ZOOM_STATE = 8;     // zoom 5-8: medium lakes (area > 1 km2)
// zoom > 8: all water bodies

// ── Smart Filtering Constants ────────────────────────────────────

/** Minimum area in km² for UNNAMED water bodies (~0.5 acres = 2,000 m² = 0.002 km²) */
const MIN_AREA_UNNAMED_KM2 = 0.002;

/** Named water bodies: no minimum area (any named body is kept) */
const MIN_AREA_NAMED_KM2 = 0;

/** Borderline range upper bound: 2 hectares = 0.02 km² (~5 acres) — unnamed
 *  bodies below this with no fishing tags and no name are likely puddles/ditches */
const BORDERLINE_UPPER_KM2 = 0.02;

/** Minimum area in km² for wetlands (5 hectares = 0.05 km²) — only large marshes are fishable */
const MIN_AREA_WETLAND_KM2 = 0.05;

/** Name substrings indicating non-fishable infrastructure (case-insensitive) */
const SKIP_NAME_PATTERNS = [
  'storm', 'retention', 'treatment', 'sewage',
  'detention', 'drainage', 'outfall', 'settling',
  'ditch', 'culvert', 'runoff', 'wastewater',
];

/** OSM tag values indicating non-fishable water */
const SKIP_WATER_TYPES = new Set([
  'wastewater', 'sewage', 'basin',
  'ditch', 'drain', 'wastewater_plant',
]);
const SKIP_LANDUSE_TYPES = new Set(['basin']);
/** OSM waterway values indicating non-fishable drainage infrastructure */
const SKIP_WATERWAY_TYPES = new Set(['ditch', 'drain', 'gutter']);

/** Name substrings that indicate a real fishing-relevant water body */
const FISHING_NAME_KEYWORDS = [
  'lake', 'creek', 'river', 'pond', 'reservoir',
  'bay', 'cove', 'harbour', 'harbor',
];

/** OSM tags that indicate explicit fishing relevance */
function hasFishingTags(tags: Record<string, string>): boolean {
  if (tags.leisure === 'fishing') return true;
  if (tags.sport === 'fishing') return true;
  if (tags.fishing === 'yes') return true;
  return false;
}

/** Check if name contains any fishing-relevant keyword */
function hasFishingName(name: string): boolean {
  if (!name) return false;
  const lower = name.toLowerCase();
  return FISHING_NAME_KEYWORDS.some((kw) => lower.includes(kw));
}

/** Check if name matches infrastructure / non-fishable patterns */
function hasSkipName(name: string): boolean {
  if (!name) return false;
  const lower = name.toLowerCase();
  return SKIP_NAME_PATTERNS.some((pat) => lower.includes(pat));
}

/**
 * Smart filter: returns true if the water body should be KEPT.
 * Applies name-based, type-based, area-based, and fishing-relevance filters.
 */
function shouldKeepWaterBody(tags: Record<string, string>, areaKm2: number | undefined, name: string): boolean {
  // 1. Type filtering: always skip wastewater / sewage / basin / ditch / drain types
  const waterTag = (tags.water ?? '').toLowerCase();
  const landuseTag = (tags.landuse ?? '').toLowerCase();
  const naturalTag = (tags.natural ?? '').toLowerCase();
  const waterwayTag = (tags.waterway ?? '').toLowerCase();
  if (SKIP_WATER_TYPES.has(waterTag)) return false;
  if (SKIP_WATERWAY_TYPES.has(waterwayTag)) return false;
  // Don't skip landuse=reservoir — those are fishable water bodies
  if (SKIP_LANDUSE_TYPES.has(landuseTag) && landuseTag !== 'reservoir') return false;

  // 1b. Wetland filtering: only keep marshes >= 5 hectares (smaller wetlands are not fishable)
  if (naturalTag === 'wetland') {
    if (areaKm2 !== undefined && areaKm2 < MIN_AREA_WETLAND_KM2) return false;
    // Unnamed wetlands without area data — skip (can't verify size)
    if (areaKm2 === undefined && !name) return false;
  }

  // 2. Fishing relevance boost: always keep if explicit fishing tags
  if (hasFishingTags(tags)) return true;

  // 3. Fishing relevance boost: always keep if name suggests fishing water body
  if (hasFishingName(name)) return true;

  // 4. Named reservoirs are always kept (e.g. Glenmore Reservoir, Bearspaw Reservoir)
  if (waterTag === 'reservoir' && name) return true;
  if (landuseTag === 'reservoir' && name) return true;
  if (tags.reservoir && name) return true;

  // 5. Name-based filtering: skip infrastructure names even if named
  if (hasSkipName(name)) return false;

  // 6. Named water bodies of ANY size are always kept (e.g. "Johnson's Pond")
  //    — the name indicates it's a known, real spot regardless of area
  if (name) return true;

  // 7. Unnamed water bodies: must meet minimum area threshold (~0.5 acres / 2,000 m²)
  if (areaKm2 !== undefined && areaKm2 < MIN_AREA_UNNAMED_KM2) return false;

  // 8. Borderline check: unnamed bodies under ~5 acres with no fishing tags — likely puddles/ditches
  if (areaKm2 !== undefined && areaKm2 < BORDERLINE_UPPER_KM2) {
    // Unnamed small water body with no fishing tags — likely a pond/ditch, skip
    return false;
  }

  // 9. Unnamed with no area data — skip (can't verify it's not a puddle)
  if (areaKm2 === undefined) return false;

  return true;
}

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry {
  spots: DiscoveredSpot[];
  timestamp: number;
  zoomTier: 'major' | 'medium' | 'all';
}

const MAX_CACHE_SIZE = 50;
const gridCache = new Map<string, CacheEntry>();

/** Set of grid cell keys currently being fetched — prevents duplicate in-flight requests */
const inFlightCells = new Set<string>();

/**
 * LRU eviction: when cache exceeds MAX_CACHE_SIZE, remove oldest entries.
 * Map insertion order is used as a proxy for recency.
 */
function evictIfNeeded(): void {
  if (gridCache.size <= MAX_CACHE_SIZE) return;
  const toRemove = gridCache.size - MAX_CACHE_SIZE;
  let removed = 0;
  for (const key of gridCache.keys()) {
    if (removed >= toRemove) break;
    gridCache.delete(key);
    removed++;
  }
}

/** Touch a cache entry to move it to the end (most-recently-used). */
function touchCache(key: string): void {
  const entry = gridCache.get(key);
  if (entry) {
    gridCache.delete(key);
    gridCache.set(key, entry);
  }
}

function gridKey(cellLat: number, cellLon: number, tier: string): string {
  return `${cellLat},${cellLon},${tier}`;
}

function gridCellSize(zoom: number): number {
  return zoom >= ZOOM_SMALL_GRID ? GRID_CELL_SIZE_ZOOMED : GRID_CELL_SIZE_DEFAULT;
}

function getGridCell(lat: number, lon: number): [number, number] {
  const cs = GRID_CELL_SIZE_DEFAULT;
  return [
    Math.floor(lat / cs) * cs,
    Math.floor(lon / cs) * cs,
  ];
}

function getCached(key: string): DiscoveredSpot[] | null {
  const entry = gridCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    gridCache.delete(key);
    return null;
  }
  touchCache(key);
  return entry.spots;
}

// ── Zoom tier logic ──────────────────────────────────────────────

function zoomTier(zoom: number): 'major' | 'medium' | 'all' {
  if (zoom <= ZOOM_COUNTRY) return 'major';
  if (zoom <= ZOOM_STATE) return 'medium';
  return 'all';
}

function minAreaForTier(tier: 'major' | 'medium' | 'all'): number {
  switch (tier) {
    case 'major': return 10;    // km2
    case 'medium': return 0.5;  // km2
    case 'all': return 0;
  }
}

// ── Overpass query builders ──────────────────────────────────────

function buildOverpassQuery(bbox: BoundingBox, tier: 'major' | 'medium' | 'all'): string {
  const { south, west, north, east } = bbox;
  const bb = `${south},${west},${north},${east}`;

  if (tier === 'major') {
    // At country zoom, only query named lakes/reservoirs with way geometry
    // (these tend to be larger, significant water bodies)
    return `
[out:json][timeout:8][bbox:${bb}];
(
  relation["natural"="water"]["water"~"lake|reservoir"]["name"];
  way["natural"="water"]["water"~"lake|reservoir"]["name"];
  relation["natural"="water"]["name"];
  way["natural"="water"]["name"];
  relation["water"="lake"]["name"];
  relation["water"="reservoir"]["name"];
  relation["landuse"="reservoir"]["name"];
  way["landuse"="reservoir"]["name"];
);
out center tags 300;
`;
  }

  if (tier === 'medium') {
    return `
[out:json][timeout:8][bbox:${bb}];
(
  relation["natural"="water"]["name"];
  way["natural"="water"]["name"];
  way["water"~"lake|river|pond|reservoir|oxbow|canal"]["name"];
  relation["water"~"lake|river|pond|reservoir|oxbow|canal"]["name"];
  relation["landuse"="reservoir"]["name"];
  way["landuse"="reservoir"]["name"];
  way["waterway"="riverbank"]["name"];
  relation["waterway"="riverbank"]["name"];
  node["leisure"="fishing"]["name"];
  way["leisure"="fishing"]["name"];
);
out center tags 500;
`;
  }

  // All — county level, include unnamed ponds etc.
  // At high zoom (>= 10), we also query broader tag sets:
  // - landuse=reservoir catches water bodies tagged differently from natural=water
  // - waterway=riverbank catches large river area polygons
  // - natural=wetland catches fishable marshes (filtered by area later)
  // - multipolygon relations are caught by the relation queries
  // - Excludes ditch/drain/basin/wastewater at query level to reduce response size
  return `
[out:json][timeout:8][bbox:${bb}];
(
  way["natural"="water"]["water"!~"ditch|drain|basin|wastewater"];
  relation["natural"="water"]["water"!~"ditch|drain|basin|wastewater"];
  way["water"~"lake|river|pond|reservoir|stream|oxbow|canal"]["water"!~"ditch|drain|basin|wastewater"];
  relation["water"~"lake|river|pond|reservoir|stream|oxbow|canal"]["water"!~"ditch|drain|basin|wastewater"];
  way["landuse"="reservoir"];
  relation["landuse"="reservoir"];
  way["waterway"="riverbank"];
  relation["waterway"="riverbank"];
  way["natural"="wetland"]["wetland"="marsh"];
  relation["natural"="wetland"]["wetland"="marsh"];
  node["leisure"="fishing"];
  node["sport"="fishing"];
  way["leisure"="fishing"];
  relation["leisure"="fishing"];
);
out center tags 800;
`;
}

// ── Water body type classification ───────────────────────────────

function classifyWaterBody(tags: Record<string, string>): DiscoveredSpot['waterBodyType'] {
  const water = tags.water ?? '';
  const natural = tags.natural ?? '';
  const landuse = tags.landuse ?? '';
  const leisure = tags.leisure ?? '';
  const waterway = tags.waterway ?? '';

  if (water === 'reservoir' || tags.reservoir || landuse === 'reservoir') return 'reservoir';
  if (water === 'river' || waterway === 'river' || waterway === 'riverbank') return 'river';
  if (water === 'stream' || waterway === 'stream') return 'stream';
  if (water === 'pond') return 'pond';
  if (water === 'lake' || natural === 'water') return 'lake';
  if (water === 'wetland' || natural === 'wetland') return 'wetland';
  if (leisure === 'fishing') return 'lake'; // fishing spots default to lake
  return 'water';
}

function estimateAreaKm2(tags: Record<string, string>): number | undefined {
  // OSM sometimes has area tags in various formats
  const wayArea = tags['way_area'];
  if (wayArea) {
    const m2 = parseFloat(wayArea);
    if (!isNaN(m2)) return m2 / 1_000_000;
  }
  return undefined;
}

function classifySize(areaKm2: number | undefined, type: string): WaterBodySize {
  if (areaKm2 !== undefined) {
    if (areaKm2 >= 10) return 'major';
    if (areaKm2 >= 1) return 'medium';
    return 'small';
  }
  // Heuristic: relations (multi-polygon) tend to be larger water bodies
  // Rivers and reservoirs tend to be bigger
  if (type === 'reservoir') return 'medium';
  if (type === 'river') return 'medium';
  if (type === 'pond' || type === 'stream') return 'small';
  return 'small';
}

// ── Score heuristic based on water body attributes ───────────────

function heuristicScore(size: WaterBodySize, type: string): number {
  let base = 50;
  if (size === 'major') base = 65;
  else if (size === 'medium') base = 55;

  if (type === 'reservoir') base += 5;
  if (type === 'river') base += 3;

  // Add some variance so pins look natural
  return Math.min(85, Math.max(30, base + Math.floor(Math.random() * 10 - 5)));
}

// ── Convert OSM element to DiscoveredSpot ────────────────────────

function osmToSpot(element: any): DiscoveredSpot | null {
  const tags = element.tags ?? {};
  const lat = element.lat ?? element.center?.lat;
  const lon = element.lon ?? element.center?.lon;

  if (lat == null || lon == null) return null;
  if (lat === 0 && lon === 0) return null;

  const name = tags.name ?? tags['name:en'] ?? '';
  const waterBodyType = classifyWaterBody(tags);
  const areaKm2 = estimateAreaKm2(tags);

  // Smart filtering: skip irrelevant water bodies (storm ponds, treatment facilities, etc.)
  if (!shouldKeepWaterBody(tags, areaKm2, name)) return null;

  const size = classifySize(areaKm2, waterBodyType);

  const displayName = name || 'Unseen Site';
  const score = heuristicScore(size, waterBodyType);

  const id = `osm-${element.type?.[0] ?? 'n'}${element.id}`;

  const typeLabel = waterBodyType.charAt(0).toUpperCase() + waterBodyType.slice(1);
  const areaLabel = areaKm2 != null ? ` · ${areaKm2 < 1 ? `${Math.round(areaKm2 * 1000)} acres` : `${areaKm2.toFixed(1)} km²`}` : '';

  const asFishingLocation: FishingLocation = {
    id,
    name: displayName,
    subtitle: `${typeLabel}${areaLabel}`,
    lat,
    lon,
    score,
    scoreBreakdown: {
      catchProbability: 50,
      cpue: 50,
      conditions: 50,
      trophyPotential: 40,
    },
    conditions: {
      waterTemp: 65,
      airTemp: 72,
      weather: 'Unknown',
      weatherIcon: 'partly-cloudy',
      windSpeed: 0,
      windDirection: '',
      pressure: 29.92,
      pressureTrend: 'steady',
      humidity: 50,
      moonPhase: '',
      solunarRating: 'fair',
      sunrise: '',
      sunset: '',
    },
    explanation: name
      ? `${name} — ${typeLabel} discovered via OpenStreetMap.`
      : `${typeLabel} water body discovered via OpenStreetMap.`,
    forecast: [],
  };

  return { id, name: displayName, lat, lon, waterBodyType, size, areaKm2, asFishingLocation };
}

// ── In-flight request tracking for cancellation ──────────────────

let activeController: AbortController | null = null;

export function cancelPendingDiscovery(): void {
  if (activeController) {
    activeController.abort();
    activeController = null;
  }
}

// ── Main discovery function ──────────────────────────────────────

export async function discoverFishingSpots(
  bbox: BoundingBox,
  zoom: number,
): Promise<DiscoveredSpot[]> {
  // Validate bbox
  const bboxArea = Math.abs(bbox.north - bbox.south) * Math.abs(bbox.east - bbox.west);
  if (bboxArea > MAX_BBOX_AREA_DEG2) {
    // Too large — return empty. The map is zoomed out too far.
    // At country level we still query, but limit the bbox
    if (zoom > ZOOM_COUNTRY) return [];
  }

  const tier = zoomTier(zoom);
  const minArea = minAreaForTier(tier);
  const cs = gridCellSize(zoom);

  // Determine which grid cells overlap the bbox
  const cellsToFetch: Array<{ cellLat: number; cellLon: number; key: string }> = [];
  const allSpots: DiscoveredSpot[] = [];

  const startLat = Math.floor(bbox.south / cs) * cs;
  const startLon = Math.floor(bbox.west / cs) * cs;
  const endLat = Math.ceil(bbox.north / cs) * cs;
  const endLon = Math.ceil(bbox.east / cs) * cs;

  // Limit grid cells to prevent excessive queries
  const maxCells = tier === 'major' ? 12 : tier === 'medium' ? 6 : 6;
  let cellCount = 0;

  for (let cLat = startLat; cLat < endLat; cLat += cs) {
    for (let cLon = startLon; cLon < endLon; cLon += cs) {
      if (cellCount >= maxCells) break;
      cellCount++;

      const key = gridKey(cLat, cLon, tier);
      const cached = getCached(key);
      if (cached) {
        allSpots.push(...cached);
      } else {
        cellsToFetch.push({ cellLat: cLat, cellLon: cLon, key });
      }
    }
  }

  // Filter out cells that are already being fetched (request deduplication)
  const deduplicatedCells = cellsToFetch.filter((c) => !inFlightCells.has(c.key));

  // Fetch uncached cells
  if (deduplicatedCells.length > 0) {
    cancelPendingDiscovery();
    activeController = new AbortController();
    const signal = activeController.signal;

    // Mark cells as in-flight
    for (const cell of deduplicatedCells) {
      inFlightCells.add(cell.key);
    }

    // Fetch cells sequentially to avoid overwhelming Overpass
    for (const cell of deduplicatedCells) {
      if (signal.aborted) break;

      const cellBbox: BoundingBox = {
        south: cell.cellLat,
        west: cell.cellLon,
        north: cell.cellLat + cs,
        east: cell.cellLon + cs,
      };

      try {
        const spots = await fetchOverpassSpots(cellBbox, tier, signal);
        gridCache.set(cell.key, {
          spots,
          timestamp: Date.now(),
          zoomTier: tier,
        });
        evictIfNeeded();
        allSpots.push(...spots);
      } catch (err: any) {
        if (err.name === 'AbortError') break;
        console.warn('[FishingSpotDiscovery] Overpass query failed for cell:', cell.key, err);
        // Cache empty result to avoid hammering on failure
        gridCache.set(cell.key, { spots: [], timestamp: Date.now(), zoomTier: tier });
        evictIfNeeded();
      } finally {
        inFlightCells.delete(cell.key);
      }
    }

    // Clean up any remaining in-flight markers (e.g. on abort)
    for (const cell of deduplicatedCells) {
      inFlightCells.delete(cell.key);
    }

    activeController = null;
  }

  // Filter by minimum area for the current zoom tier
  // At high zoom (> 10), show ALL named water bodies regardless of size
  const highZoom = zoom > 10;
  const filtered = minArea > 0
    ? allSpots.filter((s) => {
        // Named water bodies always pass at high zoom
        if (highZoom && s.name) return true;
        if (s.areaKm2 !== undefined) return s.areaKm2 >= minArea;
        // If no area data, use size classification
        if (tier === 'major') return s.size === 'major';
        if (tier === 'medium') return s.size !== 'small';
        return true;
      })
    : allSpots;

  // Filter to only spots within the actual visible bbox
  const inBounds = filtered.filter(
    (s) =>
      s.lat >= bbox.south &&
      s.lat <= bbox.north &&
      s.lon >= bbox.west &&
      s.lon <= bbox.east,
  );

  // Deduplicate by id
  const seen = new Set<string>();
  const unique: DiscoveredSpot[] = [];
  for (const s of inBounds) {
    if (!seen.has(s.id)) {
      seen.add(s.id);
      unique.push(s);
    }
  }

  return unique;
}

// ── Instant cached-data accessor (no network) ────────────────────

/**
 * Immediately returns whatever is already cached for the given bbox/zoom
 * without making any network requests. Returns null if nothing is cached.
 * This lets the UI show stale data instantly while a fresh fetch happens.
 */
export function getCachedSpotsForBBox(
  bbox: BoundingBox,
  zoom: number,
): DiscoveredSpot[] | null {
  const tier = zoomTier(zoom);
  const minArea = minAreaForTier(tier);
  const cs = gridCellSize(zoom);
  const highZoom = zoom > 10;

  const startLat = Math.floor(bbox.south / cs) * cs;
  const startLon = Math.floor(bbox.west / cs) * cs;
  const endLat = Math.ceil(bbox.north / cs) * cs;
  const endLon = Math.ceil(bbox.east / cs) * cs;

  const allSpots: DiscoveredSpot[] = [];
  let anyHit = false;

  for (let cLat = startLat; cLat < endLat; cLat += cs) {
    for (let cLon = startLon; cLon < endLon; cLon += cs) {
      const key = gridKey(cLat, cLon, tier);
      const entry = gridCache.get(key);
      // Accept even expired entries for instant display
      if (entry) {
        anyHit = true;
        allSpots.push(...entry.spots);
      }
    }
  }

  if (!anyHit) return null;

  const filtered = minArea > 0
    ? allSpots.filter((s) => {
        if (highZoom && s.name) return true;
        if (s.areaKm2 !== undefined) return s.areaKm2 >= minArea;
        if (tier === 'major') return s.size === 'major';
        if (tier === 'medium') return s.size !== 'small';
        return true;
      })
    : allSpots;

  const inBounds = filtered.filter(
    (s) =>
      s.lat >= bbox.south &&
      s.lat <= bbox.north &&
      s.lon >= bbox.west &&
      s.lon <= bbox.east,
  );

  const seen = new Set<string>();
  const unique: DiscoveredSpot[] = [];
  for (const s of inBounds) {
    if (!seen.has(s.id)) {
      seen.add(s.id);
      unique.push(s);
    }
  }

  return unique.length > 0 ? unique : null;
}

// ── Adjacent cell prefetch (runs in background after main fetch) ──

let prefetchTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * Pre-fetches grid cells adjacent to the given bbox so that the next
 * pan in any direction hits cache instead of Overpass.
 */
export function prefetchAdjacentCells(bbox: BoundingBox, zoom: number): void {
  if (prefetchTimer) clearTimeout(prefetchTimer);

  prefetchTimer = setTimeout(async () => {
    const tier = zoomTier(zoom);
    const cs = gridCellSize(zoom);
    const padLat = cs;
    const padLon = cs;

    const expandedBbox: BoundingBox = {
      south: bbox.south - padLat,
      west: bbox.west - padLon,
      north: bbox.north + padLat,
      east: bbox.east + padLon,
    };

    const startLat = Math.floor(expandedBbox.south / cs) * cs;
    const startLon = Math.floor(expandedBbox.west / cs) * cs;
    const endLat = Math.ceil(expandedBbox.north / cs) * cs;
    const endLon = Math.ceil(expandedBbox.east / cs) * cs;

    const cellsToFetch: Array<{ cellLat: number; cellLon: number; key: string }> = [];

    for (let cLat = startLat; cLat < endLat; cLat += cs) {
      for (let cLon = startLon; cLon < endLon; cLon += cs) {
        const key = gridKey(cLat, cLon, tier);
        if (!gridCache.has(key)) {
          cellsToFetch.push({ cellLat: cLat, cellLon: cLon, key });
        }
      }
    }

    // Only prefetch a few cells to avoid overloading Overpass
    const maxPrefetch = 3;
    const controller = new AbortController();

    for (let i = 0; i < Math.min(cellsToFetch.length, maxPrefetch); i++) {
      const cell = cellsToFetch[i];
      // Skip if already in-flight
      if (inFlightCells.has(cell.key)) continue;
      const cellBbox: BoundingBox = {
        south: cell.cellLat,
        west: cell.cellLon,
        north: cell.cellLat + cs,
        east: cell.cellLon + cs,
      };
      try {
        inFlightCells.add(cell.key);
        const spots = await fetchOverpassSpots(cellBbox, tier, controller.signal);
        gridCache.set(cell.key, { spots, timestamp: Date.now(), zoomTier: tier });
        evictIfNeeded();
      } catch {
        // Prefetch failure is non-critical — silently ignore
        break;
      } finally {
        inFlightCells.delete(cell.key);
      }
    }
  }, ADJACENT_PREFETCH_DELAY_MS);
}

// ── Overpass fetch for a single grid cell ─────────────────────────

async function fetchOverpassSpots(
  bbox: BoundingBox,
  tier: 'major' | 'medium' | 'all',
  signal: AbortSignal,
): Promise<DiscoveredSpot[]> {
  const query = buildOverpassQuery(bbox, tier);

  // Create a combined signal that aborts on either caller abort or timeout
  const timeoutController = new AbortController();
  const timeout = setTimeout(() => timeoutController.abort(), REQUEST_TIMEOUT_MS);

  // If caller signal already aborted, abort immediately
  if (signal.aborted) {
    clearTimeout(timeout);
    throw new DOMException('Aborted', 'AbortError');
  }
  const onCallerAbort = () => timeoutController.abort();
  signal.addEventListener('abort', onCallerAbort);

  let res: Response;
  try {
    res = await fetch(OVERPASS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `data=${encodeURIComponent(query)}`,
      signal: timeoutController.signal,
    });
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener('abort', onCallerAbort);
  }

  if (!res.ok) {
    throw new Error(`Overpass ${res.status}: ${res.statusText}`);
  }

  const data = await res.json();
  const elements: any[] = data.elements ?? [];

  const spots: DiscoveredSpot[] = [];
  for (const el of elements) {
    const spot = osmToSpot(el);
    if (spot) spots.push(spot);
  }

  return spots;
}

// ── Utility: convert discovered spots to FishingLocation array ───

export function discoveredToLocations(spots: DiscoveredSpot[]): FishingLocation[] {
  return spots.map((s) => s.asFishingLocation);
}

// ── Utility: get pin size multiplier based on water body size ────

export function pinSizeForSpot(spot: DiscoveredSpot): number {
  switch (spot.size) {
    case 'major': return 1.4;
    case 'medium': return 1.0;
    case 'small': return 0.7;
  }
}

// ── Clear cache (for testing or memory pressure) ─────────────────

export function clearDiscoveryCache(): void {
  gridCache.clear();
}

// ── Get discovery stats ──────────────────────────────────────────

export function getDiscoveryStats(): { cachedCells: number; totalCachedSpots: number } {
  let totalSpots = 0;
  for (const entry of gridCache.values()) {
    totalSpots += entry.spots.length;
  }
  return { cachedCells: gridCache.size, totalCachedSpots: totalSpots };
}
