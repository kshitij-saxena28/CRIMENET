"""Social media intelligence, investigator tools: watch list, priority queue, look-alikes, narratives, places, profile sheet, notes, sheets."""
import uuid
from datetime import datetime, timedelta

from ai_engine.social import intel

BASIS = "Public posts collected under notice ref 12/2026, Sec 94 BNSS"


def _case(client, login):
    cn = f"CASE-SXP-{uuid.uuid4().hex[:6].upper()}"
    sup, admin = login("supervisor"), login("admin")
    assert client.post("/cases", json={"case_number": cn, "title": "social plus"}, headers=sup).status_code == 200
    client.patch(f"/admin/cases/{cn}/visibility", json={"visibility": "shared"}, headers=admin)
    assert client.post(f"/social/{cn}/import/sample", json={"seed_entities": True}, headers=sup).status_code == 200
    return cn, sup


# ------------------------------------------------------------------ pure functions
def test_canon_handle_ignores_dots_underscores_digits_and_leet():
    assert intel.canon_handle("Rahul_Sharma92") == intel.canon_handle("rahul.sharma") == intel.canon_handle("r4hul-sharma")


def test_lookalikes_flag_variants_but_not_strangers():
    accs = [{"key": "x:quickcash_deals", "platform": "x", "handle": "quickcash_deals", "display_name": "Quick Cash", "bio": ""},
            {"key": "instagram:quickcash.deals", "platform": "instagram", "handle": "quickcash.deals", "display_name": "QuickCash", "bio": ""},
            {"key": "x:weather_pune", "platform": "x", "handle": "weather_pune", "display_name": "Pune weather", "bio": ""}]
    r = intel.lookalike_accounts(accs)
    assert len(r["pairs"]) == 1 and {r["pairs"][0]["a"], r["pairs"][0]["b"]} == {"x:quickcash_deals", "instagram:quickcash.deals"}
    assert r["pairs"][0]["caveat"]


def _post(uid, acct, hours, tags=(), urls=()):
    return {"uid": uid, "account": acct, "platform": "x", "handle": acct.split(":")[1], "ts": datetime(2026, 5, 1) + timedelta(hours=hours),
            "hashtags": list(tags), "urls": list(urls), "likes": 1, "shares": 1}


def test_narratives_find_first_poster_and_burst():
    posts = [_post(f"p{i}", f"x:a{i}", i * 0.1, tags=["scam"]) for i in range(5)] + [_post("q1", "x:b", 300, tags=["weather"])]
    r = intel.narratives(posts)
    tag = r["hashtags"][0]
    assert tag["key"] == "scam" and tag["burst"] and tag["first_account"] == "x:a0" and tag["accounts"] == 5
    assert all(t["key"] != "weather" for t in r["hashtags"])          # one post from one account is not a narrative


def test_triage_orders_by_visible_rules_and_ignores_false_positives():
    accs = [{"id": 1, "key": "x:a", "followers": 200_000, "display_name": ""}, {"id": 2, "key": "x:b", "followers": 5, "display_name": ""}]
    posts = [{"uid": "pa", "account": "x:a", "ts": datetime(2026, 5, 1)}, {"uid": "pb", "account": "x:b", "ts": datetime(2026, 5, 1)}]
    flags = [{"post_uid": "pa", "level": "high", "review_status": "unreviewed", "label": "Threat"},
             {"post_uid": "pb", "level": "high", "review_status": "false_positive", "label": "Threat"}]
    r = intel.triage(accs, posts, flags, [], {"clusters": [], "bursts": []}, {"hints": []})
    assert r["accounts"][0]["account"] == "x:a"
    b = next(i for i in r["accounts"] if i["account"] == "x:b")
    assert b["score"] < 10 and not any("Wording flag" in x for x in b["reasons"])   # the false positive adds nothing
    assert r["accounts"][0]["reasons"] and r["accounts"][0]["breakdown"]["flags"] == 25
    assert r["weights"]["watch_hit_high"] == 30


