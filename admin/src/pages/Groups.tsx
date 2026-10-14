import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button, Form, Input, Modal, Popconfirm, Space, Table, Tag, Typography, message } from "antd";
import { ArrowDownOutlined, ArrowUpOutlined } from "@ant-design/icons";
import { api, errorText } from "../api";

const LIMIT = 20;

type GroupRow = {
  id: number;
  name: string;
  position: number;
  status: string;
  drama_count: number;
  limit: number;
};

type Member = {
  id: number;
  title: string;
  status: string;
  cover_url: string;
  position_in_group: number;
};

type DramaHit = {
  id: number;
  title: string;
  status: string;
};

const DRAMA_STATUS: Record<string, { text: string; color: string }> = {
  draft: { text: "草稿", color: "default" },
  in_review: { text: "审核中", color: "orange" },
  published: { text: "已上架", color: "green" },
  unpublished: { text: "已下架", color: "red" },
};

function statusTag(status: string) {
  const meta = DRAMA_STATUS[status] ?? { text: status, color: "default" };
  return <Tag color={meta.color}>{meta.text}</Tag>;
}

export default function GroupsPage() {
  const [rows, setRows] = useState<GroupRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<GroupRow | null>(null);
  const [form] = Form.useForm<{ name: string }>();
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api<{ items: GroupRow[] }>("/admin/v1/groups");
      setRows(res.items);
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const ordered = useMemo(() => [...rows].sort((a, b) => a.position - b.position || a.id - b.id), [rows]);

  const move = async (index: number, delta: number) => {
    const next = index + delta;
    if (next < 0 || next >= ordered.length) return;
    const current = ordered[index];
    const other = ordered[next];
    try {
      await api(`/admin/v1/groups/${current.id}`, { method: "PATCH", body: JSON.stringify({ position: other.position }) });
      await api(`/admin/v1/groups/${other.id}`, { method: "PATCH", body: JSON.stringify({ position: current.position }) });
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const saveName = async (values: { name: string }) => {
    const name = values.name.trim();
    if (!name) return;
    try {
      if (editing) {
        await api(`/admin/v1/groups/${editing.id}`, { method: "PATCH", body: JSON.stringify({ name }) });
      } else {
        await api("/admin/v1/groups", {
          method: "POST",
          body: JSON.stringify({ name, position: ordered.length ? ordered[ordered.length - 1].position + 1 : 0 }),
        });
      }
      message.success("已保存");
      setCreating(false);
      setEditing(null);
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const toggle = async (row: GroupRow) => {
    try {
      await api(`/admin/v1/groups/${row.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: row.status === "active" ? "inactive" : "active" }),
      });
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
        <Space direction="vertical" size={0}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            首页分组
          </Typography.Title>
          <Typography.Text type="secondary">顺序即客户端分组顺序，每组最多 {LIMIT} 部。停用后客户端不展示，组内剧保留。</Typography.Text>
        </Space>
        <Button
          type="primary"
          onClick={() => {
            setEditing(null);
            form.resetFields();
            setCreating(true);
          }}
        >
          新建分组
        </Button>
      </Space>
      <Table<GroupRow>
        rowKey="id"
        loading={loading}
        dataSource={ordered}
        pagination={false}
        scroll={{ x: 760 }}
        columns={[
          { title: "顺序", dataIndex: "position", width: 72 },
          { title: "名称", dataIndex: "name", render: (name: string) => <Typography.Text strong>{name}</Typography.Text> },
          {
            title: "剧数",
            width: 100,
            render: (_, row) => (
              <Typography.Text type={row.drama_count >= LIMIT ? "danger" : undefined}>
                {row.drama_count} / {LIMIT}
              </Typography.Text>
            ),
          },
          {
            title: "状态",
            dataIndex: "status",
            width: 90,
            render: (status: string) => (status === "active" ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
          },
          {
            title: "操作",
            width: 280,
            render: (_, row, index) => (
              <Space size={0} wrap>
                <Button type="link" size="small" icon={<ArrowUpOutlined />} disabled={index === 0} onClick={() => move(index, -1)}>
                  上移
                </Button>
                <Button type="link" size="small" icon={<ArrowDownOutlined />} disabled={index === ordered.length - 1} onClick={() => move(index, 1)}>
                  下移
                </Button>
                <Button type="link" size="small" onClick={() => navigate(`/groups/${row.id}`)}>
                  管理剧
                </Button>
                <Button
                  type="link"
                  size="small"
                  onClick={() => {
                    setEditing(row);
                    form.setFieldsValue({ name: row.name });
                    setCreating(true);
                  }}
                >
                  改名
                </Button>
                <Button type="link" size="small" onClick={() => toggle(row)}>
                  {row.status === "active" ? "停用" : "启用"}
                </Button>
              </Space>
            ),
          },
        ]}
      />
      <Modal
        title={editing ? `改名 · ${editing.name}` : "新建分组"}
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={() => form.submit()}
        okText="保存"
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={saveName} style={{ marginTop: 8 }}>
          <Form.Item name="name" label="分组名称" rules={[{ required: true, message: "请填写名称" }]}>
            <Input maxLength={64} placeholder="本周推荐" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

export function GroupDetailPage() {
  const { id } = useParams();
  const [group, setGroup] = useState<GroupRow | null>(null);
  const [items, setItems] = useState<Member[]>([]);
  const [loading, setLoading] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const res = await api<{ group: GroupRow; items: Member[] }>(`/admin/v1/groups/${id}/dramas`);
      setGroup(res.group);
      setItems(res.items);
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const move = async (index: number, delta: number) => {
    const next = index + delta;
    if (next < 0 || next >= items.length) return;
    const reordered = [...items];
    const [moved] = reordered.splice(index, 1);
    reordered.splice(next, 0, moved);
    try {
      await Promise.all(
        reordered.map((row, position) =>
          api(`/admin/v1/groups/${id}/dramas/${row.id}`, {
            method: "PATCH",
            body: JSON.stringify({ position }),
          }),
        ),
      );
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const remove = async (dramaId: number) => {
    try {
      await api(`/admin/v1/groups/${id}/dramas/${dramaId}`, { method: "DELETE" });
      message.success("已移出");
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const full = (group?.drama_count ?? 0) >= LIMIT;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <div>
        <Link to="/groups">首页分组</Link>
        <Typography.Text type="secondary"> / {group?.name ?? "…"}</Typography.Text>
      </div>
      <Space style={{ width: "100%", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          组内剧 · {group?.drama_count ?? 0} / {LIMIT}
        </Typography.Title>
        <Button type="primary" disabled={full} onClick={() => setPickerOpen(true)}>
          添加剧
        </Button>
      </Space>
      <Typography.Text type="secondary">草稿和下架可以留在组里，客户端只展示已上架。上下移动只改这一组内的顺序。</Typography.Text>
      <Table<Member>
        rowKey="id"
        loading={loading}
        dataSource={items}
        pagination={false}
        scroll={{ x: 640 }}
        columns={[
          {
            title: "序",
            width: 120,
            render: (_, row, index) => (
              <Space size={0}>
                <Typography.Text style={{ width: 24, display: "inline-block" }}>{index + 1}</Typography.Text>
                <Button type="text" size="small" icon={<ArrowUpOutlined />} disabled={index === 0} onClick={() => move(index, -1)} />
                <Button type="text" size="small" icon={<ArrowDownOutlined />} disabled={index === items.length - 1} onClick={() => move(index, 1)} />
              </Space>
            ),
          },
          {
            title: "剧",
            render: (_, row) => (
              <div>
                <Link to={`/dramas/${row.id}`}>{row.title}</Link>
                <div>
                  <Typography.Text type="secondary">#{row.id}</Typography.Text>
                </div>
              </div>
            ),
          },
          { title: "状态", dataIndex: "status", width: 100, render: statusTag },
          {
            title: "",
            width: 80,
            render: (_, row) => (
              <Popconfirm title="从本组移出这部剧？" onConfirm={() => remove(row.id)} okText="移出" cancelText="取消">
                <Button type="link" size="small" danger>
                  移出
                </Button>
              </Popconfirm>
            ),
          },
        ]}
      />
      {pickerOpen && group ? (
        <AddDramasModal
          groupId={group.id}
          taken={new Set(items.map((item) => item.id))}
          room={LIMIT - (group.drama_count ?? 0)}
          onClose={() => setPickerOpen(false)}
          onSaved={async () => {
            setPickerOpen(false);
            await load();
          }}
        />
      ) : null}
    </Space>
  );
}

function AddDramasModal({
  groupId,
  taken,
  room,
  onClose,
  onSaved,
}: {
  groupId: number;
  taken: Set<number>;
  room: number;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<DramaHit[]>([]);
  const [picked, setPicked] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);

  const search = async (value: string) => {
    setQ(value);
    const query = value.trim();
    if (!query) {
      setHits([]);
      return;
    }
    try {
      const res = await api<{ items: DramaHit[] }>(`/admin/v1/dramas?q=${encodeURIComponent(query)}&page_size=20`);
      setHits(res.items);
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const toggle = (dramaId: number) => {
    setPicked((current) => {
      if (current.includes(dramaId)) return current.filter((id) => id !== dramaId);
      if (current.length >= room) {
        message.warning(`这组还能再加 ${room} 部`);
        return current;
      }
      return [...current, dramaId];
    });
  };

  const confirm = async () => {
    setSaving(true);
    try {
      for (const dramaId of picked) {
        await api(`/admin/v1/groups/${groupId}/dramas`, {
          method: "POST",
          body: JSON.stringify({ drama_id: dramaId, confirm: true }),
        });
      }
      message.success(`已加入 ${picked.length} 部`);
      await onSaved();
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title="添加剧"
      open
      onCancel={onClose}
      onOk={confirm}
      okText="确认添加"
      cancelText="取消"
      okButtonProps={{ disabled: picked.length === 0 }}
      confirmLoading={saving}
      destroyOnClose
    >
      <Input.Search placeholder="搜索剧名或 ID" value={q} onChange={(e) => search(e.target.value)} onSearch={search} allowClear />
      <Typography.Paragraph type="secondary" style={{ margin: "8px 0" }}>
        已选 {picked.length}，确认后才写入。还能再加 {room} 部。
      </Typography.Paragraph>
      <div style={{ maxHeight: 320, overflow: "auto" }}>
        {hits.map((hit) => {
          const already = taken.has(hit.id);
          return (
            <div key={hit.id} style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "8px 0", borderTop: "1px solid rgba(0,0,0,0.06)" }}>
              <span>
                {hit.title} · #{hit.id} {statusTag(hit.status)}
              </span>
              {already ? (
                <Typography.Text type="secondary">已在组内</Typography.Text>
              ) : (
                <Button type="link" size="small" onClick={() => toggle(hit.id)}>
                  {picked.includes(hit.id) ? "取消" : "勾选"}
                </Button>
              )}
            </div>
          );
        })}
        {!hits.length ? <Typography.Text type="secondary">输入剧名后显示结果</Typography.Text> : null}
      </div>
    </Modal>
  );
}

export function DramaGroupsPanel({ dramaId }: { dramaId: string }) {
  const [rows, setRows] = useState<(GroupRow & { joined: boolean; position_in_group: number | null })[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api<{ items: (GroupRow & { joined: boolean; position_in_group: number | null })[] }>(
        `/admin/v1/dramas/${dramaId}/groups`,
      );
      setRows(res.items);
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [dramaId]);

  useEffect(() => {
    load();
  }, [load]);

  const joined = rows.filter((row) => row.joined);

  const join = async (groupId: number) => {
    try {
      await api(`/admin/v1/groups/${groupId}/dramas`, {
        method: "POST",
        body: JSON.stringify({ drama_id: Number(dramaId), confirm: true }),
      });
      message.success("已加入");
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const leave = async (groupId: number) => {
    try {
      await api(`/admin/v1/groups/${groupId}/dramas/${dramaId}`, { method: "DELETE" });
      message.success("已移出");
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const rank = (row: (typeof rows)[number]) => {
    if (row.position_in_group == null) return "-";
    return `组内第 ${row.position_in_group + 1} 位`;
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 280px", gap: 16 }}>
      <div>
        <Typography.Text strong>已加入 {joined.length} 个分组</Typography.Text>
        <Table
          style={{ marginTop: 8 }}
          rowKey="id"
          loading={loading}
          dataSource={joined}
          pagination={false}
          locale={{ emptyText: "还没有加入任何分组" }}
          columns={[
            {
              title: "分组",
              render: (_, row) => (
                <div>
                  <Link to={`/groups/${row.id}`}>{row.name}</Link>
                  <div>
                    <Typography.Text type="secondary">
                      {rank(row)} · {row.drama_count}/{LIMIT}
                      {row.status !== "active" ? " · 已停用" : ""}
                    </Typography.Text>
                  </div>
                </div>
              ),
            },
            {
              title: "",
              width: 80,
              render: (_, row) => (
                <Popconfirm title="从该分组移出？" onConfirm={() => leave(row.id)} okText="移出" cancelText="取消">
                  <Button type="link" size="small" danger>
                    移出
                  </Button>
                </Popconfirm>
              ),
            },
          ]}
        />
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
          组内顺序在
          <Link to="/groups"> 首页分组 </Link>
          里调整。这里只决定这部剧属于哪些组。
        </Typography.Paragraph>
      </div>
      <div style={{ border: "1px solid rgba(0,0,0,0.06)", borderRadius: 8, padding: 12 }}>
        <Typography.Text strong>加入分组</Typography.Text>
        <div style={{ marginTop: 8 }}>
          {rows.map((row) => {
            const full = row.drama_count >= LIMIT;
            return (
              <div key={row.id} style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "8px 0", borderTop: "1px solid rgba(0,0,0,0.06)" }}>
                <span>
                  {row.name}
                  {row.status !== "active" ? <Tag style={{ marginLeft: 6 }}>停用</Tag> : null}
                </span>
                {row.joined ? (
                  <Typography.Text type="secondary">已加入</Typography.Text>
                ) : full ? (
                  <Typography.Text type="danger">已满</Typography.Text>
                ) : (
                  <Button type="link" size="small" onClick={() => join(row.id)}>
                    加入
                  </Button>
                )}
              </div>
            );
          })}
          {!rows.length ? <Typography.Text type="secondary">还没有分组，先到首页分组新建。</Typography.Text> : null}
        </div>
      </div>
    </div>
  );
}
