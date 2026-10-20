#!/usr/bin/env python3
"""Run ``import_hotlist.py`` over a manifest, one drama at a time.

Each drama is a separate child process, so a crash or a stuck upload only
costs one title; the runner records the outcome and moves on. A failed title
is retried once at the end of the pass.

Usage::

    python3 -u scripts/import_hotlist_batch.py --manifest /tmp/hotlist_manifest.json \
        --workers 4 --socks-port 11080 --publish
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPORTER = ROOT / "scripts" / "import_hotlist.py"


def now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--socks-port", type=int, default=11080)
    parser.add_argument("--forward-port", type=int, default=0)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument(
        "--fast-first-pass",
        action="store_true",
        help="first pass uploads only and skips waiting for transcodes; the retry pass publishes",
    )
    parser.add_argument("--only", help="comma-separated folder basenames")
    parser.add_argument("--start", type=int, default=0, help="skip the first N entries")
    parser.add_argument("--limit", type=int, default=0, help="process at most N entries")
    parser.add_argument("--log-dir", default=str(ROOT / "data" / "import-logs"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if args.only:
        wanted = {name.strip() for name in args.only.split(",") if name.strip()}
        manifest = [item for item in manifest if Path(item["dir"]).name in wanted]
    if args.start:
        manifest = manifest[args.start:]
    if args.limit:
        manifest = manifest[: args.limit]
    if args.dry_run:
        for index, item in enumerate(manifest, 1):
            print(f"{index:03d} {Path(item['dir']).name} -> {item['title_en']}")
        return

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    jsonl_path = log_dir / f"hotlist-{stamp}.jsonl"
    print(f"[{now()}] {len(manifest)} dramas, workers={args.workers}, publish={args.publish}")
    print(f"[{now()}] log {jsonl_path}")

    failures: list[dict] = []
    for index, item in enumerate(manifest, 1):
        folder = Path(item["dir"])
        title = item["title_en"]
        started = time.time()
        print(f"\n[{now()}] === {index}/{len(manifest)} {folder.name} -> {title} ===", flush=True)
        synopsis_path = Path("/tmp") / f"synopsis_{os.getpid()}_{index}.txt"
        synopsis_path.write_text(item["synopsis_en"], encoding="utf-8")
        command = [
            sys.executable, "-u", str(IMPORTER),
            "--dir", str(folder),
            "--title-en", title,
            "--synopsis-file", str(synopsis_path),
            "--free", str(item.get("free", 6)),
            "--price", str(item.get("price", 50)),
            "--workers", str(args.workers),
        ]
        if args.forward_port:
            command += ["--forward-port", str(args.forward_port)]
        elif args.socks_port:
            command += ["--socks-port", str(args.socks_port)]
        if args.publish:
            command.append("--publish")
        if args.fast_first_pass:
            command.append("--no-wait")

        result = subprocess.run(command, cwd=ROOT)
        elapsed = round(time.time() - started)
        status = "ok" if result.returncode == 0 else f"exit {result.returncode}"
        record = {
            "time": now(),
            "index": index,
            "dir": folder.name,
            "title_en": title,
            "status": status,
            "seconds": elapsed,
        }
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[{now()}] --- {folder.name}: {status} in {elapsed}s ---", flush=True)
        synopsis_path.unlink(missing_ok=True)
        if result.returncode != 0:
            failures.append(item)

    if failures:
        print(f"\n[{now()}] retrying {len(failures)} failed drama(s)")
        for item in failures:
            folder = Path(item["dir"])
            synopsis_path = Path("/tmp") / f"synopsis_retry_{os.getpid()}.txt"
            synopsis_path.write_text(item["synopsis_en"], encoding="utf-8")
            command = [
                sys.executable, "-u", str(IMPORTER),
                "--dir", str(folder),
                "--title-en", item["title_en"],
                "--synopsis-file", str(synopsis_path),
                "--free", str(item.get("free", 6)),
                "--price", str(item.get("price", 50)),
                "--workers", str(args.workers),
            ]
            if args.forward_port:
                command += ["--forward-port", str(args.forward_port)]
            elif args.socks_port:
                command += ["--socks-port", str(args.socks_port)]
            if args.publish:
                command.append("--publish")
            result = subprocess.run(command, cwd=ROOT)
            with jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "time": now(), "dir": folder.name, "title_en": item["title_en"],
                    "status": "retry_ok" if result.returncode == 0 else "retry_failed",
                }, ensure_ascii=False) + "\n")
            synopsis_path.unlink(missing_ok=True)

    print(f"\n[{now()}] batch finished")


if __name__ == "__main__":
    main()
