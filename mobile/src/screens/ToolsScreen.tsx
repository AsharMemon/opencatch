import React from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  Pressable,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { hapticLight } from '../utils/haptics';

// ── Tool definitions by category ─────────────────────────────────

interface ToolItem {
  label: string;
  description: string;
  ionicon: string;
  color: string;
  screen: string;
  params?: Record<string, any>;
  badge?: string;
}

interface ToolCategory {
  title: string;
  items: ToolItem[];
}

const TOOL_CATEGORIES: ToolCategory[] = [
  {
    title: 'Conditions',
    items: [
      { label: 'Forecasts', description: 'AI fishing forecasts', ionicon: 'analytics', color: palette.accent, screen: 'ForecastsTab' },
      { label: 'Tides', description: 'Tides & currents', ionicon: 'water', color: '#1E88E5', screen: 'TideChart', params: { stationId: '', stationName: 'Find Station' } },
      { label: 'Water Data', description: 'Temp, clarity, flow', ionicon: 'thermometer', color: '#1565C0', screen: 'WaterInsights', params: {} },
      { label: 'Best Times', description: 'Peak fishing windows', ionicon: 'time', color: '#E53935', screen: 'BestTimes' },
      { label: 'Pressure', description: 'Angler traffic trends', ionicon: 'people', color: '#FB8C00', screen: 'FishingPressure' },
      { label: 'Sun & Moon', description: 'Solunar periods', ionicon: 'moon', color: '#5C6BC0', screen: 'SunMoon' },
    ],
  },
  {
    title: 'Planning',
    items: [
      { label: 'Regulations', description: 'Limits & seasons', ionicon: 'document-text', color: '#8B4513', screen: 'Regulations', params: {} },
      { label: 'Species Guide', description: 'ID & techniques', ionicon: 'fish', color: '#4682B4', screen: 'SpeciesGuide' },
      { label: 'Bait Guide', description: 'Match the hatch', ionicon: 'bug', color: '#DAA520', screen: 'BaitGuide', params: {} },
      { label: 'Lake Finder', description: 'Find fishing spots', ionicon: 'search', color: '#0288D1', screen: 'LakeFinder' },
      { label: 'Knots & Rigs', description: 'Knot & rig guide', ionicon: 'link', color: '#6D4C41', screen: 'KnotGuide' },
      { label: 'Safety', description: 'Float plan & alerts', ionicon: 'shield-checkmark', color: '#2E7D32', screen: 'Safety' },
      { label: 'Alerts', description: 'Weather warnings', ionicon: 'warning', color: '#E53935', screen: 'Alerts' },
      { label: 'Trip Planner', description: 'Plan fishing trips', ionicon: 'calendar', color: '#7C3AED', screen: 'TripPlanner' },
    ],
  },
  {
    title: 'Recording',
    items: [
      { label: 'Track Trip', description: 'GPS track recording', ionicon: 'navigate', color: '#E65100', screen: 'TrackRecording' },
      { label: 'Log Catch', description: 'Report a catch', ionicon: 'add-circle', color: palette.accent, screen: 'CatchReport', params: {} },
      { label: 'My Stats', description: 'Catch analytics', ionicon: 'stats-chart', color: palette.accent, screen: 'Stats' },
      { label: 'Track History', description: 'Past trips', ionicon: 'trail-sign', color: '#6D4C41', screen: 'TrackHistory' },
    ],
  },
  {
    title: 'Boating',
    items: [
      { label: 'Fuel Calc', description: 'Trip fuel & range', ionicon: 'water', color: '#E65100', screen: 'FuelCalculator' },
      { label: 'Maintenance', description: 'Engine hours & service', ionicon: 'build', color: '#5D4037', screen: 'Maintenance' },
    ],
  },
  {
    title: 'Map Tools',
    items: [
      { label: 'Offline Maps', description: 'Download for offline', ionicon: 'cloud-offline', color: '#607D8B', screen: 'OfflineMaps', badge: 'PRO' },
      { label: 'Buoys', description: 'Weather buoy data', ionicon: 'radio', color: '#0288D1', screen: 'WeatherBuoys', params: {} },
      { label: 'Species Map', description: 'Distribution overlay', ionicon: 'analytics', color: '#2E7D32', screen: 'SpeciesMap' },
    ],
  },
  {
    title: 'Seasonal',
    items: [
      { label: 'Ice Fishing', description: 'Ice conditions & tips', ionicon: 'snow', color: '#0D47A1', screen: 'IceFishing' },
    ],
  },
];

// ── Main Screen ──────────────────────────────────────────────────

export function ToolsScreen() {
  const navigation = useNavigation<any>();

  return (
    <View style={styles.screen}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <Text style={styles.title}>Fishing Tools</Text>
          <Text style={styles.subtitle}>Everything you need on the water</Text>
        </View>

        {TOOL_CATEGORIES.map((category) => (
          <View key={category.title} style={styles.categorySection}>
            <Text style={styles.categoryTitle}>{category.title}</Text>
            <View style={styles.toolsGrid}>
              {category.items.map((tool) => (
                <Pressable
                  key={tool.label}
                  style={({ pressed }) => [styles.toolCard, pressed && styles.toolCardPressed]}
                  onPress={() => { hapticLight(); navigation.navigate(tool.screen, tool.params); }}
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
          </View>
        ))}

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
    gap: 24,
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
  categorySection: {
    gap: 10,
  },
  categoryTitle: {
    fontFamily: 'PlayfairDisplay-Regular',
    color: palette.textSecondary,
    fontSize: 15,
    fontWeight: '400',
    paddingLeft: 4,
    letterSpacing: -0.1,
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
