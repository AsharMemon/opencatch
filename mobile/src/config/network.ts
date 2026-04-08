import Constants from 'expo-constants';

const PROD_API_URL = 'https://api.opencatch.app';
// Temporary production fallback until tiles.opencatch.app DNS/TLS is live.
// Martin is currently reachable directly on the droplet IP.
const PROD_TILE_URL = 'http://24.199.80.77:3000';

function normalizeBaseUrl(value?: string | null): string | null {
  const trimmed = value?.trim();
  if (!trimmed) return null;
  return trimmed.replace(/\/+$/, '');
}

function extractExpoDevHost(): string | null {
  const constants = Constants as any;
  const rawHost =
    constants.expoConfig?.hostUri ??
    constants.manifest2?.extra?.expoClient?.hostUri ??
    constants.manifest?.debuggerHost ??
    constants.manifest?.hostUri;

  if (!rawHost || typeof rawHost !== 'string') return null;

  const withoutScheme = rawHost.replace(/^https?:\/\//, '');
  const [host] = withoutScheme.split(':');
  return host || null;
}

const expoDevHost = extractExpoDevHost();

const defaultDevApiUrl = expoDevHost
  ? `http://${expoDevHost}:8000`
  : 'http://localhost:8000';

const defaultDevTileUrl = expoDevHost
  ? `http://${expoDevHost}:3000`
  : 'http://localhost:3000';

export const API_BASE_URL =
  normalizeBaseUrl(process.env.EXPO_PUBLIC_API_BASE_URL) ??
  (__DEV__ ? defaultDevApiUrl : PROD_API_URL);

export const TILE_BASE_URL =
  normalizeBaseUrl(process.env.EXPO_PUBLIC_TILE_SERVER_URL) ??
  (__DEV__ ? defaultDevTileUrl : PROD_TILE_URL);

/**
 * Whether the tile server (Martin) is deployed and reachable in production.
 * Bathymetry tiles deployed at 24.199.80.77 (tiles.opencatch.app).
 */
export const TILE_SERVER_DEPLOYED: boolean = true;
