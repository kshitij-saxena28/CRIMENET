"""Social media intelligence: extraction, import, link suggestions, flagging accuracy floors, coordination, permissions, isolation."""
import json
import uuid

import pytest

from ai_engine.social import extract as ex
from ai_engine.social import evaluate as ev
from ai_engine.social.analysis import coordinated
from ai_engine.social.lexicon import flag_text

BASIS = "Public posts collected under notice ref 12/2026, Sec 94 BNSS"


def _case(client, login, tag="SOC"):
    cn = f"CASE-{tag}-{uuid.uuid4().hex[:6].upper()}"
    sup, admin = login("supervisor"), login("admin")
    assert client.post("/cases", json={"case_number": cn, "title": "social test"}, headers=sup).status_code == 200
    client.patch(f"/admin/cases/{cn}/visibility", json={"visibility": "shared"}, headers=admin)
    return cn, sup


def _import(client, hdr, cn, content=None, records=None, **kw):
    body = {"source": "Public page, viewed by SI Rao", "legal_basis": BASIS, "assumed_timezone": "IST", **kw}
    if content is not None:
        body["content"] = content
    if records is not None:
        body["records"] = records
    return client.post(f"/social/{cn}/import", json=body, headers=hdr)


# ------------------------------------------------------------------ extraction (pure functions)
def test_extraction_finds_identifiers_and_normalises_them():
    t = "Call 98765 43210 or +91-9876543211, mail Ravi.K@Example.com, UPI ravi.khatri@okaxis. Visit https://bit.ly/3xYz and https://example.org/a #Fraud #डबल @quick_cash"
    e = ex.extract(t)
    assert "+919876543210" in e["phones"] and "+919876543211" in e["phones"]
    assert "ravi.k@example.com" in e["emails"]
    assert "ravi.khatri@okaxis" in e["upi_ids"] and "ravi.khatri@okaxis" not in e["emails"]
    assert {"fraud", "डबल"} <= {x.lower() for x in e["hashtags"]} and "quick_cash" in e["mentions"]
    kinds = {u["domain"]: u["is_shortener"] for u in e["urls"]}
    assert kinds["bit.ly"] is True and kinds["example.org"] is False


def test_extraction_wallets_need_a_valid_checksum():
    good = "TVtP3eFzysHgdNvdfxQ3oZ77z1Ewf4xREw"
    btc = "1Xt72swPa64Dv5Y5ecT5N6BYSttvQNiUp"
    e = ex.extract(f"send to {btc} or {good}")
    vals = {w["value"] for w in e["wallets"]}
    assert btc in vals and good in vals
    bad = ex.extract("send to " + btc[:-1] + "Q")
    assert not [w for w in bad["wallets"] if w.get("checksum_valid")]


def test_extraction_places_language_and_hinglish():
    e = ex.extract("Kal Connaught Place aana, paisa lekar aana bhai, Noida mein milte hain")
    assert {p["name"] if isinstance(p, dict) else p for p in e["places"]} & {"Noida", "Connaught Place", "New Delhi"}
    assert e["language"]["code"] == "hinglish"
    assert ex.extract("The meeting is at noon tomorrow at the office.")["language"]["code"] == "en"


def test_timestamps_are_stored_in_utc_and_shown_in_ist(client, login):
    cn, sup = _case(client, login)
    r = _import(client, sup, cn, records=[{"platform": "x", "handle": "tz_user", "text": "hello world timing check", "timestamp": "2026-03-01 23:30"}])
    assert r.status_code == 200, r.text
    p = client.get(f"/social/{cn}/posts", headers=sup).json()["posts"][0]
    assert p["posted_at"].startswith("2026-03-01T18:00") and "01 Mar 2026 23:30" in p["posted_at_ist"]


# ------------------------------------------------------------------ import
def test_import_requires_legal_basis_and_hashes_the_raw_input(client, login):
    cn, sup = _case(client, login)
    assert client.post(f"/social/{cn}/import", json={"source": "x page", "legal_basis": "", "content": "hello there"}, headers=sup).status_code == 422
    text = "@a wrote: call 98765 43210 now, pay to ravi@okaxis #offer"
    r = _import(client, sup, cn, content=text, default_platform="x")
    assert r.status_code == 200, r.text
    d = r.json()
    import hashlib
    assert d["sha256"] == hashlib.sha256(text.encode()).hexdigest() and d["posts_added"] == 1
    imps = client.get(f"/social/{cn}/imports", headers=sup).json()["imports"]
    assert imps[0]["legal_basis"] == BASIS and imps[0]["collected_by"] == "supervisor" and imps[0]["sha256"] == d["sha256"]
    again = _import(client, sup, cn, content=text, default_platform="x").json()
    assert again["posts_added"] == 0 and again["duplicates_skipped"] == 1


