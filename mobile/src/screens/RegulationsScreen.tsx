import React, { useState, useMemo } from 'react';
import {
  ScrollView,
  View,
  Text,
  TextInput,
  StyleSheet,
  Pressable,
  Linking,
  Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import {
  getAllRegulations,
  getRegulations,
  isInSeason,
  formatBagLimit,
  type FishingRegulation,
  type SeasonInfo,
} from '../services/fishingRegs';

// ── Helpers ──────────────────────────────────────────────────────────────────

const SPECIES_ICONS: Record<string, keyof typeof Ionicons.glyphMap> = {
  bass: 'fish',
  walleye: 'fish',
  trout: 'fish',
  catfish: 'fish',
  crappie: 'fish',
  pike: 'fish',
  muskie: 'fish',
  salmon: 'fish',
};

const SPECIES_LABEL: Record<string, string> = {
  bass: 'Bass',
  walleye: 'Walleye',
  trout: 'Trout',
  catfish: 'Catfish',
  crappie: 'Crappie',
  pike: 'Northern Pike',
  muskie: 'Muskellunge',
  salmon: 'Salmon',
};

function formatCurrency(amount: number, country: 'US' | 'CA'): string {
  const symbol = country === 'CA' ? 'CA$' : '$';
  return `${symbol}${amount}`;
}

// ── State Picker Pill ────────────────────────────────────────────────────────

function StatePill({
  label,
  code,
  selected,
  onPress,
}: {
  label: string;
  code: string;
  selected: boolean;
  onPress: (code: string) => void;
}) {
  return (
    <Pressable
      style={[styles.pill, selected && styles.pillSelected]}
      onPress={() => onPress(code)}
    >
      <Text style={[styles.pillText, selected && styles.pillTextSelected]}>
        {label}
      </Text>
    </Pressable>
  );
}

// ── License Info Card ────────────────────────────────────────────────────────

function LicenseCard({ reg }: { reg: FishingRegulation }) {
  const { licenseCost, country, licenseUrl } = reg;

  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Ionicons name="card-outline" size={20} color={palette.accent} />
        <Text style={styles.cardTitle}>Fishing License</Text>
      </View>

      <View style={styles.licenseCostRow}>
        <View style={styles.licenseCostItem}>
          <Text style={styles.licenseCostLabel}>Resident</Text>
          <Text style={styles.licenseCostValue}>
            {formatCurrency(licenseCost.resident, country)}
          </Text>
        </View>
        <View style={styles.licenseCostDivider} />
        <View style={styles.licenseCostItem}>
          <Text style={styles.licenseCostLabel}>Non-Resident</Text>
          <Text style={styles.licenseCostValue}>
            {formatCurrency(licenseCost.nonResident, country)}
          </Text>
        </View>
      </View>

      <View style={styles.licenseBadgeRow}>
        {licenseCost.youthFree && (
          <View style={[styles.badge, styles.badgeGreen]}>
            <Ionicons name="happy-outline" size={13} color={palette.success} />
            <Text style={[styles.badgeText, { color: palette.success }]}>
              Free under {licenseCost.youthMaxAge ?? 16}
            </Text>
          </View>
        )}
        {licenseCost.seniorDiscount && (
          <View style={[styles.badge, styles.badgeBlue]}>
            <Ionicons name="star-outline" size={13} color={palette.accent} />
            <Text style={[styles.badgeText, { color: palette.accent }]}>
              Senior Discount
            </Text>
          </View>
        )}
      </View>

      <Pressable
        style={styles.linkButton}
        onPress={() => Linking.openURL(licenseUrl)}
      >
        <Ionicons name="open-outline" size={16} color={palette.accent} />
        <Text style={styles.linkButtonText}>Buy License Online</Text>
      </Pressable>
    </View>
  );
}

// ── Free Fishing Days ────────────────────────────────────────────────────────

