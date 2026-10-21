# 后台 API Key 整改方案（独立）

> 范围：**只做 API Key 体系与鉴权接口整改**，不涉及 CLI 与内容接口语义变更
> 前置阅读：`docs/drama-cli-plan.md`（CLI 整体方案）
> 迁移基准：当前 alembic head = `b7f2c9d41e03`（`staff_account_lifecycle`）
> 状态：待评审

## 1. 目标与非目标

**目标**

1. 给自动化/Agent 一个长期可用、可撤销、可审计的机器凭证（不再存后台口令）。
2. 凭证**绑定到具体 staff 账号**，权限永远不超过这个人；可再按 scope 收窄。
3. 后台可完成「新建 / 查看 / 改名改权限改有效期 / 轮换 / 撤销 / 删除」全生命周期管理。
4. **现有 37 个内容接口一行不改**即可被 key 调用。

**非目标**

- 不改 CLI 代码（下一阶段）。
- 不改 C 端用户鉴权（`users` / `minitv_user_session`）。
- 不引入 OAuth / JWT / 多租户。
- 不做「key 自服务签发」：只有 admin 能发（避免 ops 给自己造长期凭证绕过管控）。

## 2. 现状

```
Authorization: Bearer <token>
        │
        ▼
ops/router.py:44  _bearer_token()        —— 只解析出字符串
ops/router.py:51  require_admin()        —— Redis GET minitv_admin_session:<token> → user_id
        │                                   → 查 admin_users → status 必须 active
        ├── require_content_writer()      —— role ∈ {ops, admin}
        └── require_admin_role()          —— role == admin
```

- 会话 token 由 `POST /admin/v1/auth/login` 签发，**TTL 固定 8h**（`ops/service.py:24`），Redis 里只有 `token → user_id`。
- 全站 41 个后台端点都挂在这三个依赖上，所以**只改 `require_admin` 一处**就能让所有端点支持 key。
- 后台前端 `admin/src/api.ts` 在 401 时会清 token 并跳登录页 —— key 走同一响应码不影响它（前端永远发会话 token）。

## 3. 目标鉴权链路

```
Authorization: Bearer <credential>
        │
        ├─ 以 "mk_live_" 开头 ──► 查 admin_api_keys（按 key_prefix）
        │                          ├ 校验 sha256 + status + expires_at
        │                          ├ 取绑定 AdminUser，status 必须 active
        │                          ├ 按路径策略校验 scopes（仅 key 身份生效）
        │                          └ 写 api_key_events（仅写方法）+ 节流 last_used_at
        │
        └─ 其它 ───────────────► 原 Redis 会话路径（完全不变）
```

两个身份在依赖里被抹平成同一个 `AdminUser`，所以下游 `require_content_writer` / `require_admin_role` 不需要知道调用方是人是 key。

## 4. 数据模型

### 4.1 `admin_api_keys`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `admin_user_id` | BIGINT NOT NULL → `admin_users(id)` | **绑定的人**，不可改 |
| `name` | VARCHAR(64) | 用途名，如 `agent-ops/upload-bot` |
| `key_prefix` | VARCHAR(24) UNIQUE | `mk_live_` + 8 hex，用于展示与查表 |
| `key_hash` | VARCHAR(64) | `sha256(明文)`，明文不落库 |
| `scopes` | JSONB DEFAULT `'[]'` | 空数组 = 跟随账号角色 |
| `status` | VARCHAR(16) | `active` / `revoked`（CHECK） |
| `expires_at` | TIMESTAMPTZ NULL | NULL = 永不过期 |
| `last_used_at` | TIMESTAMPTZ NULL | 60s 节流更新 |
| `created_by` / `created_at` | BIGINT / TIMESTAMPTZ | |
| `revoked_at` / `revoked_by` / `revoke_reason` | | 撤销留痕 |

索引：

```sql
CREATE UNIQUE INDEX uq_admin_api_keys_prefix ON admin_api_keys(key_prefix);
CREATE UNIQUE INDEX uq_admin_api_keys_user_name_active
    ON admin_api_keys(admin_user_id, name) WHERE status = 'active';
CREATE INDEX ix_admin_api_keys_user_status ON admin_api_keys(admin_user_id, status);
```

