import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  Col,
  Divider,
  Dropdown,
  InputNumber,
  Pagination,
  Popconfirm,
  Row,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import type { MenuProps } from "antd";
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  CalendarOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DollarCircleFilled,
  FireOutlined,
  GiftOutlined,
  HeartOutlined,
  HistoryOutlined,
  InfoCircleOutlined,
  MailOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  ShareAltOutlined,
  StarOutlined,
  ThunderboltOutlined,
  TrophyOutlined,
  UserAddOutlined,
} from "@ant-design/icons";
import { api, errorText } from "../api";

/**
 * 金币与任务配置：维护 7 天签到梯度、注册奖励、绑定邮箱、绑定 Facebook、每日广告奖励。
 * 保存后的生效时间按「次日生效」执行（America/Los_Angeles）。
 */

type ConfigVersion = {
  amounts: number[];
  effective_on: string | null;
  is_default?: boolean;
};

type PendingVersion = {
  amounts: number[];
  effective_on: string;
};

type RewardItem = {
  code: string;
  name: string;
  description: string;
  kind: "gradient" | "single";
  size: number;
  default_amounts: number[];
  current: ConfigVersion;
  pending: PendingVersion | null;
};

type ChangeRow = {
  id: number;
  code: string;
  amounts: number[];
  effective_on: string | null;
  actor: string;
  created_at: string | null;
};

type Overview = {
  today: string;
  next_effective_on: string;
  items: RewardItem[];
  recent_changes: ChangeRow[];
};

const MAX_AMOUNT = 1_000_000;
const BRAND_RED = "#e24b4a";

const sameAmounts = (a: number[], b: number[]) =>
  a.length === b.length && a.every((v, i) => v === b[i]);

