import React, { useEffect, useRef, useCallback } from 'react';
import {
  View,
  StyleSheet,
  Animated,
  Easing,
  Dimensions,
  Image,
} from 'react-native';
import { palette } from '../theme/palette';

let ExpoSplashScreen: any = null;
try { ExpoSplashScreen = require('expo-splash-screen'); } catch {}

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');

const LOGO_WIDTH = SCREEN_W * 0.65;
const LOGO_ASPECT = 1396 / 2560; // OpenCatch text logo aspect ratio (2560x1396)
const LOGO_HEIGHT = LOGO_WIDTH * LOGO_ASPECT;

interface SplashScreenProps {
  onFinish: () => void;
}

export function SplashScreen({ onFinish }: SplashScreenProps) {
  const logoOpacity = useRef(new Animated.Value(0)).current;
  const logoScale = useRef(new Animated.Value(0.92)).current;
  const screenOpacity = useRef(new Animated.Value(1)).current;
  const finishCalled = useRef(false);

  const handleFinish = useCallback(() => {
    if (finishCalled.current) return;
    finishCalled.current = true;
    onFinish();
  }, [onFinish]);

  useEffect(() => {
    ExpoSplashScreen?.hideAsync?.().catch(() => {});

    // Phase 1: Logo fade in with subtle scale (0.5s)
    Animated.parallel([
      Animated.timing(logoOpacity, { toValue: 1, duration: 500, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
      Animated.timing(logoScale, { toValue: 1, duration: 500, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
    ]).start();

    // Phase 2: Hold for 1.5s, then fade out (0.5s)
    Animated.sequence([
      Animated.delay(2000), // 500ms fade-in + 1500ms hold
      Animated.timing(screenOpacity, { toValue: 0, duration: 500, easing: Easing.in(Easing.cubic), useNativeDriver: true }),
    ]).start(() => handleFinish());

    const fallback = setTimeout(handleFinish, 4000);
    return () => clearTimeout(fallback);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Animated.View style={[styles.container, { opacity: screenOpacity }]}>
      <Animated.View style={[styles.logoContainer, {
        opacity: logoOpacity,
        transform: [{ scale: logoScale }],
      }]}>
        <Image source={require('../../assets/vector-logo.png')} style={styles.logoImage} resizeMode="contain" />
      </Animated.View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
    justifyContent: 'center',
    alignItems: 'center',
  },
  logoContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    transform: [{ translateY: -SCREEN_H * 0.08 }],
  },
  logoImage: {
    width: LOGO_WIDTH,
    height: LOGO_HEIGHT,
  },
});
