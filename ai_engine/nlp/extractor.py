"""Public entry points for document intelligence.

    analyze_document(text, metadata)  one call used by the API/UI: fields, entities, relationships, English view, quality
    extract_entities(text)            entity list only
    extract_relationship_hints(...)   relationship list only

Implementation lives in ai_engine.nlp.pipeline (orchestration), facts (identifiers / people / relations), fir_parser
(label-driven fields), legal (sections -> offences), normalize (canonical forms) and translation (English view).
Everything is deterministic, offline and review-first: each item keeps its evidence span and a plain-language reason.
"""
import logging
import re
from functools import lru_cache
from pathlib import Path

from ai_engine.nlp import normalize as N
from ai_engine.nlp import pipeline as PL
from ai_engine.nlp.translation import translate_structured_to_english

log = logging.getLogger("dcn.nlp")

MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "fir_entity_context.joblib"

ENGINE_VERSION = "fir-extract-v5.0"


@lru_cache(maxsize=1)
def _context_model():
    """Load the optional context scorer. joblib files are pickles, so the file's SHA-256 must
    match MODEL_HASHES.json (written by scripts/train_fir_models.py) before it is deserialised."""
    try:
        import hashlib
        import json
        import joblib
        hashes = json.loads((MODEL_PATH.parent / "MODEL_HASHES.json").read_text())
        if hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() != hashes.get(MODEL_PATH.name):
            log.error("Model file hash mismatch - refusing to load %s", MODEL_PATH.name)
            return None
        payload = joblib.load(MODEL_PATH)
        return payload.get("model") if isinstance(payload, dict) else payload
    except (OSError, ValueError, KeyError, ImportError):
        log.warning("Context scorer unavailable", exc_info=True)
        return None


_CONTEXT_TYPES = {"PERSON", "PHONE", "VEHICLE", "ACCOUNT", "DIGITAL_IDENTIFIER", "LOCATION", "ORGANIZATION"}


def _context_signal(text: str, ents: list) -> None:
    """Secondary signal only: record the local-context model's probability for Latin-script entities. It never changes
    confidence; a very low value adds a 'context_mismatch' flag so the reviewer looks twice."""
    model = _context_model()
    if model is None:
        return
    try:
        classes = list(model.classes_)
        idx = [i for i, e in enumerate(ents) if e["type"] in _CONTEXT_TYPES and e["type"] in classes and e["surface"].isascii() and e["kind"] not in ("SECTION",)]
        if not idx:
            return
        windows = [text[max(0, ents[i]["start"] - 80): min(len(text), ents[i]["end"] + 80)] for i in idx]
        probs = model.predict_proba(windows)
        for i, pr in zip(idx, probs):
            p = float(pr[classes.index(ents[i]["type"])])
            ents[i]["context_score"] = round(p, 3)
            if p < 0.05:
                ents[i].setdefault("flags", []).append("context_mismatch")
                ents[i]["needs_review"] = True
    except Exception:  # scorer is optional
        log.debug("context scorer failed", exc_info=True)


def _prep(text, metadata=None):
    metadata = metadata or {}
    core = PL._analyze_core(text or "", metadata)
    ents = PL.build_entities(core, metadata.get("ocr_confidence"))
    return core, ents


def extract_entities(text):
    core, ents = _prep(text)
    _context_signal(text or "", ents)
    # legacy consumers look for an entity of type "FIR" for FIR-like strings such as "FIR-101"
    for m in re.finditer(r"\bFIR\b\s*(?:No\.?|Number)?\s*[-:#]\s*([A-Z0-9][A-Z0-9/\-]*)", text or "", re.I):
        if not any(e["type"] == "FIR" and e["normalized"].casefold() == m.group(0).casefold() for e in ents):
            ents.append({"text": m.group(0).strip(), "type": "FIR", "kind": "FIR", "normalized": m.group(1).upper(), "surface": m.group(0).strip(), "confidence": 0.9, "start": m.start(), "end": m.end(),
                         "method": "rules", "role": "", "roles": [], "reason": "Looks like an FIR reference (FIR-<number>)", "evidence": PL.FT.snippet(text or "", m.start(), m.end()), "flags": [], "needs_review": False, "graph_worthy": True, "plain_type": "FIR reference"})
    return ents


def extract_relationship_hints(text, entities=None):
    core, ents = _prep(text)
    return PL.build_relationships(core, ents, None)


