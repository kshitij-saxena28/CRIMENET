"""Movement / pattern-of-life analysis of observation-log entries. Pure functions, offline.

Input entries (current versions only) are dicts with: seq, t (naive UTC datetime), lat, lon (recorded or None), place (gazetteer name or ''),
place_lat, place_lon (approximate, or None), location_text, subject_seen, persons [{key,name,role}], vehicles [{reg}].
Every output sentence states what was observed; none states what it means. Gaps in coverage are never evidence of absence.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import median

from ai_engine.social.timeutil import fmt_local, to_local, zone_label

CLUSTER_KM = 0.25
MAX_PLAUSIBLE_KMH = 130.0


def haversine_km(a_lat, a_lon, b_lat, b_lon) -> float:
    r = 6371.0088
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi, dl = p2 - p1, math.radians(b_lon - a_lon)
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def _norm_place(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _points(entries):
    pts = []
    for e in entries:
        if e.get("t") is None:
            continue
        if e.get("lat") is not None and e.get("lon") is not None:
            lat, lon, prec = e["lat"], e["lon"], "recorded"
        elif e.get("place_lat") is not None:
            lat, lon, prec = e["place_lat"], e["place_lon"], "approximate"
        else:
            continue
        pts.append({"seq": e["seq"], "t": e["t"], "lat": lat, "lon": lon, "precision": prec, "label": e.get("location_text") or e.get("place") or "", "place": e.get("place") or "",
                    "subject_seen": e.get("subject_seen", True)})
    pts.sort(key=lambda p: p["t"])
    return pts


def _dur(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def analyse(entries: list[dict], tz: str = "IST", window_min: int = 30, radius_m: int = 300, subject_name: str = "the subject") -> dict:
    tzl = zone_label(tz)
    pts = _points(entries)
    subj_pts = [p for p in pts if p["subject_seen"]]
    # ---- dwell clusters
    clusters: list[dict] = []
    for p in subj_pts:
        home = None
        for c in clusters:
            if haversine_km(c["lat"], c["lon"], p["lat"], p["lon"]) <= CLUSTER_KM:
                home = c
                break
        if home is None:
            home = {"id": len(clusters) + 1, "lat": p["lat"], "lon": p["lon"], "pts": [], "labels": Counter(), "precision": p["precision"]}
            clusters.append(home)
        home["pts"].append(p)
        if p["label"]:
            home["labels"][p["label"]] += 1
        if p["precision"] == "recorded":
            home["precision"] = "recorded"
    for c in clusters:  # visits: consecutive runs in one cluster
        c["visits"] = []
        cur = None
        for p in subj_pts:
            if p in c["pts"]:
                if cur is not None and cur["last_index"] == subj_pts.index(p) - 1:
                    cur["end"], cur["last_index"] = p["t"], subj_pts.index(p)
                    cur["n"] += 1
                else:
                    cur = {"start": p["t"], "end": p["t"], "n": 1, "last_index": subj_pts.index(p)}
                    c["visits"].append(cur)
    dwell, recurring = [], []
    sentences: list[str] = []
    for c in clusters:
        label = c["labels"].most_common(1)[0][0] if c["labels"] else f"{c['lat']:.4f}, {c['lon']:.4f}"
        locs = [to_local(p["t"], tz) for p in c["pts"]]
        dates = sorted({d.date() for d in locs})
        total_min = sum((v["end"] - v["start"]).total_seconds() / 60 for v in c["visits"])
        hours = [d.hour + d.minute / 60 for d in locs]
        row = {"id": c["id"], "label": label, "lat": round(c["lat"], 5), "lon": round(c["lon"], 5), "precision": c["precision"], "observations": len(c["pts"]), "visits": len(c["visits"]),
               "distinct_days": len(dates), "observed_minutes": round(total_min, 1), "first": min(p["t"] for p in c["pts"]).isoformat(), "last": max(p["t"] for p in c["pts"]).isoformat(),
               "typical_hour_window": None, "weekdays": sorted({d.strftime("%a") for d in locs}), "entries": sorted(p["seq"] for p in c["pts"])}
        if len(hours) >= 2:
            hs = sorted(hours)
            lo, hi = hs[len(hs) // 4], hs[(3 * len(hs)) // 4 if (3 * len(hs)) // 4 < len(hs) else -1]
            row["typical_hour_window"] = f"{int(lo):02d}:00-{(int(hi) + 1) % 24:02d}:00"
        dwell.append(row)
        if len(dates) >= 2:
            recurring.append(row)
            sentences.append(f"{label} was visited on {len(dates)} separate days ({', '.join(d.strftime('%a %d %b') for d in dates[:6])}"
                             f"{'...' if len(dates) > 6 else ''}), usually between {row['typical_hour_window'] or 'varying times'} {tzl}. This may be a routine, or coincidence in a small sample.")
        elif total_min >= 10:
            sentences.append(f"{subject_name[:1].upper() + subject_name[1:]} was observed at {label} for about {_dur(total_min)} on {dates[0].strftime('%a %d %b %Y')} ({len(c['pts'])} observations).")
    dwell.sort(key=lambda r: (-r["distinct_days"], -r["observed_minutes"]))
    # ---- legs
    legs = []
    for a, b in zip(subj_pts, subj_pts[1:]):
        km = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
        if km < CLUSTER_KM:
            continue
        hrs = max((b["t"] - a["t"]).total_seconds() / 3600, 1e-6)
        kmh = km / hrs
        leg = {"from_seq": a["seq"], "to_seq": b["seq"], "from": a["label"], "to": b["label"], "km": round(km, 2), "minutes": round(hrs * 60, 1), "kmh": round(kmh, 1),
               "start": a["t"].isoformat(), "end": b["t"].isoformat(), "approximate": "approximate" in (a["precision"], b["precision"]), "implausible": kmh > MAX_PLAUSIBLE_KMH}
        legs.append(leg)
    for lg in [x for x in legs if x["minutes"] <= 180][:6]:
        sentences.append(f"Between {fmt_local(_parse(lg['start']), tz)} and {fmt_local(_parse(lg['end']), tz)} movement from {lg['from'] or 'a recorded point'} to {lg['to'] or 'another recorded point'} "
                         f"was recorded ({lg['km']} km in {_dur(lg['minutes'])}, average {lg['kmh']} km/h)."
                         + (" This speed is implausible: check the timestamps or positions." if lg["implausible"] else "")
                         + (" Positions are approximate (place name only)." if lg["approximate"] else ""))
    # ---- presence records for meetings
    presence = []
    by_seq = {p["seq"]: p for p in pts}
    for e in entries:
        p = by_seq.get(e["seq"])
        t = e.get("t")
        if t is None:
            continue
        loc = {"lat": p["lat"], "lon": p["lon"], "precision": p["precision"]} if p else {"lat": None, "lon": None, "precision": ""}
        label = e.get("location_text") or e.get("place") or ""
        if e.get("subject_seen", True):
            presence.append({"key": "__subject__", "name": subject_name, "role": "subject", "t": t, "label": label, "seq": e["seq"], **loc})
        for per in e.get("persons", []):
            presence.append({"key": per["key"], "name": per["name"], "role": per.get("role", "unknown"), "t": t, "label": label, "seq": e["seq"], **loc})
    presence.sort(key=lambda r: r["t"])
    W = timedelta(minutes=window_min)
    pair_hits: dict[tuple, list] = defaultdict(list)
    for i, a in enumerate(presence):
        for b in presence[i + 1:]:
            if b["t"] - a["t"] > W:
                break
            if a["key"] == b["key"]:
                continue
            if a["lat"] is not None and b["lat"] is not None:
                same = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) * 1000 <= radius_m
            else:
                same = bool(_norm_place(a["label"])) and _norm_place(a["label"]) == _norm_place(b["label"])
            if same:
                pair_hits[tuple(sorted((a["key"], b["key"])))].append((a, b))
    meetings = []
    for pair, hits in pair_hits.items():
        hits = sorted(hits, key=lambda h: h[0]["t"])
        sessions: list[list] = []
        for h in hits:  # one "meeting" per run of hits; a gap of more than twice the window starts a new one
            if sessions and h[0]["t"] - sessions[-1][-1][1]["t"] <= 2 * W:
                sessions[-1].append(h)
            else:
                sessions.append([h])
        for sess in sessions:
            ts = sorted({h[0]["t"] for h in sess} | {h[1]["t"] for h in sess})
            names = {}
            for a, b in sess:
                names[a["key"]], names[b["key"]] = a["name"], b["name"]
            seqs = sorted({h[0]["seq"] for h in sess} | {h[1]["seq"] for h in sess})
            place = Counter(h[0]["label"] for h in sess if h[0]["label"]).most_common(1)
            meetings.append({"persons": [names[pair[0]], names[pair[1]]], "keys": list(pair), "place": place[0][0] if place else "", "first": ts[0].isoformat(), "last": ts[-1].isoformat(),
                             "observations": len(seqs), "entries": seqs,
                             "sentence": f"{names[pair[0]]} and {names[pair[1]]} were recorded together (within {window_min} min and {radius_m} m) at {place[0][0] if place else 'a recorded location'} "
                                         f"on {fmt_local(ts[0], tz)}{'' if ts[0] == ts[-1] else ' to ' + fmt_local(ts[-1], tz)} ({len(seqs)} log entr{'y' if len(seqs) == 1 else 'ies'})."})
    meetings.sort(key=lambda m: m["first"])
    sentences += [m["sentence"] for m in meetings[:6]]
    # ---- vehicles
    veh: dict[str, dict] = {}
    for e in entries:
        for v in e.get("vehicles", []):
            r = veh.setdefault(v["reg"], {"reg": v["reg"], "valid": v.get("valid", True), "seen": 0, "entries": [], "places": Counter(), "persons": Counter(), "subject_seen_with": 0, "first": None, "last": None})
            r["seen"] += 1
            r["entries"].append(e["seq"])
            if e.get("location_text") or e.get("place"):
                r["places"][e.get("location_text") or e.get("place")] += 1
            for per in e.get("persons", []):
                r["persons"][per["name"]] += 1
            if e.get("subject_seen", True):
                r["subject_seen_with"] += 1
            if e.get("t"):
                r["first"] = min(r["first"], e["t"]) if r["first"] else e["t"]
                r["last"] = max(r["last"], e["t"]) if r["last"] else e["t"]
    vehicles = []
    for r in sorted(veh.values(), key=lambda r: -r["seen"]):
        vehicles.append({"reg": r["reg"], "valid_format": r["valid"], "seen": r["seen"], "entries": r["entries"], "places": [{"place": k, "count": v} for k, v in r["places"].most_common(4)],
                         "seen_with": [{"person": k, "count": v} for k, v in r["persons"].most_common(5)], "with_subject_in_entries": r["subject_seen_with"],
                         "first": r["first"].isoformat() if r["first"] else None, "last": r["last"].isoformat() if r["last"] else None})
        if r["seen"] >= 2:
            sentences.append(f"Vehicle {r['reg']} was recorded {r['seen']} times" + (f", most often at {r['places'].most_common(1)[0][0]}" if r["places"] else "")
                             + (f", together with {', '.join(k for k, _ in r['persons'].most_common(2))}" if r["persons"] else "") + ".")
    span = None
    if subj_pts:
        span = {"first": subj_pts[0]["t"].isoformat(), "last": subj_pts[-1]["t"].isoformat()}
    return {"timezone": tzl, "points": [{"seq": p["seq"], "t": p["t"].isoformat(), "lat": p["lat"], "lon": p["lon"], "label": p["label"], "precision": p["precision"], "subject_seen": p["subject_seen"]} for p in pts],
            "dwell_locations": dwell, "recurring_places": recurring, "legs": legs, "meetings": meetings, "vehicles": vehicles, "pattern_of_life": sentences, "observed_span": span,
            "unplaced_entries": [e["seq"] for e in entries if e["seq"] not in by_seq],
            "explain": ("Built only from log entries that have coordinates or a place name the offline gazetteer knows. Place names give an approximate position (the town or "
                        "locality centre), shown dashed on the map. 'Dwell' is the time between the first and last observation at a place, so it is a minimum. Times when nobody "
                        "was watching are gaps in coverage, not evidence that nothing happened."),
            "caveats": ["Pattern-of-life sentences describe what was recorded. They do not show intent and a small sample can show a 'routine' by chance.",
                        "Approximate positions can be kilometres off; recorded coordinates depend on the observer's device."]}


def _parse(iso: str):
    from datetime import datetime
    return datetime.fromisoformat(iso)
