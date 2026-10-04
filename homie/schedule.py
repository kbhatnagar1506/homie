"""When does a leasing office next open? Parsed from its Google hours, in Atlanta time."""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _opening(hours: list[str], day: str) -> tuple[int, int] | None:
    line = next((h for h in hours if h.startswith(day)), "")
    if "Closed" in line or "Open 24 hours" in line:
        return (0, 0) if "24 hours" in line else None
    times = re.findall(r"(\d{1,2}):(\d{2})\s*([AP]M)?", line.replace("\u202f", " "))
    if not times:
        return None
    h, m, meridiem = times[0]
    if not meridiem:  # "1:00 – 5:00 PM": the opening shares the closing's AM/PM unless that would put it after closing
        close_h, _, close_meridiem = times[-1] if len(times) > 1 else (h, m, "AM")
        meridiem = close_meridiem or "AM"
        if meridiem == "PM" and int(h) % 12 > int(close_h) % 12:
            meridiem = "AM"
    return int(h) % 12 + (12 if meridiem == "PM" else 0), int(m)


def next_open(hours: list[str], now: datetime | None = None) -> datetime:
    now = now or datetime.now(TZ)
    for offset in range(0, 8):
        day = now + timedelta(days=offset)
        opening = _opening(hours, DAYS[day.weekday()]) if hours else None
        if opening is None and not hours and day.weekday() < 5:
            opening = (10, 0)
        if opening is None:
            continue
        at = day.replace(hour=opening[0], minute=opening[1], second=0, microsecond=0) + timedelta(minutes=15)
        if at > now:
            return at
    return (now + timedelta(days=1)).replace(hour=10, minute=15, second=0, microsecond=0)


def human(at: datetime) -> str:
    today = datetime.now(TZ).date()
    day = "today" if at.date() == today else ("tomorrow" if at.date() == today + timedelta(days=1) else at.strftime("%A"))
    return f"{day} at {at.strftime('%-I:%M %p')} ET"
