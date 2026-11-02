# 2026-10-14 分集批量上架 / 下架（勾选 + 批量操作）

## 需求

后台分集表格左侧增加勾选列，勾选后可批量**上架 / 下架**；上架后的分集要能一键下架，
下架不掉视频对象。

## 后端

1. **新增分集状态 `unpublished`（已下架）**——此前分集只有 `published → not_uploaded`
   一条下行路径，那会丢"已上传"语义、逼着重新上传；现在有真正的下架态，视频对象保留：
   - `EPISODE_STATUSES` / `EPISODE_TRANSITIONS`：`published ⇄ unpublished`（外加原有的
     `→ not_uploaded` 换片路径）；
   - 迁移 `c4f7a9b2d6e1`：同步替换 DB 触发器
     `minitv_assert_episode_status_transition()`（数据库层同样只放行合法迁移）。
2. **新接口**
   - `POST /admin/v1/episodes/{id}/unpublish` 单集下架（下架时 bump playback generation，
     让已签出的本地 token 立即失效；线上 CDN token 本身 5 分钟过期）；
   - `POST /admin/v1/episodes/bulk-status`
     `{"episode_ids": [...], "action": "publish" | "unpublish"}`；
     逐条迁移，不可迁移的返回 `skipped`（`no_video` / `invalid_transition` / `not_found`），
     不会因为表里混着不同状态而整批失败；权限沿用 `require_content_writer`（ops/admin）。
3. 公开可见性沿用既有规则：所有客户端读取路径只放行 `status == "published"`，
   `unpublished` 自动从 App/官网/播放授权中消失；`prune_not_uploaded_episodes` 只清
   `not_uploaded`，不会误删下架内容。

## 前端（admin/src/pages/EpisodesPanel.tsx）

- 表格新增勾选列（`rowSelection`），没有视频的行禁用勾选；
- 工具栏在选中后出现：`已选 N 集` + **批量上架** + **批量下架** + 取消选择；
  完成后 toast 报告成功/跳过数量并刷新表格；
- 行内操作补齐：`已上架` 行显示 `下架`，`已下架` 行显示 `上架`（可重新上架）。

## 验收（2026-10-14 12:0x）

- 冒烟剧（临时建、用完删除）3 集全流程：勾选全部 → 批量上架（3/3 已上架）→
  批量下架（3/3 已下架）→ 单行重新上架 ✅；
- 真实数据：剧 #15 的 11 集「审核中」勾选后批量上架成功（成功 11 集），
  公开接口 `POST /v1/playback` 对第 3 集返回 `allowed=true` + 可播签名地址 ✅；
- 回归：`pytest` **152 passed**；admin `tsc --noEmit` + `npm run build` 通过 ✅。

截图：`episode-bulk-selected-2026-10-14.png`、`episode-bulk-published-2026-10-14.png`、
`episode-bulk-unpublished-2026-10-14.png`、`drama15-episodes-bulk-published-2026-10-14.png`
