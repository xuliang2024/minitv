"""Website trial (preview) surface: /v1/preview and /v1/preview/dramas/{id}.

The preview surface is the only place where the public website may play
videos, and it must stay published-only: draft/in-review content, paywalled
dramas and locked episodes never leak playback material for guests.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.content.upload_service import sign_callback


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


def _make_tag(client, headers, name):
    created = client.post(
        "/admin/v1/tags",
        json={"name_en": name, "name_zh": name, "category": "sub"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return created.json()


def _publish_drama(make, client, headers, *, title, free, tag_ids=None):
    drama = make.drama(title=title, status="draft", free=free)
    if tag_ids:
        updated = client.patch(
            f"/admin/v1/dramas/{drama.id}", json={"tag_ids": tag_ids}, headers=headers
        )
        assert updated.status_code == 200, updated.text
    for to_status in ("in_review", "published"):
        response = client.post(
            f"/admin/v1/dramas/{drama.id}/status", json={"to_status": to_status}, headers=headers
        )
        assert response.status_code == 200, response.text
    return drama


def _upload_episode(make, client, headers, drama, number: int, *, publish: bool):
    episode = make.episode(drama, number=number, status="not_uploaded")
    created = client.post(
        f"/admin/v1/episodes/{episode.id}/upload-session",
        json={"filename": f"ep{number}.mp4", "mime": "video/mp4", "size_bytes": 1000, "width": 720, "height": 1280},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    body = json.dumps({"video_id": created.json()["video_id"], "status": "finished"}).encode()
    callback = client.post(
        "/v1/bunny/callback",
        content=body,
        headers={"X-Bunny-Signature": sign_callback(body), "Content-Type": "application/json"},
    )
    assert callback.status_code == 200, callback.text  # episode is now in_review
    if publish:
        response = client.post(f"/admin/v1/episodes/{episode.id}/publish", headers=headers)
        assert response.status_code == 200, response.text
    return episode


def test_preview_catalog_exposes_only_free_playable_published_dramas(make, client):
    headers = _ops(make, client)
    tag = _make_tag(client, headers, "Sweet Love")

    visible = _publish_drama(make, client, headers, title="Preview Me", free=1, tag_ids=[tag["id"]])
    _upload_episode(make, client, headers, visible, 1, publish=True)   # free
    _upload_episode(make, client, headers, visible, 2, publish=True)   # locked
    _upload_episode(make, client, headers, visible, 3, publish=False)  # still in review

    # A draft drama, a published drama without published episodes, and a
    # paywalled drama (no free window) must all stay invisible.
    make.drama(title="Draft Drama", status="draft", free=1)
    quiet = _publish_drama(make, client, headers, title="No Episodes Out", free=1)
    _upload_episode(make, client, headers, quiet, 1, publish=False)
    paywalled = _publish_drama(make, client, headers, title="Paid Only", free=0)
    _upload_episode(make, client, headers, paywalled, 1, publish=True)

    catalog = client.get("/v1/preview")
    assert catalog.status_code == 200, catalog.text
    payload = catalog.json()
    assert payload["limit"] == 7

    all_row = payload["categories"][0]
    assert all_row["id"] == 0 and all_row["name"] == "All shows"
    titles = [drama["title"] for drama in all_row["dramas"]]
    assert "Preview Me" in titles
    assert "Draft Drama" not in titles
    assert "No Episodes Out" not in titles
    assert "Paid Only" not in titles

    card = next(drama for drama in all_row["dramas"] if drama["title"] == "Preview Me")
    assert card["episode_count"] == 2          # in_review episode is not public
    assert card["free_playable_count"] == 1
    assert card["free_episode_count"] == 1
    assert card["tags"] == ["Sweet Love"]

    tag_row = next(row for row in payload["categories"] if row["name"] == "Sweet Love")
    assert tag_row["total"] == 1
    assert [drama["title"] for drama in tag_row["dramas"]] == ["Preview Me"]

    text = catalog.text.lower()
    for marker in ("m3u8", "bcdn_token", "playlist"):
        assert marker not in text


def test_preview_catalog_caps_every_category_at_seven(make, client):
    headers = _ops(make, client)
    tag = _make_tag(client, headers, "Binge Worthy")
    for index in range(8):
        drama = _publish_drama(
            make, client, headers, title=f"Binge Drama {index}", free=1, tag_ids=[tag["id"]]
        )
        _upload_episode(make, client, headers, drama, 1, publish=True)

    payload = client.get("/v1/preview").json()
    row = next(row for row in payload["categories"] if row["name"] == "Binge Worthy")
    assert row["total"] == 8
    assert len(row["dramas"]) == 7  # "每个分类的前 7 个"

    all_row = payload["categories"][0]
    assert len(all_row["dramas"]) == 7


def test_preview_detail_lists_free_and_locked_episodes_without_playback_urls(make, client):
    headers = _ops(make, client)
    drama = _publish_drama(make, client, headers, title="Detail Drama", free=1)
    free = _upload_episode(make, client, headers, drama, 1, publish=True)
    locked = _upload_episode(make, client, headers, drama, 2, publish=True)
    _upload_episode(make, client, headers, drama, 3, publish=False)

    response = client.get(f"/v1/preview/dramas/{drama.id}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["drama"]["title"] == "Detail Drama"
    assert payload["drama"]["episode_count"] == 2
    assert payload["drama"]["free_playable_count"] == 1
    assert payload["total_episode_count"] == 3  # app-only episode counted, not listed

    items = payload["items"]
    assert [(item["episode_number"], item["free"]) for item in items] == [(1, True), (2, False)]
    assert items[0]["id"] == free.id
    assert items[1]["id"] == locked.id

    text = response.text.lower()
    for marker in ("m3u8", "bcdn_token", "playlist", "token="):
        assert marker not in text


def test_preview_detail_hides_unpublished_and_paywalled_dramas(make, client):
    headers = _ops(make, client)

    draft = make.drama(title="Draft Detail", status="draft", free=1)
    assert client.get(f"/v1/preview/dramas/{draft.id}").status_code == 404

    paywalled = _publish_drama(make, client, headers, title="Paywalled Detail", free=0)
    _upload_episode(make, client, headers, paywalled, 1, publish=True)
    assert client.get(f"/v1/preview/dramas/{paywalled.id}").status_code == 404

    no_episodes = _publish_drama(make, client, headers, title="Empty Detail", free=1)
    _upload_episode(make, client, headers, no_episodes, 1, publish=False)
    assert client.get(f"/v1/preview/dramas/{no_episodes.id}").status_code == 404


def test_guest_playback_of_preview_locked_episode_returns_no_url(make, client):
    """The preview player's lock is enforced by the API, not just the UI."""
    headers = _ops(make, client)
    drama = _publish_drama(make, client, headers, title="Guard Drama", free=1)
    free = _upload_episode(make, client, headers, drama, 1, publish=True)
    locked = _upload_episode(make, client, headers, drama, 2, publish=True)

    allowed = client.post("/v1/playback", json={"drama_id": drama.id, "episode_id": free.id})
    assert allowed.status_code == 200
    assert allowed.json()["allowed"] is True and "token=" in allowed.json()["url"]

    denied = client.post("/v1/playback", json={"drama_id": drama.id, "episode_id": locked.id})
    assert denied.status_code == 200
    assert denied.json()["allowed"] is False
    text = denied.text.lower()
    for marker in ("m3u8", "bcdn_token", "token="):
        assert marker not in text
