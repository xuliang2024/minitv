"""Partner preview: a supplier may play its own episodes, and only its own."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.content.models import Episode


@pytest.fixture()
def client(session):
    return TestClient(app)


def _staff_headers(client, make) -> dict:
    admin = make.admin(role="admin")
    r = client.post(
        "/admin/v1/auth/login", json={"username": admin.username, "password": "pass-1234"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _partner_headers(client, headers, *, name: str, username: str) -> dict:
    sup = client.post(
        "/admin/v1/suppliers",
        json={"name": name, "contact_email": "s@example.com"},
        headers=headers,
    )
    assert sup.status_code == 201, sup.text
    acc = client.post(
        "/admin/v1/staff",
        json={
            "username": username,
            "display_name": name,
            "role": "partner",
            "password": "partner-pass-1234",
            "supplier_id": sup.json()["id"],
        },
        headers=headers,
    )
    assert acc.status_code == 201, acc.text
    login = client.post(
        "/admin/v1/auth/login", json={"username": username, "password": "partner-pass-1234"}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_partner_previews_only_its_own_episode(make, session, client, monkeypatch):
    headers = _staff_headers(client, make)
    partner_a = _partner_headers(client, headers, name="甲", username="sp-a")
    partner_b = _partner_headers(client, headers, name="乙", username="sp-b")

    drama = client.post(
        "/partner/v1/dramas", json={"title": "预览剧"}, headers=partner_a
    ).json()
    episode = client.post(
        f"/partner/v1/dramas/{drama['id']}/episodes", json={}, headers=partner_a
    ).json()

    pending = client.get(f"/partner/v1/episodes/{episode['id']}/preview-url", headers=partner_a)
    assert pending.status_code == 409
    assert pending.json()["detail"]["code"] == "no_video"

    # Ownership first: another tenant must not be able to confirm the row exists.
    foreign = client.get(f"/partner/v1/episodes/{episode['id']}/preview-url", headers=partner_b)
    assert foreign.status_code == 404

    row = session.get(Episode, episode["id"])
    row.bunny_video_id = "own-guid"
    session.commit()
    monkeypatch.setenv("BUNNY_STREAM_LIBRARY_ID", "778985")
    monkeypatch.setenv("BUNNY_STREAM_TOKEN_KEY", "preview-key")

    signed = client.get(f"/partner/v1/episodes/{episode['id']}/preview-url", headers=partner_a)
    assert signed.status_code == 200, signed.text
    body = signed.json()
    assert body["video_id"] == "own-guid"
    assert body["episode_number"] == episode["episode_number"]
    assert body["embed_url"].startswith(
        "https://iframe.mediadelivery.net/embed/778985/own-guid?token="
    )

    # The detail payload drives the row-level button state in the UI.
    detail = client.get(f"/partner/v1/dramas/{drama['id']}", headers=partner_a).json()
    assert detail["episodes"][0]["has_video"] is True
