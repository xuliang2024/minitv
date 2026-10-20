"""Admin routes for stages 3 through 14."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db, get_engine, make_session_factory
from app.modules.account.models import User
from app.modules.account.service import (
    AccountError,
    recent_reward_changes,
    reward_config_overview,
    save_reward_config,
)
from app.modules.analytics.service import dashboard as build_dashboard
from app.modules.billing.models import Order, Plan, PlanVersion
from app.modules.billing.service import (
    BillingError,
    active_membership,
    billing_mode,
    change_price,
    get_billing_settings,
    grant_membership as billing_grant_membership,
    list_current_plans,
    membership_detail as billing_membership_detail,
    provision_prices_for_mode,
    revoke_membership as billing_revoke_membership,
    set_billing_mode,
)
from app.modules.common import bunny
from app.modules.common.models import AuditLog
from app.modules.content.models import Drama, DramaHomeGroup, Episode, HomeGroup, MediaUpload
from app.modules.content.publish_service import (
    GROUP_LIMIT,
    PublishError,
    add_to_group,
    apply_due_schedules,
    remove_from_group,
    change_drama_status,
    delete_drama,
    publish_episode,
    schedule_publish,
)
from app.modules.content.service import InvalidTransition, transition_episode
from app.modules.content.upload_service import UploadRejected, create_upload_session
from app.modules.ops.config_models import (
    Banner,
    ChannelTab,
    CopyEntry,
    HotSearch,
    LegalPage,
    MarketingStat,
    ModerationSettings,
    StoreLink,
    Testimonial,
)
from app.modules.social import moderation
from app.modules.social.models import Comment, PushRecord
from app.modules.ops.models import AdminUser
from app.modules.ops.router import require_admin, require_content_writer
from app.modules.playback.service import authorize, bump_generation
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount

router = APIRouter(prefix="/admin/v1", tags=["admin-flow"])


def _drama_or_404(db: Session, drama_id: int) -> Drama:
    drama = db.get(Drama, drama_id)
    if drama is None:
        raise HTTPException(status_code=404, detail="drama not found")
    return drama


class EpisodeIn(BaseModel):
    episode_number: int = Field(ge=1)
    title: str = Field(default="", max_length=200)


class BulkIn(BaseModel):
    count: int = Field(ge=1, le=200)


class UploadIn(BaseModel):
    filename: str = Field(max_length=255)
    mime: str
    size_bytes: int = Field(ge=0)
    width: int
    height: int
    # Browser-probed length; optional so older clients keep working.
    duration_seconds: int = Field(default=0, ge=0)


class UploadFailedIn(BaseModel):
    """Browser-side upload failure report (see ``upload_failed`` below)."""

    video_id: str = ""
    reason: str = ""


class EpisodeUpdateIn(BaseModel):
    """Partial metadata update for one episode (title / episode number)."""

    title: str | None = Field(default=None, max_length=200)
    episode_number: int | None = Field(default=None, ge=1, le=2000)


class StatusIn(BaseModel):
    to_status: str


class EpisodeBulkStatusIn(BaseModel):
    episode_ids: list[int] = Field(min_length=1, max_length=200)
    action: str = Field(pattern="^(publish|unpublish)$")


class ScheduleIn(BaseModel):
    at: datetime


class DeleteIn(BaseModel):
    confirm: bool = False


FLOOR_STYLES = {"poster3", "rank", "grid2"}
FLOOR_BADGES = {"rank", "label"}


class GroupIn(BaseModel):
    name: str
    position: int = 0


class GroupUpdateIn(BaseModel):
    """Rename, reorder, restyle, or pause a homepage group. Any subset of fields is accepted."""

    name: str | None = Field(default=None, max_length=64)
    position: int | None = None
    status: str | None = None
    style: str | None = None
    badge: str | None = None

    @field_validator("status")
    @classmethod
    def _status(cls, value: str | None) -> str | None:
        if value is not None and value not in {"active", "inactive"}:
            raise ValueError("status must be active or inactive")
        return value

    @field_validator("style")
    @classmethod
    def _style(cls, value: str | None) -> str | None:
        if value is not None and value not in FLOOR_STYLES:
            raise ValueError("style must be poster3, rank or grid2")
        return value

    @field_validator("badge")
    @classmethod
    def _badge(cls, value: str | None) -> str | None:
        if value is not None and value not in FLOOR_BADGES:
            raise ValueError("badge must be rank or label")
        return value


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
    code: str = Field(min_length=1, max_length=32)
    amounts: list[int] = Field(min_length=1, max_length=7)


class PriceIn(BaseModel):
    price_cents: int
    benefits: dict = Field(default_factory=dict)


class AdjustIn(BaseModel):
    delta: int
    reason: str = Field(min_length=1)

    @field_validator("delta")
    @classmethod
    def _nonzero(cls, value: int) -> int:
        # A zero delta would insert a coin_ledger row that violates
        # ck_coin_ledger_amount_nonzero, so reject it before hitting the DB.
        if value == 0:
            raise ValueError("delta must be non-zero")
        return value


class UserUpdateIn(BaseModel):
    """Admin-side profile edit. Omitted fields stay untouched."""

    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=320)
    avatar_url: str | None = Field(default=None, max_length=500)
    gender: str | None = Field(default=None, max_length=16)
    bio: str | None = Field(default=None, max_length=500)
    region: str | None = Field(default=None, max_length=8)
    locale: str | None = Field(default=None, max_length=8)


class CommentModIn(BaseModel):
    status: str


class ModerationSettingsIn(BaseModel):
    """Admin-editable policy. The API key is deliberately absent: it lives in
    ``OPENAI_API_KEY`` so rotating it never writes to the database."""

    enabled: bool
    base_url: str = Field(default="https://api.openai.com/v1", max_length=200)
    model: str = Field(min_length=1, max_length=64)
    categories: list[str] = Field(default_factory=list)
    system_prompt: str = Field(default="", max_length=8000)
    approve_threshold: float = Field(ge=0, le=1)
    block_threshold: float = Field(ge=0, le=1)
    fallback_action: str = "pending"
    timeout_seconds: int = Field(ge=1, le=30)

    @field_validator("categories")
    @classmethod
    def _clean_categories(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            text = str(item).strip().lower()[:32]
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned[:20]

    @field_validator("fallback_action")
    @classmethod
    def _known_fallback(cls, value: str) -> str:
        if value not in moderation.FALLBACK_ACTIONS:
            raise ValueError("fallback_action must be pending or approved")
        return value

    @field_validator("base_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        text = value.strip().rstrip("/")
        if not text.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return text


class ModerationTestIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


def require_admin_role(user: AdminUser = Depends(require_admin)) -> AdminUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return user


@router.post("/dramas/{drama_id}/episodes")
def create_episode(drama_id: int, body: EpisodeIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    _drama_or_404(db, drama_id)
    duplicate = db.scalar(
        select(Episode.id).where(
            Episode.drama_id == drama_id,
            Episode.episode_number == body.episode_number,
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail=f"第 {body.episode_number} 集已存在，请换一个集数",
        )
    title = (body.title or f"Episode {body.episode_number}").strip() or f"Episode {body.episode_number}"
    row = Episode(drama_id=drama_id, episode_number=body.episode_number, title=title[:200])
    db.add(row)
    db.commit()
    return {
        "id": row.id,
        "episode_number": row.episode_number,
        "title": row.title,
        "status": row.status,
    }


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
    page_size: int = Query(20, ge=1, le=200),
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
    # Opening the episode list is the moment an operator notices a stuck
    # "transcoding" badge. Match this page against the library listing so a
    # finished encode flips without a manual sync click. One listing, not one
    # request per row, and the listing is cached so paging does not refetch.
    if bunny.live() and any(row.status == "transcoding" and row.bunny_video_id for row in rows):
        remote_by_guid = _cached_library()
        changed = False
        for row in rows:
            remote = remote_by_guid.get(row.bunny_video_id)
            if row.status != "transcoding" or remote is None:
                continue
            try:
                if apply_remote_video(db, row, remote)["changed"]:
                    changed = True
            except InvalidTransition:
                continue
        if changed:
            db.commit()
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


@router.get("/episodes/{episode_id}/preview-url")
def episode_preview_url(
    episode_id: int,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_admin),
):
    """Return a short-lived Bunny Player URL for an admin-only preview."""
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="episode not found")
    if not episode.bunny_video_id:
        raise HTTPException(status_code=409, detail="episode has no Bunny video")
    try:
        signed = bunny.stream_embed_url(episode.bunny_video_id, ttl=3600)
    except bunny.BunnyError as exc:
        raise HTTPException(status_code=503, detail=f"preview signing unavailable: {exc}") from exc
    return {
        **signed,
        "episode_id": episode.id,
        "episode_number": episode.episode_number,
        "video_id": episode.bunny_video_id,
    }


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
            duration_seconds=body.duration_seconds,
        )
    except UploadRejected as exc:
        raise HTTPException(status_code=422, detail=exc.reason) from exc
    except bunny.BunnyError as exc:
        raise HTTPException(status_code=502, detail=f"bunny upstream: {exc}") from exc


def _latest_media_upload(db: Session, episode_id: int, video_id: str = "") -> MediaUpload | None:
    stmt = select(MediaUpload).where(MediaUpload.episode_id == episode_id)
    if video_id:
        stmt = stmt.where(MediaUpload.video_id == video_id)
    return db.scalars(stmt.order_by(MediaUpload.id.desc())).first()


# Bunny Stream video status (VideoModelStatus). 0 is "created" (no bytes, or
# bytes just landed and encode has not started). 3 is "transcoding" and is
# already playable once resolutions exist; 4 is "finished". >= 5 is a failure.
_REMOTE_FINISHED = 4
_REMOTE_TRANSCODING = 3
_REMOTE_FAILED = 5

# An upload this far past session creation with no pipeline movement is not
# queued any more: Bunny silently dropped it. Both observed flavors are
# permanent until the object is replaced — ``reencode`` is a no-op on the
# second and answers ``400 Original file is missing`` on the first. Long
# enough to ride out peak-hour delays.
_REMOTE_STALLED_AFTER_SECONDS = 3600


def _remote_upload_stalled(remote: dict) -> bool:
    """True when Bunny kept the object but the pipeline never moved on it.

    Two flavors seen in production:

    - status 0 with no bytes accounted for — the upload died in transit and
      the original never made it to storage (CDN ``/{guid}/original`` 404s).
    - status 1/2 with zero encode progress and no resolutions — Bunny has
      the original but the encoder never picked it up (sits 转码中 forever).

    Either way the row must fall to ``transcode_failed`` so the retry path
    (closed while transcoding) re-opens; without that the episode can never
    be edited, replaced or deleted again. Fresh uploads inside the queue
    window are left alone.
    """
    status = int(remote.get("status") or 0)
    if status not in (0, 1, 2):
        return False
    uploaded = remote.get("dateUploaded")
    if not uploaded:
        return False
    try:
        uploaded_at = datetime.fromisoformat(str(uploaded))
    except ValueError:
        return False
    if uploaded_at.tzinfo is None:
        uploaded_at = uploaded_at.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - uploaded_at).total_seconds() <= _REMOTE_STALLED_AFTER_SECONDS:
        return False
    if status == 0:
        # No bytes accounted for: the original never survived the upload.
        return int(remote.get("storageSize") or 0) == 0 and int(remote.get("length") or 0) == 0
    # Bytes landed but nothing was ever encoded.
    return int(remote.get("encodeProgress") or 0) == 0 and not (remote.get("availableResolutions") or "")


def _remote_has_bytes(remote: dict) -> bool:
    """True when Bunny has the original, even if encode has not started.

    ``hasOriginal`` flips true as soon as the TUS body arrives. ``status`` can
    stay 0 for a while after that, with no hash and no storage size yet — that
    is a queue delay, not a failed upload.
    """
    if remote.get("hasOriginal"):
        return True
    if int(remote.get("storageSize") or 0) > 0:
        return True
    if int(remote.get("length") or 0) > 0:
        return True
    return int(remote.get("status") or 0) >= _REMOTE_TRANSCODING


def apply_remote_video(db: Session, episode: Episode, remote: dict | None) -> dict:
    """Advance one ``transcoding`` episode from a Bunny video object.

    ``remote is None`` is the fake-mode signal: the local receiver already
    finished the upload, so the row moves to ``in_review``. A missing remote
    object (deleted upstream) is not the same thing and must be passed as an
    empty dict, which leaves the row alone.
    """
    remote_status: int | None = None
    duration_updated = False
    if remote is not None:
        remote_status = int(remote.get("status") or 0)
        length = int(remote.get("length") or 0)
        if length > 0 and episode.duration_seconds != length:
            episode.duration_seconds = length
            duration_updated = True

    changed = duration_updated
    if episode.status == "transcoding":
        resolutions = (remote or {}).get("availableResolutions") or ""
        length = int((remote or {}).get("length") or 0)
        target = None
        if remote is None or remote_status == _REMOTE_FINISHED:
            target = "in_review"
        elif remote_status == _REMOTE_TRANSCODING and resolutions and length > 0:
            # Early play: at least one resolution is already watchable.
            target = "in_review"
        elif remote_status is not None and remote_status >= _REMOTE_FAILED:
            target = "transcode_failed"
        elif _remote_upload_stalled(remote):
            # Bunny kept an object the pipeline dropped (no bytes, or an
            # encoder that never picked it up); only a fresh session
            # (re-opened by transcode_failed) can recover it.
            target = "transcode_failed"
        if target is not None:
            transition_episode(db, episode, target)
            upload = _latest_media_upload(db, episode.id, episode.bunny_video_id)
            if upload is not None:
                upload.status = "completed" if target == "in_review" else "failed"
            changed = True
    return {
        "episode_id": episode.id,
        "status": episode.status,
        "remote_status": remote_status,
        "duration_seconds": episode.duration_seconds,
        "changed": changed,
    }


def reconcile_transcoding(db: Session, remote_by_guid: dict[str, dict]) -> dict[str, int]:
    """Move every finished ``transcoding`` row forward from one library listing.

    Rows whose video is still queued or encoding stay put. A GUID absent from
    the listing is left alone too: a transient empty page must not be read as
    "Bunny deleted the video".
    """
    rows = list(db.scalars(select(Episode).where(Episode.status == "transcoding", Episode.bunny_video_id != "")))
    advanced = failed = pending = missing = 0
    for episode in rows:
        remote = remote_by_guid.get(episode.bunny_video_id)
        if remote is None:
            missing += 1
            continue
        before = episode.status
        try:
            apply_remote_video(db, episode, remote)
        except InvalidTransition:
            pending += 1
            continue
        if episode.status == before:
            pending += 1
        elif episode.status == "transcode_failed":
            failed += 1
        else:
            advanced += 1
    if advanced or failed:
        db.add(
            AuditLog(
                actor_type="system",
                actor_id=None,
                action="reconcile_transcoding",
                target_type="episode",
                target_id=None,
                payload={"advanced": advanced, "failed": failed, "pending": pending, "missing": missing},
            )
        )
        db.commit()
    return {"advanced": advanced, "failed": failed, "pending": pending, "missing": missing, "seen": len(rows)}


_reconcile_log = logging.getLogger("minitv.transcode")
_reconcile_started = False
_RECONCILE_INTERVAL_SECONDS = 120
_library_cache: tuple[float, dict[str, dict]] = (0.0, {})
_LIBRARY_CACHE_SECONDS = 30.0


def _cached_library() -> dict[str, dict]:
    """Library listing reused across page views. A failed fetch keeps the last good copy."""
    global _library_cache
    fetched_at, cached = _library_cache
    if cached and time.monotonic() - fetched_at < _LIBRARY_CACHE_SECONDS:
        return cached
    try:
        fresh = bunny.list_stream_videos()
    except bunny.BunnyError:
        return cached
    _library_cache = (time.monotonic(), fresh)
    return fresh


def _reconcile_forever() -> None:
    while True:
        time.sleep(_RECONCILE_INTERVAL_SECONDS)
        if not bunny.live():
            continue
        session = make_session_factory(get_engine())()
        try:
            pending = session.scalar(
                select(func.count()).select_from(Episode).where(Episode.status == "transcoding")
            )
            if not pending:
                continue
            result = reconcile_transcoding(session, bunny.list_stream_videos())
            if result["advanced"] or result["failed"]:
                _reconcile_log.info("transcode reconcile %s", result)
        except Exception:
            session.rollback()
            _reconcile_log.exception("transcode reconcile failed")
        finally:
            session.close()


def start_transcode_reconciler() -> None:
    """Poll Bunny for finished encodes. The library webhook is not configured."""
    global _reconcile_started
    if _reconcile_started or not bunny.live():
        return
    _reconcile_started = True
    threading.Thread(target=_reconcile_forever, name="transcode-reconcile", daemon=True).start()


@router.post("/episodes/{episode_id}/sync-status")
def sync_episode_status(episode_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    """Poll Bunny once and advance a ``transcoding`` episode.

    The transcode callback is not wired in every environment, so the admin
    console uses this endpoint as its post-upload source of truth: remote
    status 4 means the video finished transcoding, >= 5 means it failed. In
    fake mode the local receiver already completed the upload, so a
    transcoding row is treated as finished.
    """
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="episode not found")
    if not episode.bunny_video_id:
        raise HTTPException(status_code=409, detail="episode has no video")

    remote: dict | None = None
    source = "fake"
    if bunny.live():
        source = "bunny"
        if episode.status == "transcoding" or episode.duration_seconds == 0:
            try:
                remote = bunny.stream_video(episode.bunny_video_id)
            except bunny.BunnyError as exc:
                raise HTTPException(status_code=502, detail=f"bunny upstream: {exc}") from exc
    try:
        result = apply_remote_video(db, episode, remote)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result["changed"]:
        db.commit()
    return {
        "episode_id": episode.id,
        "status": episode.status,
        "remote_status": result["remote_status"],
        "duration_seconds": episode.duration_seconds,
        "source": source,
    }


@router.post("/episodes/{episode_id}/upload-failed")
def upload_failed(
    episode_id: int,
    body: UploadFailedIn | None = None,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_content_writer),
):
    """Report a browser-side upload failure so the episode can be retried.

    ``create_upload_session`` moves the episode to ``transcoding`` before any
    bytes exist. When the direct upload dies mid-flight, the row would be
    stuck there (a second session is refused while transcoding), so the admin
    console calls this: ``transcoding -> transcode_failed`` is the legal edge
    that re-opens uploads.
    """
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="episode not found")
    payload = body or UploadFailedIn()
    if episode.status != "transcoding":
        return {
            "episode_id": episode.id,
            "status": episode.status,
            "updated": False,
            "reason": payload.reason,
        }
    # A browser error after the bytes have landed must not stamp 转码失败 onto
    # a video Bunny has already accepted. Status 0 with no original means the
    # object was created and nothing arrived — that one stays retryable.
    video_id = payload.video_id or episode.bunny_video_id
    if bunny.live() and video_id:
        try:
            remote = bunny.stream_video(video_id)
        except bunny.BunnyError:
            remote = None
        if remote is not None and _remote_has_bytes(remote):
            try:
                apply_remote_video(db, episode, remote)
            except InvalidTransition as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            db.commit()
            return {
                "episode_id": episode.id,
                "status": episode.status,
                "updated": episode.status != "transcoding",
                "reason": payload.reason,
                "ignored": "bytes already at bunny",
            }
    try:
        transition_episode(db, episode, "transcode_failed")
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    upload = _latest_media_upload(db, episode.id, video_id)
    if upload is not None:
        upload.status = "failed"
    db.commit()
    return {
        "episode_id": episode.id,
        "status": episode.status,
        "updated": True,
        "reason": payload.reason,
    }


def _require_fake_mode() -> None:
    if bunny.live():
        raise HTTPException(status_code=404, detail="dev upload receiver is disabled in real mode")


@router.post("/dev-tus/{video_id}")
def dev_tus_create(video_id: str, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    """TUS create step of the fake-mode upload receiver (dev/test only).

    Mirrors Bunny's ``POST /tusupload``: answers 201 with the Location the
    client must PATCH its bytes to.
    """
    _require_fake_mode()
    upload = db.scalar(select(MediaUpload).where(MediaUpload.video_id == video_id))
    if upload is None:
        raise HTTPException(status_code=404, detail="unknown video id")
    return Response(
        status_code=201,
        headers={"Location": f"/admin/v1/dev-tus/{video_id}", "Tus-Resumable": "1.0.0"},
    )


@router.patch("/dev-tus/{video_id}")
async def dev_tus_patch(
    video_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_content_writer),
):
    """TUS data step of the fake-mode receiver: completes the upload at once."""
    _require_fake_mode()
    upload = db.scalar(select(MediaUpload).where(MediaUpload.video_id == video_id))
    if upload is None:
        raise HTTPException(status_code=404, detail="unknown video id")
    if upload.status == "completed":
        return Response(
            status_code=204,
            headers={"Upload-Offset": str(upload.size_bytes), "Tus-Resumable": "1.0.0"},
        )
    if (request.headers.get("upload-offset") or "0") != "0":
        raise HTTPException(status_code=409, detail="only single-shot uploads are supported")
    body = await request.body()
    episode = db.get(Episode, upload.episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="episode missing")
    if episode.status == "transcoding":
        try:
            transition_episode(db, episode, "in_review")
        except InvalidTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    upload.status = "completed"
    upload.size_bytes = len(body)
    episode.size_bytes = len(body)
    db.commit()
    return Response(
        status_code=204,
        headers={"Upload-Offset": str(len(body)), "Tus-Resumable": "1.0.0"},
    )


@router.patch("/episodes/{episode_id}")
def update_episode(
    episode_id: int,
    body: EpisodeUpdateIn,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_content_writer),
):
    """Edit episode metadata. Video bytes are replaced via upload-session."""
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="episode not found")
    if body.title is None and body.episode_number is None:
        raise HTTPException(status_code=422, detail="没有需要更新的字段")
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="标题不能为空")
        episode.title = title
    if body.episode_number is not None and body.episode_number != episode.episode_number:
        duplicate = db.scalar(
            select(Episode.id).where(
                Episode.drama_id == episode.drama_id,
                Episode.episode_number == body.episode_number,
                Episode.id != episode.id,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=409,
                detail=f"第 {body.episode_number} 集已存在，请换一个集数",
            )
        episode.episode_number = body.episode_number
    try:
        db.commit()
    except IntegrityError:
        # Race with a concurrent create/rename of the same number.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"第 {body.episode_number} 集已存在，请换一个集数",
        ) from None
    return {
        "id": episode.id,
        "episode_number": episode.episode_number,
        "title": episode.title,
        "status": episode.status,
    }


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


@router.post("/episodes/{episode_id}/unpublish")
def unpublish_one(episode_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    """Take one episode off the public catalog; the video object stays intact."""
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="episode not found")
    try:
        transition_episode(db, episode, "unpublished")
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    bump_generation(episode.id)  # invalidate already-signed local URLs
    db.commit()
    return {"status": episode.status}


@router.post("/episodes/bulk-status")
def bulk_episode_status(
    body: EpisodeBulkStatusIn,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_content_writer),
):
    """Publish or take down many episodes in one call.

    Rows that cannot move (wrong source status, missing video, unknown id) are
    reported in ``skipped`` instead of failing the whole batch, so the admin
    selection stays a single click even with a mixed table.
    """
    target = "published" if body.action == "publish" else "unpublished"
    rows = db.scalars(select(Episode).where(Episode.id.in_(body.episode_ids))).all()
    found = {episode.id for episode in rows}
    updated: list[dict] = []
    skipped: list[dict] = []
    for episode in rows:
        if target == "published" and not episode.bunny_video_id:
            skipped.append({"id": episode.id, "status": episode.status, "reason": "no_video"})
            continue
        try:
            transition_episode(db, episode, target)
        except InvalidTransition:
            skipped.append({"id": episode.id, "status": episode.status, "reason": "invalid_transition"})
            continue
        if target == "unpublished":
            bump_generation(episode.id)
        updated.append(
            {"id": episode.id, "episode_number": episode.episode_number, "status": episode.status}
        )
    for missing_id in sorted(set(body.episode_ids) - found):
        skipped.append({"id": missing_id, "status": None, "reason": "not_found"})
    db.commit()
    return {"action": body.action, "updated": updated, "skipped": skipped}


# ---- media assets: covers and subtitles (Bunny Storage + image CDN) ----

COVER_MAX_BYTES = 5 * 1024 * 1024
COVER_EXTS = ("jpg", "png", "webp")
COVER_MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
SUBTITLE_MAX_BYTES = 2 * 1024 * 1024


def _sniff_cover_ext(data: bytes) -> str | None:
    """Trust file bytes, not the browser-supplied Content-Type."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _cover_remote(drama_id: int, ext: str) -> str:
    return f"covers/{drama_id}/cover.{ext}"


