import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  checkin as postCheckin,
  getFavorites,
  getHistory,
  getLedger,
  getLikes,
  getMessages,
  readMessages,
  updateMe,
  type CheckinResult,
} from "../api";
import { useAuth } from "../auth/AuthProvider";
import type { Locale } from "../copy";
import { t } from "../copy";
import {
  IconBell,
  IconBookmark,
  IconCalendarCheck,
  IconChevronRight,
  IconCoin,
  IconCopy,
  IconGear,
  IconHistory,
  IconReceipt,
  IconStar,
} from "../icons";
import { IMG, thumb } from "../img";
import type {
  DramaLite,
  FavoritesPayload,
  HistoryItem,
  InboxItem,
  LedgerEntry,
} from "../types";
import { Sheet } from "../ui/Sheet";
import { PremiumSheet } from "../ui/PremiumSheet";
import { OrdersSheet } from "../ui/OrdersSheet";
import { toast } from "../ui/toast";

type MeTab = "history" | "favorites" | "likes";
type SheetId = "edit" | "ledger" | "messages" | "settings" | "premium" | "orders" | "signout";
type LoadState = "idle" | "loading" | "ready" | "error";

const CHECKIN_KEY = "minitv_h5_checkin";

function localDay(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function initialOf(value: string): string {
  return (value.trim().slice(0, 1) || "M").toUpperCase();
}

export function MePage({
  locale,
  onLocale,
  onOpen,
}: {
  locale: Locale;
  onLocale: (locale: Locale) => void;
  onOpen: (id: number) => void;
}) {
  const { status, user, openLogin, requireAuth, refresh, logout } = useAuth();
  const [tab, setTab] = useState<MeTab>("history");
  const [sheet, setSheet] = useState<SheetId | null>(null);
  const [history, setHistory] = useState<HistoryItem[] | null>(null);
  const [favorites, setFavorites] = useState<FavoritesPayload | null>(null);
  const [likes, setLikes] = useState<DramaLite[] | null>(null);
  const [states, setStates] = useState<Record<MeTab, LoadState>>({
    history: "idle",
    favorites: "idle",
    likes: "idle",
  });
  const [unread, setUnread] = useState(0);
  const [checkedToday, setCheckedToday] = useState(() => localStorage.getItem(CHECKIN_KEY) === localDay());
  const contentRef = useRef<HTMLDivElement | null>(null);

  const setTabState = useCallback((target: MeTab, value: LoadState) => {
    setStates((prev) => ({ ...prev, [target]: value }));
  }, []);

  const loadHistory = useCallback(async () => {
    setTabState("history", "loading");
    try {
      const data = await getHistory();
      setHistory(data.items);
      setTabState("history", "ready");
    } catch {
      setTabState("history", "error");
    }
  }, [setTabState]);

  const loadFavorites = useCallback(async () => {
    setTabState("favorites", "loading");
    try {
      const data = await getFavorites(50, 0);
      setFavorites(data);
      setTabState("favorites", "ready");
    } catch {
      setTabState("favorites", "error");
    }
  }, [setTabState]);

  const loadLikes = useCallback(async () => {
    setTabState("likes", "loading");
    try {
      const data = await getLikes();
      setLikes(data.items.flatMap((item) => (item.drama ? [item.drama] : [])));
      setTabState("likes", "ready");
    } catch {
      setTabState("likes", "error");
    }
  }, [setTabState]);

  useEffect(() => {
    if (status !== "user") {
      setHistory(null);
      setFavorites(null);
      setLikes(null);
      setUnread(0);
      setStates({ history: "idle", favorites: "idle", likes: "idle" });
      return;
    }
    void loadHistory();
    void loadFavorites();
    let alive = true;
    getMessages()
      .then((data) => {
        if (alive) setUnread(data.unread);
      })
      .catch(() => {
        /* the badge is optional */
      });
    return () => {
      alive = false;
    };
  }, [status, loadHistory, loadFavorites]);

  useEffect(() => {
    if (status === "user" && tab === "likes" && states.likes === "idle") void loadLikes();
  }, [status, tab, states.likes, loadLikes]);

  const jumpTo = (next: MeTab) => {
    setTab(next);
    contentRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const openTab = (next: MeTab) => {
    requireAuth(() => jumpTo(next));
  };

  const copyId = () => {
    if (!user) return;
    if (!navigator.clipboard) {
      toast(t(locale, "me_copy_fail"));
      return;
    }
    navigator.clipboard
      .writeText(user.public_id)
      .then(() => toast(t(locale, "me_copied")))
      .catch(() => toast(t(locale, "me_copy_fail")));
  };

  const checkin = () => {
    requireAuth(() => {
      if (checkedToday) {
        toast(t(locale, "me_checkin_already"));
        return;
      }
      postCheckin()
        .then((result: CheckinResult) => {
          setCheckedToday(true);
          localStorage.setItem(CHECKIN_KEY, localDay());
          toast(
            result.replayed
              ? t(locale, "me_checkin_already")
              : t(locale, "me_checkin_ok", { amount: result.amount }),
          );
          void refresh();
        })
        .catch(() => toast(t(locale, "me_checkin_fail")));
    });
  };

  const historyDramas = useMemo(() => {
    const seen = new Map<number, HistoryItem>();
    for (const item of history ?? []) {
      if (!seen.has(item.drama.id)) seen.set(item.drama.id, item);
    }
    return [...seen.values()];
  }, [history]);

  const handleRead = useCallback(() => setUnread(0), []);

  if (status === "loading") return <div className="center">…</div>;

  const guest = status !== "user" || user === null;
  const name = user?.display_name?.trim() || user?.email || "";
  const favoritesTotal = favorites?.total ?? 0;

  return (
    <div className="me-page">
      <div className="me-top">
        <span className="me-top-label">{t(locale, "nav.me")}</span>
        <div className="me-top-actions">
          <button
            type="button"
            className="icon-btn"
            aria-label={t(locale, "me_settings")}
            onClick={() => setSheet("settings")}
            data-testid="me-settings"
          >
            <IconGear size={18} />
          </button>
          <button
            type="button"
            className="icon-btn me-bell"
            aria-label={t(locale, "me_messages")}
            onClick={() => requireAuth(() => setSheet("messages"))}
            data-testid="me-messages"
          >
            <IconBell size={18} />
            {unread > 0 && <span className="me-dot">{unread > 9 ? "9+" : unread}</span>}
          </button>
        </div>
      </div>

      <section className="me-hero">
        <div className={guest ? "me-avatar guest" : "me-avatar"}>
          <span className="me-avatar-inner">
            {!guest && user?.avatar_url ? (
              <img src={thumb(user.avatar_url, IMG.avatar)} alt="" />
            ) : (
              initialOf(guest ? "M" : name)
            )}
          </span>
        </div>
        <div className="me-hero-main">
          <h2>{guest ? t(locale, "guest_title") : name}</h2>
          {guest ? (
            <p className="me-guest-line">{t(locale, "guest_body")}</p>
          ) : (
            <button type="button" className="me-uid" onClick={copyId} data-testid="me-uid">
              <span>{t(locale, "me_id")} · {user.public_id}</span>
              <IconCopy size={12} />
            </button>
          )}
        </div>
      </section>

      {guest ? (
        <button type="button" className="auth-primary me-cta" onClick={openLogin}>
          {t(locale, "sign_in")}
        </button>
      ) : (
        <div className="me-stats">
          <div className="me-stat">
            <b>{user.following_count ?? 0}</b>
            <span>{t(locale, "me_following")}</span>
          </div>
          <div className="me-stat">
            <b>{user.follower_count ?? 0}</b>
            <span>{t(locale, "me_followers")}</span>
          </div>
          <div className="me-stat">
            <b>{user.received_likes ?? 0}</b>
            <span>{t(locale, "me_likes_received")}</span>
          </div>
          <button type="button" className="me-edit" onClick={() => setSheet("edit")}>
            {t(locale, "me_edit_profile")}
          </button>
        </div>
      )}

      {user?.membership?.active ? (
        <button
          type="button"
          className="me-premium active"
          onClick={() => setSheet("premium")}
          data-testid="me-premium-active"
        >
          <span className="me-premium-crown">★</span>
          <span className="me-premium-copy">
            <b>{t(locale, "premium_active")}</b>
            <i>
              {t(locale, user.membership.canceling ? "premium_ends_on" : "premium_active_until", {
                date: user.membership.until
                  ? new Date(user.membership.until).toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US", {
                      month: "short",
                      day: "numeric",
                      year: "numeric",
                    })
                  : "—",
              })}
            </i>
          </span>
        </button>
      ) : (
        <button
          type="button"
          className="me-premium"
          onClick={() => requireAuth(() => setSheet("premium"))}
          data-testid="me-premium"
        >
          <span className="me-premium-crown">★</span>
          <span className="me-premium-copy">
            <b>{t(locale, "premium_strip_title")}</b>
            <i>{t(locale, "premium_strip_sub")}</i>
          </span>
          <span className="me-premium-go">{t(locale, "premium_strip_cta")}</span>
        </button>
      )}

      <div className="me-quick">
        <button type="button" className="me-quick-item" onClick={() => openTab("history")}>
          <span className="me-quick-icon">
            <IconHistory size={21} />
          </span>
          <b>{t(locale, "me_history")}</b>
          <i>
            {historyDramas.length === 1
              ? t(locale, "me_history_sub_one")
              : historyDramas.length > 1
                ? t(locale, "me_history_sub_count", { count: historyDramas.length })
                : t(locale, "me_history_sub")}
          </i>
        </button>
        <button
          type="button"
          className="me-quick-item"
          onClick={() => openTab("favorites")}
          data-testid="me-favorites"
        >
          <span className="me-quick-icon">
            <IconBookmark size={21} />
          </span>
          <b>{t(locale, "me_list")}</b>
          <i>{t(locale, "me_list_sub", { count: favoritesTotal })}</i>
        </button>
        <button
          type="button"
          className="me-quick-item"
          onClick={() => requireAuth(() => setSheet("ledger"))}
        >
          <span className="me-quick-icon gold">
            <IconCoin size={21} />
          </span>
          <b>{t(locale, "me_coins_title")}</b>
          <i>
            <b className="me-quick-num" data-testid="me-coins">{user?.coins ?? 0}</b>{" "}
            {t(locale, "coins")}
          </i>
        </button>
        <button type="button" className="me-quick-item" onClick={checkin} data-testid="me-checkin">
          <span className={checkedToday ? "me-quick-icon done" : "me-quick-icon"}>
            <IconCalendarCheck size={21} />
          </span>
          <b>{t(locale, "me_checkin")}</b>
          <i>{checkedToday ? t(locale, "me_checkin_done") : t(locale, "me_checkin_go")}</i>
        </button>
      </div>

      <button
        type="button"
        className="me-link-row"
        onClick={() => requireAuth(() => setSheet("orders"))}
        data-testid="me-orders"
      >
        <span className="me-link-icon">
          <IconReceipt size={18} />
        </span>
        <span className="me-link-label">{t(locale, "me_orders")}</span>
        <span className="me-link-hint">{t(locale, "me_orders_hint")}</span>
      </button>

      <div className="me-tabs" ref={contentRef}>
        <div className="me-tabs-list" role="tablist" aria-label={t(locale, "nav.me")}>
          {(["history", "favorites", "likes"] as MeTab[]).map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={tab === item}
              className={tab === item ? "me-tab on" : "me-tab"}
              onClick={() => (guest ? openTab(item) : jumpTo(item))}
              data-testid={`me-tab-${item}`}
            >
              {t(locale, `me_tab_${item}`)}
            </button>
          ))}
        </div>
      </div>

      <div className="me-content">
        {guest ? (
          <div className="me-empty">
            <span className="me-empty-icon">
              <IconStar size={22} />
            </span>
            <p>{t(locale, "sign_in_prompt")}</p>
            <span>{t(locale, "sign_in_hint")}</span>
            <button type="button" className="me-empty-cta" onClick={openLogin}>
              {t(locale, "sign_in")}
            </button>
          </div>
        ) : tab === "history" ? (
          <HistoryGrid
            items={historyDramas}
            state={states.history}
            locale={locale}
            onRetry={() => void loadHistory()}
            onOpen={onOpen}
          />
        ) : tab === "favorites" ? (
          <FavoritesGrid
            data={favorites}
            state={states.favorites}
            locale={locale}
            onRetry={() => void loadFavorites()}
            onOpen={onOpen}
          />
        ) : (
          <LikesGrid
            items={likes}
            state={states.likes}
            locale={locale}
            onRetry={() => void loadLikes()}
            onOpen={onOpen}
          />
        )}
      </div>

      {sheet === "edit" && user && (
        <EditProfileSheet
          locale={locale}
          user={user}
          onClose={() => setSheet(null)}
          onSaved={() => void refresh()}
        />
      )}
      {sheet === "ledger" && (
        <LedgerSheet locale={locale} balance={user?.coins ?? 0} onClose={() => setSheet(null)} />
      )}
      {sheet === "messages" && (
        <MessagesSheet locale={locale} onClose={() => setSheet(null)} onRead={handleRead} />
      )}
      {sheet === "orders" && <OrdersSheet locale={locale} onClose={() => setSheet(null)} />}
      {sheet === "premium" && (
        <PremiumSheet
          locale={locale}
          membership={user?.membership}
          onChanged={() => void refresh()}
          onClose={() => setSheet(null)}
        />
      )}
      {sheet === "settings" && (
        <SettingsSheet
          locale={locale}
          signedIn={!guest}
          email={user?.email ?? null}
          onLocale={onLocale}
          onEdit={() => setSheet("edit")}
          onClose={() => setSheet(null)}
          onSignOut={() => setSheet("signout")}
        />
      )}
      {sheet === "signout" && (
        <SignOutSheet
          locale={locale}
          email={user?.email ?? null}
          onCancel={() => setSheet("settings")}
          onConfirm={() => {
            setSheet(null);
            logout();
          }}
        />
      )}
    </div>
  );
}

