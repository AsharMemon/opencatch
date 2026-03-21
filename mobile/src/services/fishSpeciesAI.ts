/**
 * OpenCatch — Fish Species AI Identification Service
 *
 * Provides fish species identification from photos using a lightweight
 * on-device classifier approach. For v1, uses a feature-matching system
 * with color/shape heuristics. Future: integrate TFLite or ONNX model.
 *
 * Also includes a comprehensive species database with images, habitats,
 * and identification tips for manual lookup.
 */

// ── Types ────────────────────────────────────────────────────────────────────

export interface FishSpecies {
  id: string;
  commonName: string;
  scientificName: string;
  family: string;
  /** Brief description for identification */
  description: string;
  /** Key visual features for identification */
  identifyingFeatures: string[];
  /** Typical length range in inches */
  typicalSizeIn: { min: number; max: number };
  /** Record size in pounds */
  recordLb?: number;
  /** Primary habitat types */
  habitat: string[];
  /** Geographic range description */
  range: string;
  /** Conservation status */
  status: 'common' | 'sport' | 'threatened' | 'endangered' | 'invasive';
  /** Emoji for quick visual reference */
  icon: string;
  /** Primary body color for color-matching */
  primaryColors: string[];
  /** Body shape category */
  bodyShape: 'elongated' | 'deep' | 'torpedo' | 'flat' | 'eel-like';
  /** Fun facts */
  funFacts?: string[];
}

export interface IdentificationResult {
  species: FishSpecies;
  confidence: number; // 0-1
  matchReason: string;
}

export interface IdentificationRequest {
  /** Base64-encoded image or URI */
  imageUri?: string;
  /** Manual feature hints to improve accuracy */
  hints?: {
    bodyColor?: string;
    bodyShape?: string;
    estimatedLengthIn?: number;
    location?: string;
    waterType?: 'freshwater' | 'saltwater' | 'brackish';
    habitat?: string;
  };
}

// ── Species Database ────────────────────────────────────────────────────────

