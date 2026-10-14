/**
 * Shared episode-upload primitives.
 *
 * Both the single-episode editor and the batch upload queue talk to Bunny
 * directly — video bytes never pass through our API. Keeping the TUS
 * handshake, the local metadata probe and the transcode polling in one place
 * means the two upload paths cannot drift apart.
 */
import { api, getToken } from "../api";

export type VideoMeta = { width: number; height: number; durationSeconds: number };

export type UploadSession = {
  video_id: string;
  upload_url: string;
  headers: Record<string, string>;
  episode_id: number;
  status: string;
};

export const MAX_UPLOAD_BYTES = 200 * 1024 * 1024;

export const formatDuration = (seconds?: number) => {
  if (!seconds) return "-";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
};

export const formatFileSize = (bytes: number) => {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
  if (bytes >= 1024) return `${Math.max(1, Math.round(bytes / 1024))}KB`;
  return `${bytes}B`;
};

const toBase64 = (value: string) => {
  const bytes = new TextEncoder().encode(value);
  let raw = "";
  bytes.forEach((byte) => {
    raw += String.fromCharCode(byte);
  });
  return btoa(raw);
};

/**
 * Direct-to-Bunny TUS: POST creates the session (Bunny answers with the
 * Location to PATCH), then a single PATCH streams the file with progress. The
 * same auth headers go on both requests. In fake mode the backend swaps in a
 * same-origin dev receiver, which additionally needs our admin bearer token.
 */
export function directUpload(
  session: UploadSession,
  file: File,
  onProgress: (percent: number) => void,
  bindXhr: (xhr: XMLHttpRequest | null) => void
): Promise<void> {
  const base = new URL(session.upload_url, window.location.href);
  const headers: Record<string, string> = { ...session.headers };
  const token = getToken();
  if (base.origin === window.location.origin && token) headers.Authorization = `Bearer ${token}`;
  // Bunny answers the POST with a root-relative Location ("/tusupload/<id>");
  // it must be resolved against Bunny's origin, not the admin page.
  const resolveLocation = (location: string) => new URL(location, base).href;

  const create = () =>
    new Promise<string>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      bindXhr(xhr);
      xhr.open("POST", session.upload_url);
      xhr.setRequestHeader("Tus-Resumable", "1.0.0");
      xhr.setRequestHeader("Upload-Length", String(file.size));
      xhr.setRequestHeader("Upload-Metadata", `filename ${toBase64(file.name)}`);
      Object.entries(headers).forEach(([key, value]) => xhr.setRequestHeader(key, value));
      xhr.onload = () => {
        bindXhr(null);
        const location = xhr.getResponseHeader("Location");
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(location ? resolveLocation(location) : base.href);
        } else {
          reject(new Error(`创建直传会话失败（HTTP ${xhr.status}）`));
        }
      };
      xhr.onerror = () => {
        bindXhr(null);
        reject(new Error("创建直传会话失败（网络错误）"));
      };
      xhr.send();
    });

  const write = (location: string) =>
    new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      bindXhr(xhr);
      xhr.open("PATCH", location);
      xhr.setRequestHeader("Tus-Resumable", "1.0.0");
      xhr.setRequestHeader("Upload-Offset", "0");
      xhr.setRequestHeader("Content-Type", "application/offset+octet-stream");
      Object.entries(headers).forEach(([key, value]) => xhr.setRequestHeader(key, value));
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          onProgress(Math.floor((event.loaded / event.total) * 100));
        }
      };
      xhr.onload = () => {
        bindXhr(null);
        if (xhr.status >= 200 && xhr.status < 300) {
          onProgress(100);
          resolve();
        } else {
          reject(new Error(`上传失败（HTTP ${xhr.status}）`));
        }
      };
      xhr.onerror = () => {
        bindXhr(null);
        reject(new Error("上传失败（网络中断）"));
      };
      xhr.onabort = () => {
        bindXhr(null);
        reject(new Error("上传已取消"));
      };
      xhr.send(file);
    });

  return create().then(write);
}

/** Read resolution from the local file before asking the API for a session. */
export function probeVideo(file: File): Promise<VideoMeta> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    video.preload = "metadata";
    video.onloadedmetadata = () => {
      URL.revokeObjectURL(url);
      resolve({
        width: video.videoWidth,
        height: video.videoHeight,
        durationSeconds: Math.round(video.duration || 0),
      });
    };
    video.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("无法读取视频信息，请确认是完整的 MP4 / MOV 文件"));
    };
    video.src = url;
  });
}

/** Display-only label; both orientations are accepted for upload. */
export function orientationLabel(meta: VideoMeta): string {
  if (meta.width <= 0 || meta.height <= 0) return "方向未知";
  return meta.height > meta.width ? "竖版" : "横版";
}

/** Poll the backend until Bunny finishes transcoding (callback is unset in dev). */
export async function waitForTranscode(episodeId: number, attempts = 24, intervalMs = 5000): Promise<string> {
  let lastStatus = "transcoding";
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const res = await api<{ status: string }>(`/admin/v1/episodes/${episodeId}/sync-status`, { method: "POST" });
      lastStatus = res.status;
      if (res.status !== "transcoding") return res.status;
    } catch {
      // Transient failure (backend restart, proxy hiccup) must not abort the
      // flow: the bytes are already at Bunny, and the episode table's
      // 「同步状态」 button stays available as a manual retry.
      lastStatus = "transcoding";
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return lastStatus;
}
