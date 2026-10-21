# 剧集管理 CLI（`minitv`）整改与接入计划

> 状态：**已定框架**（2026-10-21），待评审后开工
> 已拍板：API Key 绑定到 staff 用户并带完整增删改 / 分集只删空壳 / CLI 为单文件纯标准库 Python
> 目标读者：后端、运营工具维护者、接入 Agent 的调用方

## 0. 决策记录

| 议题 | 结论 | 影响 |
|---|---|---|
| API Key 主体 | **绑定 `admin_users` 行**，键上可再收窄权限 | 角色只有一处真源；停用账号即刻废键 |
| Key 管理 | 需要**增删改查 + 轮换**，admin 角色专属 | 新增 6 个管理端点 + `admin_api_keys` 表 |
| 分集删除 | **只允许删空壳**（`not_uploaded` / `transcode_failed`） | 已发布/待审集必须先下架再删，不做 `--force` |
| CLI 载体 | **单文件、纯标准库 Python**（argparse + urllib） | 沙箱零依赖，风格对齐 `scripts/release.py` |
| Key 粒度 | 一个使用方一把 key（`agent-ops/upload-bot` 这种命名） | 泄漏只影响一把，可单独 rotate |
| Key 有效期 | 默认 **90 天**，可选 30/90/180/365；`永不=null` 仅 admin 可签 | 到期返回 401 且 `code=key_expired` |
| 沙箱剧 | **要**，用 profile 的 `sandbox_drama_id`，不引入新概念 | prod 上非沙箱剧的写操作需 `--allow-prod` |
| 首期范围 | **含 `episode upload-all`**（目录→建集→上传→发布一条链） | P3 与上传一起交付 |

## 1. 结论先行

现有 37 个内容接口已覆盖剧集管理绝大部分动作，**但直接包一层 shell 脚本给 Agent 用是不成立的**，卡在四个地方：

1. **没有机器凭证**。后台只有「用户名 + 口令 → Redis 会话 token（8h）」，Agent 要把口令明文存进 config 才能长期跑，8h 后必须重登。仓库里三个运营脚本正是这么干的（`scripts/publish_ready_dramas.py:52`、`repair_stalled_episodes.py:65`、`import_hotlist.py:222` 都默认 `ADMIN_PASS=admin-1234`），而线上口令今天已轮换 —— **这三个脚本现在线上跑不通**。
2. **分集不能删**。全仓没有 `DELETE /admin/v1/episodes/{id}`，Agent 建错一集只能整剧删掉重来。
3. **上传链路不可恢复**。TUS 授权头 1 小时过期（`bunny.py:217`），过期后无法重签（集已是 `transcoding`，再申请 `upload-session` 直接拒），也没有 `HEAD` 续传约定。
4. **写操作不幂等**。除金币解锁外无幂等键；`dramas.title` 唯一但 `create_drama` 没 catch `IntegrityError`（`ops/router.py:580`），Agent 超时重试拿到 500，无法判断「已建成」还是「没建成」。

所以顺序是：**先补 5 个后端前置能力（P0）→ 再做 CLI（P1-P3）→ 最后接 Agent（P4）**。CLI 不改任何现有接口语义，只做编排与防呆。

## 2. 现状盘点

### 2.1 已经能用的

| 能力 | 接口 | 备注 |
|---|---|---|
| 剧 CRUD | `GET/POST /dramas`、`GET/PATCH /dramas/{id}` | 缺 DELETE，删除走 `POST /dramas/{id}/delete` |
| 剧状态 | `POST /dramas/{id}/status` | **单跳**，draft→published 要调两次 |
| 排期 | `POST /dramas/{id}/schedule` + `/schedules/apply` | apply 没有 cron |
| 分集 | 建（单/批量）、列表、改 | 无删除 |
| 上传 | `upload-session` → TUS 直传 → `sync-status` → `upload-failed` | 字节不过后端 |
| 发布 | `publish` / `unpublish` / `bulk-status` | |
| 媒资 | 封面 set/clear、字幕上传 | 字幕无删除 |
| 标签 / 分组 | 各 4 / 8 个接口 | |

### 2.2 硬约束

