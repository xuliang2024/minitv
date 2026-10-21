#!/usr/bin/env python3
"""Finish draft dramas left behind by stalled Bunny encodes.

Some uploads land in Bunny but never get picked up by the encoder: the video
sits at ``status=2`` with ~5% progress, no length and no renditions forever.
The backend only flags a *zero*-progress object as stalled, so these rows stay
``transcoding`` and block the drama from publishing.

For every draft drama this script:

1. finds episodes still ``transcoding``;
2. asks Bunny for the object; if it has no length and barely any progress,
   deletes the dead object and calls ``upload-failed`` so the row becomes
   ``transcode_failed``;
3. re-runs ``import_hotlist.py`` for that drama, which re-uploads the missing
   episodes, waits for the encode and publishes.

Usage::

    python3 -u scripts/repair_stalled_episodes.py --manifest data/import-logs/manifest-2026-10-20.json \
        --min-id 116 --stale-minutes 30 --publish
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPORTER = ROOT / "scripts" / "import_hotlist.py"
API_DEFAULT = "https://api.minitv.us"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


def api(token: str, base: str, method: str, path: str, payload: dict | None = None) -> dict:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}") from exc


def login(base: str) -> str:
    body = json.dumps({
        "username": os.environ.get("ADMIN_USER", "admin"),
        "password": os.environ.get("ADMIN_PASS", "admin-1234"),
    }).encode()
    request = urllib.request.Request(
        base + "/admin/v1/auth/login",
        data=body,
        headers={"User-Agent": UA, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())["token"]


def bunny_get(key: str, video_id: str) -> dict:
    library = os.environ["BUNNY_STREAM_LIBRARY_ID"]
    request = urllib.request.Request(
        f"https://video.bunnycdn.com/library/{library}/videos/{video_id}",
        headers={"AccessKey": key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def bunny_delete(key: str, video_id: str) -> None:
    library = os.environ["BUNNY_STREAM_LIBRARY_ID"]
    request = urllib.request.Request(
        f"https://video.bunnycdn.com/library/{library}/videos/{video_id}",
        headers={"AccessKey": key},
        method="DELETE",
    )
    try:
        urllib.request.urlopen(request, timeout=30).read()
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise


def is_stalled(remote: dict, stale_minutes: int) -> bool:
    status = int(remote.get("status") or 0)
    if status not in (0, 1, 2):
        return False
    if int(remote.get("length") or 0) > 0:
        return False
    if int(remote.get("encodeProgress") or 0) > 10:
        return False
    uploaded = remote.get("dateUploaded")
    if not uploaded:
        return False
    try:
        uploaded_at = datetime.datetime.fromisoformat(str(uploaded))
    except ValueError:
        return False
    now = datetime.datetime.now(datetime.timezone.utc)
    if uploaded_at.tzinfo is None:
        uploaded_at = uploaded_at.replace(tzinfo=datetime.timezone.utc)
    return (now - uploaded_at).total_seconds() >= stale_minutes * 60


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--min-id", type=int, default=116)
    parser.add_argument("--stale-minutes", type=int, default=30)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--forward-port", type=int, default=18443)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--api", default=os.environ.get("MINITV_API", API_DEFAULT))
    args = parser.parse_args()

    key = os.environ["BUNNY_STREAM_API_KEY"]
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    by_title = {item["title_en"]: item for item in manifest}

    token = login(args.api)
    drafts: list[dict] = []
    page = 1
    while True:
        listing = api(token, args.api, "GET", f"/admin/v1/dramas?status=draft&page={page}&page_size=100")
        drafts.extend([item for item in listing.get("items", []) if item["id"] >= args.min_id])
        if len(listing.get("items", [])) < 100:
            break
        page += 1

    print(f"drafts: {len(drafts)}")
    targets: list[dict] = []
    for drama in drafts:
        entry = by_title.get(drama["title"])
        if entry is None:
            print(f"  skip #{drama['id']} {drama['title']} (not in manifest)")
            continue
        rows = api(token, args.api, "GET", f"/admin/v1/dramas/{drama['id']}/episodes?page_size=200")["items"]
        stuck = [row for row in rows if row["status"] == "transcoding"]
        failed = [row for row in rows if row["status"] == "transcode_failed"]
        repaired = 0
        for row in stuck:
            try:
                remote = bunny_get(key, row["bunny_video_id"])
            except Exception as exc:
                print(f"  #{drama['id']} ep{row['episode_number']:03d} bunny probe failed: {exc}")
                continue
            if not is_stalled(remote, args.stale_minutes):
                continue
            print(
                f"  #{drama['id']} ep{row['episode_number']:03d} stalled "
                f"(status={remote.get('status')} progress={remote.get('encodeProgress')}) -> reset"
            )
            if args.dry_run:
                repaired += 1
                continue
            bunny_delete(key, row["bunny_video_id"])
            try:
                api(token, args.api, "POST", f"/admin/v1/episodes/{row['id']}/upload-failed", {})
                repaired += 1
            except RuntimeError as exc:
                print(f"    upload-failed rejected: {exc}")
        if repaired or failed:
            targets.append(entry)
            print(f"  -> #{drama['id']} {drama['title']}: reset {repaired}, already failed {len(failed)}")

    print(f"dramas to finish: {len(targets)}")
    if args.dry_run:
        return

    for index, entry in enumerate(targets, 1):
        folder = Path(entry["dir"])
        synopsis = Path("/tmp") / f"repair_synopsis_{os.getpid()}.txt"
        synopsis.write_text(entry["synopsis_en"], encoding="utf-8")
        command = [
            sys.executable, "-u", str(IMPORTER),
            "--dir", str(folder),
            "--title-en", entry["title_en"],
            "--synopsis-file", str(synopsis),
            "--free", str(entry.get("free", 6)),
            "--price", str(entry.get("price", 50)),
            "--workers", str(args.workers),
            "--forward-port", str(args.forward_port),
        ]
        if args.publish:
            command.append("--publish")
        print(f"\n[{index}/{len(targets)}] {folder.name} -> {entry['title_en']}", flush=True)
        result = subprocess.run(command, cwd=ROOT)
        print(f"[{index}/{len(targets)}] exit {result.returncode}", flush=True)
        synopsis.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
