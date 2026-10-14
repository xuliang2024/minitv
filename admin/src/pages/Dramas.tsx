import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Button, Form, Image, Input, Modal, Select, Space, Switch, Table, Tag, Tooltip, Typography, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { api, errorText } from "../api";
import { useConfirm } from "../confirm";
import { useCanWriteContent } from "../me";

type DramaRow = {
  id: number;
  title: string;
  cover_url: string;
  status: string;
  tags: { id: number; name_en: string; name_zh: string }[];
  free_episode_count: number;
  coin_price_per_episode: number;
  episode_count: number;
  play_count: number;
  updated_at: string | null;
};

const STATUS_LABELS: Record<string, { text: string; color: string }> = {
  draft: { text: "草稿", color: "default" },
  in_review: { text: "审核中", color: "orange" },
  published: { text: "已上架", color: "green" },
  unpublished: { text: "已下架", color: "red" },
};

export default function Dramas() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<DramaRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<string | undefined>(undefined);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  // 正在切换上下架的那一行；同一时间只允许一个在途请求
  const [togglingId, setTogglingId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [createForm] = Form.useForm<{ title: string }>();
  const canWrite = useCanWriteContent();
  const { confirm, confirmModal } = useConfirm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
      if (status) params.set("status", status);
      if (q.trim()) params.set("q", q.trim());
      const res = await api<{ total: number; items: DramaRow[] }>(
        `/admin/v1/dramas?${params.toString()}`
      );
      setRows(res.items);
      setTotal(res.total);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, status, q]);

  useEffect(() => {
    load();
  }, [load]);

  /**
   * 列表内快速上下架。状态机（backend/app/modules/content/service.py）只允许
   * published <-> unpublished 这两步互跳，草稿 / 审核中必须先走审核流程，
   * 所以那些行的开关是置灰的，点开关不会误触发 409。
   */
  const togglePublish = async (row: DramaRow, next: boolean) => {
    // 上架方向先做一次最低门槛检查（详情页的上架流程会做完整检查），
    // 免得把没有封面 / 没有分集的空壳剧直接放到线上。
    const missing = next
      ? [!row.cover_url ? "还没有封面" : null, row.episode_count === 0 ? "还没有分集" : null].filter(Boolean)
      : [];
    if (missing.length) {
      confirm({
        title: `《${row.title}》还不完整`,
        okText: "仍然上架",
        cancelText: "取消",
        content: (
          <Typography.Text>
            {missing.join("、")}。上架后用户就能在 App 里看到该剧，确定仍然上架吗？
          </Typography.Text>
        ),
        onOk: () => setPublishStatus(row, next),
      });
      return;
    }
    await setPublishStatus(row, next);
  };

  const setPublishStatus = async (row: DramaRow, next: boolean) => {
    const to_status = next ? "published" : "unpublished";
    setTogglingId(row.id);
    try {
      const res = await api<{ status: string }>(`/admin/v1/dramas/${row.id}/status`, {
        method: "POST",
        body: JSON.stringify({ to_status }),
      });
      message.success(`《${row.title}》已${next ? "上架" : "下架"}`);
      if (status) {
        // 正在按状态筛选，被切走的那一行已不属于当前结果集，重新拉取整页。
        await load();
      } else {
        // 原地更新，避免整页 loading 和行顺序跳动，方便连续操作。
        setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, status: res.status } : r)));
      }
    } catch (e) {
      message.error(`${next ? "上架" : "下架"}失败：${errorText(e)}`);
    } finally {
      setTogglingId(null);
    }
  };

  /** 新建剧集：只要求剧名，接口会建出一条草稿，随后跳到详情页补全信息。 */
  const onCreate = async (values: { title: string }) => {
    setCreating(true);
    try {
      const created = await api<{ id: number; title: string }>("/admin/v1/dramas", {
        method: "POST",
        body: JSON.stringify({ title: values.title.trim() }),
      });
      message.success(`已创建草稿 #${created.id}，补全信息后即可上架`);
      setCreateOpen(false);
      createForm.resetFields();
      navigate(`/dramas/${created.id}`);
    } catch (e) {
      message.error(`创建失败：${errorText(e)}`);
    } finally {
      setCreating(false);
    }
  };

  return (
    <>
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        <Space wrap size="middle" style={{ width: "100%" }}>
          {canWrite && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              新建剧集
            </Button>
          )}
          <Select
            allowClear
            placeholder="状态筛选"
            style={{ width: 140, maxWidth: "100%" }}
            value={status}
            onChange={(v) => {
              setPage(1);
              setStatus(v);
            }}
            options={Object.entries(STATUS_LABELS).map(([value, { text }]) => ({ value, label: text }))}
          />
          <Input.Search
            placeholder="搜索剧名 / ID"
            style={{ width: 260, maxWidth: "100%" }}
            onSearch={(v) => {
              setPage(1);
              setQ(v);
            }}
            allowClear
          />
          <Typography.Text type="secondary">共 {total} 部</Typography.Text>
        </Space>
        <Table<DramaRow>
          rowKey="id"
          loading={loading}
          dataSource={rows}
          scroll={{ x: 1040 }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            onChange: (p, s) => {
              setPage(p);
              setPageSize(s);
            },
          }}
          columns={[
            {
              title: "封面 / ID",
              key: "cover",
              width: 112,
              render: (_, r) => (
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  {r.cover_url ? (
                    <Image
                      src={r.cover_url}
                      width={36}
                      height={48}
                      style={{ objectFit: "cover", borderRadius: 6, flexShrink: 0 }}
                      preview={false}
                    />
                  ) : (
                    <div
                      style={{
                        width: 36,
                        height: 48,
                        borderRadius: 6,
                        background: "#1f1f1f",
                        flexShrink: 0,
                      }}
                    />
                  )}
                  <Typography.Text code style={{ whiteSpace: "nowrap" }}>
                    #{r.id}
                  </Typography.Text>
                </div>
              ),
            },
            {
              title: "剧名 / 标签",
              key: "title",
              minWidth: 220,
              render: (_, r) => (
                <Space direction="vertical" size={2}>
                  <Typography.Text strong>{r.title}</Typography.Text>
                  <Space wrap size={4}>
                    {r.tags.map((t) => (
                      <Tag key={t.id}>{t.name_zh || t.name_en}</Tag>
                    ))}
                  </Space>
                </Space>
              ),
            },
            {
              title: "状态",
              dataIndex: "status",
              width: 156,
              render: (s: string, r) => {
                const meta = STATUS_LABELS[s] ?? { text: s, color: "default" };
                const checked = s === "published";
                const toggleable = s === "published" || s === "unpublished";
                const tip = !toggleable
                  ? s === "draft"
                    ? "草稿：先进入详情页提交审核，通过后才能上架"
                    : "审核中：进入详情页通过并上架"
                  : checked
                    ? "点击下架"
                    : "点击重新上架";
                return (
                  <Space size={8}>
                    <Tooltip title={canWrite ? tip : "当前角色只能查看状态"}>
                      <span style={{ display: "inline-flex" }}>
                        <Switch
                          size="small"
                          checked={checked}
                          disabled={!toggleable || !canWrite}
                          loading={togglingId === r.id}
                          onChange={(next) => togglePublish(r, next)}
                        />
                      </span>
                    </Tooltip>
                    <Tag color={meta.color} style={{ marginInlineEnd: 0 }}>
                      {meta.text}
                    </Tag>
                  </Space>
                );
              },
            },
            { title: "免费集数", dataIndex: "free_episode_count", width: 88 },
            { title: "单集金币", dataIndex: "coin_price_per_episode", width: 88 },
            { title: "集数", dataIndex: "episode_count", width: 64 },
            { title: "播放量", dataIndex: "play_count", width: 76 },
            {
              title: "最近更新",
              dataIndex: "updated_at",
              width: 140,
              render: (v: string | null) => {
                if (!v) return "-";
                const time = v.length >= 19 ? v.slice(11, 19) : "";
                return (
                  <div
                    style={{
                      lineHeight: 1.4,
                      whiteSpace: "nowrap",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    <div>{v.slice(0, 10)}</div>
                    {time && <div style={{ color: "#8c8c8c", fontSize: 12 }}>{time}</div>}
                  </div>
                );
              },
            },
            {
              title: "操作",
              key: "actions",
              width: 90,
              render: (_, r) => (
                <Button type="link" onClick={() => navigate(`/dramas/${r.id}`)}>
                  进入 →
                </Button>
              ),
            },
          ]}
        />
        <Modal
          title="新建剧集"
          open={createOpen}
          okText="创建并补全信息"
          cancelText="取消"
          confirmLoading={creating}
          onCancel={() => {
            setCreateOpen(false);
            createForm.resetFields();
          }}
          onOk={() => createForm.submit()}
          destroyOnClose
        >
          <Form form={createForm} layout="vertical" onFinish={onCreate} preserve={false}>
            <Form.Item
              label="剧名"
              name="title"
              rules={[
                { required: true, whitespace: true, message: "请填写剧名" },
                { max: 200, message: "剧名最多 200 个字" },
              ]}
            >
              <Input
                size="large"
                maxLength={200}
                showCount
                autoFocus
                placeholder="例如：The CEO of Flash Marriage Adores Me"
              />
            </Form.Item>
            <Alert
              type="info"
              showIcon
              message="新剧会先建为草稿"
              description="创建后进入详情页：上传封面 → 填写简介/标签 → 登记分集并上传视频 → 提交审核 → 上架。"
            />
          </Form>
        </Modal>
      </Space>

      {/* 自定义确认弹框（替代 window.confirm），放在 Space 外，避免多出一个空间距 */}
      {confirmModal}
    </>
  );
}
