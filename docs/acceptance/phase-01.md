# 阶段 1 验收记录：账号、内容、资产、订阅事务模型

- 日期：2026-10-13
- 执行人：WorkBuddy（Agent）；复核：待用户确认
- 依据：`docs/development-plan.md`「阶段 1」范围与 6 条验收标准
- Git 提交：验收时点 HEAD `5b5a48a`（stage 0 wrap-up）；本阶段变更随收尾提交入库
- 结论：**通过**。60 项 pytest 全绿（45.31s），6 条验收全部覆盖，不依赖外部 Bunny

## 1. 交付物

- 模块目录：`backend/app/modules/{content, playback, account, wallet, billing, social, ops, analytics, common}`。本阶段实现 account / content / wallet / billing 模型与状态函数（playback 本阶段无持久表，留包）+ common 基础设施表；social / ops / analytics 为空包。
- 18 张表：users、third_party_identities、verification_codes、dramas、episodes、tags、drama_tags、home_groups、drama_home_groups、wallet_accounts、coin_ledger、entitlements、plans、plan_versions、subscriptions、orders、idempotency_records、audit_logs。
- 状态机：drama（draft / in_review / published / unpublished）与 episode（not_uploaded / transcoding / transcode_failed / in_review / published）。服务层 `transition_*` 与数据库触发器双层拒绝非法迁移；删除不是状态。
- 解锁函数：`wallet.service.unlock_episode` —— 幂等重放 → 会员优先 → 已解锁 → 行锁扣币；同一事务写流水 + 权益 + 幂等记录。
- 公开可见性唯一读入口：`content.service.{list_published_dramas, get_published_drama, list_published_episodes, get_published_episode}`。
- Alembic 初始迁移 `dd164bbf884e`：全部表 + CHECK + 部分唯一索引 + 两个状态机触发器。

## 2. 验收对照（6 条）

| # | 验收标准 | 覆盖测试 | 结果 |
|---|---|---|---|
| 1 | 草稿、审核中、下架剧集及未发布分集不能从公开查询返回 | `test_published_dramas_only` / `test_published_episodes_only` / `test_unpublished_drama_hides_its_published_episodes` / `test_nonexistent_drama_returns_nothing` | 通过 |
| 2 | 剧集与分集非法迁移全部失败、合法迁移全部成功 | 全矩阵参数化 16 + 25 个 case（`test_drama_transition_matrix` / `test_episode_transition_matrix`）+ 完整快乐路径 + 失败重试路径 + 4 个数据库触发器测试（直写 UPDATE 被拒/放行/非状态列不触发） | 通过 |
| 3 | 同一用户同一分集重复插入解锁记录失败 | `test_duplicate_entitlement_rejected`（`uq_entitlements_user_episode_type`） | 通过 |
| 4 | 扣币事务余额不足时回滚，余额不为负，不留下半截流水 | `test_insufficient_balance_rolls_back_fully` + `test_negative_balance_rejected_by_database`（CHECK `ck_wallet_balance_nonnegative`） | 通过 |
| 5 | 有效订阅存在时，解锁函数不写金币流水 | `test_active_subscription_unlock_writes_no_coin_flow`（附反向用例：过期订阅仍正常扣币） | 通过 |
| 6 | 同一幂等键重放返回第一次结果，不产生第二笔流水 | `test_idempotent_replay_returns_first_result` + `test_different_key_on_unlocked_episode_does_not_double_charge` + `test_idempotency_key_unique_in_database` | 通过 |

## 3. 命令与输出

    $ cd backend && ../.venv/bin/python -m pytest -q
    ............................................................             [100%]
    60 passed in 45.31s

    $ dev 库（127.0.0.1:15432/minitv）检查：
    tables (19): alembic_version, audit_logs, coin_ledger, drama_home_groups,
      drama_tags, dramas, entitlements, episodes, home_groups,
      idempotency_records, orders, plan_versions, plans, subscriptions,
      tags, third_party_identities, users, verification_codes, wallet_accounts
    triggers: trg_dramas_status_transition, trg_episodes_status_transition
    check constraints: ck_coin_ledger_amount_nonzero, ck_wallet_balance_nonnegative
    alembic_version: dd164bbf884e

测试库 `minitv_test` 由 conftest 每次测试会话经 `alembic upgrade head` 从零构建（含触发器），用后重建；测试始终运行在真实迁移之上。

## 4. 失败用例

无。首轮运行发现一处 fixture 缺陷（alembic `env.py` 读取 `.env` 的 `DATABASE_URL` 覆盖了测试库 URL，导致迁移落错库），已修复 conftest 改用 `ALEMBIC_DATABASE_URL` 显式注入，修复后全绿。

## 5. 手工验收截图

无（本阶段为数据库与测试层验收）。

## 6. 未验证项（明确声明）

| 项 | 何时再验 |
|---|---|
| Bunny 上传会话与回调 | 阶段 3 |
| 播放授权签发 | 阶段 4 |
| 并发扣币竞态（两个不同幂等键并发解锁同一集） | 阶段 5 验收 3 |
| 幂等键 ≥ 24 小时保留策略 | 阶段 5 |
| 签到 / 广告 / 注册奖励入账规则 | 阶段 11 |
| 分组容量 20 部的应用层校验 | 阶段 6 |

## 7. 实现说明（对齐架构）

- ADR-002 账本模式：`coin_ledger` 每笔带全局唯一 `biz_key`；`wallet_accounts.balance` 为物化聚合（CHECK ≥ 0），「金币账本差额」对账指标留待统计阶段。
- 订阅单活：部分唯一索引 `uq_subscriptions_one_active_per_user`（`status = 'active'`）。
- 订单幂等预留：`orders.external_txn_id` 唯一（支付回调防重，阶段 12 启用）。
- 验证码只存哈希（`verification_codes.code_hash`）；users 表无密码列。
- 价格改版写 `plan_versions`（version 递增）；订阅钉住购买时版本快照，改价不影响存量。
- 待确认项按计划只留字段不写死规则：`dramas.country_allowlist`、`dramas.age_rating`。
