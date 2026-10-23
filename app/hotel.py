import json
import re
from dataclasses import dataclass
from datetime import date, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


@dataclass
class Stay:
    check_in: date
    check_out: date
    guests: int | None = None

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


@dataclass
class Quote:
    room_code: str
    room_name: str
    nights: int
    total: int
    available: bool
    highlights: str


class Hotel:
    def __init__(self, data: dict):
        self.data = data
        self.name = data["name"]
        self.currency = data.get("currency", "SAR")
        self.rooms = {r["code"]: r for r in data["rooms"]}
        self.weekend = set(data.get("weekend_nights", ["Fri", "Sat"]))

    @classmethod
    def load(cls, path: str) -> "Hotel":
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    # --- policies ---------------------------------------------------------

    def match_policies(self, text: str, limit: int = 2) -> list[dict]:
        text = text.lower()
        scored = []
        for policy in self.data.get("policies", []):
            hits = sum(1 for kw in policy["keywords"] if kw in text)
            if hits:
                scored.append((hits, policy))
        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored[:limit]]

    # --- rates and availability ------------------------------------------

    def nightly_rate(self, room: dict, night: date) -> int:
        if WEEKDAYS[night.weekday()] in self.weekend:
            return room["weekend_rate"]
        return room["base_rate"]

    def is_sold_out(self, code: str, night: date) -> bool:
        return night.isoformat() in self.data.get("sold_out", {}).get(code, [])

    def quote(self, stay: Stay) -> list[Quote]:
        quotes = []
        for room in self.data["rooms"]:
            if stay.guests and stay.guests > room["max_guests"]:
                continue
            nights = [stay.check_in + timedelta(days=i) for i in range(stay.nights)]
            total = sum(self.nightly_rate(room, n) for n in nights)
            available = not any(self.is_sold_out(room["code"], n) for n in nights)
            quotes.append(Quote(room["code"], room["name"], stay.nights, total, available, room["highlights"]))
        return quotes

    def known_prices(self) -> set[int]:
        """Every number the bot is allowed to quote as a price."""
        prices = set()
        for room in self.data["rooms"]:
            prices.update({room["base_rate"], room["weekend_rate"]})
        for policy in self.data.get("policies", []):
            prices.update(int(n) for n in re.findall(r"SAR\s?(\d+)", policy["answer"]))
        return prices


# --- parsing helpers ---------------------------------------------------------

def _year_for(month: int, day: int, today: date) -> int:
    candidate = date(today.year, month, day)
    return today.year + 1 if candidate < today else today.year


def parse_stay(text: str, today: date | None = None) -> Stay | None:
    """Pull check-in / check-out dates out of free text.

    Handles the formats guests actually type in ad forms and WhatsApp:
    "2026-11-12 to 2026-11-14", "12/11 - 14/11", "12-14 Nov", "Nov 12 for 3 nights".
    Day/month order is assumed (Saudi / UK style).
    """
    today = today or date.today()
    t = text.lower()
    found: list[date] = []

    for y, m, d in re.findall(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
        found.append(date(int(y), int(m), int(d)))

    if not found:
        for d, m, y in re.findall(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t):
            d, m = int(d), int(m)
            if not (1 <= m <= 12 and 1 <= d <= 31):
                continue
            year = int(y) if y else _year_for(m, d, today)
            if year < 100:
                year += 2000
            found.append(date(year, m, d))

    if not found:
        # "12-14 nov", "12 to 14 november"
        rng = re.search(r"\b(\d{1,2})\s*(?:-|–|to|till|until)\s*(\d{1,2})\s*([a-z]{3})[a-z]*", t)
        if rng and rng.group(3) in MONTHS:
            m = MONTHS[rng.group(3)]
            d1, d2 = int(rng.group(1)), int(rng.group(2))
            y = _year_for(m, d1, today)
            found = [date(y, m, d1), date(y, m, d2)]

    if not found:
        for d, mon in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*", t):
            if mon in MONTHS:
                found.append(date(_year_for(MONTHS[mon], int(d), today), MONTHS[mon], int(d)))
        for mon, d in re.findall(r"\b([a-z]{3})[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b", t):
            if mon in MONTHS:
                found.append(date(_year_for(MONTHS[mon], int(d), today), MONTHS[mon], int(d)))

    if not found:
        return None

    found = sorted(set(found))
    check_in = found[0]
    if len(found) > 1:
        check_out = found[-1]
    else:
        nights = re.search(r"(\d+|one|two|three|four|five|six)\s+nights?", t)
        n = 1
        if nights:
            n = int(nights.group(1)) if nights.group(1).isdigit() else WORD_NUMS[nights.group(1)]
        check_out = check_in + timedelta(days=n)

    if check_out <= check_in:
        return None
    return Stay(check_in, check_out, parse_guests(t))


def parse_guests(text: str) -> int | None:
    t = text.lower()
    total = 0
    for n, _ in re.findall(r"(\d+|one|two|three|four|five|six)\s+(adults?|kids?|children|child|people|persons?|guests?|pax)", t):
        total += int(n) if n.isdigit() else WORD_NUMS[n]
    fam = re.search(r"family of (\d+|one|two|three|four|five|six)", t)
    if fam:
        n = fam.group(1)
        total = max(total, int(n) if n.isdigit() else WORD_NUMS[n])
    return total or None