def test_import_formats_json_csv_and_manual(client, login):
    cn, sup = _case(client, login)
    js = json.dumps([{"platform": "twitter", "username": "@Jay", "text": "json post one two three", "created_at": "2026-04-01T10:00:00+05:30"}])
    assert _import(client, sup, cn, content=js).json()["posts_added"] == 1
    csv = "platform,handle,text,timestamp\nfacebook,mita,csv post number one,2026-04-02 09:00\nfacebook,mita,csv post number two,2026-04-02 09:05\n"
    assert _import(client, sup, cn, content=csv).json()["posts_added"] == 2
    assert _import(client, sup, cn, records=[{"platform": "instagram", "handle": "manual1", "text": "manual entry post"}]).json()["posts_added"] == 1
    accts = {a["ref"] for a in client.get(f"/social/{cn}/accounts", headers=sup).json()["accounts"]}
    assert {"x:@jay", "facebook:@mita", "instagram:@manual1"} <= accts
    assert client.post(f"/social/{cn}/import", json={"source": "s page", "legal_basis": BASIS, "content": "   "}, headers=sup).status_code == 400


# ------------------------------------------------------------------ link suggestions
def test_link_suggestions_and_accept_creates_unverified_candidate(client, login):
    cn, sup = _case(client, login)
    r = client.post(f"/social/{cn}/import/sample", json={"seed_entities": True}, headers=sup)
    assert r.status_code == 200 and r.json()["seeded_entities"]
    sug = client.get(f"/social/{cn}/links/suggestions", headers=sup).json()["suggestions"]
    by = {s["match_type"] for s in sug}
    assert "phone" in by and "upi" in by
    s = next(x for x in sug if x["match_type"] == "phone")
    assert s["strength"] == "strong" and s["entity_name"] == "+919000011223"
    a = client.post(f"/social/{cn}/links/accept", json={"account_id": s["account_id"], "entity_id": s["entity_id"], "match_type": s["match_type"]}, headers=sup)
    assert a.status_code == 200, a.text
    d = a.json()
    assert d["verification_state"] == "candidate" and d["source_ref"].startswith("social:")
    assert not [x for x in client.get("/relationships", params={"case_number": cn}, headers=sup).json() if x.get("source_ref") == d["source_ref"]]  # not in the verified graph
    rels = client.get("/relationships", params={"case_number": cn, "include_candidates": True}, headers=sup).json()
    mine = [x for x in rels if x.get("source_ref") == d["source_ref"]]
    assert mine and all(x.get("verification_state") == "candidate" for x in mine)
    after = {x["id"]: x for x in client.get(f"/social/{cn}/links/suggestions", headers=sup).json()["suggestions"]}
    assert after[s["id"]]["status"] != "suggested"


# ------------------------------------------------------------------ flags: behaviour and accuracy
def test_flags_show_matched_phrase_reason_and_caveat_and_skip_benign():
    f = flag_text("I will kill you tomorrow, you will not see next week")
    assert f and f[0]["category"] == "threat_violence" and f[0]["phrase"] and f[0]["why"] and f[0]["caveat"]
    assert "guilt" not in f[0]["why"].lower()
    benign = ["Great match today, Kohli killed it with the bat!", "Please pay the electricity bill before the due date", "Happy birthday bhai, party kab de raha hai"]
    assert all(not flag_text(t) for t in benign)


def test_flag_accuracy_meets_documented_floors():
    """Floors sit a little under the measured numbers (see docs/models/social-flagging.md). The heldout split was not used for tuning."""
    dev, held = ev.evaluate("dev"), ev.evaluate("heldout")
    assert dev["posts"] >= 60 and held["posts"] >= 40 and dev["benign_posts"] + held["benign_posts"] >= 60
    assert dev["micro"]["precision"] >= 0.9 and dev["micro"]["recall"] >= 0.9
    assert held["micro"]["precision"] >= 0.9 and held["micro"]["recall"] >= 0.6, held["micro"]
    assert held["benign_false_positive_rate"] <= 0.05 and dev["benign_false_positive_rate"] <= 0.06
    assert ev.evaluate(None)["micro"]["f1"] >= 0.85


