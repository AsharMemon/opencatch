import Constants from 'expo-constants';

// api.opencatch.app is not resolving reliably yet, so production auth/API
// traffic needs the same droplet fallback strategy as tiles for now.
const PROD_API_URL = 'http://24.199.80.77';
// Temporary production fallback until tiles.opencatch.app DNS/TLS is live.
// Martin is currently reachable through the droplet IP reverse proxy.
const PROD_TILE_URL = 'http://24.199.80.77';

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

const useLocalDevApiServer = process.env.EXPO_PUBLIC_USE_LOCAL_API_SERVER === '1';
const defaultDevApiUrl = useLocalDevApiServer
  ? (expoDevHost ? `http://${expoDevHost}:8000` : 'http://localhost:8000')
  : PROD_API_URL;

const useLocalDevTileServer = process.env.EXPO_PUBLIC_USE_LOCAL_TILE_SERVER === '1';
const defaultDevTileUrl = useLocalDevTileServer
  ? (expoDevHost ? `http://${expoDevHost}:3000` : 'http://localhost:3000')
  : PROD_TILE_URL;

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
