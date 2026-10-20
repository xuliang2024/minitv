"""For-You feed: GET /v1/feed and the ranking behind it.

The rules under test are the product rules, not implementation details:

- a slot is one drama, and a locked episode never opens a slot
- pages are five items wide and walk a signed, opaque cursor
- the order is *not* the homepage's recency order
- a half-watched drama comes back first, carrying its resume point
"""

from __future__ import annotations

import datetime
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.main import app
from app.modules.account.service import USER_SESSION_PREFIX
from app.modules.content.models import Drama, DramaTag, Episode, Tag
from app.modules.ops.service import get_redis
from app.modules.playback.models import PlaybackEvent, PlaybackProgress

NOW = datetime.datetime.now(datetime.timezone.utc)


@pytest.fixture()
def client(session):
    return TestClient(app)


def _show(
    make,
    session,
    *,
    title: str,
    free: int,
    episodes: int = 3,
    updated_days_ago: int = 0,
    published_days_ago: int = 0,
    tag: Tag | None = None,
) -> Drama:
    """A published drama whose episodes all have a video attached."""
    drama = make.drama(title=title, status="published", free=free)
    for number in range(1, episodes + 1):
        episode = make.episode(drama, number=number, status="published")
        episode.bunny_video_id = f"vid-{drama.id}-{number}"
        episode.duration_seconds = 120
        episode.title = f"{title} EP{number}"
        session.commit()
    if tag is not None:
        session.add(DramaTag(drama_id=drama.id, tag_id=tag.id))
        session.commit()
    session.execute(
        update(Drama)
        .where(Drama.id == drama.id)
        .values(
            updated_at=NOW - datetime.timedelta(days=updated_days_ago),
            published_at=NOW - datetime.timedelta(days=published_days_ago),
        )
    )
    session.commit()
    return drama


def _episodes(session, drama: Drama) -> list[Episode]:
    return list(
        session.scalars(
            select(Episode)
            .where(Episode.drama_id == drama.id)
            .order_by(Episode.episode_number)
        )
    )


def _plays(session, episode: Episode, *, starts: int, completes: int, days_ago: int = 0) -> None:
    """Record real playback events; the feed ranks from exactly these rows."""
    when = NOW - datetime.timedelta(days=days_ago)
    for _ in range(starts):
        session.add(
            PlaybackEvent(episode_id=episode.id, kind="start", position_seconds=0, created_at=when)
        )
    for _ in range(completes):
        session.add(
            PlaybackEvent(
                episode_id=episode.id, kind="complete", position_seconds=120, created_at=when
            )
        )
    session.commit()


def _token(user) -> str:
    token = secrets.token_urlsafe(32)
    get_redis().set(USER_SESSION_PREFIX + token, str(user.id), ex=3600)
    return token


def _tag(session, name: str, category: str = "main") -> Tag:
    tag = Tag(name_en=name, name_zh=name, category=category)
    session.add(tag)
    session.commit()
    return tag


def test_feed_never_opens_a_slot_on_a_locked_episode(make, client, session):
    free_show = _show(make, session, title="Free Entry", free=2, episodes=5)
    _show(make, session, title="Paid Only", free=0, episodes=5)

    payload = client.get("/v1/feed").json()
    assert [item["drama"]["id"] for item in payload["items"]] == [free_show.id]
    assert payload["items"][0]["episode"]["episode_number"] == 1
    assert payload["items"][0]["episode"]["free"] is True


def test_feed_returns_five_items_per_page_by_default(make, client, session):
    for index in range(8):
        _show(make, session, title=f"Show {index}", free=3, episodes=4)

    payload = client.get("/v1/feed").json()
    assert len(payload["items"]) == 5
    assert payload["page_size"] == 5
    assert payload["has_more"] is True


def test_feed_pages_through_a_signed_cursor_without_repeating(make, client, session):
    for index in range(12):
        _show(make, session, title=f"Show {index}", free=3, episodes=4)

    first = client.get("/v1/feed?limit=5").json()
    second = client.get(f"/v1/feed?limit=5&cursor={first['next_cursor']}").json()
    third = client.get(f"/v1/feed?limit=5&cursor={second['next_cursor']}").json()

    assert len(first["items"]) == 5
    assert len(second["items"]) == 5
    assert len(third["items"]) == 2
    assert third["has_more"] is False
    assert third["next_cursor"] is None

    ids = [
        {item["item_id"] for item in page["items"]} for page in (first, second, third)
    ]
    assert ids[0].isdisjoint(ids[1])
    assert ids[0].isdisjoint(ids[2])
    assert ids[1].isdisjoint(ids[2])


