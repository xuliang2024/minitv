import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, InputNumber, Space, Table, Tag, Tooltip, Typography, message } from "antd";
import { api, errorText } from "../api";

/**
 * 金币与任务：维护 7 天签到梯度、注册奖励、绑定邮箱、绑定 Facebook、每日广告奖励。
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

const sameAmounts = (a: number[], b: number[]) => a.length === b.length && a.every((v, i) => v === b[i]);

function formatTime(value?: string | null) {
  if (!value) return "-";
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())} ${pad(moment.getHours())}:${pad(moment.getMinutes())}`;
}

export default function Rewards() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [drafts, setDrafts] = useState<Record<string, number[]>>({});
  const [loading, setLoading] = useState(false);
  const [savingCode, setSavingCode] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api<Overview>("/admin/v1/rewards/config");
      setOverview(res);
      setDrafts(
        Object.fromEntries(
          res.items.map((item) => [item.code, [...(item.pending?.amounts ?? item.current.amounts)]])
        )
      );
    } catch (e) {
      message.error(`加载失败：${errorText(e)}`);
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

  const setDraftValue = (code: string, index: number, value: number | null) => {
    setDrafts((prev) => {
      const next = { ...prev, [code]: [...(prev[code] ?? [])] };
      next[code][index] = value === null ? Number.NaN : value;
      return next;
    });
  };

  const baseline = (item: RewardItem) => item.pending?.amounts ?? item.current.amounts;

  const isDirty = (item: RewardItem) => {
    const draft = drafts[item.code];
    if (!draft || draft.length !== item.size) return true;
    return !sameAmounts(draft, baseline(item));
  };

  const resetDraft = (item: RewardItem) => {
    setDrafts((prev) => ({ ...prev, [item.code]: [...baseline(item)] }));
  };

  const save = async (item: RewardItem) => {
    const draft = drafts[item.code] ?? [];
    if (
      draft.length !== item.size ||
      draft.some((v) => !Number.isInteger(v) || v < 1 || v > MAX_AMOUNT)
    ) {
      message.error(`${item.name}需要 ${item.size} 个 1-${MAX_AMOUNT} 之间的整数金币数`);
      return;
    }
    setSavingCode(item.code);
    try {
      const row = await api<{ effective_on: string }>("/admin/v1/rewards/config", {
        method: "POST",
        body: JSON.stringify({ code: item.code, amounts: draft }),
      });
      message.success(`${item.name}已保存，将于 ${row.effective_on}（洛杉矶时间）生效`);
      await load();
    } catch (e) {
      message.error(`保存失败：${errorText(e)}`);
    } finally {
      setSavingCode(null);
    }
  };

  const amountText = (amounts: number[]) =>
    amounts.map((v) => (Number.isFinite(v) ? `+${v}` : "—")).join(" / ");

  const currentText = (item: RewardItem) => {
    const { amounts, effective_on, is_default } = item.current;
    const suffix = is_default || !effective_on ? "内置默认" : `${effective_on} 起`;
    return `${amountText(amounts)}（${suffix}）`;
  };

  const pendingTag = (item: RewardItem, withLabel = false) => {
    if (!item.pending) return <Typography.Text type="secondary">-</Typography.Text>;
    const changed = !sameAmounts(item.pending.amounts, item.current.amounts);
    return (
      <Space direction="vertical" size={2}>
        <Space size={6}>
          {withLabel ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              待生效
            </Typography.Text>
          ) : null}
          <Tag color="orange">{item.pending.effective_on} 生效</Tag>
        </Space>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {amountText(item.pending.amounts)}
          {!changed ? "（与当前一致）" : ""}
        </Typography.Text>
      </Space>
    );
  };

  const changeColumns = useMemo(
    () => [
      { title: "时间", dataIndex: "created_at", render: (v: string) => formatTime(v), width: 170 },
      {
        title: "任务",
        dataIndex: "code",
        render: (code: string) => items.find((item) => item.code === code)?.name ?? code,
        width: 180,
      },
      {
        title: "金额",
        dataIndex: "amounts",
        render: (amounts: number[]) => amountText(amounts ?? []),
      },
      { title: "生效日期", dataIndex: "effective_on", width: 130 },
      { title: "操作人", dataIndex: "actor", width: 130 },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [items]
  );

  const singleColumns = [
    {
      title: "任务",
      dataIndex: "name",
      render: (_: unknown, item: RewardItem) => (
        <Space direction="vertical" size={2}>
          <Space size={6}>
            <Typography.Text strong>{item.name}</Typography.Text>
            <Tag>{item.code}</Tag>
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {item.description}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "当前生效",
      dataIndex: "current",
      width: 240,
      render: (_: unknown, item: RewardItem) => <Typography.Text>{currentText(item)}</Typography.Text>,
    },
    {
      title: "待生效",
      width: 190,
      render: (_: unknown, item: RewardItem) => pendingTag(item),
    },
    {
      title: "调整（金币）",
      width: 300,
      render: (_: unknown, item: RewardItem) => (
        <Space>
          <InputNumber
            min={1}
            max={MAX_AMOUNT}
            precision={0}
            style={{ width: 120 }}
            value={drafts[item.code]?.[0]}
            onChange={(value) => setDraftValue(item.code, 0, value)}
            disabled={savingCode === item.code}
          />
          <Button
            type="primary"
            size="small"
            disabled={!isDirty(item)}
            loading={savingCode === item.code}
            onClick={() => save(item)}
          >
            保存
          </Button>
          <Button size="small" disabled={!isDirty(item)} onClick={() => resetDraft(item)}>
            撤回
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={16}>
      <Space direction="vertical" size={4}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          金币与任务
        </Typography.Title>
        <Typography.Text type="secondary">
          维护签到梯度与一次性/每日任务奖励。保存后按「次日生效」执行，当天已发放的奖励不重算。
        </Typography.Text>
      </Space>

      <Alert
        type="info"
        showIcon
        message={`现在保存的改动将于 ${overview?.next_effective_on ?? "-"}（洛杉矶时间）生效`}
      />

      <Card
        title={checkin ? checkin.name : "7 天签到梯度"}
        extra={
          checkin ? (
            <Space>
              <Typography.Text type="secondary">{checkin.description}</Typography.Text>
            </Space>
          ) : null
        }
        loading={loading && !checkin}
      >
        {checkin ? (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Space wrap size={12}>
              {(drafts[checkin.code] ?? []).map((value, index) => (
                <Space key={index} direction="vertical" size={2}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    第 {index + 1} 天
                  </Typography.Text>
                  <InputNumber
                    min={1}
                    max={MAX_AMOUNT}
                    precision={0}
                    style={{ width: 104 }}
                    value={Number.isFinite(value) ? value : null}
                    onChange={(next) => setDraftValue(checkin.code, index, next)}
                    disabled={savingCode === checkin.code}
                  />
                </Space>
              ))}
            </Space>
            <Space wrap size={16}>
              <Typography.Text>当前生效：{currentText(checkin)}</Typography.Text>
              {pendingTag(checkin, true)}
            </Space>
            <Space>
              <Button
                type="primary"
                disabled={!isDirty(checkin)}
                loading={savingCode === checkin.code}
                onClick={() => save(checkin)}
              >
                保存签到梯度
              </Button>
              <Button disabled={!isDirty(checkin)} onClick={() => resetDraft(checkin)}>
                撤回未保存修改
              </Button>
              {checkin.current.is_default ? (
                <Tooltip title="数据库还没有该任务的生效配置，当前展示的是内置默认值">
                  <Tag>内置默认</Tag>
                </Tooltip>
              ) : null}
            </Space>
          </Space>
        ) : (
          <Typography.Text type="secondary">暂无配置</Typography.Text>
        )}
      </Card>

      <Card title="任务奖励" loading={loading && singles.length === 0}>
        <Table
          rowKey="code"
          dataSource={singles}
          columns={singleColumns}
          pagination={false}
          size="middle"
        />
      </Card>

      <Card title="变更记录" size="small">
        <Table
          rowKey="id"
          dataSource={overview?.recent_changes ?? []}
          columns={changeColumns}
          pagination={false}
          size="small"
          locale={{ emptyText: "暂无变更记录" }}
        />
      </Card>
    </Space>
  );
}
