"""Content service: status machines and the only public visibility read path.

The transition matrices below are mirrored 1:1 by database triggers created
in the initial Alembic migration (illegal transitions are rejected at the
database layer as well, so a buggy writer cannot corrupt states).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.content.models import DRAMA_STATUSES, EPISODE_STATUSES, Drama, Episode

DRAMA_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"in_review"}),
    "in_review": frozenset({"draft", "published"}),
    "published": frozenset({"unpublished"}),
    "unpublished": frozenset({"in_review", "published"}),  # re-review / re-publish
}

EPISODE_TRANSITIONS: dict[str, frozenset[str]] = {
    "not_uploaded": frozenset({"transcoding"}),
    "transcoding": frozenset({"in_review", "transcode_failed"}),
    "transcode_failed": frozenset({"transcoding"}),  # retry
    "in_review": frozenset({"published", "not_uploaded"}),  # publish / reset for video replacement
    "published": frozenset({"unpublished", "not_uploaded"}),  # takedown / replace video
    "unpublished": frozenset({"published", "not_uploaded"}),  # re-publish / replace video
}


class InvalidTransition(Exception):
    def __init__(self, entity: str, from_status: str, to_status: str):
        self.entity = entity
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"illegal {entity} transition: {from_status} -> {to_status}")


def _apply(transitions: dict[str, frozenset[str]], entity: str, obj, to_status: str):
    if to_status not in transitions.get(obj.status, frozenset()):
        raise InvalidTransition(entity, obj.status, to_status)
    obj.status = to_status
    return obj


def transition_drama(session: Session, drama: Drama, to_status: str) -> Drama:
    if to_status not in DRAMA_STATUSES:
        raise InvalidTransition("drama", drama.status, to_status)
    _apply(DRAMA_TRANSITIONS, "drama", drama, to_status)
    session.flush()
    return drama


def transition_episode(session: Session, episode: Episode, to_status: str) -> Episode:
    if to_status not in EPISODE_STATUSES:
        raise InvalidTransition("episode", episode.status, to_status)
    _apply(EPISODE_TRANSITIONS, "episode", episode, to_status)
    session.flush()
    return episode


# ---- public visibility: the only read path for client-facing queries ----
# Non-published dramas and non-published episodes must never leak through
# any other query. Later stages (API layer) are expected to call these
# functions instead of writing their own WHERE clauses.


def list_published_dramas(session: Session) -> list[Drama]:
    return list(
        session.scalars(
            select(Drama).where(Drama.status == "published").order_by(Drama.updated_at.desc())
        )
    )


def search_published_dramas(session: Session, q: str, limit: int = 20) -> list[Drama]:
    return list(
        session.scalars(
            select(Drama)
            .where(Drama.status == "published", Drama.title.ilike(f"%{q}%"))
            .order_by(Drama.updated_at.desc())
            .limit(limit)
        )
    )


def get_published_drama(session: Session, drama_id: int) -> Drama | None:
    drama = session.get(Drama, drama_id)
    if drama is None or drama.status != "published":
        return None
    return drama


def list_published_episodes(session: Session, drama_id: int) -> list[Episode]:
    if get_published_drama(session, drama_id) is None:
        return []
    return list(
        session.scalars(
            select(Episode)
            .where(Episode.drama_id == drama_id, Episode.status == "published")
            .order_by(Episode.episode_number)
        )
    )


def get_published_episode(session: Session, drama_id: int, episode_id: int) -> Episode | None:
    if get_published_drama(session, drama_id) is None:
        return None
    episode = session.get(Episode, episode_id)
    if episode is None or episode.drama_id != drama_id or episode.status != "published":
        return None
    return episode
