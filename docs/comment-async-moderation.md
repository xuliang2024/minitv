# 评论异步审核整改方案

状态：已实施 · 2026-10-19 · 线上 v2026.10.19.64（server）/ .65（h5）/ .66（admin）

## 1. 目标

| 目标 | 验收标准 |
|---|---|
| 发评论立即返回 | `POST /v1/episodes/{id}/comments` P99 < 300ms（不再等模型） |
| 作者立刻看到自己的评论 | 提交成功后直接出现在列表顶部，与已发布评论**完全一致**，无任何待审标记 |
| 其他人看不到未审核内容 | 访客/其他用户的列表与计数只包含 `approved` |
| 审核完成后其他人可见 | 后台判定 `approved` 后，其他人下一次拉取即可看到 |
| 前端零轮询、零手动刷新 | 前端不新增定时器、不做二次请求 |
| DB 连接不被模型调用占用 | 调模型期间不持有连接池连接 |

非目标：评论实时推送（WebSocket/SSE）、审核结果实时回写作者界面。作者提交的评论在本次会话内按普通评论展示；是否通过以服务端下一次返回为准，不推送、不轮询。

## 2. 核心决策

**不采用「先 approved、对别人隐藏」的影子发布**。理由：任何一处查询漏了 `shadow` 条件就会把未审核内容放出去，风险不可控。

采用：

- `status` 继续只表达**可见性**：`pending / approved / deleted`
- 新增 `moderation_state` 表达**异步任务状态**：`queued / running / done / failed / canceled`
- 作者可见自己的 `pending`，靠**查询条件**而不是复制一份数据：

```sql
WHERE episode_id = :episode
  AND (
        status = 'approved'
        OR (status = 'pending' AND user_id = :viewer)
      )
ORDER BY id DESC
```

访客 `:viewer` 为空时，条件退化为只看 `approved`。

## 3. 状态机

```
提交
 │
 ├─ 审核关闭 / 未配 key ──► status=pending, moderation_state=NULL（纯人工）
 │
 └─ 审核开启 ──► INSERT status=pending, state=queued ──► 立即响应
                         │
                         ├─ worker 领取：state=running, attempts+1
                         │        │
                         │        ├─ allow + score≥阈值 ──► status=approved, state=done
                         │        ├─ block + score≥阈值 ──► status=deleted,  state=done
                         │        ├─ review / 低分 ──────► status=pending,  state=done（转人工）
                         │        └─ 调用失败 ───────────► attempts<3: state=queued（重试）
                         │                                attempts=3: state=failed（留 pending）
                         │
                         └─ 人工先处理：state=canceled，worker 结果不再覆盖
```

`status` 与 `moderation_state` 的组合：

| status | moderation_state | 含义 | 作者可见 | 他人可见 |
|---|---|---|---|---|
| pending | queued / running | 排队/审核中 | ✅ 正常展示（无标记） | ❌ |
| pending | done (review) | 等人工 | ✅ 正常展示（无标记） | ❌ |
| pending | failed | 模型失败，等人工 | ✅ 正常展示（无标记） | ❌ |
| approved | done | 已发布 | ✅ | ✅ |
| deleted | done / canceled | 已拦截/已删 | ❌ | ❌ |

## 4. 数据模型

`comments` 新增 4 列（全部可空，旧数据不受影响）：

```sql
moderation_state      varchar(16)  -- queued / running / done / failed / canceled
moderation_attempts   smallint NOT NULL DEFAULT 0
moderation_claimed_at timestamptz  -- worker 领取时间，用于回收僵死任务
moderation_error      varchar(200) -- 最后一次失败原因（不含评论正文）
```

索引：

```sql
CREATE INDEX ix_comments_moderation_queue
  ON comments (id)
  WHERE moderation_state IN ('queued', 'running');
```

已有 `ix_comments_episode_status_created (episode_id, status, created_at DESC, id DESC)` 继续服务列表查询；作者可见的 `OR` 条件会走 BitmapOr，数据量上来后再评估是否拆两条查询。

迁移 revision：`e8c1f4a7b203`（`down_revision = d6a1f3c8b902`）。只加列与索引，可安全回滚。

## 5. 接口契约

### POST /v1/episodes/{id}/comments

提交只做三件事：校验、限流、落库，然后丢给后台。

