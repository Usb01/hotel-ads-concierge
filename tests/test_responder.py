from datetime import date

from app.config import ROOT, Settings
from app.hotel import Hotel
from app.responder import Responder, detect_intent

hotel = Hotel.load(str(ROOT / "data" / "hotel.json"))


def responder(provider="none"):
    s = Settings()
    s.llm_provider = provider
    return Responder(hotel, s, today=date(2026, 10, 20))


def test_intents():
    assert detect_intent("the room was dirty, really disappointed") == "complaint"
    assert detect_intent("need 20 rooms for a wedding") == "group"
    assert detect_intent("room 12-14 nov?") == "booking"
    assert detect_intent("how much is it") == "pricing"
    assert detect_intent("can I bring my cat") == "question"


def test_booking_with_dates_quotes_and_hands_off():
    d = responder().decide("12-14 Nov, 2 adults", "Sara Khan")
    assert d.intent == "booking" and d.needs_staff and d.handoff_reason == "booking_request"
    assert d.reply.startswith("Hi Sara")
    assert "SAR 1,220" in d.reply  # Thu + Fri weekend nights for Deluxe King


def test_policy_question_answered_without_staff():
    d = responder().decide("Do you have parking?")
    assert not d.needs_staff
    assert "Valet parking" in d.reply


def test_complaint_always_goes_to_staff():
    d = responder().decide("Staff were rude and the room smelled")
    assert d.needs_staff and d.handoff_reason == "complaint"


def test_unknown_question_goes_to_staff():
    d = responder().decide("Is there a mosque nearby?")
    assert d.needs_staff and d.handoff_reason == "unanswered_question"


def test_arabic_without_llm_is_acknowledged_and_handed_off():
    d = responder().decide("هل يوجد إفطار؟", "Noura")
    assert d.intent == "arabic" and d.needs_staff


def test_llm_reply_with_invented_price_is_rejected(monkeypatch):
    r = responder("openai")
    monkeypatch.setattr(r, "_call_model", lambda s, u: '{"reply": "Special offer: SAR 199 per night!", "needs_staff": false}')
    d = r.decide("Do you have any offers?")
    assert d.mode == "rules"  # fell back
    assert "199" not in d.reply


def test_llm_reply_with_known_price_is_used(monkeypatch):
    r = responder("openai")
    monkeypatch.setattr(r, "_call_model", lambda s, u: '{"reply": "Our Deluxe King starts at SAR 520 a night.", "needs_staff": false}')
    d = r.decide("how much is a room?")
    assert d.mode == "openai" and "520" in d.reply


def test_llm_failure_falls_back(monkeypatch):
    r = responder("anthropic")

    def boom(*_):
        raise TimeoutError("timeout")

    monkeypatch.setattr(r, "_call_model", boom)
    d = r.decide("Is there parking?")
    assert d.mode == "rules" and "llm_error" in d.facts
