"""Google Sign-In ID token verification.

The client obtains the token from Google. Only a signature that matches
Google's published keys, and an audience that matches this app, is trusted.
An unverified email is dropped so it cannot be used to merge accounts.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ["accounts.google.com", "https://accounts.google.com"]
_certs_cache: tuple[float, list[dict]] | None = None


class GoogleAuthError(Exception):
    """The ID token is missing, expired, or not issued for this app."""


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str | None
    email_verified: bool
    name: str
    picture: str


def configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID", "").strip())


def _audience() -> str:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        raise GoogleAuthError("google is not configured")
    return client_id


def _certs() -> list[dict]:
    global _certs_cache
    now = time.time()
    if _certs_cache is not None and _certs_cache[0] > now:
        return _certs_cache[1]
    request = urllib.request.Request(GOOGLE_CERTS_URL, headers={"User-Agent": "minitv-google"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode())
            max_age = 3600
            for part in response.headers.get("Cache-Control", "").split(","):
                item = part.strip()
                if item.startswith("max-age="):
                    max_age = int(item.removeprefix("max-age="))
    except (urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        raise GoogleAuthError("google keys are unreachable") from exc
    keys = payload.get("keys")
    if not isinstance(keys, list) or not keys:
        raise GoogleAuthError("google published no signing keys")
    _certs_cache = (now + max(60, max_age), keys)
    return keys


def reset_certs_for_tests() -> None:
    global _certs_cache
    _certs_cache = None


def verify_id_token(token: str) -> GoogleIdentity:
    """Verify a Google ID token and return the claims used for account linking."""
    import jwt
    from jwt import PyJWK

    audience = _audience()
    try:
        header = jwt.get_unverified_header(token)
        key = next(item for item in _certs() if item.get("kid") == header.get("kid"))
        claims = jwt.decode(
            token,
            PyJWK(key).key,
            algorithms=["RS256"],
            audience=audience,
            issuer=GOOGLE_ISSUERS,
        )
    except StopIteration as exc:
        raise GoogleAuthError("google signing key not found") from exc
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError("invalid google token") from exc
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise GoogleAuthError("google account has no subject")
    verified = claims.get("email_verified") is True
    raw_email = claims.get("email")
    email = raw_email.strip().lower() if verified and isinstance(raw_email, str) else None
    name = claims.get("name") if isinstance(claims.get("name"), str) else ""
    picture = claims.get("picture") if isinstance(claims.get("picture"), str) else ""
    return GoogleIdentity(
        subject=subject,
        email=email or None,
        email_verified=verified,
        name=name[:64],
        picture=picture[:500],
    )
