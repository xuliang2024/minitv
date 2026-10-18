import type { FavoritesPayload } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

const DEVICE_KEY = "minitv_h5_device";

export function deviceId(): string {
  const existing = localStorage.getItem(DEVICE_KEY);
  if (existing) return existing;
  const created = `h5-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  localStorage.setItem(DEVICE_KEY, created);
  return created;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let token: string | null = localStorage.getItem("minitv_h5_token");

export function authToken(): string | null {
  return token;
}

export function setAuthToken(value: string | null) {
  token = value;
  if (value) localStorage.setItem("minitv_h5_token", value);
  else localStorage.removeItem("minitv_h5_token");
}

let unauthorizedHandler: (() => void) | null = null;

/** The session died (expired token, revoked). Let the auth layer reset to guest. */
export function onUnauthorized(handler: (() => void) | null) {
  unauthorizedHandler = handler;
}

async function send(method: string, path: string, body?: unknown): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-Device-Id": deviceId(),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network");
  }
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const data = (await response.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* body was not JSON */
    }
    // A failed sign-in is a wrong code, not a dead session.
    if (response.status === 401 && !path.startsWith("/v1/auth/") && unauthorizedHandler) {
      unauthorizedHandler();
    }
    throw new ApiError(response.status, detail);
  }
  return response.json();
}

export function getJson<T>(path: string): Promise<T> {
  return send("GET", path) as Promise<T>;
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return send("POST", path, body) as Promise<T>;
}

export interface AuthProviders {
  email: { enabled: boolean };
  google: { enabled: boolean; client_id: string | null };
}

export interface MeProfile {
  id: number;
  public_id: string;
  email: string | null;
  display_name: string;
  avatar_url: string;
  coins: number;
}

export interface Session {
  token: string;
  user_id: number;
  public_id: string;
  created?: boolean;
}

/** Which sign-in methods this deployment actually supports. */
export function getAuthProviders(): Promise<AuthProviders> {
  return getJson<AuthProviders>("/v1/auth/providers");
}

export function sendEmailCode(email: string): Promise<{ sent: boolean }> {
  return postJson<{ sent: boolean }>("/v1/auth/email/code", { email, purpose: "login" });
}

export function authenticateEmail(email: string, code: string): Promise<Session> {
  return postJson<Session>("/v1/auth/email/authenticate", { email, code });
}

export function loginWithGoogle(idToken: string): Promise<Session> {
  return postJson<Session>("/v1/auth/google", { id_token: idToken });
}

export function getMe(): Promise<MeProfile> {
  return getJson<MeProfile>("/v1/me");
}

export function getFavorites(limit = 50, offset = 0): Promise<FavoritesPayload> {
  return getJson<FavoritesPayload>(`/v1/me/favorites?limit=${limit}&offset=${offset}`);
}
