import React, { useEffect, useState } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  ActivityIndicator,
  Pressable,
  Dimensions,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette, scoreColor, scoreLabel, getConditionBand, conditionConfig } from '../theme/palette';
import { fonts, type as typeStyles } from '../theme/typography';
import { SkeletonLoader, SkeletonCard } from '../components/ui/SkeletonLoader';
import { ScoreGauge } from '../components/ScoreGauge';
import { ScoreBreakdownBar } from '../components/ScoreBreakdownBar';
import { WeatherCard } from '../components/WeatherCard';
import { WeatherForecastSection } from '../components/WeatherForecastSection';
import { ForecastChart } from '../components/ForecastChart';
import { ExplanationCard } from '../components/ExplanationCard';
import { api } from '../services/api';
import { getCachedLocationDetail, getStaleLocationDetail, cacheLocationDetail } from '../services/locationDetailCache';
import {
  getSpeciesLikelihood,
  getMonthlyActivityChart,
  type SpeciesLikelihood,
} from '../services/speciesDistribution';
import {
  getHourlyPressure,
  getCurrentPressure,
  type HourlyPressure,
  type PressureReading,
} from '../services/fishingPressure';
import {
  getDailyBiteForecast,
  getWeeklyBiteForecast,
  formatHour,
  type DailyBiteForecast as BiteFC,
} from '../services/bestTimeWindows';
import {
  getWaterInsights,
  conditionColor,
  type WaterInsightsDashboard,
} from '../services/waterInsights';
import type {
  FishingLocation,
  ActivityLevel,
  SpeciesActivity,
  LureRecommendation,
  WaterLevel,
  PredictV2Response,
  LayerBreakdown,
} from '../types/models';
import type { RootStackProps } from '../types/navigation';

const DETAIL_WIDTH = Dimensions.get('window').width;

type Props = RootStackProps<'LocationDetail'>;

// ── Activity helpers ──────────────────────────────────────────────
function activityColor(level: ActivityLevel): string {
  switch (level) {
    case 'very-active': return palette.pinHot;
    case 'active':      return palette.pinHeatingUp;
    case 'moderate':    return palette.pinFair;
    case 'low':         return palette.pinSlow;
    case 'inactive':    return palette.textDim;
  }
}

function activityLabel(level: ActivityLevel): string {
  switch (level) {
    case 'very-active': return 'Excellent';
    case 'active':      return 'Good';
    case 'moderate':    return 'Fair';
    case 'low':         return 'Poor';
    case 'inactive':    return 'Inactive';
  }
}

// ── Species Activity Section ──────────────────────────────────────
function SpeciesActivityCard({ data }: { data: SpeciesActivity[] }) {
  if (!data || data.length === 0) return null;
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>Species Activity</Text>
      {data.map((item, index) => {
        const color = activityColor(item.activity);
        const confidencePct = Math.round(item.confidence * 100);
        return (
          <View key={item.species}>
            {index > 0 && <View style={styles.divider} />}
            <View style={styles.speciesRow}>
              {/* Left: name + depth/time */}
              <View style={styles.speciesLeft}>
                <Text style={styles.speciesName}>{item.species}</Text>
                <Text style={styles.speciesMeta}>
                  Best: {item.bestDepth} {'·'} {item.bestTime}
                </Text>
                {/* Confidence bar */}
                <View style={styles.confidenceTrack}>
                  <View
                    style={[
                      styles.confidenceFill,
                      { width: `${confidencePct}%` as any, backgroundColor: color },
                    ]}
                  />
                </View>
              </View>
              {/* Right: activity badge */}
              <View style={[styles.activityBadge, { backgroundColor: color + '1A' }]}>
                <View style={[styles.activityDot, { backgroundColor: color }]} />
                <Text style={[styles.activityLabel, { color }]}>
                  {activityLabel(item.activity)}
                </Text>
              </View>
            </View>
          </View>
        );
      })}
    </View>
  );
}

// ── Lure Recommendations Section ─────────────────────────────────
function LureRecommendationsCard({ data }: { data: LureRecommendation[] }) {
  if (!data || data.length === 0) return null;
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>Recommended Lures</Text>
      <View style={styles.lureList}>
        {data.map((lure) => {
          const matchPct = Math.round(lure.confidence * 100);
          return (
            <View key={lure.name} style={styles.lureMiniCard}>
              {/* Header row: name + match badge */}
              <View style={styles.lureHeader}>
                <View style={styles.lureNameRow}>
                  <Text style={styles.lureName}>{lure.name}</Text>
                  <View style={styles.lureTypePill}>
                    <Text style={styles.lureTypeText}>{lure.type}</Text>
                  </View>
                </View>
                <View style={styles.matchBadge}>
                  <Text style={styles.matchBadgeText}>{matchPct}%</Text>
                </View>
              </View>
              {/* Color chip + color name */}
              <View style={styles.lureColorRow}>
                <View style={[styles.colorDot, { backgroundColor: lureColorHex(lure.color) }]} />
                <Text style={styles.lureColorText}>{lure.color}</Text>
              </View>
              {/* Technique */}
              <Text style={styles.lureTechnique}>{lure.technique}</Text>
              {/* Reason */}
              <Text style={styles.lureReason}>Why: {lure.reason}</Text>
            </View>
          );
        })}
      </View>
    </View>
  );
}

// Map common lure color names to approximate display hex values
function lureColorHex(colorName: string): string {
  const name = colorName.toLowerCase();
  if (name.includes('chartreuse')) return '#A8E600';
  if (name.includes('white')) return '#E8E8E0';
  if (name.includes('black')) return '#2A2A2A';
  if (name.includes('red')) return '#C44B4B';
  if (name.includes('blue')) return '#4A8DB5';
  if (name.includes('shad') || name.includes('ghost') || name.includes('chrome')) return '#C8D0D8';
  if (name.includes('green pumpkin') || name.includes('pumpkin')) return '#5A7A3A';
  if (name.includes('brown') || name.includes('craw')) return '#8B5E3C';
  if (name.includes('watermelon')) return '#5C8C58';
  if (name.includes('pink')) return '#E8809A';
  if (name.includes('orange')) return '#D4763A';
  if (name.includes('smoke')) return '#909090';
  if (name.includes('natural')) return '#A0905A';
  return palette.accent;
}