def _quality(text, core, entities, rels, metadata, warnings, english_view):
    st = core["structured"]
    ocr = metadata.get("ocr_confidence")
    critical_labels = {"fir_number": "FIR number", "fir_date": "FIR date", "complainant": "Complainant", "place_of_occurrence": "Place of occurrence", "accused_description": "Accused / suspect description"}
    critical = [label for key, label in critical_labels.items() if not st.get(key)]
    labels = {"fir_number": "FIR number", "year": "Year", "district": "District", "police_station": "Police station", "fir_date": "FIR date", "fir_time": "FIR time", "occurrence_date": "Occurrence date",
              "occurrence_time_from": "Occurrence start", "occurrence_time_to": "Occurrence end", "place_of_occurrence": "Place of occurrence", "complainant": "Complainant / informant",
              "father_name": "Father's name", "complainant_address": "Complainant address", "accused_description": "Accused / suspect description", "investigating_officer": "Investigating officer",
              "general_diary_entry": "GD entry"}
    fc, fe = st.get("field_confidence", {}), st.get("field_evidence", {})
    field_status = {}
    for key, label in labels.items():
        val = st.get(key) or ""
        field_status[key] = {"label": label, "status": "recovered" if val else "needs_verification", "value": val, "confidence": fc.get(key), "evidence": fe.get(key, {}).get("text", ""),
                             "label_found": fe.get(key, {}).get("label", "")}
    problems = []
    n_chars = len((text or "").strip())
    if n_chars == 0:
        problems.append({"code": "NO_TEXT", "severity": "error", "message": "No readable text was found in this file.",
                         "action": "If it is a scan or photo, upload a flatter, sharper, well-lit image (at least 300 dpi, straight-on, no shadows). If it is a PDF, try the original text version or export each page as an image."})
    elif n_chars < 80:
        problems.append({"code": "SHORT_TEXT", "severity": "warning", "message": f"Only {n_chars} characters were read.", "action": "Check whether the page was cropped, or upload a higher-resolution scan."})
    if ocr is not None and ocr < 0.65 and n_chars:
        problems.append({"code": "LOW_SCAN_QUALITY", "severity": "warning", "message": f"The scan was read with low confidence ({round(ocr * 100)}%).",
                         "action": "Compare every name, number and date with the original image before accepting. Re-scanning at higher quality will help more than editing afterwards."})
    if n_chars >= 80 and not st.get("fir_number") and st.get("document_type") == "FIR":
        problems.append({"code": "NO_FIR_NUMBER", "severity": "warning", "message": "The document looks like an FIR but no FIR number could be read.", "action": "Check the header of the original document for the FIR number."})
    if n_chars >= 80 and st.get("document_type") != "FIR":
        problems.append({"code": "NOT_A_FIR", "severity": "info", "message": "This does not look like a standard FIR (no FIR heading / number). It was treated as a general document.", "action": "Entities were still extracted; check them carefully."})
    lang = st.get("language", {}) or {}
    if lang.get("primary") == "Unknown" and n_chars:
        problems.append({"code": "LANGUAGE_UNKNOWN", "severity": "warning", "message": "The language could not be identified.", "action": "Choose the language manually when uploading."})
    for e in entities:
        if "checksum_failed" in e.get("flags", []):
            problems.append({"code": "AADHAAR_CHECKSUM", "severity": "warning", "message": "An Aadhaar number does not pass its check digit.", "action": f"Verify the number \"{e['surface']}\" against the source."})
        if "invalid_calendar_date" in e.get("flags", []):
            problems.append({"code": "INVALID_DATE", "severity": "warning", "message": f"\"{e['surface']}\" is not a valid calendar date.", "action": "Check the digits in the source image (OCR often confuses 0/O and 1/l)."})
    # legacy-style date validation on header fields
    for key, label in [("fir_date", "FIR date"), ("occurrence_date", "occurrence date")]:
        value = st.get(key, "")
        m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", value.strip()) if value else None
        if m and not (1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(2)) <= 12):
            warnings.append(f"{label} looks invalid after OCR: {value}. Verify against the original image.")
    by_kind: dict = {}
    for e in entities:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
    review = [e for e in entities if e.get("needs_review") and e.get("graph_worthy", True)]
    confident = [e for e in entities if not e.get("needs_review") and e["confidence"] >= 0.9 and e.get("graph_worthy", True)]
    rel_review = [r for r in rels if r.get("needs_review")]
    found = sum(1 for v in field_status.values() if v["status"] == "recovered")
    core_conf = [fc[k] for k in critical_labels if k in fc]
    ext_conf = round(sum(core_conf) / len(core_conf), 3) if core_conf else 0.0
    summary = {"fields_found": found, "fields_total": len(field_status), "entities_total": len(entities), "entities_by_kind": by_kind, "entities_needing_review": len(review),
               "entities_high_confidence": len(confident), "relationships_total": len(rels), "relationships_needing_review": len(rel_review), "critical_fields_missing": critical,
               "extraction_confidence": ext_conf}
    return {
        "ocr_confidence": ocr,
        "language": metadata.get("language") if metadata.get("language") not in {None, "auto"} else lang.get("primary", "Unknown"),
        "language_candidates": metadata.get("language_candidates", lang.get("candidates", [])),
        "ocr_method": metadata.get("method", "unknown"),
        "warnings": warnings,
        "verification_required": bool((ocr is not None and ocr < 0.90) or warnings or critical or review),
        "critical_fields": critical,
        "field_status": field_status,
        "problems": problems,
        "summary": summary,
        "engine": {"extractor": ENGINE_VERSION, "translation": english_view.get("engine", english_view.get("method", ""))},
    }


def analyze_document(text, metadata=None):
    """One-call document intelligence result used by the API/UI.

    Extraction is role-aware and review-first. Critical fields get explicit status so
    a missing OCR value is never mistaken for a negative fact.
    """
    metadata = dict(metadata or {})
    text = text or ""
    core, entities = _prep(text, metadata)
    _context_signal(text, entities)
    structured = core["structured"]
    relationships = PL.build_relationships(core, entities, metadata.get("ocr_confidence"))
    warnings = list(metadata.get("warnings", []))
    source_language = metadata.get("language") or structured.get("language", {}).get("primary", "Unknown")
    if source_language == "auto":
        source_language = structured.get("language", {}).get("primary", "Unknown")
    english_view = translate_structured_to_english(structured, source_language, text=text, entities=entities)
    if english_view.get("warning"):
        warnings.append(english_view["warning"])
    quality = _quality(text, core, entities, relationships, metadata, warnings, english_view)
    return {
        "document_type": structured.get("document_type", "INVESTIGATION_DOCUMENT"),
        "structured": structured,
        "entities": entities,
        "relationship_hints": relationships,
        "english_view": english_view,
        "quality": quality,
    }
