import { mockConditions, mockSpots } from '../data/mockSpots';
import { ConditionsService, SpotCardModel, SpotConditions } from '../types/spots';

class MockConditionsService implements ConditionsService {
  async listSavedSpots(): Promise<SpotCardModel[]> {
    return Promise.resolve(mockSpots);
  }

  async getSpotConditions(spotId: string): Promise<SpotConditions> {
    const fallback = mockConditions['bow-riffle'];
    return Promise.resolve(mockConditions[spotId] ?? fallback);
  }
}

export const conditionsService: ConditionsService = new MockConditionsService();
