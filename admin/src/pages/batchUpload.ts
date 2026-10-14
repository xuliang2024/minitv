/**
 * Batch episode upload queue — a module-level singleton on purpose.
 *
 * The queue must outlive React components: files keep uploading while the
 * panel is collapsed (「收起 · 后台上传」) and while the operator navigates
 * elsewhere in the SPA. Only a full page reload clears it — the picked File
 * objects and the in-flight XHRs live in the page — so the panel shows a
 * "do not refresh" warning, and a beforeunload guard is registered whenever
 * any work is pending.
 *
 * Concurrency: at most MAX_CONCURRENT_UPLOADS direct uploads run at once.
 * Everything else waits in FIFO order and is refilled as slots free up.
 * Transcode polling does NOT occupy a slot — it is not upload bandwidth.
 */
import { useSyncExternalStore } from "react";
import { message } from "antd";
import { api, errorText } from "../api";
import { MAX_UPLOAD_BYTES, directUpload, probeVideo, waitForTranscode } from "./episodeUpload";
import type { UploadSession, VideoMeta } from "./episodeUpload";

export const MAX_CONCURRENT_UPLOADS = 2;

export type BatchItemStatus = "probing" | "queued" | "uploading" | "transcoding" | "done" | "failed";

export type TitleRule =
  | { kind: "episode-en" }
  | { kind: "episode-cn" }
  | { kind: "custom"; template: string };

export function titleFor(rule: TitleRule, number: number): string {
  if (rule.kind === "episode-cn") return `第 ${number} 集`;
  if (rule.kind === "custom") {
    const template = rule.template.trim();
    if (template) return template.split("{n}").join(String(number)).slice(0, 200);
  }
  return `Episode ${number}`;
}

export type BatchItem = {
  uid: number;
  dramaId: string;
  file: File;
  meta: VideoMeta | null;
  probeError: string | null;
  episodeNumber: number;
  /** Server row once created; retries reuse it so we never double-create. */
  episodeId: number | null;
  title: string;
  /** Title rule in force for this row; kept so number edits re-title correctly. */
  rule: TitleRule;
  /** True after a manual title edit; rule changes must not overwrite it. */
  titleEdited: boolean;
  status: BatchItemStatus;
  progress: number;
  error: string | null;
  /** Transcode polling timed out; keep watching via the list 「同步状态」. */
  overdue: boolean;
  session: UploadSession | null;
};

export type BatchState = { version: number; paused: boolean; items: BatchItem[] };
export type AddResult = { added: number; nextNumber: number; skippedDup: number; rejected: string[] };

const listeners = new Set<() => void>();
const settledListeners = new Set<(item: BatchItem) => void>();

let state: BatchState = { version: 0, paused: false, items: [] };
let uidSeq = 1;
/** Uploads currently inside runItem() — guards against double starts. */
const running = new Set<number>();
/** Items the operator removed mid-flight; runItem must stop at the next gate. */
const cancelled = new Set<number>();
const aborters = new Map<number, () => void>();
let announced = false;
let unloadAttached = false;

function emit(): void {
  syncUnloadGuard();
  state = { ...state, version: state.version + 1 };
  listeners.forEach((fn) => fn());
}

function byUid(uid: number): BatchItem | null {
  return state.items.find((item) => item.uid === uid) ?? null;
}

function patch(uid: number, partial: Partial<BatchItem>): void {
  const item = byUid(uid);
  if (!item) return;
  Object.assign(item, partial);
  emit();
}

function isPending(item: BatchItem): boolean {
  return item.status === "probing" || item.status === "queued";
}

/** Rows the operator may still edit here (server row not created yet). */
export function canEdit(item: BatchItem): boolean {
  return item.status === "probing" || item.status === "queued" || (item.status === "failed" && item.episodeId == null);
}

function hasPendingWork(): boolean {
  return state.items.some(
    (item) =>
      item.status === "probing" ||
      item.status === "queued" ||
      item.status === "uploading" ||
      item.status === "transcoding"
  );
}

