"""Roles (admin/supervisor/investigator/auditor/demo), demo-data isolation and toggle, fixed demo
passwords, and the sign-up request workflow."""
import itertools
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from backend.app.db.database import SessionLocal
from backend.app.models.models import Case, Entity, Event, Note, Relationship, User
from security.rbac import PERMISSIONS, ROLES, allowed

ROOT = Path(__file__).resolve().parents[2]
_seq = itertools.count(1)
# Must match conftest.PASSWORDS (never import conftest itself: that would re-run its environment set-up).
_PASSWORDS = {"admin": "Admin-Test-Pass-1", "investigator": "Invest-Test-Pass-1",
              "supervisor": "Superv-Test-Pass-1", "demo": "demo123", "auditor": "audit123"}


def _uid(prefix):
    return f"{prefix}{next(_seq)}"


def _mk_case(client, headers, number, admin_headers=None, shared=True):
    r = client.post("/workbench/new", json={"case_number": number, "title": "Role test case"}, headers=headers)
    assert r.status_code == 200, r.text
    if shared:
        assert client.patch(f"/admin/cases/{number}/visibility", json={"visibility": "shared"}, headers=admin_headers).status_code == 200


# ================================================================== permission table
def test_role_table_matches_the_specification():
    assert set(ROLES) == {"admin", "supervisor", "investigator", "auditor", "demo"}
    inv, sup = PERMISSIONS["investigator"], PERMISSIONS["supervisor"]
    assert inv < sup                                                    # a supervisor can do everything an investigator can
    for p in ("write", "assign", "note"):
        assert p not in inv and p in sup                                # ... plus change data
    assert {"read", "analyze", "review", "sensitive_read", "report"} <= inv
    assert "signup_review" in sup and "signup_review" not in PERMISSIONS["admin"]   # no admin bypass of the supervisor step
    assert "manage_users" not in sup and "manage_users" in PERMISSIONS["admin"]
    assert allowed("auditor", "audit") and allowed("auditor", "oversight")
    assert not (PERMISSIONS["auditor"] & {"write", "analyze", "sensitive_read", "review", "note", "report", "manage_users"})
    for role in ("admin", "supervisor", "auditor"):                     # data-integrity ledger (see docs/roles.md)
        assert allowed(role, "integrity")
    assert not any(allowed(r, "integrity") for r in ("investigator", "demo"))
    demo = PERMISSIONS["demo"]
    assert {"write", "approve", "assign", "note", "demo_manage"} <= demo
    assert not (demo & {"manage_users", "manage_cases", "audit", "audit_case", "signup_review"})


def test_me_and_login_report_permissions(client, login):
    body = client.get("/me", headers=login("auditor")).json()
    assert body["role"] == "auditor" and "audit" in body["permissions"] and "write" not in body["permissions"]
    assert client.get("/me", headers=login("demo")).json()["is_demo_account"] is True


# ================================================================== investigator = verify only, everywhere
def _login_mut_sweep(client, headers):
    """Every POST/PUT/PATCH/DELETE route the caller is NOT refused on (403) when sent an empty request."""
    open_routes = set()
    for path, ops in client.get("/openapi.json").json()["paths"].items():
        for method in ops:
            if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"} or path.startswith(("/auth/", "/demo")):
                continue
            if path == "/integrity/verify":  # POST only because it computes: a read-only recheck (case-scoped for non-privileged roles), changes no case data
                continue
            r = client.request(method.upper(), re.sub(r"\{[^}]+\}", "x", path), headers=headers, json={})
            if r.status_code != 403:
                open_routes.add((method.upper(), path))
    return open_routes


# Self-service items that only ever touch the caller's own records.
_SELF = {("POST", "/workflow/approvals/{aid}/withdraw"), ("POST", "/workflow/notifications/read-all"),
         ("POST", "/workflow/notifications/{nid}/read")}
_INVESTIGATOR_OK = _SELF | {
    # verify / review
    ("POST", "/documents/{document_id}/review"), ("PATCH", "/alerts/{code}"), ("POST", "/netai/link-predictions/accept"),
    ("POST", "/workflow/approvals"), ("POST", "/suspects/{entity_id}/decision"), ("POST", "/social/{case}/flags/{flag_id}/review"),
    # computation only
    ("POST", "/analytics/run"), ("POST", "/copilot"), 
    ("POST", "/workflow/watchlists/scan"), ("POST", "/social/{case}/intel/translate"),
    ("POST", "/social/{case}/intel/sheet"),  # profile sheet: read-only export, sealed in the ledger
    # export
    ("POST", "/governance/redacted-export"),
    # personal analysis workspace (saved link-chart views, watchlists): not case records
    
    ("POST", "/workflow/watchlists"), ("PATCH", "/workflow/watchlists/{wid}"), ("DELETE", "/workflow/watchlists/{wid}"),
    ("POST", "/workflow/watchlists/{wid}/items"), ("DELETE", "/workflow/watchlists/{wid}/items/{iid}"),
}


