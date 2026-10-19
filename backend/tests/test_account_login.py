"""Account sign-in: one email-code door, plus what the client may offer.

The H5 has no login screen of its own knowledge: it asks ``/v1/auth/providers``
what this deployment supports, and it never has to guess whether an email is
already registered. These tests pin both behaviours down.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.account import service as accounts


@pytest.fixture()
def client(session):
    return TestClient(app)


@pytest.fixture()
def outbox(monkeypatch):
    """Capture the plaintext code instead of calling Resend."""
    sent: list[dict] = []

    def fake_send(*, to: str, code: str, purpose: str) -> str:
        sent.append({"to": to, "code": code, "purpose": purpose})
        return "test-message-id"

    monkeypatch.setattr(accounts, "send_verification_code", fake_send)
    return sent


def _send_code(client, email: str, purpose: str | None = None) -> dict:
    body = {"email": email}
    if purpose is not None:
        body["purpose"] = purpose
    response = client.post("/v1/auth/email/code", json=body)
    assert response.status_code == 200, response.text
    assert response.json() == {"sent": True}
    return body


def _authenticate(client, email: str, code: str):
    return client.post("/v1/auth/email/authenticate", json={"email": email, "code": code})


def test_providers_reports_only_usable_methods(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    data = client.get("/v1/auth/providers").json()
    assert data["email"]["enabled"] is True
    assert data["google"] == {"enabled": False, "client_id": None}


def test_providers_serves_google_client_id(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc.apps.googleusercontent.com")
    data = client.get("/v1/auth/providers").json()
    assert data["google"] == {
        "enabled": True,
        "client_id": "abc.apps.googleusercontent.com",
    }


def test_code_defaults_to_login_purpose(client, outbox, session):
    _send_code(client, "New@Example.com")
    assert len(outbox) == 1
    assert outbox[0]["to"] == "new@example.com"
    assert outbox[0]["purpose"] == "login"

    from app.modules.account.models import VerificationCode
    from sqlalchemy import select

    rows = session.scalars(select(VerificationCode)).all()
    assert [row.purpose for row in rows] == ["login"]


def test_invalid_purpose_is_rejected(client, outbox):
    response = client.post("/v1/auth/email/code", json={"email": "a@b.com", "purpose": "reset"})
    assert response.status_code == 422
    assert outbox == []


def test_authenticate_creates_then_reuses_the_same_account(client, outbox, session):
    _send_code(client, "viewer@example.com")
    first = _authenticate(client, "viewer@example.com", outbox[0]["code"])
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["created"] is True
    assert body["token"]
    assert body["public_id"]

    me = client.get("/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "viewer@example.com"

    _send_code(client, "viewer@example.com")
    second = _authenticate(client, "viewer@example.com", outbox[1]["code"])
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["user_id"] == body["user_id"]

    from sqlalchemy import func, select
    from app.modules.account.models import User

    assert session.scalar(select(func.count()).select_from(User)) == 1


def test_code_is_single_use(client, outbox):
    _send_code(client, "once@example.com")
    code = outbox[0]["code"]
    assert _authenticate(client, "once@example.com", code).status_code == 200
    assert _authenticate(client, "once@example.com", code).status_code in (400, 404)


def test_wrong_code_does_not_create_an_account(client, outbox, session):
    _send_code(client, "typo@example.com")
    wrong = "000000" if outbox[0]["code"] != "000000" else "111111"
    response = _authenticate(client, "typo@example.com", wrong)
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid code"

    from sqlalchemy import func, select
    from app.modules.account.models import User

    assert session.scalar(select(func.count()).select_from(User)) == 0


def test_legacy_login_endpoint_still_refuses_unknown_email(client, outbox):
    _send_code(client, "ghost@example.com")
    response = client.post(
        "/v1/auth/email/login",
        json={"email": "ghost@example.com", "code": outbox[0]["code"]},
    )
    assert response.status_code == 404


def test_favorites_list_needs_a_session_and_skips_unpublished(client, outbox, make):
    assert client.get("/v1/me/favorites").status_code == 401

    published_a = make.drama(title="Saved A", status="published")
    published_b = make.drama(title="Saved B", status="published")
    draft = make.drama(title="Hidden", status="draft")
    make.episode(published_a, number=1, status="published")

    _send_code(client, "saver@example.com")
    token = _authenticate(client, "saver@example.com", outbox[0]["code"]).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    for drama in (published_a, draft, published_b):
        response = client.post(
            "/v1/reactions",
            json={"target_type": "drama", "target_id": drama.id, "kind": "favorite", "remove": False},
            headers=headers,
        )
        assert response.status_code == 200

    page = client.get("/v1/me/favorites", headers=headers)
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [published_b.id, published_a.id]
    assert [item["episode_count"] for item in body["items"]] == [0, 1]

    limited = client.get("/v1/me/favorites?limit=1", headers=headers).json()
    assert limited["total"] == 2
    assert [item["id"] for item in limited["items"]] == [published_b.id]


def test_feed_reports_like_count_and_viewer_state(client, outbox, make, session):
    drama = make.drama(title="Feed Like", status="published", free=3)
    episode = make.episode(drama, number=1, status="published")
    episode.bunny_video_id = "feed-vid-1"
    episode.duration_seconds = 90
    session.commit()

    guest_item = client.get("/v1/feed?limit=5").json()["items"]
    target = next(item for item in guest_item if item["drama"]["id"] == drama.id)
    assert target["drama"]["like_count"] == 0
    assert target["drama"]["liked"] is False

    _send_code(client, "feedfan@example.com")
    token = _authenticate(client, "feedfan@example.com", outbox[0]["code"]).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post(
        "/v1/reactions",
        json={"target_type": "drama", "target_id": drama.id, "kind": "like", "remove": False},
        headers=headers,
    )

    signed_item = client.get("/v1/feed?limit=5", headers=headers).json()["items"]
    mine = next(item for item in signed_item if item["drama"]["id"] == drama.id)
    assert mine["drama"]["like_count"] == 1
    assert mine["drama"]["liked"] is True

    guest_again = client.get("/v1/feed?limit=5").json()["items"]
    seen = next(item for item in guest_again if item["drama"]["id"] == drama.id)
    assert seen["drama"]["like_count"] == 1
    assert seen["drama"]["liked"] is False


def test_reactions_need_a_session_and_feed_the_play_payload(client, outbox, make, session):
    drama = make.drama(title="Social", status="published")
    make.episode(drama, number=1, status="published")

    assert client.post(
        "/v1/reactions",
        json={"target_type": "drama", "target_id": drama.id, "kind": "like", "remove": False},
    ).status_code == 401

    _send_code(client, "fan@example.com")
    token = _authenticate(client, "fan@example.com", outbox[0]["code"]).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    liked = client.post(
        "/v1/reactions",
        json={"target_type": "drama", "target_id": drama.id, "kind": "like", "remove": False},
        headers=headers,
    )
    assert liked.status_code == 200
    assert liked.json() == {"active": True}

    favorited = client.post(
        "/v1/reactions",
        json={"target_type": "drama", "target_id": drama.id, "kind": "favorite", "remove": False},
        headers=headers,
    )
    assert favorited.json() == {"active": True}

    play = client.get(f"/v1/dramas/{drama.id}/play", headers=headers)
    assert play.status_code == 200
    drama_body = play.json()["drama"]
    assert drama_body["like_count"] == 1
    assert drama_body["liked"] is True
    assert drama_body["favorited"] is True

    guest = client.get(f"/v1/dramas/{drama.id}/play").json()["drama"]
    assert guest["like_count"] == 1
    assert guest["liked"] is False
    assert guest["favorited"] is False

    removed = client.post(
        "/v1/reactions",
        json={"target_type": "drama", "target_id": drama.id, "kind": "like", "remove": True},
        headers=headers,
    )
    assert removed.json() == {"active": False}
    assert client.get(f"/v1/dramas/{drama.id}/play", headers=headers).json()["drama"]["like_count"] == 0


def test_history_returns_drama_cards(client, outbox, make, session):
    drama = make.drama(title="Watched Drama", status="published")
    episode = make.episode(drama, number=3, status="published")
    episode.duration_seconds = 600
    hidden = make.drama(title="Pulled Drama", status="draft")
    hidden_episode = make.episode(hidden, number=1, status="published")
    session.commit()

    assert client.get("/v1/me/history").status_code == 401

    _send_code(client, "watcher@example.com")
    token = _authenticate(client, "watcher@example.com", outbox[0]["code"]).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/v1/me", headers=headers).json()

    from app.modules.playback.models import PlaybackProgress

    session.add(PlaybackProgress(user_id=me["id"], episode_id=episode.id, position_seconds=42))
    session.add(PlaybackProgress(user_id=me["id"], episode_id=hidden_episode.id, position_seconds=9))
    session.commit()

    body = client.get("/v1/me/history", headers=headers).json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["episode_id"] == episode.id
    assert item["episode_number"] == 3
    assert item["position_seconds"] == 42
    assert item["duration_seconds"] == 600
    assert item["drama"] == {
        "id": drama.id,
        "title": "Watched Drama",
        "cover_url": "",
        "episode_count": 1,
    }


def test_liked_dramas_come_back_as_cards(client, outbox, make):
    drama = make.drama(title="Liked Drama", status="published")
    make.episode(drama, number=1, status="published")
    make.episode(drama, number=2, status="published")
    draft = make.drama(title="Unlisted Like", status="draft")

    _send_code(client, "liker@example.com")
    token = _authenticate(client, "liker@example.com", outbox[0]["code"]).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    for target in (drama, draft):
        client.post(
            "/v1/reactions",
            json={"target_type": "drama", "target_id": target.id, "kind": "like", "remove": False},
            headers=headers,
        )

    body = client.get("/v1/me/lists/like", headers=headers).json()
    assert body["total"] == 1
    assert body["items"] == [
        {
            "target_type": "drama",
            "target_id": drama.id,
            "drama": {
                "id": drama.id,
                "title": "Liked Drama",
                "cover_url": "",
                "episode_count": 2,
            },
        }
    ]
