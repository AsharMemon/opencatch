import React, { useState, useEffect, useCallback } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  Pressable,
  ActivityIndicator,
  RefreshControl,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { FeatureLocationPicker } from '../components/FeatureLocationPicker';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import {
  getCurrentFeatureLocation,
  searchFeatureLocation,
  type FeatureLocation,
} from '../services/featureLocation';
import {
  getFishingAlerts,
  type FishingWeatherAlert,
  type FishingRelevance,
} from '../services/weatherAlerts';

type AlertType = 'peak-activity' | 'pressure-drop' | 'optimal' | 'trophy' | 'weather';

interface FishingActivityAlert {
  id: string;
  type: AlertType;
  title: string;
  message: string;
  location: string;
  timeAgo: string;
  read: boolean;
}

const ALERT_IONICONS: Record<AlertType, string> = {
  'peak-activity': 'flame-outline',
  'pressure-drop': 'thermometer-outline',
  'optimal': 'sunny-outline',
  'trophy': 'trophy-outline',
  'weather': 'cloud-outline',
};

const RELEVANCE_COLORS: Record<FishingRelevance, string> = {
  dangerous: '#D32F2F',
  caution: '#F57C00',
  advisory: '#FBC02D',
  info: '#1976D2',
};

const RELEVANCE_ICONS: Record<FishingRelevance, string> = {
  dangerous: 'warning',
  caution: 'alert-circle',
  advisory: 'information-circle',
  info: 'chatbox-ellipses-outline',
};

const SAMPLE_ACTIVITY_ALERTS: FishingActivityAlert[] = [
  {
    id: 'a1',
    type: 'peak-activity',
    title: 'Peak Activity Alert',
    message: 'Score jumped to 89. Conditions aligning for excellent bass activity in the next 2 hours.',
    location: 'Lake Fork, TX',
    timeAgo: '12 min ago',
    read: false,
  },
  {
    id: 'a2',
    type: 'pressure-drop',
    title: 'Pressure Drop Incoming',
    message: 'Barometric pressure dropping 0.08 inHg over 6 hours. Bass feed aggressively before fronts.',
    location: 'Table Rock Lake, MO',
    timeAgo: '1 hr ago',
    read: false,
  },
  {
    id: 'a3',
    type: 'optimal',
    title: 'Optimal Window',
    message: 'Solunar major period + low wind + stable pressure. Best: 6:30 AM - 8:00 AM.',
    location: 'Sam Rayburn, TX',
    timeAgo: '3 hrs ago',
    read: true,
  },
];

