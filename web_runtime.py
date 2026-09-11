"""Small bridge between FastAPI and the resumable ADK application.

The agent workflow stays in agent.py. This module only starts that workflow,
returns control to the browser when request_input pauses it, and resumes the
same workflow when the browser sends the user's feedback.
"""

import json
import re
import uuid

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent import app

_MARKDOWN_HTML_FENCE = re.compile(
    r"^\s*```(?:html|HTML)?\s*\r?\n?(.*?)\r?\n?```\s*$",
    re.DOTALL,
)


def strip_markdown_html_fence(html: str) -> str:
    """Remove a wrapping markdown code fence from model-produced HTML."""
    text = str(html or "")
    match = _MARKDOWN_HTML_FENCE.match(text)
    if match:
        return match.group(1).strip()

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:html|HTML)?\s*\r?\n?", "", stripped, count=1)
        stripped = re.sub(r"\r?\n?```\s*$", "", stripped)
        return stripped.strip()
    return text


USER_ID = "web-user"

# Keep one session service and one runner alive for the web application.
# InMemorySessionService is intentionally simple for local development. A
# persistent session service can replace it later for multi-instance Cloud Run.
session_service = InMemorySessionService()
runner = Runner(app=app, session_service=session_service)


def _request_input_call_id(event) -> str | None:
    """Return the request_input function-call ID from an ADK event, if present."""
    if not event.content or not event.content.parts:
        return None

    for part in event.content.parts:
        function_call = getattr(part, "function_call", None)
        if function_call and function_call.name == "adk_request_input":
            return function_call.id
    return None


def _metadata_from_state(value) -> dict[str, str]:
    """Convert the metadata agent's JSON output into subject/preheader fields."""
    if isinstance(value, dict):
        data = value
    else:
        try:
            data = json.loads(str(value or "{}"))
        except json.JSONDecodeError:
            data = {}

    return {
        "subject": str(data.get("subject", "")).strip(),
        "preheader": str(data.get("preheader", "")).strip(),
    }


async def _current_draft(session_id: str) -> dict[str, str]:
    """Read the current email and metadata from ADK session state."""
    session = await session_service.get_session(
        app_name=app.name,
        user_id=USER_ID,
        session_id=session_id,
    )
    if session is None:
        raise RuntimeError("The email workflow session could not be found.")

    metadata = _metadata_from_state(session.state.get("email_metadata"))
    return {
        "html": strip_markdown_html_fence(str(session.state.get("email_html", ""))),
        **metadata,
    }


async def generate_email(request: str) -> dict[str, str | bool | None]:
    """Start the ADK workflow and run until it pauses for human review."""
    session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name=app.name,
        user_id=USER_ID,
        session_id=session_id,
    )

    function_call_id = None
    message = types.Content(role="user", parts=[types.Part(text=request)])

    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=message,
    ):
        request_input_id = _request_input_call_id(event)
        if request_input_id:
            function_call_id = request_input_id

    if not function_call_id:
        raise RuntimeError("The email workflow completed without pausing for review.")

    draft = await _current_draft(session_id)
    return {
        **draft,
        "session_id": session_id,
        "function_call_id": function_call_id,
        "approved": False,
    }


async def revise_email(
    session_id: str,
    function_call_id: str,
    feedback: str,
    html: str,
    subject: str,
    preheader: str,
) -> dict[str, str | bool | None]:
    """Resume the paused workflow with the human's review response.

    The state_delta keeps any manual edits made in the browser in sync with the
    ADK session before the workflow continues.
    """
    response = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=function_call_id,
                    name="adk_request_input",
                    response={"result": feedback},
                )
            )
        ],
    )

    current_metadata = json.dumps({"subject": subject, "preheader": preheader})
    next_function_call_id = None

    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=response,
        state_delta={
            "email_html": strip_markdown_html_fence(html),
            "email_metadata": current_metadata,
        },
    ):
        request_input_id = _request_input_call_id(event)
        if request_input_id:
            next_function_call_id = request_input_id

    draft = await _current_draft(session_id)
    approved = feedback.strip().lower() == "approve" and next_function_call_id is None

    if not approved and next_function_call_id is None:
        raise RuntimeError("The email workflow did not return to the review step.")

    return {
        **draft,
        "session_id": session_id,
        "function_call_id": next_function_call_id,
        "approved": approved,
    }
