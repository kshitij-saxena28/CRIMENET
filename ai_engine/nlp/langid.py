"""Language identification for FIRs and complaints: script counts plus function-word heuristics.

* Script decides the family (Devanagari, Bengali, Gurmukhi, Gujarati, Odia, Tamil, Telugu, Kannada, Malayalam,
  Arabic/Urdu, Latin).
* Devanagari is Hindi or Marathi (or a mix): decided by grammar words that differ (है/हैं/था vs आहे/होते/नाही, में/को vs मध्ये/ला).
* Latin text is English, or romanised Hindi ("Hinglish") when common Hindi function words (hai, ka, ki, ne, ko, mein, tha ...)
  make up a real share of the words.  Short forms shared with English (do, me, is, the) are not counted.
* A per-sentence version (``sentence_language``) lets one document mix languages: a Hindi FIR with English place names
  or an English FIR with a Hindi quotation is translated sentence by sentence.

Everything is offline and deterministic.  The result always lists the runner-up candidates so the UI can ask the officer.
"""
from __future__ import annotations

import re
from typing import Any

from ai_engine.nlp.translit import BLOCKS, SCRIPT_LABEL, script_of

CODE_TO_NAME = {"en": "English", "hi": "Hindi", "hinglish": "Hinglish", "mr": "Marathi", "bn": "Bengali", "gu": "Gujarati", "pa": "Punjabi", "or": "Odia",
                "ta": "Tamil", "te": "Telugu", "kn": "Kannada", "ml": "Malayalam", "ur": "Urdu", "unknown": "Unknown"}
NAME_TO_CODE = {v.lower(): k for k, v in CODE_TO_NAME.items()}
NAME_TO_CODE.update({"auto": "auto", "punjabi": "pa", "gurmukhi": "pa", "oriya": "or", "romanised hindi": "hinglish", "romanized hindi": "hinglish", "hindi (romanised)": "hinglish"})
SCRIPT_TO_CODE = {"beng": "bn", "guru": "pa", "gujr": "gu", "orya": "or", "taml": "ta", "telu": "te", "knda": "kn", "mlym": "ml", "arab": "ur"}

_HI_DEV = set("है हैं था थी थे थीं में को से का की के ने पर और नहीं किया गया गई गए हुआ हुई हुए तो भी यह वह इस उस कि जो कर करके लिए साथ बाद तक द्वारा रहा रही रहे दिया लिया अपने अपनी उन्होंने उसने मैंने हमने".split())
_MR_DEV = set("आहे आहेत होते होता होती नाही नाहीत केले केला केली झाले झाला झाली आले आला मध्ये यांनी यांना त्याने तिने त्यांनी मला माझ्या माझे माझी आम्ही तुम्ही च्या ची चे चा ला ना ने ात वर साठी पासून कडे आणि किंवा तसेच यांच्या त्याच्या त्यांच्या सुमारे रोजी".split())
_MR_ONLY = set("आहे आहेत होते नाही केले केला केली झाले झाला झाली आले मध्ये यांनी त्याने तिने त्यांनी मला माझ्या माझे आम्ही आणि सुमारे रोजी तक्रारदार पोलीस ठाणे कलम गुन्हा दाखल साक्षीदार मालमत्ता".split())
_HI_ONLY = set("है हैं था थी थे में को से का की के ने नहीं किया गया गई गए हुआ हुई हुए और यह वह इस उस उन्होंने उसने मैंने हमने शिकायतकर्ता पुलिस थाना जिला धारा मामला दर्ज संपत्ति".split())

# romanised Hindi function words that are not ordinary English words
_HX = set("""hai hain tha thi thay thee the_ ka ki ke ko se mein mai main ne par pe aur nahi nahin nhi kiya kia gaya gayi gaye gya hua hui hue
kar karke karne kiye karna wo woh usne unhone maine humne hum mera meri mere apna apni apne uska uski uske unka unki unke iska iski iske
ek teen chaar paanch raat subah shaam dopahar baje bhi lekin isliye kyunki kyon jab tab phir fir tak liye saath baad pehle pahle wale wala wali
hamare tumhare unko usko isko mujhe mujhko humko apko aapko aap tum tumne yeh ye vo unhe usme isme unme wahan yahan kahan kaun kya kaise kitna
chori dhokhadhadi thana shikayat shikayatkarta dinank tarikh aaropi aropi mobile_ paise rupaye rupay rupaiye ghar dukaan dukan gaadi gadi bike_
dhamki dhamkaya maarpeet marpit lekar dekha dekhi suna bola bolta boli bataya bulaya bhaag bhaga bhagg fraud_ sandigdh gawah gavah pidit
hoga hogi honge raha rahi rahe rahega diya diye liya liye chalu chala chali gayab nikal nikala nikali""".replace("\n", " ").split())
_HX_AMBIG = {"the_", "mobile_", "bike_", "fraud_"}
_HX.difference_update(_HX_AMBIG)
_EN = set("""the of and to in is was were at on by for with from that this it he she they his her their has have had be been are not no as an or a
i we you who which when where after before while during about into onto over under also but if then there than so such one two three""".split())

_TOKEN = re.compile(r"[A-Za-z]+")
_TOK_ANY = re.compile(r"[^\W\d_]+", re.UNICODE)


def script_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ch in text or "":
        s = script_of(ch)
        if s:
            counts[s] = counts.get(s, 0) + 1
    return counts


