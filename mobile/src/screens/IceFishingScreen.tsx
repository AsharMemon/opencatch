/**
 * OpenCatch -- Ice Fishing Screen (Redesigned)
 *
 * Map-integrated ice fishing tool. Instead of a static info page, this screen
 * helps anglers make actionable decisions:
 *
 * 1. "Show on Map" -- activates the ice thickness overlay on the map
 * 2. "Find Ice Fishing Near Me" -- filters map to safe ice lakes
 * 3. Contextual conditions at user's location with safety-first design
 * 4. Species tips and checklist moved to collapsible sections
 *
 * Research-backed redesign:
 * - Anglers want to FIND spots, not just read about ice (In-Depth Outdoors forums)
 * - Real-time ice condition verification is the #1 request (Reddit, ice fishing forums)
 * - Safety-first approach matches DNR and fishing community best practices
 * - Equipment checklist is used pre-trip, not in the field -- moved to bottom
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useNavigation } from '@react-navigation/native';
import { FeatureLocationPicker } from '../components/FeatureLocationPicker';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { hapticLight } from '../utils/haptics';
import {
  getIceThickness,
  getIceOutDate,
  isIceSafe,
  WINTER_SPECIES_TIPS,
  getIceEquipmentChecklist,
  type IceCondition,
  type IceOutPrediction,
  type IceEquipmentItem,
  type WinterSpeciesTip,
} from '../services/iceFishing';
import {
  getCurrentFeatureLocation,
  searchFeatureLocation,
  type FeatureLocation,
} from '../services/featureLocation';

// ── Main Screen ──────────────────────────────────────────────────

export function IceFishingScreen() {
  const navigation = useNavigation<any>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [condition, setCondition] = useState<IceCondition | null>(null);
  const [iceOut, setIceOut] = useState<IceOutPrediction | null>(null);
  const [checklist, setChecklist] = useState<IceEquipmentItem[]>([]);
  const [expandedSpecies, setExpandedSpecies] = useState<string | null>(null);
  const [showChecklist, setShowChecklist] = useState(false);
  const [showSpecies, setShowSpecies] = useState(false);
  const [selectedLocation, setSelectedLocation] = useState<FeatureLocation | null>(null);

  const loadData = useCallback(async (location?: FeatureLocation) => {
    setLoading(true);
    setError(null);

    try {
      const activeLocation = location ?? await getCurrentFeatureLocation({
        lat: 47.5,
        lon: -93.5,
        label: 'Default ice region',
      });
      setSelectedLocation(activeLocation);

      const [iceData, iceOutData] = await Promise.all([
        getIceThickness(activeLocation.lat, activeLocation.lon),
        getIceOutDate(activeLocation.lat, activeLocation.lon),
      ]);

      setCondition(iceData);
      setIceOut(iceOutData);
      setChecklist(getIceEquipmentChecklist());
    } catch (err) {
      console.warn('[OpenCatch] Ice fishing data error:', err);
      setError('Failed to load ice conditions. Please try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleUseCurrentLocation = useCallback(async () => {
    const location = await getCurrentFeatureLocation({
      lat: 47.5,
      lon: -93.5,
      label: 'Default ice region',
    });
    await loadData(location);
  }, [loadData]);

  const handleSearchLocation = useCallback(async (query: string) => {
    const location = await searchFeatureLocation(query);
    await loadData(location);
  }, [loadData]);

  const toggleChecklistItem = useCallback((index: number) => {
    hapticLight();
    setChecklist((prev) =>
      prev.map((item, i) => (i === index ? { ...item, checked: !item.checked } : item)),
    );
  }, []);

  const handleShowOnMap = useCallback(() => {
    hapticLight();
    // Navigate back to map and activate the ice thickness overlay
    navigation.navigate('Tabs', {
      screen: 'MapTab',
      params: { activateOverlay: 'ice-thickness' },
    });
  }, [navigation]);

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={palette.accent} />
        <Text style={styles.loadingText}>Analyzing ice conditions...</Text>
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.centered}>
        <Ionicons name="alert-circle-outline" size={48} color={palette.error} />
        <Text style={styles.errorText}>{error}</Text>
        <Pressable style={styles.retryButton} onPress={() => { void loadData(); }}>
          <Text style={styles.retryButtonText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        <FeatureLocationPicker
          label={selectedLocation?.label ?? 'Finding your location'}
          helperText={
            selectedLocation?.source === 'search'
              ? 'Ice thickness and ice-out estimates are using your searched lake or area.'
              : 'Ice thickness and ice-out estimates are using this location.'
          }
          loading={loading}
          activeSource={selectedLocation?.source}
          onUseCurrent={handleUseCurrentLocation}
          onSearchLocation={handleSearchLocation}
        />

        {/* ── Map Action Buttons ── */}
        <View style={styles.mapActions}>
          <Pressable
            style={styles.mapActionPrimary}
            onPress={handleShowOnMap}
          >
            <Ionicons name="map" size={20} color="#FFFFFF" />
            <View style={styles.mapActionTextCol}>
              <Text style={styles.mapActionTitle}>View Ice Map</Text>
              <Text style={styles.mapActionSub}>See thickness across all lakes</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color="#FFFFFF80" />
          </Pressable>

          <View style={styles.mapActionRow}>
            <Pressable
              style={styles.mapActionSecondary}
              onPress={() => {
                hapticLight();
                navigation.navigate('Tabs', {
                  screen: 'MapTab',
                  params: { activateOverlay: 'ice-thickness', filterSafe: true },
                });
              }}
            >
              <Ionicons name="shield-checkmark" size={18} color={palette.success} />
              <Text style={styles.mapActionSecText}>Safe Lakes Only</Text>
            </Pressable>

            <Pressable
              style={styles.mapActionSecondary}
              onPress={() => {
                hapticLight();
                navigation.navigate('Tabs', {
                  screen: 'MapTab',
                  params: { activateOverlay: 'ice-thickness', showAccess: true },
                });
              }}
            >
              <Ionicons name="car" size={18} color={palette.accent} />
              <Text style={styles.mapActionSecText}>Access Points</Text>
            </Pressable>
          </View>
        </View>

        {/* ── Safety Badge ── */}
        {condition && <SafetyBadge condition={condition} />}

        {/* ── Ice Thickness Card ── */}
        {condition && <ThicknessCard condition={condition} />}

        {/* ── Safety Guide ── */}
        {condition && <SafetyGuide condition={condition} />}

        {/* ── Ice-Out Prediction ── */}
        {iceOut && iceOut.estimatedDate && <IceOutCard prediction={iceOut} />}

        {/* ── Winter Species Tips (collapsible) ── */}
        <View style={styles.section}>
          <Pressable
            style={styles.sectionToggle}
            onPress={() => {
              hapticLight();
              setShowSpecies((p) => !p);
            }}
          >
            <View style={styles.sectionToggleLeft}>
              <Ionicons name="fish-outline" size={20} color={palette.accent} />
              <Text style={styles.sectionTitle}>Winter Species Tips</Text>
            </View>
            <Ionicons
              name={showSpecies ? 'chevron-up' : 'chevron-down'}
              size={18}
              color={palette.textMuted}
            />
          </Pressable>

          {showSpecies && WINTER_SPECIES_TIPS.map((tip) => (
            <SpeciesTipCard
              key={tip.species}
              tip={tip}
              expanded={expandedSpecies === tip.species}
              onToggle={() => {
                hapticLight();
                setExpandedSpecies((prev) =>
                  prev === tip.species ? null : tip.species,
                );
              }}
            />
          ))}
        </View>

        {/* ── Equipment Checklist (collapsible) ── */}
        <View style={styles.section}>
          <Pressable
            style={styles.sectionToggle}
            onPress={() => {
              hapticLight();
              setShowChecklist((p) => !p);
            }}
          >
            <View style={styles.sectionToggleLeft}>
              <Ionicons name="checkbox-outline" size={20} color={palette.accent} />
              <Text style={styles.sectionTitle}>Equipment Checklist</Text>
            </View>
            <View style={styles.checklistProgress}>
              <Text style={styles.checklistCount}>
                {checklist.filter((i) => i.checked).length}/{checklist.length}
              </Text>
              <Ionicons
                name={showChecklist ? 'chevron-up' : 'chevron-down'}
                size={18}
                color={palette.textMuted}
              />
            </View>
          </Pressable>

          {showChecklist && (
            <View style={styles.checklistBody}>
              {(['essential', 'recommended', 'optional'] as const).map((category) => {
                const items = checklist.filter((i) => i.category === category);
                if (items.length === 0) return null;
                return (
                  <View key={category} style={styles.checklistCategory}>
                    <Text style={styles.checklistCategoryLabel}>
                      {category.charAt(0).toUpperCase() + category.slice(1)}
                    </Text>
                    {items.map((item) => {
                      const idx = checklist.indexOf(item);
                      return (
                        <Pressable
                          key={item.name}
                          style={styles.checklistItem}
                          onPress={() => toggleChecklistItem(idx)}
                        >
                          <Ionicons
                            name={item.checked ? 'checkbox' : 'square-outline'}
                            size={20}
                            color={item.checked ? palette.success : palette.textMuted}
                          />
                          <View style={styles.checklistItemText}>
                            <Text
                              style={[
                                styles.checklistItemName,
                                item.checked && styles.checklistItemChecked,
                              ]}
                            >
                              {item.name}
                            </Text>
                            <Text style={styles.checklistItemDesc} numberOfLines={1}>
                              {item.description}
                            </Text>
                          </View>
                        </Pressable>
                      );
                    })}
                  </View>
                );
              })}
            </View>
          )}
        </View>

        <View style={{ height: 40 }} />
      </ScrollView>
    </View>
  );
}

