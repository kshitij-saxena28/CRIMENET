"""Timeline + map logic: event categories, offline place gazetteer, coordinate fallback, gaps, filters.

Everything here is pure (no database access) so it can be unit-tested with plain dicts.
Coordinates are never invented: each event says where its map position came from (`geo_source`) and how precise it is.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from security.masking import MASKED_ATTRS, mask_identifier

log = logging.getLogger("dcn.timeline")

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# --------------------------------------------------------------------------- categories
CATEGORY_MAP = {
    "FIR_REGISTERED": "FIR / Legal", "OCCURRENCE_RECORDED": "Incident", "INVESTIGATION_ASSIGNED": "Investigation",
    "COMMUNICATION": "Communication", "CALL": "Communication", "CDR": "Communication", "SMS": "Communication",
    "FINANCIAL": "Financial", "TRANSACTION": "Financial", "PAYMENT": "Financial",
    "VEHICLE": "Vehicle", "VEHICLE_MOVEMENT": "Vehicle", "VEHICLE_SIGHTING": "Vehicle",
    "LOCATION": "Location", "LOCATION_EVENT": "Location", "VISIT": "Location",
    "MEETING": "Meeting", "MEET": "Meeting", "DOCUMENT": "Evidence", "EVIDENCE": "Evidence",
}
# Keyword fallback for event types typed in by table imports or FIR narrative detection.
_KEYWORDS = [
    (("CALL", "SMS", "MESSAGE", "CHAT", "COMM", "CDR", "WHATSAPP", "TELEGRAM"), "Communication"),
    (("TRANSACT", "PAYMENT", "TRANSFER", "DEPOSIT", "WITHDRAW", "FINANC", "UPI", "NEFT", "IMPS", "CASH"), "Financial"),
    (("VEHICLE", "SIGHT", "ANPR", "TOLL", "CHALLAN"), "Vehicle"),
    (("MEET",), "Meeting"),
    (("LOCATION", "VISIT", "ARRIVE", "DEPART", "TRAVEL", "MOVEMENT"), "Location"),
    (("FIR", "CHARGE", "ARREST", "COURT", "BAIL", "SEIZ", "RECOVER"), "FIR / Legal"),
    (("EVIDENCE", "DOCUMENT", "FORENSIC"), "Evidence"),
    (("THEFT", "ROBBERY", "ASSAULT", "MURDER", "FRAUD", "INCIDENT", "OCCURRENCE", "OFFENCE", "OFFENSE", "KIDNAP"), "Incident"),
]

# One colour per category (also used by the UI legend). Chosen to stay readable on dark and light backgrounds.
CATEGORY_ORDER = ["Communication", "Financial", "Meeting", "Vehicle", "Location", "Incident", "FIR / Legal", "Investigation", "Evidence", "Other"]


def categorize(event_type: Any) -> str:
    et = str(event_type or "EVENT").strip().upper().replace(" ", "_")
    if et in CATEGORY_MAP:
        return CATEGORY_MAP[et]
    for words, cat in _KEYWORDS:
        if any(w in et for w in words):
            return cat
    return "Other"


# --------------------------------------------------------------------------- dates
def parse_ts(raw: Any) -> datetime | None:
    """Parse the stored ISO timestamp. Times are kept exactly as recorded (naive); tz-aware values are converted to UTC."""
    if not raw:
        return None
    text = str(raw).strip()
    try:
        d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            import pandas as pd  # slow path for odd formats only
            d = pd.to_datetime(text, format="mixed", errors="raise").to_pydatetime()
        except (ValueError, TypeError, OverflowError):
            return None
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


# --------------------------------------------------------------------------- gazetteer
def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


_KIND_RANK = {"locality": 0, "city": 1, "state": 2}


class Gazetteer:
    """Offline lookup of Indian places (cities, district HQs, states, Delhi/NCR localities) -> approximate coordinates."""

    def __init__(self, places: list[dict]):
        self.places = places
        self.by_name: dict[str, dict] = {}
        for p in sorted(places, key=lambda p: (_KIND_RANK.get(p.get("kind"), 3), -int(p.get("pop") or 0))):
            for n in [p["name"], *(p.get("aliases") or [])]:
                self.by_name.setdefault(_norm(n), p)
        # Longest names first so "New Delhi" wins over "Delhi" when scanning free text. Very short aliases only match exactly.
        self._scan = sorted((k for k in self.by_name if len(k) >= 4), key=len, reverse=True)
        self._scan_re = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(k) for k in self._scan) + r")(?![a-z0-9])") if self._scan else None

    @classmethod
    def load(cls) -> "Gazetteer":
        try:
            data = json.loads((DATA_DIR / "gazetteer_in.json").read_text(encoding="utf-8"))
            return cls(list(data.get("places") or []))
        except (OSError, ValueError):
            log.warning("Gazetteer file missing or corrupt; place-name map fallback disabled")
            return cls([])

    def lookup(self, text: Any) -> dict | None:
        """Exact match of a whole place name (or alias)."""
        return self.by_name.get(_norm(text))

    def find_in_text(self, text: Any) -> dict | None:
        """Most specific known place mentioned in free text (e.g. 'Near Sector 21 Market, Noida, UP')."""
        t = _norm(text)
        if not t or not self._scan_re:
            return None
        exact = self.by_name.get(t)
        if exact:
            return exact
        best = None
        for m in self._scan_re.finditer(t):
            p = self.by_name[m.group(1)]
            # prefer localities/cities over states; among equals prefer the longest match
            key = (_KIND_RANK.get(p.get("kind"), 3) == 2, -len(m.group(1)))
            if best is None or key < best[0]:
                best = (key, p)
        return best[1] if best else None

    def search(self, q: str, limit: int = 10) -> list[dict]:
        n = _norm(q)
        if not n:
            return []
        seen, out = set(), []
        for pass_ in (0, 1):
            for key, p in self.by_name.items():
                ok = key.startswith(n) if pass_ == 0 else (n in key and not key.startswith(n))
                if ok and id(p) not in seen:
                    seen.add(id(p)); out.append(p)
                    if len(out) >= limit:
                        return out
        return out


_GAZ: Gazetteer | None = None


def gazetteer() -> Gazetteer:
    global _GAZ
    if _GAZ is None:
        _GAZ = Gazetteer.load()
    return _GAZ


def valid_coord(lat: Any, lon: Any) -> bool:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    if la != la or lo != lo:  # NaN
        return False
    return -90 <= la <= 90 and -180 <= lo <= 180 and not (la == 0 and lo == 0)


PLACE_KEYS = ("place", "place_of_occurrence", "location", "city", "address", "district", "area", "locality", "town")
PLACE_ENTITY_TYPES = {"LOCATION", "ADDRESS", "DISTRICT", "CITY", "PLACE"}


def resolve_geo(event: dict, entities: dict[str, dict], gaz: Gazetteer | None = None) -> dict | None:
    """Best map position for an event, with its provenance.

    Order: (1) coordinates recorded on the event, (2) the linked LOCATION entity that has coordinates,
    (3) a known place name found in the event's own place fields or in the names of linked location entities.
    Person/phone/account entity coordinates are deliberately NOT used: an entity's home is not where an event happened.
    """
    gaz = gaz or gazetteer()
    if valid_coord(event.get("latitude"), event.get("longitude")):
        return {"lat": float(event["latitude"]), "lon": float(event["longitude"]), "source": "event", "precision": "recorded", "place": ""}
    md = event.get("metadata") or {}
    linked_ids = [md.get("location_id"), event.get("entity_id"), event.get("related_entity_id")]
    for eid in linked_ids:
        ent = entities.get(eid) if eid else None
        if ent and str(ent.get("type", "")).upper() in PLACE_ENTITY_TYPES and valid_coord(ent.get("latitude"), ent.get("longitude")):
            return {"lat": float(ent["latitude"]), "lon": float(ent["longitude"]), "source": "linked_location", "precision": "recorded", "place": ent.get("name") or ""}
    texts: list[str] = [str(md[k]) for k in PLACE_KEYS if md.get(k)]
    for eid in linked_ids:
        ent = entities.get(eid) if eid else None
        if ent and str(ent.get("type", "")).upper() in PLACE_ENTITY_TYPES:
            texts.append(str(ent.get("name") or ""))
            attrs = ent.get("attributes") or {}
            texts += [str(attrs[k]) for k in ("city", "district", "address") if attrs.get(k)]
    for t in texts:
        p = gaz.find_in_text(t)
        if p:
            return {"lat": float(p["lat"]), "lon": float(p["lon"]), "source": "gazetteer", "precision": "approximate", "place": p["name"]}
    return None


# --------------------------------------------------------------------------- masking
def mask_metadata(md: dict) -> dict:
    out = {}
    for k, v in (md or {}).items():
        out[k] = mask_identifier(v) if str(k).lower() in MASKED_ATTRS and v else v
    return out


# --------------------------------------------------------------------------- gaps
def compute_gaps(events: list[dict], threshold_hours: float = 24.0, limit: int = 100) -> tuple[list[dict], int]:
    """Quiet periods between consecutive dated events, longest first. Returns (top gaps, total found)."""
    dated = [(parse_ts(e.get("event_time")), e) for e in events]
    dated = sorted(((t, e) for t, e in dated if t), key=lambda x: x[0])
    gaps = []
    for (ta, a), (tb, b) in zip(dated, dated[1:]):
        hours = (tb - ta).total_seconds() / 3600
        if hours >= threshold_hours:
            gaps.append({"start": ta.isoformat(), "end": tb.isoformat(), "from_event": a.get("event_id"), "to_event": b.get("event_id"),
                         "hours": round(hours, 2), "days": round(hours / 24, 2)})
    gaps.sort(key=lambda g: -g["hours"])
    return gaps[:limit], len(gaps)


# --------------------------------------------------------------------------- main builder
def build_timeline(events: list[dict], entities: dict[str, dict], fir_docs: dict[str, str], rel_count: int, case_number: str | None,
                   *, masked: bool = False, category: str | None = None, fir_number: str | None = None, q: str | None = None,
                   date_from: str | None = None, date_to: str | None = None, geo_only: bool = False,
                   gap_hours: float = 24.0, limit: int = 5000) -> dict:
    gaz = gazetteer()
    enriched: list[dict] = []
    for e in events:
        x = dict(e)
        md = e.get("metadata") or {}
        ent = entities.get(e.get("entity_id")) or {}
        rel = entities.get(e.get("related_entity_id")) or {}
        x["entity_name"] = ent.get("name") or e.get("entity_id") or ""
        x["related_entity_name"] = (rel.get("name") or e.get("related_entity_id") or "") if e.get("related_entity_id") else ""
        x["entity_type"] = ent.get("type") or ""
        x["related_entity_type"] = rel.get("type") or "" if e.get("related_entity_id") else ""
        x["fir_number"] = md.get("fir_number") or fir_docs.get(md.get("document_id"), "")
        x["category"] = categorize(e.get("event_type"))
        x["label"] = x["category"] + " · " + str(e.get("event_type") or "Event").replace("_", " ").title()
        try:
            x["confidence"] = float(md.get("confidence", 1.0) or 1.0)
        except (TypeError, ValueError):
            x["confidence"] = 1.0
        ts = parse_ts(e.get("event_time"))
        x["date_precision"] = "day" if ts else "unknown"
        x["evidence_status"] = "source indexed" if e.get("source_ref") else "no source reference"
        geo = resolve_geo(e, entities, gaz)
        # `latitude`/`longitude` stay as recorded; the map uses map_lat/map_lon and says where they came from.
        x["map_lat"], x["map_lon"] = (geo["lat"], geo["lon"]) if geo else (None, None)
        x["geo_source"] = geo["source"] if geo else "none"
        x["geo_precision"] = geo["precision"] if geo else "none"
        x["geo_place"] = geo["place"] if geo else ""
        if masked:
            x["metadata"] = mask_metadata(md)
        enriched.append(x)

    enriched.sort(key=lambda x: (x.get("event_time") or "9999", x.get("event_id") or ""))
    for i, x in enumerate(enriched, 1):
        x["sequence"] = i

    # ---- optional server-side filters (the web UI filters in the browser but API users and reports can ask here)
    sel = enriched
    if category:
        sel = [x for x in sel if x["category"].lower() == category.lower()]
    if fir_number:
        want = "" if fir_number.lower() == "unassigned" else fir_number
        sel = [x for x in sel if (x["fir_number"] or "") == want]
    df, dtt = parse_ts(date_from), parse_ts(date_to)
    if dtt and len(str(date_to).strip()) <= 10:  # a bare date means "through the end of that day"
        dtt = dtt.replace(hour=23, minute=59, second=59)
    if df or dtt:
        def in_range(x):
            t = parse_ts(x.get("event_time"))
            return bool(t) and (not df or t >= df) and (not dtt or t <= dtt)
        sel = [x for x in sel if in_range(x)]
    if q:
        needle = q.lower()
        sel = [x for x in sel if needle in " ".join(str(x.get(k) or "") for k in ("label", "entity_name", "related_entity_name", "entity_id", "related_entity_id", "source_ref", "fir_number", "event_id", "geo_place")).lower()
               or needle in json.dumps(x.get("metadata") or {}, default=str).lower()]
    if geo_only:
        sel = [x for x in sel if x["map_lat"] is not None]

    # ---- daily buckets
    buckets: dict[str, dict] = {}
    for x in sel:
        t = parse_ts(x.get("event_time"))
        if not t:
            continue
        b = buckets.setdefault(t.date().isoformat(), {"events": 0, "entities": set(), "types": Counter(), "categories": Counter()})
        b["events"] += 1
        for eid in (x.get("entity_id"), x.get("related_entity_id")):
            if eid:
                b["entities"].add(eid)
        b["types"][x.get("event_type") or "EVENT"] += 1
        b["categories"][x["category"]] += 1
    daily = [{"date": d, "events": v["events"], "active_entities": len(v["entities"]), "event_types": dict(v["types"]), "categories": dict(v["categories"])}
             for d, v in sorted(buckets.items())]

    gaps, gaps_total = compute_gaps(sel, gap_hours)

    fir_summary: dict[str, dict] = {}
    for x in sel:
        fir = x.get("fir_number") or "Unassigned"
        z = fir_summary.setdefault(fir, {"events": 0, "categories": Counter(), "first_event": None, "last_event": None})
        z["events"] += 1
        z["categories"][x["category"]] += 1
        if x.get("event_time"):
            z["first_event"] = z["first_event"] or x["event_time"]
            z["last_event"] = x["event_time"]
    fir_summary = {k: {**v, "categories": dict(v["categories"])} for k, v in fir_summary.items()}

    geo_counts = Counter(x["geo_source"] for x in sel)
    total_sel = len(sel)
    truncated = total_sel > limit
    shown = sel[:limit]
    return {
        "case_number": case_number,
        "event_count": total_sel,
        "dated_event_count": sum(1 for x in sel if x.get("event_time")),
        "mapped_event_count": total_sel - geo_counts.get("none", 0),
        "geo_sources": dict(geo_counts),
        "returned_events": len(shown),
        "truncated": truncated,
        "daily": daily,
        "events": shown,
        "relationship_count": rel_count,
        "categories": [c for c in CATEGORY_ORDER if any(x["category"] == c for x in sel)] + sorted({x["category"] for x in sel} - set(CATEGORY_ORDER)),
        "fir_summary": fir_summary,
        "gaps": gaps,
        "gaps_total": gaps_total,
        "gap_hours": gap_hours,
        "masked": masked,
        "median_gap_hours": _median_gap(sel),
    }


def _median_gap(events: list[dict]) -> float | None:
    ts = sorted(t for t in (parse_ts(e.get("event_time")) for e in events) if t)
    if len(ts) < 3:
        return None
    return round(statistics.median((b - a).total_seconds() / 3600 for a, b in zip(ts, ts[1:])), 3)
