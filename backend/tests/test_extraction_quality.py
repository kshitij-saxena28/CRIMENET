"""Extraction and translation quality, measured on the hand-written gold set in demo_dataset/extraction_gold.

Floors are set a little below the numbers measured when the tests were written, so they catch regressions, not noise.
The heldout split was written before the extractor and never used for tuning; its floors are lower on purpose.
Run `python scripts/eval_extraction.py --failures` for the full per-field report.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from ai_engine.nlp import translit as TL  # noqa: E402
from ai_engine.nlp.extractor import analyze_document  # noqa: E402
from ai_engine.nlp.langid import detect  # noqa: E402
from ai_engine.nlp.translation import translate_document, translate_field_value  # noqa: E402
from data_pipeline.ingestion import read_text  # noqa: E402
import eval_extraction as EV  # noqa: E402

GOLD = ROOT / "demo_dataset" / "extraction_gold"


def _analyze(text: str) -> dict:
    t, meta = read_text(text.encode("utf-8"), "gold.txt", language="auto", return_meta=True)
    return analyze_document(t, meta)


@pytest.fixture(scope="module")
def docs():
    return [EV.parse_gold(p) for p in sorted(GOLD.glob("*.gold"))]


@pytest.fixture(scope="module")
def dev(docs):
    return EV.evaluate([d for d in docs if d["split"] == "dev"], _analyze)


@pytest.fixture(scope="module")
def heldout(docs):
    return EV.evaluate([d for d in docs if d["split"] == "heldout"], _analyze)


# ------------------------------------------------------------------------------------------------ gold set shape
def test_gold_set_size_and_split(docs):
    assert len(docs) >= 25
    assert sum(d["split"] == "heldout" for d in docs) >= 8
    langs = {d["lang"].lower() for d in docs}
    assert {"english", "hindi", "hinglish", "marathi"} <= langs
    assert len(langs) >= 8, langs


# ------------------------------------------------------------------------------------------------ minimum scores
@pytest.mark.parametrize("floor", [0.95])
def test_dev_micro_f1(dev, floor):
    assert dev["micro"]["f1"] >= floor, dev["micro"]


def test_heldout_micro_f1(heldout):
    assert heldout["micro"]["f1"] >= 0.92, heldout["micro"]


@pytest.mark.parametrize("split_name,floor", [("dev", 0.95), ("heldout", 0.90)])
def test_per_field_and_kind_floors(split_name, floor, dev, heldout):
    r = dev if split_name == "dev" else heldout
    for group in ("fields", "entity_kinds"):
        for name, m in r[group].items():
            if (m["tp"] + m["fn"]) < 3:      # too few examples to hold to a floor
                continue
            assert m["f1"] >= floor, f"{split_name} {name}: {m}"


def test_identifiers_are_precise(dev, heldout):
    """Wrong phone/vehicle/account numbers are worse than missing ones: precision must be very high."""
    for r in (dev, heldout):
        for kind in ("PHONE", "VEHICLE", "ACCOUNT", "AADHAAR", "PAN", "IFSC", "UPI", "EMAIL"):
            m = r["entity_kinds"].get(kind)
            if m and (m["tp"] + m["fp"]) >= 2:
                assert m["precision"] >= 0.95, (kind, m)


def test_persons_roles_relations(dev, heldout):
    assert dev["persons"]["recall"] >= 0.95 and dev["persons"]["precision"] >= 0.95
    assert heldout["persons"]["recall"] >= 0.90 and heldout["persons"]["precision"] >= 0.92
    assert dev["role_accuracy"] >= 0.95 and heldout["role_accuracy"] >= 0.92
    assert dev["relationships"]["f1"] >= 0.90
    assert heldout["relationships"]["f1"] >= 0.80


def test_sections_map_to_the_right_act(dev, heldout):
    assert dev["section_act_accuracy"] >= 0.95
    assert heldout["section_act_accuracy"] >= 0.90


def test_translation_term_recall(dev, heldout):
    assert dev["translation_term_recall"] >= 0.9
    assert heldout["translation_term_recall"] >= 0.85


def test_confidence_is_not_wildly_off(dev, heldout):
    assert dev["calibration"]["ece"] <= 0.15
    assert heldout["calibration"]["ece"] <= 0.15


# ------------------------------------------------------------------------------------------------ normalisation
@pytest.mark.parametrize("text", ["Mobile: +91-94140 55667", "फोन: ९४१४०५५६६७", "Mob 094140-55667", "Ph: 91 9414055667"])
def test_phone_formats_merge_to_one_canonical_number(text):
    r = analyze_document(text + "\n", {})
    phones = {e["normalized"] for e in r["entities"] if e["kind"] == "PHONE"}
    assert phones == {"9414055667"}, phones


def test_ocr_repair_is_flagged():
    r = analyze_document("Complainant account number 5010O234567891 was debited.\n", {})
    acc = [e for e in r["entities"] if e["kind"] == "ACCOUNT"]
    assert acc and acc[0]["normalized"] == "50100234567891"
    assert "ocr_repaired" in acc[0]["flags"] and acc[0]["needs_review"]


def test_sections_get_offence_names_and_cross_references():
    r = analyze_document("Registered under Section 379 IPC and BNS 303(2) and IT Act 66C.\n", {})
    det = {(d["act"], d["number"]): d for d in r["structured"]["section_details"]}
    assert det[("IPC", "379")]["offence"] == "Theft" and det[("IPC", "379")]["cross_reference"].startswith("BNS 303")
    assert det[("BNS", "303")]["offence"] == "Theft"
    assert ("IT", "66C") in det


def test_dates_and_amounts_are_iso_and_integer():
    r = analyze_document("On 5th March 2026 the accused took Rs. 1,45,000 and later ₹85,000.\n", {})
    vals = {(e["kind"], e["normalized"]) for e in r["entities"]}
    assert ("DATE", "2026-03-05") in vals and ("AMOUNT", "145000") in vals and ("AMOUNT", "85000") in vals


def test_every_item_has_evidence_reason_and_confidence():
    r = _analyze(next(d for d in (EV.parse_gold(p) for p in sorted(GOLD.glob("g0*.gold"))) if d["lang"] == "Hindi")["text"])
    assert r["entities"]
    for e in r["entities"]:
        assert e["reason"] and e["evidence"] and 0 <= e["confidence"] <= 1, e
    for rel in r["relationship_hints"]:
        assert rel["rationale"] and rel["evidence"] and 0 <= rel["confidence"] <= 1, rel


# ------------------------------------------------------------------------------------------------ language detection
@pytest.mark.parametrize("text,code", [
    ("The complainant reported that his mobile phone was stolen near the market.", "en"),
    ("mera mobile chori ho gaya hai aur usne mujhe dhamki di", "hinglish"),
    ("शिकायतकर्ता ने बताया कि उसका मोबाइल फोन चोरी हो गया है", "hi"),
    ("तक्रारदार यांनी पोलीस ठाण्यात तक्रार दिली आहे", "mr"),
    ("ਮੈਂ ਥਾਣੇ ਵਿੱਚ ਸ਼ਿਕਾਇਤ ਦਰਜ ਕਰਵਾਈ", "pa"),
    ("நான் காவல் நிலையத்தில் புகார் அளித்தேன்", "ta"),
    ("আমি থানায় অভিযোগ করেছি", "bn"),
])
def test_language_detection(text, code):
    assert detect(text)["code"] == code


# ------------------------------------------------------------------------------------------------ translation
def test_translation_states_engine_and_is_not_neural_by_default(monkeypatch):
    monkeypatch.delenv("INDICTRANS2_MODEL", raising=False)
    r = translate_document("मेरा मोबाइल फोन चोरी हो गया।", "Hindi", None)
    assert r["engine_id"] == "rule-engine-v2" and r["neural"] is False
    assert "not a neural model" in r["engine"].lower()
    assert r["sentences"] and {"source", "english", "quality", "confidence"} <= set(r["sentences"][0])


def test_translation_protects_identifiers():
    r = translate_document("आरोपी का मोबाइल 9414055667 और वाहन RJ 14 SK 7788 था। खाता 50100234567891, IFSC HDFC0001234।", "Hindi", None)
    for tok in ("9414055667", "RJ 14 SK 7788", "50100234567891", "HDFC0001234"):
        assert tok in r["text"], (tok, r["text"])


def test_translation_glossary_word_order_and_inflection():
    r = translate_document("आरोपी ने शिकायतकर्ता का मोबाइल चुरा लिया।", "Hindi", None)
    low = r["text"].lower()
    assert "accused" in low and "complainant" in low and "mobile" in low


def test_unknown_words_are_marked_never_silently_dropped():
    r = translate_document("मेरा मोबाइल ठुंसकंठ चोरी हो गया।", "Hindi", None)
    assert "⟦" in r["text"] and "⟧" in r["text"]
    assert any(s["quality"] in ("unknown", "rule-based") for s in r["sentences"])
    assert r["unknown_words"] and r["coverage"] < 1.0


def test_names_are_transliterated_and_places_use_english_spelling():
    assert TL.transliterate("राहुल शर्मा") == "Rahul Sharma"
    assert TL.transliterate("রাহুল শর্মা") == "Rahul Sharma"
    r = translate_document("जिला: जयपुर", "Hindi", None)
    assert "Jaipur" in r["text"]


def test_person_name_is_not_glossed_as_ordinary_word():
    r = translate_document("शिकायतकर्ता: मोहन लाल शर्मा, पुत्र गोविंद शर्मा", "Hindi", None)
    assert "Mohan Lal Sharma" in r["text"] and "red" not in r["text"].lower()


def test_native_month_dates_are_translated_digits_kept():
    r = translate_document("दिनांक 02 अक्टूबर 2026 को", "Hindi", None)
    assert "02 October 2026" in r["text"]


def test_english_document_passes_through():
    r = translate_document("The complainant lost his phone on 5 March.", "English", None)
    assert r["text"].startswith("The complainant") and all(s["quality"] == "identity" for s in r["sentences"])


def test_field_translation_helper():
    out = translate_field_value("complainant", "राहुल शर्मा", "Hindi")
    assert "Rahul" in str(out)


def test_glossary_is_large():
    from ai_engine.nlp.translation import get_lex
    assert len(get_lex("hi").table) >= 600


# ------------------------------------------------------------------------------------------------ registry / api text
def test_registry_translation_entry_says_not_neural():
    from backend.app.services import app_service
    src = Path(app_service.__file__).read_text(encoding="utf-8")
    assert "NOT neural" in src and "rule-engine-v2" in src


def test_candidate_keys_distinguish_non_latin_names():
    """Regression: an ASCII-only key gave every Devanagari name the same id, so a district and a person could merge."""
    from backend.app.services.app_service import key
    assert key("जयपुर") != key("कोतवाली नगर") and key("जयपुर")
    assert key("Rahul  Sharma") == "rahulsharma"
