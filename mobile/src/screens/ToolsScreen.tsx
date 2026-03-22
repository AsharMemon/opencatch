/**
 * ToolsScreen — Progressive disclosure redesign.
 *
 * Design principles applied:
 * - "Micro-application" decomposition (Kammerhofer & Scholz, MDPI 2020)
 * - Progressive disclosure: show popular first, collapse categories
 * - Contextual recommendations: season + time-of-day aware
 * - Recently used tracking for personalization
 * - Hick's Law: reduce visible choices to speed decision-making
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  Pressable,
  LayoutAnimation,
  Platform,
  UIManager,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { hapticLight } from '../utils/haptics';

// Enable LayoutAnimation on Android
if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

const RECENT_TOOLS_KEY = '@opencatch_recent_tools';
const MAX_RECENT = 4;

// ── Tool definitions by category ─────────────────────────────────

interface ToolItem {
  label: string;
  description: string;
  ionicon: string;
  color: string;
  screen: string;
  params?: Record<string, any>;
  badge?: string;
  /** Used for contextual recommendations */
  tags?: string[];
}

interface ToolCategory {
  title: string;
  items: ToolItem[];
  /** If true, category is hidden outside its relevant season/context */
  seasonal?: boolean;
  /** Months when this category is relevant (1-12) */
  activeMonths?: number[];
}

