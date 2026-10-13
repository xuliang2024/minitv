#!/usr/bin/env python3
"""Stage 15 local release rehearsal.

Copies a frontend dist into releases/{sha} and switches the current pointer
only after the health check succeeds. Rolling back the pointer does not touch
the database.
"""

from __future__ import annotations

import argparse
import shutil
import urllib.request
from pathlib import Path


def health_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            body = response.read()
            return response.status == 200 and b'"status"' in body and b'ok' in body
    except Exception:
        return False


def publish(dist: Path, releases: Path, sha: str, health_url: str) -> Path:
    if not health_ok(health_url):
        raise SystemExit(f"health check failed: {health_url}")
    target = releases / sha
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(dist, target)
    pointer = releases / "current"
    pointer.write_text(sha + "\n")
    return target


def rollback(releases: Path, sha: str) -> None:
    if not (releases / sha).is_dir():
        raise SystemExit(f"missing release {sha}")
    (releases / "current").write_text(sha + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--releases", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--health", default="http://127.0.0.1:18110/health")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if args.rollback:
        rollback(args.releases, args.sha)
        return
    publish(args.dist, args.releases, args.sha, args.health)


if __name__ == "__main__":
    main()
