"""STEP 1 — The CRM's memory (the database).

This file has NO AI in it. It defines the tables the CRM keeps, using
SQLAlchemy (Python classes that map to database tables).

    Contact ──< Property ──< Appliance
       │            │
       └──< Deal >──┘          (a job for a contact, at a property)
       │
       ├──< Interaction        (every message, call, visit, photo)
       ├──< Task               (reminders)
       └──< Draft              (messages waiting for the owner's approval)

    Document   — a photo/receipt and the fields read from it
    AuditLog   — every tool call any agent made, and its result
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import settings


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# Pipeline stages a job moves through. The insights agent watches these.
DEAL_STAGES = [
    "enquiry",       # someone asked about work
    "visit_booked",  # going to look at it
    "quote_sent",    # price given, waiting for a yes
    "won",           # customer said yes
    "done",          # work finished
    "invoiced",      # bill sent
    "paid",          # money received
    "lost",          # customer said no / went elsewhere
]


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    phone_e164: Mapped[str | None] = mapped_column(String(20), index=True)
    email: Mapped[str | None] = mapped_column(String(120))
    referred_by_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    opted_in: Mapped[bool] = mapped_column(Boolean, default=True)  # OK to send reminders?
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="text")  # text / voice / photo / seed
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime)  # GDPR erasure

    properties: Mapped[list[Property]] = relationship(back_populates="contact")
    deals: Mapped[list[Deal]] = relationship(back_populates="contact")


class Property(Base):
    __tablename__ = "properties"
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"))
    address: Mapped[str] = mapped_column(String(200))
    contact: Mapped[Contact] = relationship(back_populates="properties")
    appliances: Mapped[list[Appliance]] = relationship(back_populates="property")


class Appliance(Base):
    __tablename__ = "appliances"
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    kind: Mapped[str] = mapped_column(String(40), default="boiler")
    make: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(80))
    serial: Mapped[str | None] = mapped_column(String(80))
    install_year: Mapped[int | None] = mapped_column(Integer)
    last_service_date: Mapped[dt.date | None] = mapped_column(Date)
    property: Mapped[Property] = relationship(back_populates="appliances")


class Deal(Base):
    __tablename__ = "deals"
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"))
    property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id"))
    title: Mapped[str] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(String(20), default="enquiry")
    value_eur: Mapped[float | None] = mapped_column(Float)
    parts_cost_eur: Mapped[float] = mapped_column(Float, default=0.0)
    stage_changed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    contact: Mapped[Contact] = relationship(back_populates="deals")


class Interaction(Base):
    __tablename__ = "interactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"))
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("deals.id"))
    kind: Mapped[str] = mapped_column(String(20))  # message / call / visit / photo / note
    summary: Mapped[str] = mapped_column(Text)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("deals.id"))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    image_path: Mapped[str] = mapped_column(String(300))
    doc_type: Mapped[str] = mapped_column(String(30))  # data_plate / receipt / note / site_photo
    fields_json: Mapped[str] = mapped_column(Text, default="{}")
    min_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("deals.id"))
    description: Mapped[str] = mapped_column(String(300))
    due_at: Mapped[dt.datetime] = mapped_column(DateTime)
    done: Mapped[bool] = mapped_column(Boolean, default=False)


class Draft(Base):
    """A message to a CUSTOMER. Agents can only create these — never send them."""
    __tablename__ = "drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"))
    purpose: Mapped[str] = mapped_column(String(40))  # quote_chase / service_reminder / reply / win_back
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending / sent / rejected
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    agent: Mapped[str] = mapped_column(String(40))
    tool: Mapped[str] = mapped_column(String(60))
    args_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)


engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
