"""Thin programmatic runtime around the existing ADK agents for the FastAPI UI.

The CLI workflow in agent.py remains unchanged.  The browser owns the human pause,
so the web application invokes fresh generation/revision agent instances directly.
"""

import json
import re
import uuid
from typing import Any

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent import GEMINI_MODEL, email_generation_agent, revision_agent
from tools import (
    get_block,
    get_brand,
    get_image,
    get_logo,
    get_profile,
    list_block_types_subtypes,
    list_images,
    list_logos,
    list_profiles,
    render_block,
    stitch_email,
)

APP_NAME = "serenity_blooms_email_studio"
USER_ID = "web-user"


def _generation_agent() -> Agent:
    """Create an unattached copy of the existing generation agent definition."""
    return Agent(
        name="web_email_generation_agent",
        model=GEMINI_MODEL,
        description=email_generation_agent.description,
        instruction=email_generation_agent.instruction,
        tools=[
            list_profiles,
            get_profile,
            list_block_types_subtypes,
            get_block,
            get_image,
            get_logo,
            list_images,
            list_logos,
            get_brand,
            render_block,
            stitch_email,
        ],
        output_key="email_html",
    )


def _revision_agent() -> Agent:
    """Create an unattached copy of the existing revision agent definition."""
    return Agent(
        name="web_revision_agent",
        model=GEMINI_MODEL,
        description=revision_agent.description,
        instruction=revision_agent.instruction,
        output_key="email_html",
    )


def _metadata_agent() -> Agent:
    return Agent(
        name="email_metadata_agent",
        model=GEMINI_MODEL,
        description="Creates or revises the subject and preheader for an email draft.",
        instruction="""
        You write email subject lines and preheaders for Serenity Blooms.

        Return ONLY a JSON object with exactly these keys:
        {"subject": "...", "preheader": "..."}

        Keep both warm, personal, locally grounded, and consistent with the email.
        Do not invent prices, discounts, deadlines, locations, guarantees, or other
        business facts that were not supplied.

        When CURRENT SUBJECT and CURRENT PREHEADER are supplied, preserve them unless
        USER FEEDBACK explicitly asks to change one of them, or the revised HTML makes
        one of them materially inaccurate.  If there is no current metadata, create it.

        Keep the subject concise.  Keep the preheader useful and complementary to the
        subject rather than merely repeating it.
        """,
    )


async def _run_agent(agent: Agent, message: str, state: dict[str, Any] | None = None) -> str:
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=str(uuid.uuid4()),
        state=state or {},
    )
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=session_service)
    final_text = ""

    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            texts = [part.text for part in event.content.parts if getattr(part, "text", None)]
            if texts:
                final_text = "".join(texts).strip()

    close = getattr(session_service, "close", None)
    if callable(close):
        close()

    if not final_text:
        raise RuntimeError("The agent completed without returning a text response.")
    return final_text


def _strip_code_fence(value: str) -> str:
    value = value.strip()
    fenced = re.match(r"^```(?:html|json)?\s*(.*?)\s*```$", value, flags=re.I | re.S)
    return fenced.group(1).strip() if fenced else value


def _parse_metadata(value: str) -> dict[str, str]:
    value = _strip_code_fence(value)
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", value, flags=re.S)
        if not match:
            raise ValueError("Metadata agent did not return valid JSON.")
        data = json.loads(match.group(0))

    return {
        "subject": str(data.get("subject", "")).strip(),
        "preheader": str(data.get("preheader", "")).strip(),
    }


async def generate_email(request: str) -> dict[str, str]:
    html = _strip_code_fence(await _run_agent(_generation_agent(), request))
    metadata_prompt = f"""ORIGINAL USER REQUEST:\n{request}\n\nEMAIL HTML:\n{html}"""
    metadata = _parse_metadata(await _run_agent(_metadata_agent(), metadata_prompt))
    return {"html": html, **metadata}


async def revise_email(
    html: str,
    feedback: str,
    subject: str,
    preheader: str,
) -> dict[str, str]:
    revised_html = _strip_code_fence(
        await _run_agent(
            _revision_agent(),
            "Apply the requested revision.",
            state={"email_html": html, "feedback": feedback},
        )
    )

    metadata_prompt = f"""
CURRENT SUBJECT:\n{subject}\n\nCURRENT PREHEADER:\n{preheader}\n
USER FEEDBACK:\n{feedback}\n\nREVISED EMAIL HTML:\n{revised_html}
"""
    metadata = _parse_metadata(await _run_agent(_metadata_agent(), metadata_prompt))
    return {"html": revised_html, **metadata}