// ── Water Conditions Card ─────────────────────────────────────────
function minutesAgo(isoString: string): number {
  return Math.round((Date.now() - new Date(isoString).getTime()) / 60000);
}

function WaterConditionsCard({ data }: { data: WaterLevel }) {
  if (!data?.lastUpdated) return null;
  const mins = minutesAgo(data.lastUpdated);

  const trendIcon =
    data.levelTrend === 'rising'
      ? '\u2191' // ↑
      : data.levelTrend === 'falling'
      ? '\u2193' // ↓
      : '\u2014'; // —

  const trendColor =
    data.levelTrend === 'rising'
      ? palette.water
      : data.levelTrend === 'falling'
      ? palette.error
      : palette.textMuted;

  const changeSign = data.levelChange24h > 0 ? '+' : '';
  const changeColor =
    data.levelChange24h > 0
      ? palette.success
      : data.levelChange24h < 0
      ? palette.error
      : palette.textMuted;

  return (
    <View style={styles.card}>
      {/* Header */}
      <View style={styles.waterCardHeader}>
        <Text style={styles.cardTitle}>Water Conditions</Text>
        <View style={[styles.trendPill, { backgroundColor: trendColor + '18' }]}>
          <Text style={[styles.trendPillIcon, { color: trendColor }]}>{trendIcon}</Text>
          <Text style={[styles.trendPillText, { color: trendColor }]}>
            {data.levelTrend.charAt(0).toUpperCase() + data.levelTrend.slice(1)}
          </Text>
        </View>
      </View>

      {/* Main stats row */}
      <View style={styles.waterStatsRow}>
        {/* Level */}
        <View style={styles.waterStat}>
          <Text style={styles.waterStatValue}>{data.currentLevel.toFixed(2)}</Text>
          <Text style={styles.waterStatUnit}>ft</Text>
          <Text style={styles.waterStatLabel}>Stage</Text>
        </View>

        <View style={styles.waterStatDivider} />

        {/* 24h change */}
        <View style={styles.waterStat}>
          <View style={styles.waterChangeRow}>
            <Text style={[styles.waterStatValue, { color: changeColor }]}>
              {changeSign}{Math.abs(data.levelChange24h).toFixed(2)}
            </Text>
            <Text style={[styles.waterChangeArrow, { color: changeColor }]}>
              {data.levelChange24h > 0 ? '\u2191' : data.levelChange24h < 0 ? '\u2193' : ''}
            </Text>
          </View>
          <Text style={styles.waterStatUnit}>ft</Text>
          <Text style={styles.waterStatLabel}>24h Change</Text>
        </View>

        {data.flowCfs !== undefined && (
          <>
            <View style={styles.waterStatDivider} />
            <View style={styles.waterStat}>
              <Text style={styles.waterStatValue}>
                {data.flowCfs >= 1000
                  ? `${(data.flowCfs / 1000).toFixed(1)}k`
                  : String(data.flowCfs)}
              </Text>
              <Text style={styles.waterStatUnit}>CFS</Text>
              <Text style={styles.waterStatLabel}>Flow</Text>
            </View>
          </>
        )}

        {data.waterTemp !== undefined && (
          <>
            <View style={styles.waterStatDivider} />
            <View style={styles.waterStat}>
              <Text style={styles.waterStatValue}>{data.waterTemp}°</Text>
              <Text style={styles.waterStatUnit}>F</Text>
              <Text style={styles.waterStatLabel}>Water Temp</Text>
            </View>
          </>
        )}
      </View>

      {/* Station footer */}
      <View style={styles.waterFooter}>
        <View style={styles.waterFooterLeft}>
          <Text style={styles.waterStationName}>{data.stationName}</Text>
          <Text style={styles.waterDistance}>{data.distanceKm.toFixed(1)} km away</Text>
        </View>
        <Text style={styles.waterUpdated}>Updated {mins}m ago</Text>
      </View>

      <Text style={styles.waterSource}>Data from USGS</Text>
    </View>
  );
}

// ── 4-Layer Breakdown Card ────────────────────────────────────────

const LAYER_IONICONS: Record<string, string> = {
  hydrology: 'water-outline',
  weather: 'cloud-outline',
  biology: 'leaf-outline',
  history: 'bar-chart-outline',
};

const QUALITY_LABELS: Record<string, { label: string; color: string }> = {
  live: { label: 'Live', color: palette.success },
  estimated: { label: 'Est.', color: palette.warning },
  modeled: { label: 'Model', color: palette.water },
  historical: { label: 'Hist.', color: palette.textMuted },
};

function LayerBreakdownCard({ prediction }: { prediction: PredictV2Response }) {
  const breakdown = prediction?.breakdown;
  if (!breakdown?.layers) return null;
  const layers = Object.entries(breakdown.layers);

  return (
    <View style={styles.card}>
      <View style={styles.layerCardHeader}>
        <Text style={styles.cardTitle}>Prediction Breakdown</Text>
        <View style={styles.modelBadge}>
          <Text style={styles.modelBadgeText}>{prediction.model_version}</Text>
        </View>
      </View>
      <Text style={styles.layerConfidence}>
        Confidence: {Math.round(prediction.confidence * 100)}%
      </Text>
      {layers.map(([key, layer]) => {
        const iconName = LAYER_IONICONS[key] ?? 'flask-outline';
        const quality = QUALITY_LABELS[layer.data_quality] ?? QUALITY_LABELS.estimated;
        const pct = breakdown.fishing_score > 0
          ? Math.round((layer.contribution / breakdown.fishing_score) * 100)
          : 0;
        return (
          <View key={key} style={styles.layerRow}>
            <View style={styles.layerIcon}>
              <Ionicons name={iconName as any} size={18} color={palette.accent} />
            </View>
            <View style={styles.layerInfo}>
              <View style={styles.layerNameRow}>
                <Text style={styles.layerLabel}>{layer.label}</Text>
                <View style={[styles.qualityPill, { backgroundColor: quality.color + '1A' }]}>
                  <Text style={[styles.qualityPillText, { color: quality.color }]}>
                    {quality.label}
                  </Text>
                </View>
              </View>
              <Text style={styles.layerDesc} numberOfLines={2}>
                {layer.description}
              </Text>
              <View style={styles.layerBarTrack}>
                <View
                  style={[
                    styles.layerBarFill,
                    { width: `${Math.min(pct, 100)}%` as any, backgroundColor: palette.accent },
                  ]}
                />
              </View>
            </View>
            <Text style={styles.layerContrib}>+{layer.contribution}</Text>
          </View>
        );
      })}
      <View style={styles.layerTotal}>
        <Text style={styles.layerTotalLabel}>Predicted weight</Text>
        <Text style={styles.layerTotalValue}>
          {breakdown.predicted_weight_lb.toFixed(1)} lb
        </Text>
      </View>
    </View>
  );
}

