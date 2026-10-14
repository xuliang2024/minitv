from __future__ import annotations

import json

from sqlalchemy import func, select

from app.modules.content.models import Episode
from app.seed_catalog_episodes import seed_catalog_episodes


def _catalog(path, *, title: str, episodes: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "title": title,
                        "episodes": {"items": episodes},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_seed_catalog_episodes_inserts_complete_metadata_idempotently(session, make, tmp_path):
    drama = make.drama(title="I Killed My Stepfather with A Kiss", free=12)
    catalog_path = tmp_path / "dramas.json"
    _catalog(
        catalog_path,
        title=drama.title,
        episodes=[
            {"episode_number": 0, "duration_sec": 48},
            {"episode_number": 1, "duration_sec": 55},
            {"episode_number": 2, "duration_sec": 61},
        ],
    )

    first = seed_catalog_episodes(session, path=catalog_path)
    assert first["created"] == 3
    assert first["skipped"] == 0

    rows = list(
        session.scalars(
            select(Episode).where(Episode.drama_id == drama.id).order_by(Episode.episode_number)
        ).all()
    )
    assert [row.episode_number for row in rows] == [1, 2, 3]
    assert [row.duration_seconds for row in rows] == [48, 55, 61]
    assert {row.title for row in rows} == {"Episode 1", "Episode 2", "Episode 3"}
    assert {row.status for row in rows} == {"not_uploaded"}

    second = seed_catalog_episodes(session, path=catalog_path)
    assert second["created"] == 0
    assert second["skipped"] == 3
    assert session.scalar(select(func.count()).select_from(Episode).where(Episode.drama_id == drama.id)) == 3


def test_seed_catalog_episodes_does_not_overwrite_uploaded_rows(session, make, tmp_path):
    drama = make.drama(title="Driven to Love")
    uploaded = make.episode(drama, number=1, status="published")
    uploaded.title = "Uploaded episode"
    uploaded.bunny_video_id = "already-there"
    session.commit()

    catalog_path = tmp_path / "dramas.json"
    _catalog(
        catalog_path,
        title=drama.title,
        episodes=[
            {"episode_number": 0, "duration_sec": 90},
            {"episode_number": 1, "duration_sec": 91},
        ],
    )

    result = seed_catalog_episodes(session, path=catalog_path)
    assert result["created"] == 1

    session.refresh(uploaded)
    assert uploaded.title == "Uploaded episode"
    assert uploaded.status == "published"
    assert uploaded.bunny_video_id == "already-there"
    second = session.scalar(
        select(Episode).where(Episode.drama_id == drama.id, Episode.episode_number == 2)
    )
    assert second is not None
    assert second.duration_seconds == 91


def test_seed_catalog_episodes_can_target_one_drama_and_reports_unmatched(session, make, tmp_path):
    matching = make.drama(title="My Billion Dollar Waiter")
    other = make.drama(title="E2E Smoke Bunny")
    catalog_path = tmp_path / "dramas.json"
    _catalog(
        catalog_path,
        title=matching.title,
        episodes=[{"episode_number": 0, "duration_sec": 42}],
    )

    result = seed_catalog_episodes(session, drama_ids=[matching.id, other.id], path=catalog_path)

    assert result["created"] == 1
    assert [row["id"] for row in result["matched"]] == [matching.id]
    assert [row["id"] for row in result["unmatched"]] == [other.id]
    assert session.scalar(select(func.count()).select_from(Episode).where(Episode.drama_id == matching.id)) == 1
    assert session.scalar(select(func.count()).select_from(Episode).where(Episode.drama_id == other.id)) == 0