def _dev_scores(text: str) -> tuple[float, float]:
    toks = re.findall(r"[ऀ-ॿ]+", text)
    hi = mr = 0.0
    for t in toks:
        if t in _MR_ONLY:
            mr += 2.5
        elif t in _HI_ONLY:
            hi += 2.5
        elif t in _MR_DEV:
            mr += 1
        elif t in _HI_DEV:
            hi += 1
        # Marathi inflection: postposition glued to noun (…ात, …वर, …ला, …च्या, …ाने)
        elif len(t) > 3 and re.search(r"(ाच्या|ांच्या|ाचा|ाची|ाचे|ांनी|ाने|ावर|ाला|ात|ास|ाकडे|ातून|साठी)$", t):
            mr += 0.6
        elif len(t) > 3 and re.search(r"(ों|ें|ीं)$", t):
            hi += 0.6
    if "ळ" in text:
        mr += 1.5
    return hi, mr


def latin_profile(text: str) -> dict[str, Any]:
    words = [w.lower() for w in _TOKEN.findall(text or "")]
    n = len(words)
    hx = sum(1 for w in words if w in _HX)
    en = sum(1 for w in words if w in _EN)
    return {"words": n, "hindi_function": hx, "english_function": en,
            "hindi_share": round(hx / n, 3) if n else 0.0, "english_share": round(en / n, 3) if n else 0.0}


def sentence_language(sentence: str, doc_hint: str = "") -> str:
    """Code for one sentence: en / hinglish / hi / mr / other script code."""
    counts = script_counts(sentence)
    total = sum(v for k, v in counts.items())
    if not total:
        return "en"
    indic = {k: v for k, v in counts.items() if k != "latn"}
    top_ind = max(indic, key=indic.get) if indic else None
    if top_ind and indic[top_ind] >= 0.4 * total:
        if top_ind == "deva":
            hi, mr = _dev_scores(sentence)
            if mr > hi:
                return "mr"
            if hi > mr:
                return "hi"
            return doc_hint if doc_hint in ("hi", "mr") else "hi"
        return SCRIPT_TO_CODE.get(top_ind, "unknown")
    prof = latin_profile(sentence)
    if prof["hindi_function"] >= 2 and prof["hindi_share"] >= max(0.10, prof["english_share"] * 0.6):
        return "hinglish"
    if prof["hindi_function"] >= 1 and prof["words"] <= 4 and prof["english_function"] == 0:
        return "hinglish"
    return "en"


def detect(text: str, requested: str = "auto") -> dict[str, Any]:
    text = text or ""
    counts = script_counts(text)
    letters = sum(counts.values())
    shares = {k: (v / letters if letters else 0.0) for k, v in counts.items()}
    scores: dict[str, float] = {}
    notes: list[str] = []
    prof = latin_profile(text)
    dev_hi = dev_mr = 0.0
    if counts.get("deva"):
        dev_hi, dev_mr = _dev_scores(text)
        tot = dev_hi + dev_mr
        share = shares.get("deva", 0.0)
        if tot == 0:
            scores["hi"] = share * 0.55
            scores["mr"] = share * 0.45
            notes.append("Devanagari script but no Hindi/Marathi grammar words found; assuming Hindi.")
        else:
            scores["hi"] = share * dev_hi / tot
            scores["mr"] = share * dev_mr / tot
    for sc, code in SCRIPT_TO_CODE.items():
        if counts.get(sc):
            scores[code] = shares[sc]
    if counts.get("latn"):
        share = shares["latn"]
        hx_ratio = prof["hindi_function"] / prof["words"] if prof["words"] else 0.0
        romanised = prof["hindi_function"] >= 3 and hx_ratio >= max(0.08, prof["english_share"] * 0.5 * (1 if prof["english_share"] < 0.4 else 1.4))
        if romanised:
            scores["hinglish"] = share * min(1.0, 0.55 + hx_ratio * 2)
            scores["en"] = share * max(0.0, 1 - min(1.0, 0.55 + hx_ratio * 2))
        else:
            scores["en"] = share
    scores = {k: round(v, 4) for k, v in scores.items() if v > 0}
    req = (requested or "auto").strip().lower()
    req_code = NAME_TO_CODE.get(req, "auto")
    if scores:
        best = max(scores, key=scores.get)
    else:
        best = "unknown"
    # a text with real Latin content and a Devanagari header stays "the majority script"; mixed is reported, not hidden
    candidates = [k for k, v in sorted(scores.items(), key=lambda kv: -kv[1]) if v >= 0.08]
    if not candidates and best != "unknown":
        candidates = [best]
    mixed = len([c for c in candidates if c]) > 1
    conf = scores.get(best, 0.0)
    if best == "unknown" or letters < 12:
        conf = min(conf, 0.4)
    if req_code not in ("auto",) and req_code != best:
        notes.append(f"Language was set to {CODE_TO_NAME.get(req_code, requested)} by the user; the text itself looks like {CODE_TO_NAME.get(best, best)}.")
        chosen = req_code
    else:
        chosen = best
    legacy_counts = {"devanagari": counts.get("deva", 0), "latin": counts.get("latn", 0)}
    for sc, k in counts.items():
        if sc not in ("deva", "latn"):
            legacy_counts[SCRIPT_LABEL.get(sc, sc).lower()] = k
    return {
        "primary": CODE_TO_NAME.get(chosen, "Unknown"), "code": chosen, "detected": CODE_TO_NAME.get(best, "Unknown"),
        "candidates": [CODE_TO_NAME.get(c, c) for c in candidates] or [CODE_TO_NAME.get(chosen, "Unknown")],
        "candidate_codes": candidates, "script": legacy_counts, "scores": {CODE_TO_NAME.get(k, k): v for k, v in scores.items()},
        "confidence": round(conf, 3), "mixed": mixed, "romanised_hindi": best == "hinglish", "notes": notes,
        "latin_profile": prof,
    }
