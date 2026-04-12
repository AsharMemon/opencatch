import * as Location from 'expo-location';

export type FeatureLocationSource = 'current' | 'search' | 'map-center' | 'selected' | 'default';

export interface FeatureLocation {
  lat: number;
  lon: number;
  label: string;
  source: FeatureLocationSource;
}

function buildLabel(place?: Location.LocationGeocodedAddress | null, fallback?: string): string {
  if (!place) return fallback ?? 'Selected location';
  const parts: string[] = [];
  if (place.city) parts.push(place.city);
  if (place.region) parts.push(place.region);
  if (parts.length === 0 && place.name) parts.push(place.name);
  if (parts.length === 0 && place.district) parts.push(place.district);
  return parts.join(', ') || fallback || 'Selected location';
}

export async function reverseGeocodeLabel(
  lat: number,
  lon: number,
  fallback = 'Selected location',
): Promise<string> {
  try {
    const results = await Location.reverseGeocodeAsync({
      latitude: lat,
      longitude: lon,
    });
    return buildLabel(results[0], fallback);
  } catch {
    return fallback;
  }
}

export async function getCurrentFeatureLocation(
  fallback?: { lat: number; lon: number; label?: string },
): Promise<FeatureLocation> {
  const { status } = await Location.requestForegroundPermissionsAsync();
  if (status !== 'granted') {
    if (fallback) {
      return {
        lat: fallback.lat,
        lon: fallback.lon,
        label: fallback.label ?? 'Default area',
        source: 'default',
      };
    }
    throw new Error('Location permission denied');
  }

  const loc = await Location.getCurrentPositionAsync({
    accuracy: Location.Accuracy.Balanced,
  });
  const label = await reverseGeocodeLabel(
    loc.coords.latitude,
    loc.coords.longitude,
    'Current location',
  );

  return {
    lat: loc.coords.latitude,
    lon: loc.coords.longitude,
    label,
    source: 'current',
  };
}

export async function searchFeatureLocation(query: string): Promise<FeatureLocation> {
  const trimmed = query.trim();
  if (!trimmed) {
    throw new Error('Enter a place name');
  }

  const results = await Location.geocodeAsync(trimmed);
  const first = results[0];
  if (!first) {
    throw new Error('No matching place found');
  }

  const label = await reverseGeocodeLabel(first.latitude, first.longitude, trimmed);
  return {
    lat: first.latitude,
    lon: first.longitude,
    label,
    source: 'search',
  };
}