const SPECIES_DATABASE: FishSpecies[] = [
  {
    id: 'largemouth-bass',
    commonName: 'Largemouth Bass',
    scientificName: 'Micropterus salmoides',
    family: 'Centrarchidae',
    description: 'The most popular freshwater game fish in North America. Dark green back with lighter sides and a distinctive dark lateral stripe.',
    identifyingFeatures: [
      'Upper jaw extends past the eye',
      'Dark horizontal stripe along the side',
      'Deep notch between dorsal fins',
      'Dark green to olive back, white belly',
      'Large mouth relative to body size',
    ],
    typicalSizeIn: { min: 10, max: 22 },
    recordLb: 22.4,
    habitat: ['Lakes', 'Ponds', 'Reservoirs', 'Slow rivers', 'Vegetation cover'],
    range: 'Native to eastern US/Canada; introduced throughout North America',
    status: 'sport',
    icon: '🐟',
    primaryColors: ['green', 'olive', 'dark green'],
    bodyShape: 'deep',
    funFacts: [
      'Males guard the nest for up to 2 weeks after spawning',
      'Can detect prey through their lateral line even in murky water',
      'The world record (22 lb 4 oz) has stood since 1932',
    ],
  },
  {
    id: 'smallmouth-bass',
    commonName: 'Smallmouth Bass',
    scientificName: 'Micropterus dolomieu',
    family: 'Centrarchidae',
    description: 'A bronze-colored fighter found in clear, rocky waters. Known as "pound for pound the best fighter in freshwater."',
    identifyingFeatures: [
      'Upper jaw does NOT extend past the eye',
      'Bronze/brown coloration with vertical bars',
      'Red/orange eye color',
      'No distinct lateral stripe (has bars instead)',
      'Smaller mouth than largemouth',
    ],
    typicalSizeIn: { min: 8, max: 20 },
    recordLb: 11.9,
    habitat: ['Rocky lakes', 'Clear streams', 'River pools', 'Rocky shorelines'],
    range: 'Native to Great Lakes region; introduced throughout US/Canada',
    status: 'sport',
    icon: '🐟',
    primaryColors: ['bronze', 'brown', 'golden'],
    bodyShape: 'deep',
    funFacts: [
      'Prefer water temperatures of 67-72°F',
      'Can jump up to 3 feet out of the water when hooked',
      'Often found near crayfish — their favorite food',
    ],
  },
  {
    id: 'walleye',
    commonName: 'Walleye',
    scientificName: 'Sander vitreus',
    family: 'Percidae',
    description: 'A prized eating fish with distinctive glassy eyes that glow in the dark. Gold to olive colored with a white-tipped lower tail.',
    identifyingFeatures: [
      'Large, glassy/opaque eyes (reflective)',
      'Sharp teeth/canines',
      'White tip on lower tail fin',
      'Gold to olive body with dark saddle markings',
      'Two separated dorsal fins',
    ],
    typicalSizeIn: { min: 12, max: 28 },
    recordLb: 25,
    habitat: ['Large lakes', 'Reservoirs', 'Rivers', 'Deep clear water'],
    range: 'Northern US and Canada; Great Lakes region primary',
    status: 'sport',
    icon: '🐠',
    primaryColors: ['gold', 'olive', 'yellow'],
    bodyShape: 'elongated',
    funFacts: [
      'Their eyes contain a reflective layer (tapetum lucidum) for night vision',
      'State fish of Minnesota, South Dakota, and Vermont',
      'Most active during low-light conditions (dawn, dusk, overcast)',
    ],
  },
  {
    id: 'rainbow-trout',
    commonName: 'Rainbow Trout',
    scientificName: 'Oncorhynchus mykiss',
    family: 'Salmonidae',
    description: 'Beautifully colored cold-water fish with a distinctive pink/red lateral stripe and spotted body.',
    identifyingFeatures: [
      'Pink/red stripe along the lateral line',
      'Black spots on body, dorsal fin, and tail',
      'Silver sides with olive/green back',
      'Adipose fin present (small fin between dorsal and tail)',
      'Square tail (not forked)',
    ],
    typicalSizeIn: { min: 8, max: 25 },
    recordLb: 48,
    habitat: ['Cold streams', 'Mountain lakes', 'Spring-fed rivers', 'Tailwaters'],
    range: 'Native to Pacific coast; stocked throughout North America',
    status: 'sport',
    icon: '🐟',
    primaryColors: ['silver', 'pink', 'olive'],
    bodyShape: 'torpedo',
    funFacts: [
      'Sea-run rainbow trout are called steelhead',
      'Can detect one part per million of a substance in water',
      'Native to rivers draining into the Pacific Ocean',
    ],
  },
  {
    id: 'brown-trout',
    commonName: 'Brown Trout',
    scientificName: 'Salmo trutta',
    family: 'Salmonidae',
    description: 'A wary, challenging trout with distinctive red/orange spots ringed with pale halos on a brown/golden body.',
    identifyingFeatures: [
      'Brown/golden body color',
      'Red and black spots with pale halos',
      'Adipose fin often with orange edge',
      'Fewer spots on tail than rainbow trout',
      'Square to slightly forked tail',
    ],
    typicalSizeIn: { min: 8, max: 24 },
    recordLb: 42.1,
    habitat: ['Cold streams', 'Spring creeks', 'Lakes', 'Tailwaters'],
    range: 'Introduced from Europe; found throughout North America',
    status: 'sport',
    icon: '🐟',
    primaryColors: ['brown', 'golden', 'olive'],
    bodyShape: 'torpedo',
    funFacts: [
      'Introduced to North America from Germany in 1883',
      'More tolerant of warm water than other trout species',
      'Notoriously difficult to catch — considered the "thinking angler\'s fish"',
    ],
  },
  {
    id: 'channel-catfish',
    commonName: 'Channel Catfish',
    scientificName: 'Ictalurus punctatus',
    family: 'Ictaluridae',
    description: 'The most abundant catfish species in North America. Identified by its deeply forked tail and scattered dark spots.',
    identifyingFeatures: [
      'Deeply forked tail (unlike flathead/blue)',
      'Scattered dark spots on sides (young fish)',
      'Eight whisker-like barbels around mouth',
      'Silvery to blue-gray body',
      'Adipose fin present',
    ],
    typicalSizeIn: { min: 12, max: 30 },
    recordLb: 58,
    habitat: ['Rivers', 'Lakes', 'Reservoirs', 'Ponds', 'Warm water'],
    range: 'Throughout US and southern Canada',
    status: 'common',
    icon: '🐡',
    primaryColors: ['gray', 'blue-gray', 'silver'],
    bodyShape: 'elongated',
    funFacts: [
      'Have about 100,000 taste buds covering their entire body',
      'Can "smell" prey from great distances in murky water',
      'Males guard and fan the eggs until they hatch',
    ],
  },
  {
    id: 'blue-catfish',
    commonName: 'Blue Catfish',
    scientificName: 'Ictalurus furcatus',
    family: 'Ictaluridae',
    description: 'The largest catfish species in North America. Blue-gray body with a forked tail and no spots.',
    identifyingFeatures: [
      'Blue-gray body with NO spots',
      'Forked tail',
      'Straight anal fin edge (30-36 rays)',
      'Larger than channel catfish',
      'Slate blue to white coloration',
    ],
    typicalSizeIn: { min: 15, max: 45 },
    recordLb: 143,
    habitat: ['Large rivers', 'Reservoirs', 'Deep channels'],
    range: 'Mississippi, Missouri, and Ohio River basins; introduced in East Coast rivers',
    status: 'sport',
    icon: '🐡',
    primaryColors: ['blue', 'blue-gray', 'slate'],
    bodyShape: 'elongated',
    funFacts: [
      'The largest blue catfish on record weighed 143 pounds',
      'Considered invasive in Chesapeake Bay tributaries',
      'Can live for 20+ years',
    ],
  },
  {
    id: 'crappie-black',
    commonName: 'Black Crappie',
    scientificName: 'Pomoxis nigromaculatus',
    family: 'Centrarchidae',
    description: 'A popular panfish with dark, irregular spots scattered across a silvery-green body.',
    identifyingFeatures: [
      'Irregular dark spots/blotches (not in rows)',
      '7-8 dorsal spines',
      'Compressed, deep body',
      'Silver-green to dark coloration',
      'Larger mouth than most panfish',
    ],
    typicalSizeIn: { min: 6, max: 14 },
    recordLb: 6,
    habitat: ['Lakes', 'Ponds', 'Reservoirs', 'Clear water with vegetation'],
    range: 'Throughout eastern and central North America',
    status: 'common',
    icon: '🐟',
    primaryColors: ['silver', 'green', 'black'],
    bodyShape: 'deep',
    funFacts: [
      'Also called "papermouth" due to their thin, delicate lips',
      'Prefer clearer water than white crappie',
      'Excellent table fare — one of the best-tasting freshwater fish',
    ],
  },
  {
    id: 'crappie-white',
    commonName: 'White Crappie',
    scientificName: 'Pomoxis annularis',
    family: 'Centrarchidae',
    description: 'Similar to black crappie but with dark bars arranged in vertical bands rather than scattered spots.',
    identifyingFeatures: [
      'Dark vertical bars/bands (not scattered spots)',
      '5-6 dorsal spines (fewer than black crappie)',
      'Lighter, more silvery body',
      'Slightly more elongated than black crappie',
    ],
    typicalSizeIn: { min: 6, max: 14 },
    recordLb: 5.3,
    habitat: ['Lakes', 'Reservoirs', 'Rivers', 'Murky water tolerant'],
    range: 'Throughout central and eastern North America',
    status: 'common',
    icon: '🐟',
    primaryColors: ['silver', 'white', 'light green'],
    bodyShape: 'deep',
  },
  {
    id: 'northern-pike',
    commonName: 'Northern Pike',
    scientificName: 'Esox lucius',
    family: 'Esocidae',
    description: 'A large, aggressive predator with a torpedo-shaped body and distinctive light spots on a dark green background.',
    identifyingFeatures: [
      'Long, torpedo-shaped body',
      'Duck-bill shaped snout with large teeth',
      'Light/bean-shaped spots on dark green body',
      'Dorsal fin set far back near tail',
      'Cheek fully scaled, lower half of gill cover unscaled',
    ],
    typicalSizeIn: { min: 18, max: 40 },
    recordLb: 46.1,
    habitat: ['Weedy lakes', 'Rivers', 'Bays', 'Marshes'],
    range: 'Northern US and throughout Canada',
    status: 'sport',
    icon: '🐊',
    primaryColors: ['dark green', 'green', 'olive'],
    bodyShape: 'torpedo',
    funFacts: [
      'Can strike prey at speeds up to 10 mph',
      'Have been known to eat small ducks and muskrats',
      'Nicknamed "water wolf" for their predatory behavior',
    ],
  },
  {
    id: 'muskellunge',
    commonName: 'Muskellunge',
    scientificName: 'Esox masquinongy',
    family: 'Esocidae',
    description: 'The largest member of the pike family. Called "the fish of 10,000 casts" for its difficulty to catch.',
    identifyingFeatures: [
      'Dark spots/bars on light body (opposite of pike)',
      'Pointed tail fin (not rounded)',
      'Sensory pores on lower jaw (6-9 per side)',
      'Extremely large — can exceed 50 inches',
      'Lower half of cheek AND gill cover unscaled',
    ],
    typicalSizeIn: { min: 28, max: 50 },
    recordLb: 67.5,
    habitat: ['Clear lakes', 'Large rivers', 'Weedy bays'],
    range: 'Great Lakes region, St. Lawrence River, upper Mississippi',
    status: 'sport',
    icon: '🐊',
    primaryColors: ['green', 'silver', 'brown'],
    bodyShape: 'torpedo',
    funFacts: [
      'Called "the fish of 10,000 casts" due to difficulty catching them',
      'State fish of Wisconsin',
      'Can live for 30+ years',
    ],
  },
  {
    id: 'bluegill',
    commonName: 'Bluegill',
    scientificName: 'Lepomis macrochirus',
    family: 'Centrarchidae',
    description: 'The most common sunfish in North America. Named for the distinctive blue-black gill flap.',
    identifyingFeatures: [
      'Dark blue-black gill flap (ear)',
      'Dark vertical bars on sides',
      'Blue/purple sheen on face',
      'Orange/yellow breast',
      'Deep, compressed body',
    ],
    typicalSizeIn: { min: 4, max: 10 },
    recordLb: 4.8,
    habitat: ['Ponds', 'Lakes', 'Slow streams', 'Near vegetation'],
    range: 'Throughout North America',
    status: 'common',
    icon: '🐟',
    primaryColors: ['blue', 'green', 'orange'],
    bodyShape: 'deep',
    funFacts: [
      'One of the first fish most anglers catch as children',
      'Males build and guard circular nests in shallow water',
      'Can hybridize with other sunfish species',
    ],
  },
  {
    id: 'yellow-perch',
    commonName: 'Yellow Perch',
    scientificName: 'Perca flavescens',
    family: 'Percidae',
    description: 'A common and delicious panfish with distinctive dark vertical bars on a yellow-gold body.',
    identifyingFeatures: [
      'Yellow/golden body with 6-8 dark vertical bars',
      'Two separated dorsal fins',
      'Orange/red pelvic and anal fins',
      'Spiny first dorsal fin',
      'Relatively small mouth',
    ],
    typicalSizeIn: { min: 5, max: 12 },
    recordLb: 4.3,
    habitat: ['Lakes', 'Ponds', 'Slow rivers', 'Schools in open water'],
    range: 'Northern US and Canada',
    status: 'common',
    icon: '🐠',
    primaryColors: ['yellow', 'gold', 'olive'],
    bodyShape: 'elongated',
    funFacts: [
      'Travel in large schools, often near the bottom',
      'Considered one of the best-tasting freshwater fish',
      'A favorite target for ice fishing',
    ],
  },
  {
    id: 'striped-bass',
    commonName: 'Striped Bass',
    scientificName: 'Morone saxatilis',
    family: 'Moronidae',
    description: 'A powerful anadromous fish with distinctive dark horizontal stripes running along a silver body.',
    identifyingFeatures: [
      '7-8 dark horizontal stripes on silver body',
      'Large mouth',
      'Two separate dorsal fins',
      'Deep body, especially in larger fish',
      'Forked tail',
    ],
    typicalSizeIn: { min: 18, max: 40 },
    recordLb: 81.9,
    habitat: ['Coastal waters', 'Rivers', 'Reservoirs', 'Estuaries'],
    range: 'Atlantic coast; landlocked populations in reservoirs nationwide',
    status: 'sport',
    icon: '🐟',
    primaryColors: ['silver', 'white', 'olive'],
    bodyShape: 'elongated',
    funFacts: [
      'Can live in both fresh and saltwater',
      'Migrate up rivers to spawn each spring',
      'State fish of Maryland, Rhode Island, South Carolina, and others',
    ],
  },
  {
    id: 'carp-common',
    commonName: 'Common Carp',
    scientificName: 'Cyprinus carpio',
    family: 'Cyprinidae',
    description: 'A large, golden-scaled fish originally from Asia. Powerful fighters that are increasingly popular with sport anglers.',
    identifyingFeatures: [
      'Large, golden/bronze scales',
      'Two barbels on each side of mouth',
      'Downturned, protrusible mouth',
      'Long dorsal fin',
      'Heavy, deep body',
    ],
    typicalSizeIn: { min: 15, max: 35 },
    recordLb: 75.1,
    habitat: ['Lakes', 'Rivers', 'Ponds', 'Warm shallow water'],
    range: 'Throughout North America (introduced)',
    status: 'invasive',
    icon: '🐟',
    primaryColors: ['gold', 'bronze', 'brown'],
    bodyShape: 'deep',
    funFacts: [
      'Introduced to North America in the 1800s',
      'Highly valued as a sport fish in Europe',
      'Can live for 20+ years and weigh over 50 pounds',
    ],
  },
  {
    id: 'lake-trout',
    commonName: 'Lake Trout',
    scientificName: 'Salvelinus namaycush',
    family: 'Salmonidae',
    description: 'The largest char species, found in deep, cold lakes. Light spots on a dark body with a deeply forked tail.',
    identifyingFeatures: [
      'Light spots on dark gray/green body',
      'Deeply forked tail',
      'Cream-colored leading edges on lower fins',
      'No red/pink coloring',
      'Worm-like markings (vermiculations) on back',
    ],
    typicalSizeIn: { min: 15, max: 35 },
    recordLb: 72,
    habitat: ['Deep cold lakes', 'Great Lakes', 'Canadian shield lakes'],
    range: 'Northern US and Canada',
    status: 'sport',
    icon: '🐟',
    primaryColors: ['gray', 'dark green', 'silver'],
    bodyShape: 'torpedo',
    funFacts: [
      'Can live in lakes with no other fish species',
      'Some populations are found at depths over 200 feet',
      'Can live for 40+ years in cold northern lakes',
    ],
  },
];

