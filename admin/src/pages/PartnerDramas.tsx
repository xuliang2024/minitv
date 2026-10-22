import { Space, Table, Tag, Typography } from "antd";
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
  const { data, loading } = useLoad<{ items: PartnerDrama[] }>("/partner/v1/dramas", {
    items: [],
  });

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Typography.Text type="secondary">
        这里只有你自己的剧集。审核通过后由 MiniTV 运营安排上架；上传与提交功能将在下一阶段开放。
      </Typography.Text>
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
                <div style={{ fontWeight: 500 }}>{value}</div>
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
        ]}
      />
    </Space>
  );
}
