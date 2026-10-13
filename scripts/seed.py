"""Fill the CRM with realistic fake data so the agents have something to work on.

    python -m scripts.seed           # adds data (skips if already seeded)
    python -m scripts.seed --reset   # wipes the CRM database first

Creates the cast used in the guide's examples (Mary Byrne with a stuck quote,
Tom Walsh with a service due, Oakview Apartments, a drifting customer...)
plus ~40 random customers spread over two years.
"""
from __future__ import annotations

import datetime as dt
import random
import sys

from app.db import (
    Appliance, Base, Contact, Deal, Interaction, Property, SessionLocal, engine, init_db, utcnow,
)
from app.tools.crm import count_contacts

random.seed(7)
FIRST = ["Aisling", "Brian", "Ciara", "Darragh", "Eimear", "Fionn", "Gráinne", "Liam", "Niamh",
         "Oisín", "Pádraig", "Róisín", "Seán", "Sinéad", "Tadhg", "Orla", "Conor", "Aoibhinn"]
LAST = ["Murphy", "Kelly", "O'Sullivan", "Walsh", "O'Brien", "Ryan", "O'Connor", "McCarthy",
        "Doyle", "Lynch", "Fitzgerald", "Quinn", "Moore", "Brennan"]
STREETS = ["Ash Grove", "Oak Drive", "Castle Road", "Shannon View", "Mill Lane", "Abbey Park",
           "Church Street", "Riverside Close", "Hazel Court", "Willow Lawn"]
JOBS = [("Annual boiler service", 110), ("Leaking radiator valve", 140), ("New shower install", 650),
        ("Burst pipe repair", 220), ("Boiler replacement", 2400), ("Power flush", 450),
        ("Toilet cistern repair", 120), ("Hot water cylinder swap", 980)]
MAKES = [("Ideal", "Logic Combi 30"), ("Worcester Bosch", "Greenstar 30i"), ("Vaillant", "ecoTEC plus 832"),
         ("Baxi", "800 Combi 2")]


def days_ago(n: float) -> dt.datetime:
    return utcnow() - dt.timedelta(days=n)


def make_contact(db, name, phone, address, make=None, install_year=None, last_service_days=None):
    c = Contact(name=name, phone_e164=phone, source="seed")
    db.add(c); db.flush()
    p = Property(contact_id=c.id, address=address)
    db.add(p); db.flush()
    if make:
        db.add(Appliance(property_id=p.id, make=make[0], model=make[1], install_year=install_year,
                         last_service_date=(utcnow() - dt.timedelta(days=last_service_days)).date()
                         if last_service_days else None))
    return c, p


def deal(db, c, p, title, value, stage, created_days, stage_days=None):
    d = Deal(contact_id=c.id, property_id=p.id, title=title, value_eur=value, stage=stage,
             created_at=days_ago(created_days), stage_changed_at=days_ago(stage_days if stage_days is not None else created_days))
    db.add(d); db.flush()
    db.add(Interaction(contact_id=c.id, deal_id=d.id, kind="visit", summary=f"{title} — {stage}",
                       at=days_ago(created_days)))
    return d


def main() -> None:
    if "--reset" in sys.argv:
        Base.metadata.drop_all(engine)
    init_db()
    if count_contacts():
        print("Already seeded. Use --reset to start over.")
        return
    with SessionLocal() as db:
        # --- the named cast from the examples ---
        mary, mp = make_contact(db, "Mary Byrne", "+353871110001", "14 Oak Drive, Limerick",
                                MAKES[0], 2009, 200)
        deal(db, mary, mp, "Replace boiler", 2400, "quote_sent", 9, stage_days=7)

        tom, tp = make_contact(db, "Tom Walsh", "+353871110002", "3 Castle Road, Limerick",
                               MAKES[1], 2016, 350)  # service due in ~2 weeks
        deal(db, tom, tp, "Replace boiler pump", 380, "paid", 350)

        oak, op = make_contact(db, "Oakview Apartments (manager: Joe Hayes)", "+353871110003",
                               "Oakview Apartments, Dock Road, Limerick")
        for days, title, value in [(560, "Leak, Block B", 260), (470, "Leak, Block B", 190),
                                   (500, "New water heater, flat 12", 1150)]:
            deal(db, oak, op, title, value, "paid", days)
        db.add(Interaction(contact_id=oak.id, kind="call", at=days_ago(300),
                           summary="Manager asked for a quote to re-pipe Block C. Quote never sent."))

        sean, sp = make_contact(db, "Seán Quinn", "+353871110004", "8 Mill Lane, Limerick")
        deal(db, sean, sp, "New shower install", 650, "done", 6, stage_days=3)  # unbilled

        old, olp = make_contact(db, "Brian Doyle", "+353871110005", "21 Abbey Park, Limerick",
                                MAKES[2], 2011, 700)
        deal(db, old, olp, "Power flush", 450, "paid", 650)  # drifting customer

        # --- random background customers ---
        for i in range(40):
            name = f"{random.choice(FIRST)} {random.choice(LAST)}"
            addr = f"{random.randint(1, 60)} {random.choice(STREETS)}, Limerick"
            c, p = make_contact(db, name, f"+35387{2000000 + i:07d}", addr, random.choice(MAKES),
                                random.randint(2008, 2023), random.randint(30, 420))
            for _ in range(random.randint(1, 3)):
                title, value = random.choice(JOBS)
                deal(db, c, p, title, value, random.choice(["paid"] * 6 + ["lost", "quote_sent", "done"]),
                     random.randint(5, 700))
        db.commit()
    print(f"Seeded {count_contacts()} contacts.")


if __name__ == "__main__":
    main()
