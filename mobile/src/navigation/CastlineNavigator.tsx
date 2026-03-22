import React, { Suspense, useCallback, useRef, lazy, ComponentType } from 'react';
import { ActivityIndicator, Animated, Image, StyleSheet, Platform, View } from 'react-native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { Ionicons } from '@expo/vector-icons';
import { hapticLight } from '../utils/haptics';

// eslint-disable-next-line @typescript-eslint/no-var-requires
const logoImage = require('../../assets/vector-logo.png');

function HeaderLogo() {
  return (
    <Image
      source={logoImage}
      style={{ height: 32, width: 150 }}
      resizeMode="contain"
    />
  );
}

/** Skeleton fallback for lazy-loaded screens */
function ScreenSkeleton() {
  return (
    <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#FAFAF8' }}>
      <ActivityIndicator size="large" color="#0A6EBD" />
    </View>
  );
}

/** Wrap a lazy import in Suspense with skeleton fallback */
function withSuspense<P extends object>(LazyComponent: React.LazyExoticComponent<ComponentType<P>>): ComponentType<P> {
  return function SuspenseWrapped(props: P) {
    return (
      <Suspense fallback={<ScreenSkeleton />}>
        <LazyComponent {...props} />
      </Suspense>
    );
  };
}

// MapLibre GL doesn't work on web — use a placeholder
const MapScreen = Platform.OS === 'web'
  ? () => {
      const RN = require('react-native');
      return (
        <RN.View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#F5F5F5' }}>
          <Ionicons name="map-outline" size={48} color="#999" />
          <RN.Text style={{ fontSize: 18, fontWeight: '600', color: '#333', marginTop: 12 }}>Map View</RN.Text>
          <RN.Text style={{ fontSize: 14, color: '#666', marginTop: 8 }}>Available on iOS and Android</RN.Text>
        </RN.View>
      );
    }
  : require('../screens/MapScreen').MapScreen;

// ── Lazy-loaded screens (deferred until navigated to) ───────────────────────
// MapScreen is NOT lazy — it's the primary screen and must load immediately.
// Tab screens (Forecasts, Tools, Profile) are lazy since they're secondary.

