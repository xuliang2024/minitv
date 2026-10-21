import { useMemo, useState } from "react";
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
import { ROLE_LABELS } from "../me";

type KeyState = "active" | "expiring" | "expired" | "revoked";

type KeyRow = {
  id: number;
  name: string;
  user_id: number;
  username: string | null;
  display_name: string;
  key_prefix: string;
  scopes: string[];
  state: KeyState;
  status: string;
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
  revoked_at: string | null;
  revoke_reason: string | null;
};

type Summary = {
  active: number;
  expiring: number;
  expired: number;
  revoked: number;
  total: number;
};

type StaffRow = { id: number; username: string; display_name: string; role: string; status: string };

const SCOPE_LABELS: Record<string, string> = {
  read: "读",
  write: "写",
  delete: "删除",
  billing: "财务",
  staff: "账号",
};

/** Only the first three are offered here; billing/staff stay admin-session-only. */
const SCOPE_OPTIONS = [
  { value: "read", label: "读：查询剧集、分集、上传状态" },
  { value: "write", label: "写：建剧、改剧、上传、发布" },
  { value: "delete", label: "删除：删剧（高危）" },
];

const TTL_OPTIONS = [
  { value: 30, label: "30 天" },
  { value: 90, label: "90 天" },
  { value: 180, label: "180 天" },
  { value: 365, label: "365 天" },
  { value: 0, label: "长期（不推荐）" },
];

/** One source of truth for the copy-paste commands (see docs/cli-execution-plan.md §3.7). */
const CLI_INSTALL = "curl -fsSL https://minitv.us/cli/install.sh | sh";
const CLI_SET_KEY = "minitv config set-key";
const CLI_DOCTOR = "minitv doctor";
const cliOneLiner = (key: string) =>
  `curl -fsSL https://minitv.us/cli/install.sh | MINITV_API_KEY='${key}' sh`;

const STATE_TAG: Record<KeyState, { color: string; label: string }> = {
  active: { color: "green", label: "有效" },
  expiring: { color: "orange", label: "即将到期" },
  expired: { color: "red", label: "已过期" },
  revoked: { color: "default", label: "已撤销" },
};

function scopeText(scopes: string[]): string {
  if (!scopes.length) return "跟随角色";
  return scopes.map((scope) => SCOPE_LABELS[scope] ?? scope).join(" · ");
}

