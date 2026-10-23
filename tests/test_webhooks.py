import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    s = Settings()
    s.db_path = str(tmp_path / "test.db")
    s.meta_app_secret = "shh"
    s.meta_page_token = ""
    s.llm_provider = "none"
    s.dry_run = True
    return TestClient(create_app(s))


def signed(body: dict, secret="shh"):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"x-hub-signature-256": sig, "content-type": "application/json"}


def meta_event(leadgen_id="L1", phone="0551234567", question="Is there parking?"):
    return {"object": "page", "entry": [{"changes": [{"field": "leadgen", "value": {
        "leadgen_id": leadgen_id, "campaign_name": "Winter Escape", "ad_name": "Carousel", "created_time": 1792000000,
        "field_data": [{"name": "full_name", "values": ["Omar Haddad"]},
                       {"name": "phone_number", "values": [phone]},
                       {"name": "your_question", "values": [question]}]}}]}]}


def test_meta_subscription_handshake(client):
    r = client.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "dev-verify-token",
                                             "hub.challenge": "1234"})
    assert r.text == "1234"
    assert client.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "nope"}).status_code == 403


def test_meta_rejects_bad_signature(client):
    raw, headers = signed(meta_event(), secret="wrong")
    assert client.post("/webhooks/meta", content=raw, headers=headers).status_code == 401


def test_meta_lead_gets_whatsapp_reply(client):
    raw, headers = signed(meta_event())
    r = client.post("/webhooks/meta", content=raw, headers=headers).json()
    res = r["results"][0]
    assert res["intent"] == "question" and not res["needs_staff"] and res["delivery"] == "dry_run"

    conn = client.app.state.concierge.conn
    out = conn.execute("SELECT channel, recipient FROM outbox").fetchone()
    assert tuple(out) == ("whatsapp", "966551234567")


def test_duplicate_webhook_is_ignored(client):
    raw, headers = signed(meta_event("SAME"))
    client.post("/webhooks/meta", content=raw, headers=headers)
    r = client.post("/webhooks/meta", content=raw, headers=headers).json()
    assert r["results"][0]["duplicate"] is True
    assert client.app.state.concierge.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_google_lead_requires_key(client):
    body = {"lead_id": "g1", "google_key": "wrong", "user_column_data": []}
    assert client.post("/webhooks/google", json=body).status_code == 401


def test_google_lead_email_reply(client):
    body = {"lead_id": "g2", "google_key": "dev-google-key", "campaign_id": 99,
            "user_column_data": [{"column_id": "FULL_NAME", "string_value": "Lina Nair"},
                                 {"column_id": "EMAIL", "string_value": "lina@example.com"},
                                 {"column_id": "SERVER_Q", "column_name": "Question", "string_value": "Can I bring my dog?"}]}
    r = client.post("/webhooks/google", json=body).json()
    assert "pets" in r["reply"].lower()
    out = client.app.state.concierge.conn.execute("SELECT channel FROM outbox").fetchone()
    assert out[0] == "email"


def test_bot_stays_quiet_while_staff_handles_guest(client):
    raw, headers = signed(meta_event("C1", question="The room was dirty, very disappointed"))
    client.post("/webhooks/meta", content=raw, headers=headers)

    wa = {"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "966551234567", "profile": {"name": "Omar"}}],
        "messages": [{"from": "966551234567", "id": "w1", "type": "text", "text": {"body": "Hello??"}}]}}]}]}
    raw, headers = signed(wa)
    r = client.post("/webhooks/whatsapp", content=raw, headers=headers).json()
    assert r["results"][0]["held_for_staff"] is True


def test_dashboard_renders(client):
    raw, headers = signed(meta_event())
    client.post("/webhooks/meta", content=raw, headers=headers)
    html = client.get("/").text
    assert "Winter Escape" in html and "Campaign performance" in html
    assert client.get("/leads/1").status_code == 200


def test_staff_reply_resolves_handoff(client):
    raw, headers = signed(meta_event("S1", question="Is there a mosque nearby?"))
    client.post("/webhooks/meta", content=raw, headers=headers)
    client.post("/leads/1/reply", data={"body": "Yes, 3 minutes walk."})
    conn = client.app.state.concierge.conn
    assert conn.execute("SELECT COUNT(*) FROM handoffs WHERE resolved = 0").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM leads").fetchone()[0] == "replied"
