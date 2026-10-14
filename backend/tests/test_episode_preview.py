from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.common import bunny


@pytest.fixture()
def client(session):
    return TestClient(app)


def test_admin_episode_preview_url_requires_video_and_signs_player_url(
    client, session, make, monkeypatch
):
    staff = make.admin(role="admin")
    drama = make.drama(title="Preview drama", status="published")
    episode = make.episode(drama, number=1, status="not_uploaded")

    login = client.post(
        "/admin/v1/auth/login",
        json={"username": staff.username, "password": "pass-1234"},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    missing = client.get(f"/admin/v1/episodes/{episode.id}/preview-url", headers=headers)
    assert missing.status_code == 409

    episode.bunny_video_id = "preview-guid"
    session.commit()
    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setenv("BUNNY_STREAM_LIBRARY_ID", "778985")
    monkeypatch.setenv("BUNNY_STREAM_TOKEN_KEY", "preview-key")
    monkeypatch.setattr(bunny.time, "time", lambda: 1_700_000_000)

    response = client.get(f"/admin/v1/episodes/{episode.id}/preview-url", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["video_id"] == "preview-guid"
    assert body["url"].startswith(
        "https://player.mediadelivery.net/play/778985/preview-guid?token="
    )
    assert "&expires=1700003600" in body["url"]
    assert body["embed_url"].startswith(
        "https://iframe.mediadelivery.net/embed/778985/preview-guid?token="
    )
    assert "&expires=1700003600&autoplay=true" in body["embed_url"]
