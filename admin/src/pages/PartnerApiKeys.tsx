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
  Typography,
  message,
} from "antd";
import { api, errorText } from "../api";
import { formatTime, useLoad } from "../hooks";

type KeyState = "active" | "expiring" | "expired" | "revoked";

type KeyRow = {
  id: number;
  name: string;
  key_prefix: string;
  scopes: string[];
  state: KeyState;
  status: string;
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
  revoked_at: string | null;
};

type Summary = { active: number; expiring: number; expired: number; revoked: number; total: number };

const SCOPE_LABELS: Record<string, string> = {
  read: "读",
  write: "写",
  upload: "上传",
};

const SCOPE_OPTIONS = [
  { value: "read", label: "读：查询剧集、分集、上传状态" },
  { value: "write", label: "写：建剧、改剧、提交审核" },
  { value: "upload", label: "上传：视频、封面" },
];

const TTL_OPTIONS = [
  { value: 30, label: "30 天" },
  { value: 90, label: "90 天" },
  { value: 180, label: "180 天" },
  { value: 365, label: "365 天" },
  { value: 0, label: "长期（不推荐）" },
];

const STATE_TAG: Record<KeyState, { color: string; label: string }> = {
  active: { color: "green", label: "有效" },
  expiring: { color: "orange", label: "即将到期" },
  expired: { color: "red", label: "已过期" },
  revoked: { color: "default", label: "已撤销" },
};

const CLI_ONE_LINER = (key: string) =>
  `curl -fsSL https://minitv.us/cli/install.sh | MINITV_API_KEY='${key}' sh`;

function scopeText(scopes: string[]): string {
  if (!scopes.length) return "跟随角色";
  return scopes.map((scope) => SCOPE_LABELS[scope] ?? scope).join(" · ");
}

