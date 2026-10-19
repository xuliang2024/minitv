# MiniTV 系统实现清单

> 快照：2026-10-19 16:45。依据 `main` 分支代码 + 线上库（YOUR_SERVER_IP）。
> 状态口径：**完整** = 主链路可用；**部分** = 能用但缺入口/缺约束；**占位** = 只有壳。

## 0. 总览

| 项 | 现状 |
|---|---|
| 形态 | 4 个端：官网 `minitv.us`、H5 `h5.minitv.us`、Flutter App、后台 `admin.minitv.us` |
| 后端 | 单个 FastAPI 单体：**51 个 C 端接口 + 75 个后台接口**，同一进程跑 2 个后台线程任务 |
| 外部依赖 | Stripe（收款）、Bunny Stream/Storage（视频/图片/CDN）、Resend（邮件）、OpenAI 兼容 LLM（评论审核）、PostgreSQL、Redis |
| 内容规模 | 已发布 **103 部剧 / 1,216 集**；另有 1 集 `in_review`、1 集 `transcode_failed` |
| 用户规模 | 23 个 C 端用户、8 个有效会员、9 笔已支付订单（$230.53） |
| 互动规模 | 3 条已通过评论；近 7 天 341 次 start / 950 次 progress / 116 次 complete |
| 一句话 | 内容生产、播放、推荐、金币、会员、评论、后台都已跑通主链路；**客户端最后一公里入口、App 完整度、若干运营自动化**还没补齐 |

---

## 1. 后端域清单

### 1.1 账号与鉴权 — 完整

- 邮箱验证码：6 位、10 分钟有效、哈希存储、单次消费、5 次尝试上限、同邮箱同用途 5 次/小时。
- 三个入口：`/auth/email/authenticate`（存在即登录、不存在即注册）、`/auth/email/register`、`/auth/email/login`；用途分 `login / register / bind`。
- Google 登录：校验 ID token，按 `sub` 优先、已验证 email 合并；未验证邮箱不合并账号。
- 其他 OAuth：占位，返回 501。
- 会话：`secrets.token_urlsafe(32)` 存 Redis，30 天；C 端 `optional_user / require_user` 两种依赖。
- 后台账号独立于 C 端：`admin_users` + Redis 会话；角色 `ops / finance / admin`。
  - `ops`、`admin` 可写内容；`finance` 只应看钱，但**读接口没有按角色收紧**（可看剧集/用户/评论）。
  - 改价、删剧、调币等敏感写操作要求 `admin`。

### 1.2 内容与目录 — 完整

- 公开读路径只暴露 `published`：`/v1/home`、`/v1/dramas/{id}`、`/v1/search`（**只按标题 ILIKE**，不搜简介/标签）、`/v1/preview`（官网试用：每个分类前 7 部，且至少 1 集免费）、`/v1/preview/dramas/{id}`。
- 首页楼层：`/v1/home/groups` 返回分组（三列海报 / 横滑榜单 / 双列网格）+ 角标 + banner；`/v1/config` 返回频道 tab 与双语文案。
- 标签库：主分类 + 普通标签，剧与标签多对多。
- 内容状态机（数据库触发器与应用层双保险）：
  - 剧：`draft → in_review → published → unpublished → in_review/published`
  - 集：`not_uploaded → transcoding → in_review → published → unpublished`，失败可 `transcode_failed → transcoding` 重试，已发布可回 `not_uploaded` 换视频

### 1.3 播放 — 完整

- `POST /v1/playback` 是唯一签发播放地址的入口：先过 `published / 地区白名单 / free / member / entitlement` 五道判断，再签发 **5 分钟** HMAC URL（Bunny Stream token，或自建 CDN token）。
- 字幕单独签名（图片密钥与视频密钥分离，token 不能串用）。
- `playback_gen:{episode_id}` 计数器用于换视频后让旧 URL 立即失效（`bump_generation`）。
- 埋点：`start / progress / complete` 三类事件；`start` 同时给剧的 `play_count` +1；登录用户写 `playback_progress` 续播点。
- `GET /v1/dramas/{id}/play` 一次返回剧信息、全部集、每集 `free/locked/progress/comment_count`、续播点和 `entitlement` 摘要。
- 官网播放页走 `/v1/preview` + `/v1/playback`，**只放免费集**；锁定集引导去 App（产品决策，不是缺陷）。