def test_evaluation_endpoint_reports_both_splits(client, login):
    d = client.get("/social/evaluation", headers=login("investigator")).json()
    assert d["dev"]["micro"]["precision"] >= 0.9 and d["heldout"]["micro"]["recall"] >= 0.6 and d.get("note")


def test_flag_review_by_investigator_with_history(client, login):
    cn, sup = _case(client, login)
    client.post(f"/social/{cn}/import/sample", json={}, headers=sup)
    inv = login("investigator")
    flags = client.get(f"/social/{cn}/flags", headers=inv).json()["flags"]
    assert flags and all(f["review_status"] == "unreviewed" for f in flags[:1])
    fid = flags[0]["id"]
    assert client.post(f"/social/{cn}/flags/{fid}/review", json={"status": "false_positive", "note": "news report"}, headers=inv).status_code == 200
    assert client.post(f"/social/{cn}/flags/{fid}/review", json={"status": "guilty"}, headers=inv).status_code in (400, 422)
    h = client.get(f"/social/{cn}/flags/{fid}/history", headers=inv).json()["history"]
    assert h[-1]["status"] == "false_positive" and h[-1]["reviewer"] == "investigator"
    s = client.get(f"/social/{cn}/summary", headers=inv).json()["counts"]
    assert s["flags_false_positive"] == 1


# ------------------------------------------------------------------ coordination and analyses
def _planted():
    t0 = "2026-05-01T10:00:00"
    txt = "Big giveaway today! Send one thousand rupees to get ten thousand back, limited slots, hurry up now"
    rs = [{"platform": "x", "handle": f"bot{i}", "text": txt + (" !!" if i % 2 else ""), "timestamp": f"2026-05-01T10:0{i}:00+05:30"} for i in range(4)]
    rs += [{"platform": "x", "handle": "normal1", "text": "Went to the market this morning and bought fresh vegetables for dinner", "timestamp": "2026-05-01T10:02:00+05:30"},
           {"platform": "x", "handle": "normal2", "text": "Traffic near the airport was terrible today, took an hour to reach", "timestamp": "2026-05-01T10:03:00+05:30"}]
    # the same sentence, days apart from the same accounts, must not count as coordination
    rs += [{"platform": "x", "handle": "late1", "text": txt, "timestamp": "2026-05-20T10:00:00+05:30"}]
    return rs, t0


def test_coordinated_activity_is_found_in_planted_data_and_not_in_normal_posts(client, login):
    cn, sup = _case(client, login)
    recs, _ = _planted()
    assert _import(client, sup, cn, records=recs).status_code == 200
    r = client.get(f"/social/{cn}/analysis/coordinated", headers=login("investigator")).json()
    clusters = r["clusters"]
    assert clusters, r
    top = max(clusters, key=lambda c: len(c["accounts"]))
    assert {f"x:bot{i}" for i in range(4)} <= set(top["accounts"])
    assert not any("normal1" in " ".join(c["accounts"]) or "normal2" in " ".join(c["accounts"]) for c in clusters)
    assert "late1" not in " ".join(top["accounts"])


def test_coordination_engine_ignores_one_account_repeating_itself():
    ps = [{"account": "a", "text": "please subscribe to my channel for daily updates and more", "ts": __import__("datetime").datetime(2026, 1, 1, 10, m)} for m in range(4)]
    assert not coordinated(ps)["clusters"]


def test_timeline_supports_time_zones_and_network_and_attribution(client, login):
    cn, sup = _case(client, login)
    client.post(f"/social/{cn}/import/sample", json={}, headers=sup)
    inv = login("investigator")
    ist = client.get(f"/social/{cn}/analysis/timeline", params={"tz": "IST"}, headers=inv).json()
    utc = client.get(f"/social/{cn}/analysis/timeline", params={"tz": "UTC"}, headers=inv).json()
    assert ist["overall"]["heatmap"] != utc["overall"]["heatmap"] and len(ist["overall"]["heatmap"]) == 7
    net = client.get(f"/social/{cn}/analysis/network", headers=inv).json()
    assert net["nodes"] and net["edges"]
    att = client.get(f"/social/{cn}/analysis/attribution", headers=inv).json()
    assert any(h["type"] in ("phone", "upi", "email", "wallet") for h in att["hints"]), att["headline"]
    assert att["explain"]


