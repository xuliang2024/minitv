"""Partner surface: /partner/v1 — the only API a 上剧方 (supplier) may call.

Every query is scoped by the caller's `supplier_id`; there is no code path that
returns another tenant's rows. P1 ships read-only endpoints so the isolation can
be verified before suppliers are allowed to write anything (P2 adds creating
dramas and upload sessions).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.content.models import Drama, Episode
from app.modules.ops import service as ops_service
from app.modules.ops.models import ROLE_PARTNER, STATUS_ACTIVE, AdminUser, Supplier

router = APIRouter(prefix="/partner/v1", tags=["partner"])


class PartnerPasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"code": "missing_credential", "message": "缺少 Authorization 头"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return auth[len("Bearer "):]


def require_partner(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    credential = _bearer_token(request)
    try:
        user = ops_service.resolve_session_token(db, credential)
    except ops_service.SessionStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail="会话存储不可用") from exc
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_session", "message": "会话无效或已过期"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.role != ROLE_PARTNER or user.supplier_id is None:
        raise HTTPException(
            status_code=403,
            detail={"code": "partner_only", "message": "该接口仅对上剧方开放"},
        )
    supplier = db.get(Supplier, user.supplier_id)
    if supplier is None or supplier.status != STATUS_ACTIVE:
        raise HTTPException(
            status_code=403,
            detail={"code": "supplier_disabled", "message": "供应商已停用"},
        )
    return user


def _partner_drama_or_404(db: Session, user: AdminUser, drama_id: int) -> Drama:
    """Ownership check first, state check later.

    A partner asking for someone else's id must get 404, never 403: a 403 would
    confirm that the drama exists.
    """
    row = db.scalar(
        select(Drama).where(
            Drama.id == drama_id, Drama.supplier_id == user.supplier_id
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="drama not found")
    return row


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _drama_out(db: Session, drama: Drama, *, with_episodes: bool = False) -> dict:
    episode_count = (
        db.scalar(select(func.count(Episode.id)).where(Episode.drama_id == drama.id)) or 0
    )
    payload = {
        "id": drama.id,
        "title": drama.title,
        "synopsis": drama.synopsis,
        "cover_url": drama.cover_url,
        "status": drama.status,
        "review_state": drama.review_state,
        "review_note": drama.review_note,
        "free_episode_count": drama.free_episode_count,
        "episode_count": episode_count,
        "submitted_at": _iso(drama.submitted_at),
        "reviewed_at": _iso(drama.reviewed_at),
        "updated_at": _iso(drama.updated_at),
    }
    if with_episodes:
        episodes = db.scalars(
            select(Episode)
            .where(Episode.drama_id == drama.id)
            .order_by(Episode.episode_number)
        ).all()
        payload["episodes"] = [
            {
                "id": item.id,
                "episode_number": item.episode_number,
                "title": item.title,
                "status": item.status,
                "duration_seconds": item.duration_seconds,
            }
            for item in episodes
        ]
    return payload


@router.get("/me")
def me(user: AdminUser = Depends(require_partner), db: Session = Depends(get_db)):
    supplier = db.get(Supplier, user.supplier_id)
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "supplier": {
            "id": supplier.id,
            "name": supplier.name,
            "status": supplier.status,
            "revenue_share_bps": supplier.revenue_share_bps,
        },
    }


@router.get("/dramas")
def list_dramas(
    status: str | None = None,
    review_state: str | None = None,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    stmt = select(Drama).where(Drama.supplier_id == user.supplier_id)
    if status:
        stmt = stmt.where(Drama.status == status)
    if review_state:
        stmt = stmt.where(Drama.review_state == review_state)
    rows = db.scalars(stmt.order_by(Drama.updated_at.desc()).limit(200)).all()
    return {"items": [_drama_out(db, row) for row in rows]}


@router.get("/dramas/{drama_id}")
def get_drama(
    drama_id: int,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    row = _partner_drama_or_404(db, user, drama_id)
    return _drama_out(db, row, with_episodes=True)


@router.post("/auth/password")
def change_password(
    body: PartnerPasswordIn,
    request: Request,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    """Same contract as the staff endpoint: verify old password, revoke all."""
    if not ops_service.verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if body.new_password == body.old_password:
        raise HTTPException(status_code=422, detail="新密码不能与原密码相同")
    killed = ops_service.revoke_user_sessions(user.id)
    user.password_hash = ops_service.hash_password(body.new_password)
    db.commit()
    return {"revoked_sessions": killed}


@router.post("/auth/logout")
def logout(request: Request, user: AdminUser = Depends(require_partner)):
    token = _bearer_token(request)
    try:
        ops_service.logout(token)
    except ops_service.SessionStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail="会话存储不可用") from exc
    return {"ok": True}
