"""Label-driven FIR field parser (English, Hinglish, Hindi, Marathi and the labels of other Indian scripts).

Every label written anywhere in the text (one per line, or several on the same line) is located first; the value
of a field is whatever sits between its label and the next label. This is far more tolerant of the layouts seen in
real FIRs than one regex per template:

    District: Ghaziabad    P.S.: Indirapuram    Year: 2026    FIR No: 0142/2026
    थाना: कोतवाली नगर, जिला: जयपुर
    Thana: Sadar Bazar, Zila: Delhi

Values are returned together with their character offsets so the UI can show the source span.
"""
from __future__ import annotations

import re
from typing import Any

from ai_engine.nlp import labels as LB
from ai_engine.nlp import normalize as N

_SINGLE_LINE = {"fir_number", "district", "police_station", "year", "occurrence_date", "fir_date", "fir_date_weak", "time", "complainant", "father_name", "investigating_officer",
                "recorded_by", "mobile", "name", "victim", "witness", "sections"}
_PREFIX = r"(?:(?<![^\W\d_])|(?<=\d))"

_LABEL_TABLE: list[tuple[str, str, bool, re.Pattern]] = []
for _field, _labels, _weak in LB.FIELD_LABELS:
    for _lab in _labels:
        bare = re.sub(r"[.\s]", "", _lab)
        weak = _weak or len(bare) <= 3
        rx = LB.label_regex(_lab)
        _LABEL_TABLE.append((_field, _lab, weak, re.compile(rf"(?<![A-Za-z0-9\u0900-\u097F\u0980-\u0DFF\u0600-\u06FF]){rx}(?![A-Za-z\u0900-\u0DFF]{{2}})", re.I)))
_LABEL_TABLE.append(("investigating_officer", "I0", True, re.compile(r"(?<![A-Za-z0-9])I0(?![A-Za-z0-9])")))


def _find_labels(text: str) -> list[dict[str, Any]]:
    hits = []
    for field, lab, weak, rx in _LABEL_TABLE:
        for m in rx.finditer(text):
            tail = text[m.end():m.end() + 8]
            sep = re.match(r"\s*[:\-–—]+\s*", tail)
            if weak and not sep:
                # weak labels need an explicit ':' / '-'; Hinglish 'dinank 07/09/2026' style dates are handled separately
                if not (field in ("fir_date",) and re.match(r"\s*\d", tail)):
                    continue
            # a ':' / '-' directly after the label is consumed; a bare '.' too
            j = m.end() + (sep.end() if sep else 0)
            j += len(re.match(r"[ \t]*", text[j:]).group(0))
            hits.append({"field": field, "label": lab, "start": m.start(), "lend": m.end(), "vstart": j, "weak": weak, "explicit_sep": bool(sep)})
    hits.sort(key=lambda h: (h["start"], -(h["lend"] - h["start"])))
    out: list[dict[str, Any]] = []
    for h in hits:
        if out and h["start"] < out[-1]["lend"]:
            continue  # inside the previous label
        out.append(h)
    # a label whose value slot is immediately another label ('6. Complainant / Informant:' then '- Name:') is fine; keep all
    return out


def _clean_value(v: str) -> str:
    v = re.sub(r"\s+", " ", v or "")
    v = re.sub(r"^[\s:\-–—|•.]+", "", v)
    v = re.sub(r"\s*\b\d{1,2}\.\s*$", "", v)           # trailing numbering of the next section ("... 10.")
    v = re.sub(r"[\s,;:\-–—|.]+$", "", v)
    return v.strip()


def scan_fields(text: str) -> list[dict[str, Any]]:
    """Return one record per label hit: field, label, value, value span (offsets in ``text``)."""
    hits = _find_labels(text)
    recs = []
    for i, h in enumerate(hits):
        nxt = hits[i + 1]["start"] if i + 1 < len(hits) else len(text)
        vs = h["vstart"]
        ve = nxt
        field = h["field"]
        if field in _SINGLE_LINE:
            nl = text.find("\n", vs, ve)
            if nl >= 0:
                ve = nl
            if not text[vs:ve].strip():
                # value is on the next non-empty line
                m = re.match(r"\s*\n\s*(?:[-•*]\s*)?", text[vs:])
                if m and (vs + m.end()) < len(text):
                    ns = vs + m.end()
                    ne = text.find("\n", ns)
                    ne = len(text) if ne < 0 else ne
                    # do not steal the next label's line
                    if not any(hh["start"] <= ns < hh["lend"] for hh in hits):
                        vs, ve = ns, min(ne, nxt if nxt > ns else ne)
        raw = text[vs:ve]
        recs.append({"field": field, "label": h["label"], "value": _clean_value(raw), "start": vs, "end": ve, "label_start": h["start"], "weak": h["weak"], "sep": h["explicit_sep"]})
    return recs


def _first(recs, *fields):
    for r in recs:
        if r["field"] in fields and r["value"]:
            return r
    return None


def _fir_number(value: str) -> str:
    m = re.search(r"(?:[A-Za-z]{1,5}[\-/])?[0-9OoIl]+(?:\s?[/\-]\s?(?:[0-9OoIl]{1,6}))*", value)
    if not m:
        return ""
    tok = re.sub(r"\s+", "", m.group(0))
    parts = re.split(r"([/\-])", tok)
    fixed = []
    for p in parts:
        if re.fullmatch(r"[0-9OoIl]+", p) and any(c.isdigit() for c in p):
            fixed.append(N.ocr_digits(p))
        else:
            fixed.append(p)
    return "".join(fixed)


