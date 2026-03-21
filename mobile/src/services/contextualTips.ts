/**
 * Contextual fishing tips service for OpenCatch.
 *
 * Generates lake-specific, condition-aware fishing tips based on:
 * - Water body name, type, and approximate size
 * - Species known to be present
 * - Current weather conditions (wind, pressure, temp, etc.)
 * - Time of day and season
 *
 * Tips are filtered against actual current conditions so only relevant
 * advice is ever shown to the angler.
 */

// ── Types ────────────────────────────────────────────────────────

export interface WaterbodyContext {
  name: string;
  type: 'lake' | 'river' | 'pond' | 'reservoir' | 'creek' | 'stream' | 'unknown';
  lat: number;
  lon: number;
  species: string[];
}

export interface WeatherConditions {
  airTemp: number;        // degrees F
  waterTemp?: number;     // degrees F
  windSpeed: number;      // mph
  windDirection: string;  // 'N', 'NW', etc.
  pressure: number;       // inHg
  pressureTrend: 'rising' | 'falling' | 'steady';
  weather: string;        // 'Sunny', 'Cloudy', 'Rainy', etc.
  humidity: number;       // percent
  moonPhase?: string;
  sunrise?: string;       // HH:MM
  sunset?: string;        // HH:MM
}

export interface ContextualTip {
  id: string;
  icon: string;           // Ionicons name
  title: string;          // Short headline
  text: string;           // Expanded advice
  relevanceScore: number; // 0-100 — higher = more relevant right now
  category: TipCategory;
}

export type TipCategory =
  | 'weather'
  | 'wind'
  | 'pressure'
  | 'temperature'
  | 'time_of_day'
  | 'seasonal'
  | 'species'
  | 'structure'
  | 'bait'
  | 'moon'
  | 'water_type';

// ── Season / time helpers ────────────────────────────────────────

function getSeason(lat: number): 'spring' | 'summer' | 'fall' | 'winter' {
  const month = new Date().getMonth(); // 0-11
  // Southern hemisphere flips seasons
  const isNorthern = lat >= 0;
  if (isNorthern) {
    if (month >= 2 && month <= 4) return 'spring';
    if (month >= 5 && month <= 7) return 'summer';
    if (month >= 8 && month <= 10) return 'fall';
    return 'winter';
  }
  if (month >= 2 && month <= 4) return 'fall';
  if (month >= 5 && month <= 7) return 'winter';
  if (month >= 8 && month <= 10) return 'spring';
  return 'summer';
}

type TimeOfDay = 'dawn' | 'morning' | 'midday' | 'afternoon' | 'dusk' | 'night';

function getTimeOfDay(sunrise?: string, sunset?: string): TimeOfDay {
  const now = new Date();
  const hour = now.getHours();
  const minute = now.getMinutes();
  const nowMinutes = hour * 60 + minute;

  let sunriseMin = 6 * 60;  // default 06:00
  let sunsetMin = 19 * 60;  // default 19:00

  if (sunrise) {
    const parts = sunrise.split(':');
    if (parts.length >= 2) sunriseMin = parseInt(parts[0]) * 60 + parseInt(parts[1]);
  }
  if (sunset) {
    const parts = sunset.split(':');
    if (parts.length >= 2) sunsetMin = parseInt(parts[0]) * 60 + parseInt(parts[1]);
  }

  if (nowMinutes >= sunriseMin - 30 && nowMinutes <= sunriseMin + 60) return 'dawn';
  if (nowMinutes > sunriseMin + 60 && nowMinutes <= 11 * 60) return 'morning';
  if (nowMinutes > 11 * 60 && nowMinutes <= 14 * 60) return 'midday';
  if (nowMinutes > 14 * 60 && nowMinutes <= sunsetMin - 60) return 'afternoon';
  if (nowMinutes > sunsetMin - 60 && nowMinutes <= sunsetMin + 30) return 'dusk';
  return 'night';
}

function hasSpecies(species: string[], ...targets: string[]): boolean {
  const lower = species.map((s) => s.toLowerCase());
  return targets.some((t) => lower.some((s) => s.includes(t.toLowerCase())));
}

function isWindy(windSpeed: number): boolean {
  return windSpeed >= 10;
}

function isCalm(windSpeed: number): boolean {
  return windSpeed < 5;
}

