"""Label / cue vocabularies used by the FIR field parser and the person/role extractor.

Written by hand for English, Hinglish (romanised Hindi), Hindi, Marathi and the labels of the other major
Indian scripts. Values are regex fragments (already escaped where they contain punctuation).
"""
from __future__ import annotations

import re

# ------------------------------------------------------------------ structured-field labels
# (field, [label strings], weak) - weak labels need a ':' or '-' after them to count as a label.
FIELD_LABELS: list[tuple[str, list[str], bool]] = [
    ("fir_number", ["FIR No.", "FIR No", "F.I.R. No", "FIR Number", "FIR Registration No", "FIR Reg No", "Report No.", "Report No", "Crime No.", "Crime No", "Crime Number", "Cr. No.", "Case No.", "Case No",
                    "FIR क्रमांक", "FIR क्र.", "FIR क्र", "FIR नंबर", "FIR नं", "FIR संख्या", "एफआईआर क्रमांक", "एफआईआर नंबर", "एफआईआर नं", "एफआईआर क्र", "एफआईआर संख्या", "एफआयआर क्रमांक", "एफआयआर क्र", "एफआयआर नंबर",
                    "एफ.आई.आर. क्रमांक", "एफ.आई.आर. नं", "प्रथम सूचना रिपोर्ट क्रमांक", "प्रथम सूचना रिपोर्ट नंबर", "प्रथम सूचना रिपोर्ट नं", "प्रथम सूचना रिपोर्ट संख्या", "प्रथम माहिती अहवाल क्रमांक", "प्रथम माहिती अहवाल क्र",
                    "अपराध क्रमांक", "अपराध संख्या", "अपराध क्र.", "गुन्हा रजिस्टर क्रमांक", "गुन्हा रजिस्टर नंबर", "गुन्हा क्रमांक", "गुन्हा क्र.", "प्र.मा.अ. क्रमांक",
                    "এফআইআর নং", "এফআইআর নম্বর", "எஃப்.ஐ.ஆர் எண்", "எஃப்ஐஆர் எண்", "ఎఫ్‌ఐఆర్ నంబర్", "ఎఫ్ఐఆర్ నంబర్", "એફઆઈઆર નંબર", "ਐਫ.ਆਈ.ਆਰ ਨੰਬਰ", "ਐਫ਼.ਆਈ.ਆਰ ਨੰਬਰ", "ایف آئی آر نمبر", "ಎಫ್ಐಆರ್ ಸಂಖ್ಯೆ", "എഫ്ഐആർ നമ്പർ"], False),
    ("district", ["District", "Distt.", "Dist.", "Dist", "pistrict", "D1strict", "Dlstrict", "Jila", "Zila", "Zilla", "जिला", "जनपद", "जिल्हा", "জেলা", "மாவட்டம்", "జిల్లా", "જિલ્લો", "ਜ਼ਿਲ੍ਹਾ", "ضلع", "ಜಿಲ್ಲೆ", "ജില്ല"], False),
    ("police_station", ["Police Station", "Police Stn", "P.S.", "P.S", "PS", "P5", "Thana", "Thaana", "थाना", "थाने", "पुलिस थाना", "पुलिस स्टेशन", "पोलीस ठाणे", "पोलीस स्टेशन", "पोलिस स्टेशन", "पोलीस ठाण्याचे नाव",
                        "থানা", "காவல் நிலையம்", "పోలీస్ స్టేషన్", "పోలీసు స్టేషన్", "પોલીસ સ્ટેશન", "ਥਾਣਾ", "تھانہ", "ಪೊಲೀಸ್ ಠಾಣೆ", "പോലീസ് സ്റ്റേഷൻ"], False),
    ("year", ["Year", "वर्ष", "सन", "Saal", "साल", "বছর", "ஆண்டு", "సంవత్సరం", "વર્ષ", "ਸਾਲ", "سال"], True),
    ("occurrence_date", ["Date of Occurrence", "Occurrence Date", "Date of Incident", "Incident Date", "Date & Time of Occurrence", "Date and Time of Occurrence", "Day/Date", "Day / Date", "Occurrence", "Date of Offence",
                         "घटना की तारीख", "घटना का दिनांक", "घटना दिनांक", "घटनेची तारीख", "घटनेचा दिनांक", "घटना की दिनांक", "Ghatna ka dinank", "Ghatna ki tarikh"], False),
    ("fir_date", ["Date of FIR", "Date & Time of FIR", "Date and Time of FIR", "Date and time of report", "Date & time of report", "Date of Report", "Date of Registration", "Registration Date", "Reg. Date", "Dated", "Dinank", "Dinaank", "Tarikh", "Tareekh",
                  "दिनांक", "तारीख", "तिथि", "তারিখ", "தேதி", "తేదీ", "તારીખ", "ਮਿਤੀ", "تاریخ", "ದಿನಾಂಕ", "തീയതി"], False),
    ("fir_date_weak", ["Date", "Dt"], True),
    ("time", ["Time", "Samay", "Waqt", "समय", "वेळ", "সময়", "நேரம்", "సమయం", "સમય", "ਸਮਾਂ", "وقت"], True),
    ("sections", ["Acts & Sections", "Acts and Sections", "Act & Sections", "Sections", "Section", "Dhara", "Dhaara", "धारा", "धाराएं", "कलम", "कलमे", "দফা", "ধারা", "பிரிவு", "సెక్షన్", "કલમ", "ਧਾਰਾ", "دفعہ", "ಸೆಕ್ಷನ್", "വകുപ്പ്"], False),
    ("place_of_occurrence", ["Place of Occurrence", "Place of occurrence", "Place of Incident", "Scene of Crime", "Place of Offence", "Ghatna ka sthan", "Ghatna sthal", "Ghatnasthal", "घटना का स्थान", "घटना स्थल", "घटनास्थल", "घटनास्थळ", "घटनेचे ठिकाण", "घटनेचे स्थान",
                            "गुन्ह्याचे ठिकाण", "ঘটনার স্থান", "சம்பவ இடம்", "సంఘటన స్థలం", "બનાવનું સ્થળ", "ਘਟਨਾ ਸਥਾਨ", "واقعہ کی جگہ", "ಘಟನೆ ನಡೆದ ಸ್ಥಳ"], False),
    ("complainant", ["Complainant / Informant", "Complainant/Informant", "Name of complainant", "Name of the complainant", "Complainant", "Informant", "Applicant", "Shikayatkarta", "Shikayat karta", "Shikayatakarta",
                     "शिकायतकर्ता", "शिकायत कर्ता", "सूचनाकर्ता", "सूचना कर्ता", "प्रार्थी", "फिर्यादी", "तक्रारदार", "तक्रारदाराचे नाव", "माहितीदार", "अर्जदार", "পিড়িত", "অভিযোগকারী", "புகார்தாரர்", "புகார் அளித்தவர்", "ఫిర్యాదుదారు", "ఫిర్యాదుదారుడు", "ફરિયાદી", "ਸ਼ਿਕਾਇਤਕਰਤਾ", "شکایت کنندہ", "ಫಿರ್ಯಾದಿ", "പരാതിക്കാരൻ"], False),
    ("father_name", ["Father's Name", "Father Name", "Father", "Pita ka naam", "पिता का नाम", "पिताचे नाव", "वडिलांचे नाव", "वडिलांचे नांव", "পিতার নাম", "தந்தை பெயர்", "తండ్రి పేరు", "પિતાનું નામ", "ਪਿਤਾ ਦਾ ਨਾਮ", "والد کا نام"], False),
    ("accused_description", ["Details of Known / Suspected / Unknown Accused with Full Particulars", "Details of Known / Suspected / Unknown Accused", "Details of Accused", "Name of Accused", "Accused", "Suspect", "Aaropi", "Aropi",
                             "आरोपी का नाम", "आरोपी", "अज्ञात आरोपी", "संशयित आरोपी", "संशयित", "अभियुक्त", "অভিযুক্ত", "குற்றவாளி", "నిందితుడు", "આરોપી", "ਦੋਸ਼ੀ", "ملزم", "ಆರೋಪಿ"], False),
    ("witness", ["Witnesses", "Witness", "Gawah", "Gavah", "गवाह", "साक्षीदार", "প্রত্যক্ষদর্শী", "சாட்சி", "సాక్షి", "સાક્ષી", "ਗਵਾਹ", "گواہ"], False),
    ("victim", ["Victim", "पीड़िता", "पीड़ित", "पीडित", "पीडिता", "পীড়িত"], False),
    ("investigating_officer", ["Investigating Officer assigned", "Investigating Officer", "Investigation Officer", "Inquiry Officer", "Enquiry Officer", "I.O.", "IO", "Jaanch Adhikari", "Janch Adhikari", "Jaanch",
                               "जांच अधिकारी", "जाँच अधिकारी", "तपास अधिकारी", "तपासणी अधिकारी", "विवेचक", "विवेचना अधिकारी", "তদন্তকারী কর্মকর্তা", "விசாரணை அதிகாரி", "దర్యాప్తు అధికారి", "તપાસ અધિકારી", "ਜਾਂਚ ਅਧਿਕਾਰੀ", "تفتیشی افسر", "ತನಿಖಾಧಿಕಾರಿ"], False),
    ("recorded_by", ["Recorded by", "Written by", "Received by", "Reported to"], False),
    ("mobile", ["Mobile No.", "Mobile No", "Mobile", "Mob. No.", "Mob No", "Mob.", "Mob", "Contact No.", "Contact", "Phone No.", "Phone", "Ph.", "Ph", "Tel.", "Tel", "फोन", "मोबाइल", "मोबाईल", "संपर्क", "মোবাইল", "கைபேசி", "மொபைல்", "మొబైల్", "મોબાઇલ", "ਮੋਬਾਈਲ", "موبائل"], True),
    ("address", ["Address", "Add.", "Pata", "पता", "पत्ता", "ठिकाण", "ঠিকানা", "முகவரி", "చిరునామా", "સરનામું", "ਪਤਾ", "پتہ", "ವಿಳಾಸ"], True),
    ("name", ["Name", "Naam", "नाम", "नाव", "নাম", "பெயர்", "పేరు", "નામ", "ਨਾਮ", "نام"], True),
    ("property", ["Details of Properties Stolen / Involved", "Details of Properties Stolen", "Properties Stolen", "Property Stolen", "Stolen Property", "Property", "चोरी की संपत्ति", "चोरीचा माल", "चोरीची मालमत्ता", "चोरीस गेलेली मालमत्ता", "चोरी गया माल"], False),
    ("facts", ["Short Description of Incident (Brief Facts)", "Short Description of Incident", "Brief Facts of the Case", "Brief Facts", "Facts", "Ghatna", "घटना का संक्षिप्त विवरण", "घटना का विवरण", "घटना का वर्णन", "घटनेचे वर्णन", "घटनेचा तपशील",
               "घटना", "विवरण", "वर्णन", "বিবরণ", "விவரம்", "వివరాలు", "વિગત", "ਵੇਰਵਾ", "تفصیل", "ವಿವರ", "വിവരണം", "Description", "Details", "Incident"], True),
    ("action_taken", ["Action Taken / Direction for Investigation", "Action Taken", "कार्रवाई", "केलेली कारवाई"], False),
]

