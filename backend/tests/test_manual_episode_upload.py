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
