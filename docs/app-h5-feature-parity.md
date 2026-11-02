# App 与 H5 功能对齐清单

> 生成时间：2026-10-20。依据仓库当前工作区代码：`h5/src`（31 个端点）与 `app/lib`（15 个端点），后端路由以 `backend/app/modules/public_flow.py` 为准。
>
> 结论：**App 目前是"能看、不能付、不能互动、记不住人"的浏览壳**。首页/推荐/播放骨架已对齐，但账号、变现、互动、个人中心四块基本为空。

---

## 0. 2026-10-20 补全批次（本轮已落地）

当天已把 P0/P1 的大头补齐，`flutter analyze` 无告警、17 个测试全绿（含 7 个新 golden）、模拟器截图与接口级 curl 验收通过：

| 功能 | 落点 | 验收 |
|---|---|---|
| 会话层 | `lib/session.dart`：token 落盘（SharedPreferences）、启动 `/v1/me` 校验、401 自动回游客、登出、`requireAuth` 回放被拦动作 | 模拟器登录态截图（金币 370 与 curl 扣款一致） |
| 登录 | `screens/login_sheet.dart`：邮箱验证码（`/v1/auth/email/authenticate` 存在即登录）、60s 重发、错误细分、`/v1/auth/providers` | golden `login_sheet.png` |
| 我页 | `screens/me.dart`：资料卡+ID 复制、关注/粉丝/获赞、会员条、快捷四宫格、订单、历史/收藏/点赞三 tab、编辑资料/金币流水/消息/设置弹层 | 模拟器截图 |
| 会员/订单 | `screens/premium.dart`：三档套餐、订阅/一次性双模式、Stripe 跳转（url_launcher）、回前台刷新 `/v1/me`、取消续费、订单列表 | golden `premium_sheet.png` / `orders_sheet.png` |
| 任务中心 | `screens/rewards.dart`：余额、连击、进度条、领取、游客态 `/v1/tasks` | 模拟器截图（370 金币、2/8 进度） |
| 金币解锁 | `screens/unlock.dart`：锁集三出口、二次确认、幂等键按集缓存、402 `{need,have}` 校准 | golden + curl 幂等三连（420→370，重试不重复扣） |
| 评论 | `screens/comments.dart`：列表/分页/发布/点赞/计数回传，播放页与推荐流都接上 | golden + curl 发帖（pending 态） |
| 互动修正 | 点赞/收藏读 `drama.liked/favorited` 初值、未登录引导、推荐流点赞对象统一 `drama`、全局 toast | 单测/截图 |
| 分享 | `share_plus`（播放页 + 推荐流） | analyze/截图 |
| 首页打磨 | 骨架屏、无 banner 时用首楼层兜底、HOT 火标、榜单 #1 皇冠与热度、`More` 仅超限时显示 | golden 重生成 |
| 其他 | `deviceId` 持久化、语言切换（AppBar + 设置）并持久化、倍速持久化、**清晰度真切换**（见下） | 单测 |
| 清晰度 | `lib/hls.dart` 解析 master m3u8 的 `EXT-X-STREAM-INF`（标签取 Bunny 目录名 `240p/`，按档位升序）；选中档位时 `EpisodePlayer` 改为加载该档自己的 media playlist，AVPlayer 不再自适应；切档保留播放位置与播放状态；选择持久化，换集若不存在自动回 Auto | `hls_test` 5 例、`quality_switch_test` 2 例、`playback_settings` golden、真机日志 240P→198×352 / 480P→480×854 |

**仍开放**：
- Google 登录：**已接入**（2026-10-20）。`google_sign_in` 7.2 + iOS/Android OAuth client；iOS 的 id_token `aud` 是 iOS client，后端改为 `GOOGLE_CLIENT_IDS` 多 audience（server v2026.10.20.3）。
- 支付回跳：目前靠回前台刷新；Universal Link 需要上架后配 AASA。
- 死代码 `ChannelScreen`（走老 `/v1/home`，无人引用）暂未删，避免踩到其他会话。

