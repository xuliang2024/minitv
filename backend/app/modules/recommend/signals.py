"""Feature store and scoring signals for the For-You feed.

Two rules shape this file:

1. **Every statistic is fetched in one grouped query per source table.** Ranking
   a page therefore costs O(number of tables), not O(number of dramas), and the
   feed stays a fixed number of round trips no matter how large the catalog
   gets.

2. **Signals are pure functions over the store.** They never touch the
   database, they always return a value inside [0, 1], and they return
   ``config.NEUTRAL`` when they have no evidence. That is what makes the
   weighting in :mod:`ranking` meaningful: all signals are on the same scale,
   so a weight is an honest statement about relative importance.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.content import service as content
from app.modules.content.models import Drama, DramaTag, Episode, Tag
from app.modules.playback.models import PlaybackEvent, PlaybackProgress
from app.modules.social.models import Reaction
from app.modules.wallet.models import ENTITLEMENT_TYPE_COIN, Entitlement

from . import config


@dataclass
class Candidate:
    """One feed slot: a drama plus the episode the player should open on."""

    drama: Drama
    entry_episode: Episode
    source: str = ""
    resume_seconds: int = 0
    resumable: bool = False
    features: dict[str, float] = field(default_factory=dict)
    score: float = 0.0

    @property
    def key(self) -> str:
        return f"d{self.drama.id}:e{self.entry_episode.id}"


@dataclass
class FeatureStore:
    """Everything the signals need, pre-aggregated."""

    now: datetime.datetime
    member: bool = False

    # per-drama aggregates (keyed by drama id)
    plays: dict[int, int] = field(default_factory=dict)  # starts, 30d
    completes: dict[int, int] = field(default_factory=dict)  # completes, 30d
    hot: dict[int, int] = field(default_factory=dict)  # starts, 7d
    published: dict[int, int] = field(default_factory=dict)  # published episodes
    total: dict[int, int] = field(default_factory=dict)  # episodes in any status
    tags: dict[int, list[Tag]] = field(default_factory=dict)

    # normalisation helpers computed from the candidate pool
    global_completion: float = config.DEFAULT_GLOBAL_COMPLETION
    max_hot: int = 1
    max_tag_score: float = 1.0

    # per-viewer state
    user_tag_scores: dict[int, float] = field(default_factory=dict)
    impressions: dict[int, int] = field(default_factory=dict)
    progress: dict[int, int] = field(default_factory=dict)  # episode_id -> seconds
    completed_episodes: set[int] = field(default_factory=set)
    unlocked_episodes: set[int] = field(default_factory=set)


# --------------------------------------------------------------------------- #
# store construction
# --------------------------------------------------------------------------- #


def build_store(
    db: Session,
    *,
    now: datetime.datetime,
    drama_ids: list[int],
    user_id: int | None,
    member: bool = False,
) -> FeatureStore:
    store = FeatureStore(now=now, member=member)
    if not drama_ids:
        return store
    _load_drama_aggregates(db, store, drama_ids)
    store.tags = content.tags_by_drama(db, drama_ids)
    if user_id is not None:
        _load_viewer_state(db, store, user_id)
        store.user_tag_scores = _viewer_tag_affinity(db, now, user_id)
        store.max_tag_score = max(store.user_tag_scores.values(), default=1.0) or 1.0
    return store


def _load_drama_aggregates(db: Session, store: FeatureStore, drama_ids: list[int]) -> None:
    long_window = store.now - datetime.timedelta(days=config.COMPLETION_WINDOW_DAYS)
    short_window = store.now - datetime.timedelta(days=config.HOTNESS_WINDOW_DAYS)
    starts = func.count().filter(PlaybackEvent.kind == "start")
    completes = func.count().filter(PlaybackEvent.kind == "complete")
    recent_starts = func.count().filter(
        PlaybackEvent.kind == "start", PlaybackEvent.created_at >= short_window
    )
    rows = db.execute(
        select(
            Episode.drama_id,
            starts.label("plays"),
            completes.label("completes"),
            recent_starts.label("hot"),
        )
        .select_from(PlaybackEvent)
        .join(Episode, Episode.id == PlaybackEvent.episode_id)
        .where(PlaybackEvent.created_at >= long_window, Episode.drama_id.in_(drama_ids))
        .group_by(Episode.drama_id)
    )
    for drama_id, plays, completes_count, hot in rows:
        store.plays[drama_id] = int(plays)
        store.completes[drama_id] = int(completes_count)
        store.hot[drama_id] = int(hot)
        store.max_hot = max(store.max_hot, int(hot))

    counts = db.execute(
        select(
            Episode.drama_id,
            func.count(),
            func.count().filter(Episode.status == "published"),
        )
        .where(Episode.drama_id.in_(drama_ids))
        .group_by(Episode.drama_id)
    )
    for drama_id, total, published in counts:
        store.total[drama_id] = int(total)
        store.published[drama_id] = int(published)

    total_plays = db.scalar(
        select(func.count()).select_from(PlaybackEvent).where(
            PlaybackEvent.created_at >= long_window, PlaybackEvent.kind == "start"
        )
    ) or 0
    total_completes = db.scalar(
        select(func.count()).select_from(PlaybackEvent).where(
            PlaybackEvent.created_at >= long_window, PlaybackEvent.kind == "complete"
        )
    ) or 0
    store.global_completion = (
        total_completes / total_plays if total_plays else config.DEFAULT_GLOBAL_COMPLETION
    )


def _load_viewer_state(db: Session, store: FeatureStore, user_id: int) -> None:
    rows = db.execute(
        select(PlaybackProgress.episode_id, PlaybackProgress.position_seconds).where(
            PlaybackProgress.user_id == user_id
        )
    )
    store.progress = {episode_id: int(position or 0) for episode_id, position in rows}

    store.completed_episodes = set(
        db.scalars(
            select(PlaybackEvent.episode_id)
            .where(PlaybackEvent.user_id == user_id, PlaybackEvent.kind == "complete")
            .distinct()
        )
    )
    store.unlocked_episodes = set(
        db.scalars(
            select(Entitlement.episode_id).where(
                Entitlement.user_id == user_id,
                Entitlement.entitlement_type == ENTITLEMENT_TYPE_COIN,
            )
        )
    )


def _viewer_tag_affinity(db: Session, now: datetime.datetime, user_id: int) -> dict[int, float]:
    """tag_id -> affinity score, from what the viewer actually finished or liked.

    A finished episode is worth more than a liked poster, which is worth more
    than a mere start; everything decays with age so taste can drift over a
    couple of weeks instead of being frozen by the first thing someone tapped.
    """
    cutoff = now - datetime.timedelta(days=config.AFFINITY_WINDOW_DAYS)
    decay_tau = config.param("AFFINITY_DECAY_DAYS", config.AFFINITY_DECAY_DAYS)
    weights = config.AFFINITY_WEIGHTS

    def decay(moment: datetime.datetime | None) -> float:
        if moment is None:
            return 1.0
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        age_days = max(0.0, (now - moment).total_seconds() / 86400.0)
        return math.exp(-age_days / decay_tau)

    scores: dict[int, float] = {}
    event_rows = db.execute(
        select(DramaTag.tag_id, PlaybackEvent.kind, PlaybackEvent.created_at)
        .select_from(PlaybackEvent)
        .join(Episode, Episode.id == PlaybackEvent.episode_id)
        .join(DramaTag, DramaTag.drama_id == Episode.drama_id)
        .where(PlaybackEvent.user_id == user_id, PlaybackEvent.created_at >= cutoff)
    )
    for tag_id, kind, created_at in event_rows:
        weight = weights.get(kind, weights["progress"])
        scores[tag_id] = scores.get(tag_id, 0.0) + weight * decay(created_at)

    reaction_rows = db.execute(
        select(DramaTag.tag_id, Reaction.created_at)
        .select_from(Reaction)
        .join(DramaTag, DramaTag.drama_id == Reaction.target_id)
        .where(
            Reaction.user_id == user_id,
            Reaction.target_type == "drama",
            Reaction.kind.in_(("like", "favorite")),
            Reaction.created_at >= cutoff,
        )
    )
    for tag_id, created_at in reaction_rows:
        scores[tag_id] = scores.get(tag_id, 0.0) + weights["like"] * decay(created_at)
    return scores


# --------------------------------------------------------------------------- #
# signals - each returns a score in [0, 1]
# --------------------------------------------------------------------------- #


def completion_signal(store: FeatureStore, candidate: Candidate) -> float:
    """Smoothed completion rate: complete / start, pulled towards the platform mean.

    A drama with three plays and three finishes must not outrank one with three
    thousand plays and twenty-five hundred finishes, hence the prior.
    """
    drama_id = candidate.drama.id
    starts = store.plays.get(drama_id, 0)
    completes = store.completes.get(drama_id, 0)
    alpha = config.param("COMPLETION_PRIOR_STRENGTH", config.COMPLETION_PRIOR_STRENGTH)
    return (completes + alpha * store.global_completion) / (starts + alpha)


def affinity_signal(store: FeatureStore, candidate: Candidate) -> float:
    """How well the drama's tags match this viewer's history.

    Expressed as 0.5..1.0 rather than 0..1: with no history an item is
    *neutral*, not *bad*, and the explore quota is what pulls fresh content to
    the top instead of the absence of a penalty.
    """
    if not store.user_tag_scores:
        return config.NEUTRAL
    scores = [
        store.user_tag_scores.get(tag.id, 0.0) for tag in store.tags.get(candidate.drama.id, [])
    ]
    if not scores:
        return config.NEUTRAL
    best = max(scores)
    return config.NEUTRAL + config.NEUTRAL * min(1.0, best / store.max_tag_score)


def hotness_signal(store: FeatureStore, candidate: Candidate) -> float:
    """Recent watch volume, log-scaled so one hit show cannot dominate."""
    if store.max_hot <= 0:
        return config.NEUTRAL
    return math.log1p(store.hot.get(candidate.drama.id, 0)) / math.log1p(store.max_hot)


def freshness_signal(store: FeatureStore, candidate: Candidate) -> float:
    """Exponential decay from the publish date."""
    drama = candidate.drama
    moment = drama.published_at or drama.updated_at or drama.created_at
    if moment is None:
        return config.NEUTRAL
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    age_days = max(0.0, (store.now - moment).total_seconds() / 86400.0)
    tau = config.param("FRESHNESS_TAU_DAYS", config.FRESHNESS_TAU_DAYS)
    return math.exp(-age_days / tau)


def depth_signal(store: FeatureStore, candidate: Candidate) -> float:
    """Is there enough published to be worth getting into?

    A two-episode stub is a worse feed slot than a complete season even when
    the stub's completion rate is perfect.
    """
    published = store.published.get(candidate.drama.id, 0)
    full = config.param("DEPTH_FULL_EPISODES", config.DEPTH_FULL_EPISODES)
    return min(1.0, published / full) if full > 0 else config.NEUTRAL


# Registry. Adding a signal = write a function + add a weight in config.py.
SIGNALS = {
    "completion": completion_signal,
    "affinity": affinity_signal,
    "hotness": hotness_signal,
    "freshness": freshness_signal,
    "depth": depth_signal,
}


def main_tag(store: FeatureStore, candidate: Candidate) -> Tag | None:
    """The tag this drama should be counted under for diversity purposes.

    Main-category tags win so "Romance" and "Revenge" do not get merged into
    whichever sub-tag happens to sort first.
    """
    tags = store.tags.get(candidate.drama.id, [])
    if not tags:
        return None
    for tag in tags:
        if tag.category == "main":
            return tag
    return tags[0]


def viewer_state(db: Session, user_id: int | None) -> tuple[bool, set[int], dict[int, int]]:
    """(member, unlocked episode ids, episode_id -> resume seconds) for one viewer."""
    if user_id is None:
        return False, set(), {}
    from app.modules.playback.service import subscription_active

    member = subscription_active(db, user_id)
    progress = {
        episode_id: int(position or 0)
        for episode_id, position in db.execute(
            select(PlaybackProgress.episode_id, PlaybackProgress.position_seconds).where(
                PlaybackProgress.user_id == user_id
            )
        )
    }
    unlocked = set(
        db.scalars(
            select(Entitlement.episode_id).where(
                Entitlement.user_id == user_id,
                Entitlement.entitlement_type == ENTITLEMENT_TYPE_COIN,
            )
        )
    )
    return member, unlocked, progress


def playable_episodes(db: Session, drama_ids: list[int]) -> dict[int, list[Episode]]:
    """Published episodes that actually have a video, grouped by drama, in order."""
    result: dict[int, list[Episode]] = {}
    if not drama_ids:
        return result
    rows = db.scalars(
        select(Episode)
        .where(
            Episode.drama_id.in_(drama_ids),
            Episode.status == "published",
            Episode.bunny_video_id != "",
        )
        .order_by(Episode.drama_id, Episode.episode_number)
    )
    for episode in rows:
        result.setdefault(episode.drama_id, []).append(episode)
    return result


def pick_entry_episode(
    drama: Drama,
    episodes: list[Episode],
    *,
    member: bool,
    unlocked: set[int],
    progress: dict[int, int],
    completed: set[int],
) -> tuple[Episode | None, int, bool]:
    """Choose the episode a feed slot opens on.

    Locked episodes never become the entry point - that is the product rule,
    so a paywalled drama simply drops out of the feed instead of showing a
    teaser the viewer cannot continue.

    Priority:
      1. an episode the viewer is part-way through (resume point attached)
      2. the earliest episode they have not finished
      3. the first watchable episode

    Returns ``(episode, resume_seconds, resumable)``.
    """
    watchable = [
        episode
        for episode in episodes
        if episode.episode_number <= drama.free_episode_count
        or member
        or episode.id in unlocked
    ]
    if not watchable:
        return None, 0, False

    max_ratio = config.param("RESUME_MAX_RATIO", config.RESUME_MAX_RATIO)
    for episode in watchable:
        position = progress.get(episode.id, 0)
        if position > 0 and episode.id not in completed:
            ratio = position / episode.duration_seconds if episode.duration_seconds else 0.0
            if ratio < max_ratio:
                return episode, int(position), True

    for episode in watchable:
        if episode.id not in completed:
            return episode, 0, False
    return watchable[0], 0, False


def visible_dramas(db: Session, country: str | None) -> list[Drama]:
    """Published dramas the caller's region is allowed to see."""
    dramas = list(db.scalars(select(Drama).where(Drama.status == "published")))
    if not country:
        return dramas
    upper = country.strip().upper()
    return [
        drama
        for drama in dramas
        if not drama.country_allowlist
        or upper in {code.strip().upper() for code in drama.country_allowlist.split(",") if code.strip()}
    ]
