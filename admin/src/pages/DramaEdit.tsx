import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Steps,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  LinkOutlined,
  PictureOutlined,
  ReloadOutlined,
  SaveOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import type { UploadProps } from "antd";
import { api, errorText } from "../api";
import EpisodesPanel from "./EpisodesPanel";
import { PublishPanel } from "./StagePages";
import { DramaGroupsPanel } from "./Groups";

type Drama = {
  id: number;
  title: string;
  synopsis: string;
  cover_url: string;
  status: string;
  free_episode_count: number;
  coin_price_per_episode: number;
  tags: { id: number; name_en: string; name_zh: string }[];
  episode_count: number;
};

type TagOption = { id: number; name_en: string; name_zh: string; status: string; drama_count: number };

const STATUS_LABELS: Record<string, { text: string; color: string }> = {
  draft: { text: "草稿", color: "default" },
  in_review: { text: "审核中", color: "orange" },
  published: { text: "已上架", color: "green" },
  unpublished: { text: "已下架", color: "red" },
};

const COVER_MAX_BYTES = 5 * 1024 * 1024;
const COVER_TYPES = ["image/jpeg", "image/png", "image/webp"];
const COVER_W = 200;
const COVER_H = 266;

export default function DramaEdit() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const [drama, setDrama] = useState<Drama | null>(null);
  const [tagOptions, setTagOptions] = useState<TagOption[]>([]);
  const [saving, setSaving] = useState(false);
  const [coverBusy, setCoverBusy] = useState<null | "upload" | "delete">(null);
  const [dirty, setDirty] = useState(false);
  const [urlEditing, setUrlEditing] = useState(false);
  const [urlDraft, setUrlDraft] = useState("");
  const [activeTab, setActiveTab] = useState("info");
  const [coverUrl, setCoverUrl] = useState("");
  // 上架流程清单需要知道有多少集已经真正上传了视频
  const [episodeStats, setEpisodeStats] = useState<{ total: number; uploaded: number } | null>(null);

  const load = useCallback(async () => {
    const [d, t] = await Promise.all([
      api<Drama>(`/admin/v1/dramas/${id}`),
      api<{ items: TagOption[] }>("/admin/v1/tags"),
    ]);
    setDrama(d);
    setCoverUrl(d.cover_url ?? "");
    setTagOptions(t.items);
    form.setFieldsValue({
      title: d.title,
      synopsis: d.synopsis,
      cover_url: d.cover_url,
      free_episode_count: d.free_episode_count,
      coin_price_per_episode: d.coin_price_per_episode,
      tag_ids: d.tags.map((x) => x.id),
    });
    setDirty(false);
  }, [id, form]);

  useEffect(() => {
    load().catch((e) => message.error(`加载失败：${errorText(e)}`));
  }, [load]);

  const loadEpisodeStats = useCallback(async () => {
    try {
      const res = await api<{ items: { bunny_video_id: string | null }[] }>(
        `/admin/v1/dramas/${id}/episodes?page_size=200`
      );
      setEpisodeStats({
        total: res.items.length,
        uploaded: res.items.filter((e) => e.bunny_video_id).length,
      });
    } catch {
      setEpisodeStats(null);
    }
  }, [id]);

  // 切换 Tab 时重新盘点（分集页可能刚登记/上传过）
  useEffect(() => {
    loadEpisodeStats();
  }, [loadEpisodeStats, activeTab]);

  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (!dirty) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  const onSave = async (values: any) => {
    setSaving(true);
    try {
      await api(`/admin/v1/dramas/${id}`, { method: "PATCH", body: JSON.stringify(values) });
      message.success("已保存");
      setDirty(false);
      await load();
    } catch (e: any) {
      message.error(`保存失败：${errorText(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const uploadCover = async (file: File) => {
    setCoverBusy("upload");
    try {
      const fd = new FormData();
      fd.append("file", file, file.name);
      const res = await api<{ cover_url: string }>(`/admin/v1/dramas/${id}/cover`, {
        method: "POST",
        body: fd,
      });
      form.setFieldValue("cover_url", res.cover_url);
      setCoverUrl(res.cover_url);
      setDrama((d) => (d ? { ...d, cover_url: res.cover_url } : d));
      setUrlEditing(false);
      message.success("封面已更新");
    } catch (e: any) {
      message.error(`封面上传失败：${errorText(e)}`);
    } finally {
      setCoverBusy(null);
    }
  };

  const beforeUpload: UploadProps["beforeUpload"] = (file) => {
    const okType =
      COVER_TYPES.includes(file.type || "") || /\.(jpe?g|png|webp)$/i.test(file.name || "");
    if (!okType) {
      message.error("封面只支持 JPG / PNG / WebP 格式");
      return Upload.LIST_IGNORE;
    }
    if (file.size > COVER_MAX_BYTES) {
      message.error("封面大小需在 5MB 以内");
      return Upload.LIST_IGNORE;
    }
    void uploadCover(file as unknown as File);
    return Upload.LIST_IGNORE;
  };

  const removeCover = async () => {
    setCoverBusy("delete");
    try {
      await api(`/admin/v1/dramas/${id}/cover`, { method: "DELETE" });
      form.setFieldValue("cover_url", "");
      setCoverUrl("");
      setDrama((d) => (d ? { ...d, cover_url: "" } : d));
      message.success("封面已删除");
    } catch (e: any) {
      message.error(`删除封面失败：${errorText(e)}`);
    } finally {
      setCoverBusy(null);
    }
  };

  if (!drama) {
    return (
      <Card loading>
        <div style={{ height: 320 }} />
      </Card>
    );
  }

  const statusMeta = STATUS_LABELS[drama.status] ?? { text: drama.status, color: "default" };

  // ---- 上架流程：基本信息 → 登记分集 → 上传视频 → 提交上架 ----
  const uploadedCount = episodeStats?.uploaded ?? 0;
  const basicsReady = Boolean(drama.cover_url && drama.synopsis?.trim());
  const videosReady = drama.episode_count > 0 && uploadedCount >= drama.episode_count;
  const issues = [
    !drama.cover_url ? "还没有上传封面" : null,
    !drama.synopsis?.trim() ? "还没有填写简介" : null,
    drama.episode_count === 0
      ? "还没有登记分集"
      : !videosReady
        ? `还有 ${drama.episode_count - uploadedCount} 集没有上传视频`
        : null,
  ].filter((value): value is string => value !== null);

  const flowSteps = [
    {
      key: "info",
      title: "基本信息",
      desc: basicsReady ? "封面 / 简介已填" : "补封面、简介、标签",
      done: basicsReady,
    },
    {
      key: "episodes",
      title: "登记分集",
      desc: drama.episode_count > 0 ? `${drama.episode_count} 集` : "还没有分集",
      done: drama.episode_count > 0,
    },
    {
      key: "episodes",
      title: "上传视频",
      desc:
        drama.episode_count > 0
          ? `${uploadedCount}/${drama.episode_count} 集已上传`
          : "先登记分集",
      done: videosReady,
    },
    {
      key: "publish",
      title: "提交上架",
      desc: "提交审核 → 上架",
      done: drama.status === "published",
    },
  ];
  const nextStepIndex = flowSteps.findIndex((step) => !step.done);

  const uploadProps: UploadProps = {
    accept: "image/jpeg,image/png,image/webp",
    showUploadList: false,
    beforeUpload,
    disabled: coverBusy !== null,
  };

  const infoTab = (
    <Card styles={{ body: { padding: 24 } }}>
      <Form
        form={form}
        layout="vertical"
        onFinish={onSave}
        onValuesChange={() => setDirty(true)}
        requiredMark="optional"
      >
        {/* cover_url is edited through the cover panel; keep it in the form so saves carry it */}
        <Form.Item name="cover_url" hidden>
          <Input />
        </Form.Item>

        <Row gutter={[40, 24]}>
          <Col xs={24} md={8} lg={7}>
            <Space direction="vertical" size={12} style={{ width: "100%" }}>
              <Typography.Text strong>封面</Typography.Text>
              <div style={{ width: COVER_W, maxWidth: "100%" }}>
                <Upload {...uploadProps}>
                  <div
                    style={{
                      position: "relative",
                      width: COVER_W,
                      maxWidth: "100%",
                      height: COVER_H,
                      borderRadius: 12,
                      overflow: "hidden",
                      border: coverUrl ? "1px solid #f0f0f0" : "1px dashed #d9d9d9",
                      background: "#fafafa",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      cursor: coverBusy ? "wait" : "pointer",
                    }}
                  >
                    {coverUrl ? (
                      <img
                        src={coverUrl}
                        alt="封面预览"
                        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                      />
                    ) : (
                      <Space direction="vertical" align="center" size={6} style={{ opacity: 0.55 }}>
                        <PictureOutlined style={{ fontSize: 30 }} />
                        <Typography.Text type="secondary">点击上传封面</Typography.Text>
                      </Space>
                    )}
                    {coverBusy === "upload" && (
                      <div
                        style={{
                          position: "absolute",
                          inset: 0,
                          background: "rgba(255,255,255,0.7)",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        <Spin />
                      </div>
                    )}
                  </div>
                </Upload>

                <Space style={{ marginTop: 12 }} wrap>
                  <Upload {...uploadProps}>
                    <Button
                      size="small"
                      icon={<UploadOutlined />}
                      loading={coverBusy === "upload"}
                      disabled={coverBusy !== null}
                    >
                      {coverUrl ? "替换" : "上传"}
                    </Button>
                  </Upload>
                  <Popconfirm
                    title="删除封面"
                    description="将从 CDN 移除该封面，确定删除？"
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={removeCover}
                    disabled={!coverUrl}
                  >
                    <Button
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      loading={coverBusy === "delete"}
                      disabled={!coverUrl || coverBusy !== null}
                    >
                      删除
                    </Button>
                  </Popconfirm>
                </Space>

                <div style={{ marginTop: 10 }}>
                  <Button
                    type="link"
                    size="small"
                    icon={<LinkOutlined />}
                    style={{ paddingLeft: 0 }}
                    disabled={coverBusy !== null}
                    onClick={() => {
                      if (!urlEditing) setUrlDraft(coverUrl);
                      setUrlEditing(!urlEditing);
                    }}
                  >
                    {urlEditing ? "收起链接登记" : "使用图片链接登记"}
                  </Button>
                  {urlEditing && (
                    <Input.Search
                      style={{ width: COVER_W, maxWidth: "100%" }}
                      placeholder="https://cdn.example/cover.jpg"
                      value={urlDraft}
                      onChange={(e) => setUrlDraft(e.target.value)}
                      enterButton="应用"
                      onSearch={(v) => {
                        form.setFieldValue("cover_url", v.trim());
                        setCoverUrl(v.trim());
                        setDirty(true);
                        setUrlEditing(false);
                      }}
                    />
                  )}
                </div>

                <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 12, marginBottom: 0 }}>
                  支持 JPG / PNG / WebP，不超过 5MB，建议 3:4 竖版，≥ 720×960。
                </Typography.Paragraph>
              </div>
            </Space>
          </Col>

          <Col xs={24} md={16} lg={17}>
            <Form.Item
              label="剧名"
              name="title"
              rules={[{ required: true, message: "剧名必填" }]}
              style={{ maxWidth: 480 }}
            >
              <Input maxLength={200} showCount placeholder="请输入剧名" />
            </Form.Item>

            <Form.Item label="简介" name="synopsis">
              <Input.TextArea rows={6} maxLength={20000} showCount placeholder="请输入剧情简介" />
            </Form.Item>

            <Form.Item label="标签" name="tag_ids" style={{ maxWidth: 640 }}>
              <Select
                mode="multiple"
                placeholder="选择标签"
                allowClear
                optionFilterProp="label"
                options={tagOptions.map((t) => ({
                  value: t.id,
                  label: `${t.name_zh || t.name_en}（${t.name_en}）`,
                }))}
              />
            </Form.Item>

            <Row gutter={24} style={{ maxWidth: 640 }}>
              <Col span={12}>
                <Form.Item
                  label="免费集数"
                  name="free_episode_count"
                  tooltip="用户可直接观看的集数，超出的集数需要金币解锁"
                >
                  <InputNumber min={0} style={{ width: "100%" }} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item
                  label="单集金币"
                  name="coin_price_per_episode"
                  tooltip="解锁单集所需金币"
                >
                  <InputNumber min={0} style={{ width: "100%" }} />
                </Form.Item>
              </Col>
            </Row>

            <Divider style={{ margin: "8px 0 16px" }} />

            <Space>
              <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>
                保存
              </Button>
              <Button
                icon={<ReloadOutlined />}
                disabled={!dirty || saving}
                onClick={() => load().catch((e) => message.error(`重置失败：${errorText(e)}`))}
              >
                重置
              </Button>
              {dirty && <Typography.Text type="warning">有未保存的修改</Typography.Text>}
            </Space>
          </Col>
        </Row>
      </Form>
    </Card>
  );

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card styles={{ body: { padding: "16px 24px" } }}>
        <Space wrap size="middle">
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/dramas")}>
            返回列表
          </Button>
          <Divider type="vertical" style={{ height: 24 }} />
          <Typography.Title level={4} style={{ margin: 0 }}>
            {drama.title}
          </Typography.Title>
          <Tag color={statusMeta.color}>{statusMeta.text}</Tag>
          <Typography.Text type="secondary">
            ID #{drama.id} · {drama.episode_count} 集
          </Typography.Text>
        </Space>
      </Card>

      {drama.status !== "published" && (
        <Card
          size="small"
          title="上架流程"
          extra={
            nextStepIndex >= 0 ? (
              <Button
                type="link"
                style={{ paddingInline: 0 }}
                onClick={() => setActiveTab(flowSteps[nextStepIndex].key)}
              >
                去完成：{flowSteps[nextStepIndex].title} →
              </Button>
            ) : null
          }
        >
          <Steps
            size="small"
            current={nextStepIndex === -1 ? flowSteps.length : nextStepIndex}
            items={flowSteps.map((step, index) => ({
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
          { key: "episodes", label: "分集", children: <EpisodesPanel dramaId={id!} /> },
          { key: "groups", label: "首页分组", children: <DramaGroupsPanel dramaId={id!} /> },
          {
            key: "publish",
            label: "发布与下架",
            children: (
              <PublishPanel
                dramaId={id!}
                dramaTitle={drama.title}
                episodeCount={drama.episode_count}
                status={drama.status}
                issues={issues}
              />
            ),
          },
        ]}
      />
    </Space>
  );
}
