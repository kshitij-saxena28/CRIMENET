"""Build the offline map assets (run once by a developer; the results are committed, nothing is fetched at runtime).

Inputs (Natural Earth, public domain, https://www.naturalearthdata.com/), downloaded beforehand:
    ne_50m_admin_0_countries.geojson
    ne_50m_admin_1_states_provinces.geojson
    ne_10m_populated_places_simple.geojson
    python scripts/build_geo_assets.py <folder with those three files>

Outputs:
    web/data/south_asia_outline.json          neighbouring countries + Indian states, simplified for a zoomed-out map
    backend/app/data/gazetteer_in.json        approximate coordinates for Indian cities, districts, states and NCR localities

The outline is for orientation only. It is NOT an official boundary of India; do not use it for legal or territorial decisions.
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")

NEIGHBOURS = {"Pakistan", "China", "Nepal", "Bhutan", "Bangladesh", "Myanmar", "Sri Lanka", "Afghanistan", "Maldives",
              "Tajikistan", "Uzbekistan", "Turkmenistan", "Iran", "Thailand", "Laos", "Oman", "United Arab Emirates", "Mongolia", "Kyrgyzstan", "Kazakhstan"}


def rdp(pts, eps):
    """Douglas-Peucker on an open polyline (iterative)."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts); keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = pts[a], pts[b]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy) or 1e-12
        idx, dmax = -1, 0.0
        for i in range(a + 1, b):
            d = abs(dy * pts[i][0] - dx * pts[i][1] + x2 * y1 - y2 * x1) / norm
            if d > dmax:
                idx, dmax = i, d
        if dmax > eps:
            keep[idx] = True; stack.append((a, idx)); stack.append((idx, b))
    return [p for p, k in zip(pts, keep) if k]


def rdp_ring(ring, eps):
    """Simplify a closed ring: split at the middle vertex so the anchor points differ."""
    m = len(ring) // 2
    a = rdp(ring[:m + 1], eps); b = rdp(ring[m:], eps)
    return a[:-1] + b


def ring_area(r):
    return abs(sum(r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1] for i in range(len(r) - 1))) / 2


def simplify_geom(geom, eps, min_area):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    out = []
    for poly in polys:
        outer = poly[0]
        if ring_area(outer) < min_area:
            continue
        rings = []
        for i, ring in enumerate(poly):
            if i and ring_area(ring) < min_area:
                continue
            s = rdp_ring([tuple(p[:2]) for p in ring], eps)
            if len(s) >= 4:
                rings.append([[round(x, 2), round(y, 2)] for x, y in s])
        if rings:
            out.append(rings)
    if not out:
        return None
    return {"type": "MultiPolygon", "coordinates": out}


