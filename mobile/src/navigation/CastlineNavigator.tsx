import React from 'react';
import { StyleSheet, Platform } from 'react-native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { Ionicons } from '@expo/vector-icons';

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
import { CatchReportScreen } from '../screens/CatchReportScreen';
import { CollectionScreen } from '../screens/CollectionScreen';
import { ActivityScreen } from '../screens/ActivityScreen';
import { ForecastsScreen } from '../screens/ForecastsScreen';
import { ProfileScreen } from '../screens/ProfileScreen';
import { LocationDetailScreen } from '../screens/LocationDetailScreen';
import { StatsScreen } from '../screens/StatsScreen';
import { SpeciesGuideScreen } from '../screens/SpeciesGuideScreen';
import { OfflineMapsScreen } from '../screens/OfflineMapsScreen';
import { TideChartScreen } from '../screens/TideChartScreen';
import { BaitGuideScreen } from '../screens/BaitGuideScreen';
import { RegulationsScreen } from '../screens/RegulationsScreen';
import { AlertsScreen } from '../screens/AlertsScreen';
import { SafetyScreen } from '../screens/SafetyScreen';
import { TrackRecordingScreen } from '../screens/TrackRecordingScreen';
import { WeatherBuoysScreen } from '../screens/WeatherBuoysScreen';
import { SunMoonScreen } from '../screens/SunMoonScreen';
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
  ActivityTab: { focused: 'list', default: 'list-outline' },
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
        headerStyle: {
          backgroundColor: palette.background,
        },
        headerTintColor: palette.text,
        headerShadowVisible: false,
        headerTitleStyle: typeStyles.navHeader,
        tabBarStyle: {
          backgroundColor: '#FFFFFF',
          borderTopColor: palette.border,
          borderTopWidth: StyleSheet.hairlineWidth,
          height: Platform.OS === 'ios' ? 88 : 64,
          paddingBottom: Platform.OS === 'ios' ? 28 : 8,
          paddingTop: 8,
          elevation: 0,
          shadowColor: '#000',
          shadowOpacity: 0.04,
          shadowRadius: 8,
          shadowOffset: { width: 0, height: -2 },
        },
        tabBarActiveTintColor: palette.accent,
        tabBarInactiveTintColor: palette.tabInactive,
        tabBarLabelStyle: {
          fontSize: 10,
          fontWeight: '600',
          letterSpacing: 0.3,
        },
        tabBarIcon: ({ focused, color, size }) => {
          const iconConfig = TAB_ICONS[route.name];
          const iconName = iconConfig
            ? (focused ? iconConfig.focused : iconConfig.default)
            : 'ellipse-outline';
          return <Ionicons name={iconName as any} size={22} color={color} />;
        },
      })}
    >
      <Tab.Screen
        name="MapTab"
        component={MapScreen}
        options={{ title: 'Map', headerShown: false }}
      />
      <Tab.Screen
        name="LogTab"
        component={LogTabPlaceholder}
        options={{ title: 'Log', headerTitle: 'OpenCatch' }}
        listeners={({ navigation }: any) => ({
          tabPress: (e: any) => {
            e.preventDefault();
            (navigation as any).navigate('CatchReport', {});
          },
        })}
      />
      <Tab.Screen
        name="ForecastsTab"
        component={ForecastsScreen}
        options={{ title: 'Forecasts', headerTitle: 'OpenCatch' }}
      />
      <Tab.Screen
        name="ActivityTab"
        component={ActivityScreen}
        options={{ title: 'Activity', headerTitle: 'OpenCatch' }}
      />
      <Tab.Screen
        name="ProfileTab"
        options={{ title: 'Profile', headerTitle: 'OpenCatch' }}
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
        headerTitleStyle: {
          fontWeight: '600',
        },
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
        name="WeatherBuoys"
        component={WeatherBuoysScreen}
        options={{ title: 'Weather Buoys' }}
      />
      <Stack.Screen
        name="SunMoon"
        component={SunMoonScreen}
        options={{ title: 'Sun, Moon & Solunar' }}
      />
    </Stack.Navigator>
  );
}
