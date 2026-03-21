/**
 * OpenCatch — Weather Buoys Screen (C6)
 *
 * Displays real-time NDBC weather buoy data:
 * - Nearby buoys sorted by distance
 * - Wave height, water temp, wind, pressure
 * - Wave condition descriptions
 * - Expandable detail cards
 */

import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';

// ── Types (inline until weatherBuoys service is wired) ───────────────────────

interface BuoyData {
  id: string;
  name: string;
  distanceMiles: number;
  lat: number;
  lon: number;
  waterTemp?: number;    // °F
  airTemp?: number;      // °F
  waveHeight?: number;   // ft
  wavePeriod?: number;   // sec
  windSpeed?: number;    // knots
  windDirection?: number;// deg
  gustSpeed?: number;    // knots
  pressure?: number;     // hPa
  visibility?: number;   // nm
  timestamp?: string;
}

// ── Mock data (will be replaced with weatherBuoys service) ───────────────────

const MOCK_BUOYS: BuoyData[] = [
  {
    id: '45007',
    name: 'Michigan City, IN',
    distanceMiles: 12.3,
    lat: 42.674,
    lon: -87.026,
    waterTemp: 48,
    airTemp: 52,
    waveHeight: 2.3,
    wavePeriod: 5.4,
    windSpeed: 12,
    windDirection: 225,
    gustSpeed: 18,
    pressure: 1015.2,
    visibility: 10,
    timestamp: new Date().toISOString(),
  },
  {
    id: '45002',
    name: 'North Michigan',
    distanceMiles: 45.8,
    lat: 45.344,
    lon: -86.411,
    waterTemp: 44,
    airTemp: 48,
    waveHeight: 4.1,
    wavePeriod: 7.2,
    windSpeed: 18,
    windDirection: 270,
    gustSpeed: 25,
    pressure: 1012.5,
    timestamp: new Date().toISOString(),
  },
  {
    id: 'MKGM4',
    name: 'Muskegon, MI',
    distanceMiles: 28.1,
    lat: 43.228,
    lon: -86.339,
    waterTemp: 46,
    airTemp: 50,
    waveHeight: 1.8,
    wavePeriod: 4.8,
    windSpeed: 8,
    windDirection: 180,
    gustSpeed: 12,
    pressure: 1016.8,
    visibility: 15,
    timestamp: new Date().toISOString(),
  },
];

// ── Helpers ──────────────────────────────────────────────────────────────────

function waveDescription(heightFt: number): { label: string; color: string } {
  if (heightFt < 1) return { label: 'Calm', color: palette.success };
  if (heightFt < 3) return { label: 'Slight', color: '#4CAF50' };
  if (heightFt < 5) return { label: 'Moderate', color: palette.warning };
  if (heightFt < 8) return { label: 'Rough', color: '#E65100' };
  return { label: 'Very Rough', color: palette.error };
}

function windDescription(knots: number): string {
  if (knots < 5) return 'Calm';
  if (knots < 12) return 'Light';
  if (knots < 20) return 'Moderate';
  if (knots < 30) return 'Strong';
  return 'Storm';
}

function degToCardinal(deg: number): string {
  const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  return dirs[Math.round(deg / 22.5) % 16];
}

function tempColor(f: number): string {
  if (f < 40) return '#1565C0';
  if (f < 50) return '#42A5F5';
  if (f < 60) return '#4CAF50';
  if (f < 70) return '#FFA726';
  if (f < 80) return '#EF6C00';
  return '#C62828';
}

// ── Component ────────────────────────────────────────────────────────────────

