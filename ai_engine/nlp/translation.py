"""Investigation-document translation to English: honest, offline, sentence by sentence.

What it does
------------
* Splits a document into label/value segments and sentences (keeping the character offsets of the source).
* Protects identifiers (phone, vehicle, account, Aadhaar, PAN, IFSC, UPI/e-mail, dates, times, amounts, FIR numbers, sections)
  so they are copied exactly, never translated or re-spelled.
* Translates words with a police/legal glossary (Hindi, Marathi, romanised Hindi "Hinglish"; core vocabulary for Bengali,
  Gujarati, Punjabi, Tamil, Telugu, Kannada, Malayalam, Urdu), including inflected forms, and re-orders postpositions
  ("Delhi mein" -> "in Delhi", "X ka Y" -> "X's Y", verb-last -> subject-verb) for Hindi/Marathi/Hinglish.
* Transliterates names and places (Devanagari, Bengali, Gurmukhi, Gujarati, Odia, Tamil, Telugu, Kannada, Malayalam, Urdu).
* Never silently returns garbage: every sentence carries a quality flag - ``glossary`` (all content words found), ``rule-based``
  (mostly found, word order rules or word-for-word gloss), ``unknown`` (mostly not understood), ``identity`` (already English),
  or ``model`` (local neural model) - and every word the glossary does not know is shown in the English text as [[word]] and
  listed in ``unknown_words``.  Each sentence and the whole document have a confidence and a coverage figure.
* If a local neural model is installed (IndicTrans2 via ``INDICTRANS2_MODEL``) it is used per sentence and checked to have kept
  every protected identifier; otherwise the rule engine runs alone.  ``engine`` in the output always says which produced the text.

The rule engine is a glossary + reordering rules, NOT a neural translator; its output is an aid for reading, not a certified translation.
"""
from __future__ import annotations

import os
import re
import unicodedata
from functools import lru_cache
from typing import Any

from ai_engine.nlp import langid as LID
from ai_engine.nlp import normalize as N
from ai_engine.nlp import places as PLACES
from ai_engine.nlp import translit as TL
from ai_engine.nlp.lexicon_hi import HI, HX, MR
from ai_engine.nlp.lexicon_other import OTHER

RULE_ENGINE_ID = "rule-engine-v2"
RULE_ENGINE_LABEL = "Offline rule-based glossary engine v2 (not a neural model)"
MARK_L, MARK_R = "⟦", "⟧"          # visible unknown-word brackets

_W = "A-Za-z0-9" + "\u0900-\u0963\u0966-\u0DFF" + "\u0600-\u06FF\u200c\u200d"
_TOKEN_RX = re.compile(rf"[{_W}]+(?:['’][{_W}]+)?|[^\s{_W}]")

# --------------------------------------------------------------------------- normalisation & lexicon
_DEV_STRIP = dict.fromkeys(map(ord, "़‌‍"), None)


def _norm_dev(w: str) -> str:
    w = unicodedata.normalize("NFC", w).translate(_DEV_STRIP).replace("ँ", "ं")
    return w


def _norm_hx(w: str) -> str:
    w = w.lower().replace("’", "'")
    w = re.sub(r"aa", "a", w)
    w = re.sub(r"ee|ii", "i", w)
    w = re.sub(r"oo|uu", "u", w)
    w = w.replace("w", "v").replace("z", "j")
    w = re.sub(r"(.)\1+", r"\1", w)
    return w


def _norm(w: str, lang: str) -> str:
    if lang == "hinglish":
        return _norm_hx(w)
    if any("ऀ" <= c <= "෿" for c in w):
        return _norm_dev(w)
    if any("؀" <= c <= "ۿ" for c in w):
        return re.sub("[ً-ٰٟ]", "", w).replace("ي", "ی").replace("ك", "ک")
    return w.lower()


def _words(s: str) -> list[str]:
    return [m.group(0) for m in _TOKEN_RX.finditer(s) if re.match(rf"[{_W}]", m.group(0))]


class Lex:
    def __init__(self, blocks: list[str], lang: str):
        self.lang = lang
        self.table: dict[tuple, tuple[str, str]] = {}
        self.maxlen = 1
        for block in blocks:
            for line in block.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                left, right = line.split("=", 1)
                cat = "N"
                if "|" in right:
                    right, cat = right.rsplit("|", 1)
                    cat = cat.strip()
                en = right.strip()
                for v in left.split(","):
                    toks = tuple(_norm(t, lang) for t in _words(v.strip()))
                    if toks:
                        self.table[toks] = (en, cat)
                        self.maxlen = max(self.maxlen, len(toks))
        self.first = {k[0] for k in self.table}

    def __len__(self):
        return len(self.table)


@lru_cache(maxsize=None)
def get_lex(lang: str) -> Lex:
    if lang == "hi":
        return Lex([HI], "hi")
    if lang == "mr":
        return Lex([HI, MR], "mr")          # Marathi overlay wins over the shared Devanagari table
    if lang == "hinglish":
        return Lex([HX], "hinglish")
    if lang in OTHER:
        return Lex([OTHER[lang]], lang)
    return Lex([], lang)


def glossary_size() -> dict[str, int]:
    return {k: len(get_lex(k)) for k in ["hi", "mr", "hinglish", *OTHER]}


# --------------------------------------------------------------------------- inflection (Hindi / Marathi)
_HI_SUFFIX = [("ियों", ["ी", "ि"], True), ("ियां", ["ी"], True), ("ियाँ", ["ी"], True), ("ाओं", ["ा"], True), ("ाएं", ["ा", ""], True), ("ाएँ", ["ा", ""], True),
              ("ों", ["", "ा"], True), ("ें", [""], True), ("ीं", ["ी"], True), ("े", ["ा"], False), ("ी", ["ा", ""], False)]
# Marathi: (suffix, english preposition, stem endings to try)
_MR_SUFFIX = [("ांमध्ये", "in", True), ("ामध्ये", "in", False), ("मध्ये", "in", False), ("ाच्या", "of", False), ("ांच्या", "of", True), ("ाचा", "of", False), ("ाची", "of", False), ("ाचे", "of", False),
              ("ांचा", "of", True), ("ांची", "of", True), ("ांचे", "of", True), ("ांनी", "", True), ("ानी", "", False), ("ाने", "", False), ("ेने", "", False), ("ांना", "to", True),
              ("ाला", "to", False), ("ाकडून", "from", False), ("ाकडे", "to", False), ("ातून", "from", False), ("ाहून", "from", False), ("ावर", "on", False), ("ांवर", "on", True),
              ("ासाठी", "for", False), ("साठी", "for", False), ("ासोबत", "with", False), ("ाशी", "with", False), ("ांत", "in", True), ("ात", "in", False), ("ास", "to", False),
              ("च्या", "of", False), ("चा", "of", False), ("ची", "of", False), ("चे", "of", False), ("ला", "to", False), ("वर", "on", False), ("ने", "", False), ("ून", "from", False),
              ("कडे", "to", False), ("कडून", "from", False)]


