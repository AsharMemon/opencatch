import { useCallback, useEffect, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { UnitSystem } from '../types/models';

const UNITS_KEY = '@opencatch_settings_units';

/**
 * Hook to read and persist the user's preferred unit system.
 *
 * Reads from AsyncStorage on mount and exposes a toggle function.
 * Every screen that imports this hook will share the same storage key,
 * but each mount gets its own local state. For a global reactive update
 * across mounted screens consider wrapping in a Context — this is the
 * minimal useful version.
 */
export function useUnits() {
  const [units, setUnitsState] = useState<UnitSystem>('imperial');
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    AsyncStorage.getItem(UNITS_KEY).then((val) => {
      if (val === 'metric' || val === 'imperial') {
        setUnitsState(val);
      }
      setLoaded(true);
    });
  }, []);

  const setUnits = useCallback(async (next: UnitSystem) => {
    setUnitsState(next);
    await AsyncStorage.setItem(UNITS_KEY, next);
  }, []);

  const toggle = useCallback(async () => {
    const next: UnitSystem = units === 'imperial' ? 'metric' : 'imperial';
    await setUnits(next);
  }, [units, setUnits]);

  // ── Conversion helpers ──────────────────────────────────────────

  /** Convert miles to display string in current units. */
  const formatDistance = useCallback(
    (miles: number): string => {
      if (units === 'metric') {
        const km = miles * 1.60934;
        if (km < 0.1) return '< 0.1 km';
        if (km < 10) return `${km.toFixed(1)} km`;
        return `${Math.round(km)} km`;
      }
      if (miles < 0.1) return '< 0.1 mi';
      if (miles < 10) return `${miles.toFixed(1)} mi`;
      return `${Math.round(miles)} mi`;
    },
    [units],
  );

  /** Convert Fahrenheit to display string. */
  const formatTemp = useCallback(
    (f: number): string => {
      if (units === 'metric') {
        const c = ((f - 32) * 5) / 9;
        return `${Math.round(c)}°C`;
      }
      return `${Math.round(f)}°F`;
    },
    [units],
  );

  /** Convert feet to display string. */
  const formatDepth = useCallback(
    (ft: number): string => {
      if (units === 'metric') {
        const m = ft * 0.3048;
        return `${m.toFixed(1)} m`;
      }
      return `${ft.toFixed(1)} ft`;
    },
    [units],
  );

  /** Convert mph to display string. */
  const formatSpeed = useCallback(
    (mph: number): string => {
      if (units === 'metric') {
        const kmh = mph * 1.60934;
        return `${Math.round(kmh)} km/h`;
      }
      return `${Math.round(mph)} mph`;
    },
    [units],
  );

  return {
    units,
    loaded,
    setUnits,
    toggle,
    isMetric: units === 'metric',
    formatDistance,
    formatTemp,
    formatDepth,
    formatSpeed,
  } as const;
}
