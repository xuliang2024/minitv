# 上剧方 P1 实施与测试报告

> 范围：`docs/supplier-role-plan.md` P1（隔离 + 归因 + 供应商管理 + 菜单分流）
> 日期：2026-10-22　环境：本地（Docker `minitv-postgres` / `minitv-redis`，API :18110，Vite :5173）
> 结论：**P1 功能完成，测试全绿，可发布；尚未部署到线上。**

## 1. 本阶段交付

| # | 内容 | 说明 |
|---|---|---|
| 1 | `suppliers` / `supplier_share_history` 两张表 | 分成默认 8000（80%），改动留生效区间 |
| 2 | `admin_users.supplier_id` + 一供应商一账号唯一索引 | 角色新增 `partner`；`role=partner` 必须绑供应商 |
| 3 | `require_admin` 收窄为员工白名单 | partner 访问任何 `/admin/v1/*` → 403 `staff_only` |
| 4 | 新增 `/partner/v1` 只读接口 | `me` / `dramas` / `dramas/{id}` / `auth/password` / `auth/logout` |
| 5 | 剧集归属与审核字段 | `dramas.supplier_id`、`review_state`、`review_note`、`submitted_at`、`reviewed_at`、`reviewed_by` |
| 6 | 剧名唯一性改造 | 自营内唯一 + 供应商内唯一，跨供应商放开 |
| 7 | 会员归因 | `orders.source_drama_id`、`orders.refunded_at`、`subscriptions.source_drama_id`；checkout 接收 `source_drama_id` 并写入 Stripe metadata |
| 8 | 后台「上剧方管理」页 | 建供应商、改分成（含历史）、启用/禁用、创建绑定账号 |
| 9 | 后台菜单按角色分流 | partner 只见「我的剧集」；ops/finance 各见其职责入口；深链自动重定向 |

**不在 P1**（按计划留到后续）：partner 建剧/上传/提交、partner API Key 与 CLI、审核队列与批量通过、数据中心、结算单。

## 2. 数据库变更

迁移：`d3f8b1c6a940_supplier_tenancy_p1.py`（`down_revision = f1a7c2e93b64`）

- 新表：`suppliers`、`supplier_share_history`
- `admin_users`：+`supplier_id`、角色 CHECK 加 `partner`、`partner` 必填供应商、供应商唯一（partial）
- `dramas`：+`supplier_id` / `review_state` / `review_note` / `submitted_at` / `reviewed_at` / `reviewed_by`；删 `uq_dramas_title`，改建两条 partial unique index；新增 `(supplier_id,status)` 与待审索引
- `orders`：+`source_drama_id` / `refunded_at` 与两条 partial 索引
- `subscriptions`：+`source_drama_id`
- `playback_events`：+`(episode_id, created_at)` 索引

数据影响：存量 212 部剧 `supplier_id=NULL`、`review_state='none'`，全部按自营处理；无回填、无数据改写。

## 3. 测试结果

### 3.1 后端自动化

```
cd backend && ../.venv/bin/python -m pytest -q
376 passed, 1 warning in 57.89s
```

- 全量 376 条通过（含既有 360 条回归）
- P1 新增 `tests/test_partner_p1.py` **16 条**，全部通过
- 回归修复 1 条：`test_list_staff_omits_password_hash` 的字段集合加入 `supplier_id`

新增用例覆盖：

| 组 | 用例 |
|---|---|
| 供应商 | 默认 80% + 首条历史、改分成关旧开新、越界 422、重名 409 |
| 账号 | partner 缺 supplier 422、staff 带 supplier 422、一供应商第二账号 409、partner 角色不可改 400 |
| 隔离 | partner 调 `/admin/v1/{dramas,users,finance}` 全 403；staff 调 `/partner/v1/me` 403；partner 仅见自己的剧；他人剧/自营剧 404 |
| 停用 | 禁用供应商 → 账号 disabled、会话被撤销、旧 token 401、重新登录 401 |
| 归因 | checkout 写 `orders.source_drama_id` 与 Stripe metadata；草稿剧 404；webhook 回填 order 与 subscription |

### 3.2 前端构建

