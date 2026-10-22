import { useState } from "react";
import { Button, Input, Modal, Space, Table, Tag, Typography, message } from "antd";
import { api, errorText } from "../api";
import { formatTime, useLoad } from "../hooks";

type Row = {
  id: number;
  supplier: { id: number; name: string } | null;
  month: string;
  currency: string;
  gross_cents: number;
  revenue_cents: number;
  effective_share_bps: number;
  order_count: number;
  status: string;
  note: string;
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

export default function Settlements() {
  const current = new Date().toISOString().slice(0, 7);
  const [month, setMonth] = useState(current);
  const [query, setQuery] = useState(current);
  const { data, loading, reload } = useLoad<{ items: Row[]; total: number }>(
    `/admin/v1/settlements?month=${query}`,
    { items: [], total: 0 }
  );

  const generate = async () => {
    try {
      const result = await api<{ created: number; updated: number; skipped: number }>(
        "/admin/v1/settlements/generate",
        { method: "POST", body: JSON.stringify({ month: query }) }
      );
      message.success(
        `生成完成：新建 ${result.created} · 更新 ${result.updated} · 跳过 ${result.skipped}`
      );
      await reload();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const change = (row: Row, status: "confirmed" | "paid") => {
    Modal.confirm({
      title: status === "confirmed" ? "确认对账单" : "标记已打款",
      content:
        status === "confirmed"
          ? `确认 ${row.supplier?.name ?? ""} ${row.month} 的金额 ${money(row.revenue_cents, row.currency)}？确认后重新生成不会覆盖。`
          : `记录 ${row.month} 的 ${money(row.revenue_cents, row.currency)} 已打款？`,
      okText: status === "confirmed" ? "确认" : "已打款",
      onOk: async () => {
        try {
          await api(`/admin/v1/settlements/${row.id}/status`, {
            method: "POST",
            body: JSON.stringify({ status }),
          });
          message.success("已更新");
          await reload();
        } catch (e) {
          message.error(errorText(e));
          throw e;
        }
      },
    });
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space wrap>
        <Input
          type="month"
          value={month}
          onChange={(event) => setMonth(event.target.value)}
          style={{ width: 160 }}
        />
        <Button onClick={() => setQuery(month)}>查询</Button>
        <Button type="primary" onClick={generate}>
          生成 / 更新该月对账单
        </Button>
        <Typography.Text type="secondary">
          只统计每个用户的首单；已确认或已打款的月份不会被覆盖。
        </Typography.Text>
      </Space>

      <Table<Row>
        rowKey="id"
        loading={loading}
        dataSource={data.items}
        pagination={false}
        scroll={{ x: 940 }}
        columns={[
          { title: "供应商", key: "supplier", width: 160, render: (_, row) => row.supplier?.name ?? "-" },
          { title: "月份", dataIndex: "month", width: 100 },
          {
            title: "首单金额",
            dataIndex: "gross_cents",
            width: 120,
            render: (value: number, row) => money(value, row.currency),
          },
          {
            title: "分成",
            dataIndex: "effective_share_bps",
            width: 90,
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
            width: 180,
            render: (_, row) => (
              <span style={{ fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
                {formatTime(row.confirmed_at)} / {formatTime(row.paid_at)}
              </span>
            ),
          },
          {
            title: "操作",
            key: "actions",
            width: 150,
            render: (_, row) => (
              <Space size={4}>
                {row.status === "draft" && (
                  <Button type="link" size="small" onClick={() => change(row, "confirmed")}>
                    确认
                  </Button>
                )}
                {row.status === "confirmed" && (
                  <Button type="link" size="small" onClick={() => change(row, "paid")}>
                    标记打款
                  </Button>
                )}
              </Space>
            ),
          },
        ]}
      />
    </Space>
  );
}