def centroid(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    big = max(polys, key=lambda p: ring_area(p[0]))[0]
    a = cx = cy = 0.0
    for i in range(len(big) - 1):
        f = big[i][0] * big[i + 1][1] - big[i + 1][0] * big[i][1]
        a += f; cx += (big[i][0] + big[i + 1][0]) * f; cy += (big[i][1] + big[i + 1][1]) * f
    a *= 3
    return (cy / a, cx / a) if a else (big[0][1], big[0][0])


# name, lat, lon, state -- approximate city-centre coordinates (about +/-5 km); aliases are spelling variants used in FIRs.
EXTRA = [
    ("Noida", 28.5355, 77.3910, "Uttar Pradesh", ["Gautam Buddh Nagar"]), ("Greater Noida", 28.4744, 77.5040, "Uttar Pradesh", []),
    ("Gurugram", 28.4595, 77.0266, "Haryana", ["Gurgaon"]), ("Thane", 19.2183, 72.9781, "Maharashtra", []),
    ("Navi Mumbai", 19.0330, 73.0297, "Maharashtra", []), ("Vasai", 19.3919, 72.8397, "Maharashtra", ["Vasai-Virar"]),
    ("Pimpri-Chinchwad", 18.6298, 73.7997, "Maharashtra", []), ("Nashik", 19.9975, 73.7898, "Maharashtra", ["Nasik"]),
    ("Solapur", 17.6599, 75.9064, "Maharashtra", ["Sholapur"]), ("Jalgaon", 21.0077, 75.5626, "Maharashtra", []),
    ("Satara", 17.6805, 74.0183, "Maharashtra", []), ("Ratnagiri", 16.9902, 73.3120, "Maharashtra", []),
    ("Jamnagar", 22.4707, 70.0577, "Gujarat", []), ("Junagadh", 21.5222, 70.4579, "Gujarat", []), ("Bharuch", 21.7051, 72.9959, "Gujarat", []),
    ("Mehsana", 23.5880, 72.3693, "Gujarat", []), ("Anand", 22.5645, 72.9289, "Gujarat", []),
    ("Kurukshetra", 29.9695, 76.8783, "Haryana", []), ("Yamunanagar", 30.1290, 77.2674, "Haryana", []), ("Rewari", 28.1990, 76.6183, "Haryana", []),
    ("Jind", 29.3160, 76.3140, "Haryana", []), ("Nuh", 28.1069, 77.0028, "Haryana", ["Mewat"]), ("Palwal", 28.1487, 77.3320, "Haryana", []),
    ("Jhajjar", 28.6070, 76.6570, "Haryana", []), ("Mohali", 30.7046, 76.7179, "Punjab", ["SAS Nagar", "Sahibzada Ajit Singh Nagar"]),
    ("Panchkula", 30.6942, 76.8606, "Haryana", []), ("Bathinda", 30.2110, 74.9455, "Punjab", []), ("Jalandhar", 31.3260, 75.5762, "Punjab", ["Jullundur"]),
    ("Haridwar", 29.9457, 78.1642, "Uttarakhand", []), ("Rishikesh", 30.0869, 78.2676, "Uttarakhand", []), ("Nainital", 29.3803, 79.4636, "Uttarakhand", []),
    ("Haldwani", 29.2183, 79.5130, "Uttarakhand", []), ("Roorkee", 29.8543, 77.8880, "Uttarakhand", []), ("Dehradun", 30.3165, 78.0322, "Uttarakhand", ["Dehra Dun"]),
    ("Shimla", 31.1048, 77.1734, "Himachal Pradesh", ["Simla"]), ("Prayagraj", 25.4358, 81.8463, "Uttar Pradesh", ["Allahabad"]),
    ("Ayodhya", 26.7922, 82.1998, "Uttar Pradesh", ["Faizabad"]), ("Azamgarh", 26.0685, 83.1859, "Uttar Pradesh", []),
    ("Ghazipur", 25.5880, 83.5780, "Uttar Pradesh", []), ("Jaunpur", 25.7464, 82.6837, "Uttar Pradesh", []), ("Unnao", 26.5393, 80.4878, "Uttar Pradesh", []),
    ("Raebareli", 26.2309, 81.2338, "Uttar Pradesh", ["Rae Bareli"]), ("Sultanpur", 26.2648, 82.0727, "Uttar Pradesh", []), ("Barabanki", 26.9266, 81.1835, "Uttar Pradesh", []),
    ("Sambhal", 28.5904, 78.5718, "Uttar Pradesh", []), ("Amroha", 28.9031, 78.4676, "Uttar Pradesh", []), ("Bijnor", 29.3724, 78.1364, "Uttar Pradesh", []),
    ("Shamli", 29.4497, 77.3110, "Uttar Pradesh", []), ("Baghpat", 28.9484, 77.2186, "Uttar Pradesh", []), ("Jaisalmer", 26.9157, 70.9083, "Rajasthan", []),
    ("Barmer", 25.7532, 71.4181, "Rajasthan", []), ("Chittorgarh", 24.8887, 74.6269, "Rajasthan", []), ("Jhunjhunu", 28.1289, 75.3980, "Rajasthan", []),
    ("Sri Ganganagar", 29.9038, 73.8772, "Rajasthan", ["Ganganagar"]), ("Rewa", 24.5362, 81.3037, "Madhya Pradesh", []), ("Satna", 24.6005, 80.8322, "Madhya Pradesh", []),
    ("Chhindwara", 22.0574, 78.9382, "Madhya Pradesh", []), ("Dewas", 22.9676, 76.0534, "Madhya Pradesh", []), ("Khandwa", 21.8314, 76.3498, "Madhya Pradesh", []),
    ("Morena", 26.4980, 78.0008, "Madhya Pradesh", []), ("Durg", 21.1904, 81.2849, "Chhattisgarh", []), ("Korba", 22.3595, 82.7501, "Chhattisgarh", []),
    ("Raigarh", 21.8974, 83.3950, "Chhattisgarh", []), ("Jagdalpur", 19.0700, 82.0300, "Chhattisgarh", []), ("Bokaro", 23.6693, 86.1511, "Jharkhand", ["Bokaro Steel City"]),
    ("Hazaribagh", 23.9925, 85.3637, "Jharkhand", []), ("Deoghar", 24.4764, 86.6946, "Jharkhand", []), ("Dumka", 24.2676, 87.2497, "Jharkhand", []),
    ("Darbhanga", 26.1542, 85.8918, "Bihar", []), ("Arrah", 25.5541, 84.6606, "Bihar", ["Ara"]), ("Begusarai", 25.4182, 86.1272, "Bihar", []),
    ("Munger", 25.3708, 86.4734, "Bihar", ["Monghyr"]), ("Saharsa", 25.8778, 86.5947, "Bihar", []), ("Katihar", 25.5392, 87.5714, "Bihar", []),
    ("Sasaram", 24.9520, 84.0300, "Bihar", []), ("Bettiah", 26.8015, 84.5036, "Bihar", []), ("Chhapra", 25.7800, 84.7500, "Bihar", ["Chapra"]),
    ("Durgapur", 23.5204, 87.3119, "West Bengal", []), ("Kharagpur", 22.3460, 87.2320, "West Bengal", []), ("Howrah", 22.5958, 88.2636, "West Bengal", ["Haora"]),
    ("Baharampur", 24.1042, 88.2510, "West Bengal", ["Berhampore"]), ("Malda", 25.0108, 88.1411, "West Bengal", ["English Bazar"]),
    ("Cooch Behar", 26.3452, 89.4482, "West Bengal", []), ("Darjeeling", 27.0360, 88.2627, "West Bengal", []),
    ("Bhadrak", 21.0544, 86.5000, "Odisha", []), ("Balasore", 21.4934, 86.9336, "Odisha", ["Baleshwar"]), ("Koraput", 18.8135, 82.7123, "Odisha", []),
    ("Visakhapatnam", 17.6868, 83.2185, "Andhra Pradesh", ["Vishakhapatnam", "Vizag"]), ("Eluru", 16.7107, 81.0952, "Andhra Pradesh", []),
    ("Anantapur", 14.6819, 77.6006, "Andhra Pradesh", []), ("Kadapa", 14.4673, 78.8242, "Andhra Pradesh", ["Cuddapah"]), ("Chittoor", 13.2172, 79.1003, "Andhra Pradesh", []),
    ("Adilabad", 19.6641, 78.5320, "Telangana", []), ("Mahbubnagar", 16.7488, 77.9855, "Telangana", []), ("Secunderabad", 17.4399, 78.4983, "Telangana", []),
    ("Thoothukudi", 8.7642, 78.1348, "Tamil Nadu", ["Tuticorin"]), ("Tiruchirappalli", 10.7905, 78.7047, "Tamil Nadu", ["Trichy", "Tiruchi"]),
    ("Erode", 11.3410, 77.7172, "Tamil Nadu", []), ("Hosur", 12.7409, 77.8253, "Tamil Nadu", []), ("Kanyakumari", 8.0883, 77.5385, "Tamil Nadu", []),
    ("Thrissur", 10.5276, 76.2144, "Kerala", ["Trichur"]), ("Kannur", 11.8745, 75.3704, "Kerala", ["Cannanore"]), ("Palakkad", 10.7867, 76.6548, "Kerala", ["Palghat"]),
    ("Kottayam", 9.5916, 76.5222, "Kerala", []), ("Malappuram", 11.0510, 76.0711, "Kerala", []), ("Ernakulam", 9.9816, 76.2999, "Kerala", []),
    ("Udupi", 13.3409, 74.7421, "Karnataka", []), ("Hassan", 13.0068, 76.1004, "Karnataka", []), ("Dharwad", 15.4589, 75.0078, "Karnataka", []),
    ("Margao", 15.2832, 73.9862, "Goa", ["Madgaon"]), ("Vasco da Gama", 15.3982, 73.8113, "Goa", []),
    ("Dimapur", 25.9091, 93.7266, "Nagaland", []), ("Lunglei", 22.8800, 92.7300, "Mizoram", []), ("Nagaon", 26.3500, 92.6800, "Assam", []),
    ("Tinsukia", 27.4922, 95.3468, "Assam", []), ("Bongaigaon", 26.4760, 90.5590, "Assam", []),
    # Delhi / NCR localities
    ("Connaught Place", 28.6315, 77.2167, "Delhi", ["CP"]), ("Karol Bagh", 28.6519, 77.1909, "Delhi", []), ("Rohini", 28.7495, 77.0565, "Delhi", []),
    ("Dwarka", 28.5921, 77.0460, "Delhi", []), ("Saket", 28.5245, 77.2066, "Delhi", []), ("Lajpat Nagar", 28.5677, 77.2433, "Delhi", []),
    ("Nehru Place", 28.5491, 77.2533, "Delhi", []), ("Chandni Chowk", 28.6506, 77.2303, "Delhi", []), ("Okhla", 28.5355, 77.2745, "Delhi", []),
    ("Anand Vihar", 28.6469, 77.3160, "Delhi", []), ("Vasant Kunj", 28.5200, 77.1580, "Delhi", []), ("Janakpuri", 28.6219, 77.0878, "Delhi", []),
    ("Pitampura", 28.7000, 77.1310, "Delhi", []), ("Mayur Vihar", 28.6080, 77.2940, "Delhi", []), ("Laxmi Nagar", 28.6304, 77.2773, "Delhi", []),
    ("Shahdara", 28.6733, 77.2896, "Delhi", []), ("Paharganj", 28.6448, 77.2137, "Delhi", []), ("Kashmere Gate", 28.6675, 77.2280, "Delhi", []),
    ("Rajouri Garden", 28.6492, 77.1216, "Delhi", []), ("Hauz Khas", 28.5494, 77.2001, "Delhi", []), ("Greater Kailash", 28.5487, 77.2380, "Delhi", ["GK"]),
    ("Kalkaji", 28.5400, 77.2580, "Delhi", []), ("Shalimar Bagh", 28.7170, 77.1600, "Delhi", []), ("Aerocity", 28.5562, 77.1199, "Delhi", []),
    ("IGI Airport", 28.5562, 77.1000, "Delhi", ["Indira Gandhi International Airport", "Delhi Airport"]),
    ("New Delhi Railway Station", 28.6431, 77.2197, "Delhi", ["NDLS"]), ("Sadar Bazar", 28.6600, 77.2100, "Delhi", []),
    ("Red Fort", 28.6562, 77.2410, "Delhi", ["Lal Qila"]), ("Sarai Rohilla", 28.6640, 77.1860, "Delhi", []),
    ("Cyber Hub", 28.4950, 77.0890, "Haryana", ["Gurugram Cyber Hub"]), ("MG Road", 28.4790, 77.0800, "Haryana", []),
]
# Alternative spellings for cities that Natural Earth lists under a different transliteration.
ALIASES = {
    "Mumbai": ["Bombay"], "Kolkata": ["Calcutta"], "Chennai": ["Madras"], "Bengaluru": ["Bangalore", "Bengalooru"], "Pune": ["Poona"],
    "Vadodara": ["Baroda"], "Puducherry": ["Pondicherry"], "Mysuru": ["Mysore"], "Mangaluru": ["Mangalore"], "Kozhikode": ["Calicut"],
    "Belagavi": ["Belgaum"], "Ballari": ["Bellary"], "Hubballi": ["Hubli"], "Kalaburagi": ["Gulbarga"], "Shivamogga": ["Shimoga"],
    "Tumakuru": ["Tumkur"], "Vijayapura": ["Bijapur"], "Brahmapur": ["Berhampur"], "Raurkela": ["Rourkela"], "Barddhaman": ["Burdwan", "Bardhaman"],
    "Medinipur": ["Midnapore"], "Kochi": ["Cochin"], "Thiruvananthapuram": ["Trivandrum"], "Kanpur": ["Cawnpore"], "Varanasi": ["Benares", "Banaras"],
    "Delhi": ["New Delhi", "NCT of Delhi", "Old Delhi"], "Jullundur": ["Jalandhar"], "Panaji": ["Panjim"], "Guwahati": ["Gauhati"], "Vishakhapatnam": ["Visakhapatnam"],
    "Gurgaon": ["Gurugram"], "Allahabad": ["Prayagraj"], "Simla": ["Shimla"], "Dehra Dun": ["Dehradun"], "Faizabad": ["Ayodhya"], "Nasik": ["Nashik"],
    "Sholapur": ["Solapur"], "Haora": ["Howrah"], "Tuticorin": ["Thoothukudi"], "Amravati": ["Amaravati"],
}
STATE_ALIASES = {"Odisha": ["Orissa"], "Uttarakhand": ["Uttaranchal"], "Delhi": ["NCT of Delhi", "Delhi NCR", "NCR"], "Andaman and Nicobar": ["Andaman and Nicobar Islands", "Andaman & Nicobar"],
                 "Jammu and Kashmir": ["J&K", "Jammu & Kashmir"], "Dadra and Nagar Haveli and Daman and Diu": ["Daman and Diu", "Dadra and Nagar Haveli"]}


def main():
    states = json.loads((SRC / "ne_50m_admin_1_states_provinces.geojson").read_text(encoding="utf-8"))
    countries = json.loads((SRC / "ne_50m_admin_0_countries.geojson").read_text(encoding="utf-8"))
    places = json.loads((SRC / "ne_10m_populated_places_simple.geojson").read_text(encoding="utf-8"))

    ind_states = [f for f in states["features"] if f["properties"]["adm0_a3"] == "IND"]
    out_states = []
    gaz = []
    for f in ind_states:
        g = simplify_geom(f["geometry"], 0.03, 0.0002)
        if g:
            out_states.append({"type": "Feature", "properties": {"name": f["properties"]["name"]}, "geometry": g})
        lat, lon = centroid(f["geometry"])
        gaz.append({"name": f["properties"]["name"], "aliases": STATE_ALIASES.get(f["properties"]["name"], []), "lat": round(lat, 4), "lon": round(lon, 4), "kind": "state", "state": f["properties"]["name"]})
    out_countries = []
    for f in countries["features"]:
        n = f["properties"].get("NAME") or f["properties"].get("name")
        if n in NEIGHBOURS:
            g = simplify_geom(f["geometry"], 0.08, 0.02)
            if g:
                out_countries.append({"type": "Feature", "properties": {"name": n}, "geometry": g})
    outline = {
        "attribution": "Natural Earth (public domain). Simplified outline for orientation only; not an official boundary of India.",
        "countries": {"type": "FeatureCollection", "features": out_countries},
        "states": {"type": "FeatureCollection", "features": out_states},
    }
    seen = set()
    for f in places["features"]:
        p = f["properties"]
        if p["adm0name"] != "India":
            continue
        name = p["name"]
        key = (name.lower(), p.get("adm1name"))
        if key in seen:
            continue
        seen.add(key)
        al = list(ALIASES.get(name, []))
        if p.get("nameascii") and p["nameascii"] != name:
            al.append(p["nameascii"])
        gaz.append({"name": name, "aliases": al, "lat": round(float(p["latitude"]), 4), "lon": round(float(p["longitude"]), 4), "kind": "city", "state": p.get("adm1name") or "", "pop": int(p.get("pop_max") or 0)})
    # Biggest cities double as offline map labels (name, lat, lon).
    big = sorted((g for g in gaz if g["kind"] == "city" and g.get("pop")), key=lambda g: -g["pop"])[:60]
    outline["cities"] = [[g["name"], g["lat"], g["lon"]] for g in big]
    dest = ROOT / "web" / "data"; dest.mkdir(parents=True, exist_ok=True)
    (dest / "south_asia_outline.json").write_text(json.dumps(outline, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    have = {g["name"].lower() for g in gaz}
    for name, lat, lon, state, al in EXTRA:
        if name.lower() in have:
            continue
        gaz.append({"name": name, "aliases": al, "lat": lat, "lon": lon, "kind": "locality" if state in ("Delhi",) or name in ("Cyber Hub", "MG Road") else "city", "state": state})
    gdest = ROOT / "backend" / "app" / "data"; gdest.mkdir(parents=True, exist_ok=True)
    (gdest / "gazetteer_in.json").write_text(json.dumps({
        "note": "Approximate city-level coordinates (Natural Earth populated places, public domain, plus hand-added district HQs and Delhi/NCR localities). Accuracy about 5-10 km; not for legal boundaries.",
        "places": gaz}, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print("states", len(out_states), "countries", len(out_countries), "gazetteer", len(gaz))


if __name__ == "__main__":
    main()