PARTY_FIELDS = {"complainant", "accused_description", "witness", "victim", "investigating_officer", "recorded_by"}


_OCR_CLASS = {"I": "[Il1|]", "l": "[lI1|]", "i": "[il1|]", "1": "[1lI|]", "O": "[O0]", "o": "[o0]", "0": "[0Oo]", "S": "[S5]", "s": "[s5]", "5": "[5Ss]", "B": "[B8]", "b": "[b6]", "Z": "[Z2]", "z": "[z2]"}


def label_regex(label: str) -> str:
    """Literal label -> tolerant regex (flexible whitespace, optional dots)."""
    parts = []
    for ch in label:
        if ch == " ":
            parts.append(r"\s+")
        elif ch == ".":
            parts.append(r"\.?")
        elif ch in _OCR_CLASS and label.isascii() and len(re.sub(r"[\W_]", "", label)) >= 4:
            parts.append(_OCR_CLASS[ch])     # OCR often reads I/l/1, O/0, S/5, B/8 for one another inside labels ("Date of FlR", "T1me")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


# ------------------------------------------------------------------ person-role cues
RANKS = (r"Sub[\s\-]?Inspector|Assistant\s+Sub[\s\-]?Inspector|Inspector|Head\s+Constable|Constable|Insp\.?|Inspr\.?|Deputy\s+Superintendent|D\.?S\.?P\.?|A\.?C\.?P\.?|D\.?C\.?P\.?|S\.?H\.?O\.?|"
         r"A\.?S\.?I\.?|P\.?S\.?I\.?|A\.?P\.?I\.?|S\.?I\.?|P\.?I\.?|H\.?C\.?|P\.?C\.?|W\.?P\.?C\.?|C\.?I\.?|"
         r"सहायक\s+उपनिरीक्षक|सहायक\s+पोलीस\s+निरीक्षक|पोलीस\s+उपनिरीक्षक|पोलीस\s+निरीक्षक|उपनिरीक्षक|सब\s+इंस्पेक्टर|सब\s+इन्स्पेक्टर|इंस्पेक्टर|इन्स्पेक्टर|निरीक्षक|हेड\s+कांस्टेबल|हेड\s+कॉन्स्टेबल|कांस्टेबल|कॉन्स्टेबल|एएसआई|एएसआय|एसआई|पीएसआय|थानाध्यक्ष|थानेदार|सिपाही|हवलदार")
