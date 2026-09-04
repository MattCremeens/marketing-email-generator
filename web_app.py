"""FastAPI application for the Serenity Blooms Email Studio."""

import os
import re
import uuid
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Tools resolve assets to these browser-facing URLs when invoked by an agent.
os.environ.setdefault("EMAIL_ASSET_BASE_URL", "/assets")

from suppression import add_unsubscribe, suppressed_addresses
from web_runtime import generate_email, revise_email

ROOT = Path(__file__).resolve().parent

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


def _save_draft(draft_id: str, draft: dict[str, str]) -> dict[str, str]:
    with _draft_lock:
        _drafts[draft_id] = draft
    return draft


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
    _save_draft(draft_id, draft)
    return {"draft_id": draft_id, **draft}


@app.post("/api/revise")
async def api_revise(request: RevisionRequest):
    if request.draft_id not in _drafts:
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
    _save_draft(request.draft_id, draft)
    return {"draft_id": request.draft_id, **draft}


@app.post("/api/draft/save")
async def api_save_draft(request: SaveRequest):
    if request.draft_id not in _drafts:
        raise HTTPException(status_code=404, detail="Draft not found. Start a new email.")
    draft = {"html": request.html, "subject": request.subject, "preheader": request.preheader}
    _save_draft(request.draft_id, draft)
    return {"status": "saved", "draft_id": request.draft_id}


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
    valid, invalid = _parse_recipients(request.email)
    if invalid or len(valid) != 1:
        raise HTTPException(status_code=400, detail="Enter one valid email address.")
    added = add_unsubscribe(valid[0])
    return {"status": "unsubscribed" if added else "already_unsubscribed"}


@app.post("/api/send")
async def api_send():
    # Intentionally unavailable until Gmail OAuth is configured.  Keeping the route
    # separate ensures agent chat can never send mail by itself.
    raise HTTPException(
        status_code=501,
        detail="Gmail OAuth sending is not connected yet. The draft and recipient workflow is ready for that next layer.",
    )