def _best_effort_storage(action, *args) -> None:
    if not bunny.live():
        return
    try:
        action(*args)
    except bunny.BunnyError:
        return


def _purge_cover(drama_id: int, ext: str) -> None:
    """Drop the canonical object and any ?v= cache variant."""
    if not bunny.live():
        return
    try:
        url = bunny.image_url(_cover_remote(drama_id, ext))
    except bunny.BunnyError:
        return
    _best_effort_storage(bunny.purge_url, url)
    _best_effort_storage(bunny.purge_url, url + "*")


def _forget_other_covers(drama_id: int, keep_ext: str | None) -> None:
    for ext in COVER_EXTS:
        if ext == keep_ext:
            continue
        _best_effort_storage(bunny.storage_delete, _cover_remote(drama_id, ext))
        _purge_cover(drama_id, ext)


@router.post("/dramas/{drama_id}/cover")
async def upload_cover(
    drama_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_content_writer),
):
    drama = _drama_or_404(db, drama_id)
    if not bunny.live():
        raise HTTPException(status_code=503, detail="图片存储未配置，可先填写封面链接")
    data = await file.read(COVER_MAX_BYTES + 1)
    if not data or len(data) > COVER_MAX_BYTES:
        raise HTTPException(status_code=422, detail="封面大小需在 1 字节到 5MB 之间")
    ext = _sniff_cover_ext(data)
    if ext is None:
        raise HTTPException(status_code=422, detail="封面只支持 JPG、PNG、WebP")
    remote_path = _cover_remote(drama.id, ext)
    try:
        bunny.storage_upload(remote_path, data, COVER_MIME[ext])
    except bunny.BunnyError as exc:
        raise HTTPException(status_code=502, detail=f"封面上传失败：{exc}") from exc
    drama.cover_url = f"{bunny.image_url(remote_path)}?v={time.time_ns()}"
    db.commit()
    _purge_cover(drama.id, ext)
    _forget_other_covers(drama.id, ext)
    return {"cover_url": drama.cover_url}