const TOOL_CATEGORIES: ToolCategory[] = [
  {
    title: 'Conditions',
    items: [
      { label: 'Forecasts', description: 'AI fishing forecasts', ionicon: 'analytics', color: palette.accent, screen: 'ForecastsTab', tags: ['morning', 'planning', 'popular'] },
      { label: 'Tides', description: 'Tides & currents', ionicon: 'water-outline', color: '#1E88E5', screen: 'TideChart', params: { stationId: '', stationName: 'Find Station' }, tags: ['coastal', 'planning'] },
      { label: 'Water Data', description: 'Temp, clarity, flow', ionicon: 'thermometer', color: '#1565C0', screen: 'WaterInsights', params: {}, tags: ['morning', 'planning', 'popular'] },
      { label: 'Best Times', description: 'Peak fishing windows', ionicon: 'time', color: '#E53935', screen: 'BestTimes', tags: ['morning', 'planning', 'popular'] },
      { label: 'Pressure', description: 'Angler traffic trends', ionicon: 'speedometer-outline', color: '#FB8C00', screen: 'FishingPressure', tags: ['weekend'] },
      { label: 'Sun & Moon', description: 'Solunar periods', ionicon: 'moon', color: '#5C6BC0', screen: 'SunMoon', tags: ['morning', 'planning'] },
    ],
  },
  {
    title: 'Planning',
    items: [
      { label: 'Regulations', description: 'Limits & seasons', ionicon: 'document-text', color: '#8B4513', screen: 'Regulations', params: {}, tags: ['planning', 'popular'] },
      { label: 'Species Guide', description: 'ID & techniques', ionicon: 'fish', color: '#4682B4', screen: 'SpeciesGuide', tags: ['planning', 'popular'] },
      { label: 'Bait Guide', description: 'Match the hatch', ionicon: 'bug', color: '#DAA520', screen: 'BaitGuide', params: {}, tags: ['planning'] },
      { label: 'Lake Finder', description: 'Find fishing spots', ionicon: 'search', color: '#0288D1', screen: 'LakeFinder', tags: ['planning', 'popular'] },
      { label: 'Knots & Rigs', description: 'Knot & rig guide', ionicon: 'link', color: '#6D4C41', screen: 'KnotGuide', tags: ['planning'] },
      { label: 'Safety', description: 'Float plan & alerts', ionicon: 'shield-checkmark', color: '#2E7D32', screen: 'Safety', tags: ['boating'] },
      { label: 'Alerts', description: 'Weather warnings', ionicon: 'warning', color: '#E53935', screen: 'Alerts', tags: ['planning'] },
      { label: 'Trip Planner', description: 'Plan fishing trips', ionicon: 'calendar', color: '#7C3AED', screen: 'TripPlanner', tags: ['planning'] },
    ],
  },
  {
    title: 'Recording',
    items: [
      { label: 'Track Trip', description: 'GPS track recording', ionicon: 'navigate', color: '#E65100', screen: 'TrackRecording', tags: ['on-water', 'popular'] },
      { label: 'Log Catch', description: 'Report a catch', ionicon: 'add-circle', color: palette.accent, screen: 'CatchReport', params: {}, tags: ['on-water', 'popular'] },
      { label: 'My Stats', description: 'Catch analytics', ionicon: 'stats-chart', color: palette.accent, screen: 'Stats', tags: ['evening', 'review'] },
      { label: 'Track History', description: 'Past trips', ionicon: 'trail-sign', color: '#6D4C41', screen: 'TrackHistory', tags: ['evening', 'review'] },
    ],
  },
  {
    title: 'Boating',
    items: [
      { label: 'Route Planner', description: 'Autoroute & nav', ionicon: 'navigate', color: '#0A6EBD', screen: 'RoutePlanner', tags: ['boating', 'popular', 'planning'] },
      { label: 'Trip Viz', description: 'Preview & review trips', ionicon: 'analytics', color: '#4CAF50', screen: 'TripVisualization', tags: ['boating', 'planning'] },
      { label: 'Fuel Calc', description: 'Trip fuel & range', ionicon: 'calculator-outline', color: '#E65100', screen: 'FuelCalculator', tags: ['boating'] },
      { label: 'Maintenance', description: 'Engine hours & service', ionicon: 'build', color: '#5D4037', screen: 'Maintenance', tags: ['boating'] },
      { label: 'AIS Receiver', description: 'WiFi AIS setup', ionicon: 'radio', color: '#00897B', screen: 'AISSettings', tags: ['boating', 'coastal'] },
      { label: 'USACE Surveys', description: 'Channel depths & locks', ionicon: 'water-outline', color: '#1565C0', screen: 'MapTab', params: { enableOverlay: 'usace-surveys' }, tags: ['boating', 'coastal'] },
      { label: 'Seabed Type', description: 'Bottom & anchoring info', ionicon: 'layers-outline', color: '#4CAF50', screen: 'MapTab', params: { enableOverlay: 'seabed-chars' }, tags: ['boating', 'coastal'] },
      { label: 'Maritime Zones', description: 'Shipping lanes & restricted areas', ionicon: 'shield-outline', color: '#F44336', screen: 'MapTab', params: { enableOverlay: 'maritime-boundaries' }, tags: ['boating', 'coastal'] },
    ],
  },
  {
    title: 'Map Tools',
    items: [
      { label: 'Offline Maps', description: 'Download for offline', ionicon: 'cloud-offline', color: '#607D8B', screen: 'OfflineMaps', badge: 'PRO', tags: ['planning'] },
      { label: 'Buoys', description: 'Weather buoy data', ionicon: 'radio', color: '#0288D1', screen: 'WeatherBuoys', params: {}, tags: ['coastal'] },
      { label: 'Species Map', description: 'Distribution overlay', ionicon: 'earth-outline', color: '#2E7D32', screen: 'SpeciesMap', tags: ['planning'] },
    ],
  },
  {
    title: 'Seasonal',
    seasonal: true,
    activeMonths: [1, 2, 3, 11, 12],
    items: [
      { label: 'Ice Fishing', description: 'Ice conditions & tips', ionicon: 'snow', color: '#0D47A1', screen: 'IceFishing', tags: ['winter'] },
    ],
  },
];

// ── Helpers ──────────────────────────────────────────────────────

function getContextualRecommendations(): string[] {
  const hour = new Date().getHours();
  const tags: string[] = ['popular'];

  if (hour >= 4 && hour < 10) tags.push('morning', 'planning');
  else if (hour >= 10 && hour < 16) tags.push('on-water');
  else tags.push('evening', 'review');

  const dayOfWeek = new Date().getDay();
  if (dayOfWeek === 0 || dayOfWeek === 6) tags.push('weekend');

  return tags;
}

function getRecommendedTools(tags: string[]): ToolItem[] {
  const all = TOOL_CATEGORIES.flatMap((c) => c.items);
  const scored = all.map((tool) => ({
    tool,
    score: (tool.tags ?? []).filter((t) => tags.includes(t)).length,
  }));
  scored.sort((a, b) => b.score - a.score);
  return scored.filter((s) => s.score > 0).slice(0, 4).map((s) => s.tool);
}

// ── Main Screen ──────────────────────────────────────────────────