// ── Service Functions ────────────────────────────────────────────────────────

/**
 * Get all fish species in the database.
 */
export function getAllSpecies(): FishSpecies[] {
  return SPECIES_DATABASE;
}

/**
 * Get a species by its ID.
 */
export function getSpeciesById(id: string): FishSpecies | undefined {
  return SPECIES_DATABASE.find((s) => s.id === id);
}

/**
 * Search species by common name, scientific name, or family.
 */
export function searchSpecies(query: string): FishSpecies[] {
  const lower = query.toLowerCase().trim();
  if (!lower) return SPECIES_DATABASE;

  return SPECIES_DATABASE.filter(
    (s) =>
      s.commonName.toLowerCase().includes(lower) ||
      s.scientificName.toLowerCase().includes(lower) ||
      s.family.toLowerCase().includes(lower) ||
      s.description.toLowerCase().includes(lower),
  );
}

/**
 * Identify a fish species based on provided features/hints.
 *
 * This is a heuristic matcher for v1. Scores each species based on
 * how well the user's description matches known features.
 * Future: replace with TFLite/ONNX neural network for photo-based ID.
 */
export function identifyFromFeatures(request: IdentificationRequest): IdentificationResult[] {
  const hints = request.hints;
  if (!hints) {
    return SPECIES_DATABASE.map((s) => ({
      species: s,
      confidence: 0.1,
      matchReason: 'No features provided',
    }));
  }

  const scored: IdentificationResult[] = SPECIES_DATABASE.map((species) => {
    let score = 0;
    let maxScore = 0;
    const reasons: string[] = [];

    // Color matching (weight: 3)
    if (hints.bodyColor) {
      maxScore += 3;
      const colorLower = hints.bodyColor.toLowerCase();
      if (species.primaryColors.some((c) => c.includes(colorLower) || colorLower.includes(c))) {
        score += 3;
        reasons.push(`Color match: ${hints.bodyColor}`);
      }
    }

    // Body shape matching (weight: 3)
    if (hints.bodyShape) {
      maxScore += 3;
      if (species.bodyShape === hints.bodyShape.toLowerCase()) {
        score += 3;
        reasons.push(`Shape match: ${hints.bodyShape}`);
      }
    }

    // Size matching (weight: 2)
    if (hints.estimatedLengthIn) {
      maxScore += 2;
      const len = hints.estimatedLengthIn;
      if (len >= species.typicalSizeIn.min * 0.7 && len <= species.typicalSizeIn.max * 1.3) {
        score += 2;
        reasons.push(`Size match: ${len}" in typical range`);
      } else if (len >= species.typicalSizeIn.min * 0.5 && len <= species.typicalSizeIn.max * 1.5) {
        score += 1;
        reasons.push(`Size partially matches`);
      }
    }

    // Water type matching (weight: 2)
    if (hints.waterType) {
      maxScore += 2;
      const freshwaterSpecies = !species.habitat.some((h) =>
        h.toLowerCase().includes('coastal') || h.toLowerCase().includes('saltwater') || h.toLowerCase().includes('estuar'),
      );
      if (hints.waterType === 'freshwater' && freshwaterSpecies) {
        score += 2;
        reasons.push('Freshwater species');
      } else if (hints.waterType === 'saltwater' && !freshwaterSpecies) {
        score += 2;
        reasons.push('Saltwater/coastal species');
      }
    }

    // Habitat matching (weight: 1)
    if (hints.habitat) {
      maxScore += 1;
      const habitatLower = hints.habitat.toLowerCase();
      if (species.habitat.some((h) => h.toLowerCase().includes(habitatLower))) {
        score += 1;
        reasons.push(`Habitat match: ${hints.habitat}`);
      }
    }

    const confidence = maxScore > 0 ? Math.min(1, score / maxScore) : 0.1;

    return {
      species,
      confidence: Math.round(confidence * 100) / 100,
      matchReason: reasons.length > 0 ? reasons.join('; ') : 'Low match',
    };
  });

  // Sort by confidence descending
  return scored.sort((a, b) => b.confidence - a.confidence);
}