| 约束 | 位置 | 对 CLI 的影响 |
|---|---|---|
| 会话 8h、只认 `Bearer <session_token>` | `ops/service.py:24`、`ops/router.py:51` | 不能做长期凭证 |
| 无幂等键（仅 `unlock` 用） | `common/models.py:14` | 重试可能重复建 |
| `dramas.title` 唯一但未兜 409 | `ops/router.py:580` | 重名 = 500 |
| 上传 ≤200MB / mp4,mov；TUS 签名 3600s | `upload_service.py:18`、`bunny.py:217` | 大文件需续传 |
| 无 staging 环境 | — | Agent 试错直接落生产 |
| `/openapi.json` 公网 200 | FastAPI 默认 | 接口面全量暴露（可关） |

## 3. 框架

### 3.1 形态：CLI 为主，MCP 薄适配

- **主形态**：单文件纯标准库 `minitv`（argparse + urllib + json），零第三方依赖。任何有 `python3` 的沙箱丢进去就能跑，不需要 pip / venv。
- **次形态**：`minitv mcp` 把同一批函数包成 MCP stdio server（手写 JSON-RPC，不引 SDK）。只在宿主支持 MCP 时用，业务逻辑不重复实现。

### 3.2 认证：绑定用户的 API Key

新表 `admin_api_keys`：

```sql
id            BIGSERIAL PK
admin_user_id BIGINT NOT NULL REFERENCES admin_users(id)   -- 绑定的人
name          VARCHAR(64)  NOT NULL                        -- "agent-ops/upload-bot"
key_prefix    VARCHAR(24)  NOT NULL UNIQUE                 -- mk_live_a7f3c9d2，用于展示和查表
key_hash      VARCHAR(64)  NOT NULL                        -- sha256(明文)，明文不落库
scopes        JSONB        NOT NULL DEFAULT '[]'           -- 收窄用，空=跟随账号角色
status        VARCHAR(16)  NOT NULL DEFAULT 'active'       -- active | revoked
expires_at    TIMESTAMPTZ  NULL                            -- NULL = 永不过期（仅 admin 可签）
last_used_at  TIMESTAMPTZ  NULL
created_by    BIGINT       NULL REFERENCES admin_users(id)
created_at / revoked_at / revoked_by / revoke_reason
```

明文形如 `mk_live_a7f3c9d2_<43 字符>`，**只在签发/轮换的响应里出现一次**。

**鉴权解析**（只改 `require_admin`，其余 37 个接口零改动）：

```
Authorization: Bearer mk_live_...  →  取 key_prefix 查表 → 校验 sha256 + status + expires_at
                                   →  load AdminUser(id) → status 必须 active
                                   →  返回 AdminUser（roles/content_writer/admin_role 全部照旧生效）
Authorization: Bearer <其它>        →  走原 Redis 会话（后台 UI 不受影响）
```

**权限取交集**：有效权限 = `min(账号当前角色, scopes)`。scopes 只能收窄不能放大，避免一把 ops 的 key 被改出删剧能力。scope 词表与路径策略**以 `docs/admin-api-key-remediation.md` §5 为准**（5 个：`read` / `write` / `delete` / `billing` / `staff`；Agent 只给前两个）。

| scope | 覆盖 |
|---|---|
| `read` | 所有 GET（默认必带） |
| `write` | 建/改/上传/发布/封面/字幕/标签/分组 |
| `delete` | 删剧、删分集（最危险，默认不给 Agent） |
| `billing` | 财务/套餐/金币/会员（Agent 不授予） |
| `staff` | 账号与密钥管理（Agent 永不授予） |

**生命周期联动**（这是"绑人"的关键收益，必须写进测试）：

| 事件 | 对 key 的影响 | 实现方式 |
|---|---|---|
| 账号被 `disabled` | 下一次请求 401 `account_disabled` | 解析时查 `status` |
| 账号角色降级 ops→finance | key 立即跟着降权 | 角色每次现读，不缓存 |
| 账号改口令 / 重置口令 | **key 不受影响** | key 与 `password_hash` 无关 |
| 账号被删除 | 不可能（后台只禁用不删） | — |
| key 过期 | 401 `key_expired` | 解析时比对 `expires_at` |
| key 被 revoke | 立即 401，不可复活 | `status=revoked` |
| key rotate | 新明文生效、旧 key 立即废 | 一次事务里 revoke 旧 + 建新 |

