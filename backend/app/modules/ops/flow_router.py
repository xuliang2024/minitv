"""Admin routes for stages 3 through 14."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.account.models import User
from app.modules.account.service import save_reward_config
from app.modules.analytics.service import dashboard as build_dashboard
from app.modules.billing.models import Order, Subscription
from app.modules.billing.service import BillingError, change_price, list_current_plans
from app.modules.common.models import AuditLog
from app.modules.content.models import Drama, DramaHomeGroup, Episode, HomeGroup
from app.modules.content.publish_service import (
    PublishError,
    add_to_group,
    apply_due_schedules,
    change_drama_status,
    delete_drama,
    publish_episode,
    schedule_publish,
)
from app.modules.content.upload_service import UploadRejected, create_upload_session
from app.modules.ops.config_models import (
    Banner,
    ChannelTab,
    CopyEntry,
    HotSearch,
    LegalPage,
    MarketingStat,
    StoreLink,
    Testimonial,
)
from app.modules.social.models import Comment, PushRecord
from app.modules.ops.models import AdminUser
from app.modules.ops.router import require_admin, require_content_writer
from app.modules.playback.service import authorize
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount

router = APIRouter(prefix="/admin/v1", tags=["admin-flow"])


def _drama_or_404(db: Session, drama_id: int) -> Drama:
    drama = db.get(Drama, drama_id)
    if drama is None:
        raise HTTPException(status_code=404, detail="drama not found")
    return drama


class EpisodeIn(BaseModel):
    episode_number: int = Field(ge=1)
    title: str = ""


class BulkIn(BaseModel):
    count: int = Field(ge=1, le=200)


class UploadIn(BaseModel):
    filename: str
    mime: str
    size_bytes: int
    width: int
    height: int


class TitleIn(BaseModel):
    title: str


class StatusIn(BaseModel):
    to_status: str


class ScheduleIn(BaseModel):
    at: datetime


class DeleteIn(BaseModel):
    confirm: bool = False


class GroupIn(BaseModel):
    name: str
    position: int = 0


class GroupAddIn(BaseModel):
    drama_id: int
    confirm: bool = False


class PositionIn(BaseModel):
    position: int


class CopyIn(BaseModel):
    key: str
    en: str = ""
    zh: str = ""


class RewardIn(BaseModel):
    code: str
    amounts: list[int]


class PriceIn(BaseModel):
    price_cents: int
    benefits: dict = Field(default_factory=dict)


class AdjustIn(BaseModel):
    delta: int
    reason: str = Field(min_length=1)


class CommentModIn(BaseModel):
    status: str


def require_admin_role(user: AdminUser = Depends(require_admin)) -> AdminUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return user


@router.post("/dramas/{drama_id}/episodes")
def create_episode(drama_id: int, body: EpisodeIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    _drama_or_404(db, drama_id)
    row = Episode(drama_id=drama_id, episode_number=body.episode_number, title=body.title or f"Episode {body.episode_number}")
    db.add(row)
    db.commit()
    return {"id": row.id, "episode_number": row.episode_number, "status": row.status}


@router.post("/dramas/{drama_id}/episodes/bulk")
def bulk_episodes(drama_id: int, body: BulkIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    _drama_or_404(db, drama_id)
    existing = set(db.scalars(select(Episode.episode_number).where(Episode.drama_id == drama_id)))
    created = []
    for number in range(1, body.count + 1):
        if number in existing:
            continue
        row = Episode(drama_id=drama_id, episode_number=number, title=f"Episode {number}")
        db.add(row)
        created.append(number)
    db.commit()
    return {"created": created, "total": body.count}


@router.get("/dramas/{drama_id}/episodes")
def list_episodes(
    drama_id: int,
    status: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_admin),
):
    stmt = select(Episode).where(Episode.drama_id == drama_id)
    if status:
        stmt = stmt.where(Episode.status == status)
    if q:
        stmt = stmt.where(or_(Episode.title.ilike(f"%{q}%"), Episode.episode_number == _maybe_int(q)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Episode.episode_number).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": row.id,
                "episode_number": row.episode_number,
                "title": row.title,
                "status": row.status,
                "duration_seconds": row.duration_seconds,
                "bunny_video_id": row.bunny_video_id,
            }
            for row in rows
        ],
    }


def _maybe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return -1


@router.post("/episodes/{episode_id}/upload-session")
def upload_session(episode_id: int, body: UploadIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="episode not found")
    try:
        return create_upload_session(
            db,
            episode=episode,
            filename=body.filename,
            mime=body.mime,
            size_bytes=body.size_bytes,
            width=body.width,
            height=body.height,
        )
    except UploadRejected as exc:
        raise HTTPException(status_code=422, detail=exc.reason) from exc


@router.patch("/episodes/{episode_id}")
def rename_episode(episode_id: int, body: TitleIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="episode not found")
    episode.title = body.title
    db.commit()
    return {"id": episode.id, "title": episode.title, "status": episode.status}


@router.post("/episodes/{episode_id}/publish")
def publish_one(episode_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="episode not found")
    try:
        publish_episode(db, episode)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"status": episode.status}


@router.post("/dramas/{drama_id}/status")
def drama_status(drama_id: int, body: StatusIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    drama = _drama_or_404(db, drama_id)
    try:
        change_drama_status(db, drama, body.to_status, actor_id=user.id)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"status": drama.status}


@router.post("/dramas/{drama_id}/schedule")
def drama_schedule(drama_id: int, body: ScheduleIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    drama = _drama_or_404(db, drama_id)
    schedule_publish(db, drama, body.at, actor_id=user.id)
    return {"scheduled_publish_at": drama.scheduled_publish_at.isoformat() if drama.scheduled_publish_at else None}


@router.post("/schedules/apply")
def apply_schedules(db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    return {"published": apply_due_schedules(db)}


@router.post("/dramas/{drama_id}/delete")
def remove_drama(drama_id: int, body: DeleteIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin_role)):
    drama = _drama_or_404(db, drama_id)
    try:
        return delete_drama(db, drama, confirm=body.confirm, actor_id=user.id)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/dramas/{drama_id}/preview")
def preview(drama_id: int, episode_id: int | None = None, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    drama = _drama_or_404(db, drama_id)
    episode = db.scalar(select(Episode).where(Episode.drama_id == drama.id).order_by(Episode.episode_number))
    if episode_id:
        episode = db.get(Episode, episode_id)
    episodes = db.scalars(select(Episode).where(Episode.drama_id == drama.id).order_by(Episode.episode_number)).all()
    playback = None
    if episode is not None and drama.status == "published" and episode.status == "published":
        playback = authorize(db, drama_id=drama.id, episode_id=episode.id, user_id=None)
    return {
        "preview_kind": "player",
        "drama_id": drama.id,
        "title": drama.title,
        "current_episode": None
        if episode is None
        else {
            "id": episode.id,
            "number": episode.episode_number,
            "title": episode.title,
            "subtitle_url": episode.subtitle_url,
        },
        "episodes": [{"id": ep.id, "number": ep.episode_number, "status": ep.status} for ep in episodes],
        "playback": playback,
    }


@router.post("/groups")
def create_group(body: GroupIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = HomeGroup(name=body.name, position=body.position)
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.get("/groups")
def list_groups(db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    rows = db.scalars(select(HomeGroup).order_by(HomeGroup.position)).all()
    return {"items": [{"id": row.id, "name": row.name, "position": row.position} for row in rows]}


@router.patch("/groups/{group_id}")
def move_group(group_id: int, body: PositionIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    group = db.get(HomeGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="group not found")
    group.position = body.position
    db.commit()
    return {"id": group.id, "position": group.position}


@router.post("/groups/{group_id}/dramas")
def group_add(group_id: int, body: GroupAddIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    group = db.get(HomeGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="group not found")
    try:
        return add_to_group(db, group, body.drama_id, confirm=body.confirm)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.patch("/groups/{group_id}/dramas/{drama_id}")
def group_position(group_id: int, drama_id: int, body: PositionIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    link = db.get(DramaHomeGroup, (group_id, drama_id))
    if link is None:
        raise HTTPException(status_code=404, detail="not in group")
    link.position_in_group = body.position
    db.commit()
    return {"position_in_group": link.position_in_group}


@router.post("/copies")
def save_copy(body: CopyIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    if not body.en.strip():
        raise HTTPException(status_code=422, detail="en is required")
    row = db.scalar(select(CopyEntry).where(CopyEntry.key == body.key))
    if row is None:
        row = CopyEntry(key=body.key, en=body.en, zh=body.zh)
        db.add(row)
    else:
        row.en = body.en
        row.zh = body.zh
    db.commit()
    return {"key": row.key}


@router.get("/copies")
def list_copies(db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    rows = db.scalars(select(CopyEntry).order_by(CopyEntry.key)).all()
    return {"items": [{"key": row.key, "en": row.en, "zh": row.zh} for row in rows]}


@router.post("/tabs")
def save_tab(code: str, title_en: str, title_zh: str = "", position: int = 0, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = db.scalar(select(ChannelTab).where(ChannelTab.code == code))
    if row is None:
        row = ChannelTab(code=code, title_en=title_en, title_zh=title_zh, position=position)
        db.add(row)
    else:
        row.position = position
        row.title_en = title_en
        row.title_zh = title_zh
    db.commit()
    return {"id": row.id, "position": row.position}


@router.post("/banners")
def save_banner(drama_id: int, slot: str = "home", status: str = "online", db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = Banner(drama_id=drama_id, slot=slot, status=status)
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.post("/hot-searches")
def save_hot(term: str, position: int = 0, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = HotSearch(term=term, position=position)
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.post("/marketing/stats")
def save_stat(label: str, value: str, position: int = 0, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = MarketingStat(label=label, value=value, position=position)
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.post("/rewards/config")
def reward_config(body: RewardIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = save_reward_config(db, code=body.code, amounts=body.amounts)
    return {"effective_on": row.effective_on.isoformat(), "amounts": row.amounts}


@router.get("/plans")
def admin_plans(db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    return {"items": list_current_plans(db)}


@router.post("/plans/{code}/price")
def set_price(code: str, body: PriceIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin_role)):
    try:
        version = change_price(db, code=code, price_cents=body.price_cents, benefits=body.benefits, actor_id=user.id)
    except BillingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"version": version.version, "price_cents": version.price_cents}


@router.get("/comments")
def admin_comments(status: str | None = None, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    stmt = select(Comment)
    if status:
        stmt = stmt.where(Comment.status == status)
    rows = db.scalars(stmt.order_by(Comment.id.desc())).all()
    return {"items": [{"id": row.id, "body": row.body, "status": row.status, "drama_id": row.drama_id, "user_id": row.user_id} for row in rows]}


@router.post("/comments/{comment_id}")
def moderate(comment_id: int, body: CommentModIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = db.get(Comment, comment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="comment not found")
    if body.status not in {"approved", "deleted", "pending"}:
        raise HTTPException(status_code=422, detail="bad status")
    row.status = body.status
    db.commit()
    return {"status": row.status}


@router.get("/users")
def list_users(q: str | None = None, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    stmt = select(User)
    if q:
        stmt = stmt.where(or_(User.display_name.ilike(f"%{q}%"), User.public_id == q, User.email.ilike(f"%{q}%")))
    rows = db.scalars(stmt.order_by(User.id.desc()).limit(50)).all()
    items = []
    for row in rows:
        wallet = db.get(WalletAccount, row.id)
        sub = db.scalar(select(Subscription).where(Subscription.user_id == row.id, Subscription.status == "active"))
        items.append(
            {
                "id": row.id,
                "public_id": row.public_id,
                "display_name": row.display_name,
                "email": row.email,
                "coins": 0 if wallet is None else wallet.balance,
                "member": sub is not None,
                "created_at": row.created_at.isoformat(),
            }
        )
    return {"items": items}


@router.post("/users/{user_id}/coins")
def adjust(user_id: int, body: AdjustIn, db: Session = Depends(get_db), actor: AdminUser = Depends(require_admin_role)):
    wallet = db.get(WalletAccount, user_id, with_for_update=True)
    if wallet is None:
        raise HTTPException(status_code=404, detail="wallet not found")
    before = wallet.balance
    after = before + body.delta
    if after < 0:
        raise HTTPException(status_code=409, detail="balance cannot be negative")
    wallet.balance = after
    db.add(
        CoinLedgerEntry(
            user_id=user_id,
            amount=body.delta,
            balance_after=after,
            biz_type="adjustment",
            biz_key=f"adjust:{actor.id}:{user_id}:{before}:{body.delta}:{datetime.now(timezone.utc).timestamp()}",
            note=body.reason,
        )
    )
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor.id,
            action="coin_adjust",
            target_type="user",
            target_id=user_id,
            payload={"before": before, "delta": body.delta, "after": after, "reason": body.reason},
        )
    )
    db.commit()
    return {"before": before, "delta": body.delta, "after": after}


@router.get("/finance/orders")
def finance_orders(db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    rows = db.scalars(select(Order).order_by(Order.id.desc()).limit(100)).all()
    return {
        "items": [
            {
                "order_no": row.order_no,
                "user_id": row.user_id,
                "amount_cents": row.amount_cents,
                "currency": row.currency,
                "status": row.status,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.get("/finance/ledger")
def finance_ledger(db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    rows = db.scalars(select(CoinLedgerEntry).order_by(CoinLedgerEntry.id.desc()).limit(100)).all()
    return {"items": [{"user_id": row.user_id, "amount": row.amount, "biz_type": row.biz_type, "note": row.note} for row in rows]}


@router.get("/dashboard")
def dashboard(day: date | None = None, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    from app.modules.account.service import la_today
    target = day or la_today()
    data = build_dashboard(db, target)
    data["recent_orders"] = finance_orders(db=db, user=user)["items"][:5]
    return data


@router.post("/pushes")
def create_push(title: str, body: str = "", drama_id: int | None = None, delivered_count: int = 0, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = PushRecord(title=title, body=body, drama_id=drama_id, delivered_count=delivered_count)
    db.add(row)
    db.commit()
    return {"id": row.id, "delivered_count": row.delivered_count, "sent_at": row.sent_at.isoformat()}


@router.get("/pushes")
def list_pushes(db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    rows = db.scalars(select(PushRecord).order_by(PushRecord.id.desc())).all()
    return {"items": [{"id": row.id, "title": row.title, "delivered_count": row.delivered_count, "sent_at": row.sent_at.isoformat()} for row in rows]}


@router.post("/legal/{slug}")
def save_legal(slug: str, title: str, body: str = "", db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = db.get(LegalPage, slug)
    if row is None:
        row = LegalPage(slug=slug, title=title, body=body)
        db.add(row)
    else:
        row.title = title
        row.body = body
    db.commit()
    return {"slug": slug}


@router.post("/stores")
def save_store(platform: str, url: str, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = db.scalar(select(StoreLink).where(StoreLink.platform == platform))
    if row is None:
        row = StoreLink(platform=platform, url=url)
        db.add(row)
    else:
        row.url = url
    db.commit()
    return {"platform": platform, "url": url}
