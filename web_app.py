"""FastAPI application for the Serenity Blooms Email Studio."""

import html as html_lib
import os
import re
import uuid
from pathlib import Path
from threading import Lock
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Tools resolve assets to browser-facing URLs when invoked by an agent.
# Locally this defaults to FastAPI's /assets mounts. On Cloud Run, setting
# EMAIL_PUBLIC_ASSET_BASE_URL makes both previews and outgoing email use the
# public Cloud Storage URLs.
_public_asset_base = os.getenv("EMAIL_PUBLIC_ASSET_BASE_URL", "").strip().rstrip("/")
os.environ.setdefault("EMAIL_ASSET_BASE_URL", _public_asset_base or "/assets")

from gmail_service import (
    GmailNotConfiguredError,
    create_oauth_state,
    disconnect_gmail,
    fetch_account_email,
    gmail_status,
    oauth_configured,
    oauth_flow,
    save_credentials,
    send_campaign,
    validate_oauth_state,
)
from storage_backend import load_json, save_json
from suppression import (
    add_unsubscribe,
    email_from_unsubscribe_token,
    ensure_unsubscribe_footer,
    suppressed_addresses,
)
from web_runtime import generate_email, revise_email

ROOT = Path(__file__).resolve().parent
DRAFT_DIR = ROOT / "data" / "drafts"

app = FastAPI(title="Serenity Blooms Email Studio")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.mount("/assets/images", StaticFiles(directory=ROOT / "storehouse" / "assets" / "images"), name="images")
app.mount("/assets/logos", StaticFiles(directory=ROOT / "storehouse" / "assets" / "logos"), name="logos")

_drafts: dict[str, dict[str, str]] = {}
_draft_lock = Lock()


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=10000)


class RevisionRequest(BaseModel):
    draft_id: str
    feedback: str = Field(min_length=1, max_length=10000)
    html: str
    subject: str = ""
    preheader: str = ""


class SaveRequest(BaseModel):
    draft_id: str
    html: str
    subject: str = ""
    preheader: str = ""


class RecipientRequest(BaseModel):
    recipients: str = ""


class UnsubscribeRequest(BaseModel):
    email: str


class SendRequest(BaseModel):
    draft_id: str
    recipients: str = Field(min_length=1)


def _public_asset_html(html: str) -> str:
    """Rewrite legacy/local preview asset URLs when a public base is configured."""
    public_base = os.getenv("EMAIL_PUBLIC_ASSET_BASE_URL", "").strip().rstrip("/")
    if not public_base:
        return html

    pattern = re.compile(
        r"src\s*=\s*([\"'])(?:(?:https?://[^/\"']+)?/|https?:///+)assets/(images|logos)/([^?#\"']+)(?:[?#][^\"']*)?\1",
        flags=re.I,
    )

    def replace(match: re.Match[str]) -> str:
        quote_char = match.group(1)
        return (
            f"src={quote_char}{public_base}/{match.group(2)}/{match.group(3)}{quote_char}"
        )

    return pattern.sub(replace, html)


def _normalize_draft(draft: dict[str, str]) -> dict[str, str]:
    """Apply application-owned requirements after any agent or human edit."""
    return {
        "html": ensure_unsubscribe_footer(_public_asset_html(draft.get("html", ""))),
        "subject": draft.get("subject", ""),
        "preheader": draft.get("preheader", ""),
    }


def _draft_object_name(draft_id: str) -> str:
    return f"drafts/{draft_id}.json"


def _save_draft(draft_id: str, draft: dict[str, str]) -> dict[str, str]:
    draft = _normalize_draft(draft)
    with _draft_lock:
        _drafts[draft_id] = draft
        save_json(
            object_name=_draft_object_name(draft_id),
            local_path=DRAFT_DIR / f"{draft_id}.json",
            value=draft,
        )
    return draft


def _get_draft(draft_id: str) -> dict[str, str] | None:
    with _draft_lock:
        cached = _drafts.get(draft_id)
        if cached is not None:
            return dict(cached)
        loaded = load_json(
            object_name=_draft_object_name(draft_id),
            local_path=DRAFT_DIR / f"{draft_id}.json",
            default=None,
        )
        if not isinstance(loaded, dict):
            return None
        draft = _normalize_draft({
            "html": str(loaded.get("html", "")),
            "subject": str(loaded.get("subject", "")),
            "preheader": str(loaded.get("preheader", "")),
        })
        _drafts[draft_id] = draft
        return dict(draft)


