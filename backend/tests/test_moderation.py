"""Auto moderation policy: verdict mapping and failure fallback."""

from __future__ import annotations

import pytest

from app.modules.social import moderation


def test_settings_singleton_defaults(session):
    row = moderation.get_settings(session)
    assert row.id == 1
    assert row.enabled is False
    assert row.fallback_action == "pending"
    assert "spam" in row.categories
    assert session.get(moderation.ModerationSettings, 1) is not None


def test_decide_respects_thresholds(session):
    settings = moderation.get_settings(session)
    settings.approve_threshold = 0.8
    settings.block_threshold = 0.9

    assert moderation.decide("allow", 0.95, settings) == "approved"
    assert moderation.decide("allow", 0.5, settings) == "pending"
    assert moderation.decide("review", 1.0, settings) == "pending"
    assert moderation.decide("block", 0.95, settings) == "deleted"
    assert moderation.decide("block", 0.4, settings) == "pending"


def test_disabled_keeps_pending_and_skips_model(session, monkeypatch):
    settings = moderation.get_settings(session)
    settings.enabled = False
    session.commit()

    def explode(*_args, **_kwargs):
        raise AssertionError("model must not be called while disabled")

    monkeypatch.setattr(moderation, "classify", explode)
    status, result = moderation.moderate(session, "hello")
    assert status == "pending"
    assert result is None


def test_provider_failure_uses_fallback(session, monkeypatch):
    settings = moderation.get_settings(session)
    settings.enabled = True
    settings.fallback_action = "approved"
    session.commit()

    def fail(*_args, **_kwargs):
        raise moderation.ModerationError("timeout")

    monkeypatch.setattr(moderation, "classify", fail)
    status, result = moderation.moderate(session, "hello")
    assert status == "approved"
    assert result is None


def test_model_verdict_drives_status(session, monkeypatch):
    settings = moderation.get_settings(session)
    settings.enabled = True
    settings.approve_threshold = 0.7
    session.commit()

    monkeypatch.setattr(
        moderation,
        "classify",
        lambda *_: moderation.ModerationResult("allow", 0.91, "clean", "gpt-4o-mini", 120),
    )
    status, result = moderation.moderate(session, "great episode")
    assert status == "approved"
    assert result is not None
    assert result.verdict == "allow"


def test_prompt_receives_categories(session):
    settings = moderation.get_settings(session)
    settings.categories = ["spam", "privacy"]
    messages = moderation.build_messages("buy now", settings)
    assert "spam, privacy" in messages[0]["content"]
    assert "<comment>" in messages[1]["content"]


def test_key_status_never_leaks_full_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890abcd")
    status = moderation.key_status()
    assert status["configured"] is True
    assert "1234567890" not in status["masked"]
    assert status["masked"].endswith("abcd")


def test_parse_payload_accepts_fenced_json():
    data = moderation._parse_payload('```json\n{"verdict": "allow", "score": 0.9, "reason": "ok"}\n```')
    assert data["verdict"] == "allow"


def test_normalise_clamps_score_and_unknown_verdict():
    result = moderation._normalise({"verdict": "weird", "score": 3}, "m", 10)
    assert result.verdict == "review"
    assert result.score == 1.0