def test_investigator_mutating_surface_is_exactly_verify_compute_and_export(client, login):
    got = _login_mut_sweep(client, login("investigator"))
    assert got == _INVESTIGATOR_OK, {"unexpected": sorted(got - _INVESTIGATOR_OK), "missing": sorted(_INVESTIGATOR_OK - got)}


def test_auditor_mutating_surface(client, login):
    assert _login_mut_sweep(client, login("auditor")) == _SELF


def test_supervisor_reaches_every_investigator_route_but_not_user_admin(client, login):
    got = _login_mut_sweep(client, login("supervisor"))
    assert _INVESTIGATOR_OK <= got
    assert not [p for m, p in got if p.startswith(("/admin/", "/governance/backup", "/governance/cases/{case_number}/purge"))]
    assert ("POST", "/signup-requests/{rid}/supervisor-decision") in got
    assert ("POST", "/signup-requests/{rid}/create-user") not in got


def test_investigator_cannot_change_case_data_but_can_investigate(client, login):
    admin, sup, inv = login("admin"), login("supervisor"), login("investigator")
    cn = _uid("CASE-INV-")
    _mk_case(client, sup, cn, admin)
    body = {"case_number": cn}
    denied = [
        client.post("/cases", json={"case_number": _uid("CASE-X-"), "title": "t"}, headers=inv),
        client.post("/workbench/new", json={"case_number": _uid("CASE-Y-"), "title": "t"}, headers=inv),
        client.post("/entities", json={"external_id": "IX1", "name": "n", "entity_type": "PERSON", **body}, headers=inv),
        client.post("/relationships", json={"source_id": "a", "target_id": "b", "relation_type": "KNOWS", **body}, headers=inv),
        client.post("/events", json={"event_id": "EV-IX", "event_type": "MEETING", "entity_id": "a", **body}, headers=inv),
        client.post("/notes", json={"case_number": cn, "body": "x"}, headers=inv),
        client.post("/tasks", json={"case_number": cn, "title": "x"}, headers=inv),
        client.post("/documents/extract", files={"file": ("f.txt", b"FIR No: 1/2026", "text/plain")}, data=body, headers=inv),
        client.post("/documents/DOC-NONE/reprocess", headers=inv),
        client.post("/ingest/table", files={"file": ("t.csv", b"a,b\n1,2\n", "text/csv")}, data=body, headers=inv),
        client.post("/evidence/upload", files={"file": ("e.txt", b"abc", "text/plain")}, data=body, headers=inv),
        client.post("/admin/users", json={"username": "hax0r1", "password": "Hax0r-Pass-11", "role": "admin"}, headers=inv),
    ]
    assert [r.status_code for r in denied] == [403] * len(denied)
    # ... but investigating works
    assert client.get("/entities", params=body, headers=inv).status_code == 200
    assert client.post("/analytics/run", params=body, headers=inv).status_code == 200
    assert client.get(f"/report/{cn}", headers=inv).status_code == 200
    assert client.post("/copilot", json={"query": "summarise", "case_number": cn}, headers=inv).status_code == 200
    assert client.get(f"/cases/{cn}/workspace", headers=inv).status_code == 200


def test_supervisor_can_change_data_but_not_manage_users(client, login):
    admin, sup = login("admin"), login("supervisor")
    cn = _uid("CASE-SUP-")
    _mk_case(client, sup, cn, admin)
    assert client.post("/entities", json={"external_id": _uid("SUPE"), "name": "n", "entity_type": "PERSON", "case_number": cn}, headers=sup).status_code == 200
    assert client.post("/notes", json={"case_number": cn, "body": "note"}, headers=sup).status_code == 200
    assert client.post("/tasks", json={"case_number": cn, "title": "check"}, headers=sup).status_code == 200
    assert client.post("/evidence/upload", files={"file": ("e.txt", b"abc", "text/plain")}, data={"case_number": cn}, headers=sup).status_code == 200
    assert client.post("/ingest/table", files={"file": ("t.csv", b"source,target\nA1,B1\n", "text/csv")}, data={"case_number": cn}, headers=sup).status_code in (200, 400)
    assert client.get("/admin/users", headers=sup).status_code == 403
    assert client.post("/admin/users", json={"username": "hax0r2", "password": "Hax0r-Pass-11", "role": "supervisor"}, headers=sup).status_code == 403
    assert client.patch("/admin/users/investigator/role", json={"role": "admin"}, headers=sup).status_code == 403
    assert client.post("/demo/disable", headers=sup).status_code == 403


