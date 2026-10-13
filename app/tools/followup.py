"""Tools for the FOLLOW-UP agent.

The most important design decision in the whole project is visible here by
what is MISSING: there is no `send_message` tool. The follow-up agent can only
put a draft in a queue. Sending happens in plain code (app/pipeline.py) after
the owner types "approve <id>". Safety comes from which tools exist, not from
asking the model nicely.
"""
from __future__ import annotations

from ..db import Contact, Draft, SessionLocal
from .audit import audited

PURPOSES = {"quote_chase", "service_reminder", "reply", "win_back", "review_request"}


@audited("followup")
def queue_for_approval(contact_id: int, purpose: str, body: str) -> dict:
    """Put a message to a CUSTOMER into the approval queue. It is NOT sent.

    The owner sees the draft and replies "approve <draft_id>" or
    "reject <draft_id>". Write the body in the owner's voice: short, friendly,
    signed with the owner's first name, and based on real facts from the
    customer's history only.

    Args:
        contact_id: Who the message is for.
        purpose: One of quote_chase, service_reminder, reply, win_back, review_request.
        body: The exact text to send.

    Returns:
        {"draft_id": ..., "status": "pending"} or an error if the customer
        has opted out or has no phone number.
    """
    if purpose not in PURPOSES:
        return {"status": "error", "message": f"purpose must be one of {sorted(PURPOSES)}"}
    with SessionLocal() as db:
        c = db.get(Contact, contact_id)
        if c is None or c.deleted_at:
            return {"status": "error", "message": f"No contact with id {contact_id}"}
        # Opted-out customers may still get a direct reply to their own
        # enquiry, but never reminders or marketing.
        if not c.opted_in and purpose != "reply":
            return {"status": "blocked", "message": f"{c.name} has opted out of messages."}
        if not c.phone_e164:
            return {"status": "blocked", "message": f"{c.name} has no phone number on file."}
        d = Draft(contact_id=contact_id, purpose=purpose, body=body.strip())
        db.add(d)
        db.commit()
        return {"status": "pending", "draft_id": d.id, "to": c.name}
