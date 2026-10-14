"""Ops router: /admin/v1 - staff auth, drama management, tag library, search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.content.models import Drama, DramaTag, Episode, Tag
from app.modules.ops import service as ops_service
from app.modules.ops.models import AdminUser, CONTENT_WRITER_ROLES

router = APIRouter(prefix="/admin/v1", tags=["admin"])


# ---- auth dependencies ----


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return auth[len("Bearer "):]


SESSION_STORE_503_DETAIL = (
    "会话存储不可用（Redis 未连接）。请先启动 minitv-redis："
    "在仓库根目录执行 docker compose up -d，然后重试。"
)


def _session_store_503() -> HTTPException:
    return HTTPException(status_code=503, detail=SESSION_STORE_503_DETAIL)


def require_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    try:
        user = ops_service.resolve_session_token(db, _bearer_token(request))
    except ops_service.SessionStoreUnavailable as exc:
        raise _session_store_503() from exc
    if user is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    return user


def require_content_writer(user: AdminUser = Depends(require_admin)) -> AdminUser:
    if user.role not in CONTENT_WRITER_ROLES:
        raise HTTPException(status_code=403, detail=f"role '{user.role}' cannot write content")
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
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
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