const CatchReportScreen = withSuspense(lazy(() => import('../screens/CatchReportScreen').then(m => ({ default: m.CatchReportScreen }))));
const CollectionScreen = withSuspense(lazy(() => import('../screens/CollectionScreen').then(m => ({ default: m.CollectionScreen }))));
const ToolsScreen = withSuspense(lazy(() => import('../screens/ToolsScreen').then(m => ({ default: m.ToolsScreen }))));
const ForecastsScreen = withSuspense(lazy(() => import('../screens/ForecastsScreen').then(m => ({ default: m.ForecastsScreen }))));
const ProfileScreen = withSuspense(lazy(() => import('../screens/ProfileScreen').then(m => ({ default: m.ProfileScreen as ComponentType<any> }))));
const LocationDetailScreen = withSuspense(lazy(() => import('../screens/LocationDetailScreen').then(m => ({ default: m.LocationDetailScreen }))));
const StatsScreen = withSuspense(lazy(() => import('../screens/StatsScreen').then(m => ({ default: m.StatsScreen }))));
const SpeciesGuideScreen = withSuspense(lazy(() => import('../screens/SpeciesGuideScreen').then(m => ({ default: m.SpeciesGuideScreen }))));
const OfflineMapsScreen = withSuspense(lazy(() => import('../screens/OfflineMapsScreen').then(m => ({ default: m.OfflineMapsScreen }))));
const TideChartScreen = withSuspense(lazy(() => import('../screens/TideChartScreen').then(m => ({ default: m.TideChartScreen }))));
const BaitGuideScreen = withSuspense(lazy(() => import('../screens/BaitGuideScreen').then(m => ({ default: m.BaitGuideScreen }))));
const RegulationsScreen = withSuspense(lazy(() => import('../screens/RegulationsScreen').then(m => ({ default: m.RegulationsScreen }))));
const AlertsScreen = withSuspense(lazy(() => import('../screens/AlertsScreen').then(m => ({ default: m.AlertsScreen }))));
const SafetyScreen = withSuspense(lazy(() => import('../screens/SafetyScreen').then(m => ({ default: m.SafetyScreen }))));
const TrackRecordingScreen = withSuspense(lazy(() => import('../screens/TrackRecordingScreen').then(m => ({ default: m.TrackRecordingScreen }))));
const TrackHistoryScreen = withSuspense(lazy(() => import('../screens/TrackHistoryScreen').then(m => ({ default: m.TrackHistoryScreen }))));
const WeatherBuoysScreen = withSuspense(lazy(() => import('../screens/WeatherBuoysScreen').then(m => ({ default: m.WeatherBuoysScreen }))));
const SunMoonScreen = withSuspense(lazy(() => import('../screens/SunMoonScreen').then(m => ({ default: m.SunMoonScreen }))));
const FishingPressureScreen = withSuspense(lazy(() => import('../screens/FishingPressureScreen').then(m => ({ default: m.FishingPressureScreen }))));
const BestTimesScreen = withSuspense(lazy(() => import('../screens/BestTimesScreen').then(m => ({ default: m.BestTimesScreen }))));
const WaterInsightsScreen = withSuspense(lazy(() => import('../screens/WaterInsightsScreen').then(m => ({ default: m.WaterInsightsScreen }))));
const SpeciesMapScreen = withSuspense(lazy(() => import('../screens/SpeciesMapScreen').then(m => ({ default: m.SpeciesMapScreen }))));
const SettingsScreen = withSuspense(lazy(() => import('../screens/SettingsScreen').then(m => ({ default: m.SettingsScreen }))));
const AnnotationScreen = withSuspense(lazy(() => import('../screens/AnnotationScreen').then(m => ({ default: m.AnnotationScreen }))));
const ActivityLogScreen = withSuspense(lazy(() => import('../screens/ActivityLogScreen').then(m => ({ default: m.ActivityLogScreen }))));
const TripPlannerScreen = withSuspense(lazy(() => import('../screens/TripPlannerScreen').then(m => ({ default: m.TripPlannerScreen }))));
const FuelCalculatorScreen = withSuspense(lazy(() => import('../screens/FuelCalculatorScreen').then(m => ({ default: m.FuelCalculatorScreen }))));
const MaintenanceScreen = withSuspense(lazy(() => import('../screens/MaintenanceScreen').then(m => ({ default: m.MaintenanceScreen }))));
const LakeFinderScreen = withSuspense(lazy(() => import('../screens/LakeFinderScreen').then(m => ({ default: m.LakeFinderScreen }))));
const KnotGuideScreen = withSuspense(lazy(() => import('../screens/KnotGuideScreen').then(m => ({ default: m.KnotGuideScreen }))));
const IceFishingScreen = withSuspense(lazy(() => import('../screens/IceFishingScreen').then(m => ({ default: m.IceFishingScreen }))));
const RoutePlannerScreen = withSuspense(lazy(() => import('../screens/RoutePlannerScreen').then(m => ({ default: m.RoutePlannerScreen }))));
const TripVisualizationScreen = withSuspense(lazy(() => import('../screens/TripVisualizationScreen').then(m => ({ default: m.TripVisualizationScreen }))));


import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import type { RootStackParamList, TabParamList } from '../types/navigation';
import type { UserProfile } from '../services/auth';

const Tab = createBottomTabNavigator<TabParamList>();
const Stack = createNativeStackNavigator<RootStackParamList>();

const TAB_ICONS: Record<string, { focused: keyof typeof Ionicons.glyphMap; default: keyof typeof Ionicons.glyphMap }> = {
  MapTab: { focused: 'map', default: 'map-outline' },
  LogTab: { focused: 'add-circle', default: 'add-circle-outline' },
  ForecastsTab: { focused: 'analytics', default: 'analytics-outline' },
  ToolsTab: { focused: 'construct', default: 'construct-outline' },
  ProfileTab: { focused: 'person', default: 'person-outline' },
};

interface NavProps {
  user: UserProfile | null;
  onLogout: () => void;
}

