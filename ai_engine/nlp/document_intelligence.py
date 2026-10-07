"""Robust document/OCR intelligence for investigation documents.

Designed for noisy real-world photos/scans while remaining lightweight and offline.
OCR is performed locally with Tesseract; structured FIR understanding is deterministic,
explainable and review-first. English, Hindi and Marathi are supported first.
"""
from __future__ import annotations

import io
import os
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps

SUPPORTED_LANGUAGES = {
    "auto": "eng+hin+mar",
    "english": "eng",
    "hindi": "hin",
    "marathi": "mar",
    "english+hindi": "eng+hin",
    "english+marathi": "eng+mar",
    "hindi+marathi": "hin+mar",
    "english+hindi+marathi": "eng+hin+mar",
}

LANGUAGE_LABELS = {"eng": "English", "hin": "Hindi", "mar": "Marathi"}


def _script_counts(text: str) -> dict[str, int]:
    devanagari = len(re.findall(r"[\u0900-\u097F]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return {"devanagari": devanagari, "latin": latin}


def detect_language(text: str, requested: str = "auto") -> dict[str, Any]:
    """Script + romanised-Hindi heuristics (see ai_engine.nlp.langid). Keeps the legacy keys primary/candidates/script/scores."""
    from ai_engine.nlp.langid import detect
    return detect(text, requested)


def _configure_tesseract() -> tuple[bool, str]:
    """Locate Tesseract on Windows/Linux/macOS without requiring PATH setup."""
    try:
        import pytesseract
    except Exception as exc:
        return False, f"Python OCR package unavailable: {exc}"

    candidates = []
    env_cmd = os.getenv("TESSERACT_CMD", "").strip()
    if env_cmd:
        candidates.append(env_cmd)
    current = getattr(pytesseract.pytesseract, "tesseract_cmd", "")
    if current and current != "tesseract":
        candidates.append(current)
    found = shutil.which("tesseract")
    if found:
        candidates.append(found)
    if os.name == "nt":
        candidates.extend([
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        ])
    else:
        candidates.extend(["/usr/bin/tesseract", "/usr/local/bin/tesseract"])

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            return True, str(candidate)
    return False, "Tesseract OCR engine was not found. Install Tesseract or set TESSERACT_CMD to tesseract.exe."


def _available_langs() -> set[str]:
    ok, _ = _configure_tesseract()
    if not ok:
        return set()
    try:
        import pytesseract
        return set(pytesseract.get_languages(config=""))
    except Exception:
        return set()


def _language_code(requested: str) -> str:
    requested = (requested or "auto").strip().lower()
    requested = requested if requested in SUPPORTED_LANGUAGES else "auto"
    requested_code = SUPPORTED_LANGUAGES[requested]
    available = _available_langs()
    if not available:
        return requested_code
    pieces = requested_code.split("+")
    usable = [p for p in pieces if p in available]
    return "+".join(usable) if usable else "eng"


def _pil_variants(image: Image.Image) -> list[tuple[str, Image.Image]]:
    """Create OCR-friendly variants for photographs, scans and low-contrast pages."""
    img = ImageOps.exif_transpose(image).convert("RGB")
    # Avoid huge memory spikes while retaining enough resolution for small text.
    max_dim = 2600
    if max(img.size) > max_dim:
        scale = max_dim / max(img.size)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.Resampling.LANCZOS)
    gray = ImageOps.grayscale(img)
    if min(gray.size) < 1800:
        scale = min(2.0, 1800 / min(gray.size))
        gray = gray.resize((int(gray.width * scale), int(gray.height * scale)), Image.Resampling.LANCZOS)
    contrast = ImageOps.autocontrast(gray)
    sharp = contrast.filter(ImageFilter.SHARPEN).filter(ImageFilter.SHARPEN)
    # Otsu-like threshold using a histogram-derived midpoint keeps it dependency-light.
    hist = np.asarray(sharp).ravel()
    threshold = int(np.clip(np.percentile(hist, 52), 100, 210))
    binary = sharp.point(lambda p: 255 if p > threshold else 0)
    return [("enhanced", sharp), ("binary", binary), ("gray", gray)]


def _cv_document_correct(image: Image.Image) -> Image.Image:
    """Perspective-correct a photographed document when OpenCV is available."""
    try:
        import cv2
    except Exception:
        return image
    arr = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    h, w = arr.shape[:2]
    scale = 1.0
    if max(h, w) > 1800:
        scale = 1800 / max(h, w)
        small = cv2.resize(arr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        small = arr.copy()
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = sorted(contours, key=cv2.contourArea, reverse=True)[:15]
    quad = None
    for c in candidates:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        area = cv2.contourArea(c)
        if len(approx) == 4 and area > 0.20 * small.shape[0] * small.shape[1]:
            quad = approx.reshape(4, 2).astype(np.float32)
            break
    if quad is None:
        return image
    def order(pts):
        s = pts.sum(axis=1); d = np.diff(pts, axis=1).ravel()
        return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)
    pts = order(quad)
    tl, tr, br, bl = pts
    width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    width = max(width, 800); height = max(height, 1000)
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(pts, dst)
    warped = cv2.warpPerspective(small, M, (width, height), borderMode=cv2.BORDER_REPLICATE)
    return Image.fromarray(cv2.cvtColor(warped, cv2.COLOR_BGR2RGB))


def _ocr_once(image: Image.Image, lang: str, psm: int = 6) -> tuple[str, float]:
    import pytesseract
    cfg = f"--oem 3 --psm {psm}"
    data = pytesseract.image_to_data(image, lang=lang, config=cfg, output_type=pytesseract.Output.DICT)
    confs = []
    for txt, conf in zip(data.get("text", []), data.get("conf", [])):
        txt = (txt or "").strip()
        try: c = float(conf)
        except Exception: c = -1
        if txt and c >= 0: confs.append(c)
    # Keep the line structure. FIR understanding relies heavily on field/section boundaries.
    text = pytesseract.image_to_string(image, lang=lang, config=cfg)
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    return text, (sum(confs) / len(confs) / 100.0 if confs else 0.0)


def ocr_image(data: bytes, filename: str = "upload.jpg", requested_language: str = "auto") -> dict[str, Any]:
    """Robust OCR ensemble for clean scans, phone photos and mixed Indic/English FIRs.

    For auto mode we run language-specific passes rather than relying on one mixed model.
    The best candidate is selected using script-aware quality, FIR marker coverage and OCR
    confidence. This is intentionally review-first: OCR never silently corrects evidence.
    """
    try:
        import pytesseract  # noqa: F401
    except Exception as exc:
        return {"text":"", "language":"Unknown", "language_code":"", "confidence":0.0, "method":"unavailable", "warnings":[f"Python OCR package unavailable: {exc}"]}
    ok, engine = _configure_tesseract()
    if not ok:
        return {"text":"", "language":"Unknown", "language_code":"", "confidence":0.0, "method":"unavailable", "warnings":[engine]}
    try:
        image = Image.open(io.BytesIO(data))
        corrected = _cv_document_correct(image)
        available = _available_langs()
        requested_key = (requested_language or "auto").strip().lower()
        requested_key = requested_key if requested_key in SUPPORTED_LANGUAGES else "auto"
        requested_code = SUPPORTED_LANGUAGES[requested_key]
        warnings = [f"Tesseract engine: {engine}"]

        # In auto mode, run separate passes for each language. This is substantially more
        # reliable for Devanagari than asking a three-language model to decode every glyph.
        if requested_key == "auto":
            lang_candidates = [x for x in ("eng", "hin", "mar") if x in available]
            if len(lang_candidates) > 1:
                lang_candidates += [x for x in ("eng+hin", "eng+mar", "hin+mar", "eng+hin+mar") if all(y in available for y in x.split("+"))]
        else:
            lang_candidates = [x for x in requested_code.split("+") if x in available]
            if requested_code and len(lang_candidates) > 1:
                lang_candidates = [requested_code] + lang_candidates
            missing = [x for x in requested_code.split("+") if x not in available]
            if missing:
                warnings.append("Missing Tesseract language pack(s): " + ", ".join(missing))

        if not lang_candidates:
            return {"text":"", "language":"Unknown", "language_code":"", "confidence":0.0, "method":"unavailable", "warnings":warnings+["No requested OCR language packs are available."]}

        variants = _pil_variants(corrected)
        candidates=[]
        for lang in list(dict.fromkeys(lang_candidates)):
            # Start with one form-friendly pass per language. If the best result is weak,
            # a sparse-layout retry is added below. This keeps real-world uploads responsive.
            for name, variant in variants[:1]:
                for psm in (6,):
                    try:
                        text, conf = _ocr_once(variant, lang, psm)
                    except Exception:
                        continue
                    if not text.strip():
                        continue
                    info = detect_language(text, "auto")
                    scripts = info["script"]
                    latin, dev = scripts["latin"], scripts["devanagari"]
                    if lang == "eng": script_bonus = min(0.12, latin / 3000)
                    elif lang in {"hin","mar","hin+mar"}: script_bonus = min(0.18, dev / 1200)
                    else: script_bonus = min(0.12, (latin+dev) / 3500)
                    markers = [r"FIR|FIRST INFORMATION|एफ[.]?आई[.]?आर|एफआयआर|प्रथम", r"District|जिला|जिल्हा", r"Police Station|थाना|पोलीस ठाणे", r"Complainant|शिकायतकर्ता|तक्रारदार|फिर्यादी", r"Accused|Suspect|आरोपी", r"Investigating Officer|जांच अधिकारी|तपास अधिकारी"]
                    marker_score = sum(bool(re.search(m, text, re.I)) for m in markers) / len(markers)
                    length_bonus = min(0.10, len(text.strip())/3000)
                    quality = conf + script_bonus + 0.16*marker_score + length_bonus
                    candidates.append((quality, conf, name, psm, lang, text, info))

        if candidates and max(c[1] for c in candidates) < 0.72:
            # Sparse mode is especially useful for skewed phone photos and forms with large gaps.
            for lang in list(dict.fromkeys(lang_candidates)):
                try:
                    text2, conf2 = _ocr_once(variants[0][1], lang, 11)
                except Exception:
                    continue
                if text2.strip():
                    info2=detect_language(text2,"auto"); scripts2=info2["script"]
                    latin2,dev2=scripts2["latin"],scripts2["devanagari"]
                    script_bonus2=min(0.18, dev2/1200) if lang in {"hin","mar","hin+mar"} else min(0.12, latin2/3000)
                    markers2=[r"FIR|FIRST INFORMATION|एफ[.]?आई[.]?आर|एफआयआर|प्रथम",r"District|जिला|जिल्हा",r"Police Station|थाना|पोलीस ठाणे",r"Complainant|शिकायतकर्ता|तक्रारदार|फिर्यादी",r"Accused|Suspect|आरोपी",r"Investigating Officer|जांच अधिकारी|तपास अधिकारी"]
                    marker2=sum(bool(re.search(m,text2,re.I)) for m in markers2)/len(markers2)
                    q2=conf2+script_bonus2+0.16*marker2+min(0.10,len(text2.strip())/3000)
                    candidates.append((q2,conf2,"enhanced",11,lang,text2,info2))
        if not candidates:
            return {"text":"", "language":"Unknown", "language_code":"", "confidence":0.0, "method":"tesseract", "warnings":warnings+["OCR produced no usable text."]}
        best=max(candidates,key=lambda x:x[0])
        _, conf, variant, psm, lang, text, info = best
        # If a mixed-language pass was selected but a strong single-script pass exists,
        # prefer the single-script pass for cleaner field extraction.
        if requested_key == "auto":
            strong=[c for c in candidates if "+" not in c[4]]
            if strong:
                strongest=max(strong,key=lambda x:x[0])
                if strongest[0] >= best[0]-0.035 and strongest[1] >= 0.72:
                    best=strongest; _, conf, variant, psm, lang, text, info=best
        lang_info=detect_language(text, requested_language)
        if conf < 0.65: warnings.append("Low OCR confidence; verify names, numbers and dates against the source image.")
        if len(text.strip()) < 80: warnings.append("Very little text was recovered; try a clearer image or a higher-resolution scan.")
        return {"text":text,"language":lang_info["primary"],"language_code":lang,"language_candidates":lang_info["candidates"],"confidence":round(float(conf),3),"method":f"tesseract+ensemble:{variant}:psm{psm}:lang={lang}","warnings":warnings}
    except Exception as exc:
        return {"text":"", "language":"Unknown", "language_code":"", "confidence":0.0, "method":"tesseract", "warnings":[f"OCR failed: {exc}"]}


# ---------- FIR understanding ----------

def _first(patterns: list[str], text: str, flags=re.I | re.M) -> str:
    for pat in patterns:
        m = re.search(pat, text, flags)
        if m:
            return re.sub(r"\s+", " ", m.group(1).strip(" :-–—\t"))
    return ""


def _all(pattern: str, text: str) -> list[str]:
    return [re.sub(r"\s+", " ", x.strip()) for x in re.findall(pattern, text, re.I | re.M) if x.strip()]


def _line_after(labels: list[str], text: str) -> str:
    label = "(?:" + "|".join(labels) + ")"
    pat = rf"(?:^|\n)\s*(?:[-•]\s*)?{label}\s*[:\-–—]?\s*(.+)$"
    return _first([pat], text)




def _digits_ascii(value: str) -> str:
    """Normalize Devanagari digits commonly returned by Indic OCR."""
    table=str.maketrans("०१२३४५६७८९", "0123456789")
    return (value or "").translate(table)


def _normalize_ocr_text(text: str) -> str:
    t=text or ""
    t=t.replace("\u200c", "").replace("\u200d", "")
    t=_digits_ascii(t)
    # Common OCR confusions that are safe only in labels/headers; values are preserved.
    replacements={"एफआईआर": "एफआईआर", "एफ.आई.आर": "एफआईआर", "एफआयआर": "एफआयआर", "पोलीस ठाणे": "पोलीस ठाणे"}
    for a,b in replacements.items(): t=t.replace(a,b)
    return t


def _clean_field(value: str) -> str:
    v=re.sub(r"\s+", " ", (value or "")).strip(" :-–—\t.,;")
    return v


# Multilingual FIR labels. These are aliases only; values are never translated during extraction.
FIR_FIELD_ALIASES = {
    "fir_number": [
        "FIR No", "FIR Number", "F.I.R. No", "F.I.R. Number",
        "FIR क्रमांक", "FIR नंबर", "FIR नं", "FIR क्र",
        "एफआईआर क्रमांक", "एफआईआर नंबर", "एफआईआर नं", "एफआईआर क्र",
        "एफआयआर क्रमांक", "एफआयआर नंबर", "एफआयआर नं", "एफआयआर क्र",
        "प्रथम सूचना रिपोर्ट क्रमांक", "प्रथम सूचना रिपोर्ट नंबर",
        "प्रथम माहिती अहवाल क्रमांक", "प्रथम माहिती अहवाल नंबर",
    ],
    "district": ["District", "जिला", "जिल्हा"],
    "police_station": [
        "Police Station", "P.S.", "P.S", "PS", "थाना", "पुलिस थाना",
        "पोलीस स्टेशन", "पोलिस स्टेशन", "पोलीस ठाणे", "पोलीस ठाणे",
    ],
    "complainant": [
        "Complainant", "Informant", "Complainant / Informant",
        "शिकायतकर्ता", "शिकायत कर्ता", "सूचनाकर्ता", "सूचना कर्ता",
        "फिर्यादी", "फिर्यादीचे नाव", "तक्रारदार", "तक्रारदाराचे नाव",
        "माहितीदार", "अर्जदार",
    ],
    "father_name": [
        "Father's Name", "Father Name", "पिता का नाम", "पिताचे नाव",
        "वडिलांचे नाव", "वडिलांचे नांव",
    ],
    "complainant_address": [
        "Address", "पता", "पत्ता", "राहण्याचा पत्ता", "निवासी पता",
    ],
    "place_of_occurrence": [
        "Place of Occurrence", "Place of occurrence", "घटना का स्थान",
        "घटना का स्थल", "घटनास्थल", "घटनेचे ठिकाण", "घटनेचे स्थान",
        "गुन्ह्याचे ठिकाण", "गुन्ह्याचे स्थान",
    ],
    "accused_description": [
        "Accused", "Suspect", "Details of Known / Suspected / Unknown Accused",
        "आरोपी", "संशयित", "संशयित आरोपी", "अज्ञात आरोपी",
    ],
    "investigating_officer": [
        "Investigating Officer", "Investigating Officer assigned",
        "जांच अधिकारी", "जांच अधिकारी का नाम", "तपास अधिकारी",
        "तपास अधिकाऱ्याचे नाव", "तपासणी अधिकारी",
    ],
    "general_diary_entry": [
        "GD Entry", "GD Entry No", "General Diary Entry", "General Diary Entry No",
        "जनरल डायरी", "जनरल डायरी प्रविष्टि", "दैनंदिनी", "दैनंदिनी क्रमांक",
    ],
}


def _extract_label_value(text: str, labels: list[str], max_chars: int = 300) -> str:
    """Extract a value after a Hindi/Marathi/English field label.

    Handles label:value, label - value, and label followed by a value on the next line.
    The stop-label guard prevents one field from consuming the next field on the same line.
    """
    if not text:
        return ""
    label_pattern = "|".join(re.escape(x) for x in sorted(labels, key=len, reverse=True))
    stop_pattern = (
        r"(?=(?:District|Police Station|P\.?S\.?|FIR|Year|Date|Time|Complainant|Informant|"
        r"Accused|Suspect|Investigating Officer|Address|Place of Occurrence|"
        r"जिला|जिल्हा|थाना|पुलिस थाना|पोलीस स्टेशन|पोलीस ठाणे|"
        r"शिकायतकर्ता|सूचनाकर्ता|फिर्यादी|तक्रारदार|पिता|पिताचे|वडिलांचे|"
        r"पता|पत्ता|घटना|घटनास्थळ|घटनेचे|गुन्ह्याचे|आरोपी|संशयित|"
        r"जांच अधिकारी|तपास अधिकारी|जनरल डायरी|दैनंदिनी)\b)"
    )

    # Same-line value.
    m = re.search(
        rf"(?:^|\n)\s*(?:[-•]\s*)?(?:{label_pattern})\s*[:#\-–—]?\s*([^\n]{{1,{max_chars}}})",
        text,
        re.I | re.M,
    )
    if m:
        value = _clean_field(m.group(1))
        value = re.split(stop_pattern, value, maxsplit=1, flags=re.I)[0]
        return _clean_field(value)

    # Label and value on adjacent lines.
    m = re.search(
        rf"(?:^|\n)\s*(?:[-•]\s*)?(?:{label_pattern})\s*[:#\-–—]?\s*\n\s*(?:[-•]\s*)?([^\n]{{1,{max_chars}}})",
        text,
        re.I | re.M,
    )
    return _clean_field(m.group(1)) if m else ""


def _extract_person_from_labeled_line(text: str, labels: list[str]) -> str:
    """Field extraction specialized for person labels; avoids consuming the next field."""
    value = _extract_label_value(text, labels, max_chars=160)
    if not value:
        return ""
    return re.split(
        r"\s+(?=(?:Father(?:'s)? Name|पिता का नाम|वडिलांचे नाव|Address|पता|पत्ता)\s*[:\-–—]?)",
        value,
        maxsplit=1,
        flags=re.I,
    )[0].strip()


def _extract_date(value: str) -> str:
    m=re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", _digits_ascii(value))
    return f"{m.group(1).zfill(2)}/{m.group(2).zfill(2)}/{m.group(3)}" if m else ""


def _extract_time(value: str) -> str:
    m=re.search(r"\b([0-2]?\d):([0-5]\d)\s*(?:hrs?|hours?)?\b", _digits_ascii(value), re.I)
    return f"{m.group(1).zfill(2)}:{m.group(2)}" if m else ""


def _between_same_line(line: str, left: str, right: list[str]) -> str:
    m=re.search(left+r"\s*[:\-–—]?\s*(.*?)\s+(?:"+"|".join(right)+r")\s*[:\-–—]?", line, re.I)
    return _clean_field(m.group(1)) if m else ""

def parse_fir_legacy(text: str, language: str = "auto") -> dict[str, Any]:
    """First-generation template parser. Kept as a fallback layer: ``parse_fir`` overlays the label-driven parser on top of it."""
    t = _normalize_ocr_text(text or "")
    # OCR may insert decorative separators; keep line boundaries but normalize whitespace.
    lines = [re.sub(r"[ \t]+", " ", x).strip(" |\t") for x in t.splitlines()]
    lines = [x for x in lines if x]
    clean = "\n".join(lines)
    compact = re.sub(r"\s+", " ", clean)

    def field(patterns):
        for pat in patterns:
            m = re.search(pat, clean, re.I | re.M)
            if m:
                return re.sub(r"\s+", " ", m.group(1).strip(" :-–—\t"))
        return ""

    fir_no = field([
        r"(?:FIR\s*(?:No\.?|Number)|FIR\s*क्र\.?|एफ[.]?आई[.]?आर\s*(?:क्र\.?|नं\.?|क्रमांक)|एफआयआर\s*(?:क्र\.?|नं\.?|क्रमांक))\s*[:#\-]?\s*([A-Z0-9/\-]+)",
        r"(?:एफआईआर|एफ[.]?आई[.]?आर)[^\n]{0,25}?(?:क्रमांक|नंबर|नं\.?)\s*[:#\-]?\s*([A-Z0-9/\-]+)",
    ])
    district = field([r"(?:^|\n)\s*\d*\.?\s*(?:District|pistrict)\s*:\s*([^\n]+?)(?=\s+(?:P\.?S\.?|P5|9[.]?9|[0-9०-९][.]?[0-9०-९])\s*[:\-]|\s+Police Station\s*:)", r"(?:District|pistrict)\s*[:\-]\s*([^\n]+?)(?=\s+(?:P\.?S\.?|P5|9[.]?9|[0-9०-९][.]?[0-9०-९])\s*[:\-]|$)", r"जिल्हा\s*[:\-]\s*([^\n]+?)(?=\s+(?:पोलीस ठाणे|पोलीस\s+ठाणे)|$)", r"जिला\s*[:\-]\s*([^\n]+?)(?=\s+थाना|$)"])
    ps = field([
        # P.S./OCR aliases must be anchored to the start of a field; otherwise a
        # numeric fallback can accidentally match the year (e.g. 2026 -> 26) on
        # ordinary standalone "Police Station:" lines.
        r"(?:^|\n)\s*(?:P\.?S\.?|P5|9\.9)\s*[:\-]?\s*([^\n]+?)(?=\s+Year\s*:|\s+FIR\s+No\b|$)",
        r"Police Station\s*[:\-]\s*([^\n]+)",
        r"पोलीस ठाणे\s*[:\-]?\s*([^\n]+?)(?=\s+(?:वर्ष|सन|Year|FIR|एफआईआर|एफआयआर)|$)",
        r"थाना\s*[:\-]?\s*([^\n]+?)(?=\s+(?:वर्ष|सन|Year|FIR|एफआईआर|एफआयआर)|$)",
    ])
    # Same-line Hindi/Marathi header: prefer the bounded district -> station -> year parse.
    for ln in lines:
        mh = re.search(r"(?:जिला|जिल्हा)\s*[:\-]?\s*(.*?)\s+(?:थाना|पोलीस ठाणे)\s*[:\-]?\s*(.*?)\s+(?:वर्ष|सन|Year)\s*[:\-]?\s*\d{4}", ln, re.I)
        if mh:
            district = mh.group(1).strip()
            ps = mh.group(2).strip()
            break
    if ps and re.search(r"(?:दिनांक|तारीख|Date|Year|FIR)\s*[:\-]", ps, re.I):
        ps=re.split(r"\s+(?=(?:दिनांक|तारीख|Date|Year|FIR)\s*[:\-])", ps, maxsplit=1)[0].strip()
    if not ps:
        for ln in lines:
            mh = re.search(r":\s*([A-Za-z][A-Za-z ._-]{2,50})\s+Year\s*:\s*\d{4}\s+FIR\s*(?:No\.?|Number)\s*:", ln, re.I)
            if mh:
                ps = mh.group(1).strip(); break
    # Strong same-line Indic header parser: e.g. "जिला: दिल्ली थाना: सिटी सेंटर".
    for ln in lines:
        md=re.search(r"(?:जिला|जिल्हा)\s*[:\-]?\s*(.*?)\s+(?:थाना|पोलीस\s+ठाणे)\s*[:\-]?\s*(.*?)(?=\s+(?:वर्ष|सन|Year|FIR|एफआईआर|एफआयआर|दिनांक|तारीख)\b|$)", ln, re.I)
        if md:
            district=_clean_field(md.group(1)); ps=_clean_field(md.group(2)); break
    # English same-line header with OCR-tolerant P.S./District labels.
    for ln in lines:
        md=re.search(r"(?:District|pistrict)\s*[:\-]?\s*(.*?)\s+(?:P\.?S\.?|P5|9\.?9)\s*[:\-]?\s*(.*?)(?=\s+Year\s*:|\s+FIR\s*(?:No\.?|Number)\s*:|$)", ln, re.I)
        if md:
            district=_clean_field(md.group(1)); ps=_clean_field(md.group(2)); break
    # Header fallback for photographed documents where P.S./District labels are OCR-corrupted.
    header_line = next((ln for ln in lines if re.search(r"Year\s*:\s*\d{4}.*FIR\s*(?:No\.?|Number)", ln, re.I)), "")
    if header_line:
        mh = re.search(r"(?:District|pistrict)\s*:\s*(.*?)\s+(?:P\.?S\.?|P5|9[.]?9|[0-9०-९][.]?[0-9०-९])\s*:\s*([A-Za-z][A-Za-z ._-]{2,50})\s+Year\s*:", header_line, re.I)
        if mh:
            district = mh.group(1).strip(); ps = mh.group(2).strip()
        if not ps:
            ms = re.search(r":\s*([A-Za-z][A-Za-z ._-]{2,50})\s+Year\s*:\s*\d{4}\s+FIR\s*(?:No\.?|Number)\s*:", header_line, re.I)
            if ms: ps = ms.group(1).strip()
    year = field([r"\bYear\s*[:\-]\s*(\d{4})", r"\b(?:वर्ष|सन)\s*[:\-]\s*(\d{4})", r"(?:वर्ष|सन)\s*[:\-]?\s*(\d{4})"])

    if not year and fir_no and re.search(r"/(\d{4})$", fir_no):
        year=re.search(r"/(\d{4})$", fir_no).group(1)
    fir_date = _extract_date(field([
        r"(?:Date|pate)\s+of\s+FIR\s*[:\-]?\s*([^\n]+)",
        r"दिनांक(?:\s+(?:की|का))?\s*[:\-]?\s*([^\n]+)",
        r"तारीख\s*[:\-]?\s*([^\n]+)",
    ]))
    fir_time = _extract_time(field([
        r"(?:Date\s+of\s+FIR\s+)?Time\s*[:\-]?\s*([^\n]+)",
        r"समय\s*[:\-]?\s*([^\n]+)", r"वेळ\s*[:\-]?\s*([^\n]+)",
    ]))
    occurrence_date = _extract_date(field([
        r"(?:Day\s*/\s*Date|Date\s+of\s+Occurrence|Occurrence.*?Date)\s*[:\-]?\s*([^\n]+)",
        r"घटना(?:\s+की|\s+का)?[^\n]*?([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
        r"घटनेची[^\n]*?([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
    ]))
    # Time ranges may occur on the same line and are common in FIR forms.
    range_line=next((ln for ln in lines if re.search(r"Time\s+From|समय.*से|वेळ.*पासून",ln,re.I)),"")
    mtimes=re.findall(r"[0-2]?\d\s*:\s*[0-5]\d", _digits_ascii(range_line))
    time_from=_extract_time(mtimes[0]) if mtimes else _extract_time(field([r"Time\s+From\s*[:\-]?\s*([^\n]+)",r"समय.*?से\s*[:\-]?\s*([^\n]+)",r"वेळ.*?पासून\s*[:\-]?\s*([^\n]+)"]))
    time_to=_extract_time(mtimes[1]) if len(mtimes)>1 else _extract_time(field([r"\bTo\s*[:\-]?\s*([^\n]+)",r"से\s+तक\s*[:\-]?\s*([^\n]+)",r"ते\s*[:\-]?\s*([^\n]+)"]))

    # Law sections: capture explicit Section/धारा/कलम labels and comma-separated numbers.
    sections=[]
    acts_block=""
    macts=re.search(r"Acts\s*&\s*Sections\s*:\s*(.*?)(?=\n\s*3\.|\n\s*3\s)",clean,re.I|re.S)
    if not macts:
        macts=re.search(r"(?:धारा|कलम|कलमे)[^\n]*(.*?)(?=\n\s*\d+\.)",clean,re.I|re.S)
    acts_block=macts.group(1) if macts else clean
    for m in re.finditer(r"(?:Section|Sections|धारा|कलम|कलमे)\s*[:#\-]?\s*([0-9]{1,5}(?:\s*[,/&]\s*[0-9]{1,5})*)",acts_block,re.I):
        sections.extend(re.findall(r"\d{1,5}",m.group(1)))
    sections=list(dict.fromkeys(sections))

    # Work inside numbered FIR sections so repeated fields such as Address are not confused.
    def section_block(number, next_number=None):
        start = re.search(rf"(?:^|\n)\s*{number}\.\s*", clean)
        if not start: return ""
        end = re.search(rf"(?:^|\n)\s*{next_number}\.\s*", clean[start.end():]) if next_number else None
        return clean[start.end(): start.end()+end.start() if end else len(clean)]

    complainant_block = section_block(6, 7)
    occurrence_block = section_block(5, 6)

    complainant = _extract_person_from_labeled_line(
        complainant_block or clean, FIR_FIELD_ALIASES["complainant"]
    )
    # English FIRs commonly put the person's name under a generic "Name:" label in section 6.
    if not complainant and complainant_block:
        complainant = field([r"(?:^|\n)\s*[-•]?\s*Name\s*:\s*([^\n]+)"])
    # OCR/form templates may include the literal label inside the captured value.
    complainant = re.sub(r"^(?:Name|नाम|नाव)\s*[:\-–—]\s*", "", complainant, flags=re.I).strip()

    father = _extract_person_from_labeled_line(
        complainant_block or clean, FIR_FIELD_ALIASES["father_name"]
    )
    if not father and complainant_block:
        father = field([r"Father\'?s Name\s*:\s*([^\n]+)"])

    address = _extract_label_value(
        complainant_block or clean, FIR_FIELD_ALIASES["complainant_address"]
    )
    # In the occurrence section, "Address" means the occurrence address; do not use the complainant address.
    place = _extract_label_value(
        occurrence_block or clean, FIR_FIELD_ALIASES["place_of_occurrence"]
    )
    if not place and occurrence_block:
        place = field([r"(?m)^[ \t]*[-•*]?[ \t]*Address[ \t]*[:\-][ \t]*([^\n]+)"])   # a label at the start of a line, not "wallet address ..."
    # OCR normally keeps section 7 on its own line; capture only the next content line.
    accused = field([
        r"(?:Accused|Suspect)\s*[:\-]\s*([^\n]+)",
        r"आरोपी\s*[:\-]\s*([^\n]+)",
        r"Details of Known.*?Accused[^\n]*\n\s*[-•_]?\s*([^\n]+)",
        r"आरोपी[^\n]*\n\s*[-•]?\s*([^\n]+)",
    ])
    officer = field([
        r"Investigating Officer assigned\s*[:\-]\s*([^\n]+)",
        r"Investigating Officer\s*[:\-]\s*([^\n]+)",
        r"तपास अधिकारी\s*[:\-]?\s*([^\n]+)",
        r"जांच अधिकारी\s*[:\-]?\s*([^\n]+)",
        r"तपासणी अधिकारी\s*[:\-]?\s*([^\n]+)",
    ])
    gd = field([r"GD\s*(?:No\.?|Entry\s*No\.?)\s*[:\-]?\s*([A-Z0-9/-]+)", r"General Diary.*?Entry\s*No\.\s*([A-Z0-9/-]+)", r"जनरल डायरी.*?Entry\s*No\.\s*([A-Z0-9/-]+)"])

    # Generic multilingual fallback. This runs after section-aware extraction and only fills blanks.
    if not fir_no:
        fir_no = _extract_label_value(clean, FIR_FIELD_ALIASES["fir_number"], 80)
    if not district:
        district = _extract_label_value(clean, FIR_FIELD_ALIASES["district"], 120)
    if not ps:
        ps = _extract_label_value(clean, FIR_FIELD_ALIASES["police_station"], 120)
    if not complainant:
        complainant = _extract_person_from_labeled_line(clean, FIR_FIELD_ALIASES["complainant"])
    if not father:
        father = _extract_person_from_labeled_line(clean, FIR_FIELD_ALIASES["father_name"])
    if not address:
        address = _extract_label_value(clean, FIR_FIELD_ALIASES["complainant_address"], 240)
    if not place:
        place = _extract_label_value(clean, FIR_FIELD_ALIASES["place_of_occurrence"], 300)
    if not accused:
        accused = _extract_label_value(clean, FIR_FIELD_ALIASES["accused_description"], 500)
    if not officer:
        officer = _extract_person_from_labeled_line(clean, FIR_FIELD_ALIASES["investigating_officer"])
    if not gd:
        gd = _extract_label_value(clean, FIR_FIELD_ALIASES["general_diary_entry"], 80)

    property_items = []
    start = None
    for i, line in enumerate(lines):
        if re.search(r"(?:^|\d\.\s*)Details of Properties Stolen / Involved|^Stolen Property|^चोरी की संपत्ति|^चोरीचा माल", line, re.I):
            start = i + 1; break
    if start is not None:
        for line in lines[start:]:
            if re.match(r"(?:9\.|10\.|Short Description|Action Taken)", line, re.I): break
            if re.match(r"[-•]\s*", line): property_items.append(re.sub(r"^[-•]\s*", "", line).strip())
    if not property_items:
        property_items = [re.sub(r"^[-•]\s*", "", x).strip() for x in re.findall(r"(?:1\s*x)\s+([^\n]+)", clean, re.I)]
    if not property_items:
        prop_line = field([r"(?:चोरी की संपत्ति|चोरीचा माल|चोरीची मालमत्ता|चोरीस गेलेली मालमत्ता)\s*[:\-]?\s*([^\n]+)"])
        if prop_line: property_items = [prop_line]

    incident_block = section_block(9, 10)
    event_text = re.sub(r"^.*?:\s*", "", incident_block, count=1).strip() if incident_block else field([
        r"घटनेचे वर्णन\s*[:\-]?\s*(.*?)(?=\n\s*\d+\.|$)",
        r"घटनेचा तपशील\s*[:\-]?\s*(.*?)(?=\n\s*\d+\.|$)",
        r"घटना का संक्षिप्त विवरण\s*[:\-]?\s*(.*?)(?=\n\s*\d+\.|$)",
        r"घटना का विवरण\s*[:\-]?\s*(.*?)(?=\n\s*\d+\.|$)",
    ])
    event_text = re.sub(r"\s+", " ", event_text)
    phones = list(dict.fromkeys(re.findall(r"(?<!\d)(?:\+91[-\s]?)?[6-9]\d{9}(?!\d)", clean)))
    vehicles = list(dict.fromkeys(re.findall(r"\b[A-Z]{2}\s*\d{1,2}\s*[A-Z]{1,3}\s*\d{3,4}\b", clean)))
    vehicle_descriptions = list(dict.fromkeys(re.findall(r"\b(?:black|white|red|blue|grey|gray|silver|शाळ?्या|काळा|काळी|पांढरा|लाल|निळा)\s+(?:scooter|motorcycle|bike|car|वाहन|स्कूटर|मोटरसायकल)\b", clean, re.I)))
    serial_numbers = list(dict.fromkeys(re.findall(r"(?:Serial\s*No\.?|क्रमांक|सिरीयल\s*नं\.?)[\s:.-]*([A-Z0-9-]{5,})", clean, re.I)))
    accounts = list(dict.fromkeys(re.findall(r"(?:ACCT|ACCOUNT|A/C|खाता|खाते)[\s:#-]*([A-Z0-9][A-Z0-9-]{2,})", clean, re.I)))
    dates = list(dict.fromkeys(re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", clean)))
    narrative = (event_text or "") + " " + clean
    detected_events = []
    event_rules = [
        ("THEFT", ["theft", "stolen", "चोरी", "चोरले", "चोरीचा"]),
        ("PROPERTY_DAMAGE", ["shattered", "broken", "damage", "तोड", "फोड", "नुकसान"]),
        ("CCTV_SIGHTING", ["cctv", "camera footage", "सीसीटीवी", "कॅमेरा"]),
        ("SUSPECT_MOVEMENT", ["fleeing", "fled", "towards", "पळून", "पळत", "फरार"]),
        ("CASE_REGISTERED", ["registered the case", "case registered", "प्रकरण नोंद", "मामला दर्ज"]),
    ]
    for event_type, markers in event_rules:
        if any(m.lower() in narrative.lower() for m in markers):
            detected_events.append({"type": event_type, "date": occurrence_date or fir_date, "time_from": time_from, "time_to": time_to, "confidence": 0.82})
    # High-value fallbacks for compact Hindi/Marathi FIR templates.
    if not fir_no:
        m=re.search(r"(?:एफआईआर|एफआयआर)\s*(?:क्रमांक|नंबर|नं\.?|क्र\.?)\s*[:#\-]?\s*([A-Z0-9/\-]+)",clean,re.I); fir_no=_clean_field(m.group(1)) if m else ""
    if not year and fir_no:
        m=re.search(r"/(\d{4})$",fir_no); year=m.group(1) if m else ""
    if not ps:
        m=re.search(r"(?:थाना|पोलीस\s+ठाणे)\s*[:\-]?\s*(.*?)(?=\s+(?:वर्ष|सन|Year|FIR|एफआईआर|एफआयआर|दिनांक|तारीख)\b|$)",clean,re.I); ps=_clean_field(m.group(1)) if m else ""
    if not district:
        m=re.search(r"(?:जिला|जिल्हा)\s*[:\-]?\s*(.*?)(?=\s+(?:थाना|पोलीस\s+ठाणे|वर्ष|सन|Year|FIR)\b|$)",clean,re.I); district=_clean_field(m.group(1)) if m else ""
    if not place:
        m=re.search(r"(?:घटना\s*(?:का)?\s*स्थान|घटनास्थळ|घटनेचे\s+ठिकाण|गुन्ह्याचे\s+ठिकाण)\s*[:\-]?\s*([^\n]+)",clean,re.I); place=_clean_field(m.group(1)) if m else ""
    if not gd:
        m=re.search(r"(?:GD|General Diary|जनरल डायरी|दैनंदिनी)[^\n]{0,50}?(?:Entry\s*No\.?|नं\.?|क्रमांक)\s*[:\-]?\s*([A-Z0-9/-]+)",clean,re.I); gd=_clean_field(m.group(1)) if m else ""
    if not event_text:
        m=re.search(r"(?:घटना\s*(?:का\s*)?(?:विवरण|संक्षिप्त विवरण)|घटनेचा\s+(?:तपशील|वर्णन)|Short\s+Description.*?Incident)[\s:–—-]*(.*?)(?=\n\s*10\.|$)",clean,re.I|re.S); event_text=_clean_field(m.group(1)) if m else ""
    if not occurrence_date: occurrence_date=fir_date
    # Sectioned FIR templates often use compact labels without punctuation.
    if not sections:
        for m in re.finditer(r"(?:धारा|कलम|कलमे)\s*[:\-]?\s*([0-9]{1,5}(?:\s*[,/&]\s*[0-9]{1,5})*)", clean, re.I):
            sections.extend(re.findall(r"\d{1,5}",m.group(1)))
        sections=list(dict.fromkeys(sections))
    if not fir_time:
        # Header forms frequently place Date and Time on the same line.
        for ln in lines:
            if re.search(r"(?:Date|दिनांक|तारीख)",ln,re.I) and re.search(r"(?:Time|समय|वेळ)",ln,re.I):
                ts=re.findall(r"[0-2]?\d\s*:\s*[0-5]\d",_digits_ascii(ln))
                if ts: fir_time=_extract_time(ts[0]); break
    if not occurrence_date:
        for ln in lines:
            if re.search(r"(?:घटना|occurrence|offense|घटनेची)",ln,re.I):
                d=_extract_date(ln)
                if d: occurrence_date=d; break
    if not (time_from and time_to):
        for ln in lines:
            if re.search(r"(?:Time\s+From|समय|वेळ)",ln,re.I):
                ts=re.findall(r"[0-2]?\d\s*:\s*[0-5]\d",_digits_ascii(ln))
                if len(ts)>=2:
                    time_from=_extract_time(ts[0]); time_to=_extract_time(ts[1]); break
    if not place:
        for ln in lines:
            if re.match(r"^\s*[-•*]?\s*(?:Address|पता|पत्ता|घटना\s*(?:का)?\s*स्थान|घटनास्थळ|घटनेचे\s+ठिकाण)\s*[:\-]",ln,re.I):   # label at the start of the line only
                m=re.search(r"(?:Address|पता|पत्ता|घटना\s*(?:का)?\s*स्थान|घटनास्थळ|घटनेचे\s+ठिकाण)\s*[:\-]?\s*(.+)$",ln,re.I)
                if m and not re.search(r"(?:Flat|Father|वडिलांचे|पिता)",m.group(1),re.I): place=_clean_field(m.group(1)); break

    # Additional common FIR fields are retained as review-only structured text.
    # They are never promoted into the graph automatically; an investigator must verify them.
    optional_aliases = {
        "victim": ["Victim", "Victim Name", "पीड़ित", "पीडित", "तक्रारदार / पीडित"],
        "victim_address": ["Victim Address", "पीड़ित का पता", "पीडिताचा पत्ता"],
        "accused_name": ["Accused Name", "Name of Accused", "आरोपी का नाम", "आरोपीचे नाव"],
        "accused_address": ["Accused Address", "आरोपी का पता", "आरोपीचा पत्ता"],
        "registration_date": ["Date of Registration", "Registration Date", "प्रकरण नोंद दिनांक", "मामला दर्ज दिनांक"],
        "dispatch_date": ["Date of Dispatch", "Dispatch Date", "प्रेषण दिनांक"],
        "delay_reason": ["Reason for Delay", "Delay Reason", "विलंब का कारण", "विलंबाचे कारण"],
        "action_taken": ["Action Taken", "कार्रवाई की गई", "केलेली कारवाई"],
        "investigation_status": ["Investigation Status", "Case Status", "तपासाची स्थिती"],
        "property_value": ["Estimated Value", "Property Value", "संपत्तीची किंमत", "अनुमानित मूल्य"],
        "medical_exam": ["Medical Examination", "Medico Legal", "चिकित्सा परीक्षण", "वैद्यकीय तपासणी"],
        "witnesses": ["Witnesses", "Witness Names", "गवाह", "साक्षीदार"],
        "crime_category": ["Crime Type", "Nature of Offence", "अपराध का प्रकार", "गुन्ह्याचा प्रकार"],
    }
    additional_fields = {}
    for opt_key, aliases in optional_aliases.items():
        val = _extract_label_value(clean, aliases, 500)
        if val:
            additional_fields[opt_key] = val

    language_info = detect_language(clean, language)
    return {
        "document_type": "FIR" if re.search(r"FIRST INFORMATION REPORT|\bFIR\b|एफ[.]?आई[.]?आर|प्रथम सूचना रिपोर्ट|प्रथम माहिती अहवाल", clean, re.I) else "INVESTIGATION_DOCUMENT",
        "fir_number": fir_no,
        "year": year,
        "district": district,
        "police_station": ps,
        "fir_date": fir_date,
        "fir_time": fir_time,
        "occurrence_date": occurrence_date or fir_date,
        "occurrence_time_from": time_from,
        "occurrence_time_to": time_to,
        "sections": sections,
        "general_diary_entry": gd,
        "additional_fields": additional_fields,
        "place_of_occurrence": place,
        "complainant": complainant,
        "father_name": father,
        "complainant_address": address,
        "accused_description": accused,
        "investigating_officer": officer,
        "stolen_property": property_items,
        "incident_description": event_text,
        "detected_events": detected_events,
        "phones": phones,
        "vehicles": vehicles,
        "vehicle_descriptions": vehicle_descriptions,
        "serial_numbers": serial_numbers,
        "accounts": accounts,
        "dates": dates,
        "language": language_info,
    }

def intelligent_entities(text: str, structured: dict[str, Any]) -> list[dict[str, Any]]:
    """Context-aware entity extraction with roles and evidence snippets."""
    entities: list[dict[str, Any]] = []
    seen = set()
    def add(value, typ, confidence=0.9, role="", source="context-NLP"):
        value = re.sub(r"\s+", " ", str(value or "").strip(" :-–—\t"))
        if not value: return
        key = (typ, value.casefold())
        if key in seen: return
        seen.add(key)
        pos = text.casefold().find(value.casefold())
        entities.append({"text": value, "type": typ, "confidence": round(confidence, 3), "start": max(0,pos), "end": max(0,pos)+len(value), "method": source, "role": role})
    add(structured.get("fir_number"), "CASE", .99, "FIR_NUMBER")
    add(structured.get("district"), "DISTRICT", .96, "JURISDICTION")
    add(structured.get("police_station"), "POLICE_STATION", .97, "REPORTING_STATION")
    add(structured.get("complainant"), "PERSON", .97, "COMPLAINANT")
    add(structured.get("father_name"), "PERSON", .88, "FATHER_OF_COMPLAINANT")
    add(structured.get("investigating_officer"), "PERSON", .97, "INVESTIGATING_OFFICER")
    add(structured.get("place_of_occurrence"), "LOCATION", .95, "OCCURRENCE_LOCATION")
    add(structured.get("complainant_address"), "ADDRESS", .94, "COMPLAINANT_ADDRESS")
    add(structured.get("accused_description"), "SUSPECT_DESCRIPTION", .90, "UNKNOWN_OR_SUSPECT")
    for x in structured.get("phones", []): add(x, "PHONE", .98)
    for x in structured.get("vehicles", []): add(x, "VEHICLE", .98)
    for x in structured.get("vehicle_descriptions", []): add(x, "VEHICLE_DESCRIPTION", .82, "SUSPECT_VEHICLE_DESCRIPTION")
    for x in structured.get("serial_numbers", []): add(x, "DIGITAL_IDENTIFIER", .97, "PROPERTY_SERIAL_NUMBER")
    for x in structured.get("accounts", []): add(x, "ACCOUNT", .97, "FINANCIAL_ACCOUNT")
    for x in structured.get("sections", []): add(x, "LAW_SECTION", .96)
    for x in structured.get("stolen_property", []): add(x, "PROPERTY", .91, "STOLEN_PROPERTY")
    for x in structured.get("dates", []): add(x, "DATE", .97)
    # Conservative additional names from role-bearing lines in English/Hindi/Marathi.
    for pat, role in [
        (r"Name\s*:\s*([^\n]+)", "NAMED_PERSON"),
        (r"(?:तक्रारदार|माहितीदार|शिकायतकर्ता|सूचनाकर्ता)\s*[:\-]\s*([^\n]+)", "COMPLAINANT"),
        (r"(?:तपास अधिकारी|जांच अधिकारी)\s*[:\-]\s*([^\n]+)", "INVESTIGATING_OFFICER"),
    ]:
        for x in _all(pat, text): add(x, "PERSON", .90, role)
    return entities


def intelligent_relationships(text: str, entities: list[dict[str, Any]], structured: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    def rel(source, target, relation, confidence, evidence, rationale):
        if source and target and source.casefold() != target.casefold():
            out.append({"source":source,"target":target,"relation":relation,"confidence":round(confidence,3),"evidence":evidence[:600],"rationale":rationale})
    comp = structured.get("complainant", "")
    off = structured.get("investigating_officer", "")
    ps = structured.get("police_station", "")
    place = structured.get("place_of_occurrence", "")
    fir = structured.get("fir_number", "")
    if comp and fir: rel(comp, fir, "REPORTED_IN", .94, f"Complainant/informant named in FIR {fir}.", "Role-aware FIR field")
    if off and fir: rel(off, fir, "ASSIGNED_TO", .94, f"Investigating Officer assigned in FIR {fir}.", "Role-aware FIR field")
    if ps and fir: rel(ps, fir, "REGISTERED", .95, f"FIR {fir} is registered at {ps}.", "FIR header")
    if fir and place: rel(fir, place, "LOCATED_AT", .91, f"Place of occurrence: {place}.", "FIR occurrence section")
    if comp and place: rel(comp, place, "REPORTED_AT", .86, f"Complaint describes occurrence at {place}.", "FIR occurrence context")
    for v in structured.get("vehicles", []) + structured.get("vehicle_descriptions", []):
        if structured.get("accused_description"): rel(structured["accused_description"], v, "SUSPECT_VEHICLE", .74, structured["accused_description"], "Vehicle mentioned in accused description")
    # Explicit sentence-level relations, but only when entity roles make sense.
    for sent in re.split(r"(?<=[.!?।])\s+", text):
        present = [e for e in entities if e["text"].casefold() in sent.casefold()]
        if len(present) < 2: continue
        low = sent.casefold()
        relation = None
        if any(w in low for w in ["called", "call", "phone", "contacted", "कॉल", "फोन", "दूरध्वनी"]): relation = "CALLED"
        elif any(w in low for w in ["met", "meeting", "मुलाकात", "भेट"]): relation = "MET"
        elif any(w in low for w in ["vehicle", "scooter", "car", "riding", "दुचाकी", "वाहन"]): relation = "USED"
        elif any(w in low for w in ["transfer", "paid", "sent", "transaction", "हस्तांतरण", "व्यवहार"]): relation = "TRANSFERRED"
        if relation:
            rel(present[0]["text"], present[1]["text"], relation, .72, sent, "Sentence-level contextual relation")
    # Dedupe.
    unique=[]; seen=set()
    for x in out:
        k=(x["source"].casefold(),x["target"].casefold(),x["relation"])
        if k not in seen: seen.add(k); unique.append(x)
    return unique


def parse_fir(text: str, language: str = "auto") -> dict[str, Any]:
    """Structured FIR fields. Label-driven parser + validators (ai_engine.nlp.pipeline) over the legacy template parser."""
    from ai_engine.nlp.pipeline import parse_structured
    return parse_structured(text, language)
