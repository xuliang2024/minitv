# 上剧方（内容供应方）角色整改方案 v5

> 依据：`main@a6b3f32`（账号管理已上线）+ 2026-10-21 线上数据（212 部剧 / 9549 集 / 3 个员工账号）。
> v2 变更：一供应商一账号、剧名不再全局唯一、审核通过不自动上架、上剧方可看数据与收益、免费集数由上剧方定、会员开通需记录来源剧。
> v3 变更：分成比例后台可调可设（含变更历史）、续费不计收益只算首单、已上架免费集数下限 5 集、不做地域/语言分布。
> v4 变更：分成默认 80%、结算币种 USD、按自然月结算、5 集下限升级为**全局约束**（运营也不能低于 5）。
> v5 变更：上剧方支持 **API Key + CLI**（复用现有密钥体系，新增 `upload` scope 与 partner 自助密钥页）。
> v6 变更：新建剧免费集数默认 5；剧集列表不显示播放量；审核队列支持批量通过（≤50/批，逐条校验）。

## 0. 结论

新增第四种角色 `partner`（中文「上剧方」），但**不能只加一个 role 枚举**：现在后台是单租户的，`require_admin` 只判断「是不是活跃的 `admin_users`」，所有列表查询都不带归属。直接开账号等于把 212 部剧、观众邮箱、财务订单全部暴露给外部公司。

要做的是七件事：

1. `suppliers`（供应商）表 + 账号 1:1 + 剧归属；
2. `require_admin` 改成**员工角色白名单**，上剧方走独立 `/partner/v1`；
3. 审核用新列 `review_state`，不动 `dramas.status` 与 DB 触发器；
4. 运营上架前多一道「待上架」，审核通过 ≠ 上线；
5. 会员订单记 `source_drama_id`，把开通归因到剧；
6. 分成比例后台可调（留变更历史），收益按**首单**归因结算；上剧方免费集数不低于 5 集；
7. 上剧方要用 **API Key + CLI** 批量上片：复用现有密钥体系，scope 限 `read/write/upload`，CLI 按身份分流。

## 1. 已定决策（本轮拍板）

| # | 问题 | 结论 | 落库/实现 |
|---|---|---|---|
| 1 | 一供应商几个账号 | **只能一个** | `admin_users.supplier_id` 唯一（partial unique），换人=改绑，不新建 |
| 2 | 剧名唯一 | **不全局唯一**，跨供应商可同名 | 拆两条 partial unique index：自营内唯一、供应商内唯一 |
| 3 | 审核通过后是否上架 | **不自动**，运营点发布 | 通过只推 `status=draft→in_review`，进「待上架」 |
| 4 | 上剧方能否看数据 | **能**：播放、用户、转化、收益 | `/partner/v1/stats`，按 `supplier_id` 聚合，不给用户明细 |
| 5 | 定价权 | 价格**平台定**，免费集数**上剧方定** | partner 可写 `free_episode_count`，不可写 `coin_price_per_episode` |
| 6 | 会员归因 | 开通时记录来源剧 | `orders.source_drama_id` + `subscriptions.source_drama_id` + Stripe metadata |
| 7 | 分成比例 | 新建供应商**默认 80%**，后台可调，改动留历史 | `suppliers.revenue_share_bps INT NOT NULL DEFAULT 8000` + `supplier_share_history`（按下单时间取当时比例） |
| 8 | 续费是否算收益 | **不算，只算首单** | 现网订单表本就只落首单（续费由 Stripe 扣款、不写 `orders`）；复购按「首单归因」口径排除 |
| 9 | 免费集数下限 | **全局最少 5 集**（总集数不足 5 则全免），运营也只能调大 | `MIN_FREE_EPISODES=5`，提交/发布/编辑三处校验，无角色豁免 |
| 10 | 地域/语言分布 | **不做** | 数据页只给播放、用户数、转化、收益 |
| 11 | 结算币种与周期 | **USD、按自然月** | 订单原币 `amount_cents`（USD）直接汇总，不做汇率折算；次月生成上月结算单 |
| 12 | 上剧方自动化 | **API Key + CLI** | 复用 `admin_api_keys`；partner 只能拿 `read/write/upload`，独立 `/partner/v1` 路径守卫，CLI 双 surface |
| 13 | 新建剧免费集数 | **默认 5**（集数不足 5 时以集数为上限） | `POST /partner/v1/dramas` 落 `free_episode_count=5`，后续按 §4.3 校验 |
| 14 | 剧集列表 | **不显示播放量**，播放数据只在数据中心 | 列表列：剧集 / 状态 / 集数 / 免费集数 / 操作 |
| 15 | 审核队列 | **支持批量通过**，单次 ≤50，逐条校验 | `POST /admin/v1/reviews/batch`，返回成功数与跳过原因 |

## 2. 数据模型（一个迁移）