/* ---- grids ---- */

function Tile({
  drama,
  badge,
  meta,
  onOpen,
}: {
  drama: DramaLite;
  badge?: string;
  meta?: string;
  onOpen: (id: number) => void;
}) {
  return (
    <button type="button" className="card me-tile" onClick={() => onOpen(drama.id)}>
      <div className="poster">
        <span className="poster-media">
          {drama.cover_url && (
            <img src={thumb(drama.cover_url, IMG.card)} alt="" loading="lazy" decoding="async" />
          )}
        </span>
        {badge && <span className="me-tile-badge">{badge}</span>}
        <div className="poster-gradient" />
      </div>
      <p className="card-title">{drama.title}</p>
      {meta && <span className="me-tile-meta">{meta}</span>}
    </button>
  );
}

function LoadingGrid() {
  return (
    <div className="grid3 me-grid">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} className="me-skel">
          <div className="me-skel-poster" />
          <div className="me-skel-line" />
        </div>
      ))}
    </div>
  );
}

function GridError({ locale, onRetry }: { locale: Locale; onRetry: () => void }) {
  return (
    <div className="me-empty">
      <p>{t(locale, "retry")}</p>
      <button type="button" className="me-empty-cta" onClick={onRetry}>
        {t(locale, "retry")}
      </button>
    </div>
  );
}

