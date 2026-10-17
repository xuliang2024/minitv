import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Breadcrumb,
  Button,
  Card,
  Empty,
  Form,
  Image,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  FolderOpenOutlined,
  PlusOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { api, errorText } from "../api";
import { DragRow, Handle, reorder, type DragVisual } from "./dragSort";

const LIMIT_FALLBACK = 100;

function groupLimit(row: { limit?: number } | null | undefined) {
  return row?.limit || LIMIT_FALLBACK;
}

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
  cover_url: string;
};

/** Cover thumbnail with a dark placeholder, matching the dramas list page. */
function CoverThumb({ url, width = 40, height = 54 }: { url?: string; width?: number; height?: number }) {
  if (url) {
    return (
      <Image
        src={url}
        width={width}
        height={height}
        style={{
          objectFit: "cover",
          borderRadius: 6,
          flexShrink: 0,
          border: "1px solid rgba(0,0,0,0.08)",
          boxShadow: "0 1px 2px rgba(0,0,0,0.06)",
        }}
        preview={false}
      />
    );
  }
  return (
    <div
      style={{
        width,
        height,
        borderRadius: 6,
        background: "#f2f4f7",
        border: "1px dashed #d0d5dd",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "#98a2b3",
        fontSize: 10,
        flexShrink: 0,
      }}
    >
      无封面
    </div>
  );
}

const DRAMA_STATUS: Record<string, { text: string; color: string }> = {
  draft: { text: "草稿", color: "default" },
  in_review: { text: "审核中", color: "orange" },
  published: { text: "已上架", color: "green" },
  unpublished: { text: "已下架", color: "red" },
};

function statusTag(status: string, style?: React.CSSProperties) {
  const meta = DRAMA_STATUS[status] ?? { text: status, color: "default" };
  return (
    <Tag color={meta.color} style={{ margin: 0, borderRadius: 4, fontWeight: 500, ...style }}>
      {meta.text}
    </Tag>
  );
}

const GROUP_COLS = "64px minmax(200px, 1fr) 180px 110px 190px";
const DETAIL_COLS = "64px minmax(220px, 1fr) 110px 80px";

function ListHead({ columns, labels }: { columns: string; labels: React.ReactNode[] }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: columns,
        alignItems: "center",
        gap: 12,
        padding: "12px 16px",
        fontSize: 13,
        fontWeight: 600,
        color: "#475467",
        background: "#f8f9fa",
        borderBottom: "1px solid #eaecf0",
      }}
    >
      {labels.map((label, index) => (
        <span key={index}>{label}</span>
      ))}
    </div>
  );
}

