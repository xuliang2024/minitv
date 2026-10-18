import { useEffect, useState } from "react";
import { getFavorites } from "../api";
import { useAuth } from "../auth/AuthProvider";
import type { Locale } from "../copy";
import { t } from "../copy";
import { IMG, thumb } from "../img";
import { IconBack, IconChevronRight } from "../icons";
import type { FavoritesPayload } from "../types";

export function MePage({ locale, onOpen }: { locale: Locale; onOpen: (id: number) => void }) {
  const { status, user, openLogin, logout } = useAuth();
  const [view, setView] = useState<"account" | "favorites">("account");
  const [total, setTotal] = useState<number | null>(null);

  useEffect(() => {
    if (status !== "user" || view !== "account") return;
    let alive = true;
    getFavorites(1, 0)
      .then((data) => {
        if (alive) setTotal(data.total);
      })
      .catch(() => {
        if (alive) setTotal(null);
      });
    return () => {
      alive = false;
    };
  }, [status, view]);

  if (view === "favorites") {
    return <FavoritesView locale={locale} onBack={() => setView("account")} onOpen={onOpen} />;
  }

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
          {user.avatar_url ? <img src={thumb(user.avatar_url, IMG.avatar)} alt="" /> : initial}
        </div>
        <h2>{user.display_name || user.email}</h2>
        {user.email && <p>{user.email}</p>}
      </div>
      <div className="me-card">
        <span>{t(locale, "coins")}</span>
        <b data-testid="me-coins">{user.coins}</b>
      </div>
      <button type="button" className="me-row" onClick={() => setView("favorites")} data-testid="me-favorites">
        <span>{t(locale, "my_favorites")}</span>
        <span className="me-row-side">
          <b>{total ?? 0}</b>
          <IconChevronRight size={14} />
        </span>
      </button>
      <button type="button" className="me-signout" onClick={logout}>
        {t(locale, "sign_out")}
      </button>
    </div>
  );
}

function FavoritesView({
  locale,
  onBack,
  onOpen,
}: {
  locale: Locale;
  onBack: () => void;
  onOpen: (id: number) => void;
}) {
  const [data, setData] = useState<FavoritesPayload | null>(null);
  const [error, setError] = useState(false);

  const load = () => {
    setError(false);
    setData(null);
    getFavorites(50, 0)
      .then(setData)
      .catch(() => setError(true));
  };

  useEffect(load, []);

  return (
    <div className="fav-page">
      <div className="fav-bar">
        <button type="button" className="icon-btn" onClick={onBack} aria-label={t(locale, "back")}>
          <IconBack size={18} />
        </button>
        <h2>{t(locale, "my_favorites")}</h2>
        {data && <span className="fav-count">{data.total}</span>}
      </div>
      {error ? (
        <div className="center" style={{ height: "50vh" }}>
          <button className="retry" onClick={load}>{t(locale, "retry")}</button>
        </div>
      ) : data === null ? (
        <div className="center" style={{ height: "50vh" }}>…</div>
      ) : data.items.length === 0 ? (
        <div className="fav-empty">
          <p>{t(locale, "favorites_empty")}</p>
          <span>{t(locale, "favorites_hint")}</span>
        </div>
      ) : (
        <div className="grid3 fav-grid">
          {data.items.map((drama) => (
            <button key={drama.id} className="card" onClick={() => onOpen(drama.id)} type="button">
              <div className="poster">
                <span className="poster-media">
                  {drama.cover_url && (
                    <img
                      className="cover"
                      src={thumb(drama.cover_url, IMG.card)}
                      alt=""
                      loading="lazy"
                      decoding="async"
                    />
                  )}
                </span>
                <div className="poster-gradient" />
              </div>
              <p className="card-title">{drama.title}</p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
