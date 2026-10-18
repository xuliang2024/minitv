import { useEffect, useRef, useState } from "react";
import { getJson, postJson } from "../api";
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
  IconMoreDots,
  IconQuality,
  IconShare,
  IconSpeed,
  IconStar,
} from "../icons";
import { EpisodeView } from "../player/EpisodeView";
import type { PlayEpisode, PlayPayload } from "../types";

interface Props {
  dramaId: number;
  locale: Locale;
  onBack: () => void;
}

const RANGE = 30;
const SPEED_OPTIONS = [0.75, 1.0, 1.25, 1.5, 2.0];

/** The drama's own page: every episode, opened at the resume point. Finishing one jumps to the next. */
export function PlayPage({ dramaId, locale, onBack }: Props) {
  const [payload, setPayload] = useState<PlayPayload | null>(null);
  const [error, setError] = useState(false);
  const [index, setIndex] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [sheet, setSheet] = useState<"episodes" | "synopsis" | "settings" | null>(null);
  const [liked, setLiked] = useState(false);
  const [favorited, setFavorited] = useState(false);
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
        setPayload(data);
      })
      .catch(() => setError(true));
  };

  useEffect(load, [dramaId]);

  if (error) return <div className="center"><button className="retry" onClick={load}>{t(locale, "retry")}</button></div>;
  if (!payload) return <div className="center">…</div>;
  if (payload.items.length === 0) return <div className="center">{t(locale, "empty")}</div>;

  const current = payload.items[index];
  const go = (next: number) => {
    if (next < 0 || next >= payload.items.length) return;
    setIndex(next);
    setExpanded(false);
  };
  const react = (kind: "like" | "favorite", remove: boolean) => {
    postJson("/v1/reactions", { target_type: "drama", target_id: dramaId, kind, remove }).catch(() => undefined);
  };

  return (
    <div
      className="play"
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
                <Locked episode={episode} locale={locale} />
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
          onClick={() => {
            const next = !liked;
            setLiked(next);
            react("like", !next);
          }}
          aria-label="Like"
        >
          <span className="rail-icon"><IconHeart active={liked} size={30} /></span>
          <span className="rail-label">{formatCount(payload.drama.play_count, locale)}</span>
        </button>

        <button
          type="button"
          className="rail-btn"
          onClick={() => setSheet("episodes")}
          aria-label={t(locale, "episodes_tab")}
        >
          <span className="rail-icon"><IconComment size={30} /></span>
          <span className="rail-label">{payload.items.length > 0 ? payload.items.length : t(locale, "select")}</span>
        </button>

        <button
          type="button"
          className={favorited ? "rail-btn on" : "rail-btn"}
          onClick={() => {
            const next = !favorited;
            setFavorited(next);
            react("favorite", !next);
          }}
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
      {sheet && sheet !== "settings" && (
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

function Locked({ episode, locale }: { episode: PlayEpisode; locale: Locale }) {
  const price = episode.unlock?.coin_price;
  return (
    <div className="lock">
      <div>🔒<br />{t(locale, "locked")}{price ? ` · ${price}` : ""}</div>
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
