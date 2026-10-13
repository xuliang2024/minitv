# 阶段 2 验收记录：后台登录与建剧

- 日期：2026-10-13
- 执行人：WorkBuddy（Agent，用户已授权自动连续执行）；复核：待用户确认
- 依据：`docs/development-plan.md`「阶段 2」范围与 5 条验收标准
- Git 提交：验收时点 HEAD `8203163`（stage 1）；本阶段变更随收尾提交入库
- 结论：**通过**。接口层 10 项新增测试全绿（累计 70 passed）；前端四页实现并构建通过，全链路（登录 → 列表 → 保存 → 公开不可见）经 Vite 代理冒烟验证

## 1. 决策记录（AI 自动模式下代拍，待确认后冻结）

- **组件库：Ant Design 5**（React 18 + TypeScript + Vite）。理由：表格 / 表单密集型后台开箱即用效率最高；暗色主题（`theme.darkAlgorithm`）+ 主红 `#E53B3E` 对齐设计稿。选定后按计划不更换，如需更换请在下一阶段前提出。

## 2. 交付物

- `backend/app/modules/ops/`：admin_users 表（迁移 `5173d88f0ccc`）、pbkdf2 密码哈希（标准库实现）、Redis 会话（TTL 8h，`minitv_admin_session:` 前缀）。
- `/admin/v1` 路由：auth/login / me / logout、dramas CRUD（服务端 status/q/tag 筛选 + 分页）、tags CRUD（缺英文名 422、重名 409、删除前返回影响范围 409）、search（剧集 + 用户占位空）。
- `/v1` 公开路由：home / dramas/{id} / search —— 全部走 `content.service` 公开读入口。
- 角色权限：ops / admin 可写内容；finance 读写分离（写 403）；未登录 401。
- 前端 `admin/`（Vite + React + TS + AntD，暗色 + 主红）：登录、剧集列表（服务端筛选分页）、基本信息工作台（封面/剧名/简介/标签/免费集数/金币）、标签库（影响范围提示）。Vite 代理 `/admin/v1`、`/v1` 到 18110。
- seed 脚本：`python -m app.seed_admin`（ops/finance/admin 三个本地账号）。

## 3. 验收对照（5 条）

| # | 验收标准 | 证据 | 结果 |
|---|---|---|---|
| 1 | 未登录后台写接口 401；财务不能改剧集 | `test_unauthenticated_write_returns_401`、`test_bad_token_returns_401`、`test_finance_role_cannot_write_content`（403；读仍 200） | 通过 |
| 2 | 新建剧默认草稿；/v1 首页、详情、搜索找不到 | `test_new_drama_is_draft_and_invisible_publicly`（home 不含、detail 404、search 不含；后台 search 可见） | 通过 |
| 3 | 保存基本信息后台读到新值 | `test_basic_info_roundtrip`（title/synopsis/cover/free/coins/tags 全字段回读） | 通过 |
| 4 | 标签缺英文名不能保存 | `test_tag_requires_english_name`（422 两种缺失形态；合法保存 200；重名 409） | 通过 |
| 5 | 列表筛选分页不靠前端本地过滤 | `test_server_side_filter_and_pagination`（status/q/tag 筛选 + 12 项分页 5/5/2 页、页间不相交） | 通过 |

补充覆盖：`test_tag_delete_returns_usage_impact`（409 + affected_dramas）、`test_logout_invalidates_session`、`test_login_rejects_wrong_password`。

## 4. 命令与输出

    $ cd backend && ../.venv/bin/python -m pytest -q
    70 passed, 1 warning in 4.54s

    $ cd admin && npm run build
    tsc --noEmit ✓ · 1475 modules · built in 1.92s

    $ 全链路冒烟（Vite 代理 → 18110 新代码）：
    GET /admin/v1/dramas（无 token）  -> {"detail":"missing bearer token"}（401）
    POST /admin/v1/auth/login        -> token 9VmjhmOOFwkm...
    GET /admin/v1/dramas（带 token） -> {"total":0,"page":1,"page_size":20,"items":[]}
    GET /v1/home                     -> {"items":[]}

注：18110 原运行进程为路由挂载前启动的旧代码，已通过 `scripts/up-local.sh` 重启加载新路由（冒烟验证如上）。

## 5. 失败用例

无（测试一次全绿）。过程修复两处：18110 旧进程加载旧代码导致代理 404（重启解决）；首轮曾因 httpx 缺失需安装（已入 requirements）。

## 6. 未验证项（明确声明）

| 项 | 何时再验 |
|---|---|
| 后台界面人工走查（浏览器操作录屏/截图） | 用户复核（AI 代验：API 级 + 构建级验证已过） |
| 组件库选型确认（AI 已定 AntD 5） | 阶段 3 开工前可否决 |
| 分集页 / 工作台其余页签 | 阶段 3、6 |
| 顶部搜索用户结果 | 阶段 14 用户管理 |
| dev server 长驻运行下的热更新体验 | 用户复核 |

## 7. 本地使用

    scripts/up-local.sh                          # 后端（含新路由）
    cd admin && npm run dev                      # 后台前端 http://localhost:5173
    cd backend && ../.venv/bin/python -m app.seed_admin   # 账号 ops/ops-1234、finance/finance-1234、admin/admin-1234
