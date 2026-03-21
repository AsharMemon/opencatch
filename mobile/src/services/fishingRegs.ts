/**
 * OpenCatch — Fishing Regulations Service
 *
 * Provides state/province-specific fishing regulation information.
 * Data is embedded for offline access; can be supplemented with API calls.
 *
 * Covers: license requirements, season dates, bag limits, size limits,
 * special regulations, and links to official regulation documents.
 */

// ── Types ────────────────────────────────────────────────────────────────────

export interface FishingRegulation {
  state: string;
  stateCode: string;
  country: 'US' | 'CA';
  licenseUrl: string;
  regulationsUrl: string;
  licenseCost: {
    resident: number;
    nonResident: number;
    seniorDiscount?: boolean;
    youthFree?: boolean;
    youthMaxAge?: number;
  };
  freeFishingDays?: string[];  // Dates when no license needed
  generalSeason: {
    bass?: SeasonInfo;
    walleye?: SeasonInfo;
    trout?: SeasonInfo;
    catfish?: SeasonInfo;
    crappie?: SeasonInfo;
    pike?: SeasonInfo;
    muskie?: SeasonInfo;
  };
  specialNotes?: string[];
}

export interface SeasonInfo {
  openDate: string;     // "May 1" or "Year-round"
  closeDate: string;    // "March 15" or "Year-round"
  dailyBag: number;
  possessionLimit: number;
  minSizeInches?: number;
  maxSizeInches?: number;
  slotLimit?: { min: number; max: number };  // Must release fish in this range
  notes?: string;
}

// ── State Regulations Database ───────────────────────────────────────────────

