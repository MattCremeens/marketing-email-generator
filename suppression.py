"""JSON-backed unsubscribe/suppression support for Serenity Blooms.

The local build persists to data/unsubscribes.json.  The public functions are kept
small so the storage implementation can later be swapped for Cloud Storage without
changing the FastAPI routes or send workflow.
"""

import base64
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import quote

from storage_backend import load_json, save_json

DATA_FILE = Path(__file__).resolve().parent / "data" / "unsubscribes.json"
DATA_OBJECT = "unsubscribes.json"
PREVIEW_UNSUBSCRIBE_URL = "/unsubscribe/preview"
UNSUBSCRIBE_MARKER = 'data-serenity-unsubscribe="true"'
_file_lock = Lock()


def _load_unlocked() -> dict:
    data = load_json(
        object_name=DATA_OBJECT,
        local_path=DATA_FILE,
        default={"unsubscribed": []},
    )
    if not isinstance(data, dict) or not isinstance(data.get("unsubscribed", []), list):
        raise ValueError("The unsubscribe JSON has an invalid format.")
    return data


def _load() -> dict:
    with _file_lock:
        return _load_unlocked()


def suppressed_addresses() -> set[str]:
    return {
        str(item.get("email", "")).strip().lower()
        for item in _load().get("unsubscribed", [])
        if isinstance(item, dict) and item.get("email")
    }


def add_unsubscribe(email: str) -> bool:
    """Add an address to the suppression list. Returns False if already present."""
    email = email.strip().lower()
    with _file_lock:
        data = _load_unlocked()
        existing = {
            str(item.get("email", "")).strip().lower()
            for item in data.get("unsubscribed", [])
            if isinstance(item, dict) and item.get("email")
        }
        if email in existing:
            return False

        data.setdefault("unsubscribed", []).append(
            {"email": email, "timestamp": datetime.now(timezone.utc).isoformat()}
        )
        save_json(object_name=DATA_OBJECT, local_path=DATA_FILE, value=data)
    return True


def _signing_secret() -> bytes:
    """Return the HMAC secret used for unsubscribe tokens.

    A deterministic development fallback keeps local testing frictionless.  Set
    UNSUBSCRIBE_SIGNING_SECRET to a strong Secret Manager value before deployment.
    """
    secret = os.getenv("UNSUBSCRIBE_SIGNING_SECRET", "serenity-blooms-local-dev-only")
    return secret.encode("utf-8")


def create_unsubscribe_token(email: str) -> str:
    """Create a non-expiring signed token for one recipient address."""
    normalized = email.strip().lower()
    payload = base64.urlsafe_b64encode(normalized.encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_signing_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def email_from_unsubscribe_token(token: str) -> str:
    """Validate a signed token and return the normalized email address."""
    try:
        payload, supplied_signature = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("Invalid unsubscribe token.") from exc

    expected_signature = hmac.new(
        _signing_secret(), payload.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ValueError("Invalid unsubscribe token.")

    try:
        padded = payload + "=" * (-len(payload) % 4)
        email = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8").strip().lower()
    except Exception as exc:
        raise ValueError("Invalid unsubscribe token.") from exc

    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        raise ValueError("Invalid unsubscribe token.")
    return email


def unsubscribe_url_for(email: str, base_url: str) -> str:
    token = quote(create_unsubscribe_token(email), safe="")
    return f"{base_url.rstrip('/')}/unsubscribe?t={token}"


def ensure_unsubscribe_footer(html: str) -> str:
    """Ensure a standard application-owned unsubscribe footer exists in a draft."""
    if UNSUBSCRIBE_MARKER in html:
        return html

    footer = f"""
<table role="presentation" width="100%" {UNSUBSCRIBE_MARKER} style="border-collapse:collapse;">
  <tr>
    <td align="center" style="padding:22px 20px;color:#6b635c;font-family:Arial,sans-serif;font-size:12px;line-height:1.5;">
      You are receiving this marketing email from Serenity Blooms.<br>
      <a href="{PREVIEW_UNSUBSCRIBE_URL}" style="color:#5b5350;text-decoration:underline;">Unsubscribe</a>
    </td>
  </tr>
</table>
""".strip()

    body_close = re.search(r"</body\s*>", html, flags=re.I)
    if body_close:
        return html[: body_close.start()] + footer + "\n" + html[body_close.start() :]
    return html.rstrip() + "\n" + footer


def personalize_unsubscribe_link(html: str, email: str, base_url: str) -> str:
    """Return send-ready HTML with a signed link for one recipient.

    This is intended for the Gmail send layer.  It also repairs a missing footer so
    callers cannot accidentally send a marketing email without an unsubscribe link.
    """
    html = ensure_unsubscribe_footer(html)
    return html.replace(PREVIEW_UNSUBSCRIBE_URL, unsubscribe_url_for(email, base_url), 1)