### 1.4 推荐流 — 完整

- `GET /v1/feed`：召回 → 打分 → 多样性重排 → 游标分页。
- 质量分 5 个信号：完播率 0.30、口味相似 0.26、热度 0.18、新鲜度 0.14、集数深度 0.12（权重可用环境变量覆盖）。
- 三个加法项：续播加权 +0.35、疲劳惩罚 -0.09×log(曝光次数)、探索噪声 ±0.10（按 session 确定性）。
- 产品规则：**一部剧一个 session 只出现一次**；**锁定集不进推荐流**；每页 5 条、剩余 2 条时预加载；冷启动每页插 1 条探索内容。
- 会话计划与曝光计数存 Redis，Redis 挂了降级为现场重排而不是报错。

### 1.5 金币与任务 — 部分

- 账本制：`wallet_accounts.balance` + `coin_ledger`（`biz_key` 全局唯一、`amount != 0`、余额非负）。
- 获取：签到、注册、绑定邮箱/FB、每日广告、点赞 5、收藏 5、看完 8、后台调账。
- 业务日按 **America/Los_Angeles**；后台改金额**次日生效**；任务进度按「不同对象」去重。
- 缺口：绑定奖励没有路由；广告奖励 H5 无入口；三个每日任务 0 领取；**没有金币消耗场景**（见 4.1）。

### 1.6 会员与支付 — 部分

- 三档套餐 + 版本化价格（改价 = 新建 `plan_versions` 版本 + 新建 Stripe Price）。
- 全局计费开关 `subscription / one_time`，只影响新购买；时长包可叠加、不可取消；订阅取消只停续费。
- Stripe Checkout + 签名 webhook；订单 `created → paid`；`refunded` 只是 schema 与财务统计里的状态，**没有退款写入路径**（无 `charge.refunded` 处理、后台也无退款操作）；`/v1/me/orders` 只回 `paid/refunded`。
- 权益判定统一走 `billing.access_until()` = `max(current_period_end, pass_end)`。
- 缺口：权益文案与实现不符；周卡测试价仍在线上（见 4.2）。

### 1.7 评论 / 互动 / 站内信 — 部分

- 评论按**分集**：列表游标分页、发评论 30 秒/条 + 50 条/天、24 小时内可删。
- **异步审核**：新评论 `pending`，作者立即可见、他人不可见；后台线程调 LLM 返回 `allow / review / block`；线上已启用（模型 `deepseek-flash`，`review` 落到 `pending`）。
- 点赞/收藏：`reactions` 支持 drama / episode / comment 三种目标，同目标同类型唯一。
- 站内信：`/v1/me/messages` 可读可标已读，但**全仓没有任何写入口**，永远是空列表。
- 推送：只有 `push_records` 台账，无 FCM/APNs；「运营中心」入口已隐藏。

### 1.8 运营后台 — 完整（少数占位）

| 模块 | 能力 |
|---|---|
| 数据看板 | 今日 KPI、7/30 天趋势、Top 剧、转化与待办（审核/待发布） |
| 剧集管理 | 建剧/改剧/标签、封面、批量建集、TUS 直传、Bunny 状态同步、字幕、发布/下架/批量状态、删除影响预检 |
| 首页分组 | 分组 CRUD、剧集排序、楼层样式/角标 |
| 首页运营 | 频道 Tab、Banner、楼层/分组排序与预览；**文案、热搜、法务页、商店链接、营销数字只有后端接口，没有后台 UI** |
| 用户管理 | 列表/搜索、资料修改、金币调账（必填原因 + 审计） |
| 金币与任务 | 7 天签到梯度、各任务金额、次日生效、历史变更审计 |
| 评论审核 | 列表/通过/驳回/删除 |
| 自动审核 | 模型、Prompt、阈值、分类、开关、在线测试 |
| 财务订单 | 订单搜索/筛选/分页、详情（用户快照 + 会员状态）、汇总、金币流水 |
| 会员套餐 | 三档价格与权益编辑、全局计费模式切换、Stripe 价格预生成 |
| 标签库 | 标签 CRUD |
| 运营中心 | **已隐藏**：只有推送台账，不会真发推送 |

