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


def test_stored_key_is_encrypted_and_preferred(session, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-000000000000")
    monkeypatch.setenv("MODERATION_KEY_SECRET", "unit-test-secret")
    settings = moderation.get_settings(session)
    moderation.set_api_key(settings, "sk-db-111111111111")
    session.commit()

    # The row must never hold the credential in clear text.
    assert "sk-db" not in settings.api_key_secret
    assert settings.api_key_secret.startswith(moderation.KEY_BLOB_PREFIX)

    assert moderation.api_key(settings) == "sk-db-111111111111"
    status = moderation.key_status(settings)
    assert status["source"] == "database"
    assert "11111111" not in status["masked"]
    assert status["masked"].endswith("1111")


def test_clear_api_key_falls_back_to_env(session, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-222222222222")
    monkeypatch.setenv("MODERATION_KEY_SECRET", "unit-test-secret")
    settings = moderation.get_settings(session)
    moderation.set_api_key(settings, "sk-db-333333333333")
    moderation.clear_api_key(settings)
    session.commit()

    assert settings.api_key_secret == ""
    assert moderation.api_key(settings) == "sk-env-222222222222"
    assert moderation.key_status(settings)["source"] == "env"


def test_rotated_secret_degrades_to_env(session, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-444444444444")
    monkeypatch.setenv("MODERATION_KEY_SECRET", "first-secret")
    settings = moderation.get_settings(session)
    moderation.set_api_key(settings, "sk-db-555555555555")
    session.commit()

    # Losing the master secret must fail open to the env key, not crash.
    monkeypatch.setenv("MODERATION_KEY_SECRET", "second-secret")
    assert moderation.api_key(settings) == "sk-env-444444444444"


def test_set_api_key_rejects_bad_values(session):
    settings = moderation.get_settings(session)
    with pytest.raises(ValueError):
        moderation.set_api_key(settings, "short")
    with pytest.raises(ValueError):
        moderation.set_api_key(settings, "sk-has space in it")
    assert settings.api_key_secret == ""


def test_classify_accepts_draft_key_override(session, monkeypatch):
    settings = moderation.get_settings(session)
    settings.enabled = True
    session.commit()

    captured: dict[str, str] = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [
                    {
                        "message": {"content": '{"verdict": "allow", "score": 0.9, "reason": "ok"}'},
                        "finish_reason": "stop",
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None, headers=None):
            captured["auth"] = headers["Authorization"]
            return FakeResponse()

    monkeypatch.setattr(moderation.httpx, "Client", FakeClient)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = moderation.classify("hello", settings, key="sk-draft-666666666666")
    assert captured["auth"] == "Bearer sk-draft-666666666666"
    assert result.verdict == "allow"



def test_parse_payload_accepts_fenced_json():
    data = moderation._parse_payload('```json\n{"verdict": "allow", "score": 0.9, "reason": "ok"}\n```')
    assert data["verdict"] == "allow"


def test_normalise_clamps_score_and_unknown_verdict():
    result = moderation._normalise({"verdict": "weird", "score": 3}, "m", 10)
    assert result.verdict == "review"
    assert result.score == 1.0
