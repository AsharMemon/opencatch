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
import * as FileSystem from 'expo-file-system';
// Lazy-loaded to avoid crash if native module not linked
const getSharing = async () => { try { return require('expo-sharing'); } catch { return null; } };
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { SettingsToggle, SettingsButton } from '../components/SettingsRow';
import { api } from '../services/api';
import { getAllCatches } from '../services/catchEnhancements';
import { trackRecorder } from '../services/trackRecorder';
import { useUnits } from '../hooks/useUnits';
import {
  getNotificationPrefs,
  updateNotificationPrefs,
  scheduleMorningBrief,
  cancelMorningBrief,
  cancelAllNotifications,
  type NotificationPrefs,
} from '../services/dailyNotifications';
import { useNavigation } from '@react-navigation/native';
import type { UserSettings, UnitSystem, MapStyle } from '../types/models';

const MAP_STYLE_LABELS: Record<MapStyle, string> = {
  standard: 'Standard',
  satellite: 'Satellite',
  terrain: 'Terrain',
};

export function SettingsScreen() {
  const navigation = useNavigation<any>();
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [cacheSize, setCacheSize] = useState<string>('Calculating...');
  const [loading, setLoading] = useState(true);
  const [notifPrefs, setNotifPrefs] = useState<NotificationPrefs | null>(null);
  const { units, toggle: toggleUnitsHook } = useUnits();

  useEffect(() => {
    loadSettings();
    estimateCacheSize();
    loadNotifPrefs();
  }, []);

  const loadNotifPrefs = async () => {
    const prefs = await getNotificationPrefs();
    setNotifPrefs(prefs);
  };

  const toggleMorningBrief = async (val: boolean) => {
    if (val) {
      // Use stored location or default
      const prefs = notifPrefs ?? await getNotificationPrefs();
      await scheduleMorningBrief(prefs.morningBriefLat || 44.95, prefs.morningBriefLon || -93.27);
    } else {
      await cancelMorningBrief();
    }
    loadNotifPrefs();
  };

  const toggleTripReminders = async (val: boolean) => {
    await updateNotificationPrefs({ tripReminders: val });
    loadNotifPrefs();
  };

  const toggleConditionAlerts = async (val: boolean) => {
    await updateNotificationPrefs({ conditionAlerts: val });
    loadNotifPrefs();
  };

  // Keep local settings state in sync with the units hook
  useEffect(() => {
    if (settings && settings.units !== units) {
      setSettings((prev) => (prev ? { ...prev, units } : prev));
    }
  }, [units]);

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
      // Read all values and sum their byte sizes for a real estimate
      const pairs = await AsyncStorage.multiGet(keys);
      let totalBytes = 0;
      for (const [key, value] of pairs) {
        totalBytes += (key?.length ?? 0) * 2; // UTF-16
        totalBytes += (value?.length ?? 0) * 2;
      }
      const sizeMB = totalBytes / (1024 * 1024);
      if (sizeMB >= 1) {
        setCacheSize(`${sizeMB.toFixed(1)} MB (${keys.length} items)`);
      } else {
        const sizeKB = totalBytes / 1024;
        setCacheSize(`${sizeKB.toFixed(0)} KB (${keys.length} items)`);
      }
    } catch {
      setCacheSize('Unable to calculate');
    }
  };

  const updateSetting = useCallback((patch: Partial<UserSettings>) => {
    setSettings((prev) => (prev ? { ...prev, ...patch } : prev));
    api.updateSettings(patch);
  }, []);

  const toggleUnit = async () => {
    if (!settings) return;
    await toggleUnitsHook();
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

  const handleExportData = async () => {
    try {
      const [catches, tracks, currentSettings] = await Promise.all([
        getAllCatches(),
        trackRecorder.getSavedTracks(),
        api.getSettings(),
      ]);

      const exportData = {
        exportedAt: new Date().toISOString(),
        app: 'OpenCatch',
        version: '0.1.0',
        catches,
        tracks,
        settings: currentSettings,
      };

      const json = JSON.stringify(exportData, null, 2);
      const filename = `opencatch-export-${new Date().toISOString().slice(0, 10)}.json`;
      const file = new (FileSystem as any).File((FileSystem as any).Paths.cache, filename);
      (file as any).text = json;

      const SharingModule = await getSharing();
      if (SharingModule && await SharingModule.isAvailableAsync()) {
        await SharingModule.shareAsync(file.uri, {
          mimeType: 'application/json',
          dialogTitle: 'Export OpenCatch Data',
          UTI: 'public.json',
        });
      } else {
        Alert.alert('Export Saved', `Data exported to ${filename}`);
      }
    } catch (err) {
      Alert.alert('Export Failed', 'Unable to export data. Please try again.');
    }
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
          <SettingsButton
            label="AIS Receiver"
            value="WiFi AIS Setup"
            onPress={() => navigation.navigate('AISSettings')}
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

      {/* Trip & Condition Notifications */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Trip Notifications</Text>
        <View style={styles.sectionCard}>
          <SettingsToggle
            label="Morning Brief (6 AM)"
            value={notifPrefs?.morningBrief ?? false}
            onValueChange={toggleMorningBrief}
          />
          <SettingsToggle
            label="Trip Reminders"
            value={notifPrefs?.tripReminders ?? true}
            onValueChange={toggleTripReminders}
          />
          <SettingsToggle
            label="Condition Alerts"
            value={notifPrefs?.conditionAlerts ?? false}
            onValueChange={toggleConditionAlerts}
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
            onPress={estimateCacheSize}
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