function Empty({
  locale,
  titleKey,
  hintKey,
  icon,
}: {
  locale: Locale;
  titleKey: string;
  hintKey: string;
  icon: ReactNode;
}) {
  return (
    <div className="me-empty">
      <span className="me-empty-icon">{icon}</span>
      <p>{t(locale, titleKey)}</p>
      <span>{t(locale, hintKey)}</span>
    </div>
  );
}

function HistoryGrid({
  items,
  state,
  locale,
  onRetry,
  onOpen,
}: {
  items: HistoryItem[];
  state: LoadState;
  locale: Locale;
  onRetry: () => void;
  onOpen: (id: number) => void;
}) {
  if (state === "loading" || state === "idle") return <LoadingGrid />;
  if (state === "error") return <GridError locale={locale} onRetry={onRetry} />;
  if (items.length === 0) {
    return (
      <Empty
        locale={locale}
        titleKey="me_history_empty"
        hintKey="me_history_hint"
        icon={<IconHistory size={22} />}
      />
    );
  }
  return (
    <div className="grid3 me-grid">
      {items.map((item) => (
        <Tile
          key={item.drama.id}
          drama={item.drama}
          badge={t(locale, "episode_short", { number: item.episode_number })}
          meta={
            item.drama.episode_count > 0
              ? t(locale, "me_ep_progress", {
                  ep: item.episode_number,
                  total: item.drama.episode_count,
                })
              : undefined
          }
          onOpen={onOpen}
        />
      ))}
    </div>
  );
}

