#!/usr/bin/env python3
"""Verify the local MiniTV test environment."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


def check(name: str, ok: bool, detail: str) -> bool:
    mark = "OK" if ok else "MISSING"
    print(f"[{mark}] {name}: {detail}")
    return ok


def main() -> int:
    load_env(ROOT / ".env")
    failures = 0

    db_url = os.environ.get("DATABASE_URL", "")
    redis_url = os.environ.get("REDIS_URL", "")
    failures += not check("DATABASE_URL", bool(db_url), db_url or "unset")
    failures += not check("REDIS_URL", bool(redis_url), redis_url or "unset")

    try:
        import psycopg
    except ImportError:
        failures += not check("psycopg", False, "not installed in this interpreter")
        psycopg = None
    if psycopg and db_url:
        try:
            with psycopg.connect(db_url, connect_timeout=5) as conn:
                version = conn.execute("SHOW server_version").fetchone()[0]
                conn.execute("SELECT 1")
            failures += not check("PostgreSQL", True, version)
        except Exception as exc:
            failures += not check("PostgreSQL", False, str(exc))

    try:
        import redis
    except ImportError:
        failures += not check("redis-py", False, "not installed in this interpreter")
        redis = None
    if redis and redis_url:
        try:
            client = redis.Redis.from_url(redis_url, socket_connect_timeout=5)
            pong = client.ping()
            info = client.info("server")
            failures += not check("Redis", bool(pong), info.get("redis_version", "unknown"))
        except Exception as exc:
            failures += not check("Redis", False, str(exc))

    flutter = shutil.which("flutter")
    if flutter:
        proc = subprocess.run([flutter, "--version"], capture_output=True, text=True)
        first = (proc.stdout or proc.stderr).splitlines()
        failures += not check("Flutter", proc.returncode == 0, first[0] if first else flutter)
    else:
        failures += not check("Flutter", False, "not on PATH")

    adb = os.environ.get("ANDROID_HOME", "") + "/platform-tools/adb"
    if not Path(adb).exists():
        adb = shutil.which("adb") or ""
    failures += not check("adb", bool(adb) and Path(adb).exists(), adb or "missing")

    emulator = os.environ.get("ANDROID_HOME", "") + "/emulator/emulator"
    avds = []
    if Path(emulator).exists():
        proc = subprocess.run([emulator, "-list-avds"], capture_output=True, text=True)
        avds = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    failures += not check("Android emulator", bool(avds), ", ".join(avds) or "no AVD")

    sim = subprocess.run(
        ["xcrun", "simctl", "list", "runtimes"],
        capture_output=True,
        text=True,
    )
    runtime_lines = [
        line.strip()
        for line in sim.stdout.splitlines()
        if "iOS" in line and "unavailable" not in line.lower() and not line.startswith("==")
    ]
    failures += not check(
        "iOS Simulator runtime",
        bool(runtime_lines),
        runtime_lines[0] if runtime_lines else "no iOS runtime installed",
    )

    print("RESULT", "PASS" if failures == 0 else f"FAIL {failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
