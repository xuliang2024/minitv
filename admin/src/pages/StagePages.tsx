import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Alert,
  Avatar,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Row,
  Select,
  Skeleton,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  AppstoreFilled,
  CalendarOutlined,
  CheckCircleFilled,
  CheckCircleOutlined,
  DollarOutlined,
  EditOutlined,
  InfoCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RiseOutlined,
  ShoppingCartOutlined,
  ThunderboltOutlined,
  TrophyFilled,
  UserAddOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { ApiError, api, errorText } from "../api";
import { useConfirm } from "../confirm";
import { useIsAdmin } from "../me";

function useLoad<T>(path: string, fallback: T) {
  const [data, setData] = useState<T>(fallback);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reload = () => {
    setLoading(true);
    return api<T>(path)
      .then((value) => {
        setData(value);
        setError(null);
      })
      .catch((e) => {
        const text = errorText(e);
        setError(text);
        message.error(text);
      })
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);
  return { data, reload, loading, error };
}

/** ISO-8601 from the API -> local "YYYY-MM-DD HH:mm" for table cells. */
function formatTime(value?: string | null) {
  if (!value) return "-";
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())} ${pad(moment.getHours())}:${pad(moment.getMinutes())}`;
}

type DashboardMetric = {
  day: string;
  active_users: number;
  new_users: number;
  plays: number;
  completes: number;
  paid_orders: number;
  revenue_cents: number;
  coin_spend: number;
  unlock_count: number;
  completion_rate: number;
};

type Dashboard = Partial<DashboardMetric> & {
  timezone?: string;
  window?: { start: string; end: string; days: number };
  trend?: DashboardMetric[];
  previous?: Partial<DashboardMetric>;
  top_dramas?: { id: number; title: string; plays: number; completes: number; completion_rate: number }[];
  overview?: Record<string, number>;
  recent_orders?: { order_no: string; user_id: number; amount_cents: number; currency: string; status: string; created_at: string }[];
};

const money = (cents?: number) => `$${((cents ?? 0) / 100).toFixed(2)}`;
/** completion_rate is stored in basis points of the day's plays. */
const rate = (basisPoints?: number) => `${((basisPoints ?? 0) / 100).toFixed(1)}%`;
const int = (value?: number) => String(value ?? 0);

function DeltaBadge({ value, render }: { value: number; render?: (value: number) => string }) {
  if (value === 0) {
    return (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          fontSize: 12,
          color: "#98a2b3",
          background: "#f2f4f7",
          padding: "2px 8px",
          borderRadius: 12,
          fontWeight: 500,
        }}
      >
        环比持平 · 0
      </span>
    );
  }
  const isUp = value > 0;
  const text = render ? render(Math.abs(value)) : int(Math.abs(value));
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 3,
        fontSize: 12,
        color: isUp ? "#027a48" : "#b42318",
        background: isUp ? "#edfcf2" : "#fef3f2",
        padding: "2px 8px",
        borderRadius: 12,
        fontWeight: 600,
      }}
    >
      环比 {isUp ? "↑" : "↓"} {text}
    </span>
  );
}

/** 现代仪表板柱状图：限宽、轨道底色与数值提示 */
function MiniBars({
  rows,
  pick,
  color,
  format,
}: {
  rows: DashboardMetric[];
  pick: (row: DashboardMetric) => number;
  color: string;
  format: (value: number) => string;
}) {
  const values = rows.map(pick);
  const max = Math.max(1, ...values);
  return (
    <div style={{ width: "100%", padding: "4px 0" }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 10, height: 116 }}>
        {rows.map((row, index) => {
          const val = values[index];
          const heightPercent = val > 0 ? (val / max) * 100 : 0;
          return (
            <Tooltip key={row.day} title={`${row.day} · ${format(val)}`}>
              <div
                style={{
                  flex: 1,
                  height: "100%",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "flex-end",
                  alignItems: "center",
                  cursor: "pointer",
                }}
              >
                {/* 柱顶数值 */}
                <div
                  style={{
                    fontSize: 11,
                    color: val > 0 ? "#475467" : "#d0d5dd",
                    fontWeight: 600,
                    marginBottom: 4,
                    fontVariantNumeric: "tabular-nums",
                    whiteSpace: "nowrap",
                    transform: "scale(0.9)",
                  }}
                >
                  {val > 0 ? format(val).replace(/ 次|\.00/g, "") : ""}
                </div>
                {/* 柱条轨道 */}
                <div
                  style={{
                    width: "100%",
                    maxWidth: 32,
                    height: "80%",
                    background: "#f2f4f7",
                    borderRadius: "6px 6px 0 0",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "flex-end",
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      width: "100%",
                      height: `${heightPercent}%`,
                      minHeight: val > 0 ? 4 : 0,
                      background: val > 0 ? color : "transparent",
                      borderRadius: "6px 6px 0 0",
                      transition: "height 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
                    }}
                  />
                </div>
              </div>
            </Tooltip>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 10, marginTop: 8, borderTop: "1px solid #eaecf0", paddingTop: 8 }}>
        {rows.map((row) => (
          <div
            key={row.day}
            style={{
              flex: 1,
              fontSize: 11,
              color: "#98a2b3",
              textAlign: "center",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {row.day.slice(5)}
          </div>
        ))}
      </div>
    </div>
  );
}

function KpiCard({
  title,
  value,
  hint,
  icon,
  color,
  bg,
}: {
  title: string;
  value: ReactNode;
  hint: ReactNode;
  icon: ReactNode;
  color: string;
  bg: string;
}) {
  return (
    <Card
      bordered
      styles={{ body: { padding: "16px 18px" } }}
      style={{
        borderRadius: 12,
        borderColor: "#eaecf0",
        boxShadow: "0 1px 3px rgba(16, 24, 40, 0.04)",
        background: "#fff",
        height: "100%",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <span style={{ fontSize: 13, color: "#667085", fontWeight: 500 }}>{title}</span>
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: 8,
            background: bg,
            color: color,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 15,
            flexShrink: 0,
          }}
        >
          {icon}
        </div>
      </div>
      <div style={{ margin: "8px 0 6px" }}>
        <div style={{ fontSize: 24, fontWeight: 700, color: "#101828", lineHeight: 1.2, fontVariantNumeric: "tabular-nums" }}>
          {value}
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center" }}>{hint}</div>
    </Card>
  );
}

export function DashboardPage() {
  const [day, setDay] = useState("");
  const [days, setDays] = useState(7);
  const query = `/admin/v1/dashboard?days=${days}${day ? `&day=${day}` : ""}`;
  const { data, reload, loading, error } = useLoad<Dashboard>(query, {});
  const trend = data.trend ?? [];
  const previous = data.previous ?? {};
  const overview = data.overview ?? {};
  const delta = (current: number | undefined, before: number | undefined, render?: (value: number) => string) => (
    <DeltaBadge value={(current ?? 0) - (before ?? 0)} render={render} />
  );

  const kpis: {
    title: string;
    value: ReactNode;
    hint: ReactNode;
    icon: ReactNode;
    color: string;
    bg: string;
  }[] = [
    {
      title: "活跃用户（登录去重）",
      value: int(data.active_users),
      hint: delta(data.active_users, previous.active_users),
      icon: <UserOutlined />,
      color: "#1677ff",
      bg: "#eff8ff",
    },
    {
      title: "新增用户",
      value: int(data.new_users),
      hint: delta(data.new_users, previous.new_users),
      icon: <UserAddOutlined />,
      color: "#0ea5e9",
      bg: "#f0f9ff",
    },
    {
      title: "播放次数",
      value: int(data.plays),
      hint: delta(data.plays, previous.plays),
      icon: <PlayCircleOutlined />,
      color: "#e53b3e",
      bg: "#fff1f0",
    },
    {
      title: "完播次数",
      value: int(data.completes),
      hint: delta(data.completes, previous.completes),
      icon: <CheckCircleOutlined />,
      color: "#8b5cf6",
      bg: "#f5f3ff",
    },
    {
      title: "完播率",
      value: rate(data.completion_rate),
      hint: delta(data.completion_rate, previous.completion_rate, (val) => `${(val / 100).toFixed(1)}pp`),
      icon: <RiseOutlined />,
      color: "#12b76a",
      bg: "#edfcf2",
    },
    {
      title: "营收",
      value: money(data.revenue_cents),
      hint: delta(data.revenue_cents, previous.revenue_cents, money),
      icon: <DollarOutlined />,
      color: "#d97706",
      bg: "#fffbeb",
    },
    {
      title: "付费订单",
      value: int(data.paid_orders),
      hint: delta(data.paid_orders, previous.paid_orders),
      icon: <ShoppingCartOutlined />,
      color: "#f97316",
      bg: "#fff7ed",
    },
    {
      title: "金币消耗",
      value: int(data.coin_spend),
      hint: delta(data.coin_spend, previous.coin_spend),
      icon: <ThunderboltOutlined />,
      color: "#f59e0b",
      bg: "#fefce8",
    },
  ];

  const overviewItems: { title: string; value: string; isPending?: boolean; isHighlight?: boolean }[] = [
    { title: "总用户", value: int(overview.total_users) },
    { title: "有效会员", value: int(overview.active_members), isHighlight: true },
    { title: "已发布剧集", value: int(overview.published_dramas) },
    { title: "已发布集数", value: int(overview.published_episodes) },
    { title: "待发布集数", value: int(overview.episodes_awaiting_publish), isPending: true },
    { title: "待审评论", value: int(overview.pending_comments), isPending: true },
    { title: "累计播放", value: int(overview.lifetime_plays), isHighlight: true },
    { title: "累计营收", value: money(overview.lifetime_revenue_cents), isHighlight: true },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {/* 顶部标题与时区控件 */}
      <Space style={{ width: "100%", justifyContent: "space-between" }} wrap align="center">
        <Space align="center" size={10} wrap>
          <Typography.Title level={4} style={{ margin: 0 }}>
            数据看板
          </Typography.Title>
          <Tag bordered={false} style={{ background: "#eff8ff", color: "#175cd3", borderRadius: 12, padding: "2px 10px", margin: 0 }}>
            {data.timezone ?? "America/Los_Angeles"} (美西时区)
          </Tag>
          <Tag bordered={false} style={{ background: "#f2f4f7", color: "#475467", borderRadius: 12, margin: 0 }}>
            指标日：{data.day ?? "-"}
          </Tag>
        </Space>
        <Space wrap size={10}>
          <div style={{ display: "inline-flex", alignItems: "center", position: "relative" }}>
            <CalendarOutlined style={{ position: "absolute", left: 10, color: "#98a2b3", zIndex: 1, pointerEvents: "none" }} />
            <Input
              type="date"
              value={day}
              onChange={(event) => setDay(event.target.value)}
              style={{ width: 160, paddingLeft: 30, borderRadius: 6 }}
            />
          </div>
          <Select
            value={days}
            onChange={setDays}
            style={{ width: 110 }}
            options={[
              { value: 7, label: "近 7 天" },
              { value: 14, label: "近 14 天" },
              { value: 30, label: "近 30 天" },
            ]}
          />
          <Button icon={<ReloadOutlined />} onClick={reload} loading={loading} style={{ borderRadius: 6 }}>
            刷新
          </Button>
        </Space>
      </Space>

      {/* 业务规则轻提示 */}
      <Alert
        type="info"
        showIcon
        message={
          <span style={{ fontSize: 13, color: "#475467" }}>
            业务日统一按洛杉矶时区切分；指标全部来自线上真实事件打点（播放 / 支付订单 / 金币流水），环比自动对比前一天。
          </span>
        }
        style={{
          borderRadius: 8,
          border: "1px solid #d0d5dd",
          background: "#fafbfd",
        }}
      />

      {error ? <Alert type="error" showIcon message="看板加载失败" description={error} /> : null}

      {loading && trend.length === 0 ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : (
        <>
          {/* 8 个核心 KPI 卡片 */}
          <Row gutter={[16, 16]}>
            {kpis.map((item) => (
              <Col key={item.title} xs={12} sm={12} md={6} xl={3}>
                <KpiCard
                  title={item.title}
                  value={item.value}
                  hint={item.hint}
                  icon={item.icon}
                  color={item.color}
                  bg={item.bg}
                />
              </Col>
            ))}
          </Row>

          {/* 全站累计与待办面板 */}
          <Card
            bordered
            title={
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontWeight: 600, fontSize: 15 }}>全站累计与待办事项</span>
                <Tag bordered={false} style={{ background: "#f2f4f7", color: "#667085", borderRadius: 10, fontSize: 11, margin: 0 }}>
                  业务概览
                </Tag>
              </div>
            }
            styles={{ body: { padding: "16px 20px" }, header: { borderBottom: "1px solid #eaecf0", padding: "12px 20px" } }}
            style={{
              borderRadius: 12,
              borderColor: "#eaecf0",
              boxShadow: "0 1px 3px rgba(16, 24, 40, 0.04)",
              background: "#fff",
            }}
          >
            <Row gutter={[12, 12]}>
              {overviewItems.map((item) => {
                const isAlert = item.isPending && Number(item.value) > 0;
                return (
                  <Col key={item.title} xs={12} sm={8} md={6} xl={3}>
                    <div
                      style={{
                        padding: "10px 14px",
                        borderRadius: 8,
                        background: isAlert ? "#fff9f0" : "#f8f9fa",
                        border: isAlert ? "1px solid #fedf89" : "1px solid #f2f4f7",
                        transition: "all 0.15s ease",
                      }}
                    >
                      <div style={{ fontSize: 12, color: "#667085", marginBottom: 4 }}>{item.title}</div>
                      <div
                        style={{
                          fontSize: 20,
                          fontWeight: 700,
                          color: item.isHighlight ? "#1677ff" : isAlert ? "#d97706" : "#101828",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {item.value}
                      </div>
                    </div>
                  </Col>
                );
              })}
            </Row>
          </Card>

          {/* 柱状趋势图双栏 */}
          <Row gutter={[16, 16]}>
            <Col xs={24} md={12}>
              <Card
                bordered
                title={
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: 15 }}>播放次数趋势</span>
                    <Tag bordered={false} style={{ background: "#f2f4f7", color: "#667085", borderRadius: 10, fontSize: 11, margin: 0 }}>
                      {data.window?.start ?? "-"} ~ {data.window?.end ?? "-"}
                    </Tag>
                  </div>
                }
                styles={{ body: { padding: "16px 20px" }, header: { borderBottom: "1px solid #eaecf0", padding: "12px 20px" } }}
                style={{
                  borderRadius: 12,
                  borderColor: "#eaecf0",
                  boxShadow: "0 1px 3px rgba(16, 24, 40, 0.04)",
                  background: "#fff",
                }}
              >
                <MiniBars rows={trend} pick={(row) => row.plays} color="#E53B3E" format={(val) => `${val} 次`} />
              </Card>
            </Col>
            <Col xs={24} md={12}>
              <Card
                bordered
                title={
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: 15 }}>营收趋势</span>
                    <Tag bordered={false} style={{ background: "#edfcf2", color: "#027a48", borderRadius: 10, fontSize: 11, margin: 0 }}>
                      支付结算
                    </Tag>
                  </div>
                }
                styles={{ body: { padding: "16px 20px" }, header: { borderBottom: "1px solid #eaecf0", padding: "12px 20px" } }}
                style={{
                  borderRadius: 12,
                  borderColor: "#eaecf0",
                  boxShadow: "0 1px 3px rgba(16, 24, 40, 0.04)",
                  background: "#fff",
                }}
              >
                <MiniBars rows={trend} pick={(row) => row.revenue_cents} color="#12b76a" format={money} />
              </Card>
            </Col>
          </Row>

          {/* 趋势明细表格 */}
          <Card
            bordered
            title={<span style={{ fontWeight: 600, fontSize: 15 }}>趋势明细</span>}
            styles={{ body: { padding: 0 }, header: { borderBottom: "1px solid #eaecf0", padding: "12px 20px" } }}
            style={{
              borderRadius: 12,
              borderColor: "#eaecf0",
              boxShadow: "0 1px 3px rgba(16, 24, 40, 0.04)",
              overflow: "hidden",
              background: "#fff",
            }}
          >
            <Table
              rowKey="day"
              loading={loading}
              dataSource={trend}
              pagination={false}
              scroll={{ x: 920 }}
              columns={[
                {
                  title: "日期",
                  dataIndex: "day",
                  fixed: "left",
                  width: 120,
                  render: (v: string) => <span style={{ fontWeight: 600, color: "#101828" }}>{v}</span>,
                },
                {
                  title: "新增用户",
                  dataIndex: "new_users",
                  render: (v: number) => <span style={{ fontVariantNumeric: "tabular-nums" }}>{v}</span>,
                },
                {
                  title: "活跃用户",
                  dataIndex: "active_users",
                  render: (v: number) => <span style={{ fontVariantNumeric: "tabular-nums" }}>{v}</span>,
                },
                {
                  title: "播放",
                  dataIndex: "plays",
                  render: (v: number) => <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 500 }}>{v}</span>,
                },
                {
                  title: "完播",
                  dataIndex: "completes",
                  render: (v: number) => <span style={{ fontVariantNumeric: "tabular-nums" }}>{v}</span>,
                },
                {
                  title: "完播率",
                  dataIndex: "completion_rate",
                  render: (v: number) => (
                    <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 600, color: v > 0 ? "#12b76a" : "#667085" }}>
                      {rate(v)}
                    </span>
                  ),
                },
                {
                  title: "付费订单",
                  dataIndex: "paid_orders",
                  render: (v: number) => <span style={{ fontVariantNumeric: "tabular-nums" }}>{v}</span>,
                },
                {
                  title: "营收",
                  dataIndex: "revenue_cents",
                  render: (v: number) => (
                    <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 600, color: v > 0 ? "#027a48" : "#667085" }}>
                      {money(v)}
                    </span>
                  ),
                },
                {
                  title: "金币消耗",
                  dataIndex: "coin_spend",
                  render: (v: number) => <span style={{ fontVariantNumeric: "tabular-nums" }}>{v}</span>,
                },
                {
                  title: "解锁次数",
                  dataIndex: "unlock_count",
                  render: (v: number) => <span style={{ fontVariantNumeric: "tabular-nums" }}>{v}</span>,
                },
              ]}
            />
          </Card>

          {/* 热门剧集与最近订单双栏 */}
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={12}>
              <Card
                bordered
                title={
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: 15 }}>热门剧集</span>
                    <Tag bordered={false} style={{ background: "#f2f4f7", color: "#667085", borderRadius: 10, fontSize: 11, margin: 0 }}>
                      窗口内真实播放
                    </Tag>
                  </div>
                }
                styles={{ body: { padding: 0 }, header: { borderBottom: "1px solid #eaecf0", padding: "12px 20px" } }}
                style={{
                  borderRadius: 12,
                  borderColor: "#eaecf0",
                  boxShadow: "0 1px 3px rgba(16, 24, 40, 0.04)",
                  overflow: "hidden",
                  background: "#fff",
                }}
              >
                <Table
                  rowKey="id"
                  loading={loading}
                  dataSource={data.top_dramas ?? []}
                  pagination={false}
                  locale={{ emptyText: "窗口内暂无播放数据" }}
                  columns={[
                    {
                      title: "排行",
                      key: "rank",
                      width: 64,
                      render: (_, __, index) => {
                        const rankStyles = [
                          { bg: "#fff1f0", color: "#e53b3e" },
                          { bg: "#fff9f0", color: "#f79009" },
                          { bg: "#eff8ff", color: "#1677ff" },
                        ];
                        const style = rankStyles[index] ?? { bg: "#f2f4f7", color: "#667085" };
                        return (
                          <span
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              justifyContent: "center",
                              width: 22,
                              height: 22,
                              borderRadius: 6,
                              background: style.bg,
                              color: style.color,
                              fontSize: 12,
                              fontWeight: 700,
                            }}
                          >
                            {index + 1}
                          </span>
                        );
                      },
                    },
                    {
                      title: "剧集",
                      dataIndex: "title",
                      render: (title: string, row) => (
                        <Link to={`/dramas/${row.id}`} style={{ fontWeight: 600, color: "#101828" }}>
                          {title}
                        </Link>
                      ),
                    },
                    {
                      title: "播放",
                      dataIndex: "plays",
                      width: 80,
                      render: (v: number) => <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 500 }}>{v}</span>,
                    },
                    {
                      title: "完播",
                      dataIndex: "completes",
                      width: 80,
                      render: (v: number) => <span style={{ fontVariantNumeric: "tabular-nums" }}>{v}</span>,
                    },
                    {
                      title: "完播率",
                      dataIndex: "completion_rate",
                      width: 100,
                      render: (v: number) => (
                        <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 600, color: "#12b76a" }}>
                          {rate(v)}
                        </span>
                      ),
                    },
                  ]}
                />
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card
                bordered
                title={
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: 15 }}>最近订单</span>
                    <Tag bordered={false} style={{ background: "#edfcf2", color: "#027a48", borderRadius: 10, fontSize: 11, margin: 0 }}>
                      实时成交
                    </Tag>
                  </div>
                }
                styles={{ body: { padding: 0 }, header: { borderBottom: "1px solid #eaecf0", padding: "12px 20px" } }}
                style={{
                  borderRadius: 12,
                  borderColor: "#eaecf0",
                  boxShadow: "0 1px 3px rgba(16, 24, 40, 0.04)",
                  overflow: "hidden",
                  background: "#fff",
                }}
              >
                <Table
                  rowKey="order_no"
                  loading={loading}
                  dataSource={data.recent_orders ?? []}
                  pagination={false}
                  scroll={{ x: 560 }}
                  locale={{ emptyText: "暂无订单数据" }}
                  columns={[
                    {
                      title: "订单号",
                      dataIndex: "order_no",
                      render: (v: string) => (
                        <span style={{ fontFamily: "monospace", fontSize: 12, color: "#475467" }}>{v}</span>
                      ),
                    },
                    {
                      title: "用户",
                      dataIndex: "user_id",
                      width: 90,
                      render: (v: number) => (
                        <span style={{ fontSize: 12, color: "#667085" }}>#{v}</span>
                      ),
                    },
                    {
                      title: "金额",
                      dataIndex: "amount_cents",
                      width: 100,
                      render: (v: number) => (
                        <span style={{ fontWeight: 700, color: "#027a48", fontVariantNumeric: "tabular-nums" }}>
                          {money(v)}
                        </span>
                      ),
                    },
                    {
                      title: "状态",
                      dataIndex: "status",
                      width: 90,
                      render: (value: string) => (
                        <Tag
                          bordered={false}
                          style={{
                            background: value === "paid" ? "#edfcf2" : "#f2f4f7",
                            color: value === "paid" ? "#027a48" : "#667085",
                            borderRadius: 4,
                            fontWeight: 500,
                            margin: 0,
                          }}
                        >
                          {value === "paid" ? "已支付" : value}
                        </Tag>
                      ),
                    },
                    {
                      title: "时间",
                      dataIndex: "created_at",
                      width: 150,
                      render: formatTime,
                    },
                  ]}
                />
              </Card>
            </Col>
          </Row>
        </>
      )}
    </Space>
  );
}

type AdminUserRow = {
  id: number;
  public_id?: string | null;
  display_name: string;
  email?: string | null;
  avatar_url?: string | null;
  gender?: string | null;
  bio?: string | null;
  region?: string | null;
  locale?: string | null;
  coins: number;
  member: boolean;
  member_until?: string | null;
  created_at: string;
};

const GENDER_OPTIONS = [
  { value: "unspecified", label: "未设置" },
  { value: "female", label: "女性" },
  { value: "male", label: "男性" },
  { value: "other", label: "其他" },
];

const LOCALE_OPTIONS = ["en", "zh", "ja", "ko", "es", "pt"].map((value) => ({ value, label: value }));

/** 头像 + 昵称 + 副标题（简介 / 邮箱）两行单元格。 */
function UserCell({ row }: { row: AdminUserRow }) {
  const initial = row.display_name?.trim()?.[0]?.toUpperCase() ?? "?";
  return (
    <Space size={10} align="center">
      <Avatar src={row.avatar_url || undefined} style={{ background: "#f0f0f0", color: "#8c8c8c", flex: "none" }}>
        {initial}
      </Avatar>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 600, lineHeight: "20px" }}>{row.display_name || "-"}</div>
        <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis={{ tooltip: row.bio || row.email || "" }}>
          {row.bio || row.email || "—"}
        </Typography.Text>
      </div>
    </Space>
  );
}

/** 行内「调账」弹窗：预选用户、方向切换、快捷金额、改后余额实时预览。 */
function CoinAdjustModal({ user, onClose, onSaved }: { user: AdminUserRow; onClose: () => void; onSaved: (text: string) => void }) {
  const [form] = Form.useForm<{ amount: number; reason: string }>();
  const [direction, setDirection] = useState<"add" | "sub">("add");
  const [saving, setSaving] = useState(false);
  const amount = Form.useWatch("amount", form);
  const delta = (typeof amount === "number" ? amount : 0) * (direction === "add" ? 1 : -1);
  const after = user.coins + delta;
  const negative = after < 0;

  const submit = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return; // antd 已经把出错字段标红了
    setSaving(true);
    try {
      const res = await api<{ before: number; delta: number; after: number }>(`/admin/v1/users/${user.id}/coins`, {
        method: "POST",
        body: JSON.stringify({ delta: direction === "add" ? values.amount : -values.amount, reason: values.reason.trim() }),
      });
      onSaved(`已入账：${res.before} → ${res.after} 金币`);
    } catch (e: any) {
      message.error(errorText(e));
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      title="调整金币"
      okText="确认调账"
      cancelText="取消"
      confirmLoading={saving}
      okButtonProps={{ disabled: negative }}
      onOk={submit}
      onCancel={onClose}
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Card size="small" style={{ background: "#fafafa" }}>
          <Space size={16} wrap>
            <span>
              <Typography.Text type="secondary">用户：</Typography.Text>
              <Typography.Text strong>{user.display_name}</Typography.Text>
            </span>
            <span>
              <Typography.Text type="secondary">ID：</Typography.Text>
              {user.public_id || user.id}
            </span>
            <span>
              <Typography.Text type="secondary">当前余额：</Typography.Text>
              <Typography.Text strong>{user.coins}</Typography.Text>
            </span>
          </Space>
        </Card>
        <Form
          form={form}
          layout="vertical"
          initialValues={{ amount: 100, reason: "" }}
          onFinish={submit}
        >
          <Form.Item label="调整方式">
            <Radio.Group value={direction} onChange={(e) => setDirection(e.target.value)} optionType="button" buttonStyle="solid">
              <Radio.Button value="add">增加金币</Radio.Button>
              <Radio.Button value="sub">扣减金币</Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Form.Item
            name="amount"
            label="数量"
            rules={[
              { required: true, message: "请输入数量" },
              { type: "number", min: 1, message: "数量需大于 0" },
            ]}
          >
            <InputNumber min={1} precision={0} style={{ width: 160 }} placeholder="金币数量" />
          </Form.Item>
          <Space size={8} style={{ marginTop: -8, marginBottom: 14 }}>
            <Typography.Text type="secondary">快捷：</Typography.Text>
            {[100, 200, 500].map((value) => (
              <Button key={value} size="small" onClick={() => form.setFieldValue("amount", value)}>
                {value}
              </Button>
            ))}
          </Space>
          <Form.Item name="reason" label="原因" rules={[{ required: true, whitespace: true, message: "请填写调账原因" }]}>
            <Input placeholder="如：活动补发 / 客诉补偿" maxLength={200} />
          </Form.Item>
        </Form>
        {negative ? (
          <Alert type="error" showIcon message={`扣减后余额为 ${after}，余额不能为负，请减少扣减数量。`} />
        ) : (
          <Alert type="info" showIcon message={`调整后余额：${user.coins} → ${after}`} />
        )}
      </Space>
    </Modal>
  );
}

/** 行内「编辑」弹窗：管理员修改昵称 / 邮箱 / 性别 / 地区 / 语言 / 头像 / 简介。 */
function UserEditModal({ user, onClose, onSaved }: { user: AdminUserRow; onClose: () => void; onSaved: (text: string) => void }) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return; // antd 已经把出错字段标红了
    setSaving(true);
    try {
      await api(`/admin/v1/users/${user.id}`, { method: "PATCH", body: JSON.stringify(values) });
      onSaved(`已更新「${values.display_name || user.display_name}」的资料`);
    } catch (e: any) {
      message.error(errorText(e));
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      title={`编辑用户 · ${user.display_name}`}
      okText="保存"
      cancelText="取消"
      width={560}
      confirmLoading={saving}
      onOk={submit}
      onCancel={onClose}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          display_name: user.display_name ?? "",
          email: user.email ?? "",
          gender: user.gender || "unspecified",
          region: user.region ?? "",
          locale: user.locale || "en",
          avatar_url: user.avatar_url ?? "",
          bio: user.bio ?? "",
        }}
      >
        <Row gutter={16}>
          <Col xs={24} sm={12}>
            <Form.Item name="display_name" label="昵称" rules={[{ required: true, whitespace: true, message: "昵称不能为空", max: 64 }]}>
              <Input maxLength={64} placeholder="用户昵称" />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
            <Form.Item
              name="email"
              label="邮箱"
              tooltip="留空表示解绑邮箱"
              rules={[{ type: "email", message: "邮箱格式不正确" }]}
            >
              <Input maxLength={320} placeholder="user@example.com" allowClear />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
            <Form.Item name="gender" label="性别">
              <Select options={GENDER_OPTIONS} />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
            <Form.Item name="region" label="地区" tooltip="两位国家 / 地区码，如 US、HK">
              <Input maxLength={8} placeholder="US" />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
            <Form.Item name="locale" label="语言">
              <Select options={LOCALE_OPTIONS} />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
            <Form.Item name="avatar_url" label="头像地址">
              <Input maxLength={500} placeholder="https://…" allowClear />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="bio" label="简介">
          <Input.TextArea rows={3} maxLength={500} showCount placeholder="用户自我介绍" />
        </Form.Item>
        <Typography.Text type="secondary">用户 ID / 金币 / 会员状态不能在此修改；金币请使用「调账」。</Typography.Text>
      </Form>
    </Modal>
  );
}

export function UsersPage() {
  const isAdmin = useIsAdmin();
  const [keyword, setKeyword] = useState("");
  const [query, setQuery] = useState("");
  const [coinTarget, setCoinTarget] = useState<AdminUserRow | null>(null);
  const [editTarget, setEditTarget] = useState<AdminUserRow | null>(null);
  const { data, reload, loading } = useLoad<{ items: AdminUserRow[] }>(
    query ? `/admin/v1/users?q=${encodeURIComponent(query)}` : "/admin/v1/users",
    { items: [] }
  );

  // 输入停顿 300ms 后再查，避免每敲一个字符都打一次接口。
  useEffect(() => {
    const timer = setTimeout(() => setQuery(keyword.trim()), 300);
    return () => clearTimeout(timer);
  }, [keyword]);

  const saved = (text: string) => {
    message.success(text);
    setCoinTarget(null);
    setEditTarget(null);
    reload();
  };

  const adminOnly = isAdmin ? "" : "仅管理员可操作";

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>用户管理</Typography.Title>
      <Card
        title={query ? `用户列表 · 搜索「${query}」` : "用户列表"}
        extra={
          <Space>
            <Input.Search
              allowClear
              placeholder="昵称 / ID / 邮箱"
              style={{ width: 240 }}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onSearch={(value) => setQuery(value.trim())}
            />
            <Button onClick={() => reload()} loading={loading}>刷新</Button>
          </Space>
        }
      >
        <Table
          rowKey="id"
          dataSource={data.items}
          loading={loading}
          size="middle"
          scroll={{ x: 900 }}
          locale={{ emptyText: query ? `没有匹配「${query}」的用户` : "暂无用户" }}
          pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (total) => `共 ${total} 位用户` }}
          columns={[
            { title: "用户", dataIndex: "display_name", width: 240, render: (_, row) => <UserCell row={row} /> },
            {
              title: "ID",
              dataIndex: "public_id",
              width: 130,
              render: (value: string | null, row) => (
                <Typography.Text copyable={{ text: value ?? String(row.id) }} style={{ fontFamily: "monospace" }}>
                  {value || row.id}
                </Typography.Text>
              ),
            },
            {
              title: "金币",
              dataIndex: "coins",
              width: 100,
              align: "right",
              sorter: (a: AdminUserRow, b: AdminUserRow) => a.coins - b.coins,
              render: (value: number) => <Typography.Text strong>{value}</Typography.Text>,
            },
            {
              title: "会员",
              dataIndex: "member",
              width: 200,
              render: (value: boolean, row) =>
                value ? (
                  <Space size={6}>
                    <Tag color="green" style={{ marginInlineEnd: 0 }}>有效</Tag>
                    {row.member_until ? (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        至 {formatTime(row.member_until)}
                      </Typography.Text>
                    ) : null}
                  </Space>
                ) : (
                  <Typography.Text type="secondary">无</Typography.Text>
                ),
            },
            { title: "注册时间", dataIndex: "created_at", width: 160, render: (v: string) => formatTime(v) },
            {
              title: "操作",
              key: "action",
              width: 140,
              fixed: "right",
              render: (_, row) => (
                <Space size={0}>
                  <Tooltip title={adminOnly}>
                    <Button type="link" size="small" disabled={!isAdmin} onClick={() => setCoinTarget(row)}>
                      调账
                    </Button>
                  </Tooltip>
                  <Tooltip title={adminOnly}>
                    <Button type="link" size="small" disabled={!isAdmin} onClick={() => setEditTarget(row)}>
                      编辑
                    </Button>
                  </Tooltip>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      {coinTarget ? <CoinAdjustModal user={coinTarget} onClose={() => setCoinTarget(null)} onSaved={saved} /> : null}
      {editTarget ? <UserEditModal user={editTarget} onClose={() => setEditTarget(null)} onSaved={saved} /> : null}
    </Space>
  );
}

export function CommentsPage() {
  const { data, reload } = useLoad<{ items: any[] }>("/admin/v1/comments", { items: [] });
  return (
    <Table rowKey="id" dataSource={data.items} pagination={false} scroll={{ x: 720 }} columns={[
      { title: "内容", dataIndex: "body" },
      { title: "剧集", dataIndex: "drama_id" },
      { title: "用户", dataIndex: "user_id" },
      { title: "状态", dataIndex: "status" },
      { title: "操作", render: (_, row) => (
        <Space>
          <Button size="small" onClick={async () => { await api(`/admin/v1/comments/${row.id}`, { method: "POST", body: JSON.stringify({ status: "approved" }) }); reload(); }}>通过</Button>
          <Button size="small" danger onClick={async () => { await api(`/admin/v1/comments/${row.id}`, { method: "POST", body: JSON.stringify({ status: "deleted" }) }); reload(); }}>删除</Button>
        </Space>
      ) },
    ]} />
  );
}

export function FinancePage() {
  const { data } = useLoad<{ items: any[] }>("/admin/v1/finance/orders", { items: [] });
  return <Table rowKey="order_no" dataSource={data.items} pagination={false} scroll={{ x: 720 }} columns={[{ title: "订单号", dataIndex: "order_no" }, { title: "用户", dataIndex: "user_id" }, { title: "金额（分）", dataIndex: "amount_cents" }, { title: "状态", dataIndex: "status" }, { title: "时间", dataIndex: "created_at", render: (v: string) => formatTime(v) }]} />;
}

type PlanItem = {
  code: string;
  name_en: string;
  name_zh: string;
  price_cents: number;
  currency: string;
  version: number;
  benefits?: { items?: string[]; best?: boolean };
};

const PLAN_PERIOD: Record<string, string> = {
  weekly: "7 天",
  monthly: "30 天",
  yearly: "365 天",
};

function centsToDollars(cents: number) {
  return (cents / 100).toFixed(2);
}

function dollarsToCents(value: number) {
  return Math.round(value * 100);
}

export function PlansPage() {
  const { data, reload, loading } = useLoad<{ items: PlanItem[] }>("/admin/v1/plans", { items: [] });
  const [editing, setEditing] = useState<PlanItem | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<{ price: number; benefits: string; best: boolean }>();

  const openEdit = (plan: PlanItem) => {
    setEditing(plan);
    form.setFieldsValue({
      price: Number(centsToDollars(plan.price_cents)),
      benefits: (plan.benefits?.items ?? []).join("\n"),
      best: Boolean(plan.benefits?.best),
    });
  };

  const closeEdit = () => {
    if (saving) return;
    setEditing(null);
  };

  const onSave = async (values: { price: number; benefits: string; best: boolean }) => {
    if (!editing) return;
    const priceCents = dollarsToCents(values.price);
    if (!Number.isFinite(priceCents) || priceCents <= 0) {
      message.error("价格必须大于 0");
      return;
    }
    const items = values.benefits
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    setSaving(true);
    try {
      const result = await api<{ version: number; price_cents: number }>(`/admin/v1/plans/${editing.code}/price`, {
        method: "POST",
        body: JSON.stringify({
          price_cents: priceCents,
          benefits: { items, best: values.best },
        }),
      });
      message.success(`已写入新版本 v${result.version} · $${centsToDollars(result.price_cents)}`);
      setEditing(null);
      reload();
    } catch (e) {
      message.error(`保存失败：${errorText(e)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={20}>
      {/* 页面顶部标题与说明 */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
        <Space direction="vertical" size={4}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            会员套餐
          </Typography.Title>
          <Typography.Text type="secondary">
            维护三档会员套餐的价格、有效期与权益内容。修改价格会写入新的版本快照，客户端与官网将同步生效。
          </Typography.Text>
        </Space>
        <Button icon={<ReloadOutlined />} onClick={() => reload()} loading={loading}>
          刷新
        </Button>
      </div>

      <Alert
        type="info"
        showIcon
        icon={<InfoCircleOutlined />}
        message="套餐版本机制说明"
        description="修改价格或权益会即时生成递增的版本号（例如 v2），已购历史订单按购买时的版本计价对账，不会追溯改动。"
        style={{ borderRadius: 8 }}
      />

      {/* 3 列并排卡片布局 */}
      <Row gutter={[20, 20]} align="stretch">
        {(data.items ?? []).map((plan) => {
          const items = plan.benefits?.items ?? [];
          const isBest = Boolean(plan.benefits?.best);
          const periodText = PLAN_PERIOD[plan.code] ?? plan.code;

          return (
            <Col xs={24} sm={24} md={8} key={plan.code} style={{ display: "flex" }}>
              <Card
                hoverable
                loading={loading && !data.items.length}
                style={{
                  width: "100%",
                  display: "flex",
                  flexDirection: "column",
                  borderRadius: 12,
                  border: isBest ? "1.5px solid #ff4d4f" : "1px solid #e8e8e8",
                  boxShadow: isBest ? "0 6px 16px rgba(255, 77, 79, 0.08)" : "0 2px 8px rgba(0, 0, 0, 0.04)",
                  overflow: "hidden",
                  position: "relative",
                  transition: "all 0.3s ease",
                }}
                styles={{
                  body: {
                    padding: "24px",
                    display: "flex",
                    flexDirection: "column",
                    flex: 1,
                  },
                }}
              >
                {/* 推荐角标 */}
                {isBest && (
                  <div
                    style={{
                      position: "absolute",
                      top: 0,
                      right: 0,
                      background: "linear-gradient(135deg, #ff4d4f, #f5222d)",
                      color: "#fff",
                      fontSize: 12,
                      fontWeight: 600,
                      padding: "4px 14px 4px 12px",
                      borderBottomLeftRadius: 10,
                      letterSpacing: "0.5px",
                      boxShadow: "0 2px 6px rgba(245, 34, 45, 0.3)",
                    }}
                  >
                    ★ BEST VALUE
                  </div>
                )}

                {/* 卡片头部：套餐名与时长 */}
                <div style={{ marginBottom: 16 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <Typography.Title level={4} style={{ margin: 0, fontSize: 18 }}>
                      {plan.name_zh || plan.name_en}
                    </Typography.Title>
                    <Tag color="default" style={{ margin: 0, borderRadius: 10, padding: "0 8px" }}>
                      {periodText}
                    </Tag>
                  </div>
                  <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                    标识码：{plan.code}
                  </Typography.Text>
                </div>

                {/* 价格展示区 */}
                <div
                  style={{
                    padding: "16px",
                    background: isBest ? "rgba(255, 77, 79, 0.03)" : "#fafafa",
                    borderRadius: 8,
                    marginBottom: 20,
                    display: "flex",
                    alignItems: "baseline",
                    justifyContent: "space-between",
                  }}
                >
                  <div>
                    <span style={{ fontSize: 28, fontWeight: 700, color: isBest ? "#f5222d" : "#1f1f1f" }}>
                      ${centsToDollars(plan.price_cents)}
                    </span>
                    <span style={{ fontSize: 13, color: "#8c8c8c", marginLeft: 4 }}>
                      / {periodText}
                    </span>
                  </div>
                  <Tag color="processing" style={{ borderRadius: 4, margin: 0 }}>
                    v{plan.version}
                  </Tag>
                </div>

                {/* 权益列表区 */}
                <div style={{ flex: 1, marginBottom: 24 }}>
                  <Typography.Text strong style={{ fontSize: 13, display: "block", marginBottom: 12, color: "#595959" }}>
                    包含权益：
                  </Typography.Text>
                  {items.length ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                      {items.map((item) => (
                        <div key={item} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <CheckCircleFilled style={{ color: isBest ? "#ff4d4f" : "#52c41a", fontSize: 14, flexShrink: 0 }} />
                          <Typography.Text style={{ fontSize: 13 }}>{item}</Typography.Text>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                      暂无配置权益
                    </Typography.Text>
                  )}
                </div>

                {/* 操作按钮区 */}
                <div style={{ paddingTop: 16, borderTop: "1px solid #f0f0f0" }}>
                  <Button
                    type={isBest ? "primary" : "default"}
                    danger={isBest}
                    icon={<EditOutlined />}
                    block
                    size="middle"
                    onClick={() => openEdit(plan)}
                    style={{ borderRadius: 6, fontWeight: 500 }}
                  >
                    修改套餐配置
                  </Button>
                </div>
              </Card>
            </Col>
          );
        })}
      </Row>

      {/* 修改价格与权益弹窗 */}
      <Modal
        title={editing ? `修改配置 · ${editing.name_zh || editing.name_en}` : "修改配置"}
        open={editing !== null}
        onCancel={closeEdit}
        onOk={() => form.submit()}
        okText="保存新版本"
        cancelText="取消"
        confirmLoading={saving}
        maskClosable={!saving}
        keyboard={!saving}
        destroyOnClose
        width={480}
      >
        {editing ? (
          <Form form={form} layout="vertical" onFinish={onSave} requiredMark={false} style={{ marginTop: 8 }}>
            <Alert
              type="warning"
              showIcon
              message={`当前版本 v${editing.version} · $${centsToDollars(editing.price_cents)}`}
              description="点击保存将写入新版本，立即同步至客户端与官网，已有付费订单计价不受影响。"
              style={{ marginBottom: 16, borderRadius: 6 }}
            />
            <Form.Item
              label="套餐售价（美元）"
              name="price"
              rules={[
                { required: true, message: "请填写价格" },
                { type: "number", min: 0.01, message: "价格至少 $0.01" },
              ]}
            >
              <InputNumber
                min={0.01}
                step={0.01}
                precision={2}
                prefix="$"
                style={{ width: "100%" }}
                autoFocus
              />
            </Form.Item>
            <Form.Item
              label="包含权益（一行一条）"
              name="benefits"
              tooltip="每行输入一条权益，将在客户端及官网会员卡片列表中逐项展示"
            >
              <Input.TextArea rows={4} placeholder={"Unlock all\nNo ads\n1080p"} />
            </Form.Item>
            <Form.Item label="推荐标签（Best）" name="best">
              <Radio.Group>
                <Radio value={true}>标为 Best（重点高亮推荐）</Radio>
                <Radio value={false}>不标记（常规套餐）</Radio>
              </Radio.Group>
            </Form.Item>
          </Form>
        ) : null}
      </Modal>
    </Space>
  );
}

export function OpsCenterPage() {
  const [title, setTitle] = useState("");
  const { data, reload } = useLoad<{ items: any[] }>("/admin/v1/pushes", { items: [] });
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Typography.Title level={4}>运营中心</Typography.Title>
      <Space>
        <Input placeholder="推送标题" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Button type="primary" onClick={async () => { await api(`/admin/v1/pushes?title=${encodeURIComponent(title)}`, { method: "POST" }); setTitle(""); reload(); }}>记录推送</Button>
      </Space>
      <Table rowKey="id" dataSource={data.items} pagination={false} scroll={{ x: 480 }} columns={[{ title: "内容", dataIndex: "title" }, { title: "送达", dataIndex: "delivered_count" }, { title: "时间", dataIndex: "sent_at" }]} />
    </Space>
  );
}

type EpisodePreview = {
  open: boolean;
  /** iframe embed URL shown in the modal; falls back to the player page URL. */
  url?: string;
  title: string;
};

export function EpisodesPanel({ dramaId }: { dramaId: string }) {
  const [status, setStatus] = useState<string | undefined>();
  const [preview, setPreview] = useState<EpisodePreview>({ open: false, title: "" });
  const closePreview = () => setPreview({ open: false, title: "" });
  const { data, reload } = useLoad<{ items: any[] }>(`/admin/v1/dramas/${dramaId}/episodes?page_size=200${status ? `&status=${status}` : ""}`, { items: [] });

  const openPreview = async (episodeId: number, episodeNumber: number) => {
    try {
      const signed = await api<{ url: string; embed_url?: string }>(`/admin/v1/episodes/${episodeId}/preview-url`);
      setPreview({
        open: true,
        url: signed.embed_url ?? signed.url,
        title: `预览 · 第 ${episodeNumber} 集`,
      });
    } catch (e) {
      message.error(`预览失败：${errorText(e)}`);
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space>
        <Select allowClear placeholder="状态" style={{ width: 160 }} value={status} onChange={setStatus} options={["not_uploaded", "transcoding", "transcode_failed", "in_review", "published"].map((v) => ({ value: v, label: v }))} />
        <Button onClick={reload}>筛选</Button>
        <Button onClick={async () => { await api(`/admin/v1/dramas/${dramaId}/episodes/bulk`, { method: "POST", body: JSON.stringify({ count: 64 }) }); reload(); }}>登记 64 集</Button>
      </Space>
      <Table rowKey="id" dataSource={data.items} pagination={{ pageSize: 20 }} scroll={{ x: 560 }} columns={[
        { title: "集", dataIndex: "episode_number" },
        { title: "标题", dataIndex: "title" },
        { title: "状态", dataIndex: "status" },
        { title: "操作", render: (_, row) => (
          row.bunny_video_id ? (
            <Button size="small" type="link" style={{ padding: 0 }} onClick={() => openPreview(row.id, row.episode_number)}>预览</Button>
          ) : (
            <Button size="small" onClick={async () => {
              await api(`/admin/v1/episodes/${row.id}/upload-session`, { method: "POST", body: JSON.stringify({ filename: "ep.mp4", mime: "video/mp4", size_bytes: 1024, width: 720, height: 1280 }) });
              message.success("已创建直传会话，文件不经过 API");
              reload();
            }}>上传</Button>
          )
        ) },
      ]} />
      <Modal
        open={preview.open}
        title={preview.title}
        footer={null}
        width={460}
        centered
        destroyOnHidden
        onCancel={closePreview}
        styles={{ body: { padding: 0, background: "#000" } }}
      >
        {preview.url ? (
          <iframe
            src={preview.url}
            title="episode preview"
            style={{ display: "block", width: "100%", height: "74vh", border: 0, background: "#000" }}
            allow="autoplay; fullscreen; encrypted-media; picture-in-picture"
            allowFullScreen
          />
        ) : null}
      </Modal>
    </Space>
  );
}

export function GroupsPanel({ dramaId }: { dramaId: string }) {
  const { data, reload } = useLoad<{ items: any[] }>("/admin/v1/groups", { items: [] });
  const [name, setName] = useState("");
  return (
    <Space direction="vertical">
      <Space>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="分组标题" />
        <Button onClick={async () => { await api("/admin/v1/groups", { method: "POST", body: JSON.stringify({ name, position: data.items.length }) }); setName(""); reload(); }}>新建分组</Button>
      </Space>
      {data.items.map((group) => (
        <Card key={group.id} size="small" title={`${group.name} · 排序 ${group.position}`}>
          <Button onClick={async () => { await api(`/admin/v1/groups/${group.id}/dramas`, { method: "POST", body: JSON.stringify({ drama_id: Number(dramaId), confirm: true }) }); message.success("已加入"); }}>加入本组</Button>
        </Card>
      ))}
    </Space>
  );
}

type PublishAction = { to: string; label: string; primary?: boolean; danger?: boolean };

const DRAMA_STATUS_TEXT: Record<string, string> = {
  draft: "草稿",
  in_review: "审核中",
  published: "已上架",
  unpublished: "已下架",
};

const DRAMA_STATUS_COLOR: Record<string, string> = {
  draft: "default",
  in_review: "orange",
  published: "green",
  unpublished: "red",
};

type DeleteImpact = {
  groups?: string[];
  banner_count?: number;
  push_count?: number;
  episode_count?: number;
};

/**
 * 删除预检：后端用 409 + {confirm_required, impact} 返回“删除会带走什么”。
 * 走 api() 而不是裸 fetch，才能复用统一的 401 跳登录与错误解析（403 会抛出
 * “admin only”，之前直接被丢进 JSON.stringify 里展示给用户）。
 */
async function previewDramaDelete(dramaId: string): Promise<DeleteImpact | null> {
  try {
    await api(`/admin/v1/dramas/${dramaId}/delete`, {
      method: "POST",
      body: JSON.stringify({ confirm: false }),
    });
    // 后端总会先返回 409；真的 200 说明剧集已经被删掉了。
    return null;
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && e.detail?.confirm_required) {
      return (e.detail.impact ?? {}) as DeleteImpact;
    }
    throw e;
  }
}

function DeleteConfirmContent({
  dramaTitle,
  impact,
  fallbackEpisodeCount,
}: {
  dramaTitle: string;
  impact: DeleteImpact;
  fallbackEpisodeCount?: number;
}) {
  const groups = impact.groups ?? [];
  const episodes = impact.episode_count ?? fallbackEpisodeCount ?? 0;
  const rows: [string, string][] = [
    ["分集", `${episodes} 集（视频记录、字幕、封面一并删除）`],
    ["首页分组引用", groups.length ? `${groups.length} 个：${groups.join("、")}` : "无"],
    ["Banner 引用", impact.banner_count ? `${impact.banner_count} 条` : "无"],
    ["推送记录", impact.push_count ? `${impact.push_count} 条` : "无"],
    ["用户数据", "观看进度、解锁记录与评论"],
  ];
  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Alert
        type="error"
        showIcon
        message="删除后不可恢复"
        description={`《${dramaTitle}》及其关联数据会被永久删除，后台无法找回。`}
      />
      <ul style={{ margin: 0, paddingInlineStart: 20 }}>
        {rows.map(([label, value]) => (
          <li key={label}>
            {label}：{value}
          </li>
        ))}
      </ul>
    </Space>
  );
}

export function PublishPanel({
  dramaId,
  dramaTitle,
  episodeCount,
  status,
  issues = [],
}: {
  dramaId: string;
  dramaTitle: string;
  episodeCount?: number;
  status: string;
  issues?: string[];
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const { confirm, confirmModal } = useConfirm();
  const navigate = useNavigate();
  // 删除剧集是高危操作，后端只放行 admin（docs/software-architecture.md 角色矩阵）。
  const isAdmin = useIsAdmin();

  const submitStatus = async (to_status: string, label: string) => {
    setBusy(to_status);
    try {
      await api(`/admin/v1/dramas/${dramaId}/status`, {
        method: "POST",
        body: JSON.stringify({ to_status }),
      });
      message.success(`${label}成功`);
      window.location.reload();
    } catch (e) {
      // 409 = 状态机不允许的跳转（例如草稿直接上架）
      message.error(`${label}失败：${errorText(e)}`);
      setBusy(null);
    }
  };

  const act = async (to_status: string, label: string) => {
    if (issues.length === 0) {
      await submitStatus(to_status, label);
      return;
    }
    confirm({
      title: `${label}前还有未完成的步骤`,
      okText: `仍然${label}`,
      cancelText: "取消",
      content: (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Alert type="warning" showIcon message="下面这些还没有完成" />
          <ul style={{ margin: 0, paddingInlineStart: 20 }}>
            {issues.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Space>
      ),
      onOk: () => submitStatus(to_status, label),
    });
  };

  const onDelete = async () => {
    setDeleting(true);
    let impact: DeleteImpact | null = null;
    try {
      impact = await previewDramaDelete(dramaId);
    } catch (e) {
      message.error(
        e instanceof ApiError && e.status === 403
          ? "删除失败：只有管理员可以删除剧集，请用 admin 账号登录"
          : `获取删除影响范围失败：${errorText(e)}`,
      );
      return;
    } finally {
      setDeleting(false);
    }
    if (impact === null) {
      message.success(`《${dramaTitle}》已删除`);
      navigate("/dramas");
      return;
    }
    confirm({
      title: "删除剧集",
      danger: true,
      okText: "删除",
      cancelText: "取消",
      width: 480,
      content: (
        <DeleteConfirmContent
          dramaTitle={dramaTitle}
          impact={impact}
          fallbackEpisodeCount={episodeCount}
        />
      ),
      onOk: async () => {
        try {
          await api(`/admin/v1/dramas/${dramaId}/delete`, {
            method: "POST",
            body: JSON.stringify({ confirm: true }),
          });
        } catch (e) {
          message.error(
            e instanceof ApiError && e.status === 403
              ? "删除失败：只有管理员可以删除剧集，请用 admin 账号登录"
              : `删除失败：${errorText(e)}`,
          );
          throw e;
        }
        message.success(`《${dramaTitle}》已删除`);
        navigate("/dramas");
      },
    });
  };

  // 状态机见 backend/app/modules/content/service.py：
  // draft -> in_review -> published，published -> unpublished，unpublished 可重新上架。
  // 这里只列出当前状态下合法的动作，避免点了按钮才吃到 409。
  const actions: PublishAction[] =
    status === "draft"
      ? [{ to: "in_review", label: "提交审核", primary: true }]
      : status === "in_review"
        ? [
            { to: "published", label: "通过并上架", primary: true },
            { to: "draft", label: "退回草稿" },
          ]
        : status === "published"
          ? [{ to: "unpublished", label: "下架", primary: true, danger: true }]
          : [
              { to: "published", label: "重新上架", primary: true },
              { to: "in_review", label: "提交复审" },
            ];

  return (
    <>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Space size={8}>
          <Typography.Text>当前状态：</Typography.Text>
          <Tag color={DRAMA_STATUS_COLOR[status]}>{DRAMA_STATUS_TEXT[status] ?? status}</Tag>
        </Space>

      {issues.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ width: "100%" }}
          message="上架前建议先完成这些"
          description={
            <ul style={{ margin: 0, paddingInlineStart: 20 }}>
              {issues.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          }
        />
      )}

      <Space wrap>
        {actions.map((action) => (
          <Button
            key={action.to}
            type={action.primary ? "primary" : "default"}
            danger={action.danger}
            loading={busy === action.to}
            disabled={busy !== null && busy !== action.to}
            onClick={() => act(action.to, action.label)}
          >
            {action.label}
          </Button>
        ))}
      </Space>

      <Typography.Text type="secondary">
        流程：草稿 → 提交审核 → 上架；已上架可下架，下架后可重新上架。
      </Typography.Text>

      <Tooltip title={isAdmin ? "" : "仅管理员可删除剧集"}>
        {/* Tooltip 在 disabled 元素上不触发，所以包一层 span */}
        <span style={{ display: "inline-block" }}>
          <Button danger disabled={!isAdmin} loading={deleting} onClick={onDelete}>
            删除剧集
          </Button>
        </span>
      </Tooltip>

      </Space>

      {/* 自定义确认弹框（替代 window.confirm），放在 Space 外，避免多出一个空间距 */}
      {confirmModal}
    </>
  );
}
