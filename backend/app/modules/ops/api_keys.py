"""API keys: long-lived credentials bound to a staff account.

Design notes live in docs/admin-api-key-remediation.md. The invariants this
module enforces:

* a key's effective power is ``min(bound account's current role, scopes)`` —
  the role is re-read on every request and is never cached in the key row;
* ``scopes`` can only narrow, never widen, so a typo cannot turn an ``ops`` key
  into a drama-deleting one;
* the plaintext is returned exactly once (create / rotate) and only its
  sha256 is persisted;
* Redis is used for rate limiting and for throttling ``last_used_at`` writes,
  but an outage there must not lock anyone out (both paths fail open).

Sessions (``minitv_admin_session:*``) are untouched: they are a separate
credential type resolved by ``ops.service.resolve_session_token``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import redis as redis_lib
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.common.models import AuditLog
from app.modules.ops import service as ops_service
from app.modules.ops.models import STATUS_ACTIVE, AdminApiKey, AdminUser

KEY_PREFIX = "mk_live_"
PREFIX_RANDOM_BYTES = 4  # 8 hex chars
KEY_PREFIX_LEN = len(KEY_PREFIX) + PREFIX_RANDOM_BYTES * 2  # 16

SCOPES = ("read", "write", "upload", "delete", "billing", "staff")
DEFAULT_SCOPES = ("read", "write")
# Suppliers are external tenants: they may read/write their own content and
# upload video, nothing else. `staff` would let them mint keys; `billing` and
# `delete` are platform-side powers.
PARTNER_SCOPES = ("read", "write", "upload")
DEFAULT_TTL_DAYS = 90
ALLOWED_TTL_DAYS = (30, 90, 180, 365)

# Only mutating methods land in api_key_events; GETs would swamp the table.
EVENT_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

RATE_LIMIT_ENV = "API_KEY_RATE_LIMIT"
DEFAULT_RATE_LIMIT = 600  # requests per minute per key

_STAFF_PATH = re.compile(r"^/admin/v1/(?:api-keys|staff)(?:/|$)")
_PARTNER_KEY_PATH = re.compile(r"^/partner/v1/(?:api-keys|auth/keys)(?:/|$)")
_PARTNER_UPLOAD_PATH = re.compile(
    r"^/partner/v1/(?:episodes/\d+/(?:upload-session|upload-headers)"
    r"|dramas/\d+/cover|dev-tus/[^/]+)$"
)
_SELF_CREDENTIAL = re.compile(r"^/admin/v1/auth/password$")
_BILLING_PATH = re.compile(
    r"^/admin/v1/(?:finance|plans|rewards|coins|membership|billing)(?:/|$)"
)
_DELETE_DRAMA = re.compile(r"^/admin/v1/dramas/\d+/delete$")
_DELETE_EPISODE = re.compile(r"^/admin/v1/episodes/\d+$")


# ---- errors ----


def _reject(status_code: int, code: str, message: str) -> HTTPException:
    """Structured detail so clients can act on ``code`` instead of parsing text."""
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}, headers=headers
    )


# ---- generation / hashing ----


def hash_secret(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Return ``(plaintext, key_prefix, sha256)``. Plaintext is never stored."""
    prefix = KEY_PREFIX + secrets.token_hex(PREFIX_RANDOM_BYTES)
    plaintext = f"{prefix}_{secrets.token_urlsafe(32)}"
    return plaintext, prefix, hash_secret(plaintext)


def looks_like_key(credential: str) -> bool:
    return credential.startswith(KEY_PREFIX)


# ---- scope policy ----


