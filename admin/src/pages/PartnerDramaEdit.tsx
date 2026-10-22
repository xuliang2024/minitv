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
  Table,
  Tag,
  Typography,
  message,
} from "antd";
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

type Episode = {
  id: number;
  episode_number: number;
  title: string;
  status: string;
  duration_seconds: number;
  size_bytes: number;
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

function stateTag(drama: Drama) {
  if (drama.review_state === "rejected") return <Tag color="red">已驳回</Tag>;
  if (drama.review_state === "pending") return <Tag color="orange">待审核</Tag>;
  if (drama.status === "published") return <Tag color="green">已上架</Tag>;
  if (drama.status === "unpublished") return <Tag>已下架</Tag>;
  if (drama.review_state === "approved") return <Tag color="blue">已通过待上架</Tag>;
  return <Tag>草稿</Tag>;
}

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
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [coverBusy, setCoverBusy] = useState(false);
  const [episodeOpen, setEpisodeOpen] = useState(false);
  const [episodeForm] = Form.useForm();
  const [uploadingId, setUploadingId] = useState<number | null>(null);
  const [progress, setProgress] = useState(0);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const uploadTarget = useRef<number | null>(null);

  const floor = drama ? Math.min(5, drama.episode_count) : 0;

  useEffect(() => {
    if (!drama) return;
    // Submission rejects free > episode_count. Clamp the form so a one-episode
    // drama never starts at the default 5 and surprises the supplier at submit.
    const cap = Math.max(drama.episode_count, 1);
    form.setFieldsValue({
      title: drama.title,
      synopsis: drama.synopsis,
      free_episode_count: Math.min(drama.free_episode_count, cap),
      tag_ids: drama.tag_ids ?? [],
    });
  }, [drama, form]);

  const tagOptions = useMemo(
    () => tags.items.map((tag) => ({ value: tag.id, label: tag.name_zh })),
    [tags.items]
  );

  const save = async () => {
    if (!drama) return;
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
      message.success("已保存");
      await reload();
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const submit = () => {
    if (!drama) return;
    const free = Number(form.getFieldValue("free_episode_count") ?? 0);
    if (drama.episode_count > 0 && free > drama.episode_count) {
      message.error(`免费集数不能超过总集数（当前 ${drama.episode_count} 集），请改小并保存`);
      return;
    }
    Modal.confirm({
      title: "提交审核",
      content: "提交后不可再修改，需等 MiniTV 运营审核。确认提交？",
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
      message.error(errorText(e));
    } finally {
      setUploadingId(null);
      setProgress(0);
    }
  };

  if (loading && !drama) return <Card loading />;
  if (!drama) return <Alert type="error" message="剧集不存在或无权访问" />;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Space wrap>
        <Link to="/partner/dramas">← 返回我的剧集</Link>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {drama.title}
        </Typography.Title>
        {stateTag(drama)}
        <Typography.Text type="secondary">
          {drama.editable ? "草稿可编辑" : "当前状态只读"}
        </Typography.Text>
        <span style={{ flex: 1 }} />
        <Button onClick={save} loading={saving} disabled={!drama.editable}>
          保存
        </Button>
        <Button type="primary" onClick={submit} loading={submitting} disabled={!drama.editable}>
          提交审核
        </Button>
      </Space>

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

      <Row gutter={12}>
        <Col xs={24} lg={10}>
          <Card size="small" title="基本信息">
            <Form form={form} layout="vertical" disabled={!drama.editable}>
              <Form.Item
                label="剧名"
                name="title"
                rules={[{ required: true, message: "请输入剧名" }]}
              >
                <Input maxLength={200} />
              </Form.Item>
              <Form.Item
                label="简介"
                name="synopsis"
                extra="提交审核时至少 30 字"
                rules={[{ max: 5000 }]}
              >
                <Input.TextArea rows={5} />
              </Form.Item>
              <Form.Item label="标签" name="tag_ids">
                <Select
                  mode="multiple"
                  allowClear
                  options={tagOptions}
                  placeholder="从平台标签库选择"
                  optionFilterProp="label"
                />
              </Form.Item>
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item
                    label="免费集数"
                    name="free_episode_count"
                    extra={`最少 ${floor} 集 · 最多 ${drama.episode_count || 5} 集`}
                  >
                    <InputNumber
                      min={0}
                      max={Math.max(drama.episode_count, 1)}
                      style={{ width: "100%" }}
                    />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="单集价格" extra="平台定价，不可修改">
                    <Input value={`${drama.coin_price_per_episode} 金币`} disabled />
                  </Form.Item>
                </Col>
              </Row>
            </Form>
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card
            size="small"
            title={
              <Space>
                <span>封面</span>
                {!drama.cover_url && <Tag color="orange">未上传</Tag>}
              </Space>
            }
          >
            <Space align="start" size="middle">
              <div
                style={{
                  width: 108,
                  height: 144,
                  borderRadius: 6,
                  background: "#f5f5f5",
                  overflow: "hidden",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "#bfbfbf",
                  fontSize: 12,
                }}
              >
                {drama.cover_url ? (
                  <img
                    src={drama.cover_url}
                    alt="封面"
                    style={{ width: "100%", height: "100%", objectFit: "cover" }}
                  />
                ) : (
                  "无封面"
                )}
              </div>
              <Space direction="vertical">
                <Button
                  loading={coverBusy}
                  disabled={!drama.editable}
                  onClick={() => document.getElementById("partner-cover-input")?.click()}
                >
                  上传 / 替换封面
                </Button>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  JPG / PNG / WebP，≤5MB，建议 600×800
                </Typography.Text>
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
            </Space>
          </Card>

          <Card
            size="small"
            style={{ marginTop: 12 }}
            title={`分集（${drama.episode_count}）`}
            extra={
              <Button size="small" disabled={!drama.editable} onClick={() => setEpisodeOpen(true)}>
                + 添加分集
              </Button>
            }
          >
            <Table<Episode>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={drama.episodes}
              locale={{ emptyText: "还没有分集，先添加一集再上传视频" }}
              columns={[
                { title: "集", dataIndex: "episode_number", width: 54 },
                { title: "标题", dataIndex: "title" },
                {
                  title: "状态",
                  key: "status",
                  width: 120,
                  render: (_, row) => {
                    if (uploadingId === row.id)
                      return <Tag color="blue">上传中 {progress}%</Tag>;
                    const meta = EPISODE_STATUS[row.status] ?? { text: row.status };
                    return <Tag color={meta.color}>{meta.text}</Tag>;
                  },
                },
                {
                  title: "时长",
                  dataIndex: "duration_seconds",
                  width: 70,
                  render: (value: number) => formatDuration(value),
                },
                {
                  title: "大小",
                  dataIndex: "size_bytes",
                  width: 80,
                  render: (value: number) => (value ? formatFileSize(value) : "-"),
                },
                {
                  title: "操作",
                  key: "actions",
                  width: 150,
                  render: (_, row) => (
                    <Space size={4}>
                      <Button
                        type="link"
                        size="small"
                        disabled={!drama.editable || uploadingId !== null}
                        onClick={() => pickFile(row)}
                      >
                        {row.status === "not_uploaded" ? "上传" : "替换"}
                      </Button>
                      {row.status === "not_uploaded" && (
                        <Button
                          type="link"
                          size="small"
                          danger
                          disabled={!drama.editable}
                          onClick={() => removeEpisode(row)}
                        >
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
                <Divider style={{ margin: "8px 0" }} />
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
        </Col>
      </Row>

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

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提交前需：封面已上传、简介 ≥30 字、所有分集转码完成、免费集数不少于 {floor} 集。
      </Typography.Text>
    </Space>
  );
}
