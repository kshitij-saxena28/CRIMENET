import itertools
import time
from datetime import date, timedelta

from backend.app.features import workflow as wf

_n = itertools.count()


def _case(client, headers, members=()):
    cn = f"WF-{int(time.time() * 1000) % 10**8}-{next(_n)}"
    assert client.post("/cases", json={"case_number": cn, "title": "wf case"}, headers=headers).status_code == 200
    return cn


def _member(client, login, cn, username):
    r = client.post(f"/admin/cases/{cn}/members", json={"username": username}, headers=login("admin"))
    assert r.status_code == 200, r.text


def _entity(client, headers, cn, etype, name):
    r = client.post("/entities", json={"external_id": f"{etype}-{cn}-{next(_n)}", "name": name, "entity_type": etype, "case_number": cn}, headers=headers)
    assert r.status_code == 200, r.text


def _upload(client, headers, cn):
    r = client.post("/evidence/upload", files={"file": ("note.txt", b"hello")}, data={"case_number": cn}, headers=headers)
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- approvals
def test_approvals_maker_checker(client, login, make_user):
    inv_name, _, inv = make_user("supervisor")
    sup_name, _, sup = make_user("supervisor")
    _, _, sub = make_user("auditor")
    cn = _case(client, inv)
    _member(client, login, cn, sup_name)
    body = {"case_number": cn, "kind": "report_release", "ref_id": "R1", "title": "Release report R1"}
    assert client.post("/workflow/approvals", json=body, headers=sub).status_code == 403
    r = client.post("/workflow/approvals", json=body, headers=inv); assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert client.post("/workflow/approvals", json=body, headers=inv).status_code == 409
    assert client.post("/workflow/approvals", json={**body, "kind": "bogus"}, headers=inv).status_code == 400
    # supervisor got a notification, requester did not
    notes = client.get("/workflow/notifications", headers=sup).json()["notifications"]
    assert any(n["kind"] == "approval_requested" and n["case_number"] == cn for n in notes)
    # requester and auditor cannot decide; an admin who is the requester cannot self-approve either
    assert client.post(f"/workflow/approvals/{aid}/decide", json={"decision": "approve"}, headers=inv).status_code == 403
    assert client.post(f"/workflow/approvals/{aid}/decide", json={"decision": "approve"}, headers=sub).status_code == 403
    adm = login("admin")
    ar = client.post("/workflow/approvals", json={**body, "ref_id": "ADM"}, headers=adm).json()
    assert client.post(f"/workflow/approvals/{ar['id']}/decide", json={"decision": "approve"}, headers=adm).status_code == 403
    # reject needs a note
    assert client.post(f"/workflow/approvals/{aid}/decide", json={"decision": "reject"}, headers=sup).status_code == 400
    assert client.post(f"/workflow/approvals/{aid}/decide", json={"decision": "approve", "note": "ok"}, headers=sup).status_code == 200
    assert client.post(f"/workflow/approvals/{aid}/decide", json={"decision": "approve"}, headers=sup).status_code == 409
    from backend.app.db.database import SessionLocal
    with SessionLocal() as db:
        assert wf.approved(db, cn, "report_release", "R1") and not wf.approved(db, cn, "report_release", "nope")
    assert any(n["kind"] == "approval_approved" for n in client.get("/workflow/notifications", headers=inv).json()["notifications"])
    assert client.get(f"/workflow/approvals?case_number={cn}&status=approved", headers=inv).json()["approvals"][0]["id"] == aid
    # withdraw: only requester
    b2 = client.post("/workflow/approvals", json={**body, "ref_id": "R2"}, headers=inv).json()
    assert client.post(f"/workflow/approvals/{b2['id']}/withdraw", headers=sup).status_code == 403
    assert client.post(f"/workflow/approvals/{b2['id']}/withdraw", headers=inv).json()["status"] == "withdrawn"
    # a user without case access gets 404
    _, _, other = make_user("supervisor")
    assert client.post("/workflow/approvals", json=body, headers=other).status_code == 404
    acts = [e["action"] for e in client.get(f"/audit?case_number={cn}", headers=adm).json()["events"]]
    assert "approval.requested" in acts and "approval.approved" in acts and "approval.self_decision_blocked" in acts


