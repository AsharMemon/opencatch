import { SpotCardModel, SpotConditions } from '../types/spots';

export const mockSpots: SpotCardModel[] = [
  {
    id: 'bow-riffle',
    name: 'Bow Riffle',
    subtitle: 'South bank / dawn drift',
    conditionLabel: 'Water temp',
    conditionValue: '48°F',
    note: 'Flow settling into a fishable band before sunrise.',
  },
  {
    id: 'ghost-bend',
    name: 'Ghost Bend',
    subtitle: 'Cutbank seam',
    conditionLabel: 'Discharge',
    conditionValue: '312 cfs',
    note: 'Cooling slightly, still stable enough for a cautious window.',
  },
];

export const mockConditions: Record<string, SpotConditions> = {
  'bow-riffle': {
    spotId: 'bow-riffle',
    summary: 'A clean early window is opening as overnight flow pressure eases.',
    waterTemp: '48°F · climbing slowly',
    discharge: '412 cfs · down 6% / 6h',
    trend: 'Improving into first light',
    updatedAt: 'Updated 6 min ago',
    outlook: 'Next backend pass should replace this with live USGS-backed normalized conditions.',
  },
  'ghost-bend': {
    spotId: 'ghost-bend',
    summary: 'Still a placeholder read, but the shape is right for future live conditions.',
    waterTemp: '45°F · steady',
    discharge: '312 cfs · stable',
    trend: 'Neutral until more signal arrives',
    updatedAt: 'Updated 9 min ago',
    outlook: 'Hook this to the future conditions endpoint and alert substrate.',
  },
};
