import Hls from "hls.js";

export interface PlayerHandle {
  video: HTMLVideoElement;
  destroy: () => void;
  setQuality: (quality: string) => void;
}

/**
 * One HLS surface. Safari plays natively; everyone else goes through hls.js.
 * The caller owns when the URL is minted — this class never talks to the API.
 */
export function attachHls(
  video: HTMLVideoElement,
  url: string,
  onLevels?: (levels: string[]) => void
): PlayerHandle {
  let hls: Hls | null = null;
  if (url.includes(".m3u8") && Hls.isSupported()) {
    hls = new Hls({ enableWorker: true, lowLatencyMode: false });
    hls.loadSource(url);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, (_, data) => {
      if (data.levels && data.levels.length > 0) {
        // 短剧为竖屏视频，按短边归一化标准清晰度（例如 480x854 -> 480P, 360x640 -> 360P, 720x1280 -> 720P）
        const pList = data.levels.map((l) => {
          const shortSide = Math.min(l.width || 0, l.height || 0) || l.height;
          if (shortSide >= 1000) return 1080;
          if (shortSide >= 700) return 720;
          if (shortSide >= 450) return 480;
          if (shortSide >= 340) return 360;
          return shortSide;
        });
        const distinct = Array.from(new Set(pList.filter((p) => p > 0))).sort((a, b) => b - a);
        const qList = ["Auto", ...distinct.map((p) => `${p}P`)];
        onLevels?.(qList);
      }
    });
  } else {
    video.src = url;
  }
  return {
    video,
    setQuality(q: string) {
      if (!hls || !hls.levels || hls.levels.length === 0) return;
      if (q === "Auto" || q === "auto" || !q) {
        hls.currentLevel = -1;
        return;
      }
      const targetP = parseInt(q.replace(/\D/g, ""), 10);
      if (isNaN(targetP)) return;
      let closestIdx = 0;
      let minDiff = Infinity;
      hls.levels.forEach((l, idx) => {
        const shortSide = Math.min(l.width || 0, l.height || 0) || l.height;
        const diff = Math.abs(shortSide - targetP);
        if (diff < minDiff) {
          minDiff = diff;
          closestIdx = idx;
        }
      });
      hls.currentLevel = closestIdx;
    },
    destroy() {
      hls?.destroy();
      hls = null;
      video.removeAttribute("src");
      video.load();
    },
  };
}
