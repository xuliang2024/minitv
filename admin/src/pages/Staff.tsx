import { useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { api, errorText } from "../api";
import { useConfirm } from "../confirm";
import { formatTime, useLoad } from "../hooks";
import { ROLE_LABELS, ROLE_OPTIONS, useIsAdmin, useMe } from "../me";

type StaffRow = {
  id: number;
  username: string;
  display_name: string;
  role: string;
  status: string;
  disable_reason: string | null;
  last_login_at: string | null;
  created_at: string;
  updated_at: string;
};

const PASSWORD_ALPHABET =
  "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*";

/** 16 random characters, no look-alike 0/O/1/l — typed by hand often enough. */
function randomPassword(length = 16): string {
  const bytes = new Uint32Array(length);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (n) => PASSWORD_ALPHABET[n % PASSWORD_ALPHABET.length]).join("");
}

function roleTag(role: string) {
  const color = role === "admin" ? "red" : role === "ops" ? "blue" : "default";
  return <Tag color={color}>{ROLE_LABELS[role] ?? role}</Tag>;
}

export default function Staff() {
  const me = useMe();
  const isAdmin = useIsAdmin();
  const { confirm, confirmModal } = useConfirm();

  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");

  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (roleFilter) params.set("role", roleFilter);
  if (statusFilter) params.set("status", statusFilter);
  const path = `/admin/v1/staff${params.toString() ? `?${params.toString()}` : ""}`;
  const { data, reload, loading } = useLoad<{ items: StaffRow[] }>(path, { items: [] }, {
    // A non-admin would only collect a 403 toast here; the page renders the
    // "admins only" notice instead.
    enabled: isAdmin,
  });
  const rows = data.items;

  // ---- create ----
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();

  const openCreate = () => {
    createForm.resetFields();
    createForm.setFieldsValue({ role: "ops", password: randomPassword() });
    setCreateOpen(true);
  };

  const onCreate = async (values: any) => {
    try {
      await api("/admin/v1/staff", { method: "POST", body: JSON.stringify(values) });
      message.success(`已创建账号 ${values.username}`);
      setCreateOpen(false);
      await reload();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  // ---- edit ----
  const [editing, setEditing] = useState<StaffRow | null>(null);
  const [editForm] = Form.useForm();

  const openEdit = (row: StaffRow) => {
    setEditing(row);
    editForm.setFieldsValue({ display_name: row.display_name, role: row.role });
  };

  const onEdit = async (values: any) => {
    if (!editing) return;
    try {
      await api(`/admin/v1/staff/${editing.id}`, {
        method: "PATCH",
        body: JSON.stringify(values),
      });
      message.success("已保存");
      setEditing(null);
      await reload();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  // ---- reset password ----
  const [resetTarget, setResetTarget] = useState<StaffRow | null>(null);
  const [generated, setGenerated] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [resetting, setResetting] = useState(false);

  const openReset = (row: StaffRow) => {
    setResetTarget(row);
    setGenerated(randomPassword());
    setAcknowledged(false);
  };

  const copyPassword = async () => {
    try {
      await navigator.clipboard.writeText(generated);
      message.success("已复制");
    } catch {
      message.error("复制失败，请手动选中复制");
    }
  };

  const onReset = async () => {
    if (!resetTarget) return;
    setResetting(true);
    try {
      await api(`/admin/v1/staff/${resetTarget.id}/password`, {
        method: "POST",
        body: JSON.stringify({ new_password: generated }),
      });
      message.success(`${resetTarget.username} 的密码已重置，对方需重新登录`);
      setResetTarget(null);
      await reload();
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setResetting(false);
    }
  };

  // ---- disable / enable ----
  const [disableTarget, setDisableTarget] = useState<StaffRow | null>(null);
  const [reason, setReason] = useState("");
  const [disabling, setDisabling] = useState(false);

  const openDisable = (row: StaffRow) => {
    setDisableTarget(row);
    setReason("");
  };

  const onDisable = async () => {
    if (!disableTarget) return;
    setDisabling(true);
    try {
      await api(`/admin/v1/staff/${disableTarget.id}/status`, {
        method: "POST",
        body: JSON.stringify({ status: "disabled", reason: reason.trim() || null }),
      });
      message.success(`${disableTarget.username} 已禁用`);
      setDisableTarget(null);
      await reload();
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setDisabling(false);
    }
  };

  const onEnable = (row: StaffRow) => {
    confirm({
      title: `启用账号 ${row.username}`,
      content: "启用后该账号可以立即用原密码登录（若密码已被重置，请使用新密码）。",
      okText: "启用",
      onOk: async () => {
        try {
          await api(`/admin/v1/staff/${row.id}/status`, {
            method: "POST",
            body: JSON.stringify({ status: "active" }),
          });
          message.success("已启用");
          await reload();
        } catch (e) {
          message.error(errorText(e));
          throw e;
        }
      },
    });
  };

  if (!isAdmin) {
    return (
      <Alert
        type="warning"
        showIcon
        message="仅管理员可访问账号管理"
        description="当前账号没有管理员工账号的权限。如需开通或停用账号，请联系管理员。"
      />
    );
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space wrap>
        <Button type="primary" onClick={openCreate}>
          + 新增账号
        </Button>
        <Input.Search
          allowClear
          placeholder="搜索账号 / 显示名"
          style={{ width: 220 }}
          onSearch={(value) => setQuery(value.trim())}
        />
        <Select
          value={roleFilter}
          style={{ width: 130 }}
          onChange={setRoleFilter}
          options={[{ value: "", label: "全部角色" }, ...ROLE_OPTIONS]}
        />
        <Select
          value={statusFilter}
          style={{ width: 130 }}
          onChange={setStatusFilter}
          options={[
            { value: "", label: "全部状态" },
            { value: "active", label: "启用" },
            { value: "disabled", label: "已禁用" },
          ]}
        />
        <Typography.Text type="secondary">
          共 {rows.length} 个账号 · 账号不可删除，只能禁用
        </Typography.Text>
      </Space>

      <Table<StaffRow>
        rowKey="id"
        loading={loading}
        dataSource={rows}
        scroll={{ x: 1040 }}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        columns={[
          {
            title: "账号",
            dataIndex: "username",
            width: 190,
            render: (value: string, row) => (
              <div>
                <Typography.Text code>{value}</Typography.Text>
                {row.display_name && (
                  <div style={{ fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
                    {row.display_name}
                  </div>
                )}
              </div>
            ),
          },
          {
            title: "角色",
            dataIndex: "role",
            width: 90,
            render: (value: string) => roleTag(value),
          },
          {
            title: "状态",
            dataIndex: "status",
            width: 100,
            render: (value: string) => (
              <Tag color={value === "active" ? "green" : "default"}>
                {value === "active" ? "启用" : "已禁用"}
              </Tag>
            ),
          },
          {
            title: "禁用原因",
            dataIndex: "disable_reason",
            width: 170,
            render: (value: string | null) =>
              value ? (
                <Tooltip title={value}>
                  <Typography.Text ellipsis style={{ maxWidth: 150 }}>
                    {value}
                  </Typography.Text>
                </Tooltip>
              ) : (
                <Typography.Text type="secondary">-</Typography.Text>
              ),
          },
          {
            title: "最近登录",
            dataIndex: "last_login_at",
            width: 150,
            render: (value: string | null) => formatTime(value),
          },
          {
            title: "创建时间",
            dataIndex: "created_at",
            width: 150,
            render: (value: string) => formatTime(value),
          },
          {
            title: "操作",
            key: "actions",
            width: 200,
            render: (_, row) => {
              const self = row.id === me?.id;
              return (
                <Space size={4}>
                  <Button type="link" size="small" onClick={() => openEdit(row)}>
                    编辑
                  </Button>
                  <Button type="link" size="small" onClick={() => openReset(row)}>
                    重置密码
                  </Button>
                  {row.status === "active" ? (
                    <Tooltip title={self ? "不能禁用自己" : ""}>
                      <Button
                        type="link"
                        size="small"
                        danger
                        disabled={self}
                        onClick={() => openDisable(row)}
                      >
                        禁用
                      </Button>
                    </Tooltip>
                  ) : (
                    <Button type="link" size="small" onClick={() => onEnable(row)}>
                      启用
                    </Button>
                  )}
                </Space>
              );
            },
          },
        ]}
      />

      <Modal
        title="新增账号"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        okText="创建"
        forceRender
      >
        <Form form={createForm} layout="vertical" onFinish={onCreate} preserve={false}>
          <Form.Item
            label="账号"
            name="username"
            extra="3–32 位小写字母、数字或 . _ -；创建后不可修改"
            rules={[
              { required: true, message: "请输入账号" },
              {
                pattern: /^[a-z0-9][a-z0-9._-]{2,31}$/,
                message: "3–32 位小写字母、数字或 . _ -",
              },
            ]}
          >
            <Input placeholder="如 ops-lily" autoComplete="off" />
          </Form.Item>
          <Form.Item label="显示名" name="display_name">
            <Input maxLength={64} placeholder="如 李莉" />
          </Form.Item>
          <Form.Item label="角色" name="role" rules={[{ required: true }]}>
            <Select options={ROLE_OPTIONS} />
          </Form.Item>
          <Form.Item
            label="初始密码"
            name="password"
            extra={
              <span>
                建议使用随机口令，并通过安全渠道告知本人 ·{" "}
                <Typography.Link
                  onClick={() => createForm.setFieldValue("password", randomPassword())}
                >
                  生成随机口令
                </Typography.Link>
              </span>
            }
            rules={[
              { required: true, message: "请输入初始密码" },
              { min: 8, message: "至少 8 位" },
            ]}
          >
            <Input autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={editing ? `编辑账号：${editing.username}` : "编辑账号"}
        open={editing !== null}
        onCancel={() => setEditing(null)}
        onOk={() => editForm.submit()}
        okText="保存"
        forceRender
      >
        <Form form={editForm} layout="vertical" onFinish={onEdit} preserve={false}>
          <Form.Item label="显示名" name="display_name">
            <Input maxLength={64} />
          </Form.Item>
          <Form.Item
            label="角色"
            name="role"
            extra={editing?.id === me?.id ? "不能修改自己的角色" : undefined}
            rules={[{ required: true }]}
          >
            <Select options={ROLE_OPTIONS} disabled={editing?.id === me?.id} />
          </Form.Item>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            账号（用户名）创建后不可修改；改角色只影响写权限，不影响已登录会话。
          </Typography.Text>
        </Form>
      </Modal>

      <Modal
        title={resetTarget ? `重置密码：${resetTarget.display_name || resetTarget.username}` : "重置密码"}
        open={resetTarget !== null}
        onCancel={() => setResetTarget(null)}
        okText="确认重置"
        okButtonProps={{ danger: true, disabled: !acknowledged, loading: resetting }}
        onOk={onReset}
        destroyOnHidden
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Alert
            type="warning"
            showIcon
            message="重置后该账号所有已登录会话立即失效"
            description="对方需要用下面的新口令重新登录，原密码同时作废。"
          />
          <Space.Compact style={{ width: "100%" }}>
            <Input
              readOnly
              value={generated}
              style={{ fontFamily: "monospace" }}
              onFocus={(e) => e.target.select()}
            />
            <Button onClick={copyPassword}>复制</Button>
            <Button onClick={() => setGenerated(randomPassword())}>重新生成</Button>
          </Space.Compact>
          <Checkbox
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
          >
            我已通过安全渠道把新口令告知本人
          </Checkbox>
        </Space>
      </Modal>

      <Modal
        title={disableTarget ? `禁用账号：${disableTarget.username}` : "禁用账号"}
        open={disableTarget !== null}
        onCancel={() => setDisableTarget(null)}
        okText="确认禁用"
        okButtonProps={{ danger: true, loading: disabling }}
        onOk={onDisable}
        destroyOnHidden
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Alert
            type="warning"
            showIcon
            message="该账号已登录的会话会立即失效"
            description="历史操作记录保留，账号不会被删除，随时可以重新启用。"
          />
          <div style={{ marginBottom: 12 }}>
            <div style={{ marginBottom: 6, fontSize: 13 }}>禁用原因（可选）</div>
            <Input.TextArea
              rows={3}
              maxLength={200}
              showCount
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="如：离职 2026-10-21；或临时停用待核查"
            />
          </div>
        </Space>
      </Modal>

      {confirmModal}
    </Space>
  );
}