**Key 管理端点**（全部 `require_admin_role`，只有 admin 能操作）：

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/admin/v1/api-keys?user_id=&status=&q=&page=` | 全量列表，含绑定人、前缀、最后使用时间、到期 |
| `POST` | `/admin/v1/api-keys` | `{user_id, name, scopes?, expires_in_days?}` → 返回**一次性明文** |
| `PATCH` | `/admin/v1/api-keys/{id}` | 改 `name` / `scopes` / `expires_at`；改不了绑定人 |
| `POST` | `/admin/v1/api-keys/{id}/revoke` | `{reason?}`，软撤销 |
| `POST` | `/admin/v1/api-keys/{id}/rotate` | 旧 key 立即废 + 返回新明文 |
| `DELETE` | `/admin/v1/api-keys/{id}` | **仅限从未使用过**（`last_used_at IS NULL`）的 key 物理删；用过的返回 409，请走 revoke |
| `GET` | `/admin/v1/auth/keys` | 本人查看自己的 key 列表（只有前缀，无明文） |

**可追溯性**：审计保持 `actor_id=绑定人的 id`（谁做的很清楚）。另加一张极小的 `api_key_events(key_id, staff_id, method, path, status_code, created_at)`，由中间件只对写方法落一条 —— 这样"某把 key 删了哪部剧"能查，又不用改动现有 20 多处审计调用点。

**限流**：Redis 计数，每 key 600 req/min、并发上传 2；超限返回 429 + `Retry-After`。

### 3.3 配置与优先级

`~/.config/minitv/config.toml`（0600），支持多 profile：

```toml
default_profile = "prod"

[profiles.local]
base_url = "http://127.0.0.1:18110"
api_key_env = "MINITV_KEY_LOCAL"

