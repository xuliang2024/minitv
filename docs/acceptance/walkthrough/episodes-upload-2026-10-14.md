# 分集清理 + 手动上传入口 验收记录 — 2026-10-14

- 范围：dev 库「登记了但从未上传」的分集数据清理；后台「分集」页新增「上传新分集」
  入口（真实直传 Bunny）；配套后端接口（同步转码状态 / 上报上传失败 / fake 模式本地
  TUS 接收器）与脚本 `app.prune_not_uploaded_episodes`。
- 结论：dev 库 912 条占位分集已清理；浏览器端到端（创建分集 → 直传 → 转码 →
  审核中 → 上架 → 预览）全部通过。

## 1. 数据清理

```bash
cd backend
../.venv/bin/python -m app.prune_not_uploaded_episodes --dry-run   # 912 条候选，0 条被引用
../.venv/bin/python -m app.prune_not_uploaded_episodes             # 已执行
```

| | 清理前 | 清理后 |
|---|---|---|
| 分集总数 | 1044 | 132 |
| `not_uploaded` | 912 | 0 |
| 有 Bunny 视频的分集 | 132 | 132 |

- 规则：只删 `status=not_uploaded` 且 `bunny_video_id` 为空的行；连同其
  media_uploads / playback_events / playback_progress / cdn_purges 一起删除；
  被 entitlements / coin_ledger / comments 引用的行跳过并报告（本次 0 条）。
- 每次删除按剧写 `audit_logs`（action=`episode_prune_not_uploaded`，含
  deleted / skipped / kept / cascaded 计数），本次共 14 条。
- 清理后每部剧 `episode_count == 有视频的集数`，后台「上架流程」不再报
  “还有 N 集没有上传视频”。
- 遗留：剧集 #2「E2E Smoke Bunny」有 1 条旧版假上传按钮留下的空转码会话
  （Bunny 视频对象 `4c332684…` 无字节、`remote_status=0`）；该行可在列表点
  「同步状态 / 重新上传」处理，或直接删除这条测试剧。

## 2. 后台交互（admin `分集` 页）

- 工具栏：`上传新分集`（运营/管理员可见）+ 状态筛选 + 刷新 + 计数。
- 上传弹层：拖拽/点选视频（MP4 / MOV，≤200MB，横版 / 竖版均可）、集数（默认下一集）、
  标题（默认 `Episode N`）、本地探测分辨率/时长、直传进度条、转码中提示。
  方向只展示不拦截：视频信息行会标出「横版 / 竖版」。
- 上传流程：`POST /admin/v1/dramas/{id}/episodes`（默认标题、集数重复返回 409）→
  `POST /admin/v1/episodes/{id}/upload-session` → 浏览器 TUS（POST 建会话 + PATCH
  发字节，直传 `video.bunnycdn.com`，不经过 API）→ 轮询
  `POST /admin/v1/episodes/{id}/sync-status`（Bunny status 4 → `in_review`）→ 表格刷新。
- 失败可重试：浏览器上传中断时前端调 `POST /admin/v1/episodes/{id}/upload-failed`
  （`transcoding → transcode_failed`），行上出现「重新上传」，不会卡死在转码中。
- 行操作：预览（签名 iframe 弹层）、同步状态（transcoding）、上架（in_review）、
  上传 / 重新上传（未上传或转码失败）。旧版「登记 64 集」按钮与硬编码假上传已从
  本页移除。
- fake 模式（无 Bunny 凭据的本地/CI）会返回同源 `/admin/v1/dev-tus/{video_id}`
  接收器，POST 建会话 + PATCH 收字节后直接置为 `in_review`，同一套前端代码可跑通。

## 3. 验证证据

- 后端：`cd backend && ../.venv/bin/python -m pytest -q` → **134 passed**
  （新增 `test_prune_not_uploaded_episodes.py` 3 项、`test_manual_episode_upload.py`
  5 项：默认标题/重复 409、fake 同步转码、失败重试链路、dev-tus 完成上传与幂等、
  写权限守卫）。
- 前端：`cd admin && npm run build`（tsc --noEmit + vite build）通过。
- 浏览器端到端（Playwright + Chrome，dev 5173/18110，真实 Bunny minitv-dev）：
  - 剧 #17「测试」上传 0.7MB 竖版 MP4：TUS POST `201` → PATCH `204`（约 5s），
    11s 后自动进入 `审核中`，行内「上架」→ `已上架`，预览 iframe
    `https://iframe.mediadelivery.net/embed/778985/...` 返回 200。
  - 上传中途「取消上传」（60MB 文件）：弹层提示「上传已取消」，行落到 `转码失败` 并
    出现「重新上传」；再传 0.7MB 文件 22s 后回到 `审核中`。
  - 剧 #1「I Became the Mob Boss's Wife」清理后分集页显示 9 集全部「已上架」，工具栏
    显示「共 9 集」。
- 时长：`sync-status` 与转码回调都会用 Bunny 的 `length` 回填 `episodes.duration_seconds`
  （清理前登记的目录时长随行删除，老数据在下次同步时补齐）。
- 截图：`episodes-panel-2026-10-14.png`（清理后分集页）、
  `episodes-upload-modal-2026-10-14.png`（上传弹层 + 视频探测）、
  `episodes-drama17-2026-10-14.png`（上传后：审核中 + 已上架）。
