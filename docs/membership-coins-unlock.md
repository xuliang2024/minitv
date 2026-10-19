# 会员权益 / 金币获取 / 分集解锁 逻辑梳理

> 依据：当前 `main` 分支代码 + 线上库快照（2026-10-19 16:40，`YOUR_SERVER_IP`）。
> 结论优先，后面是判定顺序、数据现状和缺口清单。

## 0. 一句话结论

- **会员唯一真实生效的权益是「全站已发布剧集免锁」**；无广告/1080P/下载/优先客服要么无差异，要么没实现。
- **金币解锁链路后端完整，但客户端没有入口**：H5 和 App 的锁屏只显示价格，`entitlements` 线上为 **0 行**，从未发生过一次金币解锁。
- 当前是**一次性时长包**模式（`billing_settings.billing_mode = one_time`），线上周卡实际价格是 **$0.50 / 7 天**（测试价仍在生产）。
- 金币只进不出（除解锁），23 个钱包合计 **6,500 币**；每日任务（点赞/收藏/看完）线上 **0 次领取**。

---

## 1. 会员

### 1.1 三档套餐（线上最新版本）

| 套餐 | code | 最新版本 | 价格 | 时长 | 线上计费类型 | Stripe 价格 | best 角标 |
|---|---|---|---|---|---|---|---|
| 周卡 | `weekly` | v6 | **$0.50** | 7 天 | 一次性时长包 | 仅一次性价，**无订阅价** | 否 |
| 月卡 | `monthly` | v1 | $19.99 | 30 天 | 一次性时长包 | 双价齐全 | 是 |
| 年卡 | `yearly` | v1 | $79.99 | 365 天 | 一次性时长包 | 双价齐全 | 否 |

- 周卡价格历史：v1 $9.99 → v2 $0.10 → v3 $0.50 → v4 $0.20 → v5 $0.50 → v6 $0.50。月/年从未改过价。
- 计费模式是**全局开关**（`billing_settings` 单行表，默认 `one_time`），只影响**新购买**；已产生的订阅继续自动续费。
- 时长是硬编码：`weekly=7 / monthly=30 / yearly=365`（`billing/service.py: PLAN_DAYS`）。后台页面写「维护价格、有效期」，但有效期不可配。

### 1.2 权益清单与真实生效情况

| 权益（套餐卡文案） | 后端实现 | 实际结论 |
|---|---|---|
| 全站剧集解锁（`Unlock all`） | `playback.authorize` 判定会员直接放行 | ✅ **真实生效**，覆盖所有已发布剧集（含后续新增集） |
| 无广告（`No ads`） | 产品内没有任何广告位/广告 SDK | ⚠️ 无差异：所有人本来就无广告 |
| 1080p（`1080p`） | 仅当环境变量 `MEMBER_ONLY_1080=true` 时，`renditions_for` 从提示列表移除 1080P；线上未设置 | ⚠️ **不生效**：非会员也能看 1080P；且 H5 清晰度菜单来自 HLS manifest（`hls.js levels`），根本不读 `renditions` |
| 下载（`Download`，月卡） | 后端 / H5 / Flutter 均无下载代码 | ❌ 未实现 |
| 优先客服（`Priority support`，年卡） | 无任何实现 | ❌ 未实现 |

补充：会员**不送金币**，会员期间金币也不消耗（解锁直接走会员优先分支）。

### 1.3 权益文案存在哪

| 位置 | 数据源 | 说明 |
|---|---|---|
| 套餐卡 | `plan_versions.benefits`：`{items: string[], best: bool}` | 后台「会员套餐」页编辑（每行一条），改价/改权益都会新建版本；`GET /v1/plans` 下发，H5 `PremiumSheet` 原样渲染 |
| 已开通状态卡 | H5 硬编码三条：`Every episode unlocked / No ads / 1080p streaming` | 与套餐无关，见 `h5/src/ui/PremiumSheet.tsx:137-141` |
| 中英文案 | `h5/src/copy.ts` | `premium_*` 系列键 |

线上月卡权益是 `Weekly benefits / Download`、年卡是 `Monthly benefits / Priority support / Save 58%`——这是**叠加式表述**（月卡包含周卡全部权益），不是配置错误。

### 1.4 会员状态机