# ------------------------------------------------------------------ permissions, masking, isolation
def test_investigator_can_analyse_and_review_but_not_import_or_accept(client, login):
    cn, sup = _case(client, login)
    client.post(f"/social/{cn}/import/sample", json={"seed_entities": True}, headers=sup)
    inv = login("investigator")
    assert _import(client, inv, cn, content="hello there friend how are you").status_code == 403
    assert client.post(f"/social/{cn}/import/sample", json={}, headers=inv).status_code == 403
    assert client.post(f"/social/{cn}/links/accept", json={"account_id": 1, "entity_id": "x", "match_type": "phone"}, headers=inv).status_code == 403
    for path in ("summary", "accounts", "posts", "flags", "links/suggestions", "analysis/timeline", "analysis/network", "analysis/coordinated", "analysis/attribution", "analysis/flags"):
        assert client.get(f"/social/{cn}/{path}", headers=inv).status_code == 200, path
    assert client.get(f"/social/{cn}/report", params={"format": "json"}, headers=inv).status_code == 200


def test_auditor_see_masked_identifiers_and_cannot_write(client, login):
    cn, sup = _case(client, login)
    client.post(f"/social/{cn}/import/sample", json={"seed_entities": True}, headers=sup)
    plain = json.dumps(client.get(f"/social/{cn}/accounts", headers=sup).json())
    assert "+919000011223" in plain
    for who in ("auditor",):
        h = login(who)
        body = client.get(f"/social/{cn}/accounts", headers=h)
        assert body.status_code == 200 and body.json()["masked"] is True
        assert "+919000011223" not in body.text and "9000011223" not in body.text
        assert "ravi.khatri@okaxis" not in client.get(f"/social/{cn}/posts", headers=h).text
        assert client.get(f"/social/{cn}/analysis/coordinated", headers=h).status_code == 403
        assert client.post(f"/social/{cn}/import/sample", json={}, headers=h).status_code == 403
        assert client.get(f"/social/{cn}/report", params={"format": "json"}, headers=h).status_code == 403


def test_cross_case_isolation_and_no_existence_oracle(client, login, make_user):
    cn, sup = _case(client, login)
    client.post(f"/social/{cn}/import/sample", json={}, headers=sup)
    other, _ = _case(client, login)
    assert client.get(f"/social/{other}/posts", headers=sup).json()["total"] == 0
    assert client.get(f"/social/{other}/accounts", headers=sup).json()["total"] == 0
    # a private case another user cannot see answers 404, exactly like a case that does not exist
    admin = login("admin")
    priv = f"CASE-PRIV-{uuid.uuid4().hex[:6].upper()}"
    client.post("/cases", json={"case_number": priv, "title": "private"}, headers=admin)
    client.patch(f"/admin/cases/{priv}/visibility", json={"visibility": "private"}, headers=admin)
    _, _, outsider = make_user("investigator")
    assert client.get(f"/social/{priv}/posts", headers=outsider).status_code == client.get("/social/CASE-NOPE-0/posts", headers=outsider).status_code == 404


def test_demo_account_is_confined_to_demo_cases_and_purge_removes_social_rows(client, login):
    demo, admin, sup = login("demo"), login("admin"), login("supervisor")
    assert client.post("/demo/enable", headers=demo).status_code == 200
    try:
        real, _ = _case(client, login, "REALSOC")
        assert client.get(f"/social/{real}/summary", headers=demo).status_code == 404
        assert client.post(f"/social/{real}/import/sample", json={}, headers=demo).status_code == 404
        dc = f"CASE-DEMOSOC-{uuid.uuid4().hex[:5].upper()}"
        assert client.post("/workbench/new", json={"case_number": dc, "title": "demo social"}, headers=demo).status_code == 200
        assert client.post(f"/social/{dc}/import/sample", json={}, headers=demo).status_code == 200
        assert client.get(f"/social/{dc}/summary", headers=sup).status_code == 404  # real users never see demo cases
    finally:
        client.post("/demo/disable", headers=admin)
    from backend.app.db.database import SessionLocal
    from backend.app.features.social import SocialPost
    with SessionLocal() as db:
        assert db.query(SocialPost).filter_by(case_number=dc).count() == 0


def test_lexicon_is_transparent(client, login):
    d = client.get("/social/lexicon", headers=login("auditor")).json()
    assert d["categories"] and d["caveat"] and d["method"]
    rules = [r for c in d["categories"] for r in c["rules"]]
    assert len(rules) >= 60 and all(r["note"] and r["pattern"] for r in rules)
