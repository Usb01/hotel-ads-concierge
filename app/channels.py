"""Outbound delivery. In DRY_RUN mode (the default) nothing is sent; the
message is only recorded in the outbox table so you can see what would go out."""
import os
import re
import smtplib
from email.message import EmailMessage

import httpx

from .db import now, tx


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("05") and len(digits) == 10:  # Saudi mobile written locally
        digits = "966" + digits[1:]
    return digits if len(digits) >= 10 else None


def pick_channel(lead: dict) -> tuple[str, str] | None:
    phone = normalize_phone(lead.get("phone"))
    if phone:
        return "whatsapp", phone
    if lead.get("email"):
        return "email", lead["email"]
    return None


def send_whatsapp(settings, to: str, body: str) -> None:
    resp = httpx.post(
        f"https://graph.facebook.com/v21.0/{settings.whatsapp_phone_id}/messages",
        headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
        json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}},
        timeout=10,
    )
    resp.raise_for_status()


def send_email(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587"))) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)


def deliver(conn, settings, hotel_name: str, lead: dict, body: str) -> str:
    target = pick_channel(lead)
    if not target:
        return "no_contact"
    channel, recipient = target

    status, error = "dry_run", None
    if not settings.dry_run:
        try:
            if channel == "whatsapp":
                send_whatsapp(settings, recipient, body)
            else:
                send_email(recipient, f"Your enquiry – {hotel_name}", body)
            status = "sent"
        except Exception as exc:
            status, error = "failed", str(exc)[:300]

    with tx(conn):
        conn.execute(
            "INSERT INTO outbox (lead_id, channel, recipient, body, status, error, created_at) VALUES (?,?,?,?,?,?,?)",
            (lead["id"], channel, recipient, body, status, error, now()),
        )
    return status
