"""Transactional email via Resend.

Every product email (verification codes now, other notices later) goes through
``send_email``. The API key stays in ``RESEND_API_KEY``; the sender must be an
address on a domain verified in that Resend account.

``RESEND_FROM`` empty means the sender is not chosen yet. Calls fail closed
instead of guessing a domain that belongs to another product.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

RESEND_API = "https://api.resend.com/emails"


class MailError(Exception):
    """Resend rejected the message, or the sender is not configured."""


def configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY", "").strip() and os.environ.get("RESEND_FROM", "").strip())


def send_email(*, to: str, subject: str, text: str, html: str | None = None) -> str:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    sender = os.environ.get("RESEND_FROM", "").strip()
    if not api_key or not sender:
        raise MailError("RESEND_API_KEY and RESEND_FROM must both be set")
    payload: dict[str, object] = {
        "from": sender,
        "to": [to],
        "subject": subject,
        "text": text,
    }
    if html:
        payload["html"] = html
    request = urllib.request.Request(
        RESEND_API,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "minitv-mail",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        exc.read()
        raise MailError(f"resend rejected the message ({exc.code})") from exc
    except urllib.error.URLError as exc:
        raise MailError("resend is unreachable") from exc
    message_id = body.get("id")
    if not isinstance(message_id, str) or not message_id:
        raise MailError("resend returned no message id")
    return message_id
