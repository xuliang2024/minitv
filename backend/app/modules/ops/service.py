"""Ops service: staff authentication backed by Redis sessions.

Sessions live in Redis (short-lived state per the architecture's storage
split) under a dedicated key prefix with a TTL. Passwords are hashed with
stdlib pbkdf2_sha256 - no extra dependency.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import timedelta

import redis as redis_lib
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import load_env
from app.modules.ops.models import AdminUser

SESSION_PREFIX = "minitv_admin_session:"
SESSION_TTL = timedelta(hours=8)

_redis_client = None


class SessionStoreUnavailable(Exception):
    """Redis (the admin session store) is unreachable.

    The ops router maps this to HTTP 503 with an actionable message; a bare
    500 would hide the real cause (usually the local ``minitv-redis``
    container being down).
    """


def get_redis():
    global _redis_client
    if _redis_client is None:
        load_env()
        url = os.environ.get("REDIS_URL", "redis://:minitv_local@127.0.0.1:16379/0")
        _redis_client = redis_lib.Redis.from_url(url, decode_responses=True)
    return _redis_client


def reset_redis_for_tests() -> None:
    global _redis_client
    _redis_client = None


# ---- passwords ----

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 120_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, AttributeError):
        return False


# ---- sessions ----

def login(session: Session, *, username: str, password: str) -> tuple[AdminUser, str] | None:
    user = session.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is None or user.status != "active":
        return None
    if not verify_password(password, user.password_hash):
        return None
    token = secrets.token_urlsafe(32)
    try:
        get_redis().set(
            SESSION_PREFIX + token, str(user.id), ex=int(SESSION_TTL.total_seconds())
        )
    except redis_lib.RedisError as exc:
        raise SessionStoreUnavailable("redis session store is unavailable") from exc
    return user, token


def resolve_session_token(session: Session, token: str) -> AdminUser | None:
    try:
        user_id = get_redis().get(SESSION_PREFIX + token)
    except redis_lib.RedisError as exc:
        raise SessionStoreUnavailable("redis session store is unavailable") from exc
    if user_id is None:
        return None
    user = session.get(AdminUser, int(user_id))
    if user is None or user.status != "active":
        return None
    return user


def logout(token: str) -> None:
    try:
        get_redis().delete(SESSION_PREFIX + token)
    except redis_lib.RedisError as exc:
        raise SessionStoreUnavailable("redis session store is unavailable") from exc
