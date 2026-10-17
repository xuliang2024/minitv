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
  if (!response.ok) throw new ApiError(response.status, `HTTP ${response.status}`);
  return response.json();
}

export function getJson<T>(path: string): Promise<T> {
  return send("GET", path) as Promise<T>;
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return send("POST", path, body) as Promise<T>;
}