TITLES = r"(?:Mr|Mrs|Ms|Miss|Dr|Shri|Shree|Smt|Sri|Sh|Late|Kumari|Km|Master|Pandit|Haji|Maulana|Shrimati|Executive\s+Engineer|Engineer|Advocate|Adv|Contractor|Officer|Sir|Madam|श्री|श्रीमती|सुश्री|कुमारी|स्वर्गीय|स्व\.|श्रीमान|मिस्टर|मिसेज|डॉ\.?|डॉक्टर|शेठ|सेठ)"

CUES: dict[str, str] = {
    "COMPLAINANT": (r"Complainant\s*/\s*Informant|Name\s+of\s+(?:the\s+)?complainant|Complainant(?:'s)?\s+name|Complainant|Informant|Applicant|Shikayatkarta|Shikayat\s?karta|Shikayatakarta|शिकायतकर्ता|शिकायत\s+कर्ता|सूचनाकर्ता|प्रार्थी|फिर्यादी|तक्रारदाराचे\s+नाव|तक्रारदार|माहितीदार|अर्जदार|"
                    r"অভিযোগকারী|புகார்தாரர்|ఫిర్యాదుదారు(?:డు)?|ફરિયાદી|ਸ਼ਿਕਾਇਤਕਰਤਾ|شکایت\s+کنندہ|ಫಿರ್ಯಾದಿ|പരാതിക്കാരൻ"),
    "ACCUSED": (r"Details\s+of\s+(?:Known\s*/\s*Suspected\s*/\s*Unknown\s+)?Accused(?:\s+with\s+Full\s+Particulars)?|Name\s+of\s+(?:the\s+)?accused|Accused\s+person|Accused|Suspects?|Suspected|Culprits?|Miscreants?|Assailants?|Offenders?|Suppliers?|Peddlers?|Smugglers?|Kingpin|Mastermind|Aaropi(?:yon)?|Aropi|Aarop(?:i)?|"
                r"आरोपी\s+का\s+नाम|आरोपी|अभियुक्त|संदिग्ध|संशयित\s+आरोपी|संशयित|अज्ञात\s+आरोपी|অভিযুক্ত|குற்றவாளி|నిందితుడు|આરોપી|ਦੋਸ਼ੀ|ملزم|ಆರೋಪಿ"),
    "WITNESS": (r"Eye[\s\-]?witness(?:es)?|Witness(?:es)?|Passer[\s\-]?by|Bystander|Gawah(?:on)?|Gavah|Gawaah|Sakshidar|"
                r"प्रत्यक्षदर्शी|चश्मदीद|गवाह(?:ों)?|साक्षीदार|साक्षी|প্রত্যক্ষদর্শী|சாட்சி|సాక్షి|સાક્ષી|ਗਵਾਹ|گواہ"),
    "VICTIM": r"Victim|Injured|Deceased|Pidit(?:a)?|पीड़िता|पीड़ित|पीडिता|पीडित|पीडिताचा|पीडिताचे|পীড়িত|مظلوم",
    "INVESTIGATING_OFFICER": (r"Investigating\s+Officer(?:\s+assigned)?|Investigation\s+Officer|Inquiry\s+Officer|Enquiry\s+Officer|Case\s+Officer|(?:I\.?[O0]|l\.?[O0])\.?|Jaanch\s+Adhikari|Janch\s+Adhikari|Jaanch|"
                              r"जांच\s+अधिकारी|जाँच\s+अधिकारी|तपास\s+अधिकारी|तपासणी\s+अधिकारी|विवेचक|विवेचना\s+अधिकारी|তদন্তকারী\s+কর্মকর্তা|விசாரணை\s+அதிகாரி|దర్యాప్తు\s+అధికారి|તપાસ\s+અધિકારી|ਜਾਂਚ\s+ਅਧਿਕਾਰੀ|تفتیشی\s+افسر|ತನಿಖಾಧಿಕಾರಿ"),
    "POLICE_OFFICER": r"Recorded\s+by|Written\s+by|Received\s+by|Reported\s+to|Duty\s+Officer|Station\s+House\s+Officer|SHO",
}
KIN_CUES = (r"s\s*/\s*o|d\s*/\s*o|w\s*/\s*o|c\s*/\s*o|h\s*/\s*o|son\s+of|daughter\s+of|wife\s+of|husband\s+of|Father(?:'s)?\s+Name|Father\s+name|Pita\s+ka\s+naam|Pita\s+ka\s+nam|Pitaji|Pita|Walad|Wald|Father|Bint|Bin|Ibn|Waldiyat|"
            r"पिता\s+का\s+नाम|पिताचे\s+नाव|वडिलांचे\s+नाव|वडिलांचे\s+नांव|वडील|पिताजी|पिता|वल्द|सुपुत्री|सुपुत्र|पुत्री|पुत्र|পিতার\s+নাম|தந்தை\s+பெயர்|తండ్రి\s+పేరు|પિતાનું\s+નામ|ਪਿਤਾ\s+ਦਾ\s+ਨਾਮ|والد\s+کا\s+نام")
