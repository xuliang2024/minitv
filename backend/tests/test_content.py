"""Stage-1 acceptance #1 (public visibility) and #2 (status machines).

Visibility: draft / in_review / unpublished dramas and non-published
episodes must never be returned by the public read path.

Status machines: every legal transition succeeds, every illegal one fails,
both through the service layer and directly at the database trigger layer.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from app.modules.content import service as content
from app.modules.content.models import Drama, Episode
from app.modules.content.service import DRAMA_TRANSITIONS, EPISODE_TRANSITIONS


# ---- acceptance 1: public visibility ----


def test_published_dramas_only(make, session):
    make.drama(title="draft one", status="draft")
    make.drama(title="review one", status="in_review")
    visible = make.drama(title="published one", status="published")
    make.drama(title="unpublished one", status="unpublished")

    listed = content.list_published_dramas(session)
    assert [d.id for d in listed] == [visible.id]

    for drama in session.query(Drama).all():
        found = content.get_published_drama(session, drama.id)
        if drama.status == "published":
            assert found is not None and found.id == drama.id
        else:
            assert found is None


def test_published_episodes_only(make, session):
    drama = make.drama(status="published")
    published_ep = make.episode(drama, number=1, status="published")
    make.episode(drama, number=2, status="in_review")
    make.episode(drama, number=3, status="not_uploaded")
    make.episode(drama, number=4, status="transcoding")
    make.episode(drama, number=5, status="transcode_failed")

    episodes = content.list_published_episodes(session, drama.id)
    assert [e.id for e in episodes] == [published_ep.id]

    for episode in session.query(Episode).all():
        found = content.get_published_episode(session, drama.id, episode.id)
        if episode.status == "published":
            assert found is not None and found.id == episode.id
        else:
            assert found is None


def test_unpublished_drama_hides_its_published_episodes(make, session):
    drama = make.drama(status="unpublished")
    make.episode(drama, number=1, status="published")

    assert content.get_published_drama(session, drama.id) is None
    assert content.list_published_episodes(session, drama.id) == []
    assert content.get_published_episode(session, drama.id, 1) is None


def test_nonexistent_drama_returns_nothing(make, session):
    assert content.get_published_drama(session, 999999) is None
    assert content.list_published_episodes(session, 999999) == []
    assert content.get_published_episode(session, 999999, 1) is None


# ---- acceptance 2: status machines (service layer, full matrix) ----


ALL_DRAMA_PAIRS = [(f, t) for f in DRAMA_TRANSITIONS for t in DRAMA_TRANSITIONS]


@pytest.mark.parametrize("from_status,to_status", ALL_DRAMA_PAIRS)
def test_drama_transition_matrix(make, session, from_status, to_status):
    drama = make.drama(status=from_status)
    legal = to_status in DRAMA_TRANSITIONS[from_status]
    if legal:
        content.transition_drama(session, drama, to_status)
        session.commit()
        session.expire_all()
        assert session.get(Drama, drama.id).status == to_status
    else:
        with pytest.raises(content.InvalidTransition):
            content.transition_drama(session, drama, to_status)


ALL_EPISODE_PAIRS = [(f, t) for f in EPISODE_TRANSITIONS for t in EPISODE_TRANSITIONS]


@pytest.mark.parametrize("from_status,to_status", ALL_EPISODE_PAIRS)
def test_episode_transition_matrix(make, session, from_status, to_status):
    drama = make.drama(status="draft")
    episode = make.episode(drama, number=1, status=from_status)
    legal = to_status in EPISODE_TRANSITIONS[from_status]
    if legal:
        content.transition_episode(session, episode, to_status)
        session.commit()
        session.expire_all()
        assert session.get(Episode, episode.id).status == to_status
    else:
        with pytest.raises(content.InvalidTransition):
            content.transition_episode(session, episode, to_status)


def test_full_happy_path_drama_and_episode(make, session):
    drama = make.drama(status="draft")
    episode = make.episode(drama, number=1, status="not_uploaded")

    content.transition_drama(session, drama, "in_review")
    content.transition_drama(session, drama, "published")
    content.transition_drama(session, drama, "unpublished")
    content.transition_drama(session, drama, "in_review")
    content.transition_drama(session, drama, "published")

    content.transition_episode(session, episode, "transcoding")
    content.transition_episode(session, episode, "in_review")
    content.transition_episode(session, episode, "published")
    session.commit()

    session.expire_all()
    assert session.get(Drama, drama.id).status == "published"
    assert session.get(Episode, episode.id).status == "published"


def test_episode_failure_and_retry_path(make, session):
    drama = make.drama(status="draft")
    episode = make.episode(drama, number=1, status="not_uploaded")

    content.transition_episode(session, episode, "transcoding")
    content.transition_episode(session, episode, "transcode_failed")
    content.transition_episode(session, episode, "transcoding")
    content.transition_episode(session, episode, "in_review")
    session.commit()

    session.expire_all()
    assert session.get(Episode, episode.id).status == "in_review"


# ---- acceptance 2: database triggers reject illegal jumps directly ----


def test_database_rejects_illegal_drama_status_jump(make, session):
    drama = make.drama(status="draft")
    with pytest.raises(SQLAlchemyError):
        session.execute(update(Drama).where(Drama.id == drama.id).values(status="published"))
    session.rollback()


def test_database_allows_legal_drama_status_update(make, session):
    drama = make.drama(status="draft")
    session.execute(update(Drama).where(Drama.id == drama.id).values(status="in_review"))
    session.commit()
    session.expire_all()
    assert session.get(Drama, drama.id).status == "in_review"


def test_database_rejects_illegal_episode_status_jump(make, session):
    drama = make.drama(status="published")
    episode = make.episode(drama, number=1, status="not_uploaded")
    with pytest.raises(SQLAlchemyError):
        session.execute(
            update(Episode).where(Episode.id == episode.id).values(status="in_review")
        )
    session.rollback()


def test_database_ignores_non_status_updates(make, session):
    drama = make.drama(status="draft", title="Before")
    session.execute(update(Drama).where(Drama.id == drama.id).values(title="After"))
    session.commit()
    session.expire_all()
    assert session.get(Drama, drama.id).title == "After"
    assert session.get(Drama, drama.id).status == "draft"