def test_new_roles_can_be_assigned_by_admin_only(client, login, make_user):
    admin = login("admin")
    name, _, _ = make_user("auditor")
    for role in ("auditor", "demo", "supervisor", "investigator"):
        assert client.patch(f"/admin/users/{name}/role", json={"role": role}, headers=admin).json()["role"] == role
    assert client.patch(f"/admin/users/{name}/role", json={"role": "root"}, headers=admin).status_code == 400
    assert client.patch(f"/admin/users/{name}/role", json={"role": "auditor"}, headers=login("supervisor")).status_code == 403


# ================================================================== auditor
def test_auditor_is_read_only_masked_and_sees_the_audit_log(client, login):
    admin, sup, aud = login("admin"), login("supervisor"), login("auditor")
    cn = _uid("CASE-AUD-")
    _mk_case(client, sup, cn, admin)
    assert client.post("/entities", json={"external_id": _uid("PHA"), "name": "9876543210", "entity_type": "PHONE", "case_number": cn}, headers=sup).status_code == 200
    assert client.post("/notes", json={"case_number": cn, "body": "confidential narrative"}, headers=sup).status_code == 200
    assert cn in [c["case_number"] for c in client.get("/cases", headers=aud).json()]
    ents = client.get("/entities", params={"case_number": cn}, headers=aud).json()
    assert ents and all("9876543210" not in json_text(e) for e in ents) and any("*" in e["name"] for e in ents)
    notes = client.get("/notes", params={"case_number": cn}, headers=aud).json()
    assert notes and all("confidential" not in n["body"] for n in notes)
    for path, code in [("/documents", 403), ("/evidence", 403), ("/analytics/overview", 403), (f"/report/{cn}", 403), ("/timeline", 403)]:
        assert client.get(path, params={"case_number": cn}, headers=aud).status_code == code, path
    assert client.post("/notes", json={"case_number": cn, "body": "x"}, headers=aud).status_code == 403
    assert client.post("/entities", json={"external_id": "A1", "name": "n", "entity_type": "PERSON", "case_number": cn}, headers=aud).status_code == 403
    audit = client.get("/audit", headers=aud)
    assert audit.status_code == 200 and audit.json()["events"]
    assert client.get("/audit/verify", headers=aud).json()["verified"] is True
    assert client.get("/governance/dashboard", headers=aud).status_code == 200
    assert client.get("/admin/users", headers=aud).status_code == 403


def json_text(obj):
    import json
    return json.dumps(obj)


# ================================================================== demo isolation + toggle
@pytest.fixture(scope="module")
def demo_world(client):
    """Real data + demo data side by side; demo data is switched off again afterwards."""
    def hdr(u):
        r = client.post("/auth/login", json={"username": u, "password": _PASSWORDS[u]}); assert r.status_code == 200, r.text
        return {"Authorization": "Bearer " + r.json()["access_token"]}
    admin, sup, demo = hdr("admin"), hdr("supervisor"), hdr("demo")
    real = "CASE-REAL-ISO"
    r = client.post("/workbench/new", json={"case_number": real, "title": "Real investigation"}, headers=sup); assert r.status_code == 200, r.text
    client.patch(f"/admin/cases/{real}/visibility", json={"visibility": "shared"}, headers=admin)
    assert client.post("/entities", json={"external_id": "REAL-P1", "name": "Real Person", "entity_type": "PERSON", "case_number": real}, headers=sup).status_code == 200
    assert client.post("/notes", json={"case_number": real, "body": "real note"}, headers=sup).status_code == 200
    on = client.post("/demo/enable", headers=demo)
    assert on.status_code == 200, on.text
    yield {"admin": admin, "sup": sup, "demo": demo, "real": real, "hdr": hdr}
    client.post("/demo/disable", headers=admin)


def test_demo_user_sees_only_demo_cases_and_nothing_administrative(client, demo_world):
    w = demo_world
    listed = [c["case_number"] for c in client.get("/cases", headers=w["demo"]).json()]
    assert listed and w["real"] not in listed
    with SessionLocal() as db:
        flagged = {c.case_number for c in db.query(Case).filter(Case.is_demo.is_(True))}
        assert set(listed) == flagged and "CASE-2026-101" in flagged and w["real"] not in flagged
    for path in ("/entities", "/events", "/relationships", "/alerts", "/tasks", "/documents", "/evidence", "/timeline", "/analytics/overview"):
        assert client.get(path, params={"case_number": w["real"]}, headers=w["demo"]).status_code == 404, path
    assert client.get(f"/report/{w['real']}", headers=w["demo"]).status_code == 404
    assert client.post("/notes", json={"case_number": w["real"], "body": "x"}, headers=w["demo"]).status_code == 404
    assert client.post("/entities", json={"external_id": "ZZ1", "name": "n", "entity_type": "PERSON", "case_number": w["real"]}, headers=w["demo"]).status_code == 404
    for path in ("/admin/users", "/audit", "/audit/verify", "/health/detail", f"/admin/cases/{w['real']}/members", "/governance/backups"):
        assert client.get(path, headers=w["demo"]).status_code == 403, path
    assert client.post("/admin/users", json={"username": "hax0r3", "password": "Hax0r-Pass-11", "role": "admin"}, headers=w["demo"]).status_code == 403
    assert client.get("/signup-requests", headers=w["demo"]).status_code == 403


