"""Direct-to-Bunny upload sessions. The API stores metadata only and never reads video bytes."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.common import bunny
from app.modules.common.models import AuditLog
from app.modules.content.models import Episode, MediaUpload
from app.modules.content.service import InvalidTransition, transition_episode

MAX_BYTES = 200 * 1024 * 1024
ALLOWED_MIME = {"video/mp4", "video/quicktime"}


class UploadRejected(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def callback_secret() -> bytes:
    return os.environ.get("BUNNY_CALLBACK_SECRET", "local-dev-callback").encode()


def sign_callback(body: bytes) -> str:
    return hmac.new(callback_secret(), body, hashlib.sha256).hexdigest()


def _reset_for_replace(session: Session, episode: Episode) -> None:
    if episode.status in {"in_review", "published", "unpublished"}:
        transition_episode(session, episode, "not_uploaded")
        episode.bunny_video_id = ""


def create_upload_session(
    session: Session,
    *,
    episode: Episode,
    filename: str,
    mime: str,
    size_bytes: int,
    width: int,
    height: int,
    duration_seconds: int = 0,
) -> dict:
    if mime not in ALLOWED_MIME:
        raise UploadRejected("only MP4 or MOV is accepted")
    if size_bytes > MAX_BYTES:
        raise UploadRejected("file exceeds 200MB")
    # Orientation is deliberately unconstrained: operations uploads both
    # vertical and landscape episodes. Only dimensions that cannot describe a
    # real frame are rejected; the ratio is never used as a gate.
    if width < 0 or height < 0:
        raise UploadRejected("invalid video dimensions")
    if episode.status == "transcoding":
        raise UploadRejected("upload already in progress")
    previous_video_id = episode.bunny_video_id
    try:
        _reset_for_replace(session, episode)
        if episode.status == "transcode_failed":
            transition_episode(session, episode, "transcoding")
        elif episode.status == "not_uploaded":
            transition_episode(session, episode, "transcoding")
        else:
            raise UploadRejected(f"cannot upload from status {episode.status}")
    except InvalidTransition as exc:
        raise UploadRejected(str(exc)) from exc

    # ``vertical`` stays as metadata only (analysis/reporting); it is not a gate.
    vertical = height > width if width > 0 and height > 0 else True
    if bunny.live():
        video_id = bunny.create_stream_video(title=filename)
    else:
        video_id = secrets.token_hex(16)
    upload = MediaUpload(
        episode_id=episode.id,
        video_id=video_id,
        filename=filename,
        mime=mime,
        size_bytes=size_bytes,
        vertical=vertical,
        status="created",
    )
    episode.bunny_video_id = video_id
    episode.mime = mime
    episode.size_bytes = size_bytes
    episode.width = width
    episode.height = height
    if duration_seconds > 0:
        # Browser-probed length: a useful placeholder until Bunny reports the
        # authoritative one (callbacks / sync-status overwrite it in real mode).
        episode.duration_seconds = duration_seconds
    elif previous_video_id:
        # A replacement length is unknown at this point; the old duration
        # describes the previous video and must not survive the swap.
        episode.duration_seconds = 0
    session.add(upload)
    session.commit()
    if bunny.live():
        # Real TUS: create the session with POST + these headers, then PATCH the
        # Location returned by Bunny. The same four headers go on every request.
        upload_url = bunny.STREAM_TUS_ENDPOINT
        headers = bunny.tus_headers(video_id)
    else:
        # Fake mode: the admin console PATCHes the bytes to the local dev
        # receiver, which completes the upload immediately.
        upload_url = f"/admin/v1/dev-tus/{video_id}"
        headers = {
            "AuthorizationExpire": "300",
            "AuthorizationSignature": sign_callback(video_id.encode()),
        }
    return {
        "video_id": video_id,
        "upload_url": upload_url,
        "headers": headers,
        "episode_id": episode.id,
        "status": episode.status,
    }


def apply_callback(session: Session, *, body: bytes, signature: str, video_id: str, outcome: str) -> dict:
    expected = sign_callback(body)
    if not hmac.compare_digest(expected, signature or ""):
        session.add(
            AuditLog(
                actor_type="system",
                actor_id=None,
                action="bunny_callback_rejected",
                target_type="media_upload",
                target_id=None,
                payload={"video_id": video_id},
            )
        )
        session.commit()
        raise UploadRejected("bad signature")

    upload = session.scalar(select(MediaUpload).where(MediaUpload.video_id == video_id))
    if upload is None:
        raise UploadRejected("unknown video")
    episode = session.get(Episode, upload.episode_id)
    if episode is None:
        raise UploadRejected("episode missing")
    if upload.status == "completed":
        return {"status": episode.status, "replayed": True, "video_id": video_id}

    if outcome == "failed":
        if episode.status == "transcoding":
            transition_episode(session, episode, "transcode_failed")
        upload.status = "failed"
    else:
        if episode.status == "transcoding":
            transition_episode(session, episode, "in_review")
        upload.status = "completed"
        # Real Bunny: read the transcoded length so the admin table (and the
        # player payload) knows the duration. Best effort - a metadata hiccup
        # must not fail the callback, the sync script fills it later.
        if bunny.live():
            try:
                length = int(bunny.stream_video(video_id).get("length") or 0)
            except bunny.BunnyError:
                length = 0
            if length > 0:
                episode.duration_seconds = length
    session.commit()
    return {"status": episode.status, "replayed": False, "video_id": video_id}
