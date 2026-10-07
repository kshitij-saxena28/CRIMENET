"""Section / offence recognition for FIR text (IPC, BNS and the special Acts that appear most often).

This is a reference aid: it finds the sections a document cites, decides which Act each belongs to, names the
offence and shows the IPC<->BNS counterpart. The offence tables are curated by hand and cover the commonly
charged sections only; anything not in a table is reported as "not in the reference table" rather than guessed.
Cross-reference numbers are base section numbers (sub-sections vary) - verify against the official gazette.
"""
from __future__ import annotations

import re
from typing import Any

from ai_engine.nlp.normalize import ascii_digits

# ---------------------------------------------------------------- IPC section -> (offence, BNS counterpart)
IPC: dict[str, tuple[str, str]] = {
    "34": ("Acts done by several persons in furtherance of common intention", "3(5)"),
    "107": ("Abetment of a thing", "45"), "109": ("Punishment of abetment", "49"),
    "120A": ("Criminal conspiracy (definition)", "61(1)"), "120B": ("Punishment of criminal conspiracy", "61(2)"),
    "141": ("Unlawful assembly", "189"), "143": ("Punishment for unlawful assembly", "189(2)"), "147": ("Rioting", "191(2)"),
    "148": ("Rioting armed with deadly weapon", "191(3)"), "149": ("Member of unlawful assembly guilty of common-object offence", "190"),
    "153A": ("Promoting enmity between groups", "196"), "171": ("Bribery at elections", "170"),
    "186": ("Obstructing public servant in discharge of public functions", "221"),
    "188": ("Disobedience to order promulgated by public servant", "223"), "191": ("Giving false evidence", "227"),
    "201": ("Causing disappearance of evidence of offence", "238"), "212": ("Harbouring an offender", "249"),
    "268": ("Public nuisance", "270"), "279": ("Rash driving or riding on a public way", "281"),
    "283": ("Danger or obstruction in public way", "285"), "285": ("Negligent conduct with fire or combustible matter", "287"),
    "290": ("Punishment for public nuisance", "292"), "294": ("Obscene acts and songs in public", "296"),
    "295A": ("Deliberate acts intended to outrage religious feelings", "299"),
    "299": ("Culpable homicide", "100"), "300": ("Murder (definition)", "101"), "302": ("Murder", "103(1)"),
    "304": ("Culpable homicide not amounting to murder", "105"), "304A": ("Causing death by negligence", "106(1)"),
    "304B": ("Dowry death", "80"), "305": ("Abetment of suicide of child or person of unsound mind", "107"),
    "306": ("Abetment of suicide", "108"), "307": ("Attempt to murder", "109"), "308": ("Attempt to commit culpable homicide", "110"),
    "309": ("Attempt to commit suicide", "226"), "312": ("Causing miscarriage", "88"), "313": ("Causing miscarriage without consent", "89"),
    "323": ("Voluntarily causing hurt", "115(2)"), "324": ("Voluntarily causing hurt by dangerous weapons or means", "118(1)"),
    "325": ("Voluntarily causing grievous hurt", "117(2)"), "326": ("Grievous hurt by dangerous weapons or means", "118(2)"),
    "326A": ("Voluntarily causing grievous hurt by acid", "124(1)"), "326B": ("Attempt to throw acid", "124(2)"),
    "328": ("Causing hurt by means of poison with intent to commit an offence", "123"),
    "336": ("Act endangering life or personal safety of others", "125"), "337": ("Causing hurt by act endangering life", "125(a)"),
    "338": ("Causing grievous hurt by act endangering life", "125(b)"), "341": ("Wrongful restraint", "126(2)"),
    "342": ("Wrongful confinement", "127(2)"), "349": ("Force", "130"), "350": ("Criminal force", "131"), "352": ("Assault or criminal force", "131"),
    "353": ("Assault or criminal force to deter public servant from duty", "132"),
    "354": ("Assault or criminal force to woman with intent to outrage her modesty", "74"), "354A": ("Sexual harassment", "75"),
    "354B": ("Assault or criminal force to woman with intent to disrobe", "76"), "354C": ("Voyeurism", "77"), "354D": ("Stalking", "78"),
    "359": ("Kidnapping", "137"), "363": ("Kidnapping (punishment)", "137(2)"), "363A": ("Kidnapping or maiming a minor for begging", "139"),
    "364": ("Kidnapping in order to murder", "140(1)"), "364A": ("Kidnapping for ransom", "140(2)"),
    "365": ("Kidnapping with intent to secretly and wrongfully confine a person", "140(3)"),
    "366": ("Kidnapping a woman to compel her marriage", "87"), "370": ("Trafficking of person", "143"),
    "375": ("Rape (definition)", "63"), "376": ("Punishment for rape", "64"), "376A": ("Rape causing death or persistent vegetative state", "66"),
    "376D": ("Gang rape", "70(1)"), "377": ("Unnatural offences", "-"), "378": ("Theft (definition)", "303(1)"), "379": ("Theft", "303(2)"),
    "380": ("Theft in dwelling house, or means of transportation or place of worship", "305"),
    "381": ("Theft by clerk or servant of property in possession of master", "306"), "382": ("Theft after preparation for causing death, hurt or restraint", "307"),
    "383": ("Extortion (definition)", "308(1)"), "384": ("Extortion", "308(2)"), "385": ("Putting person in fear of injury in order to commit extortion", "308(3)"),
    "386": ("Extortion by putting a person in fear of death or grievous hurt", "308(4)"),
    "390": ("Robbery (definition)", "309(1)"), "391": ("Dacoity (definition)", "310(1)"), "392": ("Robbery", "309(4)"),
    "393": ("Attempt to commit robbery", "309(5)"), "394": ("Voluntarily causing hurt in committing robbery", "309(6)"),
    "395": ("Dacoity", "310(2)"), "396": ("Dacoity with murder", "310(3)"), "397": ("Robbery or dacoity with attempt to cause death or grievous hurt", "309(6)"),
    "399": ("Making preparation to commit dacoity", "310"), "400": ("Belonging to gang of dacoits", "310(4)"),
    "403": ("Dishonest misappropriation of property", "314"), "405": ("Criminal breach of trust (definition)", "316(1)"),
    "406": ("Criminal breach of trust", "316(2)"), "407": ("Criminal breach of trust by carrier", "316(3)"), "408": ("Criminal breach of trust by clerk or servant", "316(4)"),
    "409": ("Criminal breach of trust by public servant, banker, merchant or agent", "316(5)"),
    "411": ("Dishonestly receiving stolen property", "317(2)"), "412": ("Dishonestly receiving property stolen in dacoity", "317(3)"),
    "413": ("Habitually dealing in stolen property", "317(4)"), "414": ("Assisting in concealment of stolen property", "317(5)"),
    "415": ("Cheating (definition)", "318(1)"), "417": ("Punishment for cheating", "318(2)"), "419": ("Cheating by personation", "319(2)"),
    "420": ("Cheating and dishonestly inducing delivery of property", "318(4)"), "425": ("Mischief", "324(1)"), "426": ("Punishment for mischief", "324(2)"),
    "427": ("Mischief causing damage of fifty rupees or more", "324(3)"), "435": ("Mischief by fire or explosive with intent to cause damage", "326(f)"),
    "436": ("Mischief by fire or explosive to destroy a house", "326(g)"), "441": ("Criminal trespass", "329(1)"), "447": ("Punishment for criminal trespass", "329(3)"),
    "448": ("House-trespass", "329(4)"), "449": ("House-trespass to commit offence punishable with death", "332(a)"), "451": ("House-trespass to commit imprisonment offence", "332(b)"),
    "452": ("House-trespass after preparation for hurt, assault or restraint", "333"), "454": ("Lurking house-trespass or house-breaking", "331(3)"),
    "457": ("Lurking house-trespass or house-breaking by night", "331(4)"), "458": ("Lurking house-trespass by night after preparation for hurt", "331(5)"),
    "460": ("Lurking house-trespass by night resulting in death or grievous hurt", "331(7)"),
    "463": ("Forgery (definition)", "336(1)"), "464": ("Making a false document", "335"), "465": ("Forgery", "336(2)"),
    "466": ("Forgery of record of court or public register", "337"), "467": ("Forgery of valuable security or will", "338"),
    "468": ("Forgery for the purpose of cheating", "336(3)"), "469": ("Forgery for purpose of harming reputation", "336(4)"),
    "471": ("Using as genuine a forged document or electronic record", "340(2)"), "472": ("Making or possessing counterfeit seal with intent to commit forgery", "341"),
    "474": ("Having possession of forged document with intent to use it as genuine", "342"), "477A": ("Falsification of accounts", "344"),
    "489A": ("Counterfeiting currency notes or bank notes", "178"), "489B": ("Using as genuine forged or counterfeit currency notes", "179"),
    "494": ("Marrying again during lifetime of husband or wife", "82(1)"), "498A": ("Husband or relative of husband of a woman subjecting her to cruelty", "85"),
    "499": ("Defamation", "356(1)"), "500": ("Punishment for defamation", "356(2)"), "503": ("Criminal intimidation (definition)", "351(1)"),
    "504": ("Intentional insult with intent to provoke breach of the peace", "352"), "505": ("Statements conducing to public mischief", "353"),
    "506": ("Criminal intimidation", "351(2)"), "507": ("Criminal intimidation by anonymous communication", "351(4)"),
    "509": ("Word, gesture or act intended to insult the modesty of a woman", "79"), "510": ("Misconduct in public by a drunken person", "355"),
    "511": ("Attempt to commit offences punishable with imprisonment", "62"),
}

