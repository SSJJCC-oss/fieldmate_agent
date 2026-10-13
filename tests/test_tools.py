"""Tests for the tools. No AI and no API key needed: tools are plain functions.

    pytest -q
"""
import os
import tempfile

# Use a throwaway database for tests — must be set before importing the app.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = _tmp
os.environ["FIELDMATE_FAKE_VISION"] = "true"

from app.db import Base, engine, init_db  # noqa: E402
from app.tools import crm, followup, insights, vision  # noqa: E402


def setup_function():
    Base.metadata.drop_all(engine)
    init_db()


def test_phone_normalisation():
    assert crm.normalise_phone("087 123 4567") == "+353871234567"
    assert crm.normalise_phone("00353871234567") == "+353871234567"
    assert crm.normalise_phone("+44 7700 900123") == "+447700900123"


def test_no_duplicates_found_by_phone_or_name():
    created = crm.create_contact(name="Aoife Kelly", phone="087 555 0000", address="22 Ash Grove")
    assert crm.find_contact(phone="+353875550000")["matches"][0]["contact_id"] == created["contact_id"]
    assert crm.find_contact(name="aoife")["matches"][0]["name"] == "Aoife Kelly"
    assert crm.find_contact(address="ash grove")["matches"]
    assert crm.find_contact(name="Nobody")["matches"] == []


def test_deal_pipeline_and_stuck_quote():
    c = crm.create_contact(name="Mary Byrne", address="14 Oak Drive")
    d = crm.create_deal(contact_id=c["contact_id"], title="Replace boiler", property_id=c["property_id"])
    assert crm.move_deal_stage(deal_id=d["deal_id"], new_stage="flying")["status"] == "error"
    crm.move_deal_stage(deal_id=d["deal_id"], new_stage="quote_sent", value_eur=2400)
    assert insights.list_stuck_quotes(days_without_reply=0)["stuck_quotes"][0]["value_eur"] == 2400
    assert insights.list_stuck_quotes(days_without_reply=5)["stuck_quotes"] == []


def test_opted_out_customer_cannot_get_reminders():
    c = crm.create_contact(name="Tom Walsh", phone="0871112222")
    crm.opt_out_contact(contact_id=c["contact_id"])
    r = followup.queue_for_approval(contact_id=c["contact_id"], purpose="service_reminder", body="Hi Tom")
    assert r["status"] == "blocked"


def test_vision_untrusted_fields_are_flagged(tmp_path):
    img = os.path.join(_tmp, "plate.jpg")
    open(img, "wb").write(b"fake")
    r = vision.read_photo(image_path=img)
    assert r["status"] == "ok"
    assert r["ask_owner"] == ["install_year"]  # 0.55 confidence < 0.8 threshold


def test_vision_refuses_files_outside_uploads():
    assert vision.read_photo(image_path="/etc/passwd")["status"] == "error"


def test_every_tool_call_is_audited():
    from app.db import AuditLog, SessionLocal
    crm.create_contact(name="Seán Quinn")
    with SessionLocal() as db:
        row = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    assert row.tool == "create_contact" and row.agent == "intake"
