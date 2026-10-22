import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Button,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import { api, errorText } from "../api";
import { formatTime, useLoad } from "../hooks";

type ReviewRow = {
  id: number;
  title: string;
  cover_url: string;
  status: string;
  review_state: string;
  review_note: string | null;
  free_episode_count: number;
  episode_count: number;
  submitted_at: string | null;
  reviewed_at: string | null;
  supplier: { id: number; name: string; contact_email: string } | null;
};

type Payload = {
  items: ReviewRow[];
  total: number;
  counts: { pending: number; approved: number; rejected: number };
};

const TABS = [
  { key: "pending", label: "待审核" },
  { key: "approved", label: "待上架" },
  { key: "rejected", label: "已驳回" },
];

export default function Reviews() {
  const [state, setState] = useState("pending");
  const [page, setPage] = useState(1);
  const { data, loading, reload } = useLoad<Payload>(
    `/admin/v1/reviews?state=${state}&page=${page}&page_size=20`,
    {
    items: [],
    total: 0,
    counts: { pending: 0, approved: 0, rejected: 0 },
    }
  );
  const [selected, setSelected] = useState<number[]>([]);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectIds, setRejectIds] = useState<number[]>([]);
  const [form] = Form.useForm();

  const refresh = async () => {
    setSelected([]);
    await reload();
  };

  const decide = async (ids: number[], decision: "approve" | "reject", note = "") => {
    try {
      const result = await api<{ done: number; skipped: { id: number; reason: string }[] }>(
        "/admin/v1/reviews/batch",
        {
          method: "POST",
          body: JSON.stringify({ drama_ids: ids, decision, note }),
        }
      );
      if (result.skipped.length) {
        Modal.info({
          title: `已处理 ${result.done} 部，跳过 ${result.skipped.length} 部`,
          content: (
            <ul style={{ paddingLeft: 18 }}>
              {result.skipped.map((item) => (
                <li key={item.id}>
                  剧集 {item.id}：{item.reason}
                </li>
              ))}
            </ul>
          ),
        });
      } else {
        message.success(decision === "approve" ? "已通过" : "已驳回");
      }
      await refresh();
    } catch (e) {
      message.error(errorText(e));
      throw e;
    }
  };

  const openReject = (ids: number[]) => {
    setRejectIds(ids);
    form.resetFields();
    setRejectOpen(true);
  };

  const publish = (row: ReviewRow) => {
    Modal.confirm({
      title: `上架《${row.title}》`,
      content: `${row.episode_count} 集将同时对用户可见，确认上架？`,
      okText: "上架",
      onOk: async () => {
        try {
          await api(`/admin/v1/reviews/${row.id}/publish`, { method: "POST" });
          message.success("已上架");
          await refresh();
        } catch (e) {
          message.error(errorText(e));
          throw e;
        }
      },
    });
  };

  const counts = data.counts;

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space wrap>
        <Typography.Text type="secondary">
          通过只代表内容合格；「待上架」里再点发布才会对用户可见。
        </Typography.Text>
        {state === "pending" && selected.length > 0 && (
          <>
            <span style={{ flex: 1 }} />
            <Button type="primary" onClick={() => decide(selected, "approve")}>
              批量通过（{selected.length}）
            </Button>
            <Button danger onClick={() => openReject(selected)}>
              批量驳回
            </Button>
          </>
        )}
      </Space>

      <Tabs
        activeKey={state}
        onChange={(key) => {
          setState(key);
          setPage(1);
          setSelected([]);
        }}
        items={TABS.map((tab) => ({
          key: tab.key,
          label: `${tab.label}（${counts[tab.key as keyof typeof counts] ?? 0}）`,
        }))}
      />

      <Table<ReviewRow>
        rowKey="id"
        loading={loading}
        dataSource={data.items}
        pagination={{
          current: page,
          pageSize: 20,
          total: data.total,
          showSizeChanger: false,
          onChange: (next) => setPage(next),
        }}
        scroll={{ x: 980 }}
        rowSelection={
          state === "pending"
            ? {
                selectedRowKeys: selected,
                onChange: (keys) => setSelected(keys as number[]),
              }
            : undefined
        }
        columns={[
          {
            title: "剧集",
            dataIndex: "title",
            width: 240,
            render: (value: string, row) => (
              <div>
                <Link to={`/dramas/${row.id}`}>{value}</Link>
                {row.review_note && (
                  <div style={{ fontSize: 12, color: "#A32D2D" }}>驳回：{row.review_note}</div>
                )}
              </div>
            ),
          },
          {
            title: "供应商",
            key: "supplier",
            width: 160,
            render: (_, row) => row.supplier?.name ?? "-",
          },
          { title: "集数", dataIndex: "episode_count", width: 70 },
          { title: "免费", dataIndex: "free_episode_count", width: 70 },
          {
            title: "提交时间",
            dataIndex: "submitted_at",
            width: 150,
            render: (value: string | null) => formatTime(value),
          },
          {
            title: "状态",
            key: "state",
            width: 100,
            render: (_, row) =>
              state === "pending" ? (
                <Tag color="orange">待审核</Tag>
              ) : state === "approved" ? (
                <Tag color="blue">待上架</Tag>
              ) : (
                <Tag color="red">已驳回</Tag>
              ),
          },
          {
            title: "操作",
            key: "actions",
            width: 190,
            render: (_, row) =>
              state === "pending" ? (
                <Space size={4}>
                  <Button type="link" size="small" onClick={() => decide([row.id], "approve")}>
                    通过
                  </Button>
                  <Button
                    type="link"
                    size="small"
                    danger
                    onClick={() => openReject([row.id])}
                  >
                    驳回
                  </Button>
                  <Link to={`/dramas/${row.id}`}>查看</Link>
                </Space>
              ) : state === "approved" ? (
                <Space size={4}>
                  <Button type="link" size="small" onClick={() => publish(row)}>
                    上架
                  </Button>
                  <Link to={`/dramas/${row.id}`}>查看</Link>
                </Space>
              ) : (
                <Link to={`/dramas/${row.id}`}>查看</Link>
              ),
          },
        ]}
      />

      <Modal
        title={rejectIds.length > 1 ? `批量驳回 ${rejectIds.length} 部` : "驳回剧集"}
        open={rejectOpen}
        onCancel={() => setRejectOpen(false)}
        onOk={() => form.submit()}
        okText="确认驳回"
        okButtonProps={{ danger: true }}
        forceRender
      >
        <Form
          form={form}
          layout="vertical"
          preserve={false}
          onFinish={async (values) => {
            try {
              await decide(rejectIds, "reject", values.note);
              setRejectOpen(false);
            } catch {
              /* message already shown */
            }
          }}
        >
          <Form.Item
            label="驳回原因"
            name="note"
            rules={[{ required: true, message: "请写清楚缺什么、怎么改" }]}
            extra={rejectIds.length > 1 ? "这段原因会同时写入每一部剧" : undefined}
          >
            <Input.TextArea rows={4} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