### 1.9 数据分析 — 完整

- 全部指标从真实事件表实时聚合，`daily_metrics` 只是快照缓存：
  - 新增用户 `users.created_at`、活跃用户 = 当天有播放事件的登录用户、播放/完播 `playback_events`、收入 `orders.paid_at`、金币消耗 = 负向流水、解锁数 = `entitlements`。
- 按 LA 业务日切分；看板写快照，趋势最多 90 天。
- 小口径不一致：`overview` 的活跃会员只按 `current_period_end > now` 统计，**漏掉只有 `pass_end` 的时长包用户**。

### 1.10 基础设施 — 完整

- 幂等：金币/签到/任务/订单回调全部靠数据库唯一约束，不靠内存；解锁要求 `Idempotency-Key`。
- 审计：后台改价、改资料、调币、审核策略都写 `audit_log`。
- 后台线程：Bunny 转码对账（轮询）、评论审核 worker（带失败重试与 sweeper）。
- 健康检查 `/health` 同时探 PostgreSQL 与 Redis。
- 发布：`scripts/release.py` 统一走共享锁 + 自动版本号，R2 双桶（`minitv-web` / `minitv-h5`）+ 服务器部署。

---

## 2. 客户端实现矩阵

| 能力 | 官网 | H5 | Flutter App | 后台 |
|---|---|---|---|---|
| 浏览/搜索 | 首页 + 免费预览 + 播放页 | 首页楼层 + 搜索 + 推荐流 | 首页 + 推荐 + 搜索 | 剧集管理 |
| 登录/账号 | 无 | 邮箱验证码 + Google | 邮箱验证码（骨架） | 独立员工账号 |
| 播放 | 仅免费集（hls.js） | 全集（锁/解锁） | 有播放器，锁定只显示价格 | 预览播放 |
| 金币 | 无 | 签到、任务、流水分页 | 签到/广告两个裸按钮 | 调账 |
| 会员 | 展示套餐，CTA 去 H5 | 购买、状态、取消、订单 | 仅「会员」占位页 | 套餐与订单管理 |
| 评论 | 无 | 分集评论弹层 | 无 | 审核 |
| 个人中心 | 无 | 历史/收藏/点赞/消息/设置/订单 | 入口列表，点进去多为占位页 | — |
| 语言 | 英文单语 | 中/英切换 | 中/英切换 | 中文 |

结论：**H5 是功能最完整的 C 端**；官网承担获客与免费试用；Flutter App 仍是骨架（`RewardsScreen` 只有两个按钮，Me 的 7 个入口有 6 个落到 `SimplePage`）。

---

## 3. 状态机与关键约束

| 对象 | 状态 | 约束 |
|---|---|---|
| 剧 | draft / in_review / published / unpublished | 应用层矩阵 + DB 触发器 |
| 集 | not_uploaded / transcoding / in_review / transcode_failed / published / unpublished | 同上 |
| 订单 | created / paid / failed / refunded | `external_txn_id` 唯一；refunded 无写入路径 |
| 订阅 | active / canceled | 每用户最多一行 active（部分唯一索引） |
| 评论 | pending / approved / rejected / deleted | `status` 管可见性，`moderation_state` 管审核任务 |
| 金币 | 无状态，靠流水 | `biz_key` 唯一、`amount != 0`、余额 ≥ 0 |
| 单集权益 | 有/无 | `(user, episode, 'coin')` 唯一，不过期 |