---



## 1. 总览

| 功能域 | H5 | App | 差距 |
|---|---|---|---|
| 首页楼层 / banner / 更多 | ✅ | ✅ | 缺骨架屏、HOT 徽章、banner 兜底 |
| 推荐流（竖滑 / 预加载 / 播完选集） | ✅ | ✅ | 点赞未登录也可点、评论入口是假的 |
| 播放（签名 URL / 续播 / 1.5x 倍速 / 进度上报） | ✅ | ✅ | 清晰度选项不生效 |
| 搜索（热搜 + 结果） | ✅ | ✅ | 热搜词不可点、结果无封面 |
| 缩略图（Bunny Optimizer） | ✅ | ✅ | 已对齐（2026-10-20） |
| 登录（邮箱验证码） | ✅ | ⚠️ | 可用，但 token 不落盘、重启掉登录 |
| 登录（Google） | ✅ | ✅ | 2026-10-20 接入：iOS client 走 `GIDClientID`，Android 用 web client 作 `serverClientId` |
| 会话持久化 / 登出 / 401 兜底 | ✅ | ❌ | `main.dart` 的 token 只在内存 |
| 个人资料 `/v1/me` | ✅ | ❌ | 金币、会员、头像、关注数全拿不到 |
| 金币解锁锁集 | ✅ | ❌ | 锁集是死胡同（只有锁图标+价格文案） |
| 会员购买 / 取消 / 订单 | ✅ | ❌ | 无 plans、checkout、orders |
| 评论（列表/发布/点赞/分页） | ✅ | ❌ | 评论按钮打开的是选集面板 |
| 收藏 / 历史 / 点赞列表 | ✅ | ❌ | 我页仅 ListTile → 空白页 |
| 金币流水 / 消息中心 / 编辑资料 | ✅ | ❌ | 同上 |
| 任务中心（进度/领取/签到连击） | ✅ | ❌ | 只有两个无状态按钮 |
| 设置（语言切换 / 登出 / 版本） | ✅ | ❌ | 无入口，locale 变量没人用 |
| 分享 | ✅ | ❌ | 两处 `onShare: () {}` |
| 全局 toast / 错误提示 | ✅ | ❌ | 失败静默 |
| 设备号持久化 | ✅ | ⚠️ | `api.dart:9` 有 TODO，每次启动新 ID |

---

## 2. 接口覆盖：App 15 个 vs H5 31 个

App 已用（15）：`/v1/config`、`/v1/home/groups`、`/v1/home`（死代码）、`/v1/feed`、`/v1/dramas/{id}/play`、`/v1/dramas/{id}/comments`（仅计数）、`/v1/playback`、`/v1/playback/{start|progress|complete}`、`/v1/reactions`、`/v1/search`、`/v1/search/trending`、`/v1/auth/email/code`、`/v1/auth/email/register`、`/v1/me/checkin`、`/v1/me/ad-reward`。

**后端已具备、App 完全没调（19 个）**：

| 端点 | 用途 | 对应 App 缺口 |
|---|---|---|
| `GET /v1/me` | 资料/金币/会员/关注数 | 我页、会员条、解锁余额 |
| `GET /v1/me/history` | 观看历史 | 我页 tab |
| `GET /v1/me/favorites` | 追剧列表 | 我页 tab |
| `GET /v1/me/lists/like` | 点赞列表 | 我页 tab |
| `GET /v1/me/ledger` | 金币流水 | 金币弹层 |
| `GET /v1/me/messages` / `POST .../read` | 消息中心 + 未读红点 | 我页铃铛 |
| `GET /v1/me/tasks` / `POST .../{code}/claim` | 任务进度/领取 | 奖励页 |
| `GET /v1/tasks` | 未登录也能看任务 | 奖励页游客态 |
| `POST /v1/unlock` | 金币解锁单集（幂等键） | 锁集主 CTA |
| `GET /v1/plans` | 三档套餐 | 会员页 |
| `POST /v1/billing/checkout` | Stripe Checkout | 会员页 |
| `POST /v1/billing/cancel` | 关闭续费 | 会员页 |
| `GET /v1/me/orders` | 购买订单 | 我页 |
| `POST /v1/auth/google` | Google 登录 | 登录页 |
| `GET /v1/auth/providers` | 判断开启了哪些登录方式 | 登录页 |
| `POST /v1/auth/email/authenticate` | 存在即登录、不存在即注册 | 登录页（App 走的是仅注册） |
| `GET/POST /v1/episodes/{id}/comments` | 分集评论 | 评论面板 |
| `PATCH /v1/me` | 改昵称/简介 | 编辑资料 |

