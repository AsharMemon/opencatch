/**
 * Fish Identification Agent
 * Identifies fish species from images and provides details.
 */

// Common freshwater and saltwater fish database
const FISH_DATABASE = [
  { id: 1, name: 'Largemouth Bass', family: 'Centrarchidae', habitat: 'freshwater', avgWeight: '4-10 lbs', season: 'spring-fall', tips: 'Use plastic worms or crankbaits near cover' },
  { id: 2, name: 'Rainbow Trout', family: 'Salmonidae', habitat: 'freshwater', avgWeight: '2-8 lbs', season: 'year-round', tips: 'Fly fishing with nymphs or dry flies works best' },
  { id: 3, name: 'Bluegill', family: 'Centrarchidae', habitat: 'freshwater', avgWeight: '0.5-1 lb', season: 'spring-summer', tips: 'Small jigs or live worms under a bobber' },
  { id: 4, name: 'Channel Catfish', family: 'Ictaluridae', habitat: 'freshwater', avgWeight: '5-15 lbs', season: 'summer', tips: 'Stink bait or cut bait on the bottom' },
  { id: 5, name: 'Walleye', family: 'Percidae', habitat: 'freshwater', avgWeight: '3-8 lbs', season: 'spring-fall', tips: 'Jig with minnow or crawler harness' },
  { id: 6, name: 'Striped Bass', family: 'Moronidae', habitat: 'saltwater/freshwater', avgWeight: '10-30 lbs', season: 'spring-fall', tips: 'Trolling with umbrella rigs or live bait' },
  { id: 7, name: 'Red Drum', family: 'Sciaenidae', habitat: 'saltwater', avgWeight: '5-20 lbs', season: 'fall', tips: 'Gold spoons or cut mullet in shallow flats' },
  { id: 8, name: 'Mahi-Mahi', family: 'Coryphaenidae', habitat: 'saltwater', avgWeight: '10-30 lbs', season: 'summer', tips: 'Trolling with ballyhoo or feather lures' },
  { id: 9, name: 'Crappie', family: 'Centrarchidae', habitat: 'freshwater', avgWeight: '0.5-2 lbs', season: 'spring', tips: 'Small minnows or jigs around brush piles' },
  { id: 10, name: 'Northern Pike', family: 'Esocidae', habitat: 'freshwater', avgWeight: '5-15 lbs', season: 'spring-fall', tips: 'Large spinnerbaits or spoons near weed edges' },
];

export const fishIdentificationAgent = {
  name: 'Fish Identification',
  description: 'Identifies fish species and provides fishing tips',

  async run(input) {
    const { query, imageUri } = input;

    if (imageUri) {
      // In a production app, this would call a vision API
      return {
        status: 'image_received',
        message: 'Image analysis would require a connected vision API. Try searching by name instead.',
        suggestions: FISH_DATABASE.slice(0, 3),
      };
    }

    if (query) {
      const q = query.toLowerCase();
      const matches = FISH_DATABASE.filter(
        (fish) =>
          fish.name.toLowerCase().includes(q) ||
          fish.family.toLowerCase().includes(q) ||
          fish.habitat.toLowerCase().includes(q)
      );
      return {
        status: 'success',
        results: matches.length > 0 ? matches : FISH_DATABASE,
        query,
      };
    }

    return {
      status: 'success',
      results: FISH_DATABASE,
    };
  },
};
