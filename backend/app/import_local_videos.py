"""Attach downloaded lite.tv MP4 files to MiniTV episodes via Bunny Stream.

This is the bridge between the local video archive
(``data/litetv-videos-real/<media_id>/``) and the database-backed episode
workflow. It creates one Bunny Stream object per local file, uploads the bytes
with Bunny's HTTP API, and only then writes ``episodes.bunny_video_id``.

Usage:
    cd backend
    ../.venv/bin/python -m app.import_local_videos --drama-id 15 --dry-run
    ../.venv/bin/python -m app.import_local_videos --drama-id 15 --limit 1
    ../.venv/bin/python -m app.import_local_videos --drama-id 15 --workers 3
    ../.venv/bin/python -m app.import_local_videos --drama-id 15 --sync-status

The command is resumable/idempotent: episodes that already have a Bunny GUID
are skipped, so a failed run can simply be started again. Network uploads can
run concurrently; database writes always happen on the main thread.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_engine, load_env, make_session_factory
from app.modules.common import bunny
from app.modules.common.models import AuditLog
from app.modules.content.models import Drama, Episode, MediaUpload
from app.modules.content.service import InvalidTransition, transition_episode

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "data" / "litetv-catalog-20261013" / "dramas.json"
VIDEOS_ROOT = ROOT / "data" / "litetv-videos-real"
EPISODE_FILE_RE = re.compile(r"_ep(\d+)\.mp4$", re.IGNORECASE)


def _normalise_title(title: str) -> str:
    return " ".join(title.split()).casefold()


def _catalog_media_id(title: str, path: Path = CATALOG_PATH) -> int:
    document = json.loads(path.read_text(encoding="utf-8"))
    wanted = _normalise_title(title)
    for item in document.get("items", []):
        if _normalise_title(str(item.get("title", ""))) == wanted:
            return int(item["media_id"])
    raise ValueError(f"drama title not found in catalog: {title!r}")


def _local_files(media_id: int, videos_root: Path = VIDEOS_ROOT) -> list[tuple[int, Path]]:
    folder = videos_root / str(media_id)
    if not folder.is_dir():
        raise FileNotFoundError(f"video directory not found: {folder}")
    files: list[tuple[int, Path]] = []
    for path in folder.glob(f"{media_id}_ep*.mp4"):
        match = EPISODE_FILE_RE.search(path.name)
        if match:
            files.append((int(match.group(1)), path))
    return sorted(files)


def _probe_dimensions(path: Path) -> tuple[int, int, int]:
    """Return ``(width, height, duration_seconds)`` from the local file.

    Duration is also filled from Bunny (``length``) after transcoding, so a
    missing ffprobe degrades to "unknown for now" instead of a hard failure.
    """
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0, 0, 0
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return 0, 0, 0
    try:
        document = json.loads(result.stdout)
        stream = document["streams"][0]
        duration = int(round(float(document.get("format", {}).get("duration") or 0)))
        return int(stream.get("width") or 0), int(stream.get("height") or 0), duration
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return 0, 0, 0


def _transition_to_transcoding(session: Session, episode: Episode) -> None:
    if episode.status == "not_uploaded":
        transition_episode(session, episode, "transcoding")
    elif episode.status == "transcode_failed":
        transition_episode(session, episode, "transcoding")


def _upload_network(path: Path, title: str) -> tuple[str, int, int, int, int]:
    """Network-only upload step, safe to run in a worker thread."""
    video_id = bunny.create_stream_video(title=title)
    try:
        bunny.stream_upload(video_id, path)
    except Exception:
        # Do not leave an empty, half-created Bunny object behind on retry.
        try:
            bunny.delete_stream_video(video_id)
        except bunny.BunnyError:
            pass
        raise
    width, height, duration_seconds = _probe_dimensions(path)
    return video_id, path.stat().st_size, width, height, duration_seconds


def _record_upload(
    session: Session,
    *,
    episode_id: int,
    path: Path,
    video_id: str,
    size_bytes: int,
    width: int,
    height: int,
    duration_seconds: int = 0,
    previous_video_id: str = "",
) -> None:
    """Main-thread DB write after a Bunny upload has succeeded.

    ``previous_video_id`` is set when this upload replaced an existing Bunny
    object (``--replace``); the old object is intentionally left in place
    until the caller verifies the replacement, and its id is recorded in the
    audit entry so it can be pruned later.
    """
    episode = session.get(Episode, episode_id)
    if episode is None:
        raise ValueError(f"episode disappeared during upload: {episode_id}")
    episode.bunny_video_id = video_id
    episode.mime = "video/mp4"
    episode.size_bytes = size_bytes
    episode.width = width
    episode.height = height
    if duration_seconds > 0:
        episode.duration_seconds = duration_seconds
    _transition_to_transcoding(session, episode)
    try:
        source_file = str(path.relative_to(ROOT))
    except ValueError:
        source_file = str(path)
    session.add(
        MediaUpload(
            episode_id=episode.id,
            video_id=video_id,
            filename=path.name,
            mime="video/mp4",
            size_bytes=size_bytes,
            vertical=height > width if width and height else True,
            status="completed",
        )
    )
    audit_payload: dict[str, object] = {"video_id": video_id, "file": source_file}
    if previous_video_id:
        audit_payload.update({"replace": True, "previous_video_id": previous_video_id})
    session.add(
        AuditLog(
            actor_type="system",
            actor_id=None,
            action="local_video_import",
            target_type="episode",
            target_id=episode.id,
            payload=audit_payload,
        )
    )
    session.commit()


def _sync_statuses(session: Session, episodes: Iterable[Episode]) -> dict[str, int]:
    updated = in_review = failed = durations = 0
    for episode in episodes:
        if not episode.bunny_video_id:
            continue
        remote = bunny.stream_video(episode.bunny_video_id)
        status = int(remote.get("status", 0))
        length = int(remote.get("length") or 0)
        if length > 0 and episode.duration_seconds != length:
            # Bunny's transcoded length is authoritative; also heals rows that
            # were uploaded before duration was recorded.
            episode.duration_seconds = length
            durations += 1
            updated += 1
        if status == 4 and episode.status == "transcoding":
            transition_episode(session, episode, "in_review")
            in_review += 1
            updated += 1
        elif status >= 5 and episode.status == "transcoding":
            try:
                transition_episode(session, episode, "transcode_failed")
            except InvalidTransition:
                pass
            failed += 1
            updated += 1
    if updated:
        session.commit()
    return {"updated": updated, "in_review": in_review, "failed": failed, "durations": durations}


def pending_video_jobs(session: Session, *, catalog_path: Path = CATALOG_PATH, videos_root: Path = VIDEOS_ROOT) -> list[tuple[int, int]]:
    """Return ``(drama_id, media_id)`` for dramas that still have a local MP4 without a Bunny id."""
    document = json.loads(catalog_path.read_text(encoding="utf-8"))
    jobs: list[tuple[int, int]] = []
    for item in document.get("items", []):
        title = " ".join(str(item.get("title", "")).split())
        drama = session.scalar(select(Drama).where(Drama.title == title))
        if drama is None:
            continue
        media_id = int(item["media_id"])
        try:
            files = _local_files(media_id, videos_root)
        except FileNotFoundError:
            continue
        needed = False
        for file_number, _path in files:
            episode = session.scalar(
                select(Episode).where(
                    Episode.drama_id == drama.id,
                    Episode.episode_number == file_number + 1,
                )
            )
            if episode is not None and not episode.bunny_video_id:
                needed = True
                break
        if needed:
            jobs.append((drama.id, media_id))
    return jobs


def import_local_videos(
    session: Session,
    *,
    drama_id: int,
    media_id: int | None = None,
    catalog_path: Path = CATALOG_PATH,
    videos_root: Path = VIDEOS_ROOT,
    limit: int | None = None,
    dry_run: bool = False,
    sync_status: bool = False,
    workers: int = 1,
    replace: bool = False,
) -> dict[str, object]:
    """Upload local MP4s for one drama and/or synchronize Bunny statuses.

    ``media_id`` overrides the catalog lookup by title; demo/seed dramas whose
    title is not in the fetched catalog (e.g. drama #1 backed by media 180)
    must pass it explicitly.

    ``replace`` re-uploads episodes that already have a Bunny GUID (e.g. after
    the local archive was upgraded from 480p to 720p sources). Bunny refuses
    to overwrite an existing object ("The video has already been uploaded"),
    so a fresh object is created and swapped in; the previous object id is
    kept in the audit log for later cleanup instead of being deleted.
    """
    drama = session.get(Drama, drama_id)
    if drama is None:
        raise ValueError(f"drama not found: {drama_id}")
    media_id = media_id or _catalog_media_id(drama.title, catalog_path)
    files = _local_files(media_id, videos_root)
    if limit is not None:
        files = files[:limit]

    rows: list[dict[str, object]] = []
    jobs: list[tuple[int, int, Path, str]] = []
    skipped = missing = 0
    for file_number, path in files:
        episode_number = file_number + 1
        episode = session.scalar(
            select(Episode).where(
                Episode.drama_id == drama.id,
                Episode.episode_number == episode_number,
            )
        )
        if episode is None:
            rows.append({"episode": episode_number, "file": path.name, "status": "missing_episode"})
            missing += 1
            continue
        if episode.bunny_video_id and not replace:
            rows.append(
                {
                    "episode": episode_number,
                    "file": path.name,
                    "status": "already_uploaded",
                    "video_id": episode.bunny_video_id,
                }
            )
            skipped += 1
            continue
        previous_video_id = episode.bunny_video_id or ""
        if dry_run:
            rows.append(
                {
                    "episode": episode_number,
                    "file": path.name,
                    "status": "would_replace" if previous_video_id else "would_upload",
                    "previous_video_id": previous_video_id,
                }
            )
            continue
        jobs.append(
            (
                episode.id,
                episode_number,
                path,
                f"{drama.title} - Episode {episode_number}",
                previous_video_id,
            )
        )

    uploaded = 0
    replaced = 0
    if jobs and workers <= 1:
        for episode_id, episode_number, path, title, previous_video_id in jobs:
            video_id, size_bytes, width, height, duration_seconds = _upload_network(path, title)
            _record_upload(
                session,
                episode_id=episode_id,
                path=path,
                video_id=video_id,
                size_bytes=size_bytes,
                width=width,
                height=height,
                duration_seconds=duration_seconds,
                previous_video_id=previous_video_id,
            )
            rows.append(
                {
                    "episode": episode_number,
                    "file": path.name,
                    "status": "replaced" if previous_video_id else "uploaded",
                    "video_id": video_id,
                    "previous_video_id": previous_video_id,
                }
            )
            uploaded += 1
            if previous_video_id:
                replaced += 1
    elif jobs:
        with futures.ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {
                pool.submit(_upload_network, path, title): (
                    episode_id,
                    episode_number,
                    path,
                    previous_video_id,
                )
                for episode_id, episode_number, path, title, previous_video_id in jobs
            }
            for future in futures.as_completed(pending):
                episode_id, episode_number, path, previous_video_id = pending[future]
                try:
                    video_id, size_bytes, width, height, duration_seconds = future.result()
                except Exception as exc:  # keep other uploads going, report at the end
                    rows.append(
                        {
                            "episode": episode_number,
                            "file": path.name,
                            "status": "failed",
                            "error": str(exc),
                        }
                    )
                    continue
                _record_upload(
                    session,
                    episode_id=episode_id,
                    path=path,
                    video_id=video_id,
                    size_bytes=size_bytes,
                    width=width,
                    height=height,
                    duration_seconds=duration_seconds,
                    previous_video_id=previous_video_id,
                )
                rows.append(
                    {
                        "episode": episode_number,
                        "file": path.name,
                        "status": "replaced" if previous_video_id else "uploaded",
                        "video_id": video_id,
                        "previous_video_id": previous_video_id,
                    }
                )
                uploaded += 1
                if previous_video_id:
                    replaced += 1

    status_result = {"updated": 0, "in_review": 0, "failed": 0}
    if sync_status and not dry_run:
        status_result = _sync_statuses(
            session,
            session.scalars(
                select(Episode).where(
                    Episode.drama_id == drama.id,
                    Episode.bunny_video_id != "",
                )
            ).all(),
        )

    rows.sort(key=lambda row: int(row["episode"]))
    return {
        "drama_id": drama.id,
        "title": drama.title,
        "media_id": media_id,
        "dry_run": dry_run,
        "uploaded": uploaded,
        "replaced": replaced,
        "skipped": skipped,
        "missing": missing,
        "rows": rows,
        "sync": status_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload local MP4 files to Bunny Stream.")
    parser.add_argument("--drama-id", type=int, action="append", dest="drama_ids")
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="upload every catalog drama that still has a local MP4 without a Bunny video id",
    )
    parser.add_argument(
        "--media-id",
        type=int,
        help="override the catalog media id (for demo dramas whose title is not in the catalog)",
    )
    parser.add_argument("--limit", type=int, help="only process the first N local files")
    parser.add_argument("--workers", type=int, default=3, help="concurrent Bunny uploads (default 3)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without uploading")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="re-upload episodes that already have a Bunny video (new object; the old one is kept)",
    )
    parser.add_argument(
        "--sync-status",
        action="store_true",
        help="also poll Bunny and move finished videos to in_review",
    )
    args = parser.parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    load_env()

    if not bunny.live() and not args.dry_run:
        raise SystemExit("BUNNY_MODE must be real before uploading; use --dry-run to inspect only")
    if not args.drama_ids and not args.missing_only:
        raise SystemExit("pass --drama-id or --missing-only")

    factory = make_session_factory(get_engine())
    with factory() as session:
        targets = [(drama_id, args.media_id) for drama_id in (args.drama_ids or [])]
        if args.missing_only:
            targets = pending_video_jobs(session)
        results = []
        for drama_id, media_id in targets:
            results.append(
                import_local_videos(
                    session,
                    drama_id=drama_id,
                    media_id=media_id,
                    limit=args.limit,
                    dry_run=args.dry_run,
                    sync_status=args.sync_status,
                    workers=args.workers,
                    replace=args.replace,
                )
            )

    uploaded = skipped = failed = 0
    for result in results:
        prefix = "dry-run: " if result["dry_run"] else ""
        row_failed = sum(1 for row in result["rows"] if row["status"] == "failed")
        failed += row_failed
        uploaded += int(result["uploaded"])
        skipped += int(result["skipped"])
        print(
            f"{prefix}drama #{result['drama_id']} {result['title']} "
            f"(media {result['media_id']}): uploaded {result['uploaded']} "
            f"(replaced {result['replaced']}), skipped {result['skipped']}, "
            f"failed {row_failed}, missing episode rows {result['missing']}"
        )
        for row in result["rows"]:
            if row["status"] in {"already_uploaded", "would_upload"} and not args.dry_run:
                continue
            if row["status"] == "already_uploaded":
                continue
            suffix = f" -> {row['video_id']}" if row.get("video_id") else ""
            if row.get("error"):
                suffix = f" -> {row['error']}"
            print(f"  ep{row['episode']:03d} {row['file']}: {row['status']}{suffix}")
    print(f"total uploaded {uploaded}, skipped {skipped}, failed {failed}, dramas {len(results)}")


if __name__ == "__main__":
    main()
