import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Image, Input, Select, Space, Table, Tag, Typography } from "antd";
import { api } from "../api";

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

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space wrap>
        <Select
          allowClear
          placeholder="状态筛选"
          style={{ width: 140 }}
          value={status}
          onChange={(v) => {
            setPage(1);
            setStatus(v);
          }}
          options={Object.entries(STATUS_LABELS).map(([value, { text }]) => ({ value, label: text }))}
        />
        <Input.Search
          placeholder="搜索剧名 / ID"
          style={{ width: 260 }}
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
            width: 92,
            render: (_, r) => (
              <Space>
                {r.cover_url ? (
                  <Image src={r.cover_url} width={36} height={48} style={{ objectFit: "cover", borderRadius: 6 }} preview={false} />
                ) : (
                  <div style={{ width: 36, height: 48, borderRadius: 6, background: "#1f1f1f" }} />
                )}
                <Typography.Text code>#{r.id}</Typography.Text>
              </Space>
            ),
          },
          {
            title: "剧名 / 标签",
            key: "title",
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
            width: 96,
            render: (s: string) => {
              const meta = STATUS_LABELS[s] ?? { text: s, color: "default" };
              return <Tag color={meta.color}>{meta.text}</Tag>;
            },
          },
          { title: "免费集数", dataIndex: "free_episode_count", width: 90 },
          { title: "单集金币", dataIndex: "coin_price_per_episode", width: 90 },
          { title: "集数", dataIndex: "episode_count", width: 70 },
          { title: "播放量", dataIndex: "play_count", width: 80 },
          {
            title: "最近更新",
            dataIndex: "updated_at",
            width: 150,
            render: (v: string | null) => (v ? v.slice(0, 19).replace("T", " ") : "-"),
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
    </Space>
  );
}
