"""Transparent multilingual content-flag lexicon (English, Hindi in Devanagari, Hinglish in Latin script).

Design rules
* Every flag is produced by a named rule whose regular expression, weight and reason are listed here and served by ``GET /social/lexicon``.
  There is no learned model and no hidden score.
* A rule needs *structure* (for example "I will kill you", or a drug word next to a sale word), not a single alarming word.
* The score of a category is a noisy-OR of the matched rule weights, then reduced by visible context dampers (sport / gaming banter,
  news reporting or quotation, lyrics / fiction, awareness or fact-check wording, negation, jokes). A flag is raised at score >= FLAG_THRESHOLD.
* A flag says "this wording deserves a human look". It never says anything about a person's intent, guilt or sentiment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FLAG_THRESHOLD = 0.5
CAVEAT = ("Automatic wording match only. Sarcasm, quotation, song lyrics, news reporting, gaming or sports banter and translation "
          "can invert the meaning. Read the full post and its context, and record your decision, before relying on it.")

CATEGORIES = {
    "threat_violence": {"label": "Threat / violence", "severity": "high",
                        "why": "Wording that announces harm to a person, a group or a place. Worth a prompt human look to judge whether it is a real, credible threat."},
    "extortion_sextortion": {"label": "Extortion / sextortion", "severity": "high",
                             "why": "Demands for money or favours backed by a threat to leak private photos, videos or chats. Victims are often afraid to report; consider victim-support steps."},
    "financial_fraud": {"label": "Financial-fraud solicitation", "severity": "medium",
                        "why": "Typical scam scripts: asking for an OTP, 'double your money', fake KYC or lottery links, fee-first loans and jobs. May point to a mule account or a scam operation."},
    "hate_incitement": {"label": "Hate / incitement", "severity": "high",
                        "why": "Calls to attack, expel or dehumanise a community. Can precede public-order incidents; check for organisers, time and place."},
    "drugs_arms": {"label": "Drugs / arms trade", "severity": "high",
                   "why": "An offer or request to buy or sell narcotics, weapons or ammunition, usually with a contact, rate or delivery promise."},
    "self_harm": {"label": "Self-harm risk", "severity": "welfare",
                  "why": "Wording that may signal risk to the writer's own life. This is a welfare matter first: consider a welfare check through the proper channel."},
    "misinformation_panic": {"label": "Panic / misinformation-style forward", "severity": "medium",
                             "why": "Viral-forward style wording (urgent, share before deletion, fear of gangs, poison, bank closures). It says nothing about whether the claim is true; verify before acting."},
}

# ------------------------------------------------------------------ rules
_SEP = r"(?:\s|[,.;:!\-])"


@dataclass(frozen=True)
class Rule:
    id: str
    cat: str
    lang: str
    pattern: str
    weight: float
    note: str = ""
    no_damp: bool = False          # strong real-world markers (address, family) are never damped as banter
    rx: re.Pattern = field(init=False, repr=False, compare=False, default=None)

    def __post_init__(self):
        object.__setattr__(self, "rx", re.compile(self.pattern, re.I | re.U))


_R: list[Rule] = []


def R(id, cat, lang, pattern, weight, note="", no_damp=False):
    _R.append(Rule(id, cat, lang, pattern, weight, note, no_damp))


_VIC = r"(?:you|u|him|her|them|your (?:family|kids|wife|husband|son|daughter|mother|father|parents))"
# ---- threat / violence
R("thr_en_will_kill", "threat_violence", "en", rf"\b(?:i|we)(?:'ll|\s+will|'m\s+going\s+to|\s+am\s+going\s+to|\s+gonna|\s+shall)\s+(?:kill|murder|shoot|stab|hang|burn|butcher)\s+{_VIC}\b", 0.85, "first-person future harm to a person")
R("thr_en_beat_up", "threat_violence", "en", r"\b(?:i|we)(?:'ll|\s+will|\s+gonna)\s+(?:beat|thrash)\s+(?:you|him|her|them)\s+(?:up|to death|black and blue)\b", 0.75, "first-person future violence")
R("thr_en_you_dead", "threat_violence", "en", r"\b(?:you(?:'re|\s+are)|u\s+r)\s+(?:a\s+dead\s+(?:man|woman)|dead\s+meat|going\s+to\s+die|gonna\s+die)\b", 0.75, "'you are dead' style")
R("thr_en_know_where", "threat_violence", "en", r"\bi\s+know\s+where\s+(?:you|your\s+(?:kids?|family|wife|husband|son|daughter))\s+(?:live|work|stay|study|studies|goes?\s+to\s+school)s?\b", 0.8, "implies surveillance of a target", True)
R("thr_en_kill_you", "threat_violence", "en", r"\b(?:kill|murder|shoot|stab)\s+(?:you|your\s+(?:family|kids|wife|husband|son|daughter))\b", 0.6, "imperative or intent toward a person")
R("thr_en_kill_family", "threat_violence", "en", r"\b(?:kill|hurt|harm|finish)\s+your\s+(?:family|kids|wife|husband|son|daughter|parents)\b", 0.8, "harm to family members", True)
R("thr_en_bomb_place", "threat_violence", "en", r"\b(?:blow\s+up|bomb|blast)\s+(?:the|this|that)\s+(?:\w+\s+){0,2}(?:station|school|college|court|mall|market|temple|mosque|church|office|building|bridge|metro|airport|police|stadium)", 0.8, "harm to a named kind of place", True)
R("thr_en_bomb_planted", "threat_violence", "en", r"\b(?:bomb|explosive)s?\s+(?:is|are|has been|have been)?\s*(?:planted|placed|kept|set)\b", 0.6, "claims a device is placed")
R("thr_en_wont_spare", "threat_violence", "en", r"\bwon'?t\s+(?:leave|spare)\s+(?:you|him|her|them)\b", 0.5, "'will not spare you'")
R("thr_en_finish_you", "threat_violence", "en", r"\b(?:finish|destroy|end)\s+you\b", 0.5, "'finish you' (very common in banter)")
R("thr_hi_maar", "threat_violence", "hi", r"(?:जान\s*से\s*(?:मार|खत्म)\s*(?:दू[ंँ]?गा|दू[ंँ]?गी|डालू[ंँ]?गा|दे[ंगे]+)|मार\s*डालू[ंँ]?गा|गोली\s*(?:मार\s*दू[ंँ]?गा|से\s*उड़ा)|काट\s*डालू[ंँ]?गा|ज़?िंदा\s*नहीं\s*छोड़ू[ंँ]?गा)", 0.8, "Hindi: 'I will kill / shoot / cut you'")
R("thr_hi_bomb", "threat_violence", "hi", r"(?:बम\s*से\s*उड़ा|बम\s*लगा\s*(?:दिया|दी)\s*है|धमाका\s*कर\s*दू[ंँ]?गा)", 0.75, "Hindi: bomb wording")
R("thr_hi_target", "threat_violence", "hi", r"(?:तुझे|तुम्हें|तुमको|तेरे\s*परिवार\s*को|तेरी\s*फैमिली\s*को)\s*(?:नहीं\s*छोड़ू[ंँ]?गा|मार\s*डाल|खत्म\s*कर\s*दू[ंँ]?गा)", 0.85, "Hindi: harm to a second person or family", True)
R("thr_hx_target", "threat_violence", "hinglish", r"\b(?:tujhe|tumhe|tumko|usko|isko|unko|sabko|tere\s+parivaar\s+ko|teri\s+family\s+ko)\s+(?:jaan\s+se\s+)?(?:maar|khatam|goli\s+maar|jala|kaat)\s*(?:dunga|dungi|denge|dalunga|dalenge|duga|dega|kar\s+dunga)\b", 0.85, "Hinglish: harm to a person")
R("thr_hx_jaan", "threat_violence", "hinglish", r"\bjaan\s+se\s+(?:maar|khatam)\b", 0.75, "Hinglish: 'kill (him/you)'")
R("thr_hx_goli", "threat_violence", "hinglish", r"\bgoli\s+(?:maar|maarunga|maar\s+dunga|chala\s+dunga)\b", 0.7, "Hinglish: shoot")
R("thr_hx_maar_dunga", "threat_violence", "hinglish", r"\bmaar\s*(?:dunga|dungi|dalunga|dalenge|denge)\b", 0.6, "Hinglish: 'I will hit/kill'; very common in banter")
R("thr_hx_dekh_lunga", "threat_violence", "hinglish", r"\btujhe\s+(?:dekh\s+lunga|nahi\s+chhodunga|zinda\s+nahi\s+chhodunga|zinda\s+nahi\s+chodunga)\b", 0.65, "Hinglish: 'I will deal with you'")
R("thr_hx_bomb", "threat_violence", "hinglish", r"\b(?:bomb|blast)\s+se\s+uda\b", 0.75, "Hinglish: blow up")
# ---- extortion / sextortion
R("ext_en_pay_or", "extortion_sextortion", "en", r"\b(?:send|pay|transfer|give|deposit)\b.{0,50}\b(?:or|otherwise|else|if\s+not|unless)\b.{0,60}\b(?:leak|post|share|send|upload|expose|circulate|viral)\b.{0,40}\b(?:photos?|pics?|pictures?|videos?|nudes?|chats?|screenshots?|recording)", 0.85, "money demand plus threat to leak")
R("ext_en_leak_your", "extortion_sextortion", "en", r"\b(?:leak|expose|post|upload|circulate|share)\s+(?:your|ur)\s+(?:nude\s+|private\s+|intimate\s+|obscene\s+|naked\s+)?(?:photos?|pics?|videos?|chats?|images?|recordings?)\b", 0.6, "threat to publish private material")
R("ext_en_have_your", "extortion_sextortion", "en", r"\b(?:i\s+have|i've\s+got|we\s+have|recorded)\s+your\s+(?:nude|private|intimate|naked|obscene)\s+(?:video|videos|photos|pics|recording|screenshots|chats?)\b", 0.75, "claims to hold private material")
R("ext_en_pay_amount_or", "extortion_sextortion", "en", r"\bpay\s+(?:me\s+)?(?:rs\.?|₹|inr)?\s*[\d,]{3,}\s*(?:or|otherwise|else)\b", 0.6, "amount demanded with an ultimatum")
R("ext_en_vc_nude", "extortion_sextortion", "en", r"\bvideo\s*call\b.{0,50}\b(?:nude|naked|strip|undress)", 0.7, "sextortion opening")
R("ext_en_blackmail", "extortion_sextortion", "en", r"\bblackmail(?:ing|ed)?\s+(?:you|him|her|me)\b", 0.3, "weak: the word alone is common in news")
R("ext_hi_viral", "extortion_sextortion", "hi", r"(?:पैसे|रुपये|रकम).{0,40}(?:नहीं\s*तो|वरना|नहीं\s*दिए|नहीं\s*दिये).{0,40}(?:वीडियो|फोटो|तस्वीर|चैट).{0,30}(?:वायरल|डाल|भेज|पोस्ट|अपलोड)", 0.85, "Hindi: pay or the video/photo goes viral")
R("ext_hi_viral2", "extortion_sextortion", "hi", r"(?:वीडियो|फोटो|तस्वीर)\s*(?:को\s*)?वायरल\s*कर\s*दू[ंँ]?गा", 0.75, "Hindi: 'I will make the video/photo viral'")
R("ext_hx_viral", "extortion_sextortion", "hinglish", r"\b(?:paise|paisa|rupaye|amount|paisey)\b.{0,50}\b(?:nahi\s+to|nahin\s+to|warna|varna|otherwise)\b.{0,50}\b(?:video|photo|pics?|chat)s?\b.{0,40}\b(?:viral|leak|post|bhej|upload|share)", 0.85, "Hinglish: pay or the video goes viral")
R("ext_hx_viral2", "extortion_sextortion", "hinglish", r"\b(?:video|photo|pics?)s?\s+viral\s+kar\s*(?:dunga|dungi|denge)\b", 0.75, "Hinglish: 'I will make it viral'")
R("ext_hx_viral3", "extortion_sextortion", "hinglish", r"\bviral\s+kar\s+(?:dunga|dungi)\b", 0.55, "Hinglish: weak without the object")
# ---- financial fraud
R("fra_en_double", "financial_fraud", "en", r"\b(?:double|triple|10x|5x)\s+(?:your\s+)?(?:money|investment|income|deposit)s?\b.{0,50}\b(?:days?|hours?|weeks?|guaranteed|daily|withdraw)", 0.8, "'double your money' promise")
R("fra_en_guaranteed", "financial_fraud", "en", r"\bguaranteed\s+(?:daily\s+)?(?:returns?|profits?|income)\b", 0.55, "guaranteed-returns promise")
R("fra_en_daily_profit", "financial_fraud", "en", r"\b(?:daily|weekly)\s+(?:profit|income|returns?)\s+of\s+(?:rs\.?|₹|\d)", 0.65, "fixed daily profit promise")
R("fra_en_otp", "financial_fraud", "en", r"\b(?:send|share|give|tell|forward|read\s+out)\s+(?:me\s+)?(?:the\s+|your\s+|ur\s+)?otp\b", 0.8, "asks for an OTP")
R("fra_en_kyc", "financial_fraud", "en", r"\b(?:kyc|pan|aadhaar|aadhar)\s+(?:update|verification|expired|suspended)\b.{0,70}\b(?:click|link|call|download|apk|whatsapp)", 0.75, "fake KYC pretext with a link or call")
R("fra_en_blocked", "financial_fraud", "en", r"\byour\s+(?:bank\s+|sbi\s+|upi\s+)?account\s+(?:will\s+be|has\s+been|is)\s+(?:blocked|suspended|frozen|deactivated)\b.{0,70}\b(?:click|link|call|verify|update)", 0.75, "'account blocked' pretext")
R("fra_en_lottery", "financial_fraud", "en", r"\b(?:lottery|lucky\s+draw|prize)\b.{0,60}\b(?:won|winner|claim)\b.{0,70}\b(?:fee|charges?|tax|deposit|pay|processing)", 0.8, "prize with an up-front fee")
R("fra_en_lottery2", "financial_fraud", "en", r"\b(?:you(?:'ve)?\s+(?:have\s+)?won|winner)\b.{0,50}\b(?:lottery|lucky\s+draw|prize)\b.{0,90}\b(?:fee|charges?|tax|deposit|pay|processing)", 0.8, "'you won' with an up-front fee")
R("fra_en_wfh", "financial_fraud", "en", r"\bwork\s*from\s*home\b.{0,70}\b(?:earn|income|make)\b.{0,25}(?:rs\.?|₹)?\s*\d[\d,]*\s*(?:/|per|a)?\s*(?:day|daily|hour|week)", 0.65, "work-from-home income promise")
R("fra_en_ptjob", "financial_fraud", "en", r"\bpart[- ]time\b.{0,30}\b(?:job|work)\b.{0,70}\b(?:like|rate|review|task|subscribe)\w*\b.{0,50}\b(?:earn|rs\.?|₹|commission|per)", 0.7, "task-for-pay job scam")
R("fra_en_crypto_send", "financial_fraud", "en", r"\bsend\s+\d+(?:\.\d+)?\s*(?:btc|eth|usdt|bitcoin)\b.{0,50}\b(?:get|receive|back)\s+\d", 0.85, "crypto 'send 1 get 2' giveaway")
R("fra_en_crypto_giveaway", "financial_fraud", "en", r"\b(?:crypto|btc|bitcoin|eth|usdt)\b.{0,50}\b(?:giveaway|double)", 0.6, "crypto giveaway")
R("fra_en_loan_fee", "financial_fraud", "en", r"\bloan\b.{0,40}\b(?:approved|sanction\w*)\b.{0,70}\b(?:fee|advance|charges|insurance)", 0.7, "loan approved but fee first")
R("fra_en_qr", "financial_fraud", "en", r"\bscan\s+(?:this\s+)?qr\b.{0,50}\b(?:receive|get)\s+(?:money|payment|refund|cashback)", 0.8, "scan a QR to receive money is a scam pattern")
R("fra_en_digital_arrest", "financial_fraud", "en", r"\bdigital\s+arrest\b", 0.6, "'digital arrest' extortion script")
R("fra_hi_double", "financial_fraud", "hi", r"पैसे\s*(?:दुगने|दोगुने|डबल)", 0.8, "Hindi: double your money")
R("fra_hi_otp", "financial_fraud", "hi", r"ओटीपी\s*(?:बताओ|बताइए|बता\s*दो|शेयर\s*करो|भेजो|भेज\s*दो)", 0.8, "Hindi: give me the OTP")
R("fra_hi_kyc", "financial_fraud", "hi", r"केवाईसी\s*(?:अपडेट|वेरिफिकेशन).{0,60}(?:लिंक|क्लिक|कॉल|डाउनलोड)", 0.75, "Hindi: KYC update with a link")
R("fra_hi_lottery", "financial_fraud", "hi", r"लॉटरी\s*(?:जीत|लगी|निकली).{0,60}(?:फीस|शुल्क|टैक्स|जमा|भेजें|भेजो)", 0.75, "Hindi: lottery with a fee")
R("fra_hi_wfh", "financial_fraud", "hi", r"घर\s*बैठे\s*(?:कमाओ|कमाएं|कमाएँ|पैसे\s*कमाएं).{0,50}(?:रोज़?|प्रतिदिन|दिन)", 0.6, "Hindi: earn at home daily")
R("fra_hx_double", "financial_fraud", "hinglish", r"\bpaise\s+(?:double|dugne|dugna|duguna|doguna)\b", 0.8, "Hinglish: double your money")
R("fra_hx_otp", "financial_fraud", "hinglish", r"\botp\s+(?:batao|bata\s+do|bataiye|share\s+karo|bhejo|bhej\s+do|dedo|de\s+do)\b", 0.8, "Hinglish: give me the OTP")
R("fra_hx_kyc", "financial_fraud", "hinglish", r"\bkyc\s+(?:update|verification)\b.{0,60}\b(?:link|click|call|download)", 0.75, "Hinglish: KYC update with link")
R("fra_hx_wfh", "financial_fraud", "hinglish", r"\bghar\s+baithe\s+(?:kamao|kamaye|kamai|paise)\b.{0,60}\b(?:daily|roz|har\s+din|rs\.?|₹|\d)", 0.65, "Hinglish: earn at home")
R("fra_hx_lottery", "financial_fraud", "hinglish", r"\blottery\s+(?:lagi|jeeti|jeete|jeet|nikli)\b.{0,70}\b(?:fees?|charges?|tax|jama|deposit|bhejo|processing)", 0.75, "Hinglish: lottery with a fee")
R("fra_hx_guaranteed", "financial_fraud", "hinglish", r"\bguaranteed\s+(?:profit|return)s?\b", 0.55, "Hinglish/English mix: guaranteed profit")
# ---- hate / incitement
_GRP = r"(?:muslims?|hindus?|christians?|sikhs?|dalits?|jews?|migrants?|outsiders?|biharis?|tribals?|refugees?|kashmiris?|brahmins?|bengalis?|tamils?|marathis?|punjabis?|gujaratis?|northeast\w*|infiltrators?)"
R("hat_en_kill_all", "hate_incitement", "en", rf"\b(?:kill|exterminate|eliminate|wipe\s+out|burn|hang|lynch|slaughter)\s+(?:all|every)\s+(?:the\s+)?{_GRP}\b", 0.85, "calls for violence against a whole community", True)
R("hat_en_attack_property", "hate_incitement", "en", r"\b(?:attack|burn|loot|destroy)\s+(?:their|those)\s+(?:shops?|houses?|homes?|mosques?|temples?|churches?|neighbou?rhoods?|colon(?:y|ies))\b", 0.8, "calls to attack a community's property", True)
R("hat_en_gather", "hate_incitement", "en", r"\b(?:gather|assemble|come)\b.{0,40}\b(?:teach\s+them\s+a\s+lesson|finish\s+them|burn|attack)\b", 0.65, "mobilising for an attack")
R("hat_en_vermin", "hate_incitement", "en", rf"\b{_GRP}\s+(?:are|is)\s+(?:all\s+)?(?:vermin|cockroaches|parasites|termites|filth|animals|pests)\b", 0.75, "dehumanising language about a community")
R("hat_en_expel", "hate_incitement", "en", rf"\b{_GRP}\b.{{0,30}}\b(?:must|should|have to)\s+be\s+(?:killed|driven out|thrown out|wiped out|burnt|hanged)\b", 0.7, "calls to harm or expel a community")
R("hat_hi_kill", "hate_incitement", "hi", r"(?:सब|सभी|इन)\s*(?:मुसलमानों|हिंदुओं|हिन्दुओं|ईसाइयों|दलितों|प्रवासियों|बिहारियों|घुसपैठियों|सिखों)\s*को\s*(?:मार|काट|जला|भगा)", 0.85, "Hindi: kill/expel a whole community", True)
R("hat_hi_burn", "hate_incitement", "hi", r"(?:घर|दुकानें|दुकानों|मस्जिद|मंदिर|बस्ती).{0,12}(?:जला|फूंक|लूट)\s*दो", 0.75, "Hindi: burn/loot property", True)
R("hat_hi_gather", "hate_incitement", "hi", r"इकट्ठा\s*(?:हो|होकर).{0,40}(?:जला|मार|हमला|सबक)", 0.7, "Hindi: gather to attack")
R("hat_hx_kill", "hate_incitement", "hinglish", r"\b(?:sab|saare|sabhi)\s+(?:\w+\s+)?(?:mulle|katue|ghuspaithiye|bihari|biharis|dalit|dalits|musalman|musalmano|hindu|hinduon|migrants?|outsiders?|kashmiri)\w*\s+ko\s+(?:maro|maar|kaato|jala|bhagao)", 0.85, "Hinglish: kill/expel a whole community", True)
R("hat_hx_burn", "hate_incitement", "hinglish", r"\b(?:inke|unke|inki|unki)\s+(?:ghar|dukaan|dukan|basti|mohalla)\w*\s+(?:jala|phoonk|loot)\w*\s*(?:do|dena|denge|dalo)?\b", 0.8, "Hinglish: burn/loot property", True)
R("hat_hx_gather", "hate_incitement", "hinglish", r"\b(?:ikattha|ekatra|jama)\s+ho\s+(?:jao|jaao)\b.{0,50}\b(?:sabak|hamla|jala|maar)", 0.7, "Hinglish: gather to attack")
# ---- drugs / arms
_DRUG = r"(?:mdma|molly|lsd|ecstasy|cocaine|heroin|smack|charas|hashish|ganja|weed|mephedrone|meow\s*meow|brown\s*sugar|tramadol|opium|afeem|doda\s*post|crystal\s*meth|ketamine|kush|blotters?|xanax|alprazolam|hydro)"
_SALE = r"(?:available|for\s+sale|in\s+stock|ready|delivery|delivered|dm|whatsapp|price|rates?|cod|home\s+delivery|bulk|order|pm\s+me|contact)"
_GUN = r"(?:pistol|revolver|katta|kattaa|ak[- ]?47|rifle|glock|carbine|9\s?mm|\.32\s*bore|12\s*bore|country[- ]made|tamancha)s?"
R("drg_en_sale", "drugs_arms", "en", rf"\b{_DRUG}\b.{{0,50}}\b{_SALE}\b", 0.8, "drug word next to a sale term")
R("drg_en_sale_rev", "drugs_arms", "en", rf"\b{_SALE}\b.{{0,30}}\b{_DRUG}\b", 0.7, "sale term next to a drug word")
R("arm_en_sale", "drugs_arms", "en", rf"\b{_GUN}\b.{{0,50}}\b(?:for\s+sale|available|in\s+stock|dm|price|rates?|delivery|cash\s+on|without\s+licen[cs]e|no\s+licen[cs]e)\b", 0.8, "weapon next to a sale term")
R("arm_en_ammo", "drugs_arms", "en", r"\b(?:live\s+)?(?:cartridges?|bullets|ammo|ammunition)\b.{0,40}\b(?:for\s+sale|available|dm|rates?|price|bulk)\b", 0.7, "ammunition offered")
R("drg_hi_sale", "drugs_arms", "hi", r"(?:गांजा|चरस|अफीम|स्मैक|कोकीन|हेरोइन|एमडीएमए|ब्राउन\s*शुगर|मेफेड्रोन).{0,40}(?:उपलब्ध|चाहिए|मिलेगा|सप्लाई|डिलीवरी|रेट|भाव|संपर्क)", 0.8, "Hindi: drug word with a sale term")
R("arm_hi_sale", "drugs_arms", "hi", r"(?:कट्टा|पिस्तौल|तमंचा|रिवॉल्वर|कारतूस).{0,40}(?:उपलब्ध|बिकाऊ|चाहिए|मिलेगा|सप्लाई|रेट|भाव|बिना\s*लाइसेंस)", 0.8, "Hindi: weapon with a sale term")
R("drg_hx_sale", "drugs_arms", "hinglish", r"\b(?:ganja|charas|smack|mephedrone|mdma|brown\s+sugar|afeem|cocaine|weed|maal)\b.{0,40}\b(?:milega|milegi|available|chahiye|chahie|supply|delivery|rate|dm|home\s+delivery|ready)\b", 0.7, "Hinglish: drug word with a sale term")
R("arm_hx_sale", "drugs_arms", "hinglish", r"\b(?:katta|kattaa|tamancha|pistol|revolver|kartoos|kartus)\b.{0,40}\b(?:milega|milegi|available|chahiye|supply|rate|bina\s+licen[cs]e|bech(?:na|ne))\b", 0.8, "Hinglish: weapon with a sale term")
# ---- self harm
R("sel_en_strong", "self_harm", "en", r"\b(?:kill\s+myself|end\s+my\s+life|take\s+my\s+own\s+life|no\s+reason\s+to\s+live|better\s+off\s+without\s+me|suicide\s+note)\b", 0.75, "direct statement")
R("sel_en_intent", "self_harm", "en", r"\bi(?:'m|\s+am)?\s+(?:going|planning|ready|want|wanna)\s+to\s+(?:kill\s+myself|end\s+(?:it\s+all|my\s+life)|commit\s+suicide|die)\b", 0.8, "stated intent")
R("sel_en_method", "self_harm", "en", r"\bi(?:'ll|\s+will)\s+(?:end\s+it|hang\s+myself|jump\s+off|cut\s+myself|take\s+(?:all\s+)?(?:the\s+)?pills)\b", 0.8, "stated method")
R("sel_en_want_die", "self_harm", "en", r"\b(?:want|wanna)\s+to\s+(?:die|disappear\s+forever)\b", 0.5, "'want to die' (often hyperbole)")
R("sel_hi_strong", "self_harm", "hi", r"(?:आत्महत्या|खुदकुशी)\s*कर\s*(?:लू[ंँ]|लू[ंँ]गा|लू[ंँ]गी|रहा\s*हू[ंँ]|रही\s*हू[ंँ])", 0.8, "Hindi: 'I will / am going to die by suicide'")
R("sel_hi_live", "self_harm", "hi", r"(?:जीना\s*नहीं\s*चाहता|जीना\s*नहीं\s*चाहती|मर\s*जाना\s*चाहता|मर\s*जाना\s*चाहती|अपनी\s*जान\s*दे\s*दू[ंँ]?गा)", 0.75, "Hindi: 'I do not want to live'")
R("sel_hx_strong", "self_harm", "hinglish", r"\b(?:jeena\s+nahi\s+chahta|jeena\s+nahi\s+chahti|marna\s+chahta|marna\s+chahti|mar\s+ja+na\s+chahta|mar\s+ja+na\s+chahti|suicide\s+kar\s+(?:lunga|lungi)|khudkushi\s+kar\s+(?:lunga|lungi)|apni\s+jaan\s+de\s+(?:dunga|dungi))\b", 0.75, "Hinglish: 'I want to die'")
# ---- panic / misinformation-style forwards
R("mis_en_kidnap", "misinformation_panic", "en", r"\b(?:urgent|breaking|alert)\b.{0,60}\b(?:kidnappers?|child[- ]lifters?|gang)\b.{0,60}\b(?:in\s+your\s+area|spotted|active|forward|share|beware)", 0.75, "gang-in-your-area alert")
R("mis_en_forward_all", "misinformation_panic", "en", r"\b(?:forward|share)\s+(?:this\s+)?(?:message\s+)?(?:to\s+)?(?:everyone|all|urgently|immediately|as\s+much\s+as|maximum)\b.{0,80}\b(?:before\s+(?:it\s+(?:gets\s+)?deleted|govt|government|they)|save\s+(?:lives|your\s+family))", 0.8, "forward-to-all with urgency")
R("mis_en_atm", "misinformation_panic", "en", r"\b(?:atms?|banks?)\s+(?:will\s+be\s+|are\s+)?(?:closed|shut|empty)\b.{0,50}\b(?:tomorrow|tonight|for\s+\d+\s+days|indefinitely)\b.{0,60}\b(?:withdraw|forward|share|urgent)", 0.7, "bank-closure panic")
R("mis_en_riot", "misinformation_panic", "en", r"\b(?:riots?|curfew|violence)\b.{0,40}\b(?:breaks?\s+out|broke\s+out|started|spreading)\b.{0,60}\b(?:don'?t\s+(?:go|step)|stay\s+(?:home|indoors)|forward|share)", 0.7, "riot rumour")
R("mis_en_poison", "misinformation_panic", "en", r"\b(?:water|milk|salt|sugar|medicine)\b.{0,30}\b(?:poisoned|contaminated|laced)\b.{0,60}\b(?:forward|share|do\s+not\s+(?:drink|use|buy)|avoid)", 0.7, "poisoned-supply rumour")
R("mis_en_notes", "misinformation_panic", "en", r"\b(?:notes?|₹\s?\d+|rs\.?\s?\d+\s+notes?)\b.{0,40}\b(?:banned|invalid|demonetis\w+)\b.{0,40}\b(?:tonight|tomorrow|midnight)", 0.7, "currency-ban rumour")
R("mis_en_before_deleted", "misinformation_panic", "en", r"\bshare\b.{0,25}\bbefore\b.{0,25}\b(?:deleted|removed|banned)\b", 0.6, "'share before it is deleted'")
R("mis_hi_kidnap", "misinformation_panic", "hi", r"(?:बच्चा\s*चोर|बच्चे\s*उठाने\s*वाल[ेा])\s*गिरोह.{0,60}(?:सक्रिय|घूम|अलर्ट|फैलाएं|शेयर|फॉरवर्ड)", 0.75, "Hindi: child-lifter gang alert")
R("mis_hi_deleted", "misinformation_panic", "hi", r"(?:डिलीट|हटा)\s*होने\s*से\s*पहले\s*(?:शेयर|फॉरवर्ड)", 0.8, "Hindi: share before it is deleted")
R("mis_hi_bank", "misinformation_panic", "hi", r"कल\s*से\s*(?:बैंक|एटीएम)\s*(?:बंद|खाली)", 0.65, "Hindi: banks/ATMs closed from tomorrow")
R("mis_hx_kidnap", "misinformation_panic", "hinglish", r"\bbachch?a\s*chor\s*gang\b.{0,60}\b(?:active|ghoom|alert|share|forward|savdhan|sawdhan)", 0.75, "Hinglish: child-lifter gang alert")
R("mis_hx_forward", "misinformation_panic", "hinglish", r"\b(?:sabko|sab\s+ko|sabhi\s+ko|jyada\s+se\s+jyada)\s*(?:forward|share)\s*(?:karo|kare|karein|kijiye)\b", 0.5, "Hinglish: forward to everyone")
R("mis_hx_deleted", "misinformation_panic", "hinglish", r"\bdelete\s+hone\s+se\s+pehle\b.{0,40}\b(?:share|forward)", 0.8, "Hinglish: share before it is deleted")
R("mis_hx_atm", "misinformation_panic", "hinglish", r"\bkal\s+se\s*(?:atm|bank)\s*(?:band|khali)\b", 0.65, "Hinglish: banks/ATMs closed from tomorrow")
R("mis_hx_danga", "misinformation_panic", "hinglish", r"\b(?:urgent|breaking)\b.{0,40}\b(?:dang[ae]|curfew)\b.{0,60}\b(?:ghar\s+se\s+mat|forward|share|savdhan)", 0.7, "Hinglish: riot rumour")

RULES: tuple[Rule, ...] = tuple(_R)

# ------------------------------------------------------------------ context dampers
_SPORT = re.compile(r"(?i)(?:क्रिकेट|मैच|विकेट|चौका|छक्का|गेंदबाज|बल्लेबाज|आईपीएल|फाइनल|टीम)|\b(cricket|match|wickets?|bowler|batsman|batter|innings|ipl|kohli|dhoni|rohit|t20|odi|semi[- ]?final|finals?|series|goal|striker|football|kabaddi|hockey|tennis|chess|pubg|bgmi|valorant|gta|cs:?go|fortnite|gaming|gamer|esports|lobby|headshot|tournament|league|playoffs?|chhakka|chauka|boundary|maidan|scoreboard|umpire|dressing room|rcb|csk|mi vs)\b")
_NEWS = re.compile(r"(?i)\b(according to|police said|the police|police have|sources said|told reporters|said in a statement|was (?:booked|arrested|detained)|were (?:booked|arrested)|arrested|booked for|charge[- ]sheet|the court|fir (?:has been|was) (?:registered|filed)|reported that|news:|pti|ani reported)\b|(?:पुलिस\s*ने|पुलिस\s*के\s*अनुसार|गिरफ्तार|खबर)")
_QUOTE_LEAD = re.compile(r"(?i)\b(he|she|they|someone|caller|accused|suspect|man|woman|victim|user)\s+(?:said|says|wrote|texted|messaged|shouted|threatened|allegedly|told|posted|claimed)\b")
_LYRICS = re.compile(r"(?i)(\blyrics?\b|\bsong\b|\bgaana\b|\bmovie\b|\bfilm\b|\btrailer\b|\bdialogue\b|\bdialog\b|\bscene\b|\bweb\s*series\b|\bott\b|\bnetflix\b|\bnovel\b|\bbook\b|\bbgm\b|\bepisode\b|\bcharacter\b|\bmusic video\b|♪|🎵|🎶|#lyrics|\bmeme\b|\bsquad\b)")
_AWARE = re.compile(r"(?i)(never share|do not share|don'?t share|dont share|beware|be aware|awareness|helpline|prevention|fake news|debunk|fact[- ]?check|hoax|is fake|is false|not true|misinformation|stay safe|report (?:such|this)|cyber ?crime\.gov|\b1930\b|जागरूक|अफवाह|फर्जी|झूठ|kisi ko na batayein|मत\s*बताइए|मत\s*बताएं|na batayein|mat batao|mat share|मत शेयर)")
_JOKE = re.compile(r"(?i)(\blol\b|\blmao\b|\bjk\b|\bhaha+\b|😂|🤣|😜|😝|\bjust kidding\b|\bkidding\b|\bmazak\b|मज़ाक|मजाक)")
_NEG = re.compile(r"(?i)\b(?:not|never|won'?t|wouldn'?t|don'?t|no|nahi|nahin|kabhi nahi|मत|नहीं)\s+(?:\w+\s+){0,2}$")

DAMPERS = {
    "sport_or_gaming": (_SPORT, 0.35, {"threat_violence", "hate_incitement", "drugs_arms"}, "reads like sport or gaming banter"),
    "news_or_report": (_NEWS, 0.4, set(CATEGORIES), "reads like a news report about an incident"),
    "quotation": (_QUOTE_LEAD, 0.5, set(CATEGORIES), "reads like a report of what somebody said"),
    "lyrics_or_fiction": (_LYRICS, 0.3, set(CATEGORIES), "mentions a song, film, book or meme"),
    "awareness_or_factcheck": (_AWARE, 0.25, {"financial_fraud", "self_harm", "misinformation_panic", "extortion_sextortion"}, "reads like a warning, awareness or fact-check post"),
    "joke_marker": (_JOKE, 0.6, {"threat_violence", "hate_incitement", "extortion_sextortion"}, "contains a joke marker"),
}


BANTER = {"sport_or_gaming", "joke_marker"}


def _norm(text: str) -> str:
    t = (text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[​‌‍⁠﻿]", "", t)
    t = re.sub(r"(.)\1{3,}", r"\1\1", t)  # 'maaaaar' -> 'maar'
    return re.sub(r"[ \t\r\f\v]+", " ", t)


def _in_quotes(text: str, start: int, end: int) -> bool:
    before, after = text[:start], text[end:]
    return (before.count('"') % 2 == 1 and '"' in after)


def flag_text(text: str, threshold: float = FLAG_THRESHOLD) -> list[dict]:
    """Flags for one text. One entry per category (the strongest matches, up to 3 phrases), each with score, level, why and caveat."""
    t = _norm(text)
    if not t.strip():
        return []
    by_cat: dict[str, list[tuple[Rule, re.Match]]] = {}
    for rule in RULES:
        m = rule.rx.search(t)
        if m:
            by_cat.setdefault(rule.cat, []).append((rule, m))
    out = []
    for cat, hits in by_cat.items():
        kept: list[tuple[Rule, re.Match]] = []
        for rule, m in sorted(hits, key=lambda h: -h[0].weight):  # overlapping matches are one piece of evidence, not two
            if not any(m.start() < k[1].end() and k[1].start() < m.end() for k in kept):
                kept.append((rule, m))
        hits = kept
        p_not = 1.0
        for rule, _m in hits:
            p_not *= (1 - rule.weight)
        base = 1 - p_not
        notes: list[str] = []
        factor = 1.0
        shielded = all(r.no_damp for r, _ in hits)  # strong real-world markers ignore banter dampers, never news/quotation ones
        strongest = max(hits, key=lambda h: h[0].weight)
        for name, (rx, mult, cats, why) in DAMPERS.items():
            if cat in cats and not (shielded and name in BANTER) and rx.search(t):
                factor *= mult
                notes.append(why)
        if _in_quotes(t, strongest[1].start(), strongest[1].end()) and "reads like a report of what somebody said" not in notes:
            factor *= 0.5
            notes.append("the matched words sit inside quotation marks")
        if cat in ("threat_violence", "hate_incitement", "extortion_sextortion") and _NEG.search(t[max(0, strongest[1].start() - 24):strongest[1].start()]):
            factor *= 0.2
            notes.append("the matched words are negated ('not', 'never', 'nahi')")
        score = round(base * factor, 3)
        if score < threshold:
            continue
        level = "high" if score >= 0.75 else ("medium" if score >= 0.6 else "low")
        info = CATEGORIES[cat]
        phrases = []
        for rule, m in sorted(hits, key=lambda h: -h[0].weight)[:3]:
            ph = m.group(0).strip()
            phrases.append({"phrase": ph[:160], "rule": rule.id, "lang": rule.lang, "note": rule.note})
        out.append({"category": cat, "label": info["label"], "severity": info["severity"], "score": score, "level": level, "phrase": phrases[0]["phrase"],
                    "matches": phrases, "lang": phrases[0]["lang"], "rule": phrases[0]["rule"], "why": info["why"], "context_notes": notes, "caveat": CAVEAT})
    out.sort(key=lambda f: -f["score"])
    return out


def lexicon_listing() -> dict:
    cats = {}
    for r in RULES:
        cats.setdefault(r.cat, []).append({"id": r.id, "lang": r.lang, "weight": r.weight, "note": r.note, "pattern": r.pattern})
    return {"threshold": FLAG_THRESHOLD, "caveat": CAVEAT,
            "categories": [{"key": k, **v, "rules": cats.get(k, [])} for k, v in CATEGORIES.items()],
            "dampers": [{"key": k, "multiplier": mult, "applies_to": sorted(cs), "meaning": why} for k, (_rx, mult, cs, why) in DAMPERS.items()],
            "method": "Noisy-OR of matched rule weights per category, multiplied by every context damper that applies. Flag when the result is at least the threshold."}
