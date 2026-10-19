import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { authToken, getMe, onUnauthorized, setAuthToken, type MeProfile } from "../api";

export type AuthStatus = "loading" | "guest" | "user";

interface AuthContextValue {
  status: AuthStatus;
  user: MeProfile | null;
  loginOpen: boolean;
  openLogin: () => void;
  closeLogin: () => void;
  /** Store the session, load the profile, then replay whatever was blocked. */
  completeLogin: (token: string) => Promise<void>;
  /** Run now when signed in; otherwise ask for a sign-in and replay afterwards. */
  requireAuth: (action: () => void) => void;
  /** Re-read /v1/me after the profile or wallet changed. */
  refresh: () => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>(() => (authToken() ? "loading" : "guest"));
  const [user, setUser] = useState<MeProfile | null>(null);
  const [loginOpen, setLoginOpen] = useState(false);
  const pending = useRef<(() => void) | null>(null);

  const reset = useCallback(() => {
    setAuthToken(null);
    setUser(null);
    setStatus("guest");
    // A stored token that turned out dead: still honour the tap that needed it.
    if (pending.current) setLoginOpen(true);
  }, []);

  useEffect(() => {
    onUnauthorized(reset);
    return () => onUnauthorized(null);
  }, [reset]);

  useEffect(() => {
    if (!authToken()) {
      setStatus("guest");
      return;
    }
    let alive = true;
    getMe()
      .then((me) => {
        if (!alive) return;
        setUser(me);
        setStatus("user");
      })
      .catch(() => {
        if (alive) reset();
      });
    return () => {
      alive = false;
    };
  }, [reset]);

  // Whatever the viewer tried before signing in runs the moment the session lands.
  useEffect(() => {
    if (status !== "user") return;
    const action = pending.current;
    pending.current = null;
    if (action) action();
  }, [status]);

  const completeLogin = useCallback(async (token: string) => {
    setAuthToken(token);
    const me = await getMe();
    setUser(me);
    setLoginOpen(false);
    setStatus("user");
  }, []);

  const requireAuth = useCallback(
    (action: () => void) => {
      if (status === "user") {
        action();
        return;
      }
      pending.current = action;
      // Still verifying a stored token: hold the intent instead of flashing a sheet.
      if (status !== "loading") setLoginOpen(true);
    },
    [status],
  );

  const refresh = useCallback(async () => {
    if (!authToken()) return;
    try {
      const me = await getMe();
      setUser(me);
      setStatus("user");
    } catch {
      reset();
    }
  }, [reset]);

  const logout = useCallback(() => {
    pending.current = null;
    reset();
  }, [reset]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      loginOpen,
      openLogin: () => setLoginOpen(true),
      closeLogin: () => {
        pending.current = null;
        setLoginOpen(false);
      },
      completeLogin,
      requireAuth,
      refresh,
      logout,
    }),
    [status, user, loginOpen, completeLogin, requireAuth, refresh, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
