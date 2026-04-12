import AsyncStorage from '@react-native-async-storage/async-storage';
import { defaultSettings } from '../data/mockData';
import type { MapStyle, UnitSystem, UserSettings } from '../types/models';

const SETTINGS_KEY = '@opencatch_settings';
const LEGACY_UNITS_KEY = '@opencatch_settings_units';

const unitListeners = new Set<(units: UnitSystem) => void>();

function normalizeUnits(value: unknown): UnitSystem {
  return value === 'metric' ? 'metric' : 'imperial';
}

function normalizeMapStyle(value: unknown): MapStyle {
  return value === 'satellite' || value === 'terrain' ? value : 'standard';
}

function mergeSettings(raw?: Partial<UserSettings> | null): UserSettings {
  return {
    ...defaultSettings,
    ...(raw ?? {}),
    units: normalizeUnits(raw?.units),
    mapStyle: normalizeMapStyle(raw?.mapStyle),
    notifications: {
      ...defaultSettings.notifications,
      ...(raw?.notifications ?? {}),
    },
  };
}

async function persist(settings: UserSettings): Promise<void> {
  await AsyncStorage.multiSet([
    [SETTINGS_KEY, JSON.stringify(settings)],
    [LEGACY_UNITS_KEY, settings.units],
  ]);
}

function notifyUnitListeners(units: UnitSystem): void {
  for (const listener of unitListeners) {
    listener(units);
  }
}

export async function getStoredSettings(): Promise<UserSettings> {
  try {
    const raw = await AsyncStorage.getItem(SETTINGS_KEY);
    if (raw) {
      return mergeSettings(JSON.parse(raw) as Partial<UserSettings>);
    }
  } catch {
    // Fall back to defaults below.
  }

  try {
    const legacyUnits = await AsyncStorage.getItem(LEGACY_UNITS_KEY);
    if (legacyUnits === 'metric' || legacyUnits === 'imperial') {
      const migrated = mergeSettings({ units: legacyUnits });
      await persist(migrated);
      return migrated;
    }
  } catch {
    // Fall back to defaults below.
  }

  return mergeSettings();
}

export async function updateStoredSettings(patch: Partial<UserSettings>): Promise<UserSettings> {
  const previous = await getStoredSettings();
  const next = mergeSettings({
    ...previous,
    ...patch,
    notifications: {
      ...previous.notifications,
      ...(patch.notifications ?? {}),
    },
  });
  await persist(next);
  if (previous.units !== next.units) {
    notifyUnitListeners(next.units);
  }
  return next;
}

export function subscribeToUnitSystem(listener: (units: UnitSystem) => void): () => void {
  unitListeners.add(listener);
  return () => {
    unitListeners.delete(listener);
  };
}