> `user_name_active` 是**部分唯一索引**：撤销后同名可以重建，历史行不冲突。

### 4.2 `api_key_events`（增补，可裁剪）

现有 20+ 处 `audit_logs` 只记 `actor_id=人`，记不到「哪把 key」。这张小表补上：

| 列 | 类型 |
|---|---|
| `id` | BIGSERIAL PK |
| `key_id` | BIGINT → `admin_api_keys(id)` ON DELETE CASCADE |
| `staff_id` | BIGINT（冗余，便于按人查） |
| `method` / `path` / `status_code` / `duration_ms` | |
| `created_at` | TIMESTAMPTZ DEFAULT now() |

只对 `POST/PATCH/PUT/DELETE` 落行，GET 不落（否则一天几万行）。保留 90 天，由清理脚本删除。

## 5. 权限模型

### 5.1 scope 词表（5 个）

| scope | 覆盖 | 默认 |
|---|---|---|
| `read` | 所有 `/admin/v1/*` GET | ✔ 所有 key |
| `write` | 建/改/上传/发布/封面/字幕/标签/分组 | ✔ 运营类 key |
| `delete` | 删剧、删分集 | ✘ 需显式授予 |
| `billing` | `/finance` `/plans` `/rewards` `/coins` `/membership` `/billing` | ✘ |
| `staff` | `/staff` `/api-keys` 自身 | ✘ 永不授予 Agent |

**有效权限 = min(账号当前角色, scopes)**，scopes 只能收窄。角色每次请求现读，不缓存。

### 5.2 路径 → scope 策略（只在 key 身份下生效）

按顺序匹配，命中即停：

| # | 匹配 | 需要 scope |
|---|---|---|
| 1 | `^/admin/v1/(api-keys\|staff)` | `staff` |
| 2 | `^/admin/v1/(finance\|plans\|rewards\|coins\|membership\|billing)` | `billing` |
| 3 | `POST ^/admin/v1/dramas/\d+/delete`、`DELETE ^/admin/v1/episodes/\d+` | `delete` |
| 4 | 方法 = GET | `read` |
| 5 | 其余 | `write` |

会话身份（后台 UI）**不做该策略校验**，保持现有角色行为，避免把管理界面锁死。

### 5.3 生命周期联动

| 事件 | key 表现 | 依赖 |
|---|---|---|
| 账号禁用 | 401 `account_disabled` | 解析时查 `admin_users.status` |
| 角色降级 | 立即降权（读角色实时值） | 无缓存 |
| 改口令 / 重置口令 | **不受影响** | key 与 `password_hash` 无关 |
| key 过期 | 401 `key_expired` | 比对 `expires_at` |
| key 撤销 | 立即 401，行保留 | `status=revoked` |
| 轮换 | 旧 key 同事务立即失效 | 无宽限期 |

## 6. 接口规格

以下 8 个端点，全部挂在 `/admin/v1`，`staff` 之外的端点要求 `require_admin_role`。

### 6.1 `POST /admin/v1/api-keys` — 签发

```json
// 请求
{"user_id": 3, "name": "agent-ops/upload-bot", "scopes": ["read", "write"], "expires_in_days": 90}
```

```json
// 201
{"id": 7, "user_id": 3, "username": "agent-ops", "name": "agent-ops/upload-bot",
 "key": "mk_live_a7f3c9d2_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
 "key_prefix": "mk_live_a7f3c9d2",
 "scopes": ["read", "write"], "expires_at": "2027-01-19T02:40:00+00:00"}
```

规则：

- `user_id` 必须存在且 `status=active`，否则 422。
- `name` 同用户下 active 唯一，冲突 409 `key_name_conflict`。
- `scopes` 缺省 `["read","write"]`；必须是 5 个词表值的子集，否则 422。
- `expires_in_days` 缺省 90，允许 `30/90/180/365`；传 `null` = 永不过期（仅 admin 端点可传）。
- **`key` 字段只在此响应出现一次**，服务端只留 hash。