- 表 `subscriptions`，**一个用户最多一行 `status='active'`**（部分唯一索引 `uq_subscriptions_one_active_per_user`）。
- 权益时间窗：`access_until = max(current_period_end, pass_end)`，只取 `> now()` 的候选（`billing/service.py:142`）。行 `status` 必须为 `active`。
- `/v1/me.membership` 返回：`active / plan_code / until / canceling / billing_mode / subscription_until / pass_until`。
  - `until` = 两个窗口里更晚的那个。
  - `canceling` 仅在「订阅窗口是最后窗口且已设 `canceled_at`」时为 true。
  - `billing_mode` 描述的是**当前生效窗口**的类型，不是全局开关。
- **一次性时长包叠加**：`base = max(now, pass_end, current_period_end) + N 天`。订阅未到期时买时长包也从订阅到期日往后叠，不会烧掉剩余天数。
- **取消**：
  - 订阅：调 Stripe `cancel_at_period_end=true`，`canceled_at` 标记「不再续费」，到期前权益保留，到期由 `customer.subscription.deleted` 置 `canceled`。
  - 一次性：**不可取消**（返回 400），只能再买叠加。
- **Webhook 幂等**：`Order.external_txn_id` 唯一；一次性包重复事件按 `order.status='paid'` 直接返回 `replayed`，不会重复加天数。

### 1.5 购买链路

```
POST /v1/billing/checkout（需登录）
  → 取最新 plan_version，按全局 billing_mode 选 price（ensure_stripe_price）
  → 建本地 order(status=created) + Stripe Checkout（metadata 带 user_id/plan_code/billing_mode）
  → 用户在 Stripe 付款
  → POST /v1/billing/stripe/webhook
       checkout.session.completed(mode=payment)      → _activate_pass（叠 pass_end）
       checkout.session.completed(mode=subscription) → _activate_from_checkout（拉订阅周期）
       customer.subscription.created/updated         → _sync_from_subscription（全量快照 upsert）
       customer.subscription.deleted                 → 取消；若 pass_end 仍有效则保持 active
```

- 一次性 Checkout 有 Stripe 最低价限制 **$0.50 USD**（订阅不受限），后端 `ensure_chargeable` 提前拦截。
- Basil API 起，订阅周期在 `items.data[0].current_period_start/end`，统一走 `_period_bounds()` 读取。
- 订单历史 `GET /v1/me/orders` 只返回 `paid / refunded`，不含 `created`（放弃的收银台）。

---

## 2. 金币（积分）

### 2.1 账本模型

- `wallet_accounts.balance`：物化余额，`CHECK (balance >= 0)`。
- `coin_ledger`：一条变动一行，`amount != 0`，`biz_key` **全局唯一**（`biz_type` 仅作分类）。
- 消耗唯一入口：`POST /v1/unlock`。没有转账、没有过期、没有兑换。

### 2.2 获取渠道（线上生效配置）

| 渠道 | code | 金额 | 频率 / 条件 | 客户端入口 | 线上累计 |
|---|---|---|---|---|---|
| 7 天签到 | `checkin` | 30 / 30 / 50 / 50 / 30 / 60 / 90 | 每日一次，连续第 1-7 天；断签或第 8 天回到第 1 天 | H5 任务中心 | 77 次，+3,060 |
| 注册 | `register` | 80 | 首次建号一次性（邮箱或 Google） | 自动发放 | 23 次，+1,840 |
| 绑定邮箱 | `bind_email` | 30 | 首次绑定一次性 | **无接口，不可达** | 5 次 +150（历史 seed 数据） |
| 绑定 Facebook | `bind_facebook` | 80 | 首次绑定一次性 | **无接口，不可达** | 4 次 +320（历史 seed 数据） |
| 每日广告 | `ad` | 30 | 每天一次 | H5 无入口；Flutter 骨架页有一个裸按钮（`app/lib/screens/account.dart`） | 0 |
| 点赞 5 部剧 | `like_5` | 20 | 每天，5 部**不同**剧 | H5 任务中心 | 0 |
| 收藏 5 部剧 | `favorite_5` | 30 | 每天，5 部**不同**剧 | H5 任务中心 | 0 |
| 看完 8 集 | `watch_8` | 50 | 每天，8 集**不同**剧集（`complete` 且 `position>0`） | H5 任务中心 | 0 |
| 后台调账 | `adjustment` | 手工 | 加/减，余额不能为负，必须填原因 | 后台用户页 | 6 笔，-40 |

线上钱包：**23 个用户，合计 6,500 币**。最近一笔流水 2026-10-19 16:20。

### 2.3 规则细节

