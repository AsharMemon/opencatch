/**
 * OpenCatch — Catch Logging Enhancements
 *
 * Adds richer data to catch reports:
 * - Auto-fill weather conditions at time of catch
 * - Photo management and storage
 * - Catch statistics aggregation
 * - Personal best tracking
 * - Species-specific record keeping
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import * as ImagePicker from 'expo-image-picker';
// Note: expo-file-system v19+ uses class-based API

// ── Types ────────────────────────────────────────────────────────────────────

export interface CatchPhoto {
  uri: string;
  width: number;
  height: number;
  timestamp: number;
}

export interface EnhancedCatch {
  id: string;
  species: string;
  catchCount?: number;
  keptCount?: number;
  rating?: number;
  weight?: number;       // lbs
  length?: number;       // inches
  girth?: number;        // inches
  lat: number;
  lon: number;
  locationName?: string;
  photos: CatchPhoto[];
  bait?: string;
  technique?: string;
  waterTemp?: number;    // °F
  airTemp?: number;      // °F
  windSpeed?: number;    // mph
  windDirection?: string;
  pressure?: number;     // hPa
  cloudCover?: string;
  moonPhase?: string;
  waterClarity?: 'clear' | 'stained' | 'murky';
  waterDepth?: number;   // feet
  structure?: string;    // e.g. "rocky point", "weed bed"
  notes?: string;
  released: boolean;
  timestamp: number;     // Unix ms
  boatId?: string;
}

export interface PersonalBest {
  species: string;
  weight: number;
  length?: number;
  catchId: string;
  timestamp: number;
  locationName?: string;
}

export interface SpeciesStats {
  species: string;
  totalCaught: number;
  totalKept: number;
  totalReleased: number;
  avgWeight?: number;
  avgLength?: number;
  personalBest?: PersonalBest;
  lastCaught?: number;
  favoriteBait?: string;
  favoriteLocation?: string;
}

// ── Constants ────────────────────────────────────────────────────────────────

const CATCHES_KEY = '@opencatch/catches';
const PB_KEY = '@opencatch/personal_bests';
const catchListeners = new Set<(catches: EnhancedCatch[]) => void>();
// Photos stored at their picker URIs — no filesystem copy needed in modern Expo

function notifyCatchListeners(catches: EnhancedCatch[]): void {
  for (const listener of catchListeners) {
    try {
      listener(catches);
    } catch (error) {
      console.warn('[catchEnhancements] catch listener failed:', error);
    }
  }
}

// ── Photo Management ─────────────────────────────────────────────────────────

/**
 * Launch the camera to take a catch photo.
 */
export async function takeCatchPhoto(): Promise<CatchPhoto | null> {
  const { status } = await ImagePicker.requestCameraPermissionsAsync();
  if (status !== 'granted') return null;

  const result = await ImagePicker.launchCameraAsync({
    mediaTypes: 'images',
    quality: 0.8,
    allowsEditing: true,
    aspect: [4, 3],
  });

  if (result.canceled || !result.assets[0]) return null;

  const asset = result.assets[0];

  return {
    uri: asset.uri,
    width: asset.width,
    height: asset.height,
    timestamp: Date.now(),
  };
}

/**
 * Pick a catch photo from the gallery.
 */
export async function pickCatchPhoto(): Promise<CatchPhoto | null> {
  const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (status !== 'granted') return null;

  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: 'images',
    quality: 0.8,
    allowsEditing: true,
    aspect: [4, 3],
  });

  if (result.canceled || !result.assets[0]) return null;

  const asset = result.assets[0];

  return {
    uri: asset.uri,
    width: asset.width,
    height: asset.height,
    timestamp: Date.now(),
  };
}

// ── Weather Auto-Fill ────────────────────────────────────────────────────────

/**
 * Fetch current weather conditions for catch auto-fill.
 */
export async function fetchCurrentConditions(lat: number, lon: number): Promise<Partial<EnhancedCatch>> {
  try {
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,wind_speed_10m,wind_direction_10m,surface_pressure,cloud_cover&temperature_unit=fahrenheit&wind_speed_unit=mph`;

    const resp = await fetch(url, { signal: AbortSignal.timeout(10000) });
    if (!resp.ok) return {};

    const data = await resp.json();
    const c = data.current;
    if (!c) return {};

    const windDirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
    const windDirection = windDirs[Math.round((c.wind_direction_10m ?? 0) / 22.5) % 16];

    let cloudCover = 'Clear';
    if (c.cloud_cover > 80) cloudCover = 'Overcast';
    else if (c.cloud_cover > 50) cloudCover = 'Mostly Cloudy';
    else if (c.cloud_cover > 25) cloudCover = 'Partly Cloudy';

    return {
      airTemp: c.temperature_2m != null ? Math.round(c.temperature_2m) : undefined,
      windSpeed: c.wind_speed_10m != null ? Math.round(c.wind_speed_10m) : undefined,
      windDirection,
      pressure: c.surface_pressure != null ? Math.round(c.surface_pressure * 10) / 10 : undefined,
      cloudCover,
    };
  } catch {
    return {};
  }
}

// ── Catch Storage ────────────────────────────────────────────────────────────

/**
 * Save an enhanced catch record.
 */
export async function saveCatch(catchData: Omit<EnhancedCatch, 'id'>): Promise<EnhancedCatch> {
  const catches = await getAllCatches();

  const newCatch: EnhancedCatch = {
    ...catchData,
    id: `catch_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
  };

  catches.unshift(newCatch);
  await AsyncStorage.setItem(CATCHES_KEY, JSON.stringify(catches));
  notifyCatchListeners(catches);

  // Check for personal best
  if (newCatch.weight) {
    await checkPersonalBest(newCatch);
  }

  return newCatch;
}