function TabNavigator({ user, onLogout }: NavProps) {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        animation: 'fade' as const,
        headerStyle: {
          backgroundColor: palette.background,
        },
        headerTintColor: palette.text,
        headerShadowVisible: false,
        headerTitleStyle: { fontFamily: typeStyles.navHeader.fontFamily, fontSize: typeStyles.navHeader.fontSize, fontWeight: typeStyles.navHeader.fontWeight as any },
        tabBarStyle: {
          backgroundColor: 'rgba(255, 255, 255, 0.88)',
          borderTopColor: 'rgba(229, 229, 224, 0.5)',
          borderTopWidth: StyleSheet.hairlineWidth,
          height: Platform.OS === 'ios' ? 88 : 64,
          paddingBottom: Platform.OS === 'ios' ? 28 : 8,
          paddingTop: 8,
          elevation: 0,
          shadowColor: '#000',
          shadowOpacity: 0.06,
          shadowRadius: 12,
          shadowOffset: { width: 0, height: -3 },
          // Frosted glass backdrop on iOS
          ...(Platform.OS === 'ios' ? { backdropFilter: 'blur(20px)' } as any : {}),
        },
        tabBarActiveTintColor: palette.accent,
        tabBarInactiveTintColor: palette.tabInactive,
        tabBarLabelStyle: {
          fontSize: 10,
          fontWeight: '600',
          letterSpacing: 0.3,
          marginTop: 2,
        },
        tabBarIcon: ({ focused, color }) => {
          const iconConfig = TAB_ICONS[route.name];
          const iconName = iconConfig
            ? (focused ? iconConfig.focused : iconConfig.default)
            : 'ellipse-outline';
          return (
            <View style={{ alignItems: 'center' }}>
              <Ionicons name={iconName as any} size={22} color={color} />
              {focused && (
                <View
                  style={{
                    width: 4,
                    height: 4,
                    borderRadius: 2,
                    backgroundColor: palette.accent,
                    marginTop: 3,
                  }}
                />
              )}
            </View>
          );
        },
      })}
    >
      <Tab.Screen
        name="MapTab"
        component={MapScreen}
        options={{ title: 'Map', headerShown: false }}
        listeners={{ tabPress: () => hapticLight() }}
      />
      <Tab.Screen
        name="LogTab"
        component={LogTabPlaceholder}
        options={{ title: 'Log' }}
        listeners={({ navigation }: any) => ({
          tabPress: (e: any) => {
            e.preventDefault();
            hapticLight();
            (navigation as any).navigate('CatchReport', {});
          },
        })}
      />
      <Tab.Screen
        name="ForecastsTab"
        component={ForecastsScreen}
        options={{ title: 'Forecasts' }}
        listeners={{ tabPress: () => hapticLight() }}
      />
      <Tab.Screen
        name="ToolsTab"
        component={ToolsScreen}
        options={{ title: 'Tools' }}
        listeners={{ tabPress: () => hapticLight() }}
      />
      <Tab.Screen
        name="ProfileTab"
        options={{ title: 'Profile' }}
        listeners={{ tabPress: () => hapticLight() }}
      >
        {(props: any) => <ProfileScreen {...props} user={user} onLogout={onLogout} />}
      </Tab.Screen>
    </Tab.Navigator>
  );
}

// Placeholder for the Log tab (actual screen is a modal)
function LogTabPlaceholder() {
  return null;
}

