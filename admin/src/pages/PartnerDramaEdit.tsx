import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Steps,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  ReloadOutlined,
  SaveOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { api, errorText } from "../api";
import { useLoad, formatTime } from "../hooks";
import {
  MAX_UPLOAD_BYTES,
  type UploadSession,
  directUpload,
  formatDuration,
  formatFileSize,
  probeVideo,
} from "./episodeUpload";
import { partnerStage, partnerStageColor } from "./partnerStage";

type Episode = {
  id: number;
  episode_number: number;
  title: string;
  status: string;
  upload_status: string | null;
  duration_seconds: number;
  size_bytes: number;
  /** Server-side flag: only an episode with a Bunny video can be previewed. */
  has_video?: boolean;
};

type EpisodePreview = {
  open: boolean;
  /** Bunny iframe embed URL; absent while signing or after a failure. */
  url?: string;
  title: string;
};

type Drama = {
  id: number;
  title: string;
  synopsis: string;
  cover_url: string;
  status: string;
  review_state: string;
  review_note: string | null;
  free_episode_count: number;
  coin_price_per_episode: number;
  episode_count: number;
  editable: boolean;
  submitted_at: string | null;
  reviewed_at: string | null;
  updated_at: string;
  episodes: Episode[];
  tag_ids: number[];
};

const EPISODE_STATUS: Record<string, { text: string; color?: string }> = {
  not_uploaded: { text: "待上传" },
  transcoding: { text: "转码中", color: "orange" },
  transcode_failed: { text: "转码失败", color: "red" },
  in_review: { text: "已就绪", color: "green" },
  published: { text: "已上架", color: "green" },
  unpublished: { text: "已下架" },
};

const COVER_W = 168;
const COVER_H = 224;