function onBeforeUnload(event: BeforeUnloadEvent): void {
  event.preventDefault();
  event.returnValue = "";
}

/** A reload kills the queue; the browser's leave-confirm is the last guard. */
function syncUnloadGuard(): void {
  const needed = hasPendingWork();
  if (needed && !unloadAttached) {
    window.addEventListener("beforeunload", onBeforeUnload);
    unloadAttached = true;
  } else if (!needed && unloadAttached) {
    window.removeEventListener("beforeunload", onBeforeUnload);
    unloadAttached = false;
  }
}

function afterWork(item: BatchItem): void {
  settledListeners.forEach((fn) => fn(item));
  const busy = state.items.some(
    (row) =>
      row.status === "probing" ||
      row.status === "queued" ||
      row.status === "uploading" ||
      row.status === "transcoding"
  );
  if (!busy && state.items.length > 0 && !announced) {
    announced = true;
    const done = state.items.filter((row) => row.status === "done").length;
    const failed = state.items.filter((row) => row.status === "failed").length;
    message.success(`批量上传结束：成功 ${done} 个${failed ? `，失败 ${failed} 个（可单独重试）` : ""}`);
  }
}

/* ------------------------------- scheduler ------------------------------- */

function pump(): void {
  if (state.paused) return;
  let active = state.items.filter((item) => item.status === "uploading").length;
  for (const item of state.items) {
    if (active >= MAX_CONCURRENT_UPLOADS) break;
    if (item.status === "queued" && !running.has(item.uid)) {
      active += 1;
      void runItem(item);
    }
  }
}

function assertAlive(uid: number): void {
  if (cancelled.has(uid) || !byUid(uid)) throw new Error("已取消");
}

async function runItem(item: BatchItem): Promise<void> {
  const uid = item.uid;
  running.add(uid);
  patch(uid, { status: "uploading", progress: 0, error: null, overdue: false });
  try {
    if (item.meta == null) {
      throw new Error(item.probeError ?? "视频信息无效，请重新加入该文件");
    }
    if (item.episodeId == null) {
      const created = await api<{ id: number }>(`/admin/v1/dramas/${item.dramaId}/episodes`, {
        method: "POST",
        body: JSON.stringify({ episode_number: item.episodeNumber, title: item.title }),
      });
      item.episodeId = created.id;
    }
    assertAlive(uid);
    const session = await createSession(item);
    item.session = session;
    assertAlive(uid);
    await directUpload(
      session,
      item.file,
      (percent) => {
        const current = byUid(uid);
        if (!current || current.status !== "uploading") return;
        if (Math.floor(percent) === Math.floor(current.progress)) return;
        patch(uid, { progress: percent });
      },
      (xhr) => {
        aborters.set(uid, () => xhr?.abort());
      }
    );
    patch(uid, { status: "transcoding", progress: 100 });
    void pollTranscode(uid, session.episode_id);
  } catch (e) {
    const reason = cancelled.has(uid) ? "removed from batch queue" : errorText(e);
    if (item.episodeId != null) {
      // Re-open the row so a dead upload does not leave it stuck in
      // "transcoding" (a row in that state refuses a fresh session).
      await api(`/admin/v1/episodes/${item.episodeId}/upload-failed`, {
        method: "POST",
        body: JSON.stringify({ video_id: item.session?.video_id ?? "", reason }),
      }).catch(() => {
        /* best effort — the list still offers 同步状态 / 重新上传 */
      });
    }
    if (byUid(uid)) {
      patch(uid, { status: "failed", error: reason });
      message.error(`第 ${item.episodeNumber} 集上传失败：${reason}`);
      afterWork(item);
    }
  } finally {
    cancelled.delete(uid);
    running.delete(uid);
    aborters.delete(uid);
    pump();
  }
}

