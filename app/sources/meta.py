"""Meta Lead Ads (Facebook / Instagram) webhook handling.

Meta only sends the leadgen_id in the webhook. The actual answers have to be
fetched from the Graph API with a page access token. Without a token we fall
back to `field_data` inside the payload, which is what scripts/simulate_leads.py
sends so the flow can be run locally.
"""
import hashlib
import hmac
from datetime import datetime, timezone

import httpx

GRAPH_URL = "https://graph.facebook.com/v21.0"

FIELD_MAP = {
    "full_name": "name",
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email",
    "phone_number": "phone",
}


def _is_free_text(field_name: str) -> bool:
    """Custom "ask us anything" questions – keep the guest's words without the label."""
    name = field_name.lower()
    return any(w in name for w in ("question", "ask", "message", "comment"))


def verify_signature(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    if not app_secret:
        # local development: signature checking disabled
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def iter_leadgen_events(payload: dict):
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") == "leadgen":
                yield change.get("value", {})


def fetch_lead(leadgen_id: str, page_token: str) -> dict:
    resp = httpx.get(
        f"{GRAPH_URL}/{leadgen_id}",
        params={"access_token": page_token, "fields": "created_time,field_data,campaign_name,ad_name"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def normalize(event: dict, details: dict) -> dict:
    """Turn a Graph API lead into our lead shape.

    Any form field that isn't a contact field (e.g. "check_in_date",
    "what_would_you_like_to_know") is kept as part of the first message.
    """
    contact = {}
    extra = []
    for field in details.get("field_data", []):
        key = field.get("name", "")
        value = ", ".join(field.get("values", []))
        if key in FIELD_MAP:
            contact[FIELD_MAP[key]] = value
        elif value:
            extra.append(value if _is_free_text(key) else f"{key.replace('_', ' ')}: {value}")

    if "name" not in contact:
        contact["name"] = " ".join(filter(None, [contact.pop("first_name", ""), contact.pop("last_name", "")])) or None
    contact.pop("first_name", None)
    contact.pop("last_name", None)

    submitted = details.get("created_time") or event.get("created_time")
    if isinstance(submitted, (int, float)):
        submitted = datetime.fromtimestamp(submitted, timezone.utc).isoformat(timespec="seconds")

    return {
        "source": "meta",
        "submitted_at": submitted,
        "external_id": str(event.get("leadgen_id")),
        "campaign": details.get("campaign_name") or event.get("campaign_name"),
        "ad_name": details.get("ad_name") or event.get("ad_name"),
        "message": "\n".join(extra),
        **contact,
    }
