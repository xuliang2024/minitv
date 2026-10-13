#!/usr/bin/env python3
"""用本地目录数据合成一套竖屏短剧视频库，给 MiniTV 的开发联调用。

为什么合成而不是直接下站点正片
    lite.tv 的正片走 HLS，播放列表带签名，但该 CDN 会按客户端做授权校验：
    同一目录下的单集封面图能 200 下载，播放列表带签名仍然 401，
    用真实 Chrome 在站点源站发起也一样（见 scripts/fetch_litetv_videos.py probe）。
    当前出口（香港数据中心 IP）拿不到流，所以先用本地合成片把开发链路打通。

合成片长什么样
    每集一个 540×960（9:16）的 H.264 MP4，时长取站点真实时长（锁定集取该剧免费集的中位数）：
      · 背景是这部剧的真实竖版封面，压暗做底
      · 中间放封面主体，下方叠加剧名、集号、跑动的时间码、样例水印
      · 有真实英文字幕的集，把 SRT 作为软字幕轨封装进 MP4，同时复制一份 .srt
      · 音轨为静音 AAC（可用 --tone 换成低音量提示音），保证播放器链路完整

产物
    data/litetv-videos-local/
        README.md
        manifest.json                每集时长/体积/字幕，以及失败清单
        <media_id>/<media_id>_ep000.mp4
        <media_id>/<media_id>_ep000.srt   （该集有真实字幕时）

用法
    .venv/bin/python scripts/build_local_video_library.py preview --media-id 180
    .venv/bin/python scripts/build_local_video_library.py build --scope free
    .venv/bin/python scripts/build_local_video_library.py build --scope all --workers 8
    .venv/bin/python scripts/build_local_video_library.py verify
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime as dt
import json
import re
import shutil
import statistics
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "litetv-catalog-20261013"
CACHE = ROOT / "data" / ".cache" / "video_stills"
DEFAULT_OUT = ROOT / "data" / "litetv-videos-local"

FONT_REGULAR = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

WIDTH, HEIGHT, FPS = 540, 960, 12
CRF, PRESET = 33, "veryfast"
KEYINT = 24
POSTER_WIDTH_RATIO = 0.70


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def ffmpeg_bin() -> tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise SystemExit("需要 ffmpeg / ffprobe，请先安装")
    if not Path(FONT_BOLD).exists():
        raise SystemExit(f"缺少字体 {FONT_BOLD}")
    return ffmpeg, ffprobe


def escape_drawtext(text: str) -> str:
    """drawtext 的文本转义：反斜杠、冒号、单引号、百分号都要处理。"""
    return (text.replace("\\", "\\\\").replace(":", "\\:")
                .replace("'", "\u2019").replace("%", "\\%"))


def hms(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60:02d}\\:{seconds % 60:02d}"


def load_dramas() -> list[dict]:
    path = CATALOG / "dramas.json"
    if not path.exists():
        raise SystemExit(f"缺少 {path}，先跑 scripts/fetch_litetv_catalog.py all")
    return json.loads(path.read_text())["items"]


def poster_path(drama: dict) -> Path | None:
    rel = drama["covers"].get("poster_file")
    if rel and (CATALOG / rel).exists():
        return CATALOG / rel
    rel = drama["covers"].get("thumbnail_file")
    if rel and (CATALOG / rel).exists():
        return CATALOG / rel
    return None


def real_subtitle(drama: dict, episode: int) -> tuple[Path | None, str]:
    """按 <mid>_ep<N>_<lang>.srt 找回该集的真实字幕。"""
    mid = drama["media_id"]
    folder = CATALOG / "subtitles"
    if not folder.exists():
        return None, ""
    for path in sorted(folder.glob(f"{mid}_ep{episode}_*.srt")):
        lang = path.stem.rsplit("_", 1)[-1]
        return path, lang
    return None, ""


def episode_plan(drama: dict, scope: str) -> list[tuple[int, int, str]]:
    """返回 (集号, 时长秒, 时长来源)。"""
    items = drama["episodes"]["items"]
    free = [e for e in items if e["is_free"] and e["duration_sec"]]
    durations = [e["duration_sec"] for e in free]
    fallback = int(statistics.median(durations)) if durations else 90
    plan = []
    for ep in items:
        if ep["is_free"] and ep["duration_sec"]:
            plan.append((ep["episode_number"], int(ep["duration_sec"]), "site"))
        elif scope == "all":
            plan.append((ep["episode_number"], fallback, "synthesized"))
    if scope == "all":
        for ep in items:
            if not any(p[0] == ep["episode_number"] for p in plan):
                plan.append((ep["episode_number"], fallback, "synthesized"))
    return sorted(plan)


# --------------------------------------------------------------------------- #
# 合成静帧
# --------------------------------------------------------------------------- #
def build_still(ffmpeg: str, drama: dict, out: Path, force: bool = False) -> bool:
    if out.exists() and out.stat().st_size > 5000 and not force:
        return True
    poster = poster_path(drama)
    if not poster:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)

    fg_w = int(WIDTH * POSTER_WIDTH_RATIO)
    lines = textwrap.wrap(drama["title"], width=30)[:3] or [drama["title"][:30]]
    base_y = 690 if len(lines) == 2 else (652 if len(lines) == 3 else 700)

    chain = [
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},eq=brightness=-0.36:saturation=0.5[bg]",
        f"[0:v]scale={fg_w}:-2[fg]",
        f"[bg][fg]overlay=(W-w)/2:30[b0]",
    ]
    draw = [
        f"drawtext=fontfile='{FONT_BOLD}':text='{escape_drawtext(line)}':fontsize=29:"
        f"fontcolor=white:x=(w-text_w)/2:y={base_y + i * 38}:"
        f"box=1:boxcolor=black@0.5:boxborderw=13"
        for i, line in enumerate(lines)
    ]
    media_label = escape_drawtext(f"MEDIA {drama['media_id']}")
    draw.append(
        f"drawtext=fontfile='{FONT_REGULAR}':text='{media_label}':"
        f"fontsize=17:fontcolor=white@0.7:x=(w-text_w)/2:y=610:"
        f"box=1:boxcolor=black@0.45:boxborderw=9"
    )
    draw.append(
        f"drawtext=fontfile='{FONT_BOLD}':text='LOCAL SAMPLE - NOT LITETV VIDEO':"
        f"fontsize=16:fontcolor=0xFF7B72:x=(w-text_w)/2:y=912"
    )
    # 文字区先压暗，保证剧名和集号在任何封面上都读得清
    scrim = "drawbox=x=0:y=596:w=iw:h=364:color=black@0.72:t=fill"
    chain.append("[b0]" + ",".join([scrim] + draw) + "[v]")

    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(poster),
        "-filter_complex", ";".join(chain),
        "-map", "[v]", "-frames:v", "1", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and out.exists()


# --------------------------------------------------------------------------- #
# 合成单集视频
# --------------------------------------------------------------------------- #
def build_episode(ffmpeg: str, still: Path, out: Path, episode: int, duration: int,
                  subtitle: Path | None, tone: bool) -> tuple[bool, str]:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    total = hms(duration)
    vf = ",".join([
        f"drawtext=fontfile='{FONT_BOLD}':text='EPISODE {episode}':fontsize=25:fontcolor=0xFFD166:"
        f"x=(w-text_w)/2:y=782:box=1:boxcolor=black@0.5:boxborderw=11",
        f"drawtext=fontfile='{FONT_REGULAR}':text='%{{pts\\:hms}} / {total}':fontsize=20:"
        f"fontcolor=white@0.92:x=(w-text_w)/2:y=826:box=1:boxcolor=black@0.4:boxborderw=8",
    ])
    # 用 lavfi 的 movie 滤镜只解码一次静帧再循环：比 image2 的 -loop 1 少 4 倍 CPU
    # （-loop 1 会每帧重新解码 PNG）
    looped = f"movie={still},loop=loop=-1:size=1:start=0,setpts=N/({FPS}*TB)"
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", looped,
    ]
    if tone:
        cmd += ["-f", "lavfi", "-i",
                f"sine=frequency=440:sample_rate=48000:duration={duration}"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    if subtitle:
        cmd += ["-i", str(subtitle)]

    cmd += ["-vf", vf, "-map", "0:v", "-map", "1:a"]
    if subtitle:
        cmd += ["-map", "2:s", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng"]
    cmd += [
        "-t", str(duration),
        "-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF), "-tune", "stillimage",
        "-pix_fmt", "yuv420p", "-g", str(KEYINT), "-threads", "2",
        "-c:a", "aac", "-b:a", "32k", "-ac", "2",
        "-movflags", "+faststart", str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not tmp.exists():
        return False, (proc.stderr or "ffmpeg failed").strip().splitlines()[-1][:160] if proc.stderr else "ffmpeg failed"
    tmp.replace(out)
    return True, f"{duration}s {out.stat().st_size // 1024}KB"


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_preview(args) -> int:
    ffmpeg, _ = ffmpeg_bin()
    dramas = [d for d in load_dramas() if d["media_id"] == args.media_id]
    if not dramas:
        print(f"目录里没有 media_id={args.media_id}")
        return 1
    drama = dramas[0]
    out_dir = Path(args.out)
    still = CACHE / f"{drama['media_id']}.png"
    if not build_still(ffmpeg, drama, still, force=True):
        print("静帧合成失败")
        return 1
    plan = episode_plan(drama, "free")[:1]
    episode, duration, source = plan[0]
    sub, lang = real_subtitle(drama, episode)
    dest = out_dir / str(drama["media_id"]) / f"{drama['media_id']}_ep{episode:03d}.mp4"
    t0 = time.time()
    ok, detail = build_episode(ffmpeg, still, dest, episode, duration, sub, args.tone)
    print(f"{'OK ' if ok else 'FAIL'} {dest} {detail} ({time.time() - t0:.1f}s)"
          f"{f' 字幕={lang}' if sub else ' 无字幕'}")
    if ok:
        print(f"预览文件: {dest}")
    return 0 if ok else 1


def cmd_build(args) -> int:
    # 2026-10-13：lite.tv 正片已经可以真下载（缺的是 CDN 要求的同名 signature/expires 请求头，
    # 见 docs/litetv-video-download.md）。真片在 data/litetv-videos-real/，默认不再合成占位片，
    # 否则会覆盖/混淆真片（曾有并行会话把下载中的 .part.mp4 直接删掉）。
    real_manifest = ROOT / "data" / "litetv-videos-real" / "manifest.json"
    if real_manifest.exists() and not getattr(args, "force_synth", False):
        print(
            "拒绝合成：真片库已存在（data/litetv-videos-real/manifest.json）。\n"
            "· 继续下真片：.venv/bin/python scripts/fetch_litetv_videos.py download --episodes 0-12 --quality p480\n"
            "· 确实要合成占位片（仅兜底联调）：加 --force-synth，并用 --out 指到别的目录。",
            file=sys.stderr,
        )
        return 2
    ffmpeg, _ = ffmpeg_bin()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    dramas = load_dramas()
    if args.media_ids:
        wanted = {int(x) for x in args.media_ids.split(",")}
        dramas = [d for d in dramas if d["media_id"] in wanted]

    jobs: list[tuple[dict, int, int, str, Path | None, str]] = []
    for drama in dramas:
        for episode, duration, source in episode_plan(drama, args.scope):
            sub, lang = real_subtitle(drama, episode)
            jobs.append((drama, episode, duration, source, sub, lang))
    if args.limit:
        jobs = jobs[: args.limit]
    total_seconds = sum(j[2] for j in jobs)
    print(f"计划合成 {len(jobs)} 集，总时长 {total_seconds / 3600:.1f} 小时 → {out_dir}")

    # 先并行生成静帧（每部剧一张）
    stills: dict[int, Path] = {}
    for drama in {d["media_id"]: d for d in dramas}.values():
        still = CACHE / f"{drama['media_id']}.png"
        stills[drama["media_id"]] = still
    with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(lambda d: build_still(ffmpeg, d, stills[d["media_id"]], force=args.force), list({d["media_id"]: d for d in dramas}.values())))
    ready = sum(1 for p in stills.values() if p.exists())
    print(f"静帧就绪 {ready}/{len(stills)}")

    lock = threading.Lock()
    done = 0
    built = 0
    results = []

    def worker(job):
        nonlocal done, built
        drama, episode, duration, source, sub, lang = job
        mid = drama["media_id"]
        dest = out_dir / str(mid) / f"{mid}_ep{episode:03d}.mp4"
        if dest.exists() and dest.stat().st_size > 20_000 and not args.force:
            with lock:
                done += 1
            return (mid, episode, duration, source, True, "cached", dest.stat().st_size, lang)
        if args.max_new:
            # 分片执行：这一轮合成够 max_new 集就快速跳过其余任务，
            # 便于在被环境回收前反复续跑（已存在的文件下一轮会直接命中缓存）
            with lock:
                if built >= args.max_new:
                    return (mid, episode, duration, source, False, "deferred", 0, lang)
                built += 1
        still = stills.get(mid)
        if not still or not still.exists():
            return (mid, episode, duration, source, False, "no-poster", 0, lang)
        ok, detail = build_episode(ffmpeg, still, dest, episode, duration, sub, args.tone)
        if ok and sub:
            shutil.copyfile(sub, out_dir / str(mid) / f"{mid}_ep{episode:03d}.srt")
        with lock:
            done += 1
            if done % 50 == 0 or done == len(jobs):
                print(f"  进度 {done}/{len(jobs)}", flush=True)
        return (mid, episode, duration, source, ok, detail,
                dest.stat().st_size if ok and dest.exists() else 0, lang)

    t0 = time.time()
    with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for res in pool.map(worker, jobs):
            results.append(res)
    elapsed = time.time() - t0

    good = [r for r in results if r[4]]
    bad = [r for r in results if not r[4]]
    total_bytes = sum(r[6] for r in good)
    print(f"\n完成 {len(good)}/{len(results)} 集，{total_bytes / 1024 / 1024:.0f} MB，"
          f"用时 {elapsed / 60:.1f} 分钟")

    manifest_path = out_dir / "manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"items": []}
    merged = {f"{i['media_id']}_{i['episode_number']}": i for i in previous.get("items", [])}
    for r in good:
        mid, episode, duration, source, _, detail, size, lang = r
        merged[f"{mid}_{episode}"] = {
            "media_id": mid,
            "episode_number": episode,
            "duration_sec": duration,
            "duration_source": source,
            "file": f"{mid}/{mid}_ep{episode:03d}.mp4",
            "bytes": size,
            "subtitle_lang": lang or None,
        }
    items = sorted(merged.values(), key=lambda x: (x["media_id"], x["episode_number"]))
    manifest = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "scripts/build_local_video_library.py",
        "nature": "本地合成样例片：真实封面 + 剧名 + 集号 + 真实时长，不是 lite.tv 的正片",
        "layout": f"{WIDTH}x{HEIGHT} @{FPS}fps h264 crf{CRF} {PRESET} + AAC 静音轨",
        "source_catalog": str(CATALOG.relative_to(ROOT)),
        "stats": {
            "episodes": len(items),
            "dramas": len({i['media_id'] for i in items}),
            "bytes": sum(i["bytes"] for i in items),
            "duration_sec": sum(i["duration_sec"] for i in items),
            "with_real_subtitle": sum(1 for i in items if i["subtitle_lang"]),
            "duration_from_site": sum(1 for i in items if i["duration_source"] == "site"),
        },
        "items": items,
        "failures": [
            {"media_id": r[0], "episode_number": r[1], "reason": r[5]}
            for r in bad if r[5] != "deferred"
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    write_readme(out_dir, manifest, args)
    print(f"清单: {manifest_path}")
    return 0 if not bad else 1


def write_readme(out_dir: Path, manifest: dict, args) -> None:
    stats = manifest["stats"]
    readme = f"""# MiniTV 本地合成视频库