App 独有的 3 个：`/v1/auth/email/register`（仅注册，与 H5 的 authenticate 语义不同）、`/v1/me/ad-reward`（H5 未暴露 UI）、`/v1/home`（`ChannelScreen` 死代码，无任何引用）。

---

## 3. 页面级对比

### 3.1 首页 `home.dart` vs `HomePage.tsx`
- 已有：运营 tab、banner 轮播（5s）、poster3/grid2/rank 三种楼层、楼层"更多"→ 三列全量页、下拉刷新。
- 缺：加载骨架（H5 `HomeSkeleton`）、无 banner 时用首楼层首图兜底（H5 `heroFromGroups`）、HOT 火标、榜单第 1 名皇冠、"更多"只在超出 limit 时出现（App 恒显示）。

### 3.2 推荐流 `foryou.dart` vs `ForYouPage.tsx`
- 已有：竖滑翻剧、5 条/页 + 剩 2 条预加载、播完 CTA 跳剧集页、进度条、选集 pill、右侧 rail（头像/赞/评论/分享）。
- 缺：评论面板（点评论直接跳剧页）、真实分享（`onShare` 空实现）、点赞的登录拦截（未登录直接 401 静默失败）、点赞对象不一致（App 打的是 `episode`，H5 打 `drama`）。
- 备注：`foryou.dart:12` 注释里的"关注/推荐 segment"没有实现，属于过期注释。

### 3.3 播放页 `detail.dart` vs `PlayPage.tsx`
- 已有：竖滑切集、断点续播、倍速、选集面板（20 集一档）、简介 tab、点赞/收藏按钮、进度心跳。
- 缺（关键）：
  - **锁集无任何出口**：只有锁图标 + `$price Coins` 文案，没有"解锁本集 / 成为会员 / 去赚金币"三个 CTA（H5 `LockedEpisode` + `UnlockSheet`）。
  - **清晰度选项是装饰**：`quality` 只存在 `_DetailScreenState` 里，`_EpisodePage` 没往 `EpisodePlayer` 传，播放器也不支持 —— H5 通过 hls.js `setQuality` 真的切。建议先隐藏或标"即将开放"。
  - 评论按钮打开的是选集面板（`onComment: _openSelector`）。
  - 点赞/收藏初值恒为 false，不读 `drama.liked` / `drama.favorited`，重启后状态丢失。
  - 分享空实现。
  - 倍速/清晰度不持久化（H5 存 `localStorage`）。

### 3.4 我 `account.dart` vs `MePage.tsx`
- 已有：7 个 ListTile + 登录/注册两个独立页。
- 缺：整页。H5 有资料卡（头像/昵称/ID 复制）、关注/粉丝/获赞、会员条（到期日/不再续费）、四个快捷入口（历史/追剧/金币/签到）、订单入口、三 tab 网格（历史/收藏/点赞）、铃铛未读、设置（语言/登出）。App 点进去全是 `SimplePage`，只有一个标题。

### 3.5 奖励 `RewardsScreen` vs `RewardsPage.tsx`
- 已有：签到按钮、看广告按钮（H5 没有 ad-reward UI，App 反而多一个）。
- 缺：余额、签到连击/今日已签、任务列表（点赞 5 / 收藏 5 / 看 8 集）带进度条与"去完成/领取"、游客态用 `/v1/tasks` 展示。
- 现状：无 token 时两个按钮都是 disabled，页面等于空页。

