"""Episode-scoped comments: isolation, counts, likes and moderation visibility."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.account import service as accounts
from app.modules.ops.service import get_redis
from app.modules.social.models import Comment


@pytest.fixture()
def client(session):
    return TestClient(app)


def _headers(user) -> dict:
    token = f"test-{user.id}-{uuid.uuid4().hex[:8]}"
    get_redis().set(accounts.USER_SESSION_PREFIX + token, str(user.id), ex=3600)
    return {"Authorization": f"Bearer {token}"}


def _admin(client, make) -> dict:
    staff = make.admin(role="admin")
    response = client.post(
        "/admin/v1/auth/login", json={"username": staff.username, "password": "pass-1234"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _published(make, *, episodes: int = 2):
    drama = make.drama(title="Comment Drama", status="published", free=5)
    items = [make.episode(drama, number=n, status="published") for n in range(1, episodes + 1)]
    return drama, items


def _approve(session, comment_id: int) -> None:
    row = session.get(Comment, comment_id)
    row.status = "approved"
    session.commit()


def test_comment_is_scoped_to_its_episode(client, session, make):
    drama, (first, second) = _published(make)
    author = make.user(email="writer@example.com")
    viewer = make.user(email="viewer@example.com")

    created = client.post(
        f"/v1/episodes/{first.id}/comments", json={"body": "First!"}, headers=_headers(author)
    )
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "pending"
    comment_id = created.json()["id"]
    _approve(session, comment_id)

    mine = client.get(f"/v1/episodes/{first.id}/comments", headers=_headers(viewer))
    assert mine.status_code == 200
    body = mine.json()
    assert body["total"] == 1
    assert body["items"][0]["body"] == "First!"
    assert body["items"][0]["user"]["display_name"] == "writer"
    assert body["items"][0]["mine"] is False

    other = client.get(f"/v1/episodes/{second.id}/comments", headers=_headers(viewer))
    assert other.json() == {"items": [], "next_cursor": None, "total": 0}


def test_pending_comments_never_reach_the_client(client, session, make):
    _, (episode,) = _published(make, episodes=1)
    author = make.user(email="pending@example.com")
    created = client.post(
        f"/v1/episodes/{episode.id}/comments", json={"body": "needs review"}, headers=_headers(author)
    )
    assert created.json()["status"] == "pending"

    listed = client.get(f"/v1/episodes/{episode.id}/comments").json()
    assert listed["items"] == []
    assert listed["total"] == 0


def test_cursor_pagination_and_totals(client, session, make):
    _, (episode,) = _published(make, episodes=1)
    author = make.user(email="many@example.com")
    ids = []
    for index in range(3):
        row = Comment(
            user_id=author.id,
            drama_id=episode.drama_id,
            episode_id=episode.id,
            body=f"comment {index}",
            status="approved",
        )
        session.add(row)
        session.commit()
        ids.append(row.id)

    first_page = client.get(f"/v1/episodes/{episode.id}/comments?limit=2").json()
    assert len(first_page["items"]) == 2, first_page
    assert first_page["next_cursor"] == ids[1]
    assert first_page["total"] == 3

    second_page = client.get(
        f"/v1/episodes/{episode.id}/comments?limit=2&cursor={first_page['next_cursor']}"
    ).json()
    assert len(second_page["items"]) == 1
    assert second_page["next_cursor"] is None


def test_comment_likes_are_counted_per_comment(client, session, make):
    _, (episode,) = _published(make, episodes=1)
    author = make.user(email="liked@example.com")
    viewer = make.user(email="liker@example.com")
    created = client.post(
        f"/v1/episodes/{episode.id}/comments", json={"body": "like me"}, headers=_headers(author)
    )
    comment_id = created.json()["id"]
    _approve(session, comment_id)

    liked = client.post(
        "/v1/reactions",
        json={"target_type": "comment", "target_id": comment_id, "kind": "like"},
        headers=_headers(viewer),
    )
    assert liked.status_code == 200

    listed = client.get(f"/v1/episodes/{episode.id}/comments", headers=_headers(viewer)).json()
    assert listed["items"][0]["like_count"] == 1
    assert listed["items"][0]["liked"] is True

    client.post(
        "/v1/reactions",
        json={"target_type": "comment", "target_id": comment_id, "kind": "like", "remove": True},
        headers=_headers(viewer),
    )
    listed = client.get(f"/v1/episodes/{episode.id}/comments", headers=_headers(viewer)).json()
    assert listed["items"][0]["like_count"] == 0


def test_only_the_author_can_delete(client, session, make):
    _, (episode,) = _published(make, episodes=1)
    author = make.user(email="owner@example.com")
    stranger = make.user(email="stranger@example.com")
    created = client.post(
        f"/v1/episodes/{episode.id}/comments", json={"body": "mine"}, headers=_headers(author)
    )
    comment_id = created.json()["id"]
    _approve(session, comment_id)

    denied = client.delete(f"/v1/comments/{comment_id}", headers=_headers(stranger))
    assert denied.status_code == 403

    removed = client.delete(f"/v1/comments/{comment_id}", headers=_headers(author))
    assert removed.status_code == 200
    assert client.get(f"/v1/episodes/{episode.id}/comments").json()["total"] == 0


def test_new_comments_require_login_and_respect_cooldown(client, session, make):
    _, (episode,) = _published(make, episodes=1)
    assert client.post(f"/v1/episodes/{episode.id}/comments", json={"body": "hi"}).status_code == 401

    author = make.user(email="fast@example.com")
    first = client.post(
        f"/v1/episodes/{episode.id}/comments", json={"body": "one"}, headers=_headers(author)
    )
    assert first.status_code == 200
    second = client.post(
        f"/v1/episodes/{episode.id}/comments", json={"body": "two"}, headers=_headers(author)
    )
    assert second.status_code == 429


def test_play_payload_counts_comments_per_episode(client, session, make):
    drama, (first, second) = _published(make)
    author = make.user(email="counter@example.com")
    created = client.post(
        f"/v1/episodes/{first.id}/comments", json={"body": "counted"}, headers=_headers(author)
    )
    _approve(session, created.json()["id"])

    payload = client.get(f"/v1/dramas/{drama.id}/play").json()
    counts = {item["id"]: item["comment_count"] for item in payload["items"]}
    assert counts[first.id] == 1
    assert counts[second.id] == 0


def test_admin_queue_joins_drama_episode_and_user(client, session, make):
    drama, (episode,) = _published(make, episodes=1)
    author = make.user(email="queue@example.com")
    created = client.post(
        f"/v1/episodes/{episode.id}/comments", json={"body": "review me"}, headers=_headers(author)
    )
    assert created.status_code == 200

    headers = _admin(client, make)
    page = client.get("/admin/v1/comments?status=pending", headers=headers).json()
    assert page["total"] == 1
    row = page["items"][0]
    assert row["drama_title"] == "Comment Drama"
    assert row["episode_number"] == 1
    assert row["user_email"] == "queue@example.com"
