#!/usr/bin/env python3
"""Import one downloaded short-drama folder into MiniTV (drama -> cover -> episodes -> Bunny -> publish).

The folder layout is the one produced by the hot-list downloader::

    <dir>/metadata.json      # title, intro, cover, episodes[index, duration]
    <dir>/001.mp4 ... NNN.mp4

Every step is idempotent and resumable: an existing drama is reused, episodes
that already carry a Bunny video id are skipped, and a failed upload falls back
to ``upload-failed`` so the row can be retried on the next run.

Bunny bytes are pushed straight to the video object created by the admin
``upload-session`` endpoint. ``--socks-port`` routes those PUTs through an SSH
dynamic tunnel (``ssh -D``), which is the only fast path from a network whose
default route is a slow proxy.

Examples::

    python3 scripts/import_hotlist.py --dir "/path/002_雾锁情迷" \
        --title-en "Lost in the Mist" --synopsis-en "..." --publish

    python3 scripts/import_hotlist.py --dir ... --dry-run
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_DEFAULT = "https://api.minitv.us"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
BUNNY_STREAM_API = "https://video.bunnycdn.com"


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:300]}")


class Client:
    def __init__(self, base: str, username: str, password: str):
        self.base = base.rstrip("/")
        self.username = username
        self.password = password
        self.token = ""

    def _request(self, method: str, path: str, *, body: bytes | None = None, content_type: str = "application/json"):
        headers = {"User-Agent": UA, "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, exc.read().decode("utf-8", "replace")) from exc

    def login(self) -> dict:
        payload = json.dumps({"username": self.username, "password": self.password}).encode()
        data = self._request("POST", "/admin/v1/auth/login", body=payload)
        self.token = data["token"]
        return data["user"]

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, payload: dict | None = None):
        body = json.dumps(payload).encode() if payload is not None else b"{}"
        return self._request("POST", path, body=body)

    def patch(self, path: str, payload: dict):
        return self._request("PATCH", path, body=json.dumps(payload).encode())

    def upload_cover(self, drama_id: int, image_path: Path) -> dict:
        cmd = [
            "curl", "-sS", "--max-time", "120", "-A", UA,
            "-H", f"Authorization: Bearer {self.token}",
            "-F", f"file=@{image_path};type=image/jpeg",
            f"{self.base}/admin/v1/dramas/{drama_id}/cover",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"cover upload failed: {result.stderr.strip()}")
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"cover upload returned non-JSON: {result.stdout[:300]}") from exc
        if "cover_url" not in data:
            raise RuntimeError(f"cover upload rejected: {data}")
        return data


def probe(path: Path) -> tuple[int, int, int]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {path.name}: {result.stderr.strip()[:200]}")
    document = json.loads(result.stdout)
    stream = document["streams"][0]
    duration = int(round(float(document.get("format", {}).get("duration") or 0)))
    return int(stream["width"]), int(stream["height"]), duration


def bunny_put(video_id: str, path: Path, key: str, socks_port: int | None, forward_port: int | None = None) -> None:
    endpoint = f"{BUNNY_STREAM_API}/library/{os.environ['BUNNY_STREAM_LIBRARY_ID']}/videos/{video_id}"
    cmd = ["curl", "-sS", "--fail-with-body", "--max-time", "1800"]
    if forward_port:
        # Plain TCP forward (ssh -L). Much faster than the SOCKS relay for
        # large bodies, which OpenSSH's dynamic proxy throttles badly.
        cmd += ["--connect-to", f"video.bunnycdn.com:443:127.0.0.1:{forward_port}"]
    elif socks_port:
        cmd += ["--socks5-hostname", f"127.0.0.1:{socks_port}"]
    cmd += [
        "-X", "PUT", endpoint,
        "-H", f"AccessKey: {key}",
        "-H", "Content-Type: application/octet-stream",
        "--data-binary", f"@{path}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"bunny PUT failed ({path.name}): {result.stderr.strip()[:300]}")


def load_series(folder: Path) -> dict:
    document = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    files = {}
    for path in folder.glob("*.mp4"):
        try:
            files[int(path.stem)] = path
        except ValueError:
            continue
    if not files:
        raise SystemExit(f"no NNN.mp4 files in {folder}")
    missing = [e["index"] for e in document["episodes"] if e["index"] not in files]
    if missing:
        raise SystemExit(f"missing episode files: {missing[:10]}")
    return {"meta": document, "files": files}


def find_drama(client: Client, title: str) -> dict | None:
    data = client.get(f"/admin/v1/dramas?q={urllib.parse.quote(title)}&page_size=100")
    for item in data.get("items", []):
        if item["title"] == title:
            return item
    return None


def ensure_drama(client: Client, title: str) -> dict:
    drama = find_drama(client, title)
    if drama is not None:
        return drama
    return client.post("/admin/v1/dramas", {"title": title})


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a downloaded drama folder into MiniTV.")
    parser.add_argument("--dir", required=True, help="folder holding metadata.json and NNN.mp4")
    parser.add_argument("--title-en", required=True)
    parser.add_argument("--synopsis-en", default="")
    parser.add_argument("--synopsis-file", help="read the English synopsis from this file")
    parser.add_argument("--free", type=int, default=6, help="free episode count (default 6)")
    parser.add_argument("--price", type=int, default=50, help="coins per locked episode (default 50)")
    parser.add_argument("--workers", type=int, default=6, help="concurrent Bunny uploads (default 6)")
    parser.add_argument("--socks-port", type=int, help="SSH dynamic tunnel port for Bunny traffic")
    parser.add_argument("--forward-port", type=int, help="local end of an ssh -L forward to video.bunnycdn.com:443 (preferred)")
    parser.add_argument("--publish", action="store_true", help="publish episodes and the drama when transcodes finish")
    parser.add_argument("--wait-minutes", type=int, default=20, help="how long to poll transcodes (default 20)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--api", default=os.environ.get("MINITV_API", API_DEFAULT))
    args = parser.parse_args()

    folder = Path(args.dir).resolve()
    series = load_series(folder)
    meta = series["meta"]
    synopsis = args.synopsis_en
    if args.synopsis_file:
        synopsis = Path(args.synopsis_file).read_text(encoding="utf-8").strip()
    if not synopsis:
        raise SystemExit("pass --synopsis-en or --synopsis-file")

    print(f"series: {meta['title']} -> {args.title_en} ({len(series['files'])} episodes)")
    if args.dry_run:
        for number, path in sorted(series["files"].items()):
            print(f"  ep{number:03d} {path.name} {path.stat().st_size / 1048576:.1f}MB")
        return

    key = os.environ.get("BUNNY_STREAM_API_KEY", "").strip()
    library = os.environ.get("BUNNY_STREAM_LIBRARY_ID", "").strip()
    if not key or not library:
        raise SystemExit("BUNNY_STREAM_API_KEY and BUNNY_STREAM_LIBRARY_ID must be exported")

    client = Client(args.api, os.environ.get("ADMIN_USER", "admin"), os.environ.get("ADMIN_PASS", "admin-1234"))
    user = client.login()
    print(f"logged in as {user['username']}")

    drama = ensure_drama(client, args.title_en)
    drama_id = drama["id"]
    print(f"drama #{drama_id} status={drama['status']}")

    client.patch(
        f"/admin/v1/dramas/{drama_id}",
        {
            "synopsis": synopsis,
            "free_episode_count": args.free,
            "coin_price_per_episode": args.price,
        },
    )
    print(f"metadata set: free={args.free} price={args.price} synopsis={len(synopsis)} chars")

    if not drama.get("cover_url"):
        local_cover = folder / str(meta.get("cover_file") or "")
        with tempfile.TemporaryDirectory() as tmp:
            if local_cover.is_file():
                image = local_cover
            else:
                image = Path(tmp) / "cover.jpg"
                request = urllib.request.Request(meta["cover"], headers={"User-Agent": UA})
                with urllib.request.urlopen(request, timeout=60) as response:
                    image.write_bytes(response.read())
            client.upload_cover(drama_id, image)
        print("cover re-hosted")

    count = int(meta["episode_cnt"])
    created = client.post(f"/admin/v1/dramas/{drama_id}/episodes/bulk", {"count": count})
    print(f"episodes: total={created['total']} newly_created={len(created['created'])}")

    listing = client.get(f"/admin/v1/dramas/{drama_id}/episodes?page_size=200")
    rows = {row["episode_number"]: row for row in listing["items"]}
    if len(rows) < count:
        raise SystemExit(f"episode rows incomplete: {len(rows)}/{count}")

    pending: list[tuple[int, int, Path]] = []
    for number, path in sorted(series["files"].items()):
        row = rows.get(number)
        if row is None:
            raise SystemExit(f"episode row missing for #{number}")
        if row.get("bunny_video_id") and row["status"] in {"transcoding", "in_review", "published"}:
            continue
        pending.append((row["id"], number, path))
    print(f"uploads pending: {len(pending)}")

    failures: list[str] = []

    def upload(item: tuple[int, int, Path]) -> None:
        episode_id, number, path = item
        width, height, duration = probe(path)
        session = client.post(
            f"/admin/v1/episodes/{episode_id}/upload-session",
            {
                "filename": path.name,
                "mime": "video/mp4",
                "size_bytes": path.stat().st_size,
                "width": width,
                "height": height,
                "duration_seconds": duration,
            },
        )
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                bunny_put(session["video_id"], path, key, args.socks_port, args.forward_port)
                last_error = None
                break
            except Exception as exc:  # Bunny returns transient 500s under load
                last_error = exc
                if attempt < 3:
                    time.sleep(5 * attempt)
        if last_error is not None:
            client.post(f"/admin/v1/episodes/{episode_id}/upload-failed", {})
            raise last_error
        print(f"  ep{number:03d} uploaded -> {session['video_id']}")

    if pending:
        with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            jobs = {pool.submit(upload, item): item for item in pending}
            for future in futures.as_completed(jobs):
                _episode_id, number, _path = jobs[future]
                try:
                    future.result()
                except Exception as exc:  # keep the remaining uploads going
                    failures.append(f"ep{number:03d}: {exc}")
                    print(f"  ep{number:03d} FAILED: {exc}")

    if failures:
        print(f"\n{len(failures)} upload(s) failed; rerun to retry:")
        for line in failures:
            print("  " + line)
        # Never publish a drama with holes: exit non-zero so the batch runner
        # retries this title (already-uploaded episodes are skipped).
        return 3

    if not args.publish:
        print("\nuploads done; transcode status will advance via the backend reconciler")
        return

    deadline = time.time() + args.wait_minutes * 60
    while True:
        listing = client.get(f"/admin/v1/dramas/{drama_id}/episodes?page_size=200")
        states = [row["status"] for row in listing["items"]]
        ready = sum(1 for s in states if s in {"in_review", "published"})
        failed = sum(1 for s in states if s == "transcode_failed")
        print(f"transcode: ready={ready} failed={failed} total={len(states)}")
        if ready + failed >= len(states) or time.time() > deadline:
            break
        time.sleep(20)

    listing = client.get(f"/admin/v1/dramas/{drama_id}/episodes?page_size=200")
    failed_rows = [row for row in listing["items"] if row["status"] == "transcode_failed"]
    if failed_rows:
        print(f"{len(failed_rows)} episode(s) failed to transcode; skipping publish, rerun to retry")
        return 3
    for row in listing["items"]:
        if row["status"] != "in_review":
            continue
        try:
            client.post(f"/admin/v1/episodes/{row['id']}/publish")
        except ApiError as exc:
            print(f"  publish ep{row['episode_number']:03d} failed: {exc}")

    current = client.get(f"/admin/v1/dramas/{drama_id}")
    if current["status"] == "draft":
        client.post(f"/admin/v1/dramas/{drama_id}/status", {"to_status": "in_review"})
    if current["status"] in {"draft", "in_review"}:
        client.post(f"/admin/v1/dramas/{drama_id}/status", {"to_status": "published"})
    final = client.get(f"/admin/v1/dramas/{drama_id}")
    published = client.get(f"/admin/v1/dramas/{drama_id}/episodes?page_size=200")
    print(
        f"\ndone: drama #{drama_id} {final['status']}, "
        f"published {sum(1 for r in published['items'] if r['status'] == 'published')}/{len(published['items'])} episodes"
    )


if __name__ == "__main__":
    sys.exit(main())
