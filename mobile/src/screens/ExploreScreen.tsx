import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Animated,
  Dimensions,
  FlatList,
  PanResponder,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  ActivityIndicator,
} from 'react-native';
import {
  MapView as MLMapView,
  Camera,
  PointAnnotation,
  UserLocation,
  type CameraRef,
} from '@maplibre/maplibre-react-native';
import * as Location from 'expo-location';
import { palette, getConditionBand, conditionConfig } from '../theme/palette';
import { api } from '../services/api';
import type { FishingLocation, BestFishingV2Entry } from '../types/models';
import type { TabProps } from '../types/navigation';

// ── Constants ──────────────────────────────────────────────────────

const { width: SCREEN_WIDTH, height: SCREEN_HEIGHT } = Dimensions.get('window');

const MAP_HEIGHT = SCREEN_HEIGHT * 0.55;
const SHEET_COLLAPSED = SCREEN_HEIGHT * 0.48;
const SHEET_EXPANDED = SCREEN_HEIGHT * 0.85;
const SHEET_PEEK = SCREEN_HEIGHT * 0.35;
const SNAP_POINTS = [SHEET_PEEK, SHEET_COLLAPSED, SHEET_EXPANDED];
const HANDLE_HEIGHT = 28;

const DEFAULT_CENTER: [number, number] = [-92.0, 35.5]; // [lng, lat]
const DEFAULT_ZOOM = 4.5;

const LIGHT_STYLE_URL = 'https://tiles.openfreemap.org/styles/liberty';

type TabKey = 'for-you' | 'popular' | 'random';
type TimePeriod = 'today' | 'this-week' | 'this-month' | 'all-time';

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: 'for-you', label: 'For You', icon: '✨' },
  { key: 'popular', label: 'Popular', icon: '🏆' },
  { key: 'random', label: 'Random', icon: '🎲' },
];

const TIME_PERIODS: { key: TimePeriod; label: string }[] = [
  { key: 'today', label: 'Today' },
  { key: 'this-week', label: 'This Week' },
  { key: 'this-month', label: 'This Month' },
  { key: 'all-time', label: 'All Time' },
];

// ── Helpers ────────────────────────────────────────────────────────

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return 'Night owl session';
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  return 'Good evening';
}

