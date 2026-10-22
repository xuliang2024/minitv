# 评论架构（分集维度）

> 结论：评论的**唯一归属键是 `episode_id`**，`drama_id` 只作为冗余维度用于后台筛选与整剧统计。
> 列表、计数、审核、删除全部以「一集」为边界，不同集之间互不可见、互不影响。

## 1. 不变量

1. 一条评论必须属于某一集：`comments.episode_id NOT NULL`。
2. 客户端只能读到 `status = 'approved'` 且 `episode_id` 等于当前集的评论。
3. 集内顺序稳定：默认 `created_at DESC, id DESC`，翻页用游标（keyset），不用 offset。
4. 计数是派生值，按集聚合，不单独落冗余列，避免审核状态变化后计数漂移。
5. 未登录可读，登录才可写；写操作幂等性靠频率限制，不靠唯一键。

## 2. 数据模型

现有表（`backend/app/modules/social/models.py:30`）已经具备全部字段，只差约束与索引：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | bigint PK | |
| `episode_id` | bigint FK → episodes.id | **归属锚点，改 NOT NULL** |
| `drama_id` | bigint FK → dramas.id | 冗余维度，写入时由 episode 反查填入 |
| `user_id` | bigint FK → users.id | |
| `body` | text | 1–500 字符，纯文本，禁止富文本 |
| `status` | varchar(16) | `pending` / `approved` / `deleted` |
| `created_at` | timestamptz | 默认 `now()` |

迁移（当前线上 0 行，可直改）：

```sql
ALTER TABLE comments ALTER COLUMN episode_id SET NOT NULL;

CREATE INDEX ix_comments_episode_status_created
  ON comments (episode_id, status, created_at DESC, id DESC);
CREATE INDEX ix_comments_status_created
  ON comments (status, created_at DESC);
CREATE INDEX ix_comments_user_created
  ON comments (user_id, created_at DESC);
```

> `drama_id` 保留但不参与 C 端查询。若将来要「整剧热评」，再建 `(drama_id, status, created_at)`，不要现在加。

## 3. 接口契约

### C 端

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/v1/episodes/{episode_id}/comments?limit=20&cursor=<id>` | 只返回 approved；`limit ≤ 50` |
| `POST` | `/v1/episodes/{episode_id}/comments` | 需登录；body `{ "body": "..." }`；返回 `{id, status}` |
| `DELETE` | `/v1/comments/{id}` | 仅本人、24h 内；软删，置 `deleted` |

响应（列表）：

```json
{
  "items": [
    {
      "id": 128,
      "body": "This twist blew my mind",
      "created_at": "2026-10-19T06:40:00Z",
      "like_count": 3,
      "liked": false,
      "mine": false,
      "user": { "public_id": "u_82f1", "display_name": "Mia", "avatar_url": "https://..." }
    }
  ],
  "next_cursor": 121,
  "total": 42
}
```

- `next_cursor = null` 表示到底；`total` 用同一次 `count(*)` 拿，不必分页重复查。
- 禁止把 `user_id`、邮箱暴露给客户端；只给 `public_id`。

### 后台

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/admin/v1/comments?status=pending&episode_id=&drama_id=&q=&page=1&page_size=20` | 联表带出 `drama_title`、`episode_number`、用户邮箱/昵称 |
| `POST` | `/admin/v1/comments/{id}` | `{ "status": "approved" \| "deleted" \| "pending" }` |
| `POST` | `/admin/v1/comments/bulk` | `{ "ids": [...], "status": "approved" }` |

## 4. 读写链路

### 写

```
客户端（已登录）
  → POST /v1/episodes/{id}/comments
  → 校验：登录态 / 长度 / 频率（同用户同集 30s 一条，单日 50 条）
  → 反查 episode → drama_id（一次 select）
  → 入库：status = pending（或 auto 模式直接 approved，见 §6）
  → 返回 {id, status}
```

### 读

```
客户端进入某一集
  → GET /v1/episodes/{id}/comments
  → WHERE episode_id = :id AND status = 'approved'
    ORDER BY created_at DESC, id DESC
    LIMIT :limit
  → join users 取昵称/头像（一次 join，不做 N+1）
  → 返回 items + next_cursor + total
```

### 计数（关键：避免 N+1）

播放页 `/v1/dramas/{id}/play` 一次返回整剧所有集，如果每集单查 count 会变成 30+ 次查询。改为一条分组查询：

```python
counts = dict(
    db.execute(
        select(Comment.episode_id, func.count())
        .where(Comment.episode_id.in_(episode_ids), Comment.status == "approved")
        .group_by(Comment.episode_id)
    ).all()
)
```

