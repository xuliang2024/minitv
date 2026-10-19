import type {
  CommentCreateResult,
  CommentsPayload,
  FavoritesPayload,
  HistoryPayload,
  LedgerPayload,
  LikesPayload,
  MessagesPayload,
} from "./types";

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
  bio: string;
  coins: number;
  following_count: number;
  follower_count: number;
  received_likes: number;
  reminder_opt_in: boolean;
  membership?: {
    active: boolean;
    plan_code: string | null;
    until: string | null;
    canceling: boolean;
    billing_mode?: "subscription" | "one_time" | null;
    subscription_until?: string | null;
    pass_until?: string | null;
  };
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

export function getHistory(): Promise<HistoryPayload> {
  return getJson<HistoryPayload>("/v1/me/history");
}

export function getLikes(): Promise<LikesPayload> {
  return getJson<LikesPayload>("/v1/me/lists/like");
}

export function getLedger(): Promise<LedgerPayload> {
  return getJson<LedgerPayload>("/v1/me/ledger");
}

export function getMessages(): Promise<MessagesPayload> {
  return getJson<MessagesPayload>("/v1/me/messages");
}

export function readMessages(): Promise<{ unread: number }> {
  return postJson<{ unread: number }>("/v1/me/messages/read", {});
}

export interface CheckinResult {
  streak: number;
  amount: number;
  replayed: boolean;
  balance: number;
}

export function checkin(): Promise<CheckinResult> {
  return postJson<CheckinResult>("/v1/me/checkin", {});
}

export interface DailyTaskItem {
  code: string;
  name: string;
  description: string;
  metric: "like" | "favorite" | "complete";
  target: number;
  progress: number;
  amount: number;
  claimed: boolean;
}

export interface DailyTasksPayload {
  today: string;
  balance: number;
  checkin: { checked_today: boolean; streak: number; amount: number };
  items: DailyTaskItem[];
}

export interface TaskClaimResult {
  code: string;
  amount: number;
  balance: number;
  replayed: boolean;
  progress: number;
}

/** Daily task centre: progress, amounts and today's check-in state. */
export function getTasks(): Promise<DailyTasksPayload> {
  return getJson<DailyTasksPayload>("/v1/me/tasks");
}

/** Same catalogue without a session: amounts and targets, progress zeroed. */
export function getPublicTasks(): Promise<DailyTasksPayload> {
  return getJson<DailyTasksPayload>("/v1/tasks");
}

export function claimTask(code: string): Promise<TaskClaimResult> {
  return postJson<TaskClaimResult>(`/v1/me/tasks/${code}/claim`, {});
}

export function updateMe(body: { display_name?: string; bio?: string }): Promise<{ ok: boolean }> {
  return send("PATCH", "/v1/me", body) as Promise<{ ok: boolean }>;
}

export interface Plan {
  code: string;
  name_en: string;
  name_zh: string;
  price_cents: number;
  currency: string;
  version: number;
  version_id: number;
  benefits: { items?: string[]; best?: boolean };
  billing_mode?: "subscription" | "one_time";
}

export interface PlansPayload {
  items: Plan[];
  billing_mode?: "subscription" | "one_time";
}

export interface CheckoutResult {
  url: string;
  session_id: string;
  order_no: string;
}

/** Weekly / monthly / yearly plans, priced by the backend. */
export function getPlans(): Promise<PlansPayload> {
  return getJson<PlansPayload>("/v1/plans");
}

/** Create a Stripe Checkout session and send the browser to it. */
export function startCheckout(planCode: string): Promise<CheckoutResult> {
  return postJson<CheckoutResult>("/v1/billing/checkout", { plan_code: planCode });
}

/** Stop renewing; access stays until the end of the paid period. */
export function cancelSubscription(): Promise<{ ok: boolean }> {
  return postJson<{ ok: boolean }>("/v1/billing/cancel", {});
}

export interface OrderItem {
  order_no: string;
  plan_code: string;
  plan_name_en: string;
  plan_name_zh: string;
  amount_cents: number;
  currency: string;
  billing_mode: "subscription" | "one_time";
  status: "created" | "paid" | "failed" | "refunded";
  paid_at: string | null;
  created_at: string;
}

export interface OrdersPayload {
  items: OrderItem[];
  has_more: boolean;
}

/** Purchase history shown in the Me page. */
export function getOrders(limit = 20, offset = 0): Promise<OrdersPayload> {
  return getJson<OrdersPayload>(`/v1/me/orders?limit=${limit}&offset=${offset}`);
}


export function listComments(episodeId: number, cursor: number | null = null, limit = 20) {
  const query = new URLSearchParams({ limit: String(limit) });
  if (cursor) query.set("cursor", String(cursor));
  return getJson<CommentsPayload>(`/v1/episodes/${episodeId}/comments?${query}`);
}

export function createComment(episodeId: number, body: string) {
  return postJson<CommentCreateResult>(`/v1/episodes/${episodeId}/comments`, { body });
}
