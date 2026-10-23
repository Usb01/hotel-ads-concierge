"""Inbound WhatsApp Cloud API messages (guest replies to our auto-response)."""


def iter_text_messages(payload: dict):
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                if msg.get("type") != "text":
                    continue
                yield {
                    "wa_id": msg.get("from"),
                    "name": names.get(msg.get("from")),
                    "message_id": msg.get("id"),
                    "text": msg.get("text", {}).get("body", ""),
                }