function isOvercast(weather: string): boolean {
  return /cloud|overcast|fog/i.test(weather);
}

function isRainy(weather: string): boolean {
  return /rain|storm|drizzle|shower/i.test(weather);
}

function isSunny(weather: string): boolean {
  return /sun|clear/i.test(weather);
}

// ── Tip templates ────────────────────────────────────────────────
//
// Each template has a `match` function that returns a relevance score
// (0 = not relevant, 1-100 = relevant with weight). Only tips that
// score above 0 are shown.

interface TipTemplate {
  id: string;
  icon: string;
  category: TipCategory;
  title: string;
  text: (ctx: WaterbodyContext) => string;
  match: (
    ctx: WaterbodyContext,
    wx: WeatherConditions,
    season: string,
    timeOfDay: TimeOfDay,
  ) => number;
}

const TEMPLATES: TipTemplate[] = [
  // ── Wind tips ──────────────────────────────────────────────────
  {
    id: 'wind-blown-banks',
    icon: 'navigate-outline',
    category: 'wind',
    title: 'Fish the windward shore',
    text: (ctx) =>
      `Wind-blown banks on ${ctx.name} push baitfish and plankton toward shore. Cast along the downwind bank for active feeders.`,
    match: (_ctx, wx) => (wx.windSpeed >= 10 ? 85 : wx.windSpeed >= 7 ? 55 : 0),
  },
  {
    id: 'wind-calm-topwater',
    icon: 'water-outline',
    category: 'wind',
    title: 'Calm water — topwater time',
    text: (ctx) =>
      `Light winds on ${ctx.name} make surface lures highly effective. Try poppers, walking baits, or prop baits near structure.`,
    match: (_ctx, wx, _s, tod) =>
      wx.windSpeed < 5 && (tod === 'dawn' || tod === 'dusk') ? 80 : wx.windSpeed < 5 ? 50 : 0,
  },
  {
    id: 'wind-drift-presentation',
    icon: 'swap-horizontal-outline',
    category: 'wind',
    title: 'Use the wind drift',
    text: () =>
      `Moderate wind creates a natural drift. Use a controlled drift with a slip bobber or drop shot to cover more water efficiently.`,
    match: (_ctx, wx) => (wx.windSpeed >= 8 && wx.windSpeed <= 18 ? 65 : 0),
  },
  {
    id: 'wind-sheltered-cove',
    icon: 'shield-outline',
    category: 'wind',
    title: 'Find a sheltered cove',
    text: (ctx) =>
      `Strong winds on ${ctx.name} make open water tough. Look for protected coves, lee shores, or creek arms for calmer conditions.`,
    match: (_ctx, wx) => (wx.windSpeed >= 18 ? 90 : 0),
  },

  // ── Pressure tips ──────────────────────────────────────────────
  {
    id: 'pressure-dropping-feed',
    icon: 'trending-down-outline',
    category: 'pressure',
    title: 'Pressure dropping — fish are feeding',
    text: () =>
      `Falling barometric pressure triggers aggressive feeding in most gamefish. Fish faster-moving lures and cover more water.`,
    match: (_ctx, wx) => (wx.pressureTrend === 'falling' ? 90 : 0),
  },
  {
    id: 'pressure-dropping-bass',
    icon: 'fish-outline',
    category: 'pressure',
    title: 'Pre-front bass bite',
    text: (ctx) =>
      `Bass in ${ctx.name} feed aggressively before cold fronts. Switch to reaction baits — crankbaits, spinnerbaits, or jerkbaits.`,
    match: (ctx, wx) =>
      wx.pressureTrend === 'falling' && hasSpecies(ctx.species, 'bass', 'largemouth', 'smallmouth')
        ? 95
        : 0,
  },
  {
    id: 'pressure-rising-slow',
    icon: 'trending-up-outline',
    category: 'pressure',
    title: 'Rising pressure — slow your approach',
    text: () =>
      `Rising barometric pressure often makes fish lethargic. Downsize your lure, use finesse presentations, and focus on structure.`,
    match: (_ctx, wx) => (wx.pressureTrend === 'rising' ? 70 : 0),
  },
  {
    id: 'pressure-stable-predictable',
    icon: 'analytics-outline',
    category: 'pressure',
    title: 'Stable pressure — predictable patterns',
    text: () =>
      `Steady barometric pressure means fish hold their normal patterns. Focus on known structure and proven spots.`,
    match: (_ctx, wx) => (wx.pressureTrend === 'steady' ? 50 : 0),
  },
  {
    id: 'pressure-high-clear',
    icon: 'sunny-outline',
    category: 'pressure',
    title: 'High pressure — go deep and subtle',
    text: () =>
      `High pressure and clear skies push fish deeper. Try drop shots, shaky heads, or Ned rigs near deep structure.`,
    match: (_ctx, wx) =>
      wx.pressureTrend === 'rising' && wx.pressure > 30.2 && isSunny(wx.weather) ? 80 : 0,
  },

  // ── Temperature tips ───────────────────────────────────────────
  {
    id: 'temp-summer-deep',
    icon: 'thermometer-outline',
    category: 'temperature',
    title: 'Deep structure in summer heat',
    text: (ctx) =>
      `Water temperatures over 80\u00B0F push fish to deep structure and thermocline areas in ${ctx.name}. Fish ledges, humps, and deep points.`,
    match: (_ctx, wx, season) =>
      season === 'summer' && (wx.waterTemp ?? wx.airTemp) > 80 ? 85 : 0,
  },
  {
    id: 'temp-cold-slow',
    icon: 'snow-outline',
    category: 'temperature',
    title: 'Cold water — slow and small',
    text: () =>
      `In water below 50\u00B0F, fish metabolism slows dramatically. Use small, slow-moving presentations right on the bottom.`,
    match: (_ctx, wx) => ((wx.waterTemp ?? wx.airTemp) < 50 ? 80 : 0),
  },
  {
    id: 'temp-warming-trend',
    icon: 'arrow-up-outline',
    category: 'temperature',
    title: 'Warming trend activates fish',
    text: () =>
      `Rising air temperatures signal warming water. Fish move shallow and feed more aggressively on warm-up days.`,
    match: (_ctx, wx, season) =>
      (season === 'spring' || season === 'winter') && wx.airTemp > 55 && isSunny(wx.weather) ? 75 : 0,
  },
  {
    id: 'temp-spring-shallows',
    icon: 'leaf-outline',
    category: 'temperature',
    title: 'Spring warm-up — check the shallows',
    text: (ctx) =>
      `Spring sun warms shallow coves first on ${ctx.name}. Fish move up to feed on baitfish staging in 2-6 ft of water.`,
    match: (_ctx, wx, season) =>
      season === 'spring' && wx.airTemp >= 50 && wx.airTemp <= 72 ? 80 : 0,
  },
  {
    id: 'temp-fall-turnover',
    icon: 'leaf-outline',
    category: 'temperature',
    title: 'Fall turnover — find clean water',
    text: () =>
      `During fall turnover, oxygen levels mix and muddy water rises. Find pockets of clear water near creek channels for better fishing.`,
    match: (_ctx, wx, season) =>
      season === 'fall' && (wx.waterTemp ?? wx.airTemp) >= 55 && (wx.waterTemp ?? wx.airTemp) <= 68 ? 75 : 0,
  },

  // ── Time-of-day tips ──────────────────────────────────────────
  {
    id: 'tod-dawn-topwater',
    icon: 'sunny-outline',
    category: 'time_of_day',
    title: 'Dawn — prime topwater window',
    text: () =>
      `Early morning is the most effective time for topwater fishing. Low light and calm conditions bring fish to the surface.`,
    match: (_ctx, _wx, _s, tod) => (tod === 'dawn' ? 90 : 0),
  },
  {
    id: 'tod-dusk-feeding',
    icon: 'moon-outline',
    category: 'time_of_day',
    title: 'Dusk — evening feeding run',
    text: () =>
      `Fish feed aggressively at dusk as light fades. Work shallow cover, points, and transition areas with moving baits.`,
    match: (_ctx, _wx, _s, tod) => (tod === 'dusk' ? 88 : 0),
  },
  {
    id: 'tod-midday-deep',
    icon: 'ellipse-outline',
    category: 'time_of_day',
    title: 'Midday — target deeper structure',
    text: () =>
      `During midday sun, most fish retreat to shaded or deep structure. Focus on docks, bridges, submerged timber, and ledges.`,
    match: (_ctx, wx, season, tod) =>
      tod === 'midday' && isSunny(wx.weather) && season === 'summer' ? 80 : tod === 'midday' ? 55 : 0,
  },
  {
    id: 'tod-night-catfish',
    icon: 'moon-outline',
    category: 'time_of_day',
    title: 'Night bite — catfish and walleye',
    text: (ctx) =>
      `Night fishing on ${ctx.name} can be productive for catfish, walleye, and striped bass. Use live bait or noisy lures.`,
    match: (ctx, _wx, _s, tod) =>
      tod === 'night' && hasSpecies(ctx.species, 'catfish', 'walleye', 'striper', 'striped') ? 85 : tod === 'night' ? 40 : 0,
  },
  {
    id: 'tod-overcast-allday',
    icon: 'cloudy-outline',
    category: 'time_of_day',
    title: 'Overcast skies extend the bite',
    text: (ctx) =>
      `Cloud cover on ${ctx.name} reduces light penetration, keeping fish active in shallower water throughout the day.`,
    match: (_ctx, wx) => (isOvercast(wx.weather) ? 70 : 0),
  },

  // ── Weather pattern tips ──────────────────────────────────────
  {
    id: 'wx-rain-active',
    icon: 'rainy-outline',
    category: 'weather',
    title: 'Rain brings the bite',
    text: () =>
      `Light rain oxygenates the surface and washes insects into the water. Fish feed actively near runoff areas and creek mouths.`,
    match: (_ctx, wx) => (isRainy(wx.weather) && wx.windSpeed < 15 ? 85 : 0),
  },
  {
    id: 'wx-post-storm',
    icon: 'thunderstorm-outline',
    category: 'weather',
    title: 'Post-storm recovery bite',
    text: () =>
      `After storms pass, fish often resume feeding aggressively. Focus on areas with fresh current, runoff, and disturbed banks.`,
    match: (_ctx, wx) =>
      wx.pressureTrend === 'rising' && wx.pressure < 30.0 ? 70 : 0,
  },
  {
    id: 'wx-fog-shallow',
    icon: 'cloud-outline',
    category: 'weather',
    title: 'Foggy morning — fish shallow',
    text: () =>
      `Fog indicates stable, humid conditions. Fish stay shallow and feed confidently in the low-visibility surface conditions.`,
    match: (_ctx, wx, _s, tod) =>
      /fog/i.test(wx.weather) && (tod === 'dawn' || tod === 'morning') ? 80 : 0,
  },
  {
    id: 'wx-humidity-insects',
    icon: 'bug-outline',
    category: 'weather',
    title: 'High humidity — insect hatches',
    text: () =>
      `High humidity often triggers insect hatches near water. Panfish and trout feed on the surface during these hatches.`,
    match: (ctx, wx) =>
      wx.humidity > 75 && hasSpecies(ctx.species, 'trout', 'panfish', 'bluegill', 'crappie') ? 65 : 0,
  },

  // ── Seasonal tips ──────────────────────────────────────────────
  {
    id: 'season-spring-spawn',
    icon: 'flower-outline',
    category: 'seasonal',
    title: 'Spring spawn — fish the beds',
    text: () =>
      `Bass begin spawning when water reaches 60-68\u00B0F. Look for beds on hard-bottom flats in 2-6 ft of water near drop-offs.`,
    match: (ctx, wx, season) =>
      season === 'spring' &&
      hasSpecies(ctx.species, 'bass', 'largemouth', 'smallmouth') &&
      (wx.waterTemp ?? wx.airTemp) >= 58
        ? 90
        : 0,
  },
  {
    id: 'season-summer-thermocline',
    icon: 'layers-outline',
    category: 'seasonal',
    title: 'Summer thermocline fishing',
    text: (ctx) =>
      `In summer, ${ctx.name} develops a thermocline. Fish concentrate where oxygen and temperature are optimal — typically 15-25 ft.`,
    match: (ctx, wx, season) =>
      season === 'summer' &&
      (ctx.type === 'lake' || ctx.type === 'reservoir') &&
      (wx.waterTemp ?? wx.airTemp) > 78
        ? 80
        : 0,
  },
  {
    id: 'season-fall-baitfish',
    icon: 'pulse-outline',
    category: 'seasonal',
    title: 'Fall baitfish migration',
    text: (ctx) =>
      `In fall, shad and baitfish migrate to creek arms on ${ctx.name}. Predators follow — look for surface activity and birds diving.`,
    match: (ctx, _wx, season) =>
      season === 'fall' && (ctx.type === 'lake' || ctx.type === 'reservoir') ? 85 : 0,
  },
  {
    id: 'season-winter-slow',
    icon: 'snow-outline',
    category: 'seasonal',
    title: 'Winter — patience pays off',
    text: () =>
      `Cold water means a slower metabolism. Use finesse tactics like hair jigs, blade baits, or small swimbaits fished very slowly.`,
    match: (_ctx, wx, season) => (season === 'winter' && (wx.waterTemp ?? wx.airTemp) < 48 ? 80 : 0),
  },
  {
    id: 'season-spring-crappie',
    icon: 'fish-outline',
    category: 'seasonal',
    title: 'Spring crappie run',
    text: (ctx) =>
      `Crappie on ${ctx.name} move shallow in spring to spawn around brush piles, docks, and stake beds. Use small jigs or minnows.`,
    match: (ctx, _wx, season) =>
      season === 'spring' && hasSpecies(ctx.species, 'crappie') ? 88 : 0,
  },
  {
    id: 'season-fall-walleye',
    icon: 'fish-outline',
    category: 'seasonal',
    title: 'Fall walleye feed-up',
    text: (ctx) =>
      `Walleye in ${ctx.name} feed heavily in fall to prepare for winter. Jig and minnow combos near rocky points and reefs produce well.`,
    match: (ctx, _wx, season) =>
      season === 'fall' && hasSpecies(ctx.species, 'walleye') ? 85 : 0,
  },

  // ── Species-specific tips ──────────────────────────────────────
  {
    id: 'species-bass-structure',
    icon: 'locate-outline',
    category: 'species',
    title: 'Bass love structure transitions',
    text: (ctx) =>
      `On ${ctx.name}, focus on where hard bottom meets soft, where depth changes rapidly, or where cover meets open water.`,
    match: (ctx) =>
      hasSpecies(ctx.species, 'bass', 'largemouth', 'smallmouth') ? 60 : 0,
  },
  {
    id: 'species-trout-current',
    icon: 'water-outline',
    category: 'species',
    title: 'Trout face the current',
    text: (ctx) =>
      `Trout in ${ctx.name} hold facing upstream behind rocks, logs, and current breaks. Cast upstream and let your presentation drift naturally.`,
    match: (ctx) =>
      hasSpecies(ctx.species, 'trout', 'rainbow', 'brown', 'brook') &&
      (ctx.type === 'river' || ctx.type === 'stream' || ctx.type === 'creek')
        ? 80
        : hasSpecies(ctx.species, 'trout')
          ? 55
          : 0,
  },
  {
    id: 'species-pike-ambush',
    icon: 'flash-outline',
    category: 'species',
    title: 'Pike ambush zones',
    text: (ctx) =>
      `Northern pike on ${ctx.name} ambush prey at weed edges, creek mouths, and points. Use large spoons or swimbaits retrieved steadily.`,
    match: (ctx) => (hasSpecies(ctx.species, 'pike', 'northern') ? 70 : 0),
  },
  {
    id: 'species-catfish-scent',
    icon: 'fish-outline',
    category: 'species',
    title: 'Catfish follow scent trails',
    text: (ctx) =>
      `Channel catfish in ${ctx.name} hunt by smell. Use stink baits, cut bait, or chicken liver near deep holes and channel bends.`,
    match: (ctx, _wx, _s, tod) =>
      hasSpecies(ctx.species, 'catfish', 'channel cat')
        ? tod === 'night' || tod === 'dusk'
          ? 85
          : 60
        : 0,
  },
  {
    id: 'species-walleye-lowlight',
    icon: 'eye-off-outline',
    category: 'species',
    title: 'Walleye prefer low light',
    text: (ctx) =>
      `Walleye on ${ctx.name} have light-sensitive eyes and feed most actively at dawn, dusk, and on cloudy days. Target wind-swept points and reefs.`,
    match: (ctx, wx, _s, tod) =>
      hasSpecies(ctx.species, 'walleye')
        ? tod === 'dawn' || tod === 'dusk' || isOvercast(wx.weather)
          ? 90
          : 50
        : 0,
  },
  {
    id: 'species-musky-figure8',
    icon: 'repeat-outline',
    category: 'species',
    title: 'Musky follow — figure-8 at the boat',
    text: () =>
      `Musky often follow lures to the boat without striking. Always perform a figure-8 at the end of every retrieve.`,
    match: (ctx) => (hasSpecies(ctx.species, 'musky', 'muskie', 'muskellunge') ? 80 : 0),
  },
  {
    id: 'species-panfish-dock',
    icon: 'home-outline',
    category: 'species',
    title: 'Panfish under docks and shade',
    text: (ctx) =>
      `Bluegill and panfish on ${ctx.name} congregate around docks, shade structures, and weed edges. Small jigs and worms are deadly.`,
    match: (ctx, wx, season) =>
      hasSpecies(ctx.species, 'bluegill', 'panfish', 'sunfish', 'perch')
        ? season === 'summer' && isSunny(wx.weather)
          ? 80
          : 55
        : 0,
  },
  {
    id: 'species-striper-schooling',
    icon: 'people-outline',
    category: 'species',
    title: 'Stripers school in open water',
    text: (ctx) =>
      `Striped bass on ${ctx.name} chase shad schools in open water. Watch for surface busting and birds. Cast beyond the boil and retrieve through the school.`,
    match: (ctx, _wx, season) =>
      hasSpecies(ctx.species, 'striper', 'striped bass', 'hybrid')
        ? season === 'fall' || season === 'summer'
          ? 80
          : 55
        : 0,
  },
  {
    id: 'species-carp-corn',
    icon: 'nutrition-outline',
    category: 'species',
    title: 'Carp — patience and chum',
    text: (ctx) =>
      `Carp in ${ctx.name} feed on flats and muddy bottoms. Pre-bait an area with corn or boilies, then present a hair rig on a bolt setup.`,
    match: (ctx) => (hasSpecies(ctx.species, 'carp', 'common carp') ? 70 : 0),
  },

  // ── Structure tips ─────────────────────────────────────────────
  {
    id: 'structure-points',
    icon: 'git-merge-outline',
    category: 'structure',
    title: 'Fish underwater points',
    text: (ctx) =>
      `Points extending from shore on ${ctx.name} are natural ambush zones. Fish the tip and both sides at varying depths.`,
    match: (ctx) => (ctx.type === 'lake' || ctx.type === 'reservoir' ? 55 : 30),
  },
  {
    id: 'structure-creek-channel',
    icon: 'git-branch-outline',
    category: 'structure',
    title: 'Follow the creek channel',
    text: (ctx) =>
      `Old creek channels in ${ctx.name} act as underwater highways for fish movement. Ledges along channels hold bass, walleye, and catfish.`,
    match: (ctx) => (ctx.type === 'reservoir' ? 70 : ctx.type === 'lake' ? 50 : 0),
  },
  {
    id: 'structure-riprap',
    icon: 'cube-outline',
    category: 'structure',
    title: 'Riprap and rock walls',
    text: () =>
      `Rock walls, riprap along dams, and causeway bridges concentrate baitfish and crawfish. Parallel the rocks with crankbaits or jigs.`,
    match: (ctx) => (ctx.type === 'reservoir' || ctx.type === 'lake' ? 50 : 0),
  },
  {
    id: 'structure-river-eddies',
    icon: 'sync-outline',
    category: 'structure',
    title: 'River eddies and slack water',
    text: (ctx) =>
      `In ${ctx.name}, fish rest in eddies and slack water behind boulders, bridge pilings, and bends. Food collects in these seams.`,
    match: (ctx) =>
      ctx.type === 'river' || ctx.type === 'stream' || ctx.type === 'creek' ? 75 : 0,
  },
  {
    id: 'structure-submerged-timber',
    icon: 'leaf-outline',
    category: 'structure',
    title: 'Submerged timber holds fish',
    text: (ctx) =>
      `Standing timber and laydowns in ${ctx.name} provide ambush cover. Pitch jigs or Texas-rigged plastics tight to the wood.`,
    match: (ctx) => (ctx.type === 'reservoir' || ctx.type === 'lake' ? 55 : 0),
  },

  // ── Bait / presentation tips ───────────────────────────────────
  {
    id: 'bait-match-hatch',
    icon: 'color-palette-outline',
    category: 'bait',
    title: 'Match the hatch',
    text: () =>
      `Observe what baitfish, insects, or crawfish are present and match their size and color. Natural presentations outperform flashy ones in clear water.`,
    match: (_ctx, wx) => (isSunny(wx.weather) ? 55 : 40),
  },
  {
    id: 'bait-bright-murky',
    icon: 'color-wand-outline',
    category: 'bait',
    title: 'Bright colors for murky water',
    text: () =>
      `In stained or murky water, use chartreuse, orange, or white lures. Fish rely more on vibration and silhouette than color detail.`,
    match: (_ctx, wx) => (isRainy(wx.weather) || isOvercast(wx.weather) ? 60 : 0),
  },
  {
    id: 'bait-downsize-pressure',
    icon: 'resize-outline',
    category: 'bait',
    title: 'Downsize after high pressure',
    text: () =>
      `When fish get lockjaw from high pressure, downsize your bait. Finesse worms, small tubes, and Ned rigs can save a slow day.`,
    match: (_ctx, wx) => (wx.pressure > 30.3 && wx.pressureTrend !== 'falling' ? 70 : 0),
  },
  {
    id: 'bait-live-bait-cold',
    icon: 'pulse-outline',
    category: 'bait',
    title: 'Live bait excels in cold water',
    text: () =>
      `When water temps drop below 50\u00B0F, nothing outperforms live bait. Minnows, waxworms, or nightcrawlers fished slowly are most effective.`,
    match: (_ctx, wx) => ((wx.waterTemp ?? wx.airTemp) < 50 ? 75 : 0),
  },

  // ── Moon tips ──────────────────────────────────────────────────
  {
    id: 'moon-full',
    icon: 'moon-outline',
    category: 'moon',
    title: 'Full moon — night feeding',
    text: () =>
      `A full moon increases nighttime visibility, making fish feed more at night and less during the day. Adjust your timing accordingly.`,
    match: (_ctx, wx) => (wx.moonPhase && /full/i.test(wx.moonPhase) ? 70 : 0),
  },
  {
    id: 'moon-new',
    icon: 'ellipse-outline',
    category: 'moon',
    title: 'New moon — daytime bite improves',
    text: () =>
      `New moon means darker nights and more daytime feeding activity. Fish are often more aggressive during major solunar periods.`,
    match: (_ctx, wx) => (wx.moonPhase && /new/i.test(wx.moonPhase) ? 65 : 0),
  },

  // ── Water-type-specific tips ───────────────────────────────────
  {
    id: 'water-pond-shallow',
    icon: 'water-outline',
    category: 'water_type',
    title: 'Pond fishing — cover the whole thing',
    text: (ctx) =>
      `Ponds like ${ctx.name} are small enough to fish thoroughly. Start at one end and work methodically — don't skip any cover.`,
    match: (ctx) => (ctx.type === 'pond' ? 70 : 0),
  },
  {
    id: 'water-reservoir-dam',
    icon: 'business-outline',
    category: 'water_type',
    title: 'Reservoir dam = deep water access',
    text: (ctx) =>
      `The dam area of ${ctx.name} often holds the deepest water and concentrates baitfish near concrete structure. Fish riprap and the face.`,
    match: (ctx) => (ctx.type === 'reservoir' ? 65 : 0),
  },
  {
    id: 'water-river-seams',
    icon: 'git-compare-outline',
    category: 'water_type',
    title: 'River seams are fish magnets',
    text: (ctx) =>
      `Where fast and slow currents meet on ${ctx.name}, food collects and fish stack up. Cast into the seam and let your bait sweep across.`,
    match: (ctx) =>
      ctx.type === 'river' || ctx.type === 'stream' || ctx.type === 'creek' ? 80 : 0,
  },
  {
    id: 'water-creek-confluence',
    icon: 'git-network-outline',
    category: 'water_type',
    title: 'Creek confluences hold fish',
    text: (ctx) =>
      `Where tributaries enter ${ctx.name}, fresh oxygen and food attract baitfish and predators. These intersections are always worth checking.`,
    match: (ctx) => (ctx.type === 'lake' || ctx.type === 'reservoir' ? 60 : 0),
  },
];

