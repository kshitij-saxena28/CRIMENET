"""Fact extraction: identifiers, people (with roles), relationships - each with an evidence span and a plain reason.

Design rules
  * Every item carries ``start``/``end`` offsets into the ORIGINAL text, an ``evidence`` snippet and a ``reason``
    written for a police reviewer (why the system thinks this is a phone number / an accused / etc.).
  * Nothing is silently corrected: when OCR-confusable characters (O/0, l/1, S/5, B/8 ...) were read as digits or
    letters the item is flagged ``ocr_repaired`` and its confidence is reduced.
  * Confidence is a calibrated estimate (see ``CALIBRATION``), not a raw pattern score.
"""
from __future__ import annotations

import re
from typing import Any

from ai_engine.nlp import labels as LB
from ai_engine.nlp import legal as LG
from ai_engine.nlp import normalize as N

# ---------------------------------------------------------------------------------------------------- helpers
_WS = re.compile(r"\s+")


def _clip(s: str, n: int = 50) -> str:
    return _WS.sub(" ", s.strip())[:n]


def snippet(text: str, s: int, e: int, w: int = 70) -> str:
    a, b = max(0, s - w), min(len(text), e + w)
    out = _WS.sub(" ", text[a:b]).strip()
    return ("..." if a > 0 else "") + out + ("..." if b < len(text) else "")


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Split into sentences/lines while keeping offsets. Abbreviations (Mr., Rs., No., s/o ...) do not end a sentence."""
    spans: list[tuple[int, int]] = []
    abbr = {"mr", "mrs", "ms", "dr", "sh", "smt", "rs", "no", "mohd", "md", "st", "sr", "jr", "ph", "mob", "vs", "addl", "dy", "asst", "insp", "inspr", "late", "km", "w", "s", "d", "c", "h", "r", "u", "i", "o", "p", "b", "n", "t", "a", "e", "f", "g", "k", "l", "m", "q", "v", "x", "y", "z"}
    pos = 0
    for line in text.split("\n"):
        ls = pos
        start = 0
        for m in re.finditer(r"[.!?।]+(?=\s+\S|$)", line):
            j = m.start()
            k = j
            while k > 0 and line[k - 1].isalpha():
                k -= 1
            word = line[k:j].casefold()
            nxt = line[m.end():].lstrip()[:1]
            if word in abbr and (len(word) < 3 or nxt.islower() or nxt.isdigit()):
                continue
            if re.match(r"\d", line[k:j] or "x") and nxt.isdigit():
                continue
            seg_end = m.end()
            if line[start:seg_end].strip():
                spans.append((ls + start, ls + seg_end))
            start = seg_end
        if line[start:].strip():
            spans.append((ls + start, ls + len(line)))
        pos += len(line) + 1
    return spans


def _sent_index(spans: list[tuple[int, int]], pos: int) -> int:
    for i, (a, b) in enumerate(spans):
        if a <= pos < b:
            return i
    return max(0, len(spans) - 1)


# ---------------------------------------------------------------------------------------------------- calibration
# Confidence by evidence type (fitted on the dev split of demo_dataset/extraction_gold, checked on the held-out split).
CALIBRATION = {
    "validated_pattern": 0.97,   # structure + checksum/state-code/format validated (PAN, IFSC, valid vehicle, Aadhaar Verhoeff)
    "labelled": 0.95,            # value follows an explicit label for that field
    "pattern": 0.90,             # matches a strict pattern without a label
    "repaired": 0.80,            # OCR-confusable characters were reinterpreted
    "first_person": 0.86,
    "cue": 0.82,                 # role decided by a nearby cue word
    "weak_cue": 0.62,            # role decided by weak context (verb, possessive, neighbour word)
    "inferred": 0.55,            # inference (name inside a UPI handle, unstated Act, pronoun resolution)
    "unknown_role": 0.50,        # a person name with no role cue at all
}


def cal(kind: str, base: str, *, delta: float = 0.0) -> float:
    v = CALIBRATION.get(base, 0.7) + delta
    return round(max(0.05, min(0.99, v)), 3)


def _item(kind: str, text: str, s: int, e: int, normalized: str, confidence: float, reason: str, flags: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    d = {"kind": kind, "surface": text[s:e].strip(), "start": s, "end": e, "normalized": normalized, "confidence": round(confidence, 3), "reason": reason,
         "flags": flags or [], "evidence": snippet(text, s, e), "line": line_of(text, s)}
    d.update(extra)
    return d


# ---------------------------------------------------------------------------------------------------- identifiers
_ACCT_CTX = re.compile(r"a\s*/\s*c\b|a\.c\b|acct|account|acc\.?\s*no|bank\s+no|खाता|खाते|खात्यात|खाती|अकाउंट|হিসাব|খাতা|கணக்கு|ఖాతా|ખાતા|ਖਾਤਾ|حساب|ಖಾತೆ|അക്കൗണ്ട്|khata|khate", re.I)
_AADHAAR_CTX = re.compile(r"aadhaar|aadhar|adhaar|adhar|\buid\b|आधार|আধার|ஆதார்|ఆధార్|આધાર|ਆਧਾਰ|آدھار|ಆಧಾರ್", re.I)
_TXN_CTX = re.compile(r"transaction|txn|utr|ref(?:erence)?\s*(?:no|id|number)|order\s*id|trans\.?\s*id|rrn|लेनदेन|ट्रांजैक्शन", re.I)
_PHONE_CTX = re.compile(r"mob(?:ile)?|phone|contact|call|number|no\.|ph\b|tel|whatsapp|फोन|मोबाइल|मोबाईल|नंबर|संपर्क|मो\.|कॉल|कैल|মোবাইল|கைபேசி|మొబైల్|મોબાઇલ|ਮੋਬਾਈਲ|موبائل", re.I)
_IFSC_BANKS = set("""SBIN HDFC ICIC UTIB PUNB BARB CNRB KKBK YESB IDIB UBIN BKID CBIN MAHB IOBA ORBC ALLA SYNB FDRL INDB IBKL KARB SCBL HSBC CITI DEUT RATN AUBL ESFB PYTM AIRP JAKA TMBL CSBK DLXB KVBL SIBL VIJB UCBA BDBL IDFB NSPB PSIB SRCB TJSB COSB KCCB""".split())
_STATES = "|".join(sorted(N.INDIAN_STATE_CODES))
_VEH = re.compile(rf"(?<![A-Za-z0-9])(?P<st>{_STATES})[\s\-]?(?P<rto>[0-9OoIl]{{1,2}})[\s\-]?(?P<ser>[A-Z0-9](?:[\s\-]?[A-Z0-9]){{0,2}})[\s\-]?(?P<num>[0-9OoIlSBZ]{{4}})(?![A-Za-z0-9])")
_VEH_BH = re.compile(r"(?<![A-Za-z0-9])(?P<yy>\d{2})\s?BH\s?(?P<num>\d{4})\s?(?P<ser>[A-Z]{1,2})(?![A-Za-z0-9])")
_EMAILISH = re.compile(r"(?<![A-Za-z0-9._%+\-])(?P<local>[A-Za-z0-9][A-Za-z0-9._%+\-]{1,60})@(?P<dom>[A-Za-z][A-Za-z0-9\-]{1,30}(?:\.[A-Za-z]{2,})*)")
_RUN = re.compile(r"(?<![A-Za-z0-9])\+?(?:[0-9OoQIl|SBZG][ \-.]?){8,}[0-9OoQIl|SBZG](?![A-Za-z0-9])")
_LANDLINE = re.compile(r"(?<![\d])0\d{2,4}[\-\s]\d{6,8}(?!\d)")
_PAN = re.compile(r"(?<![A-Za-z0-9])([A-Z0-9]{5})([0-9OoIlSBZ]{4})([A-Z0-9])(?![A-Za-z0-9])")
_IFSC = re.compile(r"(?<![A-Za-z0-9])([A-Z]{4})[0OQ]([A-Z0-9]{6})(?![A-Za-z0-9])")
_ORG = re.compile(r"(?<![A-Za-z])M\s?/\s?s\.?\s+((?:[A-Z][A-Za-z&.\-]*)(?:\s+[A-Z][A-Za-z&.\-]*){0,4})")


def _overlaps(taken: list[tuple[int, int]], s: int, e: int) -> bool:
    return any(s < b and a < e for a, b in taken)


def extract_identifiers(text: str, fir_date_iso: str = "") -> list[dict[str, Any]]:
    """text must already have ASCII digits (same offsets as the original)."""
    items: list[dict[str, Any]] = []
    taken: list[tuple[int, int]] = []

    def take(s, e):
        taken.append((s, e))

    # ---- e-mail and UPI
    for m in _EMAILISH.finditer(text):
        local, dom = m.group("local"), m.group("dom")
        s, e = m.start(), m.end()
        while text[e - 1] in ".-":
            e -= 1
        dom_l = text[m.start("dom"):e].lower()
        ctx = text[max(0, s - 40):s].lower()
        if "." in dom_l and not dom_l.split(".")[0] in N.UPI_HANDLES or (("." in dom_l) and re.search(r"\.(com|in|org|net|co|edu|gov|io|xyz|info)\b", dom_l)):
            items.append(_item("EMAIL", text, s, e, text[s:e].lower(), cal("EMAIL", "validated_pattern", delta=-0.02), "E-mail address pattern (name@domain.tld)"))
            take(s, e)
        elif dom_l in N.UPI_HANDLES or re.search(r"upi|phonepe|gpay|google\s*pay|paytm|bhim|vpa", ctx):
            v = text[s:e].lower()
            base = "validated_pattern" if dom_l in N.UPI_HANDLES else "pattern"
            items.append(_item("UPI", text, s, e, v, cal("UPI", base), "UPI payment address (name@bank-handle)" + ("" if dom_l in N.UPI_HANDLES else "; handle not in the known list but UPI is mentioned nearby"),
                               handle=dom_l, local=text[s:m.start("dom") - 1].lower()))
            take(s, e)
    # ---- dates / times (masked so their digits are never read as numbers)
    for d in N.find_dates(text):
        take(d["start"], d["end"])
    for tm in N.find_times(text):
        take(tm["start"], tm["end"])
    # ---- money (masked)
    for a in N.find_amounts(text, avoid=[]):
        if not _overlaps(taken, a["start"], a["end"]):
            take(a["start"], a["end"])
    # ---- vehicle registrations
    for m in _VEH.finditer(text):
        if _overlaps(taken, m.start(), m.end()):
            continue
        st, rto_raw, ser_raw, num_raw = m.group("st"), m.group("rto"), m.group("ser"), m.group("num")
        rto = N.ocr_digits(rto_raw)
        ser = N.ocr_letters(re.sub(r"[\s\-]", "", ser_raw))
        num = N.ocr_digits(num_raw)
        if not (rto.isdigit() and 1 <= int(rto) <= 99 and ser.isalpha() and num.isdigit()):
            continue
        repaired = (rto != rto_raw) or (ser != re.sub(r"[\s\-]", "", ser_raw)) or (num != num_raw)
        v = f"{st}{int(rto)}{ser}{num}" if len(rto) == 2 and rto.startswith("0") is False else f"{st}{rto}{ser}{num}"
        v = f"{st}{rto}{ser}{num}"
        items.append(_item("VEHICLE", text, m.start(), m.end(), v, cal("VEHICLE", "repaired" if repaired else "validated_pattern"),
                           "Indian vehicle registration: state code " + st + ", RTO " + rto + ", series " + ser + ", number " + num + ("; OCR-confusable characters were corrected" if repaired else ""),
                           ["ocr_repaired"] if repaired else [], state=st))
        take(m.start(), m.end())
    for m in _VEH_BH.finditer(text):
        if not _overlaps(taken, m.start(), m.end()):
            items.append(_item("VEHICLE", text, m.start(), m.end(), f"{m.group('yy')}BH{m.group('num')}{m.group('ser')}", cal("VEHICLE", "validated_pattern"), "Bharat-series vehicle registration"))
            take(m.start(), m.end())
    # ---- PAN and IFSC
    for m in _IFSC.finditer(text):
        if _overlaps(taken, m.start(), m.end()):
            continue
        bank = m.group(1)
        ctx = text[max(0, m.start() - 30):m.start()].lower()
        if bank not in _IFSC_BANKS and "ifsc" not in ctx:
            continue
        tail = m.group(2)
        v = bank + "0" + tail
        items.append(_item("IFSC", text, m.start(), m.end(), v, cal("IFSC", "validated_pattern" if bank in _IFSC_BANKS else "pattern"), f"Bank branch code (IFSC): bank {bank}, 5th character 0, branch {tail}"))
        take(m.start(), m.end())
    for m in _PAN.finditer(text):
        if _overlaps(taken, m.start(), m.end()):
            continue
        a, b, c = m.groups()
        a2, b2, c2 = N.ocr_letters(a), N.ocr_digits(b), N.ocr_letters(c)
        if not (a2.isalpha() and b2.isdigit() and c2.isalpha()):
            continue
        ctx = text[max(0, m.start() - 25):m.start()].lower()
        ctx_pan = bool(re.search(r"\bpan\b|पैन|p\.a\.n", ctx))
        holder_ok = a2[3] in N.PAN_HOLDER_TYPES
        repaired = (a2, b2, c2) != (a, b, c)
        if not ctx_pan and not (holder_ok and not repaired):
            continue
        # PAN must not swallow something already recognised, and plain words of length 10 never pass the pattern
        v = a2 + b2 + c2
        items.append(_item("PAN", text, m.start(), m.end(), v, cal("PAN", "repaired" if repaired else "validated_pattern", delta=0.0 if holder_ok else -0.15),
                           "PAN (5 letters + 4 digits + 1 letter)" + ("" if holder_ok else "; 4th letter is not a valid holder type - verify") + ("; OCR-confusable characters were corrected" if repaired else ""),
                           (["ocr_repaired"] if repaired else []) + ([] if holder_ok else ["pan_format_doubtful"]), sensitive=True))
        take(m.start(), m.end())
    # ---- landline
    for m in _LANDLINE.finditer(text):
        if not _overlaps(taken, m.start(), m.end()):
            v = re.sub(r"\D", "", m.group(0))
            items.append(_item("LANDLINE", text, m.start(), m.end(), v, cal("LANDLINE", "pattern"), "Landline number (STD code + number)"))
            take(m.start(), m.end())
    # ---- short labelled account numbers (6-8 digits) that the long-run scanner below would ignore
    for m in re.finditer(r"(?:a\s*/\s*c|acct|account)(?:\s*(?:no\.?|number|num))?\s*[:\-#.]?\s*(?P<d>[0-9]{6,8})(?![0-9])", text, re.I):
        if not _overlaps(taken, m.start("d"), m.end("d")):
            items.append(_item("ACCOUNT", text, m.start("d"), m.end("d"), m.group("d"), cal("ACCOUNT", "labelled"), "Number written right after an account label (A/C, account)"))
            take(m.start("d"), m.end("d"))
    # ---- digit runs: phone / Aadhaar / account / other numbers
    for m in _RUN.finditer(text):
        s, e = m.start(), m.end()
        if _overlaps(taken, s, e):
            # a run partly inside a date/amount: only keep the free stretch if it is still long enough
            continue
        raw = m.group(0)
        groups = [(g.start() + s, g.end() + s, g.group(0)) for g in re.finditer(r"[0-9OoQIl|SBZG]+", raw)]
        real = sum(ch.isdigit() for ch in raw)
        if real < max(6, int(0.55 * len(re.sub(r"[ \-.+]", "", raw)))):
            continue
        before = text[max(0, s - 45):s]
        after = text[e:e + 25]
        used = False
        # phone windows first (handles "2026 9876543210" style neighbours)
        n = len(groups)
        for i0 in range(n):
            for i1 in range(n, i0, -1):
                sub = groups[i0:i1]
                digits = N.ocr_digits("".join(g[2] for g in sub))
                if not digits.isdigit() or len(digits) not in (10, 11, 12, 13, 14):
                    continue
                if _AADHAAR_CTX.search(before) and len(digits) == 12:
                    continue
                if _ACCT_CTX.search(before[-30:]) and len(digits) in (10, 12) and not re.search(r"mob|phone|contact|call", before[-15:], re.I):
                    continue
                ph = N.canon_phone(digits)
                if not ph or (len(digits) == 12 and not digits.startswith("91")):
                    continue
                if len(digits) == 12 and re.fullmatch(r"\d{4}\s\d{4}\s\d{4}", text[sub[0][0]:sub[-1][1]]) and N.verhoeff_ok(digits):
                    continue
                s0, e0 = sub[0][0], sub[-1][1]
                if text[s0 - 1:s0] == "+":
                    s0 -= 1
                repaired = any(ch.isalpha() for ch in "".join(g[2] for g in sub))
                labelled = bool(_PHONE_CTX.search(text[max(0, s0 - 30):s0]))
                base = "repaired" if repaired else ("labelled" if labelled else "pattern")
                reason = "Indian mobile number (10 digits starting 6-9" + (", country code/leading 0 removed" if len(digits) > 10 else "") + ")" + ("; written after a phone label" if labelled else "") + ("; OCR-confusable characters were corrected" if repaired else "")
                items.append(_item("PHONE", text, s0, e0, ph, cal("PHONE", base, delta=0.03 if labelled and not repaired else 0), reason, ["ocr_repaired"] if repaired else [], phone_written_as=text[s0:e0].strip()))
                take(s0, e0)
                used = True
                # remaining groups before/after may still contain another number; keep scanning simply
                break
            if used:
                break
        if used:
            continue
        digits = N.ocr_digits("".join(g[2] for g in groups))
        if not digits.isdigit():
            continue
        L_ = len(digits)
        repaired = any(ch.isalpha() for ch in raw)
        grouped = bool(re.fullmatch(r"\d{4}[ \-]\d{4}[ \-]\d{4}", raw.strip()))
        kind = None
        reason = ""
        base = "pattern"
        if L_ == 12 and _AADHAAR_CTX.search(before) and digits[0] in "23456789":
            kind, base, reason = "AADHAAR", "labelled", "12 digits written after the word Aadhaar"
        elif _ACCT_CTX.search(before[-32:]) and 6 <= L_ <= 18:
            kind, base, reason = "ACCOUNT", "labelled", "Number written after an account label (A/C, account, खाता ...)"
        elif _TXN_CTX.search(before[-32:]) and L_ >= 8:
            kind, base, reason = "TRANSACTION_ID", "labelled", "Number written after a transaction / reference label"
        elif L_ == 12 and digits[0] in "23456789" and N.verhoeff_ok(digits) and (grouped or _AADHAAR_CTX.search(before + after)):
            kind, base, reason = "AADHAAR", "validated_pattern", "12 digits in 4-4-4 groups that pass the Aadhaar (Verhoeff) check digit"
        elif L_ == 12 and digits[0] in "23456789" and N.verhoeff_ok(digits):
            kind, base, reason = "AADHAAR", "inferred", "12 digits that pass the Aadhaar check digit but no 'Aadhaar' label was found; could also be an account or reference number"
        elif L_ == 15 and re.search(r"imei", before, re.I):
            kind, base, reason = "IMEI", "labelled", "15 digits after the word IMEI"
        elif L_ == 16 and re.search(r"card|कार्ड", before, re.I):
            kind, base, reason = "CARD", "labelled", "16 digits after the word card"
        elif 11 <= L_ <= 18 and not digits.startswith("0091"):
            kind, base, reason = "ACCOUNT", "inferred", "Long number with no label; treated as a possible bank account number - please confirm"
        if not kind:
            continue
        conf_base = "repaired" if repaired and base not in ("inferred",) else base
        conf = cal(kind, conf_base) if base != "inferred" else cal(kind, "inferred", delta=-0.05)
        extra: dict[str, Any] = {"sensitive": kind in ("AADHAAR", "CARD")}
        flags = ["ocr_repaired"] if repaired else []
        if kind == "AADHAAR":
            extra["checksum_ok"] = N.verhoeff_ok(digits)
            if not extra["checksum_ok"]:
                flags.append("checksum_failed")
                conf = round(conf - 0.12, 3)
                reason += "; check digit does not match - possible typing/OCR error"
        items.append(_item(kind, text, s, e, digits, conf, reason + ("; OCR-confusable characters were corrected" if repaired else ""), flags, **extra))
        take(s, e)
    # ---- organisations written as M/s ...
    for m in _ORG.finditer(text):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" .")
        items.append(_item("ORGANIZATION", text, m.start(1), m.end(1), name, cal("ORG", "cue", delta=-0.05), "Business name written after 'M/s'"))
    items.sort(key=lambda x: x["start"])
    return items


# ---------------------------------------------------------------------------------------------------- people
_TITLE_RX = re.compile(rf"(?:{LB.TITLES})\.?\s+", re.I)
_RANK_RX = re.compile(rf"(?:{LB.RANKS})\.?(?=\s|:)\s*", re.I)
_DESC_RX = re.compile(rf"(?:{LB.DESCRIPTORS})(?=[\s:\-]|$)\s*[:\-–]?\s*", re.I)
_LINKER_RX = re.compile(r"(?:used|using|is|was|are|were|named|called|namely|identified\s+as|introduced\s+(?:himself|herself)\s+as)\s+(?:the\s+name\s+)?", re.I)
_LAT_TOK = re.compile(r"(?:[A-Z][A-Za-z'’\-]*\.?|[A-Z]\.)")
_DEV_TOK = re.compile(r"[\u0900-\u0963\u0966-\u097F]+")
_ALIAS_RX = re.compile(r"\s*(?:alias|urf|a\.?k\.?a\.?|उर्फ़?|उर्फ)\s+", re.I)
_KIN_RX = re.compile(rf"(?P<kin>{LB.KIN_CUES})\s*[:\-–]?\s*", re.I)
_LIST_SEP = re.compile(r"\s*(?:,|;|&|\band\b|तथा|एवं|\bऔर\b|\bव\b|आणि|तसेच|\bwith\b)\s*", re.I)
_LATIN_STOP = LB.LATIN_NON_NAMES
_NAME_STOP_AFTER = re.compile(r"^(?:\s*\(|\s*,\s*(?:r/o|resident|aged|age|s/o|w/o|d/o)|\s+(?:r/o|resident|aged|age|s/o|w/o|d/o|c/o))", re.I)


def _skip_prefix(text: str, i: int, allow_desc: bool = True) -> int:
    """Skip separators, titles, ranks and descriptor words that sit between a cue and a name."""
    n = len(text)
    while i < n:
        j = i
        m = re.match(r"[\s:\-–—•/]+", text[i:])
        if m:
            i += m.end()
        m = _TITLE_RX.match(text, i)
        if m:
            i = m.end()
        m = _RANK_RX.match(text, i)
        if m and m.end() > i:
            i = m.end()
        if allow_desc:
            m = _DESC_RX.match(text, i)
            if m and m.end() > i:
                i = m.end()
        # Devanagari titles / ranks are single tokens
        dm = _DEV_TOK.match(text, i)
        if dm and dm.group(0) in LB.DEV_TITLES:
            i = dm.end()
            continue
        if i == j:
            break
    return i


def _read_latin(text: str, i: int, allow_caps: bool) -> tuple[str, int] | None:
    toks = []
    j = i
    while len(toks) < 4:
        m = _LAT_TOK.match(text, j)
        if not m:
            break
        tok = m.group(0)
        bare = tok.rstrip(".").casefold()
        if bare in _LATIN_STOP or bare in LB.LATIN_ORG_WORDS:
            break
        if len(tok.rstrip(".")) >= 2 and tok.rstrip(".").isupper() and not allow_caps:
            break
        if bare in ("alias", "urf"):
            break
        toks.append(tok)
        j = m.end()
        if tok.endswith(".") and len(bare) > 1 and bare not in ("mohd", "md", "dr", "mr", "mrs", "ms", "sh", "smt", "sri", "st", "kr", "ch"):
            toks[-1] = tok.rstrip(".")
            j -= 1
            break
        m2 = re.match(r"[ \t]+", text[j:])
        if not m2:
            break
        j2 = j + m2.end()
        # lowercase particles inside names
        pm = re.match(r"(?:bin|bint|ben|de|van|al|ul|ur|e|ka|ki)\s+(?=[A-Z])", text[j2:])
        if pm and bare not in ("ka", "ki") and False:
            j = j2 + pm.end()
        else:
            j = j2
        # roll back trailing whitespace if next token is not a name token
        if not _LAT_TOK.match(text, j):
            j = j2 - len(m2.group(0)) if False else j - len(m2.group(0))
            break
    if not toks:
        return None
    end = i
    # recompute exact end offset
    pos = i
    for t in toks:
        pos = text.index(t, pos) + len(t)
    name = " ".join(t.rstrip(",") for t in toks)
    if allow_caps and name.isupper():
        name = name.title()
    name = name.strip()
    return name, pos


def _read_dev(text: str, i: int) -> tuple[str, int] | None:
    toks = []
    j = i
    ends = []
    while len(toks) < 4:
        m = _DEV_TOK.match(text, j)
        if not m:
            break
        tok = m.group(0)
        if tok in LB.DEV_STOP or tok in LB.DEV_TITLES:
            break
        toks.append(tok)
        ends.append(m.end())
        m2 = re.match(r"[ \t]+", text[m.end():])
        if not m2:
            break
        j = m.end() + m2.end()
    if not toks:
        return None
    # prefer to end the name at the last known surname when several tokens were read
    if len(toks) >= 3:
        last_sn = max((k for k, t in enumerate(toks) if t in LB.DEV_SURNAMES), default=-1)
        if last_sn >= 1:
            toks, ends = toks[: last_sn + 1], ends[: last_sn + 1]
        else:
            toks, ends = toks[:2], ends[:2]
    return " ".join(toks), ends[len(toks) - 1]


def _read_other_script(text: str, i: int) -> tuple[str, int] | None:
    m = re.match(r"[\u0900-\u0963\u0966-\u0DFF\u0600-\u06FF](?:[\u0900-\u0963\u0966-\u0DFF\u0600-\u06FF]| ){1,40}", text[i:])
    if not m:
        return None
    val = re.split(r"[,;:\n(0-9]|\s{2,}", m.group(0))[0].strip()
    words = []
    consumed = 0
    for w in val.split():
        if w in LB.OTHER_STOP:
            break
        if not words and w in LB.OTHER_TITLES:
            consumed += 1
            continue
        words.append(w)
    words = words[:3]
    name = " ".join(words)
    if len(name) < 2:
        return None
    end = i + val.find(words[-1]) + len(words[-1])
    return name, end


def read_person(text: str, i: int, allow_caps: bool = False) -> dict[str, Any] | None:
    """Read one person name (with optional alias and 's/o' relative) that starts at/after ``i``."""
    i = _skip_prefix(text, i)
    if i >= len(text):
        return None
    ch = text[i]
    res = None
    script = "latin"
    if "\u0900" <= ch <= "\u097F":
        res, script = _read_dev(text, i), "devanagari"
    elif ch.isascii() and ch.isupper():
        res = _read_latin(text, i, allow_caps)
    elif (ord(ch) > 0x0980 or 0x0600 <= ord(ch) <= 0x06FF) and ch.isalpha():
        res, script = _read_other_script(text, i), "other"
    if not res:
        return None
    name, end = res
    person: dict[str, Any] = {"name": name, "start": text.index(name.split()[0], i) if name.split()[0] in text[i:end + 1] else i, "end": end, "script": script, "alias": "", "kin": []}
    person["start"] = i
    # alias
    am = _ALIAS_RX.match(text, end)
    if am:
        r2 = _read_latin(text, am.end(), True) if text[am.end():am.end() + 1].isascii() else _read_dev(text, am.end())
        if r2:
            person["alias"], person["end"] = r2[0], r2[1]
            end = r2[1]
    # kin: "s/o Late Harbans Singh", "पुत्र सुरेश कुमार"
    km = re.match(r"\s*,?\s*", text[end:])
    k0 = end + (km.end() if km else 0)
    kmm = _KIN_RX.match(text, k0)
    if kmm:
        rel = re.sub(r"\s+", "", kmm.group("kin")).lower()
        kp = read_person(text, kmm.end(), allow_caps) if not re.match(r"\s*(?:r/o|resident)", text[kmm.end():kmm.end() + 10], re.I) else None
        if kp:
            person["kin"].append({"relation": rel, "person": kp})
            person["end"] = max(person["end"], end)
    return person


_HARM = LB.HARM_WORDS


def _roles_from_cues(text: str, sents: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """All (person, role) mentions found through label / cue words. Later merged by name."""
    out: list[dict[str, Any]] = []

    def emit(p, role, base, reason, s0=None, e0=None, rel_of=None):
        out.append({"name": p["name"], "alias": p.get("alias", ""), "role": role, "base": base, "reason": reason, "start": p["start"], "end": p["end"], "script": p["script"], "rel_of": rel_of})

    cue_rx = {role: re.compile(rf"(?<![A-Za-z\u0900-\u0DFF])(?:{alt})(?![A-Za-z\u0900-\u0DFF])", re.I) for role, alt in LB.CUES.items()}
    for role, rx in cue_rx.items():
        for m in rx.finditer(text):
            # 'IO' / 'SI' style abbreviations must be followed by a separator to count as a label
            after0 = text[m.end():m.end() + 3]
            labelled = bool(re.match(r"\s*[:\-–—]", text[m.end():m.end() + 6])) or after0.startswith("\n") or bool(re.match(r"\s*/\s*[A-Za-z]", text[m.end():m.end() + 4]))
            if role == "INVESTIGATING_OFFICER" and re.fullmatch(r"I\.?O\.?", m.group(0), re.I) and not labelled and not re.match(r"\s+(?:ASI|SI|PSI|Inspector|Sub|HC|PI|DSP)", text[m.end():m.end() + 12], re.I):
                continue
            j = m.end()
            # allow "/ Informant" tails and one line break followed by "- Name:" style
            j = _skip_prefix(text, j)
            lk = _LINKER_RX.match(text, j)
            if lk:
                j = _skip_prefix(text, lk.end())
            nm = re.match(r"(?:Name|Naam|नाम|नाव)\s*[:\-–]\s*", text[j:], re.I)
            if nm:
                j += nm.end()
            allow_caps = labelled or text[max(0, m.start() - 1):m.start()] in ("\n", "-", "•") or role in ("COMPLAINANT", "INVESTIGATING_OFFICER")
            p = read_person(text, j, allow_caps=labelled or role in ("INVESTIGATING_OFFICER",))
            if not p:
                continue
            # the name must start on the same line as the cue (or the next non-empty line for labelled cues)
            gap = text[m.end():p["start"]]
            if gap.count("\n") > 1 or (gap.count("\n") == 1 and not labelled):
                continue
            base = "labelled" if labelled else "cue"
            label_txt = m.group(0).strip()
            reason = f"Name follows the {'label' if labelled else 'word'} \"{label_txt}\" (line {line_of(text, m.start())})"
            emit(p, role, base, reason)
            for k in p["kin"]:
                emit(k["person"], "RELATIVE", base, f"Named as {k['relation'].upper()} of {p['name']} (\"{k['relation']}\")", rel_of=p["name"])
            # continuation of the list: "A and B", "A; B" within the same line
            line_end = text.find("\n", p["end"])
            line_end = len(text) if line_end < 0 else line_end
            k_end = p["end"]
            guard = 0
            tail_end = max([k["person"]["end"] for k in p["kin"]] + [p["end"]])
            cur = tail_end
            while guard < 6:
                guard += 1
                sm = _LIST_SEP.match(text, cur)
                if not sm:
                    # tolerate ", r/o ..." address fragments: skip to the next ';'
                    sc = text.find(";", cur, line_end)
                    if labelled and sc >= 0:
                        cur = sc
                        continue
                    break
                nxt = sm.end()
                # "and his associates X and Y"
                am2 = re.match(r"(?:his|her|their)\s+(?:associates?|accomplices?|aides?|companions?|friends?|साथियों|साथी|सहयोगी(?:यों)?)\s+", text[nxt:], re.I)
                if am2:
                    nxt += am2.end()
                p2 = read_person(text, nxt, allow_caps=labelled)
                if not p2 or (text.count("\n", cur, p2["start"]) > 0):
                    if labelled:
                        sc = text.find(";", cur + 1, line_end)
                        if sc >= 0:
                            cur = sc
                            continue
                    break
                if role in ("ACCUSED", "WITNESS", "VICTIM") or (role == "COMPLAINANT" and False):
                    emit(p2, role, base if labelled else "cue", reason.replace("Name follows", "Listed together with the name after") if False else f"Listed in the same {'line' if labelled else 'sentence'} after \"{label_txt}\"", )
                    for k in p2["kin"]:
                        emit(k["person"], "RELATIVE", "cue", f"Named as {k['relation'].upper()} of {p2['name']}", rel_of=p2["name"])
                    cur = max([k["person"]["end"] for k in p2["kin"]] + [p2["end"]])
                else:
                    break
    # ---- first-person complainant: "I, Priya Nair", "I am Kiran Bedi", "Main Amit Shah, ...", "मैं ... "
    for m in re.finditer(r"(?<![A-Za-z])(?:I\s*,\s*|I\s+am\s+|(?i:main|mai)\s+|मैं\s+|मी\s+)(?=(?:Mr\.?|Mrs\.?|Ms\.?|Shri|Smt|Sri)?\s*[A-Z\u0900-\u097F])", text):
        p = read_person(text, m.end(), allow_caps=False)
        if not p or len(p["name"].split()) < 2 and p["script"] == "latin":
            continue
        tail = text[p["end"]:p["end"] + 25]
        if p["script"] == "latin" and not re.match(r"\s*(?:,|\(|\s+(?:s/o|d/o|w/o|aged|age|r/o|resident|pita|son|daughter|wife|of\b))", tail, re.I):
            continue
        if p["name"].split()[0].casefold() in ("market", "road", "bazar", "bazaar", "street", "gate", "chowk", "line"):
            continue
        emit(p, "COMPLAINANT", "first_person", "Written in the first person (\"" + text[m.start():m.end()].strip() + " ...\") - the person making the complaint")
        for k in p["kin"]:
            emit(k["person"], "RELATIVE", "cue", f"Named as {k['relation'].upper()} of {p['name']}", rel_of=p["name"])
    # ---- "pita ka naam X", "father X" are covered by KIN_CUES when they follow a name; also stand-alone kin labels
    for m in re.finditer(rf"(?<![A-Za-z\u0900-\u097F])(?P<kin>{LB.KIN_CUES})\s*(?:[:\-–]|ka\s+naam|का\s+नाम)?\s*", text, re.I):
        rel = re.sub(r"\s+", "", m.group("kin")).lower()
        p = read_person(text, m.end())
        if not p or text.count("\n", m.end(), p["start"]) > 0:
            continue
        # only when the label stands on its own (start of line / after comma), otherwise the name-tail logic above handled it
        before = text[max(0, m.start() - 2):m.start()]
        if re.search(r"[A-Za-z\u0900-\u097F]\s?$", before) and not re.search(r"[,;\n]\s*$", before):
            pass
        emit(p, "RELATIVE", "labelled" if re.match(r"\s*[:\-–]", text[m.end() - 2:m.end() + 1]) else "cue", f"Named after the kinship word \"{m.group('kin').strip()}\" (line {line_of(text, m.start())})", rel_of="")
    # ---- kin of a person mentioned earlier: "my daughter Ritu Dubey", "meri beti Pooja"
    kin_words = r"(?:younger\s+|elder\s+|little\s+|minor\s+)?(?:daughter|son|brother|sister|wife|husband|mother|father|beti|beta|bhai|behan|bahen|patni|papa|mummy|बेटी|बेटा|बेटे|भाई|बहन|पुत्री|पुत्र|पत्नी|मुलगी|मुलगा|बहीण|भाऊ)"
    for m in re.finditer(rf"(?<![A-Za-z\u0900-\u097F])(?:my|his|her|our|meri|mera|mere|hamari|hamara|मेरी|मेरा|मेरे|हमारी|माझी|माझा|माझे)\s+{kin_words}\s+(?:named\s+|ka\s+naam\s+|का\s+नाम\s+)?", text, re.I):
        p = read_person(text, m.end())
        if not p or text.count("\n", m.end(), p["start"]) > 0:
            continue
        si = _sent_index(sents, m.start())
        sent_txt = text[sents[si][0]:sents[si][1]] + " " + (text[sents[si + 1][0]:sents[si + 1][1]] if si + 1 < len(sents) else "")
        role = "VICTIM" if _HARM.search(sent_txt) else "RELATIVE"
        emit(p, role, "cue" if role == "VICTIM" else "weak_cue", ("Family member named by the complainant and the sentence describes harm/loss (" + _HARM.search(sent_txt).group(0) + ")") if role == "VICTIM" else "Family member named by the complainant", rel_of="")
    # ---- "was beaten ... by Gaurav Bhati and his associates Lalit and Pintu": the agent of a harm verb is the accused
    for m in re.finditer(r"\b(?:beaten|attacked|assaulted|stabbed|shot|robbed|threatened|abducted|kidnapped|cheated|duped|hit|injured|harassed|molested|looted|snatched|defrauded|blackmailed|taken|stolen|misappropriated|borrowed|forged|misused|withheld)\b[^.\n]{0,90}?\bby\s+(?:(?:her|his|my|our|their)\s+(?:ex-?\s*)?[A-Za-z\-]+\s+)?(?=(?:Mr\.?\s+|Shri\s+)?[A-Z])", text):
        p = read_person(text, m.end(), allow_caps=False)
        if not p:
            continue
        emit(p, "ACCUSED", "cue", f"Named as the person who committed the act (\"{_clip(text[m.start():m.end()], 40)}\" ... by NAME)")
        cur = p["end"]
        for _ in range(5):
            sm = _LIST_SEP.match(text, cur)
            if not sm:
                break
            nxt = sm.end()
            am2 = re.match(r"(?:his|her|their)\s+(?:associates?|accomplices?|aides?|companions?|friends?)\s+", text[nxt:], re.I)
            if am2:
                nxt += am2.end()
            p2 = read_person(text, nxt, allow_caps=False)
            if not p2 or text.count("\n", cur, p2["start"]) > 0 or re.search(r"[.;]", text[cur:p2["start"]]):
                break
            emit(p2, "ACCUSED", "cue", "Listed together with the person who committed the act (\"by NAME and ... NAME\")")
            cur = p2["end"]
    # ---- "Executive Engineer Sudhir Agarwal demanded Rs 3,00,000 as bribe": the subject of a wrongdoing verb is a suspect
    for m in re.finditer(r"((?:[A-Z][A-Za-z.'\-]+\s+){0,3}[A-Z][A-Za-z'\-]+)\s+(?:demanded|threatened|extorted|cheated|duped|defrauded|assaulted|abused|harassed|blackmailed|stole|snatched|robbed|abducted|kidnapped|forged|misappropriated|refused\s+to\s+return|attacked|slapped|stabbed)\b", text):
        p = read_person(text, m.start(1), allow_caps=False)
        if not p or p["script"] != "latin" or not re.fullmatch(r"\s*", text[p["end"]:m.end(1)]):
            continue
        if p["name"].split()[0].casefold() in _LATIN_STOP:
            continue
        emit(p, "ACCUSED", "weak_cue", f"Subject of a wrongdoing verb (\"{_clip(text[m.start(1):m.end()], 60)}\")")
    # ---- Hinglish: "Shak hai ki Kamal Rajput ne ..." = the complainant suspects that person
    for m in re.finditer(r"(?<![A-Za-z])(?:shak|sandeh|shubha)\s+(?:hai\s+)?ki\s+|(?:शक|संदेह)\s+(?:है\s+)?कि\s+", text, re.I):
        p = read_person(text, m.end(), allow_caps=False)
        if p and text.count("\n", m.end(), p["start"]) == 0:
            emit(p, "ACCUSED", "weak_cue", "Named right after \"shak hai ki\" (the complainant suspects this person)")
    # ---- explicit "(victim)" / "(accused)" tags after a name
    for m in re.finditer(r"\(\s*(victim|accused|witness|complainant|injured)\s*\)", text, re.I):
        j = m.start()
        # walk back to the name
        seg = text[max(0, j - 50):j].rstrip()
        mm = re.search(r"((?:[A-Z][A-Za-z'’\-]*\.?\s+){0,3}[A-Z][A-Za-z'’\-]*)$", seg)
        if mm:
            nm_ = mm.group(1).strip()
            if nm_.split()[0].casefold() not in _LATIN_STOP:
                s0 = max(0, j - 50) + mm.start(1)
                emit({"name": nm_, "start": s0, "end": s0 + len(nm_), "script": "latin"}, {"victim": "VICTIM", "injured": "VICTIM", "accused": "ACCUSED", "witness": "WITNESS", "complainant": "COMPLAINANT"}[m.group(1).lower()], "labelled", f"Tagged \"({m.group(1)})\" right after the name")
    # ---- witness / accused by verb
    for m in re.finditer(r"(?<![A-Za-z])((?:Mr\.?\s+|Mrs\.?\s+|Ms\.?\s+|Shri\s+|Smt\.?\s+)?[A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+){0,2})\s+(?:saw|noticed|witnessed|observed|identified|watched|heard)\b", text):
        nm_ = m.group(1)
        first = nm_.split()[0].casefold().rstrip(".")
        if first in _LATIN_STOP and first not in ("mr", "mrs", "ms", "shri", "smt"):
            continue
        p = read_person(text, m.start(1))
        if p:
            emit(p, "WITNESS", "weak_cue", "Person who saw / noticed something (verb \"saw/noticed\" right after the name)")
    for m in re.finditer(r"(?:^|[\s(])((?:Gaon\s+ke\s+|Village\s+|Padosi\s+|Neighbou?r\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+ne\s+(?:[^.\n]{0,70}?)\b(?:dekha|dekhi|dekhe|pehchana|pahchana)\b", text):
        nm_ = re.sub(r"^(?:Gaon\s+ke|Village|Padosi|Neighbou?r)\s+", "", m.group(1))
        p = read_person(text, m.start(1) + (len(m.group(1)) - len(nm_)))
        if p:
            emit(p, "WITNESS", "weak_cue", "Person who saw something (\"... ne ... dekha\" = saw)")
    for m in re.finditer(r"([\u0900-\u097F]+(?:\s+[\u0900-\u097F]+){0,2})\s+ने\s+(?:[^.\n।]{0,50}?)(?:देखा|देखी|देखे|पहचाना)", text):
        p = read_person(text, m.start(1))
        if p and p["end"] <= m.end(1):
            emit(p, "WITNESS", "weak_cue", "Person who saw something (\"... ने ... देखा\" = saw)")
    for m in re.finditer(r"(?:^|[\s(])([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+par\s+(?:shak|sandeh)\b", text):
        p = read_person(text, m.start(1))
        if p:
            emit(p, "ACCUSED", "weak_cue", "Named as a person the complainant suspects (\"... par shak\")")
    for m in re.finditer(r"(?:ladke|ladki|vyakti|aadmi|shakhs|yuvak|youth|man|boy|person)\s+(?:ka|ki|ke)\s+(?:naam|nam)\s+", text, re.I):
        p = read_person(text, m.end())
        if p and text.count("\n", m.end(), p["start"]) == 0:
            emit(p, "ACCUSED", "weak_cue", "Named as one of the culprits (\"ladke ka naam ...\")")
    for m in re.finditer(r"(?:known|addressed|called|referred\s+to|named)\s+(?:as|by\s+the\s+name)\s+[\"'“‘]?", text, re.I):
        p = read_person(text, m.end())
        if not p:
            continue
        si = _sent_index(sents, m.start())
        sent_txt = text[sents[si][0]:sents[si][1]]
        if re.search(r"miscreant|accused|culprit|robber|thief|assailant|suspect|offender", sent_txt, re.I):
            emit(p, "ACCUSED", "cue", "Nickname used for one of the accused (\"addressed as ...\")")
    for m in re.finditer(r"(?:my|his|her|our)\s+neighbou?r\s+(?:Mr\.?\s+|Mrs\.?\s+|Ms\.?\s+|Shri\s+)?(?=[A-Z])|(?:पड़ोसी|पडोसी|शेजारी)\s+|(?:friend|neighbou?r|colleague)\s+(?=[A-Z][a-z]+\s+[A-Z][a-z]+\s+(?:can|could|will)\s+(?:confirm|testify|verify))|Padosi\s+", text, re.I):
        p = read_person(text, m.end())
        if p and text.count("\n", m.end(), p["start"]) == 0:
            si = _sent_index(sents, m.start())
            st = text[sents[si][0]:sents[si][1]]
            if re.search(r"shak|sandeh|suspect|accused", st, re.I):
                continue
            emit(p, "WITNESS", "weak_cue", "Neighbour / friend named as someone who saw or can confirm the facts")
    # ---- police officer ranks followed by a name when no label exists ("Sub Inspector Om Prakash jaanch karenge")
    for m in re.finditer(rf"(?<![A-Za-z\u0900-\u097F])(?:{LB.RANKS})\.?\s+(?=[A-Z\u0900-\u097F])", text):
        p = read_person(text, m.end(), allow_caps=False)
        if not p:
            continue
        tail = text[p["end"]:p["end"] + 30]
        if re.match(r"\s+(?:jaanch|janch|will\s+investigate|karenge|investigat)", tail, re.I):
            emit(p, "INVESTIGATING_OFFICER", "cue", "Police officer named as the one who will investigate")
        else:
            emit(p, "POLICE_OFFICER", "weak_cue", "Police rank/title written before the name")
    # ---- titled names without any other cue
    for m in re.finditer(r"(?<![A-Za-z])(?:Mr|Mrs|Ms|Miss|Shri|Smt|Sri|Sh)\.?\s+(?=[A-Z])|(?:श्री|श्रीमती|सुश्री|कुमारी|श्रीमान)\s+(?=[\u0900-\u097F])", text):
        p = read_person(text, m.start())
        if p and p["start"] >= m.start():
            emit(p, "PERSON", "unknown_role", "Name written with a title (Mr./Mrs./Shri/Smt.) but no role word nearby")
    return out


_ROLE_PRIORITY = {"COMPLAINANT": 9, "INVESTIGATING_OFFICER": 8, "POLICE_OFFICER": 7, "ACCUSED": 6, "VICTIM": 5, "WITNESS": 4, "RELATIVE": 3, "PERSON": 1}


def _same_person(a: str, b: str) -> bool:
    ka, kb = N.name_key(a).split(), N.name_key(b).split()
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if len(ka) == 1 and len(kb) > 1 and ka[0] == kb[0]:
        return True
    if len(kb) == 1 and len(ka) > 1 and kb[0] == ka[0]:
        return True
    return False


def extract_people(text: str, sents: list[tuple[int, int]]) -> list[dict[str, Any]]:
    mentions = _roles_from_cues(text, sents)
    people: list[dict[str, Any]] = []
    # process stronger mentions first so single-token mentions merge into the fuller name
    for mt in sorted(mentions, key=lambda x: (-len(x["name"].split()), x["start"])):
        target = next((p for p in people if _same_person(p["name"], mt["name"]) and p["script"] == mt["script"]), None)
        if target is None:
            people.append({"name": mt["name"], "alias": mt.get("alias", ""), "script": mt["script"], "mentions": [], "roles": {}})
            target = people[-1]
        target["mentions"].append(mt)
        if mt.get("alias") and not target["alias"]:
            target["alias"] = mt["alias"]
        cur = target["roles"].get(mt["role"])
        if cur is None or CALIBRATION.get(mt["base"], 0) > CALIBRATION.get(cur["base"], 0):
            target["roles"][mt["role"]] = mt
    out = []
    for p in people:
        roles = sorted(p["roles"], key=lambda r: -_ROLE_PRIORITY.get(r, 0))
        # a person tagged both PERSON (title only) and something else keeps the specific role
        specific = [r for r in roles if r != "PERSON"]
        primary = specific[0] if specific else "PERSON"
        m0 = p["roles"][primary]
        first = min(p["mentions"], key=lambda x: x["start"])
        base = m0["base"]
        conf = cal("PERSON", base)
        if len(p["mentions"]) > 1:
            conf = min(0.97, conf + 0.03)
        if p["script"] == "devanagari":
            conf = max(0.3, conf - 0.04)
        n_tokens = len(p["name"].split())
        if n_tokens == 1 and p["script"] == "latin":
            conf = max(0.3, conf - 0.10)
        s, e = m0["start"], m0["end"]
        # trim trailing words that got attached to the span (alias etc.)
        out.append(_item("PERSON", text, s, e, p["name"], conf, m0["reason"], [] if primary != "PERSON" else ["role_unknown"],
                         role=primary, roles=specific or ["PERSON"], alias=p["alias"], script=p["script"], mention_spans=[[x["start"], x["end"]] for x in p["mentions"]][:8], mention_spans_full=[(x["start"], x["base"]) for x in p["mentions"]],
                         kin_of=next((x["rel_of"] for x in p["mentions"] if x.get("rel_of")), "")))
        out[-1]["surface"] = p["name"]
    _add_coreference(text, out)
    return sorted(out, key=lambda x: x["start"])


def _add_coreference(text: str, people: list[dict[str, Any]]) -> None:
    """Every later occurrence of a full name (or of a first name no one else shares) is another mention of the same person.
    Used only to resolve 'the accused' / 'his' / 'uska' to the person being talked about."""
    firsts: dict[str, int] = {}
    for p in people:
        for t in {w.casefold() for w in p["normalized"].split()}:
            firsts[t] = firsts.get(t, 0) + 1
    for p in people:
        spans = [tuple(x) for x in p.get("mention_spans", [])]
        pats = [re.escape(p["normalized"]).replace(r"\ ", r"\s+")]
        toks = p["normalized"].split()
        if p["script"] == "latin" and len(toks) >= 2 and len(toks[0]) >= 4 and firsts.get(toks[0].casefold(), 0) == 1:
            pats.append(re.escape(toks[0]))
        if p.get("alias") and len(p["alias"]) >= 4:
            pats.append(re.escape(p["alias"]))
        allm = set(spans)
        for pat in pats:
            for m in re.finditer(rf"(?<![A-Za-z\u0900-\u0DFF]){pat}(?![A-Za-z\u0900-\u0DFF])", text):
                if not any(a <= m.start() < b for a, b in allm):
                    allm.add((m.start(), m.end()))
        p["all_mentions"] = sorted(allm)


def _all_mention_starts(p: dict) -> list[int]:
    return [a for a, _ in (p.get("all_mentions") or p.get("mention_spans") or [[p["start"], p["end"]]])]


# ---------------------------------------------------------------------------------------------------- relationships
_OWN_WORDS = r"(?:mobile|mob|phone|number|no\.?|contact|cell|num|nambar|नंबर|फोन|मोबाइल|UPI(?:\s+ID)?|PhonePe|GPay|Google\s*Pay|Paytm|account|a/c|खाता|email|e-mail|id)"
_VEH_VERBS = r"(?:fled|fleeing|flee|riding|rode|ride|driving|drove|escaped|fled\s+in|on|in|using|used|by|sawar|savar|bhaga|bhaag|bhag|bhagte|par|से|पर|में|वरून|वर|चालवत)"


def _mentions_before(p: dict, pos: int) -> list[int]:
    return [a for a in _all_mention_starts(p) if a < pos]


def _ref_person(people: list[dict], text: str, pos: int, want: tuple[str, ...] = ("ACCUSED",), fallback_any: bool = True) -> dict | None:
    """Person most recently mentioned before ``pos`` (used to resolve 'the accused', 'his', 'uska')."""
    def latest(cands):
        best = None
        for p in cands:
            ms = _mentions_before(p, pos)
            if ms and (best is None or max(ms) > best[0]):
                best = (max(ms), p)
        return best[1] if best else None
    cands = [p for p in people if p.get("role") in want]
    if len(cands) > 1 and "ACCUSED" in want:
        # several accused: "the accused" most likely means the principal one - the person mentioned most often before this point
        scored = [(len(_mentions_before(p, pos)), max(_mentions_before(p, pos), default=-1), p) for p in cands if _mentions_before(p, pos)]
        if scored:
            top = max(scored, key=lambda x: (x[0], x[1]))
            return top[2]
    hit = latest(cands)
    if hit or not fallback_any:
        return hit
    return latest([p for p in people if p.get("role") not in ("INVESTIGATING_OFFICER", "POLICE_OFFICER", "RELATIVE")])


def _block_owner(people: list[dict], text: str, pos: int, sents: list[tuple[int, int]] | None = None, max_lines: int = 12) -> dict | None:
    """Owner of a phone/e-mail written with a contact label.

    Two layouts are accepted: (a) a form-style line "Mobile: 98..." where the party block (complainant / accused details) that
    starts with a labelled name is a few lines above; (b) a labelled or first-person name in the same sentence as the number
    ("Informant: Mr. X, contact 98..."). A name in a *different* sentence never owns the number (it may belong to the caller)."""
    line_start = text.rfind("\n", 0, pos) + 1
    form_line = bool(re.match(r"^[ \t\-•*0-9.]*[^\n:]{0,25}[:\-][ \t]*$", text[line_start:pos]))
    s_lo, s_hi = (0, len(text))
    if sents:
        si = _sent_index(sents, pos)
        s_lo, s_hi = sents[si]
    best = None
    for p in people:
        if p.get("role") not in ("COMPLAINANT", "ACCUSED", "WITNESS", "VICTIM"):
            continue
        for mn in p.get("mention_spans_full", []):
            a, base = mn
            if a >= pos or base not in ("labelled", "first_person"):
                continue
            if form_line:
                ok = text.count("\n", a, pos) <= max_lines
            else:
                a_line = text.rfind("\n", 0, a) + 1
                # same sentence, or a labelled name that opens the very line the number is on ("Shikayatkarta: X ... Delhi. Mob 98...")
                ok = s_lo <= a < s_hi or (base == "labelled" and a_line == line_start and a - a_line <= 45)
            if ok and (best is None or a > best[0]):
                best = (a, p)
    return best[1] if best else None


def extract_relations(text: str, sents: list[tuple[int, int]], people: list[dict], idents: list[dict]) -> list[dict[str, Any]]:
    rels: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def add(src, rel, tgt, conf, s, e, why):
        if not src or not tgt:
            return
        key = (src["normalized"], rel, tgt["normalized"])
        if key in seen or src["normalized"] == tgt["normalized"]:
            return
        seen.add(key)
        rels.append({"source": src["normalized"], "target": tgt["normalized"], "relation": rel, "confidence": round(conf, 3),
                     "evidence": snippet(text, s, e, 30), "evidence_span": [s, e], "rationale": why,
                     "source_kind": src["kind"], "target_kind": tgt["kind"], "source_surface": src.get("surface", ""), "target_surface": tgt.get("surface", "")})

    for it in idents:
        kind = it["kind"]
        if kind not in ("PHONE", "UPI", "ACCOUNT", "EMAIL", "VEHICLE"):
            continue
        si = _sent_index(sents, it["start"])
        s0, e0 = sents[si]
        # window before the identifier inside the same sentence/line
        pre = text[max(s0, it["start"] - 90):it["start"]]
        owner = None
        why = ""
        base = "weak_cue"
        # (1) a person mentioned shortly before the identifier with only ownership words in between
        cand_mentions = []
        for p_ in people:
            for a_, b_ in (p_.get("all_mentions") or [[p_["start"], p_["end"]]]):
                if b_ <= it["start"] and a_ >= max(s0, it["start"] - 90):
                    cand_mentions.append((b_, a_, p_))
        for b_end, a_start, p in sorted(cand_mentions, key=lambda x: -x[0]):
            between = text[b_end:it["start"]]
            if between.count("\n") > 0 or p.get("role") == "RELATIVE":
                continue
            if kind == "VEHICLE":
                if re.search(rf"{_VEH_VERBS}|car|bike|scooter|scooty|motorcycle|vehicle|bullet|activa|swift|pulsar|splendor|scorpio|tractor|number|no\.?|gaadi|gadi|paas|के\s+पास|ke\s+paas", between, re.I) and len(between) < 85 and not re.search(r"[.।;]", between):
                    if p.get("role") in ("WITNESS", "COMPLAINANT", "INVESTIGATING_OFFICER", "POLICE_OFFICER", "RELATIVE") and not re.search(r"\bmy\b|meri|mera|मेरी|मेरा", between, re.I):
                        continue
                    owner, why, base = p, f"{p['surface']} appears right before the vehicle number in the same sentence (\"{_clip(between, 50)}\")", "weak_cue"
                    break
            else:
                if re.search(_OWN_WORDS, between, re.I) and len(between) < 70 and not re.search(r"[.।;]", between) and not re.search(r"\b(?:called|call(?:ed)?\s+(?:on|at)|contact(?:ed)?\s+the|paid|transferred|sent)\b", between, re.I):
                    owner, why, base = p, f"{p['surface']} is written right before this {kind.lower()} with an ownership word (\"{_clip(between, 45)}\")", "cue"
                    break
                if re.match(r"\s*['’]s\s", between) or re.match(r"\s*(?:ka|ki|ke|का|की|के)\s", between):
                    owner, why, base = p, f"Possessive form: {p['surface']}'s {kind.lower()}", "cue"
                    break
                break      # only the nearest eligible person can own it; an earlier person with an unrelated 'mobile' word between is not the owner
        # (1b) "Mobile: ..." line inside a party block (complainant / accused details)
        if owner is None and kind in ("PHONE", "EMAIL") and _PHONE_CTX.search(text[max(0, it["start"] - 22):it["start"]]) and it.get("confidence", 0) > 0.5:
            bo = _block_owner(people, text, it["start"], sents)
            if bo is not None and not re.search(r"\b(?:called|call\s+from|received\s+a\s+call|dialled|dialed)\b", pre[-40:], re.I):
                owner, why, base = bo, f"Phone/e-mail is listed with a contact label in the block that starts with {bo['surface']}'s details", "cue"
        # (2) pronoun: his / her / uska / उसका
        if owner is None:
            pm = re.search(r"\b(?:his|her|its|uska|uski|uske|unka|uska)\b|उसका|उसकी|उसके|इसका|\b(?:he|she)\s+(?:is|was)\s+not\s+(?:answering|picking|receiving|responding)\b[^.\n]{0,25}$", pre[-70:], re.I)
            if pm and kind != "VEHICLE":
                ref = _ref_person(people, text, it["start"], want=("ACCUSED", "PERSON"), fallback_any=True)
                # nearest earlier person mention (any role except officers) for the pronoun
                prior = [p for p in people if p["start"] < it["start"] and p.get("role") not in ("INVESTIGATING_OFFICER", "POLICE_OFFICER", "RELATIVE")]
                if not any(p.get("role") == "ACCUSED" for p in prior):
                    prior = [p for p in prior if p.get("role") != "COMPLAINANT"]   # "uska number" almost never means the complainant's own
                near = max(prior, key=lambda p: p["start"]) if prior else None
                acc_prior = [p for p in prior if p.get("role") == "ACCUSED"]
                ref = (_ref_person(acc_prior, text, it["start"], want=("ACCUSED",), fallback_any=False) if acc_prior else None) or near
                if ref:
                    owner, why, base = ref, f"\"{pm.group(0)}\" (his/her) refers back to the nearest earlier person, {ref['surface']}", "weak_cue"
            elif pm and kind == "VEHICLE":
                ref = _ref_person(people, text, it["start"], want=("ACCUSED",), fallback_any=False)
                if ref:
                    owner, why, base = ref, f"\"{pm.group(0)}\" refers to the accused {ref['surface']}", "weak_cue"
        # (3) "the accused / aaropi ... vehicle"
        if owner is None and kind == "VEHICLE":
            ctx = text[max(s0, it["start"] - 120):it["start"]]
            if re.search(r"accused|suspect|culprit|miscreant|aaropi|आरोपी|fled|fleeing|escaped|भाग", ctx, re.I):
                ref = _ref_person(people, text, it["start"], want=("ACCUSED",), fallback_any=False)
                if ref:
                    owner, why, base = ref, f"The sentence talks about the accused fleeing/using this vehicle; latest named accused is {ref['surface']}", "weak_cue"
        if owner is None and kind == "VEHICLE":
            # "Aaropi ke paas ..." in previous sentence, or sentence starting with 'Accused car'
            prev = text[sents[max(0, si - 1)][0]:e0]
            if re.search(r"aaropi|accused|आरोपी", text[s0:it["start"]], re.I) or re.search(r"\b(?:aaropi|accused|आरोपी)\b", prev[-160:], re.I) and re.search(r"paas|ke\s+paas|के\s+पास|had|possess", text[s0:it["end"] + 40], re.I):
                ref = _ref_person(people, text, it["start"], want=("ACCUSED",), fallback_any=False)
                if ref:
                    owner, why, base = ref, f"Vehicle mentioned with the accused ({ref['surface']}) as possessing/using it", "weak_cue"
        # (4) name inside a UPI / e-mail handle
        if owner is None and kind in ("UPI", "EMAIL"):
            local = re.sub(r"[^a-z]", " ", it["normalized"].split("@")[0]).split()
            local_join = re.sub(r"[^a-z]", "", it["normalized"].split("@")[0])
            best = None
            for p in people:
                if p.get("role") in ("INVESTIGATING_OFFICER", "POLICE_OFFICER"):
                    continue
                toks = [t for t in re.sub(r"[^a-z ]", " ", N.name_key(p["normalized"] + " " + (p.get("alias") or ""))).split() if len(t) >= 3]
                hit = [t for t in toks if t in local_join]
                if hit and (len(hit) >= 2 or (len(toks) == 1) or p.get("role") == "ACCUSED"):
                    best = (p, hit)
                    if p.get("role") == "ACCUSED":
                        break
            if best and best[0].get("role") != "COMPLAINANT" or (best and kind == "EMAIL"):
                owner, why, base = best[0], f"The name {'/'.join(best[1])} appears inside the handle \"{it['normalized'].split('@')[0]}\" (weak evidence: a handle can be chosen by anyone)", "inferred"
        # (5) the accused's number introduced by call words ("called ... on N")
        if owner is not None:
            rel = "USED" if kind == "VEHICLE" else "OWNS"
            conf = cal(rel, base)
            if it["kind"] in ("PHONE",) and owner.get("role") == "COMPLAINANT":
                conf = min(conf + 0.05, 0.95)
            add(owner, rel, it, conf, min(owner["start"], it["start"]), it["end"], why)
    # complainant's labelled phone/account in the complainant block: covered by rule (1)/(2). ---- call relations
    for si, (s0, e0) in enumerate(sents):
        st = text[s0:e0]
        m = re.search(r"\b(?:the\s+)?accused\b[^.]{0,30}?\b(?:had\s+)?called\s+the\s+complainant", st, re.I)
        if m:
            acc = _ref_person(people, text, s0 + m.start(), want=("ACCUSED",), fallback_any=False)
            comp = next((p for p in people if p.get("role") == "COMPLAINANT"), None)
            if acc and comp:
                add(acc, "CALLED", comp, cal("CALLED", "weak_cue"), s0 + m.start(), s0 + m.end(), "Sentence says the accused called the complainant")
        for pm in re.finditer(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:called|phoned|rang|contacted)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", st):
            a = next((p for p in people if _same_person(p["normalized"], pm.group(1))), None)
            b = next((p for p in people if _same_person(p["normalized"], pm.group(2))), None)
            if a and b:
                add(a, "CALLED", b, cal("CALLED", "cue"), s0 + pm.start(), s0 + pm.end(), f"\"{pm.group(0)}\"")
        for pm in re.finditer(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+ne\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+ko\s+(?:phone|call|fone)\s+kiya", st):
            a = next((p for p in people if _same_person(p["normalized"], pm.group(1))), None)
            b = next((p for p in people if _same_person(p["normalized"], pm.group(2))), None)
            if a and b:
                add(a, "CALLED", b, cal("CALLED", "cue"), s0 + pm.start(), s0 + pm.end(), f"\"{pm.group(0)}\"")
        # payments by the complainant
        if re.search(r"\b(?:paid|transferred|sent|deposited|transfer|bheje|bheja|jama|जमा|भेजे|भेजा|ट्रांसफर)\b", st, re.I):
            comp = next((p for p in people if p.get("role") == "COMPLAINANT"), None)
            targets = [i for i in idents if s0 <= i["start"] < e0 and i["kind"] in ("UPI", "ACCOUNT")]
            if comp and targets and re.search(r"\b(?:I|Maine|मैंने|we)\b|complainant", st):
                for tg in targets[:2]:
                    add(comp, "TRANSFERRED_TO", tg, cal("TRANSFERRED_TO", "weak_cue"), s0, e0, "Sentence says the complainant paid / sent money to this account or UPI id")
    return rels
