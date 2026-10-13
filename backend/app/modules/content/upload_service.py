"""Direct-to-Bunny upload sessions. The API stores metadata only and never reads video bytes."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

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
    if episode.status in {"in_review", "published"}:
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
) -> dict:
    if mime not in ALLOWED_MIME:
        raise UploadRejected("only MP4 or MOV is accepted")
    if size_bytes > MAX_BYTES:
        raise UploadRejected("file exceeds 200MB")
    if width <= 0 or height <= 0 or height <= width:
        raise UploadRejected("video must be vertical")
    if episode.status == "transcoding":
        raise UploadRejected("upload already in progress")
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

    video_id = secrets.token_hex(16)
    upload = MediaUpload(
        episode_id=episode.id,
        video_id=video_id,
        filename=filename,
        mime=mime,
        size_bytes=size_bytes,
        vertical=True,
        status="created",
    )
    episode.bunny_video_id = video_id
    episode.mime = mime
    episode.size_bytes = size_bytes
    episode.width = width
    episode.height = height
    session.add(upload)
    session.commit()
    return {
        "video_id": video_id,
        "upload_url": f"https://video.bunnycdn.com/tusupload/{video_id}",
        "headers": {
            "AuthorizationExpire": "300",
            "AuthorizationSignature": sign_callback(video_id.encode()),
        },
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
    session.commit()
    return {"status": episode.status, "replayed": False, "video_id": video_id}
