/**
 * OpenCatch — Water Insights Dashboard Screen
 *
 * Consolidated water conditions: temperature, flow, level, clarity,
 * dissolved oxygen. Pulls from USGS gauges with estimated fallbacks.
 *
 * Competitor parity: Fishbrain "water insights" (temp, clarity, flow).
 * OpenCatch EXCEEDS with real USGS data, fishing impact analysis, and
 * dissolved oxygen reporting.
 */

import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import {
  getWaterInsights,
  conditionColor,
  type WaterInsightsDashboard,
  type WaterInsight,
  type TrendDirection,
  type WeatherAdvisory,
} from '../services/waterInsights';
import {
  isCanadianLocation,
  getNearestCanadianStation,
  getCanadianWaterTemp,
  getCanadianStreamflow,
} from '../services/canadaData';

// ── Trend Arrow ──────────────────────────────────────────────────────────────

function TrendArrow({ trend }: { trend?: TrendDirection }) {
  if (!trend) return null;
  const icon = trend === 'rising' ? 'arrow-up' : trend === 'falling' ? 'arrow-down' : 'remove';
  const color = trend === 'rising' ? '#EF5350' : trend === 'falling' ? '#42A5F5' : palette.textMuted;
  return <Ionicons name={icon as any} size={14} color={color} />;
}

// ── Quality Badge ────────────────────────────────────────────────────────────

function QualityBadge({ quality }: { quality?: 'good' | 'moderate' | 'poor' }) {
  if (!quality) return null;
  const config = {
    good: { label: 'Good', color: '#2E7D32', bg: '#E8F5E9' },
    moderate: { label: 'Fair', color: '#F57F17', bg: '#FFF8E1' },
    poor: { label: 'Poor', color: '#C62828', bg: '#FFEBEE' },
  };
  const c = config[quality];
  return (
    <View style={[styles.qualityBadge, { backgroundColor: c.bg }]}>
      <Text style={[styles.qualityText, { color: c.color }]}>{c.label}</Text>
    </View>
  );
}

// ── Insight Card ─────────────────────────────────────────────────────────────

function InsightCard({ insight }: { insight: WaterInsight }) {
  return (
    <View style={styles.insightCard}>
      <View style={styles.insightHeader}>
        <View style={[styles.insightIconCircle, { backgroundColor: insight.color + '15' }]}>
          <Ionicons name={insight.icon as any} size={22} color={insight.color} />
        </View>
        <View style={styles.insightMeta}>
          <Text style={styles.insightLabel}>{insight.label}</Text>
          <View style={styles.insightValueRow}>
            <Text style={[styles.insightValue, { color: insight.color }]}>
              {insight.value}
            </Text>
            {insight.unit ? (
              <Text style={styles.insightUnit}>{insight.unit}</Text>
            ) : null}
            <TrendArrow trend={insight.trend} />
          </View>
        </View>
        <QualityBadge quality={insight.quality} />
      </View>
      {insight.detail ? (
        <Text style={styles.insightDetail}>{insight.detail}</Text>
      ) : null}
    </View>
  );
}

// ── Main Screen ──────────────────────────────────────────────────────────────