生成时间：{manifest['generated_at']}
生成脚本：[scripts/build_local_video_library.py](../../scripts/build_local_video_library.py)
目录数据来源：[../litetv-catalog-20261013](../litetv-catalog-20261013)（lite.tv 公开接口抓取）

## 1. 这是什么

**不是 lite.tv 的正片**，是按站点真实目录合成的占位片：
每集一个 9:16 竖屏 MP4，画面用这部剧的真实封面压暗做底，下面压一层遮罩，
叠上剧名、集号、逐帧走动的时间码和 `LOCAL SAMPLE - NOT LITETV VIDEO` 水印。
时长取站点真实时长（`--scope all` 时锁定集取该剧免费集的中位数），
音轨是静音 AAC，有真实英文字幕的集会把 SRT 作为软字幕轨封装进去。

为什么合成而不下站点的正片：lite.tv 的播放列表在 CDN 侧有客户端授权校验，
同一目录下的封面图能正常 200 下载，播放列表带签名仍然 401，
用真实 Chrome 在站点源站发起也一样（`scripts/fetch_litetv_videos.py probe` 可复现）。
当前出口拿不到流，所以先用合成片把本地开发链路打通。

## 2. 数量

| 项目 | 值 |
|---|---|
| 剧集 | {stats['dramas']} 部 |
| 视频 | {stats['episodes']} 集 |
| 总时长 | {stats['duration_sec'] / 3600:.1f} 小时 |
| 体积 | {stats['bytes'] / 1024 / 1024:.0f} MB |
| 带真实字幕 | {stats['with_real_subtitle']} 集 |
| 站点真实时长 | {stats['duration_from_site']} 集 |

