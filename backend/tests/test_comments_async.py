"""Async moderation: author sees their own pending, nobody else does."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.account import service as accounts
from app.modules.ops.service import get_redis
from app.modules.social import moderation
from app.modules.social.models import Comment


@pytest.fixture()
def client(session):
    return TestClient(app)


def _headers(user) -> dict:
    token = f"test-{user.id}-{uuid.uuid4().hex[:8]}"
    get_redis().set(accounts.USER_SESSION_PREFIX + token, str(user.id), ex=3600)
    return {"Authorization": f"Bearer {token}"}


def _published(make):
    drama = make.drama(title="Async Drama", status="published", free=5)
    episodes = [make.episode(drama, number=n, status="published") for n in (1, 2)]
    return drama, episodes


def _enable(session) -> None:
    settings = moderation.get_settings(session)
    settings.enabled = True
    session.commit()


def _comment(session, episode, user, body: str, *, state: str = "queued", attempts: int = 0) -> Comment:
    row = Comment(
        user_id=user.id,
        drama_id=episode.drama_id,
        episode_id=episode.id,
        body=body,
        status="pending",
        moderation_state=state,
        moderation_attempts=attempts,
    )
    session.add(row)
    session.commit()
    return row


def test_author_sees_pending_others_do_not(client, session, make):
    drama, (episode, _) = _published(make)
    author = make.user(email="author@example.com")
    other = make.user(email="other@example.com")

    created = client.post(
        f"/v1/episodes/{episode.id}/comments", json={"body": "instant"}, headers=_headers(author)
    )
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "pending"
    assert body["item"]["pending"] is True
    assert body["item"]["mine"] is True
    assert body["moderation"]["state"] == "disabled"  # settings off in this test

    mine = client.get(f"/v1/episodes/{episode.id}/comments", headers=_headers(author)).json()
    assert mine["total"] == 1
    assert [item["body"] for item in mine["items"]] == ["instant"]
    assert mine["items"][0]["pending"] is True

    theirs = client.get(f"/v1/episodes/{episode.id}/comments", headers=_headers(other)).json()
    assert theirs == {"items": [], "next_cursor": None, "total": 0}
    guest = client.get(f"/v1/episodes/{episode.id}/comments").json()
    assert guest["total"] == 0

    author_play = client.get(f"/v1/dramas/{drama.id}/play", headers=_headers(author)).json()
    guest_play = client.get(f"/v1/dramas/{drama.id}/play").json()
    author_counts = {item["id"]: item["comment_count"] for item in author_play["items"]}
    guest_counts = {item["id"]: item["comment_count"] for item in guest_play["items"]}
    assert author_counts[episode.id] == 1
    assert guest_counts[episode.id] == 0


def test_worker_approves_and_everyone_sees(client, session, make, monkeypatch):
    _, (episode, _) = _published(make)
    _enable(session)
    author = make.user(email="approved@example.com")
    other = make.user(email="reader@example.com")
    row = _comment(session, episode, author, "looks good")

    monkeypatch.setattr(
        moderation,
        "classify",
        lambda *_: moderation.ModerationResult("allow", 0.96, "clean", "deepseek-flash", 12),
    )
    moderation.process_comment(row.id)

    session.expire_all()
    stored = session.get(Comment, row.id)
    assert stored.status == "approved"
    assert stored.moderation_state == "done"
    assert stored.moderation_verdict == "allow"

    theirs = client.get(f"/v1/episodes/{episode.id}/comments", headers=_headers(other)).json()
    assert theirs["total"] == 1
    assert theirs["items"][0]["pending"] is False


def test_worker_block_hides_from_the_author(client, session, make, monkeypatch):
    _, (episode, _) = _published(make)
    _enable(session)
    author = make.user(email="blocked@example.com")
    row = _comment(session, episode, author, "buy coins at spam.example.com")

    monkeypatch.setattr(
        moderation,
        "classify",
        lambda *_: moderation.ModerationResult("block", 0.99, "spam", "deepseek-flash", 12),
    )
    moderation.process_comment(row.id)

    session.expire_all()
    assert session.get(Comment, row.id).status == "deleted"
    assert client.get(f"/v1/episodes/{episode.id}/comments", headers=_headers(author)).json()["total"] == 0


def test_human_decision_is_not_overwritten(client, session, make, monkeypatch):
    _, (episode, _) = _published(make)
    author = make.user(email="human@example.com")
    row = _comment(session, episode, author, "needs a human")

    staff = make.admin(role="admin")
    token = client.post(
        "/admin/v1/auth/login", json={"username": staff.username, "password": "pass-1234"}
    ).json()["token"]
    approved = client.post(
        f"/admin/v1/comments/{row.id}",
        json={"status": "approved"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert approved.status_code == 200
    assert approved.json()["moderation_state"] == "canceled"

    monkeypatch.setattr(
        moderation,
        "classify",
        lambda *_: moderation.ModerationResult("block", 0.99, "late", "deepseek-flash", 12),
    )
    moderation.process_comment(row.id)

    session.expire_all()
    stored = session.get(Comment, row.id)
    assert stored.status == "approved"
    assert stored.moderation_state == "canceled"
    assert stored.moderation_verdict is None


def test_failures_retry_three_times_then_stop(session, make, monkeypatch):
    _, (episode, _) = _published(make)
    _enable(session)
    author = make.user(email="flaky@example.com")
    row = _comment(session, episode, author, "flake", attempts=2)

    def fail(*_args, **_kwargs):
        raise moderation.ModerationError("timeout")

    monkeypatch.setattr(moderation, "classify", fail)
    moderation.process_comment(row.id)

    session.expire_all()
    stored = session.get(Comment, row.id)
    assert stored.moderation_attempts == 3
    assert stored.moderation_state == "failed"
    assert stored.status == "pending"
    assert "timeout" in (stored.moderation_error or "")

    # A failed row is terminal: no fourth attempt, no status change.
    moderation.process_comment(row.id)
    session.expire_all()
    assert session.get(Comment, row.id).moderation_attempts == 3


def test_sweeper_recovers_stale_running_rows(session, make, monkeypatch):
    _, (episode, _) = _published(make)
    _enable(session)
    author = make.user(email="stale@example.com")
    row = _comment(session, episode, author, "orphan", state="running")
    row.moderation_claimed_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)
    session.commit()

    monkeypatch.setattr(
        moderation,
        "classify",
        lambda *_: moderation.ModerationResult("allow", 0.9, "ok", "deepseek-flash", 10),
    )
    picked = moderation.process_pending_batch(limit=5)

    assert picked == 1
    session.expire_all()
    stored = session.get(Comment, row.id)
    assert stored.status == "approved"
    assert stored.moderation_state == "done"


def test_queue_is_skipped_without_a_key(session, make, monkeypatch):
    _, (episode, _) = _published(make)
    author = make.user(email="nokey@example.com")
    settings = moderation.get_settings(session)
    settings.enabled = True
    session.commit()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert moderation.queue_state(session) is None


def test_post_enqueues_the_worker(client, session, make, monkeypatch):
    _enable(session)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _, (episode, _) = _published(make)
    author = make.user(email="queue@example.com")

    monkeypatch.setattr(
        moderation,
        "classify",
        lambda *_: moderation.ModerationResult("allow", 0.93, "ok", "deepseek-flash", 9),
    )
    created = client.post(
        f"/v1/episodes/{episode.id}/comments", json={"body": "queued"}, headers=_headers(author)
    )
    body = created.json()
    assert body["status"] == "pending"
    assert body["item"]["pending"] is True
    assert body["moderation"]["state"] == "queued"

    # TestClient drains BackgroundTasks before returning.
    session.expire_all()
    assert session.get(Comment, body["id"]).status == "approved"