# descriptors that may sit between a role cue and the name and carry no name of their own
DESCRIPTORS = (r"(?:known\s+as|named|by\s+name|ka\s+naam|ka\s+nam|ki\s+naam|the\s+name|name|naam|नाम|नाव|का\s+नाम|चे\s+नाव|"
               r"neighbou?r|padosi|tenant|kiraayedaar|kirayedar|landlord|employee|friend|dost|colleague|cousin|relative|husband|wife|patni|pati|driver|"
               r"पति|पत्नी|सास|ससुर|ननद|देवर|जेठ|पड़ोसी|पडोसी|शेजारी|किरायेदार|मालिक|दोस्त|मित्र|भाई|बहन|कर्मचारी|नौकर)")

# ------------------------------------------------------------------ name reading
# Capitalised Latin words that are never person names, and Devanagari tokens that end a name.
LATIN_NON_NAMES = {w.casefold() for w in """
The A An He She It They We I You His Her Their Our My On In At By For From To Of And Or But If As Is Was Were Are Be Been Has Have Had Not No Yes
Sir Madam Respected Dear Please Kindly Subject Sub Reference Ref Thanks Thank Yours Faithfully Sincerely Regards
Police Station Thana District Sector Road Marg Street Lane Nagar Colony Market Bazar Bazaar Mall Chowk Gate Village Town City Mohalla Block Phase Flat House Plot Floor Tower Towers Apartments Apartment Society Complex Parking Lot Main
Unknown Unidentified Accused Suspect Complainant Informant Witness Victim Officer Inspector Constable Person Persons Men Man Woman Women Boy Boys Girl Girls Youth Youths Driver Owner
FIR First Information Report Section Sections Act Code IPC BNS BNSS CrPC CCTV MLC PS SHO IO ASI SI PSI DSP ACP DCP HC PC UPI ATM OTP KYC PAN Aadhaar Aadhar IFSC
Rs Rupees Cash Mobile Phone Number Account Bank Card Vehicle Car Bike Motorcycle Scooter Scooty Activa Swift Honda Maruti Bajaj Hero Royal Enfield Bullet Splendor Pulsar Scorpio Tractor Trolley
Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May June July August September October November December
Jan Feb Mar Apr Jun Jul Aug Sep Sept Oct Nov Dec Date Time Year Place Address Name Father Mother Brother Sister Son Daughter Wife Husband
Delhi Mumbai Pune Nashik Kolkata Chennai Hyderabad Bengaluru Bangalore Lucknow Jaipur Bhopal Indore Patna Ahmedabad Noida Ghaziabad Faridabad Gurugram Gurgaon Amritsar Ludhiana Jalandhar Nagpur Thane Meerut Kanpur Varanasi Agra
Hospital School College Shop Office Bank Temple Mosque Church Hotel Restaurant Market Company Firm Traders Pvt Ltd Limited Enterprises Store Showroom Insurance Department Corporation Electricity Board
Ghatna Aaropi Shikayatkarta Dhara Dinank Thana Zila Jila Gawah Padosi Kiraayedaar Meri Mera Mere Uska Uski Usne Maine Main Aur Phir Kripya Sewa Vishay Mujhe Hum Wo Ye
Yes Facts Brief Details Description Incident Occurrence Recorded Reported Received Written Complaint Report Case Crime Offence Offense Theft Robbery Fraud Cheating Assault Missing
""".split()}
# note: short-form organisation words are checked separately
DEV_STOP = set("""
ने को से का की के में पर और तथा एवं व या भी ही तो है हैं था थी थे हो हुआ हुई हुए गया गई गए किया की कर करके करने करते बताया दिया लिया द्वारा
पुत्र पुत्री पिता पति पत्नी निवासी रहने वाला वाले वाली उम्र आयु वर्ष उर्फ उर्फ़ आदि वल्द सुपुत्र सुपुत्री
आणि ला ना चा ची चे च यांनी यांना यांचा यांची यांचे रोजी राहणार रहिवासी वडील वडिलांचे मुलगा मुलगी वय वर्षे
अपना अपनी अपने मुझे मुझको हमें उन्हें उनके उसके उसकी उसका इसके इसकी इसका कुछ सब सभी बहुत रहा रही रहे दिख दिखा दिखाई दिखे दिखती दिखता देख देखा देखी देखे पकड़ा भागा भाग मारा मार कहा बोला जा रहा रही रहे मैं मेरा मेरी मेरे हमारे इस उस वह वे यह ये जो जिसने जिन्होंने कि क्योंकि
एक दो तीन चार अज्ञात अनजान व्यक्ति व्यक्ती युवक लड़का लड़की महिला आदमी लोग लोगों युवकों लड़कों बदमाश बदमाशों
दिनांक तारीख को दिन रात सुबह शाम दोपहर बजे घटना समय वेळ पोलीस पुलिस थाना थाने जिला जिल्हा धारा कलम
मौजूद उपस्थित थे आये आया आई गवाह साक्षीदार आरोपी शिकायतकर्ता तक्रारदार फिर्यादी जांच तपास अधिकारी मोबाइल मोबाईल फोन नंबर नं क्रमांक
चोरी धोखाधड़ी नकद रुपये रुपए रु लाख हजार करोड़ खाता खाते गाड़ी बाइक मोटरसायकल स्कूटर कार मकान दुकान
पता पत्ता संख्या के लिए साथ बाद पहले अंदर बाहर ऊपर नीचे जब तब यहां वहां
""".split())
DEV_TITLES = {"श्री", "श्रीमती", "सुश्री", "कुमारी", "स्वर्गीय", "श्रीमान", "मिस्टर", "मिसेज", "डॉ", "डॉक्टर", "शेठ", "सेठ", "स्व", "पंडित", "सब", "इंस्पेक्टर", "इन्स्पेक्टर", "उपनिरीक्षक",
              "निरीक्षक", "सहायक", "पोलीस", "पुलिस", "हेड", "कांस्टेबल", "कॉन्स्टेबल", "एएसआई", "एएसआय", "एसआई", "पीएसआय", "थानाध्यक्ष", "थानेदार", "सिपाही", "हवलदार", "पति", "पत्नी", "सास", "ससुर", "ननद", "देवर", "जेठ", "पड़ोसी", "पडोसी", "शेजारी", "किरायेदार", "मालिक", "दोस्त", "मित्र", "भाई", "बहन", "कर्मचारी", "नौकर"}