def _date_in(value: str) -> dict | None:
    for d in N.find_dates(value):
        if d["normalized"]:
            return d
    return None


def _time_in(value: str) -> str:
    ts = N.find_times(value)
    return ts[0]["normalized"] if ts else ""


def _place(v: str) -> str:
    return _clean_value(v)


def parse_fields(text: str) -> dict[str, Any]:
    """text must have ASCII digits (offset-preserving). Returns structured fields + provenance."""
    recs = scan_fields(text)
    out: dict[str, Any] = {}
    prov: dict[str, dict[str, Any]] = {}

    def setf(name: str, value: str, rec: dict | None, extra: dict | None = None):
        if value and name not in out:
            out[name] = value
            if rec is not None:
                prov[name] = {"start": rec["start"], "end": rec["end"], "label": rec["label"], **(extra or {})}

    r = _first(recs, "fir_number")
    if r:
        setf("fir_number", _fir_number(r["value"]), r)
    r = _first(recs, "district")
    if r:
        setf("district", re.sub(r"\s+(?:P\.?S\.?|Police\s+Station).*$", "", r["value"]).strip(), r)
    r = _first(recs, "police_station")
    if r:
        setf("police_station", r["value"], r)
    r = _first(recs, "year")
    if r:
        m = re.search(r"[0-9OoIl]{4}", r["value"])
        if m:
            setf("year", N.ocr_digits(m.group(0)), r)
    # dates: a "Date:" written with a colon beats a bare "दिनांक 05/10/2026" that sits inside the incident narrative
    cands = []
    seen_facts = False
    for rec in recs:
        if rec["field"] == "facts":
            seen_facts = True
        elif rec["field"] in ("fir_date", "fir_date_weak"):
            d = _date_in(rec["value"]) or (_date_in(text[rec["start"]: rec["start"] + 60].split("\n")[0]) if rec["field"] == "fir_date" else None)
            if d:
                cands.append(((seen_facts, not rec["sep"], rec["field"] == "fir_date_weak", rec["start"]), rec, d))
    if cands:
        _, rec, d = min(cands, key=lambda c: c[0])
        setf("fir_date_iso", d["normalized"], rec)
        t = _time_in(rec["value"])
        if t:
            setf("fir_time", t, rec)
    for rec in recs:
        if rec["field"] == "occurrence_date" and "occurrence_date_iso" not in out:
            d = _date_in(rec["value"])
            if d:
                setf("occurrence_date_iso", d["normalized"], rec)
                ts = N.find_times(rec["value"])
                if ts:
                    setf("occurrence_time_from", ts[0]["normalized"], rec)
                    if len(ts) > 1:
                        setf("occurrence_time_to", ts[1]["normalized"], rec)
    if "fir_time" not in out:
        for rec in recs:
            if rec["field"] == "time" and rec["value"]:
                t = _time_in(rec["value"])
                if t:
                    setf("fir_time", t, rec)
                    break
    r = _first(recs, "place_of_occurrence")
    if r:
        setf("place_of_occurrence", _place(r["value"]), r)
    else:
        # "Place of Occurrence:\n- Address: ..." -> the address label right after an empty place label
        for i, rec in enumerate(recs):
            if rec["field"] == "place_of_occurrence" and not rec["value"] and i + 1 < len(recs) and recs[i + 1]["field"] == "address":
                setf("place_of_occurrence", _place(recs[i + 1]["value"]), recs[i + 1])
    # "Address:" directly under the complainant heading
    party = None
    for rec in recs:
        if rec["field"] in LB.PARTY_FIELDS:
            party = rec["field"]
        elif rec["field"] == "address" and party == "complainant" and rec["value"]:
            setf("complainant_address", rec["value"], rec)
    r = _first(recs, "father_name")
    if r:
        setf("father_name_raw", r["value"], r)
    r = _first(recs, "property")
    if r:
        items = [re.sub(r"^[-•*]\s*", "", ln).strip() for ln in re.split(r"\n", text[r["start"]:r["end"]]) if ln.strip()]
        items = [i_ for i_ in items if i_]
        if items:
            out["stolen_property_raw"] = items
    r = _first(recs, "facts")
    if r and len(r["value"]) > 15:
        setf("incident_description", r["value"], r)
    # time ranges: "Time from 21:00 to 23:30 hrs" / "समय ... से ... तक"
    if "occurrence_time_from" not in out:
        m = re.search(r"(?:Time\s+from|from|समय|वेळ|between)\s*:?\s*([0-2]?\d:[0-5]\d)\s*(?:hrs?)?\s*(?:to|till|-|से|ते|and)\s*([0-2]?\d:[0-5]\d)", text, re.I)
        if m:
            a, b = N.find_times(m.group(1))[0], N.find_times(m.group(2))[0]
            out["occurrence_time_from"], out["occurrence_time_to"] = a["normalized"], b["normalized"]
    # fallbacks without labels
    if "fir_number" not in out:
        m = re.search(r"(?:FIR|F\.I\.R\.?|एफ\.?आई\.?आर|एफआयआर)\s*(?:No\.?|Number|क्र(?:मांक)?\.?|नं\.?|नंबर|#)?\s*[:\-]?\s*([0-9OoIl]+(?:\s?/\s?[0-9OoIl]{2,4})?)", text, re.I)
        if m:
            setf("fir_number", _fir_number(m.group(1)), None)
    out["_fields"] = prov
    out["_records"] = recs
    return out