```json
{
  "id": 123,
  "status": "pending",
  "item": {
    "id": 123,
    "body": "...",
    "created_at": "...",
    "like_count": 0,
    "liked": false,
    "mine": true,
    "pending": true,
    "user": { "public_id": "...", "display_name": "...", "avatar_url": "" }
  },
  "moderation": { "state": "queued" }
}
```

- 无论模型多快，响应统一为 `pending`，避免前端出现两套分支。
- 审核关闭时不入队，`moderation.state = "disabled"`，`item.pending = true`。
- 限流仍为 30s/条、50 条/天，返回 429。

### GET /v1/episodes/{id}/comments

- 可见性按第 2 节 SQL。
- 每条新增 `pending: boolean`（`true` 只会出现在 `mine` 的行上）。
- `total` = approved 数 + **当前用户自己的 pending 数**；访客和其他用户仍只算 approved。
- 播放页/Feed 的 `comment_count` 同理：登录用户看到「approved + 自己的 pending」，其他人只看到 approved。
- 这样一来，作者那条还没审完的评论在列表、头部数字、rail 计数上**都像一条普通评论**，不会出现「显示 0 条却有一条评论」的割裂感；数字按 viewer 计算，不会泄露给其他人。
- 游标分页不变（`cursor = id`）。

### 后台接口

`POST /admin/v1/comments/{id}` 人工通过/删除时，同时写 `moderation_state='canceled'`，阻止迟到的 AI 结果覆盖人工决定。

## 6. 异步执行

### 6.1 入队

`create_episode_comment` 在 `commit` 之后调用 FastAPI `BackgroundTasks.add_task(process_comment, comment_id)`。响应先返回，任务在响应发送后执行。

### 6.2 worker

`process_comment(comment_id)` 分三段，**模型调用期间不持有数据库连接**：

1. **领取**（短事务）
   ```sql
   UPDATE comments
   SET moderation_state='running',
       moderation_claimed_at=now(),
       moderation_attempts=moderation_attempts+1
   WHERE id=:id AND moderation_state='queued'
   RETURNING body;
   ```
   `rowcount=0` 说明已被处理，直接退出。
2. **调模型**：读取配置（短事务）→ commit → 关闭 Session → `classify(body, settings)`。此时连接已归还连接池。
3. **回写**（短事务）
   ```sql
   UPDATE comments
   SET moderation_state='done', moderation_verdict=..., moderation_score=...,
       moderation_reason=..., moderation_model=...,
       moderated_at=now(), status=:mapped_status
   WHERE id=:id AND moderation_state='running';
   ```
   人工已介入（`canceled`）时该 UPDATE 影响 0 行，AI 不会覆盖人工结果。

### 6.3 兜底扫描（sweeper）

`main.py` 的 `_start_background_jobs()` 里启动一个 daemon 线程，每 30s 扫一次：

- `state='queued'` 且 `attempts < 3`
- `state='running'` 且 `claimed_at < now() - 5min`（进程被杀留下的僵死任务）

每次最多领 10 条，复用 `process_comment` 的领取逻辑；多进程部署时靠原子 UPDATE 去重。

### 6.4 失败策略

- 单次失败：`state='queued'`，等下一轮重试（最多 3 次）
- 3 次失败：`state='failed'`，`status` 保持 `pending`，进入人工队列
- 审核开关在排队期间被关闭：worker 直接置 `state='canceled'`，保持 pending
- 模型超时沿用配置（当前 20s），但不再阻塞发评论

## 7. 前端交互（H5）

`CommentSheet` 的提交逻辑改为：

1. 点击 send → 清空输入框、禁用按钮
2. 用响应里的 `item` **直接插到列表顶部**
3. **不渲染「审核中」标记、不发成功 toast**——这条评论和已发布评论长得一模一样
4. `total` 用响应里的 `total`（已含自己的 pending）更新，rail 计数同步 +1
5. 不轮询、不二次请求

`pending` 字段仍保留在接口里（便于后台/调试），但 H5 不读取、不展示。作者可以给自己的 pending 评论点赞；被拦截时，下次打开列表它会消失，本次会话内不做二次提示。

失败时（429/网络）保留原文、恢复按钮，toast 报错；不发成功 toast，避免双重反馈。

### 兼容旧版本

线上仍可能缓存旧 H5（v2026.10.19.58）。旧代码只在 `status === "approved"` 时插入列表，遇到 pending 会提示「已提交，审核通过后可见」，功能不坏，只是没有即时插入。发布顺序：后端 → H5，旧客户端在几分钟内体验降级，可接受。

## 8. 测试清单