# BNS-only provisions or names used when the FIR cites BNS directly (base numbers)
BNS_ONLY: dict[str, str] = {
    "111": "Organised crime", "112": "Petty organised crime", "113": "Terrorist act", "304": "Snatching", "103": "Murder", "101": "Murder (definition)",
    "3": "General explanations / common intention (3(5))",
}

# ---------------------------------------------------------------- special Acts
ACT_SECTIONS: dict[str, dict[str, str]] = {
    "IT": {"43": "Damage to computer / computer system, unauthorised access", "65": "Tampering with computer source documents", "66": "Computer related offences",
           "66B": "Dishonestly receiving stolen computer resource", "66C": "Identity theft", "66D": "Cheating by personation using computer resource",
           "66E": "Violation of privacy", "66F": "Cyber terrorism", "67": "Publishing obscene material in electronic form",
           "67A": "Publishing sexually explicit material in electronic form", "67B": "Child sexual abuse material in electronic form",
           "69": "Interception / monitoring / decryption directions", "70": "Protected system", "72": "Breach of confidentiality and privacy", "72A": "Disclosure of information in breach of lawful contract"},
    "NDPS": {"8": "Prohibition of operations in narcotic drugs and psychotropic substances", "15": "Poppy straw", "18": "Opium poppy and opium",
             "20": "Cannabis (ganja / charas)", "21": "Manufactured drugs and preparations", "22": "Psychotropic substances", "23": "Illegal import / export",
             "25": "Allowing premises to be used for offence", "27": "Consumption of drugs", "27A": "Financing illicit traffic and harbouring offenders",
             "29": "Abetment and criminal conspiracy", "37": "Cognizable and non-bailable offences", "50": "Conditions for search of persons"},
    "ARMS": {"3": "Licence for acquisition and possession of firearms", "25": "Illegal possession / manufacture / sale of arms", "27": "Using arms", "30": "Contravention of licence or rule"},
    "POCSO": {"3": "Penetrative sexual assault", "4": "Punishment for penetrative sexual assault", "5": "Aggravated penetrative sexual assault",
              "6": "Punishment for aggravated penetrative sexual assault", "7": "Sexual assault", "8": "Punishment for sexual assault", "9": "Aggravated sexual assault",
              "10": "Punishment for aggravated sexual assault", "11": "Sexual harassment of a child", "12": "Punishment for sexual harassment", "13": "Use of child for pornographic purposes",
              "14": "Punishment for using child for pornography", "15": "Storage of pornographic material involving child", "17": "Punishment for abetment"},
    "PCA": {"7": "Public servant taking undue advantage / bribe", "8": "Offence of giving bribe to public servant", "9": "Bribe by commercial organisation",
            "11": "Public servant obtaining valuable thing without consideration", "12": "Punishment for abetment", "13": "Criminal misconduct by public servant", "17A": "Prior approval for enquiry"},
    "PMLA": {"3": "Offence of money laundering", "4": "Punishment for money laundering", "5": "Attachment of property", "50": "Powers regarding summons"},
    "DOWRY": {"3": "Penalty for giving or taking dowry", "4": "Penalty for demanding dowry", "6": "Dowry to be for the benefit of the wife"},
    "MV": {"3": "Necessity for driving licence", "5": "Responsibility of owners", "112": "Limits of speed", "134": "Duty of driver in case of accident",
           "184": "Dangerous driving", "185": "Driving by a drunken person", "187": "Punishment for offences relating to accident", "192": "Using vehicle without registration"},
    "SCST": {"3": "Punishments for offences of atrocities"},
    "EXPL": {"3": "Causing explosion likely to endanger life", "4": "Attempt to cause explosion", "5": "Making or possessing explosives"},
}