def test_demo_user_has_supervisor_abilities_inside_demo_data_and_creates_demo_cases(client, demo_world):
    w = demo_world
    assert client.post("/notes", json={"case_number": "CASE-2026-101", "body": "demo note"}, headers=w["demo"]).status_code == 200
    assert client.post("/tasks", json={"case_number": "CASE-2026-101", "title": "demo task"}, headers=w["demo"]).status_code == 200
    assert client.get("/entities", params={"case_number": "CASE-2026-101"}, headers=w["demo"]).json()
    cn = _uid("CASE-DEMO-NEW-")
    assert client.post("/workbench/new", json={"case_number": cn, "title": "made by demo"}, headers=w["demo"]).status_code == 200
    with SessionLocal() as db:
        assert db.query(Case).filter_by(case_number=cn).first().is_demo is True
    assert cn in [c["case_number"] for c in client.get("/cases", headers=w["demo"]).json()]
    # the demo account can also create cases through the FIR upload path (auto workspace) and they are demo cases too
    r = client.post("/documents/extract", files={"file": ("f.txt", b"FIR No: 77/2026\nPolice Station: Test\nIncident: theft of vehicle.", "text/plain")}, headers=w["demo"])
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        assert db.query(Case).filter_by(case_number=r.json()["case_number"]).first().is_demo is True


@pytest.mark.parametrize("who", ["investigator", "supervisor", "auditor"])
def test_real_users_never_see_demo_cases(client, demo_world, who):
    w = demo_world
    h = w["hdr"](who)
    listed = [c["case_number"] for c in client.get("/cases", headers=h).json()]
    assert w["real"] in listed or who == "investigator"  # shared real case is visible (investigator sees it too)
    assert not [c for c in listed if c.startswith("CASE-2026-1") or c.startswith("CASE-DEMO")]
    assert client.get("/entities", params={"case_number": "CASE-2026-101"}, headers=h).status_code == 404
    assert client.get("/cases/CASE-2026-101/workspace", headers=h).status_code == 404
    assert client.get("/notes", params={"case_number": "CASE-2026-101"}, headers=h).status_code == 404
    with SessionLocal() as db:
        assert db.query(Case).filter_by(case_number="CASE-2026-101").first().is_demo is True


def test_admin_sees_real_and_demo_and_status_reports_counts(client, demo_world):
    w = demo_world
    listed = [c["case_number"] for c in client.get("/cases", headers=w["admin"]).json()]
    assert w["real"] in listed and "CASE-2026-101" in listed
    st = client.get("/demo/status", headers=w["admin"]).json()
    assert st["enabled"] and st["loaded"] and st["case_count"] >= 8 and st["counts"]["entities"] > 100 and st["counts"]["events"] > 1000
    assert client.get("/demo/status", headers=w["demo"]).json()["enabled"] is True
    for who in ("supervisor", "investigator", "auditor"):
        assert client.get("/demo/status", headers=w["hdr"](who)).status_code == 403


def test_enable_is_idempotent(client, demo_world):
    w = demo_world
    with SessionLocal() as db:
        before = (db.query(Case).count(), db.query(Entity).count(), db.query(Event).count())
    r = client.post("/demo/enable", headers=w["demo"])
    assert r.status_code == 200 and "already" in r.json()["message"].lower()
    assert client.post("/demo/load", headers=w["admin"]).status_code == 200  # legacy alias
    with SessionLocal() as db:
        assert (db.query(Case).count(), db.query(Entity).count(), db.query(Event).count()) == before


