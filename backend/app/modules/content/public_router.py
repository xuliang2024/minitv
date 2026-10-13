"""Public content router: /v1 - the only client-facing read surface.

Everything goes through content.service's published-only read path; draft,
in-review and unpublished dramas are structurally invisible here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.content import service as content
from app.modules.content.models import Drama

router = APIRouter(prefix="/v1", tags=["public"])


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
