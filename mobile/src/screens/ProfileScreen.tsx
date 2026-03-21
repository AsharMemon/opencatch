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
import { getAllCatches, type EnhancedCatch } from '../services/catchEnhancements';
import { trackRecorder, type FishingTrack } from '../services/trackRecorder';
import type { UnitSystem, UserSettings } from '../types/models';
import { SkeletonLoader, SkeletonCard } from '../components/ui/SkeletonLoader';
import type { UserProfile } from '../services/auth';

// ── Real stats from AsyncStorage ─────────────────────────────────

interface RealStats {
  totalCatches: number;
  totalTrips: number;
  uniqueSpecies: number;
}

interface ProfileScreenProps {
  user: UserProfile | null;
  onLogout: () => void;
}

export function ProfileScreen({ user, onLogout }: ProfileScreenProps) {
  const navigation = useNavigation<any>();
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState('');
  const [stats, setStats] = useState<RealStats | null>(null);
  const [recentCatches, setRecentCatches] = useState<EnhancedCatch[]>([]);

  // Staggered entrance animations
  const headerOpacity = useRef(new Animated.Value(0)).current;
  const headerTranslateY = useRef(new Animated.Value(20)).current;
  const statsOpacity = useRef(new Animated.Value(0)).current;
  const statsTranslateY = useRef(new Animated.Value(20)).current;
  const contentOpacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    api.getSettings().then(setSettings);
    loadRealData();

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

  const loadRealData = async () => {
    try {
      const [catches, tracks] = await Promise.all([
        getAllCatches(),
        trackRecorder.getSavedTracks(),
      ]);

      const speciesSet = new Set<string>();
      for (const c of catches) {
        if (c.species) speciesSet.add(c.species.toLowerCase());
      }

      setStats({
        totalCatches: catches.length,
        totalTrips: tracks.length,
        uniqueSpecies: speciesSet.size,
      });

      // Show up to 4 most recent catches
      const sorted = [...catches].sort((a, b) => b.timestamp - a.timestamp);
      setRecentCatches(sorted.slice(0, 4));
    } catch {
      setStats({ totalCatches: 0, totalTrips: 0, uniqueSpecies: 0 });
      setRecentCatches([]);
    }
  };

  if (!settings) {
    return (
      <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
        {/* Profile skeleton */}
        <View style={{ alignItems: 'center', gap: 14, paddingVertical: 28 }}>
          <SkeletonLoader width={84} height={84} borderRadius={42} />
          <SkeletonLoader width="50%" height={22} borderRadius={6} />
          <SkeletonLoader width="35%" height={14} borderRadius={4} />
        </View>
        {/* Stats skeleton */}
        <SkeletonLoader width="100%" height={90} borderRadius={14} />
        {/* Section skeletons */}
        <SkeletonCard />
        <SkeletonCard />
      </ScrollView>
    );
  }

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

  const STAT_ITEMS = stats
    ? [
        { label: 'Total Catches', value: stats.totalCatches, icon: 'fish-outline' },
        { label: 'Trips', value: stats.totalTrips, icon: 'navigate-outline' },
        { label: 'Species', value: stats.uniqueSpecies, icon: 'leaf-outline' },
      ]
    : [];

  const formatCatchDate = (ts: number) => {
    const d = new Date(ts);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
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
        {stats ? (
          STAT_ITEMS.map((stat, idx) => (
            <View key={stat.label} style={[styles.statCard, idx < STAT_ITEMS.length - 1 && styles.statCardBorder]}>
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
          ))
        ) : (
          <View style={{ flex: 1, alignItems: 'center', paddingVertical: 12 }}>
            <Text style={{ color: palette.textMuted, fontSize: 13 }}>Loading stats...</Text>
          </View>
        )}
      </Animated.View>

      <Animated.View style={{ opacity: contentOpacity, gap: 24 }}>
        {/* Activity Heatmap */}
        <ActivityHeatmap />

        {/* Recent Activity — from real catches */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Recent Activity</Text>
          <View style={styles.sectionCard}>
            {recentCatches.length === 0 ? (
              <View style={styles.emptyActivity}>
                <Ionicons name="fish-outline" size={28} color={palette.textDim} />
                <Text style={styles.emptyActivityText}>No catches yet</Text>
                <Text style={styles.emptyActivitySubtext}>Log your first catch to see it here</Text>
              </View>
            ) : (
              recentCatches.map((c) => (
                <Pressable
                  key={c.id}
                  style={[styles.activityRow, styles.activityRowBorder]}
                >
                  <View style={styles.activityIcon}>
                    <Ionicons name="fish-outline" size={18} color={palette.accent} />
                  </View>
                  <View style={styles.activityInfo}>
                    <Text style={styles.activityLocation} numberOfLines={1}>
                      {c.species || c.locationName || 'Catch'}
                    </Text>
                    <Text style={styles.activityDate}>{formatCatchDate(c.timestamp)}</Text>
                  </View>
                  {c.weight ? (
                    <Text style={styles.activityCount}>{c.weight} lb</Text>
                  ) : c.length ? (
                    <Text style={styles.activityCount}>{c.length} in</Text>
                  ) : null}
                </Pressable>
              ))
            )}
            <Pressable
              style={styles.viewAllRow}
              onPress={() => { hapticLight(); navigation.navigate('ActivityLog'); }}
            >
              <Text style={styles.viewAllText}>View All</Text>
              <Ionicons name="chevron-forward" size={16} color={palette.accent} />
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
  viewAllRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 14,
    gap: 4,
  },
  viewAllText: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.accent,
  },

  // ── Empty activity ─────────────────────────────────────────────
  emptyActivity: {
    alignItems: 'center',
    paddingVertical: 20,
    gap: 6,
  },
  emptyActivityText: {
    color: palette.text,
    fontSize: 15,
    fontWeight: '600',
  },
  emptyActivitySubtext: {
    color: palette.textMuted,
    fontSize: 13,
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
