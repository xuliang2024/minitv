# 用户管理 · 会员开通 / 取消（后台整改方案）

> 依据：`main@104f388` 代码 + 2026-10-20 线上后台实际表现。
> 关联文档：`docs/membership-coins-unlock.md`（会员权益判定 / 购买链路）。

## 0. 一句话结论

后台「用户管理」现在**只能看会员、不能改**（`flow_router.py` 只有列表 / 改资料 / 调金币三个用户接口），客服补偿、测试号开通、误购收回都只能进 Stripe 后台或直接改库。本方案补 **3 个接口 + 1 个弹窗**，顺手修掉列表上已经能看见的一个读数错误。

## 1. 现状问题

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| 1 | 没有任何会员写接口 | `ops/flow_router.py:1755`(list) / `:1793`(patch) / `:1859`(coins) | 客服无法补偿会员，只能改库（无审计、易错） |
| 2 | 会员列读数错误 | `flow_router.py:1772-1786` 用 `status='active'` + `current_period_end` | 一次性时长包用户 `current_period_end` 停留在购买时刻（`billing/service.py:753-754`），页面显示「有效 至 过去时间」；已过期未回收的 active 行仍显示「有效」 |
| 3 | 分不清钱从哪来 | 同上 | 客服不敢动，怕误伤真实付费用户 |
| 4 | 取消要动 Stripe | `billing/service.py:326` `cancel_subscription` | 本地置 `canceled` 不停止 Stripe 扣费 → 「收了钱不给权益」 |
| 5 | C 端禁取消一次性包 | `billing/service.py:333-334` 抛 400 | 后台收回必须走 force 路径，不能复用 C 端函数 |

## 2. 设计约束（先认同这几条，否则实现必错）

1. **权益唯一真源**：`status='active'` 且 `max(current_period_end, pass_end) > now`（`access_until` `service.py:142`、`active_membership` `:194`）。所有播放 / 解锁判定都读它（`playback/service.py:100`、`wallet/service.py:93`）。→ 开通 / 取消**只动这两个时间窗**，不碰别处。
2. **一个用户最多一行 `active`**（部分唯一索引 `uq_subscriptions_one_active_per_user`）。→ 开通必须「先查 active 行，没有才 INSERT」，否则 500。
3. **开通的基数是 `max(now, pass_end, current_period_end)`**，与购买一次性包一致（`service.py:760`），绝不烧掉剩余天数。
4. **有 `stripe_subscription_id` 的行不要改 `plan_id` / `plan_version_id`**：webhook `_sync_from_subscription` 会用 Stripe metadata 覆盖回来，造成套餐名自己跳变。只叠 `pass_end`。
5. **取消 Stripe 订阅必须先打 Stripe**，成功才改本地；远程失败直接 502 回滚。
6. **赠送不产生订单**：不建 Price、不走 Checkout、不写 `orders`。否则污染 GMV / 财务对账（`/finance` 页与 dashboard 同源）。

## 3. 接口设计

权限统一 `require_admin_role`（超管，与「调金币」「改套餐价」一致）；只读详情用 `require_admin`（运营也能看）。

### 3.1 查询会员

```
GET /admin/v1/users/{user_id}/membership
```

```json
{
  "user": { "id": 12, "public_id": "9356346372", "display_name": "ruifeng2022", "email": "..." },
  "active": true,
  "plan_code": "monthly",
  "plan_name": "月卡",
  "until": "2026-11-19T03:21:00+00:00",
  "subscription_until": "2026-10-25T03:21:00+00:00",
  "pass_until": "2026-11-19T03:21:00+00:00",
  "canceling": false,
  "source": "one_time",            // subscription | one_time | admin_grant
  "billing_mode": "one_time",
  "stripe_subscription_id": null,  // 只下发布尔更稳：has_stripe_subscription
  "history": [
    { "id": 88, "plan_code": "monthly", "status": "active", "start": "...", "end": "...", "pass_end": "...", "canceled_at": null }
  ]
}
```

> `source` 为 `admin_grant` 需要 `subscriptions` 加 `source` 列（见 §8 P2）；不加时按 `stripe_subscription_id` + `billing_mode` 推断，先返回 `subscription` / `one_time`。

### 3.2 开通 / 续期

```
POST /admin/v1/users/{user_id}/membership
{ "plan_code": "monthly", "days": 30, "mode": "extend", "reason": "客诉补偿：10-20 播放故障" }
```

- `days`：可省略，缺省取 `PLAN_DAYS[plan_code]`（7/30/365）；上限 3650，0 或负数 422。
- `mode`：`extend`（默认，从 `max(now, 两个窗口)` 顺延）/ `restart`（从 now 起算，会覆盖剩余天数，前端必须二次确认）。
- 返回 `{ "before_until": "...", "after_until": "...", "added_days": 30, "plan_code": "monthly" }`。