function formatTime(value?: string | null) {
  if (!value) return "-";
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(
    moment.getDate()
  )} ${pad(moment.getHours())}:${pad(moment.getMinutes())}`;
}

// 任务元数据映射：提供类别、专属图标与强调色
const TASK_META: Record<
  string,
  {
    category: "onetime" | "daily";
    categoryLabel: string;
    icon: React.ReactNode;
    color: string;
    bg: string;
    hint: string;
  }
> = {
  register: {
    category: "onetime",
    categoryLabel: "新手成长",
    icon: <UserAddOutlined />,
    color: "#1677ff",
    bg: "#e6f4ff",
    hint: "新用户创建账号成功后自动发放，终身仅触发一次",
  },
  bind_email: {
    category: "onetime",
    categoryLabel: "账号安全",
    icon: <MailOutlined />,
    color: "#722ed1",
    bg: "#f9f0ff",
    hint: "首次成功绑定有效邮箱地址后发放，换绑不重发",
  },
  bind_facebook: {
    category: "onetime",
    categoryLabel: "社交绑定",
    icon: <ShareAltOutlined />,
    color: "#1877f2",
    bg: "#e8f3ff",
    hint: "首次授权绑定 Facebook 社交账号发放，换绑不重发",
  },
  ad: {
    category: "daily",
    categoryLabel: "日常激励",
    icon: <PlayCircleOutlined />,
    color: "#fa8c16",
    bg: "#fff7e6",
    hint: "每日观看完整激励广告后发放，每天限领 1 次",
  },
  like_5: {
    category: "daily",
    categoryLabel: "日常互动",
    icon: <HeartOutlined />,
    color: "#e24b4a",
    bg: "#fff1f0",
    hint: "当天给 5 部不同的剧点赞即可领取，每天限领 1 次",
  },
  favorite_5: {
    category: "daily",
    categoryLabel: "日常互动",
    icon: <StarOutlined />,
    color: "#d48806",
    bg: "#fffbe6",
    hint: "当天收藏 5 部不同的剧即可领取，每天限领 1 次",
  },
  watch_8: {
    category: "daily",
    categoryLabel: "观看任务",
    icon: <PlayCircleOutlined />,
    color: "#13c2c2",
    bg: "#e6fffb",
    hint: "当天完整观看 8 集短剧即可领取，每天限领 1 次",
  },
};

// 预设签到梯度模板
const CHECKIN_PRESETS = [
  {
    key: "ladder",
    label: "经典递增梯度（首日留存与长线拉升）",
    values: [30, 30, 50, 50, 30, 60, 90],
  },
  {
    key: "high_end",
    label: "爆发冲量梯度（周尾大礼包激励）",
    values: [20, 30, 40, 50, 60, 80, 120],
  },
  {
    key: "flat",
    label: "平缓平稳梯度（每日均衡发放）",
    values: [30, 30, 30, 40, 40, 50, 70],
  },
  {
    key: "weekend_boost",
    label: "周末双倍冲刺梯度（Day6/7 高额）",
    values: [25, 25, 35, 35, 40, 80, 100],
  },
];

export default function Rewards() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [drafts, setDrafts] = useState<Record<string, number[]>>({});
  const [loading, setLoading] = useState(false);
  const [savingCodes, setSavingCodes] = useState<Set<string>>(new Set());
  const [batchSaving, setBatchSaving] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [historyFilter, setHistoryFilter] = useState<string>("all");
  const [historyPage, setHistoryPage] = useState(1);
  const pageSize = 8;

  // 加载数据：preserveDrafts 为 true 时不覆盖正在编辑的项
  const load = useCallback(async (preserveDrafts = false) => {
    setLoading(true);
    try {
      const res = await api<Overview>("/admin/v1/rewards/config");
      setOverview(res);
      setDrafts((prev) => {
        const next: Record<string, number[]> = preserveDrafts ? { ...prev } : {};
        for (const item of res.items) {
          if (!preserveDrafts || !next[item.code]) {
            next[item.code] = [...(item.pending?.amounts ?? item.current.amounts)];
          }
        }
        return next;
      });
    } catch (e) {
      message.error(`加载配置失败：${errorText(e)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const items = overview?.items ?? [];
  const checkin = items.find((item) => item.code === "checkin");
  const singles = items.filter((item) => item.kind === "single");

  // 当前草稿的基准参考值（有 pending 用 pending，否则用 current）
  const baseline = useCallback((item: RewardItem) => {
    return item.pending?.amounts ?? item.current.amounts;
  }, []);

  // 某项是否有未保存的改动
  const isDirty = useCallback(
    (item: RewardItem) => {
      const draft = drafts[item.code];
      if (!draft || draft.length !== item.size) return false;
      return !sameAmounts(draft, baseline(item));
    },
    [drafts, baseline]
  );

  // 所有处于脏状态的项
  const dirtyItems = useMemo(() => {
    return items.filter((item) => isDirty(item));
  }, [items, isDirty]);

  // 未保存拦截防呆
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (dirtyItems.length > 0) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [dirtyItems.length]);

  const setDraftValue = (code: string, index: number, value: number | null) => {
    setDrafts((prev) => {
      const next = { ...prev, [code]: [...(prev[code] ?? [])] };
      next[code][index] = value === null ? Number.NaN : value;
      return next;
    });
  };

  const resetDraft = (item: RewardItem) => {
    setDrafts((prev) => ({ ...prev, [item.code]: [...baseline(item)] }));
    message.info(`已撤回【${item.name}】的未保存修改`);
  };

  const resetAllDrafts = () => {
    setDrafts(
      Object.fromEntries(
        items.map((item) => [item.code, [...baseline(item)]])
      )
    );
    message.info("已放弃所有未保存修改");
  };

  // 保存单项
  const saveOne = async (item: RewardItem) => {
    const draft = drafts[item.code] ?? [];
    if (
      draft.length !== item.size ||
      draft.some((v) => !Number.isInteger(v) || v < 1 || v > MAX_AMOUNT)
    ) {
      message.error(
        `${item.name} 需要填写 ${item.size} 个 1 ~ ${MAX_AMOUNT.toLocaleString()} 之间的正整数金币数`
      );
      return;
    }

    setSavingCodes((prev) => new Set(prev).add(item.code));
    try {
      const row = await api<{ code: string; amounts: number[]; effective_on: string }>(
        "/admin/v1/rewards/config",
        {
          method: "POST",
          body: JSON.stringify({ code: item.code, amounts: draft }),
        }
      );
      message.success(
        `【${item.name}】保存成功，将于 ${row.effective_on}（洛杉矶时间）正式生效`
      );
      // 局部刷新，保留其他草稿不被覆盖
      await load(true);
    } catch (e) {
      message.error(`保存失败：${errorText(e)}`);
    } finally {
      setSavingCodes((prev) => {
        const next = new Set(prev);
        next.delete(item.code);
        return next;
      });
    }
  };

  // 一键批量保存全部已修改项
  const saveAllDirty = async () => {
    if (dirtyItems.length === 0) return;
    for (const item of dirtyItems) {
      const draft = drafts[item.code] ?? [];
      if (
        draft.length !== item.size ||
        draft.some((v) => !Number.isInteger(v) || v < 1 || v > MAX_AMOUNT)
      ) {
        message.error(
          `【${item.name}】数值不合法，请检查并修正后再批量保存`
        );
        return;
      }
    }

    setBatchSaving(true);
    let successCount = 0;
    try {
      for (const item of dirtyItems) {
        await api<{ code: string; amounts: number[]; effective_on: string }>(
          "/admin/v1/rewards/config",
          {
            method: "POST",
            body: JSON.stringify({ code: item.code, amounts: drafts[item.code] }),
          }
        );
        successCount++;
      }
      message.success(
        `批量保存成功！共更新 ${successCount} 项配置，将于 ${
          overview?.next_effective_on ?? "次日"
        }（洛杉矶时间）生效`
      );
      await load(false);
    } catch (e) {
      message.error(`部分保存失败：${errorText(e)}`);
      await load(true);
    } finally {
      setBatchSaving(false);
    }
  };

  // 格式化金额序列
  const amountText = (amounts: number[]) =>
    amounts.map((v) => (Number.isFinite(v) ? `+${v}` : "—")).join(" / ");

  // 7 天签到梯度统计分析
  const checkinStats = useMemo(() => {
    if (!checkin) return null;
    const currentAmounts = checkin.current.amounts;
    const draftAmounts = drafts[checkin.code] ?? currentAmounts;
    const baseAmounts = baseline(checkin);

    const draftSum = draftAmounts.reduce(
      (acc, v) => acc + (Number.isFinite(v) ? v : 0),
      0
    );
    const baseSum = baseAmounts.reduce((acc, v) => acc + v, 0);
    const sumDiff = draftSum - baseSum;
    const day1 = draftAmounts[0] || 1;
    const day7 = draftAmounts[6] || 0;
    const boostRatio = (day7 / day1).toFixed(1);
    const dailyAvg = (draftSum / 7).toFixed(1);

    return {
      draftSum,
      baseSum,
      sumDiff,
      boostRatio,
      dailyAvg,
      day1,
      day7,
    };
  }, [checkin, drafts, baseline]);

  // 签到预设菜单
  const presetMenuItems: MenuProps["items"] = useMemo(() => {
    const list: MenuProps["items"] = CHECKIN_PRESETS.map((p) => ({
      key: p.key,
      label: (
        <Space direction="vertical" size={0}>
          <Typography.Text strong style={{ fontSize: 13 }}>
            {p.label}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            梯度: {p.values.join(" → ")} 金币 (总计 {p.values.reduce((a, b) => a + b, 0)})
          </Typography.Text>
        </Space>
      ),
      onClick: () => {
        if (!checkin) return;
        setDrafts((prev) => ({ ...prev, [checkin.code]: [...p.values] }));
        message.info(`已应用预设：${p.label}`);
      },
    }));

    if (checkin?.default_amounts) {
      list.push({ type: "divider" });
      list.push({
        key: "system_default",
        label: (
          <Space>
            <Typography.Text type="danger">恢复系统内置默认梯度</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              ({checkin.default_amounts.join(" / ")})
            </Typography.Text>
          </Space>
        ),
        onClick: () => {
          setDrafts((prev) => ({ ...prev, [checkin.code]: [...checkin.default_amounts] }));
          message.info("已重置为系统内置默认梯度");
        },
      });
    }

    return list;
  }, [checkin]);

  // 过滤后的单项任务
  const filteredSingles = useMemo(() => {
    if (categoryFilter === "all") return singles;
    return singles.filter((s) => {
      const meta = TASK_META[s.code];
      return meta ? meta.category === categoryFilter : categoryFilter === "onetime";
    });
  }, [singles, categoryFilter]);

  // 任务表格列定义
  const singleColumns = [
    {
      title: "任务类型与名称",
      dataIndex: "name",
      key: "name",
      width: 320,
      render: (_: unknown, item: RewardItem) => {
        const meta = TASK_META[item.code] ?? {
          categoryLabel: "通用任务",
          icon: <GiftOutlined />,
          color: BRAND_RED,
          bg: "#fff2f0",
          hint: item.description,
        };
        const changed = isDirty(item);
        return (
          <Space align="start" size={12}>
            <div
              style={{
                width: 40,
                height: 40,
                borderRadius: 10,
                background: meta.bg,
                color: meta.color,
                display: "grid",
                placeItems: "center",
                fontSize: 18,
                flexShrink: 0,
                border: `1px solid ${meta.color}22`,
              }}
            >
              {meta.icon}
            </div>
            <Space direction="vertical" size={2}>
              <Space size={6} wrap>
                <Typography.Text strong style={{ fontSize: 14 }}>
                  {item.name}
                </Typography.Text>
                <Tag
                  bordered={false}
                  style={{
                    fontSize: 11,
                    lineHeight: "18px",
                    background: meta.bg,
                    color: meta.color,
                    fontWeight: 500,
                  }}
                >
                  {meta.categoryLabel}
                </Tag>
                <Tag style={{ fontSize: 11, margin: 0 }}>{item.code}</Tag>
                {changed ? <Tag color="gold">已修改未保存</Tag> : null}
              </Space>
              <Typography.Text type="secondary" style={{ fontSize: 12, lineHeight: 1.4 }}>
                {meta.hint || item.description}
              </Typography.Text>
            </Space>
          </Space>
        );
      },
    },
    {
      title: "当前线上生效",
      dataIndex: "current",
      key: "current",
      width: 220,
      render: (_: unknown, item: RewardItem) => {
        const { amounts, effective_on, is_default } = item.current;
        const val = amounts[0] ?? 0;
        return (
          <Space direction="vertical" size={2}>
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 10px",
                borderRadius: 6,
                background: "#f6ffed",
                border: "1px solid #b7eb8f",
              }}
            >
              <DollarCircleFilled style={{ color: "#52c41a", fontSize: 15 }} />
              <Typography.Text strong style={{ color: "#237804", fontSize: 14 }}>
                +{val.toLocaleString()}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                金币
              </Typography.Text>
            </div>
            <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 2 }}>
              {is_default || !effective_on ? "系统内置默认" : `${effective_on} 起执行`}
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      title: "待生效预约",
      key: "pending",
      width: 220,
      render: (_: unknown, item: RewardItem) => {
        if (!item.pending) {
          return (
            <Typography.Text type="secondary" style={{ color: "#bfbfbf", fontSize: 13 }}>
              —
            </Typography.Text>
          );
        }
        const val = item.pending.amounts[0] ?? 0;
        const currentVal = item.current.amounts[0] ?? 0;
        const diff = val - currentVal;
        return (
          <Space direction="vertical" size={2}>
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 10px",
                borderRadius: 6,
                background: "#fffbe6",
                border: "1px solid #ffe58f",
              }}
            >
              <ClockCircleOutlined style={{ color: "#faad14", fontSize: 14 }} />
              <Typography.Text strong style={{ color: "#d48806", fontSize: 14 }}>
                +{val.toLocaleString()}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                金币
              </Typography.Text>
              {diff !== 0 && (
                <Tag
                  color={diff > 0 ? "success" : "error"}
                  bordered={false}
                  style={{ fontSize: 11, margin: 0, padding: "0 4px", lineHeight: "16px" }}
                >
                  {diff > 0 ? `+${diff}` : diff}
                </Tag>
              )}
            </div>
            <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 2 }}>
              预计将于 {item.pending.effective_on} 生效
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      title: "调整奖励数值",
      key: "action",
      width: 320,
      render: (_: unknown, item: RewardItem) => {
        const draftVal = drafts[item.code]?.[0];
        const baseVal = baseline(item)[0] ?? 0;
        const changed = isDirty(item);
        const isSaving = savingCodes.has(item.code) || batchSaving;
        const diff = Number.isFinite(draftVal) ? draftVal - baseVal : 0;

        return (
          <Space size={8} align="center">
            <div style={{ position: "relative" }}>
              <InputNumber
                min={1}
                max={MAX_AMOUNT}
                precision={0}
                style={{
                  width: 136,
                  borderColor: changed ? "#faad14" : undefined,
                  boxShadow: changed ? "0 0 0 2px rgba(250, 173, 20, 0.15)" : undefined,
                }}
                prefix={<span style={{ color: "#8c8c8c", fontSize: 12 }}>+</span>}
                suffix={<span style={{ color: "#8c8c8c", fontSize: 12 }}>金币</span>}
                value={Number.isFinite(draftVal) ? draftVal : null}
                onChange={(value) => setDraftValue(item.code, 0, value)}
                disabled={isSaving}
              />
              {changed && diff !== 0 && (
                <div
                  style={{
                    position: "absolute",
                    right: 8,
                    top: -18,
                    fontSize: 11,
                    fontWeight: 600,
                    color: diff > 0 ? "#52c41a" : "#ff4d4f",
                    display: "flex",
                    alignItems: "center",
                    gap: 2,
                  }}
                >
                  {diff > 0 ? (
                    <>
                      <ArrowUpOutlined style={{ fontSize: 10 }} />+{diff}
                    </>
                  ) : (
                    <>
                      <ArrowDownOutlined style={{ fontSize: 10 }} />
                      {diff}
                    </>
                  )}
                </div>
              )}
            </div>

            <Popconfirm
              title={`确认保存【${item.name}】调整？`}
              description={`调整为 +${draftVal} 金币，将于次日（${
                overview?.next_effective_on ?? "明天"
              } 洛杉矶时间）生效。`}
              onConfirm={() => saveOne(item)}
              okText="确认提交"
              cancelText="取消"
              disabled={!changed}
            >
              <Button
                type={changed ? "primary" : "default"}
                size="middle"
                icon={<SaveOutlined />}
                disabled={!changed}
                loading={isSaving}
                style={
                  changed
                    ? {
                        background: BRAND_RED,
                        borderColor: BRAND_RED,
                      }
                    : undefined
                }
              >
                保存
              </Button>
            </Popconfirm>

            {changed ? (
              <Tooltip title="撤回当前未保存输入">
                <Button
                  size="middle"
                  icon={<RollbackOutlined />}
                  disabled={isSaving}
                  onClick={() => resetDraft(item)}
                />
              </Tooltip>
            ) : null}
          </Space>
        );
      },
    },
  ];

  // 过滤后的历史记录
  const filteredChanges = useMemo(() => {
    const list = overview?.recent_changes ?? [];
    if (historyFilter === "all") return list;
    return list.filter((r) => r.code === historyFilter);
  }, [overview?.recent_changes, historyFilter]);

  const pagedChanges = useMemo(() => {
    const start = (historyPage - 1) * pageSize;
    return filteredChanges.slice(start, start + pageSize);
  }, [filteredChanges, historyPage, pageSize]);

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={20}>
      {/* 顶部现代化业务导航与全局状态栏 */}
      <Card
        variant="borderless"
        style={{
          borderRadius: 12,
          background: "linear-gradient(180deg, #ffffff 0%, #fafafa 100%)",
          boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
          border: "1px solid #ebebeb",
        }}
        styles={{ body: { padding: "18px 24px" } }}
      >
        <Row justify="space-between" align="middle" gutter={[16, 16]}>
          <Col xs={24} md={14}>
            <Space align="start" size={14}>
              <div
                style={{
                  width: 44,
                  height: 44,
                  borderRadius: 12,
                  background: "linear-gradient(135deg, #fff1f0 0%, #ffccc7 100%)",
                  display: "grid",
                  placeItems: "center",
                  color: BRAND_RED,
                  fontSize: 22,
                  boxShadow: "0 2px 6px rgba(226, 75, 74, 0.15)",
                }}
              >
                <GiftOutlined />
              </div>
              <Space direction="vertical" size={4}>
                <Space align="center" size={10} wrap>
                  <Typography.Title level={4} style={{ margin: 0, fontWeight: 700 }}>
                    金币与任务奖励配置
                  </Typography.Title>
                  <Tag color="red" bordered={false} style={{ fontWeight: 600 }}>
                    MiniTV 激励体系
                  </Tag>
                </Space>
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                  维护 7 天连续签到阶梯、新用户注册、社交账号绑定与每日激励广告金币。
                </Typography.Text>
              </Space>
            </Space>
          </Col>

          <Col xs={24} md={10}>
            <Row justify="end" align="middle" gutter={[10, 10]}>
              <Col>
                <Button
                  icon={<ReloadOutlined spin={loading} />}
                  onClick={() => load(true)}
                  disabled={loading}
                >
                  刷新配置
                </Button>
              </Col>
              {dirtyItems.length > 0 ? (
                <>
                  <Col>
                    <Button onClick={resetAllDrafts} disabled={batchSaving}>
                      放弃全部改动
                    </Button>
                  </Col>
                  <Col>
                    <Popconfirm
                      title={`确认批量保存 ${dirtyItems.length} 项修改？`}
                      description={`包含：${dirtyItems.map((d) => d.name).join("、")}。均将于 ${
                        overview?.next_effective_on ?? "次日"
                      }（洛杉矶时间）生效。`}
                      onConfirm={saveAllDirty}
                      okText="确认全部保存"
                      cancelText="取消"
                    >
                      <Button
                        type="primary"
                        loading={batchSaving}
                        icon={<SaveOutlined />}
                        style={{
                          background: BRAND_RED,
                          borderColor: BRAND_RED,
                          boxShadow: "0 2px 8px rgba(226,75,74,0.3)",
                        }}
                      >
                        批量保存全部 ({dirtyItems.length})
                      </Button>
                    </Popconfirm>
                  </Col>
                </>
              ) : (
                <Col>
                  <Tag
                    color="success"
                    icon={<CheckCircleOutlined />}
                    style={{ padding: "5px 12px", fontSize: 13, borderRadius: 20 }}
                  >
                    全部配置与线上一致
                  </Tag>
                </Col>
              )}
            </Row>
          </Col>
        </Row>

        <Divider style={{ margin: "14px 0" }} />

        {/* 规则机制摘要胶囊栏 */}
        <Row gutter={[12, 12]} align="middle">
          <Col xs={24} sm={12} lg={8}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "8px 12px",
                background: "#f0f5ff",
                borderRadius: 8,
                border: "1px solid #d6e4ff",
              }}
            >
              <ClockCircleOutlined style={{ color: "#2f54eb", fontSize: 16 }} />
              <div>
                <Typography.Text strong style={{ fontSize: 12, color: "#1d39c4" }}>
                  次日生效机制（PST 洛杉矶时间）
                </Typography.Text>
                <div style={{ fontSize: 11, color: "#597ef7" }}>
                  改动均于次日 00:00 生效，避免当天重复计算
                </div>
              </div>
            </div>
          </Col>

          <Col xs={24} sm={12} lg={8}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "8px 12px",
                background: "#fffbe6",
                borderRadius: 8,
                border: "1px solid #ffe58f",
              }}
            >
              <CalendarOutlined style={{ color: "#d48806", fontSize: 16 }} />
              <div>
                <Typography.Text strong style={{ fontSize: 12, color: "#d48806" }}>
                  下次生效时间：{overview?.next_effective_on ?? "-"}
                </Typography.Text>
                <div style={{ fontSize: 11, color: "#faad14" }}>
                  今日已领奖励保留，新数值从该日期起自动执行
                </div>
              </div>
            </div>
          </Col>

          <Col xs={24} sm={24} lg={8}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "8px 12px",
                background: "#f6ffed",
                borderRadius: 8,
                border: "1px solid #b7eb8f",
              }}
            >
              <SafetyCertificateOutlined style={{ color: "#389e0d", fontSize: 16 }} />
              <div>
                <Typography.Text strong style={{ fontSize: 12, color: "#389e0d" }}>
                  审计追溯与安全保障
                </Typography.Text>
                <div style={{ fontSize: 11, color: "#52c41a" }}>
                  单日内多次修改以最新记录为准，全量留痕
                </div>
              </div>
            </div>
          </Col>
        </Row>
      </Card>

      {/* 模块 1：7 天连续签到梯度阶梯卡片 */}
      <Card
        variant="borderless"
        style={{
          borderRadius: 12,
          boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
          border: "1px solid #ebebeb",
        }}
        title={
          <Space align="center" size={8}>
            <CalendarOutlined style={{ color: BRAND_RED, fontSize: 18 }} />
            <span style={{ fontSize: 16, fontWeight: 700 }}>
              {checkin ? checkin.name : "7 天签到梯度"}
            </span>
            <Tag color="orange" bordered={false}>
              连续 7 天周期
            </Tag>
          </Space>
        }
        extra={
          checkinStats ? (
            <Space size={16} wrap>
              <div style={{ textAlign: "right" }}>
                <span style={{ fontSize: 12, color: "#8c8c8c" }}>7天满签总计: </span>
                <Typography.Text
                  strong
                  style={{
                    fontSize: 15,
                    color: checkinStats.sumDiff !== 0 ? BRAND_RED : "#262626",
                  }}
                >
                  +{checkinStats.draftSum.toLocaleString()} 金币
                </Typography.Text>
                {checkinStats.sumDiff !== 0 && (
                  <Tag
                    color={checkinStats.sumDiff > 0 ? "success" : "error"}
                    bordered={false}
                    style={{ marginLeft: 6, fontSize: 11 }}
                  >
                    {checkinStats.sumDiff > 0
                      ? `+${checkinStats.sumDiff}`
                      : checkinStats.sumDiff}
                  </Tag>
                )}
              </div>
              <Divider type="vertical" />
              <div>
                <span style={{ fontSize: 12, color: "#8c8c8c" }}>日均发放: </span>
                <Typography.Text strong style={{ fontSize: 14 }}>
                  {checkinStats.dailyAvg} / 天
                </Typography.Text>
              </div>
              <Divider type="vertical" />
              <div>
                <span style={{ fontSize: 12, color: "#8c8c8c" }}>第7天爆发比: </span>
                <Typography.Text strong style={{ fontSize: 14, color: "#fa8c16" }}>
                  {checkinStats.boostRatio}x
                </Typography.Text>
              </div>
            </Space>
          ) : null
        }
        loading={loading && !checkin}
      >
        {checkin ? (
          <Space direction="vertical" size={18} style={{ width: "100%" }}>
            {/* 说明信息 */}
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              用户每天打开 App 签到依次领取 Day 1 ~ Day 7
              金币。若中途中断签到，周期将重置为 Day 1
              重新起算。建议在第 3 天与第 7 天设置大奖以提升次留与周留存。
            </Typography.Text>

            {/* 7 天阶梯卡片网格：桌面端 7 列横排成周期周历，小屏幕自适应 */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
                gap: 10,
                width: "100%",
              }}
            >
              {(drafts[checkin.code] ?? []).map((val, idx) => {
                const dayNum = idx + 1;
                const isDay7 = dayNum === 7;
                const isDay3 = dayNum === 3;
                const curVal = checkin.current.amounts[idx] ?? 0;
                const pendVal = checkin.pending?.amounts[idx];
                const baseVal = baseline(checkin)[idx] ?? curVal;
                const isItemDiff = Number.isFinite(val) && val !== baseVal;
                const diff = Number.isFinite(val) ? val - baseVal : 0;
                const isSaving = savingCodes.has(checkin.code) || batchSaving;

                return (
                  <div
                    key={idx}
                    style={{
                      padding: "12px 10px",
                      borderRadius: 10,
                      background: isDay7
                        ? "linear-gradient(180deg, #fffbf0 0%, #fff7e6 100%)"
                        : isDay3
                        ? "#fafbfc"
                        : "#ffffff",
                      border: isItemDiff
                        ? `1.5px solid ${BRAND_RED}`
                        : isDay7
                        ? "1.5px solid #ffd591"
                        : "1px solid #ebebeb",
                      boxShadow: isDay7
                        ? "0 2px 10px rgba(250, 173, 20, 0.18)"
                        : isItemDiff
                        ? "0 2px 8px rgba(226, 75, 74, 0.12)"
                        : "0 1px 3px rgba(0,0,0,0.02)",
                      display: "flex",
                      flexDirection: "column",
                      gap: 8,
                      position: "relative",
                      transition: "all 0.2s ease",
                    }}
                  >
                    {/* 顶部天数徽章 */}
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                      }}
                    >
                      <Tag
                        color={isDay7 ? "gold" : isDay3 ? "orange" : "default"}
                        bordered={false}
                        style={{
                          fontWeight: 700,
                          fontSize: 12,
                          padding: "1px 6px",
                          borderRadius: 4,
                          margin: 0,
                        }}
                      >
                        第 {dayNum} 天
                      </Tag>
                      {isDay7 ? (
                        <span
                          style={{
                            fontSize: 11,
                            color: "#d48806",
                            fontWeight: 700,
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 2,
                          }}
                        >
                          <TrophyOutlined /> 满签
                        </span>
                      ) : isDay3 ? (
                        <span style={{ fontSize: 11, color: "#fa8c16", fontWeight: 600 }}>
                          <FireOutlined /> 进阶
                        </span>
                      ) : null}
                    </div>

                    {/* 输入框 */}
                    <div>
                      <InputNumber
                        min={1}
                        max={MAX_AMOUNT}
                        precision={0}
                        style={{
                          width: "100%",
                          fontWeight: 700,
                          fontSize: 14,
                          textAlign: "center",
                          borderColor: isItemDiff ? BRAND_RED : undefined,
                        }}
                        prefix={
                          <DollarCircleFilled
                            style={{
                              color: isDay7 ? "#faad14" : "#bfbfbf",
                              fontSize: 12,
                            }}
                          />
                        }
                        value={Number.isFinite(val) ? val : null}
                        onChange={(next) => setDraftValue(checkin.code, idx, next)}
                        disabled={isSaving}
                      />
                    </div>

                    {/* 底部基线参考与 diff */}
                    <div
                      style={{
                        fontSize: 11,
                        display: "flex",
                        flexDirection: "column",
                        gap: 2,
                        color: "#8c8c8c",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                        }}
                      >
                        <span>当前: +{curVal}</span>
                        {isItemDiff && (
                          <span
                            style={{
                              color: diff > 0 ? "#52c41a" : "#ff4d4f",
                              fontWeight: 700,
                            }}
                          >
                            {diff > 0 ? `+${diff} ▲` : `${diff} ▼`}
                          </span>
                        )}
                      </div>
                      {pendVal !== undefined && (
                        <span style={{ color: "#d48806" }}>待生效: +{pendVal}</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            <Divider style={{ margin: "6px 0" }} />

            {/* 底部操作与工具栏 */}
            <Row justify="space-between" align="middle" gutter={[12, 12]}>
              <Col>
                <Space wrap size={10}>
                  <Dropdown menu={{ items: presetMenuItems }}>
                    <Button icon={<ThunderboltOutlined />}>
                      快速应用梯度预设 <ArrowDownOutlined style={{ fontSize: 10 }} />
                    </Button>
                  </Dropdown>
                  <Tooltip title="当前线上生效版本的生效时间">
                    <Tag icon={<CheckCircleOutlined />} color="default">
                      当前版本：
                      {checkin.current.is_default || !checkin.current.effective_on
                        ? "内置默认"
                        : `${checkin.current.effective_on} 起`}
                    </Tag>
                  </Tooltip>
                  {checkin.pending && (
                    <Tag icon={<ClockCircleOutlined />} color="orange">
                      预约更新：{checkin.pending.effective_on} 生效 (+
                      {checkin.pending.amounts.join("/")})
                    </Tag>
                  )}
                </Space>
              </Col>

              <Col>
                <Space size={8}>
                  <Button
                    disabled={!isDirty(checkin) || savingCodes.has(checkin.code) || batchSaving}
                    onClick={() => resetDraft(checkin)}
                  >
                    撤回未保存修改
                  </Button>
                  <Popconfirm
                    title="确认保存 7 天签到梯度修改？"
                    description={`新签到阶梯将于次日（${
                      overview?.next_effective_on ?? "明天"
                    } 洛杉矶时间）正式生效。`}
                    onConfirm={() => saveOne(checkin)}
                    okText="确认保存"
                    cancelText="取消"
                    disabled={!isDirty(checkin)}
                  >
                    <Button
                      type="primary"
                      disabled={!isDirty(checkin)}
                      loading={savingCodes.has(checkin.code) || batchSaving}
                      icon={<SaveOutlined />}
                      style={
                        isDirty(checkin)
                          ? {
                              background: BRAND_RED,
                              borderColor: BRAND_RED,
                              boxShadow: "0 2px 6px rgba(226,75,74,0.25)",
                            }
                          : undefined
                      }
                    >
                      保存签到梯度
                    </Button>
                  </Popconfirm>
                </Space>
              </Col>
            </Row>
          </Space>
        ) : (
          <Typography.Text type="secondary">暂无签到配置</Typography.Text>
        )}
      </Card>

      {/* 模块 2：任务奖励列表 */}
      <Card
        variant="borderless"
        style={{
          borderRadius: 12,
          boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
          border: "1px solid #ebebeb",
        }}
        title={
          <Space align="center" size={8}>
            <TrophyOutlined style={{ color: BRAND_RED, fontSize: 18 }} />
            <span style={{ fontSize: 16, fontWeight: 700 }}>任务与行为奖励配置</span>
          </Space>
        }
        extra={
          <Segmented
            options={[
              { label: "全部任务", value: "all" },
              { label: "新手与绑定", value: "onetime" },
              { label: "日常活跃", value: "daily" },
            ]}
            value={categoryFilter}
            onChange={(val) => setCategoryFilter(val as string)}
          />
        }
        loading={loading && singles.length === 0}
      >
        <Table
          rowKey="code"
          dataSource={filteredSingles}
          columns={singleColumns}
          pagination={false}
          size="middle"
        />
      </Card>

      {/* 模块 3：变更与审计记录 */}
      <Card
        variant="borderless"
        style={{
          borderRadius: 12,
          boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
          border: "1px solid #ebebeb",
        }}
        title={
          <Space align="center" size={8}>
            <HistoryOutlined style={{ color: BRAND_RED, fontSize: 18 }} />
            <span style={{ fontSize: 16, fontWeight: 700 }}>配置变更记录</span>
            <Tag bordered={false} style={{ color: "#8c8c8c" }}>
              共 {filteredChanges.length} 条记录
            </Tag>
          </Space>
        }
        extra={
          <Space size={8}>
            <span style={{ fontSize: 12, color: "#8c8c8c" }}>按任务过滤:</span>
            <Select
              size="small"
              style={{ width: 150 }}
              value={historyFilter}
              onChange={(val) => {
                setHistoryFilter(val);
                setHistoryPage(1);
              }}
              options={[
                { label: "全部任务", value: "all" },
                ...items.map((it) => ({ label: it.name, value: it.code })),
              ]}
            />
          </Space>
        }
      >
        <Table
          rowKey="id"
          dataSource={pagedChanges}
          pagination={false}
          size="middle"
          locale={{ emptyText: "暂无符合条件的变更记录" }}
          columns={[
            {
              title: "变更时间",
              dataIndex: "created_at",
              key: "created_at",
              width: 170,
              render: (v: string) => (
                <Tooltip title={v}>
                  <Space size={4}>
                    <ClockCircleOutlined style={{ color: "#bfbfbf", fontSize: 12 }} />
                    <Typography.Text style={{ fontSize: 13 }}>{formatTime(v)}</Typography.Text>
                  </Space>
                </Tooltip>
              ),
            },
            {
              title: "任务目标",
              dataIndex: "code",
              key: "code",
              width: 170,
              render: (code: string) => {
                const target = items.find((item) => item.code === code);
                const isCheckin = code === "checkin";
                return (
                  <Space size={6}>
                    <Typography.Text strong style={{ fontSize: 13 }}>
                      {target?.name ?? code}
                    </Typography.Text>
                    {isCheckin && (
                      <Tag color="orange" bordered={false} style={{ fontSize: 10, padding: "0 4px" }}>
                        7天梯度
                      </Tag>
                    )}
                  </Space>
                );
              },
            },
            {
              title: "调整后数值",
              dataIndex: "amounts",
              key: "amounts",
              render: (amounts: number[], row: ChangeRow) => {
                if (row.code === "checkin") {
                  return (
                    <Tooltip
                      title={
                        <div>
                          <div>7 天完整阶梯明细：</div>
                          {amounts.map((a, i) => (
                            <div key={i}>
                              第 {i + 1} 天: +{a} 金币
                            </div>
                          ))}
                        </div>
                      }
                    >
                      <Space size={4} wrap>
                        <Tag color="gold" style={{ fontSize: 12, cursor: "pointer" }}>
                          梯度: {amountText(amounts)}
                        </Tag>
                      </Space>
                    </Tooltip>
                  );
                }
                const val = amounts[0] ?? 0;
                return (
                  <div
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                      padding: "2px 8px",
                      borderRadius: 4,
                      background: "#f6ffed",
                      color: "#389e0d",
                      fontWeight: 600,
                      fontSize: 12,
                    }}
                  >
                    <DollarCircleFilled style={{ fontSize: 12 }} />+{val.toLocaleString()} 金币
                  </div>
                );
              },
            },
            {
              title: "生效日期与状态",
              dataIndex: "effective_on",
              key: "effective_on",
              width: 170,
              render: (effective_on: string | null) => {
                if (!effective_on) return "-";
                const today = overview?.today ?? "";
                const isPending = today && effective_on > today;
                return (
                  <Space size={6}>
                    <Typography.Text style={{ fontSize: 13 }}>{effective_on}</Typography.Text>
                    {isPending ? (
                      <Tag color="orange" bordered={false} icon={<ClockCircleOutlined />}>
                        待生效
                      </Tag>
                    ) : (
                      <Tag color="green" bordered={false} icon={<CheckCircleOutlined />}>
                        已生效
                      </Tag>
                    )}
                  </Space>
                );
              },
            },
            {
              title: "操作人",
              dataIndex: "actor",
              key: "actor",
              width: 140,
              render: (actor: string) => (
                <Space size={6}>
                  <Avatar
                    size={22}
                    style={{
                      backgroundColor: "#f56a00",
                      fontSize: 11,
                      verticalAlign: "middle",
                    }}
                  >
                    {actor.slice(0, 1)}
                  </Avatar>
                  <Typography.Text style={{ fontSize: 13 }}>{actor}</Typography.Text>
                </Space>
              ),
            },
          ]}
        />

        {filteredChanges.length > pageSize && (
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 16 }}>
            <Pagination
              size="small"
              current={historyPage}
              pageSize={pageSize}
              total={filteredChanges.length}
              onChange={(page) => setHistoryPage(page)}
              showTotal={(total) => `共 ${total} 条记录`}
            />
          </div>
        )}
      </Card>
    </Space>
  );
}
