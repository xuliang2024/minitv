import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { createComment, listComments, postJson } from "../api";
import { useAuth } from "../auth/AuthProvider";
import type { Locale } from "../copy";
import { t } from "../copy";
import { formatCount } from "../format";
import { IMG, thumb } from "../img";
import { IconClose, IconHeart } from "../icons";
import type { CommentItem } from "../types";
import { toast } from "./toast";

interface Props {
  episodeId: number;
  locale: Locale;
  initialCount?: number;
  onCountChange?: (total: number) => void;
  onClose: () => void;
}

const PAGE_SIZE = 20;
const MAX_LENGTH = 500;

function timeAgo(iso: string | null, locale: Locale): string {
  if (!iso) return "";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return t(locale, "just_now");
  if (seconds < 3600) return t(locale, "minutes_ago", { n: Math.floor(seconds / 60) });
  if (seconds < 86400) return t(locale, "hours_ago", { n: Math.floor(seconds / 3600) });
  if (seconds < 604800) return t(locale, "days_ago", { n: Math.floor(seconds / 86400) });
  return new Date(iso).toLocaleDateString();
}

/** One thread per episode: opening a different episode remounts the sheet. */
export function CommentSheet({ episodeId, locale, initialCount = 0, onCountChange, onClose }: Props) {
  const { requireAuth } = useAuth();
  const [items, setItems] = useState<CommentItem[]>([]);
  const [total, setTotal] = useState(initialCount);
  const [cursor, setCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [text, setText] = useState("");
  const [posting, setPosting] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  const publish = (next: number) => {
    setTotal(next);
    onCountChange?.(next);
  };

  const fetchPage = async (reset: boolean) => {
    if (reset) setLoading(true);
    else setLoadingMore(true);
    try {
      const page = await listComments(episodeId, reset ? null : cursor, PAGE_SIZE);
      setItems((prev) => (reset ? page.items : [...prev, ...page.items]));
      setCursor(page.next_cursor);
      publish(page.total);
    } catch {
      toast(t(locale, "err_comments_load"));
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  };

  useEffect(() => {
    setItems([]);
    setCursor(null);
    setLoading(true);
    void fetchPage(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episodeId]);

  const submit = () => {
    const body = text.trim();
    if (!body || posting) return;
    requireAuth(() => {
      void (async () => {
        setPosting(true);
        try {
          const result = await createComment(episodeId, body);
          setText("");
          if (result.status === "approved" && result.item) {
            setItems((prev) => [result.item as CommentItem, ...prev]);
            publish(total + 1);
            listRef.current?.scrollTo({ top: 0, behavior: "smooth" });
            toast(t(locale, "comment_posted"));
          } else if (result.status === "deleted") {
            toast(t(locale, "comment_blocked"));
          } else {
            toast(t(locale, "comment_pending"));
          }
        } catch (error: any) {
          toast(
            error?.status === 429
              ? t(locale, "comment_too_fast")
              : t(locale, "err_comment"),
          );
        } finally {
          setPosting(false);
        }
      })();
    });
  };

  const toggleLike = (item: CommentItem) => {
    const next = !item.liked;
    requireAuth(() => {
      setItems((prev) =>
        prev.map((row) =>
          row.id === item.id
            ? { ...row, liked: next, like_count: Math.max(0, row.like_count + (next ? 1 : -1)) }
            : row,
        ),
      );
      postJson("/v1/reactions", {
        target_type: "comment",
        target_id: item.id,
        kind: "like",
        remove: !next,
      }).catch(() => {
        setItems((prev) =>
          prev.map((row) =>
            row.id === item.id
              ? { ...row, liked: !next, like_count: Math.max(0, row.like_count + (next ? -1 : 1)) }
              : row,
          ),
        );
        toast(t(locale, "err_reaction"));
      });
    });
  };

  const content = (
    <>
      <button className="sheet-mask comment-mask" onClick={onClose} aria-label={t(locale, "close")} />
      <section
        className="sheet comment-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={t(locale, "comments")}
        onTouchStart={(event) => event.stopPropagation()}
        onTouchEnd={(event) => event.stopPropagation()}
        onWheel={(event) => event.stopPropagation()}
      >
        <div className="sheet-handle" />
        <header className="comment-header">
          <h3>
            {t(locale, "comments")}
            {total > 0 && <span className="count">{formatCount(total, locale)}</span>}
          </h3>
          <button className="comment-close" onClick={onClose} aria-label={t(locale, "close")}>
            <IconClose size={18} />
          </button>
        </header>

        <div className="comment-list" ref={listRef}>
          {loading && items.length === 0 && <div className="comment-empty">…</div>}
          {!loading && items.length === 0 && (
            <div className="comment-empty">{t(locale, "comments_empty")}</div>
          )}
          {items.map((item) => (
            <article className="comment-item" key={item.id}>
              <div className="comment-avatar">
                {item.user.avatar_url ? (
                  <img src={thumb(item.user.avatar_url, IMG.avatar)} alt="" loading="lazy" />
                ) : (
                  (item.user.display_name || "?").slice(0, 1).toUpperCase()
                )}
              </div>
              <div className="comment-main">
                <div className="comment-head">
                  <b>{item.user.display_name}</b>
                  <span>{timeAgo(item.created_at, locale)}</span>
                  {item.mine && <span className="comment-you">{t(locale, "comment_you")}</span>}
                </div>
                <p>{item.body}</p>
              </div>
              <button
                className={item.liked ? "comment-like on" : "comment-like"}
                onClick={() => toggleLike(item)}
                aria-label="Like comment"
              >
                <IconHeart size={16} active={item.liked} />
                {item.like_count > 0 && <span>{formatCount(item.like_count, locale)}</span>}
              </button>
            </article>
          ))}
          {cursor !== null && (
            <button
              className="comment-more"
              onClick={() => void fetchPage(false)}
              disabled={loadingMore}
            >
              {loadingMore ? "…" : t(locale, "comments_more")}
            </button>
          )}
        </div>

        <form
          className="comment-composer"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <input
            value={text}
            onChange={(event) => setText(event.target.value.slice(0, MAX_LENGTH))}
            placeholder={t(locale, "comment_placeholder")}
            aria-label={t(locale, "comment_placeholder")}
            enterKeyHint="send"
          />
          <button type="submit" className={text.trim() ? "ready" : ""} disabled={!text.trim() || posting}>
            {posting ? t(locale, "sending") : t(locale, "send")}
          </button>
        </form>
      </section>
    </>
  );

  // Portal above the tab bar (z 10); an in-tree sheet would be painted under it.
  const host = typeof document === "undefined" ? null : document.getElementById("app");
  return host ? createPortal(content, host) : content;
}