规则：

```
row = active subscription (FOR UPDATE)      # 无则 INSERT status=active, billing_mode='one_time'
base = max(now, pass_end, current_period_end)  if mode == 'extend' else now
row.pass_end = base + days
row.billing_mode 保持原值（有 Stripe 订阅时仍是 subscription）
if not row.stripe_subscription_id: row.plan_id/plan_version_id = 所选套餐的最新版本
AuditLog(action='membership_grant', payload={before_until, after_until, plan_code, days, mode, reason})
```

### 3.3 取消会员

```
DELETE /admin/v1/users/{user_id}/membership
{ "mode": "now", "reason": "误开：测试号", "refunded": false }
```

- `mode = period_end`（到期不续费）：只停自动续费，权益保留到窗口结束。等价于 H5 的取消。
- `mode = now`（立即收回）：两个窗口都压到 `now`，用户下一集就要金币。

规则：

```
若 stripe_subscription_id:
    period_end → POST /v1/subscriptions/{id} {cancel_at_period_end:true}
    now        → DELETE /v1/subscriptions/{id}   # 远程失败 502，不改本地
    canceled_at = now
mode == now:
    current_period_end = pass_end = now
    若两侧都已无未来窗口 → status='canceled'
else（纯时长包 / 赠送）:
    mode == now → pass_end = now；无其他窗口则 status='canceled'
    mode == period_end → 409「时长包没有续费概念，请选择立即收回」
AuditLog(action='membership_revoke', payload={mode, before_until, after_until, stripe_canceled, reason})
```

### 3.4 错误码

| 场景 | 状态码 |
|---|---|
| 用户 / 套餐不存在 | 404 |
| 无有效会员却取消 | 409 |
| 时长包选 `period_end` | 409 |
| `days` 越界、`reason` 空、`mode` 非法 | 422 |
| Stripe 取消失败 | 502（本地不动） |

## 4. 前端改造（`admin/src/pages/StagePages.tsx`）

### 4.1 列表

- `AdminUserRow` 增 `canceling` / `billing_mode` / `pass_until`。
- 「会员」列改**三态 + 来源**：`有效`（绿）/ `到期不续`（橙，`canceling`）/ `无`（灰）；有效时右侧小字 `至 YYYY-MM-DD`；来源小标签 `订阅` / `时长包` / `赠送`。整列可点，直接开弹窗。
- 操作列加第三个按钮「会员」（会员有效时仍是「会员」，图标点绿点区分），列宽 140 → 190；非管理员 `disabled` + tooltip「仅管理员可操作」，与「调账」保持一致。
- 「无」状态那一行的按钮文案改为 `开通`，颜色用 `type="link"` 强调。

### 4.2 `MembershipModal` 结构

| 区 | 内容 | 交互 |
|---|---|---|
| A 当前状态 | 状态标签 · 到期时间 · 来源 · 订单号（如有） | 只读；`canceling` 时补一句「Stripe 已设为到期不续费」 |
| B 开通 / 续期 | 套餐 `周卡 / 月卡 / 年卡` 分段控件 · 天数 `InputNumber`（默认随套餐） · 方式 `顺延 / 从今天重算` · 原因 | 底部实时预览 `2026-10-30 → 2026-11-29（+30 天）`；`restart` 选中时预览变红提示「将覆盖剩余 N 天」 |
| C 危险操作 | 折叠区，`取消会员` 红色文字按钮 | 选择 `立即收回 / 到期不续费` + 原因，二次确认（勾选「我确认收回该用户权益」） |

- 保存成功：`message.success("已开通 30 天，到期 2026-11-29")` + 列表 `reload()`。
- 有 Stripe 订阅时，C 区固定一行警示：**会同时取消 Stripe 自动续费；退款需到 Stripe 后台手动处理，本操作不退款。**
- 弹窗宽 560，与「编辑用户」一致；移动端 `width="100%"`。

### 4.3 文案

后台全中文，按钮用动词：「确认开通 / 确认收回」。避免「订阅」一词同时表示产品和动作，B 区标题统一叫「开通 / 续期」。

## 5. 必须先修的读数 bug（P0，10 行内）

`list_users`（`flow_router.py:1772-1786`）改为复用 `active_membership(db, row.id)`，并补 `canceling` / `billing_mode`：

```python
membership = active_membership(db, row.id)
"member": membership["active"],
"member_until": membership["until"],
"canceling": membership["canceling"],
"billing_mode": membership["billing_mode"],
```

理由：截图里 `ruifeng2022` 显示「有效 至 2026-10-30 11:21」——该用户是一次性包，`current_period_end` 只是购买时刻的快照，真实到期在 `pass_end`；一旦两个值不同就会出现「有效但日期已过」。另外 `status='active'` 但窗口全过的行会一直显示「有效」。