- **业务日 = America/Los_Angeles 自然日**（`account/service.py: LA`），与用户本地时区无关；所有「今日」判断、签到、任务、配置生效日都按 LA 切。
- **配置次日生效**：后台改金额写 `reward_configs.effective_on = 明天`，当天领取仍按旧值；领取时读当时生效值，避免改价重写已发奖励。
- **幂等**：
  - 签到：`checkins (user_id, day)` + ledger `checkin:{uid}:{date}`；重复签到返回 `replayed=true, amount=0`。
  - 任务：`task_claims (user_id, code, period_key)` 唯一；ledger `task:{code}:{uid}:{date}`；并发领取只有一个赢。
  - 广告：`ad_claims (user_id, day)`；重复返回 409。
  - 调账：`biz_key` 带时间戳，**不幂等**，重复提交会重复加减。
- **进度按对象去重**：同一部剧反复点赞/取消只算 1 部；同一集重复上报 complete 只算 1 集。
- **金币不过期**，没有有效期字段；也没有「会员到期金币清零」之类逻辑。
- 会员期间仍可正常赚币，只是花不出去（解锁会走会员分支）。

### 2.4 可领取但拿不到的奖励

`bind_provider()` 在 `account/service.py:510` 有实现，但**没有任何路由调用它**，所以「绑定邮箱 +30 / 绑定 Facebook +80」在当前产品里不可达。广告奖励是「接口在、H5 无入口」；Flutter 骨架的奖励页有一个未做状态反馈的裸按钮。

---

## 3. 解锁分集内容

### 3.1 一套规则，两处使用

三条件任一满足即可看：

```
free     = episode.episode_number <= drama.free_episode_count
member   = access_until(user) > now          # 订阅窗口或时长包，取更晚者
unlocked = entitlements(user, episode, 'coin') 存在
allowed  = free || member || unlocked
```

| 使用点 | 接口 | 作用 |
|---|---|---|
| 播放页数据 | `GET /v1/dramas/{id}/play` | 给每集打 `free / locked`，锁定集附 `unlock.coin_price`；同时算续播点 |
| 播放鉴权 | `POST /v1/playback` | 同一套判定；通过才签发 **5 分钟** HMAC 播放 URL（Bunny Stream 签名或自建 CDN token） |

- 未通过时返回 `allowed=false, reason=locked, unlock={coin_price, membership_available:true}`；剧/集未发布返回 `not_available`（404）；地区白名单不匹配返回 `region_blocked`。
- 单集价格在**剧维度**：`dramas.coin_price_per_episode`，同一部剧所有付费集同价。线上主流是 50 币，少量 58/59/69/79/80，另有 3 部为 0。
- 免费集数是**按集号**算的：`episode_number <= free_episode_count`，不是按发布时间或排序。

### 3.2 会员解锁（隐式，不落库）

- 不写 `entitlements`，每次请求实时算 `access_until`。
- 效果：播放页 `locked=false`、`/v1/playback` 直接放行；会员到期后所有未用币解锁的集**自动回到锁定**。
- 会员调用 `/v1/unlock` 不扣币，返回 `status="subscription", source="subscription", balance_after=null`（仍写幂等记录）。

### 3.3 积分解锁（显式）

`POST /v1/unlock`，**必须带 `Idempotency-Key`**（缺失直接 400）。

判定顺序（`wallet/service.py:55`）：

1. **幂等重放**：key 命中 `idempotency_records` → 原样返回首次结果 + `replayed=true`，不重复扣。
2. **取价**：`episode → drama.coin_price_per_episode`。
3. **会员优先**：`access_until != null` → `status=subscription`，**绝不产生金币流水**。
4. **已解锁**：已有 `coin` entitlement → `status=already_unlocked`，不扣币（覆盖换 key 的重试）。
5. **扣款**：`SELECT ... FOR UPDATE` 锁钱包；`balance < price` → 回滚 → **402** `{need, have}`，零部分写入。
6. **落账**：`balance -= price` + `coin_ledger(-price, biz_key=幂等键, episode_id)` + `entitlements(coin)` + 幂等快照，同一事务提交。

返回 `status` 三态：`unlocked / already_unlocked / subscription`，附 `balance_after`。

- 权益永久：`entitlements` 只按 `(user, episode, 'coin')` 唯一，没有过期时间。
- 与会员的关系：买会员后，之前用币解锁的集**保留**；会员到期仍可看这些集。

### 3.4 端到端时序

```
打开剧 → GET /play
   ├─ 免费集            → locked=false → POST /playback → allowed → 播放
   └─ 锁定集            → locked=true + coin_price
        ├─ 买会员（Me 页 PremiumSheet → Stripe → webhook）→ 回剧页 locked=false
        └─ 花币解锁      → POST /unlock(Idempotency-Key)
                            ├─ 402 余额不足 → 去任务中心赚币
                            └─ unlocked → 重新 POST /playback → allowed
```

