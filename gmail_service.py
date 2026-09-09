"""Gmail OAuth and send support for Serenity Blooms Email Studio.

This module deliberately sits outside the agent workflow. Agents create and revise
content; only an explicit application action is allowed to send email.

Local development stores one connected Gmail account's OAuth credentials in
``data/gmail_token.json``. Before Cloud Run deployment, move this token storage to a
persistent secret/data store rather than the container filesystem.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html as html_lib
import json
import mimetypes
import os
import re
import secrets
import time
from email.message import EmailMessage
from pathlib import Path
from threading import Lock
from typing import Any

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from storage_backend import delete_json, load_json, save_json
from suppression import personalize_unsubscribe_link, suppressed_addresses, unsubscribe_url_for

ROOT = Path(__file__).resolve().parent
TOKEN_FILE = ROOT / "data" / "gmail_token.json"
TOKEN_OBJECT = "gmail_token.json"
TOKEN_LOCK = Lock()

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    GMAIL_SEND_SCOPE,
]
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GmailNotConfiguredError(RuntimeError):
    pass


class GmailNotConnectedError(RuntimeError):
    pass


def oauth_configured() -> bool:
    return bool(os.getenv("GOOGLE_OAUTH_CLIENT_ID") and os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"))


def _client_config() -> dict[str, Any]:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise GmailNotConfiguredError(
            "Google OAuth is not configured. Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET."
        )
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def oauth_flow(redirect_uri: str, state: str | None = None) -> Flow:
    if redirect_uri.startswith("http://127.0.0.1") or redirect_uri.startswith("http://localhost"):
        # OAuthlib otherwise rejects non-HTTPS transport. Google explicitly supports
        # loopback HTTP redirect URIs for local development.
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
        # This is a confidential web OAuth client (client_secret is present).
        # Explicitly disable PKCE auto-generation so a fresh Flow created on
        # the callback does not require a code_verifier that was generated
        # only on the authorization leg.
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = redirect_uri
    return flow


def _state_secret() -> bytes:
    secret = os.getenv("APP_SESSION_SECRET") or os.getenv("UNSUBSCRIBE_SIGNING_SECRET")
    if not secret:
        secret = "serenity-blooms-local-oauth-state-only"
    return secret.encode("utf-8")


def create_oauth_state() -> str:
    body = f"{int(time.time())}:{secrets.token_urlsafe(24)}"
    payload = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_state_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def validate_oauth_state(state: str, max_age_seconds: int = 600) -> bool:
    try:
        payload, supplied_signature = state.split(".", 1)
        expected_signature = hmac.new(
            _state_secret(), payload.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return False
        padded = payload + "=" * (-len(payload) % 4)
        body = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        timestamp_text, _nonce = body.split(":", 1)
        age = time.time() - int(timestamp_text)
        return 0 <= age <= max_age_seconds
    except Exception:
        return False


def _load_token_record_unlocked() -> dict[str, Any] | None:
    data = load_json(
        object_name=TOKEN_OBJECT,
        local_path=TOKEN_FILE,
        default=None,
    )
    if not isinstance(data, dict) or not isinstance(data.get("credentials"), dict):
        return None
    return data


def _write_token_record_unlocked(credentials: Credentials, account_email: str) -> None:
    payload = {
        "account_email": account_email,
        "credentials": json.loads(credentials.to_json()),
    }
    save_json(object_name=TOKEN_OBJECT, local_path=TOKEN_FILE, value=payload)


def save_credentials(credentials: Credentials, account_email: str) -> None:
    with TOKEN_LOCK:
        _write_token_record_unlocked(credentials, account_email.strip().lower())


def disconnect_gmail() -> None:
    with TOKEN_LOCK:
        delete_json(object_name=TOKEN_OBJECT, local_path=TOKEN_FILE)


def _credentials_from_record(record: dict[str, Any]) -> Credentials:
    info = record["credentials"]
    scopes = info.get("scopes") or SCOPES
    return Credentials.from_authorized_user_info(info, scopes=scopes)


def load_credentials(refresh: bool = True) -> tuple[Credentials, str]:
    with TOKEN_LOCK:
        record = _load_token_record_unlocked()
        if not record:
            raise GmailNotConnectedError("No Gmail account is connected.")
        credentials = _credentials_from_record(record)
        account_email = str(record.get("account_email", "")).strip().lower()

        if refresh and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            _write_token_record_unlocked(credentials, account_email)

        if not credentials.valid and not (credentials.expired and credentials.refresh_token):
            raise GmailNotConnectedError("The Gmail authorization is no longer valid. Reconnect Gmail.")
        return credentials, account_email


def fetch_account_email(credentials: Credentials) -> str:
    session = AuthorizedSession(credentials)
    response = session.get(USERINFO_URL, timeout=15)
    response.raise_for_status()
    email = str(response.json().get("email", "")).strip().lower()
    if not email:
        raise RuntimeError("Google did not return the connected account email address.")
    return email


def gmail_status() -> dict[str, Any]:
    if not oauth_configured():
        return {"configured": False, "connected": False, "email": None}
    try:
        _credentials, email = load_credentials(refresh=True)
        return {"configured": True, "connected": True, "email": email or None}
    except Exception as exc:
        return {
            "configured": True,
            "connected": False,
            "email": None,
            "detail": str(exc),
        }


def _plain_text_from_html(html: str) -> str:
    text = re.sub(r"(?is)<(style|script).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</tr\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _inject_preheader(html: str, preheader: str) -> str:
    preheader = preheader.strip()
    if not preheader:
        return html
    safe = html_lib.escape(preheader)
    block = (
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;'
        'mso-hide:all;font-size:1px;line-height:1px;">'
        f"{safe}"
        "</div>"
    )
    body_open = re.search(r"<body[^>]*>", html, flags=re.I)
    if body_open:
        return html[: body_open.end()] + block + html[body_open.end() :]
    return block + html




def _asset_reference_parts(src: str) -> tuple[str, str] | None:
    """Map a browser-local asset URL to its storehouse category and filename."""
    # Handles /assets/images/x.jpg, localhost URLs, and malformed http:///assets/...
    match = re.search(r"/assets/(images|logos)/([^?#\"']+)", src, flags=re.I)
    if not match:
        return None
    return match.group(1).lower(), match.group(2)


def _prepare_email_images(html: str) -> tuple[str, list[tuple[str, Path, str, str]]]:
    """Make locally previewed assets deliverable in email.

    When EMAIL_PUBLIC_ASSET_BASE_URL is configured, browser-local /assets URLs
    are rewritten to that public base (intended for the Serenity Blooms Cloud
    Storage bucket).  Until then, matching local assets are embedded with CID
    references so local Gmail testing still displays images.
    """
    public_base = os.getenv("EMAIL_PUBLIC_ASSET_BASE_URL", "").strip().rstrip("/")
    related: list[tuple[str, Path, str, str]] = []
    cid_by_asset: dict[tuple[str, str], str] = {}

    def replace(match: re.Match[str]) -> str:
        quote = match.group(1)
        src = match.group(2)
        parts = _asset_reference_parts(src)
        if not parts:
            return match.group(0)
        category, filename = parts

        if public_base:
            new_src = f"{public_base}/{category}/{filename}"
            return f"src={quote}{new_src}{quote}"

        key = (category, filename)
        cid = cid_by_asset.get(key)
        if not cid:
            asset_path = ROOT / "storehouse" / "assets" / category / filename
            if not asset_path.is_file():
                raise ValueError(
                    f"Email asset '{category}/{filename}' was not found locally and "
                    "EMAIL_PUBLIC_ASSET_BASE_URL is not configured."
                )
            cid = f"{secrets.token_hex(12)}@serenityblooms"
            cid_by_asset[key] = cid
            mime, _ = mimetypes.guess_type(asset_path.name)
            if mime and "/" in mime:
                maintype, subtype = mime.split("/", 1)
            else:
                maintype, subtype = "application", "octet-stream"
            related.append((cid, asset_path, maintype, subtype))
        return f"src={quote}cid:{cid}{quote}"

    rewritten = re.sub(r"src\s*=\s*([\"'])(.*?)(?:\1)", replace, html, flags=re.I)
    return rewritten, related


def _gmail_raw_message(
    *,
    sender: str,
    recipient: str,
    subject: str,
    preheader: str,
    html: str,
    unsubscribe_url: str,
) -> str:
    html = _inject_preheader(html, preheader)
    html, related_images = _prepare_email_images(html)
    message = EmailMessage()
    message["To"] = recipient
    if sender:
        message["From"] = sender
    message["Subject"] = subject
    message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
    message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(_plain_text_from_html(html) or "Serenity Blooms marketing email")
    message.add_alternative(html, subtype="html")

    if related_images:
        html_part = message.get_payload()[-1]
        for cid, asset_path, maintype, subtype in related_images:
            html_part.add_related(
                asset_path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                cid=f"<{cid}>",
                filename=asset_path.name,
                disposition="inline",
            )

    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def send_campaign(
    *,
    subject: str,
    preheader: str,
    html: str,
    recipients: list[str],
    base_url: str,
) -> dict[str, Any]:
    """Send one personalized Gmail message per recipient.

    Suppression is re-checked immediately before each message so an unsubscribe that
    arrives during a send is honored for subsequent recipients.
    """
    credentials, account_email = load_credentials(refresh=True)
    if not subject.strip():
        raise ValueError("Add a subject before sending.")
    if not html.strip():
        raise ValueError("There is no email HTML to send.")

    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    sent: list[str] = []
    suppressed: list[str] = []
    failed: list[dict[str, str]] = []

    for recipient in recipients:
        normalized = recipient.strip().lower()
        if normalized in suppressed_addresses():
            suppressed.append(recipient)
            continue

        unsubscribe_url = unsubscribe_url_for(recipient, base_url)
        personalized_html = personalize_unsubscribe_link(html, recipient, base_url)
        raw = _gmail_raw_message(
            sender=account_email,
            recipient=recipient,
            subject=subject.strip(),
            preheader=preheader,
            html=personalized_html,
            unsubscribe_url=unsubscribe_url,
        )
        try:
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            sent.append(recipient)
        except HttpError as exc:
            failed.append({"email": recipient, "error": str(exc)})

    return {
        "account_email": account_email,
        "sent": sent,
        "suppressed": suppressed,
        "failed": failed,
        "counts": {
            "sent": len(sent),
            "suppressed": len(suppressed),
            "failed": len(failed),
        },
    }
