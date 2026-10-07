"""Transliteration of Indian-script names and places into Latin letters (Devanagari, Bengali, Gurmukhi, Gujarati,
Odia, Tamil, Telugu, Kannada, Malayalam, and a rough Urdu/Arabic-script reader).

The Brahmic scripts share one layout (ISCII), so every letter is mapped to the Devanagari code point at the same
offset inside its Unicode block and then written in Latin letters by one routine. Script quirks (Tamil voicing,
Bengali inherent 'o', Gurmukhi gemination mark) are handled where they matter for names.

This is a *phonetic* rendering, not a translation. Output is a best-effort spelling such as "Rahul Sharma"; it is
always shown to reviewers as transliteration. Known names and places are spelled from tables (NAMES, gazetteer).
"""
from __future__ import annotations

import re

BLOCKS = {"deva": 0x0900, "beng": 0x0980, "guru": 0x0A00, "gujr": 0x0A80, "orya": 0x0B00, "taml": 0x0B80,
          "telu": 0x0C00, "knda": 0x0C80, "mlym": 0x0D00}
SCRIPT_LABEL = {"deva": "Devanagari", "beng": "Bengali", "guru": "Gurmukhi", "gujr": "Gujarati", "orya": "Odia", "taml": "Tamil",
                "telu": "Telugu", "knda": "Kannada", "mlym": "Malayalam", "arab": "Arabic/Urdu", "latn": "Latin"}
INDO_ARYAN = {"deva", "beng", "guru", "gujr", "orya"}


def script_of(ch: str) -> str | None:
    cp = ord(ch)
    for name, base in BLOCKS.items():
        if base <= cp < base + 0x80:
            return name
    if 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or 0xFB50 <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF:
        return "arab"
    if ch.isascii() and ch.isalpha():
        return "latn"
    return None


def word_script(word: str) -> str | None:
    counts: dict[str, int] = {}
    for ch in word:
        s = script_of(ch)
        if s:
            counts[s] = counts.get(s, 0) + 1
    return max(counts, key=counts.get) if counts else None


# --------------------------------------------------------------------------- Brahmic -> Devanagari code points
_SPECIAL_TO_DEVA = {
    # Bengali: য is pronounced 'j' (য় is 'y'); ৎ is a bare 't'
    "য": "ज", "ৎ": "त", "ৰ": "र", "ৱ": "व",
    # Malayalam chillu letters (consonants without a vowel)
    "ൺ": "ण्", "ൻ": "न्", "ർ": "र्", "ൽ": "ल्", "ൾ": "ळ्", "ൿ": "क्",
    # Tamil grantha / aytham
    "ஃ": "ः",
    "\u0A70": "\u0902",
    # Gurmukhi addak (doubles next consonant) handled in code; Gurmukhi udaat/yakash ignored
}
_BENG_YA_YA = "য়"


def to_devanagari(text: str) -> list[tuple[str, str]]:
    """Return [(devanagari_char_or_str, script)] with 1 entry per source char."""
    out = []
    for ch in text:
        s = script_of(ch)
        if s in BLOCKS and s != "deva":
            if ch in _SPECIAL_TO_DEVA:
                out.append((_SPECIAL_TO_DEVA[ch], s))
                continue
            cp = ord(ch) - BLOCKS[s] + 0x0900
            out.append((chr(cp), s))
        else:
            out.append((ch, s or ""))
    return out


# --------------------------------------------------------------------------- Devanagari -> Latin
_CONS = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
         "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
         "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v",
         "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ळ": "l", "ऴ": "zh", "ऱ": "r", "ऩ": "n", "ऌ": "l",
         "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f", "य़": "y"}
_CONS["क़"], _CONS["ख़"], _CONS["ग़"], _CONS["ज़"], _CONS["ड़"], _CONS["ढ़"], _CONS["फ़"], _CONS["य़"] = "q", "kh", "g", "z", "r", "rh", "f", "y"
_VOWELS = {"अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऍ": "e", "ऑ": "o", "ऎ": "e", "ऒ": "o"}
_MATRA = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॅ": "e", "ॉ": "o", "ॆ": "e", "ॊ": "o"}
_VIRAMA = "्"
_NUKTA = "़"
_NASAL = {"ं", "ँ", "ः"}
_DIGITS_DEV = {chr(0x0966 + i): str(i) for i in range(10)}
_LABIAL = ("p", "b", "m")
_TAMIL_STOPS = {"k": "g", "ch": "s", "t": "d", "p": "b"}


