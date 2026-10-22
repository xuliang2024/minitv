const TOKEN_KEY = "minitv_admin_token";
/** Why the last session ended; the login page turns it into a hint. */
const LOGOUT_REASON_KEY = "minitv_admin_logout_reason";

/** Production builds set VITE_API_BASE; local Vite dev leaves it empty and uses the proxy. */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/**
 * 后端把「凭据已经不能用了」拆成两类状态码：
 * - 401：没带凭据 / 会话过期或被撤销（invalid_session、invalid_key…）
 * - 403：凭据本身有效但账号被禁用、密钥被吊销/过期
 * 两类的正确处理都一样——清掉本地 token 回登录页，否则页面只会一直弹红条、
 * 数据全是空的，用户以为「后台坏了」。
 */
const DEAD_CREDENTIAL_CODES = new Set([
  "invalid_session",
  "missing_credential",
  "invalid_key",
  "key_revoked",
  "key_expired",
  "account_disabled",
]);

/** True when the response means "this browser is no longer signed in". */
export function isDeadCredential(status: number, detail: any): boolean {
  if (status === 401) return true;
  const code = typeof detail?.code === "string" ? detail.code : null;
  return status === 403 && code !== null && DEAD_CREDENTIAL_CODES.has(code);
}

export function takeLogoutReason(): string | null {
  try {
    const reason = sessionStorage.getItem(LOGOUT_REASON_KEY);
    if (reason !== null) sessionStorage.removeItem(LOGOUT_REASON_KEY);
    return reason;
  } catch {
    return null;
  }
}

let reauthInFlight = false;

/**
 * Drop the local token and hand the browser back to the login page.
 *
 * A full reload (not a router push) is deliberate: `App` reads the token while
 * rendering, so the shell only disappears on a fresh document. `redirecting`
 * keeps a burst of parallel 401s from queueing several navigations.
 */
export function forceReauth(reason = "expired"): void {
  clearToken();
  try {
    sessionStorage.setItem(LOGOUT_REASON_KEY, reason);
  } catch {
    /* private mode: the hint is nice-to-have, the redirect is not */
  }
  // Already on the login screen: clearing the token is all that is needed, and
  // the form must keep working (a failed login also answers 401).
  if (window.location.pathname.startsWith("/login")) return;
  if (reauthInFlight) return;
  reauthInFlight = true;
  const here = `${window.location.pathname}${window.location.search}`;
  const query = reason === "manual" ? "" : "expired=1&";
  window.location.replace(`/login?${query}next=${encodeURIComponent(here)}`);
}

export class ApiError extends Error {
  status: number;
  detail: any;
  constructor(status: number, detail: any) {
    super(`API error ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

/** Pull a human-readable message out of an ApiError, fetch error, or unknown. */
export function errorText(e: any): string {
  const detail = e?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    if (typeof detail.message === "string") return detail.message;
    return JSON.stringify(detail);
  }
  return e?.message ?? String(e);
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  // FormData bodies must keep the browser-generated multipart boundary, so we
  // only force a JSON content-type for regular payloads.
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { "Content-Type": "application/json" }),
    ...((options.headers as Record<string, string>) ?? {}),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    let detail: any = null;
    try {
      detail = await res.json();
    } catch {
      /* non-JSON error body */
    }
    const payload = detail?.detail ?? detail;
    // 401 之外，"账号被禁用 / 密钥被吊销" 也是 403：继续留在页面上只会得到
    // 一排看不懂的红色 toast，必须同样清 token 回登录页。
    // 登录接口自己的 401 是「密码错了」，不是会话失效，不走这条路。
    const isLoginCall = path.includes("/auth/login");
    if (token && !isLoginCall && isDeadCredential(res.status, payload)) {
      forceReauth(typeof payload?.code === "string" ? payload.code : "expired");
    }
    throw new ApiError(res.status, payload);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}
