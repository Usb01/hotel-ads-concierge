"""Send fake Meta / Google / WhatsApp webhook events to the app.

    python scripts/simulate_leads.py                    # against http://127.0.0.1:8000
    python scripts/simulate_leads.py --url https://...  # against a deployed instance

All names, numbers and messages are made up.
"""
import argparse
import random
import time
from datetime import date, timedelta

import httpx

FIRST = ["Ahmed", "Sara", "Faisal", "Noura", "Omar", "Lina", "Khalid", "Reem", "Yousef", "Hana",
         "Ali", "Maha", "James", "Priya", "Tariq", "Dana", "Hassan", "Layla", "Bilal", "Mona"]
LAST = ["Al-Qahtani", "Haddad", "Al-Harbi", "Mansour", "Siddiqui", "Khan", "Al-Otaibi", "Rahman", "Carter", "Nair"]

META_CAMPAIGNS = [("Winter Corniche Escape", "Carousel – sea view rooms"),
                  ("Family Weekend Offer", "Reel – kids eat free"),
                  ("Riyadh Retargeting", "Static – 3 nights for 2")]
GOOGLE_CAMPAIGNS = {"2208431": "Search – Al Khobar hotels", "2208432": "Search – Corniche hotel sea view"}


def questions(today: date) -> list[str]:
    d = lambda n: (today + timedelta(days=n)).strftime("%d %b")  # noqa: E731
    iso = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    return [
        f"Do you have a room from {d(10)} for 2 nights? 2 adults",
        f"Family of 4, looking at {iso(21)} to {iso(24)}. Is breakfast included?",
        "How much is the sea view room for the weekend?",
        "Do you offer airport pickup from Dammam airport?",
        "Is there parking at the hotel?",
        "What are the pool timings for ladies?",
        f"{d(5)} for 3 nights, 2 adults and 1 child please",
        "Can I bring my cat?",
        "We're planning a wedding in March and need around 20 rooms",
        "What's your cancellation policy?",
        "Can I get late check out on Saturday?",
        "Last time the room was dirty and the staff were rude. Very disappointed.",
        "هل يوجد إفطار مع الغرفة؟",
        "Do you have any offers this month?",
        "Is the hotel close to the Corniche walkway and Al Rashid Mall?",
    ]


def meta_payload(i: int, name: str, phone: str, msg: str, submitted: int) -> dict:
    campaign, ad = random.choice(META_CAMPAIGNS)
    return {"object": "page", "entry": [{"id": "1000000000", "time": submitted, "changes": [{
        "field": "leadgen",
        "value": {
            "leadgen_id": f"sim-{i}-{random.randint(10**8, 10**9)}",
            "form_id": "551", "page_id": "1000000000", "created_time": submitted,
            "campaign_name": campaign, "ad_name": ad,
            "field_data": [
                {"name": "full_name", "values": [name]},
                {"name": "phone_number", "values": [phone]},
                {"name": "what_would_you_like_to_ask", "values": [msg]},
            ],
        }}]}]}


def google_payload(i: int, name: str, email: str, msg: str, key: str) -> dict:
    return {
        "lead_id": f"g-sim-{i}-{random.randint(10**8, 10**9)}", "api_version": "1.0", "form_id": 77,
        "campaign_id": int(random.choice(list(GOOGLE_CAMPAIGNS))), "google_key": key, "is_test": True,
        "user_column_data": [
            {"column_id": "FULL_NAME", "column_name": "Full Name", "string_value": name},
            {"column_id": "EMAIL", "column_name": "User Email", "string_value": email},
            {"column_id": "SERVER_QUESTION_1", "column_name": "Your question", "string_value": msg},
        ],
    }


def whatsapp_payload(wa_id: str, name: str, text: str) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "contacts": [{"wa_id": wa_id, "profile": {"name": name}}],
        "messages": [{"from": wa_id, "id": f"wamid.{random.randint(10**8, 10**9)}", "type": "text",
                      "timestamp": str(int(time.time())), "text": {"body": text}}],
    }}]}]}


def run(url: str, count: int, google_key: str, seed: int) -> None:
    random.seed(seed)
    today = date.today()
    qs = questions(today)
    client = httpx.Client(base_url=url, timeout=30)
    phones = []
    for i in range(count):
        name = f"{random.choice(FIRST)} {random.choice(LAST)}"
        msg = random.choice(qs)
        days_ago = random.choices(range(14), weights=[5, 5, 4, 4, 3, 3, 3, 2, 2, 2, 2, 1, 1, 1])[0]
        submitted = int(time.time()) - days_ago * 86400 - random.randint(0, 80000)
        if random.random() < 0.65:
            phone = f"05{random.randint(10_000_000, 99_999_999)}"
            r = client.post("/webhooks/meta", json=meta_payload(i, name, phone, msg, submitted))
            phones.append((phone, name))
        else:
            email = name.lower().replace(" ", ".").replace("-", "") + "@example.com"
            r = client.post("/webhooks/google", json=google_payload(i, name, email, msg, google_key))
        r.raise_for_status()
        print(f"{r.status_code}  {name:<24} {msg[:60]}")

    # a few guests write back on WhatsApp
    for phone, name in random.sample(phones, k=min(4, len(phones))):
        text = random.choice(["Is breakfast included?", "Do you have parking?", "Ok thanks, what time is check in?"])
        client.post("/webhooks/whatsapp", json=whatsapp_payload("966" + phone[1:], name, text)).raise_for_status()

    # reservations closes some of the booking requests, loses a few
    for lead in client.get("/api/leads", params={"status": "needs_staff"}).json():
        roll = random.random()
        if roll < 0.45:
            client.post(f"/leads/{lead['id']}/status", data={"status": "booked"})
        elif roll < 0.55:
            client.post(f"/leads/{lead['id']}/status", data={"status": "lost"})

    leads = client.get("/api/metrics").json()
    print(f"\n{leads['total']} leads, {leads['open_handoffs']} waiting for staff")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--count", type=int, default=40)
    ap.add_argument("--google-key", default="dev-google-key")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    run(args.url, args.count, args.google_key, args.seed)