def _syllables(chars: list[str], script: str):
    """Split a run of Devanagari-coded letters into syllables: dict(cons=[latin...], vowel=str|None, nasal=str)."""
    syl = []
    i, n = 0, len(chars)
    while i < n:
        ch = chars[i]
        if ch in _VOWELS:
            syl.append({"cons": [], "vowel": _VOWELS[ch], "nasal": "", "explicit": True, "indep": True})
            i += 1
        elif ch in _CONS or (i + 1 < n and chars[i + 1] == _NUKTA and ch + _NUKTA in _CONS):
            if i + 1 < n and chars[i + 1] == _NUKTA and ch + _NUKTA in _CONS:
                cons = [_CONS[ch + _NUKTA]]; i += 2
            else:
                cons = [_CONS[ch]]; i += 1
            raw = [ch]
            while i + 1 < n and chars[i] == _VIRAMA and (chars[i + 1] in _CONS):
                cons.append(_CONS[chars[i + 1]]); raw.append(chars[i + 1]); i += 2
                if i < n and chars[i] == _NUKTA:
                    i += 1
            if cons[:2] == ["k", "sh"] and len(cons) == 2:
                cons = ["ksh"]
            if cons[:2] == ["j", "n"] and len(cons) == 2 and raw == ["ज", "ञ"]:
                cons = ["gy"]
            if i < n and chars[i] == _VIRAMA:          # word-final / pre-punctuation virama: consonant without vowel
                i += 1
                syl.append({"cons": cons, "vowel": "", "nasal": "", "explicit": True, "indep": False, "dead": True})
            elif i < n and chars[i] in _MATRA:
                syl.append({"cons": cons, "vowel": _MATRA[chars[i]], "nasal": "", "explicit": True, "indep": False}); i += 1
            else:
                syl.append({"cons": cons, "vowel": None, "nasal": "", "explicit": False, "indep": False})
        elif ch in _NASAL:
            if syl:
                syl[-1]["nasal"] = "h" if ch == "ः" else "N"
            i += 1
        elif ch == _NUKTA or ch == "ऽ":
            i += 1
        else:
            syl.append({"lit": ch}); i += 1
    return syl


def _render(syl: list[dict], script: str) -> str:
    indo = script in INDO_ARYAN
    # schwa deletion (Indo-Aryan): right to left, delete an inherent 'a' at word end or before a syllable with an explicit vowel
    if indo:
        letters = [k for k, s in enumerate(syl) if "lit" not in s]
        for pos in range(len(letters) - 1, -1, -1):
            k = letters[pos]
            s = syl[k]
            if s.get("explicit") or s.get("dead") or s["nasal"]:
                continue
            if pos == len(letters) - 1:
                if pos > 0 or len(s["cons"]) > 1:
                    s["vowel"] = ""
                continue
            nxt = syl[letters[pos + 1]]
            prv = syl[letters[pos - 1]] if pos > 0 else None
            nxt_ok = not nxt.get("indep") and not nxt.get("dead") and nxt.get("vowel") != ""
            prv_ok = prv is not None and not prv.get("dead") and prv.get("vowel") != ""
            if nxt_ok and prv_ok:
                s["vowel"] = ""
    a_char = "o" if script == "beng" else "a"
    out: list[str] = []
    prev_vowel_final = False
    prev_cons_last = ""
    for idx, s in enumerate(syl):
        if "lit" in s:
            out.append(s["lit"]); prev_vowel_final = False; prev_cons_last = ""
            continue
        cons = list(s["cons"])
        if script == "taml" and len(cons) == 1 and cons[0] in _TAMIL_STOPS:
            c = cons[0]
            if prev_vowel_final and idx > 0:
                cons[0] = _TAMIL_STOPS[c]
            elif prev_cons_last in ("n", "m") and prev_cons_last:
                cons[0] = {"k": "g", "ch": "j", "t": "d", "p": "b"}[c]
        v = s["vowel"]
        if v is None:
            v = a_char
        if s.get("indep") is False and s.get("dead"):
            v = ""
        out.append("".join(cons) + v)
        if s["nasal"] == "h":
            out.append("h")
        elif s["nasal"] == "N":
            nxt_c = ""
            for t in syl[idx + 1:]:
                if "lit" in t:
                    break
                nxt_c = (t["cons"][0] if t["cons"] else "")
                break
            out.append("m" if (nxt_c and nxt_c[0] in "pbm") or (not nxt_c and script in ("telu", "knda", "mlym", "taml")) else "n")
        prev_vowel_final = bool(v) and not s.get("dead")
        prev_cons_last = "" if prev_vowel_final else (cons[-1] if cons else "")
    return "".join(out)


_NAME_TITLES = {}


def _clean_latin(w: str) -> str:
    w = w.replace("aa", "a").replace("ii", "i").replace("uu", "u")
    w = re.sub(r"(.)\1\1+", r"\1\1", w)
    return w


