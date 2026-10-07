"""Document analysis orchestrator: structured FIR fields + evidence-bearing entities + relationships + quality report.

    analyze(text, metadata) -> {
        "document_type", "structured", "entities", "relationship_hints", "english_view", "quality" }

The output is a superset of what earlier versions returned, so existing API consumers keep working:
  * ``structured`` keeps every legacy key (fir_number, sections, phones ...) and adds ISO dates, section_details,
    field_confidence and field_evidence.
  * every entity has: text, type (legacy graph type), kind (fine grained), normalized (canonical form used to merge
    duplicates across FIRs), confidence, start/end (offsets in the original text), evidence, reason, flags,
    needs_review, role / roles.
"""
from __future__ import annotations

import re
from typing import Any

from ai_engine.nlp import facts as FT
from ai_engine.nlp import fir_parser as FP
from ai_engine.nlp import labels as LB
from ai_engine.nlp import legal as LG
from ai_engine.nlp import normalize as N

KIND_TO_TYPE = {
    "PERSON": "PERSON", "ORGANIZATION": "ORGANIZATION", "PHONE": "PHONE", "LANDLINE": "PHONE", "VEHICLE": "VEHICLE", "ACCOUNT": "ACCOUNT", "UPI": "ACCOUNT",
    "EMAIL": "EMAIL", "AADHAAR": "DIGITAL_IDENTIFIER", "PAN": "DIGITAL_IDENTIFIER", "IFSC": "DIGITAL_IDENTIFIER", "CARD": "DIGITAL_IDENTIFIER", "IMEI": "DIGITAL_IDENTIFIER",
    "TRANSACTION_ID": "DIGITAL_IDENTIFIER", "AMOUNT": "MONEY", "DATE": "DATE", "TIME": "TIME", "SECTION": "LAW_SECTION",
}
NOT_GRAPH = {"MONEY", "DATE", "TIME", "LAW_SECTION"}
REVIEW_BELOW = 0.85
FLAG_NEEDS_REVIEW = {"ocr_repaired", "act_inferred", "checksum_failed", "invalid_calendar_date", "pan_format_doubtful", "role_unknown", "not_in_reference_table"}

PLAIN_KIND = {"PHONE": "phone number", "VEHICLE": "vehicle registration", "ACCOUNT": "bank account number", "UPI": "UPI id", "EMAIL": "e-mail address", "AADHAAR": "Aadhaar number",
              "PAN": "PAN", "IFSC": "IFSC code", "AMOUNT": "amount of money", "DATE": "date", "SECTION": "law section", "PERSON": "person", "ORGANIZATION": "organisation",
              "LANDLINE": "landline number", "CARD": "card number", "IMEI": "IMEI", "TRANSACTION_ID": "transaction reference", "TIME": "time"}


def _ocr_factor(ocr_conf: float | None) -> float:
    if ocr_conf is None:
        return 1.0
    return 0.85 + 0.15 * max(0.0, min(1.0, float(ocr_conf))) if ocr_conf < 0.9 else 1.0