def _pluralize(en: str) -> str:
    if not en or " " in en.strip() or not en.isalpha():
        return en
    if en.endswith(("s", "x", "ch", "sh")):
        return en + "es"
    if en.endswith("y") and en[-2:-1] not in "aeiou":
        return en[:-1] + "ies"
    return en + "s"


def _inflect(tok: str, lex: Lex):
    """Yield (english, cat, note, prep) for an inflected Devanagari token whose stem is in the lexicon."""
    n = len(tok)
    if n < 3:
        return None
    if lex.lang == "mr":
        for suf, prep, plural in _MR_SUFFIX:
            if tok.endswith(suf) and n - len(suf) >= 2:
                stem = tok[: n - len(suf)]
                for end in ("", "ा", "ी", "े", "ि", "ु"):
                    hit = lex.table.get((stem + end,))
                    if hit and hit[1] in ("N", "LBL", "ADJ", "PRON"):
                        en = _pluralize(hit[0]) if plural and hit[1] == "N" else hit[0]
                        return en, hit[1], f"Marathi case ending '{suf}'", prep
    for suf, ends, plural in _HI_SUFFIX:
        if tok.endswith(suf) and n - len(suf) >= 2:
            stem = tok[: n - len(suf)]
            for end in ends:
                hit = lex.table.get((stem + end,))
                if hit and hit[1] in ("N", "LBL", "ADJ", "V"):
                    en = _pluralize(hit[0]) if plural and hit[1] == "N" else hit[0]
                    return en, hit[1], f"inflected form (ending '{suf}')", ""
    return None


# --------------------------------------------------------------------------- protected identifiers
_ID_PATTERNS = [
    ("email", r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+"),
    ("url", r"(?:https?://|www\.)[^\s]+"),
    ("vehicle", r"(?<![A-Za-z0-9])[A-Z]{2}[\s\-]?[0-9OIl]{1,2}[\s\-]?[A-Z]{1,3}[\s\-]?[0-9]{3,4}(?![A-Za-z0-9])"),
    ("vehicle", r"(?<![A-Za-z0-9])[0-9]{2}\s?BH\s?[0-9]{4}\s?[A-Z]{1,2}(?![A-Za-z0-9])"),
    ("ifsc", r"(?<![A-Za-z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Za-z0-9])"),
    ("pan", r"(?<![A-Za-z0-9])[A-Z]{5}[0-9]{4}[A-Z](?![A-Za-z0-9])"),
    ("amount", r"(?:₹|Rs\.?|INR|रु\.?)\s?[0-9][0-9,]*(?:\.[0-9]+)?(?:/-)?"),
    ("date", r"(?<![0-9/\-])[0-9]{1,2}[/\-.][0-9]{1,2}[/\-.][0-9]{2,4}(?![0-9])"),
    ("time", r"(?<![0-9:])[0-2]?[0-9]:[0-5][0-9](?::[0-5][0-9])?(?![0-9])"),
    ("firno", r"(?<![0-9/])[0-9]{1,6}/[0-9]{2,4}(?![0-9/])"),
    ("phone", r"(?<![0-9])(?:\+?91[\s\-]?)?[6-9][0-9]{4}[\s\-]?[0-9]{5}(?![0-9])"),
    ("number", r"(?<![A-Za-z0-9])[0-9][0-9,.\-]*(?:/-)?(?![A-Za-z0-9])"),
    ("section", r"(?<![A-Za-z0-9])(?:IPC|BNS|BNSS|CrPC|NDPS|POCSO|PMLA|IT Act)(?![A-Za-z0-9])"),
]
_ID_RX = [(k, re.compile(p)) for k, p in _ID_PATTERNS]
_ENTITY_ID_KINDS = {"PHONE", "LANDLINE", "VEHICLE", "ACCOUNT", "AADHAAR", "PAN", "IFSC", "UPI", "EMAIL", "CARD", "IMEI", "TRANSACTION_ID", "DATE", "AMOUNT", "SECTION", "FIR"}


def protected_spans(text: str, entities: list | None = None) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for e in entities or []:
        if e.get("kind") in _ENTITY_ID_KINDS and isinstance(e.get("start"), int) and e["end"] > e["start"]:
            if e.get("kind") == "DATE" and re.search(r"[^\x00-\x7f]", text[e["start"]:e["end"]]):
                continue   # a date written with native month words is translated (digits stay), not copied
            spans.append((e["start"], e["end"], e["kind"].lower()))
    for kind, rx in _ID_RX:
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), kind))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    out: list[tuple[int, int, str]] = []
    for s in spans:
        if out and s[0] < out[-1][1]:
            continue
        out.append(s)
    return out


# --------------------------------------------------------------------------- categories & vocabulary helpers
_TITLE_TOKENS = {"श्री", "श्रीमती", "श्रीमान", "कुमारी", "सुश्री", "shri", "smt", "sri", "mr", "mrs", "ms", "sh", "smt.", "श्रीमन्"}
_NAME_CUE_TOKENS = {"पुत्र", "पुत्री", "s/o", "d/o", "w/o", "बेटा", "बेटी", "पत्नी", "पति", "putra", "putri"}
_PLACE_CUE_TOKENS = {"निवासी", "साकिन", "nivasi", "resident", "रहने", "वाला"}
_NAME_LABELS = {"Complainant", "Father's name", "Mother's name", "Husband's name", "Investigating Officer", "Informant"}
_PLACE_LABELS = {"District", "Police Station", "Place of occurrence", "Address", "village", "city"}
_TIME_PHRASES = {"night": "at night", "morning": "in the morning", "afternoon": "in the afternoon", "evening": "in the evening", "today": "today", "yesterday": "yesterday"}
_MONTHS = {"January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"}
_DROP = {"जी", "ji", "ॐ"}
_AMBIG_LATIN = {"is", "in", "us", "un", "do", "me", "to", "the", "hi", "no", "main", "mat", "din", "ki", "ka", "ke", "par", "pe", "ne", "se", "ko", "ya", "aur", "kal", "sab", "kar", "tab", "phir", "fir",
                "bank", "case", "cash", "card", "police", "report", "call", "phone", "office", "market", "station", "number", "fraud", "video", "photo", "link", "email", "sim", "atm", "car", "cycle",
                "hai", "tha", "thi", "the", "hain", "ho", "hua", "hue", "hui", "gaya", "gaye", "gayi", "raha", "rahe", "rahi", "liya", "diya", "kiya", "bhi", "hi", "to"}