def brahmic_word(word: str) -> str:
    """Transliterate one word written in a Brahmic script."""
    pairs = to_devanagari(word)
    if not pairs:
        return word
    script = next((s for _, s in pairs if s in BLOCKS), "deva")
    chars: list[str] = []
    geminate = False
    for ch, s in pairs:
        if s == "guru" and ch == "ੱ":            # addak: double the next consonant
            geminate = True
            continue
        if ch in _DIGITS_DEV:
            chars.append(_DIGITS_DEV[ch]); continue
        for c in ch:
            chars.append(c)
            if geminate and c in _CONS:
                chars.append(_VIRAMA); chars.append(c); geminate = False
    # keep runs of non-Brahmic characters (digits/Latin) intact
    result = []
    run: list[str] = []

    def flush():
        if run:
            result.append(_render(_syllables(run, script), script)); run.clear()
    for c in chars:
        if "ऀ" <= c <= "ॿ":
            run.append(c)
        else:
            flush(); result.append(c)
    flush()
    return _clean_latin("".join(result))


# --------------------------------------------------------------------------- Arabic script (Urdu) - rough
_AR = {"ب": "b", "پ": "p", "ت": "t", "ٹ": "t", "ث": "s", "ج": "j", "چ": "ch", "ح": "h", "خ": "kh", "د": "d", "ڈ": "d", "ذ": "z", "ر": "r", "ڑ": "r",
       "ز": "z", "ژ": "zh", "س": "s", "ش": "sh", "ص": "s", "ض": "z", "ط": "t", "ظ": "z", "غ": "gh", "ف": "f", "ق": "q", "ک": "k", "ك": "k",
       "گ": "g", "ل": "l", "م": "m", "ن": "n", "ں": "n", "ہ": "h", "ه": "h", "ة": "h", "ۃ": "h", "ھ": "h"}
_AR_V = {"ا": "a", "آ": "a", "أ": "a", "إ": "i", "ع": "a", "ئ": "i", "ء": ""}
_AR_DIA = {"َ": "a", "ِ": "i", "ُ": "u"}


def arabic_word(word: str) -> str:
    units: list[tuple[str, str]] = []   # (kind, latin) kind in C, V
    i = 0
    n = len(word)
    while i < n:
        ch = word[i]
        if ch in _AR:
            if ch == "ھ" and units and units[-1][0] == "C":
                units[-1] = ("C", units[-1][1] + "h")
            else:
                units.append(("C", _AR[ch]))
        elif ch in _AR_V:
            v = _AR_V[ch]
            if v:
                units.append(("V", v))
        elif ch == "و":
            units.append(("V", "u") if units and units[-1][0] == "C" else ("C", "v"))
        elif ch in ("ی", "ي", "ى"):
            if i == n - 1:
                units.append(("V", "i"))
            elif units and units[-1][0] == "C":
                units.append(("V", "i"))
            else:
                units.append(("C", "y"))
        elif ch in ("ے", "ۓ"):
            units.append(("V", "e"))
        elif ch in _AR_DIA:
            units.append(("V", _AR_DIA[ch]))
        elif ch == "ّ" and units:
            units.append((units[-1][0], units[-1][1]))
        elif "٠" <= ch <= "٩":
            units.append(("D", str(ord(ch) - 0x0660)))
        elif "۰" <= ch <= "۹":
            units.append(("D", str(ord(ch) - 0x06F0)))
        elif ch in "ًٌٍْٰ":
            pass
        else:
            units.append(("L", ch))
        i += 1
    out = []
    for k, (kind, lat) in enumerate(units):
        out.append(lat)
        nxt = units[k + 1] if k + 1 < len(units) else None
        if kind == "C" and nxt and nxt[0] == "C" and lat != "":
            out.append("a")
    return "".join(out)


