import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  Pressable,
  ActivityIndicator,
  TextInput,
  Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { spacing, radius, typography } from '../theme/spacing';
import {
  getAllSpecies,
  getSpecies,
  getRecommendations,
  getCurrentSeason,
  type Season,
  type WaterClarity,
  type TimeOfDay,
  type BaitRecommendation,
  type SpeciesProfile,
} from '../services/baitDatabase';

// ── Constants ─────────────────────────────────────────────────────

const SEASONS: { key: Season; label: string; icon: string }[] = [
  { key: 'spring', label: 'Spring', icon: 'flower-outline' },
  { key: 'summer', label: 'Summer', icon: 'sunny-outline' },
  { key: 'fall', label: 'Fall', icon: 'leaf-outline' },
  { key: 'winter', label: 'Winter', icon: 'snow-outline' },
];

const CLARITY_OPTIONS: { key: WaterClarity; label: string; icon: string }[] = [
  { key: 'clear', label: 'Clear', icon: 'eye-outline' },
  { key: 'stained', label: 'Stained', icon: 'water-outline' },
  { key: 'muddy', label: 'Murky', icon: 'cloud-outline' },
];

const TIME_OPTIONS: { key: TimeOfDay; label: string; icon: string }[] = [
  { key: 'morning', label: 'Morning', icon: 'sunny-outline' },
  { key: 'midday', label: 'Midday', icon: 'sunny' },
  { key: 'evening', label: 'Evening', icon: 'partly-sunny-outline' },
  { key: 'night', label: 'Night', icon: 'moon-outline' },
];

const TEMP_MIN = 32;
const TEMP_MAX = 95;
const TEMP_STEP = 1;

// ── Helpers ───────────────────────────────────────────────────────

function confidenceColor(c: number): string {
  if (c >= 0.7) return palette.success;
  if (c >= 0.4) return palette.warning;
  return palette.error;
}

function typeBadgeStyle(t: 'live' | 'artificial' | 'fly') {
  switch (t) {
    case 'live':
      return { bg: '#E8F5E9', fg: '#2E7D32', label: 'Live Bait' };
    case 'artificial':
      return { bg: palette.accentLight, fg: palette.accentDeep, label: 'Lure' };
    case 'fly':
      return { bg: '#FFF3E0', fg: '#E65100', label: 'Fly' };
  }
}

