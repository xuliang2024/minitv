# lite.tv 正片下载：早前「下不了」的结论是错的

更新时间：2026-10-13 22:40（Asia/Shanghai）

## 1. 结论（TL;DR）

lite.tv 的正片**可以下载**。早前判断「CDN 按客户端做授权校验、带签名也 401、本机网络拿不到流」
是**误判**：真正的失败原因不是 IP 或地区，而是**少发了两个请求头**。

CDN（`es-w1.lite.tv`）校验分两块：

| 位置 | 内容 | 缺了会怎样 |
|---|---|---|
| query string | `?signature=...&expires=...` | 401 |
| **请求头** | `signature: <同一个值>`、`expires: <同一个值>` | **401**（关键） |

播放器（站点用的 xgplayer）是**把签名同时放进 query 和请求头**的；只带 query 一律 401。

## 2. 复现证据

同一个签名地址 `output480.m3u8?signature=d7d5...&expires=1792134000`：

```bash
# A) 只带 query —— 401
curl -o /dev/null -w "%{http_code}\n" \
  -A "$UA" -e "https://www.lite.tv/" "$URL?signature=$SIG&expires=$EXP"
# => 401

# B) 额外带同名请求头 —— 200
curl -o playlist.m3u8 -w "%{http_code} %{size_download}\n" \
  -A "$UA" -e "https://www.lite.tv/" \
  -H "signature: $SIG" -H "expires: $EXP" "$URL?signature=$SIG&expires=$EXP"
# => 200 422
```

浏览器侧同样能证明：用 headless Chrome + CDP 抓站点播放器的真实请求，
`Network.requestWillBeSent` 里 m3u8 / .ts 的请求头就带着
`signature` 与 `expires`（另有 `Referer: https://www.lite.tv/`），响应 200。

```bash
node /tmp/cdp_headers.mjs "https://www.lite.tv/player?mid=180&eid=1" 40000
# GET 200 application/octet-stream  .../output480.m3u8?... 
# REQUEST HEADERS: { "signature": "d7d5...", "expires": "1792134000", "Referer": "https://www.lite.tv/", ... }
```

## 3. 免费范围（访客可下载的部分）

`GET /v1/media_episode/play` 对锁定集返回 `{"code":50003,"error":"Please unlock in order"}`。
2026-10-13 实测（guest 会话，mid=180 / 183）：

```
180  0:F 1:F 2:F 3:F 4:F 5:F 6:F 7:F 8:F 9:L 10:L ...   # F=可播, L=锁定
183  0:F 1:F 2:F 3:F 4:F 5:F 6:F 7:F 8:F 9:L 10:L ...
```

即每部剧**第 0–8 集**（9 集）访客可播，第 9 集起需要金币/会员解锁。
本流程只下载可播集，不做任何绕过付费墙的尝试。

> 注意：`media_episode/list` 里的 `is_free` 字段与真实可播范围并不一致
> （目录数据里标的是 0–7 集），判定要以 `play` 接口的实际返回为准。

## 4. 工具用法

```bash
# 探活：抽查若干剧的第 N 集能不能拿到播放列表
.venv/bin/python scripts/fetch_litetv_videos.py probe --samples 10 --episode 0

# ① 先扫可用性：逐集试、遇到锁定即停，产出 availability.json（哪几集真能播）
.venv/bin/python scripts/fetch_litetv_videos.py scan --episodes 0-79 --quality p480 --workers 10

# ② 按可用性清单精确下载（推荐；只下能播的集，不做无用探测）
.venv/bin/python scripts/fetch_litetv_videos.py download \
    --from-scan --quality p480 --workers 10

# 也可以按区间/指定剧下载
.venv/bin/python scripts/fetch_litetv_videos.py download \
    --media-ids 180,183 --episodes 0-8 --quality p720 --workers 6

# 校验清单里的文件是否齐全
.venv/bin/python scripts/fetch_litetv_videos.py verify
```

输出目录：`data/litetv-videos-real/<media_id>/<media_id>_epNNN.mp4`（默认），
同目录两个报告：