ACT_LABELS = {"IPC": "Indian Penal Code, 1860", "BNS": "Bharatiya Nyaya Sanhita, 2023", "IT": "Information Technology Act, 2000",
              "NDPS": "NDPS Act, 1985", "ARMS": "Arms Act, 1959", "POCSO": "POCSO Act, 2012", "PCA": "Prevention of Corruption Act, 1988",
              "PMLA": "Prevention of Money Laundering Act, 2002", "DOWRY": "Dowry Prohibition Act, 1961", "MV": "Motor Vehicles Act, 1988",
              "SCST": "SC/ST (Prevention of Atrocities) Act, 1989", "EXPL": "Explosive Substances Act, 1908",
              "BNSS": "Bharatiya Nagarik Suraksha Sanhita, 2023 (procedural)", "CRPC": "Code of Criminal Procedure, 1973 (procedural)", "BSA": "Bharatiya Sakshya Adhiniyam (procedural)"}
PROCEDURAL = {"BNSS", "CRPC", "BSA"}

_BNS_TO_IPC: dict[str, list[str]] = {}
for _ipc, (_n, _b) in IPC.items():
    _BNS_TO_IPC.setdefault(re.sub(r"\(.*", "", _b), []).append(_ipc)

BNS_NAMES = {"3": "Common intention (BNS 3(5))", "45": "Abetment", "61": "Criminal conspiracy", "62": "Attempt", "64": "Rape", "74": "Outraging modesty of a woman",
             "75": "Sexual harassment", "78": "Stalking", "79": "Insulting modesty of a woman", "80": "Dowry death", "85": "Cruelty by husband or relatives",
             "103": "Murder", "105": "Culpable homicide not amounting to murder", "106": "Death by negligence", "108": "Abetment of suicide", "109": "Attempt to murder",
             "115": "Voluntarily causing hurt", "117": "Voluntarily causing grievous hurt", "118": "Hurt by dangerous weapons", "126": "Wrongful restraint",
             "127": "Wrongful confinement", "137": "Kidnapping", "140": "Kidnapping for ransom / murder", "143": "Trafficking", "191": "Rioting", "281": "Rash driving",
             "303": "Theft", "304": "Snatching", "305": "Theft in dwelling house / transport / place of worship", "306": "Theft by clerk or servant",
             "308": "Extortion", "309": "Robbery", "310": "Dacoity", "316": "Criminal breach of trust", "317": "Stolen property", "318": "Cheating",
             "319": "Cheating by personation", "324": "Mischief", "329": "Criminal trespass", "331": "House-trespass / house-breaking", "333": "House-trespass after preparation for hurt",
             "336": "Forgery", "337": "Forgery of record", "338": "Forgery of valuable security", "340": "Using forged document as genuine", "351": "Criminal intimidation",
             "352": "Intentional insult", "356": "Defamation"}