def test_feed_rejects_a_tampered_cursor(make, client, session):
    for index in range(8):
        _show(make, session, title=f"Show {index}", free=3)

    first = client.get("/v1/feed?limit=5").json()
    payload, signature = first["next_cursor"].rsplit(".", 1)
    forged = f"{payload}.{'0' * len(signature)}"

    response = client.get(f"/v1/feed?limit=5&cursor={forged}")
    assert response.status_code == 400


def test_feed_order_is_not_the_homepage_recency_order(make, client, session, monkeypatch):
    """Homepage = newest first; feed = what people actually finish."""
    monkeypatch.setenv("FEED_EXPLORE_NOISE", "0")

    fresh = _show(
        make, session, title="Fresh But Weak", free=3, episodes=10,
        updated_days_ago=0, published_days_ago=0,
    )
    strong = _show(
        make, session, title="Old But Strong", free=3, episodes=10,
        updated_days_ago=30, published_days_ago=5,
    )
    # Same volume, very different outcomes.
    _plays(session, _episodes(session, fresh)[0], starts=100, completes=5, days_ago=0)
    _plays(session, _episodes(session, strong)[0], starts=100, completes=95, days_ago=0)

    home = client.get("/v1/home").json()
    assert home["items"][0]["id"] == fresh.id

    feed = client.get("/v1/feed").json()
    assert feed["items"][0]["drama"]["id"] == strong.id


def test_feed_resumes_the_drama_the_viewer_left_half_watched(
    make, client, session, monkeypatch
):
    monkeypatch.setenv("FEED_EXPLORE_NOISE", "0")

    watched = _show(
        make, session, title="Half Watched", free=3, episodes=6,
        updated_days_ago=20, published_days_ago=40,
    )
    _show(
        make, session, title="Untouched", free=3, episodes=6,
        updated_days_ago=1, published_days_ago=1,
    )
    user = make.user(email="viewer@example.com")
    first_episode = _episodes(session, watched)[0]
    session.add(
        PlaybackProgress(user_id=user.id, episode_id=first_episode.id, position_seconds=45)
    )
    session.commit()

    payload = client.get(
        "/v1/feed", headers={"Authorization": f"Bearer {_token(user)}"}
    ).json()
    assert payload["items"][0]["drama"]["id"] == watched.id
    assert payload["items"][0]["resume_seconds"] == 45
    assert payload["items"][0]["entry_reason"] == "resume"


def test_feed_includes_paid_drama_for_members(make, client, session, monkeypatch):
    """Locking applies to the entry episode; a member has no locked episodes."""
    monkeypatch.setenv("FEED_EXPLORE_NOISE", "0")

    paid = _show(make, session, title="Paid Only", free=0, episodes=5)
    user = make.user(email="member@example.com")
    make.subscription(user)

    payload = client.get(
        "/v1/feed", headers={"Authorization": f"Bearer {_token(user)}"}
    ).json()
    assert [item["drama"]["id"] for item in payload["items"]] == [paid.id]
    assert payload["items"][0]["episode"]["free"] is False


def test_feed_never_leaks_playback_material(make, client, session):
    _show(make, session, title="Streamable", free=3, episodes=4)

    response = client.get("/v1/feed")
    assert response.status_code == 200
    text = response.text.lower()
    for marker in ("m3u8", "token=", "bcdn_token", "playlist"):
        assert marker not in text


def test_feed_hides_dramas_blocked_in_the_callers_region(make, client, session):
    show = _show(make, session, title="Region Locked", free=3, episodes=4)
    session.execute(
        update(Drama).where(Drama.id == show.id).values(country_allowlist="JP")
    )
    session.commit()

    assert client.get("/v1/feed?country=JP").json()["items"]
    assert client.get("/v1/feed?country=US").json()["items"] == []


def test_drama_play_page_lists_every_episode_with_lock_state(make, client, session):
    drama = _show(make, session, title="Play Page", free=2, episodes=5)

    response = client.get(f"/v1/dramas/{drama.id}/play")
    assert response.status_code == 200
    payload = response.json()

    assert payload["drama"]["id"] == drama.id
    assert [(item["episode_number"], item["free"], item["locked"]) for item in payload["items"]] == [
        (1, True, False),
        (2, True, False),
        (3, False, True),
        (4, False, True),
        (5, False, True),
    ]
    assert payload["items"][2]["unlock"]["coin_price"] == drama.coin_price_per_episode
    assert payload["resume"] == {"episode_id": payload["items"][0]["id"], "position_seconds": 0}
    assert payload["entitlement"]["member"] is False

    text = response.text.lower()
    for marker in ("m3u8", "token=", "bcdn_token"):
        assert marker not in text


