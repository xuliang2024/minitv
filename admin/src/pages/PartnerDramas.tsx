import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { Link, useNavigate } from "react-router-dom";
import { api, errorText } from "../api";
import { formatTime } from "../hooks";
import { partnerStage, partnerStageColor } from "./partnerStage";

type Row = {
  id: number;
  title: string;
  cover_url: string;
  status: string;
  review_state: string;
  review_note: string | null;
  free_episode_count: number;
  coin_price_per_episode: number;
  episode_count: number;
  play_count: number;
  updated_at: string;
};

type Counts = {
  all: number;
  draft: number;
  pending: number;
  approved: number;
  published: number;
  unpublished: number;
};

const EMPTY: Counts = { all: 0, draft: 0, pending: 0, approved: 0, published: 0, unpublished: 0 };

const CARDS: { key: keyof Counts; label: string; color: string }[] = [
  { key: "all", label: "全部剧集", color: "#101828" },
  { key: "draft", label: "草稿", color: "#667085" },
  { key: "pending", label: "审核中", color: "#B54708" },
  { key: "approved", label: "待上架", color: "#175CD3" },
  { key: "published", label: "已上架", color: "#067647" },
];

export default function PartnerDramas() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Counts>(EMPTY);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<string | undefined>(undefined);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
      if (status) params.set("status", status);
      if (q.trim()) params.set("q", q.trim());
      const res = await api<{
        items: Row[];
        total: number;
        stage_counts?: Counts;
      }>(`/partner/v1/dramas?${params.toString()}`);
      setRows(res.items);
      setTotal(res.total);
      if (res.stage_counts) setCounts({ ...EMPTY, ...res.stage_counts });
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, status, q]);

  useEffect(() => {
    load();
  }, [load]);

  const create = async (values: any) => {
    setCreating(true);
    try {
      const created = await api<{ id: number }>("/partner/v1/dramas", {
        method: "POST",
        body: JSON.stringify({
          title: values.title,
          synopsis: values.synopsis ?? "",
        }),
      });
      message.success("已创建草稿，继续补封面和分集");
      setCreateOpen(false);
      form.resetFields();
      navigate(`/partner/dramas/${created.id}`);
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setCreating(false);
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="large">
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 12,
        }}
      >
        {CARDS.map((card) => {
          const active = card.key === "all" && !status;
          return (
            <div
              key={card.key}
              onClick={() => {
                if (card.key === "all") {
                  setStatus(undefined);
                  setPage(1);
                }
              }}
              style={{
                background: "#fff",
                border: `1px solid ${active ? card.color : "#eaecf0"}`,
                borderRadius: 10,
                padding: "14px 16px",
                cursor: card.key === "all" ? "pointer" : "default",
              }}
            >
              <div style={{ fontSize: 13, color: "#475467" }}>{card.label}</div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginTop: 2 }}>
                <span style={{ fontSize: 24, fontWeight: 700, color: card.color }}>
                  {counts[card.key]}
                </span>
                <span style={{ fontSize: 12, color: "#98a2b3" }}>部</span>
              </div>
            </div>
          );
        })}
      </div>

      <Space wrap>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          + 新建剧集
        </Button>
        <Select
          allowClear
          placeholder="全部状态"
          style={{ width: 160 }}
          value={status}
          onChange={(value) => {
            setStatus(value);
            setPage(1);
          }}
          options={[
            { value: "draft", label: "草稿" },
            { value: "in_review", label: "审核中" },
            { value: "published", label: "已上架" },
            { value: "unpublished", label: "已下架" },
          ]}
        />
        <Input.Search
          allowClear
          placeholder="搜索剧名 / 剧集ID..."
          style={{ width: 280 }}
          onSearch={(value) => {
            setQ(value);
            setPage(1);
          }}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          只显示你名下的剧集；提交审核后由 MiniTV 运营安排上架。
        </Typography.Text>
      </Space>

      <Table<Row>
        rowKey="id"
        loading={loading}
        dataSource={rows}
        scroll={{ x: 980 }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          onChange: (next, size) => {
            setPage(next);
            setPageSize(size);
          },
        }}
        columns={[
          {
            title: "封面 / ID",
            dataIndex: "cover_url",
            width: 110,
            render: (cover: string, row) => (
              <Space size={8}>
                <div
                  style={{
                    width: 40,
                    height: 54,
                    borderRadius: 4,
                    overflow: "hidden",
                    background: "#f2f4f7",
                  }}
                >
                  {cover ? (
                    <img
                      src={cover}
                      alt=""
                      style={{ width: "100%", height: "100%", objectFit: "cover" }}
                    />
                  ) : null}
                </div>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  #{row.id}
                </Typography.Text>
              </Space>
            ),
          },
          {
            title: "剧名",
            dataIndex: "title",
            render: (value: string, row) => (
              <div>
                <Link to={`/partner/dramas/${row.id}`} style={{ fontWeight: 500 }}>
                  {value}
                </Link>
                {row.review_note && (
                  <div style={{ fontSize: 12, color: "#B42318" }}>驳回：{row.review_note}</div>
                )}
              </div>
            ),
          },
          {
            title: "状态",
            key: "stage",
            width: 100,
            render: (_, row) => (
              <Tag color={partnerStageColor(row.status, row.review_state)}>
                {partnerStage(row.status, row.review_state)}
              </Tag>
            ),
          },
          { title: "免费集数", dataIndex: "free_episode_count", width: 90 },
          { title: "单集金币", dataIndex: "coin_price_per_episode", width: 90 },
          { title: "总集数", dataIndex: "episode_count", width: 80 },
          { title: "播放量", dataIndex: "play_count", width: 90 },
          {
            title: "最近更新",
            dataIndex: "updated_at",
            width: 150,
            render: (value: string) => formatTime(value),
          },
          {
            title: "操作",
            key: "actions",
            width: 80,
            render: (_, row) => <Link to={`/partner/dramas/${row.id}`}>进入</Link>,
          },
        ]}
      />

      <Modal
        title="新建剧集"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        okText="创建"
        confirmLoading={creating}
        forceRender
      >
        <Form form={form} layout="vertical" onFinish={create} preserve={false}>
          <Form.Item
            label="剧名"
            name="title"
            rules={[{ required: true, message: "请输入剧名" }]}
            extra="同一供应商内不能重名"
          >
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item label="简介" name="synopsis" extra="可以先留空，提交审核前需 ≥30 字">
            <Input.TextArea rows={3} maxLength={5000} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