BNS_CUTOVER = "2024-07-01"

_ACT_DEFS: list[tuple[str, str]] = [
    ("BNSS", r"B\.?N\.?S\.?S\.?(?![A-Za-z])|Bharatiya\s+Nagarik\s+Suraksha\s+Sanhita|बीएनएसएस|भारतीय\s+नागरिक\s+सुरक्षा\s+संहिता"),
    ("BNS", r"(?<![A-Za-z])B\.?\s?N\.?\s?S\.?(?![A-Za-z])|Bharatiya\s+Nyaya\s+Sanhita|भारतीय\s+न्याय\s+संहिता|बीएनएस|भा\.?\s?न्या\.?\s?सं\.?"),
    ("IPC", r"(?<![A-Za-z])I\.?\s?P\.?\s?C\.?(?![A-Za-z])|Indian\s+Penal\s+Code|भा\.?\s?दं?\.?\s?(?:वि|सं)\.?|भादवि|भादंवि|भारतीय\s+दंड\s+संहिता|भारतीय\s+दण्ड\s+संहिता|आईपीसी|आयपीसी|आई\.पी\.सी\.?|ஐபிசி|আইপিসি|ਆਈਪੀਸੀ|આઈપીસી|ఐపీసీ|ಐಪಿಸಿ|ഐപിസി|آئی\s*پی\s*سی|تعزیرات\s+ہند"),
    ("CRPC", r"(?<![A-Za-z])Cr\.?\s?P\.?\s?C\.?(?![A-Za-z])|Code\s+of\s+Criminal\s+Procedure|द(?:ं|ण्)ड\s+प्रक्रिया\s+संहिता|सीआरपीसी"),
    ("IT", r"(?<![A-Za-z])I\.?\s?T\.?\s*Act|Information\s+Technology\s+Act|आई\.?\s?टी\.?\s*(?:एक्ट|अधिनियम)|सूचना\s+प्रौद्योगिकी(?:\s+अधिनियम)?|माहिती\s+तंत्रज्ञान\s+कायदा|आयटी\s+कायदा|आईटी\s+एक्ट"),
    ("NDPS", r"(?<![A-Za-z])N\.?D\.?P\.?S\.?(?:\s+Act)?(?![A-Za-z])|Narcotic\s+Drugs(?:\s+and\s+Psychotropic\s+Substances)?(?:\s+Act)?|एनडीपीएस(?:\s+एक्ट)?"),
    ("ARMS", r"Arms\s+Act|आर्म्स\s+एक्ट|आयुध\s+अधिनियम|शस्त्र\s+अधिनियम"),
    ("POCSO", r"(?<![A-Za-z])POCSO(?:\s+Act)?(?![A-Za-z])|पॉक्सो|पोक्सो"),
    ("PCA", r"Prevention\s+of\s+Corruption\s+Act|(?<![A-Za-z])P\.?C\.?\s+Act|भ्रष्टाचार\s+निवारण\s+अधिनियम"),
    ("PMLA", r"(?<![A-Za-z])PMLA(?![A-Za-z])|Prevention\s+of\s+Money\s+Laundering\s+Act|धन\s+शोधन\s+निवारण"),
    ("DOWRY", r"Dowry\s+Prohibition\s+Act|दहेज\s+(?:प्रतिषेध|निषेध)\s+अधिनियम|हुंडा\s+प्रतिबंध"),
    ("MV", r"Motor\s+Vehicles?\s+Act|(?<![A-Za-z])M\.?V\.?\s+Act|मोटर\s+वाहन\s+अधिनियम"),
    ("SCST", r"SC\s*/\s*ST(?:\s*\(?\s*Prevention\s+of\s+Atrocities\s*\)?)?\s*Act|एससी\s*/\s*एसटी\s+एक्ट"),
    ("EXPL", r"Explosive\s+Substances\s+Act|विस्फोटक\s+पदार्थ\s+अधिनियम"),
]
_ACT_RX = re.compile("|".join(f"(?P<a{i}>{rx})" for i, (_, rx) in enumerate(_ACT_DEFS)), re.I)
_SEC_WORD = re.compile(r"(?:Acts?\s*(?:&|and)\s*Sections?|Sections?|Secs?\b\.?|u\s?/\s?ss?\b|धाराएं|धाराओं|धारा|कलमे|कलम|दफा|दफ़ा|ধারা|பிரிவுகள்|பிரிவு|సెక్షన్లు|సెక్షన్|કલમ|ਧਾਰਾ|ಸೆಕ್ಷನ್|വകുപ്പ്|دفعات|دفعہ|Dhara|Dhaara|Dafa|Kalam|(?<![A-Za-z])s\.(?=\s*\d))\s*\.?", re.I)
_DEV_SUFFIX = {"क": "A", "ख": "B", "ग": "C", "घ": "D", "ए": "A", "बी": "B", "सी": "C", "डी": "D", "ई": "E", "एफ": "F"}
_NUM = re.compile(r"(?<![\dA-Za-z])(?P<n>[0-9OoIl]{1,3}?)(?P<suf>(?:\s?[A-Za-z](?![A-Za-z]))|क|ख|ग|घ|ए|बी|सी|डी)?(?P<sub>(?:\s?\(\s*[0-9A-Za-z]{1,3}\s*\))*)(?![\dA-Za-z])")
_FILLER = re.compile(r"(?:[,;/&+:\-–]|(?:read\s+with|r/w|of\s+the|of|the|under|and|in|to|as|with)(?![A-Za-z])|(?:की|का|के|में|तथा|एवं|और|व|आणि|तसेच|ची|चा|चे|अंतर्गत|या)(?![\u0900-\u097F]))", re.I)
_YEAR = re.compile(r"(?:19|20)\d{2}(?!\d)")
_PAREN = re.compile(r"\(\s*([^()]{2,90})\)")


