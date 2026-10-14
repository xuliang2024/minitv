import { useEffect, useRef, useState } from "react";

import {
  API_BASE,
  authorizePlayback,
  fetchPreviewDrama,
  PlaybackAuth,
  PreviewDramaDetail,
  PreviewEpisode,
} from "./api";
import { LockGlyph, PlayGlyph, PosterImage, formatDuration, formatPlays } from "./ui";

type Notice = "" | "locked" | "failed";

/** Fire-and-forget playback telemetry (also drives the public play counter). */
function reportEvent(kind: "start" | "complete", episodeId: number) {
  fetch(`${API_BASE}/v1/playback/${kind}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ episode_id: episodeId, position_seconds: 0 }),
  }).catch(() => {});
}

/** One selectable resolution, derived from the live HLS manifest. */
type QualityOption = {
  /** hls.js level index (indices are per media session, not stable across episodes). */
  index: number;
  /** Menu label, e.g. "360p". */
  label: string;
  /** Pixel height of the short side — used for ordering and verification. */
  shortSide: number;
};

/**
 * Menu label for one manifest level. Bunny names rendition directories after
 * the tier ("…/360p/video.m3u8"), so prefer that; fall back to the short side
 * (the dimension players call "360p" for portrait and landscape alike).
 */
function qualityLabel(url: string, width: number, height: number): string {
  const named = /([^/]+)\/video\.m3u8(?:\?|$)/.exec(url);
  if (named && /^\d+p$/i.test(named[1])) return named[1].toLowerCase();
  const shortSide = Math.min(width, height);
  return shortSide > 0 ? `${shortSide}p` : "HD";
}

/** Quality menu options from the parsed manifest, best first. A single-level manifest gets no menu. */
function qualityOptions(hls: import("hls.js").default): QualityOption[] {
  if (hls.levels.length < 2) return [];
  return hls.levels
    .map((level, index) => ({
      index,
      label: qualityLabel(level.url[0] ?? "", level.width, level.height),
      shortSide: Math.min(level.width || 0, level.height || 0),
    }))
    .sort((a, b) => b.shortSide - a.shortSide);
}

/** Sliders glyph for the quality button. */
function QualityGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M4 8h9M19 8h1M4 16h1M11 16h9" />
      <circle cx="16" cy="8" r="2.4" />
      <circle cx="8" cy="16" r="2.4" />
    </svg>
  );
}

/**
 * Web trial player. Free episodes get a short-lived signed URL from
 * /v1/playback and stream through hls.js; locked episodes never request one -
 * clicking them (or reaching one after the last free episode) opens the
 * download-the-app overlay. Players running on hls.js (anywhere MSE exists)
 * also get a manual quality menu built from the manifest's levels; the
 * no-MSE native-HLS fallback (older iOS Safari) relies on the platform
 * controls instead.
 */
export default function WatchPage({ dramaId }: { dramaId: number }) {
  const [detail, setDetail] = useState<PreviewDramaDetail | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<PreviewEpisode | null>(null);
  const [auth, setAuth] = useState<PlaybackAuth | null>(null);
  const [notice, setNotice] = useState<Notice>("");
  const [busy, setBusy] = useState(false);
  const [needsTap, setNeedsTap] = useState(false);
  const [qualities, setQualities] = useState<QualityOption[]>([]);
  /** User pick: -1 = Auto (adaptive), otherwise an hls.js level index. */
  const [selectedQuality, setSelectedQuality] = useState(-1);
  /** Level currently being played, for the "Auto · 360p" readout. */
  const [activeQuality, setActiveQuality] = useState(-1);
  const [qualityOpen, setQualityOpen] = useState(false);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const hlsRef = useRef<import("hls.js").default | null>(null);
  const qualityRef = useRef<HTMLDivElement | null>(null);
  const retryRef = useRef(0);
  const startedRef = useRef<Set<number>>(new Set());

  // Close the quality menu on outside taps (the video element keeps its own
  // click-to-pause behavior untouched).
  useEffect(() => {
    if (!qualityOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!qualityRef.current?.contains(event.target as Node)) setQualityOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [qualityOpen]);

  const chooseQuality = (index: number) => {
    setSelectedQuality(index);
    setQualityOpen(false);
    const hls = hlsRef.current;
    if (!hls) return;
    if (index === -1) {
      // Back to adaptive: restore the size cap so ABR stays bandwidth-friendly.
      hls.capLevelToPlayerSize = true;
      hls.currentLevel = -1;
    } else {
      // Switch immediately, then lift the size cap while a manual level is
      // active (capping only has meaning for adaptive Auto).
      hls.currentLevel = index;
      hls.capLevelToPlayerSize = false;
    }
  };

  useEffect(() => {
    let alive = true;
    setDetail(null);
    setError("");
    setSelected(null);
    setAuth(null);
    setNotice("");
    fetchPreviewDrama(dramaId)
      .then((data) => {
        if (!alive) return;
        setDetail(data);
        setSelected(data.items.find((episode) => episode.free) ?? data.items[0] ?? null);
      })
      .catch(() => {
        if (alive) setError("This show is not available for free preview right now.");
      });
    return () => {
      alive = false;
    };
  }, [dramaId]);

  const play = (episode: PreviewEpisode) => {
    // Re-clicking the episode that is already streaming should not restart it.
    if (episode.free && episode.id === selected?.id && auth?.url) return;
    if (episode.id !== selected?.id) retryRef.current = 0;
    setNotice("");
    setNeedsTap(false);
    setSelected(episode);
    if (!episode.free) {
      setAuth(null);
      setNotice("locked");
      return;
    }
    setBusy(true);
    authorizePlayback(dramaId, episode.id)
      .then((result) => {
        if (!result.allowed || !result.url) {
          setAuth(null);
          setNotice("locked");
          return;
        }
        setAuth(result);
      })
      .catch(() => {
        setAuth(null);
        setNotice("failed");
      })
      .finally(() => setBusy(false));
  };

  // Latest-state refs so the hls listeners (created once per media session)
  // never read stale episode/bookmark state.
  const playRef = useRef(play);
  const selectedRef = useRef(selected);
  const detailRef = useRef(detail);
  playRef.current = play;
  selectedRef.current = selected;
  detailRef.current = detail;

  const advance = () => {
    const current = selectedRef.current;
    const items = detailRef.current?.items ?? [];
    const index = items.findIndex((episode) => episode.id === current?.id);
    const next = index >= 0 ? items[index + 1] : undefined;
    if (!next) return;
    if (next.free) {
      playRef.current(next);
    } else {
      // The next episode is locked: stop and route the viewer to the app.
      setSelected(next);
      setAuth(null);
      setNotice("locked");
    }
  };
  const advanceRef = useRef(advance);
  advanceRef.current = advance;

  useEffect(() => {
    const video = videoRef.current;
    const src = auth?.url;
    if (!video || !src) return;
    let cancelled = false;
    let hls: import("hls.js").default | null = null;
    const episodeId = auth?.episode_id;

    // Every episode starts on Auto with a fresh level list.
    setQualities([]);
    setSelectedQuality(-1);
    setActiveQuality(-1);
    setQualityOpen(false);

    const startPlayback = () => {
      setNeedsTap(false);
      video.play().catch(() => setNeedsTap(true));
    };
    const handleEnded = () => {
      if (episodeId) reportEvent("complete", episodeId);
      advanceRef.current();
    };
    const handlePlaying = () => {
      setNeedsTap(false);
      // One "start" per episode per page visit (pause/resume re-fires "playing").
      if (episodeId && !startedRef.current.has(episodeId)) {
        startedRef.current.add(episodeId);
        reportEvent("start", episodeId);
      }
    };
    const handleLoadedMetadata = () => startPlayback();
    video.addEventListener("ended", handleEnded);
    video.addEventListener("playing", handlePlaying);

    // hls.js whenever MSE is available (Chrome, Firefox, desktop Safari, iOS
    // 17.1+ via ManagedMediaSource): the manifest's levels power the quality
    // menu. Only browsers without MSE (older iOS Safari) fall back to native
    // HLS, whose platform controls carry their own variant menu.
    const nativeHls = video.canPlayType("application/vnd.apple.mpegurl") !== "";
    const mseAvailable = "MediaSource" in window || "ManagedMediaSource" in window;
    const playNatively = () => {
      video.src = src;
      video.addEventListener("loadedmetadata", handleLoadedMetadata, { once: true });
    };
    if (nativeHls && !mseAvailable) {
      playNatively();
    } else {
      import("hls.js").then(({ default: Hls }) => {
        if (cancelled) return;
        if (!Hls.isSupported()) {
          if (nativeHls) playNatively();
          else {
            setAuth(null);
            setNotice("failed");
          }
          return;
        }
        const instance = new Hls({ enableWorker: true, capLevelToPlayerSize: true });
        hls = instance;
        hlsRef.current = instance;
        instance.loadSource(src);
        instance.attachMedia(video);
        instance.on(Hls.Events.MANIFEST_PARSED, () => {
          setQualities(qualityOptions(instance));
          startPlayback();
        });
        instance.on(Hls.Events.LEVEL_SWITCHED, (_event, data) => setActiveQuality(data.level));
        instance.on(Hls.Events.ERROR, (_event, data) => {
          if (!data.fatal) return;
          if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
            hls?.recoverMediaError();
            return;
          }
          // Signed URLs expire after 5 minutes; re-authorize once and reload.
          if (data.type === Hls.ErrorTypes.NETWORK_ERROR && retryRef.current < 1 && selectedRef.current?.free) {
            retryRef.current += 1;
            playRef.current(selectedRef.current);
            return;
          }
          setAuth(null);
          setNotice("failed");
        });
      });
    }

    return () => {
      cancelled = true;
      video.removeEventListener("ended", handleEnded);
      video.removeEventListener("playing", handlePlaying);
      video.removeEventListener("loadedmetadata", handleLoadedMetadata);
      if (hlsRef.current === hls) hlsRef.current = null;
      hls?.destroy();
    };
  }, [auth?.url, auth?.episode_id]);

  if (error) {
    return (
      <main className="watch-page">
        <div className="shell">
          <a className="back-link" href="#preview">
            ← Back to free preview
          </a>
          <div className="empty-state watch-empty">
            <p>{error}</p>
            <p>
              Paid shows and locked episodes live in the MiniTV app.{" "}
              <a className="text-link" href="#download">
                Download the app →
              </a>
            </p>
          </div>
        </div>
      </main>
    );
  }

  if (!detail || !selected) {
    return (
      <main className="watch-page">
        <div className="shell">
          <a className="back-link" href="#preview">
            ← Back to free preview
          </a>
          <div className="watch-layout">
            <div className="watch-stage">
              <div className="skeleton watch-stage-skeleton" />
            </div>
            <aside className="watch-side">
              <span className="skeleton watch-title-skeleton" />
              <span className="skeleton watch-line-skeleton" />
              <span className="skeleton watch-line-skeleton" />
              <span className="skeleton watch-line-skeleton" />
            </aside>
          </div>
        </div>
      </main>
    );
  }

  const drama = detail.drama;
  const items = detail.items;
  const firstFree = items.find((episode) => episode.free) ?? null;
  const lockedCount = items.filter((episode) => !episode.free).length;
  const freeItems = items.filter((episode) => episode.free);
  const lastFree = freeItems.length > 0 ? freeItems[freeItems.length - 1].episode_number : 0;
  const appOnlyEpisodes = Math.max(0, detail.total_episode_count - items.length);
  const activeOption = qualities.find((option) => option.index === activeQuality) ?? null;
  const selectedOption = qualities.find((option) => option.index === selectedQuality) ?? null;

  return (
    <main className="watch-page">
      <div className="shell">
        <div className="watch-topbar">
          <a className="back-link" href="#preview">
            ← Back to free preview
          </a>
          <span className="watch-topbar-note">Free episodes stream on the web · paid episodes unlock in the app</span>
        </div>

        <div className="watch-layout">
          <section className="watch-stage" aria-label={`${drama.title} player`}>
            <div className="watch-frame">
              {auth?.url ? (
                <>
                  <video ref={videoRef} className="watch-video" controls playsInline />
                  {needsTap && (
                    <button
                      type="button"
                      className="watch-tap"
                      onClick={() => {
                        setQualityOpen(false);
                        videoRef.current?.play().catch(() => setNeedsTap(true));
                      }}
                    >
                      <PlayGlyph />
                      <span>Tap to play</span>
                    </button>
                  )}
                  {!needsTap && qualities.length > 0 && (
                    <div className={`watch-quality${qualityOpen ? " is-open" : ""}`} ref={qualityRef}>
                      <button
                        type="button"
                        className="watch-quality-btn"
                        aria-haspopup="true"
                        aria-expanded={qualityOpen}
                        title="Playback quality"
                        onClick={() => setQualityOpen((open) => !open)}
                      >
                        <QualityGlyph />
                        <span>{selectedOption ? selectedOption.label : activeOption ? `Auto · ${activeOption.label}` : "Auto"}</span>
                      </button>
                      {qualityOpen && (
                        <div className="watch-quality-menu" role="menu" aria-label="Playback quality">
                          <button
                            type="button"
                            role="menuitemradio"
                            aria-checked={selectedQuality === -1}
                            className={`watch-quality-item${selectedQuality === -1 ? " is-active" : ""}`}
                            data-level="-1"
                            onClick={() => chooseQuality(-1)}
                          >
                            Auto{selectedQuality === -1 && activeOption ? ` · ${activeOption.label}` : ""}
                          </button>
                          {qualities.map((option) => (
                            <button
                              key={option.index}
                              type="button"
                              role="menuitemradio"
                              aria-checked={selectedQuality === option.index}
                              className={`watch-quality-item${selectedQuality === option.index ? " is-active" : ""}`}
                              data-level={option.index}
                              data-short-side={option.shortSide}
                              onClick={() => chooseQuality(option.index)}
                            >
                              {option.label}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </>
              ) : (
                <div className="watch-cover">
                  <PosterImage src={drama.cover_url} alt={`${drama.title} cover`} />
                  {selected.free ? (
                    <button type="button" className="watch-play" onClick={() => play(selected)} disabled={busy}>
                      <PlayGlyph />
                      <span>{busy ? "Loading…" : `Play EP ${selected.episode_number} free`}</span>
                    </button>
                  ) : (
                    <span className="watch-cover-lock">
                      <LockGlyph />
                    </span>
                  )}
                </div>
              )}

              {notice === "locked" && (
                <div className="watch-overlay" role="dialog" aria-label="Episode locked">
                  <span className="watch-overlay-icon">
                    <LockGlyph />
                  </span>
                  <h3>Episode {selected.episode_number} is locked</h3>
                  <p>Unlock every episode and watch the full series in the MiniTV app.</p>
                  <div className="watch-overlay-actions">
                    <a className="btn btn-primary" href="#download">
                      Download the app
                    </a>
                    {firstFree && firstFree.id !== selected.id && (
                      <button type="button" className="btn btn-ghost" onClick={() => play(firstFree)}>
                        Watch free episodes
                      </button>
                    )}
                  </div>
                </div>
              )}

              {notice === "failed" && (
                <div className="watch-overlay" role="dialog" aria-label="Playback failed">
                  <h3>Playback hit a snag</h3>
                  <p>The signed preview link may have expired. Try again, or continue in the app.</p>
                  <div className="watch-overlay-actions">
                    <button type="button" className="btn btn-primary" onClick={() => play(selected)}>
                      Try again
                    </button>
                    <a className="btn btn-ghost" href="#download">
                      Get the app
                    </a>
                  </div>
                </div>
              )}
            </div>
          </section>

          <aside className="watch-side">
            <nav className="watch-breadcrumb" aria-label="Breadcrumb">
              <a href="#home">Home</a>
              <span aria-hidden="true">/</span>
              <a href="#preview">Free Preview</a>
              <span aria-hidden="true">/</span>
              <span className="watch-breadcrumb-current">{drama.title}</span>
            </nav>

            <h1 className="watch-title">{drama.title}</h1>
            <div className="watch-stats">
              {drama.play_count > 0 && (
                <span className="watch-stat">
                  <span className="watch-stat-star" aria-hidden="true">
                    ★
                  </span>
                  {formatPlays(drama.play_count)} plays
                </span>
              )}
              <span className="watch-stat">
                {drama.free_playable_count} free {drama.free_playable_count === 1 ? "episode" : "episodes"}
              </span>
            </div>

            <h3 className="watch-plot-title">
              Plot of EP {selected.episode_number}
              {selected.duration_seconds > 0 && (
                <span className="watch-plot-duration">{formatDuration(selected.duration_seconds)}</span>
              )}
            </h3>
            <p className="watch-synopsis">{drama.synopsis}</p>

            {drama.tags.length > 0 && (
              <div className="watch-tags">
                {drama.tags.map((tag) => (
                  <span className="watch-tag" key={tag}>
                    {tag}
                  </span>
                ))}
              </div>
            )}

            <h3 className="watch-plot-title">Episodes</h3>
            <div className="ep-grid">
              {items.map((episode) => (
                <button
                  key={episode.id}
                  type="button"
                  className={`ep-btn${episode.free ? " is-free" : " is-locked"}${
                    episode.id === selected.id ? " is-current" : ""
                  }`}
                  onClick={() => play(episode)}
                  title={
                    episode.free
                      ? `Play episode ${episode.episode_number} free`
                      : "Locked — unlock in the MiniTV app"
                  }
                >
                  {episode.episode_number}
                  {!episode.free && <LockGlyph className="ep-lock" />}
                </button>
              ))}
            </div>

            <div className="watch-cta">
              {lockedCount > 0 || appOnlyEpisodes > 0 ? (
                <>
                  <a className="btn btn-primary btn-block" href="#download">
                    {lockedCount > 0 ? "Unlock all episodes in the app" : "Watch the full series in the app"}
                  </a>
                  <p className="watch-cta-note">
                    {lastFree > 0
                      ? `Episodes 1–${lastFree} are free here. `
                      : ""}
                    Paid episodes unlock with coins or a membership in the MiniTV app
                    {appOnlyEpisodes > 0 ? ` — ${appOnlyEpisodes} more ${appOnlyEpisodes === 1 ? "episode is" : "episodes are"} app-only` : ""}.
                  </p>
                </>
              ) : (
                <>
                  <a className="btn btn-primary btn-block" href="#download">
                    Get the app for more shows
                  </a>
                  <p className="watch-cta-note">Every episode of this show is free here. The full library lives in the app.</p>
                </>
              )}
              <a className="text-link" href="#download">
                View all episodes in the app →
              </a>
            </div>
          </aside>
        </div>
      </div>
    </main>
  );
}