后端：

- [ ] POST 返回 `pending` 且带完整 `item`，响应时间不包含模型耗时（mock `classify` sleep 2s，断言请求 < 500ms；注意 TestClient 会等待 BackgroundTasks，需直接调用路由或用 ASGI 客户端并发断言）
- [ ] 作者能看到自己的 pending，且响应里没有需要展示的标记；访客和其他用户看不到
- [ ] 作者的 `total` / `comment_count` 包含自己的 pending，其他用户不包含
- [ ] worker allow → approved，其他人可见
- [ ] worker block → deleted，作者也看不到
- [ ] worker review → 保持 pending，后台可见
- [ ] 人工先通过 → `state=canceled`，worker 回写不覆盖
- [ ] 连续失败 3 次 → `failed`，不无限重试
- [ ] 僵死 `running` 超过 5 分钟被 sweeper 回收
- [ ] 审核关闭时不入队、不入 sweeper

前端：

- [ ] 提交后立即出现评论，样式与已发布完全一致（无标签、无 toast），输入框清空
- [ ] 头部数字与 rail 计数同步 +1，和列表条数一致
- [ ] 不产生额外网络请求（DevTools 只有一条 POST）
- [ ] 重新打开弹层后标签消失（已通过）或评论消失（被拦截）
- [ ] 429/网络失败保留原文

## 9. 发布与回滚

1. 迁移（只加列/索引）+ 后端：异步逻辑上线后，新评论立即变 pending；旧评论状态不变
2. H5：即时插入与计数同步（无任何待审 UI）
3. 后台无需改动（取消逻辑随后端生效）

回滚：回滚后端版本即可；新列保留不影响旧代码。若需紧急恢复同步审核，把 `moderation_state` 的入队开关关掉（新增一个 `async_enabled` 配置，默认 true）——本方案不强制加，回滚部署更直接。

## 10. 监控

- 日志：`comment_id`、`attempt`、`verdict`、`latency_ms`、`state`，不记录正文
- Redis 计数（可选）：`mod:queued`、`mod:done`、`mod:failed`、`mod:latency_p95`
- 告警：`failed` 5 分钟内 > 5 条，或 `queued` 积压 > 50 条

## 11. 工作量拆分

| 项 | 文件 | 说明 |
|---|---|---|
| 迁移 | `backend/alembic/versions/e8c1f4a7b203_async_moderation.py` | 4 列 + 部分索引 |
| 模型 | `backend/app/modules/social/models.py` | 新字段 |
| worker | `backend/app/modules/social/moderation.py` | `process_comment` / `start_moderation_worker` |
| 发评论 | `backend/app/modules/public_flow.py` | 先落库 + BackgroundTasks + 可见性查询 |
| 启动 | `backend/app/main.py` | 启动 sweeper |
| 人工审核 | `backend/app/modules/ops/flow_router.py` | 置 `canceled` |
| H5 | `CommentSheet.tsx` / `types.ts` | 即时插入 + 计数同步，不渲染 pending |
| 计数 | `public_flow.py` / `recommend/service.py` | approved + viewer 自己的 pending |
| 测试 | `backend/tests/test_comments.py` | 新增异步用例 |


## 12. 实施记录（2026-10-19）

已按本方案上线，实测：

| 指标 | 结果 |
|---|---|
| POST 发评论 | **55ms**（改造前 2.4–4.2s） |
| 作者立即可见 | ✅ 无任何「审核中」标记，头部计数 +1 |
| 访客/其他用户 | ✅ 提交后立刻拉取 total=0 |
| worker 通过后 | ✅ 访客可见，`comment_count` 双方一致 |
| 迁移 | `e8c1f4a7b203`，新增 4 列 + 部分索引 |

实现文件：

- `backend/app/modules/social/moderation.py`：`queue_state` / `process_comment` / `process_pending_batch` / `start_moderation_worker`
- `backend/app/modules/public_flow.py`：先落库 + `BackgroundTasks`、按 viewer 的可见性与计数
- `backend/app/modules/recommend/service.py`：Feed 计数加本人 pending
- `backend/app/main.py`：启动 sweeper（测试用 `MODERATION_WORKER=0` 关闭）
- `backend/app/modules/ops/flow_router.py`：人工处理置 `canceled`
- `h5/src/ui/CommentSheet.tsx`：即时插入、无标签无 toast、计数用响应 `total`

测试：`backend/tests/test_comments_async.py` 8 条，全量 268 passed。
