# 2026-10-14 后台分集预览改弹框播放 + 剧 #1 数据修复

## 两人改动

1. **预览弹框播放(替代新开标签页)**
   - 后端 `bunny.stream_embed_url` 现在同时返回 `url`(player.mediadelivery.net 独立播放页)和
     `embed_url`(iframe.mediadelivery.net 内嵌页,同一 Embed View token + `autoplay=true`);
     `/admin/v1/episodes/{id}/preview-url` 原样透出两个地址,老调用方不受影响。
   - 后台 `EpisodesPanel` 的「预览」按钮改为 Ant Design Modal + iframe(黑色底、74vh、允许自动播放/全屏),
     不再 `window.open` 新标签页。
2. **剧 #1 假数据修复**
   - 原 13 集 `demo-1..13` 占位 ID(Bunny 404)按状态机 `published -> not_uploaded` 清出,
     再以 `import_local_videos --drama-id 1 --media-id 180` 导入本地素材(media 180
     "30 Days Swapping Lives with a Mafia Boss",同为「互换身份 + 黑帮」题材、共 64 集,
     与剧 #1 简介/集数一致;新增 `--media-id` 用于标题不在目录里的演示剧)。
   - 上传 9 集(ep000-008)→ 转码完成后 `in_review -> published` 重新上架;
     ep10-13 无本地片源,保持 `not_uploaded`(表格显示「上传」)。

## 验收证据(2026-10-14 11:30-11:36)

- 公开播放链路:剧 #1 第 1-9 集 `POST /v1/playback` 全部 `allowed=true`,
  签名 HLS 播放列表 **9/9 HTTP 200**(`#EXTM3U`),iframe embed **9/9 HTTP 200**。
- 后台弹框实测(Playwright + Chrome):点击「预览」→ 弹框出现 → 框内 Bunny 播放器真实播放
  (`paused=false, currentTime=1.69s, readyState=4, video.error=null`);
  点第 2 集仍在同一弹框打开,浏览器标签数保持 1(无新开 tab)。
- 控制台无 4xx/5xx 资源请求(全流程 response 监听)。
- 回归:后端 `pytest` **126 passed**;admin `tsc --noEmit` + `npm run build` 通过。

截图:`episode-preview-modal-playing-2026-10-14.png`、`episode-preview-modal-ep2-2026-10-14.png`

## 遗留

- 剧 #1 的 `free_episode_count` 仍为 12(演示配置),实际有片源的是 1-9 集(media 180 免费范围);
  ep10 以后客户端不可见(未上架),如需口径一致可再调。