@router.delete("/dramas/{drama_id}/cover")
def delete_cover(
    drama_id: int,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_content_writer),
):
    drama = _drama_or_404(db, drama_id)
    drama.cover_url = ""
    db.commit()
    _forget_other_covers(drama.id, None)
    return {"cover_url": ""}


@router.post("/episodes/{episode_id}/subtitle")
async def upload_subtitle(
    episode_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_content_writer),
):
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="episode not found")
    if not bunny.live():
        raise HTTPException(status_code=503, detail="bunny storage not configured (BUNNY_MODE=fake)")
    data = await file.read()
    if not data or len(data) > SUBTITLE_MAX_BYTES:
        raise HTTPException(status_code=422, detail="subtitle must be between 1 byte and 2MB")
    name = os.path.basename(file.filename or "subtitle.vtt")
    ext = os.path.splitext(name)[1].lower()
    if ext not in {".vtt", ".srt"}:
        raise HTTPException(status_code=422, detail="subtitle must be .vtt or .srt")
    remote_path = f"subs/{episode.id}/sub{ext}"
    try:
        bunny.storage_upload(remote_path, data, "text/vtt" if ext == ".vtt" else "application/x-subrip")
    except bunny.BunnyError as exc:
        raise HTTPException(status_code=502, detail=f"bunny storage: {exc}") from exc
    episode.subtitle_url = remote_path
    db.commit()
    return {"subtitle_url": remote_path}


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