const REGULATIONS: FishingRegulation[] = [
  {
    state: 'Texas',
    stateCode: 'TX',
    country: 'US',
    licenseUrl: 'https://tpwd.texas.gov/business/licenses/recreational/fishing/',
    regulationsUrl: 'https://tpwd.texas.gov/regulations/outdoor-annual/',
    licenseCost: { resident: 30, nonResident: 58, youthFree: true, youthMaxAge: 16 },
    freeFishingDays: ['June first Saturday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 14, notes: 'Largemouth 14" minimum. Only 1 over 24".' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 25, possessionLimit: 50, notes: 'Channel/blue combined' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 25, possessionLimit: 50, minSizeInches: 10 },
    },
    specialNotes: ['Life jacket required for children under 13 on boats', 'Noodling (hand fishing) legal for catfish'],
  },
  {
    state: 'Minnesota',
    stateCode: 'MN',
    country: 'US',
    licenseUrl: 'https://www.dnr.state.mn.us/licenses/fishing/index.html',
    regulationsUrl: 'https://www.dnr.state.mn.us/regulations/fishing/index.html',
    licenseCost: { resident: 25, nonResident: 51, seniorDiscount: true, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['Martin Luther King Jr weekend', 'Presidents Day weekend'],
    generalSeason: {
      walleye: { openDate: 'May 10', closeDate: 'Feb 23', dailyBag: 6, possessionLimit: 6, minSizeInches: 15, notes: 'Only 1 over 20" on most waters' },
      bass: { openDate: 'May 10', closeDate: 'Feb 23', dailyBag: 6, possessionLimit: 6, notes: 'Catch-and-release only May 10 – June 7' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 10, possessionLimit: 10 },
      pike: { openDate: 'May 10', closeDate: 'Feb 23', dailyBag: 3, possessionLimit: 3, minSizeInches: 24, notes: 'Only 1 over 36"' },
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, notes: 'Streams: Apr 15 – Oct 15 in many areas' },
    },
    specialNotes: ['Ice fishing shelters must have name/address displayed', 'Invasive species laws — clean/drain/dry boats'],
  },
  {
    state: 'Florida',
    stateCode: 'FL',
    country: 'US',
    licenseUrl: 'https://myfwc.com/license/',
    regulationsUrl: 'https://myfwc.com/fishing/freshwater/regulations/',
    licenseCost: { resident: 17, nonResident: 47, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['April first Saturday', 'June first Saturday+Sunday', 'September first Saturday', 'November last Saturday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 12, notes: 'Only 1 over 16". Trophy lakes may have stricter rules.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 25, possessionLimit: 50 },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No bag limit on channel catfish' },
    },
    specialNotes: ['Saltwater fishing requires separate license', 'Peacock bass — no closed season, no size/bag limit in designated areas'],
  },
  {
    state: 'California',
    stateCode: 'CA',
    country: 'US',
    licenseUrl: 'https://wildlife.ca.gov/licensing/fishing',
    regulationsUrl: 'https://wildlife.ca.gov/regulations',
    licenseCost: { resident: 54, nonResident: 142, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['July first Saturday', 'September first Saturday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 12, notes: 'Largemouth 12", spotted bass 12"' },
      trout: { openDate: 'Apr last Saturday', closeDate: 'Nov 15', dailyBag: 5, possessionLimit: 10, notes: 'Wild trout waters: 0-2 fish, barbless hooks only' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on catfish in most waters' },
    },
    specialNotes: ['Barbless hooks required on many trout streams', 'Report Card required for steelhead and salmon'],
  },
  {
    state: 'Michigan',
    stateCode: 'MI',
    country: 'US',
    licenseUrl: 'https://www.michigan.gov/dnr/buy-and-apply/fishing',
    regulationsUrl: 'https://www.michigan.gov/dnr/managing-resources/fisheries/regulations',
    licenseCost: { resident: 26, nonResident: 76, seniorDiscount: true, youthFree: true, youthMaxAge: 16 },
    freeFishingDays: ['June second Saturday+Sunday', 'February Presidents Day weekend'],
    generalSeason: {
      walleye: { openDate: 'Apr last Saturday', closeDate: 'Mar 15', dailyBag: 5, possessionLimit: 10, minSizeInches: 15 },
      bass: { openDate: 'Jun last Saturday', closeDate: 'Dec 31', dailyBag: 5, possessionLimit: 10, minSizeInches: 14, notes: 'Catch-and-release May-June in some waters' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 24 },
      trout: { openDate: 'Apr last Saturday', closeDate: 'Sep 30', dailyBag: 5, possessionLimit: 10, notes: 'Lake trout: 3 daily on Great Lakes' },
      muskie: { openDate: 'Jun first Saturday', closeDate: 'Dec 15', dailyBag: 1, possessionLimit: 1, minSizeInches: 42 },
    },
    specialNotes: ['Great Lakes: special regulations apply', 'Invasive species prevention required at all launches'],
  },
  {
    state: 'Ontario',
    stateCode: 'ON',
    country: 'CA',
    licenseUrl: 'https://www.ontario.ca/page/fishing-licence',
    regulationsUrl: 'https://www.ontario.ca/document/ontario-fishing-regulations-summary',
    licenseCost: { resident: 28, nonResident: 75, youthFree: true, youthMaxAge: 17 },
    freeFishingDays: ['Family Day weekend', 'Canada Day weekend', 'Mothers Day weekend', 'Fathers Day weekend'],
    generalSeason: {
      walleye: { openDate: 'Jan 1', closeDate: 'Dec 31', dailyBag: 4, possessionLimit: 4, notes: 'Zone-dependent. Closed Apr 1-Jun 30 in many zones.' },
      bass: { openDate: 'Jun last Saturday', closeDate: 'Nov 30', dailyBag: 6, possessionLimit: 6, notes: 'Catch-and-release Dec 1-Jun third Friday' },
      pike: { openDate: 'Jan 1', closeDate: 'Dec 31', dailyBag: 6, possessionLimit: 6, notes: 'Closed Mar 1-May 31 in some zones' },
      trout: { openDate: 'Jan 1', closeDate: 'Sep 30', dailyBag: 5, possessionLimit: 5, notes: 'Lake trout: 2 daily in most zones' },
      muskie: { openDate: 'Jun first Saturday', closeDate: 'Dec 15', dailyBag: 1, possessionLimit: 1, minSizeInches: 36 },
    },
    specialNotes: ['Outdoors Card required to purchase fishing licence', '26 Fisheries Management Zones with different rules'],
  },
];

// ── Service Functions ────────────────────────────────────────────────────────

/**
 * Get regulations for a state/province by code.
 */
export function getRegulations(stateCode: string): FishingRegulation | undefined {
  return REGULATIONS.find((r) => r.stateCode === stateCode.toUpperCase());
}

/**
 * Get regulations by coordinates (approximate state lookup).
 */
export function getRegulationsByLocation(lat: number, lon: number): FishingRegulation | undefined {
  // Simple bounding box lookup — good enough for most NA locations
  const STATE_BOUNDS: Record<string, { lat: [number, number]; lon: [number, number] }> = {
    TX: { lat: [25.8, 36.5], lon: [-106.7, -93.5] },
    MN: { lat: [43.5, 49.4], lon: [-97.2, -89.5] },
    FL: { lat: [24.5, 31.0], lon: [-87.6, -80.0] },
    CA: { lat: [32.5, 42.0], lon: [-124.5, -114.1] },
    MI: { lat: [41.7, 48.3], lon: [-90.4, -82.1] },
    ON: { lat: [41.7, 56.9], lon: [-95.2, -74.3] },
  };

  for (const [code, bounds] of Object.entries(STATE_BOUNDS)) {
    if (lat >= bounds.lat[0] && lat <= bounds.lat[1] &&
        lon >= bounds.lon[0] && lon <= bounds.lon[1]) {
      return getRegulations(code);
    }
  }

  return undefined;
}

/**
 * Get all available regulation entries.
 */
export function getAllRegulations(): FishingRegulation[] {
  return REGULATIONS;
}

/**
 * Get season info for a species in a state.
 */
export function getSeasonInfo(stateCode: string, species: string): SeasonInfo | undefined {
  const regs = getRegulations(stateCode);
  if (!regs) return undefined;

  const key = species.toLowerCase().replace(/\s+/g, '') as keyof typeof regs.generalSeason;
  return regs.generalSeason[key];
}

/**
 * Check if fishing is currently in season for a species.
 */
export function isInSeason(stateCode: string, species: string): boolean {
  const season = getSeasonInfo(stateCode, species);
  if (!season) return true; // Unknown = assume open

  if (season.openDate === 'Year-round') return true;

  // Simple check — full date parsing would be more robust
  // For now, return true and let the user check the official regs
  return true;
}

/**
 * Format bag limit as a display string.
 */
export function formatBagLimit(season: SeasonInfo): string {
  if (season.dailyBag === 0) return 'No limit';

  let s = `${season.dailyBag} daily`;
  if (season.possessionLimit > season.dailyBag) {
    s += ` / ${season.possessionLimit} possession`;
  }
  if (season.minSizeInches) {
    s += ` · ${season.minSizeInches}" minimum`;
  }
  if (season.slotLimit) {
    s += ` · Must release ${season.slotLimit.min}"-${season.slotLimit.max}"`;
  }
  return s;
}