// ── Sub-components ───────────────────────────────────────────────

function SafetyBadge({ condition }: { condition: IceCondition }) {
  const isUnsafe = condition.safetyRating === 'unsafe' || condition.safetyRating === 'caution';

  return (
    <View style={[styles.safetyBadge, { backgroundColor: condition.safetyColor + '14' }]}>
      <View style={[styles.safetyDot, { backgroundColor: condition.safetyColor }]} />
      <View style={styles.safetyBadgeContent}>
        <Text style={[styles.safetyBadgeLabel, { color: condition.safetyColor }]}>
          {condition.safetyLabel}
        </Text>
        {isUnsafe && (
          <View style={styles.safetyWarningRow}>
            <Ionicons name="warning" size={14} color={condition.safetyColor} />
            <Text style={[styles.safetyWarningText, { color: condition.safetyColor }]}>
              Do not venture onto the ice
            </Text>
          </View>
        )}
      </View>
    </View>
  );
}

function ThicknessCard({ condition }: { condition: IceCondition }) {
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>Estimated Ice Thickness</Text>
      <View style={styles.thicknessRow}>
        <Text style={[styles.thicknessValue, { color: condition.safetyColor }]}>
          {condition.thicknessInches.toFixed(1)}"
        </Text>
        <View style={styles.thicknessDetails}>
          <View style={styles.detailRow}>
            <Ionicons
              name={
                condition.trend === 'growing'
                  ? 'trending-up'
                  : condition.trend === 'deteriorating'
                    ? 'trending-down'
                    : 'remove'
              }
              size={16}
              color={
                condition.trend === 'growing'
                  ? palette.success
                  : condition.trend === 'deteriorating'
                    ? palette.error
                    : palette.textMuted
              }
            />
            <Text style={styles.detailText}>
              {condition.trend === 'growing'
                ? 'Ice is growing'
                : condition.trend === 'deteriorating'
                  ? 'Ice is weakening'
                  : 'Ice is stable'}
            </Text>
          </View>
          <View style={styles.detailRow}>
            <Ionicons name="thermometer-outline" size={16} color={palette.textMuted} />
            <Text style={styles.detailText}>
              {isNaN(condition.recentAvgTempF)
                ? 'Temp unavailable'
                : `Avg ${Math.round(condition.recentAvgTempF)}\u00B0F (7-day)`}
            </Text>
          </View>
          <View style={styles.detailRow}>
            <Ionicons name="snow-outline" size={16} color={palette.textMuted} />
            <Text style={styles.detailText}>
              {condition.freezingDegreeDays} FDD accumulated
            </Text>
          </View>
        </View>
      </View>
      <Text style={styles.disclaimer}>
        Estimates based on air temperature models. Always verify with an auger or spud bar.
      </Text>
    </View>
  );
}