def with_db(fn):
    from backend.app.db.database import SessionLocal
    with SessionLocal() as db:
        return fn(db)


# ---------------------------------------------------------------- deadlines
def test_deadline_math_and_ics():
    today = date(2026, 6, 10)
    assert wf.deadline_state(date(2026, 6, 9), "open", today) == (-1, "overdue")
    assert wf.deadline_state(date(2026, 6, 17), "open", today) == (7, "at_risk")
    assert wf.deadline_state(date(2026, 6, 18), "open", today) == (8, "on_track")
    assert wf.deadline_state(date(2026, 6, 9), "done", today)[1] == "done"
    ics = wf.build_ics([{"id": 5, "case_number": "C-1", "label": "Chargesheet, 60 days; x", "due_date": "2026-07-01", "note": "line\nbreak " + "é" * 80}])
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "DTSTART;VALUE=DATE:20260701" in ics and "DTEND;VALUE=DATE:20260702" in ics
    assert "Chargesheet\\, 60 days\; x" in ics
    for line in ics.split("\r\n"):
        assert len(line.encode()) <= 75


def test_deadline_endpoints(client, login, make_user):
    _, _, inv = make_user("supervisor")
    _, _, sub = make_user("auditor")
    cn = _case(client, inv)
    t = client.get("/workflow/deadline-templates", headers=inv).json()
    assert "not legal advice" in t["disclaimer"] and any(x["key"] == "chargesheet_60" and x["days"] == 60 for x in t["templates"])
    start = (date.today() - timedelta(days=55)).isoformat()
    d = client.post("/workflow/deadlines", json={"case_number": cn, "template_key": "chargesheet_60", "start_date": start}, headers=inv)
    assert d.status_code == 200, d.text
    d = d.json(); assert d["days_left"] == 5 and d["state"] == "at_risk"
    assert client.post("/workflow/deadlines", json={"case_number": cn, "template_key": "chargesheet_60"}, headers=inv).status_code == 400
    assert client.post("/workflow/deadlines", json={"case_number": cn, "label": "x", "due_date": "31/02/2026"}, headers=inv).status_code == 400
    assert client.post("/workflow/deadlines", json={"case_number": cn, "template_key": "chargesheet_60", "start_date": start}, headers=sub).status_code == 403
    over = client.post("/workflow/deadlines", json={"case_number": cn, "label": "Late thing", "due_date": (date.today() - timedelta(days=2)).isoformat()}, headers=inv).json()
    assert over["state"] == "overdue" and over["days_left"] == -2
    lst = client.get(f"/workflow/deadlines?case_number={cn}", headers=inv).json()["deadlines"]
    assert len(lst) == 2
    up = client.get("/workflow/deadlines/upcoming", headers=inv).json()
    assert {x["id"] for x in up["deadlines"]} >= {d["id"], over["id"]} and up["summary"]["overdue"] >= 1
    assert client.patch(f"/workflow/deadlines/{d['id']}", json={"due_date": "2030-01-01"}, headers=inv).status_code == 400  # note needed
    p = client.patch(f"/workflow/deadlines/{d['id']}", json={"due_date": "2030-01-01", "note": "court extension"}, headers=inv)
    assert p.status_code == 200 and p.json()["due_date"] == "2030-01-01"
    assert client.patch(f"/workflow/deadlines/{over['id']}", json={"status": "done"}, headers=inv).json()["state"] == "done"
    ics = client.get(f"/workflow/deadlines/ics?case_number={cn}", headers=inv)
    assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
    assert ics.text.count("BEGIN:VEVENT") == 1 and "Late thing" not in ics.text
    _, _, other = make_user("supervisor")
    assert client.get(f"/workflow/deadlines?case_number={cn}", headers=other).status_code == 404
    assert client.get(f"/workflow/deadlines/ics?case_number={cn}", headers=other).status_code == 404
    assert client.patch(f"/workflow/deadlines/{d['id']}", json={"status": "done"}, headers=other).status_code == 404
    assert client.get(f"/workflow/deadlines/suggest?case_number={cn}", headers=inv).json()["suggested_start_date"] is None
    # only admins configure templates
    assert client.put("/workflow/deadline-templates/chargesheet_60", json={"days": 61}, headers=inv).status_code == 403
    assert client.put("/workflow/deadline-templates/chargesheet_60", json={"days": 60}, headers=login("admin")).status_code == 200