[profiles.prod]
base_url = "https://api.minitv.us"
api_key_env = "MINITV_KEY_PROD"
sandbox_drama_id = 0     # 0 = 未设置；设置后非该剧写操作需 --allow-prod
```

优先级：`--flag` > `MINITV_API_KEY` / `MINITV_BASE_URL` > `--profile` > `default_profile`。
**key 禁止用命令行参数传**（会进 shell history 与进程列表），只从环境变量或 0600 文件读。

### 3.4 输出契约

- stdout 是 TTY → 表格；非 TTY → JSON。`--format json|table|text` 强制。
- 成功 `{"ok":true,"data":{...},"meta":{"request_id":"...","elapsed_ms":123}}`
- 失败 `{"ok":false,"error":{"code":"conflict","message":"...","http_status":409,"hint":"..."}}`
- `--fields id,title,status` 裁剪、`--quiet` 只回 id（省 token）。

退出码：`0` 成功 / `1` 未知 / `2` 用法 / `3` 认证（含过期、撤销） / `4` 不存在 / `5` 状态冲突 / `6` 上传失败 / `7` 网络。

### 3.5 危险动作防呆

| 机制 | 行为 |
|---|---|
| `--dry-run` | 打印将发的 `METHOD path body`，不执行 |
| `--confirm <id 或 title>` | 删除类必填，值需与被删对象一致 |
| `--allow-prod` | 配了 `sandbox_drama_id` 后，其它剧的写操作必须显式加 |
| 无 `delete` scope | `drama delete` / `episode delete` 直接 403，连请求都不发 |

### 3.6 命令树

```
minitv auth   whoami | doctor
minitv key    list | create | patch | revoke | rotate      # 需 admin 角色
minitv drama  list|get|create|update|delete|publish|unpublish|status|schedule
minitv drama  cover set <id> <file> | cover clear <id>
minitv drama  tags  set <id> 1,2,3
minitv episode list --drama ID | create | bulk | update | delete
minitv episode upload <id> <file> [--wait] [--resume]
minitv episode upload-all --drama ID --dir PATH [--start N] [--title-rule ...] [--concurrency 2] [--publish]
minitv episode publish|unpublish <id> | publish-all --drama ID
minitv episode sync <id>
minitv episode subtitle <id> <file>
minitv tag    list|create|update|delete
minitv group  list|get|add|remove|reorder
minitv batch  import --csv FILE        # P5
minitv mcp                             # P4
```

编排语义（CLI 相对裸接口的增量价值）：

- `drama publish <id>`：按状态机自动补跳（draft → in_review → published），一次调用完成。
- `episode upload-all`：目录内文件按自然序映射集号，逐集「缺则建集 → session → TUS → sync」，并发 2，`--publish` 收尾自动发布；失败项可单独重跑。
- `drama create --title X`：遇 409 自动回读已存在的剧并返回其 id（幂等语义），配合 P0-2。

### 3.7 错误模型

后端 `HTTPException.detail` 现在有 `str` 与 `dict` 两种（删剧确认 `{confirm_required,...}`、删标签 `{affected_dramas:[...]}`）。CLI 侧统一 normalize，不要求后端改造；但 P0-2 新增的 409 必须带结构化字段，否则 CLI 无法自动接管。

## 4. P0 前置改造

| # | 事项 | 为什么必须先做 | 改动点 | 验收 |
|---|---|---|---|---|
| P0-1 | API Key 体系（绑人） | 没有它只能存口令、8h 掉线 | 迁移 `admin_api_keys` + `api_key_events`；`require_admin` 分流解析；6 个管理端点；scope 交集；Redis 限流 | `curl -H "Bearer mk_live_..."` 调 `/dramas` 200；revoke 后立即 401；禁用绑定账号后 401；`PATCH` 改 scopes 后写接口 403 |
| P0-2 | 建剧重名兜 409 | Agent 重试安全 | `create_drama` catch `IntegrityError` → `409 {code:"title_exists", drama_id}` | 同名建两次，第二次 409 且带 id |
| P0-3 | 分集删除（只删空壳） | 建错集无法回收 | `DELETE /admin/v1/episodes/{id}`，仅 `not_uploaded` / `transcode_failed`；清 `media_uploads`/`cdn_purges`；写审计 | 删 `in_review` 集返回 409 并提示「先下架」；删空壳成功且关联记录清空 |
| P0-4 | TUS 重签 + 续传 | 1h 过期后上传必失败 | 新增 `POST /episodes/{id}/upload-headers`（同 `video_id` 重签，要求 `transcoding` 且 upload 未完成）；CLI 用 `HEAD` 取 offset | 建 session 后 2h 再传仍成功；中断后 `--resume` 从 offset 续上 |
| P0-5 | 环境隔离 | Agent 试错不能落生产 | `--base-url` + profile + `sandbox_drama_id`；prod 非沙箱写需 `--allow-prod` | 无 `--allow-prod` 时改线上其它剧被本地拒绝 |

建议同批做：生产关闭 `/docs` 与 `/openapi.json`；后台 `Staff.tsx` 增加「密钥」抽屉（列表 / 新建 / 撤销 / 轮换），复用现有 `hooks.ts` 与 `useConfirm`。

## 5. 分阶段落地

| 阶段 | 内容 | 规模 | 依赖 |
|---|---|---|---|
| P1 | CLI 骨架：config/profile、HTTP 客户端（超时/重试/错误映射）、输出契约、`auth whoami`、`doctor`、`drama list/get`、`episode list` | M | P0-1 |
| P2 | 写命令：drama create/update/publish/unpublish/status/delete/schedule、cover、tags；episode create/bulk/update/delete/publish/bulk；`key` 子命令 | M | P0-2、P0-3 |
| P3 | 上传：`upload`、`upload-all`（含 `--publish`）、`subtitle`、断点续传、转码轮询、失败重跑 | L | P0-4 |
| P4 | Agent 接入：MCP 子命令、Skill 说明书（命令表 + 典型流程 + 禁忌）、沙箱剧演练、灰度开 `write` scope | M | P1-P3 |
| P5 | 收尾：用 CLI 替换三个老脚本（顺手消灭 `admin-1234`）；CSV 批量导入；分组写命令 | M | P2-P4 |

每阶段验收跑真实动作，不只看单测：P1 的 `doctor` 要输出「连通 / 身份 / 权限 / 服务端版本」四行；P3 用一部测试剧走完「建剧 → 传 3 集 → 发布 → 下架 → 删集 → 删剧」。

## 6. 上线与灰度

1. **建专用账号**：`agent-ops`（role=`ops`）+ 一把 `agent-ops/upload-bot` key，不占用人账号 —— 人离职/改密不影响自动化，而"绑人"的审计归属依然成立（key 指向 `agent-ops`）。
2. **只读灰度**：先发 `scopes:["read"]` 的 key，让 Agent 跑 `doctor` + 列表类命令一周，确认无异常。
3. **沙箱剧演练**：建一部 `[SANDBOX]` 剧，id 写进 profile，所有写操作先落它。
4. **开写**：换成 `["read","write"]`，仍不给 `delete`。
5. **按需给 delete**：只在人工确认的窗口临时签发 `["read","write","delete"]`，用完 revoke。

## 7. 风险

| 风险 | 应对 |
|---|---|
| Agent 误删/误改生产剧 | 默认无 `delete` scope + `--allow-prod` + 沙箱剧 + 删除必 `--confirm` + `api_key_events` 可追 |
| Key 泄漏 | 只存 hash、按 key 即时 revoke、每 key 限流、日志不回显 |
| 绑人导致"停用某人连带停自动化" | 专用 `agent-ops` 账号隔离；真需要时改绑（新建 key）即可 |
| `require_admin` 改动影响全站 | 只加分支不动原路径；先跑现有 331 项后端测试，再补 key 专属用例（过期/撤销/scope/禁用账号） |
| 上传中断卡 `transcoding` | P0-4 续传 + 已有 120s 对账线程兜底转 `transcode_failed` |
| CLI 语义与后台 UI 漂移 | 命令树直接映射现有接口；差异只在「自动补跳状态机」这类编排，且写进手册 |

## 附录 A：鉴权解析骨架（改动风险最高的一处）

```python
KEY_PREFIX_LEN = 16          # "mk_live_" + 8 hex，够区分，也能安全展示
def _parse_api_key(token: str) -> AdminUser | None:
    prefix = token[:KEY_PREFIX_LEN]
    row = db.scalar(select(ApiKey).where(ApiKey.key_prefix == prefix))
    if row is None:                                  # 401 invalid_key
        return None
    if not hmac.compare_digest(row.key_hash, sha256(token)):  # 401 invalid_key
        return None
    if row.status != "active":                       # 401 key_revoked
        return None
    if row.expires_at and row.expires_at <= now():   # 401 key_expired
        return None
    user = db.get(AdminUser, row.admin_user_id)
    if user is None or user.status != "active":      # 401 account_disabled
        return None
    if row.scopes:                                   # scope 只能收窄
        user = _narrow(user, row.scopes)             # 挂 effective_scopes 到实例上
    return user