// ── Inline Integration: Species Distribution Chart ──────────────────────────
function SpeciesDistributionChart({ species }: { species: SpeciesLikelihood[] }) {
  if (!species || species.length === 0) return null;
  const maxLikelihood = Math.max(...species.map((s) => s.likelihood), 0.01);
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>Species Distribution</Text>
      {species.slice(0, 6).map((sp) => {
        const pct = Math.round(sp.likelihood * 100);
        const barWidth = (sp.likelihood / maxLikelihood) * 100;
        return (
          <View key={sp.speciesId} style={styles.speciesBarRow}>
            <Text style={styles.speciesBarName} numberOfLines={1}>{sp.commonName}</Text>
            <View style={styles.speciesBarTrack}>
              <View
                style={[
                  styles.speciesBarFill,
                  {
                    width: `${barWidth}%` as any,
                    backgroundColor:
                      sp.confidence === 'high' ? '#2E7D32'
                      : sp.confidence === 'medium' ? '#FFA726'
                      : palette.textMuted,
                  },
                ]}
              />
            </View>
            <Text style={styles.speciesBarPct}>{pct}%</Text>
          </View>
        );
      })}
      <Text style={styles.speciesHint}>Based on habitat, season, and geographic range</Text>
    </View>
  );
}

// ── Inline Integration: 24-Hour Fishing Pressure Chart ──────────────────────
function PressureChartCard({ hourly, current }: { hourly: HourlyPressure[]; current: PressureReading }) {
  const maxScore = Math.max(...hourly.map((h) => h.score), 1);
  const currentHour = new Date().getHours();

  return (
    <View style={styles.card}>
      <View style={styles.pressureChartHeader}>
        <Text style={styles.cardTitle}>Fishing Pressure</Text>
        <View style={[styles.pressureNowBadge, { backgroundColor: current.color + '18' }]}>
          <Ionicons name={current.icon as any} size={13} color={current.color} />
          <Text style={[styles.pressureNowText, { color: current.color }]}>{current.label}</Text>
        </View>
      </View>
      <Text style={styles.pressureChartDesc}>{current.description}</Text>

      {/* 24-hour bar chart */}
      <View style={styles.pressureBars}>
        {hourly.map((h) => {
          const heightPct = (h.score / maxScore) * 100;
          const isNow = h.hour === currentHour;
          const barColor =
            h.level === 'very-high' || h.level === 'high' ? '#EF5350'
            : h.level === 'moderate' ? '#FFA726'
            : '#66BB6A';
          return (
            <View key={h.hour} style={styles.pressureBarCol}>
              <View style={styles.pressureBarWrapper}>
                <View
                  style={[
                    styles.pressureBar,
                    {
                      height: `${Math.max(heightPct, 4)}%` as any,
                      backgroundColor: isNow ? palette.accent : barColor,
                    },
                  ]}
                />
              </View>
              {h.hour % 6 === 0 && (
                <Text style={[styles.pressureBarLabel, isNow && { color: palette.accent, fontWeight: '700' }]}>
                  {h.hour === 0 ? '12A' : h.hour === 6 ? '6A' : h.hour === 12 ? '12P' : '6P'}
                </Text>
              )}
            </View>
          );
        })}
      </View>
      {current.peakHours.length > 0 && (
        <Text style={styles.pressurePeakHint}>
          Peak: {current.peakHours.join(', ')}
        </Text>
      )}
    </View>
  );
}

// ── Inline Integration: Best Times (3-day) ──────────────────────────────────
function BestTimesCard({ forecasts }: { forecasts: BiteFC[] }) {
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>Best Times \u2014 Next 3 Days</Text>
      {forecasts.slice(0, 3).map((fc, i) => {
        const dayLabel = i === 0 ? 'Today' : i === 1 ? 'Tomorrow' : days[fc.date.getDay()];
        const ratingColor =
          fc.overallRating >= 70 ? '#2E7D32'
          : fc.overallRating >= 55 ? '#66BB6A'
          : fc.overallRating >= 40 ? '#FFA726'
          : '#EF5350';

        return (
          <View key={i} style={[styles.bestTimeRow, i > 0 && styles.bestTimeDivider]}>
            <View style={styles.bestTimeLeft}>
              <Text style={styles.bestTimeDayLabel}>{dayLabel}</Text>
              <View style={[styles.bestTimeRatingBadge, { backgroundColor: ratingColor + '15' }]}>
                <Text style={[styles.bestTimeRating, { color: ratingColor }]}>{fc.ratingLabel}</Text>
              </View>
            </View>
            <View style={styles.bestTimeRight}>
              {fc.bestWindow ? (
                <>
                  <Ionicons name="time-outline" size={13} color={palette.accent} />
                  <Text style={styles.bestTimeWindow}>{fc.bestWindow.label}</Text>
                </>
              ) : (
                <Text style={styles.bestTimeNone}>No peak window</Text>
              )}
            </View>
          </View>
        );
      })}
      <Text style={styles.bestTimeHint}>
        Moon: {forecasts[0]?.moonPhase ?? 'Unknown'} ({forecasts[0]?.moonIllumination ?? 0}% illumination)
      </Text>
    </View>
  );
}

