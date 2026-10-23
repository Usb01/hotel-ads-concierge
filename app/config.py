import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", str(ROOT / "concierge.db")))
    hotel_file: str = field(default_factory=lambda: os.getenv("HOTEL_FILE", str(ROOT / "data" / "hotel.json")))

    # Meta (Facebook / Instagram) lead ads
    meta_verify_token: str = field(default_factory=lambda: os.getenv("META_VERIFY_TOKEN", "dev-verify-token"))
    meta_app_secret: str = field(default_factory=lambda: os.getenv("META_APP_SECRET", ""))
    meta_page_token: str = field(default_factory=lambda: os.getenv("META_PAGE_TOKEN", ""))

    # Google Ads lead form extension webhook
    google_lead_key: str = field(default_factory=lambda: os.getenv("GOOGLE_LEAD_KEY", "dev-google-key"))

    # WhatsApp Cloud API (outbound + inbound guest replies)
    whatsapp_token: str = field(default_factory=lambda: os.getenv("WHATSAPP_TOKEN", ""))
    whatsapp_phone_id: str = field(default_factory=lambda: os.getenv("WHATSAPP_PHONE_ID", ""))

    # LLM provider: "none" uses the rule-based responder
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "none").lower())
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"))

    # When true nothing leaves the server; replies are written to the outbox table
    dry_run: bool = field(default_factory=lambda: os.getenv("DRY_RUN", "1") != "0")


settings = Settings()