_ENGLISH_COMMON = set("""a an the of and or to in on at by for with from into over under about after before during near inside outside between against without through
is are was were be been being am has have had do does did will would can could may might shall should not no yes subject sub ref reference dear sir madam respected this that these those there here who whom whose which what when where why how
i me my mine we us our you your he him his she her it its they them their one two three four five six seven eight nine ten first second last next same other all any some each every many much more most
police station district complainant accused suspect victim witness officer inspector constable sub-inspector superintendent report complaint case section act theft stolen robbery fraud cheating
assault threat murder attempt property vehicle motorcycle scooter car mobile phone laptop bag purse wallet gold silver cash money rupees amount account bank card upi otp link online cyber
address name father mother husband wife son daughter brother sister resident age years year month day date time morning evening night today yesterday hours hrs approx approximately
road street lane market shop house flat building sector block colony village city town area near opposite behind front gate main parking helmet black white red blue green grey yellow
registered lodged filed arrested seized recovered found missing lost took taken given gave said stated told reported informed called sent received demanded transferred deposited withdrawn
suspected unknown person persons male female man woman boy girl youth aged wearing riding fled absconded escaped identified investigation enquiry statement evidence footage cctv
also then later earlier again while during since until upon under above below both either neither only very just still already now""".split())


_KEEP_WORDS = {"Act", "Code", "Sanhita", "Penal", "Indian", "Bharatiya", "Information", "Technology", "Dowry", "Prohibition", "Prevention", "Corruption", "Shri", "Smt.", "Mr.", "Ms.", "Kumari",
               "Kotwali", "Aadhaar", "Sub-Inspector", "Inspector", "Constable", "Head", "Assistant", "Superintendent", "Deputy", "Police", "WhatsApp", "Telegram", "Facebook", "Instagram", "PAN", "Hindi", "Marathi", "English"} | _MONTHS


def _lower_gloss(en: str) -> str:
    """Glossary entries are stored capitalised (they double as form labels); inside a sentence they are lower-cased except acronyms and proper terms."""
    out = []
    for w in en.split(" "):
        if len(w) > 1 and w.isupper() or w in _KEEP_WORDS or not w[:1].isupper():
            out.append(w)
        else:
            out.append(w.lower())
    return " ".join(out)


def _cat_kind(cat: str) -> str:
    return "func" if cat in ("AUX", "NEG", "CONJ", "PRON", "NUM") or cat.startswith("P:") else "gloss"


# --------------------------------------------------------------------------- tokenising a sentence into glossed tokens
class Tok(dict):
    """dict with attribute-ish helpers: src, start, end, en, kind, cat, note"""


def _mk(src, start, end, en, kind, cat="N", note="", prep=""):
    return Tok(src=src, start=start, end=end, en=en, kind=kind, cat=cat, note=note, prep=prep)


def _title_case_name(s: str) -> str:
    return TL.title_name(s)


def _translit_token(src: str) -> str:
    return TL.title_name(TL.transliterate_word(src))


def _classify_sentence(text: str, base: int, lang: str, spans: list[tuple[int, int, str]], person_spans: list[tuple[int, int]], place_spans: list[tuple[int, int]], mode: str = "", mode_is_label: bool = False) -> list[Tok]:
    """Tokenise ``text`` (a slice of the document starting at offset ``base``) and gloss each token."""
    lex = get_lex(lang)
    toks: list[Tok] = []
    prot = [(s - base, e - base, k) for s, e, k in spans if s >= base and e <= base + len(text)]
    raw = [(m.start(), m.end(), m.group(0)) for m in _TOKEN_RX.finditer(text)]
    # merge tokens covered by a protected span
    items: list[tuple[int, int, str, str]] = []   # start, end, src, pkind
    pi = 0
    i = 0
    while i < len(raw):
        s, e, w = raw[i]
        hit = next(((ps, pe, pk) for ps, pe, pk in prot if ps <= s < pe), None)
        if hit:
            ps, pe, pk = hit
            items.append((ps, pe, text[ps:pe], pk))
            while i < len(raw) and raw[i][0] < pe:
                i += 1
            continue
        items.append((s, e, w, ""))
        i += 1
    n = len(items)
    normed = [(_norm(x[2], lang) if not x[3] and re.match(rf"[{_W}]", x[2]) else None) for x in items]
    is_person = lambda a, b: any(ps - base <= a and b <= pe - base for ps, pe in person_spans)
    is_place = lambda a, b: any(ps - base <= a and b <= pe - base for ps, pe in place_spans)
    k = 0
    cur_mode = mode
    prev_name = False
    while k < n:
        s, e, src, pk = items[k]
        if pk:
            en = src
            if pk == "amount":
                en = re.sub(r"^(?:रु\.?|Rs\.?|INR)\s?", "Rs. ", src)
            kind = "num" if pk in ("number",) else "id"
            toks.append(_mk(src, base + s, base + e, N.ascii_digits(en), kind, "ID", pk))
            k += 1
            continue
        if not re.match(rf"[{_W}]", src):
            en = "." if src == "।" else src
            toks.append(_mk(src, base + s, base + e, en, "punct", "PUNCT"))
            k += 1
            continue
        w = normed[k]
        # longest phrase match
        matched = 0
        hit = None
        for L in range(min(lex.maxlen, n - k), 0, -1):
            seg = normed[k:k + L]
            if any(x is None for x in seg):
                continue
            key = tuple(seg)
            if key in lex.table:
                cand = lex.table[key]
                if L == 1 and lang == "hinglish" and w in {_norm_hx(a) for a in _AMBIG_LATIN} and not _hinglish_context(normed, k, lex):
                    continue
                hit, matched = cand, L
                break
        if hit and matched == 1 and hit[1] in ("N", "ADJ") and hit[0] not in ("Smt.", "Shri", "Mr.", "Ms.", "Kumari") and w not in {_norm(t, lang) for t in _NAME_CUE_TOKENS} \
                and src not in _TITLE_TOKENS and TL.word_script(src) not in (None, "latn") and (is_person(s, e) or cur_mode == "name" or prev_name) and not hit[0][:1].isupper():
            hit = None   # an ordinary word inside a person's name (Lal = red) is a name part, not a glossary word
        if hit:
            en, cat = hit
            a, b = items[k][0], items[k + matched - 1][1]
            srcs = text[a:b]
            if _norm(srcs, lang) in _DROP or srcs in _DROP:
                k += matched
                continue
            kind = _cat_kind(cat)
            if not mode_is_label:
                en = _lower_gloss(en)
            toks.append(_mk(srcs, base + a, base + b, en, "phrase" if matched > 1 and kind == "gloss" else kind, cat))
            if en in _NAME_LABELS or en in ("Accused",):
                pass
            if w in {_norm(t, lang) for t in _NAME_CUE_TOKENS} or srcs in _TITLE_TOKENS:
                cur_mode = "name"
            elif w in {_norm(t, lang) for t in _PLACE_CUE_TOKENS} or en == "resident of":
                cur_mode = "place"
            elif cat not in ("PUNCT",) and cur_mode and not (cat.startswith("P:") or cat == "CONJ"):
                if cur_mode == "place" and cat in ("N", "ADJ") and en not in ("Smt.", "Shri", "Mr.", "Ms.", "Kumari"):
                    pass   # address words (city, road) keep the place context so the next word can be looked up as a place
                elif cat not in ("N", "ADJ") or en in ("Smt.", "Shri", "Mr.", "Ms.", "Kumari"):
                    cur_mode = "name" if en in ("Smt.", "Shri", "Mr.", "Ms.", "Kumari") else cur_mode
                else:
                    cur_mode = mode if mode else ""
            prev_name = en in ("Smt.", "Shri", "Mr.", "Ms.", "Kumari")
            k += matched
            continue
        # Devanagari / Indic / Arabic word not in glossary -----------------------------------------------------
        script = TL.word_script(src)
        if script in (None, "latn"):
            # Latin word
            if re.fullmatch(r"[0-9]+", src):
                toks.append(_mk(src, base + s, base + e, src, "num", "ID"))
            elif lang == "hinglish" and src.lower() not in _ENGLISH_COMMON and not (src[:1].isupper() and k > 0) and src.isalpha() and len(src) > 2 and not src.isupper():
                toks.append(_mk(src, base + s, base + e, MARK_L + src + MARK_R, "unknown", "UNK", "not in the Hinglish glossary"))
            else:
                toks.append(_mk(src, base + s, base + e, src, "latin", "N"))
            prev_name = False
            k += 1
            continue
        # inflection
        infl = _inflect(w, lex) if script == "deva" and lang in ("hi", "mr") else None
        if infl:
            en, cat, note, prep = infl
            toks.append(_mk(src, base + s, base + e, en, "infl", cat, note))
            if prep:
                toks.append(_mk("", base + e, base + e, prep, "func", "P:mr", "Marathi case ending", prep))
            elif lang == "mr" and note.startswith("Marathi") and prep == "":
                toks.append(_mk("", base + e, base + e, "", "func", "P:agent", note))
            k += 1
            continue
        latin = TL.transliterate_word(src)
        in_person = is_person(s, e)
        if src in TL.NAMES:
            toks.append(_mk(src, base + s, base + e, TL.NAMES[src], "name", "N", "transliterated name"))
            prev_name = True
            k += 1
            continue
        gz = None
        if not in_person and cur_mode != "name" and not prev_name:
            gz = PLACES.lookup(src, latin if script != "deva" else "")
        if gz:
            toks.append(_mk(src, base + s, base + e, gz[0], "place", "N", "place (gazetteer)" if gz[1] == "gazetteer" else "place matched by sound", ""))
            prev_name = False
            k += 1
            continue
        if in_person or cur_mode == "name" or prev_name:
            toks.append(_mk(src, base + s, base + e, _translit_token(src), "name", "N", "transliterated name"))
            prev_name = True
            k += 1
            continue
        if cur_mode == "place" or is_place(s, e):
            toks.append(_mk(src, base + s, base + e, _translit_token(src), "place", "N", "transliterated place name"))
            k += 1
            continue
        toks.append(_mk(src, base + s, base + e, MARK_L + _translit_token(src) + MARK_R, "unknown", "UNK", "word not in the glossary (spelling shown as transliteration)"))
        prev_name = False
        k += 1
    return toks


