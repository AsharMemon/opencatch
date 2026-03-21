import React, { useEffect, useState } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  ActivityIndicator,
  Pressable,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette, scoreColor, scoreLabel, getConditionBand, conditionConfig } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { ScoreGauge } from '../components/ScoreGauge';
import { ScoreBreakdownBar } from '../components/ScoreBreakdownBar';
import { WeatherCard } from '../components/WeatherCard';
import { WeatherForecastSection } from '../components/WeatherForecastSection';
import { ForecastChart } from '../components/ForecastChart';
import { ExplanationCard } from '../components/ExplanationCard';
import { api } from '../services/api';
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

type Props = RootStackProps<'LocationDetail'>;

// ── Activity helpers ──────────────────────────────────────────────
function activityColor(level: ActivityLevel): string {
  switch (level) {
    case 'very-active': return palette.pinHot;
    case 'active':      return palette.pinHeatingUp;
    case 'moderate':    return palette.pinFair;
    case 'slow':        return palette.pinSlow;
    case 'inactive':    return palette.textDim;
  }
}

function activityLabel(level: ActivityLevel): string {
  switch (level) {
    case 'very-active': return 'Very Active';
    case 'active':      return 'Active';
    case 'moderate':    return 'Moderate';
    case 'slow':        return 'Slow';
    case 'inactive':    return 'Inactive';
  }
}