```sql
CREATE TABLE suppliers (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL UNIQUE,
  contact_name VARCHAR(64) NOT NULL DEFAULT '',
  contact_email VARCHAR(320) NOT NULL DEFAULT '',
  status VARCHAR(16) NOT NULL DEFAULT 'active',        -- active / disabled
  revenue_share_bps INT NOT NULL DEFAULT 8000,          -- 分成万分比，默认 80%
  note VARCHAR(500) NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_suppliers_share CHECK (revenue_share_bps BETWEEN 0 AND 10000)
);

-- 账号 1:1 供应商
ALTER TABLE admin_users ADD COLUMN supplier_id BIGINT REFERENCES suppliers(id);
ALTER TABLE admin_users DROP CONSTRAINT ck_admin_users_role;
ALTER TABLE admin_users ADD CONSTRAINT ck_admin_users_role
  CHECK (role IN ('ops','finance','admin','partner'));
ALTER TABLE admin_users ADD CONSTRAINT ck_admin_users_partner_supplier
  CHECK (role <> 'partner' OR supplier_id IS NOT NULL);
CREATE UNIQUE INDEX uq_admin_users_supplier ON admin_users (supplier_id)
  WHERE supplier_id IS NOT NULL;                       -- 一供应商一账号

-- 分成比例变更历史：结算按下单时刻的版本算，避免改比例后重算历史
CREATE TABLE supplier_share_history (
  id BIGSERIAL PRIMARY KEY,
  supplier_id BIGINT NOT NULL REFERENCES suppliers(id),
  share_bps INT NOT NULL,
  effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
  effective_to TIMESTAMPTZ,                            -- NULL = 当前生效
  changed_by BIGINT REFERENCES admin_users(id),
  note VARCHAR(200) NOT NULL DEFAULT '',
  CONSTRAINT ck_share_bps CHECK (share_bps BETWEEN 0 AND 10000)
);
CREATE INDEX ix_supplier_share_lookup ON supplier_share_history (supplier_id, effective_from DESC);

-- 剧归属 + 审核状态（不新增 dramas.status 取值）
ALTER TABLE dramas ADD COLUMN supplier_id BIGINT REFERENCES suppliers(id);
ALTER TABLE dramas ADD COLUMN review_state VARCHAR(16) NOT NULL DEFAULT 'none';
ALTER TABLE dramas ADD COLUMN review_note VARCHAR(500);
ALTER TABLE dramas ADD COLUMN submitted_at TIMESTAMPTZ;
ALTER TABLE dramas ADD COLUMN reviewed_at TIMESTAMPTZ;
ALTER TABLE dramas ADD COLUMN reviewed_by BIGINT REFERENCES admin_users(id);
CREATE INDEX ix_dramas_supplier_status ON dramas (supplier_id, status);
CREATE INDEX ix_dramas_review_state ON dramas (review_state) WHERE review_state = 'pending';

-- 剧名唯一：自营之间唯一、供应商内部唯一，跨供应商放开
ALTER TABLE dramas DROP CONSTRAINT uq_dramas_title;
CREATE UNIQUE INDEX uq_dramas_title_self ON dramas (title) WHERE supplier_id IS NULL;
CREATE UNIQUE INDEX uq_dramas_title_supplier ON dramas (supplier_id, title) WHERE supplier_id IS NOT NULL;

-- 会员归因
ALTER TABLE orders ADD COLUMN source_drama_id BIGINT REFERENCES dramas(id);
ALTER TABLE orders ADD COLUMN refunded_at TIMESTAMPTZ;   -- 结算要按退款发生月冲减
ALTER TABLE subscriptions ADD COLUMN source_drama_id BIGINT REFERENCES dramas(id);
CREATE INDEX ix_orders_source_drama ON orders (source_drama_id, paid_at)
  WHERE source_drama_id IS NOT NULL;
CREATE INDEX ix_orders_refunded_at ON orders (refunded_at) WHERE refunded_at IS NOT NULL;

-- 上剧方数据页要按剧/时间扫播放事件
CREATE INDEX ix_playback_events_episode_created ON playback_events (episode_id, created_at);
```

要点：

- 现有 212 部剧 `supplier_id=NULL`、`review_state='none'`，全部视为平台自营，不进审核队列。
- **不新增 `dramas.status` 取值**：该列有数据库触发器强制状态机（`alembic/versions/dd164bbf884e...:252`），加值要同时改 Python 矩阵、触发器、C 端可见性和后台页签。审核进度用 `review_state`，与评论的 `status` / `moderation_state` 同一套做法。
- 归因字段**必须在 P1 就加**：字段上线前产生的订单无法事后补归属，先加字段、先记录，partner 数据页可以晚点做。

## 3. 权限矩阵

| 能力 | partner | ops | finance | admin |
|---|:--:|:--:|:--:|:--:|
| 看自己的剧 / 分集 / 审核结果 | ✓ | — | — | — |
| 建剧、改简介、传封面、建集、上传 | ✓（仅 draft / rejected） | ✓（全部） | — | ✓ |
| 设置免费集数 `free_episode_count` | ✓（≥5 集） | ✓（≥5 集） | — | ✓（≥5 集） |
| 设置价格 `coin_price_per_episode` | — | ✓ | — | ✓ |
| 选标签（仅 enabled） | ✓ | ✓ | — | ✓ |
| 提交审核 | ✓ | — | — | — |
| 审核队列 / 通过 / 驳回 | — | ✓ | — | ✓ |
| 上架 / 下架 / 排期 | — | ✓ | — | ✓ |
| 删除剧 | — | — | — | ✓ |
| 看自己的播放 / 用户 / 转化 / 收益 | ✓ | — | — | — |
| 看全平台剧 / 观众 / 财务 | — | ✓（读） | ✓（读） | ✓ |
| 供应商与绑定账号管理 | — | — | — | ✓ |
| API Key / CLI（限本人范围） | ✓（read/write/upload） | ✓ | ✓（只读） | ✓ |

后端依赖：新增 `require_partner`；`require_admin` 收窄为 `STAFF_ROLES=(ops,finance,admin)`；审核接口沿用 `require_content_writer`（运营也要能审）。

## 4. 审核、上架与免费集数

### 4.1 流程

```
上剧方                              运营 / 管理员
──────────────────────────────────────────────────────────────
新建剧  status=draft, review_state=none
 上传分集（转码完成 → episode.status=in_review）
 提交审核 ────► review_state=pending
                  ├─ 通过 ─► approved，status: draft→in_review
                  │          进入「待上架」，上剧方看到「已通过待上架」
                  │          ⚠ 不自动 published
                  │          运营点「发布」→ published
                  └─ 驳回 ─► rejected + review_note（必填）
上剧方改内容 ────► review_state 自动回 none，需重新提交
published ───────► 上剧方只读；要改内容走「申请下架」(P4)
```

提交前五项校验（否则审核队列全是空壳）：

1. `cover_url` 非空；
2. `synopsis` ≥ 30 字；
3. 至少 1 集，且所有集 `status='in_review'`（转码完成）；
4. `review_state ∈ {none, rejected}` 且 `status='draft'`；
5. `floor ≤ free_episode_count ≤ episode_count`（floor = min(5, 总集数)）。

并发保护：`UPDATE ... WHERE id=:id AND review_state='pending'`，影响 0 行返 409。

### 4.2 审核通过后

