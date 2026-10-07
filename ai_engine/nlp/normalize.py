"""Canonical forms, validators and OCR-confusion repair for identifiers, dates and money.

Everything here is deterministic and offline. Each ``find_*`` function returns dictionaries with
``start``/``end`` offsets into the text it was given (offsets are preserved by ``ascii_digits`` because
digit substitution is one character for one character), so callers can point a reviewer at the exact
evidence span.
"""
from __future__ import annotations

import calendar
import re
from typing import Any

# --------------------------------------------------------------------------- digits / OCR confusions
_DIGIT_BASES = (0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66, 0x0660, 0x06F0)
_DIGIT_MAP = {ord(chr(b + i)): ord(str(i)) for b in _DIGIT_BASES for i in range(10)}
_DIGIT_MAP.update({ord(c): ord("0") + i for i, c in enumerate("０１２３４５６７８９")})


def ascii_digits(text: str) -> str:
    """Devanagari/Bengali/Gurmukhi/Gujarati/Odia/Tamil/Telugu/Kannada/Malayalam/Arabic-Indic digits -> 0-9 (same length)."""
    return (text or "").translate(_DIGIT_MAP)


_TO_DIGIT = str.maketrans({"O": "0", "o": "0", "Q": "0", "I": "1", "l": "1", "|": "1", "i": "1", "Z": "2", "S": "5", "s": "5",
                           "B": "8", "G": "6", "b": "6"})
_TO_LETTER = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"})
DIGITLIKE = "0-9OoQIl|iZSsBGb"


def ocr_digits(s: str) -> str:
    return (s or "").translate(_TO_DIGIT)


def ocr_letters(s: str) -> str:
    return (s or "").translate(_TO_LETTER)


def _was_repaired(raw: str) -> bool:
    return bool(re.search(r"[^0-9\s\-.+]", ascii_digits(raw)))


