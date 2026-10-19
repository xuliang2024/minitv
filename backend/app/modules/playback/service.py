"""Playback authorization. Tokens are directory-scoped HMAC, default 5 minutes.

Image and video secrets differ so a cover token cannot be replayed as a video URL.
Authorization does not read the transcode queue.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.billing import service as billing
from app.modules.common import bunny
from app.modules.content.models import Drama, Episode
from app.modules.ops.service import get_redis
from app.modules.playback.models import PlaybackEvent, PlaybackProgress
from app.modules.wallet.models import ENTITLEMENT_TYPE_COIN, Entitlement

TOKEN_TTL_SECONDS = 300
EVENT_KINDS = ("start", "progress", "complete")
VIDEO_PREFIX = "/videos/"
IMAGE_PREFIX = "/images/"

# Bunny's transcode ladder, in short-side pixels. Bunny never upscales, so an
# episode uploaded at 480x854 tops out at "480P"; only a 720p+ source gains a
# "720P" rendition.
RENDITION_LADDER = (240, 360, 480, 720, 1080, 1440, 2160)


def renditions_for(width: int, height: int, *, member: bool) -> list[str]:
    """Best-effort rendition names for an episode's uploaded source.

    A hint for API consumers only: the web player's quality menu is driven by
    the real HLS manifest (hls.js levels), which reflects what actually
    transcoded. The ``MEMBER_ONLY_1080`` policy still hides 1080P from
    non-members when enabled.
    """
    short = 0
    for value in (width, height):
        if value and (short == 0 or value < short):
            short = value
    ladder = [step for step in RENDITION_LADDER if step <= short] if short else [360, 720, 1080]
    if os.environ.get("MEMBER_ONLY_1080", "false").lower() == "true" and not member:
        ladder = [step for step in ladder if step < 1080]
    return [f"{step}P" for step in ladder]


def _secret(name: str, fallback: str) -> bytes:
    return os.environ.get(name, fallback).encode()


def video_secret() -> bytes:
    return _secret("PLAYBACK_TOKEN_KEY", "local-playback-token")


def image_secret() -> bytes:
    return _secret("IMAGE_TOKEN_KEY", "local-image-token")


def cdn_base() -> str:
    return os.environ.get("PLAYBACK_CDN_BASE", "https://cdn.local").rstrip("/")


def generation(episode_id: int) -> int:
    raw = get_redis().get(f"playback_gen:{episode_id}")
    return int(raw or 0)


def bump_generation(episode_id: int) -> int:
    return int(get_redis().incr(f"playback_gen:{episode_id}"))


def sign_path(path: str, secret: bytes, *, ttl: int = TOKEN_TTL_SECONDS, gen: int = 0) -> dict:
    if ttl > TOKEN_TTL_SECONDS:
        raise ValueError("playback token ttl cannot exceed 300 seconds")
    expires = int(time.time()) + ttl
    message = f"{path}|{expires}|{gen}".encode()
    token = hmac.new(secret, message, hashlib.sha256).hexdigest()
    url = f"{cdn_base()}{path}?token={token}&expires={expires}&gen={gen}"
    return {"path": path, "token": token, "expires_at": expires, "url": url, "gen": gen}


def verify_token(path: str, expires: int, token: str, gen: int, secret: bytes) -> bool:
    if expires < int(time.time()):
        return False
    message = f"{path}|{expires}|{gen}".encode()
    expected = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, token)


def _active_subscription(session: Session, user_id: int | None) -> bool:
    if user_id is None:
        return False
    # Subscription period or one-time pass, whichever ends last.
    return billing.access_until(session, user_id) is not None


def subscription_active(session: Session, user_id: int | None) -> bool:
    """Whether the viewer currently has a usable membership.

    Public because the drama play page has to paint lock badges with the same
    answer the authoriser uses, instead of re-deriving it.
    """
    return _active_subscription(session, user_id)


def _has_unlock(session: Session, user_id: int | None, episode_id: int) -> bool:
    if user_id is None:
        return False
    row = session.scalar(
        select(Entitlement).where(
            Entitlement.user_id == user_id,
            Entitlement.episode_id == episode_id,
            Entitlement.entitlement_type == ENTITLEMENT_TYPE_COIN,
        )
    )
    return row is not None


def authorize(
    session: Session,
    *,
    drama_id: int,
    episode_id: int,
    user_id: int | None,
    country: str | None = None,
) -> dict:
    drama = session.get(Drama, drama_id)
    episode = session.get(Episode, episode_id)
    if (
        drama is None
        or episode is None
        or episode.drama_id != drama_id
        or drama.status != "published"
        or episode.status != "published"
    ):
        return {"allowed": False, "reason": "not_available"}
    if drama.country_allowlist and country:
        allowed_countries = {c.strip().upper() for c in drama.country_allowlist.split(",") if c.strip()}
        if country.upper() not in allowed_countries:
            return {"allowed": False, "reason": "region_blocked"}

    free = episode.episode_number <= drama.free_episode_count
    member = _active_subscription(session, user_id)
    unlocked = _has_unlock(session, user_id, episode.id)
    if not (free or member or unlocked):
        return {
            "allowed": False,
            "reason": "locked",
            "unlock": {
                "coin_price": drama.coin_price_per_episode,
                "membership_available": True,
            },
        }

    video_id = episode.bunny_video_id or str(episode.id)
    if bunny.live():
        signed = bunny.sign_stream_playback(video_id, ttl=TOKEN_TTL_SECONDS)
    else:
        video_path = f"{VIDEO_PREFIX}{video_id}/"
        signed = sign_path(video_path, video_secret(), gen=generation(episode.id))
    renditions = renditions_for(episode.width or 0, episode.height or 0, member=member)
    subtitle = None
    if episode.subtitle_url:
        if bunny.live():
            subtitle = bunny.sign_image_url(episode.subtitle_url, ttl=TOKEN_TTL_SECONDS)
        else:
            subtitle = sign_path(f"{IMAGE_PREFIX}subs/{episode.id}/", image_secret(), gen=0)
    return {
        "allowed": True,
        "expires_at": signed["expires_at"],
        "url": signed["url"],
        "renditions": renditions,
        "subtitle": subtitle,
        "episode_id": episode.id,
        "episode_number": episode.episode_number,
        "title": episode.title,
        "duration_seconds": episode.duration_seconds,
    }


def record_event(
    session: Session,
    *,
    episode_id: int,
    kind: str,
    position_seconds: int,
    user_id: int | None,
) -> None:
    """Store one real playback event.

    ``kind="start"`` also bumps the drama's lifetime ``play_count`` so admin
    lists and marketing surfaces read a counter that reflects actual plays.
    Progress heartbeats and completions never touch the counter.
    """
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown playback event kind: {kind}")
    session.add(
        PlaybackEvent(
            user_id=user_id,
            episode_id=episode_id,
            kind=kind,
            position_seconds=position_seconds,
        )
    )
    if kind == "start":
        drama_id = session.scalar(select(Episode.drama_id).where(Episode.id == episode_id))
        if drama_id is not None:
            session.execute(
                update(Drama)
                .where(Drama.id == drama_id)
                .values(play_count=Drama.play_count + 1)
            )
    if user_id is not None:
        row = session.get(PlaybackProgress, (user_id, episode_id))
        if row is None:
            session.add(
                PlaybackProgress(
                    user_id=user_id, episode_id=episode_id, position_seconds=position_seconds
                )
            )
        else:
            row.position_seconds = position_seconds
    session.commit()
