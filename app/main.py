"""STEP 5 — The web server (FastAPI): WhatsApp's front door.

Run locally:   uvicorn app.main:app --reload --port 8080
Expose it:     ngrok http 8080
Twilio:        Sandbox setting "When a message comes in" = https://<ngrok>/twilio/whatsapp

Why reply in the BACKGROUND?
Twilio waits only ~15 seconds for an answer. A router calling two specialists
that each call several tools can take longer. So we answer Twilio "OK"
straight away (empty TwiML) and send the real reply a few seconds later via
Twilio's REST API.
"""
from __future__ import annotations

import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response

from .channels.twilio_io import download_media, send_whatsapp, validate_request
from .config import settings
from .pipeline import handle_owner_message, run_morning_briefing

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("fieldmate.main")

app = FastAPI(title="FieldMate")
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _public_url(request: Request) -> str:
    # Behind ngrok / Cloud Run the app sees http://, but Twilio signed https://.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return str(request.url.replace(scheme=proto))


async def _process_and_reply(phone: str, body: str, media_items: list[tuple[str, str]]) -> None:
    media = []
    for url, ctype in media_items:
        try:
            media.append((download_media(url, ctype), ctype))
        except Exception:
            logger.exception("Could not download media %s", url)
    reply = await handle_owner_message(phone, body, media)
    send_whatsapp(phone, reply, to_owner=True)


@app.post("/twilio/whatsapp")
async def whatsapp_webhook(request: Request, background: BackgroundTasks):
    form = await request.form()
    params = {k: v for k, v in form.items()}

    # 1. Is this really Twilio? (Otherwise anyone could post fake messages.)
    if not validate_request(_public_url(request), params, request.headers.get("X-Twilio-Signature", "")):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    # 2. WHO is this? Identity comes from Twilio, never from the AI.
    phone = params.get("From", "").removeprefix("whatsapp:").strip()
    if phone not in settings.owner_numbers:
        logger.warning("Message from non-owner %s ignored", phone)
        return Response(content=EMPTY_TWIML, media_type="application/xml")

    # 3. WHAT did they send? Text in Body, attachments in MediaUrl0..N.
    body = params.get("Body", "")
    num_media = int(params.get("NumMedia", "0") or 0)
    media_items = [(params[f"MediaUrl{i}"], params.get(f"MediaContentType{i}", "")) for i in range(num_media)]
    logger.info("From %s: %r (+%d media)", phone, body, num_media)

    # 4. Hand off to the pipeline and answer Twilio immediately.
    background.add_task(_process_and_reply, phone, body, media_items)
    return Response(content=EMPTY_TWIML, media_type="application/xml")


@app.post("/jobs/morning-briefing")
async def morning_briefing(x_jobs_token: str = Header(default="")):
    """Called by a scheduler (e.g. Cloud Scheduler) at 7am. Token-protected."""
    if x_jobs_token != settings.jobs_token:
        raise HTTPException(status_code=401, detail="Bad token")
    results = {}
    for owner in settings.owner_numbers:
        text = await run_morning_briefing(owner)
        send_whatsapp(owner, text, to_owner=True)
        results[owner] = text
    return {"status": "ok", "briefings": results}


@app.get("/health")
async def health():
    return {"status": "ok"}