function FavoritesGrid({
  data,
  state,
  locale,
  onRetry,
  onOpen,
}: {
  data: FavoritesPayload | null;
  state: LoadState;
  locale: Locale;
  onRetry: () => void;
  onOpen: (id: number) => void;
}) {
  if (state === "loading" || state === "idle") return <LoadingGrid />;
  if (state === "error") return <GridError locale={locale} onRetry={onRetry} />;
  if (!data || data.items.length === 0) {
    return (
      <Empty
        locale={locale}
        titleKey="favorites_empty"
        hintKey="favorites_hint"
        icon={<IconBookmark size={22} />}
      />
    );
  }
  return (
    <div className="grid3 me-grid fav-grid">
      {data.items.map((drama) => (
        <Tile
          key={drama.id}
          drama={drama}
          meta={drama.episode_count > 0 ? t(locale, "me_ep_total", { count: drama.episode_count }) : undefined}
          onOpen={onOpen}
        />
      ))}
    </div>
  );
}

function LikesGrid({
  items,
  state,
  locale,
  onRetry,
  onOpen,
}: {
  items: DramaLite[] | null;
  state: LoadState;
  locale: Locale;
  onRetry: () => void;
  onOpen: (id: number) => void;
}) {
  if (state === "loading" || state === "idle") return <LoadingGrid />;
  if (state === "error") return <GridError locale={locale} onRetry={onRetry} />;
  if (!items || items.length === 0) {
    return (
      <Empty
        locale={locale}
        titleKey="me_likes_empty"
        hintKey="me_likes_hint"
        icon={<IconStar size={22} />}
      />
    );
  }
  return (
    <div className="grid3 me-grid">
      {items.map((drama) => (
        <Tile
          key={drama.id}
          drama={drama}
          meta={drama.episode_count > 0 ? t(locale, "me_ep_total", { count: drama.episode_count }) : undefined}
          onOpen={onOpen}
        />
      ))}
    </div>
  );
}