def _act_at(text: str, pos: int):
    m = _ACT_RX.match(text, pos)
    if not m:
        return None
    for i, (code, _) in enumerate(_ACT_DEFS):
        if m.group(f"a{i}"):
            return code, m.end()
    return None


def _parse_num(m: re.Match) -> tuple[str, str] | None:
    raw = m.group("n")
    n = raw.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1"}))
    if not n.isdigit() or not any(c.isdigit() for c in raw):
        return None
    suf = (m.group("suf") or "").strip()
    suf = _DEV_SUFFIX.get(suf, suf.upper())
    sub = re.sub(r"\s+", "", m.group("sub") or "")
    return n.lstrip("0") + suf if n.lstrip("0") else "0", sub


def _scan(text: str, pos: int, limit: int = 260):
    """Tokenise a run of Act names / section words / numbers starting at ``pos``. Returns tokens and the end position."""
    toks: list[dict] = []
    i = pos
    stop = min(len(text), pos + limit)
    while i < stop:
        ch = text[i]
        if ch in " \t\r":
            i += 1
            continue
        if ch == "\n":
            j = _skip_ws(text, i + 1)
            if j >= len(text) or text[j] == "\n" or re.match(r"\d{1,2}\.\s", text[j:j + 4]):
                break
            i = j
            continue
        a = _act_at(text, i)
        if a:
            toks.append({"t": "act", "code": a[0], "s": i, "e": a[1]})
            i = a[1]
            y = _YEAR.match(text, _skip_ws(text, i))
            if y:
                i = y.end()
            continue
        w = _SEC_WORD.match(text, i)
        if w and w.end() > i:
            toks.append({"t": "sec", "s": i, "e": w.end()})
            i = w.end()
            continue
        p = _PAREN.match(text, i)
        if p:  # descriptive parenthesis such as "(Theft)" or "(IPC)" carries no section number
            i = p.end()
            continue
        n = _NUM.match(text, i)
        if n and any(c.isdigit() for c in n.group("n")):
            parsed = _parse_num(n)
            if parsed:
                toks.append({"t": "num", "base": parsed[0], "sub": parsed[1], "s": n.start(), "e": n.end()})
                i = n.end()
                continue
        f = _FILLER.match(text, i)
        if f and f.end() > i:
            toks.append({"t": "sep", "s": i, "e": f.end()})
            i = f.end()
            continue
        break
    return toks, i