def _finish(it: dict[str, Any], text: str, ocr_conf: float | None) -> dict[str, Any]:
    kind = it["kind"]
    typ = KIND_TO_TYPE.get(kind, kind)
    conf = it["confidence"] * _ocr_factor(ocr_conf)
    flags = list(it.get("flags", []))
    reason = it.get("reason", "")
    if ocr_conf is not None and ocr_conf < 0.9 and it.get("method") != "structured":
        reason += f"; scan confidence is {round(ocr_conf * 100)}%, so this was lowered"
    conf = round(max(0.05, min(0.995, conf)), 3)
    needs = conf < REVIEW_BELOW or any(f in FLAG_NEEDS_REVIEW for f in flags)
    text_out = it.get("text") or it["normalized"] or it["surface"]
    if kind in ("AMOUNT", "DATE", "TIME"):
        text_out = it["surface"]
    if kind == "SECTION":
        text_out = f"{it['act']} {it['number']}" + (it.get("subsection") or "")
    out = {
        "text": text_out, "type": typ, "kind": kind, "normalized": it["normalized"], "surface": it["surface"], "confidence": conf,
        "start": it["start"], "end": it["end"], "method": it.get("method", "rules"), "role": it.get("role", ""), "roles": it.get("roles", []),
        "reason": reason, "evidence": it.get("evidence") or FT.snippet(text, it["start"], it["end"]), "flags": flags, "needs_review": needs,
        "graph_worthy": typ not in NOT_GRAPH, "plain_type": PLAIN_KIND.get(kind, kind.lower()),
    }
    for k in ("alias", "sensitive", "offence", "act", "act_label", "number", "subsection", "cross_reference", "phone_written_as", "handle", "checksum_ok", "line", "kin_of", "state", "currency", "script", "mention_spans"):
        if k in it and it[k] not in ("", None):
            out[k] = it[k]
    return out


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple, dict] = {}
    out = []
    for it in items:
        key = (it["type"], it["kind"], (it["normalized"] or it["text"]).casefold())
        if key in seen:
            prev = seen[key]
            prev.setdefault("also_at", []).append([it["start"], it["end"]])
            if it["confidence"] > prev["confidence"]:
                prev["confidence"] = it["confidence"]
            continue
        seen[key] = it
        out.append(it)
    return out


def _field_prov(v2: dict, name: str) -> dict | None:
    return (v2.get("_fields") or {}).get(name)


def parse_structured(text: str, language: str = "auto") -> dict[str, Any]:
    """Structured FIR fields (legacy keys + new ones)."""
    return _analyze_core(text, {"language": language})["structured"]


