# 2026-10-14 删除剧集：自定义确认弹框 + 删除不生效的根因修复

## 问题（用户反馈）

1. 点「删除剧集」弹出的是浏览器原生 `window.confirm`，标题/内容不可控，正文直接把
   接口错误 `{"detail":"admin only"}` 当影响范围展示；
2. 点「确定」之后**什么都没发生**：剧集还在，列表里还能打开。

## 根因

- 前端：`PublishPanel` 的删除流程用裸 `fetch` 请求删除预检（不处理 401/403/非 JSON），
  然后用 `window.confirm` 展示 `JSON.stringify(body.detail?.impact ?? body)`；确认后
  直接 `await api(...)`，异常没有任何 catch，失败被静默吞掉，所以「点了没反应」。
- 权限：`POST /admin/v1/dramas/{id}/delete` 挂的是 `require_admin_role`
  （与调账、套餐改价同一档高危操作，见 docs/software-architecture.md 角色矩阵
  「管理员：调账、删除剧集、角色管理」）。用 **ops 账号**登录时预检就返回
  `403 {"detail":"admin only"}`，弹框里那串 JSON 正是这个 403；确认后的第二次调用同样
  403，于是删除永远不生效。后端本身没问题：admin 账号调用时预检 409 + 影响范围、
  确认后 200，剧集与分集一起消失（再次访问 404）。

## 修复

### 前端

- 新增 `admin/src/confirm.tsx`：`useConfirm()` 基于 antd `Modal` 的自定义确认弹框
  （支持危险按钮、异步确认 loading、失败保持弹框打开便于重试），替换后台里所有
  `window.confirm`（删除剧集、上架前未完成步骤、列表快捷上架缺封面/分集）。
- `PublishPanel` 删除流程重写：
  - 预检改走统一的 `api()`（401 自动跳登录、403 明确提示「只有管理员可以删除剧集」）；
  - 弹框展示结构化影响范围：分集 N 集 / 首页分组引用 / Banner 引用 / 推送记录 /
    用户数据（观看进度、解锁记录、评论），并明确「删除后不可恢复」；
  - 确认后调用删除接口，成功 toast + 跳回 `/dramas`；失败留在弹框并提示原因；
  - 删除按钮按角色控制：非管理员置灰 + Tooltip「仅管理员可删除剧集」。
- `admin/src/me.tsx` 增加 `useIsAdmin()`（与后端角色矩阵对齐）。

### 后端

- `content/publish_service.py::_impact` 增加 `episode_count`，删除预检返回
  `{"groups": [...], "banner_count": N, "push_count": N, "episode_count": N}`，
  弹框直接展示分集数（前端也有详情页集数兜底）。

## 验收

- 端到端（Playwright headless，1440×900，临时剧集用完即删）：
  - ops 账号：删除按钮置灰，hover 出「仅管理员可删除剧集」✅（截图 ops-disabled）
  - admin 账号：点删除 → 自定义弹框（非原生）；取消 → 剧集仍在（GET 200）✅
  - 确认删除 → `GET /admin/v1/dramas/{id}` 变 404、页面跳回 `/dramas`、列表不再出现 ✅
- 接口层：admin 预检 `409 confirm_required + impact`（含 `episode_count`）；ops 预检
  `403 admin only`；admin 确认 `200 {"deleted":true}` ✅
- 回归：`pytest` **156 passed**；admin `tsc --noEmit` + `npm run build` 通过 ✅

截图：`drama-delete-custom-modal-2026-10-14.png`（admin 自定义弹框）、
`drama-delete-ops-disabled-2026-10-14.png`（ops 置灰 + Tooltip）

## 备注

- 如需让 ops（运营）也能删除剧集，需产品决策：把 `remove_drama` 的
  `require_admin_role` 换成 `require_content_writer`，但会偏离当前
  「删除剧集 = 管理员高危操作」的设计；前端按钮的 `useIsAdmin()` 也要同步放开。
