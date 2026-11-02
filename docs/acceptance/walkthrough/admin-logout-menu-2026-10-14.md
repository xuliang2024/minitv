# 2026-10-14 后台退出登录入口（账号下拉菜单）

## 问题

后台登录后，header 右上角只有「显示名 + 角色标签」两个静态元素，没有任何退出登录的
入口，也无法查看当前登录的是哪个账号。

## 交互选型

采用后台类产品的标准做法：**把右上角账号区做成可点击的下拉菜单**。

- 触发器：红色圆形头像（显示名首字）+ 显示名 + 角色标签 + `▾`；整块是 text 按钮，
  hover 有底色反馈，窄屏（< lg 的 md 断点）自动隐藏角色标签、截断名字。
- 菜单内容：
  - 分组标题 `登录账号：<username>（角色）`——一眼确认"我是谁"，不是可点击项；
  - `退出登录`：红色危险项 + 登出图标。
- **不做二次确认**：退出不是破坏性操作，误点只需重新登录；高频后台操作少一步。
  （如需二次确认，复用 `admin/src/confirm.tsx` 的 `useConfirm()` 一句话就能加。）

## 实现（admin/src/App.tsx）

- Header 账号区改为 `Dropdown` + `Menu`（`accountMenu(me)`），`trigger=["click"]`。
- `onLogout()`：先 `POST /admin/v1/auth/logout`（服务端销毁 Redis 会话），
  **失败也继续**；然后 `clearToken()` + `setMe(null)` + `navigate("/login", {replace:true})`
  + toast「已退出登录」。token 清空后整个路由树切回登录页。

## 验收（Playwright headless）

- 打开菜单：`登录账号：ops（运营）` / `退出登录` ✅（截图 admin-logout-menu-2026-10-14.png）
- 点击退出：跳转 `/login` ✅、`localStorage.minitv_admin_token` 已清空 ✅
- 服务端会话确实销毁：旧 token 调 `/admin/v1/auth/me` 返回 **401** ✅
- 退出后访问 `/dramas` 被挡回 `/login` ✅
- 页面无 JS 报错；admin `tsc --noEmit` + `npm run build` 通过 ✅
