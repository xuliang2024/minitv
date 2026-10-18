import { useAuth } from "../auth/AuthProvider";
import type { Locale } from "../copy";
import { t } from "../copy";

export function MePage({ locale }: { locale: Locale }) {
  const { status, user, openLogin, logout } = useAuth();

  if (status === "loading") return <div className="center">…</div>;

  if (user === null) {
    return (
      <div className="me">
        <div className="me-hero">
          <div className="me-avatar guest">M</div>
          <h2>{t(locale, "guest_title")}</h2>
          <p>{t(locale, "guest_body")}</p>
        </div>
        <button type="button" className="auth-primary me-cta" onClick={openLogin}>
          {t(locale, "sign_in")}
        </button>
      </div>
    );
  }

  const initial = (user.display_name || user.email || "M").slice(0, 1).toUpperCase();

  return (
    <div className="me">
      <div className="me-hero">
        <div className="me-avatar">
          {user.avatar_url ? <img src={user.avatar_url} alt="" /> : initial}
        </div>
        <h2>{user.display_name || user.email}</h2>
        {user.email && <p>{user.email}</p>}
      </div>
      <div className="me-card">
        <span>{t(locale, "coins")}</span>
        <b data-testid="me-coins">{user.coins}</b>
      </div>
      <button type="button" className="me-signout" onClick={logout}>
        {t(locale, "sign_out")}
      </button>
    </div>
  );
}
