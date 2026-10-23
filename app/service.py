from .channels import deliver, normalize_phone, pick_channel
from .db import now, tx
from .responder import Responder


class Concierge:
    def __init__(self, conn, hotel, settings, responder: Responder | None = None):
        self.conn = conn
        self.hotel = hotel
        self.settings = settings
        self.responder = responder or Responder(hotel, settings)

    def ingest_lead(self, lead: dict) -> dict:
        """Store a lead from an ad platform and auto-reply to it.

        Ad platforms retry webhooks, so the same lead can arrive twice; the
        (source, external_id) unique key makes that a no-op.
        """
        existing = self.conn.execute(
            "SELECT id FROM leads WHERE source = ? AND external_id = ?", (lead["source"], lead["external_id"])
        ).fetchone()
        if existing:
            return {"lead_id": existing["id"], "duplicate": True}

        with tx(self.conn):
            cur = self.conn.execute(
                "INSERT INTO leads (source, external_id, campaign, ad_name, name, email, phone, submitted_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (lead["source"], lead["external_id"], lead.get("campaign"), lead.get("ad_name"),
                 lead.get("name"), lead.get("email"), normalize_phone(lead.get("phone")) or lead.get("phone"),
                 lead.get("submitted_at"), now()),
            )
            lead_id = cur.lastrowid

        text = lead.get("message") or "I'm interested in staying at your hotel."
        return {"lead_id": lead_id, "duplicate": False, **self._respond(lead_id, text, channel="form")}

    def guest_message(self, wa_id: str, text: str, name: str | None = None) -> dict:
        """A guest wrote to us on WhatsApp. Attach it to their lead (or create one)."""
        phone = normalize_phone(wa_id)
        row = self.conn.execute(
            "SELECT id FROM leads WHERE phone = ? ORDER BY id DESC LIMIT 1", (phone,)
        ).fetchone()
        if row:
            lead_id = row["id"]
        else:
            with tx(self.conn):
                lead_id = self.conn.execute(
                    "INSERT INTO leads (source, external_id, campaign, name, phone, created_at) VALUES (?,?,?,?,?,?)",
                    ("whatsapp", f"wa:{phone}", "WhatsApp (organic)", name, phone, now()),
                ).lastrowid

        open_handoff = self.conn.execute(
            "SELECT id FROM handoffs WHERE lead_id = ? AND resolved = 0", (lead_id,)
        ).fetchone()
        if open_handoff:
            # A person is already handling this guest – don't talk over them.
            self._log(lead_id, "in", "whatsapp", text, None, "guest")
            return {"lead_id": lead_id, "held_for_staff": True}

        return {"lead_id": lead_id, **self._respond(lead_id, text, channel="whatsapp")}

    def staff_reply(self, lead_id: int, body: str, resolve: bool = True) -> None:
        lead = self._lead(lead_id)
        self._log(lead_id, "out", pick_channel(lead)[0] if pick_channel(lead) else "note", body, None, "staff")
        deliver(self.conn, self.settings, self.hotel.name, lead, body)
        with tx(self.conn):
            if resolve:
                self.conn.execute("UPDATE handoffs SET resolved = 1 WHERE lead_id = ?", (lead_id,))
                self.conn.execute("UPDATE leads SET status = 'replied' WHERE id = ? AND status = 'needs_staff'",
                                  (lead_id,))

    def set_status(self, lead_id: int, status: str) -> None:
        if status not in ("new", "replied", "needs_staff", "booked", "lost"):
            raise ValueError(status)
        with tx(self.conn):
            self.conn.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
            if status in ("booked", "lost"):
                self.conn.execute("UPDATE handoffs SET resolved = 1 WHERE lead_id = ?", (lead_id,))

    # -- internals ----------------------------------------------------------

    def _respond(self, lead_id: int, text: str, channel: str) -> dict:
        lead = self._lead(lead_id)
        decision = self.responder.decide(text, lead.get("name"))
        self._log(lead_id, "in", channel, text, decision.intent, "guest")
        self._log(lead_id, "out", pick_channel(lead)[0] if pick_channel(lead) else "none", decision.reply,
                  decision.intent, "bot")
        status = deliver(self.conn, self.settings, self.hotel.name, lead, decision.reply)

        with tx(self.conn):
            self.conn.execute(
                "UPDATE leads SET status = ?, first_reply_at = COALESCE(first_reply_at, ?) WHERE id = ?",
                ("needs_staff" if decision.needs_staff else "replied", now(), lead_id),
            )
            if decision.needs_staff:
                self.conn.execute(
                    "INSERT INTO handoffs (lead_id, reason, details, created_at) VALUES (?,?,?,?)",
                    (lead_id, decision.handoff_reason, text[:500], now()),
                )
        return {"intent": decision.intent, "reply": decision.reply, "needs_staff": decision.needs_staff,
                "delivery": status, "mode": decision.mode}

    def _lead(self, lead_id: int) -> dict:
        return dict(self.conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone())

    def _log(self, lead_id, direction, channel, body, intent, author):
        with tx(self.conn):
            self.conn.execute(
                "INSERT INTO messages (lead_id, direction, channel, body, intent, author, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (lead_id, direction, channel, body, intent, author, now()),
            )
