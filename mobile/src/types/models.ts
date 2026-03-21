// ── Fishing Location ──────────────────────────────────────────────
export interface FishingLocation {
  id: string;
  name: string;
  subtitle: string;
  lat: number;
  lon: number;
  score: number; // 0-100
  scoreBreakdown: ScoreBreakdown;
  conditions: CurrentConditions;
  explanation: string; // plain-English summary
  forecast: ForecastDay[];
  speciesActivity?: SpeciesActivity[];
  lureRecommendations?: LureRecommendation[];
  waterLevel?: WaterLevel;
}

export interface ScoreBreakdown {
  catchProbability: number; // 0-100
  cpue: number; // 0-100
  conditions: number; // 0-100
  trophyPotential: number; // 0-100
}

export interface CurrentConditions {
  waterTemp: number; // degrees F
  airTemp: number;
  weather: string; // 'Sunny', 'Partly Cloudy', etc.
  weatherIcon: WeatherIcon;
  windSpeed: number; // mph
  windDirection: string; // 'NW', 'SE', etc.
  pressure: number; // inHg
  pressureTrend: 'rising' | 'falling' | 'steady';
  humidity: number; // percent
  moonPhase: string; // 'Waxing Gibbous', etc.
  solunarRating: 'poor' | 'fair' | 'good' | 'excellent';
  sunrise: string;
  sunset: string;
}

export type WeatherIcon =
  | 'sunny'
  | 'partly-cloudy'
  | 'cloudy'
  | 'rainy'
  | 'stormy'
  | 'snowy'
  | 'foggy'
  | 'windy';

// ── Forecast ─────────────────────────────────────────────────────
export interface ForecastDay {
  date: string; // YYYY-MM-DD
  dayLabel: string; // 'Mon', 'Tue', etc.
  score: number;
  highTemp: number;
  lowTemp: number;
  weather: string;
  weatherIcon: WeatherIcon;
  windSpeed: number;
  precipChance: number;
}

// ── Catch Report ─────────────────────────────────────────────────
export type FishSpeciesName = string;

export interface CatchReport {
  species: string;
  numberCaught: number;
  numberKept: number;
  largestWeight?: number; // lbs
  rating: number; // 1-5
  lat: number;
  lon: number;
  date: string; // ISO string
  notes?: string;
  photoUri?: string;
}

// ── Fish Identification ─────────────────────────────────────────
export interface FishIdentification {
  species: string;
  confidence: number;
  alternates: { species: string; confidence: number }[];
}

// ── Prediction API response (legacy, used by mock) ───────────────
export interface PredictionResponse {
  score: number;
  breakdown: ScoreBreakdown;
  explanation: string;
  conditions: CurrentConditions;
}

// ── Best Fishing response ────────────────────────────────────────
export interface BestFishingLocation {
  id: string;
  name: string;
  lat: number;
  lon: number;
  score: number;
  topReason: string;
}

// ── V2 Prediction API (matches FastAPI PredictResponse) ──────────

export interface LayerBreakdown {
  label: string;
  description: string;
  contribution: number;
  data_quality: 'live' | 'estimated' | 'modeled' | 'historical';
}

export interface PredictionBreakdown {
  fishing_score: number;
  layers: Record<string, LayerBreakdown>;
  predicted_weight_lb: number;
}

export interface PredictionInterval {
  lower_bound: number;
  upper_bound: number;
  interval_width: number;
  margin: number;
  coverage_target: number;
  method: string; // "conformal" or "geo_conformal"
}

export interface PredictV2Response {
  fishing_score: number; // 0-100
  confidence: number;
  model_version: string;
  breakdown: PredictionBreakdown;
  conditions: Record<string, unknown>;
  explanation: string;
  prediction_id?: number;
  prediction_interval?: PredictionInterval;
}

// ── V2 Forecast (matches FastAPI ForecastV2Response) ─────────────

export interface ForecastV2Day {
  date: string;
  fishing_score: number | null;
  label?: string; // "Excellent", "Good", "Fair", "Poor", "Unknown"
  predicted_weight_lb: number | null;
  explanation: string;
}

export interface ForecastV2Response {
  location: string;
  start_date: string;
  forecast: ForecastV2Day[];
}

// ── V2 Best Fishing (matches FastAPI BestFishingResponse) ────────

export interface BestFishingV2Entry {
  location: string;
  fishing_score: number;
  label?: string; // "Excellent", "Good", "Fair", "Poor"
  predicted_weight_lb: number;
  historical_avg_lb: number;
  explanation?: string;
}

export interface BestFishingV2Response {
  date: string;
  top_locations: BestFishingV2Entry[];
  total_evaluated: number;
}

// ── V2 Conditions (matches FastAPI ConditionsResponse) ───────────

export interface ApiWeatherData {
  temp_f?: number;
  feels_like_f?: number;
  wind_mph?: number;
  wind_direction?: string;
  pressure_mb?: number;
  pressure_trend?: string;
  humidity_pct?: number;
  cloud_cover_pct?: number;
  precip_in?: number;
  conditions?: string;
}

export interface ApiWaterData {
  temp_f?: number;
  level_ft?: number;
  level_trend?: string;
  discharge_cfs?: number;
  clarity?: string;
  source?: string;
}

