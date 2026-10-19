import { useEffect, useRef, useState } from "react";
import { getJson, postJson, type UnlockResult } from "../api";
import { useAuth } from "../auth/AuthProvider";
import type { Locale } from "../copy";
import { t } from "../copy";
import { formatCount } from "../format";
import {
  IconBack,
  IconChevronUp,
  IconClose,
  IconComment,
  IconEpisodes,
  IconHeart,
  IconLock,
  IconMoreDots,
  IconQuality,
  IconShare,
  IconSpeed,
  IconStar,
} from "../icons";
import { EpisodeView } from "../player/EpisodeView";
import type { PlayEpisode, PlayPayload } from "../types";
import { CommentSheet } from "../ui/CommentSheet";
import { PremiumSheet } from "../ui/PremiumSheet";
import { toast } from "../ui/toast";
import { UnlockSheet } from "../ui/UnlockSheet";

interface Props {
  dramaId: number;
  locale: Locale;
  onBack: () => void;
  onOpenRewards: () => void;
}

const RANGE = 30;
const SPEED_OPTIONS = [0.75, 1.0, 1.25, 1.5, 2.0];

/** The drama's own page: every episode, opened at the resume point. Finishing one jumps to the next. */
export function PlayPage({ dramaId, locale, onBack, onOpenRewards }: Props) {
  const { requireAuth, user, refresh } = useAuth();
  const [payload, setPayload] = useState<PlayPayload | null>(null);
  const [error, setError] = useState(false);
  const [index, setIndex] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [sheet, setSheet] = useState<"episodes" | "synopsis" | "settings" | "comments" | null>(null);
  const [unlockTarget, setUnlockTarget] = useState<PlayEpisode | null>(null);
  const [premiumOpen, setPremiumOpen] = useState(false);
  const unlockKeys = useRef<Map<number, string>>(new Map());
  const [commentCounts, setCommentCounts] = useState<Record<number, number>>({});
  const [liked, setLiked] = useState(false);
  const [favorited, setFavorited] = useState(false);
  const [likeCount, setLikeCount] = useState(0);
  const [speed, setSpeed] = useState<number>(() => {
    const saved = localStorage.getItem("minitv_speed");
    return saved ? parseFloat(saved) : 1.0;
  });
  const [quality, setQuality] = useState<string>(() => {
    return localStorage.getItem("minitv_quality") || "Auto";
  });
  const [availableQualities, setAvailableQualities] = useState<string[]>([
    "Auto",
    "1080P",
    "720P",
    "480P",
    "360P",
  ]);
  const startY = useRef(0);

  const changeSpeed = (s: number) => {
    setSpeed(s);
    localStorage.setItem("minitv_speed", String(s));
  };

  const changeQuality = (q: string) => {
    setQuality(q);
    localStorage.setItem("minitv_quality", q);
  };

  const load = () => {
    setError(false);
    setPayload(null);
    getJson<PlayPayload>(`/v1/dramas/${dramaId}/play`)
      .then((data) => {
        const resumeId = data.resume?.episode_id;
        const start = data.items.findIndex((item) => item.id === resumeId);
        setIndex(start < 0 ? 0 : start);
        setLiked(data.drama.liked);
        setFavorited(data.drama.favorited);
        setLikeCount(data.drama.like_count);
        setPayload(data);
      })
      .catch(() => setError(true));
  };

  useEffect(load, [dramaId]);

  const openUnlock = (episode: PlayEpisode) =>
    requireAuth(() => {
      const price = episode.unlock?.coin_price ?? 0;
      // A zero price is membership-only (or a misconfigured drama): never
      // create a coin flow for it, send the viewer to the plans instead.
      if (price <= 0) {
        setPremiumOpen(true);
        return;
      }
      setUnlockTarget(episode);
    });

  const unlockKeyFor = (episodeId: number) => {
    const existing = unlockKeys.current.get(episodeId);
    if (existing) return existing;
    const created =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    unlockKeys.current.set(episodeId, created);
    return created;
  };

  const finishUnlock = (result: UnlockResult) => {
    const episodeId = result.episode_id;
    setUnlockTarget(null);
    unlockKeys.current.delete(episodeId);
    setPayload((prev) => {
      if (!prev) return prev;
      const items = prev.items.map((item) =>
        result.status === "subscription" || item.id === episodeId
          ? { ...item, locked: false, unlock: null }
          : item,
      );
      const unlocked =
        result.status === "subscription"
          ? prev.entitlement.unlocked_episode_ids
          : Array.from(new Set([...prev.entitlement.unlocked_episode_ids, episodeId]));
      return {
        ...prev,
        items,
        entitlement: {
          member: prev.entitlement.member || result.status === "subscription",
          unlocked_episode_ids: unlocked,
        },
      };
    });
    void refresh();
    if (result.status === "subscription") {
      toast(t(locale, "unlock_member_active"));
    } else if (result.status === "already_unlocked") {
      toast(t(locale, "unlock_already"));
    } else {
      toast(t(locale, "unlock_success", { balance: result.balance_after ?? 0 }));
    }
  };

  const openPremium = () => requireAuth(() => setPremiumOpen(true));

  if (error) return <div className="center"><button className="retry" onClick={load}>{t(locale, "retry")}</button></div>;
  if (!payload) return <div className="center">…</div>;
  if (payload.items.length === 0) return <div className="center">{t(locale, "empty")}</div>;

  const current = payload.items[index];
  const commentCount = commentCounts[current.id] ?? current.comment_count;
  const go = (next: number) => {
    if (next < 0 || next >= payload.items.length) return;
    setIndex(next);
    setExpanded(false);
  };
  const toggleReaction = (kind: "like" | "favorite") => {
    const active = kind === "like" ? liked : favorited;
    const next = !active;
    const apply = (value: boolean) => {
      if (kind === "like") {
        setLiked(value);
        setLikeCount((count) => Math.max(0, count + (value ? 1 : -1)));
      } else {
        setFavorited(value);
      }
    };
    // Signed out: ask for a session, then replay this exact tap.
    requireAuth(() => {
      apply(next);
      postJson("/v1/reactions", { target_type: "drama", target_id: dramaId, kind, remove: !next }).catch(() => {
        apply(!next);
        toast(t(locale, "err_reaction"));
      });
    });
  };

  return (
    <div
      className={current.locked ? "play locked-view" : "play"}
      onTouchStart={(event) => { startY.current = event.touches[0].clientY; }}
      onTouchEnd={(event) => {
        if (sheet) return;
        const delta = startY.current - event.changedTouches[0].clientY;
        if (Math.abs(delta) < 40) return;
        go(delta > 0 ? index + 1 : index - 1);
      }}
    >
      <div className="snap" style={{ transform: `translateY(-${index * 100}%)` }}>
        {payload.items.map((episode, position) => (
          <div className="slot" key={episode.id}>
            {position === index && (
              episode.locked ? (
                <LockedEpisode
                  episode={episode}
                  locale={locale}
                  signedIn={Boolean(user)}
                  balance={user?.coins ?? 0}
                  onUnlock={() => openUnlock(episode)}
                  onMember={openPremium}
                  onNeedCoins={onOpenRewards}
                />
              ) : (
                <EpisodeView
                  dramaId={dramaId}
                  episodeId={episode.id}
                  resumeSeconds={episode.progress_seconds}
                  active
                  playable
                  playbackSpeed={speed}
                  quality={quality}
                  onQualitiesChange={(levels) => {
                    if (levels && levels.length > 0) {
                      setAvailableQualities(levels);
                    }
                  }}
                  onFinished={() => go(position + 1)}
                />
              )
            )}
          </div>
        ))}
      </div>
      <div className="back">
        <button className="icon-btn back-btn" onClick={onBack} aria-label="Back">
          <IconBack size={20} />
        </button>
        <span className="back-title">{t(locale, "episode_short", { number: current.episode_number })}</span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="icon-btn setting-btn"
          onClick={() => setSheet("settings")}
          aria-label={t(locale, "playback_settings")}
        >
          <IconMoreDots size={20} />
        </button>
      </div>
      <div className="scrim" />
      <div className="rail">
        <button
          type="button"
          className={liked ? "rail-btn on" : "rail-btn"}
          onClick={() => toggleReaction("like")}
          aria-label="Like"
        >
          <span className="rail-icon"><IconHeart active={liked} size={30} /></span>
          <span className="rail-label">{formatCount(likeCount, locale)}</span>
        </button>

        <button
          type="button"
          className="rail-btn"
          onClick={() => setSheet("comments")}
          aria-label={t(locale, "comments")}
        >
          <span className="rail-icon"><IconComment size={30} /></span>
          <span className="rail-label">{commentCount > 0 ? formatCount(commentCount, locale) : t(locale, "comment")}</span>
        </button>

        <button
          type="button"
          className={favorited ? "rail-btn on" : "rail-btn"}
          onClick={() => toggleReaction("favorite")}
          aria-label={t(locale, "favorite")}
        >
          <span className="rail-icon"><IconStar active={favorited} size={30} /></span>
          <span className="rail-label">{t(locale, "favorite")}</span>
        </button>

        <button
          type="button"
          className="rail-btn"
          onClick={() => {
            if (navigator.share) {
              navigator.share({ title: payload.drama.title, url: window.location.href }).catch(() => undefined);
            } else if (navigator.clipboard) {
              navigator.clipboard.writeText(window.location.href).catch(() => undefined);
            }
          }}
          aria-label={t(locale, "share")}
        >
          <span className="rail-icon"><IconShare size={30} /></span>
          <span className="rail-label">{t(locale, "share")}</span>
        </button>
      </div>
      <div className="meta">
        <h2>{payload.drama.title}</h2>
        {payload.drama.synopsis && (
          <p className={expanded ? "syn open" : "syn"} onClick={() => setExpanded(!expanded)}>
            {t(locale, "episode_short", { number: current.episode_number })} | {payload.drama.synopsis} {expanded ? t(locale, "collapse") : t(locale, "expand")}
          </p>
        )}
        {payload.drama.tags.length > 0 && <p className="tags">{payload.drama.tags.map((tag) => `#${tag}`).join(" ")}</p>}
        <p className="ai">{t(locale, "ai_disclaimer")}</p>
      </div>
      <button className="ep-bar" onClick={() => setSheet("episodes")}>
        <IconEpisodes size={18} />
        <span style={{ flex: 1, textAlign: "left" }}>{t(locale, "all_episodes_bar", { count: payload.items.length })}</span>
        <IconChevronUp size={16} />
      </button>
      {sheet === "settings" && (
        <>
          <button className="sheet-mask" onClick={() => setSheet(null)} />
          <PlaybackSettingsSheet
            locale={locale}
            speed={speed}
            onSpeedChange={changeSpeed}
            quality={quality}
            onQualityChange={changeQuality}
            availableQualities={availableQualities}
            onClose={() => setSheet(null)}
          />
        </>
      )}
      {sheet === "comments" && (
        <>
          <button className="sheet-mask" onClick={() => setSheet(null)} />
          <CommentSheet
            episodeId={current.id}
            locale={locale}
            initialCount={commentCount}
            onCountChange={(next) => setCommentCounts((prev) => ({ ...prev, [current.id]: next }))}
            onClose={() => setSheet(null)}
          />
        </>
      )}
      {sheet && sheet !== "settings" && sheet !== "comments" && (
        <>
          <button className="sheet-mask" onClick={() => setSheet(null)} />
          <EpisodeSheet
            payload={payload}
            index={index}
            tab={sheet}
            locale={locale}
            onTab={setSheet}
            onPick={(picked) => { setSheet(null); go(picked); }}
          />
        </>
      )}
      {unlockTarget && (
        <UnlockSheet
          locale={locale}
          episode={unlockTarget}
          price={unlockTarget.unlock?.coin_price ?? 0}
          balance={user?.coins ?? 0}
          idempotencyKey={unlockKeyFor(unlockTarget.id)}
          onClose={() => setUnlockTarget(null)}
          onDone={finishUnlock}
          onInsufficient={() => void refresh()}
          onNeedCoins={() => { setUnlockTarget(null); void refresh(); onOpenRewards(); }}
        />
      )}
      {premiumOpen && (
        <PremiumSheet
          locale={locale}
          membership={user?.membership}
          onChanged={() => { void refresh(); load(); }}
          onClose={() => setPremiumOpen(false)}
        />
      )}
    </div>
  );
}