// ── Inline Integration: Water Insights Card ─────────────────────────────────
function WaterInsightsCard({ data }: { data: WaterInsightsDashboard }) {
  return (
    <View style={styles.card}>
      <View style={styles.waterInsightHeader}>
        <Text style={styles.cardTitle}>Water Insights</Text>
        <View style={[styles.waterCondBadge, { backgroundColor: conditionColor(data.overallCondition) + '18' }]}>
          <Text style={[styles.waterCondText, { color: conditionColor(data.overallCondition) }]}>
            {data.overallCondition.charAt(0).toUpperCase() + data.overallCondition.slice(1)}
          </Text>
        </View>
      </View>
      <View style={styles.waterInsightGrid}>
        {data.insights.map((insight) => (
          <View key={insight.label} style={styles.waterInsightItem}>
            <Ionicons name={insight.icon as any} size={18} color={insight.color} />
            <Text style={styles.waterInsightLabel}>{insight.label}</Text>
            <Text style={[styles.waterInsightValue, { color: insight.color }]}>
              {insight.value}{insight.unit}
            </Text>
            {insight.trend && (
              <Ionicons
                name={
                  insight.trend === 'rising' ? 'trending-up' :
                  insight.trend === 'falling' ? 'trending-down' : 'remove-outline'
                }
                size={14}
                color={
                  insight.trend === 'rising' ? palette.accent :
                  insight.trend === 'falling' ? palette.error : palette.textMuted
                }
              />
            )}
          </View>
        ))}
      </View>
      <Text style={styles.waterInsightSummary}>{data.summary}</Text>
      <Text style={styles.waterInsightImpact}>{data.fishingImpact}</Text>
    </View>
  );
}

