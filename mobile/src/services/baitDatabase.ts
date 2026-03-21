/**
 * OpenCatch — Bait & Lure Recommendation Database
 *
 * Provides species-specific bait and lure recommendations based on:
 * - Target species
 * - Season / water temperature
 * - Water clarity
 * - Time of day
 * - Structure type
 *
 * Data sourced from state DNR fishing guides and expert angler knowledge.
 */

// ── Types ────────────────────────────────────────────────────────────────────

export type Season = 'spring' | 'summer' | 'fall' | 'winter';
export type WaterClarity = 'clear' | 'stained' | 'muddy';
export type TimeOfDay = 'morning' | 'midday' | 'evening' | 'night';
export type Presentation = 'topwater' | 'shallow' | 'mid-depth' | 'deep' | 'bottom';

export interface BaitRecommendation {
  name: string;
  type: 'live' | 'artificial' | 'fly';
  category: string;       // "Soft Plastic", "Crankbait", "Jig", etc.
  color: string;           // Recommended color pattern
  presentation: Presentation;
  confidence: number;      // 0-1 match quality
  tip?: string;            // Angler tip
}

export interface SpeciesProfile {
  id: string;
  commonName: string;
  scientificName: string;
  family: string;
  icon: string;             // Ionicon name
  preferredTemp: { min: number; max: number };  // °F
  preferredDepth: { min: number; max: number };  // feet
  spawnTemp: { min: number; max: number };       // °F spawn trigger range
  diet: string[];
  habitat: string[];
  baits: SeasonalBaits;
}

export interface SeasonalBaits {
  spring: BaitRecommendation[];
  summer: BaitRecommendation[];
  fall: BaitRecommendation[];
  winter: BaitRecommendation[];
}

// ── Species Database ─────────────────────────────────────────────────────────

