import { useState } from "react";
import { Card, Col, Row, Space, Table, Tag, Typography } from "antd";
import { useLoad } from "../hooks";

type Overview = {
  days: number;
  plays: number;
  completes: number;
  completion_rate: number;
  viewers: number;
  unlocks: number;
  unlock_users: number;
  first_orders: number;
  gross_cents: number;
  revenue_cents: number;
  effective_share_bps: number;
  currency: string;
};

type DramaRow = {
  id: number;
  title: string;
  status: string;
  plays: number;
  completion_rate: number;
  viewers: number;
  unlocks: number;
  first_orders: number;
  gross_cents: number;
  revenue_cents: number;
};

const money = (cents: number, currency = "USD") =>
  `${currency === "USD" ? "$" : ""}${(cents / 100).toFixed(2)}`;
const percent = (value: number) => `${(value * 100).toFixed(1)}%`;

export default function PartnerStats() {
  const [days, setDays] = useState(30);
  const { data: overview, loading } = useLoad<Overview>(
    `/partner/v1/stats/overview?days=${days}`,
    {
      days,
      plays: 0,
      completes: 0,
      completion_rate: 0,
      viewers: 0,
      unlocks: 0,
      unlock_users: 0,
      first_orders: 0,
      gross_cents: 0,
      revenue_cents: 0,
      effective_share_bps: 0,
      currency: "USD",
    }
  );
  const { data: dramas } = useLoad<{ items: DramaRow[] }>(
    `/partner/v1/stats/dramas?days=${days}`,
    { items: [] }
  );

  const metrics = [
    { label: "播放量", value: overview.plays, note: `${overview.completes} 次完播` },
    { label: "完播率", value: percent(overview.completion_rate), note: "完播 / 播放" },
    { label: "观看用户", value: overview.viewers, note: "按用户去重" },
    {
      label: "金币解锁",
      value: overview.unlocks,
      note: `${overview.unlock_users} 人 · 仅内容指标`,
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Space wrap>
        <Typography.Title level={4} style={{ margin: 0 }}>
          数据中心
        </Typography.Title>
        {[7, 30, 90].map((value) => (
          <Tag
            key={value}
            color={days === value ? "red" : undefined}
            style={{ cursor: "pointer", padding: "2px 10px" }}
            onClick={() => setDays(value)}
          >
            近 {value} 天
          </Tag>
        ))}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          金币解锁只反映内容热度，不参与分成
        </Typography.Text>
      </Space>

      <Row gutter={10}>
        {metrics.map((item) => (
          <Col xs={12} lg={6} key={item.label}>
            <Card size="small" loading={loading}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {item.label}
              </Typography.Text>
              <Typography.Title level={3} style={{ margin: "4px 0 0" }}>
                {item.value}
              </Typography.Title>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {item.note}
              </Typography.Text>
            </Card>
          </Col>
        ))}
      </Row>

      <Row gutter={10}>
        <Col xs={24} lg={8}>
          <Card size="small">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              会员首单
            </Typography.Text>
            <Typography.Title level={3} style={{ margin: "4px 0 0" }}>
              {overview.first_orders}
            </Typography.Title>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              只算每个用户的首单
            </Typography.Text>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card size="small">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              首单金额
            </Typography.Text>
            <Typography.Title level={3} style={{ margin: "4px 0 0" }}>
              {money(overview.gross_cents, overview.currency)}
            </Typography.Title>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              不含续费与复购
            </Typography.Text>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card size="small">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              预估收益 · {(overview.effective_share_bps / 100).toFixed(0)}%
            </Typography.Text>
            <Typography.Title level={3} style={{ margin: "4px 0 0" }}>
              {money(overview.revenue_cents, overview.currency)}
            </Typography.Title>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              月结 · 次月出对账单
            </Typography.Text>
          </Card>
        </Col>
      </Row>

      <Card size="small" title="分剧数据">
        <Table<DramaRow>
          rowKey="id"
          size="small"
          dataSource={dramas.items}
          pagination={false}
          scroll={{ x: 820 }}
          columns={[
            { title: "剧集", dataIndex: "title" },
            { title: "播放", dataIndex: "plays", width: 80 },
            {
              title: "完播率",
              dataIndex: "completion_rate",
              width: 90,
              render: (value: number) => percent(value),
            },
            { title: "观看用户", dataIndex: "viewers", width: 90 },
            { title: "解锁", dataIndex: "unlocks", width: 70 },
            { title: "首单", dataIndex: "first_orders", width: 70 },
            {
              title: "首单金额",
              dataIndex: "gross_cents",
              width: 110,
              render: (value: number) => money(value),
            },
            {
              title: "预估收益",
              dataIndex: "revenue_cents",
              width: 110,
              render: (value: number) => money(value),
            },
          ]}
        />
      </Card>

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        口径：首单归因（续费不计）· 退款冲减 · 币种 USD · 明细保留 90 天
      </Typography.Text>
    </Space>
  );
}
