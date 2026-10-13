"""Publish, groups, takedown and irreversible delete."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.modules.common.models import AuditLog
from app.modules.content.models import (
    CdnPurge,
    Drama,
    DramaHomeGroup,
    DramaTag,
    Episode,
    HomeGroup,
    MediaUpload,
)
from app.modules.ops.config_models import Banner
from app.modules.content.service import InvalidTransition, transition_drama, transition_episode
from app.modules.playback.models import PlaybackEvent, PlaybackProgress
from app.modules.playback.service import bump_generation
from app.modules.social.models import Comment, PushRecord
from app.modules.wallet.models import CoinLedgerEntry, Entitlement

GROUP_LIMIT = 20


class PublishError(Exception):
    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


def _impact(session: Session, drama_id: int) -> dict:
    groups = list(
        session.scalars(
            select(HomeGroup.name)
            .join(DramaHomeGroup, DramaHomeGroup.group_id == HomeGroup.id)
            .where(DramaHomeGroup.drama_id == drama_id)
        )
    )
    banners = session.scalar(select(func.count()).select_from(Banner).where(Banner.drama_id == drama_id)) or 0
    pushes = session.scalar(select(func.count()).select_from(PushRecord).where(PushRecord.drama_id == drama_id)) or 0
    return {"groups": groups, "banner_count": banners, "push_count": pushes}


def invalidate_drama(session: Session, drama: Drama, reason: str) -> None:
    episodes = session.scalars(select(Episode).where(Episode.drama_id == drama.id)).all()
    for episode in episodes:
        bump_generation(episode.id)
        session.add(CdnPurge(episode_id=episode.id, drama_id=drama.id, reason=reason))


def change_drama_status(session: Session, drama: Drama, to_status: str, *, actor_id: int) -> Drama:
    try:
        transition_drama(session, drama, to_status)
    except InvalidTransition as exc:
        raise PublishError(409, str(exc)) from exc
    if to_status == "published":
        drama.published_at = datetime.now(timezone.utc)
    if to_status == "unpublished":
        invalidate_drama(session, drama, "unpublish")
    session.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor_id,
            action=f"drama_{to_status}",
            target_type="drama",
            target_id=drama.id,
            payload={},
        )
    )
    session.commit()
    return drama


def schedule_publish(session: Session, drama: Drama, when: datetime, *, actor_id: int) -> Drama:
    drama.scheduled_publish_at = when
    session.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor_id,
            action="drama_schedule",
            target_type="drama",
            target_id=drama.id,
            payload={"at": when.isoformat()},
        )
    )
    session.commit()
    return drama


def apply_due_schedules(session: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    due = session.scalars(
        select(Drama).where(
            Drama.scheduled_publish_at.is_not(None),
            Drama.scheduled_publish_at <= now,
            Drama.status.in_(["in_review", "unpublished"]),
        )
    ).all()
    count = 0
    for drama in due:
        if drama.status == "unpublished":
            transition_drama(session, drama, "in_review")
        transition_drama(session, drama, "published")
        drama.published_at = now
        drama.scheduled_publish_at = None
        count += 1
    session.commit()
    return count


def publish_episode(session: Session, episode: Episode) -> Episode:
    try:
        transition_episode(session, episode, "published")
    except InvalidTransition as exc:
        raise PublishError(409, str(exc)) from exc
    session.commit()
    return episode


def add_to_group(session: Session, group: HomeGroup, drama_id: int, *, confirm: bool) -> dict:
    count = session.scalar(
        select(func.count()).select_from(DramaHomeGroup).where(DramaHomeGroup.group_id == group.id)
    ) or 0
    existing = session.get(DramaHomeGroup, (group.id, drama_id))
    preview = {"selected": count, "limit": GROUP_LIMIT, "already_in_group": existing is not None}
    if not confirm:
        return {"written": False, **preview}
    if existing is not None:
        return {"written": False, "reason": "already_in_group", **preview}
    if count >= GROUP_LIMIT:
        raise PublishError(409, "group is full")
    session.add(DramaHomeGroup(group_id=group.id, drama_id=drama_id, position_in_group=count))
    session.commit()
    return {"written": True, "selected": count + 1, "limit": GROUP_LIMIT}


def delete_drama(session: Session, drama: Drama, *, confirm: bool, actor_id: int) -> dict:
    impact = _impact(session, drama.id)
    if not confirm:
        raise PublishError(
            409,
            {
                "confirm_required": True,
                "message": "删除后不可恢复",
                "impact": impact,
            },
        )
    episode_ids = list(session.scalars(select(Episode.id).where(Episode.drama_id == drama.id)))
    if episode_ids:
        session.execute(update(CoinLedgerEntry).where(CoinLedgerEntry.episode_id.in_(episode_ids)).values(episode_id=None))
        session.execute(delete(Entitlement).where(Entitlement.episode_id.in_(episode_ids)))
        session.execute(delete(MediaUpload).where(MediaUpload.episode_id.in_(episode_ids)))
        session.execute(delete(CdnPurge).where(CdnPurge.episode_id.in_(episode_ids)))
        session.execute(delete(PlaybackEvent).where(PlaybackEvent.episode_id.in_(episode_ids)))
        session.execute(delete(PlaybackProgress).where(PlaybackProgress.episode_id.in_(episode_ids)))
        session.execute(delete(Comment).where(Comment.drama_id == drama.id))
        session.execute(delete(Episode).where(Episode.drama_id == drama.id))
    session.execute(delete(DramaTag).where(DramaTag.drama_id == drama.id))
    session.execute(delete(DramaHomeGroup).where(DramaHomeGroup.drama_id == drama.id))
    session.execute(delete(Banner).where(Banner.drama_id == drama.id))
    session.execute(delete(PushRecord).where(PushRecord.drama_id == drama.id))
    session.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor_id,
            action="drama_delete",
            target_type="drama",
            target_id=drama.id,
            payload=impact,
        )
    )
    session.delete(drama)
    session.commit()
    return {"deleted": True, "impact": impact}