export function WeatherBuoysScreen({ route }: any) {
  const [buoys, setBuoys] = useState<BuoyData[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    fetchBuoys();
  }, []);

  const fetchBuoys = async () => {
    setLoading(true);
    // TODO: Replace with real weatherBuoys.getNearbyBuoys() when service is ready
    await new Promise((r) => setTimeout(r, 800));
    setBuoys(MOCK_BUOYS);
    setLoading(false);
  };

  const onRefresh = async () => {
    setRefreshing(true);
    await fetchBuoys();
    setRefreshing(false);
  };

  const renderBuoyCard = ({ item }: { item: BuoyData }) => {
    const expanded = expandedId === item.id;
    const wave = item.waveHeight != null ? waveDescription(item.waveHeight) : null;

    return (
      <Pressable
        style={styles.card}
        onPress={() => setExpandedId(expanded ? null : item.id)}
      >
        {/* Header */}
        <View style={styles.cardHeader}>
          <View style={styles.buoyIcon}>
            <Ionicons name="radio-outline" size={20} color={palette.accent} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.buoyName}>{item.name}</Text>
            <Text style={styles.buoyMeta}>
              Station {item.id} • {item.distanceMiles.toFixed(1)} mi away
            </Text>
          </View>
          {wave && (
            <View style={[styles.waveBadge, { backgroundColor: wave.color + '18' }]}>
              <Text style={[styles.waveBadgeText, { color: wave.color }]}>{wave.label}</Text>
            </View>
          )}
        </View>

        {/* Quick stats row */}
        <View style={styles.quickStats}>
          {item.waterTemp != null && (
            <View style={styles.quickStat}>
              <Ionicons name="water" size={14} color={tempColor(item.waterTemp)} />
              <Text style={[styles.quickStatValue, { color: tempColor(item.waterTemp) }]}>
                {item.waterTemp}°F
              </Text>
              <Text style={styles.quickStatLabel}>Water</Text>
            </View>
          )}
          {item.waveHeight != null && (
            <View style={styles.quickStat}>
              <Ionicons name="pulse-outline" size={14} color={wave?.color ?? palette.text} />
              <Text style={styles.quickStatValue}>{item.waveHeight.toFixed(1)} ft</Text>
              <Text style={styles.quickStatLabel}>Waves</Text>
            </View>
          )}
          {item.windSpeed != null && (
            <View style={styles.quickStat}>
              <Ionicons name="flag-outline" size={14} color={palette.textSecondary} />
              <Text style={styles.quickStatValue}>{item.windSpeed} kts</Text>
              <Text style={styles.quickStatLabel}>Wind</Text>
            </View>
          )}
          {item.airTemp != null && (
            <View style={styles.quickStat}>
              <Ionicons name="thermometer-outline" size={14} color={tempColor(item.airTemp)} />
              <Text style={styles.quickStatValue}>{item.airTemp}°F</Text>
              <Text style={styles.quickStatLabel}>Air</Text>
            </View>
          )}
        </View>

        {/* Expanded details */}
        {expanded && (
          <View style={styles.details}>
            <View style={styles.detailDivider} />

            {item.windSpeed != null && item.windDirection != null && (
              <View style={styles.detailRow}>
                <Ionicons name="flag" size={16} color={palette.accent} />
                <Text style={styles.detailLabel}>Wind</Text>
                <Text style={styles.detailValue}>
                  {windDescription(item.windSpeed)} — {item.windSpeed} kts from {degToCardinal(item.windDirection)} ({item.windDirection}°)
                  {item.gustSpeed ? `, gusts ${item.gustSpeed} kts` : ''}
                </Text>
              </View>
            )}

            {item.waveHeight != null && (
              <View style={styles.detailRow}>
                <Ionicons name="pulse" size={16} color={palette.accent} />
                <Text style={styles.detailLabel}>Waves</Text>
                <Text style={styles.detailValue}>
                  {item.waveHeight.toFixed(1)} ft @ {item.wavePeriod?.toFixed(1) ?? '—'}s period
                </Text>
              </View>
            )}

            {item.pressure != null && (
              <View style={styles.detailRow}>
                <Ionicons name="speedometer" size={16} color={palette.accent} />
                <Text style={styles.detailLabel}>Pressure</Text>
                <Text style={styles.detailValue}>{item.pressure.toFixed(1)} hPa</Text>
              </View>
            )}

            {item.visibility != null && (
              <View style={styles.detailRow}>
                <Ionicons name="eye" size={16} color={palette.accent} />
                <Text style={styles.detailLabel}>Visibility</Text>
                <Text style={styles.detailValue}>{item.visibility} nm</Text>
              </View>
            )}

            <Text style={styles.timestamp}>
              Last updated: {item.timestamp ? new Date(item.timestamp).toLocaleTimeString() : 'Unknown'}
            </Text>
          </View>
        )}

        {/* Expand indicator */}
        <View style={styles.expandIndicator}>
          <Ionicons
            name={expanded ? 'chevron-up' : 'chevron-down'}
            size={16}
            color={palette.textDim}
          />
        </View>
      </Pressable>
    );
  };

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color={palette.accent} />
        <Text style={styles.loadingText}>Finding nearby buoys...</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Info banner */}
      <View style={styles.infoBanner}>
        <Ionicons name="information-circle-outline" size={16} color={palette.accent} />
        <Text style={styles.infoBannerText}>
          Real-time data from NOAA National Data Buoy Center
        </Text>
      </View>

      <FlatList
        data={buoys}
        keyExtractor={(item) => item.id}
        renderItem={renderBuoyCard}
        contentContainerStyle={styles.listContent}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={palette.accent} />
        }
        ListEmptyComponent={
          <View style={styles.emptyState}>
            <Ionicons name="radio-outline" size={48} color={palette.textDim} />
            <Text style={styles.emptyTitle}>No Buoys Nearby</Text>
            <Text style={styles.emptySubtitle}>
              Weather buoys are primarily located in coastal and Great Lakes waters
            </Text>
          </View>
        }
      />
    </View>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: palette.background,
    gap: 12,
  },
  loadingText: {
    fontSize: 14,
    color: palette.textMuted,
  },
  listContent: {
    padding: 16,
    paddingBottom: 40,
  },

  // Info banner
  infoBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 10,
    backgroundColor: palette.accentLight,
  },
  infoBannerText: {
    fontSize: 12,
    color: palette.accent,
    flex: 1,
  },

  // Card
  card: {
    backgroundColor: palette.surface,
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    marginBottom: 12,
  },
  buoyIcon: {
    width: 40,
    height: 40,
    borderRadius: 12,
    backgroundColor: palette.accentDim,
    justifyContent: 'center',
    alignItems: 'center',
  },
  buoyName: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
  },
  buoyMeta: {
    fontSize: 12,
    color: palette.textMuted,
    marginTop: 1,
  },
  waveBadge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 8,
  },
  waveBadgeText: {
    fontSize: 12,
    fontWeight: '700',
  },

  // Quick stats
  quickStats: {
    flexDirection: 'row',
    justifyContent: 'space-around',
  },
  quickStat: {
    alignItems: 'center',
    gap: 2,
  },
  quickStatValue: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
  },
  quickStatLabel: {
    fontSize: 10,
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },

  // Expanded details
  details: {
    marginTop: 12,
  },
  detailDivider: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: palette.border,
    marginBottom: 12,
  },
  detailRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    marginBottom: 10,
  },
  detailLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textSecondary,
    width: 70,
  },
  detailValue: {
    fontSize: 13,
    color: palette.text,
    flex: 1,
  },
  timestamp: {
    fontSize: 11,
    color: palette.textDim,
    marginTop: 8,
    fontStyle: 'italic',
  },

  // Expand indicator
  expandIndicator: {
    alignItems: 'center',
    marginTop: 8,
  },

  // Empty state
  emptyState: {
    alignItems: 'center',
    paddingVertical: 60,
    gap: 8,
  },
  emptyTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: palette.text,
  },
  emptySubtitle: {
    fontSize: 14,
    color: palette.textMuted,
    textAlign: 'center',
    paddingHorizontal: 20,
  },
});
