"""Deterministic, offline extraction of investigative identifiers from a social-media post or profile bio.

Pulls out @handles, #hashtags, URLs (domain + shortener flag), Indian phone numbers (normalised to +91XXXXXXXXXX), e-mails,
UPI IDs, crypto wallet addresses (checksum-validated where the format has one), place names (offline gazetteer, English and
Devanagari), languages (script + Hinglish heuristic) and dates/times mentioned in the text.

Nothing here contacts a network. URL shorteners are flagged, not expanded (that would need a live request); the officer sees the
short link and the fact that its destination is unknown.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_ZW = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)

SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "is.gd", "cutt.ly", "rb.gy", "ow.ly", "tiny.cc", "shorturl.at", "buff.ly", "rebrand.ly",
              "bl.ink", "lnkd.in", "s.id", "v.gd", "t.ly", "shorte.st", "adf.ly", "clck.ru", "amzn.to", "fb.me", "youtu.be"}
MESSAGING = {"t.me", "telegram.me", "wa.me", "chat.whatsapp.com", "signal.me", "discord.gg", "discord.com", "line.me"}
UPI_HANDLES = ("ybl", "oksbi", "okhdfcbank", "okicici", "okaxis", "paytm", "ibl", "axl", "upi", "apl", "sbi", "hdfcbank", "icici", "axisbank", "pnb",
               "fbl", "waicici", "wahdfcbank", "waaxis", "wasbi", "airtel", "jio", "freecharge", "postbank", "kotak", "barodampay", "ikwik", "aubank",
               "cnrb", "pingpay", "yesbank", "idfcbank", "indus", "rbl", "federal", "sc", "boi", "cboi", "unionbank", "uboi", "dbs", "ezetap", "abfspay")

_URL = re.compile(r"(?i)\b((?:https?://|www\.)[^\s<>\"'()\[\]{}]+|(?:bit\.ly|tinyurl\.com|t\.co|goo\.gl|is\.gd|cutt\.ly|rb\.gy|t\.me|wa\.me|chat\.whatsapp\.com)/[^\s<>\"'()\[\]{}]+)")
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}(?![\w-])")
_UPI = re.compile(r"(?i)(?<![\w.@+-])([a-z0-9][a-z0-9._\-]{1,48})@(" + "|".join(UPI_HANDLES) + r")(?![\w.-]*\w)")
_MENTION = re.compile(r"(?<![\w.@/])@([A-Za-z0-9_][A-Za-z0-9_.]{1,29})")
_HASHTAG = re.compile(r"(?<![\w&])#([^\W\d_][\wऀ-ॿ]{1,49}|\d+[^\W\d_][\wऀ-ॿ]*)", re.UNICODE)
_PHONE = re.compile(r"(?<![\w.])(?:\+?\s?91[\s\-.]?\(?0?\)?[\s\-.]*|0[\s\-]?)?([6-9](?:[\s\-.]?\d){9})(?![\d])")
_BTC_LEG = re.compile(r"(?<![A-Za-z0-9])[13][1-9A-HJ-NP-Za-km-z]{25,34}(?![A-Za-z0-9])")
_BTC_BECH = re.compile(r"(?<![A-Za-z0-9])bc1[ac-hj-np-z02-9]{11,71}(?![A-Za-z0-9])")
_ETH = re.compile(r"(?<![A-Za-z0-9])0x[a-fA-F0-9]{40}(?![A-Za-z0-9])")
_TRX = re.compile(r"(?<![A-Za-z0-9])T[1-9A-HJ-NP-Za-km-z]{33}(?![A-Za-z0-9])")
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

_DATE_TXT = re.compile(r"(?i)\b(\d{1,2}[/\-.]\d{1,2}[/\-.](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2}|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{2,4})\b")
_TIME_TXT = re.compile(r"(?i)\b((?:[01]?\d|2[0-3])[:.][0-5]\d\s?(?:am|pm)?|(?:1[0-2]|0?[1-9])\s?(?:am|pm)|(?:[01]?\d|2[0-3])[:.][0-5]\d\s?(?:hrs|hours))\b")

_AMBIGUOUS_PLACES = {"anand", "sagar", "salem", "hassan", "puri", "pali", "kolar", "mandya", "sikar", "una", "rewa", "morena", "delhi ncr"}


def _b58check(addr: str) -> bytes | None:
    n = 0
    for ch in addr:
        i = _B58.find(ch)
        if i < 0:
            return None
        n = n * 58 + i
    raw = n.to_bytes(25, "big") if n.bit_length() <= 200 else None
    if raw is None:
        return None
    pad = len(addr) - len(addr.lstrip("1"))
    if pad and raw[:pad] != b"\x00" * pad:
        return None
    if hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4] != raw[-4:]:
        return None
    return raw


def _wallets(text: str) -> list[dict]:
    out, seen = [], set()

    def add(addr, chain, validated):
        if addr not in seen:
            seen.add(addr)
            out.append({"value": addr, "chain": chain, "checksum_valid": validated})
    for m in _BTC_LEG.finditer(text):
        raw = _b58check(m.group(0))
        if raw is not None and raw[0] in (0x00, 0x05):
            add(m.group(0), "Bitcoin", True)
    for m in _BTC_BECH.finditer(text):
        add(m.group(0), "Bitcoin (bech32)", False)
    for m in _ETH.finditer(text):
        add(m.group(0), "Ethereum-style (0x)", False)
    for m in _TRX.finditer(text):
        raw = _b58check(m.group(0))
        if raw is not None and raw[0] == 0x41:
            add(m.group(0), "TRON (USDT-TRC20 style)", True)
    return out


def normalise_phone(raw: str) -> str:
    d = re.sub(r"\D", "", (raw or "").translate(_DEV_DIGITS))
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return "+91" + d if len(d) == 10 and d[0] in "6789" else ""


def phone_key(value: str) -> str:
    """Comparison key for matching a phone number against case entities: the last ten digits."""
    d = re.sub(r"\D", "", (value or "").translate(_DEV_DIGITS))
    return d[-10:] if len(d) >= 10 else ""


def url_info(raw: str) -> dict:
    u = raw.rstrip(".,;:!?)”’'\"")
    full = u if re.match(r"(?i)https?://", u) else "http://" + u
    host = re.sub(r"^(?i:https?)://", "", full).split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.split("@")[-1].split(":")[0].lower()
    domain = re.sub(r"^www\.", "", host)
    kind = "shortener" if domain in SHORTENERS else ("messaging_invite" if domain in MESSAGING else "web")
    note = ""
    if kind == "shortener":
        note = "Shortened link: the real destination is hidden and cannot be resolved offline. Resolve it on an isolated system before opening."
    elif kind == "messaging_invite":
        note = "Messaging-app link (group invite or direct chat). Joining or opening it is an officer decision under the authority note."
    return {"url": u, "domain": domain, "kind": kind, "is_shortener": kind == "shortener", "note": note}


def normalise_url(u: str) -> str:
    info = url_info(u)
    rest = re.sub(r"(?i)^(?:https?://)?(?:www\.)?[^/?#]+", "", u.rstrip(".,;:!?)"))
    return info["domain"] + rest.rstrip("/").lower()


_GAZ = None


def _gaz():
    global _GAZ
    if _GAZ is None:
        from backend.app.services.timeline_engine import gazetteer
        _GAZ = gazetteer()
    return _GAZ


def find_places(text: str) -> list[dict]:
    """All gazetteer places named in the text (English spellings and Devanagari), longest match first, de-duplicated."""
    out, seen = [], set()
    gaz = _gaz()
    low = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    if gaz._scan_re is not None:
        taken: list[tuple[int, int]] = []
        for m in gaz._scan_re.finditer(" " + low + " "):
            name = m.group(1)
            if name in _AMBIGUOUS_PLACES or any(a <= m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            p = gaz.by_name[name]
            if p["name"] not in seen:
                seen.add(p["name"])
                out.append({"name": p["name"], "state": p.get("state", ""), "kind": p.get("kind", ""), "lat": p["lat"], "lon": p["lon"], "script": "latin"})
    try:
        from ai_engine.nlp.places import _EXACT
    except Exception:  # noqa: BLE001
        _EXACT = {}
    for tok in re.findall(r"[ऀ-ॿ]+", text or ""):
        eng = _EXACT.get(tok)
        if eng and eng not in seen and eng.lower() not in _AMBIGUOUS_PLACES:
            p = gaz.lookup(eng)
            seen.add(eng)
            out.append({"name": eng, "state": (p or {}).get("state", ""), "kind": (p or {}).get("kind", ""), "lat": (p or {}).get("lat"),
                        "lon": (p or {}).get("lon"), "script": "devanagari"})
    return out


def detect_language(text: str) -> dict:
    """Script-based language guess with a Hinglish (romanised Hindi) heuristic; reuses the FIR language identifier."""
    clean = re.sub(r"https?://\S+|www\.\S+|[@#]\w+", " ", text or "")  # links, hashtags and handles are Latin script in every language
    words = re.findall(r"[^\W\d_]+", clean, re.UNICODE)
    if len(words) < 2:
        return {"code": "unknown", "name": "Unknown (too short)", "confidence": 0.0}
    from ai_engine.nlp import langid
    d = langid.detect(clean)
    code = d.get("code", "unknown")
    return {"code": code, "name": langid.CODE_TO_NAME.get(code, "Unknown"), "confidence": round(float(d.get("confidence") or 0.0), 2),
            "mixed": bool(d.get("mixed"))}


def extract(text: str) -> dict[str, Any]:
    """Everything extractable from one text. Values are normalised; original spans are not kept (the text itself is)."""
    t = (text or "").translate(_ZW)
    t_ascii_digits = t.translate(_DEV_DIGITS)
    urls = []
    for m in _URL.finditer(t):
        info = url_info(m.group(1))
        if info["domain"] and "." in info["domain"] and info["url"] not in [x["url"] for x in urls]:
            urls.append(info)
    url_spans = [m.span() for m in _URL.finditer(t)]
    emails = list(dict.fromkeys(m.group(0).lower() for m in _EMAIL.finditer(t)))
    upis = list(dict.fromkeys((m.group(1) + "@" + m.group(2)).lower() for m in _UPI.finditer(t)))
    phones = []
    for m in _PHONE.finditer(t_ascii_digits):
        if any(a <= m.start() < b for a, b in url_spans):
            continue
        p = normalise_phone(m.group(1))
        if p and p not in phones:
            phones.append(p)
    mentions = list(dict.fromkeys(m.group(1).rstrip(".").lower() for m in _MENTION.finditer(t)
                                  if not any(a <= m.start() < b for a, b in url_spans)))
    tags = list(dict.fromkeys(m.group(1).lower() for m in _HASHTAG.finditer(t)))
    return {
        "mentions": mentions, "hashtags": tags, "urls": urls, "phones": phones, "emails": emails, "upi_ids": upis, "wallets": _wallets(t),
        "places": find_places(t), "language": detect_language(t),
        "dates_mentioned": list(dict.fromkeys(m.group(1) for m in _DATE_TXT.finditer(t)))[:10],
        "times_mentioned": list(dict.fromkeys(m.group(1).strip() for m in _TIME_TXT.finditer(t)))[:10],
    }


IDENT_KEYS = ("phones", "emails", "upi_ids", "wallets")


def identifiers(ex: dict) -> list[tuple[str, str]]:
    """Flat (kind, normalised value) list used for matching and cross-account hints."""
    out = [("phone", p) for p in ex.get("phones", [])] + [("email", e) for e in ex.get("emails", [])] + [("upi", u) for u in ex.get("upi_ids", [])]
    out += [("wallet", w["value"] if isinstance(w, dict) else w) for w in ex.get("wallets", [])]
    return out


def mask_value(kind: str, value: str) -> str:
    v = str(value or "")
    if kind == "phone":
        return v[:3] + "*" * max(0, len(v) - 5) + v[-2:]
    if kind in ("email", "upi"):
        local, _, dom = v.partition("@")
        return (local[:2] + "*" * max(1, len(local) - 2)) + "@" + dom
    return v[:4] + "*" * max(0, len(v) - 8) + v[-4:] if len(v) > 8 else "*" * len(v)


def mask_text(text: str) -> str:
    """Hide phones, e-mails, UPI IDs and wallets inside free text (for roles without sensitive_read)."""
    if not text:
        return text
    t = text
    for m in sorted(_UPI.finditer(t), key=lambda x: -x.start()):
        t = t[:m.start()] + mask_value("upi", m.group(0)) + t[m.end():]
    for m in sorted(_EMAIL.finditer(t), key=lambda x: -x.start()):
        t = t[:m.start()] + mask_value("email", m.group(0)) + t[m.end():]
    for rx in (_ETH, _TRX, _BTC_BECH, _BTC_LEG):
        for m in sorted(rx.finditer(t), key=lambda x: -x.start()):
            t = t[:m.start()] + mask_value("wallet", m.group(0)) + t[m.end():]
    td = t.translate(_DEV_DIGITS)
    for m in sorted(_PHONE.finditer(td), key=lambda x: -x.start()):
        p = normalise_phone(m.group(1))
        if p:
            s, e = m.span()
            t = t[:s] + mask_value("phone", p) + t[e:]
    return t
