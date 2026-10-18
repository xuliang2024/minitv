"""For-You feed orchestration: recall, session cursor, pagination.

Product rules baked in here (they came from the app design, not from the data):

- **One slot per drama.** Swiping up means "next show", not "next episode", so a
  drama appears at most once per feed session.
- **Locked episodes never enter the feed.** A drama whose entry episode would be
  behind the paywall is simply not recommended; the paywall is reached through
  the drama's own play page instead.
- **Pages of five, preloaded.** The server returns an ordered plan through an
  opaque cursor so paging is stable even though the ranking is dynamic.

Redis holds two things and is treated as a cache: the ordered plan
(``feed:plan:{session}``) and per-viewer impression counts
(``feed:imp:{viewer}``) used for fatigue. If Redis is unreachable the feed
degrades to a freshly ranked page instead of failing.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.content import service as content
from app.modules.content.models import Drama, Episode, Tag
from app.modules.ops.service import get_redis
from app.modules.social.models import Reaction

from . import config, ranking, signals


class InvalidCursor(Exception):
    """The cursor failed its signature check."""


# --------------------------------------------------------------------------- #
# redis helpers (best-effort)
# --------------------------------------------------------------------------- #


def _redis():
    try:
        return get_redis()
    except Exception:  # redis down must not break the feed
        return None


def viewer_key(user_id: int | None, device_id: str | None) -> str:
    """Stable identity for fatigue tracking: the account, else the device."""
    if user_id is not None:
        return f"u{user_id}"
    return f"d{(device_id or 'anon').strip() or 'anon'}"


def _load_impressions(key: str) -> dict[int, int]:
    client = _redis()
    if client is None:
        return {}
    try:
        raw = client.hgetall(f"feed:imp:{key}")
    except Exception:
        return {}
    return {
        int(drama_id): int(count)
        for drama_id, count in raw.items()
        if str(drama_id).isdigit() and str(count).lstrip("-").isdigit()
    }


def _record_impressions(key: str, drama_ids: list[int]) -> None:
    if not drama_ids:
        return
    client = _redis()
    if client is None:
        return
    redis_key = f"feed:imp:{key}"
    try:
        pipe = client.pipeline()
        for drama_id in drama_ids:
            pipe.hincrby(redis_key, str(drama_id), 1)
        pipe.expire(redis_key, config.IMPRESSION_TTL_SECONDS)
        pipe.execute()
    except Exception:
        return


def _load_plan(session_id: str) -> list[dict] | None:
    client = _redis()
    if client is None:
        return None
    try:
        raw = client.get(f"feed:plan:{session_id}")
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _save_plan(session_id: str, items: list[dict]) -> None:
    client = _redis()
    if client is None:
        return
    try:
        client.set(
            f"feed:plan:{session_id}", json.dumps(items), ex=config.PLAN_TTL_SECONDS
        )
    except Exception:
        return


# --------------------------------------------------------------------------- #
# cursor
# --------------------------------------------------------------------------- #


def _cursor_secret() -> bytes:
    return os.environ.get("FEED_CURSOR_KEY", "local-feed-cursor").encode()


def encode_cursor(session_id: str, offset: int, seed: str) -> str:
    """Opaque, signed pointer into one session's plan."""
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"s": session_id, "o": offset, "sd": seed}, separators=(",", ":")
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(_cursor_secret(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}.{signature}"


def decode_cursor(raw: str | None) -> tuple[str, int, str] | None:
    if not raw or "." not in raw:
        return None
    payload, signature = raw.rsplit(".", 1)
    expected = hmac.new(_cursor_secret(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(expected, signature):
        raise InvalidCursor("bad feed cursor signature")
    try:
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return str(data["s"]), int(data["o"]), str(data["sd"])
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidCursor("malformed feed cursor") from exc


# --------------------------------------------------------------------------- #
# recall
# --------------------------------------------------------------------------- #


def _build_plan(
    db: Session,
    *,
    user_id: int | None,
    device_id: str | None,
    country: str | None,
    seed: str,
    limit: int,
) -> list[dict]:
    """Score every visible drama and return the ordered plan (ids + reasons)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    member, unlocked, progress = signals.viewer_state(db, user_id)

    dramas = signals.visible_dramas(db, country)
    drama_ids = [drama.id for drama in dramas]
    if not drama_ids:
        return []

    store = signals.build_store(
        db, now=now, drama_ids=drama_ids, user_id=user_id, member=member
    )
    store.unlocked_episodes = unlocked
    store.progress = progress
    store.impressions = _load_impressions(viewer_key(user_id, device_id))

    episodes_by_drama = signals.playable_episodes(db, drama_ids)
    candidates: list[signals.Candidate] = []
    for drama in dramas:
        entry, resume, resumable = signals.pick_entry_episode(
            drama,
            episodes_by_drama.get(drama.id, []),
            member=member,
            unlocked=unlocked,
            progress=progress,
            completed=store.completed_episodes,
        )
        if entry is None:
            # Nothing watchable without paying -> not a feed slot.
            continue
        candidates.append(
            signals.Candidate(
                drama=drama,
                entry_episode=entry,
                resume_seconds=resume,
                resumable=resumable,
            )
        )

    ordered = ranking.rank(store, candidates, seed=seed)
    _assign_sources(store, ordered)

    pool_size = max(config.RECALL_POOL_MULTIPLIER * limit, config.MIN_POOL_SIZE)
    return [
        {
            "drama_id": candidate.drama.id,
            "episode_id": candidate.entry_episode.id,
            "resume_seconds": candidate.resume_seconds,
            "source": candidate.source,
            "score": round(candidate.score, 4),
        }
        for candidate in ordered[:pool_size]
    ]


def _top(candidates: list[signals.Candidate], key, quota: int) -> list[signals.Candidate]:
    pool = [candidate for candidate in candidates if candidate.source == "explore"]
    pool.sort(key=key, reverse=True)
    return pool[:quota]


def _assign_sources(store: signals.FeatureStore, ordered: list[signals.Candidate]) -> None:
    """Label each slot with *why* it is here.

    Pure telemetry today, but it is the only way to later answer "is the
    similarity source actually driving completion?" without re-deriving intent
    from logs. Unlabelled items stay ``explore`` - the deliberate cold-start and
    discovery budget.
    """
    for candidate in ordered:
        candidate.source = "explore"

    for candidate in [item for item in ordered if item.resumable][: config.RESUME_QUOTA]:
        candidate.source = "resume"

    def tag_score(candidate: signals.Candidate) -> float:
        tags = store.tags.get(candidate.drama.id, [])
        scores = [store.user_tag_scores.get(tag.id, 0.0) for tag in tags]
        return max(scores) if scores else 0.0

    quota = config.SOURCE_QUOTA
    _label(_top(ordered, tag_score, quota), "affinity")
    _label(_top(ordered, lambda c: store.hot.get(c.drama.id, 0), quota), "trending")
    _label(_top(ordered, lambda c: c.features.get("completion", 0.0), quota), "quality")
    _label(_top(ordered, lambda c: c.features.get("freshness", 0.0), quota), "fresh")


def _label(candidates: list[signals.Candidate], source: str) -> None:
    for candidate in candidates:
        if candidate.source == "explore":
            candidate.source = source


# --------------------------------------------------------------------------- #
# pagination
# --------------------------------------------------------------------------- #


def _drama_payload(db: Session, drama: Drama, tags: list[Tag], counts: dict, *, like_count: int = 0, liked: bool = False) -> dict:
    return {
        "id": drama.id,
        "title": drama.title,
        "synopsis": drama.synopsis,
        "cover_url": drama.cover_url,
        "tags": [tag.name_en for tag in tags],
        "episode_count": counts.get("total", 0),
        "published_episode_count": counts.get("published", 0),
        "free_episode_count": drama.free_episode_count,
        "coin_price_per_episode": drama.coin_price_per_episode,
        "play_count": drama.play_count,
        "like_count": like_count,
        "liked": liked,
    }


def _reaction_stats(db: Session, drama_ids, user_id: int | None) -> tuple[dict[int, int], set[int]]:
    """Like totals for the page, plus which of them this viewer already liked.

    Two queries total, so a five-slot page never turns into five round trips.
    """
    ids = list(drama_ids)
    if not ids:
        return {}, set()
    totals = {
        int(target_id): int(total)
        for target_id, total in db.execute(
            select(Reaction.target_id, func.count())
            .where(
                Reaction.target_type == "drama",
                Reaction.kind == "like",
                Reaction.target_id.in_(ids),
            )
            .group_by(Reaction.target_id)
        )
    }
    liked: set[int] = set()
    if user_id is not None:
        liked = {
            int(target_id)
            for target_id in db.scalars(
                select(Reaction.target_id).where(
                    Reaction.user_id == user_id,
                    Reaction.target_type == "drama",
                    Reaction.kind == "like",
                    Reaction.target_id.in_(ids),
                )
            )
        }
    return totals, liked


def _materialize(
    db: Session,
    plan: list[dict],
    *,
    offset: int,
    limit: int,
    user_id: int | None,
) -> tuple[list[dict], int]:
    """Turn plan entries into client payloads, re-checking they are still valid.

    Returns ``(items, consumed)`` where ``consumed`` is how many plan entries
    were examined - entries whose episode went away between pages are skipped
    without breaking the cursor arithmetic.
    """
    if offset >= len(plan):
        return [], 0

    member, unlocked, _progress = signals.viewer_state(db, user_id)
    entries = plan[offset:]
    drama_ids = [entry["drama_id"] for entry in entries]
    episode_ids = [entry["episode_id"] for entry in entries]

    dramas = {
        drama.id: drama
        for drama in db.scalars(
            select(Drama).where(Drama.id.in_(drama_ids), Drama.status == "published")
        )
    }
    episodes = {
        episode.id: episode
        for episode in db.scalars(
            select(Episode).where(
                Episode.id.in_(episode_ids),
                Episode.status == "published",
                Episode.bunny_video_id != "",
            )
        )
    }
    tags_by_drama = content.tags_by_drama(db, list(dramas.keys()))
    counts = _episode_counts(db, dramas.keys())
    like_totals, my_likes = _reaction_stats(db, dramas.keys(), user_id)

    items: list[dict] = []
    consumed = 0
    for entry in entries:
        consumed += 1
        drama = dramas.get(entry["drama_id"])
        episode = episodes.get(entry["episode_id"])
        if drama is None or episode is None or episode.drama_id != drama.id:
            continue
        if (
            episode.episode_number > drama.free_episode_count
            and not member
            and episode.id not in unlocked
        ):
            continue
        items.append(
            {
                "item_id": f"d{drama.id}:e{episode.id}",
                "drama": _drama_payload(
                    db,
                    drama,
                    tags_by_drama.get(drama.id, []),
                    counts.get(drama.id, {}),
                    like_count=like_totals.get(drama.id, 0),
                    liked=drama.id in my_likes,
                ),
                "episode": {
                    "id": episode.id,
                    "episode_number": episode.episode_number,
                    "title": episode.title,
                    "duration_seconds": episode.duration_seconds,
                    "free": episode.episode_number <= drama.free_episode_count,
                },
                "resume_seconds": entry["resume_seconds"],
                "entry_reason": entry["source"],
                "score": entry["score"],
            }
        )
        if len(items) >= limit:
            break
    return items, consumed


def _episode_counts(db: Session, drama_ids) -> dict[int, dict]:
    ids = list(drama_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(
            Episode.drama_id,
            func.count(),
            func.count().filter(Episode.status == "published"),
        )
        .where(Episode.drama_id.in_(ids))
        .group_by(Episode.drama_id)
    )
    return {
        drama_id: {"total": int(total), "published": int(published)}
        for drama_id, total, published in rows
    }


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #


def build_feed(
    db: Session,
    *,
    user_id: int | None = None,
    device_id: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    country: str | None = None,
) -> dict:
    """Return one page of the For-You feed.

    The first call (no cursor) ranks the catalog and stores the order; later
    calls walk that stored plan, so ranking is computed once per session rather
    than per page.
    """
    size = int(limit or config.DEFAULT_PAGE_SIZE)
    size = max(1, min(size, config.MAX_PAGE_SIZE))

    try:
        resolved = decode_cursor(cursor)
    except InvalidCursor as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if resolved is None:
        session_id = uuid.uuid4().hex[:16]
        seed = uuid.uuid4().hex[:8]
        plan = _build_plan(
            db, user_id=user_id, device_id=device_id, country=country, seed=seed, limit=size
        )
        _save_plan(session_id, plan)
        offset = 0
    else:
        session_id, offset, seed = resolved
        plan = _load_plan(session_id)
        if plan is None:
            # Plan expired (or Redis was flushed): start a fresh session so the
            # client keeps scrolling instead of hitting a dead end.
            return build_feed(
                db, user_id=user_id, device_id=device_id, cursor=None, limit=size, country=country
            )

    items, consumed = _materialize(db, plan, offset=offset, limit=size, user_id=user_id)
    _record_impressions(viewer_key(user_id, device_id), [item["drama"]["id"] for item in items])

    next_offset = offset + consumed
    has_more = next_offset < len(plan)
    return {
        "session_id": session_id,
        "page_size": size,
        "items": items,
        "next_cursor": encode_cursor(session_id, next_offset, seed) if has_more else None,
        "has_more": has_more,
        "preload": {"fetch_when_remaining": config.PRELOAD_REMAINING},
    }
