/**
 * Weather Agent
 * Provides fishing-optimized weather analysis and recommendations.
 */

const WEATHER_CONDITIONS = [
  { condition: 'Sunny', fishActivity: 'moderate', tip: 'Fish deeper water or shaded areas. Early morning and late evening are best.' },
  { condition: 'Overcast', fishActivity: 'high', tip: 'Great fishing conditions! Fish are more active and willing to roam.' },
  { condition: 'Light Rain', fishActivity: 'high', tip: 'Excellent time to fish. Rain washes insects into water, triggering feeding.' },
  { condition: 'Heavy Rain', fishActivity: 'low', tip: 'Fish may be disoriented. Try muddy water near inflows after the rain stops.' },
  { condition: 'Windy', fishActivity: 'moderate-high', tip: 'Fish the windblown shore. Baitfish get pushed there, attracting predators.' },
  { condition: 'Cold Front', fishActivity: 'low', tip: 'Tough fishing. Slow down your presentation and fish deeper.' },
  { condition: 'Warm Front', fishActivity: 'high', tip: 'Fish become very active before a warm front. Great time to be on the water.' },
  { condition: 'Stable', fishActivity: 'moderate', tip: 'Consistent conditions mean predictable fishing. Stick to proven patterns.' },
];

const BAROMETRIC_EFFECTS = {
  rising: 'Fish tend to move to shallower water and feed more actively.',
  falling: 'Fish sense the pressure drop and feed aggressively before the front.',
  steady: 'Normal fishing patterns apply. Focus on structure and cover.',
  low: 'Tough bite. Use slow, subtle presentations close to the bottom.',
};

export const weatherAgent = {
  name: 'Weather Analysis',
  description: 'Analyzes weather conditions for optimal fishing',

  async run(input) {
    const { location, date } = input || {};

    // Simulated weather data (would use a real weather API in production)
    const currentCondition = WEATHER_CONDITIONS[1]; // Overcast as default
    const barometric = 'steady';

    return {
      status: 'success',
      location: location || 'Current Location',
      date: date || new Date().toLocaleDateString(),
      weather: {
        condition: currentCondition.condition,
        temperature: '68°F',
        humidity: '65%',
        wind: '8 mph SW',
        barometric: barometric,
      },
      fishing: {
        activity: currentCondition.fishActivity,
        tip: currentCondition.tip,
        barometricAdvice: BAROMETRIC_EFFECTS[barometric],
        bestTimes: ['6:00 AM - 9:00 AM', '5:00 PM - 8:00 PM'],
        rating: 4, // out of 5
      },
    };
  },
};
