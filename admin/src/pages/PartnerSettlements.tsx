import { Space, Table, Tag, Typography } from "antd";
import { formatTime, useLoad } from "../hooks";

type SettlementRow = {
  id: number;
  month: string;
  currency: string;
  gross_cents: number;
  revenue_cents: number;
  effective_share_bps: number;
  order_count: number;
  status: string;
  confirmed_at: string | null;
  paid_at: string | null;
};

const STATUS: Record<string, { text: string; color?: string }> = {
  draft: { text: "待确认", color: "orange" },
  confirmed: { text: "已确认", color: "blue" },
  paid: { text: "已打款", color: "green" },
};

const money = (cents: number, currency = "USD") =>
  `${currency === "USD" ? "$" : ""}${(cents / 100).toFixed(2)}`;

export default function PartnerSettlements() {
  const { data, loading } = useLoad<{ items: SettlementRow[] }>("/partner/v1/settlements", {
    items: [],
  });

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Typography.Text type="secondary">
        自然月结算，次月出对账单；分成按每笔订单支付时生效的比例计算。
      </Typography.Text>
      <Table<SettlementRow>
        rowKey="id"
        loading={loading}
        dataSource={data.items}
        pagination={false}
        columns={[
          { title: "月份", dataIndex: "month", width: 110 },
          {
            title: "首单金额",
            dataIndex: "gross_cents",
            width: 120,
            render: (value: number, row) => money(value, row.currency),
          },
          {
            title: "分成比例",
            dataIndex: "effective_share_bps",
            width: 100,
            render: (value: number) => `${(value / 100).toFixed(2)}%`,
          },
          {
            title: "应结金额",
            dataIndex: "revenue_cents",
            width: 120,
            render: (value: number, row) => (
              <Typography.Text strong>{money(value, row.currency)}</Typography.Text>
            ),
          },
          { title: "订单数", dataIndex: "order_count", width: 80 },
          {
            title: "状态",
            dataIndex: "status",
            width: 100,
            render: (value: string) => {
              const meta = STATUS[value] ?? { text: value };
              return <Tag color={meta.color}>{meta.text}</Tag>;
            },
          },
          {
            title: "确认 / 打款",
            key: "dates",
            width: 200,
            render: (_, row) => (
              <span style={{ fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
                {formatTime(row.confirmed_at)} / {formatTime(row.paid_at)}
              </span>
            ),
          },
        ]}
      />
    </Space>
  );
}
