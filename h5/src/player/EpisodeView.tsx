import { useEffect, useRef, useState } from "react";
import { postJson } from "../api";
import { IconPlayCenter } from "../icons";
import type { PlaybackAuth } from "../types";
import { attachHls, type PlayerHandle } from "./hls";

interface Props {
  dramaId: number;
  episodeId: number;
  resumeSeconds?: number;
  active: boolean;
  /** When false the view never asks for a URL (locked episodes). */
  playable: boolean;
  playbackSpeed?: number;
  quality?: string;
  onQualitiesChange?: (qualities: string[]) => void;
  onFinished?: () => void;
}

/**
 * Mints a signed URL only while this slot is the one on screen, then reports
 * start / 15s progress / complete. Analytics failures never stop playback.
 * Tokens live five minutes, so a URL fetched with the list would already be dead.
 */
export function EpisodeView({
  dramaId,
  episodeId,
  resumeSeconds = 0,
  active,
  playable,
  playbackSpeed = 1.0,
  quality = "Auto",
  onQualitiesChange,
  onFinished,
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const handleRef = useRef<PlayerHandle | null>(null);
  const [paused, setPaused] = useState(false);
  const [progress, setProgress] = useState(0);
  const [failed, setFailed] = useState(false);
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;
  const onQualitiesChangeRef = useRef(onQualitiesChange);
  onQualitiesChangeRef.current = onQualitiesChange;

  useEffect(() => {
    if (!active || !playable) return;
    const video = videoRef.current;
    if (!video) return;
    let handle: PlayerHandle | null = null;
    let heartbeat = 0;
    let cancelled = false;
    let finished = false;

    const report = (kind: "start" | "progress" | "complete", position: number) => {
      postJson(`/v1/playback/${kind}`, { episode_id: episodeId, position_seconds: position }).catch(() => undefined);
    };

    const finish = () => {
      if (finished) return;
      finished = true;
      window.clearInterval(heartbeat);
      report("complete", Math.floor(video.duration || 0));
      onFinishedRef.current?.();
    };

    const onTime = () => {
      if (!video.duration) return;
      setProgress(video.currentTime / video.duration);
      if (video.currentTime >= video.duration - 1.5) finish();
    };

    (async () => {
      try {
        const auth = await postJson<PlaybackAuth>("/v1/playback", { drama_id: dramaId, episode_id: episodeId });
        if (cancelled || !auth.allowed || !auth.url) {
          if (!cancelled && auth.allowed === false) setFailed(true);
          return;
        }
        handle = attachHls(video, auth.url, (levels) => {
          onQualitiesChangeRef.current?.(levels);
        });
        handleRef.current = handle;
        if (quality) {
          handle.setQuality(quality);
        }
        const start = () => {
          if (resumeSeconds > 0) video.currentTime = resumeSeconds;
          video.playbackRate = playbackSpeed;
          video.play().catch(() => setPaused(true));
          report("start", resumeSeconds);
          heartbeat = window.setInterval(() => report("progress", Math.floor(video.currentTime || 0)), 15000);
        };
        if (video.readyState >= 1) start();
        else video.addEventListener("loadedmetadata", start, { once: true });
        video.addEventListener("timeupdate", onTime);
        video.addEventListener("ended", finish);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
      window.clearInterval(heartbeat);
      video.removeEventListener("timeupdate", onTime);
      video.removeEventListener("ended", finish);
      handle?.destroy();
      handleRef.current = null;
    };
  }, [active, playable, dramaId, episodeId, resumeSeconds]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.playbackRate = playbackSpeed;
  }, [playbackSpeed]);

  useEffect(() => {
    if (handleRef.current && quality) {
      handleRef.current.setQuality(quality);
    }
  }, [quality]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (!active) {
      video.pause();
      return;
    }
    if (!paused) {
      video.playbackRate = playbackSpeed;
      video.play().catch(() => undefined);
    }
  }, [active, paused, playbackSpeed]);

  const toggle = () => {
    const video = videoRef.current;
    if (!video || !playable) return;
    if (video.paused) {
      setPaused(false);
      video.play().catch(() => undefined);
    } else {
      setPaused(true);
      video.pause();
    }
  };

  return (
    <>
      <video ref={videoRef} playsInline onClick={toggle} />
      {failed && <div className="lock">Could not play</div>}
      {paused && playable && (
        <button
          type="button"
          className="play-pause-overlay"
          aria-label="Play"
          onClick={toggle}
        >
          <span className="play-pause-badge">
            <IconPlayCenter size={34} />
          </span>
        </button>
      )}
      <div className="progress"><b style={{ width: `${progress * 100}%` }} /></div>
    </>
  );
}
