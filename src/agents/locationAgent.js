/**
 * Location Agent
 * Recommends fishing spots and analyzes locations.
 */

const SAMPLE_SPOTS = [
  { id: 1, name: 'Cedar Creek Reservoir', type: 'lake', species: ['Largemouth Bass', 'Crappie', 'Catfish'], rating: 4.5, distance: '12 mi' },
  { id: 2, name: 'Mill Creek', type: 'stream', species: ['Rainbow Trout', 'Brown Trout'], rating: 4.2, distance: '8 mi' },
  { id: 3, name: 'Eagle Point Marina', type: 'marina', species: ['Striped Bass', 'Walleye'], rating: 4.0, distance: '15 mi' },
  { id: 4, name: 'Hidden Pond', type: 'pond', species: ['Bluegill', 'Largemouth Bass'], rating: 3.8, distance: '3 mi' },
  { id: 5, name: 'River Bend Access', type: 'river', species: ['Channel Catfish', 'Walleye', 'Smallmouth Bass'], rating: 4.3, distance: '20 mi' },
];

export const locationAgent = {
  name: 'Location Scout',
  description: 'Finds and recommends fishing locations',

  async run(input) {
    const { coords, targetSpecies, maxDistance } = input || {};

    let spots = [...SAMPLE_SPOTS];

    if (targetSpecies) {
      spots = spots.filter((s) =>
        s.species.some((sp) => sp.toLowerCase().includes(targetSpecies.toLowerCase()))
      );
    }

    spots.sort((a, b) => b.rating - a.rating);

    return {
      status: 'success',
      location: coords ? `${coords.latitude}, ${coords.longitude}` : 'Current Location',
      spots: spots.slice(0, 5),
      recommendation: spots[0]
        ? `We recommend ${spots[0].name} - rated ${spots[0].rating}/5 for ${spots[0].species.join(', ')}.`
        : 'No spots found matching your criteria.',
    };
  },
};