// ── Species Activity Section ──────────────────────────────────────
function SpeciesActivityCard({ data }: { data: SpeciesActivity[] }) {
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
  const { breakdown } = prediction;
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

export function LocationDetailScreen({ route, navigation }: Props) {
  const { locationId } = route.params;
  const [location, setLocation] = useState<FishingLocation | null>(null);
  const [loading, setLoading] = useState(true);
  const [prediction, setPrediction] = useState<PredictV2Response | null>(null);
  const [predictionLoading, setPredictionLoading] = useState(false);
  const [predictionError, setPredictionError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    api.getLocation(locationId).then((loc) => {
      if (cancelled) return;
      setLocation(loc ?? null);
      setLoading(false);
      if (loc) {
        navigation.setOptions({ title: loc.name });

        // Fetch v2 prediction in parallel
        setPredictionLoading(true);
        const today = new Date().toISOString().slice(0, 10);
        api.predictV2(loc.name, today).then((pred) => {
          if (!cancelled) {
            setPrediction(pred);
            setPredictionLoading(false);
          }
        }).catch((err) => {
          if (!cancelled) {
            setPredictionError(err?.message ?? 'Prediction unavailable');
            setPredictionLoading(false);
          }
        });
      }
    });

    return () => { cancelled = true; };
  }, [locationId]);

  if (loading || !location) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator color={palette.accent} size="large" />
      </View>
    );
  }

  const { scoreBreakdown } = location;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {/* Score section */}
      <View style={styles.scoreSection}>
        {/* Condition band badge */}
        {(() => {
          const band = getConditionBand(location.score);
          const cfg = conditionConfig[band];
          return (
            <View style={[styles.conditionBadge, { backgroundColor: cfg.bgTint }]}>
              <Ionicons name={cfg.ionicon as any} size={18} color={cfg.color} />
              <Text style={[styles.conditionBadgeLabel, { color: cfg.color }]}>{cfg.label}</Text>
            </View>
          );
        })()}
        <ScoreGauge score={location.score} size={180} />
        <Text style={styles.locationName}>{location.name}</Text>
        <Text style={styles.locationSubtitle}>{location.subtitle}</Text>

        {/* Busyness / Popularity indicator */}
        <View style={styles.busynessRow}>
          <View style={styles.busynessDots}>
            {[1, 2, 3, 4, 5].map((level) => (
              <View
                key={level}
                style={[
                  styles.busynessDot,
                  { backgroundColor: level <= 2 ? palette.success : palette.surfaceRaised },
                ]}
              />
            ))}
          </View>
          <Text style={styles.busynessText}>Low activity</Text>
          <Text style={styles.busynessDetail}>3 catches logged this week</Text>
        </View>
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
      <ExplanationCard explanation={prediction?.explanation ?? location.explanation} />

      {/* 7-Day Forecast */}
      <ForecastChart forecast={location.forecast} />

      {/* Current Conditions */}
      <WeatherCard conditions={location.conditions} />

      {/* Detailed Weather Forecast */}
      <WeatherForecastSection location={location} />

      {/* Water Conditions (USGS) */}
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

      {/* Access & Parking */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Access & Parking</Text>
        <View style={styles.accessGrid}>
          <View style={styles.accessItem}>
            <View style={[styles.accessIconBg, { backgroundColor: 'rgba(61, 139, 55, 0.08)' }]}>
              <Ionicons name="car-outline" size={18} color={palette.success} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.accessLabel}>Parking</Text>
              <Text style={styles.accessValue}>Free lot, 30+ spots</Text>
            </View>
          </View>
          <View style={styles.accessItem}>
            <View style={[styles.accessIconBg, { backgroundColor: 'rgba(10, 110, 189, 0.08)' }]}>
              <Ionicons name="boat-outline" size={18} color={palette.accent} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.accessLabel}>Boat Launch</Text>
              <Text style={styles.accessValue}>Public ramp, no fee</Text>
            </View>
          </View>
          <View style={styles.accessItem}>
            <View style={[styles.accessIconBg, { backgroundColor: 'rgba(251, 140, 0, 0.08)' }]}>
              <Ionicons name="walk-outline" size={18} color={palette.warning} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.accessLabel}>Shore Access</Text>
              <Text style={styles.accessValue}>0.2 mi trail from lot</Text>
            </View>
          </View>
          <View style={styles.accessItem}>
            <View style={[styles.accessIconBg, { backgroundColor: 'rgba(120, 144, 156, 0.08)' }]}>
              <Ionicons name="globe-outline" size={18} color={palette.pinSlow} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.accessLabel}>Land Type</Text>
              <Text style={styles.accessValue}>Public — State Park</Text>
            </View>
          </View>
        </View>
      </View>

      {/* Site Tags */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Site Info</Text>
        <View style={styles.tagRow}>
          {['Shore Fishing', 'Boat Launch', 'Kayak Friendly', 'Family Friendly', 'Wheelchair Access'].map((tag) => (
            <View key={tag} style={styles.siteTag}>
              <Ionicons
                name={
                  tag === 'Shore Fishing' ? 'walk-outline' :
                  tag === 'Boat Launch' ? 'boat-outline' :
                  tag === 'Kayak Friendly' ? 'water-outline' :
                  tag === 'Family Friendly' ? 'people-outline' :
                  'accessibility-outline'
                }
                size={12}
                color={palette.accent}
              />
              <Text style={styles.siteTagText}>{tag}</Text>
            </View>
          ))}
        </View>
      </View>

      {/* Community Reviews */}
      <View style={styles.card}>
        <View style={styles.reviewHeader}>
          <Text style={styles.cardTitle}>Reviews</Text>
          <View style={styles.reviewRatingBadge}>
            <Ionicons name="star" size={13} color="#F9A825" />
            <Text style={styles.reviewRatingText}>4.3</Text>
            <Text style={styles.reviewCountText}>(47)</Text>
          </View>
        </View>
        {[
          { user: 'BassMaster_TX', rating: 5, date: 'Mar 14', text: 'Incredible morning bite. Caught 8 largemouth on topwater before 9 AM. Parking lot was only half full.' },
          { user: 'FishOn_Mike', rating: 4, date: 'Mar 10', text: 'Good shore fishing access on the north end. Water was a bit murky but still caught a few. Boat ramp is well maintained.' },
          { user: 'WeekendAngler', rating: 3, date: 'Mar 6', text: 'Decent spot but gets crowded on weekends. Try early mornings for best results. Trail to the water is easy.' },
        ].map((review, i) => (
          <View key={i} style={[styles.reviewItem, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.borderLight, paddingTop: 10 }]}>
            <View style={styles.reviewItemHeader}>
              <Text style={styles.reviewUser}>{review.user}</Text>
              <View style={styles.reviewStars}>
                {Array.from({ length: 5 }, (_, j) => (
                  <Ionicons key={j} name={j < review.rating ? 'star' : 'star-outline'} size={11} color={j < review.rating ? '#F9A825' : palette.textDim} />
                ))}
              </View>
              <Text style={styles.reviewDate}>{review.date}</Text>
            </View>
            <Text style={styles.reviewText}>{review.text}</Text>
          </View>
        ))}
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
    color: palette.text,
    fontSize: 22,
    fontWeight: '600',
    textAlign: 'center',
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
    borderRadius: 10,
    padding: 16,
    gap: 12,
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
});