def scope_for_request(method: str, path: str) -> str:
    """Which scope a request needs. Order matters: first match wins."""
    if _SELF_CREDENTIAL.match(path):
        # Changing the account password must never be reachable with a key.
        return "staff"
    if _STAFF_PATH.match(path):
        return "staff"
    if _PARTNER_KEY_PATH.match(path):
        # A supplier key may read its own key list (read scope); every mutation
        # and the full listing stay session-only so a key can never mint another.
        if path == "/partner/v1/api-keys" and method.upper() == "GET":
            return "read"
        return "staff"
    if _PARTNER_UPLOAD_PATH.match(path):
        return "upload"
    if _BILLING_PATH.match(path):
        return "billing"
    if _DELETE_DRAMA.match(path):
        return "delete"
    if method.upper() == "DELETE" and _DELETE_EPISODE.match(path):
        return "delete"
    if method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return "read"
    return "write"


def granted_scopes(key: AdminApiKey, role: str | None = None) -> set[str]:
    """Empty ``scopes`` means "exactly what the account role allows".

    For suppliers that must still be intersected with ``PARTNER_SCOPES``:
    otherwise a key created without an explicit list would inherit ``staff``
    and could reach the key-management endpoints.
    """
    scopes = set(key.scopes or []) or set(SCOPES)
    if role == "partner":
        scopes &= set(PARTNER_SCOPES)
    return scopes


# ---- runtime checks ----


def _rate_limit() -> int:
    try:
        return int(os.environ.get(RATE_LIMIT_ENV, DEFAULT_RATE_LIMIT))
    except ValueError:
        return DEFAULT_RATE_LIMIT


def _enforce_rate_limit(key_id: int) -> None:
    limit = _rate_limit()
    if limit <= 0:
        return
    minute = int(time.time() // 60)
    bucket = f"minitv_key_rl:{key_id}:{minute}"
    try:
        pipe = ops_service.get_redis().pipeline()
        pipe.incr(bucket)
        pipe.expire(bucket, 70)
        used, _ = pipe.execute()
    except redis_lib.RedisError:
        return  # fail open: rate limiting is best-effort, auth must not depend on it
    if int(used) > limit:
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limited", "message": f"密钥超过 {limit} 次/分钟限制"},
            headers={"Retry-After": "60"},
        )


def _touch(db: Session, key: AdminApiKey) -> None:
    """Record last use at most once a minute to keep GETs cheap."""
    try:
        fresh = ops_service.get_redis().set(
            f"minitv_key_touch:{key.id}", "1", nx=True, ex=60
        )
    except redis_lib.RedisError:
        fresh = True
    if not fresh:
        return
    key.last_used_at = datetime.now(timezone.utc)
    db.commit()


def resolve_credential(db: Session, credential: str) -> tuple[AdminUser, AdminApiKey]:
    """Verify a ``mk_live_...`` credential and return its bound account.

    Split from :func:`authorize` so the caller can stamp the key on the request
    *before* the scope check — a denied write then still shows up in
    ``api_key_events`` (401s have no identity to record).
    """
    prefix = credential[:KEY_PREFIX_LEN]
    key = db.scalar(select(AdminApiKey).where(AdminApiKey.key_prefix == prefix))
    if key is None or not hmac.compare_digest(key.key_hash, hash_secret(credential)):
        raise _reject(401, "invalid_key", "API key 无效")
    if key.status != STATUS_ACTIVE:
        raise _reject(401, "key_revoked", "API key 已被撤销")
    now = datetime.now(timezone.utc)
    if key.expires_at is not None and key.expires_at <= now:
        raise _reject(401, "key_expired", "API key 已过期，请轮换")
    user = db.get(AdminUser, key.admin_user_id)
    if user is None or user.status != STATUS_ACTIVE:
        raise _reject(401, "account_disabled", "密钥绑定的账号已被禁用")
    _enforce_rate_limit(key.id)
    _touch(db, key)
    return user, key


def authorize(key: AdminApiKey, method: str, path: str, role: str | None = None) -> None:
    # A supplier credential is valid on exactly one surface. Fail here (before
    # any route-level role check) so the error is stable and greppable.
    if role == "partner" and not path.startswith("/partner/v1/"):
        raise _reject(403, "partner_scope_denied", "上剧方密钥只能访问合作方接口")
    needed = scope_for_request(method, path)
    granted = granted_scopes(key, role)
    # `write` implies `upload` so keys created before the upload scope existed
    # keep working; the reverse is not true (upload alone cannot edit metadata).
    if needed == "upload" and "write" in granted:
        return
    if needed not in granted:
        raise _reject(403, "scope_denied", f"该密钥缺少 {needed} 权限")