export function WaterInsightsScreen({ route }: any) {
  const paramLat = route?.params?.lat as number | undefined;
  const paramLon = route?.params?.lon as number | undefined;
  const [dashboard, setDashboard] = useState<WaterInsightsDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [locationName, setLocationName] = useState('Your Location');
  const [canadianData, setCanadianData] = useState<{ stationName?: string; waterTempC?: number; flowCms?: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        let lat = paramLat;
        let lon = paramLon;

        // Use passed coordinates or get current location
        if (lat == null || lon == null) {
          try {
            const { status } = await Location.requestForegroundPermissionsAsync();
            if (status === 'granted') {
              const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
              lat = pos.coords.latitude;
              lon = pos.coords.longitude;
            }
          } catch {
            // Fall back to central US
          }
        }

        if (lat == null || lon == null) {
          lat = 37.0;
          lon = -95.0;
          if (!cancelled) setLocationName('Default Location');
        }

        const data = await getWaterInsights(lat, lon, {
          locationName: locationName,
          airTempF: 68,
        });
        if (!cancelled) setDashboard(data);

        // Supplement with Canadian data if location is in Canada
        if (isCanadianLocation(lat, lon)) {
          try {
            const station = await getNearestCanadianStation(lat, lon);
            if (station && !cancelled) {
              const [tempResult, flowResult] = await Promise.all([
                getCanadianWaterTemp(lat, lon).catch(() => null),
                getCanadianStreamflow(station.id).catch(() => null),
              ]);
              setCanadianData({
                stationName: station.name,
                waterTempC: tempResult?.[0]?.waterTempC ?? undefined,
                flowCms: flowResult?.[0]?.discharge ?? undefined,
              });
            }
          } catch {
            // Canadian data is supplemental
          }
        }
      } catch {
        // Will show empty state
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [paramLat, paramLon]);

  if (loading) {
    return (
      <View style={[styles.screen, styles.center]}>
        <ActivityIndicator size="large" color={palette.accent} />
        <Text style={styles.loadingText}>Fetching water data...</Text>
      </View>
    );
  }

  if (!dashboard) {
    return (
      <View style={[styles.screen, styles.center]}>
        <Ionicons name="water-outline" size={48} color={palette.textMuted} />
        <Text style={styles.emptyTitle}>No Data Available</Text>
        <Text style={styles.emptyText}>Water insights will be available when USGS gauges are nearby.</Text>
      </View>
    );
  }

  const condColor = conditionColor(dashboard.overallCondition);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {/* Condition Hero */}
      <View style={[styles.heroCard, { borderColor: condColor + '40' }]}>
        <View style={styles.heroHeader}>
          <Text style={styles.heroLocation}>{dashboard.locationName}</Text>
          <Text style={styles.heroTimestamp}>
            {dashboard.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </Text>
        </View>

        <View style={styles.heroCondRow}>
          <View style={[styles.condBadge, { backgroundColor: condColor + '15' }]}>
            <View style={[styles.condDot, { backgroundColor: condColor }]} />
            <Text style={[styles.condLabel, { color: condColor }]}>
              {!dashboard.hasRealGaugeData
                ? 'No water data available nearby'
                : `${dashboard.overallCondition.charAt(0).toUpperCase() + dashboard.overallCondition.slice(1)} Water Conditions`}
            </Text>
          </View>
        </View>

        {/* Weather advisory */}
        {dashboard.weatherAdvisory && (
          <View style={[styles.weatherAdvisory, {
            backgroundColor: dashboard.weatherAdvisory.severity === 'danger' ? '#FFEBEE'
              : dashboard.weatherAdvisory.severity === 'warning' ? '#FFF3E0' : '#E3F2FD',
          }]}>
            <Text style={[styles.weatherAdvisoryText, {
              color: dashboard.weatherAdvisory.severity === 'danger' ? '#C62828'
                : dashboard.weatherAdvisory.severity === 'warning' ? '#E65100' : '#1565C0',
            }]}>
              {dashboard.weatherAdvisory.message}
            </Text>
          </View>
        )}

        <Text style={styles.heroImpact}>{dashboard.fishingImpact}</Text>
      </View>

      {/* Insights Grid */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Water Conditions</Text>
        {dashboard.insights.map((insight, i) => (
          <InsightCard key={i} insight={insight} />
        ))}
      </View>

      {/* Canadian Water Data (if in Canada) */}
      {canadianData && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Canadian Water Data</Text>
          <View style={styles.card}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <Ionicons name="flag-outline" size={16} color="#D32F2F" />
              <Text style={{ fontSize: 13, fontWeight: '600', color: palette.text }}>
                {canadianData.stationName || 'Canadian Station'}
              </Text>
            </View>
            <View style={{ flexDirection: 'row', gap: 16 }}>
              {canadianData.waterTempC != null && (
                <View style={{ alignItems: 'center' }}>
                  <Text style={{ fontSize: 18, fontWeight: '700', color: palette.text }}>
                    {(canadianData.waterTempC * 9/5 + 32).toFixed(0)}{'\u00B0'}F
                  </Text>
                  <Text style={{ fontSize: 11, color: palette.textMuted }}>Water Temp</Text>
                </View>
              )}
              {canadianData.flowCms != null && (
                <View style={{ alignItems: 'center' }}>
                  <Text style={{ fontSize: 18, fontWeight: '700', color: palette.text }}>
                    {(canadianData.flowCms * 35.3147).toFixed(0)} cfs
                  </Text>
                  <Text style={{ fontSize: 11, color: palette.textMuted }}>Streamflow</Text>
                </View>
              )}
            </View>
            <Text style={{ fontSize: 11, color: palette.textDim, marginTop: 6 }}>
              Source: Water Survey of Canada (HYDAT)
            </Text>
          </View>
        </View>
      )}

      {/* Fishing Impact Summary */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Fishing Impact</Text>
        <View style={styles.impactCard}>
          <Ionicons name="fish-outline" size={24} color={palette.accent} />
          <Text style={styles.impactText}>{dashboard.summary}</Text>
        </View>
      </View>

      {/* Tips */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Tips for Today</Text>
        <View style={styles.card}>
          {dashboard.insights.some((i) => i.label === 'Clarity' && i.quality === 'poor') && (
            <TipRow icon="color-palette-outline" text="Muddy water: use bright colors, rattling baits, and scent-based presentations." />
          )}
          {dashboard.insights.some((i) => i.label === 'Clarity' && i.quality === 'good') && (
            <TipRow icon="eye-outline" text="Clear water: use natural colors and finesse presentations. Fish may be spooky." />
          )}
          {dashboard.insights.some((i) => i.label === 'Water Temp' && i.quality === 'good') && (
            <TipRow icon="thermometer-outline" text="Water temp is in the sweet spot. Fish should be actively feeding." />
          )}
          {dashboard.insights.some((i) => i.label === 'Dissolved O2' && i.quality === 'poor') && (
            <TipRow icon="alert-circle-outline" text="Low oxygen levels. Fish will concentrate near inflows and aerated areas." />
          )}
          {dashboard.insights.some((i) => i.label === 'Flow Rate' && i.trend === 'rising') && (
            <TipRow icon="water-outline" text="Rising water levels. Fish current breaks and eddies. Safety first." />
          )}
          <TipRow icon="location-outline" text="Check multiple spots. Conditions can vary significantly within a lake or river." />
        </View>
      </View>

      <View style={{ height: 40 }} />
    </ScrollView>
  );
}

function TipRow({ icon, text }: { icon: string; text: string }) {
  return (
    <View style={styles.tipRow}>
      <Ionicons name={icon as any} size={18} color={palette.accent} />
      <Text style={styles.tipText}>{text}</Text>
    </View>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: palette.background },
  content: { padding: 20, gap: 20 },
  center: { justifyContent: 'center', alignItems: 'center' },

  loadingText: {
    fontSize: 14,
    color: palette.textMuted,
    marginTop: 12,
  },
  emptyTitle: {
    ...typeStyles.sectionHeader,
    color: palette.text,
    marginTop: 16,
  },
  emptyText: {
    fontSize: 14,
    color: palette.textMuted,
    textAlign: 'center',
    marginTop: 8,
    paddingHorizontal: 40,
  },

  heroCard: {
    backgroundColor: palette.surface,
    borderRadius: 16,
    padding: 20,
    borderWidth: 1.5,
    gap: 12,
  },
  heroHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  heroLocation: {
    ...typeStyles.sectionHeader,
    color: palette.text,
  },
  heroTimestamp: {
    fontSize: 12,
    color: palette.textMuted,
  },
  heroCondRow: {
    flexDirection: 'row',
  },
  condBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
  },
  condDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  condLabel: {
    fontSize: 14,
    fontWeight: '700',
  },
  heroImpact: {
    fontSize: 14,
    color: palette.textSecondary,
    lineHeight: 20,
  },
  weatherAdvisory: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 10,
  },
  weatherAdvisoryText: {
    fontSize: 13,
    fontWeight: '700',
  },

  section: { gap: 10 },
  sectionTitle: {
    ...typeStyles.sectionHeader,
    color: palette.text,
    paddingLeft: 4,
  },

  insightCard: {
    backgroundColor: palette.surface,
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: palette.border,
    gap: 8,
  },
  insightHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  insightIconCircle: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
  },
  insightMeta: {
    flex: 1,
  },
  insightLabel: {
    fontSize: 12,
    color: palette.textMuted,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
  insightValueRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 4,
    marginTop: 2,
  },
  insightValue: {
    fontSize: 24,
    fontWeight: '700',
  },
  insightUnit: {
    fontSize: 14,
    color: palette.textMuted,
  },
  insightDetail: {
    fontSize: 13,
    color: palette.textMuted,
    marginLeft: 56,
  },

  qualityBadge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  qualityText: {
    fontSize: 11,
    fontWeight: '700',
  },

  impactCard: {
    backgroundColor: palette.accentDim,
    borderRadius: 12,
    padding: 16,
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 12,
  },
  impactText: {
    flex: 1,
    fontSize: 14,
    color: palette.text,
    lineHeight: 20,
  },

  card: {
    backgroundColor: palette.surface,
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: palette.border,
  },
  tipRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
    paddingVertical: 8,
  },
  tipText: {
    flex: 1,
    fontSize: 13,
    color: palette.textSecondary,
    lineHeight: 19,
  },
});
