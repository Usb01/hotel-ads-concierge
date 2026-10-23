"""Google Ads lead form webhook.

Google posts the full lead in one request and authenticates it with the
`google_key` you configure on the lead form.
"""
import hmac

CONTACT_COLUMNS = {
    "FULL_NAME": "name",
    "EMAIL": "email",
    "PHONE_NUMBER": "phone",
}


def key_is_valid(payload: dict, expected_key: str) -> bool:
    return hmac.compare_digest(str(payload.get("google_key", "")), expected_key)


def normalize(payload: dict, campaign_names: dict | None = None) -> dict:
    contact = {}
    extra = []
    first = last = ""
    for col in payload.get("user_column_data", []):
        col_id = col.get("column_id", "")
        value = col.get("string_value", "")
        if col_id in CONTACT_COLUMNS:
            contact[CONTACT_COLUMNS[col_id]] = value
        elif col_id == "FIRST_NAME":
            first = value
        elif col_id == "LAST_NAME":
            last = value
        elif value:
            label = col.get("column_name", col_id)
            extra.append(value if "question" in label.lower() else f"{label}: {value}")

    if "name" not in contact and (first or last):
        contact["name"] = f"{first} {last}".strip()

    campaign_id = str(payload.get("campaign_id", ""))
    campaign = (campaign_names or {}).get(campaign_id, f"Google Ads #{campaign_id}" if campaign_id else None)

    return {
        "source": "google",
        "external_id": str(payload.get("lead_id")),
        "campaign": campaign,
        "ad_name": None,
        "message": "\n".join(extra),
        "is_test": bool(payload.get("is_test")),
        **contact,
    }