function haversineDistance(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const R = 3958.8; // Earth radius in miles
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function shuffleArray<T>(arr: T[]): T[] {
  const shuffled = [...arr];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  return shuffled;
}

// Infer top species from a FishingLocation using speciesActivity if available
function getTopSpecies(loc: FishingLocation): string[] {
  if (loc.speciesActivity && loc.speciesActivity.length > 0) {
    return loc.speciesActivity.slice(0, 3).map((s) => s.species);
  }
  // Fallback: derive from location name patterns
  return ['Largemouth', 'Smallmouth'];
}

function getTrendIndicator(score: number): { symbol: string; color: string } {
  if (score >= 70) return { symbol: '\u2191', color: palette.success };
  if (score >= 50) return { symbol: '\u2192', color: palette.warning };
  return { symbol: '\u2193', color: palette.error };
}

function closestSnap(value: number): number {
  return SNAP_POINTS.reduce((prev, curr) =>
    Math.abs(curr - value) < Math.abs(prev - value) ? curr : prev,
  );
}

// ── Pin marker component ────────────────────────────────────────────

function ConditionPin({ score }: { score: number }) {
  const band = getConditionBand(score);
  const config = conditionConfig[band];
  return (
    <View style={styles.pinContainer}>
      <View style={[styles.pin, { backgroundColor: config.color }]}>
        <Text style={styles.pinIcon}>{config.icon}</Text>
      </View>
      <View style={[styles.pinArrow, { borderTopColor: config.color }]} />
    </View>
  );
}

// ── Site Card component ─────────────────────────────────────────────

interface SiteCardProps {
  location: FishingLocation;
  distanceMi: number | null;
  onPress: () => void;
}

function SiteCard({ location, distanceMi, onPress }: SiteCardProps) {
  const band = getConditionBand(location.score);
  const config = conditionConfig[band];
  const trend = getTrendIndicator(location.score);
  const species = getTopSpecies(location);

  return (
    <Pressable
      style={({ pressed }) => [styles.card, pressed && styles.cardPressed]}
      onPress={onPress}
    >
      {/* Top row: name + condition badge */}
      <View style={styles.cardHeader}>
        <View style={styles.cardTitleArea}>
          <Text style={styles.cardName} numberOfLines={1}>
            {location.name}
          </Text>
          <Text style={styles.cardSubtitle} numberOfLines={1}>
            {location.subtitle}
          </Text>
        </View>
        <View style={[styles.conditionBadge, { backgroundColor: config.bgTint }]}>
          <Text style={styles.conditionIcon}>{config.icon}</Text>
          <Text style={[styles.conditionLabel, { color: config.color }]}>
            {config.label}
          </Text>
        </View>
      </View>

      {/* Bottom row: species tags + distance + trend */}
      <View style={styles.cardFooter}>
        <View style={styles.speciesRow}>
          {species.map((sp) => (
            <View key={sp} style={styles.speciesTag}>
              <Text style={styles.speciesTagText}>{sp}</Text>
            </View>
          ))}
        </View>
        <View style={styles.cardMeta}>
          {distanceMi !== null && (
            <Text style={styles.distanceText}>
              {distanceMi < 1 ? '<1' : Math.round(distanceMi)} mi
            </Text>
          )}
          <Text style={[styles.trendIndicator, { color: trend.color }]}>
            {trend.symbol}
          </Text>
        </View>
      </View>
    </Pressable>
  );
}

// ── Main ExploreScreen ──────────────────────────────────────────────

// @deprecated - Explore functionality has been merged into MapScreen
export function ExploreScreen({ navigation }: TabProps<'MapTab'>) {
  const cameraRef = useRef<CameraRef>(null);

  // Data
  const [locations, setLocations] = useState<FishingLocation[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [bestFishing, setBestFishing] = useState<BestFishingV2Entry[]>([]);
  const [userLocation, setUserLocation] = useState<{
    lat: number;
    lon: number;
  } | null>(null);

  // UI state
  const [activeTab, setActiveTab] = useState<TabKey>('for-you');
  const [timePeriod, setTimePeriod] = useState<TimePeriod>('today');
  const [randomSeed, setRandomSeed] = useState(0);

  // Bottom sheet animation
  const sheetHeight = useRef(new Animated.Value(SHEET_COLLAPSED)).current;
  const currentHeight = useRef(SHEET_COLLAPSED);

  // Track animated value for clamping
  useEffect(() => {
    const id = sheetHeight.addListener(({ value }) => {
      currentHeight.current = value;
    });
    return () => sheetHeight.removeListener(id);
  }, [sheetHeight]);

  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: (_, gestureState) =>
        Math.abs(gestureState.dy) > 5,
      onPanResponderMove: (_, gestureState) => {
        const newHeight = currentHeight.current - gestureState.dy;
        const clamped = Math.max(SHEET_PEEK, Math.min(SHEET_EXPANDED, newHeight));
        sheetHeight.setValue(clamped);
      },
      onPanResponderRelease: (_, gestureState) => {
        const projected = currentHeight.current - gestureState.dy;
        const target = closestSnap(projected);
        Animated.spring(sheetHeight, {
          toValue: target,
          useNativeDriver: false,
          friction: 8,
          tension: 65,
        }).start();
        currentHeight.current = target;
      },
    }),
  ).current;

  // ── Data loading ────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;

    async function loadData() {
      try {
        // Fetch locations and best-fishing v2 data in parallel
        const today = new Date().toISOString().slice(0, 10);
        const [locs, bestRes] = await Promise.all([
          api.getLocations(),
          api.bestFishingV2(today, 10).catch(() => null),
        ]);
        if (!cancelled) {
          setLocations(locs);
          if (bestRes) {
            setBestFishing(bestRes.top_locations);
          }
          setLoading(false);
        }
      } catch (err: any) {
        if (!cancelled) {
          setLoadError(err?.message ?? 'Failed to load locations');
          setLoading(false);
        }
      }
    }

    loadData();

    (async () => {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status === 'granted') {
        const loc = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.Balanced,
        });
        if (!cancelled) {
          setUserLocation({
            lat: loc.coords.latitude,
            lon: loc.coords.longitude,
          });
        }
      }
    })();

    return () => { cancelled = true; };
  }, []);

  // ── Computed lists ──────────────────────────────────────────────

  const locationsWithDistance = useMemo(() => {
    return locations.map((loc) => ({
      location: loc,
      distanceMi: userLocation
        ? haversineDistance(userLocation.lat, userLocation.lon, loc.lat, loc.lon)
        : null,
    }));
  }, [locations, userLocation]);

  const displayList = useMemo(() => {
    switch (activeTab) {
      case 'for-you': {
        // Proximity-weighted: closer + higher score = better ranking
        return [...locationsWithDistance].sort((a, b) => {
          const scoreA =
            a.location.score * 0.6 +
            (a.distanceMi !== null ? Math.max(0, 100 - a.distanceMi * 0.5) : 50) * 0.4;
          const scoreB =
            b.location.score * 0.6 +
            (b.distanceMi !== null ? Math.max(0, 100 - b.distanceMi * 0.5) : 50) * 0.4;
          return scoreB - scoreA;
        });
      }
      case 'popular': {
        // Sort by score; timePeriod is a UI concept — in mock mode all data is "now"
        return [...locationsWithDistance].sort(
          (a, b) => b.location.score - a.location.score,
        );
      }
      case 'random': {
        // Shuffle on demand (re-shuffle when randomSeed changes)
        void randomSeed; // used for dependency only
        return shuffleArray(locationsWithDistance);
      }
      default:
        return locationsWithDistance;
    }
  }, [activeTab, locationsWithDistance, timePeriod, randomSeed]);

  // ── Handlers ────────────────────────────────────────────────────

  const handleCardPress = useCallback(
    (loc: FishingLocation) => {
      // Animate map to the location
      cameraRef.current?.setCamera({
        centerCoordinate: [loc.lon, loc.lat],
        zoomLevel: 9,
        animationDuration: 600,
      });
      // Navigate after a brief delay so the user sees the map move
      setTimeout(() => {
        navigation.navigate('LocationDetail', { locationId: loc.id });
      }, 400);
    },
    [navigation],
  );

  const handleMarkerPress = useCallback(
    (loc: FishingLocation) => {
      navigation.navigate('LocationDetail', { locationId: loc.id });
    },
    [navigation],
  );

  const handleRandomReshuffle = useCallback(() => {
    setRandomSeed((s) => s + 1);
  }, []);

  const handleTabChange = useCallback((tab: TabKey) => {
    setActiveTab(tab);
    // Expand the sheet to collapsed position when switching tabs
    Animated.spring(sheetHeight, {
      toValue: SHEET_COLLAPSED,
      useNativeDriver: false,
      friction: 8,
      tension: 65,
    }).start();
    currentHeight.current = SHEET_COLLAPSED;
  }, [sheetHeight]);

  // ── Render ──────────────────────────────────────────────────────

  const renderSiteCard = useCallback(
    ({
      item,
    }: {
      item: { location: FishingLocation; distanceMi: number | null };
    }) => (
      <SiteCard
        location={item.location}
        distanceMi={item.distanceMi}
        onPress={() => handleCardPress(item.location)}
      />
    ),
    [handleCardPress],
  );

  const keyExtractor = useCallback(
    (item: { location: FishingLocation; distanceMi: number | null }) =>
      item.location.id,
    [],
  );

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator color={palette.accent} size="large" />
      </View>
    );
  }

  if (loadError && locations.length === 0) {
    return (
      <View style={styles.loadingContainer}>
        <Text style={{ color: palette.error, fontSize: 15, textAlign: 'center', paddingHorizontal: 32 }}>
          {loadError}
        </Text>
        <Pressable
          style={{
            marginTop: 16,
            backgroundColor: palette.accent,
            borderRadius: 8,
            paddingHorizontal: 24,
            paddingVertical: 12,
          }}
          onPress={() => {
            setLoading(true);
            setLoadError(null);
            api.getLocations().then((locs) => {
              setLocations(locs);
              setLoading(false);
            }).catch((err) => {
              setLoadError(err?.message ?? 'Failed to load');
              setLoading(false);
            });
          }}
        >
          <Text style={{ color: '#fff', fontWeight: '600', fontSize: 15 }}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* ── Full-screen map ──────────────────────────────────────── */}
      <MLMapView
        style={styles.map}
        mapStyle={LIGHT_STYLE_URL}
        logoEnabled={false}
        attributionEnabled={false}
      >
        <Camera
          ref={cameraRef}
          defaultSettings={{
            centerCoordinate: DEFAULT_CENTER,
            zoomLevel: DEFAULT_ZOOM,
          }}
        />
        <UserLocation visible={!!userLocation} />
        {locations.map((loc) => (
          <PointAnnotation
            key={loc.id}
            id={`explore-loc-${loc.id}`}
            coordinate={[loc.lon, loc.lat]}
            onSelected={() => handleMarkerPress(loc)}
          >
            <ConditionPin score={loc.score} />
          </PointAnnotation>
        ))}
      </MLMapView>

      {/* ── Swipeable bottom sheet ──────────────────────────────── */}
      <Animated.View style={[styles.sheet, { height: sheetHeight }]}>
        {/* Drag handle */}
        <View style={styles.handleArea} {...panResponder.panHandlers}>
          <View style={styles.dragHandle} />
        </View>

        {/* Tab bar */}
        <View style={styles.tabBar}>
          {TABS.map((tab) => (
            <Pressable
              key={tab.key}
              style={[
                styles.tab,
                activeTab === tab.key && styles.tabActive,
              ]}
              onPress={() => handleTabChange(tab.key)}
            >
              <Text style={styles.tabIcon}>{tab.icon}</Text>
              <Text
                style={[
                  styles.tabLabel,
                  activeTab === tab.key && styles.tabLabelActive,
                ]}
              >
                {tab.label}
              </Text>
            </Pressable>
          ))}
        </View>

        {/* Section header */}
        <View style={styles.sectionHeader}>
          {activeTab === 'for-you' && (
            <>
              <Text style={styles.sectionTitle}>{getGreeting()}</Text>
              <Text style={styles.sectionSubtitle}>
                Top picks based on your location and current conditions
              </Text>
              {bestFishing.length > 0 && (
                <ScrollView
                  horizontal
                  showsHorizontalScrollIndicator={false}
                  contentContainerStyle={styles.topPicksRow}
                >
                  {bestFishing.slice(0, 5).map((entry, i) => (
                    <View key={entry.location} style={styles.topPickChip}>
                      <Text style={styles.topPickRank}>#{i + 1}</Text>
                      <Text style={styles.topPickName} numberOfLines={1}>{entry.location}</Text>
                      <Text style={styles.topPickScore}>{entry.fishing_score}</Text>
                    </View>
                  ))}
                </ScrollView>
              )}
            </>
          )}
          {activeTab === 'popular' && (
            <>
              <Text style={styles.sectionTitle}>Trending Spots</Text>
              {/* Time period chips */}
              <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={styles.timePeriodRow}
              >
                {TIME_PERIODS.map((tp) => (
                  <Pressable
                    key={tp.key}
                    style={[
                      styles.timeChip,
                      timePeriod === tp.key && styles.timeChipActive,
                    ]}
                    onPress={() => setTimePeriod(tp.key)}
                  >
                    <Text
                      style={[
                        styles.timeChipText,
                        timePeriod === tp.key && styles.timeChipTextActive,
                      ]}
                    >
                      {tp.label}
                    </Text>
                  </Pressable>
                ))}
              </ScrollView>
            </>
          )}
          {activeTab === 'random' && (
            <View style={styles.randomHeader}>
              <Text style={styles.sectionTitle}>Discover</Text>
              <Pressable style={styles.reshuffleButton} onPress={handleRandomReshuffle}>
                <Text style={styles.reshuffleIcon}>🎲</Text>
                <Text style={styles.reshuffleText}>Shuffle</Text>
              </Pressable>
            </View>
          )}
        </View>

        {/* Site card list */}
        <FlatList
          data={displayList}
          renderItem={renderSiteCard}
          keyExtractor={keyExtractor}
          contentContainerStyle={styles.listContent}
          showsVerticalScrollIndicator={false}
          ItemSeparatorComponent={() => <View style={{ height: 10 }} />}
          ListFooterComponent={<View style={{ height: 100 }} />}
        />
      </Animated.View>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FAFAF7',
  },
  loadingContainer: {
    flex: 1,
    backgroundColor: '#FAFAF7',
    alignItems: 'center',
    justifyContent: 'center',
  },

  // ── Map ──────────────────────────────────────────────────────────
  map: {
    width: SCREEN_WIDTH,
    height: MAP_HEIGHT,
  },
  pinContainer: {
    alignItems: 'center',
  },
  pin: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2.5,
    borderColor: '#FFFFFF',
    shadowColor: '#000',
    shadowOpacity: 0.2,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
    elevation: 5,
  },
  pinIcon: {
    fontSize: 14,
  },
  pinArrow: {
    width: 0,
    height: 0,
    borderLeftWidth: 6,
    borderRightWidth: 6,
    borderTopWidth: 8,
    borderLeftColor: 'transparent',
    borderRightColor: 'transparent',
    marginTop: -2,
  },

  // ── Bottom sheet ────────────────────────────────────────────────
  sheet: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: '#FAFAF7',
    borderTopLeftRadius: 12,
    borderTopRightRadius: 12,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: -2 },
    elevation: 4,
  },
  handleArea: {
    height: HANDLE_HEIGHT,
    alignItems: 'center',
    justifyContent: 'center',
  },
  dragHandle: {
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: '#D0D0CB',
  },

  // ── Tab bar ─────────────────────────────────────────────────────
  tabBar: {
    flexDirection: 'row',
    paddingHorizontal: 20,
    gap: 8,
    marginBottom: 4,
  },
  tab: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 6,
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: '#E5E5E0',
  },
  tabActive: {
    backgroundColor: '#0A6EBD',
    borderColor: '#0A6EBD',
  },
  tabIcon: {
    fontSize: 14,
  },
  tabLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: '#4A4A45',
  },
  tabLabelActive: {
    color: '#FFFFFF',
  },

  // ── Section header ──────────────────────────────────────────────
  sectionHeader: {
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 8,
  },
  sectionTitle: {
    fontSize: 18,
    color: '#1A1A18',
    fontWeight: '600',
  },
  sectionSubtitle: {
    fontSize: 13,
    color: '#8A8A85',
    marginTop: 4,
    lineHeight: 18,
  },

  // ── Time period chips (Popular tab) ─────────────────────────────
  timePeriodRow: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 10,
  },
  timeChip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 6,
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: '#E5E5E0',
  },
  timeChipActive: {
    backgroundColor: '#0A6EBD',
    borderColor: '#0A6EBD',
  },
  timeChipText: {
    fontSize: 12,
    fontWeight: '600',
    color: '#4A4A45',
  },
  timeChipTextActive: {
    color: '#FFFFFF',
  },

  // ── Random tab header ──────────────────────────────────────────
  randomHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  reshuffleButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 6,
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: '#E5E5E0',
  },
  reshuffleIcon: {
    fontSize: 14,
  },
  reshuffleText: {
    fontSize: 13,
    fontWeight: '600',
    color: '#0A6EBD',
  },

  // ── Card list ─────────────────────────────────────────────────
  listContent: {
    paddingHorizontal: 20,
    paddingTop: 4,
  },

  // ── Site card ─────────────────────────────────────────────────
  card: {
    backgroundColor: '#FFFFFF',
    borderRadius: 8,
    padding: 16,
    borderWidth: 1,
    borderColor: '#E5E5E0',
  },
  cardPressed: {
    opacity: 0.85,
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 12,
  },
  cardTitleArea: {
    flex: 1,
    gap: 2,
  },
  cardName: {
    fontSize: 16,
    fontWeight: '600',
    color: '#1A1A18',
  },
  cardSubtitle: {
    fontSize: 13,
    color: '#8A8A85',
  },
  conditionBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
  },
  conditionIcon: {
    fontSize: 13,
  },
  conditionLabel: {
    fontSize: 12,
    fontWeight: '700',
  },
  cardFooter: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 10,
  },
  speciesRow: {
    flexDirection: 'row',
    gap: 6,
    flexWrap: 'wrap',
    flex: 1,
  },
  speciesTag: {
    backgroundColor: '#F5F5F0',
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 8,
  },
  speciesTagText: {
    fontSize: 11,
    fontWeight: '600',
    color: '#4A4A45',
  },
  cardMeta: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  distanceText: {
    fontSize: 12,
    fontWeight: '600',
    color: '#8A8A85',
  },
  trendIndicator: {
    fontSize: 16,
    fontWeight: '700',
  },

  // ── Top picks row (best-fishing v2) ──────────────────────────
  topPicksRow: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 10,
    paddingRight: 4,
  },
  topPickChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: '#FFFFFF',
    borderRadius: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: '#E5E5E0',
  },
  topPickRank: {
    fontSize: 11,
    fontWeight: '700',
    color: '#0A6EBD',
  },
  topPickName: {
    fontSize: 12,
    fontWeight: '600',
    color: '#1A1A18',
    maxWidth: 120,
  },
  topPickScore: {
    fontSize: 13,
    fontWeight: '700',
    color: '#0A6EBD',
  },
});
