# 上剧方 P2 实施与测试报告

> 范围：`docs/supplier-role-plan.md` P2（上剧能力 + 自助 API Key + CLI 0.4.0）
> 日期：2026-10-22　环境：本地（Docker Postgres/Redis，API :18110，Vite :5173）
> 结论：**P2 功能完成，测试全绿；尚未部署线上。**

## 1. 本阶段交付

| # | 内容 | 说明 |
|---|---|---|
| 1 | 建剧 / 改剧 | `POST/PATCH /partner/v1/dramas`；价格强制 0、免费集数默认 5；字段白名单 |
| 2 | 分集管理 | 建集（自动集号）、改标题、删集（仅未上传） |
| 3 | 上传链路 | `upload-session` / `upload-headers` / `upload-status`，复用现有 TUS 与 Bunny |
| 4 | 封面上传 | `POST /dramas/{id}/cover`，复用同一对象路径与 sniff 逻辑 |
| 5 | 提交审核 | `POST /dramas/{id}/submit`，四项校验后置 `review_state=pending` |
| 6 | 自助 API Key | `/partner/v1/api-keys` 创建/轮换/撤销（会话）；列表允许 key 读取 |
| 7 | Scope 白名单 | partner 只能 `read/write/upload`；空 scopes 与角色取交集；`write` 兼容 `upload` |
| 8 | 路径守卫 | partner key 离开 `/partner/v1/` 一律 403 `partner_scope_denied` |
| 9 | CLI 0.4.0 | `partner drama-list/get/create/update/submit`、`episode-add/list/upload/status`、`keys-list`；`config set-key` 自动识别 surface |

## 2. 关键实现细节

### 2.1 权限收紧

- `granted_scopes(key, role)` 对 `partner` 与 `PARTNER_SCOPES` 取交集：**空 scopes 不再等于全量**，封死了「partner key 自带 staff、可自建 key」的提权链。
- 密钥管理（创建/轮换/撤销、`/auth/keys`）仍只接受会话；只有 `GET /api-keys` 允许 key 读取，方便 CLI 展示。
- `authorize()` 先做路径判定再做 scope 判定，partner key 命中 `/admin/v1` 直接 `partner_scope_denied`，错误码稳定可测。

### 2.2 状态与校验

- 上剧方只能编辑 `status=draft` 且 `review_state != pending` 的剧；已上架/审核中一律 409。
- 驳回后编辑会自动把 `review_state` 回 `none` 并清空驳回原因，需重新提交。
- 提交四项硬校验：封面非空、简介 ≥30 字、至少 1 集且全部 `in_review`、`min(5, 总集数) ≤ 免费集数 ≤ 总集数`。
- 所有写操作写 `audit_logs`，`payload` 带 `actor_role=partner` 与 `supplier_id`。

### 2.3 本地与生产差异

- 本地 `BUNNY_MODE=real`，上传会真实打到 Bunny；因此验收后删除了测试视频对象。
- 假的 dev TUS 接收端为 partner 复制了一份（`/partner/v1/dev-tus/*`），仅在 Bunny 非 live 时生效；生产走 Bunny 直传，不受影响。

## 3. 测试结果

```
cd backend && ../.venv/bin/python -m pytest -q
396 passed, 1 warning in 56.12s
```

（既有 376 条全部回归通过，新增 20 条全部通过。）

新增 `tests/test_partner_p2.py` **20 条**，覆盖：

| 组 | 用例 |
|---|---|
| 建剧 | 默认免费 5/价格 0、同供应商重名 409、跨供应商同名 201、价格字段被忽略 |
| 改剧 | 白名单字段与标签替换、无效标签 422、已上架 409 |
| 分集 | 自动集号与标题、重复集号 409、未上传可删、已上传 409 |
| 上传 | 跨供应商 episode 404、upload-session 成功进 transcoding、状态可查 |
| 提交 | 缺封面 / 简介短 / 无分集 / 有未转码 / 免费集数越界 五个 422；成功后 pending；重复提交 409 |
| 密钥 | scopes 白名单 422、partner key 可建剧但打不开 admin、只读 key 不能写/传、key 不能管理 key、撤销后 401、不能撤他人的 key |

### 3.1 CLI 端到端（真实调用本地 API）

```
minitv partner drama-create --title "CLI 上传测试剧"   → 201, id=118, free=5
minitv partner episode-add 118                        → 201, episode=7987
minitv partner episode-upload 7987 ./ep1.mp4          → 成功，episode 进入 transcoding
minitv partner episode-status 7987                    → status=transcoding
minitv partner keys-list                              → 返回该密钥元数据
minitv drama list（员工命令）                          → partner_scope_denied，exit=3
minitv auth whoami                                    → role=partner、surface=partner、scopes=read/write/upload、version=0.4.0
```

## 4. 与方案的偏差（有意为之）

| 项 | 方案 | 实际 | 原因 |
|---|---|---|---|
| CLI 密钥增删 | `keys list/create/rotate/revoke` | 只保留 `keys-list`，增删改在网页端 | 密钥管理坚持「仅会话」，而 CLI 只有 key；避免自建 key 的提权面 |
| 新增 `key-info` | 无 | `GET /partner/v1/auth/key-info` | CLI `whoami` 需要 scopes/到期，但不能列出全部密钥 |
| dev TUS | 无 | partner 版接收端 | 本地联调需要，仅 Bunny 非 live 生效 |

## 5. 已知边界

1. **partner 还没有前端编辑页**：P2 是 API + CLI；网页端仍是只读列表，P2.5/P3 再补编辑器。
2. **审核流未接**：`review_state=pending` 已能写入，但运营端审核队列（P3）还没做，目前只能直接改库/等 P3。
3. **封面上传依赖 Bunny Storage**；`BUNNY_MODE` 非 live 时返回 503，与后台现有行为一致。
4. **上传配额**（方案 §5.4.4 的 200 session/天/供应商）未实现，留到 P3 与限流一起做。

## 6. 结论

P2 打通了「上剧方自己建剧 → 建集 → 上传 → 提交审核」的完整链路，并给出了配套的密钥与 CLI。下一步 P3：运营侧待审核/待上架、批量通过、通知与配额。