### 3.5 线上现状（关键）

- `entitlements` = **0 行**：从未发生过金币解锁。
- H5 锁屏（`h5/src/pages/PlayPage.tsx:361`）只有 `🔒 Unlock this episode · 价格`，**没有解锁按钮，也没有开通会员按钮**；`PremiumSheet` 只挂在我页。
- Flutter App 同样只展示价格（`app/lib/screens/detail.dart:390`），没有解锁动作。
- 后台没有「手动授予单集权益」的接口，只能调币（`POST /admin/v1/users/{id}/coins`）。
- 结论：**当前看锁定集的唯一现实路径是买会员**，金币在 C 端实际上没有消费场景。

---

## 4. 风险清单（按影响排序）

| # | 问题 | 影响 | 位置 |
|---|---|---|---|
| 1 | 周卡线上 $0.50 / 7 天，且 v6 没有订阅价 | 切回 `subscription` 模式会按 $0.50 新建订阅价，等于以测试价卖自动续费；必须先改回 $9.99 | `plan_versions` / `ensure_stripe_price` |
| 2 | 幂等键**全局唯一、不绑定用户** | 用户 B 复用 A 的 key 会拿到 A 的结果（`replayed`），B 既没扣币也没权益；极端情况下 A 的 `balance_after` 泄露给 B | `common/models.py:25`、`wallet/service.py:59` |
| 3 | `/v1/unlock` 不校验剧/集是否 `published` | 可对未发布内容扣币，扣了也播不了 | `wallet/service.py:68` |
| 4 | `coin_price_per_episode = 0` 的剧 | 扣 0 会写 `amount=0` 的 ledger，违反 `amount != 0` 约束 → 500；线上有 3 部这样的剧 | `wallet/service.py:110-120` |
| 5 | 权益文案与实现不符 | `Download` / `Priority support` 完全没做；`No ads` 无差异；`1080P` 未限制 | 套餐 `benefits` |
| 6 | 锁屏无转化入口 | 用户看到锁只能退出；既不能花币也不能当场开会员，是最直接的流失点 | `PlayPage.tsx:361` |
| 7 | 绑定/广告奖励不可达 | 后台可配金额，但用户永远拿不到；配置形同虚设 | `bind_provider` 无路由 |
| 8 | 每日任务 0 领取 | 要么曝光不足，要么用户没动机（金币无处可花，见 #6） | `task_claims` = 0 |
| 9 | 套餐页文案「有效期」不可配 | 运营以为能改时长，实际改不了 | `admin StagePages.tsx` |

### 建议的最小修复顺序

1. 锁屏加两个按钮：**金币解锁**（`POST /unlock` + 幂等 key）和 **开通会员**（复用 `PremiumSheet`）。
2. `unlock` 补 `published` 校验、`price <= 0` 直接视为已解锁/免费、幂等键改为 `(user_id, key)` 唯一。
3. 明确权益边界：1080P 要么开 `MEMBER_ONLY_1080` 并在 HLS 层真正限制，要么从文案删除；下载/客服不做就下架文案。
4. 周卡价格回到 $9.99 之后，再考虑切回订阅模式。

---

## 附：关键代码位置

| 逻辑 | 文件:行 |
|---|---|
| 解锁判定（free / member / unlocked） | `backend/app/modules/playback/service.py:126-160` |
| 会员有效期 `access_until` | `backend/app/modules/billing/service.py:142-158` |
| 会员状态展示 `active_membership` | `backend/app/modules/billing/service.py:194-240` |
| 金币解锁事务 | `backend/app/modules/wallet/service.py:55-131` |
| 解锁接口 | `backend/app/modules/public_flow.py:164-175` |
| 播放页锁定标记 | `backend/app/modules/public_flow.py:821-976` |
| 签到 / 广告 / 绑定奖励 | `backend/app/modules/account/service.py:378-409, 510-525` |
| 每日任务进度与领取 | `backend/app/modules/account/tasks.py:48-203` |
| 任务与金额目录 | `backend/app/modules/account/service.py:43-115` |
| 一次性时长包激活 | `backend/app/modules/billing/service.py:708-775` |
| 订阅 webhook 同步 | `backend/app/modules/billing/service.py:778-852` |
| H5 套餐弹层 | `h5/src/ui/PremiumSheet.tsx` |
| H5 锁屏 | `h5/src/pages/PlayPage.tsx:361-368` |
| 后台套餐配置 | `admin/src/pages/StagePages.tsx:2160+` |