/* ---- sheets ---- */

function EditProfileSheet({
  locale,
  user,
  onClose,
  onSaved,
}: {
  locale: Locale;
  user: { display_name: string; email: string | null; bio: string };
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(user.display_name || "");
  const [bio, setBio] = useState(user.bio || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    const value = name.trim();
    if (!value) {
      setError(t(locale, "me_name_empty"));
      return;
    }
    setBusy(true);
    setError(null);
    updateMe({ display_name: value, bio: bio.trim() })
      .then(() => {
        onSaved();
        toast(t(locale, "me_saved"));
        onClose();
      })
      .catch(() => setError(t(locale, "err_generic")))
      .finally(() => setBusy(false));
  };

  return (
    <Sheet title={t(locale, "me_edit_profile")} onClose={onClose} testId="edit-profile-sheet">
      <label className="me-field">
        <span>{t(locale, "me_name")}</span>
        <input
          className="auth-input"
          value={name}
          maxLength={24}
          disabled={busy}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className="me-field">
        <span>{t(locale, "me_bio")}</span>
        <textarea
          className="auth-input me-textarea"
          value={bio}
          maxLength={80}
          placeholder={t(locale, "me_bio_placeholder")}
          disabled={busy}
          onChange={(event) => setBio(event.target.value)}
        />
      </label>
      {user.email && (
        <div className="me-field">
          <span>{t(locale, "email_label")}</span>
          <p className="me-readonly">{user.email}</p>
        </div>
      )}
      {error && <p className="auth-error">{error}</p>}
      <button type="button" className="auth-primary" onClick={save} disabled={busy}>
        {busy ? t(locale, "me_saving") : t(locale, "me_save")}
      </button>
    </Sheet>
  );
}

const LEDGER_LABEL: Record<string, string> = {
  checkin: "me_ledger_checkin",
  ad_reward: "me_ledger_ad",
  ad: "me_ledger_ad",
  unlock: "me_ledger_unlock",
  purchase: "me_ledger_purchase",
  topup: "me_ledger_purchase",
  signup: "me_ledger_signup",
  register_reward: "me_ledger_signup",
  bind_email: "me_ledger_bind",
  bind_facebook: "me_ledger_bind",
  adjustment: "me_ledger_adjust",
  refund: "me_ledger_refund",
};

function LedgerSheet({
  locale,
  balance,
  onClose,
}: {
  locale: Locale;
  balance: number;
  onClose: () => void;
}) {
  const [items, setItems] = useState<LedgerEntry[] | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(() => {
    setError(false);
    setItems(null);
    getLedger()
      .then((data) => setItems(data.items))
      .catch(() => setError(true));
  }, []);

  useEffect(load, [load]);

  return (
    <Sheet title={t(locale, "me_ledger")} onClose={onClose} testId="ledger-sheet">
      <div className="me-balance">
        <span>{t(locale, "me_balance")}</span>
        <b>
          <IconCoin size={18} />
          {balance}
        </b>
      </div>
      {error ? (
        <div className="me-empty small">
          <p>{t(locale, "err_generic")}</p>
          <button type="button" className="me-empty-cta" onClick={load}>
            {t(locale, "retry")}
          </button>
        </div>
      ) : items === null ? (
        <div className="me-sheet-loading">…</div>
      ) : items.length === 0 ? (
        <div className="me-empty small">
          <p>{t(locale, "me_ledger_empty")}</p>
        </div>
      ) : (
        <ul className="me-ledger">
          {items.map((entry, index) => (
            <li key={`${entry.created_at}-${index}`}>
              <span className={entry.amount > 0 ? "me-ledger-icon in" : "me-ledger-icon"}>
                <IconCoin size={17} />
              </span>
              <span className="me-ledger-main">
                <b>{t(locale, LEDGER_LABEL[entry.biz_type] ?? entry.biz_type)}</b>
                <i>{formatWhen(entry.created_at, locale)}</i>
              </span>
              <span className={entry.amount > 0 ? "me-ledger-amount in" : "me-ledger-amount"}>
                {entry.amount > 0 ? `+${entry.amount}` : entry.amount}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Sheet>
  );
}

function formatWhen(value: string, locale: Locale): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(locale === "zh" ? "zh-CN" : "en-US", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function MessagesSheet({
  locale,
  onClose,
  onRead,
}: {
  locale: Locale;
  onClose: () => void;
  onRead: () => void;
}) {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    getMessages()
      .then((data) => {
        if (!alive) return;
        setItems(data.items);
        if (data.unread > 0) {
          readMessages()
            .then(() => onRead())
            .catch(() => {
              /* leaving the badge is better than breaking the sheet */
            });
        }
      })
      .catch(() => {
        if (alive) setError(true);
      });
    return () => {
      alive = false;
    };
  }, [onRead]);

  return (
    <Sheet title={t(locale, "me_messages")} onClose={onClose} testId="messages-sheet">
      {error ? (
        <div className="me-empty small">
          <p>{t(locale, "err_generic")}</p>
        </div>
      ) : items === null ? (
        <div className="me-sheet-loading">…</div>
      ) : items.length === 0 ? (
        <div className="me-empty small">
          <span className="me-empty-icon">
            <IconBell size={22} />
          </span>
          <p>{t(locale, "me_messages_empty")}</p>
          <span>{t(locale, "me_messages_hint")}</span>
        </div>
      ) : (
        <ul className="me-inbox">
          {items.map((item) => (
            <li key={item.id}>
              {!item.read && <span className="me-inbox-dot" />}
              <b>{item.title}</b>
              <p>{item.body}</p>
            </li>
          ))}
        </ul>
      )}
    </Sheet>
  );
}

function SettingsSheet({
  locale,
  signedIn,
  email,
  onLocale,
  onEdit,
  onClose,
  onSignOut,
}: {
  locale: Locale;
  signedIn: boolean;
  email: string | null;
  onLocale: (locale: Locale) => void;
  onEdit: () => void;
  onClose: () => void;
  onSignOut: () => void;
}) {
  return (
    <Sheet title={t(locale, "me_settings")} onClose={onClose} testId="settings-sheet">
      {signedIn && (
        <section className="me-set-group">
          <h4>{t(locale, "me_set_account")}</h4>
          <div className="me-set-list">
            {email && (
              <div className="me-set-item">
                <span className="me-set-label">{t(locale, "email_label")}</span>
                <span className="me-set-value">{email}</span>
              </div>
            )}
            <button
              type="button"
              className="me-set-item"
              onClick={onEdit}
              data-testid="settings-edit-profile"
            >
              <span className="me-set-label">{t(locale, "me_set_edit")}</span>
              <span className="me-set-chev">
                <IconChevronRight size={14} />
              </span>
            </button>
          </div>
        </section>
      )}

      <section className="me-set-group">
        <h4>{t(locale, "me_set_prefs")}</h4>
        <div className="me-set-list">
          <div className="me-set-item">
            <span className="me-set-label">{t(locale, "me_language")}</span>
            <div className="me-seg">
              <button
                type="button"
                className={locale === "en" ? "on" : ""}
                onClick={() => onLocale("en")}
              >
                English
              </button>
              <button
                type="button"
                className={locale === "zh" ? "on" : ""}
                onClick={() => onLocale("zh")}
              >
                中文
              </button>
            </div>
          </div>
        </div>
      </section>

      <section className="me-set-group">
        <h4>{t(locale, "me_set_about")}</h4>
        <div className="me-set-list">
          <div className="me-set-item">
            <span className="me-set-label">{t(locale, "me_set_version")}</span>
            <span className="me-set-value">MiniTV · H5</span>
          </div>
        </div>
      </section>

      {signedIn && (
        <button
          type="button"
          className="me-signout"
          onClick={onSignOut}
          data-testid="settings-sign-out"
        >
          {t(locale, "sign_out")}
        </button>
      )}
    </Sheet>
  );
}

function SignOutSheet({
  locale,
  email,
  onCancel,
  onConfirm,
}: {
  locale: Locale;
  email: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Sheet title={t(locale, "sign_out_title")} onClose={onCancel} testId="signout-sheet">
      <p className="me-signout-body">{t(locale, "sign_out_body")}</p>
      {email && <p className="me-signout-email">{email}</p>}
      <button
        type="button"
        className="auth-primary me-signout-confirm"
        onClick={onConfirm}
        data-testid="signout-confirm"
      >
        {t(locale, "sign_out")}
      </button>
      <button type="button" className="me-signout-cancel" onClick={onCancel}>
        {t(locale, "sign_out_cancel")}
      </button>
    </Sheet>
  );
}