### 6.2 `GET /admin/v1/api-keys` — 列表

Query：`user_id` `status` `q`（匹配 name / prefix / username）`page` `page_size`(≤100)

响应项：`id / name / user_id / username / display_name / key_prefix / scopes / status / expires_at / last_used_at / created_at / created_by`

附带汇总：`summary: {active, expiring_soon(≤14d), expired, revoked}`。
**永不返回 `key` 或 `key_hash`。**

### 6.3 `PATCH /admin/v1/api-keys/{id}` — 改

```json
{"name": "upload-bot-v2", "scopes": ["read"], "expires_at": "2027-03-01T00:00:00Z"}
```

- 只能改这三个字段；`admin_user_id` 不可改（要换人 = 新建 + 撤销旧的）。
- 已撤销的 key 返回 409 `key_revoked`。

### 6.4 `POST /admin/v1/api-keys/{id}/revoke` — 撤销

```json
{"reason": "泄漏 / 任务结束"}
```

幂等：已撤销再调返回 200，`{"status":"revoked","revoked_at":"..."}`。

### 6.5 `POST /admin/v1/api-keys/{id}/rotate` — 轮换

```json
{"expires_in_days": 90}
```

同事务内：旧行 `status=revoked, revoke_reason='rotated'` → 插入同名新行 → 返回新明文（结构同 6.1）。
旧 key 立即 401，不设宽限。

### 6.6 `DELETE /admin/v1/api-keys/{id}` — 物理删

- 仅 `last_used_at IS NULL`（从未用过）允许，204。
- 用过 → 409 `key_in_use`，提示改走 revoke。

### 6.7 `GET /admin/v1/api-keys/{id}/events?limit=50` — 使用记录

返回最近的写操作：`method / path / status_code / created_at`。给「这把 key 到底改了什么」留证据。

### 6.8 `GET /admin/v1/auth/keys` — 本人密钥

任何登录 staff（含会话身份）可查自己名下的 key：`name / key_prefix / scopes / status / expires_at / last_used_at`。无明文、无他人。

## 7. 错误码

鉴权层返回**结构化 detail**（`{"code": "...", "message": "..."}`），CLI 依赖 `code` 区分处置动作。

| code | HTTP | 触发 | 客户端应做什么 |
|---|---|---|---|
| `missing_credential` | 401 | 无 Authorization | 检查环境变量 |
| `invalid_key` | 401 | prefix 不存在 / hash 不匹配 | 换 key |
| `key_revoked` | 401 | 已撤销 | 换 key / 找 admin 重发 |
| `key_expired` | 401 | 已过期 | 轮换 |
| `account_disabled` | 401 | 绑定账号被禁用 | 找 admin |
| `scope_denied` | 403 | scope 不足 | 不要重试 |
| `rate_limited` | 429 | 超限 | 退避后重试（带 `Retry-After`） |
| `key_not_found` | 404 | id 不存在 | — |
| `key_name_conflict` | 409 | 同用户同名 active key | 改名 |
| `key_in_use` | 409 | 删已用过的 key | 改 revoke |
| `key_revoked`(409) | 409 | PATCH 已撤销 key | — |

> `require_content_writer` / `require_admin_role` 的 403 仍是原来的纯字符串 detail（如 `role 'finance' cannot write content`），**不改**，避免动到 12 个现有测试的断言。CLI 侧把「403 且非 `scope_denied`」归为 `role_denied`。

## 8. 安全细则

| 项 | 做法 |
|---|---|
| 生成 | `prefix = "mk_live_" + token_hex(4)`；`secret = token_urlsafe(32)`；`明文 = prefix + "_" + secret` |
| 存储 | 只存 `sha256(明文)`，比对用 `hmac.compare_digest` |
| 传输 | 仅 Authorization 头；**禁止** query 参数（会进 access log） |
| 日志 | 明文与 hash 都不进访问日志；响应头加 `Cache-Control: no-store` |
| 限流 | Redis 固定窗口：每 key **600 次/分钟**，超限 429 + `Retry-After: 60` |
| `last_used_at` | Redis `SET NX EX 60` 通过才写库，避免每请求一次 UPDATE |
| 审计 | 签发/改/撤销/轮换各写一条 `audit_logs`，payload 只含 `{name, user_id, scopes, expires_at}` |
| 事件 | 写方法落 `api_key_events`（中间件，含实际 status_code） |
| 前端 | 明文只在弹窗内显示，关闭即丢；列表只显示前缀 |