规格：{manifest['layout']}，`-movflags +faststart`，可直接进 Bunny Stream 或本地 HLS 打包。

## 3. 目录结构

```
{out_dir.name}/
├── README.md
├── manifest.json          每集的时长、体积、字幕语言、时长来源
└── <media_id>/
    ├── <media_id>_ep000.mp4
    └── <media_id>_ep000.srt   该集有真实字幕时
```

`<media_id>` 与目录数据 `dramas.json` 的 `media_id` 一一对应，
可以用 `episodes.csv` 的 `episode_number` 直接拼出文件路径。

## 4. 用途

1. **上传与转码链路**：把任意几集传进开发用 Bunny Stream Library，验证上传会话、
   转码回调、HLS 输出和 `p480/p720` 地址签发。
2. **客户端联调**：本地起 API + 静态文件服务，播放页、选集页、进度条、字幕开关
   都能拿到真实可播放的流。
3. **离线演示**：不依赖外网也能演示发现、榜单、详情、播放整条链路。

## 5. 边界

- 画面是合成的，只能验证链路，不能拿来做设计稿的像素级对比；
  像素对比继续用 `assets/titled` 那套图。
- 封面主体仍来自站点的海报图，只适合本地开发联调，不要随产品或对外发布。
- 锁定集的时长是按该剧免费集中位数补的，manifest 里 `duration_source` 标了 `synthesized`。

