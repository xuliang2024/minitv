"""Supplier review queue: /admin/v1/reviews.

Approve/reject moves only `dramas.review_state`; publishing stays a separate
action because the plan deliberately keeps "approved" and "live" apart. Every
write is a conditional UPDATE on `review_state = 'pending'`, so two operators
clicking at the same time cannot double-review a drama.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.account import mail
from app.modules.common.models import AuditLog
from app.modules.content.models import Drama, Episode
from app.modules.content.service import InvalidTransition, transition_drama, transition_episode
from app.modules.ops.models import ROLE_ADMIN, ROLE_OPS, AdminUser, Supplier
from app.modules.ops.router import require_admin

router = APIRouter(prefix="/admin/v1", tags=["reviews"])
log = logging.getLogger(__name__)

REVIEW_WRITERS = (ROLE_OPS, ROLE_ADMIN)
MAX_BATCH = 50


def _require_reviewer(user: AdminUser = Depends(require_admin)) -> AdminUser:
    if user.role not in REVIEW_WRITERS:
        raise HTTPException(status_code=403, detail="role cannot review content")
    return user


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=500)


class BatchIn(BaseModel):
    drama_ids: list[int] = Field(min_length=1, max_length=MAX_BATCH)
    decision: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=500)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _episode_count_subquery():
    return (
        select(Episode.drama_id.label("drama_id"), func.count(Episode.id).label("episode_count"))
        .group_by(Episode.drama_id)
        .subquery()
    )


def _row_out(drama: Drama, supplier: Supplier | None, episode_count: int) -> dict:
    return {
        "id": drama.id,
        "title": drama.title,
        "cover_url": drama.cover_url,
        "status": drama.status,
        "review_state": drama.review_state,
        "review_note": drama.review_note,
        "free_episode_count": drama.free_episode_count,
        "episode_count": episode_count,
        "submitted_at": _iso(drama.submitted_at),
        "reviewed_at": _iso(drama.reviewed_at),
        "supplier": (
            {"id": supplier.id, "name": supplier.name, "contact_email": supplier.contact_email}
            if supplier is not None
            else None
        ),
    }


@router.get("/reviews")
def list_reviews(
    state: str = Query(default="pending", pattern="^(pending|approved|rejected)$"),
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: AdminUser = Depends(_require_reviewer),
):
    counts = dict(
        db.execute(
            select(Drama.review_state, func.count(Drama.id)).group_by(Drama.review_state)
        ).all()
    )
    pending_publish = (
        db.scalar(
            select(func.count(Drama.id)).where(
                Drama.review_state == "approved", Drama.status.in_(["draft", "in_review"])
            )
        )
        or 0
    )

    ep = _episode_count_subquery()
    stmt = (
        select(Drama, Supplier, func.coalesce(ep.c.episode_count, 0))
        .outerjoin(Supplier, Supplier.id == Drama.supplier_id)
        .outerjoin(ep, ep.c.drama_id == Drama.id)
        .where(Drama.review_state == state)
    )
    if state == "approved":
        stmt = stmt.where(Drama.status.in_(["draft", "in_review"]))
    if q:
        stmt = stmt.where(Drama.title.ilike(f"%{q.strip()}%"))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(Drama.submitted_at.desc().nullslast(), Drama.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return {
        "items": [_row_out(drama, supplier, count) for drama, supplier, count in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "counts": {
            "pending": counts.get("pending", 0),
            "approved": pending_publish,
            "rejected": counts.get("rejected", 0),
        },
    }


def _notify(db: Session, drama: Drama, decision: str, note: str) -> None:
    """Best-effort email; a mail outage must never roll back a review."""
    supplier = db.get(Supplier, drama.supplier_id) if drama.supplier_id else None
    if supplier is None or not supplier.contact_email:
        return
    if not mail.configured():
        return
    verdict = "已通过" if decision == "approve" else "已驳回"
    body = f"《{drama.title}》{verdict}。"
    if note:
        body += f"\n\n运营备注：{note}"
    body += "\n\n登录 MiniTV 合作方后台查看详情。"
    try:
        mail.send_email(
            to=supplier.contact_email,
            subject=f"MiniTV 审核结果：{drama.title} {verdict}",
            text=body,
        )
    except mail.MailError as exc:
        log.warning("review email failed for drama %s: %s", drama.id, exc)


def _decide_one(
    db: Session, drama_id: int, decision: str, note: str, actor: AdminUser, batch_id: str
) -> str | None:
    """Return None on success, or a skip reason."""
    values = {
        "review_state": "approved" if decision == "approve" else "rejected",
        "review_note": note if decision == "reject" else None,
        "reviewed_at": datetime.now(timezone.utc),
        "reviewed_by": actor.id,
    }
    result = db.execute(
        update(Drama)
        .where(Drama.id == drama_id, Drama.review_state == "pending")
        .values(**values)
    )
    if result.rowcount == 0:
        drama = db.get(Drama, drama_id)
        if drama is None:
            return "剧集不存在"
        return f"当前状态 {drama.review_state}，不是待审核"
    drama = db.get(Drama, drama_id)
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor.id,
            action=f"drama_review_{decision}",
            target_type="drama",
            target_id=drama.id,
            payload={"note": note, "batch_id": batch_id, "supplier_id": drama.supplier_id},
        )
    )
    return None


@router.post("/reviews/batch")
def batch_decision(
    body: BatchIn,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(_require_reviewer),
):
    if body.decision == "reject" and not body.note.strip():
        raise HTTPException(status_code=422, detail="驳回必须填写原因")
    batch_id = uuid.uuid4().hex
    done: list[int] = []
    skipped: list[dict] = []
    noticed: list[Drama] = []
    for drama_id in dict.fromkeys(body.drama_ids):
        reason = _decide_one(db, drama_id, body.decision, body.note.strip(), actor, batch_id)
        if reason:
            skipped.append({"id": drama_id, "reason": reason})
        else:
            done.append(drama_id)
            noticed.append(db.get(Drama, drama_id))
    db.commit()
    for drama in noticed:
        _notify(db, drama, body.decision, body.note.strip())
    return {"done": len(done), "done_ids": done, "skipped": skipped, "batch_id": batch_id}


@router.post("/reviews/{drama_id}/decision")
def one_decision(
    drama_id: int,
    body: DecisionIn,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(_require_reviewer),
):
    if body.decision == "reject" and not body.note.strip():
        raise HTTPException(status_code=422, detail="驳回必须填写原因")
    reason = _decide_one(db, drama_id, body.decision, body.note.strip(), actor, uuid.uuid4().hex)
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    db.commit()
    drama = db.get(Drama, drama_id)
    _notify(db, drama, body.decision, body.note.strip())
    return {"id": drama.id, "review_state": drama.review_state, "review_note": drama.review_note}


@router.post("/reviews/{drama_id}/publish")
def publish_reviewed(
    drama_id: int,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(_require_reviewer),
):
    """Approve + publish in one operator click (drama and its episodes)."""
    drama = db.get(Drama, drama_id)
    if drama is None:
        raise HTTPException(status_code=404, detail="drama not found")
    if drama.supplier_id is None:
        raise HTTPException(status_code=400, detail="该接口只用于上剧方剧集")
    if drama.review_state != "approved":
        raise HTTPException(
            status_code=409,
            detail={"code": "not_approved", "message": "审核通过后才能上架"},
        )
    episodes = db.scalars(select(Episode).where(Episode.drama_id == drama.id)).all()
    if not episodes:
        raise HTTPException(status_code=409, detail="没有分集，无法上架")
    not_ready = [e.episode_number for e in episodes if e.status not in ("in_review", "published")]
    if not_ready:
        raise HTTPException(
            status_code=409,
            detail={"code": "episodes_not_ready", "message": f"第 {not_ready} 集未就绪"},
        )
    try:
        for episode in episodes:
            if episode.status == "in_review":
                transition_episode(db, episode, "published")
        if drama.status == "draft":
            transition_drama(db, drama, "in_review")
        transition_drama(db, drama, "published")
    except InvalidTransition as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    drama.published_at = datetime.now(timezone.utc)
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor.id,
            action="drama_partner_publish",
            target_type="drama",
            target_id=drama.id,
            payload={"episodes": len(episodes), "supplier_id": drama.supplier_id},
        )
    )
    db.commit()
    return {"id": drama.id, "status": drama.status, "episodes": len(episodes)}