/**
 * Get all saved catches.
 */
export async function getAllCatches(): Promise<EnhancedCatch[]> {
  try {
    const json = await AsyncStorage.getItem(CATCHES_KEY);
    return json ? JSON.parse(json) : [];
  } catch {
    return [];
  }
}

export function subscribeToCatchUpdates(
  listener: (catches: EnhancedCatch[]) => void,
): () => void {
  catchListeners.add(listener);
  return () => {
    catchListeners.delete(listener);
  };
}

/**
 * Get catches filtered by species.
 */
export async function getCatchesBySpecies(species: string): Promise<EnhancedCatch[]> {
  const all = await getAllCatches();
  return all.filter(c => c.species.toLowerCase() === species.toLowerCase());
}

/**
 * Get catches within a date range.
 */
export async function getCatchesByDateRange(startMs: number, endMs: number): Promise<EnhancedCatch[]> {
  const all = await getAllCatches();
  return all.filter(c => c.timestamp >= startMs && c.timestamp <= endMs);
}

// ── Personal Best Tracking ───────────────────────────────────────────────────

async function checkPersonalBest(catchData: EnhancedCatch): Promise<boolean> {
  if (!catchData.weight) return false;

  const bests = await getPersonalBests();
  const existing = bests.find(b => b.species.toLowerCase() === catchData.species.toLowerCase());

  if (!existing || catchData.weight > existing.weight) {
    const newBest: PersonalBest = {
      species: catchData.species,
      weight: catchData.weight,
      length: catchData.length,
      catchId: catchData.id,
      timestamp: catchData.timestamp,
      locationName: catchData.locationName,
    };

    const updated = bests.filter(b => b.species.toLowerCase() !== catchData.species.toLowerCase());
    updated.push(newBest);
    await AsyncStorage.setItem(PB_KEY, JSON.stringify(updated));
    return true;
  }

  return false;
}

/**
 * Get all personal bests.
 */
export async function getPersonalBests(): Promise<PersonalBest[]> {
  try {
    const json = await AsyncStorage.getItem(PB_KEY);
    return json ? JSON.parse(json) : [];
  } catch {
    return [];
  }
}

// ── Statistics ───────────────────────────────────────────────────────────────

/**
 * Get aggregated stats for all species.
 */
export async function getSpeciesStats(): Promise<SpeciesStats[]> {
  const catches = await getAllCatches();
  const bests = await getPersonalBests();
  const speciesMap = new Map<string, EnhancedCatch[]>();

  for (const c of catches) {
    const key = c.species.toLowerCase();
    if (!speciesMap.has(key)) speciesMap.set(key, []);
    speciesMap.get(key)!.push(c);
  }

  const stats: SpeciesStats[] = [];

  for (const [species, speciesCatches] of speciesMap) {
    const weights = speciesCatches.filter(c => c.weight != null).map(c => c.weight!);
    const lengths = speciesCatches.filter(c => c.length != null).map(c => c.length!);

    // Most common bait
    const baitCounts = new Map<string, number>();
    for (const c of speciesCatches) {
      if (c.bait) baitCounts.set(c.bait, (baitCounts.get(c.bait) ?? 0) + 1);
    }
    const favoriteBait = baitCounts.size > 0
      ? [...baitCounts.entries()].sort((a, b) => b[1] - a[1])[0][0]
      : undefined;

    // Most common location
    const locCounts = new Map<string, number>();
    for (const c of speciesCatches) {
      if (c.locationName) locCounts.set(c.locationName, (locCounts.get(c.locationName) ?? 0) + 1);
    }
    const favoriteLocation = locCounts.size > 0
      ? [...locCounts.entries()].sort((a, b) => b[1] - a[1])[0][0]
      : undefined;

    stats.push({
      species: speciesCatches[0].species, // Use original casing
      totalCaught: speciesCatches.length,
      totalKept: speciesCatches.filter(c => !c.released).length,
      totalReleased: speciesCatches.filter(c => c.released).length,
      avgWeight: weights.length > 0 ? weights.reduce((a, b) => a + b, 0) / weights.length : undefined,
      avgLength: lengths.length > 0 ? lengths.reduce((a, b) => a + b, 0) / lengths.length : undefined,
      personalBest: bests.find(b => b.species.toLowerCase() === species),
      lastCaught: speciesCatches[0]?.timestamp,
      favoriteBait,
      favoriteLocation,
    });
  }

  return stats.sort((a, b) => b.totalCaught - a.totalCaught);
}