// ── Name sanitization ────────────────────────────────────────────

/** Words that are just generic water body types, not real names. */
const GENERIC_NAMES = new Set([
  'lake', 'pond', 'reservoir', 'river', 'creek', 'stream',
  'bay', 'cove', 'canal', 'channel', 'slough', 'lagoon',
  'waterway', 'water', 'body of water',
]);

/**
 * Returns a display-safe name for a water body. If the name is
 * empty, null, undefined, or is just a generic type word, we
 * return a friendly fallback like "this lake" or "this spot".
 */
function sanitizeWaterbodyName(name: string | null | undefined, type: WaterbodyContext['type']): string {
  const trimmed = (name ?? '').trim();
  if (!trimmed || GENERIC_NAMES.has(trimmed.toLowerCase())) {
    // Use the type to produce a natural-sounding fallback
    if (type && type !== 'unknown') return `this ${type}`;
    return 'this spot';
  }
  return trimmed;
}

/** Whether the species list is populated with real data. */
function hasKnownSpecies(species: string[] | null | undefined): boolean {
  return Array.isArray(species) && species.length > 0;
}

/**
 * Categories whose templates reference specific species and should
 * only fire when we actually know what's in the water.
 */
const SPECIES_DEPENDENT_CATEGORIES: Set<TipCategory> = new Set(['species']);

