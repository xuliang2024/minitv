"""Bunny gateway coverage: mode switching, token format, and the real-mode
wiring of upload sessions and playback URLs.

All network calls are stubbed (or avoided) here — live end-to-end checks are
run manually against the dev Bunny account, never in the test suite.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from app.modules.common import bunny


def test_mode_defaults_to_fake_and_live_gate(monkeypatch):
    monkeypatch.delenv("BUNNY_MODE", raising=False)
    assert bunny.mode() == "fake"
    assert bunny.live() is False
    monkeypatch.setenv("BUNNY_MODE", "real")
    assert bunny.live() is True


def test_advanced_token_format_is_reproducible():
    token = bunny.advanced_token("secret-key", "/42/", 1700000000)
    assert token.startswith("HS256-")
    body = token[len("HS256-") :]
    assert "+" not in body and "/" not in body and "=" not in body
    digest = hmac.new(b"secret-key", b"/42/1700000000", hashlib.sha256).digest()
    assert body == base64.urlsafe_b64encode(digest).decode().rstrip("=")
    # directory tokens sign the token_path parameter too (official signer behaviour)
    with_path = bunny.advanced_token("secret-key", "/42/", 1700000000, signing_data="token_path=/42/")
    digest2 = hmac.new(b"secret-key", b"/42/1700000000token_path=/42/", hashlib.sha256).digest()
    assert with_path == "HS256-" + base64.urlsafe_b64encode(digest2).decode().rstrip("=")


def test_real_mode_requires_credentials(monkeypatch):
    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.delenv("BUNNY_STREAM_TOKEN_KEY", raising=False)
    with pytest.raises(bunny.BunnyError):
        bunny.sign_stream_playback("vid-1")


def test_sign_stream_playback_url_shape(monkeypatch):
    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setenv("BUNNY_STREAM_CDN_HOST", "vz-test.b-cdn.net")
    monkeypatch.setenv("BUNNY_STREAM_TOKEN_KEY", "tok-key")
    signed = bunny.sign_stream_playback("vid-1", ttl=300)
    assert signed["url"].startswith("https://vz-test.b-cdn.net/bcdn_token=HS256-")
    assert "&token_path=%2Fvid-1%2F&expires=" in signed["url"]
    assert signed["url"].endswith("/vid-1/playlist.m3u8")


def test_create_upload_session_uses_real_stream_api(monkeypatch, session, make):
    """Real mode: the video id comes from the Bunny Stream API (stubbed here)."""
    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setenv("BUNNY_STREAM_LIBRARY_ID", "778985")
    monkeypatch.setenv("BUNNY_STREAM_API_KEY", "stream-key")
    seen = {}

    def fake_create(title: str) -> str:
        seen["title"] = title
        return "real-guid-1"

    monkeypatch.setattr(bunny, "create_stream_video", fake_create)
    from app.modules.content.upload_service import create_upload_session

    drama = make.drama(status="published")
    episode = make.episode(drama, number=1, status="not_uploaded")
    result = create_upload_session(
        session,
        episode=episode,
        filename="ep1.mp4",
        mime="video/mp4",
        size_bytes=1024,
        width=720,
        height=1280,
    )
    assert result["video_id"] == "real-guid-1"
    assert result["upload_url"] == bunny.STREAM_TUS_ENDPOINT
    assert result["headers"]["LibraryId"] == "778985"
    assert result["headers"]["VideoId"] == "real-guid-1"
    assert "AuthorizationSignature" in result["headers"]
    assert result["episode_id"] == episode.id
    assert episode.bunny_video_id == "real-guid-1"
    assert seen["title"] == "ep1.mp4"


def test_playback_uses_bunny_signed_url_in_real_mode(monkeypatch, session, make):
    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setenv("BUNNY_STREAM_CDN_HOST", "vz-test.b-cdn.net")
    monkeypatch.setenv("BUNNY_STREAM_TOKEN_KEY", "tok-key")
    from app.modules.playback import service as playback

    drama = make.drama(status="published", free=1)
    episode = make.episode(drama, number=1, status="published")
    episode.bunny_video_id = "real-guid-1"
    session.commit()
    result = playback.authorize(session, drama_id=drama.id, episode_id=episode.id, user_id=None)
    assert result["allowed"] is True
    assert "bcdn_token=HS256-" in result["url"]
    assert result["url"].endswith("/real-guid-1/playlist.m3u8")


def test_create_stream_video_retries_on_rate_limit(monkeypatch):
    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setenv("BUNNY_STREAM_LIBRARY_ID", "778985")
    monkeypatch.setenv("BUNNY_STREAM_API_KEY", "stream-key")
    calls = []

    def fake_http(method, url, **kwargs):
        calls.append(url)
        if len(calls) < 3:
            return 429, {}, b'{"message":"rate limited"}'
        return 200, {}, b'{"guid": "guid-after-retry"}'

    monkeypatch.setattr(bunny, "_http", fake_http)
    monkeypatch.setattr(bunny.time, "sleep", lambda seconds: None)
    assert bunny.create_stream_video("ep.mp4") == "guid-after-retry"
    assert len(calls) == 3


def test_create_stream_video_fails_fast_on_auth_error(monkeypatch):
    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setenv("BUNNY_STREAM_LIBRARY_ID", "778985")
    monkeypatch.setenv("BUNNY_STREAM_API_KEY", "stream-key")
    calls = []

    def fake_http(method, url, **kwargs):
        calls.append(url)
        return 401, {}, b'{"message":"unauthorized"}'

    monkeypatch.setattr(bunny, "_http", fake_http)
    with pytest.raises(bunny.BunnyError):
        bunny.create_stream_video("ep.mp4")
    assert len(calls) == 1


def test_stream_upload_uses_direct_put_api(monkeypatch, tmp_path):
    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setenv("BUNNY_STREAM_LIBRARY_ID", "778985")
    monkeypatch.setenv("BUNNY_STREAM_API_KEY", "stream-key")
    video = tmp_path / "ep.mp4"
    video.write_bytes(b"video-bytes")
    seen = {}

    def fake_http(method, url, **kwargs):
        seen.update({"method": method, "url": url, **kwargs})
        return 200, {}, b'{"success":true}'

    monkeypatch.setattr(bunny, "_http", fake_http)
    bunny.stream_upload("vid-1", video)

    assert seen["method"] == "PUT"
    assert seen["url"].endswith("/library/778985/videos/vid-1")
    assert seen["headers"]["AccessKey"] == "stream-key"
    assert seen["data"] == b"video-bytes"


def test_stream_embed_url_uses_embed_token_algorithm(monkeypatch):
    monkeypatch.setenv("BUNNY_MODE", "real")
    monkeypatch.setenv("BUNNY_STREAM_LIBRARY_ID", "778985")
    monkeypatch.setenv("BUNNY_STREAM_TOKEN_KEY", "embed-key")
    monkeypatch.setattr(bunny.time, "time", lambda: 1_700_000_000)
    signed = bunny.stream_embed_url("vid-1", ttl=300)
    expected = hashlib.sha256(b"embed-keyvid-11700000300").hexdigest()
    assert signed["expires_at"] == 1_700_000_300
    assert signed["token"] == expected
    assert signed["url"] == (
        "https://player.mediadelivery.net/play/778985/vid-1"
        f"?token={expected}&expires=1700000300"
    )
