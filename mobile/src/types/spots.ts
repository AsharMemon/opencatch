export interface SpotCardModel {
  id: string;
  name: string;
  subtitle: string;
  conditionLabel: string;
  conditionValue: string;
  note: string;
}

export interface SpotConditions {
  spotId: string;
  summary: string;
  waterTemp: string;
  discharge: string;
  trend: string;
  updatedAt: string;
  outlook: string;
}

export interface ConditionsService {
  listSavedSpots(): Promise<SpotCardModel[]>;
  getSpotConditions(spotId: string): Promise<SpotConditions>;
}