def _parse_recipients(value: str) -> tuple[list[str], list[str]]:
    pieces = [p.strip() for p in re.split(r"[,;\n\r]+", value) if p.strip()]
    email_re = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for item in pieces:
        normalized = item.lower()
        if not email_re.match(item):
            invalid.append(item)
        elif normalized not in seen:
            seen.add(normalized)
            valid.append(item)
    return valid, invalid


def _base_url(request: Request) -> str:
    configured = os.getenv("APP_BASE_URL", "").strip()
    return configured.rstrip("/") if configured else str(request.base_url).rstrip("/")


def _oauth_redirect_uri(request: Request) -> str:
    configured = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    if configured:
        return configured
    return f"{_base_url(request)}/auth/google/callback"


def _absolute_callback_url(request: Request) -> str:
    # When APP_BASE_URL is set (for Cloud Run), reconstruct the externally visible
    # authorization response URL rather than trusting an internal proxy scheme/host.
    base = _base_url(request)
    suffix = request.url.path
    if request.url.query:
        suffix += f"?{request.url.query}"
    return f"{base}{suffix}"


def _unsubscribe_page(title: str, message: str) -> HTMLResponse:
    safe_title = html_lib.escape(title)
    safe_message = html_lib.escape(message)
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} · Serenity Blooms</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin:0; min-height:100vh; display:grid; place-items:center; padding:24px;
      background:linear-gradient(180deg,#f8e9ed,#faf7f2); color:#3d3632;
      font-family:Arial,sans-serif; }}
    main {{ width:min(560px,100%); background:#fff; border:1px solid #e6d7da;
      border-radius:18px; padding:36px; text-align:center; box-shadow:0 16px 40px rgba(62,55,55,.08); }}
    .flower {{ color:#c78493; font-size:38px; margin-bottom:10px; }}
    h1 {{ font-family:Georgia,serif; font-weight:500; margin:0 0 14px; }}
    p {{ margin:0; color:#6b635c; line-height:1.6; }}
  </style>
</head>
<body><main><div class="flower">✿</div><h1>{safe_title}</h1><p>{safe_message}</p></main></body>
</html>"""
    )


def _process_unsubscribe_token(token: str) -> HTMLResponse:
    try:
        email = email_from_unsubscribe_token(token)
    except ValueError:
        return _unsubscribe_page(
            "This link is not valid",
            "We could not verify this unsubscribe link. Please contact Serenity Blooms if you need help.",
        )

    added = add_unsubscribe(email)
    if added:
        return _unsubscribe_page(
            "You have been unsubscribed",
            f"{email} will no longer receive Serenity Blooms marketing emails.",
        )
    return _unsubscribe_page(
        "Already unsubscribed",
        f"{email} is already on the Serenity Blooms unsubscribe list.",
    )


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.post("/api/generate")
async def api_generate(request: GenerateRequest):
    try:
        draft = await generate_email(request.prompt.strip())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Email generation failed: {exc}") from exc
    draft_id = str(uuid.uuid4())
    draft = _save_draft(draft_id, draft)
    return {"draft_id": draft_id, **draft}


@app.post("/api/revise")
async def api_revise(request: RevisionRequest):
    existing = _get_draft(request.draft_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Draft not found. Start a new email.")
    try:
        draft = await revise_email(
            html=request.html,
            feedback=request.feedback.strip(),
            subject=request.subject,
            preheader=request.preheader,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Email revision failed: {exc}") from exc
    draft = _save_draft(request.draft_id, draft)
    return {"draft_id": request.draft_id, **draft}


@app.post("/api/draft/save")
async def api_save_draft(request: SaveRequest):
    if _get_draft(request.draft_id) is None:
        raise HTTPException(status_code=404, detail="Draft not found. Start a new email.")
    draft = _save_draft(
        request.draft_id,
        {"html": request.html, "subject": request.subject, "preheader": request.preheader},
    )
    return {"status": "saved", "draft_id": request.draft_id, **draft}


@app.post("/api/recipients/validate")
async def api_validate_recipients(request: RecipientRequest):
    valid, invalid = _parse_recipients(request.recipients)
    suppressed = suppressed_addresses()
    blocked = [email for email in valid if email.lower() in suppressed]
    sendable = [email for email in valid if email.lower() not in suppressed]
    return {
        "sendable": sendable,
        "invalid": invalid,
        "suppressed": blocked,
        "counts": {
            "sendable": len(sendable),
            "invalid": len(invalid),
            "suppressed": len(blocked),
        },
    }


@app.post("/api/unsubscribe")
async def api_unsubscribe(request: UnsubscribeRequest):
    """Administrative/local testing route that suppresses one explicit address."""
    valid, invalid = _parse_recipients(request.email)
    if invalid or len(valid) != 1:
        raise HTTPException(status_code=400, detail="Enter one valid email address.")
    added = add_unsubscribe(valid[0])
    return {"status": "unsubscribed" if added else "already_unsubscribed"}


@app.get("/unsubscribe/preview", response_class=HTMLResponse)
async def unsubscribe_preview():
    return _unsubscribe_page(
        "Unsubscribe link preview",
        "In a sent email, this link will be personalized for the recipient. No address was unsubscribed from this preview.",
    )


@app.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_from_email(t: str = Query(min_length=10)):
    return _process_unsubscribe_token(t)


@app.post("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_one_click(t: str = Query(min_length=10)):
    """RFC 8058/Gmail one-click endpoint used by List-Unsubscribe-Post."""
    return _process_unsubscribe_token(t)


@app.get("/api/gmail/status")
async def api_gmail_status():
    return gmail_status()


@app.get("/auth/google/start")
async def google_auth_start(request: Request):
    if not oauth_configured():
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured on this application yet.",
        )
    redirect_uri = _oauth_redirect_uri(request)
    state = create_oauth_state()
    flow = oauth_flow(redirect_uri, state=state)
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return RedirectResponse(authorization_url)


@app.get("/auth/google/callback")
async def google_auth_callback(request: Request, state: str = Query(min_length=10)):
    if not validate_oauth_state(state):
        return RedirectResponse("/?gmail_error=" + quote("The Google OAuth state could not be verified."))
    try:
        redirect_uri = _oauth_redirect_uri(request)
        flow = oauth_flow(redirect_uri, state=state)
        flow.fetch_token(authorization_response=_absolute_callback_url(request))
        email = fetch_account_email(flow.credentials)
        save_credentials(flow.credentials, email)
    except Exception as exc:
        return RedirectResponse("/?gmail_error=" + quote(str(exc)))
    return RedirectResponse("/?gmail=connected")


@app.post("/api/gmail/disconnect")
async def api_gmail_disconnect():
    disconnect_gmail()
    return {"status": "disconnected"}


@app.post("/api/send")
async def api_send(request: SendRequest, http_request: Request):
    persisted_draft = _get_draft(request.draft_id)
    if persisted_draft is None:
        raise HTTPException(status_code=404, detail="Draft not found. Start a new email.")

    status = gmail_status()
    if not status.get("configured"):
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")
    if not status.get("connected"):
        raise HTTPException(status_code=401, detail="Connect a Gmail account before sending.")

    valid, invalid = _parse_recipients(request.recipients)
    suppressed_now = suppressed_addresses()
    blocked = [email for email in valid if email.lower() in suppressed_now]
    sendable = [email for email in valid if email.lower() not in suppressed_now]
    if not sendable:
        raise HTTPException(status_code=400, detail="There are no sendable recipients.")

    draft = persisted_draft

    try:
        result = send_campaign(
            subject=draft.get("subject", ""),
            preheader=draft.get("preheader", ""),
            html=draft.get("html", ""),
            recipients=sendable,
            base_url=_base_url(http_request),
        )
    except GmailNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gmail send failed: {exc}") from exc

    result["invalid"] = invalid
    result["suppressed_before_send"] = blocked
    result["counts"]["invalid"] = len(invalid)
    result["counts"]["suppressed_before_send"] = len(blocked)
    return result
