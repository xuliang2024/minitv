"""Admin manual episode upload: create row -> direct upload -> status sync."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.content.models import Episode, MediaUpload


@pytest.fixture()
def client(session):
    return TestClient(app)


def _login(client, username, password="pass-1234"):
    response = client.post("/admin/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _ops(make, client):
    staff = make.admin(role="ops")
    return _login(client, staff.username)


def _upload_session(client, headers, episode_id, **overrides):
    payload = {
        "filename": "新分集.mp4",
        "mime": "video/mp4",
        "size_bytes": 1024,
        "width": 720,
        "height": 1280,
        **overrides,
    }
    return client.post(f"/admin/v1/episodes/{episode_id}/upload-session", json=payload, headers=headers)


def test_create_episode_defaults_title_and_rejects_duplicate(make, client):
    headers = _ops(make, client)
    drama = make.drama(title="Fresh Drama")

    created = client.post(
        f"/admin/v1/dramas/{drama.id}/episodes",
        json={"episode_number": 1, "title": "   "},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["episode_number"] == 1
    assert body["title"] == "Episode 1"
    assert body["status"] == "not_uploaded"

    duplicate = client.post(
        f"/admin/v1/dramas/{drama.id}/episodes",
        json={"episode_number": 1, "title": "again"},
        headers=headers,
    )
    assert duplicate.status_code == 409
    assert "已存在" in duplicate.json()["detail"]


def test_sync_status_finishes_transcoding_in_fake_mode(make, client, session):
    headers = _ops(make, client)
    drama = make.drama(title="Sync Drama")
    episode = make.episode(drama, number=1)

    session_data = _upload_session(client, headers, episode.id)
    assert session_data.status_code == 200, session_data.text
    video_id = session_data.json()["video_id"]

    synced = client.post(f"/admin/v1/episodes/{episode.id}/sync-status", headers=headers)
    assert synced.status_code == 200, synced.text
    assert synced.json()["status"] == "in_review"

    session.expire_all()
    upload = session.scalar(select(MediaUpload).where(MediaUpload.video_id == video_id))
    assert upload is not None and upload.status == "completed"


def test_upload_failure_marks_row_retryable(make, client, session):
    headers = _ops(make, client)
    drama = make.drama(title="Retry Drama")
    episode = make.episode(drama, number=1)

    session_data = _upload_session(client, headers, episode.id)
    video_id = session_data.json()["video_id"]

    failed = client.post(
        f"/admin/v1/episodes/{episode.id}/upload-failed",
        json={"video_id": video_id, "reason": "network reset"},
        headers=headers,
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["updated"] is True
    session.expire_all()
    assert session.get(Episode, episode.id).status == "transcode_failed"
    assert session.scalar(select(MediaUpload).where(MediaUpload.video_id == video_id)).status == "failed"

    # transcode_failed accepts a fresh upload session (the retry path).
    retry = _upload_session(client, headers, episode.id, filename="retry.mp4")
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "transcoding"


def test_fake_tus_receiver_completes_the_upload(make, client, session):
    headers = _ops(make, client)
    drama = make.drama(title="Fake Tus Drama")
    episode = make.episode(drama, number=1)

    session_data = _upload_session(client, headers, episode.id)
    assert session_data.status_code == 200, session_data.text
    body = session_data.json()
    video_id = body["video_id"]
    assert body["upload_url"] == f"/admin/v1/dev-tus/{video_id}"

    created = client.post(
        body["upload_url"],
        headers={**headers, "Tus-Resumable": "1.0.0", "Upload-Length": "5"},
    )
    assert created.status_code == 201, created.text
    location = created.headers["location"]
    assert location == body["upload_url"]

    written = client.patch(
        location,
        content=b"12345",
        headers={
            **headers,
            "Tus-Resumable": "1.0.0",
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
        },
    )
    assert written.status_code == 204, written.text
    assert written.headers["upload-offset"] == "5"

    session.expire_all()
    assert session.get(Episode, episode.id).status == "in_review"
    upload = session.scalar(select(MediaUpload).where(MediaUpload.video_id == video_id))
    assert upload.status == "completed"
    assert upload.size_bytes == 5

    # Replaying the PATCH is idempotent.
    again = client.patch(
        location,
        content=b"12345",
        headers={**headers, "Tus-Resumable": "1.0.0", "Upload-Offset": "0"},
    )
    assert again.status_code == 204


def test_dev_tus_receiver_requires_content_writer(make, client):
    drama = make.drama(title="Guarded Drama")
    episode = make.episode(drama, number=1)
    writer = make.admin(role="ops")
    headers = _login(client, writer.username)
    session_data = _upload_session(client, headers, episode.id).json()

    assert client.post(session_data["upload_url"]).status_code == 401
    finance = make.admin(role="finance")
    finance_headers = _login(client, finance.username)
    assert client.post(session_data["upload_url"], headers=finance_headers).status_code == 403


def test_manual_upload_finished_callback_records_bunny_duration(
    make, client, session, monkeypatch
):
    """A finished real-mode Bunny callback stores the transcoded length."""
    from app.modules.common import bunny as bunny_module
    from app.modules.content import upload_service

    headers = _ops(make, client)
    drama = make.drama(title="Callback Duration Drama")
    episode = make.episode(drama, number=1)

    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setattr(bunny_module, "create_stream_video", lambda title: "guid-callback")
    monkeypatch.setattr(bunny_module, "tus_headers", lambda video_id, *, ttl=3600: {"Tus-Resumable": "1.0.0"})
    monkeypatch.setattr(
        bunny_module, "stream_video", lambda video_id: {"status": 4, "length": 128}
    )

    session_data = _upload_session(client, headers, episode.id)
    assert session_data.status_code == 200, session_data.text
    video_id = session_data.json()["video_id"]
    assert video_id == "guid-callback"

    body = json.dumps({"video_id": video_id, "status": "finished"}).encode()
    response = client.post(
        "/v1/bunny/callback",
        content=body,
        headers={
            "X-Bunny-Signature": upload_service.sign_callback(body),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200, response.text

    session.expire_all()
    refreshed = session.get(Episode, episode.id)
    assert refreshed.status == "in_review"
    assert refreshed.duration_seconds == 128

def test_sync_status_records_bunny_duration(make, client, session, monkeypatch):
    """Dev has no transcode callback, so sync-status also fills the duration."""
    from app.modules.common import bunny as bunny_module

    headers = _ops(make, client)
    drama = make.drama(title="Sync Duration Drama")
    episode = make.episode(drama, number=1)

    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setattr(bunny_module, "create_stream_video", lambda title: "guid-sync")
    monkeypatch.setattr(bunny_module, "tus_headers", lambda video_id, *, ttl=3600: {"Tus-Resumable": "1.0.0"})
    monkeypatch.setattr(bunny_module, "stream_video", lambda video_id: {"status": 4, "length": 97})

    session_data = _upload_session(client, headers, episode.id)
    assert session_data.status_code == 200, session_data.text

    synced = client.post(f"/admin/v1/episodes/{episode.id}/sync-status", headers=headers)
    assert synced.status_code == 200, synced.text
    assert synced.json()["status"] == "in_review"
    assert synced.json()["duration_seconds"] == 97

    session.expire_all()
    refreshed = session.get(Episode, episode.id)
    assert refreshed.status == "in_review"
    assert refreshed.duration_seconds == 97


def test_sync_status_leaves_queued_videos_transcoding(make, client, session, monkeypatch):
    """Bunny status 2 is 'uploaded, not encoding yet', not finished.

    Treating it as finished is what left a bulk import showing 转码中 forever
    once the library listing was never polled — and treating a still-queued
    upload as in_review would publish a video that cannot play.
    """
    from app.modules.common import bunny as bunny_module

    headers = _ops(make, client)
    drama = make.drama(title="Still Queued Drama")
    episode = make.episode(drama, number=1)

    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setattr(bunny_module, "create_stream_video", lambda title: "guid-queued")
    monkeypatch.setattr(bunny_module, "tus_headers", lambda video_id, *, ttl=3600: {"Tus-Resumable": "1.0.0"})
    monkeypatch.setattr(
        bunny_module,
        "stream_video",
        lambda video_id: {"status": 2, "encodeProgress": 0, "length": 0, "availableResolutions": None},
    )

    assert _upload_session(client, headers, episode.id).status_code == 200
    synced = client.post(f"/admin/v1/episodes/{episode.id}/sync-status", headers=headers)
    assert synced.status_code == 200, synced.text
    assert synced.json()["status"] == "transcoding"
    assert synced.json()["remote_status"] == 2

    session.expire_all()
    assert session.get(Episode, episode.id).status == "transcoding"


def test_sync_status_fails_an_abandoned_upload(make, client, session, monkeypatch):
    """A status-0 object that never got bytes is a dead end, not a queue.

    Bunny sometimes keeps the video object but never stores the upload (no
    storage, no length, no hash); reencode then refuses with "Original file
    is missing". Left alone the row stays 转码中 forever, and neither a new
    upload nor a delete is offered. Past the queue window it must drop to
    transcode_failed so 重新上传 appears.
    """
    from datetime import datetime, timedelta, timezone

    from app.modules.common import bunny as bunny_module

    headers = _ops(make, client)
    drama = make.drama(title="Abandoned Upload Drama")
    episode = make.episode(drama, number=1)

    monkeypatch.setenv("BUNNY_MODE", "real")
    guids = iter(["guid-abandoned", "guid-abandoned-retry"])
    monkeypatch.setattr(bunny_module, "create_stream_video", lambda title: next(guids))
    monkeypatch.setattr(bunny_module, "tus_headers", lambda video_id, *, ttl=3600: {"Tus-Resumable": "1.0.0"})
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    monkeypatch.setattr(
        bunny_module,
        "stream_video",
        lambda video_id: {
            "status": 0,
            "encodeProgress": 0,
            "length": 0,
            "storageSize": 0,
            "hasOriginal": True,
            "availableResolutions": None,
            "dateUploaded": old,
        },
    )

    assert _upload_session(client, headers, episode.id).status_code == 200
    synced = client.post(f"/admin/v1/episodes/{episode.id}/sync-status", headers=headers)
    assert synced.status_code == 200, synced.text
    assert synced.json()["status"] == "transcode_failed"

    session.expire_all()
    assert session.get(Episode, episode.id).status == "transcode_failed"
    upload = session.scalar(select(MediaUpload).where(MediaUpload.episode_id == episode.id))
    assert upload is not None and upload.status == "failed"

    # The retry path re-opens: transcode_failed accepts a fresh session.
    retry = _upload_session(client, headers, episode.id, filename="retry.mp4")
    assert retry.status_code == 200, retry.text


def test_sync_status_keeps_a_fresh_status0_upload_transcoding(make, client, session, monkeypatch):
    """The same all-zero signature minutes after upload is just a queue wait."""
    from datetime import datetime, timedelta, timezone

    from app.modules.common import bunny as bunny_module

    headers = _ops(make, client)
    drama = make.drama(title="Fresh Upload Drama")
    episode = make.episode(drama, number=1)

    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setattr(bunny_module, "create_stream_video", lambda title: "guid-fresh")
    monkeypatch.setattr(bunny_module, "tus_headers", lambda video_id, *, ttl=3600: {"Tus-Resumable": "1.0.0"})
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(
        bunny_module,
        "stream_video",
        lambda video_id: {
            "status": 0,
            "encodeProgress": 0,
            "length": 0,
            "storageSize": 0,
            "hasOriginal": True,
            "availableResolutions": None,
            "dateUploaded": fresh,
        },
    )

    assert _upload_session(client, headers, episode.id).status_code == 200
    synced = client.post(f"/admin/v1/episodes/{episode.id}/sync-status", headers=headers)
    assert synced.status_code == 200, synced.text
    assert synced.json()["status"] == "transcoding"

    session.expire_all()
    assert session.get(Episode, episode.id).status == "transcoding"


def test_sync_status_fails_a_status2_stall(make, client, session, monkeypatch):
    """status 2 with zero progress past the window = the encoder never came.

    Bunny sometimes accepts the original (status 2, bytes stored) and then
    never starts encoding; reencode is a no-op on such objects. The row must
    drop to transcode_failed or it stays 转码中 forever.
    """
    from datetime import datetime, timedelta, timezone

    from app.modules.common import bunny as bunny_module

    headers = _ops(make, client)
    drama = make.drama(title="Stalled Encoder Drama")
    episode = make.episode(drama, number=1)

    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setattr(bunny_module, "create_stream_video", lambda title: "guid-stalled")
    monkeypatch.setattr(bunny_module, "tus_headers", lambda video_id, *, ttl=3600: {"Tus-Resumable": "1.0.0"})
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    monkeypatch.setattr(
        bunny_module,
        "stream_video",
        lambda video_id: {
            "status": 2,
            "encodeProgress": 0,
            "length": 0,
            "storageSize": 2031524,
            "hasOriginal": True,
            "availableResolutions": None,
            "dateUploaded": old,
        },
    )

    assert _upload_session(client, headers, episode.id).status_code == 200
    synced = client.post(f"/admin/v1/episodes/{episode.id}/sync-status", headers=headers)
    assert synced.status_code == 200, synced.text
    assert synced.json()["status"] == "transcode_failed"

    session.expire_all()
    assert session.get(Episode, episode.id).status == "transcode_failed"


def test_sync_status_keeps_a_fresh_status2_upload_transcoding(make, client, session, monkeypatch):
    """status 2 minutes after upload is a normal queue wait, not a stall."""
    from datetime import datetime, timedelta, timezone

    from app.modules.common import bunny as bunny_module

    headers = _ops(make, client)
    drama = make.drama(title="Fresh Encoder Drama")
    episode = make.episode(drama, number=1)

    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setattr(bunny_module, "create_stream_video", lambda title: "guid-queued2")
    monkeypatch.setattr(bunny_module, "tus_headers", lambda video_id, *, ttl=3600: {"Tus-Resumable": "1.0.0"})
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(
        bunny_module,
        "stream_video",
        lambda video_id: {
            "status": 2,
            "encodeProgress": 0,
            "length": 0,
            "storageSize": 0,
            "hasOriginal": True,
            "availableResolutions": None,
            "dateUploaded": fresh,
        },
    )

    assert _upload_session(client, headers, episode.id).status_code == 200
    synced = client.post(f"/admin/v1/episodes/{episode.id}/sync-status", headers=headers)
    assert synced.status_code == 200, synced.text
    assert synced.json()["status"] == "transcoding"

    session.expire_all()
    assert session.get(Episode, episode.id).status == "transcoding"


def test_upload_failed_keeps_a_landed_video(make, client, session, monkeypatch):
    """A client error after the bytes arrive must not stamp 转码失败.

    The editor used to call upload-failed for any exception after the session
    existed, including one raised while the file was already at Bunny and only
    waiting to encode (remote status 0, hasOriginal true).
    """
    from app.modules.common import bunny as bunny_module

    headers = _ops(make, client)
    drama = make.drama(title="Landed Bytes Drama")
    episode = make.episode(drama, number=1)

    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setattr(bunny_module, "create_stream_video", lambda title: "guid-landed")
    monkeypatch.setattr(bunny_module, "tus_headers", lambda video_id, *, ttl=3600: {"Tus-Resumable": "1.0.0"})
    monkeypatch.setattr(
        bunny_module,
        "stream_video",
        lambda video_id: {
            "status": 0,
            "encodeProgress": 0,
            "length": 0,
            "storageSize": 0,
            "hasOriginal": True,
            "availableResolutions": None,
        },
    )

    created = _upload_session(client, headers, episode.id)
    assert created.status_code == 200, created.text
    video_id = created.json()["video_id"]

    failed = client.post(
        f"/admin/v1/episodes/{episode.id}/upload-failed",
        json={"video_id": video_id, "reason": "dialog closed while encoding"},
        headers=headers,
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "transcoding"
    assert failed.json()["updated"] is False
    assert failed.json()["ignored"] == "bytes already at bunny"

    session.expire_all()
    assert session.get(Episode, episode.id).status == "transcoding"
    upload = session.scalar(select(MediaUpload).where(MediaUpload.video_id == video_id))
    assert upload is not None and upload.status == "created"
