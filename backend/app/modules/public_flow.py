"""Public routes added from stage 3 onward: playback, account, billing, social, config."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.account import service as accounts
from app.modules.account import tasks as account_tasks
from app.modules.account.models import User
from app.modules.account.service import AccountError
from app.modules.billing import service as billing
from app.modules.billing.service import BillingError
from app.modules.common.auth import optional_user as _optional_user
from app.modules.common.auth import require_user as _require_user
from app.modules.common.models import AuditLog
from app.modules.content import service as content
from app.modules.content.models import Drama, Episode, Tag
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
from app.modules.playback.models import PlaybackEvent, PlaybackProgress
from app.modules.social import moderation
from app.modules.social.models import Comment, InboxMessage, Reaction
from app.modules.wallet.models import (
    ENTITLEMENT_TYPE_COIN,
    CoinLedgerEntry,
    Entitlement,
    WalletAccount,
)
from app.modules.wallet.service import (
    EpisodeNotFoundError,
    EpisodeNotPurchasableError,
    InsufficientCoinsError,
    unlock_episode,
)

router = APIRouter(prefix="/v1", tags=["public-flow"])


def _tags_of(db: Session, drama_id: int) -> list[Tag]:
    return content.tags_by_drama(db, [drama_id]).get(drama_id, [])


def _episode_counts(db: Session, drama_ids: list[int]) -> dict[int, int]:
    """Published episode count per drama: the "共 N 集" line on every card."""
    ids = [drama_id for drama_id in set(drama_ids) if drama_id]
    if not ids:
        return {}
    rows = db.execute(
        select(Episode.drama_id, func.count())
        .where(Episode.drama_id.in_(ids), Episode.status == "published")
        .group_by(Episode.drama_id)
    ).all()
    return {int(drama_id): int(count) for drama_id, count in rows}


def _drama_card(drama: Drama, totals: dict[int, int]) -> dict:
    return {
        "id": drama.id,
        "title": drama.title,
        "cover_url": drama.cover_url,
        "episode_count": totals.get(drama.id, 0),
    }


class PlaybackIn(BaseModel):
    drama_id: int
    episode_id: int
    country: str | None = None


class CodeIn(BaseModel):
    email: str
    purpose: str = "login"


class RegisterIn(BaseModel):
    email: str
    code: str
    password: str | None = None


class LoginIn(BaseModel):
    email: str
    code: str


class GoogleIn(BaseModel):
    id_token: str = Field(min_length=1)


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
    except EpisodeNotPurchasableError as exc:
        raise HTTPException(status_code=409, detail="episode is not purchasable") from exc
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


@router.get("/auth/providers")
def auth_providers():
    """What this deployment can actually sign in with.

    The client hides anything that is off instead of rendering a button that
    cannot work. The Google client id is served from the environment so a new
    origin does not need an H5 rebuild.

    Only the browser client is published here: the H5 build renders Google's
    GIS button with it, and the Flutter app reuses it as Android's
    `serverClientId`. iOS mints its own audience from the iOS client baked into
    the app, which the backend accepts through `GOOGLE_CLIENT_IDS`.
    """
    client_id = next(
        (
            item.strip()
            for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_IDS")
            for item in os.environ.get(name, "").split(",")
            if item.strip()
        ),
        "",
    )
    return {
        "email": {"enabled": True},
        "google": {"enabled": bool(client_id), "client_id": client_id or None},
    }


@router.post("/auth/email/authenticate")
def email_authenticate(body: LoginIn, db: Session = Depends(get_db)):
    """Sign in with a code, creating the account on first use."""
    try:
        user, token, created = accounts.login_or_register_with_code(db, email=body.email, code=body.code)
    except AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"token": token, "user_id": user.id, "public_id": user.public_id, "created": created}


@router.post("/auth/email/register")
def email_register(body: RegisterIn, db: Session = Depends(get_db)):
    if body.password is not None:
        raise HTTPException(status_code=422, detail="password is not accepted")
    try:
        user, token = accounts.register(db, email=body.email, code=body.code)
    except AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"token": token, "user_id": user.id, "public_id": user.public_id}


@router.post("/auth/email/login")
def email_login(body: LoginIn, db: Session = Depends(get_db)):
    try:
        user, token = accounts.login_with_code(db, email=body.email, code=body.code)
    except AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"token": token, "user_id": user.id, "public_id": user.public_id}


@router.post("/auth/google")
def google_login(body: GoogleIn, db: Session = Depends(get_db)):
    try:
        user, token, created = accounts.login_with_google(db, id_token=body.id_token)
    except AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"token": token, "user_id": user.id, "public_id": user.public_id, "created": created}


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
    membership = billing.active_membership(db, user.id)
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
        "membership": membership,
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


@router.get("/tasks")
def public_tasks(db: Session = Depends(get_db)):
    """Task catalogue for signed-out visitors.

    Same cards and amounts as /v1/me/tasks, with progress zeroed. The reward tab
    renders this immediately; the client asks for a login only when the visitor
    taps an action that needs an account.
    """
    return account_tasks.tasks_overview(db, None)


@router.get("/me/tasks")
def my_tasks(user: User = Depends(_require_user), db: Session = Depends(get_db)):
    """Daily task centre: progress, amounts and check-in state in one call."""
    return account_tasks.tasks_overview(db, user)


@router.post("/me/tasks/{code}/claim")
def claim_daily_task(
    code: str, user: User = Depends(_require_user), db: Session = Depends(get_db)
):
    try:
        return account_tasks.claim_task(db, user, code)
    except AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/me/reminder")
def reminder(enabled: bool, user: User = Depends(_require_user), db: Session = Depends(get_db)):
    user.reminder_opt_in = enabled
    db.commit()
    return {"enabled": enabled}


@router.get("/me/orders")
def my_orders(
    limit: int = 20,
    offset: int = 0,
    user: User = Depends(_require_user),
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 50))
    return billing.list_user_orders(db, user.id, limit=limit, offset=max(0, offset))


@router.get("/plans")
def plans(db: Session = Depends(get_db)):
    return {"items": billing.list_current_plans(db), "billing_mode": billing.billing_mode(db)}


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
    try:
        billing.cancel_subscription(db, user.id)
    except BillingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"ok": True}


class CheckoutIn(BaseModel):
    plan_code: str
    success_url: str | None = None
    cancel_url: str | None = None


@router.post("/billing/checkout")
def checkout(body: CheckoutIn, user: User = Depends(_require_user), db: Session = Depends(get_db)):
    """Start a Stripe Checkout for one of the plans and hand back the URL."""
    try:
        return billing.create_checkout_session(
            db,
            user_id=user.id,
            email=user.email,
            plan_code=body.plan_code,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
        )
    except BillingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/billing/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    signature = request.headers.get("Stripe-Signature", "")
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid payload") from exc
    try:
        return billing.apply_stripe_event(db, body=body, signature=signature, payload=payload)
    except BillingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


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
        "tabs": [
            {"code": t.code, "title_en": t.title_en, "title_zh": t.title_zh, "position": t.position, "locked": t.locked, "group_id": t.group_id}
            for t in tabs
        ],
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
    """Legacy drama-wide list. Kept for older clients; new UI reads per episode."""
    rows = db.scalars(
        select(Comment)
        .where(Comment.drama_id == drama_id, Comment.status == "approved")
        .order_by(Comment.id.desc())
    ).all()
    return {"items": [{"id": c.id, "body": c.body, "user_id": c.user_id} for c in rows]}


COMMENT_PAGE_MAX = 50
COMMENT_MAX_LENGTH = 500
COMMENT_COOLDOWN_SECONDS = 30
COMMENT_DAILY_LIMIT = 50


def _comment_user(user: User | None) -> dict:
    if user is None:
        return {"public_id": "", "display_name": "Deleted user", "avatar_url": ""}
    return {
        "public_id": user.public_id or "",
        "display_name": user.display_name or "MiniTV user",
        "avatar_url": user.avatar_url or "",
    }


def _comment_payload(row: Comment, user: User | None, *, like_count: int = 0, liked: bool = False) -> dict:
    return {
        "id": row.id,
        "body": row.body,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "like_count": int(like_count),
        "liked": liked,
        "mine": False,
        "user": _comment_user(user),
    }


def _comment_visibility(user: User | None):
    if user is None:
        return Comment.status == "approved"
    return or_(
        Comment.status == "approved",
        and_(Comment.status == "pending", Comment.user_id == user.id),
    )


def _comment_total(db: Session, episode_id: int, user: User | None) -> int:
    """Public approved count plus the viewer's own pending rows (kept in sync
    with what the list shows, and with per-viewer comment_count)."""
    public = db.scalar(
        select(func.count())
        .select_from(Comment)
        .where(Comment.episode_id == episode_id, Comment.status == "approved")
    ) or 0
    own_pending = 0
    if user is not None:
        own_pending = db.scalar(
            select(func.count())
            .select_from(Comment)
            .where(
                Comment.episode_id == episode_id,
                Comment.status == "pending",
                Comment.user_id == user.id,
            )
        ) or 0
    return int(public) + int(own_pending)


@router.get("/episodes/{episode_id}/comments")
def list_episode_comments(
    episode_id: int,
    limit: int = 20,
    cursor: int | None = None,
    user: User | None = Depends(_optional_user),
    db: Session = Depends(get_db),
):
    """One episode, one thread.

    Everyone sees approved comments. A signed-in viewer additionally sees their
    own pending rows, so posting feels instant without leaking unmoderated
    content to anyone else.
    """
    episode = db.get(Episode, episode_id)
    if episode is None or episode.status != "published":
        raise HTTPException(status_code=404, detail="episode not found")

    size = max(1, min(int(limit), COMMENT_PAGE_MAX))
    stmt = (
        select(Comment, User)
        .join(User, User.id == Comment.user_id)
        .where(Comment.episode_id == episode_id, _comment_visibility(user))
    )
    if cursor:
        stmt = stmt.where(Comment.id < cursor)
    rows = db.execute(stmt.order_by(Comment.id.desc()).limit(size + 1)).all()
    has_more = len(rows) > size
    rows = rows[:size]
    # The cursor is the last row on this page, not the peeked extra one.
    next_cursor = rows[-1][0].id if has_more and rows else None

    ids = [row[0].id for row in rows]
    like_counts: dict[int, int] = {}
    liked: set[int] = set()
    if ids:
        like_counts = {
            int(target_id): int(total)
            for target_id, total in db.execute(
                select(Reaction.target_id, func.count())
                .where(
                    Reaction.target_type == "comment",
                    Reaction.kind == "like",
                    Reaction.target_id.in_(ids),
                )
                .group_by(Reaction.target_id)
            )
        }
        if user is not None:
            liked = {
                int(target_id)
                for target_id in db.scalars(
                    select(Reaction.target_id).where(
                        Reaction.user_id == user.id,
                        Reaction.target_type == "comment",
                        Reaction.kind == "like",
                        Reaction.target_id.in_(ids),
                    )
                )
            }

    total = _comment_total(db, episode_id, user)

    items = []
    for comment, author in rows:
        item = _comment_payload(
            comment,
            author,
            like_count=like_counts.get(comment.id, 0),
            liked=comment.id in liked,
        )
        item["mine"] = user is not None and comment.user_id == user.id
        item["pending"] = comment.status == "pending"
        items.append(item)

    return {"items": items, "next_cursor": next_cursor, "total": int(total)}


class EpisodeCommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=COMMENT_MAX_LENGTH)


def _published_episode_or_404(db: Session, episode_id: int) -> Episode:
    episode = db.get(Episode, episode_id)
    if episode is None or episode.status != "published":
        raise HTTPException(status_code=404, detail="episode not found")
    drama = db.get(Drama, episode.drama_id)
    if drama is None or drama.status != "published":
        raise HTTPException(status_code=404, detail="episode not found")
    return episode


@router.post("/episodes/{episode_id}/comments")
def create_episode_comment(
    episode_id: int,
    body: EpisodeCommentIn,
    background: BackgroundTasks,
    user: User = Depends(_require_user),
    db: Session = Depends(get_db),
):
    """Post into one episode's thread and return immediately.

    Visibility starts as pending: the author sees it right away, everyone else
    sees it only after the background worker approves it.
    """
    episode = _published_episode_or_404(db, episode_id)
    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="comment is empty")

    now = datetime.now(timezone.utc)
    recent = db.scalar(
        select(Comment.id)
        .where(
            Comment.user_id == user.id,
            Comment.episode_id == episode_id,
            Comment.created_at >= now - timedelta(seconds=COMMENT_COOLDOWN_SECONDS),
        )
        .limit(1)
    )
    if recent is not None:
        raise HTTPException(status_code=429, detail="You are commenting too fast. Try again in a moment.")
    daily = db.scalar(
        select(func.count())
        .select_from(Comment)
        .where(Comment.user_id == user.id, Comment.created_at >= now - timedelta(days=1))
    ) or 0
    if daily >= COMMENT_DAILY_LIMIT:
        raise HTTPException(status_code=429, detail="Daily comment limit reached.")

    queue = moderation.queue_state(db)
    row = Comment(
        user_id=user.id,
        drama_id=episode.drama_id,
        episode_id=episode.id,
        body=text,
        status="pending",
        moderation_state=queue,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    if queue:
        # Runs after the response is sent; the sweeper retries if this process dies.
        background.add_task(moderation.process_comment, row.id)

    payload = _comment_payload(row, user)
    payload["mine"] = True
    payload["pending"] = True
    return {
        "id": row.id,
        "status": "pending",
        "item": payload,
        "total": _comment_total(db, episode_id, user),
        "moderation": {"state": queue or "disabled"},
    }


@router.post("/comments")
def create_comment(
    body: CommentIn,
    background: BackgroundTasks,
    user: User = Depends(_require_user),
    db: Session = Depends(get_db),
):
    """Backward-compatible alias: episode is now mandatory."""
    if body.episode_id is None:
        raise HTTPException(status_code=422, detail="episode_id is required")
    return create_episode_comment(
        body.episode_id, EpisodeCommentIn(body=body.body), background, user, db
    )


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, user: User = Depends(_require_user), db: Session = Depends(get_db)):
    row = db.get(Comment, comment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="comment not found")
    if row.user_id != user.id:
        raise HTTPException(status_code=403, detail="not your comment")
    if row.status == "deleted":
        return {"status": "deleted"}
    if row.created_at < datetime.now(timezone.utc) - timedelta(hours=24):
        raise HTTPException(status_code=400, detail="Comments can only be deleted within 24 hours.")
    row.status = "deleted"
    db.commit()
    return {"status": "deleted"}


@router.post("/reactions")
def react(body: ReactionIn, user: User = Depends(_require_user), db: Session = Depends(get_db)):
    if body.target_type not in {"drama", "episode", "comment"}:
        raise HTTPException(status_code=422, detail="bad target_type")
    if body.target_type == "comment":
        target = db.get(Comment, body.target_id)
        if target is None or target.status != "approved":
            raise HTTPException(status_code=404, detail="comment not found")
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


@router.get("/me/favorites")
def my_favorites(
    limit: int = 20,
    offset: int = 0,
    user: User = Depends(_require_user),
    db: Session = Depends(get_db),
):
    """The dramas this viewer saved, newest first.

    Favorites are a reactions row, but the client thinks in drama cards, so the
    join filters out anything unpublished since it was saved.
    """
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    base = (
        select(Drama)
        .join(Reaction, Reaction.target_id == Drama.id)
        .where(
            Reaction.user_id == user.id,
            Reaction.target_type == "drama",
            Reaction.kind == "favorite",
            Drama.status == "published",
        )
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(Reaction.id.desc()).offset(offset).limit(limit)).all()
    totals = _episode_counts(db, [drama.id for drama in rows])
    return {
        "total": int(total),
        "items": [
            {
                "id": drama.id,
                "title": drama.title,
                "cover_url": drama.cover_url,
                "synopsis": drama.synopsis,
                "play_count": drama.play_count,
                "episode_count": totals.get(drama.id, 0),
            }
            for drama in rows
        ],
    }


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


@router.get("/dramas/{drama_id}/play")
def drama_play(drama_id: int, user: User | None = Depends(_optional_user), db: Session = Depends(get_db)):
    """The drama's own play page: every episode, its lock state, and the resume point.

    Two doors lead here - the viewer taps a poster by hand, or the For-You player
    finishes an episode and hands off to this page. Either way the payload has to
    stand alone: the vertical episode scroller is painted from this one response,
    so there is no second call to fetch the episode list.

    Playback URLs are still absent; `POST /v1/playback` mints those and applies
    the free / membership / coin-unlock rules.
    """
    drama = content.get_published_drama(db, drama_id)
    if drama is None:
        raise HTTPException(status_code=404, detail="drama not found")

    uid = None if user is None else user.id
    member = playback.subscription_active(db, uid)
    progress_rows = (
        {
            episode_id: int(position or 0)
            for episode_id, position in db.execute(
                select(PlaybackProgress.episode_id, PlaybackProgress.position_seconds).where(
                    PlaybackProgress.user_id == uid
                )
            )
        }
        if uid is not None
        else {}
    )
    completed = (
        set(
            db.scalars(
                select(PlaybackEvent.episode_id).where(
                    PlaybackEvent.user_id == uid, PlaybackEvent.kind == "complete"
                )
            )
        )
        if uid is not None
        else set()
    )
    unlocked = (
        set(
            db.scalars(
                select(Entitlement.episode_id).where(
                    Entitlement.user_id == uid,
                    Entitlement.entitlement_type == ENTITLEMENT_TYPE_COIN,
                )
            )
        )
        if uid is not None
        else set()
    )

    items: list[dict] = []
    resume: dict | None = None
    episodes = content.list_published_episodes(db, drama_id)
    # One grouped query keeps the payload from turning into N+1 counts.
    comment_counts = (
        {
            int(episode_id): int(total)
            for episode_id, total in db.execute(
                select(Comment.episode_id, func.count())
                .where(
                    Comment.episode_id.in_([episode.id for episode in episodes]),
                    Comment.status == "approved",
                )
                .group_by(Comment.episode_id)
            )
        }
        if episodes
        else {}
    )
    if uid is not None and episodes:
        # The author's own pending rows count for them only, so the rail number
        # matches what their comment sheet actually shows.
        for episode_id, total in db.execute(
            select(Comment.episode_id, func.count())
            .where(
                Comment.episode_id.in_([episode.id for episode in episodes]),
                Comment.status == "pending",
                Comment.user_id == uid,
            )
            .group_by(Comment.episode_id)
        ):
            key = int(episode_id)
            comment_counts[key] = comment_counts.get(key, 0) + int(total)
    for episode in episodes:
        free = episode.episode_number <= drama.free_episode_count
        locked = not (free or member or episode.id in unlocked)
        position = progress_rows.get(episode.id, 0)
        items.append(
            {
                "id": episode.id,
                "episode_number": episode.episode_number,
                "title": episode.title,
                "duration_seconds": episode.duration_seconds,
                "free": free,
                "locked": locked,
                "progress_seconds": position,
                # A finished episode reads differently from an untouched one in
                # the episode sheet, so the flag travels with the row.
                "completed": episode.id in completed,
                "comment_count": comment_counts.get(episode.id, 0),
                "unlock": {"coin_price": drama.coin_price_per_episode} if locked else None,
            }
        )
        # Resume = the first watchable episode the viewer started but did not
        # finish; otherwise the first watchable episode at all.
        if resume is None and not locked and position > 0 and episode.id not in completed:
            resume = {"episode_id": episode.id, "position_seconds": position}
    if resume is None:
        first = next((item for item in items if not item["locked"]), None)
        resume = {"episode_id": first["id"], "position_seconds": 0} if first else None

    tags = _tags_of(db, drama.id)
    like_count = db.scalar(
        select(func.count())
        .select_from(Reaction)
        .where(Reaction.target_type == "drama", Reaction.target_id == drama.id, Reaction.kind == "like")
    ) or 0
    comment_count = db.scalar(
        select(func.count())
        .select_from(Comment)
        .where(Comment.drama_id == drama.id, Comment.status == "approved")
    ) or 0
    mine: set[str] = set()
    if uid is not None:
        mine = set(
            db.scalars(
                select(Reaction.kind).where(
                    Reaction.user_id == uid,
                    Reaction.target_type == "drama",
                    Reaction.target_id == drama.id,
                )
            ).all()
        )
    return {
        "drama": {
            "id": drama.id,
            "title": drama.title,
            "synopsis": drama.synopsis,
            "cover_url": drama.cover_url,
            "tags": [tag.name_en for tag in tags],
            "free_episode_count": drama.free_episode_count,
            "coin_price_per_episode": drama.coin_price_per_episode,
            "play_count": drama.play_count,
            "like_count": int(like_count),
            "comment_count": int(comment_count),
            "liked": "like" in mine,
            "favorited": "favorite" in mine,
        },
        "items": items,
        "resume": resume,
        "entitlement": {
            "member": member,
            "unlocked_episode_ids": sorted(unlocked),
        },
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
                dramas.append(
                    {
                        "id": drama.id,
                        "title": drama.title,
                        "cover_url": drama.cover_url,
                        "position": link.position_in_group,
                        "play_count": drama.play_count,
                    }
                )
        payload.append(
            {
                "id": group.id,
                "name": group.name,
                "position": group.position,
                "style": group.style,
                "badge": group.badge,
                "dramas": dramas,
            }
        )
    banners = db.scalars(select(Banner).where(Banner.status == "online").order_by(Banner.position)).all()
    banner_payload = []
    for banner in banners:
        drama = content.get_published_drama(db, banner.drama_id) if banner.drama_id else None
        banner_payload.append(
            {
                "id": banner.id,
                "drama_id": banner.drama_id,
                "slot": banner.slot,
                "title": None if drama is None else drama.title,
                "cover_url": None if drama is None else drama.cover_url,
                "kicker": banner.kicker,
                "headline": banner.headline or ("" if drama is None else drama.title),
            }
        )
    return {"groups": payload, "banners": banner_payload}


@router.get("/me/history")
def history(user: User = Depends(_require_user), db: Session = Depends(get_db)):
    """Watch history as cards: newest progress first, joined to drama + episode.

    The Me page paints a poster grid, so returning bare episode ids would leave
    the client with nothing to show. Unpublished dramas are filtered out the
    same way favorites are, and the legacy episode fields stay for old clients.
    """
    rows = db.execute(
        select(PlaybackProgress, Episode, Drama)
        .join(Episode, Episode.id == PlaybackProgress.episode_id)
        .join(Drama, Drama.id == Episode.drama_id)
        .where(PlaybackProgress.user_id == user.id, Drama.status == "published")
        .order_by(PlaybackProgress.updated_at.desc())
        .limit(60)
    ).all()
    totals = _episode_counts(db, [drama.id for _, _, drama in rows])
    return {
        "items": [
            {
                "episode_id": progress.episode_id,
                "position_seconds": progress.position_seconds,
                "episode_number": episode.episode_number,
                "duration_seconds": episode.duration_seconds,
                "updated_at": progress.updated_at.isoformat(),
                "drama": _drama_card(drama, totals),
            }
            for progress, episode, drama in rows
        ]
    }


@router.get("/me/lists/{kind}")
def my_list(kind: str, user: User = Depends(_require_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Reaction).where(Reaction.user_id == user.id, Reaction.kind == kind)).all()
    drama_ids = [row.target_id for row in rows if row.target_type == "drama"]
    dramas = {}
    if drama_ids:
        dramas = {
            drama.id: drama
            for drama in db.scalars(
                select(Drama).where(Drama.id.in_(drama_ids), Drama.status == "published")
            ).all()
        }
    totals = _episode_counts(db, list(dramas))
    items = []
    for row in rows:
        if row.target_type == "drama":
            drama = dramas.get(row.target_id)
            if drama is None:  # unpublished since it was liked
                continue
            items.append(
                {
                    "target_type": row.target_type,
                    "target_id": row.target_id,
                    "drama": _drama_card(drama, totals),
                }
            )
        else:
            items.append({"target_type": row.target_type, "target_id": row.target_id})
    return {"total": len(items), "items": items}
