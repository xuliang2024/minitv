"""Weighted scoring and diversity re-ranking.

``score_candidates`` turns each candidate into a single number; ``diversify``
then re-orders that list to satisfy the rules a scalar score cannot express.
Keeping them apart means the weights stay interpretable - if you bump the
affinity weight, the only thing that changes is how much taste matters, not
whether the feed looks repetitive.
"""

from __future__ import annotations

import hashlib
import math

from . import config, signals


def _clamp(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _noise(seed: str, candidate: signals.Candidate) -> float:
    """Deterministic per-(session, drama) jitter.

    Seeded rather than random so the same session replays the same order and
    the client can page through it without the plan shifting underneath it.
    """
    digest = hashlib.blake2b(f"{seed}:{candidate.drama.id}".encode(), digest_size=8).digest()
    unit = int.from_bytes(digest, "big") / float(1 << 64)
    amplitude = config.param("EXPLORE_NOISE", config.EXPLORE_NOISE)
    return (unit - 0.5) * 2.0 * amplitude


def _adjustments(store: signals.FeatureStore, candidate: signals.Candidate) -> float:
    """Additive nudges that are not "quality" and do not belong in the weights."""
    value = 0.0
    if candidate.resumable:
        value += config.param("RESUME_BOOST", config.RESUME_BOOST)
    impressions = store.impressions.get(candidate.drama.id, 0)
    if impressions > 0:
        value -= config.param("FATIGUE_PENALTY", config.FATIGUE_PENALTY) * math.log1p(impressions)
    return value


def score_candidates(
    store: signals.FeatureStore,
    candidates: list[signals.Candidate],
    *,
    seed: str,
    weights: dict[str, float] | None = None,
) -> None:
    """Fill ``candidate.features`` and ``candidate.score`` in place."""
    weights = weights if weights is not None else config.quality_weights()
    for candidate in candidates:
        features = {
            name: _clamp(function(store, candidate)) for name, function in signals.SIGNALS.items()
        }
        candidate.features = features
        quality = sum(weights.get(name, 0.0) * value for name, value in features.items())
        candidate.score = quality + _adjustments(store, candidate) + _noise(seed, candidate)


def diversify(
    store: signals.FeatureStore, candidates: list[signals.Candidate]
) -> list[signals.Candidate]:
    """Greedy re-ordering: no long run of one main category.

    Walks the score-sorted list and takes the best item that does not exceed
    the per-window quota for its main tag, deferring the rest. Items that can
    never be placed keep their original relative order at the end.
    """
    window = config.DIVERSITY_WINDOW
    max_same = config.MAX_SAME_MAIN_TAG
    ordered = sorted(candidates, key=lambda candidate: -candidate.score)
    pending = list(ordered)
    result: list[signals.Candidate] = []

    while pending:
        for index, candidate in enumerate(pending):
            tag = signals.main_tag(store, candidate)
            if tag is not None:
                recent = [
                    signals.main_tag(store, item) for item in result[-(window - 1) :]
                ]
                if recent.count(tag) >= max_same:
                    continue
            result.append(candidate)
            pending.pop(index)
            break
        else:
            # Nothing fits the quota; keep the remaining score order as-is.
            result.extend(pending)
            break
    return result


def rank(
    store: signals.FeatureStore, candidates: list[signals.Candidate], *, seed: str
) -> list[signals.Candidate]:
    """Full scoring pass: features -> weighted score -> diversity re-rank."""
    score_candidates(store, candidates, seed=seed)
    return diversify(store, candidates)