## 9. 代码改动清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `backend/alembic/versions/f1a7c2e93b64_admin_api_keys.py` | 新增 | `down_revision = "b7f2c9d41e03"`；建 2 表 + 3 索引；`downgrade` 逆序删 |
| `backend/app/modules/ops/models.py` | 追加 | `AdminApiKey`、`ApiKeyEvent` 两个模型 |
| `backend/app/modules/ops/api_keys.py` | 新增 | 生成/校验/签发/撤销/轮换/限流/scope 策略（纯函数 + 服务层，便于单测） |
| `backend/app/modules/ops/router.py:44-71` | 修改 | `_bearer_token` 拆出 `_credential()`；`require_admin` 加 key 分支；`require_content_writer` 加 scope 断言 |
| `backend/app/modules/ops/api_key_router.py` | 新增 | 6.1-6.8 八个端点（单独文件，不挤进 700 行的 router.py） |
| `backend/app/main.py:80-90` | 修改 | `include_router(api_key_router)`；注册事件中间件 |
| `backend/tests/test_api_keys.py` | 新增 | 见 §11 |
| `admin/src/pages/ApiKeyDrawer.tsx` | 新增 | Staff 页密钥抽屉 + 新建/轮换弹窗 |
| `admin/src/pages/Staff.tsx:313` | 修改 | 操作列加「密钥」按钮，仅 admin 可见 |

关键约束：**`require_admin` 的会话分支保持原样**，只在其前面加 `if credential.startswith("mk_live_")` 分流；这样后台 UI、现有脚本、331 项测试全部不受影响。

## 10. 上线步骤与回滚

1. 本地：迁移 → 建 `agent-ops`(ops) 账号 → 签发 read-only key → curl 跑 `GET /dramas`（200）、`POST /dramas`（403 `scope_denied`）。
2. 后端全量测试（现有 331 + 新增）→ 后台 `npm run build`。
3. 合并 → `python3 scripts/release.py run --target server -- ./scripts/deploy_server.sh`（迁移随部署跑）。
4. admin 前端按常规 R2 发布；`Staff.tsx` 只在 admin 角色下显示入口。
5. 线上签发第一把 key（`scopes=["read"]`），观察 `api_key_events` 与 `last_used_at` 一周。
6. 回滚：代码回滚即可（旧代码不认 `mk_live_`，返回 401）；表可保留，`alembic downgrade -1` 会删两张表，**只在不打算再用 key 时执行**。

## 11. 测试矩阵

| # | 用例 | 期望 |
|---|---|---|
| 1 | 无 Authorization 头 | 401 `missing_credential` |
| 2 | 伪造 `mk_live_xxxxxxxx_...` | 401 `invalid_key` |
| 3 | 正确 key 调 `GET /dramas` | 200 |
| 4 | 过期 key（`expires_at` 过去） | 401 `key_expired` |
| 5 | 撤销后再调 | 401 `key_revoked` |
| 6 | 绑定账号被禁用后调 | 401 `account_disabled` |
| 7 | read key 调 `POST /dramas` | 403 `scope_denied` |
| 8 | write key 调 `POST /dramas/{id}/delete` | 403 `scope_denied` |
| 9 | ops 角色 + write key 建剧 | 201 |
| 10 | admin 角色 key 无 `staff` scope 调 `/staff` | 403 |
| 11 | rotate 后：旧 key 401 / 新 key 200 | |
| 12 | `DELETE` 已用过的 key | 409 `key_in_use` |
| 13 | 同用户同名二次签发 | 409 `key_name_conflict` |
| 14 | 60 秒内 601 次请求 | 第 601 次 429 |
| 15 | 会话 token 调任意端点 | 200（回归） |
| 16 | 签发/撤销/轮换 | 各产生一条 `audit_logs` |
| 17 | 写方法调用 | `api_key_events` 落行，GET 不落 |