def _analyze_core(text: str, metadata: dict[str, Any]) -> dict[str, Any]:
    from ai_engine.nlp import document_intelligence as DI  # local import: DI imports this module lazily too

    text = text or ""
    t = N.ascii_digits(text)
    legacy = DI.parse_fir_legacy(text, metadata.get("language", "auto")) if text.strip() else {}
    v2 = FP.parse_fields(t) if text.strip() else {"_fields": {}, "_records": []}
    sents = FT.sentence_spans(t)
    ocr_conf = metadata.get("ocr_confidence")

    structured: dict[str, Any] = dict(legacy)
    field_conf: dict[str, float] = {}
    field_evidence: dict[str, dict[str, Any]] = {}

    def put(name, value, conf, prov=None, how="label"):
        if value in ("", None, []):
            return
        structured[name] = value
        field_conf[name] = round(conf * _ocr_factor(ocr_conf), 3)
        if prov:
            field_evidence[name] = {"start": prov["start"], "end": prov["end"], "text": re.sub(r"\s+", " ", text[prov["start"]:prov["end"]]).strip()[:200], "label": prov.get("label", ""), "how": how}

    for name in ("fir_number", "district", "police_station", "year", "place_of_occurrence", "complainant_address", "incident_description", "fir_time", "occurrence_time_from", "occurrence_time_to"):
        if v2.get(name):
            put(name, v2[name], 0.93 if name not in ("incident_description",) else 0.85, _field_prov(v2, name))
    # dates: ISO + legacy dd/mm/yyyy display
    for base in ("fir_date", "occurrence_date"):
        iso = v2.get(base + "_iso")
        if iso:
            structured[base] = N.iso_to_display(iso)
            structured[base + "_iso"] = iso
            field_conf[base] = round(0.93 * _ocr_factor(ocr_conf), 3)
            prov = _field_prov(v2, base + "_iso")
            if prov:
                field_evidence[base] = {"start": prov["start"], "end": prov["end"], "text": re.sub(r"\s+", " ", text[prov["start"]:prov["end"]]).strip()[:120], "label": prov.get("label", ""), "how": "label"}
        else:
            legacy_val = structured.get(base) or ""
            iso2 = N.first_date(legacy_val) if legacy_val else ""
            if iso2:
                structured[base + "_iso"] = iso2
                structured[base] = N.iso_to_display(iso2)
                field_conf[base] = round(0.75 * _ocr_factor(ocr_conf), 3)
    if structured.get("fir_date_iso") and not structured.get("occurrence_date_iso"):
        structured["occurrence_date"] = structured.get("occurrence_date") or structured.get("fir_date", "")
    # year fallback from FIR date / FIR number
    if not structured.get("year"):
        if structured.get("fir_date_iso"):
            structured["year"] = structured["fir_date_iso"][:4]
        elif re.search(r"/(\d{4})$", structured.get("fir_number", "")):
            structured["year"] = re.search(r"/(\d{4})$", structured["fir_number"]).group(1)
    for k in ("fir_number", "year", "district", "police_station"):
        if structured.get(k) and k not in field_conf:
            field_conf[k] = round(0.7 * _ocr_factor(ocr_conf), 3)

    # ---- sections
    fir_iso = structured.get("fir_date_iso", "")
    sections = LG.find_sections(t, fir_iso)
    if sections:
        nums = list(dict.fromkeys(s["number"] + (s["subsection"] if False else "") for s in sections))
        structured["sections"] = nums
        structured["section_details"] = [{"act": s["act"], "act_label": s["act_label"], "number": s["number"], "subsection": s["subsection"], "offence": s["offence"],
                                          "cross_reference": s["cross_reference"], "act_explicit": s["act_explicit"], "confidence": s["confidence"], "reason": s["reason"],
                                          "start": s["start"], "end": s["end"], "evidence": FT.snippet(text, s["start"], s["end"], 40)} for s in sections]
        field_conf["sections"] = round(min(s["confidence"] for s in sections), 3)

    # ---- facts
    idents = FT.extract_identifiers(t, fir_iso)
    dates = [d for d in N.find_dates(t)]
    times = N.find_times(t)
    amounts = N.find_amounts(t, avoid=[(i["start"], i["end"]) for i in idents])
    people = FT.extract_people(t, sents)
    # names from labelled fields in scripts the cue extractor does not read (Bengali, Tamil, Urdu ...)
    have = {N.name_key(p["normalized"]) for p in people}
    for rec in v2.get("_records", []):
        role = {"complainant": "COMPLAINANT", "investigating_officer": "INVESTIGATING_OFFICER", "accused_description": "ACCUSED", "witness": "WITNESS", "victim": "VICTIM"}.get(rec["field"])
        if not role or not rec["value"]:
            continue
        first = rec["value"][:1]
        if first.isascii() or "ऀ" <= first <= "ॿ":
            continue
        pr = FT.read_person(t, rec["start"])
        if pr and N.name_key(pr["name"]) not in have and pr["start"] <= rec["start"] + 6:
            have.add(N.name_key(pr["name"]))
            people.append(FT._item("PERSON", t, pr["start"], pr["end"], pr["name"], FT.cal("PERSON", "labelled", delta=-0.05), f"Name follows the label \"{rec['label']}\"", [],
                                   role=role, roles=[role], alias="", script=pr["script"], mention_spans=[[pr["start"], pr["end"]]], mention_spans_full=[(pr["start"], "labelled")], kin_of=""))
    people.sort(key=lambda p: p["start"])
    rels_raw = FT.extract_relations(t, sents, people, idents) if text.strip() else []

    # complainant / officer / accused fields from people when the label parser found nothing
    def first_person(role):
        for p in people:
            if p["role"] == role:
                return p
        return None

    comp = first_person("COMPLAINANT")
    if comp and (not structured.get("complainant") or field_conf.get("complainant", 0) < 0.9):
        put("complainant", comp["normalized"], comp["confidence"], {"start": comp["start"], "end": comp["end"], "label": "person cue"}, how="role-cue")
    io = first_person("INVESTIGATING_OFFICER")
    if io:
        put("investigating_officer", io["normalized"], io["confidence"], {"start": io["start"], "end": io["end"], "label": "person cue"}, how="role-cue")
    for p in people:
        if p["role"] == "RELATIVE" and re.search(r"father|पिता|वडील|pita|s/o|पुत्र", p["reason"], re.I) and p.get("kin_of") and comp and N.name_key(p["kin_of"]) == N.name_key(comp["normalized"]):
            put("father_name", p["normalized"], p["confidence"], {"start": p["start"], "end": p["end"], "label": "kinship cue"}, how="role-cue")
            break
    if not structured.get("father_name") and v2.get("father_name_raw"):
        structured["father_name"] = re.split(r"[,(]", v2["father_name_raw"])[0].strip()
    witnesses = [p["normalized"] for p in people if p["role"] == "WITNESS"]
    if witnesses:
        structured["witnesses"] = witnesses
    accused_people = [p["normalized"] for p in people if p["role"] == "ACCUSED"]
    if accused_people:
        structured["accused_names"] = accused_people
    victims = [p["normalized"] for p in people if p["role"] == "VICTIM"]
    if victims:
        structured["victims"] = victims
    # canonical identifier lists (these drive cross-FIR matching, so they must merge duplicates)
    by = lambda *ks: list(dict.fromkeys(i["normalized"] for i in idents if i["kind"] in ks and i["normalized"]))
    structured["phones"] = by("PHONE", "LANDLINE")
    structured["vehicles"] = by("VEHICLE") or structured.get("vehicles", [])
    structured["accounts"] = by("ACCOUNT") or [a for a in structured.get("accounts", []) if a]
    structured["upi_ids"] = by("UPI")
    structured["emails"] = by("EMAIL")
    structured["aadhaar_numbers"] = by("AADHAAR")
    structured["pan_numbers"] = by("PAN")
    structured["ifsc_codes"] = by("IFSC")
    structured["amounts"] = [{"rupees": int(a["normalized"]), "surface": a["surface"]} for a in amounts]
    structured["dates_iso"] = list(dict.fromkeys(d["normalized"] for d in dates if d["normalized"]))
    structured["dates"] = [N.iso_to_display(d) for d in structured["dates_iso"]] or structured.get("dates", [])
    if not structured.get("occurrence_date_iso") and structured.get("fir_date_iso"):
        earlier = [d for d in structured["dates_iso"] if d < structured["fir_date_iso"]]
        # the narrative usually names the offence date before the FIR date
        narrative_dates = [d for d in dates if d["normalized"] and d["normalized"] < structured["fir_date_iso"] and (structured.get("incident_description") or "").find(d["surface"]) >= 0]
        cand = narrative_dates[0]["normalized"] if narrative_dates else (earlier[0] if len(earlier) == 1 else "")
        if cand:
            structured["occurrence_date_iso"] = cand
            structured["occurrence_date"] = N.iso_to_display(cand)
            field_conf["occurrence_date"] = round(0.6 * _ocr_factor(ocr_conf), 3)
            field_evidence["occurrence_date"] = {"start": 0, "end": 0, "text": "", "label": "", "how": "earliest date in the narrative before the FIR date"}

    # legacy "stolen_property" list (labelled block); keep if the legacy parser produced it
    if not structured.get("stolen_property") and v2.get("stolen_property_raw"):
        structured["stolen_property"] = [re.sub(r"^[-•*\s]+", "", x) for x in v2["stolen_property_raw"] if len(x) > 2][:20]
    structured["field_confidence"] = field_conf
    structured["field_evidence"] = field_evidence
    return {"structured": structured, "idents": idents, "dates": dates, "times": times, "amounts": amounts, "people": people, "sections": sections, "relations": rels_raw, "text": text, "t": t, "sents": sents, "v2": v2}


