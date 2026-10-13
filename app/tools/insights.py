"""Tools for the INSIGHTS agent: read-only questions about the whole CRM.

Notice these are plain SQL queries — rules, not machine learning. Start with
rules you can explain; add a learned churn model later only if it beats them.
The agent's job is to call these, rank what it finds by money at stake, and
write a short briefing.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from ..db import Appliance, Contact, Deal, Property, SessionLocal, Task, utcnow
from .audit import audited


@audited("insights")
def list_stuck_quotes(days_without_reply: int = 5) -> dict:
    """Quotes that were sent but have had no answer for a while.

    These are the most valuable follow-ups: money already on the table.

    Args:
        days_without_reply: How old a quote must be to count as stuck.

    Returns:
        {"stuck_quotes": [{deal_id, contact_id, contact, title, value_eur, days_waiting}]}
    """
    cutoff = utcnow() - dt.timedelta(days=days_without_reply)
    with SessionLocal() as db:
        rows = db.execute(
            select(Deal, Contact)
            .join(Contact)
            .where(Deal.stage == "quote_sent", Deal.stage_changed_at <= cutoff)
        ).all()
        now = utcnow()
        return {
            "stuck_quotes": [
                {
                    "deal_id": d.id, "contact_id": c.id, "contact": c.name, "title": d.title,
                    "value_eur": d.value_eur, "days_waiting": (now - d.stage_changed_at).days,
                }
                for d, c in rows
            ]
        }


@audited("insights")
def list_unbilled_jobs() -> dict:
    """Jobs marked done that have not been invoiced yet (money being forgotten).

    Returns:
        {"unbilled": [{deal_id, contact, title, value_eur, parts_cost_eur, days_since_done}]}
    """
    with SessionLocal() as db:
        rows = db.execute(select(Deal, Contact).join(Contact).where(Deal.stage == "done")).all()
        now = utcnow()
        return {
            "unbilled": [
                {
                    "deal_id": d.id, "contact": c.name, "title": d.title, "value_eur": d.value_eur,
                    "parts_cost_eur": d.parts_cost_eur, "days_since_done": (now - d.stage_changed_at).days,
                }
                for d, c in rows
            ]
        }


@audited("insights")
def list_due_services(within_days: int = 30) -> dict:
    """Appliances whose yearly service falls due within the next N days.

    Only customers who are opted in are included, because the result is used
    to send reminders.

    Args:
        within_days: Look-ahead window in days.

    Returns:
        {"due_services": [{contact_id, contact, address, appliance, due_date}]}
    """
    today = utcnow().date()
    horizon = today + dt.timedelta(days=within_days)
    with SessionLocal() as db:
        rows = db.execute(
            select(Appliance, Property, Contact)
            .join(Property, Appliance.property_id == Property.id)
            .join(Contact, Property.contact_id == Contact.id)
            .where(Appliance.last_service_date.is_not(None), Contact.opted_in.is_(True),
                   Contact.deleted_at.is_(None))
        ).all()
        due = []
        for a, p, c in rows:
            due_date = a.last_service_date + dt.timedelta(days=365)
            if due_date <= horizon:
                due.append({
                    "contact_id": c.id, "contact": c.name, "address": p.address,
                    "appliance": f"{a.make or ''} {a.model or ''}".strip(),
                    "due_date": due_date.isoformat(),
                })
        return {"due_services": sorted(due, key=lambda x: x["due_date"])}


@audited("insights")
def list_drifting_customers(months_inactive: int = 18) -> dict:
    """Customers who used to book jobs but haven't for a long time (churn risk).

    Args:
        months_inactive: How long without a new job counts as drifting.

    Returns:
        {"drifting": [{contact_id, contact, jobs_total, lifetime_value_eur, last_job_date}]}
    """
    cutoff = utcnow() - dt.timedelta(days=30 * months_inactive)
    with SessionLocal() as db:
        rows = db.execute(
            select(
                Contact.id, Contact.name,
                func.count(Deal.id), func.coalesce(func.sum(Deal.value_eur), 0), func.max(Deal.created_at),
            )
            .join(Deal, Deal.contact_id == Contact.id)
            .where(Contact.opted_in.is_(True), Contact.deleted_at.is_(None))
            .group_by(Contact.id)
            .having(func.max(Deal.created_at) <= cutoff)
        ).all()
        return {
            "drifting": [
                {"contact_id": cid, "contact": name, "jobs_total": n,
                 "lifetime_value_eur": round(float(total), 2), "last_job_date": last.date().isoformat()}
                for cid, name, n, total, last in sorted(rows, key=lambda r: -float(r[3]))
            ][:10]
        }


@audited("insights")
def list_due_tasks() -> dict:
    """The owner's reminders that are due now or overdue.

    Returns:
        {"tasks": [{task_id, description, due_at}]}
    """
    with SessionLocal() as db:
        rows = db.scalars(select(Task).where(Task.done.is_(False), Task.due_at <= utcnow() + dt.timedelta(hours=12))).all()
        return {"tasks": [{"task_id": t.id, "description": t.description,
                           "due_at": t.due_at.isoformat(timespec="minutes")} for t in rows]}