export default function PartnerApiKeys() {
  const { data, loading, reload } = useLoad<{ items: KeyRow[]; summary?: Summary }>(
    "/partner/v1/api-keys",
    { items: [], summary: { active: 0, expiring: 0, expired: 0, revoked: 0, total: 0 } }
  );
  const [query, setQuery] = useState("");
  const [stateFilter, setStateFilter] = useState<string>("");
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<KeyRow | null>(null);
  const [secret, setSecret] = useState<{ key: string; name: string; expires_at: string | null } | null>(
    null
  );
  const [pendingSecret, setPendingSecret] = useState<{
    key: string;
    name: string;
    expires_at: string | null;
  } | null>(null);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return data.items.filter((row) => {
      if (needle && !row.name.toLowerCase().includes(needle)) return false;
      if (stateFilter && row.state !== stateFilter) return false;
      return true;
    });
  }, [data.items, query, stateFilter]);

  const summary = data.summary ?? { active: 0, expiring: 0, expired: 0, revoked: 0, total: 0 };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      message.success("已复制");
    } catch {
      message.error("复制失败，请手动选中");
    }
  };

  const create = async (values: any) => {
    try {
      const created = await api<KeyRow & { key: string }>("/partner/v1/api-keys", {
        method: "POST",
        body: JSON.stringify({
          name: values.name,
          scopes: values.scopes,
          expires_in_days: values.expires_in_days,
        }),
      });
      setCreateOpen(false);
      form.resetFields();
      setPendingSecret({
        key: created.key,
        name: created.name,
        expires_at: created.expires_at,
      });
      await reload();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const saveEdit = async (values: any) => {
    if (!editing) return;
    try {
      await api(`/partner/v1/api-keys/${editing.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: values.name, scopes: values.scopes }),
      });
      message.success("已保存");
      setEditing(null);
      await reload();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const rotate = (row: KeyRow) => {
    Modal.confirm({
      title: `轮换密钥 ${row.name}`,
      content: "旧密钥立即失效，新密钥只显示一次。确认轮换？",
      okText: "轮换",
      onOk: async () => {
        try {
          const issued = await api<KeyRow & { key: string }>(
            `/partner/v1/api-keys/${row.id}/rotate`,
            { method: "POST" }
          );
          setSecret({ key: issued.key, name: issued.name, expires_at: issued.expires_at });
          await reload();
        } catch (e) {
          message.error(errorText(e));
          throw e;
        }
      },
    });
  };

  const revoke = (row: KeyRow) => {
    Modal.confirm({
      title: `撤销密钥 ${row.name}`,
      content: "撤销后使用该密钥的脚本会立刻失败，且不可恢复。",
      okText: "撤销",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api(`/partner/v1/api-keys/${row.id}/revoke`, {
            method: "POST",
            body: JSON.stringify({}),
          });
          message.success("已撤销");
          await reload();
        } catch (e) {
          message.error(errorText(e));
          throw e;
        }
      },
    });
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Space wrap style={{ justifyContent: "space-between", width: "100%" }}>
        <Space wrap>
          <Input.Search
            allowClear
            placeholder="搜索密钥名称"
            style={{ width: 220 }}
            onSearch={setQuery}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Select
            allowClear
            placeholder="全部状态"
            style={{ width: 140 }}
            value={stateFilter || undefined}
            onChange={(value) => setStateFilter(value ?? "")}
            options={[
              { value: "active", label: "有效" },
              { value: "expiring", label: "即将到期" },
              { value: "expired", label: "已过期" },
              { value: "revoked", label: "已撤销" },
            ]}
          />
          <Tag color="green">有效 {summary.active}</Tag>
          {summary.expiring > 0 && <Tag color="orange">即将到期 {summary.expiring}</Tag>}
          {summary.expired > 0 && <Tag color="red">已过期 {summary.expired}</Tag>}
          {summary.revoked > 0 && <Tag>已撤销 {summary.revoked}</Tag>}
        </Space>
        <Space>
          <Button onClick={() => reload()}>刷新</Button>
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建密钥
          </Button>
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        message="密钥只在创建或轮换时显示一次，之后只能看到前缀。密钥只能访问你自己名下的剧集。"
      />

      <Table<KeyRow>
        rowKey="id"
        loading={loading}
        dataSource={rows}
        scroll={{ x: 980 }}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        columns={[
          {
            title: "名称",
            dataIndex: "name",
            width: 220,
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
            title: "前缀",
            dataIndex: "key_prefix",
            width: 190,
            render: (value: string) => (
              <Typography.Text code style={{ whiteSpace: "nowrap" }}>
                {value}…
              </Typography.Text>
            ),
          },
          {
            title: "权限",
            dataIndex: "scopes",
            width: 150,
            render: (value: string[]) => (
              <Typography.Text style={{ fontSize: 12 }}>{scopeText(value)}</Typography.Text>
            ),
          },
          {
            title: "状态",
            dataIndex: "state",
            width: 100,
            render: (value: KeyState) => {
              const meta = STATE_TAG[value] ?? { color: "default", label: value };
              return <Tag color={meta.color}>{meta.label}</Tag>;
            },
          },
          {
            title: "到期",
            dataIndex: "expires_at",
            width: 150,
            render: (value: string | null) => formatTime(value),
          },
          {
            title: "操作",
            key: "actions",
            width: 190,
            render: (_, row) => (
              <Space size={4}>
                <Button
                  type="link"
                  size="small"
                  disabled={row.state === "revoked" || row.state === "expired"}
                  onClick={() => {
                    setEditing(row);
                    editForm.setFieldsValue({ name: row.name, scopes: row.scopes });
                  }}
                >
                  编辑
                </Button>
                <Button
                  type="link"
                  size="small"
                  disabled={row.state !== "active"}
                  onClick={() => rotate(row)}
                >
                  轮换
                </Button>
                <Button
                  type="link"
                  size="small"
                  danger
                  disabled={row.state === "revoked"}
                  onClick={() => revoke(row)}
                >
                  撤销
                </Button>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title="新建密钥"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
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
          form={form}
          layout="vertical"
          onFinish={create}
          preserve={false}
          initialValues={{ scopes: ["read", "write", "upload"], expires_in_days: 90 }}
        >
          <Form.Item
            label="名称"
            name="name"
            extra="建议写成 用途/实例，便于以后区分"
            rules={[{ required: true, message: "请输入名称" }]}
          >
            <Input maxLength={64} placeholder="如 ci-upload" />
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
        <Form form={editForm} layout="vertical" onFinish={saveEdit}>
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
          <Button onClick={() => copy(secret?.key ?? "")}>复制</Button>
        </Space>

        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 12, color: "rgba(0,0,0,0.65)", marginBottom: 6 }}>
            一键安装 CLI 并写入密钥（也可手动 minitv config set-key）
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
              {CLI_ONE_LINER(secret?.key ?? "")}
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
              <Button size="small" onClick={() => copy(CLI_ONE_LINER(secret?.key ?? ""))}>
                复制命令
              </Button>
            </div>
          </div>
        </div>
      </Modal>
    </Space>
  );
}