def build_entities(core: dict[str, Any], ocr_conf: float | None = None) -> list[dict[str, Any]]:
    text = core["text"]
    st = core["structured"]
    out: list[dict[str, Any]] = []

    def add_struct(value, typ, kind, role, conf, reason, name, plain=None):
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if not value:
            return
        prov = st.get("field_evidence", {}).get(name)
        s = prov["start"] if prov and prov.get("text") else max(0, text.find(value))
        e = s + len(value)
        it = {"kind": kind, "surface": value, "text": value, "start": s, "end": e, "normalized": value, "confidence": conf * (st.get("field_confidence", {}).get(name, 0.9) / 0.93 if name in st.get("field_confidence", {}) else 1.0),
              "reason": reason, "flags": [], "role": role, "method": "structured"}
        f = _finish(it, text, ocr_conf)
        f["type"] = typ
        f["graph_worthy"] = typ not in NOT_GRAPH
        f["plain_type"] = plain or typ.lower().replace("_", " ")
        out.append(f)

    add_struct(st.get("fir_number"), "CASE", "FIR", "FIR_NUMBER", 0.98, "FIR number read from the FIR number label", "fir_number", "FIR number")
    add_struct(st.get("district"), "DISTRICT", "DISTRICT", "JURISDICTION", 0.95, "District read from the district label", "district", "district")
    add_struct(st.get("police_station"), "POLICE_STATION", "POLICE_STATION", "REPORTING_STATION", 0.95, "Police station read from the police-station label", "police_station", "police station")
    add_struct(st.get("place_of_occurrence"), "LOCATION", "LOCATION", "OCCURRENCE_LOCATION", 0.92, "Place of occurrence read from its label", "place_of_occurrence", "place of occurrence")
    add_struct(st.get("complainant_address"), "ADDRESS", "ADDRESS", "COMPLAINANT_ADDRESS", 0.9, "Address written under the complainant's details", "complainant_address", "address")
    acc = st.get("accused_description")
    if acc:
        add_struct(acc, "SUSPECT_DESCRIPTION", "SUSPECT_DESCRIPTION", "UNKNOWN_OR_SUSPECT", 0.85, "Free-text description of the accused, as written on the FIR", "accused_description", "suspect description")
    for x in st.get("stolen_property", []) or []:
        add_struct(x, "PROPERTY", "PROPERTY", "STOLEN_PROPERTY", 0.85, "Listed under stolen / involved property", "stolen_property", "property")
    for x in st.get("vehicle_descriptions", []) or []:
        add_struct(x, "VEHICLE_DESCRIPTION", "VEHICLE_DESCRIPTION", "SUSPECT_VEHICLE_DESCRIPTION", 0.78, "Vehicle described without a registration number", "vehicle_descriptions", "vehicle description")
    for x in st.get("serial_numbers", []) or []:
        add_struct(x, "DIGITAL_IDENTIFIER", "SERIAL", "PROPERTY_SERIAL_NUMBER", 0.93, "Serial number of a listed item", "serial_numbers", "serial number")

    for it in core["idents"]:
        f = _finish(it, text, ocr_conf)
        f["role"] = {"ACCOUNT": "FINANCIAL_ACCOUNT", "UPI": "FINANCIAL_ACCOUNT", "AADHAAR": "GOVT_ID", "PAN": "GOVT_ID"}.get(it["kind"], "")
        out.append(f)
    for it in core["amounts"]:
        it = dict(it)
        it.setdefault("evidence", "")
        out.append(_finish({**it, "evidence": FT.snippet(text, it["start"], it["end"])}, text, ocr_conf))
    for it in core["dates"]:
        out.append(_finish({**it, "evidence": FT.snippet(text, it["start"], it["end"])}, text, ocr_conf))
    for it in core["times"]:
        out.append(_finish({**it, "evidence": FT.snippet(text, it["start"], it["end"])}, text, ocr_conf))
    for it in core["sections"]:
        out.append(_finish({**it, "evidence": FT.snippet(text, it["start"], it["end"])}, text, ocr_conf))
    fir_comp = (st.get("complainant") or "")
    for p in core["people"]:
        f = _finish(p, text, ocr_conf)
        if p["role"] == "RELATIVE" and st.get("father_name") and N.name_key(p["normalized"]) == N.name_key(st["father_name"]):
            f["role"] = "FATHER_OF_COMPLAINANT"
        out.append(f)
    # add the complainant / officer by field when they were not found as people (e.g. label parser only)
    have = {N.name_key(x["normalized"]) for x in out if x["kind"] == "PERSON"}
    for name, role, why in (("complainant", "COMPLAINANT", "Complainant field"), ("investigating_officer", "INVESTIGATING_OFFICER", "Investigating-officer field")):
        v = st.get(name)
        if v and N.name_key(N.strip_titles(v)) not in have and N.name_key(v) not in have:
            add_struct(v, "PERSON", "PERSON", role, 0.85, f"{why} read from its label", name, "person")
    return _dedupe(out)