## 12. 验收标准

- [ ] 8 个端点全部可用，前端抽屉能完成「新建 → 看到前缀 → 轮换 → 撤销」。
- [ ] 明文只在签发/轮换响应出现，库、日志、列表接口均无明文与 hash。
- [ ] 现有 331 项后端测试全绿；新增 ≥ 17 项通过。
- [ ] 用 `agent-ops` 的 key 能完成一次真实的「建剧 → 传 1 集 → 发布」，且 `api_key_events` 有 4 条记录。
- [ ] 禁用 `agent-ops` 后该 key 立即 401，无需重启服务。

## 13. 风险

| 风险 | 应对 |
|---|---|
| `require_admin` 被所有端点复用，改错即全站 401 | 只加分支；先跑全量测试；本地先验 curl |
| 中间件写事件表拖慢写请求 | 只对写方法、单条 INSERT、失败吞掉不影响主流程 |
| 管理员误发 `staff` scope | 前端下拉不提供该选项；后端校验「agent 类 key 不得含 staff」 |
| 明文泄漏（截图、日志） | 弹窗一次性展示 + `Cache-Control: no-store` + 列表只前缀 |
| 与 8h 会话混淆 | 两条路径完全独立；`whoami` 会返回 `credential_type: session \| api_key` |

## 14. 实施记录（2026-10-21）

### 已落地

| 项 | 结果 |
|---|---|
| 迁移 | `f1a7c2e93b64_admin_api_keys.py`，`down_revision=b7f2c9d41e03`；本地 `alembic upgrade head` 通过 |
| 模型 | `ops/models.py`：`AdminApiKey` + `ApiKeyEvent`（部分唯一索引 `(admin_user_id, name) WHERE status='active'`） |
| 服务层 | `ops/api_keys.py`：生成/校验/scope 策略/限流/`last_used_at` 节流/CRUD |
| 端点 | `ops/api_key_router.py`：8 个（含 `api_key_events` 与 `/auth/keys`） |
| 鉴权 | `ops/router.py:52` 只加 `mk_live_` 分支；会话路径原样；401/403 改为结构化 `detail.code` |
| 事件 | `app/main.py` 增加中间件，仅写方法落 `api_key_events`，失败不影响请求 |
| 前端 | `admin/src/pages/ApiKeys.tsx` + `App.tsx` 菜单项「API 密钥」（仅 admin） |

### 验收结果

- **单测**：`tests/test_api_keys.py` 20 项；后端全量 **351 passed**（原 331 + 20，无回归）。
- **本地端到端（curl，:18110）**：23/23 通过，覆盖签发/鉴权/scope/轮换/撤销/删除语义/账号禁用/事件/会话回归。
- **UI（Playwright，:5173）**：登录 → 密钥列表 → 新建（选账号/权限/有效期）→ 一次性明文 → 列表回显，全链路通过；截图见 `outputs/api-keys-*.png`。
- **前端构建**：`tsc --noEmit` 与 `npm run build` 均通过。

### 与设计稿的偏差（有意为之）

1. `DELETE` 的判定用 `last_used_at IS NOT NULL`：**任何一次成功鉴权（含 GET）都会让 key 不可删**，因为 GET 也留下了归属痕迹。UI 上「删除」按钮据此置灰。
2. `PATCH` 的 `scopes: null` 视为「不修改」而不是「重置为默认」，避免误清权限。
3. 新增 `GET /admin/v1/auth/keys`：任何人可审计自己的密钥（只读、无明文）。
4. 前端未做「修改到期时间」入口（后端 `PATCH expires_at` 已支持）；延长有效期走轮换。

### 未完成 / 待决定

- **生产部署未执行**：需 `commit` 后走 `python3 scripts/release.py run --target server -- ./scripts/deploy_server.sh` + admin R2 发布。
- `staff` / `billing` scope 的界面不提供勾选，后端也未硬性禁止 admin 签发含 `staff` 的 key（有审计兜底），如需强约束可再加校验。
