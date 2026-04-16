export const API_BASE_URL = 'http://localhost:8000/api/v1';
export const STORAGE_KEY_USER = 'auth_user';
export const STORAGE_KEY_TOKENS = 'auth_tokens';

export interface User {
  id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  full_name: string | null;
  profile_picture: string | null;
  auth_provider: string;
  is_active: boolean;
  is_verified: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface Tokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface AuthResponse {
  user: User;
  tokens: Tokens;
  message: string;
}

interface RefreshTokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export async function getGoogleAuthUrl(redirectUri: string): Promise<{ url: string; redirect_uri: string }> {
  const response = await fetch(
    `${API_BASE_URL}/auth/google/url?redirect_uri=${encodeURIComponent(redirectUri)}`,
    {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
      },
    }
  );

  if (!response.ok) {
    throw new Error('Failed to get Google auth URL');
  }

  return response.json();
}

export async function exchangeGoogleCode(code: string, redirectUri: string): Promise<AuthResponse> {
  const response = await fetch(`${API_BASE_URL}/auth/google/callback`, {
    method: 'POST',
    headers: {
      'Accept': 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      code,
      redirect_uri: redirectUri,
    }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to authenticate with Google');
  }

  return response.json();
}

export async function refreshAuthTokens(refreshToken: string): Promise<RefreshTokenResponse> {
  const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      refresh_token: refreshToken,
    }),
  });

  if (!response.ok) {
    throw new Error('Failed to refresh auth token');
  }

  return response.json();
}