DEV_SURNAMES = set("""शर्मा वर्मा सिंह कुमार यादव गुप्ता गुप्त मिश्रा मिश्र पांडेय पाण्डेय पांडे पाण्डे दुबे द्विवेदी त्रिवेदी तिवारी त्रिपाठी शुक्ला शुक्ल सिन्हा श्रीवास्तव अग्रवाल अरोड़ा अरोरा कपूर खन्ना मल्होत्रा चौहान चौधरी चौबे राठौर राठौड़ राजपूत ठाकुर रावत पटेल पाटील पवार शिंदे जाधव मोरे गायकवाड देशमुख कुलकर्णी जोशी कदम भोसले सावंत मीणा जांगिड़ जांगिड त्यागी सैनी जैन देवी बाई खान अहमद अली हुसैन शेख सिद्दीकी कुरैशी नायर मेनन रेड्डी राव नायडू अय्यर वाघमारे काळे गोरे थोरात लाल प्रसाद चंद्र चंद दास घोष बनर्जी चटर्जी मुखर्जी सेन बोस वाल्मीकि बघेल साहू कश्यप निषाद केवट कुशवाहा मौर्य कोरी पासवान भारती बिष्ट नेगी रावल भाटी पांचाल""".split())

LATIN_ORG_WORDS = {"traders", "pvt", "ltd", "limited", "enterprises", "company", "co", "bank", "store", "stores", "agency", "industries", "solutions", "services", "corporation", "firm", "trust", "foundation", "motors", "jewellers", "jewelers", "finance", "hospital", "school", "college"}