export default function PartnerDramaEdit() {
  const { id } = useParams();
  const navigate = useNavigate();
  const dramaId = Number(id);

  const { data: drama, reload, loading } = useLoad<Drama | null>(
    `/partner/v1/dramas/${dramaId}`,
    null,
    { enabled: Number.isFinite(dramaId) }
  );
  const { data: tags } = useLoad<{ items: { id: number; name_zh: string }[] }>(
    "/partner/v1/tags",
    { items: [] }
  );

  const [form] = Form.useForm();
  const [activeTab, setActiveTab] = useState("info");
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [coverBusy, setCoverBusy] = useState(false);
  const [episodeOpen, setEpisodeOpen] = useState(false);
  const [episodeForm] = Form.useForm();
  const [editing, setEditing] = useState<Episode | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editForm] = Form.useForm();
  const [uploadingId, setUploadingId] = useState<number | null>(null);
  const [progress, setProgress] = useState(0);
  const [preview, setPreview] = useState<EpisodePreview>({ open: false, title: "" });
  const closePreview = () => setPreview({ open: false, title: "" });
  const fileRef = useRef<HTMLInputElement | null>(null);
  const uploadTarget = useRef<number | null>(null);

  useEffect(() => {
    if (uploadingId === null) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [uploadingId]);

  const floor = drama ? Math.min(5, drama.episode_count) : 0;
  const coverUrl = drama?.cover_url ?? "";

  useEffect(() => {
    if (!drama) return;
    const cap = Math.max(drama.episode_count, 1);
    form.setFieldsValue({
      title: drama.title,
      synopsis: drama.synopsis,
      free_episode_count: Math.min(drama.free_episode_count, cap),
      tag_ids: drama.tag_ids ?? [],
    });
    setDirty(false);
  }, [drama, form]);

  const tagOptions = useMemo(
    () => tags.items.map((tag) => ({ value: tag.id, label: tag.name_zh })),
    [tags.items]
  );

  const save = async (silent = false) => {
    if (!drama) return true;
    const values = await form.validateFields();
    setSaving(true);
    try {
      await api(`/partner/v1/dramas/${drama.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: values.title,
          synopsis: values.synopsis,
          free_episode_count: values.free_episode_count,
          tag_ids: values.tag_ids ?? [],
        }),
      });
      setDirty(false);
      if (!silent) message.success("已保存");
      await reload();
      return true;
    } catch (e) {
      message.error(errorText(e));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const submit = async () => {
    if (!drama) return;
    const free = Number(form.getFieldValue("free_episode_count") ?? 0);
    if (drama.episode_count > 0 && free > drama.episode_count) {
      message.error(`免费集数不能超过总集数（当前 ${drama.episode_count} 集），请改小并保存`);
      return;
    }
    if (dirty) {
      const ok = await save(true);
      if (!ok) return;
    }
    Modal.confirm({
      title: "提交审核",
      content: "提交后不可修改，需等 MiniTV 运营审核。确认提交？",
      okText: "提交",
      onOk: async () => {
        setSubmitting(true);
        try {
          await api(`/partner/v1/dramas/${drama.id}/submit`, { method: "POST" });
          message.success("已提交，等待审核");
          await reload();
        } catch (e) {
          message.error(errorText(e));
          throw e;
        } finally {
          setSubmitting(false);
        }
      },
    });
  };

  const removeDrama = () => {
    if (!drama) return;
    Modal.confirm({
      title: `删除《${drama.title}》？`,
      content: "只有草稿且未上传视频的剧集可以删除，删除后不可恢复。",
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api(`/partner/v1/dramas/${drama.id}`, { method: "DELETE" });
          message.success("已删除");
          navigate("/partner/dramas");
        } catch (e) {
          message.error(errorText(e));
          throw e;
        }
      },
    });
  };

  const uploadCover = async (file: File) => {
    if (!drama) return;
    if (file.size > 5 * 1024 * 1024) {
      message.error("封面不能超过 5MB");
      return;
    }
    const body = new FormData();
    body.append("file", file);
    setCoverBusy(true);
    try {
      await api(`/partner/v1/dramas/${drama.id}/cover`, { method: "POST", body });
      message.success("封面已更新");
      await reload();
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setCoverBusy(false);
    }
  };

  const addEpisode = async (values: any) => {
    if (!drama) return;
    try {
      await api(`/partner/v1/dramas/${drama.id}/episodes`, {
        method: "POST",
        body: JSON.stringify({
          episode_number: values.episode_number,
          title: (values.title ?? "").trim(),
        }),
      });
      setEpisodeOpen(false);
      episodeForm.resetFields();
      await reload();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const openPreview = async (episode: Episode) => {
    try {
      const signed = await api<{ url: string; embed_url?: string }>(
        `/partner/v1/episodes/${episode.id}/preview-url`
      );
      setPreview({
        open: true,
        url: signed.embed_url ?? signed.url,
        title: `预览 · 第 ${episode.episode_number} 集`,
      });
    } catch (e) {
      message.error(`预览失败：${errorText(e)}`);
    }
  };

  const openEdit = (episode: Episode) => {
    setEditing(episode);
    editForm.setFieldsValue({ episode_number: episode.episode_number, title: episode.title });
    setEditOpen(true);
  };

  const saveEpisode = async (values: any) => {
    if (!editing) return;
    try {
      await api(`/partner/v1/episodes/${editing.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          episode_number: values.episode_number,
          title: (values.title ?? "").trim(),
        }),
      });
      setEditOpen(false);
      await reload();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const removeEpisode = (episode: Episode) => {
    Modal.confirm({
      title: `删除第 ${episode.episode_number} 集？`,
      content: "只有未上传视频的分集可以删除，删除后不可恢复。",
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api(`/partner/v1/episodes/${episode.id}`, { method: "DELETE" });
          await reload();
        } catch (e) {
          message.error(errorText(e));
          throw e;
        }
      },
    });
  };

  const pickFile = (episode: Episode) => {
    uploadTarget.current = episode.id;
    fileRef.current?.click();
  };

  const pollStatus = async (episodeId: number) => {
    for (let attempt = 0; attempt < 24; attempt += 1) {
      try {
        const res = await api<{ status: string }>(
          `/partner/v1/episodes/${episodeId}/upload-status`
        );
        if (res.status !== "transcoding") return res.status;
      } catch {
        /* keep polling: bytes are already at Bunny */
      }
      await new Promise((resolve) => setTimeout(resolve, 5000));
    }
    return "transcoding";
  };

  const onFilePicked = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    const episodeId = uploadTarget.current;
    if (!file || !episodeId || !drama) return;
    if (file.size > MAX_UPLOAD_BYTES) {
      message.error("单个视频不能超过 200MB");
      return;
    }
    setUploadingId(episodeId);
    setProgress(0);
    try {
      const meta = await probeVideo(file);
      const session = await api<UploadSession>(
        `/partner/v1/episodes/${episodeId}/upload-session`,
        {
          method: "POST",
          body: JSON.stringify({
            filename: file.name,
            mime: file.type || "video/mp4",
            size_bytes: file.size,
            width: meta.width,
            height: meta.height,
            duration_seconds: meta.durationSeconds,
          }),
        }
      );
      await directUpload(session, file, setProgress, () => undefined);
      message.info("上传完成，等待转码…");
      const final = await pollStatus(episodeId);
      if (final === "transcode_failed") message.error("转码失败，请换文件重试");
      else if (final === "in_review") message.success("该集已就绪，可以提交审核");
      else message.info("Bunny 仍在转码，稍后刷新查看");
      await reload();
    } catch (e) {
      // Release the session server-side: create_upload_session already moved
      // the episode to transcoding, so a silent abort would look like a
      // never-ending transcode and block retries.
      try {
        await api(`/partner/v1/episodes/${episodeId}/upload-failed`, { method: "POST" });
      } catch {
        /* the stale sweeper will catch it */
      }
      message.error(errorText(e));
      await reload();
    } finally {
      setUploadingId(null);
      setProgress(0);
    }
  };

  const cancelUpload = async (episode: Episode) => {
    try {
      const res = await api<{ updated?: boolean; status: string }>(
        `/partner/v1/episodes/${episode.id}/upload-failed`,
        { method: "POST" }
      );
      if (res.updated === false) message.info("文件其实已经传完，等待转码即可");
      else message.success("已取消，可以重新上传");
      await reload();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  if (loading && !drama) return <Card loading />;
  if (!drama) return <Alert type="error" message="剧集不存在或无权访问" />;

  const readyEpisodes = drama.episodes.filter((item) => item.status === "in_review").length;
  const steps = [
    {
      title: "基本信息",
      desc: "封面 + 简介 ≥30 字",
      done: Boolean(coverUrl) && (drama.synopsis || "").trim().length >= 30,
    },
    {
      title: "分集",
      desc: `至少 1 集且全部转码完成（${readyEpisodes}/${drama.episode_count}）`,
      done: drama.episode_count > 0 && drama.episodes.every((e) => e.status === "in_review"),
    },
    {
      title: "提交审核",
      desc: drama.review_state === "pending" ? "运营审核中" : "等待提交",
      done: ["pending", "approved", "rejected"].includes(drama.review_state),
    },
    {
      title: "上架",
      desc: drama.status === "published" ? "已上架" : "运营安排",
      done: drama.status === "published",
    },
  ];
  const nextStepIndex = steps.findIndex((step) => !step.done);
  const stageTag = (
    <Tag color={partnerStageColor(drama.status, drama.review_state)}>
      {partnerStage(drama.status, drama.review_state)}
    </Tag>
  );

  const infoTab = (
    <Card styles={{ body: { padding: 24 } }}>
      <Form form={form} layout="vertical" onValuesChange={() => setDirty(true)}>
        <Row gutter={[40, 24]}>
          <Col xs={24} md={8} lg={7}>
            <Space direction="vertical" size={12} style={{ width: "100%" }}>
              <Typography.Text strong>封面</Typography.Text>
              <div style={{ width: COVER_W, maxWidth: "100%" }}>
                <div
                  style={{
                    position: "relative",
                    width: COVER_W,
                    height: COVER_H,
                    borderRadius: 12,
                    overflow: "hidden",
                    border: coverUrl ? "1px solid #f0f0f0" : "1px dashed #d9d9d9",
                    background: "#fafafa",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  {coverUrl ? (
                    <img
                      src={coverUrl}
                      alt="封面预览"
                      style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                    />
                  ) : (
                    <Typography.Text type="secondary">暂无封面</Typography.Text>
                  )}
                </div>
                <Button
                  size="small"
                  icon={<UploadOutlined />}
                  style={{ marginTop: 12 }}
                  loading={coverBusy}
                  disabled={!drama.editable}
                  onClick={() => document.getElementById("partner-cover-input")?.click()}
                >
                  {coverUrl ? "替换封面" : "上传封面"}
                </Button>
                <Typography.Paragraph
                  type="secondary"
                  style={{ fontSize: 12, marginTop: 12, marginBottom: 0 }}
                >
                  支持 JPG / PNG / WebP，≤5MB，建议 3:4 竖版（如 720×960）。
                </Typography.Paragraph>
              </div>
            </Space>
            <input
              id="partner-cover-input"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              style={{ display: "none" }}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) uploadCover(file);
              }}
            />
          </Col>

          <Col xs={24} md={16} lg={17}>
            <Form.Item
              label="剧名"
              name="title"
              rules={[{ required: true, message: "剧名必填" }]}
              style={{ maxWidth: 480 }}
            >
              <Input maxLength={200} showCount disabled={!drama.editable} />
            </Form.Item>
            <Form.Item label="简介" name="synopsis">
              <Input.TextArea
                rows={6}
                maxLength={5000}
                showCount
                disabled={!drama.editable}
                placeholder="提交审核前至少 30 字"
              />
            </Form.Item>
            <Form.Item label="标签" name="tag_ids" style={{ maxWidth: 640 }}>
              <Select
                mode="multiple"
                allowClear
                disabled={!drama.editable}
                options={tagOptions}
                placeholder="从平台标签库选择"
                optionFilterProp="label"
              />
            </Form.Item>
            <Row gutter={24} style={{ maxWidth: 640 }}>
              <Col span={12}>
                <Form.Item
                  label="免费集数"
                  name="free_episode_count"
                  extra={`最少 ${floor} 集 · 最多 ${drama.episode_count || 5} 集`}
                >
                  <InputNumber
                    min={0}
                    max={Math.max(drama.episode_count, 1)}
                    disabled={!drama.editable}
                    style={{ width: "100%" }}
                  />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="单集金币" extra="平台定价，供应商不可修改">
                  <Input value={`${drama.coin_price_per_episode} 金币`} disabled />
                </Form.Item>
              </Col>
            </Row>
            {drama.editable && (
              <Space>
                <Button
                  type="primary"
                  icon={<SaveOutlined />}
                  loading={saving}
                  onClick={() => save()}
                >
                  保存
                </Button>
                <Button
                  icon={<ReloadOutlined />}
                  disabled={!dirty || saving}
                  onClick={() => reload()}
                >
                  重置
                </Button>
                {dirty && <Typography.Text type="warning">有未保存的修改</Typography.Text>}
              </Space>
            )}
          </Col>
        </Row>
      </Form>
    </Card>
  );

  const episodesTab = (
    <Card
      styles={{ body: { padding: 24 } }}
      title={`分集（${drama.episode_count}）`}
      extra={
        <Button disabled={!drama.editable} onClick={() => setEpisodeOpen(true)}>
          + 添加分集
        </Button>
      }
    >
      <Table<Episode>
        rowKey="id"
        size="middle"
        pagination={false}
        dataSource={drama.episodes}
        locale={{ emptyText: "还没有分集，先添加一集再上传视频" }}
        columns={[
          { title: "集", dataIndex: "episode_number", width: 70 },
          { title: "标题", dataIndex: "title" },
          {
            title: "状态",
            key: "status",
            width: 120,
            render: (_, row) => {
              if (uploadingId === row.id) return <Tag color="blue">上传中 {progress}%</Tag>;
              if (row.status === "transcoding" && row.upload_status === "created") {
                return <Tag color="blue">等待上传完成</Tag>;
              }
              const meta = EPISODE_STATUS[row.status] ?? { text: row.status };
              return <Tag color={meta.color}>{meta.text}</Tag>;
            },
          },
          {
            title: "时长",
            dataIndex: "duration_seconds",
            width: 80,
            render: (value: number) => formatDuration(value),
          },
          {
            title: "大小",
            dataIndex: "size_bytes",
            width: 90,
            render: (value: number) => (value ? formatFileSize(value) : "-"),
          },
          {
            title: "操作",
            key: "actions",
            width: 230,
            render: (_, row) => (
              <Space size={4}>
                <Button
                  type="link"
                  size="small"
                  disabled={!row.has_video}
                  onClick={() => openPreview(row)}
                >
                  预览
                </Button>
                <Button
                  type="link"
                  size="small"
                  disabled={!drama.editable || uploadingId !== null}
                  onClick={() => pickFile(row)}
                >
                  {row.status === "not_uploaded" ? "上传" : "替换"}
                </Button>
                {row.status === "transcoding" && row.upload_status === "created" && (
                  <Button
                    type="link"
                    size="small"
                    danger
                    disabled={uploadingId !== null}
                    onClick={() => cancelUpload(row)}
                  >
                    取消上传
                  </Button>
                )}
                <Button
                  type="link"
                  size="small"
                  disabled={!drama.editable}
                  onClick={() => openEdit(row)}
                >
                  编辑
                </Button>
                {row.status === "not_uploaded" && (
                  <Button type="link" size="small" danger onClick={() => removeEpisode(row)}>
                    删除
                  </Button>
                )}
              </Space>
            ),
          },
        ]}
      />
      {uploadingId !== null && (
        <>
          <Divider style={{ margin: "12px 0" }} />
          <Progress percent={progress} size="small" />
        </>
      )}
      <input
        ref={fileRef}
        type="file"
        accept="video/mp4,video/quicktime"
        style={{ display: "none" }}
        onChange={onFilePicked}
      />
    </Card>
  );

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card styles={{ body: { padding: "16px 24px" } }}>
        <Space wrap size="middle">
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/partner/dramas")}>
            返回列表
          </Button>
          <Divider type="vertical" style={{ height: 24 }} />
          <Typography.Title level={4} style={{ margin: 0 }}>
            {drama.title}
          </Typography.Title>
          {stageTag}
          <Typography.Text type="secondary">
            ID #{drama.id} · {drama.episode_count} 集
          </Typography.Text>
          <span style={{ flex: 1 }} />
          {drama.editable && (
            <Button danger icon={<DeleteOutlined />} onClick={removeDrama}>
              删除草稿
            </Button>
          )}
          <Button onClick={submit} loading={submitting} disabled={!drama.editable}>
            提交审核
          </Button>
        </Space>
      </Card>

      {drama.review_note && (
        <Alert type="error" showIcon message={`驳回原因：${drama.review_note}`} />
      )}
      {!drama.editable && (
        <Alert
          type="info"
          showIcon
          message={
            drama.review_state === "pending"
              ? "已提交审核，审核期间不可修改。"
              : "审核通过或已上架后内容锁定，如需调整请联系运营。"
          }
        />
      )}

      {drama.status !== "published" && (
        <Card
          size="small"
          title="上架流程"
          extra={
            nextStepIndex >= 0 ? (
              <Button
                type="link"
                style={{ paddingInline: 0 }}
                onClick={() => setActiveTab(nextStepIndex === 1 ? "episodes" : "info")}
              >
                去完成：{steps[nextStepIndex].title} →
              </Button>
            ) : null
          }
        >
          <Steps
            size="small"
            current={nextStepIndex === -1 ? steps.length : nextStepIndex}
            items={steps.map((step, index) => ({
              title: step.title,
              description: step.desc,
              status: step.done ? "finish" : index === nextStepIndex ? "process" : "wait",
            }))}
          />
        </Card>
      )}

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          { key: "info", label: "基本信息", children: infoTab },
          { key: "episodes", label: "分集", children: episodesTab },
        ]}
      />

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提交前需：封面已上传、简介 ≥30 字、所有分集转码完成、免费集数不少于 {floor} 集。
      </Typography.Text>

      <Modal
        title="添加分集"
        open={episodeOpen}
        onCancel={() => setEpisodeOpen(false)}
        onOk={() => episodeForm.submit()}
        okText="添加"
        forceRender
      >
        <Form form={episodeForm} layout="vertical" onFinish={addEpisode} preserve={false}>
          <Form.Item label="集号" name="episode_number" extra="留空自动 +1">
            <InputNumber min={1} max={300} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item label="标题" name="title" extra="留空自动生成「第 N 集」">
            <Input maxLength={200} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={editing ? `编辑第 ${editing.episode_number} 集` : "编辑分集"}
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => editForm.submit()}
        okText="保存"
        forceRender
      >
        <Form form={editForm} layout="vertical" onFinish={saveEpisode} preserve={false}>
          <Form.Item label="集号" name="episode_number" rules={[{ required: true }]}>
            <InputNumber min={1} max={300} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item label="标题" name="title" rules={[{ required: true, message: "请输入标题" }]}>
            <Input maxLength={200} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        open={preview.open}
        title={preview.title}
        footer={null}
        width={460}
        centered
        destroyOnHidden
        onCancel={closePreview}
        styles={{ body: { padding: 0, background: "#000" } }}
      >
        {preview.url ? (
          <iframe
            src={preview.url}
            title="episode preview"
            style={{ display: "block", width: "100%", height: "74vh", border: 0, background: "#000" }}
            allow="autoplay; fullscreen; encrypted-media; picture-in-picture"
            allowFullScreen
          />
        ) : null}
      </Modal>
    </Space>
  );
}