def _skip_ws(text, i):
    while i < len(text) and text[i] in " \t":
        i += 1
    return i


def _explicit_acts_in(text: str) -> set[str]:
    return {code for m in _ACT_RX.finditer(text) for i, (code, _) in enumerate(_ACT_DEFS) if m.group(f"a{i}")}


def resolve_act(number: str, fir_date_iso: str = "", doc_acts: set[str] | None = None) -> tuple[str, float, str]:
    """Pick the Act for a bare section number. Returns (act, confidence, reason)."""
    in_ipc, in_bns = number in IPC, (number in _BNS_TO_IPC or number in BNS_ONLY)
    offence_acts = (doc_acts or set()) & {"IPC", "BNS"}
    if len(offence_acts) == 1:
        act = next(iter(offence_acts))
        return act, 0.85, f"Act not written next to the number; the document names only {act} elsewhere"
    if in_ipc and not in_bns:
        return "IPC", 0.75, "Act not written; this section number exists only in the IPC reference table"
    if in_bns and not in_ipc:
        return "BNS", 0.75, "Act not written; this section number exists only in the BNS reference table"
    if fir_date_iso:
        act = "BNS" if fir_date_iso >= BNS_CUTOVER else "IPC"
        return act, 0.6, f"Act not written; FIR date {fir_date_iso} is {'on/after' if act == 'BNS' else 'before'} the BNS start date (2024-07-01), so {act} is assumed - confirm in the source"
    return "IPC", 0.45, "Act not written and no FIR date to decide between IPC and BNS; IPC assumed - confirm in the source"