```

要点：

- **只在 `require_admin` 里加分支**，`require_content_writer` 与 `require_admin_role` 完全不改。
- 错误必须带 `code`（`HTTPException(detail={"code": "...", "message": "..."})`），否则 CLI 无法区分「过期」和「撤销」——两者的处置动作完全不同。
- `last_used_at` 用「距上次写超过 60s 才 UPDATE」的方式节流，避免每个 GET 都打一次库。
- `scopes` 收窄建议实现成写死的映射表（`delete` ⊃ `write` ⊃ `read`），不做动态推断，便于审计时人工核对。

## 附录 B：Key 签发 / 轮换的事务顺序

1. `POST /api-keys`：生成明文 → 算 prefix/hash → 插行 → 返回明文。**明文不写日志**（访问日志里也要脱敏）。
2. `POST /api-keys/{id}/rotate`：同一事务内 `revoke(旧) → insert(新)`，只返回新明文；旧 key 立刻 401，不做宽限期（Agent 会重读 config，不需要 grace）。
3. `POST /api-keys/{id}/revoke`：`status=revoked` + `revoked_at/revoked_by/reason`，行保留。
4. `DELETE /api-keys/{id}`：仅 `last_used_at IS NULL` 才物理删，否则 409 —— 用过的 key 是审计证据。
5. 所有签发/撤销/轮换写 `audit_logs`（`action` 分别为 `api_key_create` / `api_key_revoke` / `api_key_rotate`），`payload` 只放 `{name, user_id, scopes, expires_at}`，**绝不放明文或 hash**。
