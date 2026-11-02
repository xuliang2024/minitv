# 2026-10-14 分集「时长」数据修复（上传后可见）

## 问题

后台分集表格的「时长」列一直显示 `-`：上传、转码回调、状态同步三条链路都没有把
时长写进 `episodes.duration_seconds`（字段本身存在，回放接口也已经在返回它）。

时长其实读得到，有两个来源：
* 本地导入时 `ffprobe` 直接读文件（立即可用）；
* Bunny 转码完成后的视频元数据 `length`（权威值，直传路径唯一来源）。

## 修复（三条链路全部写入）

1. `backend/app/import_local_videos.py`
   - `_probe_dimensions` 增加 `format=duration`，返回 `(width, height, duration_seconds)`；
     上传完成即写入 `duration_seconds`；
   - `--sync-status` 轮询 Bunny 时用 `length` 校准（返回里新增 `durations` 计数）。
2. `backend/app/modules/content/upload_service.py`（后台直传回调）
   - `apply_callback(outcome="finished")` 在 real 模式下读一次 Bunny 元数据，把 `length`
     写入 `duration_seconds`（best-effort，Bunny 抖动不会让回调失败）。
3. `backend/app/modules/ops/flow_router.py`（后台「同步」按钮）
   - `POST /admin/v1/episodes/{id}/sync-status` 在轮询时同时回填 `length`，
     响应新增 `duration_seconds`。

## 历史数据回填

一次性脚本从 Bunny 读取 `length`，回填了 **86 条**分集（剧 #1–#15 所有真实分集）：
剧1:9、3:10、4:9、5:7、6:7、7:8、8:9、9:7、10:9、11:8、17:3。
剩余 1 条无时长的是剧 #2 的 smoke 测试空对象（Bunny 里从未上传字节，无 length）。

## 验收（2026-10-14 11:5x）

- 后台分集表(新 `EpisodesPanel`)剧 #1 的 9 集时长显示 `1:10 / 1:13 / 0:52 / 1:05 / 1:25 / 1:09 / 1:06 / 1:11 / 0:56`；
- 与播放器一致：第 1 集列表 `1:10` ↔ 弹框播放器 `duration=70.9s`；
- 弹框预览仍直接播放（`paused=false, readyState=4`）；
- 回归：`pytest` **138 passed**。

截图：`episodes-duration-column-2026-10-14.png`、`episodes-duration-preview-modal-2026-10-14.png`
