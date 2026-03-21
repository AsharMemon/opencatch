import React, { useEffect, useState } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  TextInput,
  Pressable,
  Alert,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { fonts, type as typeStyles } from '../theme/typography';
import { SettingsToggle, SettingsButton } from '../components/SettingsRow';
import { api } from '../services/api';
import type { UnitSystem, UserSettings } from '../types/models';
import type { UserProfile } from '../services/auth';

interface ProfileScreenProps {
  user: UserProfile | null;
  onLogout: () => void;
}

export function ProfileScreen({ user, onLogout }: ProfileScreenProps) {
  const navigation = useNavigation<any>();
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState('');

  useEffect(() => {
    api.getSettings().then(setSettings);
  }, []);

  if (!settings) return <View style={styles.screen} />;

  const displayName = user?.display_name || settings.displayName;

  const updateSetting = (patch: Partial<UserSettings>) => {
    setSettings((prev) => (prev ? { ...prev, ...patch } : prev));
    api.updateSettings(patch);
  };

  const toggleUnit = () => {
    const next: UnitSystem = settings.units === 'imperial' ? 'metric' : 'imperial';
    updateSetting({ units: next });
  };

  const saveName = () => {
    if (nameInput.trim()) {
      updateSetting({ displayName: nameInput.trim() });
    }
    setEditingName(false);
  };

  const handleLogout = () => {
    Alert.alert('Log Out', 'Are you sure you want to log out?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Log Out',
        style: 'destructive',
        onPress: onLogout,
      },
    ]);
  };

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {/* Profile header */}
      <View style={styles.profileCard}>
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>
            {displayName.charAt(0).toUpperCase()}
          </Text>
        </View>
        {editingName ? (
          <View style={styles.nameEditRow}>
            <TextInput
              style={styles.nameInput}
              value={nameInput}
              onChangeText={setNameInput}
              autoFocus
              onSubmitEditing={saveName}
              onBlur={saveName}
              placeholder="Your name"
              placeholderTextColor={palette.textDim}
              selectionColor={palette.accent}
            />
          </View>
        ) : (
          <Pressable
            onPress={() => {
              setNameInput(displayName);
              setEditingName(true);
            }}
          >
            <Text style={styles.displayName}>{displayName}</Text>
            <Text style={styles.editHint}>Tap to edit</Text>
          </Pressable>
        )}
        {user?.email && (
          <Text style={styles.emailText}>{user.email}</Text>
        )}
      </View>

      {/* Fishing Tools */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Fishing Tools</Text>
        <View style={styles.toolsGrid}>
          <Pressable style={styles.toolCard} onPress={() => navigation.navigate('Stats')}>
            <Ionicons name="stats-chart" size={24} color={palette.accent} />
            <Text style={styles.toolLabel}>My Stats</Text>
          </Pressable>
          <Pressable style={styles.toolCard} onPress={() => navigation.navigate('SpeciesGuide')}>
            <Ionicons name="fish" size={24} color="#4682B4" />
            <Text style={styles.toolLabel}>Species Guide</Text>
          </Pressable>
          <Pressable style={styles.toolCard} onPress={() => navigation.navigate('Regulations', {})}>
            <Ionicons name="document-text" size={24} color="#8B4513" />
            <Text style={styles.toolLabel}>Regulations</Text>
          </Pressable>
          <Pressable style={styles.toolCard} onPress={() => navigation.navigate('BaitGuide', {})}>
            <Ionicons name="bug" size={24} color="#DAA520" />
            <Text style={styles.toolLabel}>Bait Guide</Text>
          </Pressable>
          <Pressable style={styles.toolCard} onPress={() => navigation.navigate('TideChart', { stationId: '', stationName: 'Find Station' })}>
            <Ionicons name="water" size={24} color="#1E88E5" />
            <Text style={styles.toolLabel}>Tides</Text>
          </Pressable>
          <Pressable style={styles.toolCard} onPress={() => navigation.navigate('OfflineMaps')}>
            <Ionicons name="cloud-offline" size={24} color="#607D8B" />
            <Text style={styles.toolLabel}>Offline Maps</Text>
          </Pressable>
          <Pressable style={styles.toolCard} onPress={() => navigation.navigate('Alerts')}>
            <Ionicons name="warning" size={24} color="#E53935" />
            <Text style={styles.toolLabel}>Alerts</Text>
          </Pressable>
          <Pressable style={styles.toolCard} onPress={() => navigation.navigate('Safety')}>
            <Ionicons name="shield-checkmark" size={24} color="#2E7D32" />
            <Text style={styles.toolLabel}>Safety</Text>
          </Pressable>
          <Pressable style={styles.toolCard} onPress={() => navigation.navigate('TrackRecording')}>
            <Ionicons name="navigate" size={24} color="#E65100" />
            <Text style={styles.toolLabel}>Track Trip</Text>
          </Pressable>
          <Pressable style={styles.toolCard} onPress={() => navigation.navigate('WeatherBuoys', {})}>
            <Ionicons name="radio" size={24} color="#0288D1" />
            <Text style={styles.toolLabel}>Buoys</Text>
          </Pressable>
          <Pressable style={styles.toolCard} onPress={() => navigation.navigate('SunMoon')}>
            <Ionicons name="moon" size={24} color="#5C6BC0" />
            <Text style={styles.toolLabel}>Sun & Moon</Text>
          </Pressable>
        </View>
      </View>

      {/* Units */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Preferences</Text>
        <View style={styles.sectionCard}>
          <SettingsButton
            label="Units"
            value={settings.units === 'imperial' ? 'Imperial (\u00B0F, mph)' : 'Metric (\u00B0C, km/h)'}
            onPress={toggleUnit}
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

      {/* Account */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Account</Text>
        <View style={styles.sectionCard}>
          <SettingsButton
            label="Log Out"
            value=""
            onPress={handleLogout}
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
            label="Privacy Policy"
            value=""
            onPress={() => Alert.alert('Coming Soon', 'Privacy policy will be available at launch.')}
          />
          <SettingsButton
            label="Terms of Service"
            value=""
            onPress={() => Alert.alert('Coming Soon', 'Terms of service will be available at launch.')}
          />
        </View>
      </View>

      {/* Branding */}
      <View style={styles.brandSection}>
        <Text style={styles.brandTitle}>OpenCatch</Text>
        <Text style={styles.brandSubtitle}>AI-Powered Fishing Predictions</Text>
        <Text style={styles.brandVersion}>v0.1.0 MVP</Text>
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
  content: {
    padding: 20,
    gap: 24,
  },
  profileCard: {
    alignItems: 'center',
    gap: 14,
    paddingVertical: 20,
  },
  avatar: {
    width: 80,
    height: 80,
    borderRadius: 40,
    backgroundColor: palette.accent,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarText: {
    color: '#fff',
    fontSize: 32,
    fontWeight: '700',
  },
  displayName: {
    ...typeStyles.screenTitle,
    color: palette.text,
    textAlign: 'center',
  },
  editHint: {
    color: palette.textDim,
    fontSize: 12,
    textAlign: 'center',
    marginTop: 2,
  },
  emailText: {
    color: palette.textMuted,
    fontSize: 14,
    textAlign: 'center',
  },
  nameEditRow: {
    width: '70%',
  },
  nameInput: {
    backgroundColor: '#FFFFFF',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: palette.accent,
    padding: 12,
    color: palette.text,
    fontSize: 18,
    textAlign: 'center',
    fontWeight: '600',
  },
  section: {
    gap: 10,
  },
  sectionTitle: {
    color: palette.textMuted,
    fontSize: 13,
    fontWeight: '700',
    paddingLeft: 4,
  },
  sectionCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 8,
    paddingHorizontal: 16,
    borderWidth: 1,
    borderColor: palette.border,
  },
  toolsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  toolCard: {
    width: '31%',
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 16,
    alignItems: 'center',
    gap: 8,
    borderWidth: 1,
    borderColor: palette.border,
  },
  toolLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.text,
    textAlign: 'center',
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
  brandVersion: {
    color: palette.textDim,
    fontSize: 11,
    marginTop: 4,
  },
});