function FreeFishingDays({ days }: { days: string[] }) {
  if (days.length === 0) return null;

  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Ionicons name="calendar-outline" size={20} color={palette.warning} />
        <Text style={styles.cardTitle}>Free Fishing Days</Text>
      </View>
      <Text style={styles.freeDaysSubtext}>
        No license required on these dates
      </Text>
      {days.map((day, i) => (
        <View key={i} style={styles.freeDayRow}>
          <Ionicons name="checkmark-circle" size={16} color={palette.success} />
          <Text style={styles.freeDayText}>{day}</Text>
        </View>
      ))}
    </View>
  );
}

// ── Species Regulation Card ──────────────────────────────────────────────────

function SpeciesCard({
  species,
  season,
  stateCode,
}: {
  species: string;
  season: SeasonInfo;
  stateCode: string;
}) {
  const inSeason = isInSeason(stateCode, species);
  const isYearRound = season.openDate === 'Year-round';

  return (
    <View style={styles.card}>
      {/* Header row */}
      <View style={styles.speciesHeader}>
        <View style={styles.speciesNameRow}>
          <Ionicons
            name={(SPECIES_ICONS[species] ?? 'fish') as any}
            size={20}
            color={palette.accent}
          />
          <Text style={styles.speciesName}>
            {SPECIES_LABEL[species] ?? species}
          </Text>
        </View>
        <View
          style={[
            styles.seasonBadge,
            inSeason ? styles.seasonBadgeOpen : styles.seasonBadgeClosed,
          ]}
        >
          <View
            style={[
              styles.seasonDot,
              { backgroundColor: inSeason ? palette.success : palette.error },
            ]}
          />
          <Text
            style={[
              styles.seasonBadgeText,
              { color: inSeason ? palette.success : palette.error },
            ]}
          >
            {inSeason ? 'In Season' : 'Closed'}
          </Text>
        </View>
      </View>

      {/* Season dates */}
      <View style={styles.detailGrid}>
        <View style={styles.detailCell}>
          <Text style={styles.detailLabel}>Season</Text>
          <Text style={styles.detailValue}>
            {isYearRound ? 'Year-round' : `${season.openDate} - ${season.closeDate}`}
          </Text>
        </View>

        <View style={styles.detailCell}>
          <Text style={styles.detailLabel}>Daily Bag</Text>
          <Text style={styles.detailValue}>
            {season.dailyBag === 0 ? 'No limit' : season.dailyBag}
          </Text>
        </View>

        <View style={styles.detailCell}>
          <Text style={styles.detailLabel}>Possession</Text>
          <Text style={styles.detailValue}>
            {season.possessionLimit === 0 ? 'No limit' : season.possessionLimit}
          </Text>
        </View>

        {season.minSizeInches != null && (
          <View style={styles.detailCell}>
            <Text style={styles.detailLabel}>Min Size</Text>
            <Text style={styles.detailValue}>{season.minSizeInches}"</Text>
          </View>
        )}

        {season.maxSizeInches != null && (
          <View style={styles.detailCell}>
            <Text style={styles.detailLabel}>Max Size</Text>
            <Text style={styles.detailValue}>{season.maxSizeInches}"</Text>
          </View>
        )}

        {season.slotLimit && (
          <View style={styles.detailCell}>
            <Text style={styles.detailLabel}>Slot Limit</Text>
            <Text style={styles.detailValue}>
              Release {season.slotLimit.min}" - {season.slotLimit.max}"
            </Text>
          </View>
        )}
      </View>

      {/* Notes */}
      {season.notes ? (
        <View style={styles.speciesNoteRow}>
          <Ionicons
            name="information-circle-outline"
            size={15}
            color={palette.textMuted}
          />
          <Text style={styles.speciesNoteText}>{season.notes}</Text>
        </View>
      ) : null}
    </View>
  );
}

// ── Main Screen ──────────────────────────────────────────────────────────────