export default function GroupsPage() {
  const [rows, setRows] = useState<GroupRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<GroupRow | null>(null);
  const [form] = Form.useForm<{ name: string }>();
  const [dragId, setDragId] = useState<number | null>(null);
  const [overId, setOverId] = useState<number | null>(null);
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
  const totalDramas = useMemo(() => rows.reduce((acc, r) => acc + (r.drama_count || 0), 0), [rows]);
  const activeCount = useMemo(() => rows.filter((r) => r.status === "active").length, [rows]);

  const endDrag = () => {
    setDragId(null);
    setOverId(null);
  };

  const moveRow = async (fromId: number, toId: number) => {
    const from = ordered.findIndex((row) => row.id === fromId);
    const to = ordered.findIndex((row) => row.id === toId);
    if (from < 0 || to < 0 || from === to) return;
    const positioned = reorder(ordered, from, to).map((row, position) => ({ ...row, position }));
    setRows(positioned);
    try {
      await Promise.all(
        positioned.map((row) =>
          api(`/admin/v1/groups/${row.id}`, { method: "PATCH", body: JSON.stringify({ position: row.position }) }),
        ),
      );
    } catch (e) {
      message.error(errorText(e));
      await load();
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
      message.success(row.status === "active" ? "已停用分组" : "已启用分组");
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {/* 头部标题与快捷统计 */}
      <Space style={{ width: "100%", justifyContent: "space-between" }} align="center" wrap>
        <Space align="center" size={12} wrap>
          <Typography.Title level={4} style={{ margin: 0 }}>
            首页分组
          </Typography.Title>
          <Tag bordered={false} style={{ background: "#f2f4f7", color: "#475467", borderRadius: 12, padding: "2px 10px", margin: 0 }}>
            共 {ordered.length} 个分组
          </Tag>
          <Tag bordered={false} style={{ background: "#ecfdf3", color: "#027a48", borderRadius: 12, padding: "2px 10px", margin: 0 }}>
            {activeCount} 个启用
          </Tag>
          <Tag bordered={false} style={{ background: "#eff8ff", color: "#175cd3", borderRadius: 12, padding: "2px 10px", margin: 0 }}>
            收纳 {totalDramas} 部剧
          </Tag>
        </Space>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          style={{ fontWeight: 500 }}
          onClick={() => {
            setEditing(null);
            form.resetFields();
            setCreating(true);
          }}
        >
          新建分组
        </Button>
      </Space>

      {/* 业务规则与交互轻提示 */}
      <Alert
        type="info"
        showIcon
        message={
          <span style={{ fontSize: 13, color: "#475467" }}>
            拖动左侧手柄调整顺序，即客户端首页展示顺序。每组最多容纳 <strong>{LIMIT_FALLBACK}</strong> 部剧。停用后客户端不展示该分组，组内剧集数据仍保留。
          </span>
        }
        style={{
          borderRadius: 8,
          border: "1px solid #d0d5dd",
          background: "#fafbfd",
        }}
      />

      {/* 主卡片容器 */}
      <Spin spinning={loading}>
        <Card
          bordered
          styles={{ body: { padding: 0 } }}
          style={{
            borderRadius: 12,
            borderColor: "#eaecf0",
            boxShadow: "0 1px 3px rgba(16, 24, 40, 0.05)",
            overflow: "hidden",
            background: "#fff",
          }}
        >
          <ListHead columns={GROUP_COLS} labels={["排序", "分组名称", "剧集容量", "状态", "操作"]} />
          <div style={{ padding: "10px 12px", display: "grid", gap: 6, minHeight: 60 }}>
            {ordered.map((row, index) => {
              const visual: DragVisual = {
                active: false,
                dragging: dragId === row.id,
                dropping: overId === row.id,
              };
              const limit = groupLimit(row);
              const isFull = row.drama_count >= limit;
              const fillRate = limit > 0 ? row.drama_count / limit : 0;
              const strokeColor = isFull ? "#d92d20" : fillRate >= 0.8 ? "#f79009" : "#12b76a";

              return (
                <DragRow
                  key={row.id}
                  columns={GROUP_COLS}
                  draggable
                  visual={visual}
                  style={{
                    padding: "10px 16px",
                    gap: 12,
                    borderRadius: 8,
                  }}
                  onDragStart={() => setDragId(row.id)}
                  onDragEnd={endDrag}
                  onDragOver={() => {
                    if (dragId == null) return;
                    setOverId(row.id);
                  }}
                  onDrop={() => {
                    if (dragId != null) void moveRow(dragId, row.id);
                    endDrag();
                  }}
                >
                  {(onArm) => (
                    <>
                      {/* 排序手柄与圆角序号 */}
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <Handle onArm={onArm} />
                        <span
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            justifyContent: "center",
                            width: 22,
                            height: 22,
                            borderRadius: 6,
                            background: index < 3 ? "#fff1f0" : "#f2f4f7",
                            color: index < 3 ? "#e53b3e" : "#475467",
                            fontSize: 12,
                            fontWeight: 600,
                          }}
                        >
                          {index + 1}
                        </span>
                      </div>

                      {/* 分组名称 */}
                      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
                        <Link
                          to={`/groups/${row.id}`}
                          style={{
                            fontWeight: 600,
                            fontSize: 14,
                            color: row.status === "active" ? "#101828" : "#98a2b3",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                          title="点击进入组内剧管理"
                        >
                          {row.name}
                        </Link>
                        {row.status !== "active" && (
                          <Tag bordered={false} style={{ fontSize: 11, background: "#f2f4f7", color: "#667085", margin: 0 }}>
                            已停用
                          </Tag>
                        )}
                      </div>

                      {/* 剧集容量与进度条 */}
                      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 12 }}>
                          <span style={{ fontWeight: 600, color: isFull ? "#d92d20" : "#344054" }}>
                            {row.drama_count} <span style={{ color: "#98a2b3", fontWeight: 400 }}>/ {limit} 部</span>
                          </span>
                          <span style={{ fontSize: 11, color: isFull ? "#d92d20" : fillRate >= 0.8 ? "#b54708" : "#98a2b3" }}>
                            {isFull ? "已满额" : `余 ${limit - row.drama_count}`}
                          </span>
                        </div>
                        <Progress
                          percent={Math.min(100, Math.round(fillRate * 100))}
                          size={["100%", 5]}
                          showInfo={false}
                          strokeColor={strokeColor}
                          trailColor="#eaecf0"
                        />
                      </div>

                      {/* 状态展示 */}
                      <div>
                        <Badge
                          status={row.status === "active" ? "success" : "default"}
                          text={
                            <span
                              style={{
                                fontSize: 13,
                                fontWeight: 500,
                                color: row.status === "active" ? "#027a48" : "#667085",
                              }}
                            >
                              {row.status === "active" ? "启用中" : "已停用"}
                            </span>
                          }
                        />
                      </div>

                      {/* 操作按钮组 */}
                      <Space size={4}>
                        <Button
                          type="link"
                          size="small"
                          icon={<FolderOpenOutlined />}
                          style={{ fontWeight: 600, paddingInline: 4 }}
                          onClick={() => navigate(`/groups/${row.id}`)}
                        >
                          管理剧
                        </Button>
                        <Button
                          type="text"
                          size="small"
                          icon={<EditOutlined style={{ fontSize: 13, color: "#667085" }} />}
                          style={{ color: "#475467", paddingInline: 6 }}
                          onClick={() => {
                            setEditing(row);
                            form.setFieldsValue({ name: row.name });
                            setCreating(true);
                          }}
                        >
                          改名
                        </Button>
                        {row.status === "active" ? (
                          <Popconfirm
                            title="确认停用该分组？"
                            description="停用后客户端首页将隐藏此分组，组内剧集仍保留。"
                            onConfirm={() => toggle(row)}
                            okText="停用"
                            cancelText="取消"
                            okButtonProps={{ danger: true }}
                          >
                            <Button type="text" size="small" danger style={{ paddingInline: 6 }}>
                              停用
                            </Button>
                          </Popconfirm>
                        ) : (
                          <Button
                            type="text"
                            size="small"
                            style={{ color: "#027a48", paddingInline: 6, fontWeight: 500 }}
                            onClick={() => toggle(row)}
                          >
                            启用
                          </Button>
                        )}
                      </Space>
                    </>
                  )}
                </DragRow>
              );
            })}
            {!loading && !ordered.length ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="还没有分组，点右上角「新建分组」开始创建"
                style={{ margin: "24px 0" }}
              />
            ) : null}
          </div>
        </Card>
      </Spin>

      <Modal
        title={editing ? `改名 · ${editing.name}` : "新建分组"}
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={() => form.submit()}
        okText="保存"
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={saveName} style={{ marginTop: 12 }}>
          <Form.Item name="name" label="分组名称" rules={[{ required: true, message: "请填写分组名称" }]}>
            <Input maxLength={64} placeholder="例如：本周推荐、逆袭霸总" autoFocus />
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
  const [dragId, setDragId] = useState<number | null>(null);
  const [overId, setOverId] = useState<number | null>(null);

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

  const endDrag = () => {
    setDragId(null);
    setOverId(null);
  };

  const moveRow = async (fromId: number, toId: number) => {
    const from = items.findIndex((row) => row.id === fromId);
    const to = items.findIndex((row) => row.id === toId);
    if (from < 0 || to < 0 || from === to) return;
    const positioned = reorder(items, from, to).map((row, position) => ({ ...row, position_in_group: position }));
    setItems(positioned);
    try {
      await Promise.all(
        positioned.map((row) =>
          api(`/admin/v1/groups/${id}/dramas/${row.id}`, {
            method: "PATCH",
            body: JSON.stringify({ position: row.position_in_group }),
          }),
        ),
      );
    } catch (e) {
      message.error(errorText(e));
      await load();
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

  const full = (group?.drama_count ?? 0) >= groupLimit(group);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {/* 规范面包屑导航 */}
      <Breadcrumb
        items={[
          { title: <Link to="/groups">首页分组</Link> },
          { title: group?.name ?? "组内剧管理" },
        ]}
      />

      {/* 头部标题与操作 */}
      <Space style={{ width: "100%", justifyContent: "space-between" }} align="center" wrap>
        <Space align="center" size={12} wrap>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {group?.name ?? "组内剧"}
          </Typography.Title>
          <Tag
            bordered={false}
            style={{
              background: full ? "#fef3f2" : "#eff8ff",
              color: full ? "#d92d20" : "#175cd3",
              borderRadius: 12,
              padding: "2px 10px",
              fontWeight: 500,
              margin: 0,
            }}
          >
            {group?.drama_count ?? 0} / {groupLimit(group)} 部剧
          </Tag>
          {group?.status === "active" ? (
            <Tag bordered={false} style={{ background: "#ecfdf3", color: "#027a48", borderRadius: 12, margin: 0 }}>
              启用中
            </Tag>
          ) : (
            <Tag bordered={false} style={{ background: "#f2f4f7", color: "#667085", borderRadius: 12, margin: 0 }}>
              已停用
            </Tag>
          )}
        </Space>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          disabled={full}
          onClick={() => setPickerOpen(true)}
          style={{ fontWeight: 500 }}
        >
          添加剧
        </Button>
      </Space>

      {/* 说明提示条 */}
      <Alert
        type="info"
        showIcon
        message={
          <span style={{ fontSize: 13, color: "#475467" }}>
            草稿与已下架剧集也可以保留在组内；客户端仅向用户展示<strong>已上架</strong>的剧集。拖动左侧手柄仅调整该组内的剧集展示次序。
          </span>
        }
        style={{
          borderRadius: 8,
          border: "1px solid #d0d5dd",
          background: "#fafbfd",
        }}
      />

      {/* 列表主卡片 */}
      <Spin spinning={loading}>
        <Card
          bordered
          styles={{ body: { padding: 0 } }}
          style={{
            borderRadius: 12,
            borderColor: "#eaecf0",
            boxShadow: "0 1px 3px rgba(16, 24, 40, 0.05)",
            overflow: "hidden",
            background: "#fff",
          }}
        >
          <ListHead columns={DETAIL_COLS} labels={["排序", "剧目信息", "上架状态", "操作"]} />
          <div style={{ padding: "10px 12px", display: "grid", gap: 6, minHeight: 60 }}>
            {items.map((row, index) => {
              const visual: DragVisual = {
                active: false,
                dragging: dragId === row.id,
                dropping: overId === row.id,
              };
              return (
                <DragRow
                  key={row.id}
                  columns={DETAIL_COLS}
                  draggable
                  visual={visual}
                  style={{
                    padding: "8px 16px",
                    gap: 12,
                    borderRadius: 8,
                  }}
                  onDragStart={() => setDragId(row.id)}
                  onDragEnd={endDrag}
                  onDragOver={() => {
                    if (dragId == null) return;
                    setOverId(row.id);
                  }}
                  onDrop={() => {
                    if (dragId != null) void moveRow(dragId, row.id);
                    endDrag();
                  }}
                >
                  {(onArm) => (
                    <>
                      {/* 排序手柄与圆角序号 */}
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <Handle onArm={onArm} />
                        <span
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            justifyContent: "center",
                            width: 22,
                            height: 22,
                            borderRadius: 6,
                            background: index < 3 ? "#fff1f0" : "#f2f4f7",
                            color: index < 3 ? "#e53b3e" : "#475467",
                            fontSize: 12,
                            fontWeight: 600,
                          }}
                        >
                          {index + 1}
                        </span>
                      </div>

                      {/* 封面与标题信息 */}
                      <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
                        <CoverThumb url={row.cover_url} width={38} height={50} />
                        <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
                          <Link
                            to={`/dramas/${row.id}`}
                            style={{
                              fontWeight: 600,
                              fontSize: 14,
                              color: "#101828",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {row.title}
                          </Link>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            #{row.id}
                          </Typography.Text>
                        </div>
                      </div>

                      {/* 状态 */}
                      <div>{statusTag(row.status)}</div>

                      {/* 操作 */}
                      <div>
                        <Popconfirm
                          title="移出剧集"
                          description="确定从本分组移出这部剧？（剧集本身不受影响）"
                          onConfirm={() => remove(row.id)}
                          okText="移出"
                          cancelText="取消"
                          okButtonProps={{ danger: true }}
                        >
                          <Button type="text" size="small" danger icon={<DeleteOutlined />} style={{ paddingInline: 6 }}>
                            移出
                          </Button>
                        </Popconfirm>
                      </div>
                    </>
                  )}
                </DragRow>
              );
            })}
            {!loading && !items.length ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="组里还没有剧，点右上角「添加剧」"
                style={{ margin: "24px 0" }}
              />
            ) : null}
          </div>
        </Card>
      </Spin>

      {pickerOpen && group ? (
        <AddDramasModal
          groupId={group.id}
          taken={new Set(items.map((item) => item.id))}
          room={groupLimit(group) - (group.drama_count ?? 0)}
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
  const [catalog, setCatalog] = useState<DramaHit[]>([]);
  const [picked, setPicked] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadAll = async () => {
      const first = await api<{ items: DramaHit[]; total: number }>("/admin/v1/dramas?page=1&page_size=100");
      const pages = Math.ceil((first.total ?? first.items.length) / 100);
      const rest = await Promise.all(
        Array.from({ length: Math.max(0, pages - 1) }, (_, index) =>
          api<{ items: DramaHit[] }>(`/admin/v1/dramas?page=${index + 2}&page_size=100`),
        ),
      );
      setCatalog([...first.items, ...rest.flatMap((page) => page.items)]);
    };
    loadAll()
      .catch((e) => message.error(errorText(e)))
      .finally(() => setLoading(false));
  }, []);

  const needle = q.trim().toLowerCase().replace(/^#+/, "");
  const hits = needle
    ? catalog.filter((hit) => hit.title.toLowerCase().includes(needle) || String(hit.id).includes(needle))
    : catalog;

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
      title="添加剧入组"
      open
      onCancel={onClose}
      onOk={confirm}
      okText={picked.length ? `确认添加 (${picked.length})` : "确认添加"}
      cancelText="取消"
      okButtonProps={{ disabled: picked.length === 0 }}
      confirmLoading={saving}
      destroyOnClose
      width={560}
    >
      <Input.Search
        autoFocus
        placeholder="搜索剧名或 ID"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        allowClear
        style={{ marginTop: 8 }}
      />
      <div
        style={{
          margin: "12px 0 8px",
          padding: "8px 12px",
          background: "#f8f9fa",
          borderRadius: 6,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontSize: 13,
        }}
      >
        <span style={{ color: "#475467" }}>
          已选择 <strong style={{ color: "#1677ff" }}>{picked.length}</strong> 部剧
        </span>
        <span style={{ color: "#667085" }}>
          本组还可容纳 <strong>{room}</strong> 部
        </span>
      </div>
      <div
        style={{
          maxHeight: 340,
          overflowY: "auto",
          paddingRight: 4,
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}
      >
        {hits.map((hit) => {
          const already = taken.has(hit.id);
          const isSelected = picked.includes(hit.id);
          return (
            <div
              key={hit.id}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
                padding: "8px 10px",
                borderRadius: 8,
                background: isSelected ? "#eff8ff" : "transparent",
                border: isSelected ? "1px solid #b2ddff" : "1px solid #f2f4f7",
                transition: "all 0.15s ease",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10, flex: 1, minWidth: 0 }}>
                <CoverThumb url={hit.cover_url} width={32} height={42} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 500, fontSize: 13, color: "#101828" }}>{hit.title}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      #{hit.id}
                    </Typography.Text>
                    {statusTag(hit.status, { fontSize: 10, lineHeight: "16px", padding: "0 4px" })}
                  </div>
                </div>
              </div>
              {already ? (
                <Tag bordered={false} style={{ background: "#f2f4f7", color: "#98a2b3", margin: 0 }}>
                  已在组内
                </Tag>
              ) : (
                <Button
                  type={isSelected ? "primary" : "default"}
                  size="small"
                  onClick={() => toggle(hit.id)}
                  style={{ borderRadius: 4 }}
                >
                  {isSelected ? "已选" : "选择"}
                </Button>
              )}
            </div>
          );
        })}
        {!hits.length ? (
          <div style={{ padding: "24px 0", textAlign: "center" }}>
            <Typography.Text type="secondary">{loading ? "正在加载剧库..." : "没有匹配的剧集"}</Typography.Text>
          </div>
        ) : null}
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
  const available = rows.filter((row) => !row.joined);

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
    if (row.position_in_group == null) return "组内位置待定";
    return `组内第 ${row.position_in_group + 1} 位`;
  };

  const groupCard = (row: (typeof rows)[number], joinedCard: boolean) => {
    const cap = groupLimit(row);
    const full = row.drama_count >= cap;
    const inactive = row.status !== "active";
    const room = Math.max(cap - row.drama_count, 0);
    const fill = Math.min(row.drama_count / cap, 1);
    const barColor = full ? "#cf1322" : fill >= 0.8 ? "#d48806" : "#1677ff";
    return (
      <div
        key={row.id}
        style={{
          width: 280,
          border: "1px solid rgba(0,0,0,0.08)",
          borderRadius: 10,
          padding: "14px 14px 12px",
          background: "#fff",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <Link to={`/groups/${row.id}`} style={{ fontWeight: 600, fontSize: 15 }}>
            {row.name}
          </Link>
          {inactive ? <Tag>停用</Tag> : joinedCard ? <Tag color="green">已加入</Tag> : <Tag color="blue">启用</Tag>}
        </div>
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "rgba(0,0,0,0.45)", marginBottom: 6 }}>
            <span>{row.drama_count}/{cap} 部</span>
            <span>{full ? "已满" : `还可加 ${room} 部`}</span>
          </div>
          <div style={{ height: 4, borderRadius: 2, background: "rgba(0,0,0,0.06)", overflow: "hidden" }}>
            <div style={{ width: `${fill * 100}%`, height: "100%", background: barColor }} />
          </div>
          {joinedCard ? (
            <div style={{ marginTop: 6, fontSize: 12, color: "rgba(0,0,0,0.45)" }}>{rank(row)}</div>
          ) : null}
        </div>
        {joinedCard ? (
          <Popconfirm title="从该分组移出？" onConfirm={() => leave(row.id)} okText="移出" cancelText="取消">
            <Button size="small" danger block>
              移出
            </Button>
          </Popconfirm>
        ) : full || inactive ? (
          <Button size="small" block disabled>
            {full ? "已满" : "已停用"}
          </Button>
        ) : (
          <Button size="small" type="primary" ghost block onClick={() => join(row.id)}>
            加入
          </Button>
        )}
      </div>
    );
  };

  const cardGrid = (items: typeof rows, joinedCard: boolean) => (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 10 }}>
      {items.map((row) => groupCard(row, joinedCard))}
    </div>
  );

  return (
    <div>
      {joined.length ? (
        <div>
          <Typography.Text strong>已加入 {joined.length} 个分组</Typography.Text>
          {cardGrid(joined, true)}
        </div>
      ) : (
        <div
          style={{
            border: "1px dashed rgba(0,0,0,0.15)",
            borderRadius: 10,
            padding: "14px 16px",
            background: "rgba(0,0,0,0.015)",
            maxWidth: 572,
          }}
        >
          <Typography.Text strong>还没加入任何分组</Typography.Text>
          <div style={{ marginTop: 4, color: "rgba(0,0,0,0.45)", fontSize: 13 }}>
            下面选一个分组加入。加入后会出现在 App 首页对应分组里，组内顺序到首页分组页调整。
          </div>
        </div>
      )}

      <div style={{ marginTop: 18 }}>
        <Typography.Text strong>{joined.length ? "还可加入" : "选择分组"}</Typography.Text>
        {!rows.length && !loading ? (
          <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
            还没有分组，先到
            <Link to="/groups"> 首页分组 </Link>
            新建。
          </Typography.Paragraph>
        ) : null}
        {available.length ? cardGrid(available, false) : null}
        {joined.length && !available.length && !loading ? (
          <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
            没有还能加入的分组。
          </Typography.Paragraph>
        ) : null}
      </div>

      <Typography.Paragraph type="secondary" style={{ marginTop: 14, marginBottom: 0 }}>
        组内顺序在
        <Link to="/groups"> 首页分组 </Link>
        里调整。这里只决定这部剧属于哪些组。
      </Typography.Paragraph>
    </div>
  );
}
