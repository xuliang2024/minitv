import { useCallback, useEffect, useRef, useState } from "react";
import { getJson, postJson } from "../api";
import { useAuth } from "../auth/AuthProvider";
import type { Locale } from "../copy";
import { t } from "../copy";
import { formatCount } from "../format";
import { IMG, thumb } from "../img";
import { toast } from "../ui/toast";
import { IconComment, IconEpisodes, IconHeart, IconShare } from "../icons";
import { EpisodeView } from "../player/EpisodeView";
import type { FeedItem, FeedPage } from "../types";

const PAGE_SIZE = 5;
const PRELOAD_REMAINING = 2;

interface Props {
  locale: Locale;
  onOpenDrama: (dramaId: number) => void;
}

/** One vertical page is one drama. Swiping up means the next show, not the next episode. */
export function ForYouPage({ locale, onOpenDrama }: Props) {
  const [items, setItems] = useState<FeedItem[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [index, setIndex] = useState(0);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const loadingRef = useRef(false);
  const startY = useRef(0);

  const cursorRef = useRef<string | null>(null);
  const hasMoreRef = useRef(true);

  const loadMore = useCallback(async (reset = false) => {
    if (loadingRef.current) return;
    if (!reset && !hasMoreRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    setError(false);
    try {
      const query = new URLSearchParams({ limit: String(PAGE_SIZE) });
      if (!reset && cursorRef.current) query.set("cursor", cursorRef.current);
      const page = await getJson<FeedPage>(`/v1/feed?${query}`);
      const nextCursor = page.has_more ? page.next_cursor : null;
      cursorRef.current = nextCursor;
      hasMoreRef.current = Boolean(nextCursor);
      setHasMore(Boolean(nextCursor));
      setItems((prev) => {
        const base = reset ? [] : prev;
        const known = new Set(base.map((item) => item.item_id));
        return [...base, ...page.items.filter((item) => !known.has(item.item_id))];
      });
      if (reset) setIndex(0);
    } catch {
      setError(true);
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadMore(true);
  }, [loadMore]);

  useEffect(() => {
    if (hasMore && items.length - index - 1 <= PRELOAD_REMAINING) loadMore(false);
  }, [index, items.length, hasMore, loadMore]);

  const go = (next: number) => {
    if (next < 0 || next >= items.length) return;
    setIndex(next);
  };

  if (error && items.length === 0) {
    return <div className="center"><button className="retry" onClick={() => loadMore(true)}>{t(locale, "retry")}</button></div>;
  }
  if (items.length === 0) return <div className="center">{loading ? "…" : t(locale, "empty")}</div>;

  return (
    <div
      className="feed"
      onTouchStart={(event) => { startY.current = event.touches[0].clientY; }}
      onTouchEnd={(event) => {
        const delta = startY.current - event.changedTouches[0].clientY;
        if (Math.abs(delta) < 40) return;
        go(delta > 0 ? index + 1 : index - 1);
      }}
      onWheel={(event) => {
        if (Math.abs(event.deltaY) < 30) return;
        go(event.deltaY > 0 ? index + 1 : index - 1);
      }}
    >
      <div className="snap" style={{ transform: `translateY(-${index * 100}%)` }}>
        {items.map((item, position) => (
          <FeedSlot
            key={item.item_id}
            item={item}
            active={position === index}
            locale={locale}
            onOpenDrama={onOpenDrama}
          />
        ))}
      </div>
    </div>
  );
}

function FeedSlot({
  item, active, locale, onOpenDrama,
}: {
  item: FeedItem;
  active: boolean;
  locale: Locale;
  onOpenDrama: (id: number) => void;
}) {
  const [finished, setFinished] = useState(false);
  const [liked, setLiked] = useState(item.drama.liked);
  const [likeCount, setLikeCount] = useState(item.drama.like_count);
  const { requireAuth } = useAuth();
  const drama = item.drama;
  const episode = item.episode;
  const tags = drama.tags.map((tag) => `#${tag}`).join(" ");

  useEffect(() => {
    setFinished(false);
    setLiked(item.drama.liked);
    setLikeCount(item.drama.like_count);
  }, [item.item_id, item.drama.liked, item.drama.like_count]);

  const toggleLike = () => {
    const next = !liked;
    const apply = (value: boolean) => {
      setLiked(value);
      setLikeCount((count) => Math.max(0, count + (value ? 1 : -1)));
    };
    requireAuth(() => {
      apply(next);
      postJson("/v1/reactions", {
        target_type: "drama",
        target_id: drama.id,
        kind: "like",
        remove: !next,
      }).catch(() => {
        apply(!next);
        toast(t(locale, "err_reaction"));
      });
    });
  };

  return (
    <div className="slot">
      <EpisodeView
        dramaId={drama.id}
        episodeId={episode.id}
        resumeSeconds={item.resume_seconds}
        active={active}
        playable
        onFinished={() => setFinished(true)}
      />
      <div className="scrim" />
      <div className="rail">
        <div className="avatar" onClick={() => onOpenDrama(drama.id)} role="button" tabIndex={0}>
          {drama.cover_url && (
            <img
              className="cover"
              src={thumb(drama.cover_url, IMG.avatar)}
              alt=""
              loading="lazy"
              decoding="async"
            />
          )}
        </div>
        <button
          type="button"
          className={liked ? "rail-btn on" : "rail-btn"}
          onClick={toggleLike}
          aria-label="Like"
        >
          <span className="rail-icon"><IconHeart active={liked} size={30} /></span>
          <span className="rail-label">{formatCount(likeCount, locale)}</span>
        </button>
        <button
          type="button"
          className="rail-btn"
          onClick={() => onOpenDrama(drama.id)}
          aria-label="Episodes"
        >
          <span className="rail-icon"><IconComment size={30} /></span>
          <span className="rail-label">{drama.published_episode_count || t(locale, "select")}</span>
        </button>
        <button
          type="button"
          className="rail-btn"
          onClick={() => {
            if (navigator.share) {
              navigator.share({ title: drama.title, url: window.location.href }).catch(() => undefined);
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
      {finished && (
        <button className="next-cta" onClick={() => onOpenDrama(drama.id)}>
          <IconEpisodes size={16} />
          <span>{t(locale, "all_episodes", { count: drama.published_episode_count })}</span>
        </button>
      )}
      <div className="meta-row">
        <div className="meta">
          <h2>{drama.title}</h2>
          <div className="line">
            <span className="ep-chip">{t(locale, "episode_chip", { number: episode.episode_number })}</span>
            {tags && <span className="meta-tags">{tags}</span>}
          </div>
        </div>
        <button className="select-pill" onClick={() => onOpenDrama(drama.id)}>
          <IconEpisodes size={15} />
          <span>{t(locale, "select")}</span>
        </button>
      </div>
    </div>
  );
}