export function RegulationsScreen({ route }: any) {
  const allRegs = useMemo(() => getAllRegulations(), []);
  const initialCode = route?.params?.stateCode ?? allRegs[0].stateCode;
  const [selectedCode, setSelectedCode] = useState<string>(initialCode);
  const [searchQuery, setSearchQuery] = useState('');

  const reg = useMemo(
    () => getRegulations(selectedCode),
    [selectedCode],
  );

  const filteredRegs = useMemo(() => {
    if (!searchQuery.trim()) return allRegs;
    const q = searchQuery.toLowerCase();
    return allRegs.filter(
      (r) => r.state.toLowerCase().includes(q) || r.stateCode.toLowerCase().includes(q),
    );
  }, [allRegs, searchQuery]);

  if (!reg) {
    return (
      <View style={styles.empty}>
        <Text style={styles.emptyText}>
          No regulations data available for this region.
        </Text>
      </View>
    );
  }

  const speciesEntries = Object.entries(reg.generalSeason).filter(
    ([, v]) => v != null,
  ) as [string, SeasonInfo][];

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      showsVerticalScrollIndicator={false}
    >
      {/* Screen title */}
      <Text style={styles.screenTitle}>Fishing Regulations</Text>

      {/* Disclaimer banner */}
      <View style={styles.disclaimerBanner}>
        <Ionicons name="alert-circle-outline" size={16} color="#B45309" />
        <Text style={styles.disclaimerBannerText}>
          Regulations shown are for reference only. Always verify with official sources before fishing.
        </Text>
      </View>

      {/* Search bar for jurisdictions */}
      <View style={styles.searchBarRow}>
        <Ionicons name="search-outline" size={16} color={palette.textMuted} />
        <TextInput
          style={styles.searchInput}
          placeholder="Search state or province..."
          placeholderTextColor={palette.textDim}
          value={searchQuery}
          onChangeText={setSearchQuery}
          autoCorrect={false}
          clearButtonMode="while-editing"
        />
      </View>

      {/* State/province picker */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.pickerScroll}
        contentContainerStyle={styles.pickerContent}
      >
        {filteredRegs.map((r) => (
          <StatePill
            key={r.stateCode}
            label={r.state}
            code={r.stateCode}
            selected={r.stateCode === selectedCode}
            onPress={(code) => {
              setSelectedCode(code);
              setSearchQuery('');
            }}
          />
        ))}
        {filteredRegs.length === 0 && (
          <Text style={{ color: palette.textMuted, fontSize: 13, paddingVertical: 8 }}>
            No matching jurisdictions
          </Text>
        )}
      </ScrollView>

      {/* Region label */}
      <View style={styles.regionRow}>
        <Ionicons
          name={reg.country === 'CA' ? 'leaf-outline' : 'flag-outline'}
          size={16}
          color={palette.textMuted}
        />
        <Text style={styles.regionText}>
          {reg.state}
          {reg.country === 'CA' ? ', Canada' : ', USA'}
        </Text>
      </View>

      {/* License info */}
      <LicenseCard reg={reg} />

      {/* Free fishing days */}
      {reg.freeFishingDays && reg.freeFishingDays.length > 0 && (
        <FreeFishingDays days={reg.freeFishingDays} />
      )}

      {/* Species regulations */}
      <Text style={styles.sectionHeader}>Species Regulations</Text>
      {speciesEntries.map(([species, season]) => (
        <SpeciesCard
          key={species}
          species={species}
          season={season}
          stateCode={selectedCode}
        />
      ))}

      {/* Special notes */}
      {reg.specialNotes && reg.specialNotes.length > 0 && (
        <View style={styles.card}>
          <View style={styles.cardHeader}>
            <Ionicons
              name="alert-circle-outline"
              size={20}
              color={palette.warning}
            />
            <Text style={styles.cardTitle}>Special Notes</Text>
          </View>
          {reg.specialNotes.map((note, i) => (
            <View key={i} style={styles.specialNoteRow}>
              <Text style={styles.bulletDot}>{'\u2022'}</Text>
              <Text style={styles.specialNoteText}>{note}</Text>
            </View>
          ))}
        </View>
      )}

      {/* Official regulations link */}
      <Pressable
        style={styles.officialButton}
        onPress={() => Linking.openURL(reg.regulationsUrl)}
      >
        <Ionicons name="document-text-outline" size={18} color="#FFF" />
        <Text style={styles.officialButtonText}>View Official Regulations</Text>
        <Ionicons name="open-outline" size={16} color="rgba(255,255,255,0.7)" />
      </Pressable>

      {/* More jurisdictions note */}
      <View style={styles.comingSoonRow}>
        <Ionicons name="globe-outline" size={16} color={palette.textMuted} />
        <Text style={styles.comingSoonText}>
          More jurisdictions coming soon — all 50 states and 13 provinces.
        </Text>
      </View>

      {/* Disclaimer */}
      <Text style={styles.disclaimer}>
        Regulations shown are for reference only. Always verify with your
        state or province's official wildlife agency before fishing.
        Data may be outdated or incomplete.
      </Text>
    </ScrollView>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  content: {
    paddingBottom: 48,
  },

  // ── Title ──────────────────────────────────────────
  screenTitle: {
    ...typeStyles.screenTitle,
    color: palette.text,
    paddingHorizontal: 20,
    paddingTop: Platform.OS === 'ios' ? 60 : 24,
    paddingBottom: 8,
  },

  // ── Disclaimer banner ────────────────────────────────
  disclaimerBanner: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    backgroundColor: '#FEF3C7',
    marginHorizontal: 16,
    marginBottom: 12,
    padding: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#FDE68A',
  },
  disclaimerBannerText: {
    flex: 1,
    fontSize: 12,
    color: '#92400E',
    lineHeight: 17,
    fontWeight: '500',
  },

  // ── Search bar ──────────────────────────────────────
  searchBarRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginHorizontal: 16,
    marginBottom: 10,
    backgroundColor: palette.surface,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: palette.border,
  },
  searchInput: {
    flex: 1,
    fontSize: 14,
    color: palette.text,
    paddingVertical: 0,
  },

  // ── Coming soon ─────────────────────────────────────
  comingSoonRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginHorizontal: 20,
    marginTop: 16,
    marginBottom: 4,
    paddingVertical: 8,
  },
  comingSoonText: {
    flex: 1,
    fontSize: 12,
    color: palette.textMuted,
    fontStyle: 'italic',
  },

  // ── Picker ─────────────────────────────────────────
  pickerScroll: {
    flexGrow: 0,
    marginBottom: 12,
  },
  pickerContent: {
    paddingHorizontal: 16,
    gap: 8,
  },
  pill: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.border,
  },
  pillSelected: {
    backgroundColor: palette.accent,
    borderColor: palette.accent,
  },
  pillText: {
    fontSize: 14,
    fontWeight: '500',
    color: palette.textSecondary,
  },
  pillTextSelected: {
    color: '#FFFFFF',
  },

  // ── Region label ───────────────────────────────────
  regionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 20,
    marginBottom: 16,
  },
  regionText: {
    fontSize: 14,
    color: palette.textMuted,
  },

  // ── Card (shared) ─────────────────────────────────
  card: {
    backgroundColor: palette.surface,
    marginHorizontal: 16,
    marginBottom: 12,
    borderRadius: 14,
    padding: 16,
    borderWidth: 1,
    borderColor: palette.borderLight,
    ...Platform.select({
      ios: {
        shadowColor: '#000',
        shadowOpacity: 0.04,
        shadowRadius: 8,
        shadowOffset: { width: 0, height: 2 },
      },
      android: { elevation: 1 },
    }),
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 12,
  },
  cardTitle: {
    ...typeStyles.cardTitle,
    color: palette.text,
  },

  // ── License card ───────────────────────────────────
  licenseCostRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  licenseCostItem: {
    flex: 1,
    alignItems: 'center',
  },
  licenseCostLabel: {
    fontSize: 12,
    color: palette.textMuted,
    marginBottom: 2,
  },
  licenseCostValue: {
    fontSize: 22,
    fontWeight: '700',
    color: palette.text,
  },
  licenseCostDivider: {
    width: 1,
    height: 36,
    backgroundColor: palette.borderLight,
  },
  licenseBadgeRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 12,
  },
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 12,
  },
  badgeGreen: {
    backgroundColor: 'rgba(61, 139, 55, 0.08)',
  },
  badgeBlue: {
    backgroundColor: palette.accentDim,
  },
  badgeText: {
    fontSize: 12,
    fontWeight: '600',
  },
  linkButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingVertical: 8,
    borderTopWidth: 1,
    borderTopColor: palette.borderLight,
    marginTop: 4,
  },
  linkButtonText: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.accent,
  },

  // ── Free fishing days ──────────────────────────────
  freeDaysSubtext: {
    fontSize: 12,
    color: palette.textMuted,
    marginBottom: 10,
  },
  freeDayRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 4,
  },
  freeDayText: {
    fontSize: 14,
    color: palette.text,
  },

  // ── Section header ─────────────────────────────────
  sectionHeader: {
    ...typeStyles.sectionHeader,
    color: palette.text,
    paddingHorizontal: 20,
    marginTop: 8,
    marginBottom: 12,
  },

  // ── Species card ───────────────────────────────────
  speciesHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  speciesNameRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  speciesName: {
    ...typeStyles.cardTitle,
    color: palette.text,
    fontSize: 16,
  },
  seasonBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 10,
  },
  seasonBadgeOpen: {
    backgroundColor: 'rgba(61, 139, 55, 0.08)',
  },
  seasonBadgeClosed: {
    backgroundColor: 'rgba(196, 75, 75, 0.08)',
  },
  seasonDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
  },
  seasonBadgeText: {
    fontSize: 12,
    fontWeight: '700',
  },

  // Detail grid
  detailGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 4,
    marginBottom: 8,
  },
  detailCell: {
    width: '48%' as any,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginBottom: 4,
  },
  detailLabel: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 2,
  },
  detailValue: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.text,
  },

  // Species notes
  speciesNoteRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 6,
    marginTop: 4,
    backgroundColor: palette.surfaceRaised,
    padding: 10,
    borderRadius: 8,
  },
  speciesNoteText: {
    flex: 1,
    fontSize: 13,
    color: palette.textSecondary,
    lineHeight: 18,
  },

  // ── Special notes ──────────────────────────────────
  specialNoteRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    paddingVertical: 3,
  },
  bulletDot: {
    fontSize: 16,
    color: palette.textMuted,
    lineHeight: 20,
  },
  specialNoteText: {
    flex: 1,
    fontSize: 14,
    color: palette.textSecondary,
    lineHeight: 20,
  },

  // ── Official button ────────────────────────────────
  officialButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: palette.accent,
    marginHorizontal: 16,
    marginTop: 8,
    paddingVertical: 14,
    borderRadius: 14,
  },
  officialButtonText: {
    fontSize: 15,
    fontWeight: '700',
    color: '#FFFFFF',
  },

  // ── Disclaimer ─────────────────────────────────────
  disclaimer: {
    fontSize: 11,
    color: palette.textDim,
    textAlign: 'center',
    paddingHorizontal: 32,
    marginTop: 16,
    lineHeight: 16,
  },

  // ── Empty state ────────────────────────────────────
  empty: {
    flex: 1,
    backgroundColor: palette.background,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
  },
  emptyText: {
    fontSize: 15,
    color: palette.textMuted,
    textAlign: 'center',
  },
});