## 6. 重新生成

```bash
.venv/bin/python scripts/build_local_video_library.py build --scope free   # 免费集（真实时长）
.venv/bin/python scripts/build_local_video_library.py build --scope all    # 补齐锁定集
.venv/bin/python scripts/build_local_video_library.py preview --media-id 180
.venv/bin/python scripts/build_local_video_library.py verify
```
"""
    (out_dir / "README.md").write_text(readme)


def cmd_verify(args) -> int:
    _, ffprobe = ffmpeg_bin()
    out_dir = Path(args.out)
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        print("还没有合成结果")
        return 1
    manifest = json.loads(manifest_path.read_text())
    items = manifest["items"]
    missing, bad = [], []
    checked = 0
    for item in items[: args.sample]:
        path = out_dir / item["file"]
        if not path.exists():
            missing.append(item["file"])
            continue
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-show_entries", "stream=codec_name,width,height", "-of", "json", str(path)],
            capture_output=True, text=True)
        try:
            info = json.loads(proc.stdout)
            streams = {s["codec_name"] for s in info["streams"]}
            duration = float(info["format"]["duration"])
        except Exception:  # noqa: BLE001
            bad.append(item["file"])
            continue
        if "h264" not in streams or abs(duration - item["duration_sec"]) > 1.5:
            bad.append(f"{item['file']} ({duration:.1f}s vs {item['duration_sec']}s)")
        checked += 1
    print(f"清单 {len(items)} 集，抽检 {checked} 集")
    print(f"总体积 {manifest['stats']['bytes'] / 1024 / 1024:.0f} MB，"
          f"总时长 {manifest['stats']['duration_sec'] / 3600:.1f} 小时")
    print(f"真实字幕 {manifest['stats']['with_real_subtitle']} 集，"
          f"站点真实时长 {manifest['stats']['duration_from_site']} 集")
    if missing:
        print(f"缺失 {len(missing)}: {missing[:5]}")
    if bad:
        print(f"异常 {len(bad)}: {bad[:5]}")
    if missing or bad:
        return 1
    print("[OK] 视频库抽检通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="合成 MiniTV 本地视频库")
    parser.add_argument("command", choices=["preview", "build", "verify"])
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--scope", choices=["free", "all"], default="free",
                        help="free=只合成免费集（真实时长）；all=补齐锁定集（时长取中位数）")
    parser.add_argument("--media-ids", help="只处理这些 media_id，逗号分隔")
    parser.add_argument("--media-id", type=int, default=180, help="preview 用")
    parser.add_argument("--limit", type=int, help="最多合成多少集")
    parser.add_argument("--max-new", type=int, default=0,
                        help="本轮最多新合成多少集，其余留到下一轮（配合缓存续跑）")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--sample", type=int, default=40, help="verify 抽检集数")
    parser.add_argument("--tone", action="store_true", help="用低音量提示音代替静音")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-synth", action="store_true",
                        help="真片已存在时仍强制合成占位片（仅兜底联调用）")
    args = parser.parse_args()
    if args.command == "preview":
        return cmd_preview(args)
    if args.command == "build":
        return cmd_build(args)
    return cmd_verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