// ── Open-Meteo weather fetch for any lat/lon ─────────────────────
async function fetchOpenMeteoConditions(
  lat: number,
  lon: number,
): Promise<Partial<import('../types/models').CurrentConditions> | null> {
  try {
    const params = new URLSearchParams({
      latitude: lat.toFixed(4),
      longitude: lon.toFixed(4),
      current_weather: 'true',
      hourly: 'relative_humidity_2m,pressure_msl',
      forecast_days: '1',
      timezone: 'auto',
    });
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    const resp = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`, {
      signal: controller.signal,
    });
    clearTimeout(timeout);
    if (!resp.ok) return null;
    const data = await resp.json();
    const cw = data.current_weather;
    if (!cw) return null;

    const currentHour = new Date().getHours();
    const humidity = data.hourly?.relative_humidity_2m?.[currentHour] ?? 50;
    const pressureHpa = data.hourly?.pressure_msl?.[currentHour] ?? 1013;
    const pressureInHg = Math.round(pressureHpa / 33.8639 * 100) / 100;

    // Wind direction from degrees to compass
    const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
    const windDir = dirs[Math.round(cw.winddirection / 22.5) % 16];

    // Weather code to description
    const weatherCode = cw.weathercode ?? 0;
    let weather = 'Clear';
    let weatherIcon: 'sunny' | 'partly-cloudy' | 'cloudy' | 'rainy' | 'stormy' | 'snowy' | 'foggy' | 'windy' = 'sunny';
    if (weatherCode <= 1) { weather = 'Clear'; weatherIcon = 'sunny'; }
    else if (weatherCode <= 3) { weather = 'Partly Cloudy'; weatherIcon = 'partly-cloudy'; }
    else if (weatherCode <= 48) { weather = 'Cloudy'; weatherIcon = 'cloudy'; }
    else if (weatherCode <= 67) { weather = 'Rainy'; weatherIcon = 'rainy'; }
    else if (weatherCode <= 77) { weather = 'Snowy'; weatherIcon = 'snowy'; }
    else if (weatherCode <= 82) { weather = 'Rainy'; weatherIcon = 'rainy'; }
    else { weather = 'Stormy'; weatherIcon = 'stormy'; }

    const airTempF = Math.round(cw.temperature * 9 / 5 + 32);
    const windMph = Math.round(cw.windspeed * 0.621371);

    // Compute solunar and moon info from bestTimeWindows service
    // Apply weather penalties based on actual current conditions
    const biteForecast = getDailyBiteForecast(lat, lon, {
      tempF: airTempF,
      windMph,
      weatherPenalty: {
        weatherCode,
        tempF: airTempF,
        windMph,
      },
    });

    return {
      airTemp: airTempF,
      weather,
      weatherIcon,
      windSpeed: windMph,
      windDirection: windDir,
      pressure: pressureInHg,
      pressureTrend: 'steady',
      humidity,
      moonPhase: biteForecast.moonPhase,
      solunarRating: biteForecast.overallRating >= 70 ? 'excellent'
        : biteForecast.overallRating >= 55 ? 'good'
        : biteForecast.overallRating >= 40 ? 'fair' : 'poor',
      sunrise: formatHour(Math.round(biteForecast.sunrise)),
      sunset: formatHour(Math.round(biteForecast.sunset)),
    };
  } catch {
    return null;
  }
}

export function LocationDetailScreen({ route, navigation }: Props) {
  const { locationId } = route.params;
  const [location, setLocation] = useState<FishingLocation | null>(null);
  const [loading, setLoading] = useState(true);
  const [prediction, setPrediction] = useState<PredictV2Response | null>(null);
  const [predictionLoading, setPredictionLoading] = useState(false);
  const [predictionError, setPredictionError] = useState<string | null>(null);

  // Integrated feature state
  const [speciesData, setSpeciesData] = useState<SpeciesLikelihood[]>([]);
  const [hourlyPressure, setHourlyPressure] = useState<HourlyPressure[]>([]);
  const [currentPressure, setCurrentPressure] = useState<PressureReading | null>(null);
  const [biteForecasts, setBiteForecasts] = useState<BiteFC[]>([]);
  const [waterInsights, setWaterInsights] = useState<WaterInsightsDashboard | null>(null);

  useEffect(() => {
    let cancelled = false;

    // 1. Try instant cache hit (shows UI immediately)
    const cached = getCachedLocationDetail(locationId) ?? getStaleLocationDetail(locationId);
    if (cached) {
      setLocation(cached);
      setLoading(false);
      navigation.setOptions({ title: cached.name || 'Unseen Site' });

      // Kick off prediction immediately using cached location
      setPredictionLoading(true);
      const today = new Date().toISOString().slice(0, 10);
      api.predictV2(cached.name ?? '', today).then((pred) => {
        if (!cancelled) { setPrediction(pred); setPredictionLoading(false); }
      }).catch((err) => {
        if (!cancelled) { setPredictionError(err?.message ?? 'Prediction unavailable'); setPredictionLoading(false); }
      });

      // For OSM-discovered spots (or any spot with placeholder conditions),
      // fetch live weather from Open-Meteo to populate conditions
      if (cached.lat && cached.lon && (!cached.conditions?.weather || cached.conditions.weather === 'Unknown')) {
        fetchOpenMeteoConditions(cached.lat, cached.lon).then((cond) => {
          if (!cancelled && cond) {
            setLocation((prev) => prev ? { ...prev, conditions: { ...prev.conditions, ...cond } } : prev);
          }
        }).catch(() => {});
      }
    }

    // 2. Always fetch fresh data in background (revalidate)
    api.getLocation(locationId).then((loc) => {
      if (cancelled) return;
      if (!loc) {
        // OSM-discovered spots won't be found via api — stop loading if we had no cache
        if (!cached) setLoading(false);
        return;
      }
      cacheLocationDetail(loc);
      setLocation(loc);
      setLoading(false);
      navigation.setOptions({ title: loc.name || 'Unseen Site' });

      // Only start prediction if we didn't already from cache
      if (!cached) {
        setPredictionLoading(true);
        const today = new Date().toISOString().slice(0, 10);
        api.predictV2(loc.name ?? '', today).then((pred) => {
          if (!cancelled) { setPrediction(pred); setPredictionLoading(false); }
        }).catch((err) => {
          if (!cancelled) { setPredictionError(err?.message ?? 'Prediction unavailable'); setPredictionLoading(false); }
        });
      }

      // Fetch live weather if conditions seem like placeholders
      if (loc.lat && loc.lon && (!loc.conditions?.weather || loc.conditions.weather === 'Unknown')) {
        fetchOpenMeteoConditions(loc.lat, loc.lon).then((cond) => {
          if (!cancelled && cond) {
            setLocation((prev) => prev ? { ...prev, conditions: { ...prev.conditions, ...cond } } : prev);
          }
        }).catch(() => {});
      }
    }).catch(() => {
      if (!cancelled && !cached) setLoading(false);
    });

    return () => { cancelled = true; };
  }, [locationId]);

  // Load integrated feature data once we have the location
  useEffect(() => {
    if (!location) return;
    let cancelled = false;
    const { lat, lon, name } = location;

    // Only show species distribution for known (non-OSM) locations with real data.
    // OSM-discovered spots don't have verified species data.
    const isDiscovered = location.id.startsWith('osm-');
    if (!isDiscovered) {
      setSpeciesData(getSpeciesLikelihood(lat, lon));
    } else {
      setSpeciesData([]);
    }
    setHourlyPressure(getHourlyPressure());
    setCurrentPressure(getCurrentPressure({ lat, lon }));
    setBiteForecasts(getWeeklyBiteForecast(lat, lon));

    // Async water insights
    getWaterInsights(lat, lon, { locationName: name }).then((w) => {
      if (!cancelled) setWaterInsights(w);
    }).catch(() => {});

    return () => { cancelled = true; };
  }, [location]);

  if (loading || !location) {
    return (
      <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
        <View style={{ alignItems: 'center', gap: 12, paddingVertical: 20 }}>
          <SkeletonLoader width={100} height={100} borderRadius={50} />
          <SkeletonLoader width="60%" height={22} borderRadius={6} />
          <SkeletonLoader width="40%" height={14} borderRadius={4} />
        </View>
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </ScrollView>
    );
  }

  // Update score breakdown from prediction if available
  const predScore = prediction?.breakdown?.fishing_score ?? prediction?.fishing_score;
  const scoreBreakdown = predScore != null
    ? {
        catchProbability: Math.round(predScore * 0.3),
        cpue: Math.round(predScore * 0.3),
        conditions: Math.round(predScore * 0.25),
        trophyPotential: Math.round(predScore * 0.15),
      }
    : location.scoreBreakdown ?? {
        catchProbability: 50,
        cpue: 50,
        conditions: 50,
        trophyPotential: 40,
      };
  const rawScore = predScore ?? location.score ?? 0;
  // If score is 0 (not yet computed) and prediction is still loading, show skeleton
  const scoreIsLoading = rawScore === 0 && predictionLoading;
  const safeScore = rawScore;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {/* Score section */}
      <View style={styles.scoreSection}>
        {/* Condition band badge — hide when score is loading */}
        {scoreIsLoading ? (
          <View style={{ alignItems: 'center', paddingVertical: 20, gap: 8 }}>
            <SkeletonLoader width={100} height={100} borderRadius={50} />
            <SkeletonLoader width="40%" height={16} borderRadius={4} />
          </View>
        ) : (
          <>
            {(() => {
              const band = getConditionBand(safeScore);
              const cfg = conditionConfig[band];
              return (
                <View style={[styles.conditionBadge, { backgroundColor: cfg.bgTint }]}>
                  <Ionicons name={cfg.ionicon as any} size={18} color={cfg.color} />
                  <Text style={[styles.conditionBadgeLabel, { color: cfg.color }]}>{cfg.label}</Text>
                </View>
              );
            })()}
            <ScoreGauge score={safeScore} size={180} />
          </>
        )}
        <Text style={styles.locationName}>{location.name || 'Unseen Site'}</Text>
        <Text style={styles.locationSubtitle}>{location.subtitle || 'Water Body'}</Text>

        {/* Busyness / Popularity — only shown when real data is available */}
      </View>

      {/* Score Breakdown */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Score Breakdown</Text>
        <View style={styles.breakdownList}>
          <ScoreBreakdownBar label="Catch Prob." value={scoreBreakdown.catchProbability} />
          <ScoreBreakdownBar label="CPUE" value={scoreBreakdown.cpue} />
          <ScoreBreakdownBar label="Conditions" value={scoreBreakdown.conditions} />
          <ScoreBreakdownBar label="Trophy" value={scoreBreakdown.trophyPotential} />
        </View>
      </View>

      {/* V2 4-Layer Prediction Breakdown */}
      {predictionLoading && (
        <View style={[styles.card, { alignItems: 'center', paddingVertical: 24 }]}>
          <ActivityIndicator color={palette.accent} size="small" />
          <Text style={{ color: palette.textMuted, fontSize: 13, marginTop: 8 }}>
            Loading ML prediction...
          </Text>
        </View>
      )}
      {prediction && !predictionLoading && (
        <LayerBreakdownCard prediction={prediction} />
      )}
      {predictionError && !predictionLoading && !prediction && (
        <View style={[styles.card, { alignItems: 'center', paddingVertical: 16 }]}>
          <Text style={{ color: palette.textMuted, fontSize: 13 }}>
            {predictionError}
          </Text>
        </View>
      )}

      {/* AI Explanation */}
      {(prediction?.explanation || location.explanation) && (
        <ExplanationCard explanation={prediction?.explanation ?? location.explanation ?? ''} />
      )}

      {/* 7-Day Forecast */}
      {(location.forecast ?? []).length > 0 && (
        <ForecastChart forecast={location.forecast ?? []} />
      )}

      {/* Current Conditions */}
      {location.conditions && (
        <WeatherCard conditions={location.conditions} />
      )}

      {/* Detailed Weather Forecast */}
      {location.conditions && (
        <WeatherForecastSection location={location} />
      )}

      {/* ── Integrated Feature Cards ─────────────────────────────────── */}

      {/* Best Times — next 3 days */}
      {biteForecasts.length > 0 && (
        <BestTimesCard forecasts={biteForecasts} />
      )}

      {/* Fishing Pressure 24-hour chart */}
      {hourlyPressure.length > 0 && currentPressure && (
        <PressureChartCard hourly={hourlyPressure} current={currentPressure} />
      )}

      {/* Water Insights (from service, richer than waterLevel) */}
      {waterInsights && waterInsights.insights.length > 0 && (
        <WaterInsightsCard data={waterInsights} />
      )}

      {/* Species Distribution Chart */}
      {speciesData.length > 0 && (
        <SpeciesDistributionChart species={speciesData} />
      )}
      {/* Species data unavailable notice for discovered (OSM) spots */}
      {speciesData.length === 0 && location.id.startsWith('osm-') && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Species</Text>
          <View style={{ alignItems: 'center', paddingVertical: 12, gap: 6 }}>
            <Ionicons name="fish-outline" size={24} color={palette.textDim} />
            <Text style={{ color: palette.textMuted, fontSize: 13, textAlign: 'center' }}>
              Species data not available for this location
            </Text>
          </View>
        </View>
      )}

      {/* Water Conditions (legacy USGS card) */}
      {location.waterLevel && (
        <WaterConditionsCard data={location.waterLevel} />
      )}

      {/* Species Activity */}
      {location.speciesActivity && location.speciesActivity.length > 0 && (
        <SpeciesActivityCard data={location.speciesActivity} />
      )}

      {/* Lure Recommendations */}
      {location.lureRecommendations && location.lureRecommendations.length > 0 && (
        <LureRecommendationsCard data={location.lureRecommendations} />
      )}

      {/* Depth Map Placeholder */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Depth Map</Text>
        <View style={styles.depthMapPlaceholder}>
          <View style={styles.depthGradient}>
            {palette.depthContours.map((color, i) => (
              <View
                key={i}
                style={[
                  styles.depthBand,
                  {
                    backgroundColor: color,
                    flex: 1,
                  },
                ]}
              />
            ))}
          </View>
          <View style={styles.depthOverlay}>
            <Ionicons name="water-outline" size={28} color={palette.water} />
            <Text style={styles.depthText}>Bathymetric contour map coming soon</Text>
            <Text style={styles.depthSubtext}>
              Detailed underwater topography with depth contours
            </Text>
          </View>
        </View>
      </View>

      {/* Access & Parking — will show real data when access point service populates it */}
      {/* Site Tags — will show real data when available */}

      {/* Community Reviews */}
      <View style={styles.card}>
        <View style={styles.reviewHeader}>
          <Text style={styles.cardTitle}>Reviews</Text>
        </View>
        <View style={{ alignItems: 'center', paddingVertical: 16, gap: 8 }}>
          <Ionicons name="chatbubble-outline" size={28} color={palette.textDim} />
          <Text style={{ color: palette.textMuted, fontSize: 14 }}>No reviews yet</Text>
          <Text style={{ color: palette.textDim, fontSize: 12, textAlign: 'center' }}>
            Be the first to share your experience at this spot.
          </Text>
        </View>
        <Pressable style={styles.reviewCTA}>
          <Ionicons name="create-outline" size={14} color={palette.accent} />
          <Text style={styles.reviewCTAText}>Write a Review</Text>
        </Pressable>
      </View>

      {/* Log a Catch CTA */}
      <Pressable
        style={({ pressed }) => [styles.ctaButton, pressed && styles.ctaPressed]}
        onPress={() =>
          navigation.navigate('CatchReport', {
            locationId: location.id,
            lat: location.lat,
            lon: location.lon,
          })
        }
      >
        <Ionicons name="fish-outline" size={20} color="#FFFFFF" />
        <Text style={styles.ctaText}>Log a Catch</Text>
      </Pressable>

      {/* Bottom spacer */}
      <View style={{ height: 40 }} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  content: {
    padding: 16,
    gap: 16,
  },
  loadingContainer: {
    flex: 1,
    backgroundColor: palette.background,
    alignItems: 'center',
    justifyContent: 'center',
  },
  scoreSection: {
    alignItems: 'center',
    gap: 10,
    paddingVertical: 10,
  },
  locationName: {
    fontFamily: 'PlayfairDisplay-Bold',
    color: palette.text,
    fontSize: 22,
    fontWeight: '400',
    textAlign: 'center',
    letterSpacing: -0.3,
  },
  locationSubtitle: {
    color: palette.textMuted,
    fontSize: 14,
  },
  busynessRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginTop: 8,
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
  },
  busynessDots: {
    flexDirection: 'row',
    gap: 3,
  },
  busynessDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  busynessText: {
    color: palette.success,
    fontSize: 12,
    fontWeight: '600',
  },
  busynessDetail: {
    color: palette.textMuted,
    fontSize: 11,
    marginLeft: 'auto',
  },
  conditionBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 8,
    paddingHorizontal: 16,
    paddingVertical: 8,
    marginBottom: 4,
  },
  conditionBadgeIcon: {
    // kept for layout compatibility
  },
  conditionBadgeLabel: {
    fontSize: 15,
    fontWeight: '700',
  },
  card: {
    backgroundColor: palette.surface,
    borderRadius: 12,
    padding: 16,
    gap: 12,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  cardTitle: {
    ...typeStyles.sectionHeader,
    color: palette.text,
  },
  breakdownList: {
    gap: 12,
  },
  depthMapPlaceholder: {
    height: 160,
    borderRadius: 8,
    overflow: 'hidden',
    position: 'relative',
  },
  depthGradient: {
    flex: 1,
    flexDirection: 'row',
  },
  depthBand: {
    height: '100%',
  },
  depthOverlay: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.75)',
    gap: 6,
  },
  depthIcon: {
    fontSize: 28,
  },
  depthText: {
    color: palette.water,
    fontSize: 14,
    fontWeight: '700',
  },
  depthSubtext: {
    color: palette.textMuted,
    fontSize: 12,
    textAlign: 'center',
  },
  ctaButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: palette.accent,
    borderRadius: 8,
    paddingVertical: 16,
  },
  ctaPressed: {
    opacity: 0.85,
  },
  ctaIcon: {
    fontSize: 20,
  },
  ctaText: {
    color: '#fff',
    fontSize: 17,
    fontWeight: '700',
  },

  // ── Access & Parking ────────────────────────────────────────────
  accessGrid: {
    gap: 10,
  },
  accessItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  accessIconBg: {
    width: 36,
    height: 36,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  accessLabel: {
    color: palette.textMuted,
    fontSize: 11,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
  accessValue: {
    color: palette.text,
    fontSize: 14,
    fontWeight: '500',
  },

  // ── Site Tags ──────────────────────────────────────────────────
  tagRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  siteTag: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: palette.accentDim,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 6,
  },
  siteTagText: {
    color: palette.accent,
    fontSize: 12,
    fontWeight: '600',
  },

  // ── Reviews ────────────────────────────────────────────────────
  reviewHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  reviewRatingBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
  },
  reviewRatingText: {
    color: palette.text,
    fontSize: 14,
    fontWeight: '700',
  },
  reviewCountText: {
    color: palette.textMuted,
    fontSize: 12,
  },
  reviewItem: {
    gap: 4,
  },
  reviewItemHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  reviewUser: {
    color: palette.text,
    fontSize: 13,
    fontWeight: '600',
  },
  reviewStars: {
    flexDirection: 'row',
    gap: 1,
  },
  reviewDate: {
    color: palette.textMuted,
    fontSize: 11,
    marginLeft: 'auto',
  },
  reviewText: {
    color: palette.textSecondary,
    fontSize: 13,
    lineHeight: 19,
  },
  reviewCTA: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: palette.borderLight,
    marginTop: 4,
  },
  reviewCTAText: {
    color: palette.accent,
    fontSize: 14,
    fontWeight: '600',
  },

  // ── Species Activity ────────────────────────────────────────────
  divider: {
    height: 1,
    backgroundColor: palette.borderLight,
    marginVertical: 10,
  },
  speciesRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 12,
  },
  speciesLeft: {
    flex: 1,
    gap: 3,
  },
  speciesName: {
    color: palette.text,
    fontSize: 14,
    fontWeight: '600',
  },
  speciesMeta: {
    color: palette.textMuted,
    fontSize: 12,
  },
  confidenceTrack: {
    height: 3,
    borderRadius: 2,
    backgroundColor: palette.borderLight,
    marginTop: 5,
    overflow: 'hidden',
  },
  confidenceFill: {
    height: '100%',
    borderRadius: 2,
  },
  activityBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 5,
    flexShrink: 0,
  },
  activityDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  activityLabel: {
    fontSize: 12,
    fontWeight: '600',
  },

  // ── Lure Recommendations ────────────────────────────────────────
  lureList: {
    gap: 10,
  },
  lureMiniCard: {
    backgroundColor: palette.background,
    borderRadius: 8,
    padding: 12,
    gap: 6,
    borderWidth: 1,
    borderColor: palette.borderLight,
  },
  lureHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 8,
  },
  lureNameRow: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 6,
  },
  lureName: {
    color: palette.text,
    fontSize: 14,
    fontWeight: '700',
  },
  lureTypePill: {
    backgroundColor: palette.accentDim,
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  lureTypeText: {
    color: palette.accent,
    fontSize: 11,
    fontWeight: '600',
  },
  matchBadge: {
    backgroundColor: palette.accent,
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 3,
    flexShrink: 0,
  },
  matchBadgeText: {
    color: '#fff',
    fontSize: 12,
    fontWeight: '700',
  },
  lureColorRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  colorDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    borderWidth: 1,
    borderColor: palette.border,
  },
  lureColorText: {
    color: palette.textSecondary,
    fontSize: 12,
    fontWeight: '500',
  },
  lureTechnique: {
    color: palette.textSecondary,
    fontSize: 12,
    fontStyle: 'italic',
    lineHeight: 17,
  },
  lureReason: {
    color: palette.textMuted,
    fontSize: 11,
    lineHeight: 16,
  },

  // ── Water Conditions ────────────────────────────────────────────
  waterCardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  trendPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  trendPillIcon: {
    fontSize: 13,
    fontWeight: '700',
  },
  trendPillText: {
    fontSize: 12,
    fontWeight: '700',
  },
  waterStatsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: palette.background,
    borderRadius: 8,
    paddingVertical: 12,
    paddingHorizontal: 12,
  },
  waterStat: {
    flex: 1,
    alignItems: 'center',
    gap: 1,
  },
  waterChangeRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 2,
  },
  waterStatValue: {
    color: palette.text,
    fontSize: 20,
    fontWeight: '700',
    lineHeight: 24,
  },
  waterChangeArrow: {
    fontSize: 14,
    fontWeight: '700',
    lineHeight: 22,
  },
  waterStatUnit: {
    color: palette.textMuted,
    fontSize: 10,
    fontWeight: '600',
  },
  waterStatLabel: {
    color: palette.textMuted,
    fontSize: 10,
    fontWeight: '500',
    marginTop: 2,
    textAlign: 'center',
  },
  waterStatDivider: {
    width: 1,
    height: 36,
    backgroundColor: palette.border,
    marginHorizontal: 4,
  },
  waterFooter: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    justifyContent: 'space-between',
  },
  waterFooterLeft: {
    gap: 2,
    flex: 1,
  },
  waterStationName: {
    color: palette.textSecondary,
    fontSize: 12,
    fontWeight: '600',
  },
  waterDistance: {
    color: palette.textMuted,
    fontSize: 11,
  },
  waterUpdated: {
    color: palette.textMuted,
    fontSize: 11,
    textAlign: 'right',
  },
  waterSource: {
    color: palette.textDim,
    fontSize: 10,
    fontWeight: '500',
    letterSpacing: 0.3,
  },

  // ── Layer Breakdown Card ──────────────────────────────────────
  layerCardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  modelBadge: {
    backgroundColor: palette.accentDim,
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  modelBadgeText: {
    color: palette.accent,
    fontSize: 11,
    fontWeight: '600',
  },
  layerConfidence: {
    color: palette.textMuted,
    fontSize: 12,
    fontWeight: '600',
    marginBottom: 4,
  },
  layerRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
    paddingVertical: 8,
    borderTopWidth: 1,
    borderTopColor: palette.borderLight,
  },
  layerIcon: {
    width: 26,
    alignItems: 'center',
    paddingTop: 2,
  },
  layerInfo: {
    flex: 1,
    gap: 3,
  },
  layerNameRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  layerLabel: {
    color: palette.text,
    fontSize: 14,
    fontWeight: '600',
  },
  qualityPill: {
    borderRadius: 8,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  qualityPillText: {
    fontSize: 10,
    fontWeight: '700',
  },
  layerDesc: {
    color: palette.textMuted,
    fontSize: 12,
    lineHeight: 16,
  },
  layerBarTrack: {
    height: 3,
    borderRadius: 2,
    backgroundColor: palette.borderLight,
    marginTop: 4,
    overflow: 'hidden',
  },
  layerBarFill: {
    height: '100%',
    borderRadius: 2,
  },
  layerContrib: {
    color: palette.accent,
    fontSize: 14,
    fontWeight: '700',
    minWidth: 32,
    textAlign: 'right',
    paddingTop: 2,
  },
  layerTotal: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderTopWidth: 1,
    borderTopColor: palette.border,
    paddingTop: 10,
    marginTop: 4,
  },
  layerTotalLabel: {
    color: palette.textSecondary,
    fontSize: 13,
    fontWeight: '600',
  },
  layerTotalValue: {
    color: palette.text,
    fontSize: 16,
    fontWeight: '700',
  },

  // ── Species Distribution Chart ────────────────────────────────────
  speciesBarRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 8,
  },
  speciesBarName: {
    width: 100,
    fontSize: 12,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  speciesBarTrack: {
    flex: 1,
    height: 10,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 5,
    overflow: 'hidden',
  },
  speciesBarFill: {
    height: 10,
    borderRadius: 5,
  },
  speciesBarPct: {
    width: 32,
    fontSize: 12,
    fontWeight: '700',
    color: palette.text,
    textAlign: 'right',
  },
  speciesHint: {
    fontSize: 11,
    color: palette.textDim,
    marginTop: 4,
  },

  // ── Pressure Chart ────────────────────────────────────────────────
  pressureChartHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  pressureNowBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
  },
  pressureNowText: {
    fontSize: 12,
    fontWeight: '700',
  },
  pressureChartDesc: {
    fontSize: 12,
    color: palette.textSecondary,
    lineHeight: 16,
    marginBottom: 4,
  },
  pressureBars: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    height: 80,
    gap: 1,
    marginBottom: 16,
  },
  pressureBarCol: {
    flex: 1,
    alignItems: 'center',
  },
  pressureBarWrapper: {
    width: '100%',
    height: 64,
    justifyContent: 'flex-end',
  },
  pressureBar: {
    width: '100%',
    borderRadius: 2,
    minHeight: 2,
  },
  pressureBarLabel: {
    fontSize: 8,
    color: palette.textDim,
    marginTop: 4,
    position: 'absolute' as const,
    bottom: -14,
  },
  pressurePeakHint: {
    fontSize: 11,
    color: palette.textMuted,
    marginTop: 4,
  },

  // ── Best Times Card ───────────────────────────────────────────────
  bestTimeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 8,
  },
  bestTimeDivider: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: palette.borderLight,
  },
  bestTimeLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  bestTimeDayLabel: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
    width: 72,
  },
  bestTimeRatingBadge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 8,
  },
  bestTimeRating: {
    fontSize: 12,
    fontWeight: '700',
  },
  bestTimeRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  bestTimeWindow: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.text,
  },
  bestTimeNone: {
    fontSize: 12,
    color: palette.textMuted,
  },
  bestTimeHint: {
    fontSize: 11,
    color: palette.textDim,
    marginTop: 6,
  },

  // ── Water Insights Card ───────────────────────────────────────────
  waterInsightHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 8,
  },
  waterCondBadge: {
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: 10,
  },
  waterCondText: {
    fontSize: 12,
    fontWeight: '700',
  },
  waterInsightGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 8,
  },
  waterInsightItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: 10,
  },
  waterInsightLabel: {
    fontSize: 11,
    color: palette.textMuted,
    fontWeight: '500',
  },
  waterInsightValue: {
    fontSize: 14,
    fontWeight: '700',
  },
  waterInsightSummary: {
    fontSize: 12,
    color: palette.textSecondary,
    lineHeight: 16,
  },
  waterInsightImpact: {
    fontSize: 12,
    color: palette.textMuted,
    marginTop: 4,
    fontStyle: 'italic',
  },
});
