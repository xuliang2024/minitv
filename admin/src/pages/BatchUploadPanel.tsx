/**
 * Batch upload UI: the toolbar button, the queue modal and the floating
 * progress pill shown after the modal is collapsed. All state lives in
 * ``batchUpload.ts`` — these components only render it and forward actions.
 */
import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Input,
  InputNumber,
  Modal,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Upload,
  message,
} from "antd";
import type { TableColumnsType, UploadProps } from "antd";
import { CloudUploadOutlined, CloseOutlined } from "@ant-design/icons";
import { MAX_CONCURRENT_UPLOADS, batchQueue, canEdit, useBatchQueue } from "./batchUpload";
import type { BatchItem, TitleRule } from "./batchUpload";
import { formatDuration, formatFileSize } from "./episodeUpload";

const RULE_OPTIONS = [
  { value: "episode-en", label: "Episode {n}" },
  { value: "episode-cn", label: "第 {n} 集" },
  { value: "custom", label: "自定义…" },
];

function isActive(item: BatchItem): boolean {
  return item.status !== "done" && item.status !== "failed";
}

/* ------------------------------ toolbar button ------------------------------ */

export function BatchUploadButton({ onClick, disabled = false }: { onClick: () => void; disabled?: boolean }) {
  const { items } = useBatchQueue();
  const active = items.filter(isActive).length;
  return (
    <Button icon={<CloudUploadOutlined />} disabled={disabled} onClick={onClick}>
      批量上传{active > 0 ? `（${active}）` : ""}
    </Button>
  );
}

/* -------------------------------- status cell ------------------------------- */

function StatusCell({ item, queuedRank }: { item: BatchItem; queuedRank: number | null }) {
  if (item.status === "probing") return <Tag>读取中…</Tag>;
  if (item.status === "queued") {
    return (
      <Space size={6}>
        <Tag style={{ marginRight: 0 }}>排队中</Tag>
        {queuedRank === 1 && <span style={{ fontSize: 12, color: "#8c8c8c" }}>下一位</span>}
      </Space>
    );
  }
  if (item.status === "uploading") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Tag color="processing" style={{ marginRight: 0 }}>
          上传中
        </Tag>
        <Progress percent={item.progress} size="small" showInfo={false} style={{ flex: 1, margin: 0 }} />
        <span style={{ fontSize: 12, color: "#8c8c8c", width: 34, textAlign: "right" }}>
          {Math.floor(item.progress)}%
        </span>
      </div>
    );
  }
  if (item.status === "transcoding") {
    return (
      <Space size={6}>
        <Tag color="orange" style={{ marginRight: 0 }}>
          转码中
        </Tag>
        {item.overdue && (
          <Tooltip title="Bunny 仍在处理，稍后可在列表点「同步状态」">
            <span style={{ fontSize: 12, color: "#8c8c8c" }}>稍后同步</span>
          </Tooltip>
        )}
      </Space>
    );
  }
  if (item.status === "done") {
    return (
      <Tag color="green" style={{ marginRight: 0 }}>
        已完成
      </Tag>
    );
  }
  return (
    <Tooltip title={item.error ?? undefined}>
      <Tag color="red" style={{ marginRight: 0 }}>
        上传失败
      </Tag>
    </Tooltip>
  );
}

/* ---------------------------------- modal ----------------------------------- */

