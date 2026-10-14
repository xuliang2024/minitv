"""Episode editing: metadata PATCH (title / number) and video replacement."""

from __future__ import annotations

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
        "filename": "replace.mp4",
        "mime": "video/mp4",
        "size_bytes": 2048,
        "width": 720,
        "height": 1280,
        "duration_seconds": 42,
        **overrides,
    }
    return client.post(f"/admin/v1/episodes/{episode_id}/upload-session", json=payload, headers=headers)


def test_patch_edits_title_and_episode_number(make, client, session):
    headers = _ops(make, client)
    drama = make.drama(title="Edit Drama")
    episode = make.episode(drama, number=1)

    updated = client.patch(
        f"/admin/v1/episodes/{episode.id}",
        json={"title": "  Episode One  ", "episode_number": 5},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["title"] == "Episode One"
    assert body["episode_number"] == 5
    assert body["status"] == "not_uploaded"

    session.expire_all()
    row = session.get(Episode, episode.id)
    assert (row.title, row.episode_number) == ("Episode One", 5)


def test_patch_rejects_duplicate_number_empty_title_and_unknown_id(make, client, session):
    headers = _ops(make, client)
    drama = make.drama(title="Conflict Drama")
    first = make.episode(drama, number=1)
    make.episode(drama, number=2)

    duplicate = client.patch(
        f"/admin/v1/episodes/{first.id}", json={"episode_number": 2}, headers=headers
    )
    assert duplicate.status_code == 409
    assert "已存在" in duplicate.json()["detail"]

    empty = client.patch(f"/admin/v1/episodes/{first.id}", json={"title": "   "}, headers=headers)
    assert empty.status_code == 422

    nothing = client.patch(f"/admin/v1/episodes/{first.id}", json={}, headers=headers)
    assert nothing.status_code == 422

    assert client.patch("/admin/v1/episodes/999999", json={"title": "x"}, headers=headers).status_code == 404

    session.expire_all()
    row = session.get(Episode, first.id)
    # Rejected edits must not leave partial writes behind.
    assert (row.title, row.episode_number) == ("", 1)


def test_replace_video_from_published_resets_to_transcoding(make, client, session):
    headers = _ops(make, client)
    drama = make.drama(title="Replace Published", status="published")
    episode = make.episode(drama, number=1, status="published")
    episode.bunny_video_id = "old-guid"
    episode.duration_seconds = 90
    session.commit()

    response = _upload_session(client, headers, episode.id)
    assert response.status_code == 200, response.text
    new_video_id = response.json()["video_id"]
    assert new_video_id != "old-guid"

    session.expire_all()
    row = session.get(Episode, episode.id)
    assert row.status == "transcoding"
    assert row.bunny_video_id == new_video_id
    # The probed length replaces the stale one until Bunny reports the real value.
    assert row.duration_seconds == 42

    uploads = session.scalars(
        select(MediaUpload).where(MediaUpload.episode_id == episode.id).order_by(MediaUpload.id)
    ).all()
    assert len(uploads) == 1 and uploads[0].video_id == new_video_id


def test_replace_video_from_unpublished_is_allowed(make, client, session):
    headers = _ops(make, client)
    drama = make.drama(title="Replace Unpublished", status="published")
    episode = make.episode(drama, number=1, status="unpublished")
    episode.bunny_video_id = "taken-down-guid"
    episode.duration_seconds = 30
    session.commit()

    response = _upload_session(client, headers, episode.id, duration_seconds=0)
    assert response.status_code == 200, response.text

    session.expire_all()
    row = session.get(Episode, episode.id)
    assert row.status == "transcoding"
    assert row.bunny_video_id == response.json()["video_id"]
    # No probed length and a fresh video: the stale duration must be dropped.
    assert row.duration_seconds == 0


def test_replace_video_rejected_while_transcoding(make, client):
    headers = _ops(make, client)
    drama = make.drama(title="Busy Drama")
    episode = make.episode(drama, number=1, status="transcoding")
    episode.bunny_video_id = "in-flight-guid"

    response = _upload_session(client, headers, episode.id)
    assert response.status_code == 422
    assert "in progress" in response.json()["detail"]
