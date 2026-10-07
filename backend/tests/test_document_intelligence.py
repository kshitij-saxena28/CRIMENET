from ai_engine.nlp.extractor import analyze_document


def test_fir_structured_english():
    text = '''FIRST INFORMATION REPORT
1. District: METROPOLITAN CENTRAL P.S: CITY CENTER Year: 2026 FIR No: 0452/2026
Date of FIR: 05/09/2026 Time: 14:30 hrs
2. Acts & Sections:
- Indian Penal Code (IPC): Section 379 (Theft), Section 411 (Dishonestly receiving stolen property)
5. Place of Occurrence:
- Address: Main Parking Lot, Sector 4 Market Complex, Greater Noida
6. Complainant / Informant:
- Name: Rahul Sharma
- Father's Name: Alok Sharma
- Address: Flat 402, Alpine Towers, Greater Noida
7. Details of Known / Suspected / Unknown Accused with Full Particulars:
- One unknown person, wearing a black jacket and helmet, riding a black scooter (No plate visible).
8. Details of Properties Stolen / Involved:
- 1x Slate Gray Laptop (Model: Ultimate-Book, Serial No: SN-987654321-X)
9. Short Description of Incident (Brief Facts): The complainant discovered the laptop missing. CCTV footage shows a masked rider fleeing.
10. Action Taken / Direction for Investigation:
- Investigating Officer assigned: ASI Vikram Singh (Batch No. 8841)
'''
    r = analyze_document(text, {"language": "English", "ocr_confidence": 0.95})
    s = r["structured"]
    assert r["document_type"] == "FIR"
    assert s["fir_number"] == "0452/2026"
    assert s["sections"] == ["379", "411"]
    assert s["complainant"] == "Rahul Sharma"
    assert s["place_of_occurrence"].startswith("Main Parking Lot")
    assert "black scooter" in s["accused_description"]
    assert s["detected_events"]


def test_fir_multilingual_fields():
    text = '''प्रथम सूचना रिपोर्ट
जिला: दिल्ली थाना: सिटी सेंटर
एफआईआर क्रमांक: 0452/2026 दिनांक: 05/09/2026
शिकायतकर्ता: राहुल शर्मा
पिता का नाम: आलोक शर्मा
पता: ग्रेटर नोएडा
आरोपी: एक अज्ञात व्यक्ति काले हेलमेट में
चोरी की संपत्ति: लैपटॉप और बैग
जांच अधिकारी: एएसआई विक्रम सिंह
'''
    r = analyze_document(text, {"language": "Hindi", "ocr_confidence": 0.94})
    s = r["structured"]
    assert r["document_type"] == "FIR"
    assert s["fir_number"] == "0452/2026"
    assert s["complainant"] == "राहुल शर्मा"
    assert s["accused_description"].startswith("एक अज्ञात")
    assert s["stolen_property"] == ["लैपटॉप और बैग"]


def test_marathi_structured_and_english_view():
    text = """प्रथम माहिती अहवाल
जिल्हा: पुणे पोलीस ठाणे: सिटी सेंटर वर्ष: 2026 एफआयआर क्रमांक: 0452/2026
दिनांक: 05/09/2026 वेळ: 14:30
तक्रारदार: राहुल शर्मा
वडिलांचे नाव: आलोक शर्मा
पत्ता: ग्रेटर नोएडा
आरोपी: एक अज्ञात व्यक्ती काळ्या हेल्मेटमध्ये
घटनेचे ठिकाण: मुख्य पार्किंग जागा, सेक्टर 4 मार्केट कॉम्प्लेक्स, ग्रेटर नोएडा
चोरीची मालमत्ता: लॅपटॉप आणि बॅग
तपास अधिकारी: एएसआय विक्रम सिंह"""
    r = analyze_document(text, {"language": "Marathi", "ocr_confidence": 0.95})
    s = r["structured"]
    assert s["police_station"] == "सिटी सेंटर"
    assert s["fir_time"] == "14:30"
    assert s["complainant"] == "राहुल शर्मा"
    assert s["place_of_occurrence"].startswith("मुख्य पार्किंग")
    assert "Laptop" in r["english_view"]["fields"]["stolen_property"][0]
    assert "rahul sharma" in r["english_view"]["fields"]["complainant"].casefold()


def test_fir_standalone_police_station_label_is_not_confused_with_year():
    text = '''FIRST INFORMATION REPORT
District: Ghaziabad
Police Station: Indirapuram
Year: 2026
FIR No: 0142/2026
Date of FIR: 12/08/2026
Complainant: Rahul Sharma
'''
    r = analyze_document(text, {"language": "English"})
    assert r["structured"]["police_station"] == "Indirapuram"