function seasonIcon(s: Season): string {
  return SEASONS.find((x) => x.key === s)?.icon ?? 'calendar-outline';
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ── Pro Tips ──────────────────────────────────────────────────────

const PRO_TIPS: Record<string, string[]> = {
  'largemouth-bass': [
    'Match the hatch -- observe what forage is present and choose colors accordingly.',
    'Bass relate to cover. Always cast past the target and retrieve through it.',
    'In cold fronts, downsize your presentation and slow your retrieve speed.',
    'Early morning and late evening topwater bites are the most explosive of the year.',
  ],
  'smallmouth-bass': [
    'Smallmouth love current -- focus on eddies, current breaks, and seam lines.',
    'Crawfish patterns are effective year-round; match the local crawfish color.',
    'Light line (6-8 lb fluorocarbon) is critical in clear water.',
    'Rocky points and bluff walls hold fish in all four seasons.',
  ],
  walleye: [
    'Low-light periods (dawn, dusk, overcast) are prime walleye feeding times.',
    'Chartreuse is the universal walleye color -- always have it in your box.',
    'Slow presentations win in cold water; speed up when water temps exceed 60F.',
    'Use your electronics to find baitfish -- walleye will be nearby.',
  ],
  'rainbow-trout': [
    'Present your fly or bait upstream and let it drift naturally with the current.',
    'Match the insect hatch -- carry a small seine net to check what is hatching.',
    'Trout have excellent eyesight; use the lightest tippet you can manage.',
    'Early morning and overcast days produce the best dry-fly fishing.',
  ],
  'channel-catfish': [
    'Fresh cut bait outperforms old bait -- keep it cold and replace often.',
    'Night fishing in summer is peak time for big channel cats.',
    'Use circle hooks to improve hookup rates and reduce gut-hooking.',
    'Current areas near dams and spillways concentrate catfish year-round.',
  ],
  crappie: [
    'Electronics are key -- find brush piles and suspended fish before you drop a jig.',
    'Spider-rigging multiple rods lets you dial in the exact depth.',
    'During the spawn, look for males fanning beds in 2-6 feet around woody cover.',
    'Downsize your jig in winter -- 1/32 oz can make all the difference.',
  ],
};

// ── Component ─────────────────────────────────────────────────────

export function BaitGuideScreen({ route }: any) {
  const allSpecies = useMemo(() => getAllSpecies(), []);
  const initialSpecies = route?.params?.species ?? allSpecies[0]?.id ?? 'largemouth-bass';
  const spotName = route?.params?.spotName as string | undefined;
  const spotLat = route?.params?.spotLat as number | undefined;
  const spotLon = route?.params?.spotLon as number | undefined;
  const isContextual = !!spotName;

  const [selectedSpecies, setSelectedSpecies] = useState<string>(initialSpecies);
  const [season, setSeason] = useState<Season>(getCurrentSeason());
  const [waterTemp, setWaterTemp] = useState<string>('68');
  const [clarity, setClarity] = useState<WaterClarity>('stained');
  const [timeOfDay, setTimeOfDay] = useState<TimeOfDay>('morning');
  const [loading, setLoading] = useState(true);
  const [recommendations, setRecommendations] = useState<BaitRecommendation[]>([]);

  const species = useMemo(() => getSpecies(selectedSpecies), [selectedSpecies]);

  // Fetch recommendations whenever inputs change
  const refresh = useCallback(() => {
    setLoading(true);
    // Small delay to show loading state for perceived responsiveness
    const timer = setTimeout(() => {
      const temp = parseInt(waterTemp, 10);
      const recs = getRecommendations(selectedSpecies, {
        season,
        waterTemp: isNaN(temp) ? undefined : temp,
        waterClarity: clarity,
        timeOfDay,
      });
      setRecommendations(recs);
      setLoading(false);
    }, 200);
    return () => clearTimeout(timer);
  }, [selectedSpecies, season, waterTemp, clarity, timeOfDay]);

  useEffect(() => {
    const cleanup = refresh();
    return cleanup;
  }, [refresh]);

  // ── Render ────────────────────────────────────────────────────

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {/* Header -- contextual when arriving from a spot */}
      {isContextual ? (
        <View style={styles.contextBanner}>
          <Ionicons name="location" size={16} color={palette.accent} />
          <View style={styles.contextBannerText}>
            <Text style={styles.screenTitle}>Best Bait Now</Text>
            <Text style={styles.subtitle}>at {spotName}</Text>
          </View>
        </View>
      ) : (
        <>
          <Text style={styles.screenTitle}>Bait Guide</Text>
          <Text style={styles.subtitle}>Species-specific bait and lure recommendations</Text>
        </>
      )}

      {/* Species Selector */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.pillRow}
        style={styles.pillScroll}
      >
        {allSpecies.map((sp) => {
          const active = sp.id === selectedSpecies;
          return (
            <Pressable
              key={sp.id}
              onPress={() => setSelectedSpecies(sp.id)}
              style={[styles.pill, active && styles.pillActive]}
            >
              <Ionicons
                name={sp.icon as any}
                size={14}
                color={active ? '#FFF' : palette.textSecondary}
                style={{ marginRight: 4 }}
              />
              <Text style={[styles.pillText, active && styles.pillTextActive]}>
                {sp.commonName}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>

      {/* Season Indicator */}
      <View style={styles.seasonBar}>
        {SEASONS.map((s) => {
          const active = s.key === season;
          return (
            <Pressable
              key={s.key}
              onPress={() => setSeason(s.key)}
              style={[styles.seasonItem, active && styles.seasonItemActive]}
            >
              <Ionicons
                name={s.icon as any}
                size={16}
                color={active ? palette.accent : palette.textMuted}
              />
              <Text style={[styles.seasonLabel, active && styles.seasonLabelActive]}>
                {s.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {/* Conditions Card */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Current Conditions</Text>

        {/* Water Temperature */}
        <View style={styles.conditionRow}>
          <View style={styles.conditionLabelRow}>
            <Ionicons name="thermometer-outline" size={16} color={palette.textSecondary} />
            <Text style={styles.conditionLabel}>Water Temp</Text>
          </View>
          <View style={styles.tempInputWrap}>
            <TextInput
              style={styles.tempInput}
              keyboardType="number-pad"
              value={waterTemp}
              onChangeText={(v) => {
                const stripped = v.replace(/[^0-9]/g, '');
                if (stripped === '') {
                  setWaterTemp('');
                  return;
                }
                const n = parseInt(stripped, 10);
                if (n <= TEMP_MAX) setWaterTemp(String(n));
              }}
              maxLength={2}
              selectTextOnFocus
            />
            <Text style={styles.tempUnit}>°F</Text>
          </View>
        </View>

        {/* Preferred range hint */}
        {species && (
          <Text style={styles.tempHint}>
            {species.commonName} preferred: {species.preferredTemp.min}–{species.preferredTemp.max}°F
          </Text>
        )}

        {/* Water Clarity */}
        <View style={styles.conditionRow}>
          <View style={styles.conditionLabelRow}>
            <Ionicons name="water-outline" size={16} color={palette.textSecondary} />
            <Text style={styles.conditionLabel}>Water Clarity</Text>
          </View>
        </View>
        <View style={styles.optionRow}>
          {CLARITY_OPTIONS.map((opt) => {
            const active = opt.key === clarity;
            return (
              <Pressable
                key={opt.key}
                onPress={() => setClarity(opt.key)}
                style={[styles.optionBtn, active && styles.optionBtnActive]}
              >
                <Ionicons
                  name={opt.icon as any}
                  size={14}
                  color={active ? '#FFF' : palette.textSecondary}
                />
                <Text style={[styles.optionText, active && styles.optionTextActive]}>
                  {opt.label}
                </Text>
              </Pressable>
            );
          })}
        </View>

        {/* Time of Day */}
        <View style={styles.conditionRow}>
          <View style={styles.conditionLabelRow}>
            <Ionicons name="time-outline" size={16} color={palette.textSecondary} />
            <Text style={styles.conditionLabel}>Time of Day</Text>
          </View>
        </View>
        <View style={styles.optionRow}>
          {TIME_OPTIONS.map((opt) => {
            const active = opt.key === timeOfDay;
            return (
              <Pressable
                key={opt.key}
                onPress={() => setTimeOfDay(opt.key)}
                style={[styles.optionBtn, active && styles.optionBtnActive]}
              >
                <Ionicons
                  name={opt.icon as any}
                  size={14}
                  color={active ? '#FFF' : palette.textSecondary}
                />
                <Text style={[styles.optionText, active && styles.optionTextActive]}>
                  {opt.label}
                </Text>
              </Pressable>
            );
          })}
        </View>
      </View>

      {/* Recommended Baits */}
      <Text style={styles.sectionHeader}>
        Recommended Baits
        <Text style={styles.sectionCount}> ({recommendations.length})</Text>
      </Text>

      {loading ? (
        <View style={styles.loadingWrap}>
          <ActivityIndicator size="small" color={palette.accent} />
          <Text style={styles.loadingText}>Analyzing conditions...</Text>
        </View>
      ) : recommendations.length === 0 ? (
        <View style={styles.emptyWrap}>
          <Ionicons name="alert-circle-outline" size={32} color={palette.textMuted} />
          <Text style={styles.emptyText}>No recommendations for these conditions.</Text>
        </View>
      ) : (
        recommendations.map((bait, index) => {
          const badge = typeBadgeStyle(bait.type);
          const confPct = Math.round(bait.confidence * 100);
          const barColor = confidenceColor(bait.confidence);

          return (
            <View key={`${bait.name}-${index}`} style={styles.baitCard}>
              {/* Top row: name + type badge */}
              <View style={styles.baitHeader}>
                <Text style={styles.baitName}>{bait.name}</Text>
                <View style={[styles.typeBadge, { backgroundColor: badge.bg }]}>
                  <Text style={[styles.typeBadgeText, { color: badge.fg }]}>{badge.label}</Text>
                </View>
              </View>

              {/* Category + presentation */}
              <Text style={styles.baitMeta}>
                {bait.category} · {capitalize(bait.presentation)}
              </Text>

              {/* Confidence bar */}
              <View style={styles.confidenceRow}>
                <Text style={styles.confidenceLabel}>Match</Text>
                <View style={styles.confidenceTrack}>
                  <View
                    style={[
                      styles.confidenceFill,
                      { width: `${confPct}%` as any, backgroundColor: barColor },
                    ]}
                  />
                </View>
                <Text style={[styles.confidencePct, { color: barColor }]}>{confPct}%</Text>
              </View>

              {/* Color recommendation */}
              <View style={styles.detailRow}>
                <Ionicons name="color-palette-outline" size={14} color={palette.textMuted} />
                <Text style={styles.detailText}>
                  <Text style={styles.detailLabel}>Color: </Text>
                  {bait.color}
                </Text>
              </View>

              {/* Technique tip */}
              {bait.tip && (
                <View style={styles.detailRow}>
                  <Ionicons name="bulb-outline" size={14} color={palette.warning} />
                  <Text style={styles.detailText}>{bait.tip}</Text>
                </View>
              )}

              {/* Best conditions */}
              <View style={styles.detailRow}>
                <Ionicons name={seasonIcon(season) as any} size={14} color={palette.textMuted} />
                <Text style={styles.detailText}>
                  <Text style={styles.detailLabel}>Season: </Text>
                  {capitalize(season)} pick
                  {bait.presentation === 'topwater'
                    ? ' · Best in low light'
                    : bait.presentation === 'deep'
                      ? ' · Target deeper structure'
                      : ''}
                </Text>
              </View>
            </View>
          );
        })
      )}

      {/* Pro Tips */}
      {species && PRO_TIPS[species.id] && (
        <>
          <Text style={styles.sectionHeader}>
            <Ionicons name="school-outline" size={18} color={palette.text} /> Pro Tips
          </Text>
          <View style={styles.card}>
            {PRO_TIPS[species.id].map((tip, i) => (
              <View key={i} style={styles.tipRow}>
                <View style={styles.tipBullet}>
                  <Text style={styles.tipBulletText}>{i + 1}</Text>
                </View>
                <Text style={styles.tipText}>{tip}</Text>
              </View>
            ))}
          </View>
        </>
      )}

      {/* Species Info Footer */}
      {species && (
        <View style={[styles.card, styles.speciesInfoCard]}>
          <Text style={styles.cardTitle}>{species.commonName}</Text>
          <Text style={styles.scientificName}>{species.scientificName}</Text>

          <View style={styles.speciesStatRow}>
            <View style={styles.speciesStat}>
              <Ionicons name="thermometer-outline" size={14} color={palette.accent} />
              <Text style={styles.speciesStatLabel}>Temp Range</Text>
              <Text style={styles.speciesStatValue}>
                {species.preferredTemp.min}–{species.preferredTemp.max}°F
              </Text>
            </View>
            <View style={styles.speciesStat}>
              <Ionicons name="arrow-down-outline" size={14} color={palette.accent} />
              <Text style={styles.speciesStatLabel}>Depth Range</Text>
              <Text style={styles.speciesStatValue}>
                {species.preferredDepth.min}–{species.preferredDepth.max} ft
              </Text>
            </View>
            <View style={styles.speciesStat}>
              <Ionicons name="heart-outline" size={14} color={palette.accent} />
              <Text style={styles.speciesStatLabel}>Spawn Temp</Text>
              <Text style={styles.speciesStatValue}>
                {species.spawnTemp.min}–{species.spawnTemp.max}°F
              </Text>
            </View>
          </View>

          <Text style={styles.speciesDetailHeading}>Primary Diet</Text>
          <Text style={styles.speciesDetailText}>{species.diet.join(', ')}</Text>

          <Text style={styles.speciesDetailHeading}>Key Habitat</Text>
          <Text style={styles.speciesDetailText}>{species.habitat.join(', ')}</Text>
        </View>
      )}

      <View style={{ height: 40 }} />
    </ScrollView>
  );
}

// ── Styles ────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  content: {
    paddingHorizontal: spacing.lg,
    paddingTop: Platform.OS === 'ios' ? 60 : spacing.xxl,
    paddingBottom: spacing.xxxl,
  },
  screenTitle: {
    ...typeStyles.screenTitle,
    color: palette.text,
    marginBottom: 2,
  },
  subtitle: {
    fontSize: typography.bodySmall,
    color: palette.textMuted,
    marginBottom: spacing.lg,
  },

  // ── Species Pills ──
  pillScroll: {
    marginHorizontal: -spacing.lg,
    marginBottom: spacing.md,
  },
  pillRow: {
    paddingHorizontal: spacing.lg,
    gap: spacing.sm,
  },
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: radius.full,
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.border,
  },
  pillActive: {
    backgroundColor: palette.accent,
    borderColor: palette.accent,
  },
  pillText: {
    fontSize: typography.bodySmall,
    color: palette.textSecondary,
    fontWeight: '500',
  },
  pillTextActive: {
    color: '#FFF',
  },

  // ── Season Bar ──
  seasonBar: {
    flexDirection: 'row',
    backgroundColor: palette.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: palette.border,
    marginBottom: spacing.lg,
    overflow: 'hidden',
  },
  seasonItem: {
    flex: 1,
    alignItems: 'center',
    paddingVertical: 10,
    gap: 2,
  },
  seasonItemActive: {
    backgroundColor: palette.accentDim,
    borderBottomWidth: 2,
    borderBottomColor: palette.accent,
  },
  seasonLabel: {
    fontSize: typography.caption,
    color: palette.textMuted,
    fontWeight: '500',
  },
  seasonLabelActive: {
    color: palette.accent,
    fontWeight: '600',
  },

  // ── Card ──
  card: {
    backgroundColor: palette.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: palette.borderLight,
    padding: spacing.lg,
    marginBottom: spacing.lg,
  },
  cardTitle: {
    ...typeStyles.cardTitle,
    color: palette.text,
    marginBottom: spacing.md,
  },

  // ── Conditions ──
  conditionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: spacing.xs,
  },
  conditionLabelRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  conditionLabel: {
    fontSize: typography.body,
    color: palette.textSecondary,
    fontWeight: '500',
  },
  tempInputWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.surfaceRaised,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: palette.border,
    paddingHorizontal: 10,
    paddingVertical: Platform.OS === 'ios' ? 6 : 2,
  },
  tempInput: {
    fontSize: typography.body,
    color: palette.text,
    fontWeight: '600',
    minWidth: 32,
    textAlign: 'center',
    padding: 0,
  },
  tempUnit: {
    fontSize: typography.bodySmall,
    color: palette.textMuted,
    marginLeft: 2,
  },
  tempHint: {
    fontSize: typography.caption,
    color: palette.textMuted,
    marginBottom: spacing.md,
    fontStyle: 'italic',
  },
  optionRow: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginBottom: spacing.md,
  },
  optionBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    paddingVertical: 8,
    borderRadius: radius.sm,
    backgroundColor: palette.surfaceRaised,
    borderWidth: 1,
    borderColor: palette.border,
  },
  optionBtnActive: {
    backgroundColor: palette.accent,
    borderColor: palette.accent,
  },
  optionText: {
    fontSize: typography.caption,
    color: palette.textSecondary,
    fontWeight: '500',
  },
  optionTextActive: {
    color: '#FFF',
  },

  // ── Section Headers ──
  sectionHeader: {
    ...typeStyles.sectionHeader,
    color: palette.text,
    marginBottom: spacing.md,
    marginTop: spacing.sm,
  },
  sectionCount: {
    fontSize: typography.bodySmall,
    color: palette.textMuted,
    fontWeight: '400',
  },

  // ── Loading / Empty ──
  loadingWrap: {
    alignItems: 'center',
    paddingVertical: spacing.xxxl,
    gap: spacing.sm,
  },
  loadingText: {
    fontSize: typography.bodySmall,
    color: palette.textMuted,
  },
  emptyWrap: {
    alignItems: 'center',
    paddingVertical: spacing.xxxl,
    gap: spacing.sm,
  },
  emptyText: {
    fontSize: typography.body,
    color: palette.textMuted,
  },

  // ── Bait Card ──
  baitCard: {
    backgroundColor: palette.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: palette.borderLight,
    padding: spacing.lg,
    marginBottom: spacing.md,
  },
  baitHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 4,
  },
  baitName: {
    fontSize: typography.body,
    fontWeight: '700',
    color: palette.text,
    flex: 1,
    marginRight: spacing.sm,
  },
  typeBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: radius.full,
  },
  typeBadgeText: {
    fontSize: typography.caption,
    fontWeight: '600',
  },
  baitMeta: {
    fontSize: typography.bodySmall,
    color: palette.textMuted,
    marginBottom: spacing.sm,
  },

  // ── Confidence ──
  confidenceRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: spacing.sm,
    gap: spacing.sm,
  },
  confidenceLabel: {
    fontSize: typography.caption,
    color: palette.textMuted,
    fontWeight: '500',
    width: 36,
  },
  confidenceTrack: {
    flex: 1,
    height: 6,
    borderRadius: 3,
    backgroundColor: palette.surfaceRaised,
    overflow: 'hidden',
  },
  confidenceFill: {
    height: '100%',
    borderRadius: 3,
  },
  confidencePct: {
    fontSize: typography.caption,
    fontWeight: '700',
    width: 34,
    textAlign: 'right',
  },

  // ── Detail Rows ──
  detailRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 6,
    marginBottom: 6,
  },
  detailLabel: {
    fontWeight: '600',
    color: palette.textSecondary,
  },
  detailText: {
    flex: 1,
    fontSize: typography.bodySmall,
    color: palette.textSecondary,
    lineHeight: 18,
  },

  // ── Pro Tips ──
  tipRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.md,
    marginBottom: spacing.md,
  },
  tipBullet: {
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: palette.accentDim,
    alignItems: 'center',
    justifyContent: 'center',
  },
  tipBulletText: {
    fontSize: typography.caption,
    fontWeight: '700',
    color: palette.accent,
  },
  tipText: {
    flex: 1,
    fontSize: typography.bodySmall,
    color: palette.textSecondary,
    lineHeight: 19,
  },

  // ── Species Info ──
  speciesInfoCard: {
    marginTop: spacing.sm,
  },
  // ── Context Banner ──
  contextBanner: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
    marginBottom: spacing.sm,
  },
  contextBannerText: {
    flex: 1,
  },

  scientificName: {
    fontSize: typography.bodySmall,
    color: palette.textMuted,
    fontStyle: 'italic',
    marginBottom: spacing.md,
    marginTop: -spacing.sm,
  },
  speciesStatRow: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginBottom: spacing.md,
  },
  speciesStat: {
    flex: 1,
    alignItems: 'center',
    backgroundColor: palette.surfaceRaised,
    borderRadius: radius.sm,
    paddingVertical: spacing.sm,
    gap: 2,
  },
  speciesStatLabel: {
    fontSize: typography.caption,
    color: palette.textMuted,
    fontWeight: '500',
  },
  speciesStatValue: {
    fontSize: typography.bodySmall,
    color: palette.text,
    fontWeight: '600',
  },
  speciesDetailHeading: {
    fontSize: typography.bodySmall,
    color: palette.textSecondary,
    fontWeight: '600',
    marginBottom: 2,
    marginTop: spacing.sm,
  },
  speciesDetailText: {
    fontSize: typography.bodySmall,
    color: palette.textMuted,
    lineHeight: 19,
  },
});
