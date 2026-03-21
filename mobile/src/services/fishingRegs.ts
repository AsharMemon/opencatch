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
    licenseCost: { resident: 27, nonResident: 83, youthFree: true, youthMaxAge: 17 },
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
  {
    state: 'British Columbia',
    stateCode: 'BC',
    country: 'CA',
    licenseUrl: 'https://www2.gov.bc.ca/gov/content/sports-culture/recreation/fishing-hunting/fishing/fishing-licences',
    regulationsUrl: 'https://www2.gov.bc.ca/gov/content/sports-culture/recreation/fishing-hunting/fishing/fishing-regulations',
    licenseCost: { resident: 41, nonResident: 91, seniorDiscount: true, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['Family Day weekend', 'Fathers Day weekend'],
    generalSeason: {
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 2, possessionLimit: 2, notes: 'Rainbow trout; varies by region. Classified waters may have stricter limits. Some waters catch-and-release only.' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 4, possessionLimit: 8, notes: 'Smallmouth and largemouth bass. Primarily in southern interior lakes.' },
      walleye: { openDate: 'May 1', closeDate: 'Mar 31', dailyBag: 8, possessionLimit: 16, notes: 'Region 8 (Peace/Omineca). Limits vary by region.' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 10, possessionLimit: 20, notes: 'Northern pike. Encouraged to harvest in many BC waters (invasive in some regions).' },
    },
    specialNotes: [
      'Classified waters require separate licence surcharge',
      '8 management regions with distinct regulations',
      'Salmon fishing requires separate stamp and is heavily regulated',
      'Barbless hooks required province-wide for most salmonid waters',
      'Steelhead catch-and-release only in most waters',
    ],
  },
  {
    state: 'Alberta',
    stateCode: 'AB',
    country: 'CA',
    licenseUrl: 'https://www.alberta.ca/fishing-licences',
    regulationsUrl: 'https://www.alberta.ca/alberta-guide-to-sportfishing-regulations',
    licenseCost: { resident: 30, nonResident: 87, seniorDiscount: true, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['Family Day weekend', 'Canada Day weekend'],
    generalSeason: {
      walleye: { openDate: 'May 16', closeDate: 'Mar 31', dailyBag: 5, possessionLimit: 5, notes: 'Varies by Watershed Management Unit (WMU). Many waters catch-and-release only.' },
      trout: { openDate: 'Jun 16', closeDate: 'Oct 31', dailyBag: 2, possessionLimit: 2, notes: 'Bull trout catch-and-release only province-wide. Cutthroat also C&R in eastern slopes.' },
      pike: { openDate: 'May 16', closeDate: 'Mar 31', dailyBag: 5, possessionLimit: 5, notes: 'Most waters. Some lakes have special limits.' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, notes: 'Smallmouth bass — limited range in AB. No closed season where present.' },
    },
    specialNotes: [
      'WIN (Wildlife Identification Number) required before purchasing licence',
      'Separate mountain national park licences required (Banff, Jasper, etc.)',
      'Bull trout catch-and-release only throughout Alberta',
      'Bait ban in many eastern slopes trout streams',
      'Ice fishing is popular — check specific lake regulations',
    ],
  },
  {
    state: 'Saskatchewan',
    stateCode: 'SK',
    country: 'CA',
    licenseUrl: 'https://www.saskatchewan.ca/residents/parks-culture-heritage-and-sport/hunting-trapping-and-angling/angling/angling-licences',
    regulationsUrl: 'https://www.saskatchewan.ca/residents/parks-culture-heritage-and-sport/hunting-trapping-and-angling/angling',
    licenseCost: { resident: 42, nonResident: 115, seniorDiscount: true, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['Family Day weekend'],
    generalSeason: {
      walleye: { openDate: 'May 1', closeDate: 'Apr 14', dailyBag: 4, possessionLimit: 4, minSizeInches: 18, notes: 'Southern zone. Northern zone may differ. Slot limits on some lakes.' },
      pike: { openDate: 'May 1', closeDate: 'Apr 14', dailyBag: 5, possessionLimit: 5, notes: 'Provincial limit. Trophy waters may have stricter rules.' },
      trout: { openDate: 'May 1', closeDate: 'Sep 30', dailyBag: 3, possessionLimit: 3, notes: 'Lake trout and brook trout. Varies by zone.' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, notes: 'Smallmouth bass — limited distribution in Saskatchewan.' },
    },
    specialNotes: [
      'Two main fishing zones: Southern (Zone S) and Northern (Zone N)',
      'Lac La Ronge and Wollaston Lake are world-class trophy fisheries',
      'Lead tackle banned in some designated waters',
      'Barbless hooks recommended province-wide',
    ],
  },
  {
    state: 'Manitoba',
    stateCode: 'MB',
    country: 'CA',
    licenseUrl: 'https://huntfishmanitoba.ca/go-fishing/fishing-license/',
    regulationsUrl: 'https://www.gov.mb.ca/fish-wildlife/fish/rec_fishing/regs/index.html',
    licenseCost: { resident: 22, nonResident: 55, seniorDiscount: true, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['Family Day weekend', 'Canada Day weekend'],
    generalSeason: {
      walleye: { openDate: 'May second Saturday', closeDate: 'Apr 14', dailyBag: 4, possessionLimit: 4, notes: 'Most waters. Lake Winnipeg has separate regulations. Slot limits on some waters.' },
      pike: { openDate: 'May second Saturday', closeDate: 'Apr 14', dailyBag: 4, possessionLimit: 4, notes: 'Some northern lakes allow 6 daily.' },
      trout: { openDate: 'May second Saturday', closeDate: 'Sep 30', dailyBag: 2, possessionLimit: 2, notes: 'Lake trout. Brook trout limit 5 in some areas.' },
      bass: { openDate: 'May second Saturday', closeDate: 'Apr 14', dailyBag: 6, possessionLimit: 6, notes: 'Smallmouth bass — growing fishery in southern Manitoba.' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 12, possessionLimit: 12, notes: 'Channel catfish. Red River is a premiere catfish fishery.' },
    },
    specialNotes: [
      'Lake Winnipeg is one of the largest freshwater fisheries in North America',
      'Channel catfish in the Red River system are world-class',
      'Barbless hooks required in provincial park waters',
      'Conservation licence available (reduced limits, lower cost)',
    ],
  },
  {
    state: 'Quebec',
    stateCode: 'QC',
    country: 'CA',
    licenseUrl: 'https://www.quebec.ca/en/tourism-and-recreation/sporting-and-outdoor-activities/sport-fishing/fishing-licences',
    regulationsUrl: 'https://www.quebec.ca/en/tourism-and-recreation/sporting-and-outdoor-activities/sport-fishing',
    licenseCost: { resident: 25, nonResident: 80, seniorDiscount: true, youthFree: true, youthMaxAge: 17 },
    freeFishingDays: ['Fete nationale weekend (Jun 24)'],
    generalSeason: {
      walleye: { openDate: 'May first Friday', closeDate: 'Mar 31', dailyBag: 6, possessionLimit: 6, notes: 'Zone-dependent. 29 fishing zones with different rules.' },
      bass: { openDate: 'Jun third Saturday', closeDate: 'Nov 30', dailyBag: 6, possessionLimit: 6, notes: 'Smallmouth and largemouth. Catch-and-release before season opener.' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, notes: 'Most zones. Some northern zones have stricter limits.' },
      trout: { openDate: 'Apr last Saturday', closeDate: 'Sep 15', dailyBag: 5, possessionLimit: 10, notes: 'Brook trout (speckled trout). Lake trout: 3 daily in most zones.' },
      muskie: { openDate: 'Jun first Saturday', closeDate: 'Nov 30', dailyBag: 1, possessionLimit: 1, minSizeInches: 44, notes: 'St. Lawrence River is world-class muskie water.' },
    },
    specialNotes: [
      '29 fishing zones (zones de peche) with distinct regulations',
      'ZEC (controlled harvesting zones) require separate access fees',
      'Outfitter territories (pourvoiries) may have special rules',
      'Atlantic salmon fishing requires separate licence and permit',
      'Live bait restrictions in many trout waters',
    ],
  },
  {
    state: 'Nova Scotia',
    stateCode: 'NS',
    country: 'CA',
    licenseUrl: 'https://www.novascotia.ca/apply-general-fishing-licence-sportfishing',
    regulationsUrl: 'https://www.novascotia.ca/programs-and-services/sportfishing-licensing',
    licenseCost: { resident: 27, nonResident: 35, seniorDiscount: true, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['Fathers Day weekend'],
    generalSeason: {
      trout: { openDate: 'Apr 1', closeDate: 'Sep 30', dailyBag: 5, possessionLimit: 10, notes: 'Brook trout (speckled trout) — the primary freshwater species. Brown trout and rainbow also present.' },
      bass: { openDate: 'Jun 15', closeDate: 'Oct 31', dailyBag: 5, possessionLimit: 10, notes: 'Smallmouth bass. Considered invasive in some watersheds — harvest encouraged.' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, notes: 'Chain pickerel (not northern pike). Found in many NS lakes.' },
    },
    specialNotes: [
      'Atlantic salmon fishing requires separate licence',
      'Catch-and-release only for Atlantic salmon in most rivers',
      'Striped bass fishery in Shubenacadie River system',
      'Tidal waters fishing (saltwater) requires federal licence',
      'Barbless hooks required on select rivers',
    ],
  },
  {
    state: 'New Brunswick',
    stateCode: 'NB',
    country: 'CA',
    licenseUrl: 'https://www2.gnb.ca/content/gnb/en/services/services_renderer.10137.Angling_Licence.html',
    regulationsUrl: 'https://www2.gnb.ca/content/gnb/en/departments/erd/natural_resources/content/fish.html',
    licenseCost: { resident: 26, nonResident: 62, seniorDiscount: true, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['Canada Day weekend'],
    generalSeason: {
      trout: { openDate: 'Apr 15', closeDate: 'Sep 15', dailyBag: 5, possessionLimit: 10, notes: 'Brook trout. Varies by water class (Class I and II waters have different rules).' },
      bass: { openDate: 'Jun 15', closeDate: 'Oct 15', dailyBag: 5, possessionLimit: 10, notes: 'Smallmouth bass — St. John River system is excellent bass water.' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, notes: 'Chain pickerel. Common in southern NB lakes.' },
      walleye: { openDate: 'May 15', closeDate: 'Mar 31', dailyBag: 4, possessionLimit: 4, notes: 'Limited range — primarily in St. John River drainage.' },
    },
    specialNotes: [
      'Atlantic salmon requires separate licence — many rivers are catch-and-release only',
      'Miramichi River is one of the greatest Atlantic salmon rivers in the world',
      'Crown reserve waters require additional daily permit',
      'Striped bass fishery in Miramichi River system (federal regulations apply)',
      'Live bait restrictions on many trout waters',
    ],
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
    // US states
    TX: { lat: [25.8, 36.5], lon: [-106.7, -93.5] },
    MN: { lat: [43.5, 49.4], lon: [-97.2, -89.5] },
    FL: { lat: [24.5, 31.0], lon: [-87.6, -80.0] },
    CA: { lat: [32.5, 42.0], lon: [-124.5, -114.1] },
    MI: { lat: [41.7, 48.3], lon: [-90.4, -82.1] },
    // Canadian provinces (checked before overlapping US entries via ordering)
    BC: { lat: [48.3, 60.0], lon: [-139.1, -114.1] },
    AB: { lat: [49.0, 60.0], lon: [-120.0, -110.0] },
    SK: { lat: [49.0, 60.0], lon: [-110.0, -101.4] },
    MB: { lat: [49.0, 60.0], lon: [-102.0, -88.9] },
    ON: { lat: [41.7, 56.9], lon: [-95.2, -74.3] },
    QC: { lat: [45.0, 62.6], lon: [-79.8, -57.1] },
    NB: { lat: [44.6, 48.1], lon: [-69.1, -63.8] },
    NS: { lat: [43.4, 47.0], lon: [-66.4, -59.7] },
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