export interface ApiSpeciesActivity {
  species: string;
  probability: number;
  activity_level: string;
  optimal_temp_f?: number;
  notes?: string;
}

export interface ConditionsV2Response {
  score: number;
  confidence: string;
  label: string;
  summary: string;
  location_name: string;
  weather: ApiWeatherData;
  water: ApiWaterData;
  species: ApiSpeciesActivity[];
  top_factors: string[];
  updated_at: string;
}

// ── V2 Catch Report (matches FastAPI CatchReportCreate) ──────────

export interface CatchReportV2Create {
  user_id: string;
  lat: number;
  lon: number;
  trip_start: string; // ISO datetime
  trip_end: string;   // ISO datetime
  effort_hours: number;
  species: string;
  catch_count: number;
  kept_count: number;
  largest_weight_lb?: number;
  rating: number; // 1-5
  conditions_snapshot?: Record<string, unknown>;
  reported_at?: string;
  notes?: string;
}

export interface CatchReportV2Response {
  id: number;
  status: string;
  resolved_predictions?: Record<string, unknown>[];
}

// ── Fish Species (for collection/rolodex) ────────────────────────
export interface FishSpecies {
  id: string;
  commonName: string;
  scientificName: string;
  family: string;
  description: string;
  avgWeight: string; // "2-5 lbs"
  record: string; // "22 lbs 4 oz"
  habitat: string;
  difficulty: 'beginner' | 'intermediate' | 'advanced' | 'expert';
  rarity: 'common' | 'uncommon' | 'rare' | 'legendary';
  silhouetteIcon: string; // emoji fallback
}

export interface CaughtFish {
  speciesId: string;
  species: FishSpecies;
  firstCaught: string; // ISO date
  totalCaught: number;
  personalBest: number; // lbs
  locations: string[]; // location names
}

export interface FishCollection {
  caught: CaughtFish[];
  totalSpecies: number;
  caughtCount: number;
  completionPercent: number;
}

// ── Site Rankings ────────────────────────────────────────────────
export interface RankedSite {
  id: string;
  name: string;
  subtitle: string;
  lat: number;
  lon: number;
  rank: number;
  score: number;
  trend: 'up' | 'down' | 'steady';
  trendDelta: number; // points change
  topSpecies: string[];
  distanceMi?: number;
}

// ── Alerts ──────────────────────────────────────────────────────
export type AlertType = 'peak-activity' | 'pressure-drop' | 'optimal-conditions' | 'trophy-chance' | 'weather-warning';

export interface FishingAlert {
  id: string;
  type: AlertType;
  title: string;
  message: string;
  locationName?: string;
  locationId?: string;
  timestamp: string; // ISO
  read: boolean;
  priority: 'low' | 'medium' | 'high';
}

// ── Depth/Bathymetry ────────────────────────────────────────────
export interface DepthContour {
  depth: number; // in feet
  coordinates: Array<{ lat: number; lon: number }>;
}

export interface LakeBathymetry {
  lakeId: string;
  lakeName: string;
  maxDepth: number;
  avgDepth: number;
  contours: DepthContour[];
  surfaceArea: number; // acres
}

// ── Personal Waypoints ──────────────────────────────────────────
export interface Waypoint {
  id: string;
  name: string;
  notes?: string;
  lat: number;
  lon: number;
  icon: WaypointIcon;
  color: string;
  createdAt: string; // ISO
  catches: number; // total catches logged here
}

export type WaypointIcon = 'pin' | 'fish' | 'anchor' | 'star' | 'warning';

// ── Fish Activity ────────────────────────────────────────────────
export type ActivityLevel = 'inactive' | 'low' | 'moderate' | 'active' | 'very-active';

export interface SpeciesActivity {
  species: string;
  activity: ActivityLevel;
  confidence: number; // 0-1
  bestDepth: string; // "5-15 ft"
  bestTime: string; // "Early Morning"
}

// ── Lure Recommendations ─────────────────────────────────────────
export interface LureRecommendation {
  name: string;
  type: string; // "Crankbait", "Soft Plastic", "Spinnerbait", etc.
  color: string; // "Chartreuse/White", "Natural Shad", etc.
  technique: string; // "Slow roll along bottom", "Twitch and pause"
  confidence: number; // 0-1 match to current conditions
  reason: string; // "Falling pressure favors reaction baits"
}

// ── Water Level Data ────────────────────────────────────────────
export interface WaterLevel {
  stationId: string;
  stationName: string;
  distanceKm: number;
  currentLevel: number; // feet
  levelChange24h: number; // feet change in 24h
  levelTrend: 'rising' | 'falling' | 'stable';
  flowCfs?: number; // cubic feet per second
  waterTemp?: number; // degrees F
  lastUpdated: string; // ISO
}

// ── User Settings ────────────────────────────────────────────────
export type UnitSystem = 'imperial' | 'metric';

export type MapStyle = 'standard' | 'satellite' | 'terrain';

export interface UserSettings {
  displayName: string;
  units: UnitSystem;
  mapStyle: MapStyle;
  notifications: {
    dailyForecast: boolean;
    scoreAlerts: boolean;
    weeklyDigest: boolean;
    bestTimeAlerts: boolean;
    weatherAlerts: boolean;
  };
}