/**
 * Template IDs that reference specific species even though they
 * live outside the 'species' category.
 */
const SPECIES_DEPENDENT_IDS = new Set([
  'pressure-dropping-bass',
  'season-spring-spawn',
  'season-spring-crappie',
  'season-fall-walleye',
  'tod-night-catfish',
  'wx-humidity-insects',
]);

// ── Public API ───────────────────────────────────────────────────

/**
 * Returns the 2-3 most relevant contextual fishing tips for the
 * given water body and current conditions.
 *
 * - Names that are empty or generic ("Lake", "Reservoir") are
 *   replaced with "this lake" / "this spot" so tips read naturally.
 * - Species-specific tips are suppressed when no species data is
 *   available (e.g. for OSM-discovered spots).
 */
export function getTipsForWaterbody(
  waterbody: WaterbodyContext,
  conditions: WeatherConditions,
): ContextualTip[] {
  const season = getSeason(waterbody.lat);
  const timeOfDay = getTimeOfDay(conditions.sunrise, conditions.sunset);

  // Build a sanitized context so templates never see a broken name
  const safeCtx: WaterbodyContext = {
    ...waterbody,
    name: sanitizeWaterbodyName(waterbody.name, waterbody.type),
    species: waterbody.species ?? [],
  };

  const speciesKnown = hasKnownSpecies(waterbody.species);

  const scored: ContextualTip[] = TEMPLATES
    .filter((tmpl) => {
      // Skip species-dependent templates when we have no species data
      if (!speciesKnown) {
        if (SPECIES_DEPENDENT_CATEGORIES.has(tmpl.category)) return false;
        if (SPECIES_DEPENDENT_IDS.has(tmpl.id)) return false;
      }
      return true;
    })
    .map((tmpl) => {
      const relevance = tmpl.match(safeCtx, conditions, season, timeOfDay);
      return {
        id: tmpl.id,
        icon: tmpl.icon,
        title: tmpl.title,
        text: tmpl.text(safeCtx),
        relevanceScore: relevance,
        category: tmpl.category,
      };
    })
    .filter((tip) => tip.relevanceScore > 0);

  // Sort by relevance descending, then pick top 3 from different categories
  scored.sort((a, b) => b.relevanceScore - a.relevanceScore);

  const result: ContextualTip[] = [];
  const usedCategories = new Set<TipCategory>();

  for (const tip of scored) {
    if (result.length >= 3) break;
    // Prefer diversity — skip if category already used (unless we have few results)
    if (usedCategories.has(tip.category) && scored.length > 5) continue;
    result.push(tip);
    usedCategories.add(tip.category);
  }

  // If we still have < 2, fill from remaining
  if (result.length < 2) {
    for (const tip of scored) {
      if (result.length >= 2) break;
      if (!result.some((r) => r.id === tip.id)) {
        result.push(tip);
      }
    }
  }

  return result;
}

/**
 * Infer a water body type from its name.
 */
export function inferWaterbodyType(name: string): WaterbodyContext['type'] {
  const lower = name.toLowerCase();
  if (/\blake\b/.test(lower)) return 'lake';
  if (/\breservoir\b/.test(lower)) return 'reservoir';
  if (/\bpond\b/.test(lower)) return 'pond';
  if (/\briver\b/.test(lower)) return 'river';
  if (/\bcreek\b/.test(lower)) return 'creek';
  if (/\bstream\b/.test(lower)) return 'stream';
  return 'unknown';
}
