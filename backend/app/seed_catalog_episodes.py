"""Seed dramas and complete episode metadata from the local lite.tv catalog.

The local catalog contains every published episode for 100 dramas, including
episodes whose video was not downloaded. The admin episode API only reads the
``episodes`` table, so a drama is not usable until those rows exist.

Usage:
    cd backend && ../.venv/bin/python -m app.seed_catalog_episodes
    cd backend && ../.venv/bin/python -m app.seed_catalog_episodes --drama-id 15
    cd backend && ../.venv/bin/python -m app.seed_catalog_episodes --dry-run

Guarantees:
* Idempotent: dramas are keyed by exact title; episodes by ``(drama_id, episode_number)``.
* Non-destructive: existing drama and episode rows are never changed.
* Missing catalog titles are inserted as published drafts of metadata only.
  Episodes start at ``not_uploaded``; video bytes are a separate upload step.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_engine, make_session_factory
from app.modules.content.models import Drama, DramaTag, Episode, Tag

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "data" / "litetv-catalog-20261013" / "dramas.json"


def _normalise_title(title: str) -> str:
    return " ".join(title.split()).casefold()


def load_catalog(path: Path = CATALOG_PATH) -> list[dict]:
    """Return catalog drama documents that have a title and episode list."""
    if not path.exists():
        raise FileNotFoundError(f"catalog not found: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for drama in document.get("items", []):
        title = str(drama.get("title", "")).strip()
        episodes = drama.get("episodes", {}).get("items", [])
        if title and episodes:
            items.append(drama)
    return items


def load_catalog_episodes(path: Path = CATALOG_PATH) -> dict[str, list[dict]]:
    """Return ``{normalised title: catalog episodes}`` from ``dramas.json``."""
    return {
        _normalise_title(str(drama["title"])): drama["episodes"]["items"]
        for drama in load_catalog(path)
    }


def _cover_url(drama: dict) -> str:
    covers = drama.get("covers") or {}
    return str(covers.get("poster_source_url") or "")[:500]


def _tag_names(drama: dict) -> list[tuple[str, str]]:
    classification = drama.get("classification") or {}
    names: list[tuple[str, str]] = []
    main = str(classification.get("main_category") or "").strip()
    if main:
        names.append((main, "main"))
    for name in classification.get("sub_categories") or []:
        text = str(name).strip()
        if text and text != main:
            names.append((text, "sub"))
    return names


def _ensure_tag(session: Session, name: str, category: str) -> Tag:
    tag = session.scalar(select(Tag).where(Tag.name_en == name))
    if tag is None:
        tag = Tag(name_en=name[:64], name_zh="", category=category, status="enabled")
        session.add(tag)
        session.flush()
    return tag


def ensure_catalog_drama(session: Session, item: dict, *, dry_run: bool = False) -> tuple[Drama | None, bool]:
    """Insert a missing catalog drama. Existing rows are left untouched."""
    title = str(item["title"]).strip()[:200]
    existing = session.scalar(select(Drama).where(Drama.title == title))
    if existing is not None:
        return existing, False
    classification = item.get("classification") or {}
    if dry_run:
        return None, True
    drama = Drama(
        title=title,
        synopsis=str(item.get("description") or ""),
        cover_url=_cover_url(item),
        free_episode_count=int(classification.get("free_episode_count") or 0),
        coin_price_per_episode=int(classification.get("coin_price_per_episode") or 0),
        status="published",
        play_count=0,
    )
    session.add(drama)
    session.flush()
    linked = set(
        session.scalars(select(DramaTag.tag_id).where(DramaTag.drama_id == drama.id)).all()
    )
    for name, category in _tag_names(item):
        tag = _ensure_tag(session, name, category)
        if tag.id not in linked:
            session.add(DramaTag(drama_id=drama.id, tag_id=tag.id))
            linked.add(tag.id)
    return drama, True


def seed_drama_episodes(
    session: Session,
    drama: Drama,
    catalog_episodes: Iterable[dict],
    *,
    dry_run: bool = False,
) -> int:
    """Insert missing rows for one drama and return the number created."""
    existing = set(
        session.scalars(
            select(Episode.episode_number).where(Episode.drama_id == drama.id)
        ).all()
    )
    created = 0
    for item in catalog_episodes:
        # The source catalog is zero-based; MiniTV admin/client numbering is
        # one-based (Episode 1 == catalog episode_number 0).
        number = int(item["episode_number"]) + 1
        if number in existing:
            continue
        if not dry_run:
            session.add(
                Episode(
                    drama_id=drama.id,
                    episode_number=number,
                    title=f"Episode {number}",
                    duration_seconds=int(item.get("duration_sec") or 0),
                    status="not_uploaded",
                )
            )
        existing.add(number)
        created += 1
    return created


def seed_catalog_episodes(
    session: Session,
    *,
    drama_ids: Iterable[int] | None = None,
    path: Path = CATALOG_PATH,
    dry_run: bool = False,
    create_missing: bool = True,
) -> dict[str, object]:
    """Seed catalog dramas and their complete episode metadata."""
    items = load_catalog(path)
    created_dramas: list[dict[str, object]] = []
    if create_missing and drama_ids is None:
        for item in items:
            drama, created = ensure_catalog_drama(session, item, dry_run=dry_run)
            if created:
                created_dramas.append(
                    {
                        "id": None if drama is None else drama.id,
                        "title": str(item["title"]),
                        "media_id": item.get("media_id"),
                    }
                )

    catalog = {
        _normalise_title(str(item["title"])): item["episodes"]["items"]
        for item in items
    }
    stmt = select(Drama).order_by(Drama.id)
    if drama_ids is not None:
        ids = list(drama_ids)
        stmt = stmt.where(Drama.id.in_(ids)) if ids else stmt.where(False)
    dramas = list(session.scalars(stmt).all())
    matched: list[dict[str, object]] = []
    unmatched: list[dict[str, object]] = []
    created_total = 0
    for drama in dramas:
        episodes = catalog.get(_normalise_title(drama.title))
        if not episodes:
            unmatched.append({"id": drama.id, "title": drama.title})
            continue
        created = seed_drama_episodes(session, drama, episodes, dry_run=dry_run)
        created_total += created
        matched.append(
            {
                "id": drama.id,
                "title": drama.title,
                "catalog_total": len(episodes),
                "created": created,
            }
        )
    if not dry_run:
        session.commit()
    return {
        "dry_run": dry_run,
        "dramas": len(dramas),
        "created_dramas": created_dramas,
        "matched": matched,
        "unmatched": unmatched,
        "created": created_total,
        "skipped": sum(int(row["catalog_total"]) - int(row["created"]) for row in matched),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import complete episode metadata from the local lite.tv catalog."
    )
    parser.add_argument(
        "--drama-id",
        type=int,
        action="append",
        dest="drama_ids",
        help="only seed this drama id; repeat for multiple ids",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be created without writing to the database",
    )
    args = parser.parse_args()

    factory = make_session_factory(get_engine())
    with factory() as session:
        result = seed_catalog_episodes(
            session,
            drama_ids=args.drama_ids,
            dry_run=args.dry_run,
        )

    prefix = "dry-run: " if result["dry_run"] else ""
    print(
        f"{prefix}created {len(result['created_dramas'])} dramas; "
        f"matched {len(result['matched'])}/{result['dramas']} dramas; "
        f"created {result['created']} episodes; skipped {result['skipped']} existing"
    )
    for row in result["created_dramas"]:
        print(f"  + drama #{row['id']} media {row['media_id']} {row['title']}")
    for row in result["matched"]:
        print(
            f"  #{row['id']} {row['title']}: catalog {row['catalog_total']}, "
            f"created {row['created']}"
        )
    for row in result["unmatched"]:
        print(f"  unmatched #{row['id']} {row['title']}")


if __name__ == "__main__":
    main()