def _group_counts(db: Session) -> dict[int, int]:
    rows = db.execute(
        select(DramaHomeGroup.group_id, func.count()).group_by(DramaHomeGroup.group_id)
    ).all()
    return {group_id: count for group_id, count in rows}


def _group_out(row: HomeGroup, drama_count: int) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "position": row.position,
        "status": row.status,
        "style": row.style,
        "badge": row.badge,
        "drama_count": drama_count,
        "limit": GROUP_LIMIT,
    }


@router.get("/groups")
def list_groups(db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    rows = db.scalars(select(HomeGroup).order_by(HomeGroup.position, HomeGroup.id)).all()
    counts = _group_counts(db)
    return {"items": [_group_out(row, counts.get(row.id, 0)) for row in rows]}


@router.patch("/groups/{group_id}")
def update_group(group_id: int, body: GroupUpdateIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    """Rename or pause a group. A body that only carries position keeps the old contract."""
    group = db.get(HomeGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="group not found")
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="name is required")
        group.name = name
    if "status" in data and data["status"] is not None:
        group.status = data["status"]
    if "position" in data and data["position"] is not None:
        group.position = data["position"]
    if "style" in data and data["style"] is not None:
        group.style = data["style"]
    if "badge" in data and data["badge"] is not None:
        group.badge = data["badge"]
    if not data:
        raise HTTPException(status_code=422, detail="nothing to update")
    db.commit()
    count = db.scalar(select(func.count()).select_from(DramaHomeGroup).where(DramaHomeGroup.group_id == group.id)) or 0
    return _group_out(group, count)


@router.post("/groups/{group_id}/dramas")
def group_add(group_id: int, body: GroupAddIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    group = db.get(HomeGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="group not found")
    try:
        return add_to_group(db, group, body.drama_id, confirm=body.confirm)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/groups/{group_id}/dramas")
def group_dramas(group_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    group = db.get(HomeGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="group not found")
    links = db.scalars(
        select(DramaHomeGroup)
        .where(DramaHomeGroup.group_id == group_id)
        .order_by(DramaHomeGroup.position_in_group, DramaHomeGroup.drama_id)
    ).all()
    items = []
    for link in links:
        drama = db.get(Drama, link.drama_id)
        if drama is None:
            continue
        items.append(
            {
                "id": drama.id,
                "title": drama.title,
                "status": drama.status,
                "cover_url": drama.cover_url,
                "position_in_group": link.position_in_group,
            }
        )
    count = len(items)
    return {"group": _group_out(group, count), "items": items}


@router.delete("/groups/{group_id}/dramas/{drama_id}")
def group_remove(group_id: int, drama_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    if db.get(HomeGroup, group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    if not remove_from_group(db, group_id, drama_id):
        raise HTTPException(status_code=404, detail="not in group")
    return {"removed": True}


@router.patch("/groups/{group_id}/dramas/{drama_id}")
def group_position(group_id: int, drama_id: int, body: PositionIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    link = db.get(DramaHomeGroup, (group_id, drama_id))
    if link is None:
        raise HTTPException(status_code=404, detail="not in group")
    link.position_in_group = body.position
    db.commit()
    return {"position_in_group": link.position_in_group}


@router.get("/dramas/{drama_id}/groups")
def drama_groups(drama_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    """Groups this drama belongs to, plus every group so the editor can offer the rest."""
    _drama_or_404(db, drama_id)
    rows = db.scalars(select(HomeGroup).order_by(HomeGroup.position, HomeGroup.id)).all()
    counts = _group_counts(db)
    links = {
        link.group_id: link.position_in_group
        for link in db.scalars(select(DramaHomeGroup).where(DramaHomeGroup.drama_id == drama_id)).all()
    }
    items = []
    for row in rows:
        item = _group_out(row, counts.get(row.id, 0))
        item["joined"] = row.id in links
        item["position_in_group"] = links.get(row.id)
        items.append(item)
    return {"items": items}


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


DISCOVER_CODE = "discover"


def _tab_out(row: ChannelTab) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "title_en": row.title_en,
        "title_zh": row.title_zh,
        "position": row.position,
        "enabled": row.enabled,
        "locked": row.locked or row.code == DISCOVER_CODE,
        "group_id": row.group_id,
    }


class TabIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    title_en: str = Field(min_length=1, max_length=64)
    title_zh: str = Field(default="", max_length=64)
    position: int = 0
    enabled: bool = True
    group_id: int | None = None


class TabUpdateIn(BaseModel):
    title_en: str | None = Field(default=None, max_length=64)
    title_zh: str | None = Field(default=None, max_length=64)
    position: int | None = None
    enabled: bool | None = None
    group_id: int | None = None


@router.get("/tabs")
def list_tabs(db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    rows = db.scalars(select(ChannelTab).order_by(ChannelTab.position, ChannelTab.id)).all()
    return {"items": [_tab_out(row) for row in rows]}


@router.post("/tabs")
def save_tab(body: TabIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    if body.group_id is not None and db.get(HomeGroup, body.group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    row = db.scalar(select(ChannelTab).where(ChannelTab.code == body.code))
    if row is None:
        row = ChannelTab(code=body.code, title_en=body.title_en, title_zh=body.title_zh, position=body.position)
        row.enabled = body.enabled
        row.locked = body.code == DISCOVER_CODE
        row.group_id = None if row.locked else body.group_id
        db.add(row)
    else:
        row.position = body.position
        row.title_en = body.title_en
        row.title_zh = body.title_zh
        row.enabled = body.enabled
        if not (row.locked or row.code == DISCOVER_CODE):
            row.group_id = body.group_id
    db.commit()
    return _tab_out(row)


@router.patch("/tabs/{tab_id}")
def update_tab(tab_id: int, body: TabUpdateIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = db.get(ChannelTab, tab_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tab not found")
    data = body.model_dump(exclude_unset=True)
    if "group_id" in data and data["group_id"] is not None and db.get(HomeGroup, data["group_id"]) is None:
        raise HTTPException(status_code=404, detail="group not found")
    locked = row.locked or row.code == DISCOVER_CODE
    if locked and ("position" in data or "enabled" in data or "group_id" in data):
        raise HTTPException(status_code=409, detail="discover tab is fixed")
    if "title_en" in data and data["title_en"] is not None:
        row.title_en = data["title_en"]
    if "title_zh" in data and data["title_zh"] is not None:
        row.title_zh = data["title_zh"]
    if "position" in data and data["position"] is not None:
        row.position = data["position"]
    if "enabled" in data and data["enabled"] is not None:
        row.enabled = data["enabled"]
    if "group_id" in data:
        row.group_id = data["group_id"]
    db.commit()
    return _tab_out(row)


@router.delete("/tabs/{tab_id}")
def delete_tab(tab_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = db.get(ChannelTab, tab_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tab not found")
    if row.locked or row.code == DISCOVER_CODE:
        raise HTTPException(status_code=409, detail="discover tab is fixed")
    db.delete(row)
    db.commit()
    return {"deleted": True}


def _banner_out(row: Banner, drama: Drama | None) -> dict:
    return {
        "id": row.id,
        "slot": row.slot,
        "drama_id": row.drama_id,
        "title": None if drama is None else drama.title,
        "cover_url": None if drama is None else drama.cover_url,
        "kicker": row.kicker,
        "headline": row.headline,
        "status": row.status,
        "position": row.position,
    }


class BannerIn(BaseModel):
    drama_id: int
    slot: str = "home"
    status: str = "online"
    position: int = 0
    kicker: str = Field(default="", max_length=80)
    headline: str = Field(default="", max_length=80)


class BannerUpdateIn(BaseModel):
    drama_id: int | None = None
    status: str | None = None
    position: int | None = None
    kicker: str | None = Field(default=None, max_length=80)
    headline: str | None = Field(default=None, max_length=80)


@router.get("/banners")
def list_banners(slot: str = "home", db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    rows = db.scalars(select(Banner).where(Banner.slot == slot).order_by(Banner.position, Banner.id)).all()
    return {"items": [_banner_out(row, db.get(Drama, row.drama_id) if row.drama_id else None) for row in rows]}


@router.post("/banners")
def save_banner(body: BannerIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    drama = db.get(Drama, body.drama_id)
    if drama is None:
        raise HTTPException(status_code=404, detail="drama not found")
    count = db.scalar(select(func.count()).select_from(Banner).where(Banner.slot == body.slot, Banner.status == "online")) or 0
    if body.status == "online" and count >= 3:
        raise HTTPException(status_code=409, detail="a banner holds 3 dramas at most")
    row = Banner(
        drama_id=drama.id,
        slot=body.slot,
        status=body.status,
        position=body.position,
        kicker=body.kicker,
        headline=body.headline or drama.title,
    )
    db.add(row)
    db.commit()
    return _banner_out(row, drama)


@router.patch("/banners/{banner_id}")
def update_banner(banner_id: int, body: BannerUpdateIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = db.get(Banner, banner_id)
    if row is None:
        raise HTTPException(status_code=404, detail="banner not found")
    data = body.model_dump(exclude_unset=True)
    drama = db.get(Drama, row.drama_id) if row.drama_id else None
    if "drama_id" in data and data["drama_id"] is not None:
        drama = db.get(Drama, data["drama_id"])
        if drama is None:
            raise HTTPException(status_code=404, detail="drama not found")
        row.drama_id = drama.id
    if "status" in data and data["status"] is not None:
        row.status = data["status"]
    if "position" in data and data["position"] is not None:
        row.position = data["position"]
    if "kicker" in data and data["kicker"] is not None:
        row.kicker = data["kicker"]
    if "headline" in data and data["headline"] is not None:
        row.headline = data["headline"]
    db.commit()
    return _banner_out(row, drama)


@router.delete("/banners/{banner_id}")
def delete_banner(banner_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = db.get(Banner, banner_id)
    if row is None:
        raise HTTPException(status_code=404, detail="banner not found")
    db.delete(row)
    db.commit()
    return {"deleted": True}


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


@router.get("/rewards/config")
def get_reward_config(db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    data = reward_config_overview(db)
    data["recent_changes"] = recent_reward_changes(db)
    return data


@router.post("/rewards/config")
def reward_config(body: RewardIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    try:
        row = save_reward_config(db, code=body.code, amounts=body.amounts)
    except AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    actor = user.display_name or user.username
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=user.id,
            action="reward_config_update",
            target_type="reward_config",
            target_id=row.id,
            payload={
                "code": row.code,
                "amounts": list(row.amounts),
                "effective_on": row.effective_on.isoformat(),
                "actor": actor,
            },
        )
    )
    db.commit()
    return {"code": row.code, "amounts": list(row.amounts), "effective_on": row.effective_on.isoformat()}


@router.get("/plans")
def admin_plans(db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    return {"items": list_current_plans(db), "billing_mode": billing_mode(db)}


class BillingModeIn(BaseModel):
    billing_mode: str


@router.get("/billing/settings")
def read_billing_settings(db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    row = get_billing_settings(db)
    return {
        "billing_mode": row.billing_mode,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by": row.updated_by,
    }


@router.post("/billing/settings")
def update_billing_settings(
    body: BillingModeIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin_role)
):
    """Global switch: affects new purchases only, existing subscriptions keep renewing."""
    try:
        row = set_billing_mode(db, body.billing_mode, user.id)
        provisioned = provision_prices_for_mode(db, row.billing_mode)
    except BillingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=user.id,
            action="billing_mode_change",
            target_type="billing_settings",
            target_id=row.id,
            payload={"billing_mode": row.billing_mode, "prices": provisioned},
        )
    )
    db.commit()
    return {
        "billing_mode": row.billing_mode,
        "prices": provisioned,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.post("/plans/{code}/price")
def set_price(code: str, body: PriceIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin_role)):
    try:
        version = change_price(db, code=code, price_cents=body.price_cents, benefits=body.benefits, actor_id=user.id)
    except BillingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "version": version.version,
        "price_cents": version.price_cents,
        "stripe_price_id": version.stripe_price_id,
    }


@router.get("/comments")
def admin_comments(
    status: str | None = None,
    drama_id: int | None = None,
    episode_id: int | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_admin),
):
    """Moderation queue with everything the reviewer needs in one row."""
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    stmt = (
        select(
            Comment,
            Drama.title,
            Episode.episode_number,
            User.display_name,
            User.email,
        )
        .join(Drama, Drama.id == Comment.drama_id)
        .join(Episode, Episode.id == Comment.episode_id, isouter=True)
        .join(User, User.id == Comment.user_id)
    )
    if status:
        stmt = stmt.where(Comment.status == status)
    if drama_id:
        stmt = stmt.where(Comment.drama_id == drama_id)
    if episode_id:
        stmt = stmt.where(Comment.episode_id == episode_id)
    if q and q.strip():
        stmt = stmt.where(Comment.body.ilike(f"%{q.strip()}%"))

    # Tab counters: same filters except status, so the numbers always match what the tabs would show.
    counts_stmt = select(Comment.status, func.count()).group_by(Comment.status)
    if drama_id:
        counts_stmt = counts_stmt.where(Comment.drama_id == drama_id)
    if episode_id:
        counts_stmt = counts_stmt.where(Comment.episode_id == episode_id)
    if q and q.strip():
        counts_stmt = counts_stmt.where(Comment.body.ilike(f"%{q.strip()}%"))
    counts = {"pending": 0, "approved": 0, "deleted": 0, "total": 0}
    for row_status, row_count in db.execute(counts_stmt).all():
        counts[row_status] = int(row_count)
        counts["total"] += int(row_count)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(Comment.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "body": row.body,
                "status": row.status,
                "drama_id": row.drama_id,
                "drama_title": drama_title,
                "episode_id": row.episode_id,
                "episode_number": episode_number,
                "user_id": row.user_id,
                "user_display_name": display_name,
                "user_email": email,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "moderation_verdict": row.moderation_verdict,
                "moderation_score": row.moderation_score,
                "moderation_reason": row.moderation_reason,
                "moderation_model": row.moderation_model,
                "moderation_state": row.moderation_state,
            }
            for row, drama_title, episode_number, display_name, email in rows
        ],
        "total": int(total),
        "counts": counts,
        "page": page,
        "page_size": page_size,
    }


def _moderation_payload(row: ModerationSettings) -> dict:
    return {
        "enabled": row.enabled,
        "base_url": row.base_url,
        "model": row.model,
        "categories": list(row.categories or []),
        "system_prompt": row.system_prompt,
        "approve_threshold": row.approve_threshold,
        "block_threshold": row.block_threshold,
        "fallback_action": row.fallback_action,
        "timeout_seconds": row.timeout_seconds,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by": row.updated_by,
        "key": moderation.key_status(),
        "default_base_url": moderation.default_base_url(),
        "default_categories": moderation.DEFAULT_CATEGORIES,
        "default_prompt": moderation.DEFAULT_SYSTEM_PROMPT,
    }


@router.get("/moderation/settings")
def read_moderation_settings(db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    return _moderation_payload(moderation.get_settings(db))


@router.post("/moderation/settings")
def update_moderation_settings(
    body: ModerationSettingsIn,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_admin_role),
):
    """Save the OpenAI moderation policy and record the change in audit_logs."""
    row = moderation.get_settings(db)
    before = {
        "enabled": row.enabled,
        "model": row.model,
        "base_url": row.base_url,
        "approve_threshold": row.approve_threshold,
        "block_threshold": row.block_threshold,
        "fallback_action": row.fallback_action,
        "timeout_seconds": row.timeout_seconds,
        "categories": list(row.categories or []),
    }
    row.enabled = body.enabled
    row.base_url = body.base_url
    row.model = body.model.strip()
    row.categories = body.categories
    row.system_prompt = body.system_prompt
    row.approve_threshold = body.approve_threshold
    row.block_threshold = body.block_threshold
    row.fallback_action = body.fallback_action
    row.timeout_seconds = body.timeout_seconds
    row.updated_by = user.id
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=user.id,
            action="moderation_settings_change",
            target_type="moderation_settings",
            target_id=row.id,
            payload={
                "before": before,
                "after": {
                    "enabled": body.enabled,
                    "model": body.model.strip(),
                    "base_url": body.base_url,
                    "approve_threshold": body.approve_threshold,
                    "block_threshold": body.block_threshold,
                    "fallback_action": body.fallback_action,
                    "timeout_seconds": body.timeout_seconds,
                    "categories": body.categories,
                },
                "prompt_chars": len(body.system_prompt),
            },
        )
    )
    db.commit()
    db.refresh(row)
    return _moderation_payload(row)


@router.post("/moderation/test")
def test_moderation(
    body: ModerationTestIn,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_admin),
):
    """Dry-run one piece of text against the saved policy. Writes nothing."""
    settings = moderation.get_settings(db)
    try:
        result = moderation.classify(body.text, settings)
    except moderation.ModerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "enabled": settings.enabled,
        "status": moderation.decide(result.verdict, result.score, settings),
        "result": result.as_dict(),
    }


@router.post("/comments/{comment_id}")
def moderate(comment_id: int, body: CommentModIn, db: Session = Depends(get_db), user: AdminUser = Depends(require_content_writer)):
    row = db.get(Comment, comment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="comment not found")
    if body.status not in {"approved", "deleted", "pending"}:
        raise HTTPException(status_code=422, detail="bad status")
    row.status = body.status
    # A human decision wins over a late model response: the worker's final
    # UPDATE is guarded by moderation_state='running'.
    if row.moderation_state in {"queued", "running"}:
        row.moderation_state = "canceled"
    db.commit()
    return {"status": row.status, "moderation_state": row.moderation_state}


GENDERS = ("unspecified", "female", "male", "other")


@router.get("/users")
def list_users(q: str | None = None, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    stmt = select(User)
    if q:
        needle = q.strip()
        if needle:
            stmt = stmt.where(
                or_(
                    User.display_name.ilike(f"%{needle}%"),
                    User.public_id == needle,
                    User.email.ilike(f"%{needle}%"),
                )
            )
    rows = db.scalars(stmt.order_by(User.id.desc()).limit(50)).all()
    items = []
    for row in rows:
        wallet = db.get(WalletAccount, row.id)
        # Read the entitlement the same way playback does. Looking at the
        # subscription row directly reported `current_period_end`, which for a
        # one-time pass is just the purchase moment — the real expiry lives in
        # `pass_end`, so the column showed "valid until <a past date>".
        membership = active_membership(db, row.id)
        items.append(
            {
                "id": row.id,
                "public_id": row.public_id,
                "display_name": row.display_name,
                "email": row.email,
                "avatar_url": row.avatar_url,
                "gender": row.gender,
                "bio": row.bio,
                "region": row.region,
                "locale": row.locale,
                "coins": 0 if wallet is None else wallet.balance,
                "member": membership["active"],
                "member_until": membership["until"],
                "canceling": membership["canceling"],
                "billing_mode": membership["billing_mode"],
                "created_at": row.created_at.isoformat(),
            }
        )
    return {"items": items}


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    body: UserUpdateIn,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_admin_role),
):
    row = db.get(User, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")

    data = body.model_dump(exclude_unset=True)
    before: dict = {}

    if "display_name" in data:
        name = (data["display_name"] or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="昵称不能为空")
        row.display_name = name
        before["display_name"] = name
    if "email" in data:
        email = (data["email"] or "").strip().lower() or None
        if email is not None and ("@" not in email or email.startswith("@") or email.endswith("@")):
            raise HTTPException(status_code=422, detail="邮箱格式不正确")
        if email is not None:
            conflict = db.scalar(select(User.id).where(User.email == email, User.id != user_id))
            if conflict is not None:
                raise HTTPException(status_code=409, detail="该邮箱已被其他用户绑定")
        row.email = email
        before["email"] = email
    if "gender" in data:
        gender = (data["gender"] or "").strip() or "unspecified"
        if gender not in GENDERS:
            raise HTTPException(status_code=422, detail="性别取值不合法")
        row.gender = gender
        before["gender"] = gender
    if "avatar_url" in data:
        row.avatar_url = (data["avatar_url"] or "").strip()
        before["avatar_url"] = row.avatar_url
    if "bio" in data:
        row.bio = (data["bio"] or "").strip()
        before["bio"] = row.bio
    if "region" in data:
        row.region = (data["region"] or "").strip()
        before["region"] = row.region
    if "locale" in data:
        row.locale = (data["locale"] or "").strip() or "en"
        before["locale"] = row.locale

    if not before:
        return {"updated": False}

    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor.id,
            action="user_update",
            target_type="user",
            target_id=user_id,
            payload=before,
        )
    )
    db.commit()
    return {"updated": True, "user": {"id": row.id, "public_id": row.public_id, **before}}


@router.post("/users/{user_id}/coins")
def adjust(user_id: int, body: AdjustIn, db: Session = Depends(get_db), actor: AdminUser = Depends(require_admin_role)):
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    wallet = db.get(WalletAccount, user_id, with_for_update=True)
    if wallet is None:
        # Legacy rows may predate the wallet table; create it on demand so the
        # operator does not have to run a migration just to grant coins.
        wallet = WalletAccount(user_id=user_id, balance=0)
        db.add(wallet)
        db.flush()
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


class MembershipGrantIn(BaseModel):
    plan_code: str
    days: int | None = Field(default=None, ge=1, le=3650)
    # extend = stack on what is left; restart = count from today (burns the rest)
    mode: str = "extend"
    reason: str = ""


class MembershipRevokeIn(BaseModel):
    # now = lose access immediately; period_end = keep access, stop renewing
    mode: str = "now"
    reason: str = ""


@router.get("/users/{user_id}/membership")
def user_membership(
    user_id: int,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_admin),
):
    """会员详情：真实到期时间、来源、最近 6 条记录与上一次后台调整。"""
    account = db.get(User, user_id)
    if account is None:
        raise HTTPException(status_code=404, detail="user not found")
    payload = billing_membership_detail(db, user_id)
    payload["user"] = {
        "id": account.id,
        "public_id": account.public_id,
        "display_name": account.display_name,
        "email": account.email,
    }
    last = payload.get("last_adjust")
    if last and last.get("actor_id"):
        staff = db.get(AdminUser, last["actor_id"])
        if staff is not None:
            last["actor_name"] = staff.display_name or staff.username
    return payload


@router.post("/users/{user_id}/membership")
def grant_user_membership(
    user_id: int,
    body: MembershipGrantIn,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_admin_role),
):
    """后台开通 / 续期：只叠 `pass_end`，不建订单、不碰 Stripe 价格。"""
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    try:
        return billing_grant_membership(
            db,
            user_id=user_id,
            plan_code=body.plan_code,
            days=body.days,
            mode=body.mode,
            reason=body.reason,
            actor_id=actor.id,
        )
    except BillingError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/users/{user_id}/membership")
def revoke_user_membership(
    user_id: int,
    body: MembershipRevokeIn,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_admin_role),
):
    """后台取消：Stripe 订阅先打远程，成功后本地两个时间窗一起收回。"""
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    try:
        return billing_revoke_membership(
            db,
            user_id=user_id,
            mode=body.mode,
            reason=body.reason,
            actor_id=actor.id,
        )
    except BillingError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


ORDER_STATUSES = ("created", "paid", "failed", "refunded")
ORDER_BILLING_MODES = ("subscription", "one_time")

ORDER_LEDGER_JOINS = (
    (User, User.id == Order.user_id),
    (PlanVersion, PlanVersion.id == Order.plan_version_id),
    (Plan, Plan.id == PlanVersion.plan_id),
)


def _order_payload(order: Order, account: User | None, version: PlanVersion | None, plan: Plan | None) -> dict:
    """One order row: legacy flat fields for the dashboard + nested user/plan."""
    return {
        "order_no": order.order_no,
        "user_id": order.user_id,
        "amount_cents": order.amount_cents,
        "currency": order.currency,
        "billing_mode": order.billing_mode,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "external_txn_id": order.external_txn_id,
        "user": None
        if account is None
        else {
            "id": account.id,
            "public_id": account.public_id,
            "display_name": account.display_name,
            "email": account.email,
            "avatar_url": account.avatar_url,
            "region": account.region,
            "locale": account.locale,
            "created_at": account.created_at.isoformat(),
        },
        "plan": None
        if plan is None
        else {
            "code": plan.code,
            "name_en": plan.name_en,
            "name_zh": plan.name_zh,
            "version": None if version is None else version.version,
            "price_cents": None if version is None else version.price_cents,
            "currency": None if version is None else version.currency,
            "benefits": None if version is None else (version.benefits or {}),
        },
    }


@router.get("/finance/orders")
def finance_orders(
    q: str | None = None,
    status: str | None = None,
    billing_mode: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_admin),
):
    """订单明细：下单用户 + 套餐快照 + 渠道流水，支持搜索 / 筛选 / 分页。

    数据看板（/dashboard）复用本函数取最近订单，因此顶层保留 user_id /
    amount_cents / status / created_at 这些旧字段，新增信息放在 user / plan 里。
    """
    conditions = []
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        conditions.append(
            or_(
                Order.order_no.ilike(like),
                Order.external_txn_id.ilike(like),
                User.email.ilike(like),
                User.display_name.ilike(like),
                User.public_id == needle,
            )
        )
    if status in ORDER_STATUSES:
        conditions.append(Order.status == status)
    if billing_mode in ORDER_BILLING_MODES:
        conditions.append(Order.billing_mode == billing_mode)

    joins = ORDER_LEDGER_JOINS
    count_stmt = select(func.count(Order.id))
    for target, onclause in joins:
        count_stmt = count_stmt.outerjoin(target, onclause)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = db.scalar(count_stmt) or 0

    stmt = select(Order, User, PlanVersion, Plan)
    for target, onclause in joins:
        stmt = stmt.outerjoin(target, onclause)
    if conditions:
        stmt = stmt.where(*conditions)
    rows = db.execute(
        stmt.order_by(Order.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()

    items = [_order_payload(*row) for row in rows]

    grouped = db.execute(
        select(
            Order.status,
            func.count(Order.id),
            func.coalesce(func.sum(Order.amount_cents), 0),
        ).group_by(Order.status)
    ).all()
    counts = {row[0]: {"orders": int(row[1]), "amount_cents": int(row[2])} for row in grouped}
    paid = counts.get("paid", {"orders": 0, "amount_cents": 0})
    refunded = counts.get("refunded", {"orders": 0, "amount_cents": 0})
    created = counts.get("created", {"orders": 0, "amount_cents": 0})
    failed = counts.get("failed", {"orders": 0, "amount_cents": 0})
    total_orders = sum(item["orders"] for item in counts.values()) or 0

    # 今日成交按后台统一业务日（洛杉矶时区）统计，与数据看板口径一致。
    from app.modules.account.service import LA, la_today

    day_start = datetime.combine(la_today(), datetime.min.time(), tzinfo=LA).astimezone(
        timezone.utc
    )
    today_paid_cents = (
        db.scalar(
            select(func.coalesce(func.sum(Order.amount_cents), 0)).where(
                Order.status == "paid", Order.paid_at >= day_start
            )
        )
        or 0
    )
    today_orders = (
        db.scalar(select(func.count(Order.id)).where(Order.created_at >= day_start)) or 0
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "summary": {
            "total_orders": total_orders,
            "paid_orders": paid["orders"],
            "refunded_orders": refunded["orders"],
            "created_orders": created["orders"],
            "failed_orders": failed["orders"],
            "paid_cents": paid["amount_cents"],
            "refunded_cents": refunded["amount_cents"],
            "pending_cents": created["amount_cents"],
            # 口径：gross = 曾经收到的钱（含后来退掉的），net = 退款后真正留下的。
            "gross_cents": paid["amount_cents"] + refunded["amount_cents"],
            "net_cents": paid["amount_cents"],
            "today_paid_cents": int(today_paid_cents),
            "today_orders": int(today_orders),
            "currency": "USD",
        },
    }


@router.get("/finance/orders/{order_no}")
def finance_order_detail(
    order_no: str,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_admin),
):
    """单笔订单详情：在列表字段之外补充该用户的累计消费与当前会员状态。"""
    stmt = select(Order, User, PlanVersion, Plan)
    for target, onclause in ORDER_LEDGER_JOINS:
        stmt = stmt.outerjoin(target, onclause)
    row = db.execute(stmt.where(Order.order_no == order_no)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="order not found")

    payload = _order_payload(*row)
    account = row[1]
    if account is None:
        payload["user_stats"] = None
        payload["membership"] = None
        return payload

    paid_orders, paid_cents = db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.amount_cents), 0)).where(
            Order.user_id == account.id, Order.status == "paid"
        )
    ).one()
    refunded_cents = (
        db.scalar(
            select(func.coalesce(func.sum(Order.amount_cents), 0)).where(
                Order.user_id == account.id, Order.status == "refunded"
            )
        )
        or 0
    )
    payload["user_stats"] = {
        "paid_orders": int(paid_orders),
        "paid_cents": int(paid_cents),
        "refunded_cents": int(refunded_cents),
    }
    payload["membership"] = active_membership(db, account.id)
    return payload


@router.get("/finance/ledger")
def finance_ledger(db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    rows = db.scalars(select(CoinLedgerEntry).order_by(CoinLedgerEntry.id.desc()).limit(100)).all()
    return {"items": [{"user_id": row.user_id, "amount": row.amount, "biz_type": row.biz_type, "note": row.note} for row in rows]}


@router.get("/dashboard")
def dashboard(
    day: date | None = None,
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_admin),
):
    """Aggregates for one business day (dashboard timezone) plus the trend window."""
    from app.modules.account.service import la_today

    target = day or la_today()
    data = build_dashboard(db, target, days=days)
    data["recent_orders"] = finance_orders(page=1, page_size=5, db=db, user=user)["items"]
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