def _hinglish_context(normed, k, lex) -> bool:
    """Ambiguous Latin words (do, me, is, the ...) count as Hindi only when neighbours are unambiguous Hindi words."""
    for j in (k - 2, k - 1, k + 1, k + 2):
        if 0 <= j < len(normed) and normed[j] and (normed[j],) in lex.table and normed[j] not in {_norm_hx(a) for a in _AMBIG_LATIN} and normed[j] not in _ENGLISH_COMMON:
            return True
    return False


# --------------------------------------------------------------------------- reordering into English
class _Chunk:
    def __init__(self):
        self.toks: list[Tok] = []
        self.p = None      # postposition kind or None
        self.pen = ""
        self.kind = "np"   # np | verb | conj | punct


def _np_text(toks: list[Tok]) -> str:
    if _is_datey(toks):
        toks = [t for t in toks if t["en"] not in ("date", "Date")]
    out: list[str] = []
    for i, t in enumerate(toks):
        en = t["en"]
        if not en:
            continue
        if en == "o'clock" and out:
            # "11:30 o'clock" -> "at 11:30"
            j = len(out) - 1
            out.insert(j, "at")
            continue
        out.append(en)
    return " ".join(out)


def _is_datey(toks: list[Tok]) -> bool:
    for t in toks:
        if t["cat"] == "ID" and t["note"] == "date":
            return True
        if t["en"] in _MONTHS:
            return True
    return False


def _time_of_day(toks: list[Tok]) -> str:
    for t in toks:
        if t["en"] in _TIME_PHRASES and len([x for x in toks if x["kind"] != "punct"]) == 1:
            return _TIME_PHRASES[t["en"]]
    return ""


def _reorder(toks: list[Tok], reorder: bool) -> tuple[str, bool]:
    """Turn a glossed token list into one English string. Returns (text, reordered)."""
    reordered = False
    # split into clauses at subordinating conjunctions (and 'and' when the clause already has a verb)
    clauses: list[list[Tok]] = [[]]
    seps: list[Tok | None] = []
    have_verb = False
    for t in toks:
        if t["cat"] in ("V",):
            have_verb = True
        if t["cat"] == "CONJ" and (t["en"] in ("that", "because", "therefore", "when", "while", "if", "then", "but") or (t["en"] in ("and", "or") and have_verb)):
            seps.append(t)
            clauses.append([])
            have_verb = False
            continue
        if t["cat"] == "PUNCT" and t["en"] in (";",):
            seps.append(t)
            clauses.append([])
            have_verb = False
            continue
        clauses[-1].append(t)
    pieces: list[str] = []
    for ci, cl in enumerate(clauses):
        txt, r = _clause(cl, reorder)
        reordered = reordered or r
        if ci > 0:
            sep = seps[ci - 1]
            pieces.append(sep["en"] if sep["cat"] != "PUNCT" else ";")
        if txt:
            pieces.append(txt)
    return " ".join(p for p in pieces if p), reordered