export function BatchUploadModal({
  open,
  onClose,
  dramaId,
  existingNumbers,
}: {
  open: boolean;
  onClose: () => void;
  dramaId: string;
  existingNumbers: number[];
}) {
  const { items, paused } = useBatchQueue();
  const mine = items.filter((item) => item.dramaId === dramaId);
  const [startInput, setStartInput] = useState(1);
  const [ruleKind, setRuleKind] = useState<string>("episode-en");
  const [customTpl, setCustomTpl] = useState("");
  const seenPick = useRef(new Set<string>());
  const prevOpen = useRef(false);

  const currentRule = (): TitleRule =>
    ruleKind === "custom" ? { kind: "custom", template: customTpl } : { kind: ruleKind as "episode-en" | "episode-cn" };

  // Suggest the next free number each time the dialog opens (not on every
  // table refresh, so an edit the operator is mid-way through is not lost).
  useEffect(() => {
    if (open && !prevOpen.current) {
      setStartInput(batchQueue.nextSuggested(dramaId, existingNumbers));
    }
    prevOpen.current = open;
  }, [open, dramaId, existingNumbers]);

  const onPick: UploadProps["onChange"] = (info) => {
    const fresh: File[] = [];
    for (const entry of info.fileList) {
      if (seenPick.current.has(entry.uid)) continue;
      seenPick.current.add(entry.uid);
      const raw = entry.originFileObj as File | undefined;
      if (raw) fresh.push(raw);
    }
    if (fresh.length === 0) return;
    const res = batchQueue.addFiles({ dramaId, files: fresh, startNumber: startInput, rule: currentRule() });
    if (res.added > 0) setStartInput(res.nextNumber);
    if (res.skippedDup > 0) message.info(`已跳过 ${res.skippedDup} 个重复文件（同名同大小已在队列中）`);
    if (res.rejected.length > 0) {
      const preview = res.rejected.slice(0, 3).join("；");
      message.error(`跳过 ${res.rejected.length} 个文件：${preview}${res.rejected.length > 3 ? "…" : ""}`);
    }
  };

  const commitStart = (value: number | null) => {
    if (value == null || !Number.isFinite(value)) return;
    const actual = batchQueue.renumberFrom({ dramaId, start: value, rule: currentRule(), reserved: existingNumbers });
    setStartInput(actual);
    if (actual !== value) message.info(`第 ${value} 集已被占用，队列从第 ${actual} 集开始`);
  };

  const onRuleChange = (kind: string) => {
    setRuleKind(kind);
    const rule: TitleRule = kind === "custom" ? { kind: "custom", template: customTpl } : { kind: kind as "episode-en" | "episode-cn" };
    batchQueue.retitle(dramaId, rule);
  };

  const onCustomChange = (template: string) => {
    setCustomTpl(template);
    batchQueue.retitle(dramaId, { kind: "custom", template });
  };

  // Ranks for queued rows: the head of the queue is "next up".
  const queueRank = new Map<number, number>();
  let rankCursor = 0;
  for (const item of mine) {
    if (item.status === "queued") queueRank.set(item.uid, ++rankCursor);
  }

  const stats = {
    total: mine.length,
    uploading: mine.filter((item) => item.status === "uploading").length,
    queued: mine.filter((item) => item.status === "queued" || item.status === "probing").length,
    transcoding: mine.filter((item) => item.status === "transcoding").length,
    done: mine.filter((item) => item.status === "done").length,
    failed: mine.filter((item) => item.status === "failed").length,
  };

  const columns: TableColumnsType<BatchItem> = [
    {
      title: "集",
      width: 76,
      render: (_, item) =>
        canEdit(item) ? (
          <InputNumber
            size="small"
            min={1}
            max={2000}
            style={{ width: 64 }}
            value={item.episodeNumber}
            onChange={(value) => {
              if (typeof value === "number") batchQueue.editNumber(item.uid, value);
            }}
          />
        ) : (
          <span>{item.episodeNumber}</span>
        ),
    },
    {
      title: "视频文件",
      ellipsis: true,
      render: (_, item) => (
        <div style={{ minWidth: 0 }}>
          <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={item.file.name}>
            {item.file.name}
          </div>
          <div style={{ fontSize: 12, color: "#8c8c8c", marginTop: 1 }}>
            {formatFileSize(item.file.size)}
            {item.meta
              ? ` · ${formatDuration(item.meta.durationSeconds)}`
              : item.status === "probing"
                ? " · 读取中…"
                : ""}
          </div>
          {item.status === "failed" && item.error && (
            <div style={{ fontSize: 12, color: "#cf1322", marginTop: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={item.error}>
              {item.error}
            </div>
          )}
        </div>
      ),
    },
    {
      title: "标题",
      width: 200,
      render: (_, item) =>
        canEdit(item) ? (
          <Input
            size="small"
            value={item.title}
            maxLength={200}
            onChange={(event) => batchQueue.editTitle(item.uid, event.target.value)}
          />
        ) : (
          <span style={{ display: "inline-block", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", verticalAlign: "bottom" }} title={item.title}>
            {item.title}
          </span>
        ),
    },
    {
      title: "上传状态",
      width: 216,
      render: (_, item) => <StatusCell item={item} queuedRank={queueRank.get(item.uid) ?? null} />,
    },
    {
      title: "操作",
      width: 92,
      render: (_, item) => (
        <Space size={4}>
          {item.status === "failed" && (
            <Button type="link" size="small" style={{ padding: 0 }} onClick={() => batchQueue.retry(item.uid)}>
              重试
            </Button>
          )}
          <Button
            type="text"
            size="small"
            icon={<CloseOutlined />}
            aria-label="移除"
            onClick={() => batchQueue.remove(item.uid)}
          />
        </Space>
      ),
    },
  ];

  const handleCollapse = () => {
    const active = mine.some(isActive);
    onClose();
    if (active) message.info("已收起，上传将在后台继续（请勿刷新页面）");
  };

  return (
    <Modal
      open={open}
      width={880}
      title="批量上传分集"
      maskClosable={false}
      onCancel={handleCollapse}
      footer={
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
          <div style={{ fontSize: 12, color: "#595959" }}>
            共 {stats.total} 个 · 上传中 <span style={{ color: "#185fa5", fontWeight: 500 }}>{stats.uploading} / {MAX_CONCURRENT_UPLOADS}</span>
            {" · "}排队 {stats.queued}
            {" · "}转码中 {stats.transcoding}
            {" · "}已完成 {stats.done}
            {" · "}失败 {stats.failed}
          </div>
          <Space size={8}>
            <Button size="small" disabled={stats.done === 0} onClick={() => batchQueue.clearDone()}>
              清空已完成
            </Button>
            <Tooltip title={paused ? "继续启动排队中的文件" : "正在上传的 2 个会先传完，其余暂停启动"}>
              <Button size="small" onClick={() => batchQueue.setPaused(!paused)}>
                {paused ? "恢复队列" : "暂停队列"}
              </Button>
            </Tooltip>
            <Button size="small" type="primary" onClick={handleCollapse}>
              收起 · 后台上传
            </Button>
          </Space>
        </div>
      }
    >
      {mine.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="上传期间请勿刷新或关闭此页面"
          description="队列仅保存在当前页面：刷新后「排队中」的文件需要重新拖入；已进入 Bunny 转码的不受影响。"
        />
      )}

      <Upload.Dragger
        className="ep-drop"
        multiple
        accept=".mp4,.mov,video/mp4,video/quicktime"
        fileList={[]}
        showUploadList={false}
        beforeUpload={() => false}
        onChange={onPick}
      >
        <div className="ep-drop-inner">
          <CloudUploadOutlined className="ep-drop-icon" />
          <div className="ep-drop-title">拖入视频，或点击选择 — 上传中也能继续追加</div>
          <div className="ep-drop-hint">支持一次多选 · MP4 / MOV · 单个 ≤ 200MB</div>
        </div>
      </Upload.Dragger>

      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap", margin: "12px 0 8px", fontSize: 13, color: "#595959" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          起始集数
          <InputNumber
            size="small"
            min={1}
            max={2000}
            style={{ width: 70 }}
            value={startInput}
            onChange={(value) => {
              if (typeof value === "number") setStartInput(value);
            }}
            onBlur={() => commitStart(startInput)}
          />
        </span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          标题规则
          <Select size="small" style={{ width: 130 }} value={ruleKind} options={RULE_OPTIONS} onChange={onRuleChange} />
        </span>
        {ruleKind === "custom" && (
          <Input
            size="small"
            style={{ width: 220 }}
            placeholder="如：神秘短剧 第{n}集"
            value={customTpl}
            onChange={(event) => onCustomChange(event.target.value)}
          />
        )}
        <span style={{ marginLeft: "auto", fontSize: 12, color: "#8c8c8c" }}>新文件自动接续编号 · 每行可手改</span>
      </div>

      <Table<BatchItem>
        rowKey="uid"
        size="small"
        pagination={false}
        dataSource={mine}
        columns={columns}
        scroll={{ y: 320 }}
        locale={{ emptyText: "还没有文件：把视频拖到上面的区域，或点击选择（可一次多选）" }}
      />
    </Modal>
  );
}

/* ------------------------------ floating pill ------------------------------- */

export function BatchUploadPill({
  dramaId,
  hidden,
  onOpen,
}: {
  dramaId: string;
  hidden: boolean;
  onOpen: () => void;
}) {
  const { items } = useBatchQueue();
  const mine = items.filter((item) => item.dramaId === dramaId);
  const active = mine.filter(isActive);
  if (hidden || active.length === 0) return null;
  const done = mine.filter((item) => item.status === "done").length;
  const uploading = mine.filter((item) => item.status === "uploading").length;
  const queued = mine.filter((item) => item.status === "queued" || item.status === "probing").length;
  const transcoding = mine.filter((item) => item.status === "transcoding").length;
  const parts = [`已完成 ${done}/${mine.length}`];
  if (uploading > 0) parts.push(`上传中 ${uploading}/${MAX_CONCURRENT_UPLOADS}`);
  if (queued > 0) parts.push(`排队 ${queued}`);
  if (transcoding > 0) parts.push(`转码中 ${transcoding}`);

  return (
    <div
      style={{
        position: "fixed",
        right: 24,
        bottom: 24,
        zIndex: 900,
        display: "flex",
        alignItems: "center",
        gap: 12,
        background: "#fff",
        border: "1px solid #f0f0f0",
        borderRadius: 12,
        boxShadow: "0 6px 16px rgba(0, 0, 0, 0.08)",
        padding: "10px 14px",
      }}
    >
      <Tag color="processing" style={{ marginRight: 0 }}>
        批量上传
      </Tag>
      <span style={{ fontSize: 13 }}>{parts.join(" · ")}</span>
      <span style={{ fontSize: 12, color: "#bfbfbf" }}>请勿刷新页面</span>
      <Button size="small" type="primary" onClick={onOpen}>
        打开
      </Button>
    </div>
  );
}
