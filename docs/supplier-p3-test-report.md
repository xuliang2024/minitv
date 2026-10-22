# 上剧方 P3 实施与测试报告

> 范围：`docs/supplier-role-plan.md` P3（审核队列、批量通过、上架交接、通知）
> 日期：2026-10-22　环境：本地（Docker Postgres/Redis，API :18110，Vite :5173）
> 结论：**P3 功能完成，测试全绿；尚未部署线上。**

## 1. 本阶段交付

| # | 内容 | 说明 |
|---|---|---|
| 1 | 审核队列 | `GET /admin/v1/reviews?state=pending|approved|rejected`，带计数与分页 |
| 2 | 单条裁决 | `POST /reviews/{id}/decision`，通过/驳回；驳回原因必填 |
| 3 | 批量裁决 | `POST /reviews/batch`，≤50 条，逐条校验、返回 `done/skipped` |
| 4 | 上架交接 | `POST /reviews/{id}/publish`，一次点击同时发布剧集与全部分集 |
| 5 | 发布守卫 | 旧剧集页/排期任务无法绕过审核直接上架（409 `not_approved`） |
| 6 | 邮件通知 | 通过/驳回后发给供应商联系人；发送失败不回滚裁决 |
| 7 | 运营页面 | 「待审核」页：三个 tab、多选批量、驳回弹窗、上架确认 |

## 2. 关键设计

### 2.1 通过 ≠ 上架

```
pending --approve--> approved（status 仍是 draft）
approved --publish--> published（剧集 + 所有 in_review 分集一起）
```

「待上架」tab 只列 `review_state=approved AND status IN (draft, in_review)`，运营点「上架」才真正对外可见。这样审核（内容合格）与排期（何时上线）解耦。

### 2.2 并发安全

裁决不是「先读后写」，而是条件更新：

```sql
UPDATE dramas SET review_state='approved', ...
 WHERE id = :id AND review_state = 'pending'
```

`rowcount=0` 即视为跳过并返回原因（不存在 / 已是其他状态）。两个人同时点批量通过，第二次会得到 `skipped`，不会重复写审计或重复发信。

### 2.3 批量语义

- 单次最多 50 条（Pydantic `max_length`，超出 422）。
- **逐条处理，不做全有或全无**：能过的过，不能过的进 `skipped` 并带原因。
- 驳回必须带原因，这段原因同时写入每一部剧的 `review_note`；需要不同理由就逐条驳回。
- 审计按剧各写一行，`payload.batch_id` 是同一个 uuid，可回溯「这次批量是谁点的」。

### 2.4 通知

- 收件人：`suppliers.contact_email`；未填或 `RESEND_*` 未配置时静默跳过。
- 内容：剧名 + 通过/驳回 + 运营备注 + 去合作方后台查看。
- 发送在事务提交之后，`MailError` 只记日志，不影响裁决结果。

## 3. 测试结果

```
cd backend && ../.venv/bin/python -m pytest -q
406 passed, 1 warning in 60.86s
```

新增 `tests/test_partner_p3.py` **10 条**：

| 组 | 用例 |
|---|---|
| 队列 | 只列上剧方剧集（自营剧不进队列）、供应商名与集数正确 |
| 权限 | finance 读队列/裁决 403（仅 ops/admin） |
| 单条 | 通过后仍为 draft、记录 reviewed_by；驳回缺原因 422；重复裁决 409 |
| 批量 | 3 条中 1 条已处理 → done=2 + skipped 带原因；驳回缺原因 422；51 条 422 |
| 上架 | 未通过直接发布 409 `not_approved`；通过后发布成功且分集全部 published；重复发布 409 |
| 通知 | 驳回后 `send_email` 被调用，收件人/主题/备注正确 |

### 3.1 浏览器验收（Playwright，真实点击）

```
ops 侧栏            = 含「待审核」
队列 tab            = 待审核（3）/ 待上架（0）/ 已驳回（0）
勾选 2 条批量通过    = 待审核（1）/ 待上架（2）/ 已驳回（0）
待上架点「上架」     = toast「已上架」，待上架（1）
剩下 1 条驳回        = 填原因后 待审核（0）/ 待上架（1）/ 已驳回（1）
```

数据库核对：

```
122 待审一号  draft      rejected  "封面分辨率不足，请重新上传 600×800"
123 待审二号  draft      approved
124 待审三号  published  approved   （5 集全部 published）
```

截图：`/tmp/reviews-pending.png`、`/tmp/reviews-approved.png`、`/tmp/reviews-rejected.png`。

## 4. 已知边界

1. **审核页只处理上剧方内容**：自营剧不经过队列（`supplier_id IS NULL` 直接过滤），仍走原有剧集管理页。
2. **无「撤回」入口**：供应商提交后不能自己撤回，只能等驳回；如需撤回，P4 可加 `review_state=pending → none` 的接口。
3. **邮件依赖 Resend 配置**：未配置时静默跳过，队列里也看不到「已通知」状态。
4. **无审核 SLA 提醒**：方案 §15 要求 24h 内处理，但还没有超时告警/待办提醒。
5. **上架前不校验免费集数下限**：提交时已校验，理论上发布时不会变；若运营在后台改过，发布不会二次拦截。

## 5. 结论

P3 让供应链闭环真正跑通：供应商提交 → 运营审核（可批量）→ 待上架 → 一键发布（剧集 + 分集）。下一步 P4：数据中心（播放/转化/收益）与月度结算单。
