import type { FishingLocation, ForecastDay, CurrentConditions, ScoreBreakdown } from '../types/models';
import type { WaterbodyBounds } from './accessPointService';

export interface GpsLakeCatalogEntry {
  id: string;
  catalog: 'us' | 'ca';
  country: 'US' | 'CA' | string;
  jurisdiction: string;
  subregion: string;
  name: string;
  waterBodyType: 'lake' | 'river' | 'stream' | 'reservoir' | 'pond' | string;
  lat: number;
  lon: number;
  bbox: WaterbodyBounds;
  bboxDiagonalKm: number;
  listedScale: string;
  chartId: string;
  lakeUrl: string;
  sourceUrl: string;
  subregionUrl: string;
  nearbyCities: string[];
  areaAcres?: number | null;
  shorelineMiles?: number | null;
  inventorySource: string;
  licenseNote: string;
}

const RAW_CATALOG = require('../data/generated/gpsLakeCatalog.json') as GpsLakeCatalogEntry[];

const DEFAULT_SCORE = 0;
const DEFAULT_BREAKDOWN: ScoreBreakdown = {
  catchProbability: 50,
  cpue: 50,
  conditions: 50,
  trophyPotential: 50,
};

const DEFAULT_CONDITIONS: CurrentConditions = {
  waterTemp: 65,
  airTemp: 72,
  weather: 'Loading local conditions',
  weatherIcon: 'partly-cloudy',
  windSpeed: 8,
  windDirection: 'SW',
  pressure: 29.92,
  pressureTrend: 'steady',
  humidity: 55,
  moonPhase: 'Unknown',
  solunarRating: 'fair',
  sunrise: '6:30 AM',
  sunset: '7:45 PM',
};

const DEFAULT_FORECAST: ForecastDay[] = [];
const DEFAULT_NEARBY_POND_MIN_ACRES = 20;
const DEFAULT_NEARBY_POND_MIN_DIAGONAL_KM = 0.6;
const SKIP_DEFAULT_NEARBY_NAME_PATTERNS = [
  /\bstorm\b/i,
  /\bretention\b/i,
  /\bdetention\b/i,
  /\bwastewater\b/i,
  /\bsewage\b/i,
  /\btreatment\b/i,
];

const catalogById = new Map<string, GpsLakeCatalogEntry>(RAW_CATALOG.map((entry) => [entry.id, entry]));

function typeLabel(type: string): string {
  const pretty = type.replace(/_/g, ' ');
  return pretty.charAt(0).toUpperCase() + pretty.slice(1);
}

function haversineMiles(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const R = 3958.7613;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function toFishingLocation(entry: GpsLakeCatalogEntry): FishingLocation {
  const areaLabel =
    entry.areaAcres && entry.areaAcres > 0
      ? `${Math.round(entry.areaAcres).toLocaleString()} ac`
      : entry.listedScale || entry.subregion || '';
  const subregionPart = entry.subregion ? ` · ${entry.subregion}` : '';
  return {
    id: entry.id,
    name: entry.name,
    subtitle: `${typeLabel(entry.waterBodyType)} · ${entry.jurisdiction}${subregionPart}${areaLabel ? ` · ${areaLabel}` : ''}`,
    lat: entry.lat,
    lon: entry.lon,
    score: DEFAULT_SCORE,
    scoreBreakdown: DEFAULT_BREAKDOWN,
    conditions: DEFAULT_CONDITIONS,
    explanation: 'Cataloged lake from the GPS Nautical discovery index. Live conditions load when you focus the spot.',
    forecast: DEFAULT_FORECAST,
  };
}

function queryMatches(entry: GpsLakeCatalogEntry, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    entry.name.toLowerCase().includes(q) ||
    entry.jurisdiction.toLowerCase().includes(q) ||
    entry.subregion.toLowerCase().includes(q) ||
    entry.nearbyCities.some((city) => city.toLowerCase().includes(q))
  );
}

function shouldIncludeByDefaultNearby(entry: GpsLakeCatalogEntry): boolean {
  const name = entry.name.trim();
  if (!name) return false;
  if (SKIP_DEFAULT_NEARBY_NAME_PATTERNS.some((pattern) => pattern.test(name))) {
    return false;
  }
  if (entry.waterBodyType !== 'pond') {
    return true;
  }

  const areaAcres = entry.areaAcres ?? 0;
  const isLargeEnough =
    areaAcres >= DEFAULT_NEARBY_POND_MIN_ACRES ||
    entry.bboxDiagonalKm >= DEFAULT_NEARBY_POND_MIN_DIAGONAL_KM;
  const hasFishingStyleName = /\b(lake|reservoir|slough|millpond)\b/i.test(name);

  return isLargeEnough || hasFishingStyleName;
}

export function getGpsLakeCatalog(): GpsLakeCatalogEntry[] {
  return RAW_CATALOG;
}

export function isGpsCatalogLocation(id: string): boolean {
  return catalogById.has(id);
}

export function getGpsLakeCatalogEntryById(id: string): GpsLakeCatalogEntry | undefined {
  return catalogById.get(id);
}

export function getCatalogLakeBoundsForLocationId(id: string): WaterbodyBounds | undefined {
  return catalogById.get(id)?.bbox;
}

export function getNearbyCatalogLocations(
  lat: number,
  lon: number,
  maxDistanceMiles: number = 62,
  limit: number = 250,
): FishingLocation[] {
  return RAW_CATALOG
    .filter((entry) => shouldIncludeByDefaultNearby(entry))
    .map((entry) => ({
      entry,
      distanceMiles: haversineMiles(lat, lon, entry.lat, entry.lon),
    }))
    .filter(({ distanceMiles }) => distanceMiles <= maxDistanceMiles)
    .sort((a, b) => a.distanceMiles - b.distanceMiles)
    .slice(0, limit)
    .map(({ entry }) => toFishingLocation(entry));
}

export function searchCatalogLocations(
  query: string,
  lat?: number,
  lon?: number,
  limit: number = 250,
): FishingLocation[] {
  const filtered = RAW_CATALOG.filter((entry) => queryMatches(entry, query));
  const ranked = lat != null && lon != null
    ? filtered
        .map((entry) => ({
          entry,
          distanceMiles: haversineMiles(lat, lon, entry.lat, entry.lon),
        }))
        .sort((a, b) => a.distanceMiles - b.distanceMiles)
        .map(({ entry }) => entry)
    : filtered;
  return ranked.slice(0, limit).map(toFishingLocation);
}
