"""Small, dependency-free time helpers shared by the social-media and surveillance modules.

Time zones are fixed offsets (no tz database needed, so it works on any offline workstation). India has no daylight saving.
Everything is stored as naive UTC; the viewer picks a display zone.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

ZONES = {"IST": 330, "UTC": 0, "GMT": 0, "GST": 240, "PKT": 300, "BST": 360, "SGT": 480, "JST": 540, "EST": -300, "PST": -480}
DEFAULT_ZONE = "IST"
_OFFSET = re.compile(r"^([+-])(\d{1,2}):?(\d{2})?$")


def zone_minutes(tz: Any) -> int:
    """Offset in minutes for 'IST', 'UTC', '+05:30', '-0800' ... Unknown values fall back to IST."""
    s = str(tz or DEFAULT_ZONE).strip().upper()
    if s in ZONES:
        return ZONES[s]
    m = _OFFSET.match(s)
    if m:
        mins = int(m.group(2)) * 60 + int(m.group(3) or 0)
        if mins <= 14 * 60:
            return mins if m.group(1) == "+" else -mins
    return ZONES[DEFAULT_ZONE]


def zone_label(tz: Any) -> str:
    s = str(tz or DEFAULT_ZONE).strip().upper()
    return s if s in ZONES else (s if _OFFSET.match(s) else DEFAULT_ZONE)


def parse_ts(value: Any, assume_tz: Any = DEFAULT_ZONE) -> datetime | None:
    """Parse many timestamp spellings into NAIVE UTC. Naive inputs are assumed to be in ``assume_tz`` (default IST)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        d = value
    elif isinstance(value, (int, float)) or re.fullmatch(r"\d{9,13}(\.\d+)?", str(value).strip()):
        x = float(value)
        if x > 1e11:  # milliseconds
            x /= 1000.0
        try:
            return datetime.fromtimestamp(x, tz=timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        s = str(value).strip()
        d = None
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError:
            for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y %H:%M",
                        "%d %b %Y", "%b %d, %Y %H:%M", "%b %d, %Y", "%a %b %d %H:%M:%S %z %Y", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
                try:
                    d = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            if d is None:
                try:
                    from dateutil import parser as _dp
                    d = _dp.parse(s, dayfirst=True)
                except (ValueError, OverflowError, TypeError, ImportError):
                    return None
    if d.tzinfo is None:
        d = d - timedelta(minutes=zone_minutes(assume_tz))
    else:
        d = d.astimezone(timezone.utc)
    return d.replace(tzinfo=None)


def to_local(d: datetime, tz: Any = DEFAULT_ZONE) -> datetime:
    return d + timedelta(minutes=zone_minutes(tz))


def iso_utc(d: datetime | None) -> str:
    return d.replace(microsecond=0).isoformat() + "Z" if d else ""


def fmt_local(d: datetime | None, tz: Any = DEFAULT_ZONE) -> str:
    return f"{to_local(d, tz):%d %b %Y %H:%M} {zone_label(tz)}" if d else ""
