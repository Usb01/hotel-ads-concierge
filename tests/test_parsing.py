from datetime import date

from app.hotel import Hotel, Stay, parse_guests, parse_stay
from app.config import ROOT

TODAY = date(2026, 10, 20)
hotel = Hotel.load(str(ROOT / "data" / "hotel.json"))


def test_iso_range():
    s = parse_stay("arriving 2026-11-12 leaving 2026-11-14", TODAY)
    assert (s.check_in, s.check_out, s.nights) == (date(2026, 11, 12), date(2026, 11, 14), 2)


def test_day_month_slash_is_day_first():
    s = parse_stay("12/11 - 15/11 please", TODAY)
    assert s.check_in == date(2026, 11, 12) and s.nights == 3


def test_short_range_with_month_name():
    s = parse_stay("we want 12-14 nov", TODAY)
    assert s.check_in == date(2026, 11, 12) and s.check_out == date(2026, 11, 14)


def test_single_date_plus_nights():
    s = parse_stay("Nov 3rd for three nights", TODAY)
    assert s.check_in == date(2026, 11, 3) and s.nights == 3


def test_past_month_rolls_to_next_year():
    s = parse_stay("5 Feb for 2 nights", TODAY)
    assert s.check_in == date(2027, 2, 5)


def test_no_dates():
    assert parse_stay("do you have parking?", TODAY) is None


def test_guests():
    assert parse_guests("2 adults and 1 child") == 3
    assert parse_guests("family of 5") == 5
    assert parse_guests("just me") is None


def test_weekend_nights_cost_more():
    # Thu 2026-11-12 and Fri 2026-11-13 are weekend nights for this hotel
    q = {x.room_code: x for x in hotel.quote(Stay(date(2026, 11, 11), date(2026, 11, 14)))}
    assert q["DLX"].total == 520 + 610 + 610


def test_rooms_filtered_by_guest_count():
    codes = {q.room_code for q in hotel.quote(Stay(date(2026, 11, 1), date(2026, 11, 2), guests=4))}
    assert codes == {"FAM"}


def test_sold_out_dates():
    q = {x.room_code: x for x in hotel.quote(Stay(date(2026, 12, 30), date(2027, 1, 2)))}
    assert q["DLX"].available and not q["FAM"].available and not q["SEA"].available