function PlaybackSettingsSheet({
  locale,
  speed,
  onSpeedChange,
  quality,
  onQualityChange,
  availableQualities,
  onClose,
}: {
  locale: Locale;
  speed: number;
  onSpeedChange: (s: number) => void;
  quality: string;
  onQualityChange: (q: string) => void;
  availableQualities: string[];
  onClose: () => void;
}) {
  return (
    <div className="sheet settings-sheet" role="dialog" aria-modal="true" aria-label={t(locale, "playback_settings")}>
      <div className="sheet-handle" />
      <header className="settings-header">
        <h3>{t(locale, "playback_settings")}</h3>
        <button type="button" className="close-btn" onClick={onClose} aria-label={t(locale, "close")}>
          <IconClose size={18} />
        </button>
      </header>

      <div className="settings-body">
        {/* 倍速调节 */}
        <div className="settings-group">
          <div className="group-title">
            <span className="group-icon"><IconSpeed size={18} /></span>
            <span className="group-label">{t(locale, "speed")}</span>
            <span className="group-current">{speed === 1.0 ? "1.0x" : `${speed}x`}</span>
          </div>
          <div className="pills-grid">
            {SPEED_OPTIONS.map((val) => {
              const active = Math.abs(val - speed) < 0.01;
              return (
                <button
                  key={val}
                  type="button"
                  className={active ? "setting-pill on" : "setting-pill"}
                  onClick={() => onSpeedChange(val)}
                >
                  {val === 1.0 ? "1.0x" : `${val}x`}
                </button>
              );
            })}
          </div>
        </div>

        {/* 分辨率/清晰度调节 */}
        <div className="settings-group">
          <div className="group-title">
            <span className="group-icon"><IconQuality size={18} /></span>
            <span className="group-label">{t(locale, "quality")}</span>
            <span className="group-current">{quality === "Auto" ? t(locale, "auto_quality") : quality}</span>
          </div>
          <div className="pills-grid qualities">
            {availableQualities.map((item) => {
              const active = item.toLowerCase() === quality.toLowerCase();
              return (
                <button
                  key={item}
                  type="button"
                  className={active ? "setting-pill on" : "setting-pill"}
                  onClick={() => onQualityChange(item)}
                >
                  {item === "Auto" ? t(locale, "auto_quality") : item}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function LockedEpisode({
  episode,
  locale,
  signedIn,
  balance,
  onUnlock,
  onMember,
  onNeedCoins,
}: {
  episode: PlayEpisode;
  locale: Locale;
  signedIn: boolean;
  balance: number;
  onUnlock: () => void;
  onMember: () => void;
  onNeedCoins: () => void;
}) {
  const price = episode.unlock?.coin_price ?? 0;
  const short = Math.max(0, price - balance);
  const coinOnly = price > 0;

  return (
    <div className="lock">
      <div className="lock-panel">
        <div className="lock-badge"><IconLock size={30} /></div>
        <h2>{t(locale, "unlock_title", { number: episode.episode_number })}</h2>
        <p className="lock-sub">{t(locale, "unlock_sub")}</p>

        {coinOnly && (
          <div className="lock-price">
            <b>{t(locale, "unlock_price", { price })}</b>
            <span>{t(locale, "unlock_per_episode")}</span>
          </div>
        )}
        {signedIn && (
          <p className="lock-balance">{t(locale, "unlock_balance", { balance })}</p>
        )}

        {coinOnly && (
          !signedIn ? (
            <button type="button" className="lock-cta" onClick={onUnlock} data-testid="lock-login">
              {t(locale, "unlock_login_cta")}
            </button>
          ) : short > 0 ? (
            <button type="button" className="lock-cta ghost" onClick={onNeedCoins} data-testid="lock-need-coins">
              {t(locale, "unlock_short_cta", { short })}
            </button>
          ) : (
            <button type="button" className="lock-cta" onClick={onUnlock} data-testid="lock-unlock">
              {t(locale, "unlock_cta", { price })}
            </button>
          )
        )}

        <button
          type="button"
          className={coinOnly ? "lock-member" : "lock-cta"}
          onClick={onMember}
          data-testid="lock-member"
        >
          {coinOnly ? t(locale, "unlock_member_cta") : t(locale, "premium_strip_cta")}
        </button>
      </div>
    </div>
  );
}

function EpisodeSheet({
  payload, index, tab, locale, onTab, onPick,
}: {
  payload: PlayPayload;
  index: number;
  tab: "episodes" | "synopsis";
  locale: Locale;
  onTab: (tab: "episodes" | "synopsis") => void;
  onPick: (index: number) => void;
}) {
  const ranges: number[] = [];
  for (let start = 0; start < payload.items.length; start += RANGE) ranges.push(start);
  const [range, setRange] = useState(Math.floor(index / RANGE) * RANGE);
  const slice = payload.items.slice(range, range + RANGE);

  return (
    <div className="sheet">
      <header>
        <button className={tab === "synopsis" ? "on" : ""} onClick={() => onTab("synopsis")}>{t(locale, "synopsis")}</button>
        <button className={tab === "episodes" ? "on" : ""} onClick={() => onTab("episodes")}>{t(locale, "episodes_tab")}</button>
      </header>
      <div className="body">
        {tab === "synopsis" ? (
          <p className="syn-full">{payload.drama.synopsis || t(locale, "empty")}</p>
        ) : (
          <>
            <div className="ranges">
              {ranges.map((start) => (
                <button key={start} className={start === range ? "on" : ""} onClick={() => setRange(start)}>
                  {start + 1}-{Math.min(start + RANGE, payload.items.length)}
                </button>
              ))}
            </div>
            <div className="ep-grid">
              {slice.map((episode) => {
                const position = payload.items.indexOf(episode);
                return (
                  <button
                    key={episode.id}
                    className={`${position === index ? "on" : ""} ${episode.locked ? "locked" : ""}`}
                    onClick={() => onPick(position)}
                  >
                    {episode.episode_number}
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
