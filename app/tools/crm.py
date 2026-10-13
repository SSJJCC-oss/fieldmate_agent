"""STEP 2 — Tools: the agent's hands.

A tool is just a normal Python function. The agent cannot touch the database
directly; it can only *ask* to run one of these. ADK turns each function into
a description the model reads:

    name       -> what it's called            (find_contact)
    arguments  -> what it needs, with types   (name: str, phone: str = "")
    docstring  -> WHEN and WHY to use it      (the most important part!)

So the docstrings below are written for the model, not just for humans.
Every tool returns a small dict, so the model can read the result and decide
its next step (that's the "check" part of the agent loop).
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import func, select

from ..db import (
    DEAL_STAGES, Appliance, Contact, Deal, Interaction, Property, SessionLocal, Task, utcnow,
)
from .audit import audited


# ---------- helpers (not tools) ----------

def normalise_phone(phone: str) -> str:
    """Irish-friendly E.164: '087 123 4567' -> '+353871234567'."""
    digits = re.sub(r"[^\d+]", "", phone or "")
    if not digits:
        return ""
    if digits.startswith("+"):
        return digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    if digits.startswith("0"):
        return "+353" + digits[1:]
    return "+" + digits


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _contact_dict(c: Contact) -> dict:
    return {
        "contact_id": c.id,
        "name": c.name,
        "phone": c.phone_e164,
        "opted_in": c.opted_in,
        "properties": [
            {"property_id": p.id, "address": p.address} for p in c.properties
        ],
    }


# ---------- intake tools ----------

@audited("intake")
def find_contact(name: str = "", phone: str = "", address: str = "") -> dict:
    """Search for an existing customer BEFORE creating a new one.

    Always call this first when a message mentions a person, so you never
    create a duplicate. Matches on phone number (most reliable), then on name,
    then on address. Partial names work ("Mary" finds "Mary Byrne").

    Args:
        name: The person's name, or part of it.
        phone: Their phone number in any format.
        address: Their address, or part of it (e.g. "Ash Grove").

    Returns:
        {"matches": [ ...up to 5 contacts with their properties... ]}.
        An empty list means nobody matched, so it's safe to create_contact.
        More than one match means you should ask the owner which one.
    """
    with SessionLocal() as db:
        query = select(Contact).where(Contact.deleted_at.is_(None))
        candidates = db.scalars(query).all()
        phone_n = normalise_phone(phone)
        name_n, addr_n = _norm(name), _norm(address)
        matches = []
        for c in candidates:
            if phone_n and c.phone_e164 == phone_n:
                return {"matches": [_contact_dict(c)]}  # phone is unique enough
            score = 0
            if name_n and name_n in _norm(c.name):
                score += 2
            if addr_n and any(addr_n in _norm(p.address) for p in c.properties):
                score += 1
            if score:
                matches.append((score, c))
        matches.sort(key=lambda pair: -pair[0])
        return {"matches": [_contact_dict(c) for _, c in matches[:5]]}


@audited("intake")
def create_contact(
    name: str,
    phone: str = "",
    address: str = "",
    referred_by_contact_id: int = 0,
    source: str = "text",
) -> dict:
    """Create a new customer (and their property, if an address is known).

    Only call this after find_contact returned no match. Never invent a name,
    phone number or address: leave a field empty if it wasn't given.

    Args:
        name: Full name as given.
        phone: Phone number if given, else empty.
        address: Address of the property where the work is, if given.
        referred_by_contact_id: contact_id of whoever referred them, or 0.
        source: How we heard: "text", "voice" or "photo".

    Returns:
        {"contact_id": ..., "property_id": ... or None}
    """
    with SessionLocal() as db:
        c = Contact(
            name=name.strip(),
            phone_e164=normalise_phone(phone) or None,
            referred_by_id=referred_by_contact_id or None,
            source=source,
        )
        db.add(c)
        db.flush()
        property_id = None
        if address.strip():
            p = Property(contact_id=c.id, address=address.strip())
            db.add(p)
            db.flush()
            property_id = p.id
        db.commit()
        return {"status": "created", "contact_id": c.id, "property_id": property_id}


@audited("intake")
def add_property(contact_id: int, address: str) -> dict:
    """Add another address (property) to an existing customer.

    Args:
        contact_id: The customer it belongs to.
        address: The full address.

    Returns:
        {"property_id": ...}
    """
    with SessionLocal() as db:
        p = Property(contact_id=contact_id, address=address.strip())
        db.add(p)
        db.commit()
        return {"status": "created", "property_id": p.id}


@audited("intake")
def create_deal(contact_id: int, title: str, property_id: int = 0, value_eur: float = 0.0) -> dict:
    """Open a new job (deal) for a customer, starting at stage "enquiry".

    Call this when a customer asks for new work. One deal per piece of work.

    Args:
        contact_id: The customer.
        title: Short description, e.g. "Boiler losing pressure".
        property_id: Where the work is, or 0 if unknown.
        value_eur: Quoted price if one was given, else 0.

    Returns:
        {"deal_id": ..., "stage": "enquiry"}
    """
    with SessionLocal() as db:
        d = Deal(
            contact_id=contact_id,
            property_id=property_id or None,
            title=title.strip(),
            value_eur=value_eur or None,
        )
        db.add(d)
        db.commit()
        return {"status": "created", "deal_id": d.id, "stage": d.stage}


@audited("intake")
def move_deal_stage(deal_id: int, new_stage: str, value_eur: float = 0.0) -> dict:
    """Move a job to a new stage of the pipeline.

    Stages, in order: enquiry, visit_booked, quote_sent, won, done,
    invoiced, paid, lost. Use "quote_sent" together with value_eur when the
    owner says they gave a price.

    Args:
        deal_id: The job to move.
        new_stage: One of the stages listed above.
        value_eur: The quoted or final price, if the owner just gave one.

    Returns:
        {"deal_id": ..., "old_stage": ..., "new_stage": ...} or an error.
    """
    if new_stage not in DEAL_STAGES:
        return {"status": "error", "message": f"Unknown stage. Use one of {DEAL_STAGES}"}
    with SessionLocal() as db:
        d = db.get(Deal, deal_id)
        if d is None:
            return {"status": "error", "message": f"No deal with id {deal_id}"}
        old = d.stage
        d.stage = new_stage
        d.stage_changed_at = utcnow()
        if value_eur:
            d.value_eur = value_eur
        db.commit()
        return {"status": "updated", "deal_id": d.id, "old_stage": old, "new_stage": new_stage}


@audited("intake")
def log_interaction(contact_id: int, kind: str, summary: str, deal_id: int = 0) -> dict:
    """Record something that happened with a customer (the CRM's diary).

    Call this for every meaningful event: a call, a site visit, a message,
    a photo received. Keep the summary to one or two factual sentences.

    Args:
        contact_id: The customer.
        kind: One of "message", "call", "visit", "photo", "note".
        summary: What happened, in plain words.
        deal_id: The related job, or 0.

    Returns:
        {"interaction_id": ...}
    """
    with SessionLocal() as db:
        i = Interaction(contact_id=contact_id, deal_id=deal_id or None, kind=kind, summary=summary)
        db.add(i)
        db.commit()
        return {"status": "logged", "interaction_id": i.id}


@audited("intake")
def add_task(description: str, due_in_hours: float, contact_id: int = 0, deal_id: int = 0) -> dict:
    """Create a reminder for the owner.

    Args:
        description: What needs doing, e.g. "Invoice Aoife Kelly".
        due_in_hours: How many hours from now it is due (e.g. 8 for "at 5pm"
            when it's 9am now). Use 0 for "as soon as possible".
        contact_id: Related customer, or 0.
        deal_id: Related job, or 0.

    Returns:
        {"task_id": ..., "due_at": ...}
    """
    with SessionLocal() as db:
        t = Task(
            description=description,
            due_at=utcnow() + dt.timedelta(hours=due_in_hours),
            contact_id=contact_id or None,
            deal_id=deal_id or None,
        )
        db.add(t)
        db.commit()
        return {"status": "created", "task_id": t.id, "due_at": t.due_at.isoformat(timespec="minutes")}


@audited("intake")
def get_customer_history(contact_id: int) -> dict:
    """Everything we know about one customer: properties, appliances, jobs
    and the 20 most recent interactions. Use it to answer questions like
    "what did I do at Oakview last year?" or before writing a follow-up.

    Args:
        contact_id: The customer.

    Returns:
        A dict with "contact", "properties" (with appliances), "deals" and
        "interactions" (newest first).
    """
    with SessionLocal() as db:
        c = db.get(Contact, contact_id)
        if c is None or c.deleted_at:
            return {"status": "error", "message": f"No contact with id {contact_id}"}
        deals = db.scalars(select(Deal).where(Deal.contact_id == contact_id)).all()
        interactions = db.scalars(
            select(Interaction)
            .where(Interaction.contact_id == contact_id)
            .order_by(Interaction.at.desc())
            .limit(20)
        ).all()
        return {
            "contact": {"contact_id": c.id, "name": c.name, "phone": c.phone_e164, "opted_in": c.opted_in},
            "properties": [
                {
                    "property_id": p.id,
                    "address": p.address,
                    "appliances": [
                        {
                            "kind": a.kind, "make": a.make, "model": a.model,
                            "install_year": a.install_year,
                            "last_service_date": str(a.last_service_date or ""),
                        }
                        for a in p.appliances
                    ],
                }
                for p in c.properties
            ],
            "deals": [
                {
                    "deal_id": d.id, "title": d.title, "stage": d.stage,
                    "value_eur": d.value_eur, "parts_cost_eur": d.parts_cost_eur,
                    "created": d.created_at.date().isoformat(),
                }
                for d in deals
            ],
            "interactions": [
                {"date": i.at.date().isoformat(), "kind": i.kind, "summary": i.summary}
                for i in interactions
            ],
        }


@audited("intake")
def opt_out_contact(contact_id: int) -> dict:
    """Stop all reminders and marketing to this customer, immediately.

    Call this as soon as the owner says a customer asked not to be contacted.

    Args:
        contact_id: The customer.

    Returns:
        {"status": "opted_out"}
    """
    with SessionLocal() as db:
        c = db.get(Contact, contact_id)
        if c is None:
            return {"status": "error", "message": f"No contact with id {contact_id}"}
        c.opted_in = False
        db.commit()
        return {"status": "opted_out", "contact_id": contact_id}


# ---------- tools used by the vision agent to SAVE what it read ----------

@audited("vision")
def save_appliance(
    property_id: int,
    make: str,
    model: str = "",
    serial: str = "",
    install_year: int = 0,
    kind: str = "boiler",
) -> dict:
    """Save an appliance (boiler, heat pump, cylinder...) read from a data-plate
    photo onto a customer's property.

    Only call this with fields whose confidence was high enough. Never save a
    value you had to guess.

    Args:
        property_id: The property where the appliance is installed.
        make: Manufacturer, e.g. "Ideal".
        model: Model name, e.g. "Logic Combi 30".
        serial: Serial number, if readable.
        install_year: Year of manufacture/installation, or 0 if unknown.
        kind: "boiler", "heat_pump", "cylinder", etc.

    Returns:
        {"appliance_id": ...}
    """
    with SessionLocal() as db:
        if db.get(Property, property_id) is None:
            return {"status": "error", "message": f"No property with id {property_id}"}
        a = Appliance(
            property_id=property_id, make=make, model=model or None,
            serial=serial or None, install_year=install_year or None, kind=kind,
        )
        db.add(a)
        db.commit()
        return {"status": "saved", "appliance_id": a.id}


@audited("vision")
def add_parts_cost(deal_id: int, amount_eur: float, description: str = "") -> dict:
    """Add the cost of parts (read from a supplier receipt) to a job, so it
    ends up on the customer's invoice.

    Args:
        deal_id: The job the parts were bought for.
        amount_eur: Total from the receipt.
        description: Short list of what was bought.

    Returns:
        {"deal_id": ..., "parts_cost_eur": new total}
    """
    with SessionLocal() as db:
        d = db.get(Deal, deal_id)
        if d is None:
            return {"status": "error", "message": f"No deal with id {deal_id}"}
        d.parts_cost_eur = round((d.parts_cost_eur or 0) + amount_eur, 2)
        db.add(Interaction(contact_id=d.contact_id, deal_id=d.id, kind="note",
                           summary=f"Parts €{amount_eur:.2f}: {description}".strip()))
        db.commit()
        return {"status": "updated", "deal_id": d.id, "parts_cost_eur": d.parts_cost_eur}


@audited("intake")
def list_open_deals(contact_id: int = 0) -> dict:
    """List jobs that are not finished (not paid or lost), optionally for one
    customer. Use it to find the deal_id the owner is talking about.

    Args:
        contact_id: Limit to one customer, or 0 for everyone.

    Returns:
        {"deals": [{deal_id, contact, title, stage, value_eur}]}
    """
    with SessionLocal() as db:
        q = select(Deal, Contact.name).join(Contact).where(Deal.stage.not_in(["paid", "lost"]))
        if contact_id:
            q = q.where(Deal.contact_id == contact_id)
        rows = db.execute(q.order_by(Deal.created_at.desc()).limit(30)).all()
        return {
            "deals": [
                {"deal_id": d.id, "contact": name, "title": d.title, "stage": d.stage, "value_eur": d.value_eur}
                for d, name in rows
            ]
        }


def count_contacts() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count(Contact.id))) or 0
