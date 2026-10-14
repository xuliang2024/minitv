import { useCallback, useEffect, useState } from "react";
import { Button, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message } from "antd";
import { api } from "../api";

type TagRow = {
  id: number;
  name_en: string;
  name_zh: string;
  category: string;
  status: string;
  drama_count: number;
};

export default function Tags() {
  const [rows, setRows] = useState<TagRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<TagRow | null>(null);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api<{ items: TagRow[] }>("/admin/v1/tags");
      setRows(res.items);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (row: TagRow) => {
    setEditing(row);
    form.setFieldsValue({
      name_en: row.name_en,
      name_zh: row.name_zh,
      category: row.category,
      status: row.status,
    });
    setModalOpen(true);
  };

  const onSave = async (values: any) => {
    try {
      if (editing) {
        await api(`/admin/v1/tags/${editing.id}`, { method: "PATCH", body: JSON.stringify(values) });
      } else {
        await api("/admin/v1/tags", { method: "POST", body: JSON.stringify(values) });
      }
      message.success("已保存");
      setModalOpen(false);
      await load();
    } catch (e: any) {
      const detail = e?.detail;
      if (typeof detail === "string") {
        message.error(`保存失败：${detail}`);
      } else if (detail?.msg) {
        const field = detail?.loc?.slice(-1)[0];
        message.error(`保存失败：${field ? field + " " : ""}${detail.msg}`);
      } else {
        message.error(`保存失败：${e?.message ?? e}`);
      }
    }
  };

  const onDelete = async (row: TagRow) => {
    try {
      await api(`/admin/v1/tags/${row.id}`, { method: "DELETE" });
      message.success("已删除");
      await load();
    } catch (e: any) {
      const detail = e?.detail;
      if (detail?.affected_dramas) {
        const names = detail.affected_dramas.map((d: any) => `#${d.id} ${d.title}`).join("、");
        Modal.warning({ title: "标签已被使用，无法删除", content: `影响范围：${names}` });
      } else {
        message.error(`删除失败：${e?.message ?? e}`);
      }
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space>
        <Button type="primary" onClick={openCreate}>
          + 新建标签
        </Button>
        <Typography.Text type="secondary">共 {rows.length} 个标签</Typography.Text>
      </Space>
      <Table<TagRow>
        rowKey="id"
        loading={loading}
        dataSource={rows}
        scroll={{ x: 760 }}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        columns={[
          { title: "英文名（必填）", dataIndex: "name_en", render: (v: string) => <Typography.Text code>{v}</Typography.Text> },
          { title: "中文名", dataIndex: "name_zh" },
          { title: "分类", dataIndex: "category", render: (v: string) => v || "-" },
          { title: "关联剧数", dataIndex: "drama_count", width: 90 },
          {
            title: "状态",
            dataIndex: "status",
            width: 90,
            render: (v: string) =>
              v === "enabled" ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>,
          },
          {
            title: "操作",
            key: "actions",
            width: 140,
            render: (_, r) => (
              <Space>
                <Button type="link" size="small" onClick={() => openEdit(r)}>
                  编辑
                </Button>
                <Popconfirm title="确认删除该标签？" onConfirm={() => onDelete(r)}>
                  <Button type="link" size="small" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <Modal
        title={editing ? `编辑标签：${editing.name_en}` : "新建标签"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={onSave}>
          <Form.Item
            label="英文名"
            name="name_en"
            rules={[{ required: true, message: "英文名必填" }]}
          >
            <Input maxLength={64} placeholder="Urban Fantasy" />
          </Form.Item>
          <Form.Item label="中文名" name="name_zh">
            <Input maxLength={64} placeholder="都市奇幻" />
          </Form.Item>
          <Form.Item label="分类" name="category">
            <Input maxLength={32} placeholder="genre / mood / region" />
          </Form.Item>
          <Form.Item label="状态" name="status" initialValue="enabled">
            <Select
              options={[
                { value: "enabled", label: "启用" },
                { value: "disabled", label: "停用" },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
