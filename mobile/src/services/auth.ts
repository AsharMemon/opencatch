/**
 * OpenCatch Auth Service
 *
 * Handles JWT token storage, login, register, refresh, and logout.
 * Tokens are persisted in AsyncStorage and attached to API requests.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

const STORAGE_KEYS = {
  ACCESS_TOKEN: '@opencatch/access_token',
  REFRESH_TOKEN: '@opencatch/refresh_token',
  USER_PROFILE: '@opencatch/user_profile',
} as const;

// ── Types ──────────────────────────────────────────────────────

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface UserProfile {
  user_id: number;
  email: string;
  display_name: string;
  created_at: string | null;
}

export interface AuthState {
  isAuthenticated: boolean;
  user: UserProfile | null;
  accessToken: string | null;
}

// ── API base URL (same as api.ts) ──────────────────────────────

const DEV_API_URL = 'http://localhost:8000';
const PROD_API_URL = 'https://api.castline.app';
const BASE_URL = __DEV__ ? DEV_API_URL : PROD_API_URL;

// ── Token storage ──────────────────────────────────────────────

export async function getAccessToken(): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
  } catch {
    return null;
  }
}

export async function getRefreshToken(): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(STORAGE_KEYS.REFRESH_TOKEN);
  } catch {
    return null;
  }
}

async function storeTokens(tokens: AuthTokens): Promise<void> {
  await AsyncStorage.multiSet([
    [STORAGE_KEYS.ACCESS_TOKEN, tokens.access_token],
    [STORAGE_KEYS.REFRESH_TOKEN, tokens.refresh_token],
  ]);
}

async function storeUserProfile(profile: UserProfile): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEYS.USER_PROFILE, JSON.stringify(profile));
}

export async function getStoredUserProfile(): Promise<UserProfile | null> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEYS.USER_PROFILE);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

async function clearAuth(): Promise<void> {
  await AsyncStorage.multiRemove([
    STORAGE_KEYS.ACCESS_TOKEN,
    STORAGE_KEYS.REFRESH_TOKEN,
    STORAGE_KEYS.USER_PROFILE,
  ]);
}

// ── JWT decode (minimal, no library needed) ────────────────────

function decodeJwtPayload(token: string): Record<string, any> | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(payload));
  } catch {
    return null;
  }
}

export function isTokenExpired(token: string): boolean {
  const payload = decodeJwtPayload(token);
  if (!payload || !payload.exp) return true;
  // Add 30-second buffer
  return Date.now() / 1000 >= payload.exp - 30;
}

// ── Auth API calls ─────────────────────────────────────────────

async function authRequest<T>(path: string, body: object): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: 'Unknown error' }));
    throw new AuthError(
      errorBody.detail || `Request failed (${res.status})`,
      res.status,
    );
  }

  return res.json();
}

export class AuthError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'AuthError';
    this.status = status;
  }
}

// ── Public API ─────────────────────────────────────────────────

export const auth = {
  /**
   * Register a new account and return tokens + profile.
   */
  async register(
    email: string,
    password: string,
    displayName: string,
  ): Promise<AuthState> {
    const tokens = await authRequest<AuthTokens>('/api/v1/auth/register', {
      email,
      password,
      display_name: displayName,
    });

    await storeTokens(tokens);

    // Fetch the full profile
    const profile = await auth.fetchProfile(tokens.access_token);
    await storeUserProfile(profile);

    return {
      isAuthenticated: true,
      user: profile,
      accessToken: tokens.access_token,
    };
  },

  /**
   * Login with email and password.
   */
  async login(email: string, password: string): Promise<AuthState> {
    const tokens = await authRequest<AuthTokens>('/api/v1/auth/login', {
      email,
      password,
    });

    await storeTokens(tokens);

    const profile = await auth.fetchProfile(tokens.access_token);
    await storeUserProfile(profile);

    return {
      isAuthenticated: true,
      user: profile,
      accessToken: tokens.access_token,
    };
  },

  /**
   * Refresh the access token using the stored refresh token.
   * Returns the new access token, or null if refresh fails.
   */
  async refresh(): Promise<string | null> {
    const refreshToken = await getRefreshToken();
    if (!refreshToken) return null;

    try {
      const tokens = await authRequest<AuthTokens>('/api/v1/auth/refresh', {
        refresh_token: refreshToken,
      });
      await storeTokens(tokens);
      return tokens.access_token;
    } catch {
      // Refresh token is invalid/expired — clear everything
      await clearAuth();
      return null;
    }
  },

  /**
   * Get a valid access token, refreshing if needed.
   * Returns null if the user is not authenticated.
   */
  async getValidToken(): Promise<string | null> {
    const accessToken = await getAccessToken();
    if (!accessToken) return null;

    if (!isTokenExpired(accessToken)) {
      return accessToken;
    }

    // Token expired — try refreshing
    return auth.refresh();
  },

  /**
   * Fetch the user profile from the backend.
   */
  async fetchProfile(accessToken: string): Promise<UserProfile> {
    const res = await fetch(`${BASE_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    if (!res.ok) {
      throw new AuthError('Failed to fetch profile', res.status);
    }

    return res.json();
  },

  /**
   * Check if there is a stored session and restore it.
   * Returns the auth state (authenticated or not).
   */
  async restoreSession(): Promise<AuthState> {
    const accessToken = await getAccessToken();
    if (!accessToken) {
      return { isAuthenticated: false, user: null, accessToken: null };
    }

    // Try to use the stored token
    if (!isTokenExpired(accessToken)) {
      const profile = await getStoredUserProfile();
      return {
        isAuthenticated: true,
        user: profile,
        accessToken,
      };
    }

    // Token expired — try refresh
    const newToken = await auth.refresh();
    if (newToken) {
      const profile = await getStoredUserProfile();
      return {
        isAuthenticated: true,
        user: profile,
        accessToken: newToken,
      };
    }

    return { isAuthenticated: false, user: null, accessToken: null };
  },

  /**
   * Logout — clear all stored tokens and profile.
   */
  async logout(): Promise<void> {
    await clearAuth();
  },
};