function SafetyGuide({ condition }: { condition: IceCondition }) {
  const thresholds = [
    { label: '4"+ Walk / Ice Fish', inches: 4, color: '#2E7D32' },
    { label: '5"+ Snowmobile / ATV', inches: 5, color: '#1B5E20' },
    { label: '8-12" Small Vehicle', inches: 8, color: '#0D47A1' },
    { label: '12-15" Medium Truck', inches: 12, color: '#1A237E' },
  ];

  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>Safety Guide</Text>
      <Text style={styles.cardBody}>{condition.safetyGuidelines}</Text>
      <View style={styles.thresholdBar}>
        {thresholds.map((t) => {
          const isMet = condition.thicknessInches >= t.inches;
          return (
            <View key={t.label} style={styles.thresholdItem}>
              <View
                style={[
                  styles.thresholdIndicator,
                  { backgroundColor: isMet ? t.color : palette.surfaceAlt },
                ]}
              >
                <Ionicons
                  name={isMet ? 'checkmark-circle' : 'close-circle'}
                  size={16}
                  color={isMet ? '#fff' : palette.textDim}
                />
              </View>
              <Text style={[styles.thresholdLabel, isMet && { color: t.color, fontWeight: '600' }]}>
                {t.label}
              </Text>
            </View>
          );
        })}
      </View>
    </View>
  );
}

