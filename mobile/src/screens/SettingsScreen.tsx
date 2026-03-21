import React, { useEffect, useState, useCallback } from 'react';
import {
  Alert,
  Linking,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  ActivityIndicator,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { SettingsToggle, SettingsButton } from '../components/SettingsRow';
import { api } from '../services/api';
import type { UserSettings, UnitSystem, MapStyle } from '../types/models';

const MAP_STYLE_LABELS: Record<MapStyle, string> = {
  standard: 'Standard',
  satellite: 'Satellite',
  terrain: 'Terrain',
};

export function SettingsScreen() {
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [cacheSize, setCacheSize] = useState<string>('Calculating...');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadSettings();
    estimateCacheSize();
  }, []);

  const loadSettings = async () => {
    setLoading(true);
    try {
      const s = await api.getSettings();
      setSettings(s);
    } catch {
      // Failed to load settings
    }
    setLoading(false);
  };

  const estimateCacheSize = async () => {
    try {
      const keys = await AsyncStorage.getAllKeys();
      // Rough estimate: count keys as a proxy
      const sizeMB = Math.max(0.1, keys.length * 0.01);
      setCacheSize(`${sizeMB.toFixed(1)} MB (${keys.length} items)`);
    } catch {
      setCacheSize('Unable to calculate');
    }
  };

  const updateSetting = useCallback((patch: Partial<UserSettings>) => {
    setSettings((prev) => (prev ? { ...prev, ...patch } : prev));
    api.updateSettings(patch);
  }, []);

  const toggleUnit = () => {
    if (!settings) return;
    const next: UnitSystem = settings.units === 'imperial' ? 'metric' : 'imperial';
    updateSetting({ units: next });
  };

  const cycleMapStyle = () => {
    if (!settings) return;
    const styles: MapStyle[] = ['standard', 'satellite', 'terrain'];
    const currentIdx = styles.indexOf(settings.mapStyle || 'standard');
    const next = styles[(currentIdx + 1) % styles.length];
    updateSetting({ mapStyle: next });
  };

  const handleClearCache = () => {
    Alert.alert(
      'Clear Cache',
      'This will remove cached weather data, map tiles, and search history. Your catches and settings will be kept.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Clear',
          style: 'destructive',
          onPress: async () => {
            try {
              const keys = await AsyncStorage.getAllKeys();
              // Keep critical keys
              const protectedPrefixes = ['@opencatch_settings', '@opencatch_catches', '@opencatch_auth', '@opencatch_onboarding'];
              const toRemove = keys.filter(
                (k) => !protectedPrefixes.some((p) => k.startsWith(p)),
              );
              await AsyncStorage.multiRemove(toRemove);
              Alert.alert('Done', `Cleared ${toRemove.length} cached items.`);
              estimateCacheSize();
            } catch {
              Alert.alert('Error', 'Failed to clear cache.');
            }
          },
        },
      ],
    );
  };

  const handleExportData = () => {
    Alert.alert(
      'Export Data',
      'Your catch history and trip data will be exported. Use the Catch Export screen for CSV format.',
      [{ text: 'OK' }],
    );
  };

  if (loading || !settings) {
    return (
      <View style={[styles.screen, styles.centered]}>
        <ActivityIndicator color={palette.accent} size="large" />
      </View>
    );
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {/* Units */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Units & Display</Text>
        <View style={styles.sectionCard}>
          <SettingsButton
            label="Units"
            value={settings.units === 'imperial' ? 'Imperial (F, mph)' : 'Metric (C, km/h)'}
            onPress={toggleUnit}
          />
        </View>
      </View>

      {/* Map */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Map</Text>
        <View style={styles.sectionCard}>
          <SettingsButton
            label="Default Map Style"
            value={MAP_STYLE_LABELS[settings.mapStyle || 'standard']}
            onPress={cycleMapStyle}
          />
          <SettingsButton
            label="Offline Maps"
            value="Manage Downloads"
            onPress={() => {
              // Navigate handled by parent
            }}
          />
        </View>
      </View>

      {/* Notifications */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Notifications</Text>
        <View style={styles.sectionCard}>
          <SettingsToggle
            label="Daily Forecast"
            value={settings.notifications.dailyForecast}
            onValueChange={(val) =>
              updateSetting({
                notifications: { ...settings.notifications, dailyForecast: val },
              })
            }
          />
          <SettingsToggle
            label="Best Time Alerts"
            value={settings.notifications.bestTimeAlerts ?? true}
            onValueChange={(val) =>
              updateSetting({
                notifications: { ...settings.notifications, bestTimeAlerts: val },
              })
            }
          />
          <SettingsToggle
            label="Weather Alerts"
            value={settings.notifications.weatherAlerts ?? true}
            onValueChange={(val) =>
              updateSetting({
                notifications: { ...settings.notifications, weatherAlerts: val },
              })
            }
          />
          <SettingsToggle
            label="Score Alerts"
            value={settings.notifications.scoreAlerts}
            onValueChange={(val) =>
              updateSetting({
                notifications: { ...settings.notifications, scoreAlerts: val },
              })
            }
          />
          <SettingsToggle
            label="Weekly Digest"
            value={settings.notifications.weeklyDigest}
            onValueChange={(val) =>
              updateSetting({
                notifications: { ...settings.notifications, weeklyDigest: val },
              })
            }
          />
        </View>
      </View>

      {/* Data & Storage */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Data & Storage</Text>
        <View style={styles.sectionCard}>
          <SettingsButton
            label="Storage Used"
            value={cacheSize}
            onPress={() => {}}
          />
          <SettingsButton
            label="Clear Cache"
            value=""
            onPress={handleClearCache}
          />
          <SettingsButton
            label="Export All Data"
            value=""
            onPress={handleExportData}
          />
        </View>
      </View>

      {/* About */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>About</Text>
        <View style={styles.sectionCard}>
          <SettingsButton
            label="Version"
            value="0.1.0 (MVP)"
            onPress={() => {}}
          />
          <SettingsButton
            label="Licenses"
            value=""
            onPress={() =>
              Alert.alert(
                'Open Source Licenses',
                'OpenCatch uses MapLibre GL, Expo, React Native, and other open source libraries. Full license details will be available at launch.',
              )
            }
          />
          <SettingsButton
            label="Privacy Policy"
            value=""
            onPress={() =>
              Alert.alert(
                'Privacy Policy',
                'Your data stays on your device. OpenCatch does not sell or share personal data. Full policy coming at launch.',
              )
            }
          />
          <SettingsButton
            label="Terms of Service"
            value=""
            onPress={() =>
              Alert.alert('Coming Soon', 'Terms of service will be available at launch.')
            }
          />
        </View>
      </View>

      {/* Branding */}
      <View style={styles.brandSection}>
        <Text style={styles.brandTitle}>OpenCatch</Text>
        <Text style={styles.brandSubtitle}>AI-Powered Fishing Predictions</Text>
      </View>

      <View style={{ height: 40 }} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  centered: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  content: {
    padding: 20,
    gap: 24,
  },
  section: {
    gap: 10,
  },
  sectionTitle: {
    color: palette.textMuted,
    fontSize: 13,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    paddingLeft: 4,
  },
  sectionCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    paddingHorizontal: 16,
    borderWidth: 1,
    borderColor: palette.border,
  },
  brandSection: {
    alignItems: 'center',
    paddingVertical: 20,
    gap: 4,
  },
  brandTitle: {
    ...typeStyles.brand,
    color: palette.accent,
    fontSize: 20,
  },
  brandSubtitle: {
    color: palette.textMuted,
    fontSize: 13,
  },
});