# --------------------------------------------------------------------------- public API
NAMES = {
    # surnames / common words that plain phonetics spell badly (Devanagari; the same words are found in other scripts by
    # transliterating to a skeleton key - see name_lookup)
    "सिंह": "Singh", "सिंग": "Singh", "कुमार": "Kumar", "शर्मा": "Sharma", "वर्मा": "Verma", "गुप्ता": "Gupta", "गुप्त": "Gupta", "यादव": "Yadav",
    "पांडेय": "Pandey", "पांडे": "Pandey", "पाण्डेय": "Pandey", "मिश्रा": "Mishra", "मिश्र": "Mishra", "अग्रवाल": "Agarwal", "अग्रवाला": "Agarwal", "राजपूत": "Rajput",
    "चौहान": "Chauhan", "चौधरी": "Chaudhary", "ठाकुर": "Thakur", "तिवारी": "Tiwari", "त्रिपाठी": "Tripathi", "दुबे": "Dubey", "जोशी": "Joshi",
    "पटेल": "Patel", "पाटील": "Patil", "शिंदे": "Shinde", "जाधव": "Jadhav", "पवार": "Pawar", "देशमुख": "Deshmukh", "कुलकर्णी": "Kulkarni",
    "गायकवाड": "Gaikwad", "मोरे": "More", "भोसले": "Bhosale", "कांबळे": "Kamble", "खान": "Khan", "अहमद": "Ahmad", "अली": "Ali",
    "हुसैन": "Hussain", "शेख": "Sheikh", "मोहम्मद": "Mohammad", "मुहम्मद": "Mohammad", "श्रीवास्तव": "Srivastava", "सक्सेना": "Saxena",
    "कपूर": "Kapoor", "मल्होत्रा": "Malhotra", "सिन्हा": "Sinha", "झा": "Jha", "राव": "Rao", "रेड्डी": "Reddy", "नायर": "Nair", "देवी": "Devi",
    "प्रसाद": "Prasad", "रामप्रसाद": "Ramprasad", "राम": "Ram", "श्याम": "Shyam", "कृष्ण": "Krishna", "लाल": "Lal", "चंद": "Chand", "चंद्र": "Chandra",
    "नाथ": "Nath", "दास": "Das", "घोष": "Ghosh", "बनर्जी": "Banerjee", "मुखर्जी": "Mukherjee", "चटर्जी": "Chatterjee", "बोस": "Bose", "सेन": "Sen",
    "राहुल": "Rahul", "अमित": "Amit", "सुनील": "Sunil", "अनिल": "Anil", "विनोद": "Vinod", "रमेश": "Ramesh", "सुरेश": "Suresh", "महेश": "Mahesh",
    "संजय": "Sanjay", "अजय": "Ajay", "विजय": "Vijay", "विक्रम": "Vikram", "आलोक": "Alok", "अशोक": "Ashok", "कमला": "Kamla", "सुनीता": "Sunita",
    "अनिता": "Anita", "श्रीमती": "Smt.", "श्री": "Shri", "कुमारी": "Kumari", "पुत्र": "S/o", "पुत्री": "D/o",
}
_NAME_SKELETONS: dict[str, str] = {}


def transliterate_word(word: str) -> str:
    """One word in any supported script -> Latin. Latin/digits are returned unchanged."""
    if not word:
        return word
    if word in NAMES:
        return NAMES[word]
    sc = word_script(word)
    if sc == "arab":
        out = arabic_word(word)
        key = skeleton(out)
        return _name_index().get(key, out) if len(key) >= 3 else out
    if sc in BLOCKS:
        out = brahmic_word(word)
        if sc != "deva":
            # known surnames written in other scripts: match on consonant skeleton of the Devanagari-spelt name
            key = skeleton(out)
            if len(key) >= 3 and key in _name_index():
                return _name_index()[key]
        return out
    return word


_IDX: dict[str, str] | None = None


def _name_index() -> dict[str, str]:
    global _IDX
    if _IDX is None:
        _IDX = {}
        for dev, eng in NAMES.items():
            if len(eng) >= 4 and not eng.endswith(".") and "/" not in eng:
                _IDX.setdefault(skeleton(eng), eng)
        # skeleton collisions that are ambiguous are dropped
        seen: dict[str, set] = {}
        for dev, eng in NAMES.items():
            seen.setdefault(skeleton(eng), set()).add(eng)
        for k, v in seen.items():
            if len(v) > 1:
                _IDX.pop(k, None)
    return _IDX


def skeleton(latin: str) -> str:
    """Consonant-class skeleton used to match a transliteration with a Latin spelling (e.g. Kolkata ~ kalkata)."""
    s = re.sub(r"[^a-z]", "", (latin or "").lower())
    s = s.replace("h", "")
    s = re.sub(r"[aeiouyw]", "", s)
    table = {"k": "k", "g": "k", "q": "k", "x": "k", "c": "s", "s": "s", "z": "s", "j": "s", "t": "t", "d": "t", "p": "p", "b": "p", "f": "p", "v": "p"}
    return "".join(table.get(c, c) for c in s)


def title_name(latin: str) -> str:
    def cap(w):
        if not w or w.endswith(".") or w in ("S/o", "D/o", "W/o"):
            return w
        return w[:1].upper() + w[1:]
    return " ".join(cap(w) for w in latin.split(" "))


_WORD_RX = re.compile(r"[^\s,;:()\[\]{}\"'“”‘’|/\\]+")


def transliterate(text: str, *, title: bool = True) -> str:
    """Transliterate every non-Latin word of ``text``; Latin words, digits and punctuation are kept."""
    def rep(m):
        w = m.group(0)
        stripped = w.rstrip(".،।")
        tail = w[len(stripped):]
        core = stripped
        if word_script(core) in (None, "latn"):
            return w
        out = transliterate_word(core)
        return (title_name(out) if title else out) + tail.replace("।", ".")
    return _WORD_RX.sub(rep, text or "")
