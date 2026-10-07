"""Check the local OCR runtime used by DARK CRIMENET."""
from ai_engine.nlp.document_intelligence import _configure_tesseract, _available_langs

ok, detail = _configure_tesseract()
print("Tesseract engine:", detail)
if not ok:
    raise SystemExit(1)
langs = _available_langs()
for code, label in [("eng", "English"), ("hin", "Hindi"), ("mar", "Marathi")]:
    print(f"{label:8}: {'OK' if code in langs else 'MISSING'}")
missing = [x for x in ("eng", "hin", "mar") if x not in langs]
if missing:
    print("Missing language packs:", ", ".join(missing))
    raise SystemExit(2)
print("OCR setup: READY for English + Hindi + Marathi")