def authenticate(
    db: Session, credential: str, method: str, path: str
) -> tuple[AdminUser, AdminApiKey]:
    """Resolve + authorize in one call (kept for callers that do not need audit)."""
    user, key = resolve_credential(db, credential)
    authorize(key, method, path)
    return user, key


# ---- validation helpers ----


def normalise_scopes(
    raw: list[str] | None, allowed: tuple[str, ...] | None = None
) -> list[str]:
    """Validate a caller-supplied scope list.

    ``None`` means "use the default set". An **empty** list is rejected on
    purpose: ``granted_scopes`` treats an empty stored list as "follow the
    account role" (the legacy meaning for keys created before scopes existed),
    so accepting ``[]`` here would silently upgrade a read-only key to full
    account power — a privilege escalation through a plain PATCH.
    """
    if raw is None:
        return list(DEFAULT_SCOPES)
    if not raw:
        raise HTTPException(
            status_code=422,
            detail="至少选择一个权限；如需跟随账号角色请省略 scopes 字段",
        )
    pool = allowed or SCOPES
    unknown = [item for item in raw if item not in SCOPES]
    forbidden = [item for item in raw if item in SCOPES and item not in pool]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown scope: {', '.join(unknown)}（可选 {', '.join(SCOPES)}）",
        )
    if forbidden:
        raise HTTPException(
            status_code=422,
            detail=f"该角色不能授予 scope: {', '.join(forbidden)}（可选 {', '.join(pool)}）",
        )
    # dict.fromkeys keeps the caller's order while dropping duplicates.
    return list(dict.fromkeys(raw))


def _expiry(expires_in_days: int | None) -> datetime | None:
    if expires_in_days is None:
        return None
    if expires_in_days not in ALLOWED_TTL_DAYS:
        allowed = "/".join(str(day) for day in ALLOWED_TTL_DAYS)
        raise HTTPException(status_code=422, detail=f"有效期只能是 {allowed} 天，或 null 表示长期")
    return datetime.now(timezone.utc) + timedelta(days=expires_in_days)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _audit(db: Session, actor_id: int | None, action: str, key: AdminApiKey, extra: dict) -> None:
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor_id,
            action=action,
            target_type="api_key",
            target_id=key.id,
            # Never store the plaintext or the hash here.
            payload={
                "name": key.name,
                "user_id": key.admin_user_id,
                "scopes": list(key.scopes or []),
                "expires_at": key.expires_at.isoformat() if key.expires_at else None,
                **extra,
            },
        )
    )


def _insert(
    db: Session,
    *,
    admin_user_id: int,
    name: str,
    scopes: list[str],
    expires_at: datetime | None,
    actor_id: int | None,
) -> tuple[AdminApiKey, str]:
    plaintext, prefix, digest = generate_key()
    key = AdminApiKey(
        admin_user_id=admin_user_id,
        name=name,
        key_prefix=prefix,
        key_hash=digest,
        scopes=list(scopes),
        status=STATUS_ACTIVE,
        expires_at=expires_at,
        created_by=actor_id,
    )
    db.add(key)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "key_name_conflict", "message": "该账号下已有同名密钥"},
        ) from exc
    return key, plaintext


# ---- lifecycle ----


def create_key(
    db: Session,
    *,
    admin_user_id: int,
    name: str,
    scopes: list[str] | None,
    expires_in_days: int | None,
    actor_id: int | None,
) -> tuple[AdminApiKey, str]:
    user = db.get(AdminUser, admin_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="staff not found")
    if user.status != STATUS_ACTIVE:
        raise HTTPException(status_code=422, detail="账号已禁用，无法签发密钥")
    key, plaintext = _insert(
        db,
        admin_user_id=admin_user_id,
        name=name.strip(),
        scopes=normalise_scopes(scopes),
        expires_at=_expiry(expires_in_days),
        actor_id=actor_id,
    )
    _audit(db, actor_id, "api_key_create", key, {})
    db.commit()
    db.refresh(key)
    return key, plaintext


