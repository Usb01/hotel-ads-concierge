"""Decides what to say back to a guest and whether a human needs to step in.

Two modes:
  * rules  – keyword intents + templates, no external calls (default)
  * llm    – OpenAI or Anthropic writes the reply, but only from facts we hand
             it (matched policies + computed quotes). The reply is checked
             before it goes out; if it mentions a price we didn't give it, we
             throw it away and fall back to the rules reply.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date

import httpx

from .hotel import Hotel, parse_stay

ARABIC = re.compile(r"[؀-ۿ]")

COMPLAINT_WORDS = ["complain", "complaint", "dirty", "rude", "terrible", "worst", "disappointed",
                   "refund my", "never again", "angry", "unacceptable", "smell"]
GROUP_WORDS = ["wedding", "conference", "corporate", "group booking", "event", "rooms for our team", "10 rooms"]
BOOKING_WORDS = ["book", "reserve", "reservation", "availability", "available", "stay", "room for", "nights"]
PRICE_WORDS = ["price", "rate", "cost", "how much", "offer", "discount", "package"]
GREETING_WORDS = ["hi", "hello", "hey", "salam", "good morning", "good evening"]


@dataclass
class Decision:
    intent: str
    reply: str
    needs_staff: bool = False
    handoff_reason: str | None = None
    facts: dict = field(default_factory=dict)
    mode: str = "rules"


def _has(text: str, words: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(w)}", text) for w in words)


def detect_intent(text: str) -> str:
    t = text.lower()
    if _has(t, COMPLAINT_WORDS):
        return "complaint"
    if _has(t, GROUP_WORDS):
        return "group"
    if parse_stay(t) or _has(t, BOOKING_WORDS):
        return "booking"
    if _has(t, PRICE_WORDS):
        return "pricing"
    return "question"


def first_name(name: str | None) -> str:
    return (name or "").strip().split(" ")[0] or "there"


class Responder:
    def __init__(self, hotel: Hotel, settings, today: date | None = None):
        self.hotel = hotel
        self.settings = settings
        self.today = today

    # -- public -------------------------------------------------------------

    def decide(self, text: str, guest_name: str | None = None) -> Decision:
        decision = self._rules(text, guest_name)
        if self.settings.llm_provider in ("openai", "anthropic") and decision.intent not in ("complaint",):
            try:
                llm = self._llm(text, guest_name, decision)
                if llm:
                    return llm
            except Exception as exc:  # network, quota, bad JSON ... keep the rules reply
                decision.facts["llm_error"] = str(exc)[:200]
        return decision

    # -- rules mode ---------------------------------------------------------

    def _rules(self, text: str, guest_name: str | None) -> Decision:
        h = self.hotel
        name = first_name(guest_name)
        intent = detect_intent(text)

        if ARABIC.search(text) and self.settings.llm_provider == "none":
            return Decision(
                intent="arabic",
                reply=f"أهلاً {name}، شكراً لتواصلك مع {h.name}. سيرد عليك أحد موظفي الحجوزات خلال دقائق.",
                needs_staff=True,
                handoff_reason="arabic_message",
            )

        if intent == "complaint":
            return Decision(
                intent,
                f"Hi {name}, I'm really sorry to hear this. I've passed your message to our duty manager, "
                f"who will contact you directly within 15 minutes.",
                needs_staff=True,
                handoff_reason="complaint",
            )

        if intent == "group":
            return Decision(
                intent,
                f"Hi {name}, thanks for thinking of {h.name}! Group and event bookings get special rates. "
                f"Our sales team will send you a proposal today. Could you share the dates and number of rooms?",
                needs_staff=True,
                handoff_reason="group_booking",
            )

        stay = parse_stay(text, self.today)
        policies = h.match_policies(text)
        parts = [f"Hi {name}, thanks for your message!"]
        facts: dict = {"policies": [p["id"] for p in policies]}

        if stay:
            quotes = [q for q in h.quote(stay) if q.available]
            facts["stay"] = {"check_in": stay.check_in.isoformat(), "check_out": stay.check_out.isoformat(),
                             "guests": stay.guests}
            facts["quotes"] = [q.__dict__ for q in quotes]
            when = f"{stay.check_in:%d %b} – {stay.check_out:%d %b} ({stay.nights} night{'s' if stay.nights > 1 else ''})"
            if quotes:
                lines = [f"• {q.room_name}: {h.currency} {q.total:,} total ({q.highlights})" for q in quotes]
                parts.append(f"For {when} we have:\n" + "\n".join(lines))
                parts.append("Reply with the room you like and I'll ask our reservations team to hold it for you.")
                for p in policies:
                    parts.append(p["answer"])
                return Decision("booking", "\n\n".join(parts), True, "booking_request", facts)
            parts.append(f"Unfortunately we're fully booked for {when} in rooms that fit your group. "
                         f"I've asked our reservations team to check alternatives for you.")
            return Decision("booking", "\n\n".join(parts), True, "sold_out", facts)

        if policies:
            parts.extend(p["answer"] for p in policies)
            if intent == "booking":
                parts.append("Which dates are you looking at, and how many guests? I'll check availability right away.")
            return Decision(intent, "\n\n".join(parts), False, None, facts)

        if intent in ("booking", "pricing"):
            cheapest = min(h.rooms.values(), key=lambda r: r["base_rate"])
            parts.append(f"Rooms start from {h.currency} {cheapest['base_rate']} per night "
                         f"({cheapest['name']}). Which dates are you looking at, and how many guests?")
            return Decision(intent, "\n\n".join(parts), False, None, facts)

        parts.append("I've shared your question with our front desk team, who'll get back to you shortly. "
                     f"For anything urgent you can call us on {h.data.get('phone')}.")
        return Decision("question", "\n\n".join(parts), True, "unanswered_question", facts)

    # -- LLM mode -----------------------------------------------------------

    SYSTEM_PROMPT = (
        "You are the WhatsApp concierge for {hotel}. Answer the guest briefly and warmly, in the "
        "same language they wrote in. Use ONLY the facts provided. Never invent prices, discounts, "
        "availability or policies. If the facts don't answer the question, say a colleague will follow up "
        "and set needs_staff to true. Booking requests always need staff to confirm. "
        "Respond as JSON: {{\"reply\": string, \"needs_staff\": boolean}}"
    )

    def _llm(self, text: str, guest_name: str | None, base: Decision) -> Decision | None:
        facts = {
            "hotel": self.hotel.name,
            "currency": self.hotel.currency,
            "check_in_time": self.hotel.data.get("check_in"),
            "check_out_time": self.hotel.data.get("check_out"),
            "rooms": [{k: r[k] for k in ("name", "max_guests", "base_rate", "weekend_rate", "highlights")}
                      for r in self.hotel.rooms.values()],
            "relevant_policies": [p["answer"] for p in self.hotel.match_policies(text, limit=3)],
            "quotes_for_requested_dates": base.facts.get("quotes", []),
            "requested_stay": base.facts.get("stay"),
            "phone": self.hotel.data.get("phone"),
        }
        system = self.SYSTEM_PROMPT.format(hotel=self.hotel.name)
        user = f"Guest name: {guest_name or 'unknown'}\nGuest message: {text}\n\nFacts:\n{json.dumps(facts, default=str)}"

        raw = self._call_model(system, user)
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        reply = str(data.get("reply", "")).strip()
        if not reply or not self._prices_ok(reply, base.facts):
            return None

        needs_staff = bool(data.get("needs_staff")) or base.needs_staff
        return Decision(
            intent=base.intent,
            reply=reply,
            needs_staff=needs_staff,
            handoff_reason=base.handoff_reason or ("llm_flagged" if needs_staff else None),
            facts=base.facts,
            mode=self.settings.llm_provider,
        )

    def _prices_ok(self, reply: str, facts: dict) -> bool:
        allowed = self.hotel.known_prices() | {int(q["total"]) for q in facts.get("quotes", [])}
        for match in re.findall(r"(?:SAR|ريال|﷼)\s?([\d,]+)|([\d,]+)\s?(?:SAR|ريال)", reply):
            value = next(m for m in match if m)
            if int(value.replace(",", "")) not in allowed:
                return False
        return True

    def _call_model(self, system: str, user: str) -> str:
        s = self.settings
        if s.llm_provider == "openai":
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {s.openai_api_key}"},
                json={
                    "model": s.openai_model,
                    "response_format": {"type": "json_object"},
                    "temperature": 0.3,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01"},
            json={
                "model": s.anthropic_model,
                "max_tokens": 600,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return "".join(b.get("text", "") for b in resp.json()["content"])
