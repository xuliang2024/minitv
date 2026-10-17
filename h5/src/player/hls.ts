import Hls from "hls.js";

export interface PlayerHandle {
  video: HTMLVideoElement;
  destroy: () => void;
}

/**
 * One HLS surface. Safari plays natively; everyone else goes through hls.js.
 * The caller owns when the URL is minted — this class never talks to the API.
 */
export function attachHls(video: HTMLVideoElement, url: string): PlayerHandle {
  let hls: Hls | null = null;
  if (url.includes(".m3u8") && Hls.isSupported()) {
    hls = new Hls({ enableWorker: true, lowLatencyMode: false });
    hls.loadSource(url);
    hls.attachMedia(video);
  } else {
    video.src = url;
  }
  return {
    video,
    destroy() {
      hls?.destroy();
      hls = null;
      video.removeAttribute("src");
      video.load();
    },
  };
}