def build_relationships(core: dict[str, Any], entities: list[dict[str, Any]], ocr_conf: float | None) -> list[dict[str, Any]]:
    text, st = core["text"], core["structured"]
    fir = st.get("fir_number", "")
    rels: list[dict[str, Any]] = []

    def rel(source, target, relation, conf, evidence, why, span=None):
        if source and target and str(source).casefold() != str(target).casefold():
            rels.append({"source": source, "target": target, "relation": relation, "confidence": round(conf, 3), "evidence": evidence[:600], "rationale": why, "evidence_span": span or []})

    comp = st.get("complainant", "")
    io = st.get("investigating_officer", "")
    ps = st.get("police_station", "")
    place = st.get("place_of_occurrence", "")
    fe = st.get("field_evidence", {})
    if comp and fir:
        rel(comp, fir, "REPORTED_IN", 0.94, f"Complainant/informant named in FIR {fir}.", "Complainant field of the FIR", [fe.get("complainant", {}).get("start", 0), fe.get("complainant", {}).get("end", 0)])
    if io and fir:
        rel(io, fir, "ASSIGNED_TO", 0.94, f"Investigating Officer assigned in FIR {fir}.", "Investigating-officer field of the FIR")
    if ps and fir:
        rel(ps, fir, "REGISTERED", 0.95, f"FIR {fir} is registered at {ps}.", "FIR header", [fe.get("police_station", {}).get("start", 0), fe.get("police_station", {}).get("end", 0)])
    if fir and place:
        rel(fir, place, "LOCATED_AT", 0.91, f"Place of occurrence: {place}.", "FIR occurrence section", [fe.get("place_of_occurrence", {}).get("start", 0), fe.get("place_of_occurrence", {}).get("end", 0)])
    if comp and place:
        rel(comp, place, "REPORTED_AT", 0.86, f"Complaint describes occurrence at {place}.", "FIR occurrence context")
    role_rel = {"ACCUSED": "ACCUSED_IN", "WITNESS": "WITNESS_IN", "VICTIM": "VICTIM_IN"}
    for p in core["people"]:
        r = role_rel.get(p["role"])
        if r and fir:
            rel(p["normalized"], fir, r, min(p["confidence"], 0.95), p.get("evidence", ""), p["reason"], [p["start"], p["end"]])
    for x in core["relations"]:
        rels.append({k: x[k] for k in ("source", "target", "relation", "confidence", "evidence", "rationale", "evidence_span")} | {"needs_review": x["confidence"] < REVIEW_BELOW})
    seen: set[tuple] = set()
    out = []
    for x in rels:
        k = (str(x["source"]).casefold(), str(x["target"]).casefold(), x["relation"])
        if k in seen:
            continue
        seen.add(k)
        x["confidence"] = round(x["confidence"] * _ocr_factor(ocr_conf), 3)
        x.setdefault("needs_review", x["confidence"] < REVIEW_BELOW)
        out.append(x)
    return out
