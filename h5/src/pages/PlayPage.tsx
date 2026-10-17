import { useEffect, useRef, useState } from "react";
import { getJson, postJson } from "../api";
import type { Locale } from "../copy";
import { t } from "../copy";
import { formatCount } from "../format";
import { EpisodeView } from "../player/EpisodeView";
import type { PlayEpisode, PlayPayload } from "../types";

interface Props {
  dramaId: number;
  locale: Locale;
  onBack: () => void;
}

const RANGE = 30;

/** The drama's own page: every episode, opened at the resume point. Finishing one jumps to the next. */
export function PlayPage({ dramaId, locale, onBack }: Props) {
  const [payload, setPayload] = useState<PlayPayload | null>(null);
  const [error, setError] = useState(false);
  const [index, setIndex] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [sheet, setSheet] = useState<"episodes" | "synopsis" | null>(null);
  const [liked, setLiked] = useState(false);
  const [favorited, setFavorited] = useState(false);
  const startY = useRef(0);

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
                  onFinished={() => go(position + 1)}
                />
              )
            )}
          </div>
        ))}
      </div>
      <div className="back">
        <button className="icon-btn" onClick={onBack}>←</button>
        <span>{t(locale, "episode_short", { number: current.episode_number })}</span>
      </div>
      <div className="scrim" />
      <div className="rail">
        <button style={{ color: liked ? "var(--gold)" : "#fff" }} onClick={() => { const next = !liked; setLiked(next); react("like", !next); }}>
          ♥<span>{formatCount(payload.drama.play_count, locale)}</span>
        </button>
        <button onClick={() => setSheet("episodes")}>💬</button>
        <button style={{ color: favorited ? "var(--gold)" : "#fff" }} onClick={() => { const next = !favorited; setFavorited(next); react("favorite", !next); }}>★</button>
        <button>↗</button>
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
        ☰ <span style={{ flex: 1, textAlign: "left" }}>{t(locale, "all_episodes_bar", { count: payload.items.length })}</span> ⌃
      </button>
      {sheet && (
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
