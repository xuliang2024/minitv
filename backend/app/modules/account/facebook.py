"""Facebook Login token verification.

Two shapes of proof reach this backend:

* An OpenID Connect ``id_token`` from Limited Login (the iOS SDK mode that
  never touches the advertising identifier). It is a JWT signed by Facebook,
  so — exactly like Google's — the claims are trusted once the signature and
  the audience check out.
* An opaque ``access_token`` from the Android SDK or the H5 JavaScript SDK.
  It proves nothing on its own, so it is exchanged first: ``debug_token`` says
  which app minted it and whether it is still valid, then ``/me`` yields the
  profile.

Either way the token has to belong to `FACEBOOK_APP_ID`; a token minted for
somebody else's Facebook app is refused.

Facebook does not publish an ``email_verified`` claim. The Graph API only
returns ``email`` for an address the user has confirmed, but that is a property
of the response, not of the token — the account-linking decision lives in
``service.py``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

FACEBOOK_JWKS_URL = "https://www.facebook.com/.well-known/oauth/openid/jwks/"
FACEBOOK_ISSUER = "https://www.facebook.com"
GRAPH_BASE = "https://graph.facebook.com/v21.0"
# `picture.type(large)` is the largest the default profile edge returns without
# an extra call; it is what the H5 and the app show on the profile screen.
GRAPH_FIELDS = "id,name,email,picture.type(large)"
_certs_cache: tuple[float, list[dict]] | None = None


class FacebookAuthError(Exception):
    """The token is missing, expired, forged, or not issued for this app."""


@dataclass(frozen=True)
class FacebookIdentity:
    subject: str
    email: str | None
    name: str
    picture: str
    # "limited_login" (verified JWT) or "graph" (access token exchange). Only
    # logged; both are equally trusted once they get here.
    via: str = "graph"


def app_id() -> str:
    return os.environ.get("FACEBOOK_APP_ID", "").strip()


def app_secret() -> str:
    return os.environ.get("FACEBOOK_APP_SECRET", "").strip()


def configured() -> bool:
    """True when the client can be shown a button and mint a token at all."""
    return bool(app_id())


def verify(access_token: str | None = None, id_token: str | None = None) -> FacebookIdentity:
    """Verify whichever token the client managed to obtain."""
    if id_token:
        return verify_id_token(id_token)
    if access_token:
        return verify_access_token(access_token)
    raise FacebookAuthError("facebook token is missing")


def _request_json(path: str, params: dict[str, str]) -> dict:
    url = f"{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "minitv-facebook"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        # Graph puts the reason in an OAuth-shaped error body; it is a client
        # problem (bad/expired token), never a 500 of ours.
        raise FacebookAuthError("facebook rejected the token") from exc
    except (urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        raise FacebookAuthError("facebook is unreachable") from exc
    if not isinstance(payload, dict):
        raise FacebookAuthError("facebook returned an unexpected payload")
    if "error" in payload:
        raise FacebookAuthError("facebook rejected the token")
    return payload


def _certs() -> list[dict]:
    global _certs_cache
    now = time.time()
    if _certs_cache is not None and _certs_cache[0] > now:
        return _certs_cache[1]
    request = urllib.request.Request(FACEBOOK_JWKS_URL, headers={"User-Agent": "minitv-facebook"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode())
            max_age = 3600
            for part in response.headers.get("Cache-Control", "").split(","):
                item = part.strip()
                if item.startswith("max-age="):
                    max_age = int(item.removeprefix("max-age="))
    except (urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        raise FacebookAuthError("facebook keys are unreachable") from exc
    keys = payload.get("keys")
    if not isinstance(keys, list) or not keys:
        raise FacebookAuthError("facebook published no signing keys")
    _certs_cache = (now + max(60, max_age), keys)
    return keys


def reset_certs_for_tests() -> None:
    global _certs_cache
    _certs_cache = None


def verify_id_token(token: str) -> FacebookIdentity:
    """Verify a Limited Login ID token and return the claims used for linking."""
    import jwt
    from jwt import PyJWK

    audience = app_id()
    if not audience:
        raise FacebookAuthError("facebook is not configured")
    try:
        header = jwt.get_unverified_header(token)
        key = next(item for item in _certs() if item.get("kid") == header.get("kid"))
        claims = jwt.decode(
            token,
            PyJWK(key).key,
            algorithms=["RS256"],
            audience=audience,
            issuer=FACEBOOK_ISSUER,
        )
    except StopIteration as exc:
        raise FacebookAuthError("facebook signing key not found") from exc
    except FacebookAuthError:
        raise
    except Exception as exc:
        raise FacebookAuthError("invalid facebook token") from exc
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise FacebookAuthError("facebook account has no subject")
    picture = claims.get("picture")
    return FacebookIdentity(
        subject=subject,
        email=_clean_email(claims.get("email")),
        name=_clean_name(claims.get("name")),
        picture=picture[:500] if isinstance(picture, str) else "",
        via="limited_login",
    )


def verify_access_token(token: str) -> FacebookIdentity:
    """Exchange an opaque access token for the profile it was minted for."""
    app = app_id()
    secret = app_secret()
    if not app or not secret:
        raise FacebookAuthError("facebook is not configured")
    debug = _request_json(
        f"{GRAPH_BASE}/debug_token",
        {"input_token": token, "access_token": f"{app}|{secret}"},
    )
    data = debug.get("data") if isinstance(debug.get("data"), dict) else {}
    if not data.get("is_valid"):
        raise FacebookAuthError("facebook access token is invalid")
    if str(data.get("app_id")) != app:
        raise FacebookAuthError("facebook token was issued for another app")
    profile = _request_json(
        f"{GRAPH_BASE}/me",
        {"fields": GRAPH_FIELDS, "access_token": token},
    )
    subject = profile.get("id")
    if not isinstance(subject, str) or not subject:
        raise FacebookAuthError("facebook account has no id")
    raw_picture = profile.get("picture")
    picture = ""
    if isinstance(raw_picture, dict):
        url = (raw_picture.get("data") or {}).get("url")
        if isinstance(url, str):
            picture = url
    return FacebookIdentity(
        subject=subject,
        email=_clean_email(profile.get("email")),
        name=_clean_name(profile.get("name")),
        picture=picture[:500],
        via="graph",
    )


def _clean_email(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    return email if "@" in email else None


def _clean_name(value: object) -> str:
    return value.strip()[:64] if isinstance(value, str) else ""