---

## 4. 空壳 / 断点清单（按影响排序）

### 4.1 金币没有消费出口

- 后端解锁链路完整，但 H5 与 App 的锁屏都只显示价格，**没有解锁按钮**；线上 `entitlements` = 0。
- 因此金币只进不出，每日任务也没人领（`task_claims` = 0）。

### 4.2 会员权益名不副实

- `Download`、`Priority support` 无实现；`No ads` 无差异；`1080P` 未限制（`MEMBER_ONLY_1080` 未开，且 H5 清晰度走 HLS）。
- 周卡线上 $0.50/7 天且无订阅价，切回订阅模式会按测试价卖自动续费。

### 4.3 定时发布不会自动执行

- `scheduled_publish_at` 有字段、有接口，但只有手动调 `POST /admin/v1/schedules/apply` 才生效；**没有 cron、后台 UI 也不会触发**。

### 4.4 站内信 / 推送是空壳

- `InboxMessage` 无任何写入口；`push_records` 只是台账，无 FCM/APNs，运营中心已隐藏。

### 4.5 绑定奖励不可达

- `bind_provider()` 无路由，绑定邮箱/FB 的奖励永远发不出去。

### 4.6 官网文案超出实现

- 官网写「download shows」「up to 1080p」「unlock with coins or membership in the app」，但 App 既不能花币、也没有下载。

### 4.7 若干边界漏洞

- 解锁幂等键全局唯一、不绑用户；`/unlock` 不校验 `published`；0 价剧解锁会触发 `amount=0` 约束 500。
- 删除剧集会**物理删除该剧全部 `entitlements`**（用户花币买的单集权益直接消失，无退款、无补偿），只靠删除前的 `impact` 提示。
- 搜索只搜标题。
- `overview` 活跃会员漏算纯时长包用户。
- `finance` 角色读接口未收紧。
- 后台文案/热搜/法务/商店等配置只能直接调接口，运营无法自助维护。

---

## 5. 如果要继续推进，建议顺序

1. **补金币出口**：锁屏加「花币解锁 / 开通会员」两个按钮，先让金币闭环。
2. **修解锁边界**：published 校验、0 价处理、幂等键 `(user_id, key)`。
3. **价格回正**：周卡回 $9.99，再决定订阅/一次性模式。
4. **权益对齐**：1080P、下载、无广告三选一——要么实现，要么从文案删除。
5. **App 对齐 H5**：Rewards / Me / 评论 / 会员至少补齐到 H5 的能力面。
6. **自动化补齐**：定时发布 cron；站内信/推送要么接通道，要么下线入口。

---

## 附：关键代码位置

| 域 | 文件 |
|---|---|
| 路由装配 | `backend/app/main.py` |
| C 端主接口 | `backend/app/modules/public_flow.py` |
| 内容公开读 | `backend/app/modules/content/public_router.py` |
| 状态机 | `backend/app/modules/content/service.py` |
| 播放鉴权 | `backend/app/modules/playback/service.py` |
| 推荐 | `backend/app/modules/recommend/{router,service,ranking,signals,config}.py` |
| 金币/解锁 | `backend/app/modules/wallet/service.py` |
| 账号/奖励 | `backend/app/modules/account/{service,tasks}.py` |
| 会员/支付 | `backend/app/modules/billing/service.py` |
| 评论审核 | `backend/app/modules/social/moderation.py` |
| 后台接口 | `backend/app/modules/ops/{router,flow_router}.py` |
| 看板 | `backend/app/modules/analytics/service.py` |
| H5 | `h5/src/App.tsx` + `h5/src/pages/*` |
| 官网 | `web/src/{App,preview,watch}.tsx` |
| App | `app/lib/main.dart` + `app/lib/screens/*` |
| 后台前端 | `admin/src/App.tsx` + `admin/src/pages/*` |