export function ToolsScreen() {
  const navigation = useNavigation<any>();
  const [recentTools, setRecentTools] = useState<string[]>([]);
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(
    new Set(['Conditions', 'Recording']),
  );

  // Load recent tools from storage
  useEffect(() => {
    AsyncStorage.getItem(RECENT_TOOLS_KEY).then((raw) => {
      if (raw) {
        try { setRecentTools(JSON.parse(raw)); } catch { /* ignore */ }
      }
    });
  }, []);

  // Track tool usage
  const trackToolUse = useCallback(async (label: string) => {
    const updated = [label, ...recentTools.filter((t) => t !== label)].slice(0, MAX_RECENT);
    setRecentTools(updated);
    await AsyncStorage.setItem(RECENT_TOOLS_KEY, JSON.stringify(updated));
  }, [recentTools]);

  const handleToolPress = useCallback((tool: ToolItem) => {
    hapticLight();
    trackToolUse(tool.label);
    navigation.navigate(tool.screen, tool.params);
  }, [navigation, trackToolUse]);

  const toggleCategory = useCallback((title: string) => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setExpandedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(title)) { next.delete(title); } else { next.add(title); }
      return next;
    });
  }, []);

  // Build contextual recommendations
  const contextTags = getContextualRecommendations();
  const recommended = getRecommendedTools(contextTags);

  // Build recent tools list
  const allTools = TOOL_CATEGORIES.flatMap((c) => c.items);
  const recentToolItems = recentTools
    .map((label) => allTools.find((t) => t.label === label))
    .filter(Boolean) as ToolItem[];

  // Filter seasonal categories
  const currentMonth = new Date().getMonth() + 1;
  const visibleCategories = TOOL_CATEGORIES.filter((c) => {
    if (c.seasonal && c.activeMonths) {
      return c.activeMonths.includes(currentMonth);
    }
    return true;
  });

  return (
    <View style={styles.screen}>
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        <View style={styles.header}>
          <Text style={styles.title}>Fishing Tools</Text>
          <Text style={styles.subtitle}>Everything you need on the water</Text>
        </View>

        {/* Recently Used — personalized quick access */}
        {recentToolItems.length > 0 && (
          <View style={styles.section}>
            <Text style={styles.sectionLabel}>Recently Used</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.recentRow}>
              {recentToolItems.map((tool) => (
                <Pressable
                  key={tool.label}
                  style={({ pressed }) => [styles.recentChip, pressed && styles.chipPressed]}
                  onPress={() => handleToolPress(tool)}
                >
                  <View style={[styles.recentIconWrap, { backgroundColor: tool.color + '14' }]}>
                    <Ionicons name={tool.ionicon as any} size={18} color={tool.color} />
                  </View>
                  <Text style={styles.recentLabel} numberOfLines={1}>{tool.label}</Text>
                </Pressable>
              ))}
            </ScrollView>
          </View>
        )}

        {/* Recommended For You — contextual */}
        {recommended.length > 0 && recentToolItems.length === 0 && (
          <View style={styles.section}>
            <Text style={styles.sectionLabel}>Recommended Now</Text>
            <View style={styles.recommendedRow}>
              {recommended.map((tool) => (
                <Pressable
                  key={tool.label}
                  style={({ pressed }) => [styles.recommendedCard, pressed && styles.toolCardPressed]}
                  onPress={() => handleToolPress(tool)}
                >
                  <View style={[styles.toolIconWrap, { backgroundColor: tool.color + '14' }]}>
                    <Ionicons name={tool.ionicon as any} size={22} color={tool.color} />
                  </View>
                  <Text style={styles.toolLabel}>{tool.label}</Text>
                  <Text style={styles.toolDesc} numberOfLines={1}>{tool.description}</Text>
                </Pressable>
              ))}
            </View>
          </View>
        )}

        {/* Collapsible categories with progressive disclosure */}
        {visibleCategories.map((category) => {
          const isExpanded = expandedCategories.has(category.title);
          const previewItems = category.items.slice(0, 3);
          const hasMore = category.items.length > 3;

          return (
            <View key={category.title} style={styles.categorySection}>
              <Pressable
                style={styles.categoryHeader}
                onPress={() => toggleCategory(category.title)}
              >
                <Text style={styles.categoryTitle}>{category.title}</Text>
                <View style={styles.categoryRight}>
                  <Text style={styles.categoryCount}>{category.items.length}</Text>
                  <Ionicons
                    name={isExpanded ? 'chevron-up' : 'chevron-down'}
                    size={16}
                    color={palette.textMuted}
                  />
                </View>
              </Pressable>

              <View style={styles.toolsGrid}>
                {(isExpanded ? category.items : previewItems).map((tool) => (
                  <Pressable
                    key={tool.label}
                    style={({ pressed }) => [styles.toolCard, pressed && styles.toolCardPressed]}
                    onPress={() => handleToolPress(tool)}
                  >
                    {tool.badge && (
                      <View style={styles.proBadge}>
                        <Text style={styles.proBadgeText}>{tool.badge}</Text>
                      </View>
                    )}
                    <View style={[styles.toolIconWrap, { backgroundColor: tool.color + '14' }]}>
                      <Ionicons name={tool.ionicon as any} size={22} color={tool.color} />
                    </View>
                    <Text style={styles.toolLabel}>{tool.label}</Text>
                    <Text style={styles.toolDesc} numberOfLines={1}>{tool.description}</Text>
                  </Pressable>
                ))}
              </View>

              {/* "See All" hint when collapsed and has more items */}
              {!isExpanded && hasMore && (
                <Pressable
                  style={styles.seeAllButton}
                  onPress={() => toggleCategory(category.title)}
                >
                  <Text style={styles.seeAllText}>
                    +{category.items.length - 3} more
                  </Text>
                  <Ionicons name="chevron-down" size={14} color={palette.accent} />
                </Pressable>
              )}
            </View>
          );
        })}

        <View style={{ height: 40 }} />
      </ScrollView>
    </View>
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
    gap: 20,
  },
  header: {
    gap: 4,
  },
  title: {
    ...typeStyles.screenTitle,
    color: palette.text,
  },
  subtitle: {
    color: palette.textMuted,
    fontSize: 14,
  },

  // ── Section headers ─────────────────────────────────────────────
  section: {
    gap: 10,
  },
  sectionLabel: {
    fontSize: 11,
    fontWeight: '700',
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    paddingLeft: 4,
  },

  // ── Recently Used row ───────────────────────────────────────────
  recentRow: {
    gap: 10,
    paddingRight: 20,
  },
  recentChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: '#FFFFFF',
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 12,
    shadowColor: '#000',
    shadowOpacity: 0.05,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  chipPressed: {
    transform: [{ scale: 0.96 }],
    opacity: 0.8,
  },
  recentIconWrap: {
    width: 32,
    height: 32,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  recentLabel: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
  },

  // ── Recommended row ─────────────────────────────────────────────
  recommendedRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  recommendedCard: {
    width: '47%',
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 14,
    alignItems: 'center',
    gap: 6,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
    borderWidth: 1,
    borderColor: palette.accent + '20',
  },

  // ── Category sections ───────────────────────────────────────────
  categorySection: {
    gap: 10,
  },
  categoryHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 4,
    paddingVertical: 4,
  },
  categoryTitle: {
    fontFamily: 'PlayfairDisplay-Regular',
    color: palette.textSecondary,
    fontSize: 15,
    fontWeight: '400',
    letterSpacing: -0.1,
  },
  categoryRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  categoryCount: {
    fontSize: 12,
    color: palette.textMuted,
    fontWeight: '500',
  },

  // ── Tool cards ──────────────────────────────────────────────────
  toolsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  toolCard: {
    width: '31%',
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 14,
    alignItems: 'center',
    gap: 6,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  toolIconWrap: {
    width: 40,
    height: 40,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 2,
  },
  toolLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.text,
    textAlign: 'center',
  },
  toolDesc: {
    fontSize: 10,
    color: palette.textMuted,
    textAlign: 'center',
  },
  toolCardPressed: {
    transform: [{ scale: 0.96 }],
    opacity: 0.9,
  },

  // ── "See All" button ────────────────────────────────────────────
  seeAllButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    paddingVertical: 6,
  },
  seeAllText: {
    fontSize: 13,
    color: palette.accent,
    fontWeight: '600',
  },

  // ── Badge ───────────────────────────────────────────────────────
  proBadge: {
    position: 'absolute',
    top: 6,
    right: 6,
    backgroundColor: '#F59E0B',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 6,
    zIndex: 1,
  },
  proBadgeText: {
    fontSize: 9,
    fontWeight: '800',
    color: '#fff',
    letterSpacing: 0.5,
  },
});