`approved` 只是「内容合格」，不等于对外可见。运营可在「待上架」列表批量发布。上剧方的列表里这两种要分开显示：**已通过待上架** / **已上架**。

### 4.3 免费集数规则（下限 5 集）

上剧方可以定免费集数，但**不能把付费墙提前**到 5 集以内，否则等于自己拆掉转化漏斗的入口。

```python
MIN_FREE_EPISODES = 5
DEFAULT_FREE_EPISODES = 5
floor = min(MIN_FREE_EPISODES, episode_count)   # 总集数不足 5 时视为全免
```

- **新建剧默认 5**：`POST /partner/v1/dramas` 不传就落 5；前端 stepper 预填 5，少一步输入。
- **草稿期放宽**：建剧时还没传集，允许 `free_episode_count` 暂时大于当前集数（比如先按 5 集免费规划 20 集）。真正的 `floor ≤ free ≤ episode_count` 在**提交审核**和**发布**时才卡；已通过/已上架后再改，则每次保存都校验。

| 剧状态 | 可设范围（所有角色一致） |
|---|---|
| draft / rejected | `floor <= n <= episode_count` |
| approved / 待上架 / published / unpublished | `max(当前值, floor) <= n <= episode_count`（只能调大） |

- **全局约束，没有角色豁免**：运营/管理员也不能把已上架剧的免费集数降到 5 以下；确实要做全免活动时，用「临时免费」独立开关（后续需求），不要动 `free_episode_count`。
- 三处校验：**提交审核**、**发布**（`change_drama_status` → published）、**任何一次保存**（含运营在剧集管理里改免费集数）。前端在输入框下直接显示 `最少 {floor} 集`。
- 存量处理：线上 179 部已上架剧只有 **1 部** `free_episode_count=1`（均值 7.6）。因为改成全局约束，这一部一旦被编辑或重新发布就会被拦，所以上线时跑一次性脚本把它调到 5：

  ```sql
  UPDATE dramas SET free_episode_count = 5, updated_at = now()
   WHERE status = 'published' AND free_episode_count < 5;
  ```

  只影响 1 行，对用户是放宽（多给 4 集免费），不涉及退款；执行前先导出该行留档。
- 价格字段 `coin_price_per_episode` partner 传入直接忽略（不报错，前端漏改不至于整单失败），并记审计。

### 4.4 批量审核

供应商一次传 20 部时，逐条点通过太慢。加一个批量入口，但**不能变成绕过校验的后门**：

```
POST /admin/v1/reviews/batch
{ "drama_ids": [301, 302, 303], "decision": "approve", "note": "" }
```

- 单次 **≤50 条**，超出 422；`decision` 只有 `approve` / `reject`。
- **逐条跑与单条相同的校验**（`review_state='pending'`、集数/转码/封面等），不是「全有或全无」：能过的过，不能过的进 `skipped` 并带原因。
- 并发安全：每条仍用 `UPDATE ... WHERE review_state='pending'`，两个人同时点批量不会重复处理。
- 驳回时 `note` 必填，同一句话写进每条 `review_note`；需要不同理由就逐条驳回。
- 审计按剧各写一行，`payload.batch_id` 用同一个 uuid，能回溯「这次批量是谁点的」。
- 返回 `{ "done": 18, "skipped": [{"id": 31, "reason": "已被处理"}, {"id": 44, "reason": "分集未完成转码"}] }`。
- 前端：表头多选 + 顶部「批量通过 / 批量驳回」，确认弹窗列出 N 部剧名与预计跳过项；结果用 toast，并保留可展开的跳过清单。

## 5. 接口清单

### 5.1 上剧方 `/partner/v1`（全部 `Depends(require_partner)`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/me` | 账号 + 供应商 + 分成状态（是否已签约） |
| GET | `/dramas?status=&review_state=` | 只返回自己的 |
| POST | `/dramas` | 建剧；价格强制 0；**免费集数默认 5**（集数不足 5 时取集数） |
| GET | `/dramas/{id}` | 含分集、审核结果、驳回原因 |
| PATCH | `/dramas/{id}` | 白名单：title/synopsis/cover_url/tag_ids/free_episode_count/country_allowlist/age_rating |
| POST | `/dramas/{id}/cover` | multipart |
| POST | `/dramas/{id}/submit` | 提交审核 |
| POST | `/dramas/{id}/episodes` | 建集 |
| PATCH / DELETE | `/episodes/{id}` | 仅 draft 且未上传可删 |
| POST | `/episodes/{id}/upload-session` | 复用 `create_upload_session` |
| GET | `/episodes/{id}/upload-status` | 转码进度 |
| GET | `/stats?days=30` | 概览 + 分剧明细（见 §6） |
| POST | `/auth/password` | 复用改密逻辑 |

