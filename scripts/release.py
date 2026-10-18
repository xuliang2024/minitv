#!/usr/bin/env python3
"""LiteTV 统一发布工具（多 Agent 安全，一个版本一个版本上线）。

规则与流程见 docs/release-process.md；Agent 强制纪律见 AGENTS.md。

子命令:
  status    查看发布锁 / 最近发布 / 线上当前版本
  r2        发布静态前端到 Cloudflare R2（完整版本目录 + 根目录切换）
  run       在版本号 + 锁保护下执行任意上线命令（服务器部署等）
  rollback  把某个已发布的 R2 版本重新提升为根目录（不新增版本号）

核心保证:
  - 同一时刻全区只有一个发布（flock 于 git 公共目录，跨 worktree 生效）；
  - 每次发布自动分配 vYYYY.MM.DD.N 版本号并打 git tag；
  - 发布记录落盘 deployments/releases/ 并随 git 提交（成功/失败都留痕）；
  - 失败自动删 tag、写 failed 记录；已分配的版本号不复用。
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

REPO = Path(__file__).resolve().parents[1]
RECORDS_DIR = REPO / "deployments" / "releases"
CF_API = "https://api.cloudflare.com/client/v4"
DEFAULT_BUCKET = "minitv-web"
TZ_NAME = "Asia/Shanghai"
LOCK_NAME = "minitv-release.lock"
UPLOAD_WORKERS = 6
VERIFY_UA = "lite-tv-release/1.0"
VERSION_RE = re.compile(r"^v(\d{4})\.(\d{2})\.(\d{2})\.(\d+)$")


class ReleaseError(Exception):
    """可直接展示给用户的发布错误。"""


class LockBusy(Exception):
    def __init__(self, holder: dict):
        super().__init__("release lock is busy")
        self.holder = holder


# ---------------------------------------------------------------- 基础工具

def load_dotenv() -> None:
    path = REPO / ".env"
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def now() -> datetime:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(TZ_NAME))
        except Exception:
            pass
    return datetime.now(timezone(timedelta(hours=8)))


def fmt_time(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} 失败: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def git_common_dir() -> Path:
    out = git("rev-parse", "--git-common-dir")
    path = Path(out)
    if not path.is_absolute():
        path = (REPO / path).resolve()
    return path


def resolve_sha(rev: str) -> str:
    return git("rev-parse", f"{rev}^{{commit}}")


def operator_info() -> dict:
    return {
        "user": os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown",
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "agent_session": os.environ.get("CODEX_SESSION_ID"),
        "cwd": str(Path.cwd()),
    }


def fmt_operator(session: str | None) -> str:
    user = os.environ.get("USER") or "unknown"
    host = socket.gethostname()
    if session:
        return f"{user}@{host} (codex session {session[:8]}…)"
    return f"{user}@{host}"


# ---------------------------------------------------------------- 发布锁

class ReleaseLock:
    """flock 独占锁；进程退出（含崩溃）自动释放，不会留下死锁。"""

    def __init__(self, path: Path, wait_seconds: int):
        self.path = path
        self.wait = wait_seconds
        self.fd: int | None = None

    def _read_meta_file(self) -> dict:
        try:
            raw = self.path.read_text()
            return json.loads(raw) if raw.strip() else {}
        except Exception:
            return {}

    def _write_meta(self, meta: dict) -> None:
        assert self.fd is not None
        payload = json.dumps(meta, ensure_ascii=False, indent=2).encode()
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.ftruncate(self.fd, 0)
        os.write(self.fd, payload)
        os.fsync(self.fd)

    def acquire(self, meta: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.time() + max(0, self.wait)
        announced = False
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    holder = self._read_meta_file()
                    os.close(fd)
                    raise LockBusy(holder)
                if not announced:
                    announced = True
                    holder = self._read_meta_file()
                    who = fmt_operator((holder or {}).get("agent_session"))
                    target = (holder or {}).get("target") or "?"
                    started = (holder or {}).get("started_at") or "?"
                    print(f"⏳ 已有发布在进行（{who}，目标 {target}，开始于 {started}），等待最多 {self.wait}s …")
                time.sleep(2)
        self.fd = fd
        self._write_meta(meta)

    def update_meta(self, meta: dict) -> None:
        if self.fd is not None:
            self._write_meta(meta)

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            self._write_meta({})
        except OSError:
            pass
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None


def lock_status() -> dict:
    path = git_common_dir() / LOCK_NAME
    info: dict = {"held": False, "meta": None}
    if not path.exists():
        return info
    fd = os.open(path, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            info["held"] = True
        raw = os.read(fd, 65536).decode(errors="replace")
        if raw.strip():
            try:
                info["meta"] = json.loads(raw)
            except ValueError:
                info["meta"] = {"raw": raw}
    finally:
        os.close(fd)
    return info


def describe_lock(info: dict) -> str:
    if info.get("held"):
        meta = info.get("meta") or {}
        who = fmt_operator(meta.get("agent_session"))
        return (
            f"占用中：{who}（pid {meta.get('pid', '?')}，目标 {meta.get('target', '?')}，"
            f"版本 {meta.get('version') or '分配中'}，开始 {meta.get('started_at', '?')}）"
        )
    return "空闲"


# ---------------------------------------------------------------- 版本号

def next_version() -> str:
    today = now().strftime("%Y.%m.%d")
    prefix = f"v{today}."
    max_n = 0
    for line in git("tag", "-l", f"{prefix}*").splitlines():
        m = VERSION_RE.match(line.strip())
        if m and f"{m.group(1)}.{m.group(2)}.{m.group(3)}" == today:
            max_n = max(max_n, int(m.group(4)))
    if RECORDS_DIR.exists():
        for f in RECORDS_DIR.glob(f"{prefix}*.json"):
            m = VERSION_RE.match(f.stem)
            if m:
                max_n = max(max_n, int(m.group(4)))
    return f"{prefix}{max_n + 1}"


def create_tag(version: str, sha: str, target: str) -> None:
    if git("tag", "-l", version):
        raise ReleaseError(f"tag {version} 已存在（不应发生，请人工检查）")
    git("tag", "-a", version, "-m", f"LiteTV {target} release {version}", sha)


def delete_tag(version: str) -> None:
    git("tag", "-d", version, check=False)


# ---------------------------------------------------------------- 记录

def write_record(record: dict) -> Path:
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    path = RECORDS_DIR / f"{record['version']}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return path


def write_rollback_record(record: dict) -> Path:
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now().strftime("%Y%m%d-%H%M%S")
    path = RECORDS_DIR / f"rollback-{stamp}.json"
    n = 2
    while path.exists():
        path = RECORDS_DIR / f"rollback-{stamp}-{n}.json"
        n += 1
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return path


def load_records() -> list[dict]:
    records: list[dict] = []
    if not RECORDS_DIR.exists():
        return records
    for f in RECORDS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
        except ValueError:
            continue
        data["_file"] = f.name
        data["_mtime"] = f.stat().st_mtime
        records.append(data)
    records.sort(key=lambda r: r.get("_mtime", 0), reverse=True)
    return records


def commit_record(paths: list[Path], message: str) -> bool:
    """只提交指定文件（pathspec 提交，不碰其他会话的改动）。失败仅警告。"""
    rels = [str(p.relative_to(REPO)) for p in paths]
    add = subprocess.run(["git", "add", "--", *rels], cwd=REPO, capture_output=True, text=True)
    if add.returncode != 0:
        return False
    commit = subprocess.run(
        ["git", "commit", "-m", message, "--", *rels],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return commit.returncode == 0


# ---------------------------------------------------------------- R2 客户端

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".pdf": "application/pdf",
    ".wasm": "application/wasm",
}


def content_type_for(rel: str) -> str:
    return CONTENT_TYPES.get(Path(rel).suffix.lower(), "application/octet-stream")


def cache_control_for(rel: str) -> str:
    if rel == "index.html":
        return "no-cache"
    if rel.startswith("assets/") or "/assets/" in rel:
        return "public, max-age=31536000, immutable"
    return "public, max-age=300"


class R2:
    def __init__(self, account: str, bucket: str, token: str):
        self.account = account
        self.bucket = bucket
        self.token = token

    def _url(self, suffix: str, key: str | None = None) -> str:
        url = f"{CF_API}/accounts/{self.account}/{suffix}"
        if key is not None:
            url += "/" + urllib.parse.quote(key, safe="")
        return url

    def _request(self, method: str, url: str, data: bytes | None = None,
                 headers: dict | None = None, timeout: int = 120) -> bytes:
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("User-Agent", "lite-tv-release/1.0")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            detail = body
            try:
                parsed = json.loads(body)
                if parsed.get("errors"):
                    detail = json.dumps(parsed["errors"], ensure_ascii=False)
            except ValueError:
                pass
            raise ReleaseError(f"Cloudflare API {method} {url} 失败（HTTP {exc.code}）: {detail[:400]}")
        except urllib.error.URLError as exc:
            raise ReleaseError(f"网络错误（{method} {url}）: {exc.reason}")

    def head_bucket(self) -> None:
        self._request("GET", self._url(f"r2/buckets/{self.bucket}"), timeout=30)

    def put(self, key: str, data: bytes, content_type: str, cache_control: str) -> None:
        url = self._url(f"r2/buckets/{self.bucket}/objects", key)
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                self._request(
                    "PUT", url, data=data,
                    headers={"Content-Type": content_type, "Cache-Control": cache_control},
                )
                return
            except ReleaseError as exc:
                last_error = exc
                if "HTTP 4" in str(exc):
                    raise
                time.sleep(0.8 * attempt)
        raise ReleaseError(f"上传 {key} 失败（重试 3 次）: {last_error}")

    def get(self, key: str) -> bytes:
        return self._request("GET", self._url(f"r2/buckets/{self.bucket}/objects", key))

    def list_keys(self, prefix: str = "", per_page: int = 1000) -> list[str]:
        url = self._url(f"r2/buckets/{self.bucket}/objects")
        query = f"prefix={urllib.parse.quote(prefix, safe='')}&per_page={per_page}"
        body = self._request("GET", f"{url}?{query}", timeout=60)
        parsed = json.loads(body)
        return [o["key"] for o in parsed.get("result", [])]

    def delete(self, key: str) -> None:
        self._request("DELETE", self._url(f"r2/buckets/{self.bucket}/objects", key))


def make_r2(bucket_override: str | None = None) -> R2:
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    account = os.environ.get("R2_ACCOUNT_ID", "")
    bucket = bucket_override or os.environ.get("R2_BUCKET") or DEFAULT_BUCKET
    if not token:
        raise ReleaseError("缺少 CLOUDFLARE_API_TOKEN（需在 shell 环境变量中）")
    if not account:
        raise ReleaseError("缺少 R2_ACCOUNT_ID（写入项目 .env 或环境变量）")
    return R2(account, bucket, token)


# ---------------------------------------------------------------- 文件与上传

def scan_dist(dist: Path) -> list[tuple[str, bytes]]:
    entries: list[tuple[str, bytes]] = []
    for p in sorted(dist.rglob("*")):
        if not p.is_file():
            continue
        if p.name in {".DS_Store", "Thumbs.db"} or p.name.endswith((".swp", ".tmp")):
            continue
        rel = p.relative_to(dist).as_posix()
        entries.append((rel, p.read_bytes()))
    return entries


def tree_digest(entries: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for rel, data in sorted(entries):
        h.update(rel.encode())
        h.update(b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def human_size(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{n} B"


def upload_entries(r2: R2, entries: list[tuple[str, bytes]], key_prefix: str, label: str) -> None:
    if not entries:
        return
    total = len(entries)
    done = 0
    failures: list[tuple[str, str]] = []

    def task(rel: str, data: bytes) -> None:
        r2.put(f"{key_prefix}{rel}", data, content_type_for(rel), cache_control_for(rel))

    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        futures = {pool.submit(task, rel, data): rel for rel, data in entries}
        for fut in as_completed(futures):
            rel = futures[fut]
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001 - 汇总所有上传错误
                failures.append((rel, str(exc)))
            done += 1
            print(f"\r  {label}: {done}/{total}", end="", flush=True)
    print()
    if failures:
        detail = "\n".join(f"    - {rel}: {err}" for rel, err in failures[:10])
        raise ReleaseError(f"{len(failures)} 个文件上传失败：\n{detail}")


def verify_public(match_digest: str, base_url: str | None = None) -> bool:
    base = (base_url if base_url is not None else os.environ.get("R2_PUBLIC_BASE_URL", "")).rstrip("/")
    if not base:
        return True
    url = f"{base}/?release-check={int(time.time())}"
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": VERIFY_UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
            if hashlib.sha256(body).hexdigest() == match_digest:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


# ---------------------------------------------------------------- 子命令

def cmd_status(args: argparse.Namespace) -> int:
    info = lock_status()
    records = load_records()[: max(0, args.limit)]
    current: dict | None = None
    current_error: str | None = None
    try:
        r2 = make_r2()
        try:
            current = json.loads(r2.get("releases/current.json"))
        except ReleaseError as exc:
            if "HTTP 404" not in str(exc):
                current_error = str(exc)
    except ReleaseError as exc:
        current_error = str(exc)

    if args.json:
        print(json.dumps({
            "lock": info,
            "current": current,
            "current_error": current_error,
            "releases": records,
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"🔄 发布锁：{describe_lock(info)}")
    if current:
        print(
            f"🌐 线上当前版本：{current.get('version')}（{current.get('sha', '')[:8]}，"
            f"{current.get('published_at') or current.get('rolled_back_at') or '?'}）"
        )
    elif current_error:
        print(f"🌐 线上当前版本：未知（{current_error}）")
    else:
        print("🌐 线上当前版本：尚未发布")
    if records:
        print("\n最近发布：")
        print(f"  {'版本':<18} {'目标':<8} {'状态':<10} {'提交':<9} {'完成时间':<20} 备注")
        for r in records:
            version = str(r.get("version") or r.get("_file") or "?")
            target = str(r.get("target") or r.get("action") or "?")
            status = str(r.get("status") or "?")
            sha = str(r.get("short_sha") or r.get("sha") or "")[:8]
            finished = r.get("finished_at") or r.get("published_at") or ""
            dt = parse_iso(str(finished))
            when = fmt_time(dt) if dt else str(finished)[:19]
            note = str(r.get("note") or "")
            if len(note) > 30:
                note = note[:29] + "…"
            print(f"  {version:<18} {target:<8} {status:<10} {sha:<9} {when:<20} {note}")
    else:
        print("\n（还没有任何发布记录）")
    return 0


def cmd_r2(args: argparse.Namespace) -> int:
    dist = Path(args.dist)
    if not dist.is_absolute():
        dist = (Path.cwd() / dist).resolve()
    if not dist.is_dir():
        raise ReleaseError(f"构建目录不存在: {dist}")
    entries = scan_dist(dist)
    if not entries:
        raise ReleaseError(f"构建目录是空的: {dist}")
    if not args.allow_no_index and not (dist / "index.html").is_file():
        raise ReleaseError(f"构建目录缺少 index.html: {dist}（确有需要加 --allow-no-index）")

    dirty = False
    source = args.source.strip()
    if source:
        st = git("status", "--porcelain", "--", source)
        dirty = bool(st)
        if dirty and not args.allow_dirty:
            raise ReleaseError(
                f"发布前先提交 {source}/ 的改动（git status 如下）：\n{st}\n"
                f"或者显式使用 --allow-dirty 并在 --note 里说明原因。"
            )

    index_mtime = (dist / "index.html").stat().st_mtime if (dist / "index.html").exists() else 0
    stale_files: list[str] = []
    web = REPO / (source or "web")
    for candidate in [web / "src", web / "public"]:
        if candidate.exists():
            for p in candidate.rglob("*"):
                if p.is_file() and p.stat().st_mtime > index_mtime:
                    stale_files.append(str(p.relative_to(REPO)))
    if len(stale_files) > 3:
        stale_files = stale_files[:3] + [f"…共 {len(stale_files)} 个"]
    if stale_files:
        print(f"⚠️  dist 可能落后于源码（下列文件比 dist/index.html 新）：\n    - " + "\n    - ".join(stale_files))
        print("    （仅提示；如需确认请重新构建：cd web && npm run build）")

    digest = tree_digest(entries)
    total_size = sum(len(data) for _, data in entries)

    # Guard: the H5 build must never be promoted into the marketing-site bucket.
    target_bucket = (args.bucket or os.environ.get("R2_BUCKET") or DEFAULT_BUCKET).strip()
    h5_bucket = (os.environ.get("R2_H5_BUCKET") or "minitv-h5").strip()
    dist_parts = [part.lower() for part in Path(args.dist).parts]
    source_parts = [part.lower() for part in Path(source).parts] if source else []
    looks_like_h5 = "h5" in dist_parts or "h5" in source_parts
    if looks_like_h5 and target_bucket != h5_bucket:
        raise ReleaseError(
            f"拒绝发布：这次要传的是 H5 构建（{args.dist}），但目标桶是 {target_bucket}。\n"
            f"H5 站点是独立桶 {h5_bucket}，误发到营销站桶会直接覆盖官网根目录。\n"
            f"正确命令：python3 scripts/release.py r2 --bucket {h5_bucket} --dist {args.dist} ..."
        )

    if args.dry_run:
        version = next_version()
        print("📋 dry-run 发布计划（不创建 tag、不上传、不写记录）")
        print(f"   预览版本号: {version}（正式执行时重算）")
        print(f"   文件数量:   {len(entries)}（{human_size(total_size)}）")
        print(f"   目标桶:     {target_bucket}")
        print(f"   版本目录:   releases/{version}/")
        promote_prefix = (args.prefix or "").strip("/")
        promote_where = f"{promote_prefix}/" if promote_prefix else "桶根目录"
        print(f"   提升位置:   {'否（--no-promote）' if args.no_promote else promote_where}")
        print(f"   dist 摘要:  {digest[:16]}…")
        r2 = make_r2(args.bucket)
        r2.head_bucket()
        print("   ✅ 桶可达性检查通过")
        return 0

    sha = resolve_sha(args.sha)
    short = sha[:8]
    started = now()
    lock = ReleaseLock(git_common_dir() / LOCK_NAME, args.wait)
    base_meta = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "user": os.environ.get("USER"),
        "agent_session": os.environ.get("CODEX_SESSION_ID"),
        "target": "r2",
        "started_at": started.isoformat(timespec="seconds"),
        "cwd": str(Path.cwd()),
        "version": None,
    }
    try:
        lock.acquire(base_meta)
    except LockBusy as exc:
        print(f"❌ 发布锁被占用（等待 {args.wait}s 超时），本次不发布。")
        print(f"   当前持有者: {describe_lock({'held': True, 'meta': exc.holder})}")
        print("   建议: 稍后重试，或加大 --wait；不要删除/绕过锁文件。")
        return 3

    version: str | None = None
    tag_created = False
    record_path: Path | None = None
    try:
        version = next_version()
        lock.update_meta({**base_meta, "version": version})
        create_tag(version, sha, "r2")
        tag_created = True
        print(f"🚀 {version} 发布开始（提交 {short}，{len(entries)} 个文件，{human_size(total_size)}）")

        r2 = make_r2(args.bucket)
        version_prefix = f"releases/{version}/"
        upload_entries(r2, entries, version_prefix, "上传版本目录")

        promoted = False
        promote_prefix = (args.prefix or "").strip("/")
        live_prefix = f"{promote_prefix}/" if promote_prefix else ""
        pointer_key = f"{live_prefix}releases/current.json" if live_prefix else "releases/current.json"
        if not args.no_promote:
            others = [(rel, data) for rel, data in entries if rel != "index.html"]
            index_only = [(rel, data) for rel, data in entries if rel == "index.html"]
            upload_entries(r2, others, live_prefix, f"切换 {live_prefix or '根目录'}")
            upload_entries(r2, index_only, live_prefix, f"切换 {live_prefix or '根目录'}(index)")
            r2.put(
                pointer_key,
                (
                    json.dumps({
                        "version": version,
                        "sha": sha,
                        "short_sha": short,
                        "target": "r2",
                        "bucket": args.bucket or os.environ.get("R2_BUCKET") or DEFAULT_BUCKET,
                        "published_at": now().isoformat(timespec="seconds"),
                        "files": len(entries),
                    }, ensure_ascii=False, indent=2) + "\n"
                ).encode(),
                "application/json; charset=utf-8",
                "no-cache",
            )
            promoted = True

        finished = now()
        record = {
            "version": version,
            "target": "r2",
            "status": "published",
            "sha": sha,
            "short_sha": short,
            "bucket": args.bucket or os.environ.get("R2_BUCKET") or DEFAULT_BUCKET,
            "prefix": version_prefix,
            "promote_prefix": live_prefix,
            "promoted_root": promoted,
            "dist_digest": digest,
            "files": [{"path": rel, "size": len(data)} for rel, data in entries],
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": finished.isoformat(timespec="seconds"),
            "operator": operator_info(),
            "note": args.note,
            "dirty": dirty,
        }
        record_path = write_record(record)
        committed = commit_record([record_path], f"chore(release): {version} → r2 ({record['bucket']})")

        ok_line = "✅ 发布完成"
        print(ok_line)
        print(f"   版本:     {version}")
        print(f"   提交:     {short}（tag {version}）")
        print(f"   文件:     {len(entries)} 个（{human_size(total_size)}）")
        print(f"   桶:       {record['bucket']}")
        print(f"   版本目录: {version_prefix}")
        if promoted:
            print(f"   线上目录: 已切换 {live_prefix or '/'}（index.html 最后上传）")
            index_data = next((data for rel, data in entries if rel == "index.html"), None)
            verified = bool(index_data is not None and verify_public(
                hashlib.sha256(index_data).hexdigest(),
                args.verify_url or None,
            ))
            if verified:
                base = (args.verify_url or os.environ.get("R2_PUBLIC_BASE_URL", "")).rstrip("/")
                print(f"   线上校验: {base} 内容一致 ✅")
            else:
                print("   ⚠️ 线上校验未通过：检查 R2 根路径 Rewrite 规则（/ → /index.html）与缓存，稍后复核")
        else:
            print("   根目录:   未切换（--no-promote）")
        print(f"   记录:     {record_path.relative_to(REPO)}" + ("（已提交）" if committed else "（未提交，请手动检查）"))
        if dirty:
            print("   ⚠️ 本次发布包含未提交改动（--allow-dirty）")
        return 0
    except Exception as exc:
        if tag_created and version:
            delete_tag(version)
        if version:
            failed = {
                "version": version,
                "target": "r2",
                "status": "failed",
                "sha": sha,
                "short_sha": short,
                "started_at": started.isoformat(timespec="seconds"),
                "finished_at": now().isoformat(timespec="seconds"),
                "operator": operator_info(),
                "note": args.note,
                "error": str(exc),
            }
            try:
                p = write_record(failed)
                commit_record([p], f"chore(release): {version} r2 failed")
            except Exception:
                pass
        raise
    finally:
        lock.release()


def cmd_run(args: argparse.Namespace) -> int:
    cmd = [c for c in args.cmd if c != "--"]
    if not cmd:
        raise ReleaseError("缺少要执行的命令，用法: release.py run --target server -- ./scripts/deploy_server.sh")

    tree_dirty = bool(git("status", "--porcelain"))
    if tree_dirty and not args.allow_dirty:
        raise ReleaseError("工作区不干净。服务器发布要求整树干净；确需发布先提交，或加 --allow-dirty。")

    sha = resolve_sha(args.sha)
    short = sha[:8]

    if args.dry_run:
        version = next_version()
        print("📋 dry-run：不执行、不打 tag、不写记录")
        print(f"   预览版本号: {version}")
        print(f"   目标:       {args.target}")
        print(f"   提交:       {short}")
        print(f"   命令:       {' '.join(cmd)}")
        print(f"   工作区:     {'脏（--allow-dirty）' if tree_dirty else '干净'}")
        return 0

    started = now()
    lock = ReleaseLock(git_common_dir() / LOCK_NAME, args.wait)
    base_meta = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "user": os.environ.get("USER"),
        "agent_session": os.environ.get("CODEX_SESSION_ID"),
        "target": args.target,
        "started_at": started.isoformat(timespec="seconds"),
        "cwd": str(Path.cwd()),
        "version": None,
    }
    try:
        lock.acquire(base_meta)
    except LockBusy as exc:
        print(f"❌ 发布锁被占用（等待 {args.wait}s 超时），本次不发布。")
        print(f"   当前持有者: {describe_lock({'held': True, 'meta': exc.holder})}")
        return 3

    version: str | None = None
    tag_created = False
    try:
        version = next_version()
        lock.update_meta({**base_meta, "version": version})
        create_tag(version, sha, args.target)
        tag_created = True
        print(f"🚀 {version} 启动（目标 {args.target}，提交 {short}）")
        print(f"   $ {' '.join(cmd)}")
        proc = subprocess.run(cmd, cwd=REPO)
        finished = now()
        status = "published" if proc.returncode == 0 else "failed"
        record = {
            "version": version,
            "target": args.target,
            "status": status,
            "sha": sha,
            "short_sha": short,
            "command": cmd,
            "exit_code": proc.returncode,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": finished.isoformat(timespec="seconds"),
            "operator": operator_info(),
            "note": args.note,
            "dirty": tree_dirty,
        }
        record_path = write_record(record)
        committed = commit_record([record_path], f"chore(release): {version} → {args.target} ({status})")
        if proc.returncode != 0:
            delete_tag(version)
            print(f"❌ 命令退出码 {proc.returncode}，发布失败；已删除 tag {version}，保留 failed 记录。")
            return 1
        print(f"✅ {version} 发布完成（目标 {args.target}）")
        print(f"   记录: {record_path.relative_to(REPO)}" + ("（已提交）" if committed else "（未提交）"))
        return 0
    finally:
        lock.release()


def cmd_rollback(args: argparse.Namespace) -> int:
    version = args.to
    record_file = RECORDS_DIR / f"{version}.json"
    if not record_file.exists():
        raise ReleaseError(f"找不到发布记录: {record_file}")
    record = json.loads(record_file.read_text())
    if record.get("target") != "r2":
        raise ReleaseError(f"{version} 不是 R2 发布记录（target={record.get('target')}）")
    files = [f["path"] for f in record.get("files", [])]
    if not files:
        raise ReleaseError(f"{version} 的记录里没有文件清单，无法回滚")
    live_prefix = str(record.get("promote_prefix") or "").strip("/")
    if live_prefix:
        live_prefix = live_prefix + "/"
    pointer_key = f"{live_prefix}releases/current.json" if live_prefix else "releases/current.json"

    if args.dry_run:
        print("📋 dry-run 回滚计划（不写任何东西）")
        print(f"   目标版本: {version}（{len(files)} 个文件，切换 {live_prefix or '根目录'}）")
        print(f"   提交:     {record.get('short_sha')}")
        print(f"   记录:     {record_file.relative_to(REPO)}")
        return 0

    started = now()
    lock = ReleaseLock(git_common_dir() / LOCK_NAME, args.wait)
    base_meta = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "user": os.environ.get("USER"),
        "agent_session": os.environ.get("CODEX_SESSION_ID"),
        "target": "r2-rollback",
        "started_at": started.isoformat(timespec="seconds"),
        "cwd": str(Path.cwd()),
        "version": f"rollback→{version}",
    }
    try:
        lock.acquire(base_meta)
    except LockBusy as exc:
        print(f"❌ 发布锁被占用（等待 {args.wait}s 超时），本次不发布。")
        print(f"   当前持有者: {describe_lock({'held': True, 'meta': exc.holder})}")
        return 3

    try:
        r2 = make_r2()
        previous_version = None
        try:
            previous_version = json.loads(r2.get(pointer_key)).get("version")
        except ReleaseError:
            pass

        entries: list[tuple[str, bytes]] = []
        print(f"🚀 回滚到 {version}（{len(files)} 个文件）")
        for rel in files:
            data = r2.get(f"releases/{version}/{rel}")
            entries.append((rel, data))
        others = [(rel, data) for rel, data in entries if rel != "index.html"]
        index_only = [(rel, data) for rel, data in entries if rel == "index.html"]
        upload_entries(r2, others, live_prefix, f"切换 {live_prefix or '根目录'}")
        upload_entries(r2, index_only, live_prefix, f"切换 {live_prefix or '根目录'}(index)")

        r2.put(
            pointer_key,
            (
                json.dumps({
                    "version": version,
                    "sha": record.get("sha"),
                    "short_sha": record.get("short_sha"),
                    "target": "r2",
                    "published_at": record.get("published_at") or record.get("finished_at"),
                    "rolled_back_at": now().isoformat(timespec="seconds"),
                    "files": len(files),
                }, ensure_ascii=False, indent=2) + "\n"
            ).encode(),
            "application/json; charset=utf-8",
            "no-cache",
        )

        rb_record = {
            "version": f"rollback-{version}",
            "action": "rollback",
            "target": "r2",
            "status": "published",
            "to": version,
            "from_version": previous_version,
            "files": len(files),
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": now().isoformat(timespec="seconds"),
            "operator": operator_info(),
            "note": args.note,
        }
        path = write_rollback_record(rb_record)
        committed = commit_record([path], f"chore(release): rollback r2 → {version}")
        print(f"✅ 已回滚到 {version}" + (f"（原版本 {previous_version}）" if previous_version else ""))
        print(f"   记录: {path.relative_to(REPO)}" + ("（已提交）" if committed else "（未提交）"))
        return 0
    finally:
        lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release.py", description="LiteTV 统一发布工具（多 Agent 安全）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="查看锁 / 最近发布 / 线上当前版本")
    p_status.add_argument("--json", action="store_true", help="输出 JSON（供脚本/Agent 读取）")
    p_status.add_argument("--limit", type=int, default=12, help="显示最近多少条记录（默认 12）")
    p_status.set_defaults(func=cmd_status)

    p_r2 = sub.add_parser("r2", help="发布静态前端到 Cloudflare R2")
    p_r2.add_argument("--dist", default="web/dist", help="构建产物目录（默认 web/dist）")
    p_r2.add_argument("--source", default="web", help="要求工作区干净的源码路径（默认 web；置空跳过校验）")
    p_r2.add_argument("--bucket", default=None, help="覆盖桶名（默认取 R2_BUCKET）")
    p_r2.add_argument("--note", default="", help="发布备注（写进记录）")
    p_r2.add_argument("--wait", type=int, default=60, help="等锁秒数（默认 60；0 立即失败）")
    p_r2.add_argument("--dry-run", action="store_true", help="只校验和展示计划，不写任何东西")
    p_r2.add_argument("--allow-dirty", action="store_true", help="允许带未提交改动发布（记录 dirty 标记）")
    p_r2.add_argument("--no-promote", action="store_true", help="只上传版本目录，不切换线上目录")
    p_r2.add_argument("--prefix", default="", help="提升到桶内前缀（如 admin）；版本归档仍在 releases/{version}/")
    p_r2.add_argument("--verify-url", default="", help="线上校验基址（默认 R2_PUBLIC_BASE_URL）")
    p_r2.add_argument("--allow-no-index", action="store_true", help="允许没有 index.html")
    p_r2.add_argument("--sha", default="HEAD", help="打 tag 的提交（默认 HEAD）")
    p_r2.set_defaults(func=cmd_r2)

    p_run = sub.add_parser("run", help="在版本号+锁保护下执行任意上线命令（服务器部署等）")
    p_run.add_argument("--target", required=True, help="目标名（如 server）")
    p_run.add_argument("--note", default="")
    p_run.add_argument("--wait", type=int, default=60)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--allow-dirty", action="store_true")
    p_run.add_argument("--sha", default="HEAD")
    p_run.add_argument("cmd", nargs=argparse.REMAINDER, help="-- 之后是要执行的命令")
    p_run.set_defaults(func=cmd_run)

    p_rb = sub.add_parser("rollback", help="回滚（R2 重新切换根目录）")
    rb_sub = p_rb.add_subparsers(dest="what", required=True)
    p_rb_r2 = rb_sub.add_parser("r2", help="把历史版本重新提升为根目录")
    p_rb_r2.add_argument("--to", required=True, help="要回滚到的版本号，如 v2026.10.14.3")
    p_rb_r2.add_argument("--note", default="")
    p_rb_r2.add_argument("--wait", type=int, default=60)
    p_rb_r2.add_argument("--dry-run", action="store_true")
    p_rb_r2.set_defaults(func=cmd_rollback)

    args = parser.parse_args(argv)
    load_dotenv()
    try:
        return args.func(args)
    except ReleaseError as exc:
        print(f"❌ {exc}")
        return 1
    except KeyboardInterrupt:
        print("\n⚠️ 已中断（锁已自动释放；如已创建 tag 请检查）")
        return 130


if __name__ == "__main__":
    sys.exit(main())