export default function ApiKeys() {
  const { confirm, confirmModal } = useConfirm();

  const [query, setQuery] = useState("");
  const [stateFilter, setStateFilter] = useState<string>("");
  const [createOpen, setCreateOpen] = useState(false);
  const [agentOpen, setAgentOpen] = useState(false);
  const [editing, setEditing] = useState<KeyRow | null>(null);
  const [secret, setSecret] = useState<{ key: string; name: string; expires_at: string | null } | null>(
    null
  );
  // The plaintext modal opens only after the create dialog has finished its
  // close animation, otherwise both modals are visible for ~200ms.
  const [pendingSecret, setPendingSecret] = useState<{
    key: string;
    name: string;
    expires_at: string | null;
  } | null>(null);
  const [createForm] = Form.useForm();
  const [editForm] = Form.useForm();

  const params = new URLSearchParams();
  if (query) params.set("q", query);
  // The list endpoint filters on the stored status; "expiring"/"expired" are
  // derived states the server paints, so those are filtered on the client.
  if (stateFilter === "active" || stateFilter === "revoked") params.set("status", stateFilter);
  const path = `/admin/v1/api-keys${params.toString() ? `?${params.toString()}` : ""}`;
  const { data, reload, loading } = useLoad<{ items: KeyRow[]; summary: Summary; total: number }>(
    path,
    { items: [], summary: { active: 0, expiring: 0, expired: 0, revoked: 0, total: 0 }, total: 0 }
  );

  const staff = useLoad<{ items: StaffRow[] }>("/admin/v1/staff", { items: [] });
  const staffOptions = useMemo(
    () =>
      staff.data.items
        .filter((row) => row.status === "active")
        .map((row) => ({
          value: row.id,
          label: `${row.username}（${ROLE_LABELS[row.role] ?? row.role}）`,
        })),
    [staff.data.items]
  );

  const rows = useMemo(() => {
    // `expiring` / `expired` are derived states, and an expired key still has
    // status=active in the database — so every non-empty filter has to be
    // applied on the painted state, not only on the server-side status.
    if (!stateFilter) return data.items;
    return data.items.filter((row) => row.state === stateFilter);
  }, [data.items, stateFilter]);

  const onCreate = async (values: any) => {
    try {
      const created = await api<KeyRow & { key: string }>("/admin/v1/api-keys", {
        method: "POST",
        body: JSON.stringify({
          user_id: values.user_id,
          name: values.name,
          scopes: values.scopes,
          expires_in_days: values.expires_in_days === 0 ? null : values.expires_in_days,
        }),
      });
      setCreateOpen(false);
      createForm.resetFields();
      setPendingSecret({ key: created.key, name: created.name, expires_at: created.expires_at });
      reload();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const onEdit = async (values: any) => {
    if (!editing) return;
    try {
      await api(`/admin/v1/api-keys/${editing.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: values.name, scopes: values.scopes }),
      });
      setEditing(null);
      reload();
      message.success("已保存");
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const onRotate = (row: KeyRow) => {
    confirm({
      title: `轮换密钥 ${row.name}`,
      content: "旧密钥会立即失效，正在使用它的自动化会中断。",
      okText: "轮换",
      onOk: async () => {
        try {
          const fresh = await api<KeyRow & { key: string }>(
            `/admin/v1/api-keys/${row.id}/rotate`,
            { method: "POST", body: JSON.stringify({}) }
          );
          setSecret({ key: fresh.key, name: fresh.name, expires_at: fresh.expires_at });
          reload();
        } catch (e) {
          message.error(errorText(e));
          throw e; // keep the dialog open so the operator can retry
        }
      },
    });
  };

  const onRevoke = (row: KeyRow) => {
    confirm({
      title: `撤销密钥 ${row.name}`,
      content: "撤销后立即失效且不可恢复；需要继续使用请新建或轮换。",
      okText: "撤销",
      danger: true,
      onOk: async () => {
        try {
          await api(`/admin/v1/api-keys/${row.id}/revoke`, {
            method: "POST",
            body: JSON.stringify({}),
          });
          reload();
          message.success("已撤销");
        } catch (e) {
          message.error(errorText(e));
          throw e;
        }
      },
    });
  };

  const onDelete = (row: KeyRow) => {
    confirm({
      title: `删除密钥 ${row.name}`,
      content: "仅从未使用过的密钥可以删除。",
      okText: "删除",
      danger: true,
      onOk: async () => {
        try {
          await api(`/admin/v1/api-keys/${row.id}`, { method: "DELETE" });
          reload();
          message.success("已删除");
        } catch (e) {
          message.error(errorText(e));
          throw e;
        }
      },
    });
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      message.success("已复制");
    } catch {
      message.warning("复制失败，请手动选中");
    }
  };

  const copySecret = () => (secret ? copy(secret.key) : Promise.resolve());

  return (
    <Space direction="vertical" size={14} style={{ width: "100%" }}>
      <Space wrap size={12} style={{ justifyContent: "space-between", width: "100%" }}>
        <Space wrap size={8}>
          <Input.Search
            allowClear
            placeholder="搜索名称 / 前缀 / 账号"
            style={{ width: 260 }}
            onSearch={setQuery}
          />
          <Select
            style={{ width: 150 }}
            value={stateFilter}
            onChange={setStateFilter}
            options={[
              { value: "", label: "全部状态" },
              { value: "active", label: "有效" },
              { value: "expiring", label: "即将到期" },
              { value: "expired", label: "已过期" },
              { value: "revoked", label: "已撤销" },
            ]}
          />
          <Tag color="green">有效 {data.summary.active}</Tag>
          {data.summary.expiring > 0 && <Tag color="orange">即将到期 {data.summary.expiring}</Tag>}
          {data.summary.expired > 0 && <Tag color="red">已过期 {data.summary.expired}</Tag>}
          {data.summary.revoked > 0 && <Tag>已撤销 {data.summary.revoked}</Tag>}
        </Space>
        <Space>
          <Button onClick={() => setAgentOpen(true)}>接入 Agent</Button>
          <Button onClick={() => reload()}>刷新</Button>
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建密钥
          </Button>
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        message="密钥只在创建或轮换时显示一次，之后只能看到前缀。密钥的权限永远不会超过其绑定账号的角色。"
      />

      <Table<KeyRow>
        rowKey="id"
        loading={loading}
        dataSource={rows}
        scroll={{ x: 1120 }}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        columns={[
          {
            title: "名称",
            dataIndex: "name",
            width: 210,
            render: (value: string, row) => (
              <div>
                <Typography.Text>{value}</Typography.Text>
                <div style={{ fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
                  {row.last_used_at ? `最近使用 ${formatTime(row.last_used_at)}` : "从未使用"}
                </div>
              </div>
            ),
          },
          {
            title: "绑定账号",
            dataIndex: "username",
            width: 190,
            render: (value: string | null, row) => (
              <div>
                <Typography.Text code style={{ whiteSpace: "nowrap" }}>
                  {value ?? "-"}
                </Typography.Text>
                {row.display_name && (
                  <div style={{ fontSize: 12, color: "rgba(0,0,0,0.45)" }}>{row.display_name}</div>
                )}
              </div>
            ),
          },
          {
            title: "前缀",
            dataIndex: "key_prefix",
            width: 170,
            render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
          },
          {
            title: "权限",
            dataIndex: "scopes",
            width: 130,
            render: (value: string[]) => (
              <Typography.Text
                type={value.includes("delete") ? "danger" : undefined}
                style={{ fontSize: 12 }}
              >
                {scopeText(value)}
              </Typography.Text>
            ),
          },
          {
            title: "状态",
            dataIndex: "state",
            width: 100,
            render: (value: KeyState) => (
              <Tag color={STATE_TAG[value].color}>{STATE_TAG[value].label}</Tag>
            ),
          },
          {
            title: "到期",
            dataIndex: "expires_at",
            width: 150,
            render: (value: string | null) =>
              value ? formatTime(value) : <Typography.Text type="secondary">长期</Typography.Text>,
          },
          {
            title: "操作",
            key: "actions",
            width: 210,
            render: (_, row) => {
              const revoked = row.status === "revoked";
              return (
                <Space size={2}>
                  <Button
                    type="link"
                    size="small"
                    disabled={revoked}
                    onClick={() => {
                      setEditing(row);
                      editForm.setFieldsValue({ name: row.name, scopes: row.scopes });
                    }}
                  >
                    编辑
                  </Button>
                  <Tooltip title={revoked ? "已撤销，请新建" : ""}>
                    <Button
                      type="link"
                      size="small"
                      disabled={revoked}
                      onClick={() => onRotate(row)}
                    >
                      轮换
                    </Button>
                  </Tooltip>
                  <Button
                    type="link"
                    size="small"
                    danger
                    disabled={revoked}
                    onClick={() => onRevoke(row)}
                  >
                    撤销
                  </Button>
                  <Tooltip title={row.last_used_at ? "已使用过，只能撤销" : ""}>
                    <Button
                      type="link"
                      size="small"
                      danger
                      disabled={row.last_used_at !== null}
                      onClick={() => onDelete(row)}
                    >
                      删除
                    </Button>
                  </Tooltip>
                </Space>
              );
            },
          },
        ]}
      />

      <Typography.Text type="secondary">
        共 {data.total} 把密钥 · 明文只在签发时出现一次，之后只能看到前缀
      </Typography.Text>

      <Modal
        title="新建密钥"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        okText="签发"
        forceRender
        afterClose={() => {
          if (pendingSecret) {
            setSecret(pendingSecret);
            setPendingSecret(null);
          }
        }}
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={onCreate}
          preserve={false}
          initialValues={{ scopes: ["read", "write"], expires_in_days: 90 }}
        >
          <Form.Item
            label="绑定账号"
            name="user_id"
            extra="密钥的权限不会超过该账号的角色；账号被禁用时密钥立即失效"
            rules={[{ required: true, message: "请选择账号" }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="选择 staff 账号"
              options={staffOptions}
            />
          </Form.Item>
          <Form.Item
            label="名称"
            name="name"
            extra="同一账号下不能重名，建议写成 用途/实例"
            rules={[{ required: true, message: "请输入名称" }]}
          >
            <Input maxLength={64} placeholder="如 agent-ops/upload-bot" />
          </Form.Item>
          <Form.Item label="权限" name="scopes" rules={[{ required: true }]}>
            <Checkbox.Group options={SCOPE_OPTIONS} style={{ display: "grid", gap: 6 }} />
          </Form.Item>
          <Form.Item label="有效期" name="expires_in_days">
            <Select options={TTL_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑密钥"
        open={editing !== null}
        onCancel={() => setEditing(null)}
        onOk={() => editForm.submit()}
        okText="保存"
        forceRender
      >
        <Form form={editForm} layout="vertical" onFinish={onEdit}>
          <Form.Item label="名称" name="name" rules={[{ required: true }]}>
            <Input maxLength={64} />
          </Form.Item>
          <Form.Item label="权限" name="scopes" rules={[{ required: true }]}>
            <Checkbox.Group options={SCOPE_OPTIONS} style={{ display: "grid", gap: 6 }} />
          </Form.Item>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            到期时间不能在这里改；需要延长请轮换。
          </Typography.Text>
        </Form>
      </Modal>

      <Modal
        title="密钥已生成"
        open={secret !== null}
        onCancel={() => setSecret(null)}
        footer={
          <Button type="primary" onClick={() => setSecret(null)}>
            我已保存
          </Button>
        }
        maskClosable={false}
        width={620}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="只显示这一次。关闭后无法再次查看，遗失请轮换。"
        />
        <Input.TextArea
          readOnly
          value={secret?.key ?? ""}
          autoSize={{ minRows: 2, maxRows: 3 }}
          style={{ fontFamily: "monospace", fontSize: 12 }}
        />
        <Space style={{ marginTop: 12, justifyContent: "space-between", width: "100%" }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {secret ? `到期 ${secret.expires_at ? formatTime(secret.expires_at) : "长期"}` : ""}
          </Typography.Text>
          <Button onClick={copySecret}>复制</Button>
        </Space>

        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 12, color: "rgba(0,0,0,0.65)", marginBottom: 6 }}>
            复制给 Agent 的一键命令（安装 CLI + 写入密钥 + 自检）
          </div>
          <div
            style={{
              background: "#e6f1fb",
              border: "1px solid #b5d4f4",
              borderRadius: 8,
              padding: "10px 11px",
            }}
          >
            <Typography.Text
              style={{ fontFamily: "monospace", fontSize: 12, wordBreak: "break-all" }}
            >
              {cliOneLiner(secret?.key ?? "")}
            </Typography.Text>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginTop: 8,
                gap: 12,
              }}
            >
              <span style={{ fontSize: 11, color: "#a32d2d" }}>
                命令含密钥，勿截图或粘贴到公开渠道
              </span>
              <Button size="small" onClick={() => copy(cliOneLiner(secret?.key ?? ""))}>
                复制命令
              </Button>
            </div>
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 6 }}>
            密钥经环境变量传入、在安装脚本内部走 stdin，不会出现在进程列表；执行方的 shell 历史仍会留下这条命令。
          </Typography.Text>
        </div>
      </Modal>

      <Modal
        title="接入 Agent"
        open={agentOpen}
        onCancel={() => setAgentOpen(false)}
        footer={null}
        width={620}
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
          三步把命令行交给 Agent。装完后它能查剧、导数据，权限不超过这把密钥。
        </Typography.Paragraph>

        {[
          {
            title: "① 安装 CLI",
            code: CLI_INSTALL,
            hint: "装到 ~/.local/bin/minitv，需要 python3 ≥ 3.8；重复执行即升级。",
          },
          {
            title: "② 设置 API Key",
            code: CLI_SET_KEY,
            hint: "隐藏输入、先校验再落盘；也可 echo \"$KEY\" | minitv config set-key --stdin。",
          },
          {
            title: "③ 自检",
            code: CLI_DOCTOR,
            hint: "连不上会说清是网络、密钥还是权限问题。",
          },
        ].map((step) => (
          <div key={step.title} style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>{step.title}</div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 12,
                background: "rgba(0,0,0,0.03)",
                border: "1px solid rgba(0,0,0,0.08)",
                borderRadius: 8,
                padding: "9px 11px",
              }}
            >
              <Typography.Text
                style={{ fontFamily: "monospace", fontSize: 12, wordBreak: "break-all" }}
              >
                {step.code}
              </Typography.Text>
              <Button size="small" onClick={() => copy(step.code)}>
                复制
              </Button>
            </div>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {step.hint}
            </Typography.Text>
          </div>
        ))}

        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          已有密钥时按上面三步手工做一次即可；想把密钥一起交给 Agent，就在新建密钥成功后复制弹窗里的「一键命令」。
        </Typography.Text>
      </Modal>

      {confirmModal}
    </Space>
  );
}