async function createSession(item: BatchItem): Promise<UploadSession> {
  const meta = item.meta;
  if (meta == null) throw new Error("视频信息无效，请重新加入该文件");
  const payload = JSON.stringify({
    filename: item.file.name,
    mime: item.file.type || "video/mp4",
    size_bytes: item.file.size,
    width: meta.width,
    height: meta.height,
    duration_seconds: meta.durationSeconds,
  });
  const url = `/admin/v1/episodes/${item.episodeId}/upload-session`;
  try {
    return await api<UploadSession>(url, { method: "POST", body: payload });
  } catch (e) {
    // A reload / crash can strand the row in "transcoding" with no bytes;
    // upload-failed re-opens it so the retry can take a fresh session.
    if (item.episodeId != null && errorText(e).includes("already in progress")) {
      await api(`/admin/v1/episodes/${item.episodeId}/upload-failed`, {
        method: "POST",
        body: JSON.stringify({ reason: "stale session retry" }),
      }).catch(() => {});
      return await api<UploadSession>(url, { method: "POST", body: payload });
    }
    throw e;
  }
}

async function pollTranscode(uid: number, episodeId: number): Promise<void> {
  const final = await waitForTranscode(episodeId);
  const item = byUid(uid);
  if (!item || item.status !== "transcoding") return;
  if (final === "transcode_failed") {
    patch(uid, { status: "failed", error: "Bunny 转码失败，请检查视频后重试" });
    message.error(`第 ${item.episodeNumber} 集转码失败`);
    afterWork(item);
  } else if (final === "transcoding") {
    patch(uid, { overdue: true });
    afterWork(item);
  } else {
    patch(uid, { status: "done" });
    afterWork(item);
  }
}

/* -------------------------------- actions -------------------------------- */

function probeItem(item: BatchItem): void {
  void (async () => {
    try {
      const meta = await probeVideo(item.file);
      if (!byUid(item.uid)) return;
      patch(item.uid, { meta, status: "queued" });
      pump();
    } catch (e) {
      if (!byUid(item.uid)) return;
      const reason = errorText(e);
      patch(item.uid, { status: "failed", error: reason, probeError: reason });
      message.error(`无法读取视频信息：${item.file.name}`);
      afterWork(item);
    }
  })();
}

function addFiles(opts: { dramaId: string; files: File[]; startNumber: number; rule: TitleRule }): AddResult {
  const { dramaId, files, startNumber, rule } = opts;
  const taken = new Set(state.items.filter((item) => item.dramaId === dramaId).map((item) => item.episodeNumber));
  const seen = new Set(state.items.map((item) => `${item.file.name}:${item.file.size}`));
  const rejected: string[] = [];
  let skippedDup = 0;
  let cursor = Math.max(1, Math.floor(startNumber) || 1);
  const next: BatchItem[] = [];
  for (const file of files) {
    const looksLikeVideo =
      ["video/mp4", "video/quicktime"].includes(file.type) || /\.(mp4|mov)$/i.test(file.name);
    if (!looksLikeVideo) {
      rejected.push(`${file.name}（仅支持 MP4 / MOV）`);
      continue;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      rejected.push(`${file.name}（超过 200MB）`);
      continue;
    }
    const key = `${file.name}:${file.size}`;
    if (seen.has(key)) {
      skippedDup += 1;
      continue;
    }
    while (taken.has(cursor)) cursor += 1;
    next.push({
      uid: uidSeq++,
      dramaId,
      file,
      meta: null,
      probeError: null,
      episodeNumber: cursor,
      episodeId: null,
      title: titleFor(rule, cursor),
      rule,
      titleEdited: false,
      status: "probing",
      progress: 0,
      error: null,
      overdue: false,
      session: null,
    });
    taken.add(cursor);
    seen.add(key);
    cursor += 1;
  }
  if (next.length > 0) {
    state.items = [...state.items, ...next];
    announced = false;
    emit();
    next.forEach((item) => probeItem(item));
  }
  return { added: next.length, nextNumber: cursor, skippedDup, rejected };
}

