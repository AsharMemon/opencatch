import React, { useEffect, useRef, useState } from 'react';
import {
  Animated,
  ScrollView,
  View,
  Text,
  StyleSheet,
  TextInput,
  Pressable,
  Alert,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { fonts, type as typeStyles } from '../theme/typography';
import { SettingsToggle, SettingsButton } from '../components/SettingsRow';
import { TodaysFishingBrief } from '../components/TodaysFishingBrief';
import { ActivityHeatmap } from '../components/ActivityHeatmap';
import { AnimatedNumber } from '../components/ui/AnimatedNumber';
import { hapticLight, hapticSelection } from '../utils/haptics';
import { api } from '../services/api';
import type { UnitSystem, UserSettings } from '../types/models';
import type { UserProfile } from '../services/auth';

// ── Mock recent activity data ───────────────────────────────────

interface RecentActivity {
  id: string;
  date: string;
  location: string;
  fishCount: number;
  ionicon: string;
}

const RECENT_ACTIVITY: RecentActivity[] = [
  { id: '1', date: 'Mar 16', location: 'Lake Fork, TX', fishCount: 7, ionicon: 'fish-outline' },
  { id: '2', date: 'Mar 14', location: 'Grand Lake, OK', fishCount: 4, ionicon: 'fish-outline' },
  { id: '3', date: 'Mar 10', location: 'Sam Rayburn, TX', fishCount: 11, ionicon: 'fish-outline' },
  { id: '4', date: 'Mar 7', location: 'Table Rock Lake, MO', fishCount: 3, ionicon: 'fish-outline' },
];

// ── Mock stats ─────────────────────────────────────────────────

const STATS = [
  { label: 'Total Catches', value: 25, icon: 'fish-outline' },
  { label: 'Trips', value: 12, icon: 'navigate-outline' },
  { label: 'Species', value: 8, icon: 'leaf-outline' },
];

interface ProfileScreenProps {
  user: UserProfile | null;
  onLogout: () => void;
}

export function ProfileScreen({ user, onLogout }: ProfileScreenProps) {
  const navigation = useNavigation<any>();
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState('');

  // Staggered entrance animations
  const headerOpacity = useRef(new Animated.Value(0)).current;
  const headerTranslateY = useRef(new Animated.Value(20)).current;
  const statsOpacity = useRef(new Animated.Value(0)).current;
  const statsTranslateY = useRef(new Animated.Value(20)).current;
  const contentOpacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    api.getSettings().then(setSettings);

    // Staggered entrance
    Animated.stagger(150, [
      Animated.parallel([
        Animated.timing(headerOpacity, { toValue: 1, duration: 400, useNativeDriver: true }),
        Animated.spring(headerTranslateY, { toValue: 0, damping: 20, stiffness: 200, useNativeDriver: true }),
      ]),
      Animated.parallel([
        Animated.timing(statsOpacity, { toValue: 1, duration: 400, useNativeDriver: true }),
        Animated.spring(statsTranslateY, { toValue: 0, damping: 20, stiffness: 200, useNativeDriver: true }),
      ]),
      Animated.timing(contentOpacity, { toValue: 1, duration: 400, useNativeDriver: true }),
    ]).start();
  }, []);

  if (!settings) return <View style={styles.screen} />;

  const displayName = user?.display_name || settings.displayName;

  const updateSetting = (patch: Partial<UserSettings>) => {
    setSettings((prev) => (prev ? { ...prev, ...patch } : prev));
    api.updateSettings(patch);
  };

  const toggleUnit = () => {
    hapticSelection();
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
    hapticLight();
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
      {/* Settings gear icon */}
      <Pressable
        style={styles.settingsGear}
        onPress={() => { hapticLight(); navigation.navigate('Settings'); }}
        hitSlop={12}
      >
        <Ionicons name="settings-outline" size={24} color={palette.textMuted} />
      </Pressable>

      {/* Today's Fishing Brief — top-level intelligence card */}
      <TodaysFishingBrief />

      {/* Profile header with gradient background */}
      <Animated.View style={[styles.profileCard, { opacity: headerOpacity, transform: [{ translateY: headerTranslateY }] }]}>
        <LinearGradient
          colors={['rgba(10, 110, 189, 0.12)', 'rgba(10, 110, 189, 0.03)', 'transparent']}
          start={{ x: 0.5, y: 0 }}
          end={{ x: 0.5, y: 1 }}
          style={styles.profileGradient}
        />
        <View style={styles.avatarOuter}>
          <LinearGradient
            colors={[palette.accent, palette.accentDeep]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.avatar}
          >
            <Text style={styles.avatarText}>
              {displayName.charAt(0).toUpperCase()}
            </Text>
          </LinearGradient>
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
              hapticLight();
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
      </Animated.View>

      {/* Stats row with animated numbers */}
      <Animated.View style={[styles.statsRow, { opacity: statsOpacity, transform: [{ translateY: statsTranslateY }] }]}>
        {STATS.map((stat, idx) => (
          <View key={stat.label} style={[styles.statCard, idx < STATS.length - 1 && styles.statCardBorder]}>
            <View style={styles.statIconCircle}>
              <Ionicons name={stat.icon as any} size={16} color={palette.accent} />
            </View>
            <AnimatedNumber
              value={stat.value}
              duration={800 + idx * 200}
              style={styles.statValue}
            />
            <Text style={styles.statLabel}>{stat.label}</Text>
          </View>
        ))}
      </Animated.View>

      <Animated.View style={{ opacity: contentOpacity, gap: 24 }}>
        {/* Activity Heatmap */}
        <ActivityHeatmap />

        {/* Recent Activity */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Recent Activity</Text>
          <View style={styles.sectionCard}>
            {RECENT_ACTIVITY.map((activity, idx) => (
              <Pressable
                key={activity.id}
                style={[
                  styles.activityRow,
                  idx < RECENT_ACTIVITY.length - 1 && styles.activityRowBorder,
                ]}
              >
                <View style={styles.activityIcon}>
                  <Ionicons name={activity.ionicon as any} size={18} color={palette.accent} />
                </View>
                <View style={styles.activityInfo}>
                  <Text style={styles.activityLocation} numberOfLines={1}>
                    {activity.location}
                  </Text>
                  <Text style={styles.activityDate}>{activity.date}</Text>
                </View>
                <Text style={styles.activityCount}>{activity.fishCount} fish</Text>
              </Pressable>
            ))}
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
      </Animated.View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  settingsGear: {
    alignSelf: 'flex-end',
    marginBottom: -8,
  },
  content: {
    padding: 20,
    gap: 24,
  },

  // ── Profile header with gradient ──────────────────────────────
  profileCard: {
    alignItems: 'center',
    gap: 14,
    paddingVertical: 28,
    borderRadius: 16,
    overflow: 'hidden',
    backgroundColor: palette.surface,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  profileGradient: {
    ...StyleSheet.absoluteFillObject,
  },
  avatarOuter: {
    shadowColor: palette.accent,
    shadowOpacity: 0.25,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 6,
  },
  avatar: {
    width: 84,
    height: 84,
    borderRadius: 42,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 3,
    borderColor: 'rgba(255, 255, 255, 0.9)',
  },
  avatarText: {
    color: '#fff',
    fontSize: 32,
    fontWeight: '700',
  },
  displayName: {
    fontFamily: 'PlayfairDisplay-Bold',
    fontSize: 24,
    fontWeight: '400',
    letterSpacing: -0.3,
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
    borderRadius: 10,
    borderWidth: 1.5,
    borderColor: palette.accent,
    padding: 12,
    color: palette.text,
    fontSize: 18,
    textAlign: 'center',
    fontWeight: '600',
  },

  // ── Stats row ─────────────────────────────────────────────────
  statsRow: {
    flexDirection: 'row',
    backgroundColor: palette.surface,
    borderRadius: 14,
    paddingVertical: 18,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  statCard: {
    flex: 1,
    alignItems: 'center',
    gap: 6,
  },
  statCardBorder: {
    borderRightWidth: StyleSheet.hairlineWidth,
    borderRightColor: palette.borderLight,
  },
  statIconCircle: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: palette.accentDim,
    alignItems: 'center',
    justifyContent: 'center',
  },
  statValue: {
    fontSize: 22,
    fontWeight: '700',
    color: palette.text,
    fontVariant: ['tabular-nums'],
  },
  statLabel: {
    fontSize: 11,
    fontWeight: '500',
    color: palette.textMuted,
    letterSpacing: 0.2,
  },

  // ── Sections ──────────────────────────────────────────────────
  section: {
    gap: 10,
  },
  sectionTitle: {
    fontFamily: 'PlayfairDisplay-Regular',
    color: palette.textSecondary,
    fontSize: 14,
    fontWeight: '400',
    paddingLeft: 4,
    letterSpacing: -0.1,
  },
  sectionCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    paddingHorizontal: 16,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },

  // ── Activity rows ─────────────────────────────────────────────
  activityRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 14,
    gap: 12,
  },
  activityRowBorder: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
  },
  activityIcon: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: palette.accentDim,
    alignItems: 'center',
    justifyContent: 'center',
  },
  activityInfo: {
    flex: 1,
    gap: 2,
  },
  activityLocation: {
    color: palette.text,
    fontSize: 15,
    fontWeight: '600',
  },
  activityDate: {
    color: palette.textMuted,
    fontSize: 13,
  },
  activityCount: {
    color: palette.accent,
    fontSize: 13,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
  },

  // ── Branding ──────────────────────────────────────────────────
  brandSection: {
    alignItems: 'center',
    paddingVertical: 24,
    gap: 4,
  },
  brandTitle: {
    fontFamily: 'PlayfairDisplay-Bold',
    fontSize: 20,
    fontWeight: '400',
    color: palette.accent,
    letterSpacing: -0.5,
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
