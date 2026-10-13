"""Talk to FieldMate in your terminal — no WhatsApp, no Twilio needed.

    python -m scripts.chat            # normal
    python -m scripts.chat --trace    # also shows every agent and tool call

Commands inside the chat:
    /photo path/to/image.jpg  your caption    -> sends a photo (copied into uploads/)
    approve 3 / reject 3 / drafts             -> the approval flow
    quit
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import shutil
import sys
import uuid
from pathlib import Path

from app.config import settings
from app.pipeline import handle_owner_message

OWNER = settings.owner_numbers[0] if settings.owner_numbers else "+353870000000"
TRACE = "--trace" in sys.argv


def show_event(event) -> None:
    """Print each step of the agent loop: DECIDE (function call) and CHECK (result)."""
    if not event.content or not event.content.parts:
        return
    for part in event.content.parts:
        if part.function_call:
            args = dict(part.function_call.args or {})
            print(f"   🧠 {event.author} → calls {part.function_call.name}({args})")
        elif part.function_response:
            resp = str(part.function_response.response)
            print(f"   🔧 {part.function_response.name} returned {resp[:200]}{'…' if len(resp) > 200 else ''}")


async def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    if TRACE:
        # The specialists' own tool calls happen inside the router's call, so we
        # show them via the audit log, which records every tool call.
        logging.getLogger("fieldmate.audit").setLevel(logging.INFO)
    print(f"FieldMate terminal chat — you are the owner ({OWNER}). Type 'quit' to exit.\n")
    while True:
        try:
            line = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.lower() in {"quit", "exit"}:
            break
        media = []
        if line.startswith("/photo "):
            _, src, *caption = line.split(" ", 2)
            src_path = Path(src).expanduser()
            if not src_path.exists():
                print("   (no such file)")
                continue
            dest = Path(settings.upload_dir) / f"{uuid.uuid4().hex}{src_path.suffix}"
            dest.parent.mkdir(exist_ok=True)
            shutil.copy(src_path, dest)
            media = [(str(dest), mimetypes.guess_type(dest.name)[0] or "image/jpeg")]
            line = caption[0] if caption else ""
        reply = await handle_owner_message(OWNER, line, media, on_event=show_event if TRACE else None)
        print(f"FieldMate: {reply}\n")


if __name__ == "__main__":
    asyncio.run(main())
