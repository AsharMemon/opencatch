/**
 * OpenCatch — Boat Profile Service
 *
 * Store and manage boat/watercraft profiles for:
 * - Float plan pre-fill
 * - Track recording metadata
 * - Speed/fuel calculations
 * - Insurance documentation
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

// ── Types ────────────────────────────────────────────────────────────────────

export type BoatType =
  | 'bass_boat'
  | 'kayak'
  | 'canoe'
  | 'pontoon'
  | 'jon_boat'
  | 'center_console'
  | 'skiff'
  | 'inflatable'
  | 'sailboat'
  | 'shore'
  | 'other';

export interface BoatProfile {
  id: string;
  name: string;
  type: BoatType;
  make?: string;
  model?: string;
  year?: number;
  length?: number;       // feet
  color?: string;
  registrationNumber?: string;
  hullId?: string;
  engineType?: string;   // e.g. "Mercury 150HP"
  fuelCapacity?: number; // gallons
  maxCapacity?: number;  // persons
  photo?: string;        // URI
  draftFt?: number;      // draft depth in feet (for draft accessibility overlay)
  isDefault: boolean;
  createdAt: number;
}

export const BOAT_TYPE_LABELS: Record<BoatType, string> = {
  bass_boat: 'Bass Boat',
  kayak: 'Kayak',
  canoe: 'Canoe',
  pontoon: 'Pontoon',
  jon_boat: 'Jon Boat',
  center_console: 'Center Console',
  skiff: 'Skiff',
  inflatable: 'Inflatable',
  sailboat: 'Sailboat',
  shore: 'Shore / Bank',
  other: 'Other',
};

export const BOAT_TYPE_ICONS: Record<BoatType, string> = {
  bass_boat: 'boat',
  kayak: 'boat-outline',
  canoe: 'boat-outline',
  pontoon: 'boat',
  jon_boat: 'boat-outline',
  center_console: 'boat',
  skiff: 'boat-outline',
  inflatable: 'boat-outline',
  sailboat: 'boat',
  shore: 'walk-outline',
  other: 'boat-outline',
};

// ── Storage ──────────────────────────────────────────────────────────────────

const STORAGE_KEY = '@opencatch/boat_profiles';

/**
 * Get all saved boat profiles.
 */
export async function getBoatProfiles(): Promise<BoatProfile[]> {
  try {
    const json = await AsyncStorage.getItem(STORAGE_KEY);
    return json ? JSON.parse(json) : [];
  } catch {
    return [];
  }
}

/**
 * Get the default boat profile.
 */
export async function getDefaultBoat(): Promise<BoatProfile | null> {
  const profiles = await getBoatProfiles();
  return profiles.find(b => b.isDefault) ?? profiles[0] ?? null;
}

/**
 * Save a new boat profile.
 */
export async function saveBoatProfile(profile: Omit<BoatProfile, 'id' | 'createdAt'>): Promise<BoatProfile> {
  const profiles = await getBoatProfiles();

  const newProfile: BoatProfile = {
    ...profile,
    id: `boat_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    createdAt: Date.now(),
  };

  // If this is the default, unset others
  if (newProfile.isDefault) {
    profiles.forEach(p => { p.isDefault = false; });
  }

  // If this is the first, make it default
  if (profiles.length === 0) {
    newProfile.isDefault = true;
  }

  profiles.push(newProfile);
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(profiles));
  return newProfile;
}

/**
 * Update an existing boat profile.
 */
export async function updateBoatProfile(id: string, updates: Partial<BoatProfile>): Promise<BoatProfile | null> {
  const profiles = await getBoatProfiles();
  const idx = profiles.findIndex(p => p.id === id);
  if (idx === -1) return null;

  // If setting as default, unset others
  if (updates.isDefault) {
    profiles.forEach(p => { p.isDefault = false; });
  }

  profiles[idx] = { ...profiles[idx], ...updates };
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(profiles));
  return profiles[idx];
}

/**
 * Delete a boat profile.
 */
export async function deleteBoatProfile(id: string): Promise<void> {
  let profiles = await getBoatProfiles();
  profiles = profiles.filter(p => p.id !== id);

  // If we deleted the default, make the first remaining one default
  if (profiles.length > 0 && !profiles.some(p => p.isDefault)) {
    profiles[0].isDefault = true;
  }

  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(profiles));
}

/**
 * Format boat profile for display in float plans.
 */
export function formatBoatDescription(boat: BoatProfile): string {
  const parts = [
    BOAT_TYPE_LABELS[boat.type],
    boat.make && boat.model ? `${boat.make} ${boat.model}` : boat.make || boat.model,
    boat.year ? `(${boat.year})` : null,
    boat.length ? `${boat.length}ft` : null,
    boat.color,
  ].filter(Boolean);
  return parts.join(' — ');
}

/**
 * Format boat for float plan details.
 */
export function formatForFloatPlan(boat: BoatProfile): string {
  return [
    `Vessel: ${boat.name}`,
    `Type: ${BOAT_TYPE_LABELS[boat.type]}`,
    boat.make ? `Make: ${boat.make}` : null,
    boat.model ? `Model: ${boat.model}` : null,
    boat.year ? `Year: ${boat.year}` : null,
    boat.length ? `Length: ${boat.length}ft` : null,
    boat.color ? `Color: ${boat.color}` : null,
    boat.registrationNumber ? `Registration: ${boat.registrationNumber}` : null,
    boat.hullId ? `Hull ID: ${boat.hullId}` : null,
    boat.engineType ? `Engine: ${boat.engineType}` : null,
    boat.maxCapacity ? `Max Capacity: ${boat.maxCapacity} persons` : null,
  ].filter(Boolean).join('\n');
}
