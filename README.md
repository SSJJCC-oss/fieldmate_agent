# FieldMate

A multi-agent WhatsApp CRM for tradespeople. The owner sends voice notes,
photos and short texts from the van; a team of AI agents keeps the customer
records, reads data plates and receipts, spots stuck quotes and services due,
and drafts follow-ups that the owner approves before anything is sent.

**Stack:** Python · Google ADK + Gemini · FastAPI · SQLAlchemy (SQLite → Postgres) · Twilio WhatsApp

```
            router (talks to the owner)
   ┌───────────┼────────────┬──────────────┐
 intake      vision      insights       follow-up
 records     photos      briefing       drafts only
   └───────────┴────────────┴──────────────┘
                 CRM database + audit log
```

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # add GOOGLE_API_KEY and your number
pytest -q                     # tools work (no AI needed)
python -m scripts.seed --reset
python -m scripts.chat --trace
```

Full walkthrough, architecture and pipeline explanation: **[GUIDE.md](GUIDE.md)**.

## Project layout
```
app/
  config.py        settings from .env
  db.py            CRM tables
  tools/           what agents can DO (crm, vision, insights, followup, audit)
  agents.py        router + 4 specialists
  pipeline.py      one message, start to finish
  main.py          WhatsApp webhook + morning-briefing endpoint
  channels/        Twilio in/out
scripts/           seed data, terminal chat
tests/             tool tests
```
