# Serenity Blooms Email Studio — Local Web App

This FastAPI layer intentionally keeps the existing `agent.py` CLI workflow intact.
The browser becomes the human-in-the-loop controller for the web application while
`web_runtime.py` reuses the existing generation and revision agent instructions.

## What this first build includes

- Initial prompt-driven email generation
- Rendered HTML email preview
- Editable subject and preheader
- Editable raw HTML with live preview
- Conversational revisions using the existing revision-agent behavior
- Explicit **New Email** and **Save Draft** controls
- Distribution-list parsing and validation
- JSON-backed unsubscribe/suppression list in `data/unsubscribes.json`
- Local asset serving through `/assets/images/...` and `/assets/logos/...`
- A configurable `EMAIL_ASSET_BASE_URL` hook in `tools.py` for later Cloud Storage URLs

The **Approve & Send** control is present but deliberately disabled until Gmail OAuth
is added. Sending is kept outside the agents so a chat message can never authorize an
email send.

## Install

From the project directory and your existing virtual environment:

```bash
pip install -r requirements.txt
```

Keep using the same Gemini / Google Cloud authentication configuration that already
works with `adk run .`.

## Run locally

From the `marketing_email_generator` directory:

```bash
uvicorn web_app:app --reload
```

Then open:

```text
http://127.0.0.1:8000
```

## Asset behavior

`tools.py` still returns local filesystem paths when `EMAIL_ASSET_BASE_URL` is unset,
which preserves the existing CLI behavior. `web_app.py` sets it to `/assets`, so email
HTML generated through the web app uses URLs the browser can render.

Later, the environment variable can point at the public Cloud Storage asset base URL
without changing the agents or the storehouse profiles.

## Unsubscribes

The suppression list is intentionally simple for the current business scale:

```text
data/unsubscribes.json
```

`POST /api/unsubscribe` can add an address, and `/api/recipients/validate` automatically
removes suppressed addresses from the sendable set. The later Gmail send endpoint should
reuse that same validation before any message is transmitted.