## 6. 验收用例

| # | 场景 | 期望 |
|---|---|---|
| 1 | 无会员用户开通月卡 | `until = now+30d`，列表变「有效」，订单表**不新增行** |
| 2 | 剩余 20 天再开 30 天（顺延） | `until = 原到期 + 30d`，不出现 20 天被吞 |
| 3 | 同上但选「从今天重算」 | `until = now+30d`，前端有红色覆盖提示 |
| 4 | 有 Stripe 订阅用户开通 | `pass_end` 顺延，`plan_id` 不变，Stripe 侧无任何调用（看日志无 `/v1/subscriptions`） |
| 5 | 订阅用户「到期不续费」 | Stripe `cancel_at_period_end=true`，权益仍在，列表显示「到期不续」 |
| 6 | 订阅用户「立即收回」 | Stripe `DELETE`，`/v1/me.membership.active=false`，再播放锁集返回需解锁 |
| 7 | 纯时长包「立即收回」 | `pass_end=now`，`active=false`，`/v1/unlock` 恢复扣金币 |
| 8 | 纯时长包选「到期不续费」 | 409 |
| 9 | 无会员用户取消 | 409 |
| 10 | 运营账号调用 3 个写接口 | 403；读详情 200 |
| 11 | 两个人同时点开通 | 行锁串行，天数只加一次（可用两个并发请求验） |
| 12 | 审计 | 每次开通/取消都有 `audit_logs` 行，payload 含前后到期时间与原因 |

## 7. 实施顺序

| 阶段 | 内容 | 说明 |
|---|---|---|
| P0 | §5 列表读数修正 + `GET membership` | 只读，先上线就能让客服看清真实到期 |
| P1 | `POST` / `DELETE` 接口 + 单测（新增 `backend/tests/test_admin_membership.py`，参考 `test_stages_3_15.py:491-560` 的用户段） | 只加行、不改表 |
| P2 | 前端 `MembershipModal` + 列表三态 | 依赖 P0 字段 |
| P3（可选） | `subscriptions` 加 `source` / `granted_by` 迁移 | 才需要 alembic + 新 revision；不加也能跑，只是「赠送」标签靠 audit_log 反查 |

风险点：写接口会动线上付费用户的权益，**必须按 P0→P1→P2 分开上线**，每步发版后用测试号 #17 走一遍 §6 的 5/6/7 三条。

## 8. 明确不做

- 不做「后台直接建 Stripe 订阅/订单」：会污染 GMV、客户在 Stripe 门户能看到赠送记录。
- 不做自动退款：`refunded` 由 Stripe 后台触发，`orders.status` 由 webhook 回写。
- 不做批量开通：先保证单人链路正确。

---

## 9. 实施记录（2026-10-20）

已按 P0 → P1 → P2 落地；P3（`subscriptions.source` 列）按「不加也能跑」跳过。

| 项 | 落点 |
|---|---|
| 列表读数修正 | `ops/flow_router.py` `list_users` 改用 `billing.active_membership()`，新增 `canceling` / `billing_mode` |
| 会员逻辑 | `billing/service.py`：`membership_detail` / `grant_membership` / `revoke_membership` |
| 接口 | `GET/POST/DELETE /admin/v1/users/{user_id}/membership`（读 `require_admin`，写 `require_admin_role`） |
| 前端 | `admin/src/pages/StagePages.tsx`：`MembershipModal` + 会员列三态 + 操作列第三项 |
| 测试 | `backend/tests/test_admin_membership.py`（14 条，含列表回归） |

实施中的三处调整：

1. **取消区默认折叠**（标题「危险操作」+ 右侧「取消会员」链接）。展开后弹窗会超出 900px 视口，折叠后首屏完整可见，也少一次误点。
2. **确认按钮文案改「确认收回」**：与折叠入口「取消会员」区分，避免两个同名红色按钮。
3. **`_stripe_request` 修了一处**：原实现 `payload is None` 时无条件把方法改成 GET，`DELETE /v1/subscriptions/{id}` 根本发不出去；改成只对默认 POST 生效。

`source` 未加列：详情用 `stripe_subscription_id` + `billing_mode` 推 `subscription` / `one_time`，另从 `audit_logs` 取最近一次 `membership_grant` / `membership_revoke` 作为「最近后台调整」，足够客服判断谁在什么时候给过什么。

验证：后端 `pytest` 全量 303 passed；`admin` 的 `tsc --noEmit` + `vite build` 通过；本地 Playwright 走完「开通 30 天 → 列表变有效 → 展开危险区 → 勾选 → 立即收回 → 列表回无」，无 pageerror。
