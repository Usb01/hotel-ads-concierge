import json
import logging
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import metrics
from .config import Settings, settings as default_settings
from .db import connect
from .hotel import Hotel
from .service import Concierge
from .sources import google, meta, whatsapp

log = logging.getLogger("concierge")
HERE = Path(__file__).parent


def create_app(settings: Settings = default_settings) -> FastAPI:
    app = FastAPI(title="Hotel Ads Concierge")
    conn = connect(settings.db_path)
    hotel = Hotel.load(settings.hotel_file)
    concierge = Concierge(conn, hotel, settings)
    templates = Jinja2Templates(directory=HERE / "templates")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.state.concierge = concierge

    # ---- webhooks ---------------------------------------------------------

    @app.get("/webhooks/meta")
    def meta_verify(request: Request):
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == settings.meta_verify_token:
            return PlainTextResponse(q.get("hub.challenge", ""))
        raise HTTPException(403, "verification failed")

    @app.post("/webhooks/meta")
    async def meta_leads(request: Request):
        raw = await request.body()
        if not meta.verify_signature(raw, request.headers.get("x-hub-signature-256"), settings.meta_app_secret):
            raise HTTPException(401, "bad signature")
        payload = json.loads(raw)
        results = []
        for event in meta.iter_leadgen_events(payload):
            if settings.meta_page_token:
                details = meta.fetch_lead(event["leadgen_id"], settings.meta_page_token)
            else:
                details = event  # local simulation carries field_data inline
            results.append(concierge.ingest_lead(meta.normalize(event, details)))
        return {"processed": len(results), "results": results}

    @app.post("/webhooks/google")
    async def google_leads(request: Request):
        payload = await request.json()
        if not google.key_is_valid(payload, settings.google_lead_key):
            raise HTTPException(401, "bad google_key")
        lead = google.normalize(payload, hotel.data.get("google_campaigns"))
        return concierge.ingest_lead(lead)

    @app.get("/webhooks/whatsapp")
    def whatsapp_verify(request: Request):
        return meta_verify(request)

    @app.post("/webhooks/whatsapp")
    async def whatsapp_inbound(request: Request):
        raw = await request.body()
        if not meta.verify_signature(raw, request.headers.get("x-hub-signature-256"), settings.meta_app_secret):
            raise HTTPException(401, "bad signature")
        out = [concierge.guest_message(m["wa_id"], m["text"], m["name"])
               for m in whatsapp.iter_text_messages(json.loads(raw))]
        return {"processed": len(out), "results": out}

    # ---- dashboard --------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        data = metrics.dashboard(conn)
        handoffs = conn.execute(
            "SELECT h.*, l.name, l.source, l.campaign FROM handoffs h JOIN leads l ON l.id = h.lead_id "
            "WHERE h.resolved = 0 ORDER BY h.id DESC LIMIT 20").fetchall()
        recent = conn.execute("SELECT * FROM leads ORDER BY id DESC LIMIT 12").fetchall()
        return templates.TemplateResponse(request, "dashboard.html",
                                          {"m": data, "handoffs": handoffs, "recent": recent, "hotel": hotel,
                                           "settings": settings})

    @app.get("/leads/{lead_id}", response_class=HTMLResponse)
    def lead_detail(request: Request, lead_id: int):
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            raise HTTPException(404)
        msgs = conn.execute("SELECT * FROM messages WHERE lead_id = ? ORDER BY id", (lead_id,)).fetchall()
        open_handoff = conn.execute("SELECT * FROM handoffs WHERE lead_id = ? AND resolved = 0", (lead_id,)).fetchone()
        return templates.TemplateResponse(request, "lead.html",
                                          {"lead": lead, "messages": msgs, "handoff": open_handoff, "hotel": hotel})

    @app.post("/leads/{lead_id}/reply")
    def lead_reply(lead_id: int, body: str = Form(...)):
        concierge.staff_reply(lead_id, body.strip())
        return RedirectResponse(f"/leads/{lead_id}", status_code=303)

    @app.post("/leads/{lead_id}/status")
    def lead_status(lead_id: int, status: str = Form(...)):
        concierge.set_status(lead_id, status)
        return RedirectResponse(f"/leads/{lead_id}", status_code=303)

    @app.get("/api/metrics")
    def api_metrics():
        return metrics.dashboard(conn)

    @app.get("/api/leads")
    def api_leads(status: str | None = None, limit: int = 100):
        sql, args = "SELECT * FROM leads", []
        if status:
            sql, args = sql + " WHERE status = ?", [status]
        rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", [*args, limit]).fetchall()
        return [dict(r) for r in rows]

    @app.get("/health")
    def health():
        return {"ok": True, "llm": settings.llm_provider, "dry_run": settings.dry_run}

    return app


app = create_app()
