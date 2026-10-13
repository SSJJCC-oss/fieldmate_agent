"""Talking to Twilio (WhatsApp). Plain code, no AI.

- validate_request: proves a webhook call really came from Twilio.
- download_media:   fetches photos / voice notes Twilio received.
- send_whatsapp:    sends an APPROVED message to a customer.
"""
from __future__ import annotations

import logging
import mimetypes
import uuid
from pathlib import Path

import requests
from twilio.request_validator import RequestValidator

from ..config import settings

logger = logging.getLogger("fieldmate.twilio")


def validate_request(url: str, params: dict, signature: str) -> bool:
    if not settings.validate_twilio_signature:
        return True
    return RequestValidator(settings.twilio_auth_token).validate(url, params, signature)


def download_media(media_url: str, content_type: str) -> str:
    """Saves the file into uploads/ and returns its path."""
    ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".bin"
    path = Path(settings.upload_dir) / f"{uuid.uuid4().hex}{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(media_url, auth=(settings.twilio_account_sid, settings.twilio_auth_token), timeout=30)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return str(path)


def send_whatsapp(to_phone_e164: str, body: str, to_owner: bool = False) -> dict:
    """DRY_RUN only protects CUSTOMERS. Replies to the owner always go out,
    otherwise you couldn't test in the WhatsApp Sandbox."""
    if settings.dry_run and not to_owner:
        logger.info("[DRY RUN] would send to %s: %s", to_phone_e164, body)
        print(f"\n   📤 [DRY RUN] WhatsApp to {to_phone_e164}:\n   {body}\n")
        return {"status": "dry_run"}
    from twilio.rest import Client

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    msg = client.messages.create(
        from_=settings.twilio_whatsapp_number, to=f"whatsapp:{to_phone_e164}", body=body
    )
    return {"status": "sent", "sid": msg.sid}