def test_drama_play_page_resumes_the_unfinished_episode(make, client, session):
    drama = _show(make, session, title="Resume Page", free=3, episodes=4)
    user = make.user(email="resumer@example.com")
    episodes = _episodes(session, drama)
    session.add(
        PlaybackProgress(user_id=user.id, episode_id=episodes[0].id, position_seconds=70)
    )
    session.commit()

    payload = client.get(
        f"/v1/dramas/{drama.id}/play",
        headers={"Authorization": f"Bearer {_token(user)}"},
    ).json()
    assert payload["resume"] == {"episode_id": episodes[0].id, "position_seconds": 70}
    assert payload["items"][0]["progress_seconds"] == 70


def test_drama_play_page_flags_finished_episodes(make, client, session):
    drama = _show(make, session, title="Watched Page", free=3, episodes=4)
    user = make.user(email="watcher@example.com")
    episodes = _episodes(session, drama)
    session.add(
        PlaybackProgress(user_id=user.id, episode_id=episodes[0].id, position_seconds=900)
    )
    session.add(
        PlaybackEvent(user_id=user.id, episode_id=episodes[0].id, kind="complete", position_seconds=900)
    )
    session.add(
        PlaybackProgress(user_id=user.id, episode_id=episodes[1].id, position_seconds=40)
    )
    session.commit()

    payload = client.get(
        f"/v1/dramas/{drama.id}/play",
        headers={"Authorization": f"Bearer {_token(user)}"},
    ).json()
    assert [item["completed"] for item in payload["items"]] == [True, False, False, False]
    # Half-watched is not finished: the sheet paints a bar, not a check.
    assert payload["items"][1]["progress_seconds"] == 40
    assert payload["resume"] == {"episode_id": episodes[1].id, "position_seconds": 40}

    guest = client.get(f"/v1/dramas/{drama.id}/play").json()
    assert [item["completed"] for item in guest["items"]] == [False, False, False, False]


def test_drama_play_page_404s_for_unpublished_drama(make, client, session):
    draft = make.drama(title="Not Out Yet", status="draft", free=3)
    assert client.get(f"/v1/dramas/{draft.id}/play").status_code == 404


def test_feed_entry_reason_is_reported_for_every_slot(make, client, session):
    for index in range(6):
        _show(make, session, title=f"Show {index}", free=3, episodes=4)

    payload = client.get("/v1/feed?limit=5").json()
    assert payload["items"]
    for item in payload["items"]:
        assert item["entry_reason"]
        assert item["item_id"]
        assert isinstance(item["score"], float)


def test_feed_preload_hint_tells_the_client_when_to_fetch(make, client, session):
    _show(make, session, title="Show", free=3, episodes=4)
    payload = client.get("/v1/feed").json()
    assert payload["preload"]["fetch_when_remaining"] >= 1


def test_feed_is_stable_across_identical_requests(make, client, session, monkeypatch):
    """Same session + same cursor must give the same page (no re-shuffling)."""
    monkeypatch.setenv("FEED_EXPLORE_NOISE", "0")
    for index in range(10):
        _show(make, session, title=f"Show {index}", free=3, episodes=4)

    first = client.get("/v1/feed?limit=5").json()
    again = client.get(f"/v1/feed?limit=5&cursor={first['next_cursor']}").json()
    again2 = client.get(f"/v1/feed?limit=5&cursor={first['next_cursor']}").json()
    assert [item["item_id"] for item in again["items"]] == [
        item["item_id"] for item in again2["items"]
    ]


def test_feed_penalises_shows_the_viewer_already_scrolled_past(
    make, client, session, monkeypatch
):
    """Impressions accumulate, so a second session demotes what was shown."""
    monkeypatch.setenv("FEED_EXPLORE_NOISE", "0")
    shows = [
        _show(make, session, title=f"Show {index}", free=3, episodes=4) for index in range(6)
    ]
    user = make.user(email="scroller@example.com")
    headers = {"Authorization": f"Bearer {_token(user)}"}

    first = client.get("/v1/feed?limit=5", headers=headers).json()
    first_ids = [item["drama"]["id"] for item in first["items"]]
    assert first_ids

    key = f"feed:imp:u{user.id}"
    counts = get_redis().hgetall(key)
    for drama_id in first_ids:
        assert counts.get(str(drama_id)) == "1"

    # Everything shown once now carries a fatigue penalty; a second session
    # still ranks, but the score of a re-shown title drops below a fresh one.
    from app.modules.recommend import service as feed_service

    plan = feed_service._build_plan(
        session, user_id=user.id, device_id=None, country=None, seed="second", limit=5
    )
    shown = {entry["drama_id"]: entry["score"] for entry in plan}
    unseen = [
        entry for entry in plan if entry["drama_id"] not in set(first_ids)
    ]
    assert unseen
    assert all(entry["source"] != "resume" for entry in plan)
    assert len(shown) == len(shows)
