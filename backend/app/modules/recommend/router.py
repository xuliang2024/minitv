"""GET /v1/feed - the For-You vertical feed.

Kept thin on purpose: everything that decides *what* to show lives in
:mod:`app.modules.recommend.service` so the ranking can be exercised without
going through HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.account.models import User
from app.modules.common.auth import optional_user

from . import config, service

router = APIRouter(prefix="/v1", tags=["feed"])


@router.get("/feed")
def feed(
    request: Request,
    cursor: str | None = None,
    limit: int = Query(default=config.DEFAULT_PAGE_SIZE, ge=1, le=config.MAX_PAGE_SIZE),
    country: str | None = None,
    user: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
):
    """One page of the For-You feed.

    - ``cursor``  opaque pointer from the previous page. Omit it to start a new
      session - that is also what a pull-to-refresh should send.
    - ``limit``   page size, 1..20, default 5. The client asks for the next page
      once ``preload.fetch_when_remaining`` slots are left.
    - ``country`` ISO code, used for the drama region allowlist.

    Identity is the bearer token when signed in, otherwise ``X-Device-Id``, so
    guests still get a stable session. No playback material is attached; the
    client posts to ``/v1/playback`` when it is about to watch a slot.
    """
    device_id = (request.headers.get("X-Device-Id") or "").strip() or None
    return service.build_feed(
        db,
        user_id=None if user is None else user.id,
        device_id=device_id,
        cursor=cursor,
        limit=limit,
        country=(country or "").strip().upper() or None,
    )
