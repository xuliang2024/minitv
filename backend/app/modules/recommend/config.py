"""Tunable knobs for the For-You feed.

Reading happens at call time (not import time) so weights can be adjusted with
an environment variable and picked up by a reload, which matters while the
ranking is still being calibrated against real watches.
"""

from __future__ import annotations

import os

# What a signal returns when it has no evidence at all. Using the middle of the
# range - not 0 - keeps a cold item competitive instead of burying it.
NEUTRAL = 0.5

# ---- paging ----
DEFAULT_PAGE_SIZE = 5
MAX_PAGE_SIZE = 20
# How many items of the current page may remain before the client should have
# the next page in hand. 2 = preload while two episodes are still unwatched.
PRELOAD_REMAINING = 2

# ---- scoring: quality signals ----
# Weights are re-normalised on load, so a partial env override cannot silently
# change the overall score scale.
QUALITY_WEIGHTS: dict[str, float] = {
    "completion": 0.30,  # did people who started this finish it
    "affinity": 0.26,  # does it match what this viewer likes
    "hotness": 0.18,  # is it being watched right now
    "freshness": 0.14,  # is it new
    "depth": 0.12,  # is there enough of it to be worth starting
}

# ---- scoring: additive adjustments (applied after the weighted sum) ----
RESUME_BOOST = 0.35  # the viewer left this one half-watched
FATIGUE_PENALTY = 0.09  # multiplied by log1p(times shown to this viewer)
EXPLORE_NOISE = 0.10  # seeded jitter amplitude
EXPLORE_QUOTA = 1  # cold/unknown items injected per page

# ---- signal parameters ----
COMPLETION_WINDOW_DAYS = 30
COMPLETION_PRIOR_STRENGTH = 20.0  # Bayesian smoothing strength
DEFAULT_GLOBAL_COMPLETION = 0.35  # used until the platform has real data
HOTNESS_WINDOW_DAYS = 7
FRESHNESS_TAU_DAYS = 21.0  # half-life-ish decay for "new"
AFFINITY_WINDOW_DAYS = 45
AFFINITY_DECAY_DAYS = 14.0
AFFINITY_WEIGHTS = {"complete": 3.0, "like": 2.0, "start": 1.0, "progress": 0.5}
DEPTH_FULL_EPISODES = 10.0  # published episodes that count as "a full show"

# ---- recall / session ----
RECALL_POOL_MULTIPLIER = 6  # ordered pool = page_size * this
MIN_POOL_SIZE = 12
SOURCE_QUOTA = 3  # max dramas one recall source may contribute
RESUME_QUOTA = 2  # max "continue watching" slots
IMPRESSION_TTL_SECONDS = 7 * 24 * 3600
PLAN_TTL_SECONDS = 7 * 24 * 3600

# ---- diversity ----
DIVERSITY_WINDOW = 5
MAX_SAME_MAIN_TAG = 2

# ---- resume ----
RESUME_MAX_RATIO = 0.95  # beyond this the episode counts as finished


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def quality_weights() -> dict[str, float]:
    """Weights for the quality signals, re-normalised to sum to 1."""
    raw = {
        name: _env_float(f"FEED_W_{name.upper()}", value) for name, value in QUALITY_WEIGHTS.items()
    }
    total = sum(max(0.0, value) for value in raw.values())
    if total <= 0:
        return dict(QUALITY_WEIGHTS)
    return {name: max(0.0, value) / total for name, value in raw.items()}


def param(name: str, default: float) -> float:
    """Read a tunable parameter, allowing a ``FEED_<NAME>`` env override."""
    return _env_float(f"FEED_{name}", default)
