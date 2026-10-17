import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  ConfigProvider,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import type { UploadFile, UploadProps } from "antd";
import {
  CloseOutlined,
  CloudUploadOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SyncOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import { api, errorText } from "../api";
import { useCanWriteContent } from "../me";
import {
  MAX_UPLOAD_BYTES,
  directUpload,
  formatDuration,
  formatFileSize,
  orientationLabel,
  probeVideo,
  waitForTranscode,
} from "./episodeUpload";
import type { UploadSession, VideoMeta } from "./episodeUpload";
import { batchQueue } from "./batchUpload";
import { BatchUploadButton, BatchUploadModal, BatchUploadPill } from "./BatchUploadPanel";

type EpisodeRow = {
  id: number;
  episode_number: number;
  title: string;
  status: string;
  duration_seconds: number;
  bunny_video_id: string;
};

const STATUS_META: Record<string, { text: string; color: string }> = {
  not_uploaded: { text: "未上传", color: "default" },
  transcoding: { text: "转码中", color: "processing" },
  transcode_failed: { text: "转码失败", color: "error" },
  in_review: { text: "审核中", color: "orange" },
  published: { text: "已上架", color: "green" },
  unpublished: { text: "已下架", color: "red" },
};

/* ---------- video picker (shared by the create and edit dialogs) ---------- */

type VideoPick = {
  fileList: UploadFile[];
  file: File | null;
  meta: VideoMeta | null;
  error: string | null;
  probing: boolean;
  onFileChange: UploadProps["onChange"];
  reset: () => void;
};

function useVideoPick(): VideoPick {
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [meta, setMeta] = useState<VideoMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  /** Sequence guard: a slow probe must not overwrite a newer pick. */
  const probeSeq = useRef(0);

  const reset = useCallback(() => {
    probeSeq.current += 1;
    setFileList([]);
    setFile(null);
    setMeta(null);
    setError(null);
    setProbing(false);
  }, []);

  const onFileChange: UploadProps["onChange"] = async (info) => {
    const next = info.fileList.slice(-1);
    setFileList(next);
    setError(null);
    setMeta(null);
    const picked = (next[0]?.originFileObj as File | undefined) ?? null;
    setFile(picked);
    if (!picked) {
      setProbing(false);
      return;
    }
    const looksLikeVideo =
      ["video/mp4", "video/quicktime"].includes(picked.type) || /\.(mp4|mov)$/i.test(picked.name);
    if (!looksLikeVideo) {
      setFileList([]);
      setFile(null);
      setProbing(false);
      setError("只支持 MP4 / MOV 文件");
      return;
    }
    if (picked.size > MAX_UPLOAD_BYTES) {
      setFileList([]);
      setFile(null);
      setProbing(false);
      setError(`文件 ${formatFileSize(picked.size)}，超过 200MB 上限`);
      return;
    }
    probeSeq.current += 1;
    const seq = probeSeq.current;
    setProbing(true);
    try {
      // Resolution only: orientation is not restricted. Ops uploads both
      // vertical and landscape episodes, so never gate on width vs height here.
      const probed = await probeVideo(picked);
      if (probeSeq.current !== seq) return;
      setMeta(probed);
    } catch (e) {
      if (probeSeq.current !== seq) return;
      setFileList([]);
      setFile(null);
      setError(errorText(e));
    } finally {
      if (probeSeq.current === seq) setProbing(false);
    }
  };

  return { fileList, file, meta, error, probing, onFileChange, reset };
}

function VideoDrop({
  pick,
  disabled,
  title,
  hint,
}: {
  pick: VideoPick;
  disabled: boolean;
  title: string;
  hint: string;
}) {
  return (
    <Upload.Dragger
      className="ep-drop"
      accept=".mp4,.mov,video/mp4,video/quicktime"
      maxCount={1}
      fileList={pick.fileList}
      showUploadList={false}
      beforeUpload={() => false}
      onChange={pick.onFileChange}
      disabled={disabled}
    >
      <div className="ep-drop-inner">
        <CloudUploadOutlined className="ep-drop-icon" />
        <div className="ep-drop-title">{title}</div>
        <div className="ep-drop-hint">{hint}</div>
      </div>
    </Upload.Dragger>
  );
}

function VideoFileCard({
  pick,
  onClear,
  onPreview,
  clearDisabled,
  progress,
}: {
  pick: VideoPick;
  onClear: () => void;
  onPreview: () => void;
  clearDisabled: boolean;
  progress: number | null;
}) {
  const name = pick.file?.name ?? "已选择的视频";
  const detail = pick.meta
    ? `${pick.meta.width}×${pick.meta.height} · ${orientationLabel(pick.meta)} · ${formatDuration(pick.meta.durationSeconds)} · ${formatFileSize(pick.file?.size ?? 0)}`
    : pick.probing
      ? "正在读取分辨率和时长…"
      : "等待视频信息";
  return (
    <div className="ep-file">
      <div className="ep-video-mark" aria-hidden>
        <VideoCameraOutlined />
      </div>
      <div className="ep-file-main">
        <div className="ep-file-name" title={name}>
          {name}
        </div>
        <div className="ep-file-sub">{detail}</div>
        {progress != null && (
          <Progress percent={progress} size="small" status="active" style={{ margin: "8px 0 0" }} />
        )}
      </div>
      <div className="ep-file-actions">
        <Button type="link" size="small" className="ep-text-btn" icon={<PlayCircleOutlined />} onClick={onPreview}>
          预览
        </Button>
        <Button
          type="text"
          size="small"
          icon={<CloseOutlined />}
          disabled={clearDisabled}
          aria-label="移除视频"
          onClick={onClear}
        />
      </div>
    </div>
  );
}

/* ---------- create / edit dialog ---------- */

/**
 * One dialog for both flows: ``editing === null`` creates a new episode row
 * (video required); otherwise it edits an existing row - title / number are
 * patched, and picking a file additionally replaces the Bunny video.
 */
function EpisodeEditorModal({
  open,
  dramaId,
  episodes,
  editing,
  onClose,
  onSaved,
}: {
  open: boolean;
  dramaId: string;
  episodes: EpisodeRow[];
  editing: EpisodeRow | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form] = Form.useForm<{ episode_number: number; title: string }>();
  const pick = useVideoPick();
  const resetPick = pick.reset;
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "saving" | "uploading" | "transcoding">("idle");
  const [progress, setProgress] = useState(0);
  /** Existing videos stay collapsed until the operator asks to replace. */
  const [replacing, setReplacing] = useState(
    () => editing == null || !editing.bunny_video_id || editing.status === "transcode_failed"
  );
  /** Episode row id once created on the server: retries must reuse it. */
  const [episodeId, setEpisodeId] = useState<number | null>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  /** Local blob URL for a not-yet-uploaded pick; revoked on close or unmount. */
  const previewBlobRef = useRef<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<
    { open: false } | { open: true; kind: "embed" | "file"; url: string; title: string }
  >({ open: false });

  useEffect(
    () => () => {
      if (previewBlobRef.current) URL.revokeObjectURL(previewBlobRef.current);
    },
    []
  );

  useEffect(() => {
    if (open) return;
    if (previewBlobRef.current) {
      URL.revokeObjectURL(previewBlobRef.current);
      previewBlobRef.current = null;
    }
    setPreview({ open: false });
  }, [open]);

  const closePreview = () => {
    if (previewBlobRef.current) {
      URL.revokeObjectURL(previewBlobRef.current);
      previewBlobRef.current = null;
    }
    setPreview({ open: false });
  };

  const previewCurrent = async () => {
    if (!editing?.bunny_video_id) return;
    setPreviewing(true);
    try {
      const signed = await api<{ url: string; embed_url?: string }>(`/admin/v1/episodes/${editing.id}/preview-url`);
      if (previewBlobRef.current) {
        URL.revokeObjectURL(previewBlobRef.current);
        previewBlobRef.current = null;
      }
      setPreview({
        open: true,
        kind: "embed",
        url: signed.embed_url ?? signed.url,
        title: `预览 · 第 ${editing.episode_number} 集`,
      });
    } catch (e) {
      message.error(`预览失败：${errorText(e)}`);
    } finally {
      setPreviewing(false);
    }
  };

  const previewPicked = () => {
    if (!pick.file) return;
    if (previewBlobRef.current) URL.revokeObjectURL(previewBlobRef.current);
    const url = URL.createObjectURL(pick.file);
    previewBlobRef.current = url;
    setPreview({ open: true, kind: "file", url, title: `预览 · ${pick.file.name}` });
  };

  const clearPick = () => {
    if (preview.open && preview.kind === "file") closePreview();
    resetPick();
  };

  const isCreate = editing === null;
  const busy = phase !== "idle";
  /** Only the byte upload locks the dialog; transcoding runs in the background. */
  const locked = phase === "uploading";
  const canReplaceVideo = editing === null || editing.status !== "transcoding";

  const nextNumber = useMemo(
    () => episodes.reduce((max, row) => Math.max(max, row.episode_number), 0) + 1,
    [episodes]
  );

  useEffect(() => {
    if (!open) return;
    resetPick();
    setError(null);
    setProgress(0);
    setPhase("idle");
    setEpisodeId(editing?.id ?? null);
    form.setFieldsValue(
      editing
        ? { episode_number: editing.episode_number, title: editing.title }
        : { episode_number: nextNumber, title: `Episode ${nextNumber}` }
    );
  }, [open, editing, nextNumber, form, resetPick]);

  const save = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    if (isCreate && !(pick.file && pick.meta)) {
      setError("请先选择要上传的视频文件");
      return;
    }
    if (pick.file && !pick.meta) {
      setError(pick.probing ? "正在读取视频信息，请稍候" : "视频信息无效，请重新选择文件");
      return;
    }
    setPhase("saving");
    setError(null);
    let session: UploadSession | null = null;
    try {
      let targetId = episodeId;
      if (targetId == null) {
        const created = await api<{ id: number }>(`/admin/v1/dramas/${dramaId}/episodes`, {
          method: "POST",
          body: JSON.stringify({
            episode_number: values.episode_number,
            title: (values.title ?? "").trim(),
          }),
        });
        targetId = created.id;
        setEpisodeId(targetId);
      } else {
        // Metadata edits (title / number) are a plain PATCH; the video file,
        // when picked, is swapped in afterwards through the upload session.
        await api(`/admin/v1/episodes/${targetId}`, {
          method: "PATCH",
          body: JSON.stringify({
            episode_number: values.episode_number,
            title: (values.title ?? "").trim(),
          }),
        });
      }
      if (pick.file && pick.meta) {
        setPhase("uploading");
        setProgress(0);
        session = await api<UploadSession>(`/admin/v1/episodes/${targetId}/upload-session`, {
          method: "POST",
          body: JSON.stringify({
            filename: pick.file.name,
            mime: pick.file.type || "video/mp4",
            size_bytes: pick.file.size,
            // Probed values only: never substitute a default resolution, it
            // would misreport landscape uploads as portrait.
            width: pick.meta.width,
            height: pick.meta.height,
            duration_seconds: pick.meta.durationSeconds,
          }),
        });
        await directUpload(session, pick.file, setProgress, (xhr) => {
          xhrRef.current = xhr;
        });
        setPhase("transcoding");
        const finalStatus = await waitForTranscode(targetId);
        if (finalStatus === "transcode_failed") {
          setError("Bunny 转码失败，请检查视频文件后重试");
          setPhase("idle");
          return;
        }
        if (finalStatus === "in_review") {
          message.success(
            isCreate
              ? `第 ${values.episode_number} 集上传完成，已进入审核中`
              : `第 ${values.episode_number} 集视频已替换；转码完成，需要重新上架`
          );
        } else {
          message.info("视频已上传，Bunny 仍在转码；稍后可在列表点「同步状态」");
        }
      } else {
        message.success("分集已更新");
      }
      onSaved();
      onClose();
    } catch (e) {
      if (session) {
        // Open the retry path: transcoding rows cannot start a new session.
        try {
          await api(`/admin/v1/episodes/${session.episode_id}/upload-failed`, {
            method: "POST",
            body: JSON.stringify({ video_id: session.video_id, reason: errorText(e) }),
          });
        } catch {
          /* best effort - the row can still be synced/pruned later */
        }
      }
      setError(errorText(e));
      setPhase("idle");
    }
  };

  const cancelUpload = () => {
    xhrRef.current?.abort();
  };

  const stopReplacing = () => {
    clearPick();
    setReplacing(false);
  };

  const primaryLabel = isCreate
    ? "开始上传"
    : pick.file
      ? editing?.bunny_video_id
        ? "保存并替换视频"
        : "保存并上传视频"
      : "保存";

  // ``editing`` is the row from when the dialog opened. Once a replace starts,
  // that badge is stale — a previous 转码失败 must not stay on the title while
  // the new file is uploading or already at Bunny.
  const liveStatus = phase === "uploading" || phase === "transcoding" ? "transcoding" : editing?.status;
  const statusMeta = liveStatus ? STATUS_META[liveStatus] ?? { text: liveStatus, color: "default" } : null;
  const hasVideo = Boolean(editing?.bunny_video_id);
  const showFile = Boolean(pick.file);
  const showDrop = canReplaceVideo && replacing && !showFile;
  const showCurrent = hasVideo && !replacing && phase !== "uploading" && phase !== "transcoding";
  /** Failed transcodes have a Bunny id but nothing playable yet. */
  const canPreviewCurrent = Boolean(editing && editing.status !== "transcode_failed");

  const infoSection = (
    <section className="ep-section">
      <div className="ep-kicker">基本信息</div>
      <Form form={form} layout="vertical" disabled={busy}>
        <div className="ep-fields">
          <Form.Item
            label="集数"
            name="episode_number"
            rules={[{ required: true, message: "请输入集数" }]}
            style={{ marginBottom: 0 }}
          >
            <InputNumber min={1} max={2000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            label="标题"
            name="title"
            rules={[{ required: true, message: "请输入标题" }]}
            style={{ marginBottom: 0 }}
          >
            <Input placeholder="这一集的标题" maxLength={200} />
          </Form.Item>
        </div>
      </Form>
    </section>
  );

  const videoSection = (
    <section className="ep-section">
      <div className="ep-section-head">
        <div className="ep-kicker">
          视频
          {isCreate && <span className="ep-req">必填</span>}
        </div>
        {hasVideo && replacing && canReplaceVideo && phase === "idle" && (
          <Button type="link" size="small" className="ep-text-btn" onClick={stopReplacing}>
            取消替换
          </Button>
        )}
      </div>

      {!canReplaceVideo && (
        <div className="ep-note">转码进行中，完成前不能替换视频。标题和集数可以先保存。</div>
      )}

      {editing?.status === "transcode_failed" && replacing && !showFile && (
        <div className="ep-note">上次转码失败。重新选择视频并保存后会再次转码，完成后进入审核。</div>
      )}

      {showCurrent && (
        <div className="ep-video-card">
          <div className="ep-video-mark" aria-hidden>
            <VideoCameraOutlined />
          </div>
          <div className="ep-video-copy">
            <div className="ep-video-title">当前视频</div>
            <div className="ep-video-sub">
              {editing?.duration_seconds ? `时长 ${formatDuration(editing.duration_seconds)}` : "时长未知"}
            </div>
          </div>
          <div className="ep-video-actions">
            {canPreviewCurrent && (
              <Button icon={<PlayCircleOutlined />} loading={previewing} onClick={previewCurrent}>
                预览
              </Button>
            )}
            {canReplaceVideo && (
              <Button disabled={busy} onClick={() => setReplacing(true)}>
                替换视频
              </Button>
            )}
          </div>
        </div>
      )}

      {showDrop && (
        <VideoDrop
          pick={pick}
          disabled={busy}
          title={hasVideo ? "把新视频拖到这里，或点击选择" : "把视频拖到这里，或点击选择"}
          hint={
            hasVideo
              ? "MP4、MOV · 不超过 200MB。保存后会重新转码，完成前不能播放"
              : "MP4、MOV · 横版或竖版 · 不超过 200MB · 直传 Bunny"
          }
        />
      )}

      {showFile && (
        <VideoFileCard
          pick={pick}
          onClear={clearPick}
          onPreview={previewPicked}
          clearDisabled={busy}
          progress={phase === "uploading" ? progress : null}
        />
      )}

      {pick.meta && hasVideo && phase === "idle" && (
        <div className="ep-warn">保存后会立即重新转码，期间不能播放，完成后需要重新上架。</div>
      )}

      {pick.error && <div className="ep-error">{pick.error}</div>}
    </section>
  );

  return (
    <ConfigProvider
      theme={{
        token: { colorPrimary: "#E53B3E", borderRadius: 8 },
        components: {
          Input: {
            activeBorderColor: "#1f1f1f",
            hoverBorderColor: "#bfbfbf",
            activeShadow: "0 0 0 2px rgba(31, 31, 31, 0.06)",
          },
          InputNumber: {
            activeBorderColor: "#1f1f1f",
            hoverBorderColor: "#bfbfbf",
            activeShadow: "0 0 0 2px rgba(31, 31, 31, 0.06)",
          },
        },
      }}
    >
      <Modal
        rootClassName="ep-editor-root"
        open={open}
        title={
          <span className="ep-title">
            {editing ? `编辑第 ${editing.episode_number} 集` : "上传新分集"}
            {statusMeta ? <Tag color={statusMeta.color}>{statusMeta.text}</Tag> : null}
          </span>
        }
        width={600}
        maskClosable={!locked && !preview.open}
        keyboard={!preview.open}
        closable={!locked}
        onCancel={locked ? undefined : onClose}
        styles={{ body: { paddingTop: 8 } }}
        footer={
          <div className="ep-footer">
            {locked ? (
              <Button danger onClick={cancelUpload}>
                取消上传
              </Button>
            ) : phase === "transcoding" ? (
              <Button onClick={onClose}>关闭</Button>
            ) : (
              <>
                <Button onClick={onClose} disabled={phase === "saving"}>
                  取消
                </Button>
                <Button
                  type="primary"
                  loading={phase === "saving"}
                  disabled={pick.probing || (isCreate && !pick.file)}
                  onClick={save}
                >
                  {primaryLabel}
                </Button>
              </>
            )}
          </div>
        }
      >
        <div className="ep-editor">
          {isCreate ? videoSection : infoSection}
          {isCreate ? infoSection : videoSection}
          {phase === "transcoding" && (
            <Alert
              type="info"
              showIcon
              message="文件已上传，Bunny 正在转码"
              description="转码完成后会自动进入审核中。列表里的旧状态可以先忽略，关闭后会刷新。"
            />
          )}
          {error && <Alert type="error" showIcon message={error} />}
        </div>
      </Modal>
      <Modal
        open={preview.open}
        title={preview.open ? preview.title : "预览"}
        width={720}
        footer={null}
        centered
        destroyOnHidden
        zIndex={1100}
        onCancel={closePreview}
        styles={{ body: { padding: 0, background: "#000", lineHeight: 0 } }}
      >
        {preview.open && preview.kind === "embed" ? (
          <iframe
            title={preview.title}
            src={preview.url}
            style={{ display: "block", width: "100%", aspectRatio: "9 / 16", maxHeight: "70vh", border: 0, background: "#000" }}
            allow="accelerometer; autoplay; fullscreen; encrypted-media; picture-in-picture"
            allowFullScreen
          />
        ) : null}
        {preview.open && preview.kind === "file" ? (
          <video
            key={preview.url}
            src={preview.url}
            controls
            autoPlay
            playsInline
            style={{ display: "block", width: "100%", maxHeight: "70vh", objectFit: "contain", background: "#000" }}
          />
        ) : null}
      </Modal>
    </ConfigProvider>
  );
}

export default function EpisodesPanel({ dramaId }: { dramaId: string }) {
  const [rows, setRows] = useState<EpisodeRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<string | undefined>(undefined);
  const [editorOpen, setEditorOpen] = useState(false);
  /** Bumps on each open so the dialog remounts with a fresh replace/upload state. */
  const [editorNonce, setEditorNonce] = useState(0);
  /** null means the editor is in "upload new episode" mode. */
  const [editing, setEditing] = useState<EpisodeRow | null>(null);
  const [preview, setPreview] = useState<{ open: boolean; url?: string; title: string }>({
    open: false,
    title: "",
  });
  const [selectedKeys, setSelectedKeys] = useState<number[]>([]);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const canWrite = useCanWriteContent();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const query = new URLSearchParams({ page_size: "200" });
      if (status) query.set("status", status);
      const res = await api<{ items: EpisodeRow[] }>(
        `/admin/v1/dramas/${dramaId}/episodes?${query.toString()}`
      );
      setRows(res.items);
    } catch (e) {
      message.error(`加载分集失败：${errorText(e)}`);
    } finally {
      setLoading(false);
    }
  }, [dramaId, status]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    // Batch uploads run outside this component: refresh the table when an
    // item settles, coalescing bursts (upload finish → transcode → done).
    let timer: number | null = null;
    const unsubscribe = batchQueue.onSettled(() => {
      if (timer != null) return;
      timer = window.setTimeout(() => {
        timer = null;
        load();
      }, 800);
    });
    return () => {
      unsubscribe();
      if (timer != null) window.clearTimeout(timer);
    };
  }, [load]);

  const openEditor = (row: EpisodeRow | null) => {
    setEditing(row);
    setEditorNonce((nonce) => nonce + 1);
    setEditorOpen(true);
  };

  const openPreview = async (row: EpisodeRow) => {
    try {
      const signed = await api<{ url: string; embed_url?: string }>(`/admin/v1/episodes/${row.id}/preview-url`);
      setPreview({ open: true, url: signed.embed_url ?? signed.url, title: `预览 · 第 ${row.episode_number} 集` });
    } catch (e) {
      message.error(`预览失败：${errorText(e)}`);
    }
  };

  const syncStatus = async (row: EpisodeRow) => {
    try {
      const res = await api<{ status: string }>(`/admin/v1/episodes/${row.id}/sync-status`, { method: "POST" });
      const meta = STATUS_META[res.status];
      message.success(`第 ${row.episode_number} 集状态：${meta?.text ?? res.status}`);
      load();
    } catch (e) {
      message.error(`同步失败：${errorText(e)}`);
    }
  };

  const publish = async (row: EpisodeRow) => {
    try {
      await api(`/admin/v1/episodes/${row.id}/publish`, { method: "POST" });
      message.success(`第 ${row.episode_number} 集已上架`);
      load();
    } catch (e) {
      message.error(`上架失败：${errorText(e)}`);
    }
  };

  const unpublish = async (row: EpisodeRow) => {
    try {
      await api(`/admin/v1/episodes/${row.id}/unpublish`, { method: "POST" });
      message.success(`第 ${row.episode_number} 集已下架`);
      load();
    } catch (e) {
      message.error(`下架失败：${errorText(e)}`);
    }
  };

  const bulkStatus = async (action: "publish" | "unpublish") => {
    if (selectedKeys.length === 0) return;
    setBulkBusy(true);
    const label = action === "publish" ? "上架" : "下架";
    try {
      const res = await api<{ updated: unknown[]; skipped: unknown[] }>(
        "/admin/v1/episodes/bulk-status",
        {
          method: "POST",
          body: JSON.stringify({ episode_ids: selectedKeys, action }),
        }
      );
      const skipped = res.skipped.length;
      message.success(
        `批量${label}完成：成功 ${res.updated.length} 集${skipped ? `，跳过 ${skipped} 集（状态不符或缺少视频）` : ""}`
      );
      setSelectedKeys([]);
      load();
    } catch (e) {
      message.error(`批量${label}失败：${errorText(e)}`);
    } finally {
      setBulkBusy(false);
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space wrap size="middle">
        {canWrite && (
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => openEditor(null)}
          >
            上传新分集
          </Button>
        )}
        {canWrite && <BatchUploadButton onClick={() => setBatchOpen(true)} />}
        <Select
          allowClear
          placeholder="状态筛选"
          style={{ width: 160 }}
          value={status}
          onChange={(value) => setStatus(value)}
          options={Object.entries(STATUS_META).map(([value, meta]) => ({ value, label: meta.text }))}
        />
        <Button icon={<ReloadOutlined />} onClick={() => load()}>
          刷新
        </Button>
        <Typography.Text type="secondary">共 {rows.length} 集</Typography.Text>
        {selectedKeys.length > 0 && (
          <>
            <Typography.Text>已选 {selectedKeys.length} 集</Typography.Text>
            <Button
              type="primary"
              disabled={!canWrite}
              loading={bulkBusy}
              onClick={() => bulkStatus("publish")}
            >
              批量上架
            </Button>
            <Button
              danger
              disabled={!canWrite}
              loading={bulkBusy}
              onClick={() => bulkStatus("unpublish")}
            >
              批量下架
            </Button>
            <Button type="link" onClick={() => setSelectedKeys([])}>
              取消选择
            </Button>
          </>
        )}
      </Space>

      <Table<EpisodeRow>
        rowKey="id"
        loading={loading}
        dataSource={rows}
        pagination={{ pageSize: 20 }}
        scroll={{ x: 720 }}
        rowSelection={{
          selectedRowKeys: selectedKeys,
          onChange: (keys) => setSelectedKeys(keys as number[]),
          getCheckboxProps: (row) => ({
            // Nothing to take down/publish while a placeholder has no video.
            disabled: !row.bunny_video_id,
          }),
        }}
        columns={[
          { title: "集", dataIndex: "episode_number", width: 70 },
          {
            title: "标题",
            dataIndex: "title",
            ellipsis: true,
            render: (value: string) =>
              value || <Typography.Text type="secondary">未命名</Typography.Text>,
          },
          { title: "时长", dataIndex: "duration_seconds", width: 90, render: (value: number) => formatDuration(value) },
          {
            title: "状态",
            dataIndex: "status",
            width: 110,
            render: (value: string) => {
              const meta = STATUS_META[value] ?? { text: value, color: "default" };
              return <Tag color={meta.color}>{meta.text}</Tag>;
            },
          },
          {
            title: "操作",
            width: 300,
            render: (_, row) => (
              <Space size={12} wrap>
                {row.bunny_video_id && row.status !== "transcode_failed" && (
                  <Button size="small" type="link" style={{ padding: 0 }} onClick={() => openPreview(row)}>
                    预览
                  </Button>
                )}
                {row.status === "transcoding" && (
                  <Button size="small" type="link" style={{ padding: 0 }} icon={<SyncOutlined />} onClick={() => syncStatus(row)}>
                    同步状态
                  </Button>
                )}
                {canWrite && (!row.bunny_video_id || row.status === "transcode_failed") && (
                  <Button
                    size="small"
                    type={row.status === "transcode_failed" ? "link" : "primary"}
                    danger={row.status === "transcode_failed"}
                    style={row.status === "transcode_failed" ? { padding: 0 } : undefined}
                    onClick={() => openEditor(row)}
                  >
                    {row.status === "transcode_failed" ? "重新上传" : "上传"}
                  </Button>
                )}
                {canWrite && (
                  <Button size="small" type="link" style={{ padding: 0 }} onClick={() => openEditor(row)}>
                    编辑
                  </Button>
                )}
                {row.status === "in_review" && canWrite && (
                  <Popconfirm title={`上架第 ${row.episode_number} 集？`} onConfirm={() => publish(row)}>
                    <Button size="small" type="link" style={{ padding: 0 }}>
                      上架
                    </Button>
                  </Popconfirm>
                )}
                {row.status === "unpublished" && canWrite && (
                  <Popconfirm title={`重新上架第 ${row.episode_number} 集？`} onConfirm={() => publish(row)}>
                    <Button size="small" type="link" style={{ padding: 0 }}>
                      上架
                    </Button>
                  </Popconfirm>
                )}
                {row.status === "published" && canWrite && (
                  <Popconfirm title={`下架第 ${row.episode_number} 集？`} onConfirm={() => unpublish(row)}>
                    <Button size="small" type="link" danger style={{ padding: 0 }}>
                      下架
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]}
      />

      <EpisodeEditorModal
        key={editorNonce}
        open={editorOpen}
        dramaId={dramaId}
        episodes={rows}
        editing={editing}
        onClose={() => {
          setEditorOpen(false);
          load();
        }}
        onSaved={load}
      />

      <Modal
        open={preview.open}
        title={preview.title}
        width={720}
        footer={null}
        destroyOnHidden
        onCancel={() => setPreview({ open: false, title: "" })}
      >
        {preview.url ? (
          <iframe
            title={preview.title}
            src={preview.url}
            style={{ width: "100%", aspectRatio: "9 / 16", maxHeight: "70vh", border: 0 }}
            allow="accelerometer; autoplay; encrypted-media; picture-in-picture"
            allowFullScreen
          />
        ) : null}
      </Modal>

      <BatchUploadModal
        open={batchOpen}
        onClose={() => setBatchOpen(false)}
        dramaId={dramaId}
        existingNumbers={rows.map((row) => row.episode_number)}
      />

      <BatchUploadPill dramaId={dramaId} hidden={batchOpen} onOpen={() => setBatchOpen(true)} />
    </Space>
  );
}
