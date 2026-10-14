from __future__ import annotations

import json

from sqlalchemy import func, select

from app.modules.common import bunny
from app.modules.common.models import AuditLog
from app.modules.content.models import Episode, MediaUpload
from app.import_local_videos import import_local_videos


def _catalog(path, *, media_id: int, title: str) -> None:
    path.write_text(
        json.dumps({"items": [{"media_id": media_id, "title": title}]}),
        encoding="utf-8",
    )


def test_import_local_videos_uploads_backfills_and_is_idempotent(
    session, make, tmp_path, monkeypatch
):
    drama = make.drama(title="I Killed My Stepfather with A Kiss", status="published", free=12)
    make.episode(drama, number=1)
    make.episode(drama, number=2)

    catalog_path = tmp_path / "dramas.json"
    _catalog(catalog_path, media_id=204, title=drama.title)
    videos_root = tmp_path / "videos"
    media_dir = videos_root / "204"
    media_dir.mkdir(parents=True)
    (media_dir / "204_ep000.mp4").write_bytes(b"episode-zero")
    (media_dir / "204_ep001.mp4").write_bytes(b"episode-one")

    created: list[str] = []
    uploaded: list[tuple[str, str]] = []
    monkeypatch.setattr(bunny, "create_stream_video", lambda title: created.append(title) or f"guid-{len(created)}")
    monkeypatch.setattr(bunny, "stream_upload", lambda video_id, path: uploaded.append((video_id, path.name)))
    monkeypatch.setattr(bunny, "stream_video", lambda video_id: {"status": 4})

    first = import_local_videos(
        session,
        drama_id=drama.id,
        catalog_path=catalog_path,
        videos_root=videos_root,
        sync_status=True,
        workers=2,
    )
    assert first["uploaded"] == 2
    assert first["sync"]["in_review"] == 2
    assert len(uploaded) == 2
    assert created == [
        f"{drama.title} - Episode 1",
        f"{drama.title} - Episode 2",
    ]

    rows = list(
        session.scalars(
            select(Episode).where(Episode.drama_id == drama.id).order_by(Episode.episode_number)
        ).all()
    )
    assert [row.bunny_video_id for row in rows] == ["guid-1", "guid-2"]
    assert [row.status for row in rows] == ["in_review", "in_review"]
    assert [row.size_bytes for row in rows] == [len(b"episode-zero"), len(b"episode-one")]
    assert session.scalar(select(func.count()).select_from(MediaUpload)) == 2
    assert session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "local_video_import")
    ) == 2

    second = import_local_videos(
        session,
        drama_id=drama.id,
        catalog_path=catalog_path,
        videos_root=videos_root,
        sync_status=True,
    )
    assert second["uploaded"] == 0
    assert second["skipped"] == 2
    assert len(uploaded) == 2


def test_import_local_videos_media_id_override(session, make, tmp_path, monkeypatch):
    """Demo dramas whose title is not in the catalog pass ``media_id`` explicitly."""
    drama = make.drama(title="I Became the Mob Boss's Wife", status="published", free=12)
    make.episode(drama, number=1)

    catalog_path = tmp_path / "dramas.json"
    _catalog(catalog_path, media_id=180, title="30 Days Swapping Lives with a Mafia Boss")
    videos_root = tmp_path / "videos"
    media_dir = videos_root / "180"
    media_dir.mkdir(parents=True)
    (media_dir / "180_ep000.mp4").write_bytes(b"episode-zero")

    monkeypatch.setattr(bunny, "create_stream_video", lambda title: "guid-override")
    monkeypatch.setattr(bunny, "stream_upload", lambda video_id, path: None)
    monkeypatch.setattr(bunny, "stream_video", lambda video_id: {"status": 4})

    result = import_local_videos(
        session,
        drama_id=drama.id,
        media_id=180,
        catalog_path=catalog_path,
        videos_root=videos_root,
        sync_status=True,
    )
    assert result["media_id"] == 180
    assert result["uploaded"] == 1
    episode = session.scalar(select(Episode).where(Episode.drama_id == drama.id))
    assert episode.bunny_video_id == "guid-override"
    assert episode.status == "in_review"