def test_deadline_suggest_from_verified_fir(client, make_user):
    _, _, inv = make_user("supervisor")
    cn = _case(client, inv)
    fir = (b"FIRST INFORMATION REPORT\nDistrict: Gautam Budh Nagar P.S.: City Center Year: 2026 FIR No: 0452/2026\n"
           b"Date of FIR: 05/09/2026 Time: 14:30 hrs\nActs & Sections: Section 379\nComplainant / Informant:\nName: Rahul Sharma\n"
           b"Accused: Unknown person riding scooter DL01AB1234\nShort Description of Incident: Theft.\nInvestigating Officer: ASI Vikram Singh\n")
    r = client.post("/documents/extract", files={"file": ("fir.txt", fir)}, data={"case_number": cn}, headers=inv)
    assert r.status_code == 200, r.text
    did = r.json()["document_id"]
    assert client.get(f"/workflow/deadlines/suggest?case_number={cn}", headers=inv).json()["suggested_start_date"] is None  # not verified yet
    assert client.post(f"/documents/{did}/review", json={"status": "Verified"}, headers=inv).status_code == 200
    s = client.get(f"/workflow/deadlines/suggest?case_number={cn}", headers=inv).json()
    assert s["suggested_start_date"] == "2026-09-05" and s["document_id"] == did


# ---------------------------------------------------------------- watchlists
def test_normalisation():
    assert wf.normalise("phone", "+91 98765-43210") == wf.normalise("phone", "09876543210") == "9876543210"
    assert wf.normalise("vehicle", "dl 01-ab 1234") == "DL01AB1234"
    assert wf.normalise("email", " Foo@Bar.COM ") == "foo@bar.com"
    assert wf.normalise("account", "ac-12 34") == "AC1234"
    assert wf.names_match("Rahul Sharma", "SHARMA, Rahul")[0]
    assert wf.names_match("Mohammad Irfan Khan", "Mohamed Irfan Khan")[0]
    assert wf.names_match("Shri Rahul Sharma", "rahul sharma")[0]
    assert not wf.names_match("Rahul Sharma", "Rohit Verma")[0]
    assert not wf.names_match("Rahul", "Rahul Sharma")[0]


