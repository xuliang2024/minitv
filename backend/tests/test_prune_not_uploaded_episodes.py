from __future__ import annotations

from sqlalchemy import func, select

from app.modules.common.models import AuditLog
from app.modules.content.models import Episode
from app.modules.playback.models import PlaybackEvent, PlaybackProgress
from app.modules.wallet.models import Entitlement
from app.prune_not_uploaded_episodes import prune_not_uploaded_episodes


def _episodes(session, drama_id: int) -> list[Episode]:
    return list(
        session.scalars(
            select(Episode).where(Episode.drama_id == drama_id).order_by(Episode.episode_number)
        ).all()
    )


def test_prune_deletes_upload_less_placeholders_only(session, make):
    drama = make.drama(title="Mixed Drama")
    uploaded = make.episode(drama, number=1, status="transcoding")
    uploaded.bunny_video_id = "video-1"
    session.commit()
    placeholder_a = make.episode(drama, number=2, status="not_uploaded")
    placeholder_b = make.episode(drama, number=3, status="not_uploaded")
    # A failed upload keeps its Bunny video id and must survive the prune.
    failed = make.episode(drama, number=4, status="transcode_failed")
    failed.bunny_video_id = "video-4"
    session.commit()

    result = prune_not_uploaded_episodes(session)

    assert result["deleted"] == 2
    remaining = _episodes(session, drama.id)
    assert [row.episode_number for row in remaining] == [1, 4]
    assert {row.id for row in remaining} == {uploaded.id, failed.id}
    assert session.get(Episode, placeholder_a.id) is None
    assert session.get(Episode, placeholder_b.id) is None
    audit = session.scalar(
        select(AuditLog).where(AuditLog.action == "episode_prune_not_uploaded")
    )
    assert audit is not None
    assert audit.target_type == "drama"
    assert audit.target_id == drama.id
    assert audit.payload["deleted"] == 2


def test_prune_cascades_telemetry_but_keeps_paid_rows(session, make):
    drama = make.drama(title="Paid Drama")
    user = make.user(email="buyer@example.com")
    free_junk = make.episode(drama, number=1, status="not_uploaded")
    paid = make.episode(drama, number=2, status="not_uploaded")
    session.add(PlaybackEvent(episode_id=free_junk.id, kind="progress", position_seconds=5))
    session.add(PlaybackProgress(user_id=user.id, episode_id=free_junk.id, position_seconds=5))
    session.add(Entitlement(user_id=user.id, episode_id=paid.id))
    session.commit()

    result = prune_not_uploaded_episodes(session)

    # The telemetry-only placeholder is gone together with its telemetry ...
    assert result["deleted"] == 1
    assert result["skipped"] == 1
    assert session.get(Episode, free_junk.id) is None
    assert session.scalar(select(func.count()).select_from(PlaybackEvent)) == 0
    assert session.scalar(select(func.count()).select_from(PlaybackProgress)) == 0
    # ... while the purchased episode and its entitlement stay untouched.
    assert session.get(Episode, paid.id) is not None
    assert session.get(Entitlement, session.scalar(select(Entitlement.id))) is not None
    report = next(row for row in result["reports"] if row["id"] == drama.id)
    assert report["skipped"] == 1
    assert report["blocked"][str(paid.id)] == ["entitlements"]


def test_prune_dry_run_and_drama_filter(session, make):
    first = make.drama(title="First")
    second = make.drama(title="Second")
    make.episode(first, number=1, status="not_uploaded")
    make.episode(second, number=1, status="not_uploaded")

    dry = prune_not_uploaded_episodes(session, dry_run=True)
    assert dry["deleted"] == 2
    assert session.scalar(select(func.count()).select_from(Episode)) == 2

    scoped = prune_not_uploaded_episodes(session, drama_ids=[first.id])
    assert scoped["deleted"] == 1
    assert [row.drama_id for row in session.scalars(select(Episode)).all()] == [second.id]

    # The other drama is still untouched; a second pass is a no-op.
    assert prune_not_uploaded_episodes(session, drama_ids=[first.id])["deleted"] == 0
    assert prune_not_uploaded_episodes(session)["deleted"] == 1
    assert prune_not_uploaded_episodes(session)["deleted"] == 0
