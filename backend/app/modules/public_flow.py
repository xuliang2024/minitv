"""Public routes added from stage 3 onward: playback, account, billing, social, config."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.account import service as accounts
from app.modules.account.models import User
from app.modules.account.service import AccountError
from app.modules.billing import service as billing
from app.modules.billing.service import BillingError
from app.modules.common.models import AuditLog
from app.modules.content import service as content
from app.modules.content.models import Drama, Episode
from app.modules.content.upload_service import UploadRejected, apply_callback
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
from app.modules.playback import service as playback
from app.modules.playback.models import PlaybackProgress
from app.modules.social.models import Comment, InboxMessage, Reaction
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount
from app.modules.wallet.service import EpisodeNotFoundError, InsufficientCoinsError, unlock_episode

router = APIRouter(prefix="/v1", tags=["public-flow"])


def _optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else None
    return accounts.resolve_user(db, token)


def _require_user(user: User | None = Depends(_optional_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="login required")
    return user


class PlaybackIn(BaseModel):
    drama_id: int
    episode_id: int
    country: str | None = None


class CodeIn(BaseModel):
    email: str
    purpose: str = "register"


class RegisterIn(BaseModel):
    email: str
    code: str
    password: str | None = None


class EventIn(BaseModel):
    episode_id: int
    position_seconds: int = 0


class UnlockIn(BaseModel):
    episode_id: int


class ProfileIn(BaseModel):
    display_name: str | None = None
    avatar_url: str | None = None
    gender: str | None = None
    bio: str | None = None
    birthday: str | None = None
    region: str | None = None
    locale: str | None = None


class CommentIn(BaseModel):
    drama_id: int
    episode_id: int | None = None
    body: str = Field(min_length=1, max_length=1000)


class ReactionIn(BaseModel):
    target_type: str
    target_id: int
    kind: str
    remove: bool = False


@router.post("/playback")
def playback_auth(body: PlaybackIn, user: User | None = Depends(_optional_user), db: Session = Depends(get_db)):
    result = playback.authorize(
        db, drama_id=body.drama_id, episode_id=body.episode_id, user_id=None if user is None else user.id, country=body.country
    )
    if result.get("reason") == "not_available":
        raise HTTPException(status_code=404, detail="not available")
    return result


@router.post("/playback/start")
def playback_start(body: EventIn, user: User | None = Depends(_optional_user), db: Session = Depends(get_db)):
    playback.record_event(db, episode_id=body.episode_id, kind="start", position_seconds=body.position_seconds, user_id=None if user is None else user.id)
    return {"ok": True}


@router.post("/playback/progress")
def playback_progress(body: EventIn, user: User | None = Depends(_optional_user), db: Session = Depends(get_db)):
    playback.record_event(db, episode_id=body.episode_id, kind="progress", position_seconds=body.position_seconds, user_id=None if user is None else user.id)
    return {"ok": True}


@router.post("/playback/complete")
def playback_complete(body: EventIn, user: User | None = Depends(_optional_user), db: Session = Depends(get_db)):
    playback.record_event(db, episode_id=body.episode_id, kind="complete", position_seconds=body.position_seconds, user_id=None if user is None else user.id)
    return {"ok": True}


@router.post("/unlock")
def unlock(body: UnlockIn, request: Request, user: User = Depends(_require_user), db: Session = Depends(get_db)):
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key required")
    try:
        result = unlock_episode(db, user_id=user.id, episode_id=body.episode_id, idempotency_key=key)
    except InsufficientCoinsError as exc:
        raise HTTPException(status_code=402, detail={"need": exc.need, "have": exc.have}) from exc
    except EpisodeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc
    return result.__dict__


@router.post("/auth/email/code")
def email_code(body: CodeIn, db: Session = Depends(get_db)):
    try:
        accounts.issue_code(db, email=body.email, purpose=body.purpose)
    except AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"sent": True}


@router.post("/auth/email/register")
def email_register(body: RegisterIn, db: Session = Depends(get_db)):
    if body.password is not None:
        raise HTTPException(status_code=422, detail="password is not accepted")
    try:
        user, token = accounts.register(db, email=body.email, code=body.code)
    except AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"token": token, "user_id": user.id, "public_id": user.public_id}


@router.post("/auth/oauth/{provider}")
def oauth(provider: str):
    try:
        accounts.oauth_login(None, provider=provider)  # type: ignore[arg-type]
    except AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    raise HTTPException(status_code=501, detail="not configured")


@router.get("/me")
def me(user: User = Depends(_require_user), db: Session = Depends(get_db)):
    wallet = db.get(WalletAccount, user.id)
    return {
        "id": user.id,
        "public_id": user.public_id,
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "gender": user.gender,
        "bio": user.bio,
        "birthday": user.birthday.isoformat() if user.birthday else None,
        "region": user.region,
        "locale": user.locale,
        "following_count": user.following_count,
        "follower_count": user.follower_count,
        "received_likes": user.received_likes,
        "coins": 0 if wallet is None else wallet.balance,
        "reminder_opt_in": user.reminder_opt_in,
    }


@router.patch("/me")
def update_me(body: ProfileIn, user: User = Depends(_require_user), db: Session = Depends(get_db)):
    data = body.model_dump(exclude_none=True)
    if "birthday" in data:
        from datetime import date
        user.birthday = date.fromisoformat(data.pop("birthday"))
    for key, value in data.items():
        setattr(user, key, value)
    db.commit()
    return {"ok": True}


@router.get("/me/ledger")
def ledger(user: User = Depends(_require_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(CoinLedgerEntry).where(CoinLedgerEntry.user_id == user.id).order_by(CoinLedgerEntry.id.desc())).all()
    return {
        "items": [
            {
                "amount": row.amount,
                "balance_after": row.balance_after,
                "biz_type": row.biz_type,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.post("/me/checkin")
def checkin(user: User = Depends(_require_user), db: Session = Depends(get_db)):
    return accounts.check_in(db, user)


@router.post("/me/ad-reward")
def ad_reward(user: User = Depends(_require_user), db: Session = Depends(get_db)):
    try:
        return accounts.claim_ad(db, user)
    except AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/me/reminder")
def reminder(enabled: bool, user: User = Depends(_require_user), db: Session = Depends(get_db)):
    user.reminder_opt_in = enabled
    db.commit()
    return {"enabled": enabled}


@router.get("/plans")
def plans(db: Session = Depends(get_db)):
    return {"items": billing.list_current_plans(db)}


@router.post("/billing/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    signature = request.headers.get("X-Pay-Signature", "")
    try:
        payload = json.loads(body)
        return billing.apply_webhook(db, body=body, signature=signature, payload=payload)
    except BillingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/billing/cancel")
def cancel(user: User = Depends(_require_user), db: Session = Depends(get_db)):
    billing.cancel_subscription(db, user.id)
    return {"ok": True}


@router.get("/config")
def config(locale: str = "en", db: Session = Depends(get_db)):
    tabs = db.scalars(select(ChannelTab).where(ChannelTab.enabled.is_(True)).order_by(ChannelTab.position)).all()
    copies = {}
    for row in db.scalars(select(CopyEntry)).all():
        if locale == "zh" and row.zh:
            copies[row.key] = row.zh
        else:
            if locale == "zh" and not row.zh:
                db.add(AuditLog(actor_type="system", actor_id=None, action="copy_fallback", target_type="copy", target_id=row.id, payload={"key": row.key}))
            copies[row.key] = row.en
    db.commit()
    return {
        "tabs": [{"code": t.code, "title_en": t.title_en, "title_zh": t.title_zh, "position": t.position} for t in tabs],
        "copies": copies,
    }


@router.get("/marketing")
def marketing(db: Session = Depends(get_db)):
    dramas = content.list_published_dramas(db)[:6]
    return {
        "stats": [
            {"label": s.label, "value": s.value}
            for s in db.scalars(select(MarketingStat).order_by(MarketingStat.position)).all()
        ],
        "testimonials": [
            {"author": t.author, "body": t.body}
            for t in db.scalars(select(Testimonial).order_by(Testimonial.position)).all()
        ],
        "stores": {s.platform: s.url for s in db.scalars(select(StoreLink)).all()},
        "trending": [{"id": d.id, "title": d.title, "cover_url": d.cover_url, "play_count": d.play_count} for d in dramas],
        "plans": billing.list_current_plans(db),
    }


@router.get("/legal/{slug}")
def legal(slug: str, db: Session = Depends(get_db)):
    page = db.get(LegalPage, slug)
    if page is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"slug": page.slug, "title": page.title, "body": page.body}


@router.get("/dramas/{drama_id}/comments")
def list_comments(drama_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(Comment).where(Comment.drama_id == drama_id, Comment.status == "approved")).all()
    return {"items": [{"id": c.id, "body": c.body, "user_id": c.user_id} for c in rows]}


@router.post("/comments")
def create_comment(body: CommentIn, user: User = Depends(_require_user), db: Session = Depends(get_db)):
    row = Comment(user_id=user.id, drama_id=body.drama_id, episode_id=body.episode_id, body=body.body, status="pending")
    db.add(row)
    db.commit()
    return {"id": row.id, "status": "pending"}


@router.post("/reactions")
def react(body: ReactionIn, user: User = Depends(_require_user), db: Session = Depends(get_db)):
    existing = db.scalar(
        select(Reaction).where(
            Reaction.user_id == user.id,
            Reaction.target_type == body.target_type,
            Reaction.target_id == body.target_id,
            Reaction.kind == body.kind,
        )
    )
    if body.remove:
        if existing is not None:
            db.delete(existing)
            db.commit()
        return {"active": False}
    if existing is None:
        db.add(Reaction(user_id=user.id, target_type=body.target_type, target_id=body.target_id, kind=body.kind))
        db.commit()
    return {"active": True}


@router.get("/me/messages")
def messages(user: User = Depends(_require_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(InboxMessage).where(InboxMessage.user_id == user.id).order_by(InboxMessage.id.desc())).all()
    unread = sum(1 for row in rows if row.read_at is None)
    return {
        "unread": unread,
        "items": [
            {"id": row.id, "kind": row.kind, "title": row.title, "body": row.body, "read": row.read_at is not None}
            for row in rows
        ],
    }


@router.post("/me/messages/read")
def read_messages(user: User = Depends(_require_user), db: Session = Depends(get_db)):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for row in db.scalars(select(InboxMessage).where(InboxMessage.user_id == user.id, InboxMessage.read_at.is_(None))):
        row.read_at = now
    db.commit()
    return {"unread": 0}


@router.get("/feed")
def feed(db: Session = Depends(get_db)):
    episodes = db.scalars(
        select(Episode)
        .join(Drama, Drama.id == Episode.drama_id)
        .where(Drama.status == "published", Episode.status == "published")
        .order_by(Episode.id.desc())
        .limit(20)
    ).all()
    return {
        "items": [
            {
                "drama_id": ep.drama_id,
                "episode_id": ep.id,
                "episode_number": ep.episode_number,
                "title": ep.title,
            }
            for ep in episodes
        ]
    }


@router.post("/bunny/callback")
async def bunny_callback(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    signature = request.headers.get("X-Bunny-Signature", "")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="json required") from exc
    # Accept our local shape ({"video_id", "status"}) and Bunny's native
    # webhook shape ({"VideoGuid", "Status": 0..5}) so a real Stream webhook
    # can point at this endpoint once the API is publicly reachable.
    video_id = payload.get("video_id") or payload.get("VideoGuid")
    if not video_id:
        raise HTTPException(status_code=400, detail="video id required")
    raw_status = payload.get("status", payload.get("Status", "finished"))
    if isinstance(raw_status, int):
        if raw_status < 4:  # created / uploaded / processing / transcoding
            return {"ignored": True, "status": raw_status}
        outcome = "failed" if raw_status >= 5 else "finished"
    else:
        outcome = "failed" if str(raw_status).lower() in {"failed", "error"} else "finished"
    try:
        return apply_callback(
            db,
            body=body,
            signature=signature,
            video_id=video_id,
            outcome=outcome,
        )
    except UploadRejected as exc:
        code = 401 if exc.reason == "bad signature" else 400
        raise HTTPException(status_code=code, detail=exc.reason) from exc


@router.get("/search/trending")
def trending(db: Session = Depends(get_db)):
    rows = db.scalars(select(HotSearch).where(HotSearch.enabled.is_(True)).order_by(HotSearch.position)).all()
    return {"items": [row.term for row in rows]}


@router.get("/home/groups")
def home_groups(db: Session = Depends(get_db)):
    from app.modules.content.models import DramaHomeGroup, HomeGroup
    groups = db.scalars(select(HomeGroup).where(HomeGroup.status == "active").order_by(HomeGroup.position)).all()
    payload = []
    for group in groups:
        links = db.scalars(
            select(DramaHomeGroup).where(DramaHomeGroup.group_id == group.id).order_by(DramaHomeGroup.position_in_group)
        ).all()
        dramas = []
        for link in links:
            drama = content.get_published_drama(db, link.drama_id)
            if drama is not None:
                dramas.append({"id": drama.id, "title": drama.title, "cover_url": drama.cover_url, "position": link.position_in_group})
        payload.append({"id": group.id, "name": group.name, "position": group.position, "dramas": dramas})
    banners = db.scalars(select(Banner).where(Banner.status == "online").order_by(Banner.position)).all()
    return {"groups": payload, "banners": [{"id": b.id, "drama_id": b.drama_id, "slot": b.slot} for b in banners]}


@router.get("/me/history")
def history(user: User = Depends(_require_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(PlaybackProgress).where(PlaybackProgress.user_id == user.id)).all()
    return {"items": [{"episode_id": row.episode_id, "position_seconds": row.position_seconds} for row in rows]}


@router.get("/me/lists/{kind}")
def my_list(kind: str, user: User = Depends(_require_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Reaction).where(Reaction.user_id == user.id, Reaction.kind == kind)).all()
    return {"items": [{"target_type": row.target_type, "target_id": row.target_id} for row in rows]}