export const SPECIES_DB: SpeciesProfile[] = [
  {
    id: 'largemouth-bass',
    commonName: 'Largemouth Bass',
    scientificName: 'Micropterus salmoides',
    family: 'Centrarchidae',
    icon: 'fish-outline',
    preferredTemp: { min: 65, max: 85 },
    preferredDepth: { min: 2, max: 20 },
    spawnTemp: { min: 60, max: 70 },
    diet: ['shad', 'bluegill', 'crayfish', 'frogs', 'worms'],
    habitat: ['vegetation', 'docks', 'laydowns', 'points', 'creek channels'],
    baits: {
      spring: [
        { name: 'Jerkbait', type: 'artificial', category: 'Hard Bait', color: 'Natural Shad', presentation: 'shallow', confidence: 0.9, tip: 'Pause 3-5 seconds between twitches near spawning flats' },
        { name: 'Senko (wacky rig)', type: 'artificial', category: 'Soft Plastic', color: 'Green Pumpkin', presentation: 'shallow', confidence: 0.95, tip: 'Wacky rig and let fall naturally along dock posts' },
        { name: 'Lipless Crankbait', type: 'artificial', category: 'Crankbait', color: 'Red Crawfish', presentation: 'shallow', confidence: 0.85, tip: 'Yo-yo retrieve over emerging grass beds' },
        { name: 'Spinnerbait', type: 'artificial', category: 'Spinnerbait', color: 'White/Chartreuse', presentation: 'shallow', confidence: 0.8, tip: 'Slow roll along transition banks' },
        { name: 'Live Shiners', type: 'live', category: 'Live Bait', color: 'Natural', presentation: 'shallow', confidence: 0.9, tip: 'Free-line near vegetation edges' },
      ],
      summer: [
        { name: 'Texas Rig Creature Bait', type: 'artificial', category: 'Soft Plastic', color: 'Black/Blue', presentation: 'deep', confidence: 0.9, tip: 'Flip into heavy cover — docks, brush piles, matted grass' },
        { name: 'Deep Diving Crankbait', type: 'artificial', category: 'Crankbait', color: 'Sexy Shad', presentation: 'deep', confidence: 0.85, tip: 'Target 10-15ft ledges along creek channels' },
        { name: 'Topwater Frog', type: 'artificial', category: 'Topwater', color: 'Black', presentation: 'topwater', confidence: 0.85, tip: 'Walk across lily pads and matted vegetation at dawn/dusk' },
        { name: 'Drop Shot', type: 'artificial', category: 'Finesse', color: 'Morning Dawn', presentation: 'deep', confidence: 0.8, tip: 'Suspend above brush on deep points during midday' },
        { name: 'Buzzbait', type: 'artificial', category: 'Topwater', color: 'Black/Red', presentation: 'topwater', confidence: 0.75, tip: 'First light along shallow banks — keep it moving' },
      ],
      fall: [
        { name: 'Squarebill Crankbait', type: 'artificial', category: 'Crankbait', color: 'Chartreuse/Black Back', presentation: 'shallow', confidence: 0.9, tip: 'Crank through rock transitions as bass follow shad' },
        { name: 'Swimbait', type: 'artificial', category: 'Swimbait', color: 'Bluegill', presentation: 'mid-depth', confidence: 0.85, tip: 'Steady retrieve along creek channels where shad school' },
        { name: 'Spinnerbait', type: 'artificial', category: 'Spinnerbait', color: 'White', presentation: 'shallow', confidence: 0.85, tip: 'Burn it fast over shallow flats where shad are balling' },
        { name: 'Jig & Trailer', type: 'artificial', category: 'Jig', color: 'PB&J', presentation: 'bottom', confidence: 0.8, tip: 'Slow drag on main lake points' },
        { name: 'Shad-colored Jerkbait', type: 'artificial', category: 'Hard Bait', color: 'Ghost Minnow', presentation: 'shallow', confidence: 0.8, tip: 'Twitch and pause near bluff walls' },
      ],
      winter: [
        { name: 'Blade Bait', type: 'artificial', category: 'Metal', color: 'Silver', presentation: 'deep', confidence: 0.9, tip: 'Lift and drop on deep structure — 20-30ft' },
        { name: 'Jerkbait (suspending)', type: 'artificial', category: 'Hard Bait', color: 'Table Rock Shad', presentation: 'mid-depth', confidence: 0.85, tip: 'Long pauses (10-15 sec) in 40-50°F water' },
        { name: 'Football Jig', type: 'artificial', category: 'Jig', color: 'Green Pumpkin', presentation: 'deep', confidence: 0.8, tip: 'Drag slowly on rocky points and ledges' },
        { name: 'Ned Rig', type: 'artificial', category: 'Finesse', color: 'TRD Green Pumpkin', presentation: 'bottom', confidence: 0.85, tip: 'Subtle hops on gravel/rock transitions' },
        { name: 'Alabama Rig', type: 'artificial', category: 'Umbrella', color: 'Shad', presentation: 'mid-depth', confidence: 0.75, tip: 'Slow roll along channel swings (check local legality)' },
      ],
    },
  },
  {
    id: 'smallmouth-bass',
    commonName: 'Smallmouth Bass',
    scientificName: 'Micropterus dolomieu',
    family: 'Centrarchidae',
    icon: 'fish-outline',
    preferredTemp: { min: 60, max: 78 },
    preferredDepth: { min: 5, max: 30 },
    spawnTemp: { min: 58, max: 65 },
    diet: ['crayfish', 'minnows', 'hellgrammites', 'gobies', 'insects'],
    habitat: ['rocky points', 'bluffs', 'current breaks', 'gravel bars', 'boulder fields'],
    baits: {
      spring: [
        { name: 'Tube Jig', type: 'artificial', category: 'Soft Plastic', color: 'Smoke/Green Pumpkin', presentation: 'bottom', confidence: 0.95, tip: 'Hop along rocky shorelines as fish move shallow to spawn' },
        { name: 'Ned Rig', type: 'artificial', category: 'Finesse', color: 'Green Pumpkin', presentation: 'bottom', confidence: 0.9, tip: 'Subtle presentation on gravel spawning flats' },
        { name: 'Live Crawfish', type: 'live', category: 'Live Bait', color: 'Natural', presentation: 'bottom', confidence: 0.9, tip: 'Hook through tail, let walk on rocky bottom' },
        { name: 'Jerkbait', type: 'artificial', category: 'Hard Bait', color: 'Perch', presentation: 'shallow', confidence: 0.8 },
      ],
      summer: [
        { name: 'Drop Shot', type: 'artificial', category: 'Finesse', color: 'Goby', presentation: 'deep', confidence: 0.95, tip: 'Key technique for deep summer smallmouth on Great Lakes' },
        { name: 'Topwater Walking Bait', type: 'artificial', category: 'Topwater', color: 'Bone', presentation: 'topwater', confidence: 0.85, tip: 'Early morning on calm rock flats — explosive strikes' },
        { name: 'Swimbait', type: 'artificial', category: 'Swimbait', color: 'Alewife', presentation: 'mid-depth', confidence: 0.8 },
        { name: 'Hair Jig', type: 'artificial', category: 'Jig', color: 'Brown/Orange', presentation: 'bottom', confidence: 0.85, tip: 'Drag on deep gravel transitions' },
      ],
      fall: [
        { name: 'Crankbait', type: 'artificial', category: 'Crankbait', color: 'Crawfish', presentation: 'mid-depth', confidence: 0.9, tip: 'Crank rocky points as fish transition deeper' },
        { name: 'Tube Jig', type: 'artificial', category: 'Soft Plastic', color: 'Smoke', presentation: 'deep', confidence: 0.85 },
        { name: 'Spinnerbait', type: 'artificial', category: 'Spinnerbait', color: 'White/Silver', presentation: 'mid-depth', confidence: 0.75 },
        { name: 'Jerkbait', type: 'artificial', category: 'Hard Bait', color: 'Natural', presentation: 'shallow', confidence: 0.8 },
      ],
      winter: [
        { name: 'Blade Bait', type: 'artificial', category: 'Metal', color: 'Silver', presentation: 'deep', confidence: 0.9, tip: 'Vertical jigging on deep structure' },
        { name: 'Hair Jig', type: 'artificial', category: 'Jig', color: 'Brown', presentation: 'deep', confidence: 0.85 },
        { name: 'Drop Shot', type: 'artificial', category: 'Finesse', color: 'Smoke', presentation: 'deep', confidence: 0.85 },
      ],
    },
  },
  {
    id: 'walleye',
    commonName: 'Walleye',
    scientificName: 'Sander vitreus',
    family: 'Percidae',
    icon: 'fish-outline',
    preferredTemp: { min: 55, max: 72 },
    preferredDepth: { min: 8, max: 40 },
    spawnTemp: { min: 42, max: 50 },
    diet: ['perch', 'shad', 'minnows', 'leeches', 'nightcrawlers'],
    habitat: ['rocky reefs', 'gravel bars', 'weed edges', 'current areas', 'mud flats'],
    baits: {
      spring: [
        { name: 'Jig & Minnow', type: 'live', category: 'Jig', color: 'Chartreuse/White', presentation: 'bottom', confidence: 0.95, tip: 'Vertical jig near dam tailwaters during spawn run' },
        { name: 'Jig & Twister Tail', type: 'artificial', category: 'Jig', color: 'Chartreuse', presentation: 'bottom', confidence: 0.85 },
        { name: 'Crawler Harness', type: 'live', category: 'Live Rig', color: 'Chartreuse Blades', presentation: 'bottom', confidence: 0.8, tip: 'Slow troll along gravel transitions' },
      ],
      summer: [
        { name: 'Live Leech (Lindy Rig)', type: 'live', category: 'Live Rig', color: 'Natural', presentation: 'bottom', confidence: 0.9, tip: 'Drift Lindy rigs over mid-lake structure' },
        { name: 'Crawler Harness', type: 'live', category: 'Live Rig', color: 'Gold/Chartreuse', presentation: 'bottom', confidence: 0.9, tip: 'Troll at 1.2 mph along weed edges' },
        { name: 'Deep Crankbait', type: 'artificial', category: 'Crankbait', color: 'Firetiger', presentation: 'deep', confidence: 0.8, tip: 'Troll along deep break lines' },
        { name: 'Slip Bobber & Leech', type: 'live', category: 'Live Rig', color: 'Natural', presentation: 'mid-depth', confidence: 0.85, tip: 'Set depth just above weed tops at dusk' },
      ],
      fall: [
        { name: 'Jig & Minnow', type: 'live', category: 'Jig', color: 'Orange/Chartreuse', presentation: 'deep', confidence: 0.9, tip: 'Work deep structure as fish stage for fall feed-up' },
        { name: 'Crankbait', type: 'artificial', category: 'Crankbait', color: 'Perch', presentation: 'mid-depth', confidence: 0.85 },
        { name: 'Blade Bait', type: 'artificial', category: 'Metal', color: 'Silver', presentation: 'deep', confidence: 0.8 },
      ],
      winter: [
        { name: 'Jigging Rap', type: 'artificial', category: 'Jigging', color: 'Glow', presentation: 'deep', confidence: 0.9, tip: 'Ice fishing — aggressive lift-drop near bottom' },
        { name: 'Tip-up with Minnow', type: 'live', category: 'Live Rig', color: 'Natural', presentation: 'mid-depth', confidence: 0.85, tip: 'Set at 2/3 depth near structure' },
        { name: 'Spoon', type: 'artificial', category: 'Metal', color: 'Gold', presentation: 'deep', confidence: 0.8 },
      ],
    },
  },
  {
    id: 'rainbow-trout',
    commonName: 'Rainbow Trout',
    scientificName: 'Oncorhynchus mykiss',
    family: 'Salmonidae',
    icon: 'fish-outline',
    preferredTemp: { min: 50, max: 65 },
    preferredDepth: { min: 3, max: 15 },
    spawnTemp: { min: 42, max: 52 },
    diet: ['insects', 'nymphs', 'worms', 'minnows', 'salmon eggs', 'crayfish'],
    habitat: ['riffles', 'runs', 'pools', 'spring-fed streams', 'cold tailwaters'],
    baits: {
      spring: [
        { name: 'PowerBait', type: 'artificial', category: 'Dough Bait', color: 'Rainbow', presentation: 'bottom', confidence: 0.85, tip: 'Stocked trout love it — float off bottom with marshmallow' },
        { name: 'Nightcrawler', type: 'live', category: 'Live Bait', color: 'Natural', presentation: 'bottom', confidence: 0.9, tip: 'Thread on hook, drift through runs' },
        { name: 'Pheasant Tail Nymph (#14)', type: 'fly', category: 'Nymph', color: 'Natural', presentation: 'mid-depth', confidence: 0.9, tip: 'Dead drift under indicator through riffle tails' },
        { name: 'Rooster Tail Spinner', type: 'artificial', category: 'Inline Spinner', color: 'Gold/Black', presentation: 'mid-depth', confidence: 0.8 },
      ],
      summer: [
        { name: 'Elk Hair Caddis (#16)', type: 'fly', category: 'Dry Fly', color: 'Tan', presentation: 'topwater', confidence: 0.9, tip: 'Evening caddis hatch — dead drift or skitter' },
        { name: 'Woolly Bugger (#8)', type: 'fly', category: 'Streamer', color: 'Olive', presentation: 'mid-depth', confidence: 0.85, tip: 'Strip through deeper pools in morning' },
        { name: 'Small Spinner', type: 'artificial', category: 'Inline Spinner', color: 'Silver', presentation: 'mid-depth', confidence: 0.8, tip: 'Cast upstream and retrieve with current' },
        { name: 'Live Crickets', type: 'live', category: 'Live Bait', color: 'Natural', presentation: 'topwater', confidence: 0.75 },
      ],
      fall: [
        { name: 'Egg Pattern (#12)', type: 'fly', category: 'Egg', color: 'Peach/Orange', presentation: 'bottom', confidence: 0.9, tip: 'Below spawning salmon — dead drift through runs' },
        { name: 'Streamer', type: 'fly', category: 'Streamer', color: 'Sculpin', presentation: 'deep', confidence: 0.85, tip: 'Strip along cut banks and undercuts' },
        { name: 'Spinner', type: 'artificial', category: 'Inline Spinner', color: 'Gold/Firetiger', presentation: 'mid-depth', confidence: 0.8 },
      ],
      winter: [
        { name: 'Nymph (Hare\'s Ear #16)', type: 'fly', category: 'Nymph', color: 'Natural', presentation: 'bottom', confidence: 0.85, tip: 'Tiny nymphs, dead drift, very slow water' },
        { name: 'PowerBait', type: 'artificial', category: 'Dough Bait', color: 'Chartreuse', presentation: 'bottom', confidence: 0.8, tip: 'Stocked lake trout in winter — fish slow' },
        { name: 'Small Jig', type: 'artificial', category: 'Jig', color: 'White/Pink', presentation: 'deep', confidence: 0.75 },
      ],
    },
  },
  {
    id: 'channel-catfish',
    commonName: 'Channel Catfish',
    scientificName: 'Ictalurus punctatus',
    family: 'Ictaluridae',
    icon: 'fish-outline',
    preferredTemp: { min: 70, max: 85 },
    preferredDepth: { min: 5, max: 30 },
    spawnTemp: { min: 75, max: 85 },
    diet: ['cut bait', 'worms', 'stink bait', 'chicken liver', 'shad', 'crawfish'],
    habitat: ['channel edges', 'holes', 'rip-rap', 'dam tailwaters', 'creek mouths'],
    baits: {
      spring: [
        { name: 'Cut Shad', type: 'live', category: 'Cut Bait', color: 'Natural', presentation: 'bottom', confidence: 0.9, tip: 'Fish on bottom near channel bends as water warms' },
        { name: 'Nightcrawler', type: 'live', category: 'Live Bait', color: 'Natural', presentation: 'bottom', confidence: 0.85 },
        { name: 'Chicken Liver', type: 'live', category: 'Prepared Bait', color: 'Natural', presentation: 'bottom', confidence: 0.8, tip: 'Wrap in mesh to keep on hook' },
      ],
      summer: [
        { name: 'Punch Bait / Stink Bait', type: 'artificial', category: 'Prepared Bait', color: 'N/A', presentation: 'bottom', confidence: 0.9, tip: 'Peak catfishing — hot temps = peak activity' },
        { name: 'Live Bluegill (where legal)', type: 'live', category: 'Live Bait', color: 'Natural', presentation: 'bottom', confidence: 0.9, tip: 'Trophy flatheads — hook through back' },
        { name: 'Cut Shad', type: 'live', category: 'Cut Bait', color: 'Natural', presentation: 'bottom', confidence: 0.85 },
        { name: 'Hot Dogs', type: 'live', category: 'Prepared Bait', color: 'N/A', presentation: 'bottom', confidence: 0.7, tip: 'Surprisingly effective for stocked channel cats' },
      ],
      fall: [
        { name: 'Cut Shad', type: 'live', category: 'Cut Bait', color: 'Natural', presentation: 'bottom', confidence: 0.9, tip: 'Feed-up period before winter — target creek mouths' },
        { name: 'Nightcrawler', type: 'live', category: 'Live Bait', color: 'Natural', presentation: 'bottom', confidence: 0.85 },
      ],
      winter: [
        { name: 'Cut Shad', type: 'live', category: 'Cut Bait', color: 'Natural', presentation: 'deep', confidence: 0.8, tip: 'Fish deep holes — slow bite but trophy potential' },
        { name: 'Blood Bait', type: 'live', category: 'Prepared Bait', color: 'N/A', presentation: 'deep', confidence: 0.7 },
      ],
    },
  },
  {
    id: 'crappie',
    commonName: 'Crappie',
    scientificName: 'Pomoxis spp.',
    family: 'Centrarchidae',
    icon: 'fish-outline',
    preferredTemp: { min: 55, max: 75 },
    preferredDepth: { min: 5, max: 25 },
    spawnTemp: { min: 56, max: 65 },
    diet: ['minnows', 'insects', 'small shad', 'zooplankton'],
    habitat: ['brush piles', 'standing timber', 'dock pilings', 'bridge pilings', 'weed edges'],
    baits: {
      spring: [
        { name: 'Jig & Minnow (1/16 oz)', type: 'live', category: 'Jig', color: 'Chartreuse/White', presentation: 'shallow', confidence: 0.95, tip: 'Key combo for spawning crappie in 2-6ft around brush' },
        { name: 'Bobby Garland Baby Shad', type: 'artificial', category: 'Soft Plastic', color: 'Monkey Milk', presentation: 'shallow', confidence: 0.9, tip: 'Slow swim past brush piles' },
        { name: 'Slip Float & Minnow', type: 'live', category: 'Live Rig', color: 'Natural', presentation: 'mid-depth', confidence: 0.9, tip: 'Suspend minnow at exact depth over brush tops' },
      ],
      summer: [
        { name: 'Trolling Crankbait', type: 'artificial', category: 'Crankbait', color: 'Chartreuse', presentation: 'deep', confidence: 0.85, tip: 'Spider rig at 0.8 mph over deep brush' },
        { name: 'Jig (1/32 oz)', type: 'artificial', category: 'Jig', color: 'Electric Chicken', presentation: 'deep', confidence: 0.8, tip: 'Vertical jig deep brush piles using electronics' },
        { name: 'Live Minnow', type: 'live', category: 'Live Bait', color: 'Natural', presentation: 'deep', confidence: 0.85 },
      ],
      fall: [
        { name: 'Jig & Minnow', type: 'live', category: 'Jig', color: 'Pink/White', presentation: 'mid-depth', confidence: 0.9, tip: 'Follow shad schools — crappie follow the bait' },
        { name: 'Road Runner Jig', type: 'artificial', category: 'Bladed Jig', color: 'White', presentation: 'mid-depth', confidence: 0.8 },
      ],
      winter: [
        { name: 'Jig (1/16 oz)', type: 'artificial', category: 'Jig', color: 'Blue/Chartreuse', presentation: 'deep', confidence: 0.85, tip: 'Suspend jig 6 inches above brush tops — barely move it' },
        { name: 'Live Minnow', type: 'live', category: 'Live Bait', color: 'Natural', presentation: 'deep', confidence: 0.9, tip: 'Tight-line minnow in deep timber' },
      ],
    },
  },
];