### 3.6 搜索 `search.dart` vs `SearchPage.tsx`
- 已有：热搜词、结果列表、按回车搜索。
- 缺：热搜词可点（App `ListTile` 没有 onTap）、结果带封面缩略图、搜索历史、加载/空态。

### 3.7 登录 `LoginScreen/RegisterScreen` vs `LoginSheet.tsx`
- 已有：邮箱 + 6 位码（App 是单输入框，H5 是 6 格自动跳转）、发码。
- 缺：`/v1/auth/providers` 驱动的 Google 按钮（App 写死三个"is not configured"）、60s 重发倒计时、错误文案细分（invalid code / expired / too many / mail failed）、"存在即登录"的 authenticate 流程。
- 坑：App 用 `purpose: 'register'` + `/v1/auth/email/register`，老用户在这条路径上会撞"已存在"。

---

## 4. 建议补齐顺序

**P0 —— 不做就没有商业闭环**
1. 会话层：token 落盘（`shared_preferences`）+ 启动时 `/v1/me` + 401 重置 + 登出。
2. 金币解锁：`POST /v1/unlock`（`Idempotency-Key` 按集缓存）+ 二次确认弹层 + 402 `{need,have}` 校准 + 余额不足跳奖励页。
3. 评论：`CommentSheet` 等价页面（列表/分页/发布/点赞/删除/30s 限流提示）。
4. 会员：`/v1/plans` + `checkout`（App 内需 `url_launcher` 开 Stripe）+ 取消续费 + 订单列表。
5. 我页：资料卡 + 金币/会员 + 历史/收藏/点赞三 tab + 金币流水 + 消息。

**P1 —— 体验闭环**
6. 任务中心（`/v1/me/tasks` + claim + `/v1/tasks` 游客态 + 连击）。
7. 清晰度：要么接 AVPlayer 的 variant 切换，要么隐藏选项。
8. 互动前置：点赞/收藏未登录时引导登录（等价 `requireAuth`），并读初值。
9. 分享：`share_plus` + Universal Link。
10. 设置：语言切换（`locale` 现在没人用）+ 登出 + 版本号。
11. 搜索：热搜可点、结果加封面、历史词。

**P2 —— 打磨**
12. 骨架屏、空态、banner 兜底、HOT/皇冠徽章、More 阈值。
13. `deviceId` 持久化（`api.dart:9` TODO）。
14. 全局 toast（现在所有失败都静默）。
15. 支付回跳/深链（App 不能用 `?checkout=success`，需 Universal Link 或回前台刷新 `/v1/me`）。
16. 删除死代码 `ChannelScreen`（无人引用，走的是 `/v1/home` 老接口）。

---

## 5. 证据索引

| 事项 | 位置 |
|---|---|
| App token 仅内存 | `app/lib/main.dart:47`、`main.dart:67` |
| 我页占位 | `app/lib/screens/account.dart:77-95` |
| 锁集无出口 | `app/lib/screens/detail.dart:390-417` |
| 清晰度未生效 | `detail.dart:45,124,809`（仅 state）、`episode_player.dart` 无 quality 参数 |
| 评论按钮 = 选集 | `app/lib/screens/detail.dart:258` |
| 分享空实现 | `detail.dart:263`、`foryou.dart:260` |
| 点赞未拦截 | `foryou.dart:188-199` |
| H5 解锁链路 | `h5/src/pages/PlayPage.tsx:97-152`、`ui/UnlockSheet.tsx` |
| H5 会员链路 | `ui/PremiumSheet.tsx`、`api.ts:getPlans/startCheckout/cancelSubscription` |
| H5 我页 | `h5/src/pages/MePage.tsx` |
| H5 任务中心 | `h5/src/pages/RewardsPage.tsx` |
