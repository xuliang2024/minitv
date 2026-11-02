# 批量上传分集（批量编辑 + 排队上传）验收记录 — 2026-10-14

- 范围：后台「剧集管理 → 分集」新增「批量上传」交互。按用户确认的设计草图落地：
  可持续追加视频、自动批量编辑集数/标题、排队上传（同时最多 2 个）、防刷新警示 +
  浏览器级拦截、收起后后台继续 + 右下角进度浮条、失败可重试。
- 结论：`admin npm run build`（tsc + vite build）通过；Playwright 真实浏览器端到端
  （dev 5173/18110，真实 Bunny minitv-dev）**全部检查通过**；后端零改动。
- 测试数据已清理（见文末），剧 #17 还原为原有 1-4 集。

## 1. 实现清单

| 文件 | 说明 |
|---|---|
| `admin/src/pages/episodeUpload.ts`（新） | 从 EpisodesPanel 抽出的共享上传原语：TUS 直传（directUpload）/ 本地探测（probeVideo）/ 转码轮询（waitForTranscode）/ 格式化与常量 |
| `admin/src/pages/batchUpload.ts`（新） | 模块级单例队列 store（useSyncExternalStore）：并发 2 调度 + FIFO 补位、自动编号（起始集数、跳过已占用）、标题规则（Episode {n} / 第 {n} 集 / 自定义 {n}）、失败上报 upload-failed、重试复用 episodeId、中途移除（abort + 上报）、有未完成任务时挂 beforeunload、全部结束 toast。跨组件/路由存活，仅整页刷新丢失 |
| `admin/src/pages/BatchUploadPanel.tsx`（新） | 工具栏「批量上传（N）」按钮、880px 弹窗（警示条 / 常驻拖拽区 / 起始集数 + 标题规则 / 队列表格 / 统计与 清空已完成·暂停队列·收起·后台上传）、收起后右下角浮条（已完成 x/y · 转码中 n · 请勿刷新页面 · 打开） |
| `admin/src/pages/EpisodesPanel.tsx`（改） | 接入入口/弹窗/浮条；批量项 settle 后自动刷新列表；本地上传原语改为共享模块 import |

后端未改动：复用 `POST /dramas/{id}/episodes`（重复集数 409）、`POST /episodes/{id}/upload-session`、
`POST /episodes/{id}/sync-status`、`POST /episodes/{id}/upload-failed`。

## 2. 端到端验收（Playwright + Chrome，真实 Bunny）

测试剧 #17「测试」（原有 1-4 集）。追加 4 个 0.37MB 测试视频（编号 13-16）+ 1 个失败注入项（17）。

通过项（ALL CHECKS PASSED）：

1. 批量弹窗打开，一次多选 4 个文件全部入队；
2. 防刷新警示条可见（含「已进入 Bunny 转码的不受影响」说明）；
3. **并发上限 = 2**：采样期间最大同时上传数 2，其余显示「排队中 · 下一位」；
4. **刷新被 beforeunload 拦截**：reload 触发 `beforeunload` 确认框，取消后页面保持、队列未丢；
5. 4 个直传全部完成、0 失败（上传阶段约 21s）；
6. **收起后右下角浮条可见**，点「打开」可重新展开弹窗（队列在收起期间继续跑）；
7. 注入失败（第 5 个文件建集接口返回 500）→ 行内红色错误 + 「上传失败」+「重试」；
8. 点「重试」→ 重新建集、重新直传成功，离开失败态；
9. 收起后主列表自动刷新，出现新分集；服务端新增 5 集、无失败行。

截图（本目录）：

- `batch-upload-01-queue.png` —— 入队瞬间：上传中 2/2（含进度条）、排队 1「下一位」、转码中 1
- `batch-upload-02-uploaded.png` —— 4 个直传完成后的状态
- `batch-upload-03-pill.png` —— 收起后的浮条 + 主表刷新（含工具栏「批量上传（4）」计数）
- `batch-upload-04-transcoding.png` —— 转码中（含「稍后同步」提示）
- `batch-upload-05-retry.png` —— 注入失败行（红字错误）+ 重试入口
- `batch-upload-06-final-list.png` —— 主列表回写 + 浮条（已完成 0/5 · 转码中 5）

## 3. 外部状况与备注

- **Bunny 编码队列当时整体停滞**：测试时段内 13 个已上传视频（含更早人工操作的 ep4）
  长时间 `remote_status=2` 且 `encodeProgress=0`（30+ 分钟）。直传字节已全部到达
  Bunny（TUS POST/PATCH 均 2xx）；编码完成后的状态流转（sync-status → in_review）
  复用当日已验收的单集同一路径。轮询超时的行显示「转码中 · 稍后同步」，可在主列表
  点「同步状态」刷新（截图 05 可见）。
- 已知边界（设计内）：队列存于当前页面内存，刷新/关闭标签页会丢（警示条 +
  beforeunload 双重兜底）；不支持断点续传（中断即整文件重传）。
- **测试数据清理**：剧 #17 新增的 13 条测试分集（编号 5-17）及其 Bunny 对象、
  media_uploads 级联全部删除（`audit_logs.action=batch_upload_e2e_cleanup`，
  deleted=13）；剧 #17 还原为原有 1-4 集。ep4 处于「转码中」为人工操作遗留，未触碰。

## 4. 复跑方式（一次性脚本）

```bash
# 生成测试视频
ffmpeg -y -f lavfi -i "testsrc2=duration=3:size=360x640:rate=24" \
  -f lavfi -i "sine=frequency=440:duration=3" -c:v libx264 -preset ultrafast \
  -b:v 600k -pix_fmt yuv420p -c:a aac -b:a 48k /tmp/litetv-batch-test/batch-ep-a.mp4
# 端到端（NODE_PATH 指向全局 playwright）
NODE_PATH=/opt/homebrew/lib/node_modules node /tmp/litetv-batch-test/test-batch-upload.mjs
# 测试数据清理（先 --dry-run）
cd backend && PYTHONPATH=. ../.venv/bin/python /tmp/litetv-batch-test/cleanup_test_episodes.py --dry-run
```