/** Re-title pending rows after a rule change; manual edits win. */
function retitle(dramaId: string, rule: TitleRule): void {
  let changed = false;
  for (const item of state.items) {
    if (item.dramaId !== dramaId || !isPending(item) || item.titleEdited) continue;
    item.rule = rule;
    const title = titleFor(rule, item.episodeNumber);
    if (title !== item.title) {
      item.title = title;
      changed = true;
    }
  }
  if (changed) emit();
}

/** Re-number all not-yet-started rows from ``start``, skipping occupied numbers. */
function renumberFrom(opts: { dramaId: string; start: number; rule: TitleRule; reserved: number[] }): number {
  const { dramaId, start, rule, reserved } = opts;
  const reservedSet = new Set(reserved);
  for (const item of state.items) {
    if (item.dramaId === dramaId && !isPending(item)) reservedSet.add(item.episodeNumber);
  }
  let cursor = Math.max(1, Math.floor(start) || 1);
  let actualStart = cursor;
  let assigned = false;
  let changed = false;
  for (const item of state.items) {
    if (item.dramaId !== dramaId || !isPending(item)) continue;
    while (reservedSet.has(cursor)) cursor += 1;
    if (!assigned) {
      actualStart = cursor;
      assigned = true;
    }
    item.rule = rule;
    if (item.episodeNumber !== cursor) {
      item.episodeNumber = cursor;
      changed = true;
    }
    if (!item.titleEdited) {
      const title = titleFor(rule, cursor);
      if (title !== item.title) {
        item.title = title;
        changed = true;
      }
    }
    reservedSet.add(cursor);
    cursor += 1;
  }
  if (changed) emit();
  return assigned ? actualStart : Math.max(1, Math.floor(start) || 1);
}

/** Next free episode number for new files of this drama. */
function nextSuggested(dramaId: string, serverNumbers: number[]): number {
  let max = 0;
  for (const n of serverNumbers) if (Number.isFinite(n)) max = Math.max(max, n);
  for (const item of state.items) if (item.dramaId === dramaId) max = Math.max(max, item.episodeNumber);
  return max + 1;
}

function editNumber(uid: number, episodeNumber: number): void {
  const item = byUid(uid);
  if (!item || !canEdit(item) || !Number.isFinite(episodeNumber)) return;
  item.episodeNumber = Math.max(1, Math.floor(episodeNumber));
  if (!item.titleEdited) item.title = titleFor(item.rule, item.episodeNumber);
  emit();
}

function editTitle(uid: number, title: string): void {
  const item = byUid(uid);
  if (!item || !canEdit(item)) return;
  item.title = title.slice(0, 200);
  item.titleEdited = true;
  emit();
}

function remove(uid: number): void {
  const item = byUid(uid);
  if (!item) return;
  cancelled.add(uid);
  if (item.status === "uploading") aborters.get(uid)?.();
  state.items = state.items.filter((row) => row.uid !== uid);
  emit();
  pump();
}

function retry(uid: number): void {
  const item = byUid(uid);
  if (!item) return;
  announced = false;
  if (item.meta == null) {
    patch(uid, { status: "probing", error: null, probeError: null });
    probeItem(item);
    return;
  }
  patch(uid, { status: "queued", error: null, progress: 0, overdue: false });
  pump();
}

function setPaused(paused: boolean): void {
  state.paused = paused;
  emit();
  if (!paused) pump();
}

function clearDone(): void {
  state.items = state.items.filter((item) => item.status !== "done");
  emit();
}

function onSettled(fn: (item: BatchItem) => void): () => void {
  settledListeners.add(fn);
  return () => {
    settledListeners.delete(fn);
  };
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function getSnapshot(): BatchState {
  return state;
}

export function useBatchQueue(): BatchState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export const batchQueue = {
  addFiles,
  retitle,
  renumberFrom,
  nextSuggested,
  editNumber,
  editTitle,
  remove,
  retry,
  setPaused,
  clearDone,
  onSettled,
  subscribe,
  getSnapshot,
};