def test_import_local_videos_fills_duration_from_probe_and_bunny(
    session, make, tmp_path, monkeypatch
):
    """Duration is probed locally at upload and healed from Bunny on sync."""
    import app.import_local_videos as import_module

    drama = make.drama(title="Duration Drama", status="published")
    make.episode(drama, number=1)

    catalog_path = tmp_path / "dramas.json"
    _catalog(catalog_path, media_id=999, title=drama.title)
    videos_root = tmp_path / "videos"
    media_dir = videos_root / "999"
    media_dir.mkdir(parents=True)
    (media_dir / "999_ep000.mp4").write_bytes(b"episode-zero")

    monkeypatch.setattr(bunny, "create_stream_video", lambda title: "guid-duration")
    monkeypatch.setattr(bunny, "stream_upload", lambda video_id, path: None)
    monkeypatch.setattr(bunny, "stream_video", lambda video_id: {"status": 4, "length": 96})
    monkeypatch.setattr(import_module, "_probe_dimensions", lambda path: (720, 1280, 95))

    result = import_local_videos(
        session,
        drama_id=drama.id,
        catalog_path=catalog_path,
        videos_root=videos_root,
        sync_status=True,
    )
    assert result["uploaded"] == 1
    assert result["sync"]["durations"] == 1
    episode = session.scalar(select(Episode).where(Episode.drama_id == drama.id))
    # probe wrote 95 at upload time, then Bunny's length (96) won on sync
    assert episode.duration_seconds == 96


def test_import_local_videos_replace_swaps_guid_and_audits_previous(
    session, make, tmp_path, monkeypatch
):
    """``replace=True`` re-uploads existing episodes and records the old guid.

    Bunny refuses to overwrite an existing object ("The video has already
    been uploaded"), so a replacement uploads a fresh object; the previous
    guid must survive in the audit log for later cleanup.
    """
    drama = make.drama(title="I Killed My Stepfather with A Kiss", status="published", free=12)
    make.episode(drama, number=1)

    catalog_path = tmp_path / "dramas.json"
    _catalog(catalog_path, media_id=204, title=drama.title)
    videos_root = tmp_path / "videos"
    media_dir = videos_root / "204"
    media_dir.mkdir(parents=True)
    source = media_dir / "204_ep000.mp4"
    source.write_bytes(b"episode-zero-480p")

    created: list[str] = []
    monkeypatch.setattr(
        bunny, "create_stream_video", lambda title: created.append(title) or f"guid-{len(created)}"
    )
    monkeypatch.setattr(bunny, "stream_upload", lambda video_id, path: None)
    monkeypatch.setattr(bunny, "stream_video", lambda video_id: {"status": 4})

    first = import_local_videos(
        session, drama_id=drama.id, catalog_path=catalog_path, videos_root=videos_root
    )
    assert first["uploaded"] == 1
    assert first["replaced"] == 0

    # The archive was upgraded in place (same filename, 720p content).
    source.write_bytes(b"episode-zero-720p")

    keep = import_local_videos(
        session, drama_id=drama.id, catalog_path=catalog_path, videos_root=videos_root
    )
    assert keep["uploaded"] == 0
    assert keep["skipped"] == 1

    replaced = import_local_videos(
        session,
        drama_id=drama.id,
        catalog_path=catalog_path,
        videos_root=videos_root,
        replace=True,
    )
    assert replaced["uploaded"] == 1
    assert replaced["replaced"] == 1
    assert replaced["rows"][0]["status"] == "replaced"
    assert replaced["rows"][0]["previous_video_id"] == "guid-1"

    episode = session.scalar(select(Episode).where(Episode.drama_id == drama.id))
    assert episode.bunny_video_id == "guid-2"

    latest = session.scalars(
        select(AuditLog)
        .where(AuditLog.action == "local_video_import", AuditLog.target_id == episode.id)
        .order_by(AuditLog.id.desc())
    ).first()
    assert latest is not None
    assert latest.payload["previous_video_id"] == "guid-1"
    assert latest.payload["video_id"] == "guid-2"
    assert latest.payload["replace"] is True

    # A dry run lists the replacement without touching Bunny.
    plan = import_local_videos(
        session,
        drama_id=drama.id,
        catalog_path=catalog_path,
        videos_root=videos_root,
        dry_run=True,
        replace=True,
    )
    assert plan["rows"][0]["status"] == "would_replace"
    assert plan["rows"][0]["previous_video_id"] == "guid-2"
    assert len(created) == 2
