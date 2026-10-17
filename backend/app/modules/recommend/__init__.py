"""For-You feed recommendation ("发现 / For You" vertical swipe surface).

The pipeline is deliberately split into three replaceable stages:

    recall        ->  scoring        ->  re-ranking
    service.py       signals.py/ranking.py   ranking.py

- recall  pulls a candidate pool from several independent sources so the feed
  is never the output of one query.
- scoring is a weighted sum of *normalised* signals; every signal returns a
  value in [0, 1] and NEUTRAL when it has no evidence, so weights can be
  re-tuned without renormalising the inputs.
- re-ranking enforces the product rules a score cannot express: one slot per
  drama, no main-tag streaks, and a seeded jitter so two people never see the
  exact same order.

Playback material is *never* attached here. The feed only decides what to show;
`POST /v1/playback` still decides whether it can be watched.
"""
