#!/usr/bin/env python3
"""Verify the local MiniTV test environment.

Stage-0 checks always count toward the result: .env values, PostgreSQL,
Redis, and the Python dependencies the app imports (run via the project
virtualenv; the script re-executes itself inside `.venv` when needed).

Flutter, the Android toolchain, and the iOS Simulator runtime are stage-8
environment gates: they are reported separately and never fail stage 0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ensure_venv() -> None:
    """Re-exec with the project virtualenv so psycopg/redis are importable."""
    venv_dir = ROOT / ".venv"
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.exists():
        return
    if Path(sys.prefix).resolve() == venv_dir.resolve():
        return
    try:
        os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])
    except OSError as exc:
        print(f"[WARN] could not re-exec into {venv_python}: {exc}", file=sys.stderr)


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


def gate(name: str, ok: bool, detail: str) -> bool:
    """Stage-8 environment gate: reported separately, never fails stage 0."""
    mark = "OK" if ok else "DEFER"
    print(f"[{mark}] {name} (stage gate): {detail}")
    return ok


def find_flutter() -> str | None:
    found = shutil.which("flutter")
    if found:
        return found
    for candidate in (
        Path("/opt/homebrew/share/flutter/bin/flutter"),
        Path.home() / "development/flutter/bin/flutter",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def main() -> int:
    ensure_venv()
    load_env(ROOT / ".env")
    failures = 0

    db_url = os.environ.get("DATABASE_URL", "")
    redis_url = os.environ.get("REDIS_URL", "")
    failures += not check("DATABASE_URL", bool(db_url), db_url or "unset")
    failures += not check("REDIS_URL", bool(redis_url), redis_url or "unset")

    try:
        import psycopg
    except ImportError:
        failures += not check("psycopg", False, f"not installed in {sys.executable}")
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
        failures += not check("redis-py", False, f"not installed in {sys.executable}")
        redis = None
    if redis and redis_url:
        try:
            client = redis.Redis.from_url(redis_url, socket_connect_timeout=5)
            pong = client.ping()
            info = client.info("server")
            failures += not check("Redis", bool(pong), info.get("redis_version", "unknown"))
        except Exception as exc:
            failures += not check("Redis", False, str(exc))

    print("--- stage 8 environment gates (do not fail stage 0) ---")
    missing_gates: list[str] = []

    flutter = find_flutter()
    if flutter:
        proc = subprocess.run([flutter, "--version"], capture_output=True, text=True)
        first = (proc.stdout or proc.stderr).splitlines()
        ok = gate("Flutter", proc.returncode == 0, first[0] if first else flutter)
    else:
        ok = gate("Flutter", False, "not installed - re-check before client stages (客户端阶段前再验)")
    if not ok:
        missing_gates.append("Flutter")

    android_home = os.environ.get("ANDROID_HOME", "")
    adb = android_home + "/platform-tools/adb"
    if not Path(adb).exists():
        adb = shutil.which("adb") or ""
    ok = gate("adb", bool(adb) and Path(adb).exists(), adb or "missing - re-check before stage 8")
    if not ok:
        missing_gates.append("adb")

    emulator = android_home + "/emulator/emulator"
    avds = []
    if Path(emulator).exists():
        proc = subprocess.run([emulator, "-list-avds"], capture_output=True, text=True)
        avds = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    ok = gate("Android emulator", bool(avds), ", ".join(avds) or "no AVD - re-check before stage 8")
    if not ok:
        missing_gates.append("Android emulator")

    try:
        sim = subprocess.run(["xcrun", "simctl", "list", "runtimes"], capture_output=True, text=True)
        sim_out = sim.stdout
    except FileNotFoundError:
        sim_out = ""
    runtime_lines = [
        line.strip()
        for line in sim_out.splitlines()
        if "iOS" in line and "unavailable" not in line.lower() and not line.startswith("==")
    ]
    ok = gate(
        "iOS Simulator runtime",
        bool(runtime_lines),
        runtime_lines[0] if runtime_lines else "no iOS runtime - re-check before stage 8",
    )
    if not ok:
        missing_gates.append("iOS Simulator runtime")

    print("RESULT", "PASS" if failures == 0 else f"FAIL {failures}")
    if missing_gates:
        print("DEFERRED", ", ".join(missing_gates), "(stage 8 gates; re-check before client stages)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