function IceOutCard({ prediction }: { prediction: IceOutPrediction }) {
  return (
    <View style={styles.card}>
      <View style={styles.cardTitleRow}>
        <Ionicons name="calendar-outline" size={18} color={palette.accent} />
        <Text style={styles.cardTitle}>Ice-Out Prediction</Text>
      </View>
      {prediction.daysUntilIceOut > 0 ? (
        <View style={styles.iceOutRow}>
          <Text style={styles.iceOutDays}>{prediction.daysUntilIceOut}</Text>
          <Text style={styles.iceOutDaysLabel}>days until estimated ice-out</Text>
        </View>
      ) : null}
      <Text style={styles.cardBody}>{prediction.description}</Text>
      <View style={styles.confidenceBadge}>
        <Text style={styles.confidenceText}>
          Confidence: {prediction.confidence}
        </Text>
      </View>
    </View>
  );
}

function SpeciesTipCard({
  tip,
  expanded,
  onToggle,
}: {
  tip: WinterSpeciesTip;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <Pressable
      style={[styles.speciesCard, expanded && styles.speciesCardExpanded]}
      onPress={onToggle}
    >
      <View style={styles.speciesHeader}>
        <View style={styles.speciesNameRow}>
          <Ionicons name={tip.icon as any} size={18} color={palette.accent} />
          <Text style={styles.speciesName}>{tip.species}</Text>
        </View>
        <Ionicons
          name={expanded ? 'chevron-up' : 'chevron-down'}
          size={18}
          color={palette.textMuted}
        />
      </View>

      {!expanded && (
        <Text style={styles.speciesQuick} numberOfLines={1}>
          {tip.bestDepthFt} | {tip.bestTime}
        </Text>
      )}

      {expanded && (
        <View style={styles.speciesBody}>
          <View style={styles.speciesDetailRow}>
            <Ionicons name="resize-outline" size={14} color={palette.textMuted} />
            <Text style={styles.speciesDetailLabel}>Depth:</Text>
            <Text style={styles.speciesDetailValue}>{tip.bestDepthFt}</Text>
          </View>
          <View style={styles.speciesDetailRow}>
            <Ionicons name="time-outline" size={14} color={palette.textMuted} />
            <Text style={styles.speciesDetailLabel}>Best Time:</Text>
            <Text style={styles.speciesDetailValue}>{tip.bestTime}</Text>
          </View>
          <View style={styles.speciesDetailRow}>
            <Ionicons name="bug-outline" size={14} color={palette.textMuted} />
            <Text style={styles.speciesDetailLabel}>Bait:</Text>
            <Text style={styles.speciesDetailValue}>{tip.bait.join(', ')}</Text>
          </View>
          <Text style={styles.speciesTechnique}>{tip.technique}</Text>
        </View>
      )}
    </Pressable>
  );
}