def test_disable_removes_only_demo_data_and_is_repeatable(client, demo_world):
    w = demo_world
    demo_case = _uid("CASE-DEMO-TMP-")
    assert client.post("/workbench/new", json={"case_number": demo_case, "title": "temp"}, headers=w["demo"]).status_code == 200
    assert client.post("/notes", json={"case_number": demo_case, "body": "temp note"}, headers=w["demo"]).status_code == 200
    with SessionLocal() as db:
        real_before = {"cases": db.query(Case).filter_by(case_number=w["real"]).count(),
                       "entity": db.query(Entity).filter_by(external_id="REAL-P1").count(),
                       "notes": db.query(Note).filter_by(case_number=w["real"]).count(),
                       "users": db.query(User).count()}
        real_events = db.query(Event).filter(Event.metadata_json.notlike('%CASE-2026-%')).count()
    assert real_before == {"cases": 1, "entity": 1, "notes": 1, "users": real_before["users"]}
    r = client.post("/demo/disable", headers=w["demo"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False and body["removed"]["cases"] >= 9 and body["removed"]["entities"] > 100
    with SessionLocal() as db:
        assert db.query(Case).filter(Case.is_demo.is_(True)).count() == 0
        assert db.query(Case).filter_by(case_number="CASE-2026-101").count() == 0 and db.query(Case).filter_by(case_number=demo_case).count() == 0
        assert db.query(Entity).filter_by(external_id="P001").count() == 0 and db.query(Entity).filter_by(external_id="L001").count() == 0
        assert db.query(Note).filter_by(case_number=demo_case).count() == 0
        assert db.query(Relationship).filter(Relationship.model_version.like("synthetic%")).count() == 0
        # real data untouched
        assert db.query(Case).filter_by(case_number=w["real"]).count() == real_before["cases"]
        assert db.query(Entity).filter_by(external_id="REAL-P1").count() == real_before["entity"]
        assert db.query(Note).filter_by(case_number=w["real"]).count() == real_before["notes"]
        assert db.query(User).count() == real_before["users"]
        assert db.query(Event).filter(Event.metadata_json.notlike('%CASE-2026-%')).count() == real_events
    assert client.get("/entities", params={"case_number": w["real"]}, headers=w["sup"]).json()
    # demo user now sees empty states, not errors
    assert client.get("/cases", headers=w["demo"]).json() == []
    assert client.get("/demo/status", headers=w["demo"]).json()["enabled"] is False
    assert client.get("/entities", params={"case_number": "CASE-2026-101"}, headers=w["demo"]).status_code == 404
    # repeatable
    again = client.post("/demo/disable", headers=w["admin"]).json()
    assert again["enabled"] is False and again["removed"] == {"cases": 0}
    # audit trail
    actions = [e["action"] for e in client.get("/audit", headers=w["admin"]).json()["events"]]
    assert "demo.enabled" in actions and actions.count("demo.disabled") >= 2
    # and it comes back
    back = client.post("/demo/enable", headers=w["demo"]).json()
    assert back["enabled"] and back["case_count"] == 8  # user-made demo cases are gone, seed cases are back
    assert client.post("/notes", json={"case_number": "CASE-2026-101", "body": "again"}, headers=w["demo"]).status_code == 200


def test_disable_keeps_real_data_that_shares_ids_or_tags(client, demo_world):
    """A real case's entity that also lists a demo case in case_numbers is never deleted."""
    w = demo_world
    client.post("/demo/enable", headers=w["admin"])
    import json as _j
    with SessionLocal() as db:
        e = Entity(external_id="SHARED-1", name="shared", entity_type="PERSON",
                   attributes=_j.dumps({"case_number": w["real"], "case_numbers": [w["real"], "CASE-2026-101"]}))
        db.add(e); db.commit()
    client.post("/demo/disable", headers=w["admin"])
    with SessionLocal() as db:
        assert db.query(Entity).filter_by(external_id="SHARED-1").count() == 1
        db.query(Entity).filter_by(external_id="SHARED-1").delete(); db.commit()
    client.post("/demo/enable", headers=w["admin"])


# ================================================================== upgrade of an existing database
def test_existing_database_is_migrated_in_place_and_seed_cases_flagged():
    tmp = Path(tempfile.mkdtemp(prefix="dcn-mig-"))
    db_path = tmp / "old.db"
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE cases (id INTEGER PRIMARY KEY, case_number VARCHAR(100), title VARCHAR(255), status VARCHAR(50),
                            summary TEXT, created_by VARCHAR(100), visibility VARCHAR(20), created_at DATETIME);
        INSERT INTO cases (case_number, title, status, created_by, visibility) VALUES
          ('CASE-2026-101', 'Synthetic Network Investigation A', 'Active', 'seed_demo', 'shared'),
          ('REAL-1', 'A real case', 'Active', 'alice', 'restricted'),
          ('REAL-2', 'Synthetic Network Investigation but typed by a person', 'Active', 'bob', 'shared');
    """)
    con.commit(); con.close()
    env = {k: v for k, v in os.environ.items() if k not in {"ADMIN_PASSWORD", "INVESTIGATOR_PASSWORD", "SUPERVISOR_PASSWORD"}}
    env.update(DATABASE_URL=f"sqlite:///{db_path}", EVIDENCE_DIR=str(tmp / "ev"), APP_ENV="demo")
    code = "import backend.app.main; import backend.app.main as m; print('ok')"
    for _ in range(2):  # twice: the migration is idempotent
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(ROOT))
        assert r.returncode == 0 and "ok" in r.stdout, r.stderr
    con = sqlite3.connect(db_path)
    flags = dict(con.execute("SELECT case_number, is_demo FROM cases").fetchall())
    assert flags == {"CASE-2026-101": 1, "REAL-1": 0, "REAL-2": 0}
    assert sorted(u for (u,) in con.execute("SELECT username FROM users")) == ["admin", "auditor", "demo", "investigator", "supervisor"]


# ================================================================== fixed demo passwords + policy
def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.endswith("_PASSWORD") and k not in {"SECRET_KEY", "APP_ENV", "DATABASE_URL"}}
    env.update(extra)
    return env


def test_fixed_demo_passwords_work_and_are_not_written_to_a_file():
    tmp = Path(tempfile.mkdtemp(prefix="dcn-pw-"))
    cred = ROOT / "initial_credentials.txt"
    existed = cred.exists(); mtime = cred.stat().st_mtime if existed else None
    code = ("from fastapi.testclient import TestClient;from backend.app.main import app;c=TestClient(app);"
            "pw={'admin':'admin123','demo':'demo123','investigator':'invest123','supervisor':'super123','auditor':'audit123'};"
            "res={u:c.post('/auth/login',json={'username':u,'password':p}).json().get('role') for u,p in pw.items()};"
            "print(res)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT),
                       env=_clean_env(APP_ENV="demo", SECRET_KEY="d" * 48, DATABASE_URL=f"sqlite:///{tmp / 'p.db'}", EVIDENCE_DIR=str(tmp / "ev")))
    assert r.returncode == 0, r.stderr
    assert eval(r.stdout.strip().splitlines()[-1]) == {u: u for u in ("admin", "demo", "investigator", "supervisor", "auditor")}
    assert "admin123" not in r.stdout.replace("'admin123'", "")  # never echoed
    assert cred.exists() == existed and (not existed or cred.stat().st_mtime == mtime)


def test_environment_variables_override_the_demo_passwords():
    tmp = Path(tempfile.mkdtemp(prefix="dcn-pw2-"))
    code = ("from fastapi.testclient import TestClient;from backend.app.main import app;c=TestClient(app);"
            "print(c.post('/auth/login',json={'username':'admin','password':'admin123'}).status_code,"
            "c.post('/auth/login',json={'username':'admin','password':'Custom-Admin-77'}).status_code)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT),
                       env=_clean_env(APP_ENV="demo", SECRET_KEY="d" * 48, ADMIN_PASSWORD="Custom-Admin-77",
                                      DATABASE_URL=f"sqlite:///{tmp / 'p.db'}", EVIDENCE_DIR=str(tmp / "ev")))
    assert r.stdout.split()[-2:] == ["401", "200"], r.stderr


def test_password_policy_is_relaxed_in_demo_and_strict_otherwise(monkeypatch):
    from fastapi import HTTPException
    from security import passwords
    monkeypatch.setattr(passwords.settings, "app_env", "demo")
    passwords.validate_password("abcdef", "someone"); passwords.validate_password("admin123", "admin")  # 6+ chars, no letter+digit rule
    for bad in ("abcde", "", "Someone"):
        with pytest.raises(HTTPException):
            passwords.validate_password(bad, "someone")
    monkeypatch.setattr(passwords.settings, "app_env", "production")
    for bad in ("abcdef", "abcdefghijk", "short1", "Password123", "admin123", "xxsomeone123xx"):
        with pytest.raises(HTTPException):
            passwords.validate_password(bad, "someone")
    passwords.validate_password("Str0ng-Passphrase-12", "someone")


def test_production_bootstraps_only_admin_with_random_password(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.app.db.database import Base
    from security import auth
    for k in [k for k in os.environ if k.endswith("_PASSWORD")]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(auth.settings, "app_env", "production")
    monkeypatch.setattr(auth, "ROOT", tmp_path)
    eng = create_engine("sqlite:///:memory:"); Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as db:
        auth.bootstrap_users(db)
        rows = db.query(User).all()
        assert [u.username for u in rows] == ["admin"]
        assert not any(auth._verify(p, rows[0].password_hash) for p in ("admin123", "OLDPASS123"))
    assert "admin:" in (tmp_path / "initial_credentials.txt").read_text()


# ================================================================== sign-up request workflow
def _req(username=None, role="investigator", password="Signup-Pass-77", **over):
    body = {"username": username or _uid("newoff"), "full_name": "Asha Verma", "email": "asha.verma@police.example",
            "designation": "Inspector", "badge_no": "B-1234", "requested_role": role, "reason": "Joining the cyber cell", "password": password}
    body.update(over)
    return body


def _find(client, headers, username):
    rows = [r for r in client.get("/signup-requests", headers=headers).json()["requests"] if r["username"] == username]
    return rows[0] if rows else None


def test_signup_full_flow_needs_supervisor_before_admin(client, login):
    admin, sup = login("admin"), login("supervisor")
    body = _req()
    r = client.post("/auth/signup-request", json=body)
    assert r.status_code == 202 and r.json()["status"] == "received"
    assert body["password"] not in r.text
    row = _find(client, admin, body["username"]); rid = row["id"]
    assert row["status"] == "pending_supervisor" and row["can_create"] is False and "password" not in str(row).lower().replace("password_hash", "")
    assert "password_hash" not in row
    # not before the supervisor
    assert client.post(f"/signup-requests/{rid}/create-user", json={}, headers=admin).status_code == 409
    assert client.post(f"/signup-requests/{rid}/supervisor-decision", json={"approve": True}, headers=admin).status_code == 403   # no admin bypass
    assert client.post(f"/signup-requests/{rid}/supervisor-decision", json={"approve": True}, headers=login("investigator")).status_code == 403
    assert client.post(f"/signup-requests/{rid}/supervisor-decision", json={"approve": True}, headers=login("demo")).status_code == 403
    assert client.post("/auth/login", json={"username": body["username"], "password": body["password"]}).status_code == 401
    # supervisor queue
    assert _find(client, sup, body["username"])["can_decide"] is True
    d = client.post(f"/signup-requests/{rid}/supervisor-decision", json={"approve": True, "note": "Known officer"}, headers=sup)
    assert d.status_code == 200 and d.json()["status"] == "supervisor_approved" and d.json()["supervisor_by"] == "supervisor"
    assert client.post(f"/signup-requests/{rid}/supervisor-decision", json={"approve": False}, headers=sup).status_code == 409
    # only admin creates
    assert client.post(f"/signup-requests/{rid}/create-user", json={}, headers=sup).status_code == 403
    c = client.post(f"/signup-requests/{rid}/create-user", json={}, headers=admin)
    assert c.status_code == 200 and c.json()["status"] == "created" and c.json()["final_role"] == "investigator"
    assert client.post(f"/signup-requests/{rid}/create-user", json={}, headers=admin).status_code == 409
    lg = client.post("/auth/login", json={"username": body["username"], "password": body["password"]})
    assert lg.status_code == 200 and lg.json()["role"] == "investigator"
    with SessionLocal() as db:
        from backend.app.features.signup import SignupRequest
        assert db.query(SignupRequest).filter_by(id=rid).first().password_hash == ""   # hash wiped once the account exists
    actions = [e["action"] for e in client.get("/audit", headers=admin).json()["events"]]
    assert {"signup.requested", "signup.supervisor_approved", "signup.user_created"} <= set(actions)


def test_admin_may_choose_a_different_role_than_requested(client, login):
    admin, sup = login("admin"), login("supervisor")
    body = _req(role="supervisor")
    client.post("/auth/signup-request", json=body)
    rid = _find(client, admin, body["username"])["id"]
    client.post(f"/signup-requests/{rid}/supervisor-decision", json={"approve": True}, headers=sup)
    assert client.post(f"/signup-requests/{rid}/create-user", json={"role": "wizard"}, headers=admin).status_code == 400
    c = client.post(f"/signup-requests/{rid}/create-user", json={"role": "auditor"}, headers=admin)
    assert c.json()["final_role"] == "auditor"
    assert client.post("/auth/login", json={"username": body["username"], "password": body["password"]}).json()["role"] == "auditor"


def test_supervisor_rejection_blocks_creation_and_frees_the_username(client, login):
    admin, sup = login("admin"), login("supervisor")
    body = _req()
    client.post("/auth/signup-request", json=body)
    rid = _find(client, admin, body["username"])["id"]
    d = client.post(f"/signup-requests/{rid}/supervisor-decision", json={"approve": False, "note": "Not in our unit"}, headers=sup)
    assert d.json()["status"] == "supervisor_rejected"
    assert client.post(f"/signup-requests/{rid}/create-user", json={}, headers=admin).status_code == 409
    assert client.post(f"/signup-requests/{rid}/reject", json={"reason": "closing"}, headers=admin).status_code == 409   # already closed
    client.post("/auth/signup-request", json=body)  # may apply again
    assert len([r for r in client.get("/signup-requests", headers=admin).json()["requests"] if r["username"] == body["username"]]) == 2


def test_admin_can_reject_an_open_request_with_a_reason(client, login):
    admin, sup = login("admin"), login("supervisor")
    body = _req()
    client.post("/auth/signup-request", json=body)
    rid = _find(client, admin, body["username"])["id"]
    assert client.post(f"/signup-requests/{rid}/reject", json={}, headers=admin).status_code == 422
    assert client.post(f"/signup-requests/{rid}/reject", json={"reason": "no"}, headers=sup).status_code == 403
    r = client.post(f"/signup-requests/{rid}/reject", json={"reason": "Not authorised for this system"}, headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["admin_note"]
    assert client.post(f"/signup-requests/{rid}/supervisor-decision", json={"approve": True}, headers=sup).status_code == 409
    assert client.post("/auth/login", json={"username": body["username"], "password": body["password"]}).status_code == 401


def test_supervisor_sees_queue_and_own_history_admin_sees_all_others_nothing(client, login):
    admin, sup = login("admin"), login("supervisor")
    a, b = _req(), _req()
    client.post("/auth/signup-request", json=a); client.post("/auth/signup-request", json=b)
    ra, rb = _find(client, admin, a["username"])["id"], _find(client, admin, b["username"])["id"]
    client.post(f"/signup-requests/{ra}/supervisor-decision", json={"approve": True}, headers=sup)
    client.post(f"/signup-requests/{ra}/create-user", json={}, headers=admin)      # closed, decided by supervisor
    assert _find(client, sup, a["username"]) and _find(client, sup, b["username"])  # history + queue
    client.post(f"/signup-requests/{rb}/reject", json={"reason": "not eligible"}, headers=admin)  # rejected by admin, never decided by supervisor
    assert _find(client, sup, b["username"]) is None
    assert _find(client, admin, b["username"])["status"] == "rejected"
    for who in ("investigator", "auditor"):
        assert client.get("/signup-requests", headers=login(who)).status_code == 403
    assert client.get("/signup-requests").status_code == 401


def test_signup_answers_are_generic_and_duplicates_are_not_stored(client, login):
    admin = login("admin")
    fresh = client.post("/auth/signup-request", json=_req())
    existing = client.post("/auth/signup-request", json=_req(username="admin"))            # a real account
    assert existing.status_code == fresh.status_code == 202 and existing.json() == fresh.json()
    assert _find(client, admin, "admin") is None
    body = _req()
    client.post("/auth/signup-request", json=body)
    again = client.post("/auth/signup-request", json=body)                                 # already pending
    assert again.status_code == 202 and again.json() == fresh.json()
    assert len([r for r in client.get("/signup-requests", headers=admin).json()["requests"] if r["username"] == body["username"]]) == 1


@pytest.mark.parametrize("patch,codes", [
    ({"requested_role": "admin"}, (422,)), ({"requested_role": "demo"}, (422,)), ({"username": "a b"}, (400,)), ({"username": "ab"}, (422,)),
    ({"email": "not-an-email"}, (400,)), ({"password": "abc"}, (422, 400)), ({"reason": ""}, (422,)), ({"full_name": ""}, (422,)),
])
def test_signup_rejects_malformed_requests(client, patch, codes):
    assert client.post("/auth/signup-request", json=_req(**patch)).status_code in codes


def test_signup_password_policy_and_no_secret_leak(client, login, monkeypatch):
    from security import passwords
    monkeypatch.setattr(passwords.settings, "app_env", "production")   # strict policy applies to requests too
    r = client.post("/auth/signup-request", json=_req(password="alllettersonly"))
    assert r.status_code == 400 and "alllettersonly" not in r.text
    body = _req(password="Fine-Password-91")
    assert client.post("/auth/signup-request", json=body).status_code == 202
    monkeypatch.setattr(passwords.settings, "app_env", "demo")
    with SessionLocal() as db:
        from backend.app.features.signup import SignupRequest
        row = db.query(SignupRequest).filter_by(username=body["username"]).first()
        assert row.password_hash.startswith("pbkdf2_sha256$") and "Fine-Password-91" not in row.password_hash
    text = str(client.get("/signup-requests", headers=login("admin")).json())
    assert "Fine-Password-91" not in text and "pbkdf2" not in text


def test_signup_is_rate_limited(client):
    codes = [client.post("/auth/signup-request", json=_req()).status_code for _ in range(7)]
    assert codes[:5] == [202] * 5 and codes[5:] == [429, 429]


def test_supervisors_are_notified_of_new_requests(client, login):
    sup = login("supervisor")
    body = _req()
    client.post("/auth/signup-request", json=body)
    notes = client.get("/workflow/notifications", headers=sup).json()
    rows = notes["notifications"] if isinstance(notes, dict) else notes
    assert any(n.get("kind") == "signup_request" for n in rows)