def _clause(toks: list[Tok], reorder: bool) -> tuple[str, bool]:
    # chunking
    chunks: list[_Chunk] = []
    cur = _Chunk()

    def flush():
        nonlocal cur
        if cur.toks:
            chunks.append(cur)
        cur = _Chunk()
    for t in toks:
        cat = t["cat"]
        if cat.startswith("P:"):
            kind = cat[2:]
            cur.p, cur.pen = kind, t["en"]
            if not cur.toks and chunks and chunks[-1].p is None and chunks[-1].kind == "np":
                prev = chunks.pop()
                prev.p, prev.pen = kind, t["en"]
                cur = prev
            chunks.append(cur)
            cur = _Chunk()
        elif cat in ("V", "AUX", "NEG"):
            flush()
            if chunks and chunks[-1].kind == "verb":
                chunks[-1].toks.append(t)
            else:
                c = _Chunk()
                c.kind = "verb"
                c.toks.append(t)
                chunks.append(c)
        elif cat == "PUNCT":
            if t["en"] in (",",):
                flush()
                c = _Chunk()
                c.kind = "punct"
                c.toks.append(t)
                chunks.append(c)
            else:
                flush()
                c = _Chunk()
                c.kind = "punct"
                c.toks.append(t)
                chunks.append(c)
        else:
            cur.toks.append(t)
    flush()
    # verb group text
    def verb_text(c: _Chunk) -> str:
        vs = [t for t in c.toks if t["cat"] == "V"]
        neg = any(t["cat"] == "NEG" for t in c.toks)
        aux = [t for t in c.toks if t["cat"] == "AUX"]
        parts: list[str] = []
        seen_v = False
        for t in c.toks:
            if t["cat"] == "V":
                if seen_v and t["en"] in ("went", "was done", "happened") and len(vs) > 1:
                    continue       # passive / compound helper after a main verb
                parts.append(t["en"])
                seen_v = True
        if not vs:
            parts = [t["en"] for t in aux[:1]]
        if neg:
            parts.insert(0, "not")
        return " ".join(parts)
    # render each chunk
    rendered: list[tuple[_Chunk, str]] = []
    i = 0
    pending_gen = ""
    for c in chunks:
        if c.kind == "verb":
            rendered.append((c, verb_text(c)))
            continue
        if c.kind == "punct":
            rendered.append((c, c.toks[0]["en"]))
            continue
        np = _np_text(c.toks)
        if pending_gen:
            np = f"{pending_gen} {np}".strip()
            pending_gen = ""
        p = c.p
        if p == "gen":
            pending_gen = (np + "'s") if np else ""
            if not np:
                pending_gen = ""
            continue
        if p in (None,):
            rendered.append((c, np))
        elif p == "agent":
            rendered.append((c, np))
        elif p == "dat":
            tod = _time_of_day(c.toks)
            if tod:
                rendered.append((c, tod))
            elif _is_datey(c.toks):
                rendered.append((c, f"on {np}"))
            else:
                rendered.append((c, f"to {np}"))
        elif p == "mr":
            rendered.append((c, f"{c.pen} {np}".strip()))
        else:
            tod = _time_of_day(c.toks) if p in ("loc",) else ""
            rendered.append((c, tod or f"{c.pen} {np}".strip()))
    if pending_gen:
        rendered.append((_Chunk(), pending_gen.rstrip("'s") + " of"))
    if not reorder:
        return _join(rendered), False
    # SOV -> SVO within the clause (only when there is a verb and a subject-like chunk before it)
    vidx = next((i for i, (c, _) in enumerate(rendered) if c.kind == "verb"), None)
    if vidx is None:
        return _join(rendered), False
    sidx = next((i for i, (c, _) in enumerate(rendered[:vidx]) if c.kind == "np" and c.p in (None, "agent") and _np_text(c.toks)), None)
    if sidx is None:
        return _join(rendered), False
    subj = rendered[sidx]
    verbs = [r for r in rendered if r[0].kind == "verb"]
    verb = verbs[0]
    rest = [r for j, r in enumerate(rendered) if j != sidx and r[0] is not verb[0]]
    lead = [r for j, r in enumerate(rendered) if j < sidx]
    tail = [r for j, r in enumerate(rendered) if j > sidx and r[0] is not verb[0]]
    order = lead + [subj, verb] + tail
    moved = order != rendered
    return _join(order), moved


def _join(rendered: list[tuple[_Chunk, str]]) -> str:
    out = ""
    for c, s in rendered:
        if not s:
            continue
        if c.kind == "punct":
            out += s if s in (",", ".", ":", ";", ")", "!", "?") else (" " + s if out else s)
            if s in ("(", "["):
                out += ""
            continue
        out += (" " if out and not out.endswith(("(", "[")) else "") + s
    return out.strip()


# --------------------------------------------------------------------------- sentence assembly & quality
def _sentence_quality(toks: list[Tok], lang: str, reordered: bool) -> tuple[str, float, float, list[str]]:
    content = [t for t in toks if t["kind"] in ("gloss", "phrase", "infl", "unknown", "func") and t["cat"] not in ("PUNCT", "ID") and (t["kind"] != "func" or t["cat"] in ("PRON", "NUM"))]
    known = [t for t in content if t["kind"] != "unknown"]
    unknown = [t for t in content if t["kind"] == "unknown"]
    total = len(content)
    if total == 0:
        return "identity", 1.0, 1.0, []
    coverage = len(known) / total
    if coverage >= 0.9 and (not reordered or total <= 8):
        q = "glossary"
    elif coverage >= 0.5:
        q = "rule-based"
    else:
        q = "unknown"
    if lang not in ("hi", "mr", "hinglish") and q == "glossary" and total > 3:
        q = "rule-based"          # word-for-word gloss of a language without reordering rules
    if q == "glossary" and lang in ("hi", "mr", "hinglish") and total > 8 and len(unknown) == 0 and reordered:
        q = "rule-based"
    base = {"glossary": 0.82, "rule-based": 0.58, "unknown": 0.2}[q]
    conf = round(base * (0.55 + 0.45 * coverage), 3)
    return q, conf, round(coverage, 3), [t["src"] for t in unknown]


