"""Remove placeholder episodes that were registered but never uploaded.

``app.seed_catalog_episodes`` and the admin "登记 N 集" action register every
episode of a drama as a ``not_uploaded`` row, including episodes we have no
video for. Those rows can never play, and the admin console can now create
episodes on demand while uploading the video ("上传新分集"), so the leftovers
are noise: they inflate ``episode_count`` and bury the playable episodes.

This command deletes episode rows whose status is ``not_uploaded`` and that
have no Bunny video id, i.e. nothing was ever uploaded for them. Upload
sessions, playback telemetry and CDN purge records attached to those
placeholders are removed with them; rows referenced by user value or user
content (entitlements, coin ledger, comments) are skipped and reported so no
paid unlock or comment is lost. Every deletion is recorded in ``audit_logs``.

Usage:
    cd backend && ../.venv/bin/python -m app.prune_not_uploaded_episodes --dry-run
    cd backend && ../.venv/bin/python -m app.prune_not_uploaded_episodes
    cd backend && ../.venv/bin/python -m app.prune_not_uploaded_episodes --drama-id 1
"""

from __future__ import annotations

import argparse
from typing import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import get_engine, make_session_factory
from app.modules.common.models import AuditLog
from app.modules.content.models import CdnPurge, Drama, Episode, MediaUpload
from app.modules.playback.models import PlaybackEvent, PlaybackProgress
from app.modules.social.models import Comment
from app.modules.wallet.models import CoinLedgerEntry, Entitlement

# (label, model) pairs whose ``episode_id`` column references ``episodes.id``.
# ``CASCADE`` rows only describe the episode (upload sessions, telemetry, CDN
# bookkeeping) and are deleted with the placeholder. ``BLOCKING`` rows carry
# user value or user content (paid unlocks, coin movements, comments); an
# episode referenced by any of them is kept and reported instead.
CASCADE = (
    ("media_uploads", MediaUpload),
    ("playback_events", PlaybackEvent),
    ("playback_progress", PlaybackProgress),
    ("cdn_purges", CdnPurge),
)
BLOCKING = (
    ("entitlements", Entitlement),
    ("coin_ledger", CoinLedgerEntry),
    ("comments", Comment),
)


def _referenced_episode_ids(
    session: Session, ids: list[int], tables: tuple
) -> dict[int, list[str]]:
    """Map episode id -> list of tables that still reference it."""
    blocked: dict[int, list[str]] = {}
    for label, model in tables:
        rows = session.scalars(select(model.episode_id).where(model.episode_id.in_(ids))).all()
        for episode_id in {row for row in rows if row is not None}:
            blocked.setdefault(int(episode_id), []).append(label)
    return blocked


def prune_not_uploaded_episodes(
    session: Session,
    *,
    drama_ids: Iterable[int] | None = None,
    dry_run: bool = False,
) -> dict:
    """Delete upload-less placeholder episodes and report per drama."""
    stmt = select(Drama).order_by(Drama.id)
    if drama_ids is not None:
        ids = list(drama_ids)
        stmt = stmt.where(Drama.id.in_(ids)) if ids else stmt.where(False)
    dramas = list(session.scalars(stmt).all())

    reports: list[dict] = []
    deleted_total = 0
    skipped_total = 0
    for drama in dramas:
        candidates = list(
            session.scalars(
                select(Episode.id).where(
                    Episode.drama_id == drama.id,
                    Episode.status == "not_uploaded",
                    Episode.bunny_video_id == "",
                )
            ).all()
        )
        total_rows = session.scalar(
            select(func.count()).select_from(Episode).where(Episode.drama_id == drama.id)
        ) or 0
        if not candidates:
            reports.append(
                {
                    "id": drama.id,
                    "title": drama.title,
                    "episodes": total_rows,
                    "candidates": 0,
                    "deleted": 0,
                    "skipped": 0,
                }
            )
            continue
        blocked = _referenced_episode_ids(session, candidates, BLOCKING)
        deletable = [episode_id for episode_id in candidates if episode_id not in blocked]
        cascaded = {label: 0 for label, _ in CASCADE}
        if deletable and not dry_run:
            for label, model in CASCADE:
                result = session.execute(delete(model).where(model.episode_id.in_(deletable)))
                cascaded[label] = result.rowcount or 0
            session.execute(delete(Episode).where(Episode.id.in_(deletable)))
            session.add(
                AuditLog(
                    actor_type="system",
                    actor_id=None,
                    action="episode_prune_not_uploaded",
                    target_type="drama",
                    target_id=drama.id,
                    payload={
                        "deleted": len(deletable),
                        "skipped_referenced": len(blocked),
                        "kept_episodes": total_rows - len(deletable),
                        "cascaded": cascaded,
                    },
                )
            )
            session.commit()
        deleted_total += len(deletable)
        skipped_total += len(blocked)
        reports.append(
            {
                "id": drama.id,
                "title": drama.title,
                "episodes": total_rows,
                "candidates": len(candidates),
                "deleted": len(deletable),
                "skipped": len(blocked),
                "cascaded": cascaded,
                "blocked": {str(k): v for k, v in sorted(blocked.items())},
            }
        )

    return {
        "dry_run": dry_run,
        "dramas": len(dramas),
        "reports": reports,
        "deleted": deleted_total,
        "skipped": skipped_total,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete not_uploaded episode placeholders that were never uploaded."
    )
    parser.add_argument(
        "--drama-id",
        type=int,
        action="append",
        dest="drama_ids",
        help="only prune this drama id; repeat for multiple ids",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be deleted without writing to the database",
    )
    args = parser.parse_args()

    factory = make_session_factory(get_engine())
    with factory() as session:
        result = prune_not_uploaded_episodes(
            session,
            drama_ids=args.drama_ids,
            dry_run=args.dry_run,
        )

    prefix = "dry-run: " if result["dry_run"] else ""
    print(
        f"{prefix}scanned {result['dramas']} dramas; "
        f"deleted {result['deleted']} placeholder episodes; "
        f"skipped {result['skipped']} referenced rows"
    )
    for row in result["reports"]:
        if not row["candidates"]:
            continue
        print(
            f"  #{row['id']} {row['title']}: episodes {row['episodes']}, "
            f"not_uploaded {row['candidates']}, deleted {row['deleted']}, "
            f"skipped {row['skipped']}"
        )
        for episode_id, tables in row.get("blocked", {}).items():
            print(f"    skipped episode #{episode_id}: referenced by {', '.join(tables)}")


if __name__ == "__main__":
    main()