// ── Styles ────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  content: {
    padding: 20,
    gap: 16,
  },
  centered: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: palette.background,
    padding: 32,
    gap: 12,
  },
  loadingText: {
    fontSize: 14,
    color: palette.textMuted,
    marginTop: 8,
  },
  errorText: {
    fontSize: 15,
    color: palette.textSecondary,
    textAlign: 'center',
    lineHeight: 22,
  },
  retryButton: {
    backgroundColor: palette.accent,
    paddingHorizontal: 24,
    paddingVertical: 10,
    borderRadius: 8,
    marginTop: 8,
  },
  retryButtonText: {
    color: '#fff',
    fontWeight: '600',
    fontSize: 14,
  },

  // ── Map Action Buttons ──
  mapActions: {
    gap: 10,
  },
  mapActionPrimary: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.accent,
    borderRadius: 14,
    padding: 16,
    gap: 14,
    shadowColor: palette.accent,
    shadowOpacity: 0.25,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 4,
  },
  mapActionTextCol: {
    flex: 1,
  },
  mapActionTitle: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '700',
  },
  mapActionSub: {
    color: '#FFFFFFA0',
    fontSize: 12,
    marginTop: 2,
  },
  mapActionRow: {
    flexDirection: 'row',
    gap: 10,
  },
  mapActionSecondary: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: palette.surface,
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: palette.border,
  },
  mapActionSecText: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.text,
  },

  // ── Safety Badge ──
  safetyBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 16,
    borderRadius: 14,
    gap: 12,
  },
  safetyDot: {
    width: 12,
    height: 12,
    borderRadius: 6,
  },
  safetyBadgeContent: {
    flex: 1,
    gap: 4,
  },
  safetyBadgeLabel: {
    fontSize: 18,
    fontWeight: '700',
    letterSpacing: -0.2,
  },
  safetyWarningRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  safetyWarningText: {
    fontSize: 13,
    fontWeight: '500',
  },

  // ── Cards ──
  card: {
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 16,
    gap: 10,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  cardTitle: {
    ...typeStyles.cardTitle,
    color: palette.text,
  },
  cardTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  cardBody: {
    ...typeStyles.bodySmall,
    color: palette.textSecondary,
  },

  // ── Thickness ──
  thicknessRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 16,
  },
  thicknessValue: {
    fontSize: 48,
    fontWeight: '800',
    fontVariant: ['tabular-nums'],
    letterSpacing: -1,
  },
  thicknessDetails: {
    flex: 1,
    gap: 6,
  },
  detailRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  detailText: {
    fontSize: 13,
    color: palette.textSecondary,
  },
  disclaimer: {
    fontSize: 11,
    color: palette.textDim,
    fontStyle: 'italic',
    lineHeight: 16,
  },

  // ── Threshold bar ──
  thresholdBar: {
    gap: 8,
    marginTop: 4,
  },
  thresholdItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  thresholdIndicator: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  thresholdLabel: {
    fontSize: 13,
    color: palette.textMuted,
  },

  // ── Ice-out ──
  iceOutRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 8,
  },
  iceOutDays: {
    ...typeStyles.stat,
    color: palette.accent,
  },
  iceOutDaysLabel: {
    fontSize: 14,
    color: palette.textSecondary,
  },
  confidenceBadge: {
    alignSelf: 'flex-start',
    backgroundColor: palette.surfaceAlt,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 6,
  },
  confidenceText: {
    fontSize: 11,
    color: palette.textMuted,
    fontWeight: '500',
    textTransform: 'capitalize',
  },

  // ── Section ──
  section: {
    gap: 10,
  },
  sectionTitle: {
    ...typeStyles.sectionHeader,
    color: palette.text,
  },
  sectionToggle: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 4,
  },
  sectionToggleLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },

  // ── Species tips ──
  speciesCard: {
    backgroundColor: palette.surface,
    borderRadius: 12,
    padding: 14,
    gap: 6,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  speciesCardExpanded: {
    borderColor: palette.accent + '30',
    borderWidth: 1,
  },
  speciesHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  speciesNameRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  speciesName: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.text,
  },
  speciesQuick: {
    fontSize: 12,
    color: palette.textMuted,
    paddingLeft: 26,
  },
  speciesBody: {
    gap: 6,
    paddingTop: 4,
    paddingLeft: 26,
  },
  speciesDetailRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  speciesDetailLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.textSecondary,
    width: 65,
  },
  speciesDetailValue: {
    fontSize: 12,
    color: palette.textSecondary,
    flex: 1,
  },
  speciesTechnique: {
    fontSize: 12,
    color: palette.textMuted,
    lineHeight: 18,
    marginTop: 4,
    fontStyle: 'italic',
  },

  // ── Checklist ──
  checklistProgress: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  checklistCount: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textMuted,
  },
  checklistBody: {
    gap: 12,
  },
  checklistCategory: {
    gap: 6,
  },
  checklistCategoryLabel: {
    fontSize: 12,
    fontWeight: '700',
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  checklistItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 4,
  },
  checklistItemText: {
    flex: 1,
  },
  checklistItemName: {
    fontSize: 14,
    fontWeight: '500',
    color: palette.text,
  },
  checklistItemChecked: {
    color: palette.textMuted,
    textDecorationLine: 'line-through',
  },
  checklistItemDesc: {
    fontSize: 11,
    color: palette.textDim,
  },
});
