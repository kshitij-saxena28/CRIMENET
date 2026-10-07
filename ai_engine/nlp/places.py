"""Gazetteer of Indian cities, states and common locality words used to spell place names correctly in English.

Matching is (1) exact native spelling (Hindi/Marathi list below) or (2) a consonant-skeleton match of the transliteration
(so "কলকাতা", "కోల్కతా" and "Kolkata" meet), accepted only when exactly one place has that skeleton and it is at least
three consonants long.  Matched places are flagged "place_match" so a reviewer can see it was inferred from the sound.
"""
from __future__ import annotations

from ai_engine.nlp.translit import skeleton

# English | native spellings (Devanagari) separated by comma
_PLACES = """
Delhi | दिल्ली, नई दिल्ली, देहली
New Delhi | नई दिल्ली
Mumbai | मुंबई, मुम्बई, बंबई
Pune | पुणे, पुण्यात, पुण्याला
Thane | ठाणे, ठाण्यात
Nashik | नाशिक, नासिक, नाशिकमध्ये
Nagpur | नागपूर, नागपुर
Aurangabad | औरंगाबाद
Kolhapur | कोल्हापूर, कोल्हापुर
Solapur | सोलापूर, सोलापुर
Navi Mumbai | नवी मुंबई
Lucknow | लखनऊ, लखनउ
Kanpur | कानपुर
Agra | आगरा
Varanasi | वाराणसी, बनारस
Prayagraj | प्रयागराज, इलाहाबाद
Ghaziabad | गाजियाबाद, ग़ाज़ियाबाद
Noida | नोएडा
Greater Noida | ग्रेटर नोएडा
Meerut | मेरठ
Gorakhpur | गोरखपुर
Bareilly | बरेली
Aligarh | अलीगढ़
Mathura | मथुरा
Jaipur | जयपुर
Jodhpur | जोधपुर
Udaipur | उदयपुर
Kota | कोटा
Ajmer | अजमेर
Bikaner | बीकानेर
Bhopal | भोपाल
Indore | इंदौर, इन्दौर
Gwalior | ग्वालियर
Jabalpur | जबलपुर
Ujjain | उज्जैन
Patna | पटना
Gaya | गया
Muzaffarpur | मुजफ्फरपुर
Ranchi | रांची, राँची
Jamshedpur | जमशेदपुर
Dhanbad | धनबाद
Kolkata | कोलकाता, कलकत्ता
Howrah | हावड़ा
Siliguri | सिलीगुड़ी
Bhubaneswar | भुवनेश्वर
Cuttack | कटक
Guwahati | गुवाहाटी
Chandigarh | चंडीगढ़, चण्डीगढ़
Ludhiana | लुधियाना
Amritsar | अमृतसर
Jalandhar | जालंधर, जलंधर
Patiala | पटियाला
Gurugram | गुरुग्राम, गुड़गांव, गुरुग्राम
Faridabad | फरीदाबाद
Panipat | पानीपत
Rohtak | रोहतक
Hisar | हिसार
Dehradun | देहरादून
Haridwar | हरिद्वार
Shimla | शिमला
Srinagar | श्रीनगर
Jammu | जम्मू
Raipur | रायपुर
Bilaspur | बिलासपुर
Ahmedabad | अहमदाबाद
Surat | सूरत
Vadodara | वडोदरा, बड़ौदा
Rajkot | राजकोट
Gandhinagar | गांधीनगर
Hyderabad | हैदराबाद
Secunderabad | सिकंदराबाद
Visakhapatnam | विशाखापत्तनम
Vijayawada | विजयवाड़ा
Chennai | चेन्नई, मद्रास
Coimbatore | कोयंबटूर
Madurai | मदुरै
Bengaluru | बेंगलुरु, बंगलौर, बेंगलूरु
Mysuru | मैसूर
Kochi | कोच्चि, कोचीन
Thiruvananthapuram | तिरुवनंतपुरम
Kozhikode | कोझिकोड
Goa | गोवा
Panaji | पणजी
Uttar Pradesh | उत्तर प्रदेश, उत्तरप्रदेश
Madhya Pradesh | मध्य प्रदेश, मध्यप्रदेश
Maharashtra | महाराष्ट्र
Rajasthan | राजस्थान
Bihar | बिहार
Jharkhand | झारखंड
West Bengal | पश्चिम बंगाल
Odisha | ओडिशा, उड़ीसा
Punjab | पंजाब
Haryana | हरियाणा
Uttarakhand | उत्तराखंड
Himachal Pradesh | हिमाचल प्रदेश
Gujarat | गुजरात
Karnataka | कर्नाटक
Tamil Nadu | तमिलनाडु, तमिल नाडु
Kerala | केरल
Telangana | तेलंगाना
Andhra Pradesh | आंध्र प्रदेश
Assam | असम
Chhattisgarh | छत्तीसगढ़
"""

_EXACT: dict[str, str] = {}
_SKEL: dict[str, set] = {}
for _line in _PLACES.strip().splitlines():
    _eng, _nat = [x.strip() for x in _line.split("|", 1)]
    for _n in _nat.split(","):
        _n = _n.strip()
        if _n:
            _EXACT[_n] = _eng
    _k = skeleton(_eng.replace(" ", ""))
    if len(_k) >= 3:
        _SKEL.setdefault(_k, set()).add(_eng)
# hand-added roman variants that the skeleton misses
for _alt, _eng in {"lakhnau": "Lucknow", "kalkatta": "Kolkata", "banglore": "Bengaluru", "bangalore": "Bengaluru", "bombay": "Mumbai", "madras": "Chennai",
                   "vizag": "Visakhapatnam", "baroda": "Vadodara", "gurgaon": "Gurugram", "calcutta": "Kolkata", "allahabad": "Prayagraj"}.items():
    _k = skeleton(_alt)
    if len(_k) >= 3:
        _SKEL.setdefault(_k, set()).add(_eng)

COMMON_ENGLISH_NAMES = {"Delhi", "Mumbai", "Pune", "Thane", "Nashik", "Lucknow", "Kolkata", "Chennai", "Hyderabad", "Ahmedabad", "Patna", "Jalandhar"}


def lookup(native: str, latin: str = "") -> tuple[str, str] | None:
    """(English name, how) for a word in native script, or None.  how is 'gazetteer' (exact) or 'sound' (skeleton)."""
    if native in _EXACT:
        return _EXACT[native], "gazetteer"
    if latin:
        k = skeleton(latin)
        if len(k) >= 3:
            hits = _SKEL.get(k, set())
            if len(hits) == 1:
                return next(iter(hits)), "sound"
    return None