def test_watchlist_hook_notifies_only_owners_with_access(client, login, make_user):
    inv_name, _, inv = make_user("supervisor")
    _, _, owner_no_access = make_user("supervisor")
    sup_name, _, sup = make_user("supervisor")
    cn = _case(client, inv)
    _member(client, login, cn, sup_name)
    # inv's own list (private, this case) + supervisor's unit list + an outsider's global list
    w1 = client.post("/workflow/watchlists", json={"name": "Suspect phones", "case_number": cn}, headers=inv).json()
    assert client.post(f"/workflow/watchlists/{w1['id']}/items", json={"kind": "phone", "value": "+91 98111 22233"}, headers=inv).status_code == 200
    assert client.post(f"/workflow/watchlists/{w1['id']}/items", json={"kind": "phone", "value": "091-98111-22233"}, headers=inv).status_code == 409
    assert client.post(f"/workflow/watchlists/{w1['id']}/items", json={"kind": "phone", "value": "12"}, headers=inv).status_code == 400
    w2 = client.post("/workflow/watchlists", json={"name": "Global phones"}, headers=owner_no_access).json()
    client.post(f"/workflow/watchlists/{w2['id']}/items", json={"kind": "phone", "value": "9811122233"}, headers=owner_no_access)
    w3 = client.post("/workflow/watchlists", json={"name": "Unit vehicles", "scope": "unit"}, headers=sup).json()
    client.post(f"/workflow/watchlists/{w3['id']}/items", json={"kind": "vehicle", "value": "DL 01 AB 1234"}, headers=sup)
    _entity(client, inv, cn, "PHONE", "9811122233")
    _entity(client, inv, cn, "VEHICLE", "dl01ab1234")
    _upload(client, inv, cn)  # evidence_uploaded hook -> scan
    mine = client.get("/workflow/notifications", headers=inv).json()["notifications"]
    hit = [n for n in mine if n["kind"] == "watchlist_hit"]
    assert len(hit) == 1 and hit[0]["case_number"] == cn and "9811122233" in hit[0]["body"] and "not a finding" in hit[0]["body"]
    assert [n for n in client.get("/workflow/notifications", headers=sup).json()["notifications"] if n["kind"] == "watchlist_hit"]
    assert not [n for n in client.get("/workflow/notifications", headers=owner_no_access).json()["notifications"] if n["kind"] == "watchlist_hit"]
    # de-duplicated on rescan; manual scan endpoint reports hits
    _upload(client, inv, cn)
    assert len([n for n in client.get("/workflow/notifications", headers=inv).json()["notifications"] if n["kind"] == "watchlist_hit"]) == 1
    sc = client.post("/workflow/watchlists/scan", json={"case_number": cn}, headers=inv).json()
    assert sc["hits"] and sc["notifications_created"] == 0
    assert client.post("/workflow/watchlists/scan", json={"case_number": cn}, headers=owner_no_access).status_code == 404
    _, _, sub = make_user("auditor")
    assert client.post("/workflow/watchlists/scan", json={"case_number": cn}, headers=sub).status_code == 403
    # ownership rules: unit list visible to others but only owner/admin edits
    assert w3["id"] in [w["id"] for w in client.get("/workflow/watchlists", headers=inv).json()["watchlists"]]
    assert w1["id"] not in [w["id"] for w in client.get("/workflow/watchlists", headers=owner_no_access).json()["watchlists"]]
    assert client.delete(f"/workflow/watchlists/{w3['id']}", headers=inv).status_code == 403
    assert client.delete(f"/workflow/watchlists/{w1['id']}", headers=owner_no_access).status_code == 404
    assert client.delete(f"/workflow/watchlists/{w1['id']}", headers=inv).status_code == 200
    assert client.post("/workflow/watchlists", json={"name": "x2", "case_number": cn}, headers=owner_no_access).status_code == 404


# ---------------------------------------------------------------- notifications
def test_notification_isolation(client, make_user):
    a_name, _, a = make_user("supervisor")
    _, _, b = make_user("supervisor")
    with_db(lambda db: wf.notify(db, a_name, "info", "Private to A", "secret"))
    la = client.get("/workflow/notifications?unread=true", headers=a).json()
    assert la["unread"] == 1 and client.get("/workflow/notifications/unread-count", headers=a).json()["unread"] == 1
    nid = la["notifications"][0]["id"]
    assert client.get("/workflow/notifications", headers=b).json()["notifications"] == []
    assert client.post(f"/workflow/notifications/{nid}/read", headers=b).status_code == 404
    assert client.post(f"/workflow/notifications/{nid}/read", headers=a).json()["read"] is True
    with_db(lambda db: wf.notify(db, a_name, "info", "Second"))
    assert client.post("/workflow/notifications/read-all", headers=b).json()["marked"] == 0
    assert client.post("/workflow/notifications/read-all", headers=a).json()["marked"] == 1
    assert client.get("/workflow/notifications/unread-count").status_code == 401


def test_watchlist_hook_on_document_extraction(client, make_user):
    _, _, inv = make_user("supervisor")
    cn = _case(client, inv)
    w = client.post("/workflow/watchlists", json={"name": "Vehicles", "case_number": cn}, headers=inv).json()
    client.post(f"/workflow/watchlists/{w['id']}/items", json={"kind": "vehicle", "value": "DL-01-AB-1234"}, headers=inv)
    fir = (b"FIRST INFORMATION REPORT\nFIR No: 0452/2026\nDate of FIR: 05/09/2026\nComplainant / Informant:\nName: Rahul Sharma\n"
           b"Accused: Unknown person riding scooter DL01AB1234\nShort Description of Incident: Theft.\n")
    assert client.post("/documents/extract", files={"file": ("fir.txt", fir)}, data={"case_number": cn}, headers=inv).status_code == 200
    hits = [n for n in client.get("/workflow/notifications", headers=inv).json()["notifications"] if n["kind"] == "watchlist_hit"]
    assert hits and hits[0]["case_number"] == cn
