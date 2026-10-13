"""STEP 4 — The pipeline: what happens to ONE message, start to finish.

    WhatsApp ──► webhook (main.py) ──► handle_owner_message()  ◄── or scripts/chat.py
                                             │
              (a) approve / reject?  ── plain code, NO AI ── send or discard draft
                                             │ otherwise
              (b) prepare input: photos -> "[photo: path]", voice -> transcript
                                             │
              (c) run the router agent (ADK Runner)
                    router ──► specialist agent(s) ──► tools ──► database
                                             │
              (d) reply text ──► back to the owner

The webhook and the terminal chat both call the same function, so you can
build and test everything without WhatsApp, then plug WhatsApp in.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types

from .agents import root_agent
from .channels.twilio_io import send_whatsapp
from .config import settings
from .db import Contact, Draft, SessionLocal, init_db, utcnow
from .tools.vision import transcribe_audio

logger = logging.getLogger("fieldmate.pipeline")

APP_NAME = "fieldmate"

init_db()
# Conversation memory (what was said earlier in this chat) is stored in a
# database, so it survives restarts. This is separate from the CRM tables.
session_service = DatabaseSessionService(db_url=settings.session_db_url)
runner = Runner(
    agent=root_agent, app_name=APP_NAME, session_service=session_service, auto_create_session=True
)

APPROVAL_RE = re.compile(r"^\s*(approve|reject)\s+#?(\d+)\s*$", re.IGNORECASE)


# ---------- (a) approvals: deterministic, no AI ----------

def list_pending_drafts() -> str:
    with SessionLocal() as db:
        rows = db.query(Draft, Contact.name).join(Contact).filter(Draft.status == "pending").all()
    if not rows:
        return "No drafts waiting."
    return "\n\n".join(f"#{d.id} to {name} ({d.purpose}):\n{d.body}" for d, name in rows)


def handle_approval(action: str, draft_id: int) -> str:
    with SessionLocal() as db:
        draft = db.get(Draft, draft_id)
        if draft is None or draft.status != "pending":
            return f"There's no pending draft #{draft_id}."
        contact = db.get(Contact, draft.contact_id)
        if action == "reject":
            draft.status = "rejected"
            db.commit()
            return f"Draft #{draft_id} discarded."
        # Re-check consent at send time: the customer may have opted out
        # after the draft was written.
        if not contact.opted_in and draft.purpose != "reply":
            draft.status = "rejected"
            db.commit()
            return f"Not sent: {contact.name} has opted out."
        result = send_whatsapp(contact.phone_e164, draft.body)
        draft.status = "sent"
        draft.sent_at = utcnow()
        db.commit()
        return f"✅ Sent to {contact.name}." if result["status"] != "dry_run" else f"✅ Sent to {contact.name} (dry run)."


# ---------- (b)+(c)+(d) everything else goes to the agents ----------

async def handle_owner_message(
    owner_phone: str,
    text: str,
    media: list[tuple[str, str]] | None = None,
    on_event=None,
) -> str:
    """media = list of (local_path, content_type) already saved to uploads/."""
    text = (text or "").strip()

    # (a) The owner approving/rejecting a draft never touches the AI.
    m = APPROVAL_RE.match(text)
    if m:
        return handle_approval(m.group(1).lower(), int(m.group(2)))
    if text.lower() in {"drafts", "pending"}:
        return list_pending_drafts()

    # (b) Turn attachments into something a text model can route.
    parts = []
    for path, content_type in media or []:
        if content_type.startswith("image/"):
            parts.append(f"[photo: {path}]")
        elif content_type.startswith("audio/"):
            transcript = transcribe_audio(path)
            parts.append(f"(voice note) {transcript}")
    if text:
        parts.append(text)
    if not parts:
        return "I didn't get anything there — send a message, photo or voice note."

    now = dt.datetime.now().strftime("%A %d %B %Y, %H:%M")
    message = f"[now: {now}]\n" + "\n".join(parts)
    logger.info("Agent input from %s: %s", owner_phone, message)

    # (c) Run the agent loop. Session id = owner's phone, so each owner has
    # their own ongoing conversation.
    content = types.Content(role="user", parts=[types.Part(text=message)])
    reply = "Sorry, something went wrong on my side. Please try again."
    try:
        async for event in runner.run_async(user_id=owner_phone, session_id=owner_phone, new_message=content):
            # Every tool call and result also flows through here as an event —
            # handy for watching the agent think (see scripts/chat.py --trace).
            if on_event:
                on_event(event)
            if event.is_final_response() and event.content and event.content.parts:
                reply = "".join(p.text or "" for p in event.content.parts).strip() or reply
    except Exception:
        logger.exception("Agent run failed")
    # (d)
    return reply


async def run_morning_briefing(owner_phone: str) -> str:
    return await handle_owner_message(owner_phone, "Give me this morning's briefing.")