然后把 `comment_count` 写进每个 episode item。`comment_count` 只统计 approved，与列表口径一致。

### 缓存

第一版不加缓存。列表按 `episode_id + status + created_at` 有索引，单集评论量级在千级以内，Postgres 直接扛。若后续 QPS 上来，按 `episode:{id}:comments:v1` 做 60s 短缓存，写/审核时删 key，不做本地缓存。

## 5. 状态机

```
                ┌─────────────┐
   用户提交 ───▶ │   pending   │
                └──────┬──────┘
              通过 │        │ 删除
                   ▼        ▼
              ┌─────────┐  ┌─────────┐
              │ approved│─▶│ deleted │
              └─────────┘  └─────────┘
                   ▲             │
                   └── 恢复 ─────┘   （仅管理员，记 audit_log）
```

- 只有 `approved` 对客户端可见；`pending` / `deleted` 一律不出现在任何 C 端接口。
- 状态变更写 `audit_logs`（actor、from、to、reason），后台可追溯。
- 审核接口必须校验管理员角色：当前 `require_content_writer` 已有，保持。

## 6. 自动审核（已实现）

后台「自动审核」页（`/moderation`）配置 OpenAI 模型做落库前判定，不再只有「先审后发」一种可能：

| 配置项 | 默认 | 说明 |
|---|---|---|
| 启用 | 关闭 | 关闭时全部走人工，行为与以前一致 |
| 模型 | `deepseek-flash` | 推理模型，token 预算至少 900，否则会截断 |
| Base URL | `https://codex.apiz.ai/v1` | 任意 OpenAI 兼容中转都可用 |
| 审核维度 | 色情/暴力/仇恨/骚扰/广告/隐私/违法 | 写入提示词的 `{categories}` |
| 系统提示词 | 内置 | 要求返回 `verdict / score / reason` |
| 自动通过阈值 | 0.75 | `allow` 且置信度达标 → `approved` |
| 自动拦截阈值 | 0.75 | `block` 且置信度达标 → `deleted` |
| 失败降级 | `pending` | 超时/限流/密钥失效时落到 `pending` |
| 超时 | 20s | 单次模型调用上限；deepseek-flash 实测 2–3s |

判定映射：

```
allow  + score >= approve_threshold → approved
block  + score >= block_threshold   → deleted
其余（review / 低分 / 模型失败）      → pending
```

- 密钥优先取后台「自动审核」页保存的副本（Fernet 加密存 `moderation_settings.api_key_secret`，只回显尾号、不回显完整值）；未保存时回退到环境变量 `OPENAI_API_KEY`。主密钥 `MODERATION_KEY_SECRET` 只在服务器 .env，更换后旧密文解不开会退回环境变量。未配置密钥时按降级策略处理。
- 每次判定把 `verdict / score / reason / model / moderated_at` 写进 `comments`，后台评论列表可追溯，人工通过或删除始终优先。
- 模型只接收评论正文，不发送邮箱、用户 ID 或剧集信息。

## 7. 前端挂载点

| 端 | 位置 | 状态 |
|---|---|---|
| H5 播放页 / For You | 右侧 rail 气泡图标 | ✅ 打开当前集 `CommentSheet`，标签用该集 `comment_count` |
| H5 选集 | 底部 `ep-bar` / `select-pill` | ✅ 气泡不再兼任选集；选集走独立入口 |
| H5 弹层 | `h5/src/ui/CommentSheet.tsx` | ✅ 列表 + 发表 + 点赞 + 游标加载；`createPortal` 到 `#app`，z 11/12 盖住底栏 |
| 播放/Feed 计数 | `items[].comment_count` | ✅ 均按集聚合，一条 group by |
| 后台 | `admin/src/pages/StagePages.tsx` | ✅ 状态 Tab、搜索、剧名/集数/用户联表、自动判定原因、服务端分页 |
| Flutter App | `foryou.dart:401` / `detail.dart:245` | ⏳ 仍跳详情页，待 App 侧接评论页 |

## 8. 安全与边界

- 渲染：纯文本，React 默认转义；禁止 `dangerouslySetInnerHTML`。
- 频率：Redis 不可用时用 `comments` 表自身查最近一条时间即可，避免引入新依赖。
- 不做（本期）：楼中楼回复、@ 提及、图片评论、举报工单、按集热度排序。
- 删除剧集时：`publish_service.py:186` 已有 `delete(Comment).where(drama_id == ...)`，改为按 `episode_id in (...)` 删，语义更准。
