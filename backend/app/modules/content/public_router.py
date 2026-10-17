"""Public content router: /v1 - the only client-facing read surface.

Everything goes through content.service's published-only read path; draft,
in-review and unpublished dramas are structurally invisible here.

The ``/v1/preview`` surface powers the website's free-preview player: it
lists, per category, the first few dramas that have at least one published
episode (the "public data"), and returns the published episode list with a
``free`` flag. Locked episodes never carry playback material on any public
endpoint - only ``POST /v1/playback`` mints a signed URL, and only for free
episodes when the caller is a guest.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.content import service as content
from app.modules.content.models import Drama, Episode, Tag

router = APIRouter(prefix="/v1", tags=["public"])

# "每个分类的前 7 个" - the website preview shows at most this many dramas per
# category; everything beyond that cap is app-only.
PREVIEW_LIMIT = 7


def _drama_out(drama: Drama) -> dict:
    return {
        "id": drama.id,
        "title": drama.title,
        "synopsis": drama.synopsis,
        "cover_url": drama.cover_url,
        "status": drama.status,
        "free_episode_count": drama.free_episode_count,
        "coin_price_per_episode": drama.coin_price_per_episode,
        "updated_at": drama.updated_at.isoformat() if drama.updated_at else None,
    }


def _published_episodes(db: Session, drama_id: int) -> list[Episode]:
    """Published episodes with a video attached, in episode order."""
    return list(
        db.scalars(
            select(Episode)
            .where(
                Episode.drama_id == drama_id,
                Episode.status == "published",
                Episode.bunny_video_id != "",
            )
            .order_by(Episode.episode_number)
        )
    )


def _previewable_dramas(db: Session, counters: dict[int, dict]) -> list[Drama]:
    """Published dramas that expose at least one *free* playable episode, most
    played first (list_published_dramas already sorts by recency, and the
    stable sort below keeps recency as the tiebreaker).

    A drama whose free window is empty cannot be trial-watched on the web, so
    it is app-only and stays out of the preview surface entirely.
    """
    dramas = [
        d
        for d in content.list_published_dramas(db)
        if counters.get(d.id, {}).get("free_playable_count", 0) > 0
    ]
    dramas.sort(key=lambda drama: -drama.play_count)
    return dramas


def _episode_counters(db: Session) -> dict[int, dict]:
    """drama_id -> {episode_count, free_playable_count} over published episodes."""
    rows = db.execute(
        select(
            Episode.drama_id,
            func.count().label("episode_count"),
            func.count().filter(Episode.episode_number <= Drama.free_episode_count).label("free_count"),
        )
        .join(Drama, Drama.id == Episode.drama_id)
        .where(Episode.status == "published", Episode.bunny_video_id != "")
        .group_by(Episode.drama_id)
    ).all()
    return {
        row.drama_id: {"episode_count": row.episode_count, "free_playable_count": row.free_count}
        for row in rows
    }


_tags_by_drama = content.tags_by_drama


def _preview_drama_out(drama: Drama, counters: dict, tags: list[Tag]) -> dict:
    return {
        "id": drama.id,
        "title": drama.title,
        "synopsis": drama.synopsis,
        "cover_url": drama.cover_url,
        "play_count": drama.play_count,
        "free_episode_count": drama.free_episode_count,
        "coin_price_per_episode": drama.coin_price_per_episode,
        "episode_count": counters.get("episode_count", 0),
        "free_playable_count": counters.get("free_playable_count", 0),
        "tags": [tag.name_en for tag in tags],
    }


@router.get("/preview")
def preview_catalog(db: Session = Depends(get_db)):
    """Website trial surface: first N dramas per category, public data only.

    A drama is previewable when it is published and has at least one
    published episode with a video attached. Categories are the enabled tags
    that cover at least one previewable drama, mains first. Within a category
    dramas are ordered by play count, then recency. "All shows" is a
    pseudo-category with the same per-category cap.
    """
    counters = _episode_counters(db)
    dramas = _previewable_dramas(db, counters)
    previewable_ids = {d.id for d in dramas}
    counters = {pid: counters[pid] for pid in previewable_ids}
    tags_by_drama = _tags_by_drama(db, [d.id for d in dramas])

    def card(drama: Drama) -> dict:
        return _preview_drama_out(drama, counters.get(drama.id, {}), tags_by_drama.get(drama.id, []))

    categories: list[dict] = [
        {
            "id": 0,
            "name": "All shows",
            "main": True,
            "total": len(dramas),
            "dramas": [card(d) for d in dramas[:PREVIEW_LIMIT]],
        }
    ]

    all_tags = list(
        db.scalars(
            select(Tag)
            .where(Tag.status == "enabled")
            .order_by(Tag.category.asc(), Tag.id)  # "main" sorts before "sub"
        )
    )
    for tag in all_tags:
        linked = [d for d in dramas if tag in tags_by_drama.get(d.id, [])]
        if not linked:
            continue
        categories.append(
            {
                "id": tag.id,
                "name": tag.name_en,
                "main": tag.category == "main",
                "total": len(linked),
                "dramas": [card(d) for d in linked[:PREVIEW_LIMIT]],
            }
        )
    return {"limit": PREVIEW_LIMIT, "categories": categories}


@router.get("/preview/dramas/{drama_id}")
def preview_drama(drama_id: int, db: Session = Depends(get_db)):
    """Preview detail for one drama: metadata plus the published episode list.

    Locked episodes appear in the list (the website shows them behind a lock
    and sends users to the app) but this response never contains playback
    URLs for them.
    """
    drama = content.get_published_drama(db, drama_id)
    if drama is None:
        raise HTTPException(status_code=404, detail="drama not found")
    episodes = _published_episodes(db, drama.id)
    if not episodes:
        raise HTTPException(status_code=404, detail="no published episodes")

    counters = {
        "episode_count": len(episodes),
        "free_playable_count": sum(1 for ep in episodes if ep.episode_number <= drama.free_episode_count),
    }
    if counters["free_playable_count"] == 0:
        raise HTTPException(status_code=404, detail="no free episodes")
    tags = _tags_by_drama(db, [drama.id]).get(drama.id, [])
    total_episode_count = db.scalar(
        select(func.count()).select_from(Episode).where(Episode.drama_id == drama.id)
    ) or 0
    return {
        "drama": _preview_drama_out(drama, counters, tags),
        "items": [
            {
                "id": ep.id,
                "episode_number": ep.episode_number,
                "title": ep.title,
                "duration_seconds": ep.duration_seconds,
                "free": ep.episode_number <= drama.free_episode_count,
            }
            for ep in episodes
        ],
        "total_episode_count": total_episode_count,
    }


@router.get("/home")
def home(db: Session = Depends(get_db)):
    dramas = content.list_published_dramas(db)
    return {"items": [_drama_out(d) for d in dramas]}


@router.get("/dramas/{drama_id}")
def drama_detail(drama_id: int, db: Session = Depends(get_db)):
    drama = content.get_published_drama(db, drama_id)
    if drama is None:
        raise HTTPException(status_code=404, detail="drama not found")
    return _drama_out(drama)


@router.get("/search")
def search(q: str = Query(min_length=1), db: Session = Depends(get_db)):
    dramas = content.search_published_dramas(db, q)
    return {"items": [_drama_out(d) for d in dramas]}
