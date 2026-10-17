"""Bunny (bunny.net) gateways: Stream video, Storage files, CDN tokens, purge.

Two modes, selected by the ``BUNNY_MODE`` environment variable:

- ``fake`` (default): local stubs. Upload sessions get locally generated video
  ids and a local callback signature; playback signs local URLs against
  ``PLAYBACK_CDN_BASE`` (default ``https://cdn.local``). Tests always run in
  this mode (pinned in conftest), so nothing here touches the network.
- ``real``: calls the Bunny APIs with the credentials below.

Real mode environment (see ``.env`` locally, ``.env.example`` for the template):

- ``BUNNY_API_KEY``            account-level API key (purge, resource admin)
- ``BUNNY_STREAM_LIBRARY_ID``  Stream library id
- ``BUNNY_STREAM_API_KEY``     Stream library API key (create video objects)
- ``BUNNY_STREAM_CDN_HOST``    playback hostname, e.g. ``vz-xxxx.b-cdn.net``
- ``BUNNY_STREAM_TOKEN_KEY``   Stream pull zone URL token key (playback signing)
- ``BUNNY_STORAGE_ZONE`` / ``BUNNY_STORAGE_HOST`` / ``BUNNY_STORAGE_PASSWORD``
- ``BUNNY_IMAGE_CDN_HOST`` / ``BUNNY_IMAGE_TOKEN_KEY``

URL token format (Bunny "Advanced Token Authentication"):

    token = "HS256-" + base64url(HMAC-SHA256(security_key, signature_path + expires))

HLS playback uses path-based directory tokens: the token is embedded in the
path (``/bcdn_token=...&expires=...&token_path=.../{video_id}/playlist.m3u8``)
so that relative segment requests inherit authentication from the manifest
path without the player having to re-sign each request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STREAM_API = "https://video.bunnycdn.com"
STREAM_TUS_ENDPOINT = f"{STREAM_API}/tusupload"
ACCOUNT_API = "https://api.bunny.net"
DEFAULT_TTL_SECONDS = 300


class BunnyError(Exception):
    """Raised when a Bunny API call fails or real mode is misconfigured."""


def mode() -> str:
    return os.environ.get("BUNNY_MODE", "fake").strip().lower() or "fake"


def live() -> bool:
    """True when the gateways below should talk to the real Bunny APIs."""
    return mode() == "real"


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise BunnyError(f"BUNNY_MODE=real but {name} is not configured")
    return value


def _http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 30,
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:  # HTTP-level error: return the body for callers
        return exc.code, dict(exc.headers), exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BunnyError(f"{method} {url} failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Stream: video object creation + TUS upload authorization
# ---------------------------------------------------------------------------


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def create_stream_video(title: str, *, attempts: int = 3) -> str:
    """Create a Stream video object and return its GUID (real mode).

    Retries on transient upstream failures (rate limits and 5xx) with a small
    exponential backoff: bulk upload-session creation can otherwise trip the
    library API rate limit and surface as upstream 502s.
    """
    payload = json.dumps({"title": title or "untitled"}).encode()
    last_error = ""
    for attempt in range(attempts):
        status, _, body = _http(
            "POST",
            f"{STREAM_API}/library/{_require('BUNNY_STREAM_LIBRARY_ID')}/videos",
            headers={"AccessKey": _require("BUNNY_STREAM_API_KEY"), "Content-Type": "application/json"},
            data=payload,
        )
        if status in (200, 201):
            video_id = json.loads(body).get("guid", "")
            if not video_id:
                raise BunnyError(f"create video returned no guid: {body[:200]!r}")
            return video_id
        last_error = f"{status}: {body[:200]!r}"
        if status not in RETRYABLE_STATUS:
            break
        if attempt < attempts - 1:
            time.sleep(0.5 * (2**attempt))
    raise BunnyError(f"create video failed ({last_error})")


def stream_upload(video_id: str, file_path: str | os.PathLike[str], *, timeout: int = 1800) -> None:
    """Upload one local video file to an existing Stream video object.

    Bunny supports a direct ``PUT`` for files up to 2 GB. Retries are
    deliberately left to the caller: a dropped connection restarts the whole
    upload, so the import script records the GUID only after this call returns.
    """
    data = Path(file_path).read_bytes()
    status, _, body = _http(
        "PUT",
        f"{STREAM_API}/library/{_require('BUNNY_STREAM_LIBRARY_ID')}/videos/{video_id}",
        headers={
            "AccessKey": _require("BUNNY_STREAM_API_KEY"),
            "Accept": "application/json",
            "Content-Type": "application/octet-stream",
        },
        data=data,
        timeout=timeout,
    )
    if status not in (200, 201):
        raise BunnyError(f"stream upload failed ({status}): {body[:200]!r}")


def list_stream_videos(*, page_size: int = 1000, attempts: int = 3) -> dict[str, dict]:
    """Return every video in the library, keyed by GUID.

    One page is up to 1000 items. Callers that need to reconcile a few hundred
    episodes should use this instead of one ``GET`` per video: the library API
    rate-limits single-object reads long before it rate-limits a listing.
    """
    library_id = _require("BUNNY_STREAM_LIBRARY_ID")
    found: dict[str, dict] = {}
    page = 1
    while True:
        last_error = ""
        data: dict | None = None
        for attempt in range(attempts):
            status, _, body = _http(
                "GET",
                f"{STREAM_API}/library/{library_id}/videos?page={page}&itemsPerPage={page_size}",
                headers={"AccessKey": _require("BUNNY_STREAM_API_KEY"), "Accept": "application/json"},
                timeout=90,
            )
            if status == 200:
                data = json.loads(body)
                break
            last_error = f"{status}: {body[:200]!r}"
            if status not in RETRYABLE_STATUS or attempt == attempts - 1:
                raise BunnyError(f"list videos failed ({last_error})")
            time.sleep(0.5 * (2**attempt))
        assert data is not None
        items = data.get("items") or []
        for item in items:
            guid = item.get("guid")
            if guid:
                found[guid] = item
        total = int(data.get("totalItems") or 0)
        if not items or page * page_size >= total:
            return found
        page += 1


def stream_video(video_id: str, *, attempts: int = 3) -> dict:
    """Return the Bunny Stream video object, retrying transient failures."""
    last_error = ""
    for attempt in range(attempts):
        status, _, body = _http(
            "GET",
            f"{STREAM_API}/library/{_require('BUNNY_STREAM_LIBRARY_ID')}/videos/{video_id}",
            headers={"AccessKey": _require("BUNNY_STREAM_API_KEY"), "Accept": "application/json"},
        )
        if status == 200:
            return json.loads(body)
        last_error = f"{status}: {body[:200]!r}"
        if status not in RETRYABLE_STATUS:
            break
        if attempt < attempts - 1:
            time.sleep(0.5 * (2**attempt))
    raise BunnyError(f"get video failed ({last_error})")


def delete_stream_video(video_id: str) -> None:
    """Best-effort cleanup for a video object whose upload never completed."""
    status, _, body = _http(
        "DELETE",
        f"{STREAM_API}/library/{_require('BUNNY_STREAM_LIBRARY_ID')}/videos/{video_id}",
        headers={"AccessKey": _require("BUNNY_STREAM_API_KEY"), "Accept": "application/json"},
    )
    if status not in (200, 201, 202, 204, 404):
        raise BunnyError(f"delete video failed ({status}): {body[:200]!r}")


def tus_headers(video_id: str, *, ttl: int = 3600) -> dict[str, str]:
    """Authorization headers the client must send on every TUS request.

    Bunny expects the same four headers on the create (POST) *and* on every
    PATCH of the upload session.
    """
    library_id = _require("BUNNY_STREAM_LIBRARY_ID")
    api_key = _require("BUNNY_STREAM_API_KEY")
    expire = int(time.time()) + ttl
    signature = hashlib.sha256(f"{library_id}{api_key}{expire}{video_id}".encode()).hexdigest()
    return {
        "AuthorizationSignature": signature,
        "AuthorizationExpire": str(expire),
        "VideoId": video_id,
        "LibraryId": library_id,
    }


def stream_cdn_host() -> str:
    return _require("BUNNY_STREAM_CDN_HOST")


# ---------------------------------------------------------------------------
# URL signing (Bunny Advanced Token Authentication)
# ---------------------------------------------------------------------------


def advanced_token(security_key: str, signature_path: str, expires: int, *, signing_data: str = "") -> str:
    """HMAC-SHA256 URL token in Bunny's ``HS256-<base64url>`` format.

    ``signing_data`` mirrors the official signers (BunnyWay/BunnyCDN.TokenAuthentication):
    the alphabetically sorted ``key=value`` parameters that also appear on the
    URL, excluding ``token``/``expires`` — e.g. ``token_path=/videos/`` for
    directory tokens. Signature message: ``signature_path + expires + signing_data``.
    """
    message = f"{signature_path}{expires}{signing_data}".encode()
    digest = hmac.new(security_key.encode(), message, hashlib.sha256).digest()
    return "HS256-" + base64.urlsafe_b64encode(digest).decode().rstrip("=")


def sign_directory(security_key: str, token_path: str, *, ttl: int = DEFAULT_TTL_SECONDS) -> dict:
    """Sign a directory token valid for every file under ``token_path``.

    The ``token_path`` parameter is part of the signature, exactly like the
    official signers when ``pathAllowed`` is supplied.
    """
    if not token_path.endswith("/"):
        raise ValueError("directory token path must end with '/'")
    expires = int(time.time()) + ttl
    token = advanced_token(security_key, token_path, expires, signing_data=f"token_path={token_path}")
    return {"token": token, "expires_at": expires, "path": token_path}


def sign_stream_playback(video_id: str, *, ttl: int = DEFAULT_TTL_SECONDS) -> dict:
    """Signed HLS playback URL (path-based directory token, segments inherit it).

    URL shape follows the official signer:
    ``/bcdn_token=<token>&token_path=<enc>&expires=<ts>/{video_id}/playlist.m3u8``
    """
    token_path = f"/{video_id}/"
    signed = sign_directory(_require("BUNNY_STREAM_TOKEN_KEY"), token_path, ttl=ttl)
    encoded = urllib.parse.quote(token_path, safe="")
    url = (
        f"https://{stream_cdn_host()}/bcdn_token={signed['token']}&token_path={encoded}"
        f"&expires={signed['expires_at']}/{video_id}/playlist.m3u8"
    )
    return {**signed, "url": url, "gen": 0}


def stream_embed_url(video_id: str, *, ttl: int = 3600) -> dict:
    """Signed Bunny Player URLs for an admin preview.

    Embed-view token authentication hashes ``token_key + video_id + expires``
    with SHA-256 and returns hex, unlike the path-based CDN token above. The
    same token works for both surfaces:

    * ``url``       - standalone player page (new-tab fallback)
    * ``embed_url`` - iframe embed used by the admin preview modal
    """
    library_id = _require("BUNNY_STREAM_LIBRARY_ID")
    key = _require("BUNNY_STREAM_TOKEN_KEY")
    expires = int(time.time()) + ttl
    token = hashlib.sha256(f"{key}{video_id}{expires}".encode()).hexdigest()
    query = f"token={token}&expires={expires}"
    return {
        "token": token,
        "expires_at": expires,
        "url": f"https://player.mediadelivery.net/play/{library_id}/{video_id}?{query}",
        "embed_url": f"https://iframe.mediadelivery.net/embed/{library_id}/{video_id}?{query}&autoplay=true&playsinline=true",
    }


def sign_image_url(remote_path: str, *, ttl: int = DEFAULT_TTL_SECONDS) -> dict:
    """Signed URL for a storage-backed file served by the image CDN.

    ``remote_path`` is a path inside the Storage Zone, e.g. ``subs/12/en.vtt``.
    Uses the query-string form of the token (single-file access).
    """
    path = "/" + remote_path.lstrip("/")
    token_path = path.rsplit("/", 1)[0] + "/"
    signed = sign_directory(_require("BUNNY_IMAGE_TOKEN_KEY"), token_path, ttl=ttl)
    encoded = urllib.parse.quote(token_path, safe="")
    url = f"https://{image_cdn_host()}{path}?token={signed['token']}&token_path={encoded}&expires={signed['expires_at']}"
    return {**signed, "url": url, "gen": 0}


# ---------------------------------------------------------------------------
# Storage: cover and subtitle files
# ---------------------------------------------------------------------------


def storage_upload(remote_path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    """Upload bytes to the Storage Zone (primary region endpoint)."""
    zone = _require("BUNNY_STORAGE_ZONE")
    host = _require("BUNNY_STORAGE_HOST")
    status, _, body = _http(
        "PUT",
        f"https://{host}/{zone}/{remote_path.lstrip('/')}",
        headers={"AccessKey": _require("BUNNY_STORAGE_PASSWORD"), "Content-Type": content_type},
        data=data,
        timeout=60,
    )
    if status not in (200, 201):
        raise BunnyError(f"storage upload failed ({status}): {body[:200]!r}")


def storage_delete(remote_path: str) -> None:
    """Delete one Storage Zone object. A missing object is not an error."""
    zone = _require("BUNNY_STORAGE_ZONE")
    host = _require("BUNNY_STORAGE_HOST")
    status, _, body = _http(
        "DELETE",
        f"https://{host}/{zone}/{remote_path.lstrip('/')}",
        headers={"AccessKey": _require("BUNNY_STORAGE_PASSWORD")},
        timeout=30,
    )
    if status not in (200, 204, 404):
        raise BunnyError(f"storage delete failed ({status}): {body[:200]!r}")


def image_cdn_host() -> str:
    return _require("BUNNY_IMAGE_CDN_HOST")


def image_url(remote_path: str) -> str:
    """Plain (unsigned) image CDN URL for a storage object."""
    return f"https://{image_cdn_host()}/{remote_path.lstrip('/')}"


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------


def purge_url(url: str) -> None:
    """Purge one URL (wildcards allowed) from the Bunny edge cache."""
    status, _, body = _http(
        "POST",
        f"{ACCOUNT_API}/purge?url={urllib.parse.quote(url, safe='')}&async=true",
        headers={"AccessKey": _require("BUNNY_API_KEY")},
    )
    if status >= 300:
        raise BunnyError(f"purge failed ({status}) for {url}: {body[:200]!r}")