# --------------------------------------------------------------------------- validators
_VD = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
       [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1], [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
       [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VP = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
       [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1], [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff_ok(number: str) -> bool:
    """Aadhaar numbers carry a Verhoeff check digit; a pass makes a 12-digit run far more likely to be one."""
    if not re.fullmatch(r"\d{12}", number or ""):
        return False
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _VD[c][_VP[i % 8][int(ch)]]
    return c == 0


INDIAN_STATE_CODES = set("""AN AP AR AS BR CH CG DD DL DN GA GJ HP HR JH JK KA KL LA LD MH ML MN MP MZ NL OD OR PB PY RJ SK TN TR TS UK UA UP WB""".split())
PAN_HOLDER_TYPES = set("PCHFATBLJG")

UPI_HANDLES = set("""ybl ibl axl okaxis okhdfcbank okicici oksbi paytm apl upi sbi hdfcbank icici axisbank kotak yesbank pnb boi cnrb fbl
aubank barodampay federal idfcfirst indus ikwik jupiteraxis rbl slice postbank freecharge airtel jio ptaxis ptyes pthdfc ptsbi
okbizaxis waicici wahdfcbank waaxis wasbi abfspay dbs cboi centralbank unionbank unionbankofindia uboi utbi yapl juspay tapicici
ezeepay mahb psb sib sc hsbc idbi kbl karb kvb ubi tmb equitas dlb csbpay csbcash""".split())


def canon_phone(raw: str) -> str:
    """'+91 98765 43210' / '098765-43210' / '9876543210' -> '9876543210'; '' when it is not an Indian mobile number."""
    d = re.sub(r"\D", "", ascii_digits(raw))
    if len(d) == 14 and d.startswith("0091"):
        d = d[4:]
    elif len(d) == 13 and d.startswith("091"):
        d = d[3:]
    elif len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d if re.fullmatch(r"[6-9]\d{9}", d) else ""


def canon_vehicle(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()


def canon_account(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()


# --------------------------------------------------------------------------- dates
_EN_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_MONTHS: dict[str, int] = {}
for _i, _m in enumerate(_EN_MONTHS, start=1):
    _MONTHS[_m] = _i
    _MONTHS[_m[:3]] = _i
_MONTHS.update({"sept": 9, "janwari": 1, "farwari": 2, "farvari": 2, "mai": 5, "agast": 8, "sitambar": 9, "sitamber": 9, "aktubar": 10,
                "navambar": 11, "navamber": 11, "disambar": 12, "disamber": 12})
_HI_MONTHS = {
    "जनवरी": 1, "जनवरी": 1, "फरवरी": 2, "फ़रवरी": 2, "मार्च": 3, "अप्रैल": 4, "अप्रेल": 4, "मई": 5, "जून": 6, "जुलाई": 7, "अगस्त": 8,
    "सितंबर": 9, "सितम्बर": 9, "सितमबर": 9, "अक्टूबर": 10, "अक्तूबर": 10, "अक्टुबर": 10, "नवंबर": 11, "नवम्बर": 11, "नवमबर": 11,
    "दिसंबर": 12, "दिसम्बर": 12, "दिसमबर": 12,
    # Marathi
    "जानेवारी": 1, "फेब्रुवारी": 2, "एप्रिल": 4, "मे": 5, "जुलै": 7, "ऑगस्ट": 8, "सप्टेंबर": 9, "ऑक्टोबर": 10, "नोव्हेंबर": 11, "डिसेंबर": 12,
}
_MONTHS.update(_HI_MONTHS)
_MONTH_RX = "|".join(sorted((re.escape(k) for k in _MONTHS), key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th|ला|वीं|वी)?"
_DAYD = r"[0-3]?[0-9OoIl]"

_NUMERIC_DATE = re.compile(r"(?<![\dA-Za-z/])([0-9OoIl]{1,2})\s*[/\-.]\s*([0-9OoIl]{1,2})\s*[/\-.]\s*([0-9OoIl]{4}|[0-9OoIl]{2})(?![\dA-Za-z])")
_ISO_DATE = re.compile(r"(?<![\d])((?:19|20)\d{2})-(\d{2})-(\d{2})(?![\d])")
_TEXT_DATE_DMY = re.compile(rf"(?<![\d/])(\d{{1,2}}){_ORD}\s*(?:of\s+)?({_MONTH_RX})\.?,?\s+((?:19|20)\d{{2}})(?!\d)", re.I)
_TEXT_DATE_MDY = re.compile(rf"\b({_MONTH_RX})\.?\s+(\d{{1,2}}){_ORD},?\s+((?:19|20)\d{{2}})(?!\d)", re.I)
_TWO_DAY_DATE = re.compile(rf"(?<![\d/])(\d{{1,2}}){_ORD}\s*(?:and|&|/|to|-|और|व)\s*(\d{{1,2}}){_ORD}\s+({_MONTH_RX})\.?,?\s+((?:19|20)\d{{2}})(?!\d)", re.I)


def _mk_date(y: int, m: int, d: int) -> str:
    return f"{y:04d}-{m:02d}-{d:02d}"


def _valid_ymd(y: int, m: int, d: int) -> bool:
    return 1900 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= calendar.monthrange(y, m)[1]


def find_dates(text: str) -> list[dict[str, Any]]:
    """All calendar dates (day-first, Indian convention) with ISO normalisation. Text must have ASCII digits."""
    out: list[dict[str, Any]] = []
    taken: list[tuple[int, int]] = []

    def free(s, e):
        return not any(s < b and a < e for a, b in taken)

    def add(m, y, mo, d, reason, repaired=False, conf=0.96):
        if not free(m.start(), m.end()):
            return
        taken.append((m.start(), m.end()))
        ok = _valid_ymd(y, mo, d)
        flags = []
        if repaired:
            flags.append("ocr_repaired")
        if not ok:
            flags.append("invalid_calendar_date")
        out.append({"kind": "DATE", "surface": m.group(0), "start": m.start(), "end": m.end(), "normalized": _mk_date(y, mo, d) if ok else "",
                    "confidence": (0.9 if repaired else conf) if ok else 0.3, "reason": reason if ok else "Looks like a date but is not a valid calendar date (possible OCR error): " + m.group(0),
                    "flags": flags, "day": d, "month": mo, "year": y})

    for m in _TWO_DAY_DATE.finditer(text):
        mo = _MONTHS.get(m.group(3).lower().rstrip("."), 0) or _MONTHS.get(m.group(3), 0)
        y = int(m.group(4))
        for g in (1, 2):
            d = int(m.group(g))
            if free(m.start(g), m.end(g)) or True:
                out.append({"kind": "DATE", "surface": m.group(g) + " " + m.group(3) + " " + m.group(4), "start": m.start(), "end": m.end(),
                            "normalized": _mk_date(y, mo, d) if _valid_ymd(y, mo, d) else "", "confidence": 0.9,
                            "reason": "Two dates written together ('4th and 5th January 2026'); day-first order", "flags": ["date_range_pair"], "day": d, "month": mo, "year": y})
        taken.append((m.start(), m.end()))
    for m in _TEXT_DATE_DMY.finditer(text):
        mo = _MONTHS.get(m.group(2).lower()) or _MONTHS.get(m.group(2)) or 0
        add(m, int(m.group(3)), mo, int(m.group(1)), "Date written with a month name; converted to ISO (yyyy-mm-dd)")
    for m in _TEXT_DATE_MDY.finditer(text):
        mo = _MONTHS.get(m.group(1).lower()) or _MONTHS.get(m.group(1)) or 0
        add(m, int(m.group(3)), mo, int(m.group(2)), "Date written as 'Month day, year'; converted to ISO")
    for m in _ISO_DATE.finditer(text):
        add(m, int(m.group(1)), int(m.group(2)), int(m.group(3)), "ISO date")
    for m in _NUMERIC_DATE.finditer(text):
        d_raw, mo_raw, y_raw = m.group(1), m.group(2), m.group(3)
        repaired = bool(re.search(r"[^\d]", d_raw + mo_raw + y_raw))
        d, mo, y = int(ocr_digits(d_raw)), int(ocr_digits(mo_raw)), int(ocr_digits(y_raw))
        if y < 100:
            y += 2000
        add(m, y, mo, d, "Numeric date read day-first (dd/mm/yyyy); converted to ISO" + ("; OCR letters in digits were corrected" if repaired else ""), repaired)
    out.sort(key=lambda x: x["start"])
    return out


def iso_to_display(iso: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else ""


def first_date(text: str) -> str:
    for d in find_dates(ascii_digits(text or "")):
        if d["normalized"]:
            return d["normalized"]
    return ""


def find_times(text: str) -> list[dict[str, Any]]:
    out = []
    for m in re.finditer(r"(?<![\d:])([0-2]?[0-9OoIl])\s*:\s*([0-5][0-9OoIl])(?:\s*:\s*[0-5]\d)?\s*(hrs?\.?|hours?|बजे|am|pm|AM|PM)?(?![\d])", text):
        h = int(ocr_digits(m.group(1))); mi = int(ocr_digits(m.group(2)))
        suf = (m.group(3) or "").lower()
        if suf == "pm" and h < 12:
            h += 12
        if suf == "am" and h == 12:
            h = 0
        if h > 23:
            continue
        out.append({"kind": "TIME", "surface": m.group(0).strip(), "start": m.start(), "end": m.end(), "normalized": f"{h:02d}:{mi:02d}",
                    "confidence": 0.93, "reason": "Clock time", "flags": []})
    return out


# --------------------------------------------------------------------------- money
_CUR_PRE = r"(?:(?<![A-Za-z])(?:Rs|INR|RS)\.?|₹|रु\.?|रू\.?|(?<![A-Za-z])Rupees?)"
_CUR_POST = (r"(?:/-|(?<![A-Za-z])(?:rupees?|rupaye|rupaiye|rupay|rupayon|only)(?![A-Za-z])|रुपये|रुपए|रूपये|रुपया|रुपयों|रुपयांची|रुपयांचा|रुपयांचे|रु\b|टका|টাকা|ரூபாய்|ரூ|"
             r"రూపాయలు|రూపాయి|రూ|રૂપિયા|રૂપિયાની|રૂ|ਰੁਪਏ|ਰੁਪਈਏ|ರೂಪಾಯಿ|ರೂ|രൂപ|روپے|روپیہ|روپئے|Rs\.?)")
_UNITS = {"lakh": 10**5, "lakhs": 10**5, "lac": 10**5, "lacs": 10**5, "लाख": 10**5, "लाखों": 10**5, "लक्ष": 10**5,
          "crore": 10**7, "crores": 10**7, "cr": 10**7, "करोड़": 10**7, "करोड": 10**7, "कोटी": 10**7,
          "thousand": 10**3, "hazar": 10**3, "hazaar": 10**3, "hajar": 10**3, "हजार": 10**3, "हज़ार": 10**3, "हजारों": 10**3, "hundred": 100,
          "लाख": 10**5, "লাখ": 10**5, "লক্ষ": 10**5, "லட்சம்": 10**5, "లక్షలు": 10**5, "లక్ష": 10**5, "લાખ": 10**5, "ਲੱਖ": 10**5, "ಲಕ್ಷ": 10**5, "ലക്ഷം": 10**5,
          "لاکھ": 10**5, "کروڑ": 10**7, "কোটি": 10**7, "கோடி": 10**7, "కోట్లు": 10**7, "કરોડ": 10**7, "ਕਰੋੜ": 10**7, "ಕೋಟಿ": 10**7, "കോടി": 10**7}
_UNIT_RX = "|".join(sorted((re.escape(k) for k in _UNITS), key=len, reverse=True))
_NUM_LOOSE = r"(?:[0-9OoQ](?:[0-9OoQ,]*[0-9OoQ])?)(?:\.\d+)?"
_AMT_PRE = re.compile(rf"(?P<cur>{_CUR_PRE})\s*(?P<num>{_NUM_LOOSE})\s*(?P<unit>(?:{_UNIT_RX})(?![A-Za-z]))?\s*(?P<post>/-|(?<![A-Za-z])only(?![A-Za-z]))?", re.I)
_AMT_UNIT = re.compile(rf"(?<![A-Za-z0-9.,])(?P<num>[0-9](?:[0-9,]*[0-9])?(?:\.\d+)?)\s*(?P<unit>(?:{_UNIT_RX})(?![A-Za-z]))(?:\s*(?P<post>{_CUR_POST}))?", re.I)
_AMT_POST = re.compile(rf"(?<![A-Za-z0-9.,/\-])(?P<num>[0-9](?:[0-9,]*[0-9])?(?:\.\d+)?)\s*(?P<post>{_CUR_POST})", re.I)


def _to_number(raw: str) -> float | None:
    s = ocr_digits(raw.replace(",", ""))
    if not re.fullmatch(r"\d+(?:\.\d+)?", s):
        return None
    return float(s)


def find_amounts(text: str, avoid: list[tuple[int, int]] | None = None) -> list[dict[str, Any]]:
    """Rupee amounts -> integer rupees (lakh/crore/hazar aware). Currency marker or a unit word is required."""
    avoid = avoid or []
    out: list[dict[str, Any]] = []
    taken: list[tuple[int, int]] = []

    def free(s, e):
        return not any(s < b and a < e for a, b in taken + avoid)

    def add(m, num_raw, unit, reason, conf=0.95):
        n = _to_number(num_raw)
        if n is None or not free(m.start(), m.end()):
            return
        val = n * (_UNITS.get(unit.lower() if unit else "", _UNITS.get(unit or "", 1)) if unit else 1)
        repaired = bool(re.search(r"[OoQ]", num_raw))
        taken.append((m.start(), m.end()))
        out.append({"kind": "AMOUNT", "surface": m.group(0).strip(), "start": m.start(), "end": m.start() + len(m.group(0).rstrip()),
                    "normalized": str(int(round(val))), "confidence": 0.86 if repaired else conf,
                    "reason": reason + ("; OCR letters O/o read as zero" if repaired else ""),
                    "flags": ["ocr_repaired"] if repaired else [], "currency": "INR"})

    for m in _AMT_PRE.finditer(text):
        if not re.search(r"\d", m.group("num")):
            continue  # "Rs." followed by letters only is not an amount
        add(m, m.group("num"), m.group("unit"), "Rupee amount after a currency marker; normalised to whole rupees")
    for m in _AMT_UNIT.finditer(text):
        add(m, m.group("num"), m.group("unit"), "Amount written with a unit word (lakh/crore/thousand); converted to whole rupees", 0.9)
    for m in _AMT_POST.finditer(text):
        add(m, m.group("num"), None, "Number followed by a rupee word; normalised to whole rupees", 0.92)
    # "Rs. 25,000 bheje aur phir 15000 aur bheje": a bare number after 'phir/aur/and/then' close to an earlier amount keeps its currency
    for m in re.finditer(r"(?:(?<![A-Za-z])(?:phir|fir|aur|and|then|another|further|also)(?![A-Za-z])|फिर|और|पुन:)\s+(?P<num>[0-9][0-9,]{2,6})(?![0-9,]*[0-9/:%.\-])(?!\s*(?:%|km|kg|gm|grams?|hrs|pm|am|days|din|saal|years))", text, re.I):
        s0 = m.start("num")
        prev = [a for a in out if a["end"] <= s0 and s0 - a["end"] <= 90 and not re.search(r"[\n\u0964]", text[a["end"]:s0]) and not re.search(r"[.]\s+[A-Z]", text[a["end"]:s0])]
        if not prev or not free(s0, m.end("num")):
            continue
        n = _to_number(m.group("num"))
        if n is None or n < 100:
            continue
        taken.append((s0, m.end("num")))
        out.append({"kind": "AMOUNT", "surface": m.group("num"), "start": s0, "end": m.end("num"), "normalized": str(int(n)), "confidence": 0.7, "currency": "INR",
                    "reason": "Bare number after 'phir/aur/and' following another rupee amount in the same sentence; assumed to be rupees too - please confirm", "flags": ["elided_currency"]})
    out.sort(key=lambda x: x["start"])
    return out


# --------------------------------------------------------------------------- misc
def strip_titles(name: str) -> str:
    return re.sub(r"^(?:(?:Mr|Mrs|Ms|Miss|Dr|Shri|Smt|Sri|Sh|Late|Kumari|Km)\.?\s+)+", "", (name or "").strip(), flags=re.I)


def name_key(name: str) -> str:
    """Case/punctuation-insensitive key used to merge the same person written slightly differently."""
    n = strip_titles(name).casefold()
    n = re.sub(r"[^\wऀ-෿؀-ۿ ]", " ", n)
    return " ".join(n.split())
