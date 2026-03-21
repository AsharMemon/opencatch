import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { Ionicons } from '@expo/vector-icons';

import HomeScreen from './src/screens/HomeScreen';
import IdentifyScreen from './src/screens/IdentifyScreen';
import ExploreScreen from './src/screens/ExploreScreen';
import LogScreen from './src/screens/LogScreen';

const Tab = createBottomTabNavigator();

const TAB_ICONS = {
  Home: { focused: 'home', unfocused: 'home-outline' },
  Identify: { focused: 'fish', unfocused: 'fish-outline' },
  Explore: { focused: 'compass', unfocused: 'compass-outline' },
  Log: { focused: 'journal', unfocused: 'journal-outline' },
};

export default function App() {
  return (
    <NavigationContainer>
      <Tab.Navigator
        screenOptions={({ route }) => ({
          headerShown: false,
          tabBarIcon: ({ focused, color, size }) => {
            const icons = TAB_ICONS[route.name];
            return <Ionicons name={focused ? icons.focused : icons.unfocused} size={size} color={color} />;
          },
          tabBarActiveTintColor: '#0A7AFF',
          tabBarInactiveTintColor: '#999',
          tabBarStyle: { paddingBottom: 8, height: 60 },
        })}
      >
        <Tab.Screen name="Home" component={HomeScreen} />
        <Tab.Screen name="Identify" component={IdentifyScreen} options={{ tabBarLabel: 'Fish ID' }} />
        <Tab.Screen name="Explore" component={ExploreScreen} />
        <Tab.Screen name="Log" component={LogScreen} options={{ tabBarLabel: 'Catch Log' }} />
      </Tab.Navigator>
    </NavigationContainer>
  );
}
