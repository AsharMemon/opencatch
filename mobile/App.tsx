import React, { useState, useCallback, useEffect } from 'react';
import { StatusBar } from 'expo-status-bar';
import { NavigationContainer, DefaultTheme } from '@react-navigation/native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ActivityIndicator, View } from 'react-native';
import {
  useFonts,
  PlayfairDisplay_400Regular,
  PlayfairDisplay_700Bold,
  PlayfairDisplay_400Regular_Italic,
} from '@expo-google-fonts/playfair-display';
import { OpenCatchNavigator } from './src/navigation/CastlineNavigator';
import { SplashScreen } from './src/screens/SplashScreen';
import { AuthScreen } from './src/screens/AuthScreen';
import { palette } from './src/theme/palette';
import { auth, type AuthState, type UserProfile } from './src/services/auth';
import { setOnTokenExpired } from './src/services/api';

type AppState = 'splash' | 'auth' | 'app';

const openCatchTheme = {
  ...DefaultTheme,
  dark: false,
  colors: {
    ...DefaultTheme.colors,
    background: palette.background,
    card: palette.surface,
    text: palette.text,
    border: palette.border,
    primary: palette.accent,
    notification: palette.accent,
  },
};

export default function App() {
  const [appState, setAppState] = useState<AppState>('splash');
  const [user, setUser] = useState<UserProfile | null>(null);

  const [fontsLoaded] = useFonts({
    'PlayfairDisplay-Regular': PlayfairDisplay_400Regular,
    'PlayfairDisplay-Bold': PlayfairDisplay_700Bold,
    'PlayfairDisplay-Italic': PlayfairDisplay_400Regular_Italic,
  });

  // Register the token-expired callback so the api layer can trigger logout
  useEffect(() => {
    setOnTokenExpired(() => {
      setUser(null);
      setAppState('auth');
    });
  }, []);

  const handleSplashFinish = useCallback(async () => {
    // Try to restore a saved session
    try {
      const restored = await auth.restoreSession();
      if (restored.isAuthenticated && restored.user) {
        setUser(restored.user);
        setAppState('app');
        return;
      }
    } catch {
      // Session restoration failed — show auth screen
    }
    setAppState('auth');
  }, []);

  const handleAuth = useCallback((state: AuthState) => {
    setUser(state.user);
    setAppState('app');
  }, []);

  const handleLogout = useCallback(async () => {
    await auth.logout();
    setUser(null);
    setAppState('auth');
  }, []);

  if (!fontsLoaded) {
    return (
      <View style={{ flex: 1, backgroundColor: palette.background, alignItems: 'center', justifyContent: 'center' }}>
        <ActivityIndicator color={palette.accent} size="large" />
      </View>
    );
  }

  if (appState === 'splash') {
    return (
      <SafeAreaProvider>
        <StatusBar style="light" />
        <SplashScreen onFinish={handleSplashFinish} />
      </SafeAreaProvider>
    );
  }

  if (appState === 'auth') {
    return (
      <SafeAreaProvider>
        <StatusBar style="light" />
        <AuthScreen onAuth={handleAuth} />
      </SafeAreaProvider>
    );
  }

  return (
    <SafeAreaProvider>
      <NavigationContainer theme={openCatchTheme}>
        <StatusBar style="light" />
        <OpenCatchNavigator user={user} onLogout={handleLogout} />
      </NavigationContainer>
    </SafeAreaProvider>
  );
}
