import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import * as Location from 'expo-location';
import { Ionicons } from '@expo/vector-icons';
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

// ── Main Screen ──────────────────────────────────────────────────

export function IceFishingScreen() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [condition, setCondition] = useState<IceCondition | null>(null);
  const [iceOut, setIceOut] = useState<IceOutPrediction | null>(null);
  const [checklist, setChecklist] = useState<IceEquipmentItem[]>([]);
  const [expandedSpecies, setExpandedSpecies] = useState<string | null>(null);
  const [showChecklist, setShowChecklist] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setError('Location permission required to estimate ice conditions');
        setLoading(false);
        return;
      }

      const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
      const lat = loc.coords.latitude;
      const lon = loc.coords.longitude;

      const [iceData, iceOutData] = await Promise.all([
        getIceThickness(lat, lon),
        getIceOutDate(lat, lon),
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

  const toggleChecklistItem = useCallback((index: number) => {
    hapticLight();
    setChecklist((prev) =>
      prev.map((item, i) => (i === index ? { ...item, checked: !item.checked } : item)),
    );
  }, []);

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
        <Pressable style={styles.retryButton} onPress={loadData}>
          <Text style={styles.retryButtonText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {/* Safety Badge */}
        {condition && <SafetyBadge condition={condition} />}

        {/* Ice Thickness Card */}
        {condition && <ThicknessCard condition={condition} />}

        {/* Safety Guide */}
        {condition && <SafetyGuide condition={condition} />}

        {/* Ice-Out Prediction */}
        {iceOut && iceOut.estimatedDate && <IceOutCard prediction={iceOut} />}

        {/* Winter Species Tips */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Winter Species Tips</Text>
          {WINTER_SPECIES_TIPS.map((tip) => (
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

        {/* Equipment Checklist */}
        <View style={styles.section}>
          <Pressable
            style={styles.checklistHeader}
            onPress={() => {
              hapticLight();
              setShowChecklist((p) => !p);
            }}
          >
            <View style={styles.checklistHeaderLeft}>
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
                : `Avg ${Math.round(condition.recentAvgTempF)}°F (7-day)`}
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

  // Safety Badge
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

  // Cards
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

  // Thickness
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

  // Threshold bar
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

  // Ice-out
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

  // Section
  section: {
    gap: 10,
  },
  sectionTitle: {
    ...typeStyles.sectionHeader,
    color: palette.text,
  },

  // Species tips
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

  // Checklist
  checklistHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  checklistHeaderLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
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
