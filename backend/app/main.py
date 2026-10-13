"""Local health app for the MiniTV test environment."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import redis
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[2]


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


load_env()
app = FastAPI(title="MiniTV Local", version="0.0.0")


@app.get("/health")
def health() -> dict:
    db_url = os.environ["DATABASE_URL"]
    redis_url = os.environ["REDIS_URL"]
    with psycopg.connect(db_url, connect_timeout=3) as conn:
        pg_version = conn.execute("SHOW server_version").fetchone()[0]
    client = redis.Redis.from_url(redis_url, socket_connect_timeout=3)
    client.ping()
    return {
        "status": "ok",
        "env": os.environ.get("APP_ENV", "local"),
        "postgres": pg_version,
        "redis": client.info("server").get("redis_version"),
    }
