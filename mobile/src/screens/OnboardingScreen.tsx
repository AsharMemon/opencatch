import React, { useRef, useState } from 'react';
import {
  Animated,
  Dimensions,
  FlatList,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
  ViewToken,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { palette } from '../theme/palette';
import { fonts, type as typeStyles } from '../theme/typography';

const { width: SCREEN_WIDTH, height: SCREEN_HEIGHT } = Dimensions.get('window');

export const ONBOARDING_COMPLETE_KEY = '@opencatch_onboarding_complete';

interface OnboardingProps {
  onComplete: () => void;
}

interface OnboardingPage {
  key: string;
  title: string;
  subtitle: string;
  icon: keyof typeof Ionicons.glyphMap;
  iconColor: string;
  features: { icon: keyof typeof Ionicons.glyphMap; text: string }[];
}

const PAGES: OnboardingPage[] = [
  {
    key: 'discover',
    title: 'Discover Fishing Spots',
    subtitle: 'Find the best lakes, rivers, and streams near you with live condition data.',
    icon: 'map',
    iconColor: palette.accent,
    features: [
      { icon: 'location', text: 'Thousands of spots with real-time conditions' },
      { icon: 'navigate', text: 'Boat launches, parking, and trail access' },
      { icon: 'star', text: 'AI-ranked by current fishing quality' },
    ],
  },
  {
    key: 'conditions',
    title: 'Real-Time Conditions',
    subtitle: 'Weather, water data, and bite forecasts — everything you need before heading out.',
    icon: 'analytics',
    iconColor: '#FB8C00',
    features: [
      { icon: 'thermometer', text: 'Water temp, flow, and clarity from USGS' },
      { icon: 'partly-sunny', text: 'Hourly weather and wind forecasts' },
      { icon: 'flame', text: 'Multi-factor bite prediction engine' },
    ],
  },
  {
    key: 'track',
    title: 'Track Your Adventures',
    subtitle: 'Record trips, log catches, and build your personal fishing history.',
    icon: 'compass',
    iconColor: '#E65100',
    features: [
      { icon: 'camera', text: 'Photo catch logging with weather auto-fill' },
      { icon: 'footsteps', text: 'GPS trip tracking with speed coloring' },
      { icon: 'stats-chart', text: 'Personal stats and catch analytics' },
    ],
  },
  {
    key: 'getstarted',
    title: 'Get Started',
    subtitle: 'Enable location access to find spots near you and get personalized forecasts.',
    icon: 'fish',
    iconColor: palette.accent,
    features: [
      { icon: 'location', text: 'Location helps find nearby fishing spots' },
      { icon: 'notifications', text: 'Get alerted when conditions are prime' },
      { icon: 'shield-checkmark', text: 'Your data stays on your device' },
    ],
  },
];

export function OnboardingScreen({ onComplete }: OnboardingProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const flatListRef = useRef<FlatList>(null);
  const scrollX = useRef(new Animated.Value(0)).current;

  const isLastPage = currentIndex === PAGES.length - 1;

  const handleNext = () => {
    if (isLastPage) {
      handleGetStarted();
    } else {
      flatListRef.current?.scrollToIndex({ index: currentIndex + 1, animated: true });
    }
  };

  const handleSkip = async () => {
    await AsyncStorage.setItem(ONBOARDING_COMPLETE_KEY, 'true');
    onComplete();
  };

  const handleGetStarted = async () => {
    // Request location permission
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        // User denied — still proceed
      }
    } catch {
      // Permission request failed — continue anyway
    }

    await AsyncStorage.setItem(ONBOARDING_COMPLETE_KEY, 'true');
    onComplete();
  };

  const onViewableItemsChanged = useRef(
    ({ viewableItems }: { viewableItems: ViewToken[] }) => {
      if (viewableItems.length > 0 && viewableItems[0].index != null) {
        setCurrentIndex(viewableItems[0].index);
      }
    },
  ).current;

  const viewabilityConfig = useRef({ viewAreaCoveragePercentThreshold: 50 }).current;

  const renderPage = ({ item, index }: { item: OnboardingPage; index: number }) => (
    <View style={styles.page}>
      {/* Hero icon area */}
      <View style={styles.heroArea}>
        <View style={[styles.iconCircle, { backgroundColor: item.iconColor + '15' }]}>
          <View style={[styles.iconInner, { backgroundColor: item.iconColor + '25' }]}>
            <Ionicons name={item.icon} size={56} color={item.iconColor} />
          </View>
        </View>
      </View>

      {/* Content */}
      <View style={styles.contentArea}>
        <Text style={styles.pageTitle}>{item.title}</Text>
        <Text style={styles.pageSubtitle}>{item.subtitle}</Text>

        <View style={styles.featuresContainer}>
          {item.features.map((feature, i) => (
            <View key={i} style={styles.featureRow}>
              <View style={[styles.featureIconBg, { backgroundColor: item.iconColor + '12' }]}>
                <Ionicons name={feature.icon} size={18} color={item.iconColor} />
              </View>
              <Text style={styles.featureText}>{feature.text}</Text>
            </View>
          ))}
        </View>
      </View>
    </View>
  );

  return (
    <View style={styles.container}>
      {/* Skip button */}
      {!isLastPage && (
        <Pressable style={styles.skipButton} onPress={handleSkip}>
          <Text style={styles.skipText}>Skip</Text>
        </Pressable>
      )}

      {/* Pages */}
      <Animated.FlatList
        ref={flatListRef}
        data={PAGES}
        renderItem={renderPage}
        keyExtractor={(item) => item.key}
        horizontal
        pagingEnabled
        showsHorizontalScrollIndicator={false}
        bounces={false}
        onScroll={Animated.event(
          [{ nativeEvent: { contentOffset: { x: scrollX } } }],
          { useNativeDriver: false },
        )}
        onViewableItemsChanged={onViewableItemsChanged}
        viewabilityConfig={viewabilityConfig}
      />

      {/* Bottom controls */}
      <View style={styles.bottomArea}>
        {/* Dot indicators */}
        <View style={styles.dotsRow}>
          {PAGES.map((_, i) => {
            const inputRange = [
              (i - 1) * SCREEN_WIDTH,
              i * SCREEN_WIDTH,
              (i + 1) * SCREEN_WIDTH,
            ];
            const dotWidth = scrollX.interpolate({
              inputRange,
              outputRange: [8, 24, 8],
              extrapolate: 'clamp',
            });
            const dotOpacity = scrollX.interpolate({
              inputRange,
              outputRange: [0.3, 1, 0.3],
              extrapolate: 'clamp',
            });
            return (
              <Animated.View
                key={i}
                style={[
                  styles.dot,
                  {
                    width: dotWidth,
                    opacity: dotOpacity,
                    backgroundColor: palette.accent,
                  },
                ]}
              />
            );
          })}
        </View>

        {/* Action button */}
        <Pressable
          style={[styles.nextButton, isLastPage && styles.getStartedButton]}
          onPress={handleNext}
        >
          {isLastPage ? (
            <>
              <Ionicons name="location" size={20} color="#FFFFFF" />
              <Text style={styles.nextButtonText}>Enable Location & Get Started</Text>
            </>
          ) : (
            <>
              <Text style={styles.nextButtonText}>Next</Text>
              <Ionicons name="arrow-forward" size={20} color="#FFFFFF" />
            </>
          )}
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
  },
  skipButton: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 60 : 40,
    right: 24,
    zIndex: 10,
    paddingVertical: 8,
    paddingHorizontal: 16,
  },
  skipText: {
    color: palette.textMuted,
    fontSize: 16,
    fontWeight: '600',
  },
  page: {
    width: SCREEN_WIDTH,
    flex: 1,
    paddingTop: Platform.OS === 'ios' ? 120 : 100,
  },
  heroArea: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 40,
  },
  iconCircle: {
    width: 160,
    height: 160,
    borderRadius: 80,
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconInner: {
    width: 110,
    height: 110,
    borderRadius: 55,
    alignItems: 'center',
    justifyContent: 'center',
  },
  contentArea: {
    flex: 1,
    paddingHorizontal: 32,
    gap: 16,
  },
  pageTitle: {
    ...typeStyles.screenTitle,
    fontSize: 28,
    color: palette.text,
    textAlign: 'center',
  },
  pageSubtitle: {
    color: palette.textSecondary,
    fontSize: 16,
    lineHeight: 24,
    textAlign: 'center',
  },
  featuresContainer: {
    marginTop: 24,
    gap: 16,
  },
  featureRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
  },
  featureIconBg: {
    width: 40,
    height: 40,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  featureText: {
    flex: 1,
    color: palette.text,
    fontSize: 15,
    lineHeight: 20,
  },
  bottomArea: {
    paddingHorizontal: 32,
    paddingBottom: Platform.OS === 'ios' ? 50 : 32,
    gap: 24,
  },
  dotsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  dot: {
    height: 8,
    borderRadius: 4,
  },
  nextButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: palette.accent,
    paddingVertical: 16,
    borderRadius: 14,
    shadowColor: '#000',
    shadowOpacity: 0.12,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
    elevation: 4,
  },
  getStartedButton: {
    backgroundColor: palette.accent,
  },
  nextButtonText: {
    color: '#FFFFFF',
    fontSize: 17,
    fontWeight: '700',
  },
});
