#!/usr/bin/env python3
"""把 lite.tv 的免费集正片下载到本地（HLS → MP4）。

前置说明
    lite.tv 的正片走 HLS，地址由 `GET /v1/media_episode/play` 下发：

        {main_domain}{p480}?signature={signature}&expires={expires}

    只有免费集能拿到地址，锁定集返回 `{"code":50003,"error":"Please unlock in order"}`。
    本脚本只下载免费集，不尝试任何绕过付费墙的做法。

    **关键点（2026-10-13 实测）**：CDN 校验的签名不只在 query string 里，播放器还会把
    `signature` / `expires` 作为同名 HTTP 请求头发出去。少了这两个头，即便签名正确、
    未过期，播放列表和分片也一律返回 401 Authorization Required。
    本脚本的 probe / download 都带上了这两个头。

用法
    .venv/bin/python scripts/fetch_litetv_videos.py probe
    .venv/bin/python scripts/fetch_litetv_videos.py download --per-drama 1
    .venv/bin/python scripts/fetch_litetv_videos.py download --free-episodes --quality p720
    .venv/bin/python scripts/fetch_litetv_videos.py download --media-ids 180 --quality p720
    .venv/bin/python scripts/fetch_litetv_videos.py verify
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime as dt
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_litetv_catalog import API_ROOT, SITE_ROOT, LiteTVClient, UA  # noqa: E402

CATALOG = ROOT / "data" / "litetv-catalog-20261013"
# 注意：data/litetv-videos-local 目前由并行的合成脚本（build_local_video_library.py）占用，
# 真片固定写到这里，避免两个进程互相覆盖。
DEFAULT_OUT = ROOT / "data" / "litetv-videos-real"
CACHE = ROOT / "data" / ".cache"
QUALITY = {"p480": "video_url_hls_480", "p720": "video_url_hls_720"}
# 单集下载硬超时：正常一集 10–30 秒，卡住的连接必须在 15 分钟内被切断
DOWNLOAD_TIMEOUT = 900


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def load_catalog() -> list[dict]:
    path = CATALOG / "dramas.json"
    if not path.exists():
        raise SystemExit(f"缺少 {path}，先跑 scripts/fetch_litetv_catalog.py")
    return json.loads(path.read_text())["items"]


def play_once(client: LiteTVClient, media_id: int, episode: int) -> dict:
    """查一集的播放信息：锁定集（50003）**只请求一次**，不做重试退避。

    `LiteTVClient.call()` 对非 10000 的响应会退避重试 5 次（约 6 秒），
    对「锁定集」这种确定性结果纯属浪费——批量跑 100 部剧时会白等几十分钟。
    成功的响应照旧写进 data/.cache/play/，可被后续复用。
    """
    fp = CACHE / "play" / f"{media_id}_{episode}.json"
    if fp.exists() and fp.stat().st_size > 20:
        try:
            cached = json.loads(fp.read_text())
        except json.JSONDecodeError:
            cached = None
        if isinstance(cached, dict) and cached.get("code") == 10000:
            return cached
    resp = client._raw("GET", "/v1/media_episode/play",
                       params={"media_id": media_id, "episode_number": episode})
    if resp.get("code") in (80005, 80001):  # 访客凭证过期：重登一次再试
        client.login()
        resp = client._raw("GET", "/v1/media_episode/play",
                           params={"media_id": media_id, "episode_number": episode})
    if resp.get("code") == 10000:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(resp, ensure_ascii=False))
    return resp


def stream_url(client: LiteTVClient, media_id: int, episode: int, quality: str) -> tuple[dict | None, str]:
    """取某一集的签名播放地址；锁定的集返回 (None, 原因)。

    返回值里必须同时带上 signature / expires：这两者不只是 query 参数，
    CDN 还要求把它们作为同名请求头发送（见 cdn_headers 的说明）。
    """
    resp = play_once(client, media_id, episode)
    code = resp.get("code")
    if code == 50003:
        return None, "locked"
    data = resp.get("data") or {}
    episode_data = data.get("cur_episode")
    if not isinstance(episode_data, dict):
        return None, f"no-episode({code})"
    path = episode_data.get(quality) or ""
    if not path:
        return None, f"no-{quality}"
    sig = episode_data.get("signature") or ""
    exp = episode_data.get("expires") or ""
    url = f"{episode_data['main_domain']}{path}"
    if sig:
        url += f"?signature={sig}&expires={exp}"
    return {"url": url, "signature": sig, "expires": str(exp)}, "ok"


def cdn_headers(sig: str, exp: str, referer: str | None = None) -> dict[str, str]:
    """CDN 真正校验的是请求头，不是 query。

    实测（2026-10-13）：同一个签名地址
      · 只带 ?signature=...&expires=... → HTTP 401 Authorization Required
      · 额外带 `signature:` / `expires:` 两个同名请求头 → HTTP 200
    浏览器端由播放器（xgplayer）加这两个头；缺了它们，播放列表和分片一律 401。
    """
    headers = {
        "User-Agent": UA,
        "Referer": referer or SITE_ROOT + "/",
        "Accept": "*/*",
    }
    if sig:
        headers["signature"] = sig
    if exp:
        headers["expires"] = str(exp)
    return headers


def ffmpeg_header_block(headers: dict[str, str]) -> str:
    """ffmpeg -headers 需要 CRLF 分隔的多行字符串。"""
    return "".join(f"{k}: {v}\r\n" for k, v in headers.items())


def http_status(stream: dict, timeout: int = 20) -> tuple[int | None, str]:
    req = urllib.request.Request(
        stream["url"],
        headers=cdn_headers(stream["signature"], stream["expires"]),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


# --------------------------------------------------------------------------- #
# probe：确认当前网络能不能拉流
# --------------------------------------------------------------------------- #
def cmd_probe(client: LiteTVClient, args) -> int:
    dramas = load_catalog()[: args.samples]
    print(f"用 {len(dramas)} 部剧的第 {args.episode} 集探测 {args.quality} 播放列表\n")
    ok = 0
    for drama in dramas:
        mid = drama["media_id"]
        stream, reason = stream_url(client, mid, args.episode, args.quality)
        if not stream:
            print(f"  {mid:>4} {drama['title'][:38]:<40} 拿不到地址: {reason}")
            continue
        status, _ = http_status(stream)
        verdict = "可下载" if status in (200, 206) else "被拒绝"
        print(f"  {mid:>4} {drama['title'][:38]:<40} HTTP {status} {verdict}")
        if status in (200, 206):
            ok += 1
    print(f"\n结论: {ok}/{len(dramas)} 部可拉流")
    if ok == 0:
        print("\n当前网络拿不到流：看上面每行给出的原因（locked / no-episode / HTTP 401）。")
        return 1
    return 0


# --------------------------------------------------------------------------- #
# scan：扫出每部剧访客真实可播的集（目录里的 is_free 字段不可信）
# --------------------------------------------------------------------------- #
def cmd_scan(client: LiteTVClient, args) -> int:
    dramas = load_catalog()
    if args.media_ids:
        wanted = {int(x) for x in args.media_ids.split(",")}
        dramas = [d for d in dramas if d["media_id"] in wanted]
    rng = parse_episode_range(args.episodes) or (0, 79)
    print(f"扫描 {len(dramas)} 部剧：从第 {rng[0]} 集起逐集试，遇到锁定即停"
          f"（上限第 {rng[1]} 集，{args.workers} 线程）")

    def worker(drama):
        mid = drama["media_id"]
        free, odd = [], []
        ep = rng[0]
        while ep <= rng[1]:
            stream, why = stream_url(client, mid, ep, args.quality)
            if stream:
                free.append(ep)
                ep += 1
                continue
            if why == "locked":
                break          # 免费集是连续前缀，遇到锁定即可停
            odd.append({"episode_number": ep, "reason": why})
            ep += 1
        return {"media_id": mid, "title": drama["title"],
                "free_episodes": free, "locked_from": (max(free) + 1) if free else rng[0],
                "anomalies": odd}

    items = []
    with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(worker, dramas):
            items.append(row)
            print(f"  {row['media_id']:>4} 免费 {len(row['free_episodes'])} 集 "
                  f"({row['free_episodes'][0] if row['free_episodes'] else '-'}"
                  f"–{row['free_episodes'][-1] if row['free_episodes'] else '-'})"
                  f" 锁定自 {row['locked_from']} 起  {row['title'][:30]}", flush=True)

    # 子集扫描（--media-ids）时与旧清单元数据合并，避免把整份可用性报告冲掉
    fp = args.out / "availability.json"
    previous: dict[int, dict] = {}
    if args.media_ids and fp.exists():
        try:
            for row in json.loads(fp.read_text()).get("items", []):
                previous[row["media_id"]] = row
        except json.JSONDecodeError:
            previous = {}
    merged = {**previous, **{row["media_id"]: row for row in items}}
    all_items = [merged[mid] for mid in sorted(merged)]

    counts = [len(r["free_episodes"]) for r in all_items]
    out = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": f"{API_ROOT}/v1/media_episode/play",
        "quality_probed": args.quality,
        "scanned_range": list(rng),
        "scanned_this_run": [row["media_id"] for row in items],
        "note": "free_episodes 是访客实际能拿到播放地址的集；50003 = 站点侧锁定（金币/会员）",
        "drama_count": len(all_items),
        "total_free_episodes": sum(counts),
        "counts": {
            "min": min(counts) if counts else 0,
            "max": max(counts) if counts else 0,
            "avg": round(sum(counts) / len(counts), 2) if counts else 0,
        },
        "items": all_items,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n本轮扫描 {len(items)} 部；清单合计 {out['drama_count']} 部剧、"
          f"{out['total_free_episodes']} 集可播"
          f"（每部 {out['counts']['min']}–{out['counts']['max']} 集，均值 {out['counts']['avg']}）")
    print(f"清单: {fp}")
    return 0


# --------------------------------------------------------------------------- #
# download：拉 HLS 并封装成 MP4
# --------------------------------------------------------------------------- #
def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def download_one(stream: dict, dest: Path) -> tuple[bool, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.mp4")
    headers = cdn_headers(stream["signature"], stream["expires"])
    headers["Accept"] = "*/*"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        # 这两个头必须带上，否则 CDN 对播放列表和分片一律 401
        "-user_agent", UA,
        "-headers", ffmpeg_header_block(headers),
        # 断流/半开连接保护：默认 ffmpeg 会无限期挂在读上（实测卡死过一整集，59 分钟无进展），
        # 这里给读写设 30s 超时并允许重连，卡住的集会失败退出、由下一轮续跑补上。
        "-rw_timeout", "30000000",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-i", stream["url"],
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        str(tmp),
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)   # 独立进程组，便于超时精确清理
        try:
            _, stderr = proc.communicate(timeout=DOWNLOAD_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
            tmp.unlink(missing_ok=True)
            return False, f"下载超时（>{DOWNLOAD_TIMEOUT // 60} 分钟），已中断"
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return False, f"ffmpeg 启动失败: {exc}"
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        return False, (stderr or "ffmpeg 失败").strip()[:200]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size",
         "-of", "json", str(tmp)],
        capture_output=True, text=True,
    )
    try:
        info = json.loads(probe.stdout)["format"]
    except Exception:  # noqa: BLE001
        info = {}
    tmp.replace(dest)
    return True, f"{info.get('duration', '?')}s {int(info.get('size', 0)) // 1024}KB"


def parse_episode_range(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    if "-" in text:
        lo, hi = text.split("-", 1)
        return int(lo), int(hi)
    return int(text), int(text)


def cmd_download(client: LiteTVClient, args) -> int:
    if not ffmpeg_available():
        raise SystemExit("需要 ffmpeg / ffprobe，请先安装")
    out_dir: Path = args.out
    dramas = load_catalog()
    if args.media_ids:
        wanted = {int(x) for x in args.media_ids.split(",")}
        dramas = [d for d in dramas if d["media_id"] in wanted]
    if not dramas:
        print("没有匹配的剧")
        return 1

    rng = parse_episode_range(args.episodes)
    jobs: list[tuple[dict, int]] = []
    if args.from_scan:
        # 用 scan 的可用性清单精确建任务：只下访客真能播的集，不浪费锁定集的探测
        fp = out_dir / "availability.json"
        if not fp.exists():
            raise SystemExit(f"缺少 {fp}，先跑 `scan` 子命令")
        avail = json.loads(fp.read_text())
        by_mid = {d["media_id"]: d for d in dramas}
        for row in avail["items"]:
            drama = by_mid.get(row["media_id"])
            if not drama:
                continue
            for ep in row["free_episodes"]:
                jobs.append((drama, ep))
        print(f"按 {fp.name} 建任务：{len(avail['items'])} 部剧、可播 "
              f"{avail['total_free_episodes']} 集")
    else:
        for drama in dramas:
            eps = sorted({e["episode_number"] for e in drama["episodes"]["items"]})
            if rng:
                eps = [e for e in eps if rng[0] <= e <= rng[1]]
            elif not args.free_episodes:
                # 站点不再按 is_free 标记免费集，改成“按顺序试”，锁定的集会被跳过并记进 failures
                eps = eps[: args.per_drama]
            jobs.extend((drama, e) for e in eps)
    if not jobs:
        print("没有可下的集")
        return 1
    print(f"待尝试 {len(jobs)} 集（锁定的集会被跳过并记录）→ {out_dir}")

    # 先探一集，拿到 401 之类的问题就别空跑
    head = stream_url(client, jobs[0][0]["media_id"], jobs[0][1], args.quality)
    probe, reason = head
    if not probe:
        print(f"第一集取不到播放地址: {reason}")
        return 1
    status, _ = http_status(probe)
    if status not in (200, 206):
        print(f"第一集播放列表返回 HTTP {status}（预期 200）：检查 signature/expires 请求头是否带上")
        return 1

    lock = threading.Lock()
    results: list[dict] = []
    done = 0
    # 同一部剧里多集指向同一个 HLS 文件的情况很常见（例如 mid=14/15/18 整部剧共用一个文件）。
    # 第一份照常下载，后面的集直接硬链接过去：每集路径都在，但不重复占盘、不重复拉流。
    source_map: dict[str, Path] = {}

    def worker(job):
        nonlocal done
        drama, episode = job
        mid = drama["media_id"]
        dest = out_dir / str(mid) / f"{mid}_ep{episode:03d}.mp4"
        record = {"media_id": mid, "episode_number": episode,
                  "file": f"{mid}/{mid}_ep{episode:03d}.mp4", "title": drama["title"]}
        if dest.exists() and dest.stat().st_size > 10_000 and not args.force:
            record.update(ok=True, reason="exists", bytes=dest.stat().st_size)
            return record
        stream, why = stream_url(client, mid, episode, args.quality)
        if not stream:
            record.update(ok=False, reason=why, bytes=0)
            return record

        key = stream["url"].split("?", 1)[0]
        with lock:
            twin = source_map.get(key)
            if twin is None:
                source_map[key] = dest          # 占位，避免并发重复下载同一文件
            elif twin.exists() and twin.stat().st_size > 10_000:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.unlink(missing_ok=True)
                try:
                    os.link(twin, dest)
                    record.update(ok=True, reason=f"dedup→{twin.parent.name}/{twin.name}",
                                  bytes=dest.stat().st_size)
                    done += 1
                    print(f"  [{done}/{len(jobs)}] link {mid} ep{episode:03d} → "
                          f"{twin.parent.name}/{twin.name}", flush=True)
                    return record
                except OSError as exc:  # 跨卷等无法硬链接时退回下载
                    record["reason"] = f"link failed: {exc}"

        ok, detail = download_one(stream, dest)
        if ok and record.get("reason", "").startswith("link failed"):
            detail = f"{detail}（link 失败已改为实下）"
        record.update(ok=ok, reason=detail, bytes=dest.stat().st_size if ok and dest.exists() else 0)
        with lock:
            done += 1
            flag = "ok " if ok else "FAIL"
            print(f"  [{done}/{len(jobs)}] {flag} {mid} ep{episode:03d} {detail}", flush=True)
        return record

    with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for record in pool.map(worker, jobs):
            results.append(record)

    good = [r for r in results if r["ok"]]
    total = sum(r["bytes"] for r in good)
    locked = [r for r in results if not r["ok"] and r["reason"] == "locked"]
    failed = [r for r in results if not r["ok"] and r["reason"] != "locked"]
    print(f"\n完成 {len(good)}/{len(results)} 集，共 {total / 1024 / 1024:.1f} MB"
          f"（锁定 {len(locked)} 集，失败 {len(failed)} 集）")

    manifest = {
        "source": f"{API_ROOT}/v1/media_episode/play + CDN({QUALITY[args.quality]})",
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "quality": args.quality,
        "kind": "real-download",
        "note": "站点免费范围内可播放的正片；锁定集在站点侧需要金币或会员解锁，未下载",
        "cdn_requirement": "播放列表与分片请求必须带同名 signature / expires 请求头，否则 401",
        "count": len(good),
        "bytes": total,
        "items": [
            {"media_id": r["media_id"], "episode_number": r["episode_number"],
             "file": r["file"], "bytes": r["bytes"], "title": r["title"]}
            for r in good
        ],
        "locked": [
            {"media_id": r["media_id"], "episode_number": r["episode_number"],
             "title": r["title"]} for r in locked
        ],
        "failures": [
            {"media_id": r["media_id"], "episode_number": r["episode_number"],
             "reason": r["reason"]}
            for r in failed
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"清单: {out_dir / 'manifest.json'}")
    return 0 if good else 1


def cmd_dedupe(args) -> int:
    """把内容完全相同的集改成硬链接（每集路径保留，只占一份盘）。

    站点上不少剧把同一段视频挂到多个集号上（实测 mid=14/15/18 整部剧共用一个文件），
    下载阶段已按 HLS 路径去重；这里再用内容哈希兜一遍（跨剧重复、并发竞态都能收掉）。
    """
    out_dir: Path = args.out
    files = sorted(p for p in out_dir.glob("*/*.mp4") if p.is_file())
    if not files:
        print("没有可去重的文件")
        return 1
    print(f"计算 {len(files)} 个文件的哈希…")
    by_hash: dict[str, list[Path]] = {}
    inode_hash: dict[tuple[int, int], str] = {}   # 已是硬链接的直接复用哈希，不重复读盘
    for path in files:
        st = path.stat()
        key = (st.st_dev, st.st_ino)
        digest = inode_hash.get(key)
        if digest is None:
            h = hashlib.md5()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            digest = h.hexdigest()
            inode_hash[key] = digest
        by_hash.setdefault(digest, []).append(path)

    groups = {h: ps for h, ps in by_hash.items() if len(ps) > 1}
    saved = 0
    for h, paths in groups.items():
        keeper = paths[0]
        for dup in paths[1:]:
            if dup.stat().st_ino == keeper.stat().st_ino:
                continue
            size = dup.stat().st_size
            tmp = dup.with_suffix(".dedupe_tmp")
            dup.unlink()
            try:
                os.link(keeper, dup)
                saved += size
            except OSError as exc:
                print(f"  [warn] {dup} 硬链接失败（{exc}），保持独立文件")
                shutil.copy2(keeper, dup)
                continue
            finally:
                tmp.unlink(missing_ok=True)

    report = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": len(files),
        "unique_contents": len(by_hash),
        "duplicate_files": sum(len(ps) - 1 for ps in groups.values()),
        "duplicate_groups": len(groups),
        "bytes_saved": saved,
        "groups": [
            {"md5": h, "count": len(ps), "files": [str(p.relative_to(out_dir)) for p in ps]}
            for h, ps in sorted(groups.items(), key=lambda kv: -len(kv[1]))
        ],
    }
    (out_dir / "dedupe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n{len(files)} 个文件 = {len(by_hash)} 份不同内容；"
          f"{report['duplicate_files']} 个重复文件改为硬链接，省下 {saved / 1024 / 1024:.1f} MB")
    print(f"报告: {out_dir / 'dedupe.json'}")
    return 0


def cmd_verify(args) -> int:
    out_dir: Path = args.out
    manifest = out_dir / "manifest.json"
    if not manifest.exists():
        print("还没有下载结果")
        return 1
    data = json.loads(manifest.read_text())
    bad = []
    for item in data["items"]:
        fp = out_dir / item["file"]
        if not fp.exists() or fp.stat().st_size < 10_000:
            bad.append(item["file"])
    print(f"清单 {data['count']} 集，缺失 {len(bad)} 集")
    print(f"总体积 {data['bytes'] / 1024 / 1024:.1f} MB")
    if bad:
        print("缺失示例:", bad[:5])
        return 1
    print("[OK] 视频文件齐全")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 lite.tv 免费集正片")
    parser.add_argument("command", choices=["probe", "scan", "download", "dedupe", "verify"])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--quality", choices=["p480", "p720"], default="p720")
    parser.add_argument("--per-drama", type=int, default=1, help="每部剧按顺序试前几集")
    parser.add_argument("--free-episodes", action="store_true",
                        help="每部剧按顺序试完所有集，锁定的集跳过并记入 manifest.locked")
    parser.add_argument("--episodes", help="只要这个集号区间，如 0-48（scan 时是扫描上限）")
    parser.add_argument("--from-scan", action="store_true",
                        help="按 out 目录里的 availability.json 精确下载可播集（推荐）")
    parser.add_argument("--media-ids", help="只下载这些 media_id，逗号分隔")
    parser.add_argument("--workers", type=int, default=4, help="download 并发数 / scan 线程数")
    parser.add_argument("--samples", type=int, default=10, help="probe 抽样的剧数")
    parser.add_argument("--episode", type=int, default=0, help="probe 抽查第几集")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.command in ("verify", "dedupe"):
        return cmd_verify(args) if args.command == "verify" else cmd_dedupe(args)

    client = LiteTVClient(CACHE)
    client.login()
    if args.command == "probe":
        return cmd_probe(client, args)
    if args.command == "scan":
        return cmd_scan(client, args)
    return cmd_download(client, args)


if __name__ == "__main__":
    raise SystemExit(main())
