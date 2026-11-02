# 金币与任务页（后台 /rewards）验收记录 — 2026-10-14

- 范围：管理后台「金币与任务」页 + `/admin/v1/rewards/config` 接口 + `app.seed_rewards` 预设数据。
- 结论：预设数据已写入 dev 库；页面交互（加载 / 编辑 / 校验 / 保存 / 次日生效 / 撤回 / 变更记录）全部通过自动化验收。
- 生效口径：America/Los_Angeles；当天（LA 2026-10-13）保存，次日 2026-10-14 生效。

## 预设数据（`python -m app.seed_rewards`）

| 任务 | code | 预设值 |
|---|---|---|
| 7 天签到梯度 | `checkin` | +30 / +30 / +50 / +50 / +30 / +60 / +90 |
| 注册奖励 | `register` | +80 |
| 绑定邮箱 | `bind_email` | +30 |
| 绑定 Facebook | `bind_facebook` | +80 |
| 每日广告奖励 | `ad` | +30 |

脚本幂等（有生效行则 skip），`--reset` 只清理目录内的 code 后重写当天基线。

## 接口

- `GET /admin/v1/rewards/config`：返回 5 个任务的当前生效值（含 `effective_on` / `is_default`）、`pending` 待生效值、`today`、`next_effective_on` 与最近变更记录。只读，所有后台角色可访问。
- `POST /admin/v1/rewards/config`：`require_content_writer`（运营/管理员），校验 code 与金额个数（签到固定 7 个、其余 1 个，1–1000000 整数），写 `effective_on = 明天` 的行；同一天重复保存更新同一行（不再 500），并写 `audit_logs`（action=`reward_config_update`，含操作人）。
- 财务角色写 → 403；未知 code / 金额个数不对 / 金额 ≤0 → 422。

## 页面交互

- 顶部提示「现在保存的改动将于 {次日}（洛杉矶时间）生效」。
- 签到梯度：7 个数字输入（第 1–7 天）+ 当前生效值 + 待生效标签（含"与当前一致"标记）+ 保存 / 撤回未保存修改。
- 任务奖励表：注册 / 绑定邮箱 / 绑定 Facebook / 每日广告，逐行显示当前生效、待生效、调整（金币）+ 保存 / 撤回，未修改时按钮禁用。
- 变更记录：时间、任务、金额、生效日期、操作人。
- 校验在前端与后端同时生效（清空输入保存会被拦截并提示 7 个 1-1000000 的整数）。

## 自动化证据

- 后端：`cd backend && MINITV_TEST_DB=... ../.venv/bin/python -m pytest -q` → **126 passed**（含新增 `test_reward_config_admin.py` 5 项、`test_seed_rewards.py` 3 项）。
  - 覆盖：默认值回退、保存次日生效、同日重复保存 upsert 单行、审计写入、非法 code/个数/金额 422、财务 403、梯度改动"今天不生效明天生效"。
- 前端：`cd admin && npm run build`（tsc --noEmit + vite build）通过。
- 浏览器端到端（Playwright，headless Chromium，dev 服务 5173/18110）：**24 项检查全部通过**，无前端控制台错误。
  - 加载预设值 → 非法输入拦截 → 改签到第 1 天为 35 保存（提示"将于 2026-10-14（洛杉矶时间）生效"）→ 广告改 45 保存 → 刷新后待生效值持久化 → 撤回未保存修改复原 → 服务端 pending/current 校验（current 仍是旧值，未提前生效）→ 通过界面恢复预设值 → 变更记录含操作人。

截图：`rewards-01-overview.png`（页面总览）、`rewards-02-pending.png`（待生效状态）、`rewards-03-final.png`（恢复预设后的最终状态 + 变更记录）。

## 收尾状态

- dev 库最终保留 5 条当天生效的预设行；验收过程产生的 2 条 pending 行已清理，`audit_logs` 保留完整变更记录（页面"变更记录"可见，操作人=运营示例）。
- 未提交代码；改动文件：`backend/app/modules/account/service.py`、`backend/app/modules/ops/flow_router.py`、`backend/app/seed_rewards.py`、`backend/tests/test_reward_config_admin.py`、`backend/tests/test_seed_rewards.py`、`admin/src/pages/Rewards.tsx`、`admin/src/App.tsx`、`admin/src/pages/StagePages.tsx`。