export function OpenCatchNavigator({ user, onLogout }: NavProps) {
  return (
    <Stack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: palette.background },
        headerTintColor: palette.text,
        headerShadowVisible: false,
        contentStyle: { backgroundColor: palette.background },
        headerTitleStyle: { fontFamily: typeStyles.navHeader.fontFamily, fontSize: typeStyles.navHeader.fontSize, fontWeight: typeStyles.navHeader.fontWeight as any },
        animation: 'fade_from_bottom',
        animationDuration: 250,
      }}
    >
      <Stack.Screen
        name="Tabs"
        options={{ headerShown: false }}
      >
        {() => <TabNavigator user={user} onLogout={onLogout} />}
      </Stack.Screen>
      <Stack.Screen
        name="LocationDetail"
        component={LocationDetailScreen}
        options={{ title: 'Location' }}
      />
      <Stack.Screen
        name="CatchReport"
        component={CatchReportScreen}
        options={{
          title: 'Log a Catch',
          presentation: 'modal',
        }}
      />
      <Stack.Screen
        name="Stats"
        component={StatsScreen}
        options={{ title: 'My Stats' }}
      />
      <Stack.Screen
        name="SpeciesGuide"
        component={SpeciesGuideScreen}
        options={{ title: 'Species Guide' }}
      />
      <Stack.Screen
        name="OfflineMaps"
        component={OfflineMapsScreen}
        options={{ title: 'Offline Maps' }}
      />
      <Stack.Screen
        name="TideChart"
        component={TideChartScreen}
        options={{ title: 'Tides & Currents' }}
      />
      <Stack.Screen
        name="BaitGuide"
        component={BaitGuideScreen}
        options={{ title: 'Bait Guide' }}
      />
      <Stack.Screen
        name="Regulations"
        component={RegulationsScreen}
        options={{ title: 'Fishing Regulations' }}
      />
      <Stack.Screen
        name="Alerts"
        component={AlertsScreen}
        options={{ title: 'Weather Alerts' }}
      />
      <Stack.Screen
        name="Safety"
        component={SafetyScreen}
        options={{ title: 'Safety & Float Plan' }}
      />
      <Stack.Screen
        name="TrackRecording"
        component={TrackRecordingScreen}
        options={{ title: 'Track Recording' }}
      />
      <Stack.Screen
        name="TrackHistory"
        component={TrackHistoryScreen}
        options={{ title: 'Track History' }}
      />
      <Stack.Screen
        name="WeatherBuoys"
        component={WeatherBuoysScreen}
        options={{ title: 'Weather Buoys' }}
      />
      <Stack.Screen
        name="SunMoon"
        component={SunMoonScreen}
        options={{ title: 'Sun, Moon & Solunar' }}
      />
      <Stack.Screen
        name="FishingPressure"
        component={FishingPressureScreen}
        options={{ title: 'Fishing Pressure' }}
      />
      <Stack.Screen
        name="BestTimes"
        component={BestTimesScreen}
        options={{ title: 'Best Fishing Times' }}
      />
      <Stack.Screen
        name="WaterInsights"
        component={WaterInsightsScreen}
        options={{ title: 'Water Insights' }}
      />
      <Stack.Screen
        name="SpeciesMap"
        component={SpeciesMapScreen}
        options={{ title: 'Species Map' }}
      />
      <Stack.Screen
        name="Settings"
        component={SettingsScreen}
        options={{ title: 'Settings' }}
      />
      <Stack.Screen
        name="Annotations"
        component={AnnotationScreen}
        options={{ title: 'Annotations' }}
      />
      <Stack.Screen
        name="ActivityLog"
        component={ActivityLogScreen}
        options={{ title: 'Activity Log' }}
      />
      <Stack.Screen
        name="TripPlanner"
        component={TripPlannerScreen}
        options={{ title: 'Trip Planner' }}
      />
      <Stack.Screen
        name="FuelCalculator"
        component={FuelCalculatorScreen}
        options={{ title: 'Fuel Calculator' }}
      />
      <Stack.Screen
        name="Maintenance"
        component={MaintenanceScreen}
        options={{ title: 'Engine & Maintenance' }}
      />
      <Stack.Screen
        name="LakeFinder"
        component={LakeFinderScreen}
        options={{ title: 'Lake Finder' }}
      />
      <Stack.Screen
        name="KnotGuide"
        component={KnotGuideScreen}
        options={{ title: 'Knots & Rigs' }}
      />
      <Stack.Screen
        name="IceFishing"
        component={IceFishingScreen}
        options={{ title: 'Ice Fishing' }}
      />
      <Stack.Screen
        name="RoutePlanner"
        component={RoutePlannerScreen}
        options={{ title: 'Route Planner' }}
      />
      <Stack.Screen
        name="TripVisualization"
        component={TripVisualizationScreen}
        options={{ title: 'Trip Visualization' }}
      />
    </Stack.Navigator>
  );
}