```
cd admin && npm run build       # tsc --noEmit && vite build
✓ 3058 modules transformed / built in 3.13s
```

无类型错误。仅剩既有 chunk 体积提示（与本次无关）。

### 3.3 浏览器端到端（Playwright，真实点击）

| # | 场景 | 期望 | 实测 |
|---|---|---|---|
| 1 | admin 新建供应商「验收影视」 | 落库 80%，列表出现 | ✅ |
| 2 | 为该供应商创建账号 `sp-accept` | 账号创建并显示在「绑定账号」 | ✅ |
| 3 | partner 登录 | 落到 `/partner/dramas` | ✅ |
| 4 | partner 侧栏 | 只有「我的剧集」 | ✅ 仅 `MiniTV 合作方 + 我的剧集` |
| 5 | partner 深链 `/dramas` | 重定向回 `/partner/dramas` | ✅ |
| 6 | ops 侧栏 | 无账号管理/上剧方管理/财务订单 | ✅ 仅 数据看板/剧集/分组/首页运营/金币/评论/自动审核/标签库 |
| 7 | finance 侧栏 | 有财务订单、无剧集管理 | ✅ 数据看板/用户管理/财务订单/会员套餐 |

截图：`/tmp/p1-suppliers.png`（上剧方管理）、`/tmp/p1-partner.png`（合作方壳）。

## 4. 已知边界与风险

| # | 项 | 说明 |
|---|---|---|
| 1 | partner API Key 尚未开放 | P2 实现；落地时必须同时修 `granted_scopes()` 的空 scopes 提权口并加路径守卫（方案 §5.4） |
| 2 | partner 暂无写接口 | P2 才有建剧/上传；当前 `POST/PATCH /partner/v1/dramas` 为 404 |
| 3 | 审核流未接 | `review_state` 只有 `none`；P3 才流转，后台暂无审核入口 |
| 4 | 剧名唯一性已变化 | P2 建剧时查重必须限定 `supplier_id`，否则会把别家同名判成 409 |
| 5 | 菜单收窄是 UX 变化 | ops 不再看到「用户管理/财务订单/会员套餐」，后端读权限未变（仍 200），只是入口隐藏 |
| 6 | 禁用供应商不自动下架剧 | 按方案约定由运营决定；当前只停账号与会话 |
| 7 | 本地 dev 代理 | `admin/vite.config.ts` 新增 `/partner/v1` 代理，仅开发环境生效 |

## 5. 上线前检查清单（未执行）

1. 备份线上库（`pg_dump` 到 `/opt/minitv/backups/`）
2. 确认线上 `alembic_version`：应为 `f1a7c2e93b64`（API Key 迁移）或更早 `b7f2c9d41e03`
3. `release.py run --target server -- ./scripts/deploy_server.sh`（执行 `f1a` → `d3f`）
4. 迁移后自检：

   ```sql
   SELECT count(*) FROM suppliers;                       -- 0
   SELECT count(*) FROM dramas WHERE supplier_id IS NULL; -- = 总剧数
   SELECT indexname FROM pg_indexes WHERE tablename='dramas';  -- 含两条 title partial index
   SELECT column_name FROM information_schema.columns
    WHERE table_name='orders' AND column_name IN ('source_drama_id','refunded_at'); -- 2 行
   ```

5. `release.py r2 --dist admin/dist --source admin --prefix admin --verify-url https://admin.minitv.us`
6. 线上回归：admin/ops/finance 登录与菜单、`GET /admin/v1/dramas` 200、`GET /partner/v1/me` 用员工 token 应 403
7. 回滚：R2 `release.py rollback r2 --to <上一版>`；DB `alembic downgrade b7f2c9d41e03`（**前提：没有任何 partner 剧**，否则恢复剧名全局唯一会失败）

## 6. 结论

P1 的三条目标全部达成：

- **隔离**：partner 无法进入员工面（403），只看得到自己的剧（他人 404）
- **归因**：新订单在创建时即写入来源剧，webhook 回填订阅，历史数据不受影响
- **管理**：供应商与分成比例可在后台维护，改动有历史、有审计

可以进入 P2（partner 建剧 / 上传 / 提交 + API Key + CLI）。
