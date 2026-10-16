"""STEP 0 — Settings.

Every secret and switch comes from environment variables (loaded from .env),
never from the code. That way the same code runs on your laptop and in the
cloud, and no API key ever ends up on GitHub.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() == "true"


class Settings:
    # The "brain". Google ADK reads GOOGLE_API_KEY from the environment itself.
    model: str = os.environ.get("FIELDMATE_MODEL", "gemini-3.8-flash")

    # Where the CRM lives. SQLite (a single file) is perfect for learning;
    # switch to Postgres later by changing only this URL.
    database_url: str = os.environ.get("DATABASE_URL", "sqlite:///fieldmate.db")
    session_db_url: str = os.environ.get(
        "SESSION_DB_URL", "sqlite+aiosqlite:///fieldmate_sessions.db"
    )

    # Twilio (WhatsApp)
    twilio_account_sid: str = os.environ.get("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.environ.get("TWILIO_AUTH_TOKEN", "")
    twilio_whatsapp_number: str = os.environ.get(
        "TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886"
    )
    validate_twilio_signature: bool = _flag("VALIDATE_TWILIO_SIGNATURE", "true")

    # Version 1 talks ONLY to the business owner. Comma-separated E.164 numbers.
    owner_numbers: list[str] = [
        n.strip() for n in os.environ.get("OWNER_NUMBERS", "").split(",") if n.strip()
    ]

    # DRY_RUN=true -> messages to customers are printed, not actually sent.
    dry_run: bool = _flag("DRY_RUN", "true")

    # Protects the /jobs/* endpoints that a scheduler calls.
    jobs_token: str = os.environ.get("JOBS_TOKEN", "change-me")

    # Vision: below this confidence, the agent must ask instead of saving.
    vision_min_confidence: float = float(os.environ.get("VISION_MIN_CONFIDENCE", "0.8"))

    business_name: str = os.environ.get("BUSINESS_NAME", "Declan's Plumbing & Heating")
    upload_dir: str = os.environ.get("UPLOAD_DIR", "uploads")


settings = Settings()