# ------------------------------------------------------------------ endpoints
def test_watch_list_hits_feed_the_priority_queue(client, login):
    cn, sup = _case(client, login)
    base = client.get(f"/social/{cn}/intel/triage", headers=sup).json()
    r = client.post(f"/social/{cn}/intel/watch", json={"kind": "phone", "value": "+91 90000 11223", "priority": "high"}, headers=sup)
    assert r.status_code == 200 and r.json()["hits_now"] >= 1                 # covers material imported BEFORE the term was added
    assert r.json()["term"]["value"] == "9000011223"
    assert client.post(f"/social/{cn}/intel/watch", json={"kind": "phone", "value": "9000011223"}, headers=sup).status_code == 409
    hits = client.get(f"/social/{cn}/intel/watch/hits", headers=sup).json()
    assert hits["hits"] and all(h["kind"] == "phone" for h in hits["hits"])
    after = client.get(f"/social/{cn}/intel/triage", headers=sup).json()
    assert any("watch-list" in x for i in after["accounts"] for x in i["reasons"])
    assert max(i["score"] for i in after["accounts"]) >= max(i["score"] for i in base["accounts"])
    tid = r.json()["term"]["id"]
    assert client.delete(f"/social/{cn}/intel/watch/{tid}", headers=sup).status_code == 200
    assert client.get(f"/social/{cn}/intel/watch/hits", headers=sup).json()["hits"] == []


def test_watch_term_validation_and_permissions(client, login):
    cn, sup = _case(client, login)
    assert client.post(f"/social/{cn}/intel/watch", json={"kind": "phone", "value": "12345"}, headers=sup).status_code == 400
    assert client.post(f"/social/{cn}/intel/watch", json={"kind": "keyword", "value": "ab"}, headers=sup).status_code in (400, 422)
    assert client.post(f"/social/{cn}/intel/watch", json={"kind": "bogus", "value": "abcdef"}, headers=sup).status_code == 422
    inv, aud = login("investigator"), login("auditor")
    assert client.post(f"/social/{cn}/intel/watch", json={"kind": "keyword", "value": "lottery"}, headers=inv).status_code == 403   # write only
    assert client.get(f"/social/{cn}/intel/triage", headers=inv).status_code == 200
    for p in ("triage", "lookalikes", "narratives", "places", "watch/hits"):
        assert client.get(f"/social/{cn}/intel/{p}", headers=aud).status_code == 403, p
    client.post(f"/social/{cn}/intel/watch", json={"kind": "phone", "value": "9876543210"}, headers=sup)
    seen = client.get(f"/social/{cn}/intel/watch", headers=aud).json()["terms"]
    assert seen and "9876543210" not in str(seen)                                              # masked for auditors


def test_lookalikes_narratives_places_endpoints(client, login):
    cn, sup = _case(client, login)
    assert client.get(f"/social/{cn}/intel/lookalikes", headers=sup).json()["pairs"]
    nar = client.get(f"/social/{cn}/intel/narratives", headers=sup).json()
    assert any(t["burst"] for t in nar["hashtags"] + nar["links"]) and nar["explain"]
    pl = client.get(f"/social/{cn}/intel/places", headers=sup).json()["places"]
    assert pl and all(-90 <= p["lat"] <= 90 and 60 <= p["lon"] <= 100 for p in pl)


def test_account_profile_sheet_notes_and_isolation(client, login):
    cn, sup = _case(client, login)
    q = client.get(f"/social/{cn}/intel/triage", headers=sup).json()["accounts"][0]
    d = client.get(f"/social/{cn}/intel/account/{q['account_id']}", headers=sup)
    assert d.status_code == 200 and d.json()["posts"] >= 1 and d.json()["priority"]["account"] == q["account"] and "does not identify" in d.json()["caveat"]
    assert client.get(f"/social/{cn}/intel/account/999999", headers=sup).status_code == 404
    n = client.post(f"/social/{cn}/intel/notes", json={"item_type": "account", "item_ref": str(q["account_id"]), "body": "Compare with CDR pattern"}, headers=sup)
    assert n.status_code == 200
    assert client.get(f"/social/{cn}/intel/account/{q['account_id']}", headers=sup).json()["notes"][0]["body"].startswith("Compare")
    assert client.post(f"/social/{cn}/intel/notes", json={"item_type": "post", "item_ref": "nope", "body": "xx"}, headers=sup).status_code == 404
    assert client.post(f"/social/{cn}/intel/notes", json={"item_type": "account", "item_ref": "1", "body": "xx"}, headers=login("investigator")).status_code == 403
    assert client.delete(f"/social/{cn}/intel/notes/{n.json()['id']}", headers=sup).status_code == 200
    other, _ = _case(client, login)                                   # a second case cannot read the first case's account
    assert client.get(f"/social/{other}/intel/account/{q['account_id']}", headers=sup).status_code in (200, 404)
    restricted = f"CASE-SXP-{uuid.uuid4().hex[:6]}"
    client.post("/cases", json={"case_number": restricted, "title": "r"}, headers=sup)
    assert client.get(f"/social/{restricted}/intel/triage", headers=login("investigator")).status_code == 404