def _finish_text(s: str, source_ended_with_stop: bool = False, capitalise: bool = True) -> str:
    s = re.sub(r"\s+([,.;:!?)])", r"\1", s)
    s = re.sub(r"([(\[])\s+", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    if capitalise and s and s[0].islower():
        s = s[0].upper() + s[1:]
    return s


_ABBR = {"rs", "no", "dr", "shri", "smt", "sh", "mr", "mrs", "ms", "ps", "p.s", "dt", "s/o", "d/o", "w/o", "st", "u/s", "sec", "hc", "si", "asi", "vs", "etc", "r/o"}


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Split text (one line or a value) into sentences; returns (start, end) offsets."""
    spans = []
    start = 0
    n = len(text)
    for m in re.finditer(r"[।?!]+|\.(?=\s+[^\s])|\.$", text):
        end = m.end()
        if m.group(0) == "." or m.group(0).startswith("."):
            before = text[start:m.start()]
            w = re.findall(rf"[{_W}./]+$", before)
            last = w[-1].lower().rstrip(".") if w else ""
            if last in _ABBR or len(last) <= 1 or re.fullmatch(r"[0-9]+", last):
                continue
            nxt = text[end:].lstrip()[:1]
            if nxt and nxt.islower() and re.match(r"[a-z]", nxt):
                continue
        if text[start:end].strip():
            spans.append((start, end))
        start = end
    if text[start:].strip():
        spans.append((start, n))
    return spans


def _label_segments(line: str, lang_hint: str) -> list[tuple[int, int, int, int]]:
    """Find 'Label: value' pairs on one line -> [(label_start, label_end, value_start, value_end)] covering the line.  A segment
    without a label has label_start == label_end == value_start."""
    lex_by = {l: get_lex(l) for l in ("hi", "mr", "hinglish")}
    colons = [m.start() for m in re.finditer(r":|(?<=\s)[-–](?=\s)", line) if not re.search(r"[0-9]$", line[:m.start()].rstrip()[-1:] or " ") or True]
    words = [(m.start(), m.end(), m.group(0)) for m in _TOKEN_RX.finditer(line) if re.match(rf"[{_W}]", m.group(0))]
    marks: list[tuple[int, int, int]] = []   # label_start, label_end(colon start), value_start
    used = 0
    for c in colons:
        if line[c] == ":" and c > 0 and line[c - 1].isdigit() and c + 1 < len(line) and line[c + 1].isdigit():
            continue                              # a time such as 11:30
        before = [w for w in words if w[1] <= c and w[0] >= used]
        if not before:
            continue
        # longest run of trailing words (max 5) that forms a glossary phrase, else the words since the last mark (max 5)
        best = None
        for L in range(min(5, len(before)), 0, -1):
            run = before[-L:]
            if any(re.search(r"[0-9]", w[2]) for w in run):
                continue
            if run[-1][1] + 1 < c and line[run[-1][1]:c].strip():
                continue
            ok = False
            for lg, lx in lex_by.items():
                if tuple(_norm(w[2], lg) for w in run) in lx.table:
                    ok = True
                    break
            if ok:
                best = run
                break
        if best is None:
            first_in_line = not marks and len(before) <= 5 and not re.search(r"[0-9]", " ".join(w[2] for w in before))
            if first_in_line and line[c] == ":":
                best = before
            else:
                continue
        ls = best[0][0]
        if line[c] != ":":
            continue
        marks.append((ls, c, c + 1))
        used = c + 1
    segs: list[tuple[int, int, int, int]] = []
    pos = 0
    if not marks:
        return [(0, 0, 0, len(line))]
    if marks[0][0] > 0 and line[:marks[0][0]].strip():
        segs.append((0, 0, 0, marks[0][0]))
    for i, (ls, le, vs) in enumerate(marks):
        ve = marks[i + 1][0] if i + 1 < len(marks) else len(line)
        segs.append((ls, le, vs, ve))
    return segs


def _label_mode(label_en: str) -> str:
    if label_en in _NAME_LABELS:
        return "name"
    if label_en in _PLACE_LABELS:
        return "place"
    if label_en == "Accused":
        return "name"
    return ""


def _translate_slice(text: str, base: int, lang: str, spans, person_spans, place_spans, mode: str = "", is_label: bool = False) -> dict[str, Any]:
    toks = _classify_sentence(text, base, lang, spans, person_spans, place_spans, mode, is_label)
    reorder = lang in ("hi", "mr", "hinglish")
    en, reordered = _reorder(toks, reorder)
    q, conf, cov, unk = _sentence_quality(toks, lang, reordered)
    return {"toks": toks, "english": en, "quality": q, "confidence": conf, "coverage": cov, "unknown": unk, "reordered": reordered}


# --------------------------------------------------------------------------- optional neural model
def neural_status() -> dict[str, Any]:
    model = os.getenv("INDICTRANS2_MODEL", "").strip()
    if not model:
        return {"available": False, "engine": "", "reason": "INDICTRANS2_MODEL is not set"}
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        from IndicTransToolkit.processor import IndicProcessor  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on the machine
        return {"available": False, "engine": "", "reason": f"model libraries not installed ({exc.__class__.__name__})"}
    return {"available": True, "engine": "indictrans2-local", "model": model, "reason": ""}


_NEURAL_CODES = {"hi": "hin_Deva", "mr": "mar_Deva", "bn": "ben_Beng", "gu": "guj_Gujr", "pa": "pan_Guru", "ta": "tam_Taml", "te": "tel_Telu", "kn": "kan_Knda", "ml": "mal_Mlym", "ur": "urd_Arab", "or": "ory_Orya"}


@lru_cache(maxsize=1)
def _load_tok(name: str):  # pragma: no cover - needs a local model
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(name, trust_remote_code=True)


@lru_cache(maxsize=1)
def _load_model(name: str):  # pragma: no cover
    from transformers import AutoModelForSeq2SeqLM
    return AutoModelForSeq2SeqLM.from_pretrained(name, trust_remote_code=True)


def _neural_translate(text: str, lang: str) -> str | None:  # pragma: no cover - needs a local model
    st = neural_status()
    code = _NEURAL_CODES.get(lang)
    if not st["available"] or not code:
        return None
    try:
        import torch
        from IndicTransToolkit.processor import IndicProcessor
        tok, model = _load_tok(st["model"]), _load_model(st["model"])
        proc = IndicProcessor(inference=True)
        batch = proc.preprocess_batch([text], src_lang=code, tgt_lang="eng_Latn")
        inputs = tok(batch, truncation=True, padding="longest", return_tensors="pt")
        with torch.no_grad():
            gen = model.generate(**inputs, num_beams=4, max_length=256)
        dec = tok.batch_decode(gen, skip_special_tokens=True)
        return proc.postprocess_batch(dec, lang="eng_Latn")[0]
    except Exception:
        return None


# --------------------------------------------------------------------------- document translation
def translate_document(text: str, language: str = "auto", entities: list | None = None) -> dict[str, Any]:
    """Sentence-level translation of a whole document. Offsets refer to the original ``text``."""
    text = text or ""
    doc_info = LID.detect(text, language or "auto")
    doc_code = doc_info["code"] if doc_info["code"] in LID.CODE_TO_NAME else "hi"
    ascii_text = N.ascii_digits(text)                      # same length as text
    spans = protected_spans(ascii_text, entities)
    person_spans = [(e["start"], e["end"]) for e in entities or [] if e.get("type") == "PERSON" and isinstance(e.get("start"), int)]
    place_spans = [(e["start"], e["end"]) for e in entities or [] if e.get("type") in ("LOCATION",) and isinstance(e.get("start"), int)]
    neural = neural_status()
    sentences: list[dict[str, Any]] = []
    pos = 0
    for line in text.split("\n"):
        line_start = pos
        pos += len(line) + 1
        if not line.strip():
            continue
        lead = len(line) - len(line.lstrip(" \t-•*"))
        for ls, le, vs, ve in _label_segments(line, doc_code):
            segs: list[tuple[str, int, str]] = []       # (kind, start, text)
            label_res = None
            mode = ""
            if le > ls:
                lab_txt = line[ls:le]
                lang_l = LID.sentence_language(lab_txt, doc_code) if LID.script_counts(lab_txt) else doc_code
                lang_l = "hi" if lang_l in ("en",) and doc_code != "en" else lang_l
                label_res = _translate_slice(lab_txt, line_start + ls, lang_l if lang_l != "en" else "hinglish", spans, person_spans, place_spans, "", True)
                mode = _label_mode(label_res["english"])
            value = line[vs:ve]
            base = line_start + vs
            for a, b in (_sentence_spans(value) or []):
                seg = value[a:b]
                if not seg.strip():
                    continue
                lead_ws = len(seg) - len(seg.lstrip())
                sl = LID.sentence_language(seg, doc_code)
                s_abs = base + a + lead_ws
                e_abs = base + b - (len(seg) - len(seg.rstrip()))
                if sl == "en":
                    if label_res is not None and a == 0 and not any(x for x in sentences if x.get("_pending")):
                        pass
                    sentences.append({"start": s_abs, "end": e_abs, "source": text[s_abs:e_abs], "english": text[s_abs:e_abs], "quality": "identity", "confidence": 1.0, "coverage": 1.0,
                                      "language": "English", "language_code": "en", "engine": "identity", "unknown": [], "tokens": [], "label": ""})
                    continue
                lang_s = sl if sl in LID.CODE_TO_NAME else doc_code
                res = _translate_slice(seg.strip(), s_abs, lang_s, spans, person_spans, place_spans, mode if a == 0 else "")
                english = _finish_text(res["english"], seg.rstrip().endswith(("।", ".")), capitalise=not (label_res is not None and a == 0))
                engine = RULE_ENGINE_ID
                quality, conf = res["quality"], res["confidence"]
                if neural["available"] and lang_s in _NEURAL_CODES:
                    out = _neural_translate(seg.strip(), lang_s)
                    ids = [t["en"] for t in res["toks"] if t["kind"] in ("id", "num") and len(t["en"]) >= 3]
                    if out and all(i in out for i in ids):
                        english, quality, conf, engine = out, "model", 0.85, neural["engine"]
                sentences.append({"start": s_abs, "end": e_abs, "source": text[s_abs:e_abs], "english": english, "quality": quality, "confidence": conf, "coverage": res["coverage"],
                                  "language": LID.CODE_TO_NAME.get(lang_s, lang_s), "language_code": lang_s, "engine": engine, "unknown": res["unknown"],
                                  "reordered": res["reordered"], "tokens": [_tok_out(t) for t in res["toks"] if t["kind"] != "punct" or t["en"] not in ("",)],
                                  "label": label_res["english"] if label_res else ""})
            if label_res is not None:
                lab_abs = line_start + ls
                lab_en = _finish_text(label_res["english"], False)
                lab_tokens = [_tok_out(t) for t in label_res["toks"]] + [_tok_out(Tok(src=":", start=line_start + le, end=line_start + le + 1, en=":", kind="punct", cat="PUNCT", note="", prep=""))]
                mine = [s for s in sentences if s["start"] >= line_start + vs and s["end"] <= line_start + ve]
                first = min(mine, key=lambda s: s["start"]) if mine else None
                if first is not None:
                    first["english"] = f"{lab_en}: {first['english']}"
                    first["source"] = text[lab_abs:first["end"]]
                    first["start"] = lab_abs
                    first["tokens"] = lab_tokens + first["tokens"]
                    first["unknown"] = label_res["unknown"] + first["unknown"]
                    first["label"] = lab_en
                    if first["engine"] == "identity":
                        first["engine"] = RULE_ENGINE_ID
                        first["language"] = LID.CODE_TO_NAME.get(doc_code, "")
                    if first["quality"] == "identity" or first["quality"] == "glossary" and label_res["quality"] != "glossary" and label_res["quality"] != "identity":
                        first["quality"], first["confidence"], first["coverage"] = label_res["quality"], label_res["confidence"], label_res["coverage"]
                else:
                    sentences.append({"start": lab_abs, "end": line_start + le + 1, "source": text[lab_abs:line_start + le + 1], "english": lab_en + ":", "quality": label_res["quality"], "confidence": label_res["confidence"],
                                      "coverage": label_res["coverage"], "language": "", "language_code": doc_code, "engine": RULE_ENGINE_ID, "unknown": label_res["unknown"],
                                      "tokens": lab_tokens, "label": lab_en})
    sentences.sort(key=lambda s: s["start"])
    for i, s in enumerate(sentences):
        s["index"] = i
    return _document_result(text, sentences, doc_info, neural)


def _tok_out(t: Tok) -> dict[str, Any]:
    return {"src": t["src"], "en": t["en"], "kind": t["kind"], "start": t["start"], "end": t["end"], "note": t["note"]}


def _document_result(text: str, sentences: list[dict[str, Any]], doc_info: dict[str, Any], neural: dict[str, Any]) -> dict[str, Any]:
    translated = [s for s in sentences if s["engine"] != "identity"]
    weights = [max(1, len([t for t in s["tokens"] if t["kind"] in ("gloss", "phrase", "infl", "unknown")])) for s in translated]
    if translated:
        conf = sum(s["confidence"] * w for s, w in zip(translated, weights)) / sum(weights)
        cov = sum(s["coverage"] * w for s, w in zip(translated, weights)) / sum(weights)
    else:
        conf, cov = 1.0, 1.0
    unknown: dict[str, int] = {}
    for s in translated:
        for u in s["unknown"]:
            unknown[u] = unknown.get(u, 0) + 1
    counts: dict[str, int] = {}
    for s in sentences:
        counts[s["quality"]] = counts.get(s["quality"], 0) + 1
    engines: dict[str, int] = {}
    for s in sentences:
        engines[s["engine"]] = engines.get(s["engine"], 0) + 1
    used_model = engines.get(neural.get("engine", "@@"), 0) > 0
    if not translated:
        engine_label, engine_id = "No translation needed (text is already English)", "identity"
    elif used_model:
        engine_label = f"Local neural model ({neural.get('model', '')}) with the offline rule engine as fallback for sentences where the model dropped an identifier"
        engine_id = neural["engine"] + "+" + RULE_ENGINE_ID
    else:
        engine_label, engine_id = RULE_ENGINE_LABEL, RULE_ENGINE_ID
    # English text preserving line structure
    lines: dict[int, list[str]] = {}
    for s in sentences:
        ln = text.count("\n", 0, s["start"])
        lines.setdefault(ln, []).append(s["english"])
    total_lines = text.count("\n") + 1
    full = "\n".join(" ".join(lines.get(i, [])) for i in range(total_lines))
    return {"sentences": sentences, "text": full.strip("\n"), "confidence": round(conf, 3), "coverage": round(cov, 3), "unknown_words": [{"word": w, "count": c} for w, c in sorted(unknown.items(), key=lambda kv: -kv[1])],
            "quality_counts": counts, "engine": engine_label, "engine_id": engine_id, "neural": used_model, "neural_available": neural["available"],
            "neural_note": "" if neural["available"] else f"No local neural model in use ({neural['reason']}); the rule engine ran alone.", "language": doc_info}


# --------------------------------------------------------------------------- field-level translation (legacy API kept)
_IDENT_KEYS = {"fir_number", "year", "fir_date", "fir_time", "occurrence_date", "occurrence_time_from", "occurrence_time_to", "general_diary_entry", "sections", "serial_numbers",
               "phones", "vehicles", "accounts", "upi_ids", "emails", "aadhaar_numbers", "pan_numbers", "ifsc_codes", "dates", "dates_iso", "document_type"}
_NAME_KEYS = {"complainant", "father_name", "father_name_raw", "investigating_officer", "witnesses", "accused_names", "victims"}
_PLACE_KEYS = {"district", "police_station", "place_of_occurrence", "complainant_address"}
_SKIP_KEYS = {"language", "detected_events", "field_confidence", "field_evidence", "section_details", "amounts"}


def _has_indic(s: str) -> bool:
    return any(TL.script_of(c) not in (None, "latn") for c in s)


def transliterate_devanagari(text: str) -> str:
    """Backward-compatible name: transliterate every Indian-script word (all supported scripts) into Latin letters."""
    return TL.transliterate(text)


def glossary_translate(text: str, language: str = "auto") -> str:
    """Backward-compatible: translate a short text to English with the rule engine (unknown words marked)."""
    if not text:
        return ""
    return translate_document(text, language)["text"]


def translate_field_value(key: str, value: str, language: str, entities: list | None = None) -> dict[str, Any]:
    """Translate one extracted field while preserving identifiers and transliterating names."""
    value = "" if value is None else str(value)
    base = {"engine": RULE_ENGINE_ID}
    if not value:
        return {"text": "", "method": "empty", "confidence": 1.0, "warning": "", "quality": "identity", **base}
    if key in _IDENT_KEYS or key.endswith("_iso") or not _has_indic(value) and (language in ("English", "en") or key in _NAME_KEYS | _PLACE_KEYS):
        return {"text": value, "method": "preserve-identifier" if key in _IDENT_KEYS or key.endswith("_iso") else "identity", "confidence": 0.99, "warning": "", "quality": "identity", **base}
    if key in _NAME_KEYS:
        out = TL.title_name(TL.transliterate(value, title=False)) if _has_indic(value) else value
        return {"text": out, "method": "name-transliteration", "confidence": 0.85, "quality": "rule-based",
                "warning": "Names are transliterated (spelled in English letters), not translated; verify the spelling against the source.", **base}
    lang = language
    code = LID.NAME_TO_CODE.get((language or "").lower(), "auto")
    sl = LID.sentence_language(value, code if code in LID.CODE_TO_NAME else "hi")
    if sl == "en":
        return {"text": value, "method": "identity", "confidence": 1.0, "warning": "", "quality": "identity", **base}
    mode = "place" if key in _PLACE_KEYS else ("name" if key == "accused_description" else "")
    ascii_v = N.ascii_digits(value)
    spans = protected_spans(ascii_v, entities=None)
    res = _translate_slice(value, 0, sl, spans, [], [], mode)
    text = _finish_text(res["english"], False)
    if key in ("stolen_property", "property_items") and text:
        text = text[:1].upper() + text[1:]
    warn = "" if res["quality"] in ("glossary",) else "Highlighted (⟦ ⟧) words are not in the glossary; check them against the source."
    return {"text": text, "method": f"{RULE_ENGINE_ID}:{res['quality']}", "confidence": res["confidence"], "warning": warn, "quality": res["quality"], "unknown": res["unknown"], **base}


def translate_text_to_english(text: str, language: str) -> dict[str, Any]:
    """Legacy entry point: translate free text; returns text/method/confidence/warning plus sentence-level detail."""
    if not text:
        return {"text": "", "method": "identity", "confidence": 1.0, "warning": "", "engine": "identity", "sentences": []}
    r = translate_document(text, language)
    warn = "" if r["engine_id"] == "identity" else (r["neural_note"] or "Machine translation is assistive; verify names, legal terms and places against the source.")
    return {"text": r["text"], "method": r["engine_id"], "confidence": r["confidence"], "warning": warn, "engine": r["engine"], "sentences": r["sentences"], "unknown_words": r["unknown_words"]}


def translate_structured_to_english(structured: dict[str, Any], language: str, text: str | None = None, entities: list | None = None) -> dict[str, Any]:
    """English view of an analysed document: per-field translations plus (when ``text`` is given) a sentence-level side-by-side."""
    if not structured and not text:
        return {"fields": {}, "narrative": "", "method": "identity", "confidence": 1.0, "warning": "", "engine": "identity", "engine_id": "identity", "sentences": [], "text": ""}
    fields: dict[str, Any] = {}
    methods: list[str] = []
    confs: list[float] = []
    warnings: list[str] = []
    unknown_all: dict[str, int] = {}
    narrative = ""

    def one(key, val):
        r = translate_field_value(key, str(val), language, entities)
        methods.append(r["method"])
        confs.append(r["confidence"])
        if r.get("warning"):
            warnings.append(r["warning"])
        for u in r.get("unknown", []):
            unknown_all[u] = unknown_all.get(u, 0) + 1
        return r["text"]
    for key, value in (structured or {}).items():
        if key in _SKIP_KEYS or key.startswith("_"):
            continue
        if isinstance(value, list):
            if value and isinstance(value[0], dict):
                continue
            fields[key] = [one(key, v) for v in value]
        elif isinstance(value, dict):
            fields[key] = {nk: (one(nk, nv) if not isinstance(nv, (list, dict)) else nv) for nk, nv in value.items()}
        elif isinstance(value, (int, float)):
            fields[key] = value
        else:
            fields[key] = one(key, value or "")
            if key == "incident_description":
                narrative = fields[key]
    doc = translate_document(text, language, entities) if text is not None else None
    if doc is None:
        engine_label, engine_id = RULE_ENGINE_LABEL, RULE_ENGINE_ID
        sentences, full, conf = [], "", (round(sum(confs) / len(confs), 3) if confs else 1.0)
        cov, unknown_words, neural, note, counts = 1.0, [{"word": w, "count": c} for w, c in unknown_all.items()], False, "", {}
        lang_info = {}
    else:
        engine_label, engine_id, sentences, full, conf, cov = doc["engine"], doc["engine_id"], doc["sentences"], doc["text"], doc["confidence"], doc["coverage"]
        unknown_words, neural, note, counts, lang_info = doc["unknown_words"], doc["neural"], doc["neural_note"], doc["quality_counts"], doc["language"]
        if not narrative:
            narrative = " ".join(s["english"] for s in sentences[:6])
    warning = ""
    if engine_id != "identity":
        warning = (note + " " if note else "") + "This English view is a machine aid; check names, sections, amounts and places against the original before relying on it."
        if unknown_words:
            warning += f" {len(unknown_words)} word(s) were not understood and are highlighted in the English view (marked with ⟦ ⟧ in the text)."
        warning = warning.strip()
    return {
        "fields": fields, "narrative": narrative, "text": full, "sentences": sentences,
        "method": engine_id, "engine": engine_label, "engine_id": engine_id, "neural": neural,
        "confidence": conf, "coverage": cov, "quality_counts": counts, "unknown_words": unknown_words,
        "warning": warning, "source_language": language, "detected_language": lang_info, "target_language": "English",
    }