### 5.2 员工侧 `/admin/v1` 增补

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/suppliers` | ops 可读 | 含绑定账号、剧数 |
| POST / PATCH | `/suppliers`、`/suppliers/{id}` | admin | 资料（名称/联系人/状态）；**新建时 `revenue_share_bps` 缺省 8000，并写入首条 share_history** |
| POST | `/suppliers/{id}/share` | admin | 设分成：`{share_bps, note}`，关旧记录 + 插新记录，同步 `suppliers.revenue_share_bps` |
| GET | `/suppliers/{id}/share` | admin | 当前比例 + 变更历史（谁改的、何时生效） |
| POST | `/suppliers/{id}/status` | admin | 启用/禁用（同时禁用其账号） |
| GET | `/reviews?state=pending` | ops+admin | 审核队列 |
| POST | `/reviews/batch` | ops+admin | 批量通过/驳回：`{drama_ids, decision, note}`，单次 ≤50，返回 `{done, skipped:[{id,reason}]}` |
| POST | `/dramas/{id}/review` | ops+admin | `{decision, note}` |
| GET | `/dramas?supplier_id=&review_state=` | ops+admin | 列表加筛选 |
| POST | `/staff` | admin | 扩展：`role=partner` 时 `supplier_id` 必填且未被占用 |

### 5.3 计费（C 端改动，P1 一起上）

```
POST /v1/billing/checkout
{ "plan_code": "monthly", "source_drama_id": 123 }   # 可选，H5 从剧集/播放页打开时带
```

- `public_flow.CheckoutIn` 加字段；`create_checkout_session(source_drama_id=...)`；
- Stripe metadata 写 `source_drama_id`（一次性放 `payment_intent_data`，订阅放 `subscription_data`）；
- 落库：checkout 创建的 `Order` 直接带；webhook 补单时从 metadata 读；订阅续费订单继承 `subscriptions.source_drama_id`。

### 5.4 API Key 与 CLI（上剧方自动化上片）

现有基础可以直接复用：`admin_api_keys`（绑 `admin_users`、只存 sha256、`mk_live_` 前缀、scopes 只能收窄、`api_key_events` 记写操作）+ `cli/minitv` 0.3.0（47 条命令、TUS 断点续传）。partner 账号本身就是一行 `admin_users`，**不需要新表**，要加的是三样：角色闸门、scope 白名单、CLI 双 surface。

#### 5.4.1 角色闸门（最关键）

- `require_admin` 收窄为 `STAFF_ROLES` 后，partner 的 key 天然进不了 `/admin/v1/*`（403 `staff only`）。
- `require_partner` 必须**同时接受会话 token 和 API key**：抽一个 `resolve_credential(request, db)`——`mk_live_` 前缀走 `api_keys.resolve_key`，否则走 `resolve_session_token`，再判 `role=partner` 且 `supplier_id` 非空。
- 双保险：密钥鉴权里再加一条路径守卫——`owner.role == 'partner'` 且 path 不以 `/partner/v1/` 开头 → 403 `partner_scope_denied`。防止以后有人把 partner 接进某个 admin 依赖时静默放行。

#### 5.4.2 Scope 白名单

| scope | partner | ops | finance | admin | 覆盖路径 |
|---|:--:|:--:|:--:|:--:|---|
| `read` | ✓ | ✓ | ✓ | ✓ | GET/HEAD |
| `write` | ✓ | ✓ | — | ✓ | 建剧/改剧/建集/提交 |
| `upload`（新增） | ✓ | ✓ | — | ✓ | upload-session / upload-complete / 封面 / 字幕 |
| `delete` | — | ✓ | — | ✓ | 删集（删剧仍限 admin） |
| `billing` | — | — | ✓ | ✓ | 财务/套餐/会员 |
| `staff` | — | — | — | ✓ | 密钥管理、账号 |

- 新增 `upload` 到 `SCOPES`，与 `write` 分开：上传是最贵、最容易被滥用的动作，给只读/只改元数据的 key 不应顺带获得上传能力。
- 创建/修改 key 时校验 `scopes ⊆ allowed_scopes(owner.role)`：partner 传 `billing`/`staff`/`delete` → **422**，不是静默裁剪。
- **空 scopes 不能等于全量**：现有 `granted_scopes()` 把「scopes 为空」解释为 `set(SCOPES)`（历史兼容）。partner 必须改成 `(set(scopes) or set(SCOPES)) & ROLE_SCOPES[role]`，否则一个「跟随账号」的 partner key 会自带 `staff`，理论上能调密钥管理接口自建 key。这一行是提权漏洞的封口，P2 必须带上。
- 兼容：判定上传时**`write` 视为包含 `upload`**（老 key 不受影响），但只有 `upload` 的 key 不能改元数据、不能提交审核。
- 路径 → scope：`/partner/v1/*` GET→`read`；`/episodes/{id}/upload-session|upload-complete`、`/dramas/{id}/cover`→`upload`；其余写→`write`；`/partner/v1/api-keys*`→`staff`（**密钥管理只能用会话操作**，不能用 key 创建 key，避免提权链）。
- 有效权限仍是 `min(账号当前角色, scopes)`，角色每次请求重新读库。

#### 5.4.3 自助管理

| 方法 | 路径 | 权限 |
|---|---|---|
| GET | `/partner/v1/api-keys` | partner（只看自己的） |
| POST | `/partner/v1/api-keys` | partner（scope 限 read/write/upload，TTL 默认 90 天） |
| POST | `/partner/v1/api-keys/{id}/rotate` | partner（明文只返回一次） |
| POST | `/partner/v1/api-keys/{id}/revoke` | partner |
| GET | `/partner/v1/auth/keys` | partner（本人 key 列表，CLI 用） |

复用 `api_key_router` 的服务函数，`owner` 固定为 `me.id`。管理员在「上剧方管理」里**只能看到**「有无密钥 / 最近使用时间」，不能替对方创建（避免平台持有商家凭据）；要代建就重置密码让对方自己建。

#### 5.4.4 限流与配额

- 速率：partner key 默认 **120 rpm**（现有 `API_KEY_RATE_LIMIT` 是全局 600，按 key 维度加严）。
- 上传配额：每供应商每天默认 **200 个 upload-session**、单文件 200MB 不变；超限 429 带 `retry_after`。按 `supplier_id` 计数，不是按 key（换 key 不能绕过）。
- 禁用供应商时：`status=disabled` + **撤销其全部 API key** + 清会话，同一个操作里完成；账号被禁用后即使 key 未撤销，`resolve_key` 也会因 `status != active` 返回 401（双保险）。

#### 5.4.5 CLI

同一份 `cli/minitv`，不发布第二个二进制。版本目标 **0.4.0**。

- `config set-key` 先试 `GET /admin/v1/auth/me`，403 再试 `GET /partner/v1/me`，把 `surface: staff|partner` 写进 profile；`whoami` 显示角色与 scopes。
- 命令树按 surface 裁剪：partner 执行 staff 命令时给中文提示（「当前密钥是上剧方身份，不能发布剧集」），不是甩一段 403 JSON。
- 新增 partner 命令：

  ```bash
  minitv drama list | get | create | update
  minitv drama submit <id>                    # 提交审核
  minitv drama takedown <id> --reason "..."   # 申请下架
  minitv episode add <drama_id> --number 1
  minitv episode upload <episode_id> <file>   # TUS，--resume 断点续传
  minitv episode status <episode_id>
  minitv stats --days 30
  minitv settlements --year 2026
  minitv keys list | create --scopes read,write,upload | rotate | revoke
  ```

- staff 侧补充：`minitv review list|approve|reject`（审核队列），其余沿用现有 47 条命令。
- 发布：`release.py r2 --dist cli --source cli --prefix cli --allow-no-index`（`https://minitv.us/cli/install.sh`）。

#### 5.4.6 一个口径冲突要拍板

之前定的是「审核通过不自动上架，必须运营点发布」。所以 partner CLI 只给 `submit`（提交审核）和 `takedown`（申请下架），**不给 `publish`**；真正的上架/下架由运营用 staff 身份执行。

如果你说的「方便上下架剧」是指**上剧方自己就能把剧放上线**，那就和这条决策冲突，需要二选一：

- A（当前方案）：partner 提交 → 运营上架，CLI 无 `publish`；
- B：审核通过即自动上架，partner 可 `publish/unpublish`，但内容未复核就对外可见的风险由平台承担。

方案默认 A。要改 B 的话告诉我，我把审核流的第 4 节一起改。

## 6. 数据与收益

### 6.1 指标口径（先定义再实现）

| 指标 | 口径 |
|---|---|
| 播放量 | `playback_events.kind='start'` 计数 |
| 完播数 / 完播率 | `kind='complete'` 计数；率 = 完播 / 播放（同周期） |
| 观看用户数 | 该供应商剧的 `distinct user_id`（游客无 user_id，不计） |
| 人均观看集数 | `distinct episode_id / distinct user_id` |
| 金币解锁次数 / 人数 | `coin_ledger.biz_type='unlock'` join 集 → 剧 → 供应商 |
| 会员首单数 / 金额 | `orders.status='paid'`、`source_drama_id ∈ 自己的剧`，且是该用户在该剧的**第一笔**已支付订单；金额取 `amount_cents` |
| 会员转化率 | 首单人数 / 观看用户数 |
| 收益 | 首单净额（扣退款）× **该笔订单 `paid_at` 时生效的** 分成比例；比例变更不改写历史 |

**只算首单**：同一用户对同一部剧的第二次及以后购买（一次性时长包复购、换套餐重买）不计入上剧方收益，也不计入「会员首单数」。实现上不需要额外标记，聚合时按用户+剧取最早一笔即可：

```sql
SELECT DISTINCT ON (o.user_id, o.source_drama_id) o.*
  FROM orders o
 WHERE o.status = 'paid' AND o.source_drama_id = ANY(:drama_ids)
 ORDER BY o.user_id, o.source_drama_id, o.paid_at ASC, o.id ASC;
```

订阅续费天然不计入：现网**不为续费落 `orders`**（`invoice.paid` 只同步 `subscriptions`，`apply_stripe_event` 只有 checkout / subscription 两类分支），订单表里本身就只有首单。将来如果补续费订单，必须加 `is_renewal` 标记并在本查询里排除。

**金币不代表现金**：金币目前只从任务奖励发放（`coin_ledger.biz_type=register_reward/checkin/ad_reward`），没有购买入口，所以金币解锁只作内容消费指标，不计金额收益。将来若上金币包，再加金币 GMV。

### 6.2 归因链路

```
H5/App 剧详情或播放页 → PremiumSheet(sourceDramaId)
   → POST /v1/billing/checkout { source_drama_id }
   → Stripe metadata[source_drama_id]
   → orders.source_drama_id（首单）
   → subscriptions.source_drama_id（订阅续费继承）
   → /partner/v1/stats 按剧汇总
```

规则：

- 一次开通只归一部剧（最后点击归因）；从「我的」页直接开通 → `NULL`（自然流量）。
- 归因的是**开通动作**，不要求用户看过该剧；不改变会员权益（会员仍全平台通用）。
- 退款：`orders.status='refunded'` 时统计扣减，收益同步冲减。
- 续费：记录继承用于统计；**结算是否计入续费**是合同口径问题（见 §12）。

### 6.3 数据页（`/partner/v1/stats?days=30`）

```json
{
  "range": {"days": 30, "from": "...", "to": "..."},
  "summary": {
    "plays": 12400, "completes": 3100, "completion_rate": 0.25,
    "watchers": 2100, "unlocks": 380, "unlock_users": 240,
    "members": 36, "member_amount_cents": 35964, "member_rate": 0.017,
    "revenue_cents": 17982, "share_bps": 8000, "currency": "USD"
  },
  "dramas": [
    {"id": 301, "title": "…", "plays": 8000, "completes": 2000,
     "watchers": 1400, "unlocks": 260, "members": 22, "member_amount_cents": 21978}
  ]
}
```

- `members` / `member_amount_cents` / `revenue_cents` 都是**首单口径**；`revenue_cents` 用每笔订单 `paid_at` 当时生效的比例逐笔算完再求和，不是「总额 × 当前比例」。
- 默认 30 天，最长 90 天（明细表直查，防拖库）；`days>90` → 422。
- 金额一律 **USD 原币**（`amount_cents / 100`）展示，不做汇率折算；分成比例为 8000（默认）时按 80% 计。
- 只给聚合，不给用户明细（无邮箱/手机号/单用户行为）；**不提供地域/语言分布**（已确认不做）。

### 6.4 性能

`playback_events` 会持续增长。P4 前先靠 `(episode_id, created_at)` 索引 + 90 天窗口；数据量上来后再加日聚合表 `supplier_daily_stats(supplier_id, day, plays, completes, watchers, unlocks, orders, gmv_cents)`，由定时任务回填，接口只读聚合表。

### 6.5 月度结算单

按月生成，**只结算首单**，USD 原币，不做汇率折算。

```sql
CREATE TABLE supplier_settlements (
  id BIGSERIAL PRIMARY KEY,
  supplier_id BIGINT NOT NULL REFERENCES suppliers(id),
  period CHAR(7) NOT NULL,                     -- '2026-10'，自然月（America/Los_Angeles）
  currency CHAR(3) NOT NULL DEFAULT 'USD',
  orders_count INT NOT NULL DEFAULT 0,         -- 首单笔数（不含退款）
  gross_cents BIGINT NOT NULL DEFAULT 0,       -- 首单总额
  refund_cents BIGINT NOT NULL DEFAULT 0,      -- 当月退款（按 refunded_at）
  net_cents BIGINT NOT NULL DEFAULT 0,         -- gross - refund
  payable_cents BIGINT NOT NULL DEFAULT 0,     -- 逐笔 × 下单时比例，退款同样冲减
  status VARCHAR(16) NOT NULL DEFAULT 'draft', -- draft / confirmed / paid / void
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  confirmed_at TIMESTAMPTZ,
  paid_at TIMESTAMPTZ,
  note VARCHAR(500) NOT NULL DEFAULT '',
  UNIQUE (supplier_id, period)
);
```

生成规则：

1. 每月 **1 日 03:00（LA）** 生成上月结算单，状态 `draft`；
2. 取该月 `paid_at` 的首单（`DISTINCT ON (user_id, source_drama_id)`），逐笔乘「该笔 `paid_at` 时生效的比例」求和 → `payable_cents`；
3. 退款按 `refunded_at` 落在当月的订单冲减；若当月无可冲金额，`payable_cents` 允许为负，结转到下月（不做跨月追溯修改已确认的单）；
4. `draft` 可重算（幂等：先删当月 draft 再插）；`confirmed` 之后禁止改数，只能 `void` 后重新生成；
5. 供应商只能看自己的结算单（`GET /partner/v1/settlements?year=2026`），看不到别家。

接口：

| 方法 | 路径 | 权限 |
|---|---|---|
| GET | `/partner/v1/settlements?year=` | partner（自己的） |
| GET | `/admin/v1/settlements?period=&status=` | finance / admin |
| POST | `/admin/v1/settlements/{id}/confirm` | admin |
| POST | `/admin/v1/settlements/{id}/paid` | admin |
| POST | `/admin/v1/settlements/generate?period=` | admin（补跑） |

不自动打款：只出对账单与应付金额，线下付款后由 admin 标 `paid`（避免在没有财务系统时假装闭环）。

## 7. 前端改造

### 7.1 菜单按角色分流（必做）

上剧方是外部人员，不能让他看见「财务订单」「用户管理」这些名字。

| 角色 | 可见入口 |
|---|---|
| partner | 我的剧集、数据中心、**API 密钥**、账号设置（改密） |
| ops | 数据看板、剧集管理、**待审核**、**待上架**、首页分组、首页运营、金币与任务、评论审核、自动审核、标签库 |
| finance | 数据看板、财务订单、用户管理、会员套餐（只读） |
| admin | 全部 + 账号管理 + 上剧方管理 |

### 7.2 页面

| 页面 | 角色 | 要点 |
|---|---|---|
| `PartnerDramas.tsx` | partner | 状态徽标：草稿 / 待审核 / 已通过待上架 / 已上架 / 已驳回 / 已下架；驳回原因就地展开 |
| `PartnerDramaEdit.tsx` | partner | 复用上传逻辑，隐藏发布/下架/删除/改价 |
| `PartnerStats.tsx` | partner | 7/30/90 天切换；四个指标卡 + 分剧表 + 收益块（按当前生效分成显示，USD） |
| `PartnerApiKeys.tsx` | partner | 新建/轮换/撤销密钥；scope 勾选框只有 read/write/upload；明文只显示一次；显示最近使用与到期日 |
| `ReviewsPage.tsx` | ops/admin | 待审核队列；支持多选**批量通过/驳回**（≤50，逐条校验，展示跳过原因）；通过/驳回（驳回原因必填） |
| `PendingPublishPage.tsx` | ops/admin | 已通过待上架，支持批量发布 |
| `SuppliersPage.tsx` | admin | 供应商 CRUD、绑定账号、**分成比例（默认 80%，含变更历史/生效时间）**、重置账号密码 |

**不要复用 `DramaEdit.tsx` 给上剧方**：那页混着删除、发布、改价、分组逻辑，靠 `if` 屏蔽迟早漏。

### 7.3 计费侧改动

H5 `PremiumSheet`（以及 App 对应弹层）新增 `sourceDramaId`，在剧详情/播放页打开时传入；「我的」页打开时传空。开通成功后不需要额外埋点，订单里已有归因。

## 8. 数据隔离六条硬规则

1. partner 的所有查询必须带 `supplier_id` 条件，统一走 `_partner_drama_or_404(db, me, id)`；禁止直接 `db.get(Drama, id)`。
2. 写操作先判归属（404，不泄露存在性）、再判状态（409）。
3. 上传 session / 封面 / 字幕每个入口都校验 `episode → drama → supplier_id`。
4. `/admin/v1/*` 对 partner 一律 403，靠 `require_admin` 白名单，不靠各接口自觉。
5. 统计 SQL 的 `WHERE` 必须先落到「自己的剧 id 集合」，再 join 播放/订单，避免漏条件扫全表。
6. 审计 payload 只记自己的 `supplier_id`、剧 id、动作。

## 9. 兼容与迁移顺序

1. 迁移（suppliers、账号 1:1、dramas 审核列、剧名索引、归因列、播放索引）；
2. `STAFF_ROLES` 白名单（**先于任何 partner 账号创建**）；
3. 现有后台回归（ops/finance/admin 行为不变，331 项测试应全绿）；
4. 建第一个供应商 + 账号，开放 `/partner/v1`；
5. 线上 212 部剧保持自营（`supplier_id=NULL`），可在剧集详情补录归属。

## 10. 分阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P1 隔离 + 归因地基** | 迁移、suppliers、role=partner、`require_admin` 白名单、菜单分流、partner 只读、**checkout 记 source_drama_id**、供应商 CRUD + 分成比例设置 | 上剧方登录只看自己的剧；所有 admin 接口 403；新订单有归因；后台能设分成并留历史 |
| **P2 上剧能力** | partner 写接口（建剧/分集/上传/封面/免费集数）+ 提交校验 + 审计 + **partner 自助 API Key** + **CLI 0.4.0（drama/episode/upload/submit/stats）** | 能完整上传一部剧并提交；能用 CLI 跑通「建剧→上传→提交」 |
| **P3 审核与上架** | review_state 流转、待审核/待上架、通过/驳回、**批量审核（≤50）**、邮件通知、并发保护 | 运营完成一次「审核→上架」闭环；20 部剧可一次过审 |
| **P4 数据与收益** | `/partner/v1/stats`、分成比例（P1 已有设置，这里接统计）、月度结算单 + 定时生成、partner 结算页、日聚合表 | 上剧方能看自己 30 天播放/转化/收益与上月结算单 |

P1 单独上线即有安全价值；归因字段越早上线，数据越完整。

## 11. 测试清单（关键 36 条）

| # | 用例 | 期望 |
|---|---|---|
| 1 | partner 调 `/admin/v1/dramas` | 403 |
| 2 | partner 列表只含自己的剧 | 不含他人/自营 |
| 3 | partner 读/改他人剧 | 404 |
| 4 | 同一供应商建第二个账号 | 409（唯一索引） |
| 5 | partner 传 `coin_price_per_episode` | 被忽略，库值不变 |
| 6 | partner 把免费集数设为 > 总集数 | 422 |
| 7 | 已上架剧把免费集数调小 | 409（需运营操作） |
| 8 | partner 改已 published 的剧 | 409 |
| 9 | 空壳剧提交 | 422 + 缺项 |
| 10 | 重复提交 | 409 |
| 11 | ops 审核通过 | approved + status=in_review，**不 published** |
| 12 | 驳回不带原因 | 422 |
| 13 | 两人同时审核 | 一个 200、一个 409 |
| 14 | 供应商被禁用 | 账号旧 token 401、新登录 401；剧不自动下架 |
| 15 | 带 `source_drama_id` 下单 | 订单落该剧；退款后统计扣减 |
| 16 | 两家供应商同名剧 | 都能创建；自营重名仍 409 |
| 17 | 改分成比例后查历史订单 | 变更前的订单仍按旧比例，变更后按新比例 |
| 18 | 同一用户对同一剧第二次购买 | 不计入首单数与收益 |
| 19 | 已上架剧把免费集数设为 4（总集数 ≥5） | 422；总集数 3 时设 3 通过 |
| 20 | 分成设 12000（>10000） | 422，且不落历史 |
| 21 | 新建供应商不传分成 | 落库 8000，并有一条 share_history |
| 22 | 运营把已上架剧免费集数改成 4 | 422（全局约束，无角色豁免） |
| 23 | 同月重复生成结算单 | 幂等：同 period 只有一条，重算覆盖 draft |
| 24 | confirmed 后改数 | 409；只能 void 重生成 |
| 25 | 退款落在次月 | 次月结算单冲减，历史月份不回溯 |
| 26 | partner key 调 `/admin/v1/dramas` | 403 `partner_scope_denied` |
| 27 | partner 建 key 时传 `billing`/`staff` | 422，不落库 |
| 28 | 只读 key 调 upload-session | 403 `scope_denied` |
| 29 | 撤销 key 后再请求 | 401 `key_revoked` |
| 30 | 禁用供应商 | 会话 401、key 401、key 行被撤销 |
| 31 | CLI `config set-key` 用 partner key | surface=partner，whoami 显示「上剧方」 |
| 32 | partner CLI 执行 `drama publish` | 本地拒绝并提示需运营上架（不发请求） |
| 33 | 新建剧不传 `free_episode_count` | 落库 5 |
| 34 | 总集数 3，提交时 free=5 | 422；改成 3 通过 |
| 35 | 批量通过 20 条，其中 2 条已被处理 | `done=18`，`skipped` 两条带原因 |
| 36 | 批量驳回不带 note / 一次 51 条 | 422；不产生任何状态变更 |

## 12. 风险与注意事项

| # | 风险 | 处置 |
|---|---|---|
| 1 | **`require_admin` 未改就建 partner = 全平台泄露** | P1 第一个 commit 改白名单；测试 #1 作为上线门槛 |
| 2 | 存量订单无归因 | 归因字段 P1 上线；历史订单接受 `NULL`，报表标注「归因上线前」 |
| 3 | 归因被刷（自然流量被算给某剧） | 只记录、不自动结算；结算按合同与人工核定，报表区分「归因开通」与「自然开通」 |
| 4 | 续费被算进上剧方收益 | 续费不落 `orders`，天然只有首单；复购用 `DISTINCT ON (user_id, source_drama_id)` 取最早一笔排除 |
| 5 | 金币被误当收入 | 明确金币无购买入口，不计金额；报表只作消费指标 |
| 6 | 已上架剧被供应商改内容绕审 | published 对 partner 全只读 |
| 7 | 免费集数被用来拆付费墙 | §4.3 的状态化限制 + 审计 |
| 8 | 播放明细表拖慢统计 | 90 天窗口 + `(episode_id, created_at)` 索引；P4 上日聚合 |
| 9 | 一供应商一账号，人员离职无备用 | 账号改绑 `supplier_id` 或重置密码；不要新建第二个（DB 会拒） |
| 10 | 供应商禁用后内容仍在架 | 明确不自动下架，运营决定（写进 SOP） |
| 11 | 剧名重复导致搜索歧义 | 跨供应商同名放开后，后台列表必须显示供应商；C 端搜索按热度排序 |
| 12 | 上传配额成本 | 按供应商统计体积，防单家占满 Bunny |
| 13 | 法务 | 入驻要内容授权承诺书；下架响应时效写进合同 |
| 14 | 分成比例配错（少打/多打一个 0） | 0–10000 校验 + 修改弹二次确认 + 历史可追溯；发现后只能改回，已生成结算单走 void 重算 |
| 15 | 改比例把历史收益一起改了 | `supplier_share_history` 按生效区间取值，逐笔订单算，不做「总额 × 当前比例」 |
| 16 | 存量剧 `free<5` 撞全局约束 | 上线时一次性脚本把那 1 部调到 5（只 1 行，对用户放宽），先导出留档 |
| 17 | **默认 80% 挤压平台毛利** | Stripe 约 2.9%+$0.30，另有 CDN/转码/税费；80% 分成下平台毛利约 17%。上线前用真实客单价核算，个别供应商可单独降到 70%/60% |
| 18 | 退款/拒付跨月 | 按 `refunded_at` 归月冲减，负值结转下月；chargeback 与退款同处理，不回溯已确认结算单 |
| 19 | 结算口径与 `/finance` 不一致 | 结算单只算首单；两个页面都要标注口径，避免财务对不上账时误解 |
| 20 | **partner 密钥泄露** | 默认 90 天 TTL、可一键撤销、`last_used_at` 可查、120 rpm 限流；密钥只能碰自己供应商的数据，泄露的爆炸半径有上限 |
| 21 | 上传配额被换 key 绕过 | 配额按 `supplier_id` 计数而不是按 key；每天 200 个 session，超限 429 |
| 22 | 用 key 自建 key 提权 | 密钥管理路径要求 `staff` scope，且 `staff` 不授予 partner；只允许会话操作 |
| 23 | CLI 版本与后端 surface 不匹配 | `set-key` 探测 `/partner/v1/me`，老版本 CLI 拿到 404 时提示升级；服务端不改既有 admin 契约 |
| 24 | 批量通过误放不合格内容 | 每条仍跑单条校验、不做全有或全无；确认弹窗列剧名；单次 ≤50；审计带 `batch_id` 可回溯 |
| 25 | 草稿期免费集数大于集数被误判 | 只在提交/发布/已上架后校验，草稿保存不拦；前端 stepper 上限随集数变化 |

## 13. 定稿参数（无待确认项）

| 参数 | 值 |
|---|---|
| 新建供应商默认分成 | `8000`（80%），创建时写首条 `supplier_share_history` |
| 分成可调范围 | 0–10000，仅 admin 可改，改动带 note 与生效时间 |
| 结算币种 | USD 原币（`amount_cents / 100`），不折人民币 |
| 结算周期 | 自然月（America/Los_Angeles）；次月 1 日 03:00 生成上月 `draft` |
| 结算范围 | **仅首单**；续费与复购不计入 |
| 免费集数下限 | 全局 5 集（总集数 <5 时全免），任何角色都不能更低 |
| 上剧方数据维度 | 播放/完播/观看用户/解锁/首单会员/转化/收益；无地域、无语言、无用户明细 |
| 一供应商账号数 | 1 个 |
| 上剧方自动化 | API Key（`read`/`write`/`upload`，TTL 90 天）+ CLI 0.4.0；无 `publish` 权限 |
| 新建剧免费集数 | 默认 5；草稿期不校验上限，提交/发布时按 `min(5,总集数)` 卡 |
| 剧集列表列 | 剧集 / 状态 / 集数 / 免费集数 / 操作（**不含播放量**） |
| 批量审核 | 单次 ≤50，逐条校验，返回跳过原因；驳回共用一条原因 |

## 14. 上线验收清单（P1 发完先跑这一套）

```bash
# 1) 隔离：partner 调 admin 接口必须 403
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $PARTNER" \
  https://api.minitv.us/admin/v1/dramas          # 期望 403

# 2) 隔离：partner 列表只有自己的剧
curl -s -H "Authorization: Bearer $PARTNER" https://api.minitv.us/partner/v1/dramas | jq '.items | length'

# 3) 归因：下单带 source_drama_id，订单落库
curl -s -X POST https://api.minitv.us/v1/billing/checkout \
  -H "Authorization: Bearer $USER" -H 'Content-Type: application/json' \
  -d '{"plan_code":"monthly","source_drama_id":301}' | jq '.url'
#   → psql: select order_no, source_drama_id from orders order by id desc limit 1;

# 4) 分成：新建供应商默认 8000，改一次留两条历史
curl -s -H "Authorization: Bearer $ADMIN" https://api.minitv.us/admin/v1/suppliers | jq '.items[0].revenue_share_bps'

# 5) 免费集数：全局下限
curl -s -o /dev/null -w '%{http_code}\n' -X PATCH \
  https://api.minitv.us/partner/v1/dramas/301 \
  -H "Authorization: Bearer $PARTNER" -H 'Content-Type: application/json' \
  -d '{"free_episode_count":3}'                  # 期望 422
```

UI 走查：用 partner 登录 → 只看到「我的剧集 / 数据中心 / API 密钥 / 账号设置」；用 finance 登录 → 看不到「账号管理」「上剧方管理」；admin 能看到全部。

```bash
# 6) 上剧方 key 不能进 admin（路径守卫）
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $PARTNER_KEY" \
  https://api.minitv.us/admin/v1/dramas          # 期望 403 partner_scope_denied

# 7) 上剧方 key 只能拿 read/write/upload
curl -s -X POST https://api.minitv.us/partner/v1/api-keys \
  -H "Authorization: Bearer $PARTNER_SESSION" -H 'Content-Type: application/json' \
  -d '{"name":"ci","scopes":["billing"]}'        # 期望 422

# 8) CLI 全链路（上剧方）
minitv config set-key --profile partner --base-url https://api.minitv.us
minitv auth whoami                               # 期望 role=partner、surface=partner
minitv drama create --title "测试剧 A"            # 免费集数应为 5
minitv episode add <drama_id> --number 1
minitv episode upload <episode_id> ./ep1.mp4 --resume
minitv drama submit <drama_id>
minitv drama publish <drama_id>                  # 期望本地拒绝：需运营上架

# 9) 批量审核
curl -s -X POST https://api.minitv.us/admin/v1/reviews/batch \
  -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"drama_ids":[301,302,303],"decision":"approve"}'
#   → {"done":3,"skipped":[]}；再调一次应为 done:0 + 三条 skipped
```

## 15. 运营 SOP（跟着上线一起发）

1. **审核时效**：工作日 24h 内给出通过/驳回；驳回必须写清「缺什么、怎么改」。
2. **上架节奏**：审核通过进入待上架，运营按首页排期决定何时发布，不承诺供应商具体上线日期。
3. **下架响应**：涉及版权/合规投诉，先下架后补流程（`unpublished` 会触发 CDN purge）。
4. **结算对账**：每月 3 号前确认上月 `draft` 结算单；有争议先 `void` 再重算，不在 `confirmed` 上直接改数。
5. **账号生命周期**：供应商换人只改绑 `supplier_id` 或重置密码，不新建第二个账号（DB 唯一索引会拒）。
