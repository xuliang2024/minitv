"""Database engine and declarative base for MiniTV."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Path | None = None) -> None:
    target = path if path is not None else ROOT / ".env"
    if not target.exists():
        return
    for raw in target.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


def database_url() -> str:
    load_env()
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql://minitv:minitv_local@127.0.0.1:15432/minitv",
    )
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    return url


class Base(DeclarativeBase):
    """Shared declarative base across all modules."""


def make_engine(url: str | None = None):
    return create_engine(url or database_url(), pool_pre_ping=True)


def make_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def get_db():
    factory = make_session_factory(get_engine())
    db = factory()
    try:
        yield db
    finally:
        db.close()
