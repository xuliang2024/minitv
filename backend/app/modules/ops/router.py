"""Ops router: /admin/v1 - staff auth, drama management, tag library, search."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.common.models import AuditLog
from app.modules.content.models import Drama, DramaTag, Episode, Tag
from app.modules.ops import api_keys as api_key_service
from app.modules.ops import service as ops_service
from app.modules.ops.models import (
    CONTENT_WRITER_ROLES,
    ROLE_ADMIN,
    ROLE_OPS,
    ROLES,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUSES,
    AdminUser,
)

router = APIRouter(prefix="/admin/v1", tags=["admin"])


# ---- auth dependencies ----


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"code": "missing_credential", "message": "缺少 Authorization 头"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return auth[len("Bearer "):]


SESSION_STORE_503_DETAIL = (
    "会话存储不可用（Redis 未连接）。请先启动 minitv-redis："
    "在仓库根目录执行 docker compose up -d，然后重试。"
)


def _session_store_503() -> HTTPException:
    return HTTPException(status_code=503, detail=SESSION_STORE_503_DETAIL)


def require_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    """Accept either a staff session token or an API key.

    API keys are bound to an ``admin_users`` row and resolved into that account,
    so every downstream dependency (role checks, audit ``actor_id``) keeps
    working unchanged. The session path is deliberately untouched.
    """
    credential = _bearer_token(request)
    if api_key_service.looks_like_key(credential):
        user, key = api_key_service.resolve_credential(db, credential)
        # Stamp the key before the scope check: a denied write must still be
        # recorded in api_key_events (the middleware reads these after response).
        request.state.api_key_id = key.id
        request.state.api_key_staff_id = user.id
        api_key_service.authorize(key, request.method, request.url.path)
        return user
    try:
        user = ops_service.resolve_session_token(db, credential)
    except ops_service.SessionStoreUnavailable as exc:
        raise _session_store_503() from exc
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_session", "message": "会话无效或已过期"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_content_writer(user: AdminUser = Depends(require_admin)) -> AdminUser:
    if user.role not in CONTENT_WRITER_ROLES:
        raise HTTPException(status_code=403, detail=f"role '{user.role}' cannot write content")
    return user


def require_admin_role(user: AdminUser = Depends(require_admin)) -> AdminUser:
    """Super-admin only: delete drama, price/billing changes, coin adjust, staff."""
    if user.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="admin only")
    return user


# ---- schemas ----


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str


class LoginIn(BaseModel):
    username: str
    password: str


class StaffCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(default="", max_length=64)
    role: str = Field(default=ROLE_OPS)
    password: str = Field(min_length=8, max_length=128)


class StaffUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    role: str | None = None


class StaffStatusIn(BaseModel):
    status: str
    reason: str | None = Field(default=None, max_length=200)


class StaffPasswordIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class PasswordChangeIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class DramaCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class DramaUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    synopsis: str | None = Field(default=None, max_length=20000)
    cover_url: str | None = Field(default=None, max_length=500)
    free_episode_count: int | None = Field(default=None, ge=0)
    coin_price_per_episode: int | None = Field(default=None, ge=0)
    tag_ids: list[int] | None = None


class TagIn(BaseModel):
    name_en: str = Field(min_length=1, max_length=64)
    name_zh: str = Field(default="", max_length=64)
    category: str = Field(default="", max_length=32)
    status: str = Field(default="enabled", max_length=16)


class TagUpdateIn(BaseModel):
    name_en: str | None = Field(default=None, min_length=1, max_length=64)
    name_zh: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, max_length=16)


# ---- serializers ----


def _tag_out(db: Session, tag: Tag) -> dict:
    drama_count = (
        db.scalar(select(func.count(DramaTag.drama_id)).where(DramaTag.tag_id == tag.id)) or 0
    )
    return {
        "id": tag.id,
        "name_en": tag.name_en,
        "name_zh": tag.name_zh,
        "category": tag.category,
        "status": tag.status,
        "drama_count": drama_count,
    }


def _drama_out(db: Session, drama: Drama) -> dict:
    tag_rows = db.scalars(
        select(Tag).join(DramaTag, DramaTag.tag_id == Tag.id).where(DramaTag.drama_id == drama.id)
    ).all()
    episode_count = (
        db.scalar(select(func.count(Episode.id)).where(Episode.drama_id == drama.id)) or 0
    )
    return {
        "id": drama.id,
        "title": drama.title,
        "synopsis": drama.synopsis,
        "cover_url": drama.cover_url,
        "status": drama.status,
        "free_episode_count": drama.free_episode_count,
        "coin_price_per_episode": drama.coin_price_per_episode,
        "tags": [{"id": t.id, "name_en": t.name_en, "name_zh": t.name_zh} for t in tag_rows],
        "episode_count": episode_count,
        # Lifetime starts reported by the player; see playback.record_event.
        "play_count": drama.play_count,
        "updated_at": drama.updated_at.isoformat() if drama.updated_at else None,
    }


def _get_drama_or_404(db: Session, drama_id: int) -> Drama:
    drama = db.get(Drama, drama_id)
    if drama is None:
        raise HTTPException(status_code=404, detail="drama not found")
    return drama


# ---- auth ----


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    try:
        result = ops_service.login(db, username=body.username, password=body.password)
    except ops_service.SessionStoreUnavailable as exc:
        raise _session_store_503() from exc
    if result is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    user, token = result
    return {"token": token, "user": AdminUserOut.model_validate(user).model_dump()}


@router.get("/auth/me")
def me(user: AdminUser = Depends(require_admin)):
    return AdminUserOut.model_validate(user).model_dump()


@router.post("/auth/logout")
def logout(request: Request, user: AdminUser = Depends(require_admin)):
    try:
        ops_service.logout(_bearer_token(request))
    except ops_service.SessionStoreUnavailable as exc:
        raise _session_store_503() from exc
    return {"ok": True}


@router.post("/auth/password")
def change_own_password(
    body: PasswordChangeIn,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_admin),
):
    """Self-service password change. Requires the current password.

    Every session of this account is revoked on success — including the one
    making this call — so the client must send the user back to the login page
    and sign in again with the new password.
    """
    if not ops_service.verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if body.new_password == body.old_password:
        raise HTTPException(status_code=422, detail="新密码不能与原密码相同")
    _validate_password(body.new_password, user.username)
    killed = _revoke_or_503(user.id)
    user.password_hash = ops_service.hash_password(body.new_password)
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=user.id,
            action="staff_password_change",
            target_type="admin_user",
            target_id=user.id,
            payload={"revoked_sessions": killed},
        )
    )
    db.commit()
    return {"revoked_sessions": killed}


# ---- staff accounts ----
#
# Accounts are never deleted. Disabling is enforced on every request by
# `resolve_session_token`, which re-reads the row and rejects a non-active
# status, so a disabled account loses access immediately rather than at TTL.

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _staff_out(row: AdminUser) -> dict:
    """Never serialise password_hash — not even a prefix."""
    return {
        "id": row.id,
        "username": row.username,
        "display_name": row.display_name,
        "role": row.role,
        "status": row.status,
        "disable_reason": row.disable_reason,
        "last_login_at": _iso(row.last_login_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _get_staff_or_404(db: Session, staff_id: int) -> AdminUser:
    row = db.get(AdminUser, staff_id)
    if row is None:
        raise HTTPException(status_code=404, detail="staff not found")
    return row


def _clean_username(raw: str) -> str:
    username = raw.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(
            status_code=422,
            detail="账号需为 3–32 位小写字母、数字或 . _ -，并以字母或数字开头",
        )
    return username


def _validate_role(role: str) -> str:
    if role not in ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {', '.join(ROLES)}")
    return role


def _validate_password(password: str, username: str) -> str:
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="密码至少 8 位")
    if len(password) > 128:
        raise HTTPException(status_code=422, detail="密码最多 128 位")
    if password != password.strip():
        raise HTTPException(status_code=422, detail="密码首尾不能是空格")
    if password.lower() == username.lower():
        raise HTTPException(status_code=422, detail="密码不能与账号相同")
    return password


def _revoke_or_503(user_id: int) -> int:
    try:
        return ops_service.revoke_user_sessions(user_id)
    except ops_service.SessionStoreUnavailable as exc:
        raise _session_store_503() from exc


def _ensure_admin_remains(db: Session, row: AdminUser) -> None:
    """Belt and braces for the "never leave the back office without admins" rule.

    The reachable paths are already blocked (nobody may disable or demote
    themselves), but a future relaxation of those rules must not be able to
    strand the last active admin.
    """
    remaining = (
        db.scalar(
            select(func.count(AdminUser.id)).where(
                AdminUser.role == ROLE_ADMIN,
                AdminUser.status == STATUS_ACTIVE,
                AdminUser.id != row.id,
            )
        )
        or 0
    )
    if remaining == 0:
        raise HTTPException(status_code=409, detail="至少要保留一个启用状态的管理员")


@router.get("/staff")
def list_staff(
    q: str | None = None,
    role: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_admin_role),
):
    stmt = select(AdminUser)
    needle = (q or "").strip()
    if needle:
        pattern = f"%{needle}%"
        stmt = stmt.where(
            or_(AdminUser.username.ilike(pattern), AdminUser.display_name.ilike(pattern))
        )
    if role:
        stmt = stmt.where(AdminUser.role == role)
    if status:
        stmt = stmt.where(AdminUser.status == status)
    rows = db.scalars(stmt.order_by(AdminUser.id).limit(200)).all()
    return {"items": [_staff_out(row) for row in rows]}


@router.post("/staff", status_code=201)
def create_staff(
    body: StaffCreateIn,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_admin_role),
):
    username = _clean_username(body.username)
    _validate_role(body.role)
    _validate_password(body.password, username)
    if db.scalar(select(AdminUser.id).where(AdminUser.username == username)) is not None:
        raise HTTPException(status_code=409, detail="账号已存在")
    row = AdminUser(
        username=username,
        display_name=body.display_name.strip(),
        role=body.role,
        password_hash=ops_service.hash_password(body.password),
        status=STATUS_ACTIVE,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:  # concurrent insert of the same username
        db.rollback()
        raise HTTPException(status_code=409, detail="账号已存在") from exc
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor.id,
            action="staff_create",
            target_type="admin_user",
            target_id=row.id,
            payload={"username": row.username, "role": row.role},
        )
    )
    db.commit()
    db.refresh(row)
    return _staff_out(row)


@router.patch("/staff/{staff_id}")
def update_staff(
    staff_id: int,
    body: StaffUpdateIn,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_admin_role),
):
    row = _get_staff_or_404(db, staff_id)
    before: dict = {}
    changed: list[str] = []

    if body.display_name is not None:
        display_name = body.display_name.strip()
        if display_name != row.display_name:
            before["display_name"] = row.display_name
            row.display_name = display_name
            changed.append("display_name")

    if body.role is not None and body.role != row.role:
        _validate_role(body.role)
        if row.id == actor.id:
            raise HTTPException(status_code=400, detail="不能修改自己的角色")
        if row.role == ROLE_ADMIN and row.status == STATUS_ACTIVE:
            _ensure_admin_remains(db, row)
        before["role"] = row.role
        row.role = body.role
        changed.append("role")

    if not changed:
        return {"before": {}, "after": _staff_out(row)}

    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor.id,
            action="staff_update",
            target_type="admin_user",
            target_id=row.id,
            payload={
                "changed": changed,
                "from": before,
                "to": {key: getattr(row, key) for key in changed},
            },
        )
    )
    db.commit()
    db.refresh(row)
    return {"before": before, "after": _staff_out(row)}


@router.post("/staff/{staff_id}/status")
def set_staff_status(
    staff_id: int,
    body: StaffStatusIn,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_admin_role),
):
    if body.status not in STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(STATUSES)}")
    row = _get_staff_or_404(db, staff_id)
    if body.status == row.status:
        return {
            "id": row.id,
            "status": row.status,
            "disable_reason": row.disable_reason,
            "revoked_sessions": 0,
        }

    reason = (body.reason or "").strip() or None
    killed = 0
    if body.status == STATUS_DISABLED:
        if row.id == actor.id:
            raise HTTPException(status_code=400, detail="不能禁用自己")
        if row.role == ROLE_ADMIN and row.status == STATUS_ACTIVE:
            _ensure_admin_remains(db, row)
        # Revoke before commit: if Redis is down the request fails and the
        # account stays usable, which is easier to explain than a disabled
        # account with a live session.
        killed = _revoke_or_503(row.id)

    row.status = body.status
    row.disable_reason = reason if body.status == STATUS_DISABLED else None
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor.id,
            action="staff_disable" if body.status == STATUS_DISABLED else "staff_enable",
            target_type="admin_user",
            target_id=row.id,
            payload={"reason": reason, "revoked_sessions": killed},
        )
    )
    db.commit()
    return {
        "id": row.id,
        "status": row.status,
        "disable_reason": row.disable_reason,
        "revoked_sessions": killed,
    }


@router.post("/staff/{staff_id}/password", status_code=204)
def reset_staff_password(
    staff_id: int,
    body: StaffPasswordIn,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_admin_role),
):
    row = _get_staff_or_404(db, staff_id)
    _validate_password(body.new_password, row.username)
    killed = _revoke_or_503(row.id)
    row.password_hash = ops_service.hash_password(body.new_password)
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor.id,
            action="staff_password_reset",
            target_type="admin_user",
            target_id=row.id,
            payload={"revoked_sessions": killed},
        )
    )
    db.commit()
    return Response(status_code=204)


# ---- dramas ----


@router.get("/dramas")
def list_dramas(
    status: str | None = None,
    q: str | None = None,
    tag_id: int | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    stmt = select(Drama)
    count_stmt = select(func.count(Drama.id))
    if status:
        stmt = stmt.where(Drama.status == status)
        count_stmt = count_stmt.where(Drama.status == status)
    if q:
        stmt = stmt.where(Drama.title.ilike(f"%{q}%"))
        count_stmt = count_stmt.where(Drama.title.ilike(f"%{q}%"))
    if tag_id is not None:
        tagged = select(DramaTag.drama_id).where(DramaTag.tag_id == tag_id)
        stmt = stmt.where(Drama.id.in_(tagged))
        count_stmt = count_stmt.where(Drama.id.in_(tagged))
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(
        stmt.order_by(Drama.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    # 全库按状态计数，不受分页 / 筛选影响，给列表页顶部的统计条用。
    counts = {
        row.status: row.n
        for row in db.execute(
            select(Drama.status, func.count(Drama.id).label("n")).group_by(Drama.status)
        )
    }
    status_counts = {s: int(counts.get(s, 0)) for s in ("draft", "in_review", "published", "unpublished")}
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "status_counts": {"all": sum(status_counts.values()), **status_counts},
        "items": [_drama_out(db, d) for d in rows],
    }


@router.post("/dramas")
def create_drama(
    body: DramaCreateIn,
    user: AdminUser = Depends(require_content_writer),
    db: Session = Depends(get_db),
):
    drama = Drama(title=body.title)  # new dramas always start as drafts
    db.add(drama)
    db.commit()
    return _drama_out(db, drama)


@router.get("/dramas/{drama_id}")
def get_drama(
    drama_id: int,
    user: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _drama_out(db, _get_drama_or_404(db, drama_id))


@router.patch("/dramas/{drama_id}")
def update_drama(
    drama_id: int,
    body: DramaUpdateIn,
    user: AdminUser = Depends(require_content_writer),
    db: Session = Depends(get_db),
):
    drama = _get_drama_or_404(db, drama_id)
    data = body.model_dump(exclude_unset=True)
    tag_ids = data.pop("tag_ids", None)
    for field, value in data.items():
        setattr(drama, field, value)
    if tag_ids is not None:
        tags = [db.get(Tag, tid) for tid in tag_ids]
        if any(t is None for t in tags):
            db.rollback()
            raise HTTPException(status_code=422, detail="unknown tag id in tag_ids")
        db.query(DramaTag).filter(DramaTag.drama_id == drama.id).delete()
        for tid in dict.fromkeys(tag_ids):  # tolerate duplicate ids from clients
            db.add(DramaTag(drama_id=drama.id, tag_id=tid))
    db.commit()
    db.refresh(drama)
    return _drama_out(db, drama)


# ---- tags ----


@router.get("/tags")
def list_tags(
    user: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(Tag).order_by(Tag.name_en)).all()
    return {"items": [_tag_out(db, t) for t in rows]}


@router.post("/tags")
def create_tag(
    body: TagIn,
    user: AdminUser = Depends(require_content_writer),
    db: Session = Depends(get_db),
):
    existing = db.scalar(select(Tag).where(Tag.name_en == body.name_en))
    if existing is not None:
        raise HTTPException(status_code=409, detail="tag name_en already exists")
    tag = Tag(**body.model_dump())
    db.add(tag)
    db.commit()
    return _tag_out(db, tag)


@router.patch("/tags/{tag_id}")
def update_tag(
    tag_id: int,
    body: TagUpdateIn,
    user: AdminUser = Depends(require_content_writer),
    db: Session = Depends(get_db),
):
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="tag not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(tag, field, value)
    db.commit()
    db.refresh(tag)
    return _tag_out(db, tag)


@router.delete("/tags/{tag_id}")
def delete_tag(
    tag_id: int,
    user: AdminUser = Depends(require_content_writer),
    db: Session = Depends(get_db),
):
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="tag not found")
    affected = db.scalars(
        select(Drama).join(DramaTag, DramaTag.drama_id == Drama.id).where(DramaTag.tag_id == tag_id)
    ).all()
    if affected:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "message": "tag is in use; resolve references before deleting",
                "affected_dramas": [{"id": d.id, "title": d.title} for d in affected],
            },
        )
    db.delete(tag)
    db.commit()
    return {"ok": True}


# ---- top search: dramas + users (users land in a later stage) ----


@router.get("/search")
def search(
    q: str = Query(min_length=1),
    user: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    dramas = db.scalars(
        select(Drama).where(Drama.title.ilike(f"%{q}%")).order_by(Drama.updated_at.desc()).limit(20)
    ).all()
    return {
        "dramas": [{"id": d.id, "title": d.title, "status": d.status} for d in dramas],
        "users": [],
    }