def offence_name(act: str, number: str) -> tuple[str, str]:
    """(offence name or '', cross-reference text)."""
    if act == "IPC":
        if number in IPC:
            return IPC[number][0], (f"BNS {IPC[number][1]}" if IPC[number][1] != "-" else "")
    elif act == "BNS":
        ipcs = _BNS_TO_IPC.get(number, [])
        if number in BNS_NAMES:
            return BNS_NAMES[number], ("IPC " + "/".join(ipcs)) if ipcs else ""
        if number in BNS_ONLY:
            return BNS_ONLY[number], ""
        if ipcs:
            return IPC[ipcs[0]][0], "IPC " + "/".join(ipcs)
    elif act in ACT_SECTIONS and number in ACT_SECTIONS[act]:
        return ACT_SECTIONS[act][number], ""
    return "", ""


def find_sections(text: str, fir_date_iso: str = "") -> list[dict[str, Any]]:
    """Every charged section the text cites, with Act, offence name, evidence span and reason."""
    text = ascii_digits(text or "")
    doc_acts = _explicit_acts_in(text)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    i = 0
    # anchors: a section word, or an Act name directly followed by a number
    anchor = re.compile(_SEC_WORD.pattern + "|" + "|".join(f"(?:{rx})" for _, rx in _ACT_DEFS), re.I)
    while i < len(text):
        m = anchor.search(text, i)
        if not m:
            break
        toks, end = _scan(text, m.start())
        if not any(t["t"] == "num" for t in toks):
            i = m.end()
            continue
        # A run that starts with an Act name only counts when a number follows within the scan and it is not prose.
        groups: list[dict] = []
        pre_act = None
        group: list[dict] = []
        gpre = None
        for t in toks:
            if t["t"] == "act":
                if group:
                    if gpre is None:
                        groups.append({"nums": group, "act": t["code"], "how": "following", "act_span": (t["s"], t["e"])})
                        group, gpre, pre_act = [], None, None
                    else:
                        groups.append({"nums": group, "act": gpre[0], "how": "preceding", "act_span": gpre[1]})
                        group, gpre, pre_act = [], None, (t["code"], (t["s"], t["e"]))
                else:
                    pre_act = (t["code"], (t["s"], t["e"]))
            elif t["t"] == "num":
                if not group:
                    gpre = pre_act
                group.append(t)
        if group:
            groups.append({"nums": group, "act": gpre[0] if gpre else None, "how": "preceding" if gpre else "none", "act_span": gpre[1] if gpre else None})
        last_act = None
        for g in groups:
            act, how, conf_base, reason = g["act"], g["how"], 0.96, ""
            if act is None and last_act:
                act, how, conf_base = last_act, "list", 0.92
                reason = f"Listed together with {ACT_LABELS.get(last_act, last_act)} sections in the same citation"
            elif act:
                reason = f"{ACT_LABELS.get(act, act)} named {'right after' if how == 'following' else 'right before'} the section number"
            inferred = False
            for t in g["nums"]:
                number = t["base"]
                a = act
                r = reason
                conf = conf_base
                if a is None:
                    a, conf, r = resolve_act(number, fir_date_iso, doc_acts)
                    inferred = True
                key = (a, number)
                if a in PROCEDURAL:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                name, xref = offence_name(a, number)
                if not name:
                    conf = min(conf, 0.85)
                    r += "; not in the offence reference table - look it up in the official text"
                out.append({"kind": "SECTION", "act": a, "act_label": ACT_LABELS.get(a, a), "number": number, "subsection": t["sub"],
                            "normalized": f"{a}:{number}", "surface": text[t["s"]:t["e"]].strip(), "start": t["s"], "end": t["e"],
                            "offence": name, "cross_reference": xref, "act_explicit": not inferred and how != "list",
                            "confidence": round(conf, 3), "reason": r.strip("; "), "flags": (["act_inferred"] if inferred else []) + ([] if name else ["not_in_reference_table"])})
            if act:
                last_act = act
        i = max(end, m.end())
    return out