- `availability.json`：`scan` 的产物，每部剧 `free_episodes` / `locked_from`（app 后台可直接当"免费集"依据，
  比接口里那个不可信的 `is_free` 准）
- `manifest.json`：`download` 的产物，`items`（成功集 + 字节数）、`locked`、`failures`、
  `cdn_requirement`（必须带签名的请求头）

下载实现：`ffmpeg -user_agent <UA> -headers "signature: ...\r\nexpires: ...\r\n..." -i <m3u8> -c copy`。
分片请求与播放列表走同一组 header，所以两个头必须都给。
已存在的文件默认跳过（所以先下的 720p 不会被 480p 覆盖）。

> 性能提示：不要与该目录里并行的 x264 合成任务抢机器——编码占满 CPU 时下载会从 ~2 MB/s
> 掉到 ~0.5 MB/s。`build_local_video_library.py` 现在默认拒跑（见 §5）。

## 4.1 本仓库当前的全量抓取结果

```bash
.venv/bin/python scripts/fetch_litetv_videos.py scan --episodes 0-79 --quality p480 --workers 10
# 本轮扫描 100 部；清单合计 100 部剧、1197 集可播（每部 7–80 集，均值 11.97）
# 2026-10-14 01:24 全部完成：1197/1197 集、0 失败、0 锁定，36.1 小时内容、实际占盘 15 GB；
# 内容哈希去重后 1197 集 = 943 份不同内容（254 个重复集号改为硬链接，省 1.8 GB）

.venv/bin/python scripts/fetch_litetv_videos.py download --from-scan --quality p480 --workers 10
# 待尝试 1197 集 → data/litetv-videos-real

# 收尾：内容去重 + 清单校验
.venv/bin/python scripts/fetch_litetv_videos.py dedupe   # 1197 个文件 = 943 份不同内容
.venv/bin/python scripts/fetch_litetv_videos.py verify   # 清单 1197 集，缺失 0 集
```

> 库说明（含数据口径、硬链接含义、清晰度混合情况）另见
> [../data/litetv-videos-real/README.md](../data/litetv-videos-real/README.md)。

免费集数在剧与剧之间差别很大（7 集到整部 80 集都有），
所以**不能**按固定集数下（早前按"前 9 集"估的口径是错的）。


## 5. 目录分工（重要：本仓库同时有另一个会话在跑）

| 目录 | 内容 | 由谁写 |
|---|---|---|
| `data/litetv-videos-real/` | **站点真片**（HLS 转封装 MP4，720×1280，带真实音轨/字幕） | `scripts/fetch_litetv_videos.py` |
| `data/litetv-videos-synth/` | 合成占位视频存档（静态封面 + `EPISODE n` 烧字 + 静音，871 集） | 无人写；`build_local_video_library.py` 已默认拒跑 |
| ~~`data/litetv-videos-local/`~~ | 已废弃：占位视频目录，2026-10-13 23:00 起不再使用 | — |

踩过的坑：两个进程同时写 `data/litetv-videos-local` 会互相覆盖——
并行会话的 `find data/litetv-videos-local -name "*.part.mp4" -delete` 会删掉
真片下载中的临时文件，ffmpeg 随即报
`Unable to re-open ... output file for shifting data`。
因此真片固定写 `data/litetv-videos-real/`，**不要再把两边指到同一目录**。

## 6. 验收方法

```bash
ffprobe -v error -show_entries format=duration,size \
        -show_entries stream=codec_name,width,height -of default=nw=1 \
        data/litetv-videos-real/180/180_ep001.mp4
# duration=73.13  width=720  height=1280  codec=h264 / aac

# 与站点播放页时限对得上（截图里 01:13），抽帧可肉眼比对：
ffmpeg -ss 7 -i data/litetv-videos-real/180/180_ep001.mp4 -frames:v 1 /tmp/frame.png
```

对照基准：`https://www.lite.tv/player?mid=180&eid=1` 播放页 00:07 的画面
（红裙 + 珍珠项链女性）与抽帧一致；文件时长 73 s 与站点一致。