export function AlertsScreen() {
  const [activityAlerts, setActivityAlerts] = useState<FishingActivityAlert[]>(SAMPLE_ACTIVITY_ALERTS);
  const [weatherAlerts, setWeatherAlerts] = useState<FishingWeatherAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [tab, setTab] = useState<'all' | 'weather' | 'fishing'>('all');
  const [selectedLocation, setSelectedLocation] = useState<FeatureLocation | null>(null);

  const fetchAlerts = useCallback(async (location?: FeatureLocation) => {
    try {
      const next = location ?? await getCurrentFeatureLocation({
        lat: 44.98,
        lon: -93.27,
        label: 'Default alerts area',
      });
      setSelectedLocation(next);
      const alerts = await getFishingAlerts(next.lat, next.lon);
      setWeatherAlerts(alerts);
    } catch (err) {
      console.warn('[AlertsScreen] Failed to fetch weather alerts:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await fetchAlerts();
    setRefreshing(false);
  }, [fetchAlerts]);

  const handleUseCurrentLocation = useCallback(async () => {
    setRefreshing(true);
    try {
      const location = await getCurrentFeatureLocation({
        lat: 44.98,
        lon: -93.27,
        label: 'Default alerts area',
      });
      await fetchAlerts(location);
    } finally {
      setRefreshing(false);
    }
  }, [fetchAlerts]);

  const handleSearchLocation = useCallback(async (query: string) => {
    setRefreshing(true);
    try {
      const location = await searchFeatureLocation(query);
      await fetchAlerts(location);
    } finally {
      setRefreshing(false);
    }
  }, [fetchAlerts]);

  const markRead = (id: string) => {
    setActivityAlerts((prev) =>
      prev.map((a) => (a.id === id ? { ...a, read: true } : a)),
    );
  };

  const totalAlerts = weatherAlerts.length + activityAlerts.length;
  const dangerousCount = weatherAlerts.filter((a) => a.fishingRelevance === 'dangerous').length;

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={palette.accent} />}
    >
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.title}>Alerts</Text>
        {dangerousCount > 0 && (
          <View style={styles.dangerBadge}>
            <Ionicons name="warning" size={12} color="#fff" />
            <Text style={styles.dangerBadgeText}>{dangerousCount} danger</Text>
          </View>
        )}
      </View>

      <FeatureLocationPicker
        label={selectedLocation?.label ?? 'Finding your location'}
        helperText={
          selectedLocation?.source === 'search'
            ? 'Weather alerts are filtered for the place you searched for.'
            : 'Weather alerts are filtered for this location.'
        }
        loading={loading || refreshing}
        activeSource={selectedLocation?.source}
        onUseCurrent={handleUseCurrentLocation}
        onSearchLocation={handleSearchLocation}
      />

      {/* Tab selector */}
      <View style={styles.tabRow}>
        {(['all', 'weather', 'fishing'] as const).map((t) => (
          <Pressable
            key={t}
            style={[styles.tab, tab === t && styles.tabActive]}
            onPress={() => setTab(t)}
          >
            <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
              {t === 'all' ? 'All' : t === 'weather' ? `Weather (${weatherAlerts.length})` : `Fishing (${activityAlerts.length})`}
            </Text>
          </Pressable>
        ))}
      </View>

      {/* Weather alerts section */}
      {(tab === 'all' || tab === 'weather') && weatherAlerts.length > 0 && (
        <View style={styles.section}>
          {tab === 'all' && <Text style={styles.sectionTitle}>Weather Alerts</Text>}
          {weatherAlerts.map((alert) => (
            <View
              key={alert.id}
              style={[
                styles.weatherAlertCard,
                { borderLeftColor: RELEVANCE_COLORS[alert.fishingRelevance] },
              ]}
            >
              <View style={styles.weatherAlertHeader}>
                <Ionicons
                  name={RELEVANCE_ICONS[alert.fishingRelevance] as any}
                  size={20}
                  color={RELEVANCE_COLORS[alert.fishingRelevance]}
                />
                <View style={{ flex: 1, marginLeft: 10 }}>
                  <Text style={styles.weatherAlertEvent}>{alert.event}</Text>
                  <Text style={styles.weatherAlertSeverity}>
                    {alert.severity} · {alert.fishingRelevance.toUpperCase()}
                  </Text>
                </View>
              </View>
              <Text style={styles.weatherAlertSummary}>{alert.fishingSummary}</Text>
              {alert.instruction && (
                <Text style={styles.weatherAlertInstruction}>
                  {alert.instruction.slice(0, 200)}{alert.instruction.length > 200 ? '...' : ''}
                </Text>
              )}
              <Text style={styles.weatherAlertArea}>{alert.areaDesc}</Text>
            </View>
          ))}
        </View>
      )}

      {/* Loading */}
      {loading && (
        <View style={styles.loadingRow}>
          <ActivityIndicator size="small" color={palette.accent} />
          <Text style={styles.loadingText}>Checking NWS weather alerts...</Text>
        </View>
      )}

      {/* No weather alerts */}
      {!loading && weatherAlerts.length === 0 && (tab === 'all' || tab === 'weather') && (
        <View style={styles.noWeatherCard}>
          <Ionicons name="checkmark-circle" size={24} color="#4CAF50" />
          <View style={{ flex: 1, marginLeft: 10 }}>
            <Text style={styles.noWeatherTitle}>No active weather alerts</Text>
            <Text style={styles.noWeatherSubtitle}>Conditions look clear for your area</Text>
          </View>
        </View>
      )}

      {/* Fishing activity alerts */}
      {(tab === 'all' || tab === 'fishing') && (
        <View style={styles.section}>
          {tab === 'all' && <Text style={styles.sectionTitle}>Fishing Activity</Text>}
          <View style={styles.list}>
            {activityAlerts.map((alert) => (
              <Pressable
                key={alert.id}
                style={[styles.alertCard, !alert.read && styles.alertCardUnread]}
                onPress={() => markRead(alert.id)}
              >
                <View style={styles.alertIconContainer}>
                  <Ionicons
                    name={(ALERT_IONICONS[alert.type] ?? 'notifications-outline') as any}
                    size={20}
                    color={palette.accent}
                  />
                </View>
                <View style={styles.alertContent}>
                  <View style={styles.alertHeaderRow}>
                    <Text style={styles.alertTitle}>{alert.title}</Text>
                    {!alert.read && <View style={styles.unreadDot} />}
                  </View>
                  <Text style={styles.alertMessage}>{alert.message}</Text>
                  <View style={styles.alertFooter}>
                    <Text style={styles.alertLocation}>{alert.location}</Text>
                    <Text style={styles.alertTime}>{alert.timeAgo}</Text>
                  </View>
                </View>
              </Pressable>
            ))}
          </View>
        </View>
      )}

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
    padding: 20,
    gap: 16,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  title: {
    ...typeStyles.screenTitle,
    color: palette.text,
    flex: 1,
  },
  dangerBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: '#D32F2F',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  dangerBadgeText: {
    color: '#fff',
    fontSize: 12,
    fontWeight: '700',
  },
  tabRow: {
    flexDirection: 'row',
    gap: 8,
  },
  tab: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 16,
    backgroundColor: '#F0F0EC',
  },
  tabActive: {
    backgroundColor: palette.accent,
  },
  tabText: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.text,
  },
  tabTextActive: {
    color: '#fff',
  },
  section: {
    gap: 10,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: palette.textSecondary,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  // Weather alerts
  weatherAlertCard: {
    backgroundColor: '#fff',
    borderRadius: 10,
    padding: 14,
    borderLeftWidth: 4,
    borderWidth: 1,
    borderColor: palette.border,
  },
  weatherAlertHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 8,
  },
  weatherAlertEvent: {
    fontSize: 15,
    fontWeight: '700',
    color: palette.text,
  },
  weatherAlertSeverity: {
    fontSize: 11,
    color: palette.textSecondary,
    fontWeight: '600',
    marginTop: 1,
  },
  weatherAlertSummary: {
    fontSize: 13,
    color: palette.text,
    lineHeight: 19,
    marginBottom: 6,
  },
  weatherAlertInstruction: {
    fontSize: 12,
    color: palette.textSecondary,
    fontStyle: 'italic',
    lineHeight: 17,
    marginBottom: 6,
  },
  weatherAlertArea: {
    fontSize: 11,
    color: palette.textDim,
  },
  loadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    padding: 16,
  },
  loadingText: {
    fontSize: 13,
    color: palette.textSecondary,
  },
  noWeatherCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#E8F5E9',
    borderRadius: 10,
    padding: 14,
  },
  noWeatherTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: '#2E7D32',
  },
  noWeatherSubtitle: {
    fontSize: 12,
    color: '#4CAF50',
    marginTop: 1,
  },
  // Fishing activity alerts
  list: {
    gap: 10,
  },
  alertCard: {
    backgroundColor: palette.surface,
    borderRadius: 10,
    padding: 16,
    flexDirection: 'row',
    gap: 14,
  },
  alertCardUnread: {
    backgroundColor: palette.accentLight,
  },
  alertIconContainer: {
    width: 40,
    height: 40,
    borderRadius: 12,
    backgroundColor: palette.surfaceRaised,
    alignItems: 'center',
    justifyContent: 'center',
  },
  alertContent: {
    flex: 1,
    gap: 6,
  },
  alertHeaderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  alertTitle: {
    color: palette.text,
    fontSize: 15,
    fontWeight: '700',
    flex: 1,
  },
  unreadDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: palette.accent,
  },
  alertMessage: {
    color: palette.textSecondary,
    fontSize: 13,
    lineHeight: 19,
  },
  alertFooter: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: 2,
  },
  alertLocation: {
    color: palette.accent,
    fontSize: 12,
    fontWeight: '600',
  },
  alertTime: {
    color: palette.textDim,
    fontSize: 12,
  },
});