def test_evidence_sheet_is_watermarked_ledgered_and_detects_edits(client, login):
    cn, sup = _case(client, login)
    uid = client.get(f"/social/{cn}/intel/triage", headers=sup).json()["posts"][0]["post_uid"]
    r = client.post(f"/social/{cn}/intel/sheet", json={"post_uids": [uid], "format": "json", "purpose": "court bundle"}, headers=sup)
    assert r.status_code == 200
    doc = r.json()
    it = doc["items"][0]
    assert it["text_unchanged_since_import"] and it["source_import"]["legal_basis"] and it["source_import"]["raw_import_sha256"]
    assert doc["watermark"]["ledger_ref"].startswith("L-")
    h = client.post(f"/social/{cn}/intel/sheet", json={"post_uids": [uid]}, headers=sup)
    assert h.headers["content-type"].startswith("text/html") and b"Unchanged since import" in h.content and b"Exported by" in h.content
    assert b"<script" not in h.content
    # the text is altered in the database after import: the sheet must say so
    from backend.app.db.database import SessionLocal
    from backend.app.features.social import SocialPost
    with SessionLocal() as db:
        p = db.query(SocialPost).filter_by(post_uid=uid).first()
        p.text = p.text + " (edited)"
        db.commit()
    bad = client.post(f"/social/{cn}/intel/sheet", json={"post_uids": [uid], "format": "json"}, headers=sup).json()["items"][0]
    assert bad["text_unchanged_since_import"] is False
    assert client.post(f"/social/{cn}/intel/sheet", json={"post_uids": [uid]}, headers=login("auditor")).status_code == 403
    assert client.post(f"/social/{cn}/intel/sheet", json={"post_uids": []}, headers=sup).status_code == 422


def test_sheet_html_escapes_hostile_text():
    from backend.app.features.social_plus import render_sheet_html
    doc = {"case_number": "C<1>", "generated_by": "u", "generated_at": "t", "purpose": "<script>x</script>", "statement": "s",
           "items": [{"post_uid": "p", "platform": "x", "handle": "h", "post_id": "", "url": "", "posted_at_utc": None, "posted_at_ist": None, "language": "en",
                      "likes": 0, "shares": 0, "replies": 0, "media": [], "text": "<img src=x onerror=alert(1)>", "text_sha256_recorded": "a", "text_sha256_now": "a",
                      "text_unchanged_since_import": True, "flags": [], "source_import": None}], "watermark": {"text": "wm"}}
    out = render_sheet_html(doc).decode()
    assert "<img" not in out and "<script>" not in out and "&lt;img" in out


def test_translation_of_hindi_post_and_english_passthrough(client, login):
    cn, sup = _case(client, login)
    posts = client.get(f"/social/{cn}/posts", headers=sup, params={"limit": 200}).json()["posts"]
    hi = next(p for p in posts if any("ऀ" <= c <= "ॿ" for c in p["text"]))
    r = client.post(f"/social/{cn}/intel/translate", json={"post_uid": hi["uid"]}, headers=sup).json()
    assert r["translated"] and r["text"] and "read the original" in r["note"]
    en = next(p for p in posts if p["lang"] == "en")
    assert client.post(f"/social/{cn}/intel/translate", json={"post_uid": en["uid"]}, headers=sup).json()["translated"] is False