HARM_WORDS = re.compile(r"chori|stolen|theft|snatch|missing|gumshuda|gayab|lapata|lapta|kidnap|abduct|beaten|assault|injur|hurt|threat|rape|molest|murder|killed|died|cheat|fraud|thagi|"
                        r"चोरी|चोरले|चोरीस|चुरा|छीन|लापता|गुम|गायब|अपहरण|मारपीट|मारा|धमकी|धोखा|ठगी|बलात्कार|हत्या|"
                        r"नहीं\s+आई|नहीं\s+लौट|waapas\s+nahi|vapas\s+nahi|nahi\s+aayi|nahi\s+lauti|nahi\s+mila|bahla\s+fusla|bhaga\s+kar|bhagakar|le\s+gaya|le\s+gaye", re.I)


# function words / postpositions of the other Indian scripts: a person name never contains them
OTHER_STOP = set("""
સાથે ને નો ની નું ના થી માં પર અને એ આ તે છે હતું હતો હતી થયું થયો થઈ કરી કર્યું કર્યો બે એક ત્રણ
র এর কে থেকে ও এবং এই সেই আছে ছিল হয় হয়েছে করে করেছে দ্বারা জন্য মোবাইল ফোন টাকা তারিখ
மற்றும் என்ற இடம் ஒரு இந்த அந்த உள்ளது ஆகிய ஆல் இல் க்கு
మరియు ఒక ఈ ఆ ఉంది లో కి ను తో నుండి నుంచి కు పై యొక్క ద్వారా వద్ద
ಮತ್ತು ಒಂದು ಈ ಆ ಇದೆ ಅವರು
ഒരു ഈ ആ ഉം ഒപ്പം
ਨੇ ਨੂੰ ਤੋਂ ਵਿੱਚ ਦਾ ਦੀ ਦੇ ਅਤੇ ਇੱਕ ਸੀ ਹੈ
کو سے میں کی کا کے اور ایک تھی تھا ہے ہیں
""".split())

# ranks / honorifics written before names in other scripts (skipped when reading a name)
OTHER_TITLES = set("""
এসআই এএসআই ইন্সপেক্টর শ্রী শ্রীমতি
திரு திருமதி
શ્રી શ્રીમતી પીએસઆઈ
శ్రీ శ్రీమతి
ಶ್ರೀ ಶ್ರೀಮತಿ
ശ്രീ ശ്രീമതി
ਸ੍ਰੀ ਸ਼੍ਰੀ ਸ੍ਰੀਮਤੀ
جناب محترمہ
""".split())
