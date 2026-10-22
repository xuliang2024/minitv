# 上剧方 P4 实施与测试报告

> 范围：`docs/supplier-role-plan.md` P4（数据中心、月度结算单）
> 日期：2026-10-22　环境：本地（Docker Postgres/Redis，API :18110，Vite :5173）
> 结论：**P4 功能完成，测试全绿，已上线（admin v2026.10.22.5 / server v2026.10.22.6）。**

## 1. 本阶段交付

| # | 内容 | 说明 |
|---|---|---|
| 1 | 数据中心接口 | `GET /partner/v1/stats/overview?days=7|30|90` |
| 2 | 分剧数据 | `GET /partner/v1/stats/dramas`，按剧聚合播放/完播/用户/解锁/首单/收益 |
| 3 | 月度结算表 | `supplier_settlements`（供应商 + 月份唯一），状态 `draft/confirmed/paid` |
| 4 | 结算生成 | `POST /admin/v1/settlements/generate`，按自然月重算，跳过已确认/已打款 |
| 5 | 结算列表 | admin：按月/状态查询；partner：`GET /partner/v1/settlements` |
| 6 | 状态流转 | admin 确认 → 标记打款；只能前进，不能回退 |
| 7 | 网页 | partner「数据中心」「结算单」；admin「结算单」（finance/admin） |

## 2. 口径与算法

### 2.1 数据口径

| 指标 | 来源 | 规则 |
|---|---|---|
| 播放量 | `playback_events.kind='start'` | 真实起播事件，不用 `dramas.play_count` |
| 完播率 | `complete / start` | 同一时间窗 |
| 观看用户 | `start` 事件 `user_id` 去重 | 匿名播放不计 |
| 金币解锁 | `coin_ledger` 负向 `biz_type='unlock'` | **只做内容指标，不参与分成** |
| 会员首单 | `orders` | 每个用户**有史以来第一笔**已支付订单，且 `source_drama_id` 属于该供应商 |
| 收益 | `amount × 支付时生效的分成比例` | 逐单计算后求和，不做「总额 × 当前比例」 |

### 2.2 首单 + 退款的边界

首单用窗口函数取 `row_number() over (partition by user_id order by paid_at, id)`，**不按月份重置**：

- 用户第一单是自然流量（`source_drama_id IS NULL`）→ 后续再买该供应商的剧也不算首单；
- 第一单退款（`refunded_at` 非空）→ 下一轮生成时自然消失；
- 已确认/已打款的月份不会被重算覆盖，退款只影响草稿月份。

### 2.3 分成按支付时点

`supplier_share_history` 是生效区间表，逐单取 `effective_from ≤ paid_at < effective_to` 的 `share_bps`；月中改比例会把当月拆成两段。`effective_share_bps` 用**金额加权**，避免 `revenue/gross` 的整数回算误差（1999 分 × 80% = 1599 分，回算会得 7998bps）。

## 3. 测试结果

```
cd backend && ../.venv/bin/python -m pytest -q
414 passed, 1 warning in 112.16s
```

新增 `tests/test_partner_p4.py` **8 条**：

| 组 | 用例 |
|---|---|
| 概览 | 播放/完播/完播率/观看用户/解锁/解锁人数正确；首单只算 1 笔；收益 1599 分 |
| 隔离 | 自营剧的播放不计入供应商数据 |
| 结算 | 生成 1 条草稿、金额与订单数正确；partner 能读到自己的对账单 |
| 分成 | 月中 80%→70%，两笔各 1000 分 → 收益 1500 分、加权比例 7500bps |
| 锁定 | 确认后重新生成 `skipped=1`；打款成功；回退确认 409 |
| 权限 | ops 读结算 403，finance 200 |
| 退款 | 首单退款后草稿重算为 0 分 / 0 单 |

### 3.1 浏览器验收（Playwright，真实点击）

```
partner 侧栏          = 我的剧集 / 数据中心 / 结算单 / API 密钥
数据中心              = 播放 2 · 完播率 50.0% · 观众 2 · 解锁 1
                        首单 2 · $29.98 · 收益 $23.98（80%）
结算单（partner）     = 2026-10 · $23.98 · 待确认
结算单（finance）     = 行含供应商与金额；点「确认」→「标记打款」→ 已打款
partner 回看          = 已打款
```

截图：`/tmp/partner-stats.png`、`/tmp/partner-settlements.png`、`/tmp/admin-settlements-paid.png`。

## 4. 已知边界

1. **只结算 USD**：`orders.currency` 非 USD 时金额仍按分累加，未做汇率换算（当前 Stripe 全 USD，暂不触发）。
2. **自然月按 UTC**：月末 00:00 UTC 切分；若以后要按北京时间结算需改 `month_bounds`。
3. **退款只减当月草稿**：已确认月份不回溯，需人工开调整单（P5 可加 `adjustments`）。
4. **没有打款凭证/发票**：`paid` 只是一个状态，未对接银行或生成 PDF 对账单。
5. **统计窗口上限 90 天**：与方案「明细保留 90 天」一致，更长周期需另做归档表。

## 5. 上线注意

- 新迁移 `e5b2a9c741d3`（仅建 `supplier_settlements` 表），部署时自动执行。
- 结算读取 `orders.source_drama_id`，P1 之前的存量订单为 NULL，不会计入任何供应商。
- 建议先在线上跑一次空月份生成（无订单 → 0 条），确认无异常后再等真实订单。

## 6. 线上验收（2026-10-22 11:28）

发布：admin `v2026.10.22.5`、server `v2026.10.22.6`；迁移 `d3f8b1c6a940 → e5b2a9c741d3`。
迁移前备份：`/opt/minitv/backups/pre-p4-20261022-1125.dump`（1.2 MB）。

| # | 检查 | 结果 |
|---|---|---|
| 1 | `alembic_version` | `e5b2a9c741d3` ✅ |
| 2 | `supplier_settlements` 表 | 存在，0 行 ✅ |
| 3 | finance `GET /admin/v1/settlements` | 200，空列表 ✅ |
| 4 | ops 同接口 | 403 `role cannot access settlements` ✅ |
| 5 | finance 生成当月（无订单） | `created=0 / updated=0 / skipped=0` ✅ |
| 6 | partner `stats/overview` | 200，播放/观众/首单/金额全 0 ✅ |
| 7 | partner `stats/dramas`、`settlements` | 200，空数组 ✅ |
| 8 | partner 线上菜单 | 我的剧集 / 数据中心 / 结算单 / API 密钥 ✅ |
| 9 | finance 线上菜单 | 含「结算单」，无「剧集管理」✅ |
| 10 | 线上结算页生成按钮 | toast「生成完成」✅ |

验收数据已清理：`suppliers=0 / admin_users=3 / dramas=212 / settlements=0`。

## 7. 结论

P4 补齐了「内容 → 转化 → 收益」的完整可见性，并把分成结算做成可复核的月度对账单。至此方案 P1–P4 全部落地，仅剩运营侧细节（撤回提交、SLA 提醒、排期守卫）可作为 P5 打磨。
