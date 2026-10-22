"""Partner surface: /partner/v1 — the only API a 上剧方 (supplier) may call.

Every query is scoped by the caller's `supplier_id`; there is no code path that
returns or mutates another tenant's rows. P1 shipped read-only endpoints, P2
adds the upload pipeline (create drama / episodes / TUS session / cover) plus
self-service API keys with a partner-only scope whitelist.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.billing.models import Order
from app.modules.common.models import AuditLog
from app.modules.ops import settlements as settlement_service
from app.modules.content import upload_service
from app.modules.content.models import Drama, DramaTag, Episode, MediaUpload, Tag
from app.modules.content.service import InvalidTransition, transition_episode
from app.modules.ops import api_keys as api_key_service
from app.modules.ops import service as ops_service
from app.modules.ops.flow_router import (
    COVER_MAX_BYTES,
    COVER_MIME,
    _cover_remote,
    _forget_other_covers,
    _purge_cover,
    _require_fake_mode,
    _sniff_cover_ext,
)
from app.modules.ops.models import (
    ROLE_PARTNER,
    STATUS_ACTIVE,
    AdminApiKey,
    AdminUser,
    Supplier,
    SupplierSettlement,
    SupplierShareHistory,
)
from app.modules.playback.models import PlaybackEvent
from app.modules.wallet.models import CoinLedgerEntry

router = APIRouter(prefix="/partner/v1", tags=["partner"])

MIN_FREE_EPISODES = 5
DEFAULT_FREE_EPISODES = 5
MAX_EPISODES = 300


# ---- auth ----


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"code": "missing_credential", "message": "缺少 Authorization 头"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return auth[len("Bearer "):]


def _resolve(request: Request, db: Session) -> tuple[AdminUser, AdminApiKey | None]:
    credential = _bearer_token(request)
    if api_key_service.looks_like_key(credential):
        user, key = api_key_service.resolve_credential(db, credential)
        # Defence in depth: even if a future dependency forgets a role check,
        # a partner key can never leave its own surface.
        if user.role == ROLE_PARTNER and not request.url.path.startswith("/partner/v1/"):
            raise HTTPException(
                status_code=403,
                detail={"code": "partner_scope_denied", "message": "上剧方密钥只能访问合作方接口"},
            )
        request.state.api_key_id = key.id
        request.state.api_key_staff_id = user.id
        return user, key
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
    return user, None


def _require_supplier(db: Session, user: AdminUser) -> Supplier:
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
    return supplier


def require_partner(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    user, key = _resolve(request, db)
    _require_supplier(db, user)
    if key is not None:
        api_key_service.authorize(key, request.method, request.url.path, user.role)
    return user


def require_partner_session(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    """Credential management must never be reachable with a key."""
    if api_key_service.looks_like_key(_bearer_token(request)):
        raise HTTPException(
            status_code=403,
            detail={"code": "session_required", "message": "该接口只能用登录会话调用"},
        )
    user, _ = _resolve(request, db)
    _require_supplier(db, user)
    return user


# ---- schemas ----


class PartnerPasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class DramaCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    synopsis: str = Field(default="", max_length=5000)
    free_episode_count: int = Field(default=DEFAULT_FREE_EPISODES, ge=0, le=1000)
    country_allowlist: str = Field(default="", max_length=200)
    age_rating: int | None = Field(default=None, ge=0, le=18)


class DramaUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    synopsis: str | None = Field(default=None, max_length=5000)
    cover_url: str | None = Field(default=None, max_length=500)
    tag_ids: list[int] | None = None
    free_episode_count: int | None = Field(default=None, ge=0, le=1000)
    country_allowlist: str | None = Field(default=None, max_length=200)
    age_rating: int | None = Field(default=None, ge=0, le=18)


class EpisodeCreateIn(BaseModel):
    episode_number: int | None = Field(default=None, ge=1, le=MAX_EPISODES)
    title: str = Field(default="", max_length=200)


class EpisodeUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    episode_number: int | None = Field(default=None, ge=1, le=MAX_EPISODES)


class UploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    mime: str = Field(min_length=1, max_length=64)
    size_bytes: int = Field(ge=1)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    duration_seconds: int = Field(default=0, ge=0)


class PartnerKeyCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    scopes: list[str] | None = None
    expires_in_days: int | None = Field(default=90)


class PartnerKeyRevokeIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class PartnerKeyUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    scopes: list[str] | None = None


# ---- helpers ----


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _audit(db: Session, actor: AdminUser, action: str, drama_id: int | None, payload: dict) -> None:
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor.id,
            action=action,
            target_type="drama" if drama_id else "supplier",
            target_id=drama_id or actor.supplier_id,
            payload={**payload, "actor_role": ROLE_PARTNER, "supplier_id": actor.supplier_id},
        )
    )


def _partner_drama_or_404(db: Session, user: AdminUser, drama_id: int) -> Drama:
    """Ownership first, state later: a 403 would confirm the row exists."""
    row = db.scalar(
        select(Drama).where(Drama.id == drama_id, Drama.supplier_id == user.supplier_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="drama not found")
    return row


def _editable(drama: Drama) -> None:
    """Suppliers may only touch drafts (including a rejected draft under repair)."""
    if drama.status != "draft":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "drama_locked",
                "message": f"剧集当前状态 {drama.status}，上剧方只能编辑草稿",
            },
        )
    if drama.review_state == "pending":
        raise HTTPException(
            status_code=409,
            detail={"code": "drama_in_review", "message": "审核中不可修改"},
        )


def _episode_or_404(db: Session, user: AdminUser, episode_id: int) -> Episode:
    row = db.scalar(
        select(Episode)
        .join(Drama, Drama.id == Episode.drama_id)
        .where(Episode.id == episode_id, Drama.supplier_id == user.supplier_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="episode not found")
    return row


def _episode_count(db: Session, drama_id: int) -> int:
    return db.scalar(select(func.count(Episode.id)).where(Episode.drama_id == drama_id)) or 0


def _drama_out(db: Session, drama: Drama, *, with_episodes: bool = False) -> dict:
    payload = {
        "id": drama.id,
        "title": drama.title,
        "synopsis": drama.synopsis,
        "cover_url": drama.cover_url,
        "status": drama.status,
        "review_state": drama.review_state,
        "review_note": drama.review_note,
        "free_episode_count": drama.free_episode_count,
        # Read-only on this surface: pricing belongs to the platform.
        "coin_price_per_episode": drama.coin_price_per_episode,
        "play_count": drama.play_count,
        "episode_count": _episode_count(db, drama.id),
        "submitted_at": _iso(drama.submitted_at),
        "reviewed_at": _iso(drama.reviewed_at),
        "updated_at": _iso(drama.updated_at),
        "editable": drama.status == "draft" and drama.review_state != "pending",
    }
    if with_episodes:
        episodes = db.scalars(
            select(Episode)
            .where(Episode.drama_id == drama.id)
            .order_by(Episode.episode_number)
        ).all()
        uploads = {
            row.episode_id: row.status
            for row in db.scalars(
                select(MediaUpload)
                .where(MediaUpload.episode_id.in_([item.id for item in episodes]))
                .order_by(MediaUpload.id)
            ).all()
        }
        payload["episodes"] = [
            {
                "id": item.id,
                "episode_number": item.episode_number,
                "title": item.title,
                "status": item.status,
                "upload_status": uploads.get(item.id),
                "duration_seconds": item.duration_seconds,
                "size_bytes": item.size_bytes,
            }
            for item in episodes
        ]
        payload["tag_ids"] = [
            row.tag_id
            for row in db.scalars(select(DramaTag).where(DramaTag.drama_id == drama.id)).all()
        ]
    return payload


def _replace_tags(db: Session, drama: Drama, tag_ids: list[int]) -> None:
    rows = db.scalars(select(Tag).where(Tag.id.in_(tag_ids))).all() if tag_ids else []
    if len(rows) != len(set(tag_ids)):
        raise HTTPException(status_code=422, detail="存在无效的标签")
    if any(tag.status != "enabled" for tag in rows):
        raise HTTPException(status_code=422, detail="存在已下线的标签")
    db.execute(delete(DramaTag).where(DramaTag.drama_id == drama.id))
    for tag in rows:
        db.add(DramaTag(drama_id=drama.id, tag_id=tag.id))


# ---- profile / auth ----


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


@router.post("/auth/password")
def change_password(
    body: PartnerPasswordIn,
    user: AdminUser = Depends(require_partner_session),
    db: Session = Depends(get_db),
):
    if not ops_service.verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if body.new_password == body.old_password:
        raise HTTPException(status_code=422, detail="新密码不能与原密码相同")
    killed = ops_service.revoke_user_sessions(user.id)
    user.password_hash = ops_service.hash_password(body.new_password)
    db.commit()
    return {"revoked_sessions": killed}


@router.post("/auth/logout")
def logout(request: Request, user: AdminUser = Depends(require_partner_session)):
    try:
        ops_service.logout(_bearer_token(request))
    except ops_service.SessionStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail="会话存储不可用") from exc
    return {"ok": True}


# ---- dramas ----


@router.get("/tags")
def list_tags(
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    """Only enabled tags; suppliers may pick from the platform taxonomy."""
    rows = db.scalars(
        select(Tag).where(Tag.status == "enabled").order_by(Tag.category, Tag.id)
    ).all()
    return {
        "items": [
            {
                "id": tag.id,
                "name_zh": tag.name_zh,
                "name_en": tag.name_en,
                "category": tag.category,
            }
            for tag in rows
        ]
    }


@router.get("/dramas")
def list_dramas(
    status: str | None = None,
    review_state: str | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    base = select(Drama).where(Drama.supplier_id == user.supplier_id)
    if status:
        base = base.where(Drama.status == status)
    if review_state:
        base = base.where(Drama.review_state == review_state)
    if q and q.strip():
        base = base.where(Drama.title.ilike(f"%{q.strip()}%"))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.order_by(Drama.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    counts = dict(
        db.execute(
            select(Drama.status, func.count(Drama.id))
            .where(Drama.supplier_id == user.supplier_id)
            .group_by(Drama.status)
        ).all()
    )
    counts["all"] = db.scalar(
        select(func.count(Drama.id)).where(Drama.supplier_id == user.supplier_id)
    ) or 0
    review_counts = dict(
        db.execute(
            select(Drama.review_state, func.count(Drama.id))
            .where(Drama.supplier_id == user.supplier_id)
            .group_by(Drama.review_state)
        ).all()
    )
    def _count(*where) -> int:
        return db.scalar(
            select(func.count(Drama.id)).where(Drama.supplier_id == user.supplier_id, *where)
        ) or 0

    stage_counts = {
        "all": total,
        "draft": _count(Drama.status == "draft", Drama.review_state.in_(["none", "rejected"])),
        "pending": _count(Drama.review_state == "pending"),
        "approved": _count(
            Drama.review_state == "approved", Drama.status.in_(["draft", "in_review"])
        ),
        "published": _count(Drama.status == "published"),
        "unpublished": _count(Drama.status == "unpublished"),
    }
    return {
        "items": [_drama_out(db, row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "status_counts": counts,
        "review_counts": review_counts,
        "stage_counts": stage_counts,
    }


@router.post("/dramas", status_code=201)
def create_drama(
    body: DramaCreateIn,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="剧名不能为空")
    exists = db.scalar(
        select(Drama).where(Drama.supplier_id == user.supplier_id, Drama.title == title)
    )
    if exists is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "title_exists", "message": "你已有同名剧", "drama_id": exists.id},
        )
    drama = Drama(
        title=title,
        synopsis=body.synopsis.strip(),
        supplier_id=user.supplier_id,
        free_episode_count=body.free_episode_count,
        country_allowlist=body.country_allowlist.strip(),
        age_rating=body.age_rating,
        coin_price_per_episode=0,  # pricing stays platform-side
        status="draft",
        review_state="none",
    )
    db.add(drama)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名剧已存在") from exc
    _audit(db, user, "partner_drama_create", drama.id, {"title": drama.title})
    db.commit()
    db.refresh(drama)
    return _drama_out(db, drama, with_episodes=True)


@router.get("/dramas/{drama_id}")
def get_drama(
    drama_id: int,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    row = _partner_drama_or_404(db, user, drama_id)
    return _drama_out(db, row, with_episodes=True)


@router.patch("/dramas/{drama_id}")
def update_drama(
    drama_id: int,
    body: DramaUpdateIn,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    drama = _partner_drama_or_404(db, user, drama_id)
    _editable(drama)
    data = body.model_dump(exclude_unset=True)

    if data.get("title") is not None:
        title = data["title"].strip()
        if not title:
            raise HTTPException(status_code=422, detail="剧名不能为空")
        other = db.scalar(
            select(Drama).where(
                Drama.supplier_id == user.supplier_id,
                Drama.title == title,
                Drama.id != drama.id,
            )
        )
        if other is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "title_exists", "message": "你已有同名剧", "drama_id": other.id},
            )
        drama.title = title
    for field in ("synopsis", "cover_url", "country_allowlist"):
        if data.get(field) is not None:
            setattr(drama, field, data[field].strip())
    if data.get("free_episode_count") is not None:
        drama.free_episode_count = data["free_episode_count"]
    if "age_rating" in data:
        drama.age_rating = data["age_rating"]
    if body.tag_ids is not None:
        _replace_tags(db, drama, body.tag_ids)
    if drama.review_state == "rejected":
        # Editing a rejected drama clears the verdict; it must be resubmitted.
        drama.review_state = "none"
        drama.review_note = None
    _audit(db, user, "partner_drama_update", drama.id, {"fields": sorted(data)})
    db.commit()
    db.refresh(drama)
    return _drama_out(db, drama, with_episodes=True)


@router.post("/dramas/{drama_id}/cover")
async def upload_cover(
    drama_id: int,
    file: UploadFile = File(...),
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    from app.modules.common import bunny

    drama = _partner_drama_or_404(db, user, drama_id)
    _editable(drama)
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
    drama.cover_url = f"{bunny.image_url(remote_path)}?v={datetime.now(timezone.utc).timestamp()}"
    _audit(db, user, "partner_cover_upload", drama.id, {})
    db.commit()
    _purge_cover(drama.id, ext)
    _forget_other_covers(drama.id, ext)
    return {"cover_url": drama.cover_url}


# ---- episodes ----


@router.post("/dramas/{drama_id}/episodes", status_code=201)
def create_episode(
    drama_id: int,
    body: EpisodeCreateIn,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    drama = _partner_drama_or_404(db, user, drama_id)
    _editable(drama)
    count = _episode_count(db, drama.id)
    if count >= MAX_EPISODES:
        raise HTTPException(status_code=422, detail=f"单部剧最多 {MAX_EPISODES} 集")
    number = body.episode_number
    if number is None:
        number = (
            db.scalar(
                select(func.max(Episode.episode_number)).where(Episode.drama_id == drama.id)
            )
            or 0
        ) + 1
    if db.scalar(
        select(Episode.id).where(
            Episode.drama_id == drama.id, Episode.episode_number == number
        )
    ):
        raise HTTPException(status_code=409, detail=f"第 {number} 集已存在")
    episode = Episode(
        drama_id=drama.id,
        episode_number=number,
        title=body.title.strip() or f"第 {number} 集",
        status="not_uploaded",
    )
    db.add(episode)
    db.flush()
    _audit(db, user, "partner_episode_create", drama.id, {"episode": number})
    db.commit()
    db.refresh(episode)
    return {
        "id": episode.id,
        "episode_number": episode.episode_number,
        "title": episode.title,
        "status": episode.status,
    }


@router.patch("/episodes/{episode_id}")
def update_episode(
    episode_id: int,
    body: EpisodeUpdateIn,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    episode = _episode_or_404(db, user, episode_id)
    drama = db.get(Drama, episode.drama_id)
    _editable(drama)
    if body.episode_number is not None and body.episode_number != episode.episode_number:
        taken = db.scalar(
            select(Episode.id).where(
                Episode.drama_id == episode.drama_id,
                Episode.episode_number == body.episode_number,
                Episode.id != episode.id,
            )
        )
        if taken:
            raise HTTPException(status_code=409, detail=f"第 {body.episode_number} 集已存在")
        episode.episode_number = body.episode_number
    if body.title is not None:
        episode.title = body.title.strip()
    db.commit()
    return {
        "id": episode.id,
        "title": episode.title,
        "episode_number": episode.episode_number,
    }


@router.delete("/episodes/{episode_id}", status_code=204)
def delete_episode(
    episode_id: int,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    episode = _episode_or_404(db, user, episode_id)
    drama = db.get(Drama, episode.drama_id)
    _editable(drama)
    if episode.status != "not_uploaded":
        raise HTTPException(
            status_code=409,
            detail={"code": "episode_has_video", "message": "已上传视频的分集不能删除"},
        )
    number = episode.episode_number
    db.delete(episode)
    _audit(db, user, "partner_episode_delete", drama.id, {"episode": number})
    db.commit()
    return None


@router.post("/episodes/{episode_id}/upload-session")
def upload_session(
    episode_id: int,
    body: UploadIn,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    from app.modules.common import bunny

    episode = _episode_or_404(db, user, episode_id)
    drama = db.get(Drama, episode.drama_id)
    _editable(drama)
    try:
        payload = upload_service.create_upload_session(
            db,
            episode=episode,
            filename=body.filename,
            mime=body.mime,
            size_bytes=body.size_bytes,
            width=body.width,
            height=body.height,
            duration_seconds=body.duration_seconds,
        )
    except upload_service.UploadRejected as exc:
        raise HTTPException(status_code=422, detail=exc.reason) from exc
    except bunny.BunnyError as exc:
        raise HTTPException(status_code=502, detail=f"bunny upstream: {exc}") from exc
    # Fake mode's receiver lives under the staff surface; point the supplier at
    # its own copy so the path guard stays intact.
    if not bunny.live() and payload.get("upload_url", "").startswith("/admin/v1/"):
        payload["upload_url"] = payload["upload_url"].replace("/admin/v1/", "/partner/v1/")
    return payload


def _own_upload(db: Session, user: AdminUser, video_id: str) -> MediaUpload:
    upload = db.scalar(
        select(MediaUpload)
        .join(Episode, Episode.id == MediaUpload.episode_id)
        .join(Drama, Drama.id == Episode.drama_id)
        .where(
            MediaUpload.video_id == video_id,
            Drama.supplier_id == user.supplier_id,
        )
    )
    if upload is None:
        raise HTTPException(status_code=404, detail="unknown video id")
    return upload


@router.post("/dev-tus/{video_id}")
def partner_dev_tus_create(
    video_id: str,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    """Fake-mode TUS receiver for local development (never used in production)."""
    _require_fake_mode()
    _own_upload(db, user, video_id)
    return Response(
        status_code=201,
        headers={"Location": f"/partner/v1/dev-tus/{video_id}", "Tus-Resumable": "1.0.0"},
    )


@router.head("/dev-tus/{video_id}")
def partner_dev_tus_head(
    video_id: str,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    _require_fake_mode()
    upload = _own_upload(db, user, video_id)
    offset = upload.size_bytes if upload.status == "completed" else 0
    return Response(
        status_code=200,
        headers={
            "Upload-Offset": str(offset),
            "Upload-Length": str(upload.size_bytes),
            "Tus-Resumable": "1.0.0",
        },
    )


@router.patch("/dev-tus/{video_id}")
async def partner_dev_tus_patch(
    video_id: str,
    request: Request,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    _require_fake_mode()
    upload = _own_upload(db, user, video_id)
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


@router.post("/episodes/{episode_id}/upload-headers")
def upload_headers(
    episode_id: int,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    from app.modules.common import bunny

    episode = _episode_or_404(db, user, episode_id)
    if episode.status != "transcoding":
        raise HTTPException(
            status_code=409,
            detail={"code": "upload_not_resumable", "message": "只有转码中的分集可以续传"},
        )
    upload = db.scalars(
        select(MediaUpload)
        .where(
            MediaUpload.episode_id == episode.id,
            MediaUpload.video_id == episode.bunny_video_id,
        )
        .order_by(MediaUpload.id.desc())
    ).first()
    if upload is None:
        raise HTTPException(status_code=404, detail="upload not found")
    return {
        "episode_id": episode.id,
        "video_id": episode.bunny_video_id,
        "upload_url": bunny.STREAM_TUS_ENDPOINT if bunny.live() else "",
        "headers": bunny.tus_headers(episode.bunny_video_id) if bunny.live() else {},
    }


@router.get("/episodes/{episode_id}/upload-status")
def upload_status(
    episode_id: int,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    episode = _episode_or_404(db, user, episode_id)
    upload = db.scalars(
        select(MediaUpload)
        .where(MediaUpload.episode_id == episode.id)
        .order_by(MediaUpload.id.desc())
    ).first()
    # A session that never received bytes would otherwise sit in "transcoding"
    # forever (the supplier sees 转码中 and cannot retry). 30 minutes is beyond
    # a realistic 200MB upload on a slow link, so it is safe to call it dead.
    if (
        episode.status == "transcoding"
        and upload is not None
        and upload.status == "created"
        and upload.created_at is not None
    ):
        age = datetime.now(timezone.utc) - upload.created_at
        if age > timedelta(minutes=30):
            from app.modules.common import bunny

            has_bytes = False
            if bunny.live() and episode.bunny_video_id:
                try:
                    remote = bunny.stream_video(episode.bunny_video_id)
                    # hasOriginal flips true as soon as the first TUS bytes
                    # arrive and stays true for the whole (possibly hours-long)
                    # upload, so it is the liveness signal here. Size/length
                    # only appear after the last byte.
                    has_bytes = bool(
                        remote.get("hasOriginal")
                        or int(remote.get("storageSize") or 0) > 0
                        or int(remote.get("length") or 0) > 0
                        or int(remote.get("status") or 0) >= 3
                        or int(remote.get("encodeProgress") or 0) > 0
                    )
                except bunny.BunnyError:
                    has_bytes = False
            if not has_bytes:
                try:
                    transition_episode(db, episode, "transcode_failed")
                    upload.status = "failed"
                    db.commit()
                except InvalidTransition:
                    db.rollback()
    return {
        "episode_id": episode.id,
        "episode_number": episode.episode_number,
        "status": episode.status,
        "duration_seconds": episode.duration_seconds,
        "size_bytes": episode.size_bytes,
        "video_id": episode.bunny_video_id,
        "upload_status": upload.status if upload else None,
    }


@router.post("/episodes/{episode_id}/upload-failed")
def upload_failed(
    episode_id: int,
    body: dict | None = None,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    """Let the supplier release a dead upload session immediately."""
    episode = _episode_or_404(db, user, episode_id)
    drama = db.get(Drama, episode.drama_id)
    _editable(drama)
    if episode.status != "transcoding":
        return {"episode_id": episode.id, "status": episode.status, "updated": False}
    from app.modules.common import bunny

    video_id = episode.bunny_video_id
    if bunny.live() and video_id:
        try:
            remote = bunny.stream_video(video_id)
        except bunny.BunnyError:
            remote = None
        if remote is not None and (
            int(remote.get("storageSize") or 0) > 0
            or int(remote.get("length") or 0) > 0
            or int(remote.get("status") or 0) >= 3
            or int(remote.get("encodeProgress") or 0) > 0
        ):
            return {
                "episode_id": episode.id,
                "status": episode.status,
                "updated": False,
                "ignored": "bytes already at bunny",
            }
    try:
        transition_episode(db, episode, "transcode_failed")
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    upload = db.scalars(
        select(MediaUpload)
        .where(MediaUpload.episode_id == episode.id)
        .order_by(MediaUpload.id.desc())
    ).first()
    if upload is not None:
        upload.status = "failed"
    db.commit()
    return {"episode_id": episode.id, "status": episode.status, "updated": True}


@router.delete("/dramas/{drama_id}", status_code=204)
def delete_drama(
    drama_id: int,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    """Suppliers may delete their own drafts; anything with video or review is locked."""
    drama = _partner_drama_or_404(db, user, drama_id)
    _editable(drama)
    episodes = db.scalars(select(Episode).where(Episode.drama_id == drama.id)).all()
    if any(item.status != "not_uploaded" for item in episodes):
        raise HTTPException(
            status_code=409,
            detail={"code": "drama_has_video", "message": "已上传视频的剧集不能删除"},
        )
    title = drama.title
    for item in episodes:
        db.delete(item)
    db.execute(delete(DramaTag).where(DramaTag.drama_id == drama.id))
    db.delete(drama)
    _audit(db, user, "partner_drama_delete", None, {"title": title, "drama_id": drama_id})
    db.commit()
    return None


@router.post("/dramas/{drama_id}/submit")
def submit_drama(
    drama_id: int,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    drama = _partner_drama_or_404(db, user, drama_id)
    if drama.status != "draft":
        raise HTTPException(
            status_code=409,
            detail={"code": "drama_locked", "message": "只有草稿可以提交审核"},
        )
    if drama.review_state not in ("none", "rejected"):
        raise HTTPException(
            status_code=409,
            detail={"code": "drama_in_review", "message": "已提交，等待审核"},
        )
    if not drama.cover_url:
        raise HTTPException(
            status_code=422, detail={"code": "cover_required", "message": "请先上传封面"}
        )
    if len((drama.synopsis or "").strip()) < 30:
        raise HTTPException(
            status_code=422,
            detail={"code": "synopsis_too_short", "message": "简介至少 30 字"},
        )
    episodes = db.scalars(
        select(Episode).where(Episode.drama_id == drama.id).order_by(Episode.episode_number)
    ).all()
    if not episodes:
        raise HTTPException(
            status_code=422, detail={"code": "episodes_required", "message": "至少上传 1 集"}
        )
    pending = [e.episode_number for e in episodes if e.status != "in_review"]
    if pending:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "episodes_not_ready",
                "message": f"第 {', '.join(map(str, pending))} 集尚未完成转码",
                "episodes": pending,
            },
        )
    floor = min(MIN_FREE_EPISODES, len(episodes))
    if not (floor <= drama.free_episode_count <= len(episodes)):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "free_episode_count_out_of_range",
                "message": f"免费集数需在 {floor}–{len(episodes)} 之间",
                "floor": floor,
                "max": len(episodes),
            },
        )
    drama.review_state = "pending"
    drama.submitted_at = datetime.now(timezone.utc)
    drama.review_note = None
    _audit(db, user, "partner_drama_submit", drama.id, {"episodes": len(episodes)})
    db.commit()
    db.refresh(drama)
    return _drama_out(db, drama, with_episodes=True)



# ---- stats (P4) ----


def _supplier_episode_ids(user: AdminUser):
    return select(Episode.id).join(Drama, Drama.id == Episode.drama_id).where(
        Drama.supplier_id == user.supplier_id
    )


def _play_counts(db: Session, ids, start: datetime) -> tuple[int, int]:
    rows = db.execute(
        select(PlaybackEvent.kind, func.count(PlaybackEvent.id))
        .where(
            PlaybackEvent.episode_id.in_(ids),
            PlaybackEvent.created_at >= start,
            PlaybackEvent.kind.in_(["start", "complete"]),
        )
        .group_by(PlaybackEvent.kind)
    ).all()
    counts = dict(rows)
    return counts.get("start", 0), counts.get("complete", 0)


def _unlock_counts(db: Session, ids, start: datetime) -> tuple[int, int]:
    row = db.execute(
        select(func.count(CoinLedgerEntry.id), func.count(func.distinct(CoinLedgerEntry.user_id)))
        .where(
            CoinLedgerEntry.episode_id.in_(ids),
            CoinLedgerEntry.amount < 0,
            CoinLedgerEntry.biz_type == "unlock",
            CoinLedgerEntry.created_at >= start,
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


@router.get("/stats/overview")
def stats_overview(
    days: int = Query(default=30, ge=1, le=90),
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    start = datetime.now(timezone.utc) - timedelta(days=days)
    supplier = db.get(Supplier, user.supplier_id)
    ids = _supplier_episode_ids(user)
    plays, completes = _play_counts(db, ids, start)
    unlock_total, unlock_users = _unlock_counts(db, ids, start)
    viewers = (
        db.scalar(
            select(func.count(func.distinct(PlaybackEvent.user_id))).where(
                PlaybackEvent.episode_id.in_(ids),
                PlaybackEvent.kind == "start",
                PlaybackEvent.created_at >= start,
                PlaybackEvent.user_id.is_not(None),
            )
        )
        or 0
    )
    orders = settlement_service.first_orders_for_supplier(
        db, supplier.id, start.date(), datetime.now(timezone.utc).date() + timedelta(days=1)
    )
    money = settlement_service.revenue_for_orders(db, supplier, orders)
    return {
        "days": days,
        "plays": plays,
        "completes": completes,
        "completion_rate": round(completes / plays, 4) if plays else 0,
        "viewers": int(viewers),
        "unlocks": unlock_total,
        "unlock_users": unlock_users,
        "first_orders": money["order_count"],
        "gross_cents": money["gross_cents"],
        "revenue_cents": money["revenue_cents"],
        "effective_share_bps": money["effective_share_bps"],
        "currency": money["currency"],
    }


@router.get("/stats/dramas")
def stats_dramas(
    days: int = Query(default=30, ge=1, le=90),
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    start = datetime.now(timezone.utc) - timedelta(days=days)
    supplier = db.get(Supplier, user.supplier_id)
    dramas = db.scalars(
        select(Drama).where(Drama.supplier_id == user.supplier_id).order_by(Drama.id)
    ).all()
    ids = [row.id for row in dramas]
    plays: dict[int, int] = {}
    completes: dict[int, int] = {}
    viewers: dict[int, int] = {}
    unlocks: dict[int, int] = {}
    if ids:
        for drama_id, kind, count in db.execute(
            select(Episode.drama_id, PlaybackEvent.kind, func.count(PlaybackEvent.id))
            .join(Episode, Episode.id == PlaybackEvent.episode_id)
            .where(
                Episode.drama_id.in_(ids),
                PlaybackEvent.created_at >= start,
                PlaybackEvent.kind.in_(["start", "complete"]),
            )
            .group_by(Episode.drama_id, PlaybackEvent.kind)
        ).all():
            (plays if kind == "start" else completes)[drama_id] = count
        for drama_id, count in db.execute(
            select(Episode.drama_id, func.count(func.distinct(PlaybackEvent.user_id)))
            .join(Episode, Episode.id == PlaybackEvent.episode_id)
            .where(
                Episode.drama_id.in_(ids),
                PlaybackEvent.kind == "start",
                PlaybackEvent.created_at >= start,
                PlaybackEvent.user_id.is_not(None),
            )
            .group_by(Episode.drama_id)
        ).all():
            viewers[drama_id] = count
        for drama_id, count in db.execute(
            select(Episode.drama_id, func.count(CoinLedgerEntry.id))
            .join(Episode, Episode.id == CoinLedgerEntry.episode_id)
            .where(
                Episode.drama_id.in_(ids),
                CoinLedgerEntry.amount < 0,
                CoinLedgerEntry.biz_type == "unlock",
                CoinLedgerEntry.created_at >= start,
            )
            .group_by(Episode.drama_id)
        ).all():
            unlocks[drama_id] = count

    orders = settlement_service.first_orders_for_supplier(
        db, supplier.id, start.date(), datetime.now(timezone.utc).date() + timedelta(days=1)
    )
    history = list(
        db.scalars(
            select(SupplierShareHistory)
            .where(SupplierShareHistory.supplier_id == supplier.id)
            .order_by(SupplierShareHistory.effective_from)
        )
    )
    order_stats: dict[int, dict] = {}
    for order in orders:
        if order.source_drama_id is None or order.source_drama_id not in ids:
            continue
        paid_at = order.paid_at
        if paid_at.tzinfo is None:
            paid_at = paid_at.replace(tzinfo=timezone.utc)
        bps = settlement_service._share_at(history, paid_at, supplier.revenue_share_bps)
        item = order_stats.setdefault(order.source_drama_id, {"orders": 0, "gross": 0, "revenue": 0})
        item["orders"] += 1
        item["gross"] += order.amount_cents
        item["revenue"] += order.amount_cents * bps // 10000

    items = []
    for drama in dramas:
        p = plays.get(drama.id, 0)
        c = completes.get(drama.id, 0)
        money = order_stats.get(drama.id, {"orders": 0, "gross": 0, "revenue": 0})
        items.append(
            {
                "id": drama.id,
                "title": drama.title,
                "cover_url": drama.cover_url,
                "status": drama.status,
                "plays": p,
                "completion_rate": round(c / p, 4) if p else 0,
                "viewers": viewers.get(drama.id, 0),
                "unlocks": unlocks.get(drama.id, 0),
                "first_orders": money["orders"],
                "gross_cents": money["gross"],
                "revenue_cents": money["revenue"],
            }
        )
    return {"days": days, "currency": "USD", "items": items}


@router.get("/settlements")
def my_settlements(
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(SupplierSettlement)
        .where(SupplierSettlement.supplier_id == user.supplier_id)
        .order_by(SupplierSettlement.period_start.desc())
        .limit(24)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "month": row.period_start.strftime("%Y-%m"),
                "currency": row.currency,
                "gross_cents": row.gross_cents,
                "revenue_cents": row.revenue_cents,
                "effective_share_bps": row.effective_share_bps,
                "order_count": row.order_count,
                "status": row.status,
                "confirmed_at": _iso(row.confirmed_at),
                "paid_at": _iso(row.paid_at),
            }
            for row in rows
        ]
    }


# ---- api keys (session only) ----


def _own_key(db: Session, user: AdminUser, key_id: int) -> AdminApiKey:
    key = db.get(AdminApiKey, key_id)
    if key is None or key.admin_user_id != user.id:
        raise HTTPException(status_code=404, detail="key not found")
    return key


def _key_out(key: AdminApiKey) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "key_prefix": key.key_prefix,
        "scopes": list(key.scopes or []),
        "state": api_key_service.key_state(key),
        "status": key.status,
        "expires_at": _iso(key.expires_at),
        "last_used_at": _iso(key.last_used_at),
        "created_at": _iso(key.created_at),
        "revoked_at": _iso(key.revoked_at),
        "revoke_reason": key.revoke_reason,
    }


@router.get("/api-keys")
def list_keys(
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(AdminApiKey)
        .where(AdminApiKey.admin_user_id == user.id)
        .order_by(AdminApiKey.id.desc())
    ).all()
    items = [_key_out(key) for key in rows]
    summary = {
        "active": sum(1 for item in items if item["state"] == "active"),
        "expiring": sum(1 for item in items if item["state"] == "expiring"),
        "expired": sum(1 for item in items if item["state"] == "expired"),
        "revoked": sum(1 for item in items if item["state"] == "revoked"),
        "total": len(items),
    }
    return {"items": items, "summary": summary}


@router.get("/auth/keys")
def list_own_keys(
    user: AdminUser = Depends(require_partner_session),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(AdminApiKey)
        .where(AdminApiKey.admin_user_id == user.id)
        .order_by(AdminApiKey.id.desc())
    ).all()
    return {"items": [_key_out(key) for key in rows]}


@router.get("/auth/key-info")
def current_key_info(
    request: Request,
    user: AdminUser = Depends(require_partner),
    db: Session = Depends(get_db),
):
    """Metadata of the calling key only — what the CLI needs for `whoami`.

    Deliberately narrower than `/auth/keys`: a credential must not be able to
    enumerate its owner's other keys.
    """
    credential = _bearer_token(request)
    if not api_key_service.looks_like_key(credential):
        raise HTTPException(status_code=400, detail="请使用 API Key 调用")
    key = db.scalar(
        select(AdminApiKey).where(
            AdminApiKey.key_prefix == credential[: api_key_service.KEY_PREFIX_LEN]
        )
    )
    if key is None:
        raise HTTPException(status_code=404, detail="key not found")
    return _key_out(key)


@router.post("/api-keys", status_code=201)
def create_key(
    body: PartnerKeyCreateIn,
    user: AdminUser = Depends(require_partner_session),
    db: Session = Depends(get_db),
):
    scopes = api_key_service.normalise_scopes(body.scopes, api_key_service.PARTNER_SCOPES)
    key, plaintext = api_key_service.create_key(
        db,
        admin_user_id=user.id,
        name=body.name,
        scopes=scopes,
        expires_in_days=body.expires_in_days,
        actor_id=user.id,
    )
    return {**_key_out(key), "key": plaintext}


@router.patch("/api-keys/{key_id}")
def update_key(
    key_id: int,
    body: PartnerKeyUpdateIn,
    user: AdminUser = Depends(require_partner_session),
    db: Session = Depends(get_db),
):
    key = _own_key(db, user, key_id)
    scopes = (
        api_key_service.normalise_scopes(body.scopes, api_key_service.PARTNER_SCOPES)
        if body.scopes is not None
        else None
    )
    key = api_key_service.update_key(
        db,
        key,
        name=body.name,
        scopes=scopes,
        expires_at=None,
        expires_at_set=False,
        actor_id=user.id,
    )
    return _key_out(key)


@router.post("/api-keys/{key_id}/rotate", status_code=201)
def rotate_key(
    key_id: int,
    user: AdminUser = Depends(require_partner_session),
    db: Session = Depends(get_db),
):
    key = _own_key(db, user, key_id)
    fresh, plaintext = api_key_service.rotate_key(
        db, key, expires_in_days=api_key_service.DEFAULT_TTL_DAYS, actor_id=user.id
    )
    return {**_key_out(fresh), "key": plaintext}


@router.post("/api-keys/{key_id}/revoke")
def revoke_key(
    key_id: int,
    body: PartnerKeyRevokeIn | None = None,
    user: AdminUser = Depends(require_partner_session),
    db: Session = Depends(get_db),
):
    key = _own_key(db, user, key_id)
    key = api_key_service.revoke_key(
        db, key, reason=body.reason if body else None, actor_id=user.id
    )
    return _key_out(key)
