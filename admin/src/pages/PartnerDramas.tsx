import { useState } from "react";
import { Button, Form, Input, Modal, Space, Table, Tag, Typography, message } from "antd";
import { Link, useNavigate } from "react-router-dom";
import { api, errorText } from "../api";
import { formatTime, useLoad } from "../hooks";

type PartnerDrama = {
  id: number;
  title: string;
  synopsis: string;
  cover_url: string;
  status: string;
  review_state: string;
  review_note: string | null;
  free_episode_count: number;
  episode_count: number;
  submitted_at: string | null;
  reviewed_at: string | null;
  updated_at: string;
};

function stateTag(row: PartnerDrama) {
  if (row.review_state === "rejected") return <Tag color="red">已驳回</Tag>;
  if (row.review_state === "pending") return <Tag color="orange">待审核</Tag>;
  if (row.status === "published") return <Tag color="green">已上架</Tag>;
  if (row.status === "unpublished") return <Tag>已下架</Tag>;
  if (row.review_state === "approved") return <Tag color="blue">已通过待上架</Tag>;
  return <Tag>草稿</Tag>;
}

/** Read-only in P1: creating dramas and uploading episodes land in P2. */
export default function PartnerDramas() {
  const navigate = useNavigate();
  const { data, loading, reload } = useLoad<{ items: PartnerDrama[] }>("/partner/v1/dramas", {
    items: [],
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();

  const create = async (values: any) => {
    try {
      const created = await api<{ id: number }>("/partner/v1/dramas", {
        method: "POST",
        body: JSON.stringify({
          title: values.title,
          synopsis: values.synopsis ?? "",
        }),
      });
      message.success("已创建草稿，去上传封面和分集");
      setCreateOpen(false);
      form.resetFields();
      navigate(`/partner/dramas/${created.id}`);
    } catch (e) {
      message.error(errorText(e));
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space wrap>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          + 新建剧集
        </Button>
        <Typography.Text type="secondary">
          这里只有你自己的剧集。提交审核后由 MiniTV 运营安排上架。
        </Typography.Text>
      </Space>
      <Table<PartnerDrama>
        rowKey="id"
        loading={loading}
        dataSource={data.items}
        pagination={false}
        scroll={{ x: 860 }}
        columns={[
          {
            title: "剧集",
            dataIndex: "title",
            width: 260,
            render: (value: string, row) => (
              <div>
                <Link to={`/partner/dramas/${row.id}`} style={{ fontWeight: 500 }}>
                  {value}
                </Link>
                {row.review_note && (
                  <div style={{ fontSize: 12, color: "#A32D2D" }}>驳回：{row.review_note}</div>
                )}
              </div>
            ),
          },
          {
            title: "状态",
            key: "state",
            width: 140,
            render: (_, row) => stateTag(row),
          },
          { title: "集数", dataIndex: "episode_count", width: 80 },
          { title: "免费集数", dataIndex: "free_episode_count", width: 100 },
          {
            title: "提交时间",
            dataIndex: "submitted_at",
            width: 150,
            render: (value: string | null) => formatTime(value),
          },
          {
            title: "最近更新",
            dataIndex: "updated_at",
            width: 150,
            render: (value: string) => formatTime(value),
          },
          {
            title: "操作",
            key: "actions",
            width: 90,
            render: (_, row) => (
              <Link to={`/partner/dramas/${row.id}`}>
                {row.review_state === "pending" ? "查看" : "编辑"}
              </Link>
            ),
          },
        ]}
      />

      <Modal
        title="新建剧集"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        okText="创建"
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