// ── Recommendation Engine ────────────────────────────────────────────────────

/**
 * Get the current season based on month and latitude.
 */
export function getCurrentSeason(lat?: number): Season {
  const month = new Date().getMonth(); // 0-11
  // Adjust for southern hemisphere (not applicable for NA but future-proof)
  const isNorth = !lat || lat >= 0;

  if (isNorth) {
    if (month >= 2 && month <= 4) return 'spring';
    if (month >= 5 && month <= 7) return 'summer';
    if (month >= 8 && month <= 10) return 'fall';
    return 'winter';
  } else {
    if (month >= 2 && month <= 4) return 'fall';
    if (month >= 5 && month <= 7) return 'winter';
    if (month >= 8 && month <= 10) return 'spring';
    return 'summer';
  }
}

/**
 * Get bait recommendations for a species and current conditions.
 */
export function getRecommendations(
  speciesId: string,
  options?: {
    season?: Season;
    waterTemp?: number;
    waterClarity?: WaterClarity;
    timeOfDay?: TimeOfDay;
    lat?: number;
  },
): BaitRecommendation[] {
  const species = SPECIES_DB.find((s) => s.id === speciesId);
  if (!species) return [];

  const season = options?.season ?? getCurrentSeason(options?.lat);
  let baits = [...species.baits[season]];

  // Adjust confidence based on water temperature
  if (options?.waterTemp !== undefined) {
    const temp = options.waterTemp;
    const { min, max } = species.preferredTemp;
    const tempFactor = temp >= min && temp <= max ? 1.0 : 0.7;
    baits = baits.map((b) => ({ ...b, confidence: b.confidence * tempFactor }));
  }

  // Adjust for water clarity
  if (options?.waterClarity === 'muddy') {
    baits = baits.map((b) => ({
      ...b,
      confidence: b.confidence * (b.color.toLowerCase().includes('chartreuse') || b.type === 'live' ? 1.1 : 0.8),
    }));
  } else if (options?.waterClarity === 'clear') {
    baits = baits.map((b) => ({
      ...b,
      confidence: b.confidence * (b.category === 'Finesse' || b.type === 'fly' ? 1.1 : 0.95),
    }));
  }

  // Adjust for time of day
  if (options?.timeOfDay === 'night') {
    baits = baits.map((b) => ({
      ...b,
      confidence: b.confidence * (b.presentation === 'topwater' ? 0.5 : b.color.toLowerCase().includes('black') ? 1.2 : 1.0),
    }));
  } else if (options?.timeOfDay === 'midday') {
    baits = baits.map((b) => ({
      ...b,
      confidence: b.confidence * (b.presentation === 'deep' ? 1.1 : b.presentation === 'topwater' ? 0.7 : 1.0),
    }));
  }

  // Normalize confidence to 0-1
  baits = baits.map((b) => ({
    ...b,
    confidence: Math.min(1, Math.max(0, b.confidence)),
  }));

  // Sort by confidence
  return baits.sort((a, b) => b.confidence - a.confidence);
}

/**
 * Get all species profiles.
 */
export function getAllSpecies(): SpeciesProfile[] {
  return SPECIES_DB;
}

/**
 * Get species by ID.
 */
export function getSpecies(id: string): SpeciesProfile | undefined {
  return SPECIES_DB.find((s) => s.id === id);
}

/**
 * Get recommended species for a given water temperature.
 */
export function getSpeciesForTemp(waterTempF: number): SpeciesProfile[] {
  return SPECIES_DB
    .filter((s) => waterTempF >= s.preferredTemp.min - 5 && waterTempF <= s.preferredTemp.max + 5)
    .sort((a, b) => {
      const aMid = (a.preferredTemp.min + a.preferredTemp.max) / 2;
      const bMid = (b.preferredTemp.min + b.preferredTemp.max) / 2;
      return Math.abs(waterTempF - aMid) - Math.abs(waterTempF - bMid);
    });
}
