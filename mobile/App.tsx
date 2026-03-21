import React, { useState, useCallback, useEffect, useRef } from 'react';
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
import AsyncStorage from '@react-native-async-storage/async-storage';
import { OpenCatchNavigator } from './src/navigation/CastlineNavigator';
import { SplashScreen } from './src/screens/SplashScreen';
import { AuthScreen } from './src/screens/AuthScreen';
import { OnboardingScreen, ONBOARDING_COMPLETE_KEY } from './src/screens/OnboardingScreen';
import { palette } from './src/theme/palette';
import { auth, type AuthState, type UserProfile } from './src/services/auth';
import { setOnTokenExpired } from './src/services/api';

type AppState = 'splash' | 'onboarding' | 'auth' | 'app';

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

  // ── Pre-warm critical checks in parallel with splash animation ────
  // We kick off onboarding + auth checks immediately so they're ready
  // by the time the splash animation finishes (~3.3 s).
  const preWarmResult = useRef<{
    resolved: boolean;
    nextState: AppState;
    user: UserProfile | null;
  }>({ resolved: false, nextState: 'auth', user: null });

  const splashDone = useRef(false);
  const preWarmDone = useRef(false);

  // Apply the pre-warmed result and transition out of splash
  const tryTransition = useCallback(() => {
    if (!splashDone.current || !preWarmDone.current) return;
    const result = preWarmResult.current;
    if (result.user) setUser(result.user);
    setAppState(result.nextState);
  }, []);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      let nextState: AppState = 'auth';
      let resolvedUser: UserProfile | null = null;

      // 1. Check onboarding (fast — AsyncStorage read)
      try {
        const onboardingDone = await AsyncStorage.getItem(ONBOARDING_COMPLETE_KEY);
        if (!onboardingDone) {
          nextState = 'onboarding';
          if (!cancelled) {
            preWarmResult.current = { resolved: true, nextState, user: null };
            preWarmDone.current = true;
            tryTransition();
          }
          return;
        }
      } catch {
        // AsyncStorage failed — skip onboarding
      }

      // 2. Try to restore session
      try {
        const restored = await auth.restoreSession();
        if (restored.isAuthenticated && restored.user) {
          resolvedUser = restored.user;
          nextState = 'app';
        }
      } catch {
        // Session restoration failed — show auth screen
      }

      if (!cancelled) {
        preWarmResult.current = { resolved: true, nextState, user: resolvedUser };
        preWarmDone.current = true;
        tryTransition();
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [tryTransition]);

  // ── Splash finish handler ─────────────────────────────────────────
  // Called when the splash animation completes (~3.3 s). If the pre-warm
  // check already resolved we transition immediately; otherwise we wait
  // for pre-warm to finish (it'll call tryTransition when ready).
  const handleSplashFinish = useCallback(() => {
    splashDone.current = true;
    tryTransition();
  }, [tryTransition]);

  const handleOnboardingComplete = useCallback(async () => {
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

  if (appState === 'onboarding') {
    return (
      <SafeAreaProvider>
        <StatusBar style="dark" />
        <OnboardingScreen onComplete={handleOnboardingComplete} />
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
