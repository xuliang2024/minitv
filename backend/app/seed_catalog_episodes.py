"""Seed complete episode metadata from the local lite.tv catalog.

The local catalog contains every published episode for the imported dramas,
including episodes whose video was not downloaded. The admin episode API only
reads the ``episodes`` table, so importing a drama without this step leaves the
分集 tab empty even though metadata exists in
``data/litetv-catalog-20261013/dramas.json``.

Usage:
    cd backend && ../.venv/bin/python -m app.seed_catalog_episodes
    cd backend && ../.venv/bin/python -m app.seed_catalog_episodes --drama-id 15
    cd backend && ../.venv/bin/python -m app.seed_catalog_episodes --dry-run

Guarantees:
* Idempotent: episodes are keyed by ``(drama_id, episode_number)``.
* Non-destructive: existing episode rows are never changed.
* Local-data driven: only exact title matches are imported; unmatched dramas
  are reported and skipped.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_engine, make_session_factory
from app.modules.content.models import Drama, Episode

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "data" / "litetv-catalog-20261013" / "dramas.json"


def _normalise_title(title: str) -> str:
    return " ".join(title.split()).casefold()


def load_catalog_episodes(path: Path = CATALOG_PATH) -> dict[str, list[dict]]:
    """Return ``{normalised title: catalog episodes}`` from ``dramas.json``."""
    if not path.exists():
        raise FileNotFoundError(f"catalog not found: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    by_title: dict[str, list[dict]] = {}
    for drama in document.get("items", []):
        title = _normalise_title(str(drama.get("title", "")))
        episodes = drama.get("episodes", {}).get("items", [])
        if title and episodes:
            by_title[title] = episodes
    return by_title


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
) -> dict[str, object]:
    """Seed complete catalog episode metadata for matching local dramas."""
    catalog = load_catalog_episodes(path)
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
        f"{prefix}matched {len(result['matched'])}/{result['dramas']} dramas; "
        f"created {result['created']} episodes; skipped {result['skipped']} existing"
    )
    for row in result["matched"]:
        print(
            f"  #{row['id']} {row['title']}: catalog {row['catalog_total']}, "
            f"created {row['created']}"
        )
    for row in result["unmatched"]:
        print(f"  unmatched #{row['id']} {row['title']}")


if __name__ == "__main__":
    main()
