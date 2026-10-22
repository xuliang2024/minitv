import { useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { api, errorText } from "../api";
import { formatTime, useLoad } from "../hooks";

type KeyRow = {
  id: number;
  name: string;
  key_prefix: string;
  scopes: string[];
  state: string;
  status: string;
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
};

const STATE: Record<string, { text: string; color?: string }> = {
  active: { text: "生效中", color: "green" },
  expired: { text: "已过期", color: "orange" },
  revoked: { text: "已撤销" },
};

export default function PartnerApiKeys() {
  const { data, loading, reload } = useLoad<{ items: KeyRow[] }>("/partner/v1/api-keys", {
    items: [],
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();
  const [plaintext, setPlaintext] = useState<string | null>(null);

  const create = async (values: any) => {
    try {
      const issued = await api<KeyRow & { key: string }>("/partner/v1/api-keys", {
        method: "POST",
        body: JSON.stringify({
          name: values.name,
          scopes: values.scopes,
          expires_in_days: values.expires_in_days,
        }),
      });
      setCreateOpen(false);
      form.resetFields();
      setPlaintext(issued.key);
      await reload();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const rotate = (row: KeyRow) => {
    Modal.confirm({
      title: `轮换「${row.name}」`,
      content: "旧密钥会立即失效；新密钥明文只显示一次。",
      okText: "轮换",
      onOk: async () => {
        try {
          const issued = await api<KeyRow & { key: string }>(
            `/partner/v1/api-keys/${row.id}/rotate`,
            { method: "POST" }
          );
          setPlaintext(issued.key);
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
      title: `撤销「${row.name}」`,
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
      <Space wrap>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          + 新建密钥
        </Button>
        <Typography.Text type="secondary">
          给上传脚本/CLI 用。密钥只能访问你自己的剧集，可随时撤销。
        </Typography.Text>
      </Space>

      <Table<KeyRow>
        rowKey="id"
        loading={loading}
        dataSource={data.items}
        pagination={false}
        columns={[
          { title: "名称", dataIndex: "name", width: 160 },
          {
            title: "前缀",
            dataIndex: "key_prefix",
            width: 180,
            render: (value: string) => <Typography.Text code>{value}…</Typography.Text>,
          },
          {
            title: "权限",
            dataIndex: "scopes",
            width: 220,
            render: (value: string[]) => (
              <Space size={4} wrap>
                {value.map((scope) => (
                  <Tag key={scope}>{scope}</Tag>
                ))}
              </Space>
            ),
          },
          {
            title: "状态",
            dataIndex: "state",
            width: 100,
            render: (value: string) => {
              const meta = STATE[value] ?? { text: value };
              return <Tag color={meta.color}>{meta.text}</Tag>;
            },
          },
          {
            title: "到期",
            dataIndex: "expires_at",
            width: 140,
            render: (value: string | null) => formatTime(value),
          },
          {
            title: "最近使用",
            dataIndex: "last_used_at",
            width: 140,
            render: (value: string | null) => formatTime(value),
          },
          {
            title: "操作",
            key: "actions",
            width: 140,
            render: (_, row) => (
              <Space size={4}>
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
        title="新建 API 密钥"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        okText="创建"
        forceRender
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
            rules={[{ required: true, message: "比如 ci-upload" }]}
          >
            <Input maxLength={64} placeholder="ci-upload" />
          </Form.Item>
          <Form.Item label="权限" name="scopes" rules={[{ required: true }]}>
            <Checkbox.Group
              options={[
                { label: "read（查看）", value: "read" },
                { label: "write（建剧/改剧/提交）", value: "write" },
                { label: "upload（上传视频/封面）", value: "upload" },
              ]}
            />
          </Form.Item>
          <Form.Item label="有效天数" name="expires_in_days">
            <InputNumber min={1} max={365} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="请立即保存密钥"
        open={plaintext !== null}
        onCancel={() => setPlaintext(null)}
        footer={
          <Button type="primary" onClick={() => setPlaintext(null)}>
            我已保存
          </Button>
        }
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Alert
            type="warning"
            showIcon
            message="明文只显示一次，关闭后无法再次查看；丢了就轮换。"
          />
          <Typography.Paragraph copyable={{ text: plaintext ?? "" }} code>
            {plaintext}
          </Typography.Paragraph>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            用法：minitv config set-key --profile partner，然后粘贴上面的 key。
          </Typography.Text>
        </Space>
      </Modal>
    </Space>
  );
}
