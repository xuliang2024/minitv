#!/usr/bin/env python3
"""生成「剧名 → 封面 → 视频」对照索引。

数据源（全部是已落地的真实数据，不联网）
    data/litetv-catalog-20261013/dramas.json      剧集主记录（标题/简介/封面/分类/集数）
    data/litetv-catalog-20261013/covers/          封面文件
    data/litetv-videos-real/manifest.json         已下载的真片清单
    data/litetv-videos-real/availability.json      每部剧访客可播的集号范围

产物
    data/litetv-catalog-20261013/index-title-cover-video.csv   一行一部剧
    data/litetv-catalog-20261013/index-episode-video.csv       一行一集（有视频的集）
    docs/_generated/title-cover-video-table.md                 给指南用的 Markdown 表格

用法
    .venv/bin/python scripts/build_data_index.py
    .venv/bin/python scripts/build_data_index.py --no-probe   # 跳过时长探测，快很多
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "litetv-catalog-20261013"
VIDEOS = ROOT / "data" / "litetv-videos-real"
DOCS_GEN = ROOT / "docs" / "_generated"


def ffprobe_meta(path: Path) -> tuple[float, str]:
    """返回 (时长秒, 分辨率字符串)；失败返回 (0, '')。"""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-show_entries", "stream=width,height", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30)
        info = json.loads(proc.stdout)
        duration = float(info["format"]["duration"])
        video = next(s for s in info["streams"] if s.get("width"))
        return duration, f"{video['width']}x{video['height']}"
    except Exception:  # noqa: BLE001
        return 0.0, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="生成剧名/封面/视频对照索引")
    parser.add_argument("--no-probe", action="store_true", help="不探测视频时长与分辨率")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    dramas = json.loads((CATALOG / "dramas.json").read_text())["items"]
    manifest = json.loads((VIDEOS / "manifest.json").read_text())
    availability = {i["media_id"]: i for i in json.loads((VIDEOS / "availability.json").read_text())["items"]}

    videos_by_drama: dict[int, list[dict]] = {}
    for item in manifest["items"]:
        videos_by_drama.setdefault(item["media_id"], []).append(item)

    # 逐集补齐时长与分辨率
    meta: dict[str, tuple[float, str]] = {}
    if not args.no_probe:
        rows = [(f"{i['media_id']}/{i['file']}", VIDEOS / i["file"]) for i in manifest["items"]]
        print(f"探测 {len(rows)} 个视频的时长/分辨率 ...")
        with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for key, value in zip([r[0] for r in rows], pool.map(lambda r: ffprobe_meta(r[1]), rows)):
                meta[key] = value

    # ---- 剧集级索引 ----
    drama_rows = []
    for drama in sorted(dramas, key=lambda d: d["media_id"]):
        mid = drama["media_id"]
        vids = sorted(videos_by_drama.get(mid, []), key=lambda v: v["episode_number"])
        free = availability.get(mid, {}).get("free_episodes") or [v["episode_number"] for v in vids]
        poster = drama["covers"].get("poster_file") or ""
        thumb = drama["covers"].get("thumbnail_file") or ""
        drama_rows.append({
            "media_id": mid,
            "title": drama["title"],
            "main_category": drama["classification"].get("main_category") or "",
            "sub_categories": "|".join(drama["classification"].get("sub_categories") or []),
            "poster_file": f"data/litetv-catalog-20261013/{poster}" if poster else "",
            "thumbnail_file": f"data/litetv-catalog-20261013/{thumb}" if thumb else "",
            "video_dir": f"data/litetv-videos-real/{mid}/" if vids else "",
            "video_episode_count": len(vids),
            "video_first_file": vids[0]["file"] if vids else "",
            "video_last_file": vids[-1]["file"] if vids else "",
            "free_episode_min": min(free) if free else "",
            "free_episode_max": max(free) if free else "",
            "catalog_total_episodes": drama["episodes"]["total_episodes"],
            "catalog_published_episodes": drama["episodes"]["published_episodes"],
            "site_url": drama["site_url"],
            "description": drama["description"],
        })

    drama_csv = CATALOG / "index-title-cover-video.csv"
    with drama_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(drama_rows[0].keys()))
        writer.writeheader()
        writer.writerows(drama_rows)
    print(f"写出 {drama_csv.relative_to(ROOT)} （{len(drama_rows)} 行）")

    # ---- 分集级索引 ----
    episode_rows = []
    for item in sorted(manifest["items"], key=lambda i: (i["media_id"], i["episode_number"])):
        key = f"{item['media_id']}/{item['file']}"
        duration, resolution = meta.get(key, (0.0, ""))
        episode_rows.append({
            "media_id": item["media_id"],
            "title": item.get("title") or "",
            "episode_number": item["episode_number"],
            "video_file": f"data/litetv-videos-real/{item['file']}",
            "bytes": item["bytes"],
            "duration_sec": f"{duration:.2f}" if duration else "",
            "resolution": resolution,
            "poster_file": next(
                (r["poster_file"] for r in drama_rows if r["media_id"] == item["media_id"]), ""),
        })
    episode_csv = CATALOG / "index-episode-video.csv"
    with episode_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(episode_rows[0].keys()))
        writer.writeheader()
        writer.writerows(episode_rows)
    print(f"写出 {episode_csv.relative_to(ROOT)} （{len(episode_rows)} 行）")

    # ---- 给指南用的 Markdown 表格 ----
    DOCS_GEN.mkdir(parents=True, exist_ok=True)
    lines = [
        "| media_id | 剧名 | 主分类 | 封面 | 视频目录 | 有视频集数 | 免费集范围 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in drama_rows:
        poster_name = Path(row["poster_file"]).name if row["poster_file"] else "—"
        rng = (f'{row["free_episode_min"]}–{row["free_episode_max"]}'
               if row["free_episode_min"] != "" else "—")
        lines.append(
            f'| {row["media_id"]} | {row["title"]} | {row["main_category"]} | '
            f'`{poster_name}` | `{row["media_id"]}/` | {row["video_episode_count"]} | {rng} |')
    table_path = DOCS_GEN / "title-cover-video-table.md"
    table_path.write_text("\n".join(lines) + "\n")
    print(f"写出 {table_path.relative_to(ROOT)} （{len(drama_rows)} 行表格）")

    total_bytes = sum(i["bytes"] for i in manifest["items"])
    total_duration = sum(v[0] for v in meta.values()) if meta else 0
    print(f"\n合计：{len(drama_rows)} 部剧 / {len(episode_rows)} 集视频 / "
          f"{total_bytes / 1024 ** 3:.1f} GB"
          + (f" / {total_duration / 3600:.1f} 小时" if total_duration else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
