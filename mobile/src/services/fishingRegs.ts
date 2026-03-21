/**
 * OpenCatch — Fishing Regulations Service
 *
 * Provides state/province-specific fishing regulation information.
 * Data is embedded for offline access; can be supplemented with API calls.
 *
 * Covers: license requirements, season dates, bag limits, size limits,
 * special regulations, and links to official regulation documents.
 *
 * DISCLAIMER: Regulations change annually. Always verify with the official
 * fish & wildlife agency before fishing. This data is approximate and
 * intended as a convenience reference only.
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
    salmon?: SeasonInfo;
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
// All 50 US states + 13 Canadian provinces/territories
// License costs approximate for 2025-2026 season. Verify with official sources.

const REGULATIONS: FishingRegulation[] = [
  // ═══════════════════════════════════════════════════════════════════════════
  //  UNITED STATES (50 states, alphabetical)
  // ═══════════════════════════════════════════════════════════════════════════

  {
    state: 'Alabama',
    stateCode: 'AL',
    country: 'US',
    licenseUrl: 'https://www.outdooralabama.com/licenses-and-permits',
    regulationsUrl: 'https://www.outdooralabama.com/fishing/freshwater-fishing',
    licenseCost: { resident: 14, nonResident: 54, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first Saturday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 12, notes: 'Largemouth 12" min on most public waters. Spotted bass no size limit on some waters.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 30, possessionLimit: 30, notes: 'Combined black and white crappie' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No daily limit on most waters' },
    },
    specialNotes: ['Freshwater and saltwater licenses are separate', 'No closed season on most freshwater species'],
  },
  {
    state: 'Alaska',
    stateCode: 'AK',
    country: 'US',
    licenseUrl: 'https://www.adfg.alaska.gov/index.cfm?adfg=license.main',
    regulationsUrl: 'https://www.adfg.alaska.gov/index.cfm?adfg=fishregulations.main',
    licenseCost: { resident: 20, nonResident: 100, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June second Saturday+Sunday'],
    generalSeason: {
      salmon: { openDate: 'Jun 1', closeDate: 'Sep 30', dailyBag: 3, possessionLimit: 6, notes: 'King salmon require king stamp ($15 resident, $100 NR). Varies by river system.' },
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 2, possessionLimit: 2, notes: 'Rainbow trout (steelhead) catch-and-release on many rivers' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, notes: 'No size limit. Encouraged harvest in some areas.' },
    },
    specialNotes: ['King salmon stamp required for king salmon fishing', 'Catch-and-release only for steelhead in many rivers', 'Remote areas may have special regulations'],
  },
  {
    state: 'Arizona',
    stateCode: 'AZ',
    country: 'US',
    licenseUrl: 'https://www.azgfd.com/license/',
    regulationsUrl: 'https://www.azgfd.com/fishing/regulations/',
    licenseCost: { resident: 37, nonResident: 55, youthFree: true, youthMaxAge: 9 },
    freeFishingDays: ['June first Saturday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, minSizeInches: 13, notes: 'Largemouth 13" min. Smallmouth 13" min. Combined limit.' },
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, notes: 'Stocked trout waters. Native Apache trout catch-and-release only.' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on channel catfish in most waters' },
    },
    specialNotes: ['Trout stamp required for trout ($24.50)', 'Colorado River has special regulations', 'Community fishing waters have different rules'],
  },
  {
    state: 'Arkansas',
    stateCode: 'AR',
    country: 'US',
    licenseUrl: 'https://www.agfc.com/en/licensing/',
    regulationsUrl: 'https://www.agfc.com/en/fishing/regulations/',
    licenseCost: { resident: 23, nonResident: 70, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first weekend'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 12, minSizeInches: 12, notes: 'LM bass 12" min on most waters. Some trophy lakes 14"-18" slot.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 30, possessionLimit: 30, notes: 'Combined black and white crappie' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on channel catfish' },
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, notes: 'Trout permit required ($5). White River and tributaries are world-class.' },
    },
    specialNotes: ['Trout permit required in addition to fishing license', 'White River is a premier trout destination', 'Noodling (hand fishing) is legal'],
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
    state: 'Colorado',
    stateCode: 'CO',
    country: 'US',
    licenseUrl: 'https://cpw.state.co.us/buyapply/Pages/Fishing.aspx',
    regulationsUrl: 'https://cpw.state.co.us/learn/Pages/FishingRegulations.aspx',
    licenseCost: { resident: 36, nonResident: 101, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first weekend'],
    generalSeason: {
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 4, possessionLimit: 8, notes: 'Gold Medal waters may be catch-and-release or 2-fish limit with artificial only' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 12, notes: 'Largemouth and smallmouth combined' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, notes: 'Varies by reservoir. Some waters have slot limits.' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'Unlimited harvest — pike are invasive in many CO waters' },
    },
    specialNotes: ['Gold Medal waters have special regulations', 'Pike harvest encouraged — invasive species in many reservoirs', 'Habitat stamp required ($10)'],
  },
  {
    state: 'Connecticut',
    stateCode: 'CT',
    country: 'US',
    licenseUrl: 'https://portal.ct.gov/deep/fishing/general-information/fishing-licenses-and-permits',
    regulationsUrl: 'https://portal.ct.gov/deep/fishing/general-information/ct-fishing-guide',
    licenseCost: { resident: 32, nonResident: 63, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June second Saturday'],
    generalSeason: {
      trout: { openDate: 'Apr second Saturday', closeDate: 'Mar 31', dailyBag: 5, possessionLimit: 10, notes: 'Opening day is a major event. Catch-and-release areas available.' },
      bass: { openDate: 'Jun third Saturday', closeDate: 'Nov 30', dailyBag: 6, possessionLimit: 6, minSizeInches: 12, notes: 'Catch-and-release only before season opener' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 2, possessionLimit: 4, minSizeInches: 26, notes: 'Chain pickerel' },
    },
    specialNotes: ['Inland and marine fishing licenses are separate', 'Trout Management Areas have special rules', 'Trout stamp may be required for stocked streams'],
  },
  {
    state: 'Delaware',
    stateCode: 'DE',
    country: 'US',
    licenseUrl: 'https://dnrec.alpha.delaware.gov/fish-wildlife/licenses/',
    regulationsUrl: 'https://dnrec.alpha.delaware.gov/fish-wildlife/fishing/',
    licenseCost: { resident: 13, nonResident: 26, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first Saturday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, minSizeInches: 12, notes: 'Largemouth 12" minimum' },
      trout: { openDate: 'Apr 1', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, notes: 'Stocked trout. Trout stamp required ($4.20).' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No daily limit on catfish' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 25, possessionLimit: 25, notes: 'Combined black and white crappie' },
    },
    specialNotes: ['Trout stamp required for trout fishing', 'Small state but diverse fishing — ponds, rivers, and tidal waters'],
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
    state: 'Georgia',
    stateCode: 'GA',
    country: 'US',
    licenseUrl: 'https://georgiawildlife.com/licenses-permits-passes',
    regulationsUrl: 'https://georgiawildlife.com/fishing/regulations',
    licenseCost: { resident: 15, nonResident: 50, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first Saturday', 'September last Saturday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 10, possessionLimit: 10, minSizeInches: 12, notes: 'Largemouth 12" min. Some lakes have special slot limits.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 30, possessionLimit: 30, notes: 'Combined black and white crappie' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on channel and blue catfish' },
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 8, possessionLimit: 8, notes: 'Trout streams in north GA mountains. Seasonal stocking.' },
    },
    specialNotes: ['Trout stamp required for trout fishing ($5)', 'North Georgia mountains offer excellent trout streams', 'Lake Lanier, Oconee, Sinclair are top bass lakes'],
  },
  {
    state: 'Hawaii',
    stateCode: 'HI',
    country: 'US',
    licenseUrl: 'https://dlnr.hawaii.gov/dar/fishing/freshwater-fishing-license/',
    regulationsUrl: 'https://dlnr.hawaii.gov/dar/fishing/freshwater-fishing/',
    licenseCost: { resident: 5, nonResident: 25, youthFree: true, youthMaxAge: 8 },
    freeFishingDays: ['June first Saturday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 10, possessionLimit: 10, notes: 'Largemouth, smallmouth and peacock bass. Wahiawa Reservoir is top bass water.' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on channel catfish' },
      trout: { openDate: 'Aug first Saturday', closeDate: 'Sep last Sunday', dailyBag: 5, possessionLimit: 5, notes: 'Rainbow trout stocked seasonally in Kauai streams only' },
    },
    specialNotes: ['Freshwater license required only for freshwater fishing', 'No saltwater fishing license needed', 'Most popular species are reef fish (no license required)'],
  },
  {
    state: 'Idaho',
    stateCode: 'ID',
    country: 'US',
    licenseUrl: 'https://idfg.idaho.gov/licenses',
    regulationsUrl: 'https://idfg.idaho.gov/fish/rules',
    licenseCost: { resident: 31, nonResident: 98, seniorDiscount: true, youthFree: true, youthMaxAge: 13 },
    freeFishingDays: ['June second Saturday+Sunday'],
    generalSeason: {
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 12, notes: 'Rainbow trout. Varies by region. Some waters catch-and-release only.' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 12, notes: 'Smallmouth bass in Snake River tributaries' },
      salmon: { openDate: 'Jun 15', closeDate: 'Sep 30', dailyBag: 2, possessionLimit: 4, notes: 'Chinook salmon. Steelhead permit required. Varies by river.' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 12, notes: 'Popular in southern reservoirs' },
    },
    specialNotes: ['Steelhead permit required ($12.75 resident)', 'Salmon permit required for salmon fishing', 'Many catch-and-release waters in central Idaho'],
  },
  {
    state: 'Illinois',
    stateCode: 'IL',
    country: 'US',
    licenseUrl: 'https://www.ifishillinois.org/licensing/',
    regulationsUrl: 'https://www.ifishillinois.org/regulations/',
    licenseCost: { resident: 15, nonResident: 32, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June second weekend', 'Father\'s Day weekend'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, minSizeInches: 14, notes: 'Catch-and-release only Mar 15 - Jun 15 in some waters' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, notes: 'Channel catfish. No size limit.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 25, possessionLimit: 25, notes: 'Combined black and white crappie' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, minSizeInches: 14, notes: 'Popular at Rend Lake, Carlyle Lake' },
    },
    specialNotes: ['Inland trout stamp required for trout ($6.50)', 'Lake Michigan salmon stamp required ($6.50)', 'No closed season on most warmwater species'],
  },
  {
    state: 'Indiana',
    stateCode: 'IN',
    country: 'US',
    licenseUrl: 'https://www.in.gov/dnr/fish-and-wildlife/licensing/',
    regulationsUrl: 'https://www.in.gov/dnr/fish-and-wildlife/fishing/',
    licenseCost: { resident: 17, nonResident: 35, youthFree: true, youthMaxAge: 17 },
    freeFishingDays: ['June first weekend'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, minSizeInches: 12, notes: 'Largemouth and smallmouth combined' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No daily limit on channel catfish' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 25, possessionLimit: 25, notes: 'Combined black and white crappie' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 4, possessionLimit: 4, minSizeInches: 14, notes: 'Popular on Ohio River tributaries' },
    },
    specialNotes: ['Trout/salmon stamp required ($11)', 'Lake Michigan offers excellent salmon and steelhead', 'Youth under 18 fish free'],
  },
  {
    state: 'Iowa',
    stateCode: 'IA',
    country: 'US',
    licenseUrl: 'https://www.iowadnr.gov/Fishing/Fishing-Licenses',
    regulationsUrl: 'https://www.iowadnr.gov/Fishing/Iowa-Fish-Species',
    licenseCost: { resident: 22, nonResident: 48, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first weekend'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 12, notes: 'LM bass 12" min. SM bass 12" min.' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 15, notes: 'Popular at West Okoboji, Spirit Lake, Mississippi River' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 15, possessionLimit: 30, notes: 'Channel catfish combined' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 25, possessionLimit: 50, notes: 'Combined black and white crappie' },
    },
    specialNotes: ['Trout stamp required for trout ($13)', 'Boundary waters with neighboring states have special rules', 'No closed season on most warmwater species'],
  },
  {
    state: 'Kansas',
    stateCode: 'KS',
    country: 'US',
    licenseUrl: 'https://ksoutdoors.com/Fishing/Fishing-Licenses',
    regulationsUrl: 'https://ksoutdoors.com/Fishing/Fishing-Regulations',
    licenseCost: { resident: 28, nonResident: 53, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first Saturday+Sunday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 15, notes: 'LM bass 15" on type 1 waters. 12" on type 2.' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 15, notes: 'Varies by water type' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 50, possessionLimit: 50, notes: 'Very generous limits. Combined black and white crappie.' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 10, possessionLimit: 20, notes: 'Channel catfish. Flathead 5 daily.' },
    },
    specialNotes: ['No trout stamp needed — trout available at select stocked locations', 'Wiper (white bass hybrid) 5 daily', 'Milford, El Dorado are top reservoirs'],
  },
  {
    state: 'Kentucky',
    stateCode: 'KY',
    country: 'US',
    licenseUrl: 'https://fw.ky.gov/Fish/Pages/Fishing-Licenses-and-Permits.aspx',
    regulationsUrl: 'https://fw.ky.gov/Fish/Pages/Fishing-Regulations.aspx',
    licenseCost: { resident: 23, nonResident: 55, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first weekend'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, minSizeInches: 12, notes: 'LM and SM bass. Some lakes have special slot limits.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 30, possessionLimit: 30, notes: 'Kentucky Lake and Lake Barkley are world-class crappie destinations' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on catfish' },
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 8, possessionLimit: 8, notes: 'Trout stocked in select tailwaters and streams. Trout permit required.' },
    },
    specialNotes: ['Trout permit required ($10)', 'Lake Cumberland is a top striper/trout lake', 'Kentucky Lake is world-class crappie water'],
  },
  {
    state: 'Louisiana',
    stateCode: 'LA',
    country: 'US',
    licenseUrl: 'https://www.wlf.louisiana.gov/page/fishing-licenses',
    regulationsUrl: 'https://www.wlf.louisiana.gov/page/fishing-regulations',
    licenseCost: { resident: 10, nonResident: 60, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first weekend'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 10, possessionLimit: 10, minSizeInches: 14, notes: 'Largemouth 14" min. Toledo Bend is premier bass water.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 50, possessionLimit: 50, notes: 'Very generous limits. Locally called "sac-a-lait".' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit' },
    },
    specialNotes: ['One of the cheapest resident licenses in the US', 'Separate saltwater license available', 'No closed season on freshwater species', 'Atchafalaya Basin is exceptional for bass and catfish'],
  },
  {
    state: 'Maine',
    stateCode: 'ME',
    country: 'US',
    licenseUrl: 'https://www.maine.gov/ifw/fishing-wildlife/fishing/licensing.html',
    regulationsUrl: 'https://www.maine.gov/ifw/fishing-wildlife/fishing/laws-rules/',
    licenseCost: { resident: 25, nonResident: 64, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['February Presidents Day weekend', 'June first weekend'],
    generalSeason: {
      trout: { openDate: 'Apr 1', closeDate: 'Sep 30', dailyBag: 5, possessionLimit: 5, notes: 'Brook trout (native). Brown and rainbow also present. Lake trout 2 daily.' },
      bass: { openDate: 'Jun 1', closeDate: 'Nov 30', dailyBag: 5, possessionLimit: 5, notes: 'Smallmouth bass are excellent in Maine. Catch-and-release Feb-May.' },
      salmon: { openDate: 'Apr 1', closeDate: 'Sep 30', dailyBag: 1, possessionLimit: 2, notes: 'Landlocked salmon. Atlantic salmon catch-and-release only.' },
    },
    specialNotes: ['Ice fishing is extremely popular', 'Many remote ponds accessible only by hiking', 'Atlantic salmon catch-and-release only — endangered species'],
  },
  {
    state: 'Maryland',
    stateCode: 'MD',
    country: 'US',
    licenseUrl: 'https://dnr.maryland.gov/fisheries/Pages/licenses.aspx',
    regulationsUrl: 'https://dnr.maryland.gov/fisheries/Pages/regulations.aspx',
    licenseCost: { resident: 32, nonResident: 55, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first Saturday', 'July 4th'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, minSizeInches: 12, notes: 'Largemouth 12" and smallmouth 12". Potomac River is excellent.' },
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, notes: 'Trout stamp required ($5). Stocked in western MD streams.' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on channel catfish. Blue catfish no limit (invasive).' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 2, possessionLimit: 2, minSizeInches: 18, notes: 'Deep Creek Lake and upper Potomac' },
    },
    specialNotes: ['Chesapeake Bay fishing requires a saltwater license', 'Blue catfish harvest encouraged — invasive in Chesapeake', 'Trout stamp required for trout fishing'],
  },
  {
    state: 'Massachusetts',
    stateCode: 'MA',
    country: 'US',
    licenseUrl: 'https://www.mass.gov/freshwater-fishing-license',
    regulationsUrl: 'https://www.mass.gov/info-details/freshwater-fishing-regulations',
    licenseCost: { resident: 37, nonResident: 47, youthFree: true, youthMaxAge: 14 },
    freeFishingDays: ['June first weekend'],
    generalSeason: {
      trout: { openDate: 'Apr second Saturday', closeDate: 'Year-round', dailyBag: 3, possessionLimit: 3, notes: 'Opening day is a major event. Stocked trout.' },
      bass: { openDate: 'Jul 1', closeDate: 'Nov 30', dailyBag: 5, possessionLimit: 5, minSizeInches: 12, notes: 'Catch-and-release only before Jul 1' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 2, possessionLimit: 2, minSizeInches: 28, notes: 'Northern pike. Chain pickerel no size limit.' },
    },
    specialNotes: ['Quabbin Reservoir is a trophy lake trout destination', 'Freshwater and saltwater licenses are separate', 'No closed season on panfish'],
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
    state: 'Mississippi',
    stateCode: 'MS',
    country: 'US',
    licenseUrl: 'https://www.mdwfp.com/license/fishing-licenses/',
    regulationsUrl: 'https://www.mdwfp.com/fishing-boating/',
    licenseCost: { resident: 12, nonResident: 64, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first Saturday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 10, possessionLimit: 10, minSizeInches: 14, notes: 'Varies by water type. Ross Barnett Reservoir is excellent.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 30, possessionLimit: 30, notes: 'Mississippi is top crappie state. Enid, Grenada, Sardis lakes.' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No daily limit. Mississippi River is premier catfish water.' },
    },
    specialNotes: ['No closed season on freshwater species', 'One of the cheapest resident licenses in the US', 'Farm ponds do not require a license'],
  },
  {
    state: 'Missouri',
    stateCode: 'MO',
    country: 'US',
    licenseUrl: 'https://mdc.mo.gov/fishing/regulations/permits',
    regulationsUrl: 'https://mdc.mo.gov/fishing/regulations',
    licenseCost: { resident: 12, nonResident: 49, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June second Saturday+Sunday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, minSizeInches: 12, notes: 'Largemouth 12" min. Smallmouth 12" min.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 30, possessionLimit: 60, notes: 'Combined black and white crappie' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 20, possessionLimit: 20, notes: 'Channel catfish. Blue catfish 10 daily.' },
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 4, possessionLimit: 8, notes: 'Trout permit required ($7). Excellent tailwater trout fisheries.' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 4, possessionLimit: 8, minSizeInches: 15, notes: 'Table Rock, Stockton, Truman lakes' },
    },
    specialNotes: ['Trout permit required ($7) for trout areas', 'One of the cheapest fishing licenses in the US', 'Conservation areas provide free fishing access'],
  },
  {
    state: 'Montana',
    stateCode: 'MT',
    country: 'US',
    licenseUrl: 'https://fwp.mt.gov/buyandapply/fishinglicenses',
    regulationsUrl: 'https://fwp.mt.gov/fish/regulations',
    licenseCost: { resident: 31, nonResident: 118, youthFree: true, youthMaxAge: 11 },
    freeFishingDays: ['June second weekend'],
    generalSeason: {
      trout: { openDate: 'May third Saturday', closeDate: 'Nov 30', dailyBag: 5, possessionLimit: 10, notes: 'Rainbow, brown, brook, cutthroat trout. World-class fly fishing.' },
      bass: { openDate: 'May third Saturday', closeDate: 'Nov 30', dailyBag: 5, possessionLimit: 10, notes: 'Smallmouth bass in Missouri River and Yellowstone' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 10, possessionLimit: 20, notes: 'Fort Peck Reservoir is a top walleye destination' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 10, possessionLimit: 10, notes: 'Invasive in some waters — harvest encouraged' },
    },
    specialNotes: ['World-renowned fly fishing rivers (Bighorn, Madison, Missouri)', 'Conservation license option available', 'Bull trout catch-and-release only statewide'],
  },
  {
    state: 'Nebraska',
    stateCode: 'NE',
    country: 'US',
    licenseUrl: 'https://outdoornebraska.gov/fishing-licenses/',
    regulationsUrl: 'https://outdoornebraska.gov/fishingregs/',
    licenseCost: { resident: 38, nonResident: 76, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first weekend'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 12, notes: 'LM and SM bass combined' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 4, possessionLimit: 8, minSizeInches: 15, notes: 'McConaughy is premier walleye water' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 10, possessionLimit: 20, notes: 'Channel catfish. Flathead 5 daily.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 15, possessionLimit: 30, notes: 'Combined black and white crappie' },
    },
    specialNotes: ['Lake McConaughy is a top multi-species fishery', 'No trout stamp required', 'Ice fishing very popular on sandhills lakes'],
  },
  {
    state: 'Nevada',
    stateCode: 'NV',
    country: 'US',
    licenseUrl: 'https://www.ndow.org/licensing/',
    regulationsUrl: 'https://www.ndow.org/fish/',
    licenseCost: { resident: 40, nonResident: 80, youthFree: true, youthMaxAge: 11 },
    freeFishingDays: ['June first Saturday'],
    generalSeason: {
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, notes: 'Lahontan cutthroat trout in Pyramid Lake (tribal permit required).' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 12, notes: 'Lake Mead, Lake Mohave, Rye Patch Reservoir' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on catfish' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, notes: 'Rye Patch and Lahontan reservoirs' },
    },
    specialNotes: ['Pyramid Lake requires separate tribal permit', 'Lake Mead/Mohave under multistate regulations', 'Desert waters — limited but quality fisheries'],
  },
  {
    state: 'New Hampshire',
    stateCode: 'NH',
    country: 'US',
    licenseUrl: 'https://www.wildlife.nh.gov/fishing/fishing-license',
    regulationsUrl: 'https://www.wildlife.nh.gov/fishing/fishing-regulations',
    licenseCost: { resident: 45, nonResident: 63, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first Saturday'],
    generalSeason: {
      trout: { openDate: 'Jan 1', closeDate: 'Oct 15', dailyBag: 5, possessionLimit: 5, notes: 'Brook trout and stocked trout. Some catch-and-release waters.' },
      bass: { openDate: 'Jun 15', closeDate: 'Nov 30', dailyBag: 5, possessionLimit: 5, minSizeInches: 12, notes: 'Smallmouth bass excellent. Lake Winnipesaukee is top water.' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 2, possessionLimit: 2, minSizeInches: 28, notes: 'Northern pike and chain pickerel' },
      salmon: { openDate: 'Apr 1', closeDate: 'Sep 30', dailyBag: 2, possessionLimit: 2, notes: 'Landlocked salmon in Big and Little lakes' },
    },
    specialNotes: ['Ice fishing very popular', 'Lake Winnipesaukee is largest lake in state', 'Saltwater fishing does not require a license'],
  },
  {
    state: 'New Jersey',
    stateCode: 'NJ',
    country: 'US',
    licenseUrl: 'https://www.nj.gov/dep/fgw/fishing_license.htm',
    regulationsUrl: 'https://www.nj.gov/dep/fgw/fishing.htm',
    licenseCost: { resident: 23, nonResident: 34, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June second Saturday+Sunday'],
    generalSeason: {
      trout: { openDate: 'Apr second Saturday', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, notes: 'Opening day is a major event. Trout stamp required ($10.50).' },
      bass: { openDate: 'Jun second Saturday', closeDate: 'Nov 30', dailyBag: 5, possessionLimit: 5, minSizeInches: 12, notes: 'Catch-and-release before season opener' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on channel catfish' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 2, possessionLimit: 2, minSizeInches: 24, notes: 'Northern pike. Chain pickerel no size limit.' },
    },
    specialNotes: ['Trout stamp required ($10.50)', 'Round Valley Reservoir is trophy trout/bass water', 'Delaware River shared with PA — check both states regulations'],
  },
  {
    state: 'New Mexico',
    stateCode: 'NM',
    country: 'US',
    licenseUrl: 'https://www.wildlife.state.nm.us/fishing/licenses-permits/',
    regulationsUrl: 'https://www.wildlife.state.nm.us/fishing/fishing-rules-info/',
    licenseCost: { resident: 25, nonResident: 56, youthFree: true, youthMaxAge: 11 },
    freeFishingDays: ['June first Saturday'],
    generalSeason: {
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, notes: 'Rainbow and brown trout in mountain streams and reservoirs' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 14, notes: 'Elephant Butte is top bass lake' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 15, possessionLimit: 30, notes: 'Channel catfish. Flathead 2 daily.' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, notes: 'Clayton Lake, Conchas Lake' },
    },
    specialNotes: ['Tribal lands require separate tribal permits', 'Rio Grande provides excellent year-round trout fishing', 'High-altitude lakes may have special regulations'],
  },
  {
    state: 'New York',
    stateCode: 'NY',
    country: 'US',
    licenseUrl: 'https://www.dec.ny.gov/outdoor/7917.html',
    regulationsUrl: 'https://www.dec.ny.gov/outdoor/7894.html',
    licenseCost: { resident: 25, nonResident: 50, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June last Saturday+Sunday', 'February Presidents Day weekend'],
    generalSeason: {
      trout: { openDate: 'Apr 1', closeDate: 'Oct 15', dailyBag: 5, possessionLimit: 10, notes: 'Opening day is Apr 1. Streams and lakes.' },
      bass: { openDate: 'Jun third Saturday', closeDate: 'Nov 30', dailyBag: 5, possessionLimit: 5, minSizeInches: 12, notes: 'Catch-and-release before season opener. St. Lawrence is world-class.' },
      walleye: { openDate: 'May 1', closeDate: 'Mar 15', dailyBag: 5, possessionLimit: 5, minSizeInches: 15, notes: 'Oneida Lake, Lake Erie tributaries' },
      pike: { openDate: 'May 1', closeDate: 'Mar 15', dailyBag: 5, possessionLimit: 5, minSizeInches: 24, notes: 'Northern pike' },
      muskie: { openDate: 'Jun first Saturday', closeDate: 'Nov 30', dailyBag: 1, possessionLimit: 1, minSizeInches: 36, notes: 'St. Lawrence River is premier muskie water' },
    },
    specialNotes: ['Great Lakes require special stamps', 'Adirondack waters have unique regulations', 'St. Lawrence River is world-class smallmouth and muskie water'],
  },
  {
    state: 'North Carolina',
    stateCode: 'NC',
    country: 'US',
    licenseUrl: 'https://www.ncwildlife.org/Licensing/Fishing-License',
    regulationsUrl: 'https://www.ncwildlife.org/Fishing/Regulations',
    licenseCost: { resident: 25, nonResident: 45, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['July 4th'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, minSizeInches: 14, notes: 'LM bass 14" in most waters. Varies by water type.' },
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 7, possessionLimit: 7, notes: 'Mountain trout waters in western NC. Special trout license required.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 20, possessionLimit: 20, notes: 'Combined black and white crappie' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on channel catfish. Blue catfish 20 daily.' },
    },
    specialNotes: ['Mountain trout waters have special regulations and require trout license', 'Lake Norman, Falls Lake are top bass lakes', 'Coastal fishing requires CRFL (free)'],
  },
  {
    state: 'North Dakota',
    stateCode: 'ND',
    country: 'US',
    licenseUrl: 'https://gf.nd.gov/buy/licenses',
    regulationsUrl: 'https://gf.nd.gov/fishing/regulations',
    licenseCost: { resident: 18, nonResident: 48, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first weekend'],
    generalSeason: {
      walleye: { openDate: 'May 1', closeDate: 'Mar 31', dailyBag: 5, possessionLimit: 10, minSizeInches: 14, notes: 'Lake Sakakawea and Devils Lake are top destinations' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 24, notes: 'Northern pike' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 12, notes: 'Smallmouth bass growing fishery' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 10, possessionLimit: 20, notes: 'Channel catfish. Missouri River system.' },
    },
    specialNotes: ['Lake Sakakawea is world-class walleye water', 'Devils Lake is premier perch and walleye', 'Ice fishing is extremely popular'],
  },
  {
    state: 'Ohio',
    stateCode: 'OH',
    country: 'US',
    licenseUrl: 'https://ohiodnr.gov/buy-and-apply/fishing-resources/fishing-licenses-permits',
    regulationsUrl: 'https://ohiodnr.gov/fishing-resources/fishing-regulations',
    licenseCost: { resident: 19, nonResident: 40, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June second weekend'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, minSizeInches: 12, notes: 'Largemouth and smallmouth combined' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, minSizeInches: 15, notes: 'Lake Erie is walleye capital of the world' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 30, possessionLimit: 30, notes: 'Combined black and white crappie' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No daily limit on catfish' },
    },
    specialNotes: ['Lake Erie walleye fishing is world-class', 'No closed season on warmwater species', 'Steelhead run in Lake Erie tributaries (fall/winter)'],
  },
  {
    state: 'Oklahoma',
    stateCode: 'OK',
    country: 'US',
    licenseUrl: 'https://www.wildlifedepartment.com/licensing',
    regulationsUrl: 'https://www.wildlifedepartment.com/fishing/regulations',
    licenseCost: { resident: 25, nonResident: 55, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first Saturday+Sunday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, minSizeInches: 14, notes: 'LM bass 14" on most waters. Grand Lake, Tenkiller are top waters.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 25, possessionLimit: 25, notes: 'Combined black and white crappie' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on channel catfish. Blue catfish 10 daily.' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, minSizeInches: 16, notes: 'Canton Lake, Fort Gibson Lake' },
    },
    specialNotes: ['Paddlefish snagging season (Mar-May) at Grand Lake', 'No closed season on warmwater species', 'Noodling (hand fishing) is legal'],
  },
  {
    state: 'Oregon',
    stateCode: 'OR',
    country: 'US',
    licenseUrl: 'https://myodfw.com/fishing/licensing',
    regulationsUrl: 'https://myodfw.com/recreation-report/fishing-report',
    licenseCost: { resident: 44, nonResident: 111, youthFree: true, youthMaxAge: 11 },
    freeFishingDays: ['June first weekend', 'November last Saturday'],
    generalSeason: {
      trout: { openDate: 'Apr last Saturday', closeDate: 'Oct 31', dailyBag: 5, possessionLimit: 10, notes: 'Rainbow, brown, brook trout. Varies by region.' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 20, notes: 'Smallmouth in John Day and Umpqua rivers are excellent' },
      salmon: { openDate: 'Varies', closeDate: 'Varies', dailyBag: 2, possessionLimit: 2, notes: 'Chinook and coho. Salmon/steelhead tag required ($25.50 resident).' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, minSizeInches: 16, notes: 'Columbia River. No limit encouragement in some areas.' },
    },
    specialNotes: ['Combined angling tag for salmon/steelhead required ($25.50 resident)', 'Columbia River shared with Washington', 'Deschutes River is world-class trout water'],
  },
  {
    state: 'Pennsylvania',
    stateCode: 'PA',
    country: 'US',
    licenseUrl: 'https://www.fishandboat.com/Fish/FishingLicenses/Pages/default.aspx',
    regulationsUrl: 'https://www.fishandboat.com/Fish/FishingRegulations/Pages/default.aspx',
    licenseCost: { resident: 23, nonResident: 53, seniorDiscount: true, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June second Sunday (Father\'s Day weekend)'],
    generalSeason: {
      trout: { openDate: 'Apr second Saturday', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, notes: 'Opening day is a PA tradition. Trout/salmon permit required ($9.97).' },
      bass: { openDate: 'Jun 15', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, minSizeInches: 12, notes: 'Catch-and-release only before Jun 15. Susquehanna River is premier.' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, minSizeInches: 15, notes: 'Lake Erie and Raystown Lake' },
      muskie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 1, possessionLimit: 2, minSizeInches: 40, notes: 'Allegheny River and select lakes' },
    },
    specialNotes: ['Trout/salmon permit required ($9.97)', 'Lake Erie offers salmon, steelhead, walleye', 'Opening day of trout is a statewide tradition'],
  },
  {
    state: 'Rhode Island',
    stateCode: 'RI',
    country: 'US',
    licenseUrl: 'https://dem.ri.gov/programs/fish-wildlife/freshwater-fisheries/licenses-permits',
    regulationsUrl: 'https://dem.ri.gov/programs/fish-wildlife/freshwater-fisheries',
    licenseCost: { resident: 18, nonResident: 35, youthFree: true, youthMaxAge: 14 },
    freeFishingDays: ['June first weekend'],
    generalSeason: {
      trout: { openDate: 'Apr second Saturday', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, notes: 'Stocked trout. Opening day tradition.' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, minSizeInches: 12, notes: 'Largemouth bass' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 2, possessionLimit: 2, notes: 'Chain pickerel. Common in RI ponds.' },
    },
    specialNotes: ['Smallest US state but numerous fishing ponds', 'Saltwater fishing does not require a license', 'Worden Pond is largest natural lake'],
  },
  {
    state: 'South Carolina',
    stateCode: 'SC',
    country: 'US',
    licenseUrl: 'https://www.dnr.sc.gov/licenses/freshwaterlicense.html',
    regulationsUrl: 'https://www.dnr.sc.gov/fishing.html',
    licenseCost: { resident: 10, nonResident: 35, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first Saturday', 'July 4th'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 14, notes: 'LM bass 14" on most public waters. Santee Cooper is legendary.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 20, possessionLimit: 40, notes: 'Santee Cooper lakes are premier crappie water' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on catfish. Santee Cooper blue catfish are huge.' },
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, notes: 'Mountain trout in upstate SC. Chattooga River is excellent.' },
    },
    specialNotes: ['One of the cheapest resident licenses in the US', 'Santee Cooper lakes produce world-record catfish', 'No closed season on freshwater species'],
  },
  {
    state: 'South Dakota',
    stateCode: 'SD',
    country: 'US',
    licenseUrl: 'https://gfp.sd.gov/fishing-licenses/',
    regulationsUrl: 'https://gfp.sd.gov/fishing/regulations/',
    licenseCost: { resident: 28, nonResident: 67, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first weekend'],
    generalSeason: {
      walleye: { openDate: 'May 1', closeDate: 'Mar 31', dailyBag: 4, possessionLimit: 8, minSizeInches: 15, notes: 'Oahe Reservoir is top walleye water' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, minSizeInches: 12, notes: 'Largemouth and smallmouth' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 3, possessionLimit: 6, minSizeInches: 24, notes: 'Northern pike' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, notes: 'Channel catfish. Missouri River system.' },
    },
    specialNotes: ['Lake Oahe is legendary walleye and salmon water', 'Ice fishing extremely popular', 'Paddlefish snagging season available'],
  },
  {
    state: 'Tennessee',
    stateCode: 'TN',
    country: 'US',
    licenseUrl: 'https://www.tn.gov/twra/license-sales.html',
    regulationsUrl: 'https://www.tn.gov/twra/fishing/fishing-regulations.html',
    licenseCost: { resident: 33, nonResident: 49, youthFree: true, youthMaxAge: 12 },
    freeFishingDays: ['June first Saturday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, minSizeInches: 14, notes: 'LM bass varies by reservoir. Smallmouth in Pickwick and Dale Hollow.' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 15, possessionLimit: 30, notes: 'Kentucky Lake in TN is world-class crappie water' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on catfish' },
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 7, possessionLimit: 7, notes: 'Tailwater trout fishing is excellent. South Holston, Watauga rivers.' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, minSizeInches: 15, notes: 'Center Hill Lake and Dale Hollow' },
    },
    specialNotes: ['Trout stamp required for trout ($11)', 'Dale Hollow holds world-record smallmouth bass', 'TVA tailwaters provide year-round trout fishing'],
  },
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
    state: 'Utah',
    stateCode: 'UT',
    country: 'US',
    licenseUrl: 'https://wildlife.utah.gov/fishing-license.html',
    regulationsUrl: 'https://wildlife.utah.gov/guidebooks/fishing-guidebook.html',
    licenseCost: { resident: 34, nonResident: 85, youthFree: true, youthMaxAge: 11 },
    freeFishingDays: ['June first Saturday'],
    generalSeason: {
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 4, possessionLimit: 8, notes: 'Rainbow, brown, cutthroat trout. Many quality waters.' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 12, notes: 'Lake Powell and Sand Hollow are top bass waters' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 10, possessionLimit: 10, notes: 'Utah Lake, Starvation Reservoir' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'Northern pike: no limit — invasive species' },
    },
    specialNotes: ['Lake Powell is a premiere multi-species fishery', 'Pike harvest encouraged — invasive species', 'Bonneville cutthroat trout catch-and-release in some waters'],
  },
  {
    state: 'Vermont',
    stateCode: 'VT',
    country: 'US',
    licenseUrl: 'https://vtfishandwildlife.com/fishing/fishing-licenses-and-laws',
    regulationsUrl: 'https://vtfishandwildlife.com/fishing/fishing-regulations',
    licenseCost: { resident: 28, nonResident: 54, youthFree: true, youthMaxAge: 14 },
    freeFishingDays: ['June first Saturday'],
    generalSeason: {
      trout: { openDate: 'Apr second Saturday', closeDate: 'Oct 31', dailyBag: 6, possessionLimit: 12, notes: 'Brook, brown, rainbow trout. Battenkill is legendary.' },
      bass: { openDate: 'Jun second Saturday', closeDate: 'Nov 30', dailyBag: 5, possessionLimit: 5, minSizeInches: 12, notes: 'Lake Champlain is top bass water' },
      walleye: { openDate: 'May second Saturday', closeDate: 'Mar 15', dailyBag: 3, possessionLimit: 6, minSizeInches: 15, notes: 'Lake Champlain' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, minSizeInches: 20, notes: 'Northern pike in Lake Champlain' },
      salmon: { openDate: 'Apr second Saturday', closeDate: 'Oct 31', dailyBag: 2, possessionLimit: 2, notes: 'Landlocked Atlantic salmon in Lake Champlain' },
    },
    specialNotes: ['Battenkill is world-renowned trout stream', 'Lake Champlain shared with NY', 'Ice fishing popular statewide'],
  },
  {
    state: 'Virginia',
    stateCode: 'VA',
    country: 'US',
    licenseUrl: 'https://dwr.virginia.gov/fishing/regulations/licenses/',
    regulationsUrl: 'https://dwr.virginia.gov/fishing/regulations/',
    licenseCost: { resident: 23, nonResident: 47, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first Saturday', 'October first Saturday'],
    generalSeason: {
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, minSizeInches: 12, notes: 'LM and SM combined. James River smallmouth is excellent.' },
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, notes: 'Trout license required ($23). Shenandoah National Park streams.' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, minSizeInches: 18, notes: 'New River, Claytor Lake' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 20, possessionLimit: 20, notes: 'Blue catfish in James and Rappahannock (no limit on some waters — invasive)' },
      muskie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 2, possessionLimit: 2, minSizeInches: 30, notes: 'New River and Shenandoah River' },
    },
    specialNotes: ['Trout license required ($23) for designated trout waters', 'Blue catfish harvest encouraged — invasive in Chesapeake tributaries', 'Shenandoah and James rivers are world-class smallmouth water'],
  },
  {
    state: 'Washington',
    stateCode: 'WA',
    country: 'US',
    licenseUrl: 'https://wdfw.wa.gov/licenses/fishing',
    regulationsUrl: 'https://wdfw.wa.gov/fishing/regulations',
    licenseCost: { resident: 63, nonResident: 150, youthFree: true, youthMaxAge: 14 },
    freeFishingDays: ['June first Saturday+Sunday'],
    generalSeason: {
      trout: { openDate: 'Apr last Saturday', closeDate: 'Oct 31', dailyBag: 5, possessionLimit: 5, notes: 'Rainbow trout. Varies by region.' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, notes: 'Smallmouth and largemouth. Columbia River system.' },
      salmon: { openDate: 'Varies', closeDate: 'Varies', dailyBag: 2, possessionLimit: 2, notes: 'Chinook, coho, sockeye. Must check specific river openings.' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 8, possessionLimit: 16, minSizeInches: 18, notes: 'Columbia River. No limit on some sections.' },
    },
    specialNotes: ['Combination license covers freshwater and saltwater', 'Columbia River shared with Oregon', 'Puget Sound salmon fishing requires separate endorsement'],
  },
  {
    state: 'West Virginia',
    stateCode: 'WV',
    country: 'US',
    licenseUrl: 'https://wvdnr.gov/fishing/fishing-licenses/',
    regulationsUrl: 'https://wvdnr.gov/fishing/fishing-regulations/',
    licenseCost: { resident: 19, nonResident: 37, youthFree: true, youthMaxAge: 14 },
    freeFishingDays: ['June first Saturday+Sunday'],
    generalSeason: {
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, notes: 'Trout stamp required ($3). Stocked trout streams and catch-and-release areas.' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, minSizeInches: 12, notes: 'Smallmouth bass in New and Greenbrier rivers are excellent' },
      catfish: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 0, possessionLimit: 0, notes: 'No limit on channel catfish. Flathead in Kanawha River.' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 4, possessionLimit: 4, minSizeInches: 15, notes: 'Burnsville and Stonewall Jackson lakes' },
      muskie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 2, possessionLimit: 2, minSizeInches: 36, notes: 'Burnsville Lake and Elk River' },
    },
    specialNotes: ['Trout stamp required ($3)', 'New River and Greenbrier are premier smallmouth streams', 'Stonecoal Lake is trophy muskie water'],
  },
  {
    state: 'Wisconsin',
    stateCode: 'WI',
    country: 'US',
    licenseUrl: 'https://dnr.wisconsin.gov/topic/Fishing/licenses',
    regulationsUrl: 'https://dnr.wisconsin.gov/topic/Fishing/regulations',
    licenseCost: { resident: 20, nonResident: 50, seniorDiscount: true, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['June first Saturday+Sunday', 'January third weekend'],
    generalSeason: {
      walleye: { openDate: 'May first Saturday', closeDate: 'Mar 1', dailyBag: 5, possessionLimit: 5, minSizeInches: 15, notes: 'Varies by county. Many waters have special slot limits.' },
      bass: { openDate: 'Jun third Saturday', closeDate: 'Mar 1', dailyBag: 5, possessionLimit: 5, minSizeInches: 14, notes: 'Catch-and-release only before season opener' },
      muskie: { openDate: 'Jun first Saturday', closeDate: 'Nov 30', dailyBag: 1, possessionLimit: 1, minSizeInches: 40, notes: 'Wisconsin is top muskie state. World record from WI waters.' },
      pike: { openDate: 'May first Saturday', closeDate: 'Mar 1', dailyBag: 5, possessionLimit: 5, minSizeInches: 26, notes: 'Northern pike' },
      crappie: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 25, possessionLimit: 25, notes: 'Combined black and white crappie' },
      trout: { openDate: 'May first Saturday', closeDate: 'Sep 30', dailyBag: 5, possessionLimit: 10, notes: 'Inland trout stamp required ($10). Driftless Area is world-class.' },
    },
    specialNotes: ['Inland trout stamp required ($10)', 'Great Lakes salmon stamp required ($10)', 'Driftless Area is premier trout destination', 'Wisconsin holds world muskie records'],
  },
  {
    state: 'Wyoming',
    stateCode: 'WY',
    country: 'US',
    licenseUrl: 'https://wgfd.wyo.gov/Fishing-and-Boating/Fishing-Licenses',
    regulationsUrl: 'https://wgfd.wyo.gov/Fishing-and-Boating/Fishing-Regulations',
    licenseCost: { resident: 27, nonResident: 102, youthFree: true, youthMaxAge: 13 },
    freeFishingDays: ['June first Saturday'],
    generalSeason: {
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 12, notes: 'Cutthroat, rainbow, brown, brook trout. Yellowstone area is legendary.' },
      bass: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, notes: 'Smallmouth bass in Bighorn and North Platte rivers' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 12, notes: 'Boysen and Glendo reservoirs' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 6, possessionLimit: 6, notes: 'Northern pike in select waters' },
    },
    specialNotes: ['Yellowstone cutthroat trout conservation is critical', 'Wind River Indian Reservation requires separate tribal permit', 'North Platte River is world-class trout water'],
  },

  // ═══════════════════════════════════════════════════════════════════════════
  //  CANADA (13 provinces/territories, alphabetical)
  // ═══════════════════════════════════════════════════════════════════════════

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
  {
    state: 'Newfoundland & Labrador',
    stateCode: 'NL',
    country: 'CA',
    licenseUrl: 'https://www.gov.nl.ca/ffa/licensing/',
    regulationsUrl: 'https://www.nfl.dfo-mpo.gc.ca/en/anglers-guide',
    licenseCost: { resident: 23, nonResident: 80, youthFree: true, youthMaxAge: 17 },
    freeFishingDays: ['Fathers Day weekend'],
    generalSeason: {
      trout: { openDate: 'Jan 15', closeDate: 'Sep 15', dailyBag: 12, possessionLimit: 24, notes: 'Brook trout (mud trout). Extremely generous limits. Labrador has incredible wild fisheries.' },
      salmon: { openDate: 'Jun 1', closeDate: 'Sep 15', dailyBag: 2, possessionLimit: 6, notes: 'Atlantic salmon. Separate licence and tagging required. Many rivers catch-and-release only.' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 12, possessionLimit: 24, notes: 'Northern pike in Labrador only (invasive). Harvest strongly encouraged.' },
    },
    specialNotes: [
      'Atlantic salmon licence is separate and more expensive',
      'Federal DFO regulations apply to salmon and trout',
      'Labrador has world-class brook trout and Atlantic salmon',
      'Pike are invasive in Labrador — unlimited harvest encouraged',
      'Ice fishing is popular for trout and landlocked salmon',
    ],
  },
  {
    state: 'Northwest Territories',
    stateCode: 'NT',
    country: 'CA',
    licenseUrl: 'https://www.gov.nt.ca/ecc/en/services/get-fishing-licence',
    regulationsUrl: 'https://www.gov.nt.ca/ecc/en/services/fishing-regulations',
    licenseCost: { resident: 15, nonResident: 60, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: ['Canada Day weekend'],
    generalSeason: {
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 3, possessionLimit: 5, notes: 'Lake trout. Great Slave Lake produces trophy-sized fish.' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 10, notes: 'Northern pike are abundant. Great Slave Lake is excellent.' },
      walleye: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, notes: 'Hay River and Great Slave Lake' },
    },
    specialNotes: [
      'Great Slave Lake is one of the best trophy lake trout fisheries in the world',
      'Arctic grayling catch-and-release only in many waters',
      'Remote fly-in lodges offer incredible wilderness fishing',
      'National park waters require separate federal permits',
      'Inconnu (sheefish) is a unique northern species',
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
    state: 'Nunavut',
    stateCode: 'NU',
    country: 'CA',
    licenseUrl: 'https://gov.nu.ca/fishing-licence',
    regulationsUrl: 'https://www.gov.nu.ca/environment/sport-fishing',
    licenseCost: { resident: 10, nonResident: 40, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: [],
    generalSeason: {
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 3, possessionLimit: 5, notes: 'Arctic char (the primary sport fish) and lake trout.' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, notes: 'Northern pike in limited range (southern Nunavut only)' },
    },
    specialNotes: [
      'Arctic char is the primary and most sought-after sport fish',
      'Remote fly-in fishing camps offer world-class char fishing',
      'Short open-water season (July-September in most areas)',
      'Inuit harvesting rights take precedence',
      'Very limited road access — most fishing is fly-in or boat-in',
    ],
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
    state: 'Prince Edward Island',
    stateCode: 'PE',
    country: 'CA',
    licenseUrl: 'https://www.princeedwardisland.ca/en/information/environment-energy-and-climate-action/angling-resources-and-information-centre',
    regulationsUrl: 'https://www.princeedwardisland.ca/en/information/environment-energy-and-climate-action/angling-resources-and-information-centre',
    licenseCost: { resident: 10, nonResident: 20, seniorDiscount: true, youthFree: true, youthMaxAge: 18 },
    freeFishingDays: [],
    generalSeason: {
      trout: { openDate: 'Apr 15', closeDate: 'Sep 30', dailyBag: 5, possessionLimit: 10, notes: 'Brook trout (speckled trout) — the primary freshwater species. Stocking in many rivers.' },
      bass: { openDate: 'Jun 15', closeDate: 'Oct 31', dailyBag: 5, possessionLimit: 10, notes: 'Smallmouth bass — growing population in some river systems' },
    },
    specialNotes: [
      'Most affordable fishing licence in Canada',
      'Brook trout are the primary freshwater species',
      'Saltwater fishing (lobster, mackerel) is extremely popular',
      'Atlantic salmon populations are recovering — check regulations',
      'Courtesy licence free for seniors (60+) and youth (16-18)',
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
    state: 'Yukon',
    stateCode: 'YT',
    country: 'CA',
    licenseUrl: 'https://yukon.ca/en/outdoor-recreation-and-wildlife/fishing-and-boating/get-yukon-fishing-licence',
    regulationsUrl: 'https://yukon.ca/en/outdoor-recreation-and-wildlife/fishing-and-boating/see-fishing-rules-and-regulations',
    licenseCost: { resident: 20, nonResident: 50, seniorDiscount: true, youthFree: true, youthMaxAge: 15 },
    freeFishingDays: [],
    generalSeason: {
      trout: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 2, possessionLimit: 2, notes: 'Lake trout and rainbow trout. Many waters catch-and-release only.' },
      pike: { openDate: 'Year-round', closeDate: 'Year-round', dailyBag: 5, possessionLimit: 5, notes: 'Northern pike are abundant in lakes and rivers' },
      salmon: { openDate: 'Jul 1', closeDate: 'Sep 15', dailyBag: 1, possessionLimit: 2, notes: 'Chinook salmon on Yukon River system. Very limited allocation.' },
    },
    specialNotes: [
      'Arctic grayling are a premier sport fish — catch-and-release on many waters',
      'Lake trout conservation limits are strict',
      'First Nations harvesting rights take precedence',
      'Remote wilderness fishing — plan carefully for access',
      'Seniors (65+) fish free',
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
    // ── US states ──────────────────────────────────────────
    AL: { lat: [30.2, 35.0], lon: [-88.5, -84.9] },
    AK: { lat: [51.2, 71.4], lon: [-179.2, -129.0] },
    AZ: { lat: [31.3, 37.0], lon: [-114.8, -109.0] },
    AR: { lat: [33.0, 36.5], lon: [-94.6, -89.6] },
    CA: { lat: [32.5, 42.0], lon: [-124.5, -114.1] },
    CO: { lat: [37.0, 41.0], lon: [-109.1, -102.0] },
    CT: { lat: [41.0, 42.1], lon: [-73.7, -71.8] },
    DE: { lat: [38.5, 39.8], lon: [-75.8, -75.0] },
    FL: { lat: [24.5, 31.0], lon: [-87.6, -80.0] },
    GA: { lat: [30.4, 35.0], lon: [-85.6, -80.8] },
    HI: { lat: [18.9, 22.2], lon: [-160.3, -154.8] },
    ID: { lat: [42.0, 49.0], lon: [-117.2, -111.0] },
    IL: { lat: [37.0, 42.5], lon: [-91.5, -87.0] },
    IN: { lat: [37.8, 41.8], lon: [-88.1, -84.8] },
    IA: { lat: [40.4, 43.5], lon: [-96.6, -90.1] },
    KS: { lat: [37.0, 40.0], lon: [-102.1, -94.6] },
    KY: { lat: [36.5, 39.1], lon: [-89.6, -82.0] },
    LA: { lat: [29.0, 33.0], lon: [-94.0, -89.0] },
    ME: { lat: [43.1, 47.5], lon: [-71.1, -66.9] },
    MD: { lat: [37.9, 39.7], lon: [-79.5, -75.0] },
    MA: { lat: [41.2, 42.9], lon: [-73.5, -69.9] },
    MI: { lat: [41.7, 48.3], lon: [-90.4, -82.1] },
    MN: { lat: [43.5, 49.4], lon: [-97.2, -89.5] },
    MS: { lat: [30.2, 35.0], lon: [-91.7, -88.1] },
    MO: { lat: [36.0, 40.6], lon: [-95.8, -89.1] },
    MT: { lat: [44.4, 49.0], lon: [-116.1, -104.0] },
    NE: { lat: [40.0, 43.0], lon: [-104.1, -95.3] },
    NV: { lat: [35.0, 42.0], lon: [-120.0, -114.0] },
    NH: { lat: [42.7, 45.3], lon: [-72.6, -70.7] },
    NJ: { lat: [38.9, 41.4], lon: [-75.6, -73.9] },
    NM: { lat: [31.3, 37.0], lon: [-109.1, -103.0] },
    NY: { lat: [40.5, 45.0], lon: [-79.8, -71.9] },
    NC: { lat: [33.8, 36.6], lon: [-84.3, -75.5] },
    ND: { lat: [45.9, 49.0], lon: [-104.1, -96.6] },
    OH: { lat: [38.4, 42.0], lon: [-84.8, -80.5] },
    OK: { lat: [33.6, 37.0], lon: [-103.0, -94.4] },
    OR: { lat: [42.0, 46.3], lon: [-124.6, -116.5] },
    PA: { lat: [39.7, 42.3], lon: [-80.5, -74.7] },
    RI: { lat: [41.1, 42.0], lon: [-71.9, -71.1] },
    SC: { lat: [32.0, 35.2], lon: [-83.4, -78.6] },
    SD: { lat: [42.5, 45.9], lon: [-104.1, -96.4] },
    TN: { lat: [35.0, 36.7], lon: [-90.3, -81.6] },
    TX: { lat: [25.8, 36.5], lon: [-106.7, -93.5] },
    UT: { lat: [37.0, 42.0], lon: [-114.1, -109.0] },
    VT: { lat: [42.7, 45.0], lon: [-73.4, -71.5] },
    VA: { lat: [36.5, 39.5], lon: [-83.7, -75.2] },
    WA: { lat: [45.5, 49.0], lon: [-124.8, -116.9] },
    WV: { lat: [37.2, 40.6], lon: [-82.6, -77.7] },
    WI: { lat: [42.5, 47.1], lon: [-92.9, -86.2] },
    WY: { lat: [41.0, 45.0], lon: [-111.1, -104.1] },

    // ── Canadian provinces (checked before overlapping US entries via ordering) ──
    BC: { lat: [48.3, 60.0], lon: [-139.1, -114.1] },
    AB: { lat: [49.0, 60.0], lon: [-120.0, -110.0] },
    SK: { lat: [49.0, 60.0], lon: [-110.0, -101.4] },
    MB: { lat: [49.0, 60.0], lon: [-102.0, -88.9] },
    ON: { lat: [41.7, 56.9], lon: [-95.2, -74.3] },
    QC: { lat: [45.0, 62.6], lon: [-79.8, -57.1] },
    NB: { lat: [44.6, 48.1], lon: [-69.1, -63.8] },
    NS: { lat: [43.4, 47.0], lon: [-66.4, -59.7] },
    NL: { lat: [46.6, 60.4], lon: [-67.8, -52.6] },
    PE: { lat: [46.0, 47.1], lon: [-64.4, -62.0] },
    YT: { lat: [60.0, 69.6], lon: [-141.0, -123.8] },
    NT: { lat: [60.0, 78.8], lon: [-136.5, -102.0] },
    NU: { lat: [51.7, 83.1], lon: [-120.4, -61.2] },
  };

  // Check Canadian provinces first (they overlap lat ranges with some US states)
  const canadianFirst = ['YT', 'NT', 'NU', 'BC', 'AB', 'SK', 'MB', 'ON', 'QC', 'NB', 'NS', 'NL', 'PE'];
  for (const code of canadianFirst) {
    const bounds = STATE_BOUNDS[code];
    if (bounds && lat >= bounds.lat[0] && lat <= bounds.lat[1] &&
        lon >= bounds.lon[0] && lon <= bounds.lon[1]) {
      return getRegulations(code);
    }
  }

  // Then US states
  for (const [code, bounds] of Object.entries(STATE_BOUNDS)) {
    if (canadianFirst.includes(code)) continue;
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