def update_key(
    db: Session,
    key: AdminApiKey,
    *,
    name: str | None,
    scopes: list[str] | None,
    expires_at: datetime | None,
    expires_at_set: bool,
    actor_id: int | None,
) -> AdminApiKey:
    if key.status != STATUS_ACTIVE:
        raise HTTPException(
            status_code=409,
            detail={"code": "key_revoked", "message": "已撤销的密钥不能再修改"},
        )
    changed: dict[str, object] = {}
    if name is not None and name.strip() != key.name:
        changed["name"] = name.strip()
        key.name = name.strip()
    if scopes is not None:
        clean = normalise_scopes(scopes)
        if clean != list(key.scopes or []):
            changed["scopes"] = clean
            key.scopes = clean
    if expires_at_set:
        value = _as_utc(expires_at)
        changed["expires_at"] = value.isoformat() if value else None
        key.expires_at = value
    if not changed:
        return key
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "key_name_conflict", "message": "该账号下已有同名密钥"},
        ) from exc
    _audit(db, actor_id, "api_key_update", key, {"changed": changed})
    db.commit()
    db.refresh(key)
    return key


def revoke_key(
    db: Session, key: AdminApiKey, *, reason: str | None, actor_id: int | None
) -> AdminApiKey:
    if key.status == "revoked":
        return key  # idempotent
    key.status = "revoked"
    key.revoked_at = datetime.now(timezone.utc)
    key.revoked_by = actor_id
    key.revoke_reason = (reason or "").strip() or None
    _audit(db, actor_id, "api_key_revoke", key, {"reason": key.revoke_reason})
    db.commit()
    db.refresh(key)
    return key


def rotate_key(
    db: Session, key: AdminApiKey, *, expires_in_days: int | None, actor_id: int | None
) -> tuple[AdminApiKey, str]:
    """Revoke the old key and mint a replacement in one transaction.

    The replacement keeps the name (the partial unique index excludes revoked
    rows, so there is no conflict) and the scopes unless explicitly changed.
    """
    if key.status != STATUS_ACTIVE:
        raise HTTPException(
            status_code=409,
            detail={"code": "key_revoked", "message": "已撤销的密钥不能轮换，请新建"},
        )
    key.status = "revoked"
    key.revoked_at = datetime.now(timezone.utc)
    key.revoked_by = actor_id
    key.revoke_reason = "rotated"
    db.flush()
    fresh, plaintext = _insert(
        db,
        admin_user_id=key.admin_user_id,
        name=key.name,
        scopes=list(key.scopes or []),
        expires_at=_expiry(expires_in_days),
        actor_id=actor_id,
    )
    _audit(db, actor_id, "api_key_rotate", fresh, {"replaced": key.id})
    db.commit()
    db.refresh(fresh)
    return fresh, plaintext


def delete_key(db: Session, key: AdminApiKey, *, actor_id: int | None) -> None:
    if key.last_used_at is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "key_in_use",
                "message": "密钥已被使用，是审计证据，请改为撤销",
            },
        )
    snapshot = {
        "name": key.name,
        "user_id": key.admin_user_id,
        "scopes": list(key.scopes or []),
    }
    db.delete(key)
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor_id,
            action="api_key_delete",
            target_type="api_key",
            target_id=None,
            payload=snapshot,
        )
    )
    db.commit()


# ---- read helpers used by the router ----


def key_state(key: AdminApiKey) -> str:
    """One of ``revoked`` / ``expired`` / ``expiring`` / ``active``."""
    if key.status != STATUS_ACTIVE:
        return "revoked"
    if key.expires_at is None:
        return "active"
    now = datetime.now(timezone.utc)
    if key.expires_at <= now:
        return "expired"
    if key.expires_at - now <= timedelta(days=14):
        return "expiring"
    return "active"
