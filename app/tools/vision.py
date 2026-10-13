"""Tools for the VISION agent — the computer-vision part of FieldMate.

Version 1 uses "Approach B" from the design: send the photo to a
vision-language model (Gemini) and force it to answer in a fixed JSON shape,
with a confidence score for every field.

Phase 4 of the implementation guide adds "Approach A" (YOLOv8 detector +
OpenCV clean-up + OCR) behind the SAME function signature, so you can compare
the two on a labelled test set without touching the agent at all. That clean
seam — agent on top, swappable vision pipeline underneath — is the point.
"""
from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import settings
from ..db import Document, SessionLocal
from .audit import audited


class ExtractedField(BaseModel):
    name: str = Field(description="Field name, e.g. make, model, serial, install_year, supplier, date, total_eur, line_items, customer, price")
    value: str = Field(description="Exactly what is written. Empty string if unreadable.")
    confidence: float = Field(description="0.0-1.0: how sure you are this value is read correctly")


class Extraction(BaseModel):
    doc_type: str = Field(description="One of: data_plate, receipt, handwritten_note, site_photo, other")
    fields: list[ExtractedField]
    caption: str = Field(description="One short sentence describing the image")


PROMPT = """You read photos sent by a plumber / heating engineer.
1. Decide the doc_type: data_plate (appliance rating plate), receipt
   (supplier receipt), handwritten_note, site_photo (a job in progress,
   no text to read) or other.
2. Extract fields:
   - data_plate: make, model, serial, install_year (year of manufacture), fuel
   - receipt: supplier, date (YYYY-MM-DD), total_eur, line_items (short list)
   - handwritten_note: customer, address, work, price_eur
   - site_photo / other: no fields, just a caption
3. Give each field an honest confidence. If glare, blur or angle makes a
   character uncertain, LOWER the confidence. Never guess a value to fill a
   field: return an empty value with confidence 0 instead.
"""


def _safe_path(image_path: str) -> Path:
    """Only allow files inside the uploads folder (the model chooses the path,
    so we must not let it read arbitrary files on the server)."""
    root = Path(settings.upload_dir).resolve()
    p = (root / Path(image_path).name).resolve() if not Path(image_path).is_absolute() else Path(image_path).resolve()
    if root not in p.parents and p != root:
        raise ValueError("Image must be inside the uploads folder")
    return p


def _call_gemini(path: Path) -> Extraction:
    from google import genai
    from google.genai import types

    client = genai.Client()  # reads GOOGLE_API_KEY from the environment
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    response = client.models.generate_content(
        model=settings.model,
        contents=[types.Part.from_bytes(data=path.read_bytes(), mime_type=mime), PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Extraction,
            temperature=0,
        ),
    )
    return Extraction.model_validate_json(response.text)


@audited("vision")
def read_photo(image_path: str) -> dict:
    """Read a photo the owner sent and return structured fields with a
    confidence score for each one.

    Call this for every message that contains "[photo: <path>]". Then:
    - fields with "trusted": true can be saved with save_appliance /
      add_parts_cost;
    - fields listed in "ask_owner" must NOT be saved — ask the owner to
      confirm them instead.

    Args:
        image_path: The path exactly as it appeared in "[photo: ...]".

    Returns:
        {"document_id", "doc_type", "caption", "fields": [{name, value,
        confidence, trusted}], "ask_owner": [names of uncertain fields]}
    """
    try:
        path = _safe_path(image_path)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    if not path.exists():
        return {"status": "error", "message": f"No such image: {image_path}"}
    if os.environ.get("FIELDMATE_FAKE_VISION") == "true":  # for offline tests
        extraction = Extraction(doc_type="data_plate", caption="Boiler data plate",
                                fields=[ExtractedField(name="make", value="Ideal", confidence=0.97),
                                        ExtractedField(name="install_year", value="2012", confidence=0.55)])
    else:
        extraction = _call_gemini(path)

    threshold = settings.vision_min_confidence
    fields = [
        {"name": f.name, "value": f.value, "confidence": round(f.confidence, 2),
         "trusted": bool(f.value) and f.confidence >= threshold}
        for f in extraction.fields
    ]
    with SessionLocal() as db:
        doc = Document(
            image_path=str(path), doc_type=extraction.doc_type,
            fields_json=json.dumps(fields),
            min_confidence=min([f["confidence"] for f in fields], default=1.0),
        )
        db.add(doc)
        db.commit()
        doc_id = doc.id
    return {
        "status": "ok",
        "document_id": doc_id,
        "doc_type": extraction.doc_type,
        "caption": extraction.caption,
        "fields": fields,
        "ask_owner": [f["name"] for f in fields if not f["trusted"]],
    }


@audited("vision")
def link_document(document_id: int, contact_id: int = 0, deal_id: int = 0) -> dict:
    """Attach a photo you have read to a customer and/or a job, so it shows
    up in their history (e.g. for the invoice, or for tax time).

    Args:
        document_id: From read_photo.
        contact_id: The customer it belongs to, or 0.
        deal_id: The job it belongs to, or 0.

    Returns:
        {"status": "linked"}
    """
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if doc is None:
            return {"status": "error", "message": f"No document {document_id}"}
        doc.contact_id = contact_id or doc.contact_id
        doc.deal_id = deal_id or doc.deal_id
        db.commit()
        return {"status": "linked", "document_id": document_id}


def transcribe_audio(path: str) -> str:
    """Voice note -> text. Not a tool: the pipeline calls it BEFORE the agents
    run, so to the agents a voice note looks exactly like a typed message."""
    from google import genai
    from google.genai import types

    p = Path(path)
    client = genai.Client()
    mime = mimetypes.guess_type(p.name)[0] or "audio/ogg"
    response = client.models.generate_content(
        model=settings.model,
        contents=[types.Part.from_bytes(data=p.read_bytes(), mime_type=mime),
                  "Transcribe this voice note word for word. Irish names and places are likely."],
    )
    return (response.text or "").strip()
