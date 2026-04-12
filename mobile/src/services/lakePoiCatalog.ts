import type { MarinaPOI } from './marinaDirectory';
import type { AccessPoint, AccessPointType } from './accessPointService';

export type HarvestedLakePoiType =
  | 'marine_fuel'
  | 'gas_station'
  | 'marina'
  | 'bait_shop'
  | 'boat_rental'
  | 'fishing_pier'
  | 'tackle_shop'
  | 'boat_ramp'
  | 'kayak_launch'
  | 'parking'
  | 'trailhead'
  | 'fish_cleaning'
  | 'picnic_site'
  | 'shore_fishing';

export interface HarvestedLakePoiEntry {
  id: string;
  name: string;
  lat: number;
  lon: number;
  type: HarvestedLakePoiType;
  fuelTypes?: string[];
  amenities: string[];
  phone: string;
  website: string;
  address: string;
  operator: string;
  source: string;
}

interface HarvestedLakePoiBucket {
  lakeName?: string;
  jurisdiction?: string;
  country?: string;
  pois: HarvestedLakePoiEntry[];
}

const RAW_POI_CATALOG = require('../data/generated/gpsLakePoiCatalog.json') as Record<string, HarvestedLakePoiBucket>;

function haversineMiles(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const R = 3958.8;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function getLocalLakePois(lakeId?: string | null): HarvestedLakePoiEntry[] {
  if (!lakeId) return [];
  return RAW_POI_CATALOG[lakeId]?.pois ?? [];
}

export function getLocalLakeMarineFuelPois(lakeId?: string | null): HarvestedLakePoiEntry[] {
  return getLocalLakePois(lakeId).filter((poi) =>
    poi.type === 'marine_fuel' ||
    poi.amenities.includes('fuel') ||
    poi.amenities.includes('diesel') ||
    poi.amenities.includes('gasoline') ||
    ((poi.fuelTypes?.length ?? 0) > 0),
  );
}

function toAccessType(type: HarvestedLakePoiType): AccessPointType | null {
  switch (type) {
    case 'boat_ramp':
      return 'boat_launch';
    case 'kayak_launch':
      return 'kayak_launch';
    case 'parking':
      return 'parking';
    case 'trailhead':
      return 'trailhead';
    case 'fish_cleaning':
      return 'fish_cleaning';
    case 'picnic_site':
      return 'picnic_site';
    case 'shore_fishing':
      return 'shore_fishing';
    case 'fishing_pier':
      return 'fishing_pier';
    default:
      return null;
  }
}

export function getLocalLakeAccessPoints(lakeId?: string | null): AccessPoint[] {
  const rows: AccessPoint[] = [];
  for (const poi of getLocalLakePois(lakeId)) {
    const type = toAccessType(poi.type);
    if (!type) continue;
    rows.push({
      id: poi.id,
      name: poi.name,
      lat: poi.lat,
      lon: poi.lon,
      type,
      operator: poi.operator || undefined,
      website: poi.website || undefined,
    });
  }
  return rows;
}

function toMarinaType(type: HarvestedLakePoiType): MarinaPOI['type'] | null {
  switch (type) {
    case 'marina':
    case 'bait_shop':
    case 'boat_rental':
    case 'fishing_pier':
    case 'tackle_shop':
      return type;
    case 'boat_ramp':
      return 'boat_ramp';
    default:
      return null;
  }
}

export function getLocalLakeMarinaPois(
  lakeId: string | null | undefined,
  originLat?: number,
  originLon?: number,
): MarinaPOI[] {
  const rows: MarinaPOI[] = [];
  for (const poi of getLocalLakePois(lakeId)) {
    const type = toMarinaType(poi.type);
    if (!type) continue;
    const distanceMiles =
      originLat != null && originLon != null
        ? haversineMiles(originLat, originLon, poi.lat, poi.lon)
        : undefined;
    rows.push({
      id: poi.id,
      name: poi.name,
      lat: poi.lat,
      lon: poi.lon,
      type,
      phone: poi.phone || undefined,
      website: poi.website || undefined,
      address: poi.address || undefined,
      amenities: poi.amenities.map((value) => value.replace(/_/g, ' ')),
      distanceMiles,
    });
  }

  if (originLat == null || originLon == null) {
    return rows;
  }

  return rows.sort((a, b) => (a.distanceMiles ?? 0) - (b.distanceMiles ?? 0));
}
