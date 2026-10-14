"""Admin routes for stages 3 through 14."""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.account.models import User
from app.modules.account.service import (
    AccountError,
    recent_reward_changes,
    reward_config_overview,
    save_reward_config,
)
from app.modules.analytics.service import dashboard as build_dashboard
from app.modules.billing.models import Order, Subscription
from app.modules.billing.service import BillingError, change_price, list_current_plans
from app.modules.common import bunny
from app.modules.common.models import AuditLog
from app.modules.content.models import Drama, DramaHomeGroup, Episode, HomeGroup, MediaUpload
from app.modules.content.publish_service import (
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
    StoreLink,
    Testimonial,
)
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


class GroupIn(BaseModel):
    name: str
    position: int = 0


class GroupUpdateIn(BaseModel):
    """Rename, reorder, or pause a homepage group. Any subset of fields is accepted."""

    name: str | None = Field(default=None, max_length=64)
    position: int | None = None
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _status(cls, value: str | None) -> str | None:
        if value is not None and value not in {"active", "inactive"}:
            raise ValueError("status must be active or inactive")
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

    remote_status: int | None = None
    duration_updated = False
    source = "fake"
    if bunny.live():
        source = "bunny"
        if episode.status == "transcoding" or episode.duration_seconds == 0:
            try:
                remote = bunny.stream_video(episode.bunny_video_id)
            except bunny.BunnyError as exc:
                raise HTTPException(status_code=502, detail=f"bunny upstream: {exc}") from exc
            remote_status = int(remote.get("status", 0))
            length = int(remote.get("length") or 0)
            if length > 0 and episode.duration_seconds != length:
                # Bunny's transcoded length is what the player will report.
                episode.duration_seconds = length
                duration_updated = True

    if episode.status == "transcoding":
        target = None
        if remote_status is None or remote_status == 4:
            target = "in_review"
        elif remote_status >= 5:
            target = "transcode_failed"
        if target is not None:
            try:
                transition_episode(db, episode, target)
            except InvalidTransition as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            upload = _latest_media_upload(db, episode.id, episode.bunny_video_id)
            if upload is not None:
                upload.status = "completed" if target == "in_review" else "failed"
            db.commit()
        elif duration_updated:
            db.commit()
    elif duration_updated:
        db.commit()

    return {
        "episode_id": episode.id,
        "status": episode.status,
        "remote_status": remote_status,
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
    try:
        transition_episode(db, episode, "transcode_failed")
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    upload = _latest_media_upload(db, episode.id, payload.video_id or episode.bunny_video_id)
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
        "drama_count": drama_count,
        "limit": 20,
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
        sub = db.scalar(select(Subscription).where(Subscription.user_id == row.id, Subscription.status == "active"))
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
                "member": sub is not None,
                "member_until": None if sub is None else sub.current_period_end.isoformat(),
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
