/**
 * Catch Log Agent
 * Manages fishing catch records and provides analytics.
 */

// In-memory store (would use AsyncStorage or a database in production)
let catches = [
  { id: 1, species: 'Largemouth Bass', weight: 4.5, length: 18, location: 'Cedar Creek', date: '2026-03-15', lure: 'Plastic Worm', photo: null },
  { id: 2, species: 'Rainbow Trout', weight: 2.1, length: 14, location: 'Mill Creek', date: '2026-03-10', lure: 'Elk Hair Caddis', photo: null },
  { id: 3, species: 'Crappie', weight: 1.2, length: 11, location: 'Cedar Creek', date: '2026-03-08', lure: 'Small Jig', photo: null },
];

let nextId = 4;

export const catchLogAgent = {
  name: 'Catch Log',
  description: 'Records and analyzes fishing catches',

  async run(input) {
    const { action, data } = input || { action: 'list' };

    switch (action) {
      case 'add': {
        const newCatch = { id: nextId++, ...data, date: data?.date || new Date().toISOString().split('T')[0] };
        catches.push(newCatch);
        return { status: 'success', message: 'Catch logged!', catch: newCatch };
      }

      case 'list':
        return {
          status: 'success',
          catches: [...catches].reverse(),
          total: catches.length,
        };

      case 'stats': {
        const speciesCounts = {};
        let totalWeight = 0;
        catches.forEach((c) => {
          speciesCounts[c.species] = (speciesCounts[c.species] || 0) + 1;
          totalWeight += c.weight || 0;
        });
        return {
          status: 'success',
          stats: {
            totalCatches: catches.length,
            totalWeight: totalWeight.toFixed(1),
            speciesBreakdown: speciesCounts,
            topSpecies: Object.entries(speciesCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || 'N/A',
            biggestCatch: catches.reduce((max, c) => (c.weight > (max?.weight || 0) ? c : max), null),
          },
        };
      }

      default:
        return { status: 'success', catches: [...catches].reverse(), total: catches.length };
    }
  },
};
