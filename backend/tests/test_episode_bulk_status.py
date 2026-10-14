"""Bulk publish / takedown for episodes (admin 分集批量上架、下架)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.content.models import Episode


@pytest.fixture()
def client(session):
    return TestClient(app)


def _login(client, username, password="pass-1234"):
    response = client.post("/admin/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _episode_with_video(session, make, drama, number, status, video_id):
    episode = make.episode(drama, number=number, status=status)
    episode.bunny_video_id = video_id
    session.commit()
    return episode


def test_bulk_publish_then_unpublish_reports_skips(session, make, client):
    staff = make.admin(role="ops")
    headers = _login(client, staff.username)
    drama = make.drama(title="Bulk Drama", status="published")
    ready = _episode_with_video(session, make, drama, 1, "in_review", "video-1")
    offline = _episode_with_video(session, make, drama, 2, "unpublished", "video-2")
    no_video = make.episode(drama, number=3, status="in_review")
    transcoding = _episode_with_video(session, make, drama, 4, "transcoding", "video-4")

    response = client.post(
        "/admin/v1/episodes/bulk-status",
        json={
            "episode_ids": [ready.id, offline.id, no_video.id, transcoding.id, 999_999],
            "action": "publish",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert {row["id"] for row in body["updated"]} == {ready.id, offline.id}
    reasons = {(row["id"], row["reason"]) for row in body["skipped"]}
    assert (no_video.id, "no_video") in reasons
    assert (transcoding.id, "invalid_transition") in reasons
    assert (999_999, "not_found") in reasons

    session.expire_all()
    assert session.get(Episode, ready.id).status == "published"
    assert session.get(Episode, offline.id).status == "published"

    taken_down = client.post(
        "/admin/v1/episodes/bulk-status",
        json={"episode_ids": [ready.id, offline.id], "action": "unpublish"},
        headers=headers,
    )
    assert taken_down.status_code == 200, taken_down.text
    assert len(taken_down.json()["updated"]) == 2
    assert taken_down.json()["skipped"] == []
    session.expire_all()
    assert session.get(Episode, ready.id).status == "unpublished"
    assert session.get(Episode, offline.id).status == "unpublished"


def test_bulk_status_requires_content_writer(session, make, client):
    staff = make.admin(role="finance")
    headers = _login(client, staff.username)
    drama = make.drama(title="Bulk Permission Drama")
    episode = _episode_with_video(session, make, drama, 1, "in_review", "video")

    response = client.post(
        "/admin/v1/episodes/bulk-status",
        json={"episode_ids": [episode.id], "action": "publish"},
        headers=headers,
    )
    assert response.status_code == 403


def test_unpublish_endpoint_round_trips_an_episode(session, make, client):
    staff = make.admin(role="ops")
    headers = _login(client, staff.username)
    drama = make.drama(title="Unpublish Drama", status="published")
    episode = _episode_with_video(session, make, drama, 1, "in_review", "video")

    published = client.post(f"/admin/v1/episodes/{episode.id}/publish", headers=headers)
    assert published.status_code == 200, published.text

    response = client.post(f"/admin/v1/episodes/{episode.id}/unpublish", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "unpublished"
    session.expire_all()
    assert session.get(Episode, episode.id).status == "unpublished"

    again = client.post(f"/admin/v1/episodes/{episode.id}/unpublish", headers=headers)
    assert again.status_code == 409

    republished = client.post(f"/admin/v1/episodes/{episode.id}/publish", headers=headers)
    assert republished.status_code == 200
    assert republished.json()["status"] == "published"
