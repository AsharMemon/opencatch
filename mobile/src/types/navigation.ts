import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { BottomTabScreenProps } from '@react-navigation/bottom-tabs';
import type { CompositeScreenProps, NavigatorScreenParams } from '@react-navigation/native';

// Bottom tab param list
export type TabParamList = {
  MapTab: undefined;
  LogTab: undefined;
  ForecastsTab: undefined;
  ToolsTab: undefined;
  ProfileTab: undefined;
};

// Stack param list (wraps tabs + detail screens)
export type RootStackParamList = {
  Tabs: NavigatorScreenParams<TabParamList>;
  LocationDetail: { locationId: string };
  CatchReport: { locationId?: string; lat?: number; lon?: number };
  FishDetail: { speciesId: string };
  SiteRanking: { siteId: string };
  Stats: undefined;
  TrackDetail: { trackId: string };
  Regulations: { stateCode?: string };
  BaitGuide: { species?: string };
  TideChart: { stationId: string; stationName: string };
  SpeciesGuide: undefined;
  OfflineMaps: undefined;
  Alerts: undefined;
  Safety: undefined;
  TrackRecording: undefined;
  TrackHistory: undefined;
  WeatherBuoys: { lat?: number; lon?: number };
  SunMoon: undefined;
  FishingPressure: undefined;
  BestTimes: undefined;
  WaterInsights: { lat?: number; lon?: number };
  SpeciesMap: undefined;
  Settings: undefined;
  Annotations: undefined;
};

// Screen prop helpers
export type RootStackProps<T extends keyof RootStackParamList> =
  NativeStackScreenProps<RootStackParamList, T>;

export type TabProps<T extends keyof TabParamList> = CompositeScreenProps<
  BottomTabScreenProps<TabParamList, T>,
  NativeStackScreenProps<RootStackParamList>
>;
