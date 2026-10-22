import { useState } from "react";
import {
  Button,
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
import { useConfirm } from "../confirm";
import { formatTime, useLoad } from "../hooks";

type SupplierAccount = {
  id: number;
  username: string;
  status: string;
  last_login_at: string | null;
};

type SupplierRow = {
  id: number;
  name: string;
  contact_name: string;
  contact_email: string;
  status: string;
  revenue_share_bps: number;
  drama_count: number;
  account: SupplierAccount | null;
  created_at: string;
};

export default function Suppliers() {
  const { confirm, confirmModal } = useConfirm();
  const { data, reload, loading } = useLoad<{ items: SupplierRow[] }>("/admin/v1/suppliers", {
    items: [],
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();

  const [shareTarget, setShareTarget] = useState<SupplierRow | null>(null);
  const [shareForm] = Form.useForm();

  const [accountTarget, setAccountTarget] = useState<SupplierRow | null>(null);
  const [accountForm] = Form.useForm();

  const openCreate = () => {
    createForm.resetFields();
    createForm.setFieldsValue({ revenue_share_bps: 80 });
    setCreateOpen(true);
  };

  const onCreate = async (values: any) => {
    try {
      await api("/admin/v1/suppliers", {
        method: "POST",
        body: JSON.stringify({
          name: values.name,
          contact_name: values.contact_name ?? "",
          contact_email: values.contact_email ?? "",
          revenue_share_bps: Math.round((values.revenue_share_bps ?? 80) * 100),
          note: values.note ?? "",
        }),
      });
      message.success("已创建供应商，下一步为它创建登录账号");
      setCreateOpen(false);
      await reload();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const openShare = (row: SupplierRow) => {
    setShareTarget(row);
    shareForm.setFieldsValue({
      share_percent: row.revenue_share_bps / 100,
      note: "",
    });
  };

  const onShare = async (values: any) => {
    if (!shareTarget) return;
    try {
      await api(`/admin/v1/suppliers/${shareTarget.id}/share`, {
        method: "POST",
        body: JSON.stringify({
          share_bps: Math.round(values.share_percent * 100),
          note: values.note ?? "",
        }),
      });
      message.success("分成比例已更新，历史订单仍按原比例结算");
      setShareTarget(null);
      await reload();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const onToggle = (row: SupplierRow) => {
    const disabling = row.status === "active";
    confirm({
      title: `${disabling ? "禁用" : "启用"}供应商：${row.name}`,
      content: disabling
        ? "禁用后该供应商的账号会立即退出登录且无法再登录，已上架的剧不受影响。"
        : "启用后该账号可以重新登录。",
      danger: disabling,
      okText: disabling ? "禁用" : "启用",
      onOk: async () => {
        try {
          await api(`/admin/v1/suppliers/${row.id}/status`, {
            method: "POST",
            body: JSON.stringify({ status: disabling ? "disabled" : "active" }),
          });
          message.success("已保存");
          await reload();
        } catch (e) {
          message.error(errorText(e));
          throw e;
        }
      },
    });
  };

  const openAccount = (row: SupplierRow) => {
    setAccountTarget(row);
    accountForm.resetFields();
    accountForm.setFieldsValue({ username: `sp-${row.id}` });
  };

  const onAccount = async (values: any) => {
    if (!accountTarget) return;
    try {
      await api("/admin/v1/staff", {
        method: "POST",
        body: JSON.stringify({
          username: values.username,
          display_name: accountTarget.name,
          role: "partner",
          password: values.password,
          supplier_id: accountTarget.id,
        }),
      });
      message.success("账号已创建，请把用户名和新密码交给对方");
      setAccountTarget(null);
      await reload();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space wrap>
        <Button type="primary" onClick={openCreate}>
          + 新建供应商
        </Button>
        <Typography.Text type="secondary">
          一个供应商只能绑定一个账号 · 新供应商默认分成 80%
        </Typography.Text>
      </Space>

      <Table<SupplierRow>
        rowKey="id"
        loading={loading}
        dataSource={data.items}
        pagination={false}
        scroll={{ x: 1000 }}
        columns={[
          { title: "供应商", dataIndex: "name", width: 180 },
          {
            title: "联系人",
            key: "contact",
            width: 200,
            render: (_, row) => (
              <div>
                <div>{row.contact_name || "-"}</div>
                <div style={{ fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
                  {row.contact_email || ""}
                </div>
              </div>
            ),
          },
          {
            title: "绑定账号",
            key: "account",
            width: 230,
            render: (_, row) =>
              row.account ? (
                <div>
                  <Typography.Text code>{row.account.username}</Typography.Text>
                  <div style={{ fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
                    {row.account.status === "active" ? "启用" : "已禁用"} · 最近登录{" "}
                    {formatTime(row.account.last_login_at)}
                  </div>
                </div>
              ) : (
                <Button size="small" onClick={() => openAccount(row)}>
                  创建账号
                </Button>
              ),
          },
          { title: "剧数", dataIndex: "drama_count", width: 80 },
          {
            title: "分成",
            dataIndex: "revenue_share_bps",
            width: 100,
            render: (value: number) => `${(value / 100).toFixed(0)}%`,
          },
          {
            title: "状态",
            dataIndex: "status",
            width: 90,
            render: (value: string) =>
              value === "active" ? <Tag color="green">启用</Tag> : <Tag>已禁用</Tag>,
          },
          {
            title: "操作",
            key: "actions",
            width: 180,
            render: (_, row) => (
              <Space size={4}>
                <Button type="link" size="small" onClick={() => openShare(row)}>
                  改分成
                </Button>
                <Button
                  type="link"
                  size="small"
                  danger={row.status === "active"}
                  onClick={() => onToggle(row)}
                >
                  {row.status === "active" ? "禁用" : "启用"}
                </Button>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title="新建供应商"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        okText="创建"
        forceRender
      >
        <Form form={createForm} layout="vertical" onFinish={onCreate} preserve={false}>
          <Form.Item
            label="供应商名称"
            name="name"
            rules={[{ required: true, message: "请输入名称" }]}
          >
            <Input maxLength={100} placeholder="如 星野传媒" />
          </Form.Item>
          <Form.Item label="联系人" name="contact_name">
            <Input maxLength={64} />
          </Form.Item>
          <Form.Item
            label="联系邮箱"
            name="contact_email"
            rules={[{ type: "email", message: "邮箱格式不正确" }]}
          >
            <Input maxLength={320} placeholder="用于接收审核结果" />
          </Form.Item>
          <Form.Item
            label="分成比例（%）"
            name="revenue_share_bps"
            extra="默认 80%，可随时调整；历史订单按当时比例结算"
            rules={[{ required: true, message: "请输入分成比例" }]}
          >
            <InputNumber min={0} max={100} style={{ width: "100%" }} addonAfter="%" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={shareTarget ? `调整分成：${shareTarget.name}` : "调整分成"}
        open={shareTarget !== null}
        onCancel={() => setShareTarget(null)}
        onOk={() => shareForm.submit()}
        okText="保存"
        forceRender
      >
        <Form form={shareForm} layout="vertical" onFinish={onShare} preserve={false}>
          <Form.Item
            label="新分成比例（%）"
            name="share_percent"
            rules={[{ required: true, message: "请输入比例" }]}
          >
            <InputNumber min={0} max={100} style={{ width: "100%" }} addonAfter="%" />
          </Form.Item>
          <Form.Item label="变更说明" name="note">
            <Input maxLength={200} placeholder="如 续约下调 / 首年优惠" />
          </Form.Item>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            改完只影响之后支付的订单；已生成的对账单不会回溯。
          </Typography.Text>
        </Form>
      </Modal>

      <Modal
        title={accountTarget ? `创建账号：${accountTarget.name}` : "创建账号"}
        open={accountTarget !== null}
        onCancel={() => setAccountTarget(null)}
        onOk={() => accountForm.submit()}
        okText="创建账号"
        forceRender
      >
        <Form form={accountForm} layout="vertical" onFinish={onAccount} preserve={false}>
          <Form.Item
            label="登录账号"
            name="username"
            rules={[
              { required: true, message: "请输入账号" },
              { pattern: /^[a-z0-9][a-z0-9._-]{2,31}$/, message: "3–32 位小写字母、数字或 . _ -" },
            ]}
          >
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item
            label="初始密码"
            name="password"
            rules={[
              { required: true, message: "请输入初始密码" },
              { min: 8, message: "至少 8 位" },
            ]}
          >
            <Input autoComplete="new-password" />
          </Form.Item>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            一个供应商只能有一个账号；创建后请通过安全渠道把账号密码交给对方。
          </Typography.Text>
        </Form>
      </Modal>

      {confirmModal}
    </Space>
  );
}
