from datetime import datetime, timedelta, timezone
from statistics import median


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def dashboard(conn, days: int = 14) -> dict:
    leads = [dict(r) for r in conn.execute("SELECT * FROM leads")]
    total = len(leads)
    by_status = {s: 0 for s in ("new", "replied", "needs_staff", "booked", "lost")}
    for lead in leads:
        by_status[lead["status"]] = by_status.get(lead["status"], 0) + 1

    response_secs = [
        (_ts(l["first_reply_at"]) - _ts(l["created_at"])).total_seconds()
        for l in leads if l["first_reply_at"]
    ]

    campaigns = {}
    for lead in leads:
        key = (lead["source"], lead["campaign"] or "(unknown)")
        c = campaigns.setdefault(key, {"source": key[0], "campaign": key[1], "leads": 0, "booked": 0, "needs_staff": 0})
        c["leads"] += 1
        c["booked"] += lead["status"] == "booked"
        c["needs_staff"] += lead["status"] == "needs_staff"
    for c in campaigns.values():
        c["conversion"] = round(100 * c["booked"] / c["leads"], 1) if c["leads"] else 0.0

    intents = {}
    for row in conn.execute("SELECT intent, COUNT(*) n FROM messages WHERE direction='in' AND intent IS NOT NULL GROUP BY intent"):
        intents[row["intent"]] = row["n"]

    today = datetime.now(timezone.utc).date()
    daily = {(today - timedelta(days=i)).isoformat(): 0 for i in range(days - 1, -1, -1)}
    for lead in leads:
        d = (lead["submitted_at"] or lead["created_at"])[:10]
        if d in daily:
            daily[d] += 1

    return {
        "total": total,
        "by_status": by_status,
        "booked_rate": round(100 * by_status["booked"] / total, 1) if total else 0.0,
        "median_response_secs": round(median(response_secs), 1) if response_secs else None,
        "auto_resolved": by_status["replied"],
        "campaigns": sorted(campaigns.values(), key=lambda c: -c["leads"]),
        "intents": dict(sorted(intents.items(), key=lambda kv: -kv[1])),
        "daily": daily,
        "open_handoffs": conn.execute("SELECT COUNT(*) FROM handoffs WHERE resolved = 0").fetchone()[0],
    }