/**
 * Get species that are commonly found in a specific water type.
 */
export function getSpeciesByWaterType(waterType: 'freshwater' | 'saltwater'): FishSpecies[] {
  if (waterType === 'freshwater') {
    return SPECIES_DATABASE.filter(
      (s) => !s.habitat.some((h) => h.toLowerCase().includes('coastal') || h.toLowerCase().includes('saltwater')),
    );
  }
  return SPECIES_DATABASE.filter(
    (s) => s.habitat.some((h) => h.toLowerCase().includes('coastal') || h.toLowerCase().includes('estuar')),
  );
}

/**
 * Get species that match a size range.
 */
export function getSpeciesBySize(minInches: number, maxInches: number): FishSpecies[] {
  return SPECIES_DATABASE.filter(
    (s) => s.typicalSizeIn.max >= minInches && s.typicalSizeIn.min <= maxInches,
  );
}

/**
 * Get the top identifying features for telling apart similar species.
 */
export function getComparisonTips(speciesId1: string, speciesId2: string): string[] {
  const s1 = getSpeciesById(speciesId1);
  const s2 = getSpeciesById(speciesId2);
  if (!s1 || !s2) return [];

  const tips: string[] = [];
  tips.push(`${s1.commonName} vs ${s2.commonName}:`);

  // Compare features
  s1.identifyingFeatures.forEach((f) => {
    if (!s2.identifyingFeatures.some((f2) => f2.toLowerCase().includes(f.toLowerCase().split(' ')[0]))) {
      tips.push(`${s1.commonName}: ${f}`);
    }
  });
  s2.identifyingFeatures.forEach((f) => {
    if (!s1.identifyingFeatures.some((f2) => f2.toLowerCase().includes(f.toLowerCase().split(' ')[0]))) {
      tips.push(`${s2.commonName}: ${f}`);
    }
  });

  // Size comparison
  if (s1.typicalSizeIn.max !== s2.typicalSizeIn.max) {
    tips.push(
      `Size: ${s1.commonName} typically ${s1.typicalSizeIn.min}-${s1.typicalSizeIn.max}" vs ${s2.commonName} ${s2.typicalSizeIn.min}-${s2.typicalSizeIn.max}"`,
    );
  }

  return tips;
}

/**
 * Common confusion pairs — species that are often mistaken for each other.
 */
export const CONFUSION_PAIRS: Array<{ species1: string; species2: string; keyDifference: string }> = [
  {
    species1: 'largemouth-bass',
    species2: 'smallmouth-bass',
    keyDifference: 'Largemouth: jaw extends past eye, horizontal stripe. Smallmouth: jaw stops at eye, vertical bars.',
  },
  {
    species1: 'northern-pike',
    species2: 'muskellunge',
    keyDifference: 'Pike: light spots on dark body. Muskie: dark spots/bars on light body. Check jaw pores.',
  },
  {
    species1: 'crappie-black',
    species2: 'crappie-white',
    keyDifference: 'Black crappie: scattered spots, 7-8 dorsal spines. White crappie: vertical bars, 5-6 spines.',
  },
  {
    species1: 'channel-catfish',
    species2: 'blue-catfish',
    keyDifference: 'Channel cat: spots on sides, rounded anal fin. Blue cat: no spots, straight anal fin edge.',
  },
  {
    species1: 'rainbow-trout',
    species2: 'brown-trout',
    keyDifference: 'Rainbow: pink lateral stripe, black spots. Brown: red spots with halos, no pink stripe.',
  },
];
