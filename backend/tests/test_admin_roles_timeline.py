import pytest


def test_three_roles_and_admin_portal(client, login):
    ah, ih, sh = login("admin"), login("supervisor"), login("auditor")
    assert client.get("/admin/users", headers=ah).status_code == 200
    assert client.get("/admin/users", headers=ih).status_code == 403
    assert client.get("/admin/users", headers=sh).status_code == 403
    assert client.post("/workbench/new", json={"case_number": "CASE-ROLE", "title": "t"}, headers=ih).status_code == 200
    client.post("/admin/cases/CASE-ROLE/members", json={"username": "auditor"}, headers=ah)
    assert client.post("/notes", json={"case_number": "CASE-ROLE", "body": "Review source chronology."}, headers=ih).status_code == 200
    assert client.post("/documents/extract", files={"file": ("x.txt", b"FIR No: 1/2026", "text/plain")}, data={"case_number": "CASE-ROLE"}, headers=sh).status_code == 403


def test_timeline_compare_unknown_case_is_404(client, login):
    r = client.get("/timeline/compare", params={"case_number": "CASE-DOES-NOT-EXIST", "fir_numbers": "A,B"}, headers=login("supervisor"))
    assert r.status_code == 404


def test_timeline_compare_contract(client, login):
    h = login("supervisor")
    client.post("/workbench/new", json={"case_number": "CASE-TLC", "title": "t"}, headers=h)
    r = client.get("/timeline/compare", params={"case_number": "CASE-TLC", "fir_numbers": "A,B"}, headers=h)
    assert r.status_code == 200
    data = r.json(); assert data["firs"] == ["A", "B"]; assert "interlinks" in data


def test_mfa_is_gone(client, login):
    assert client.post("/auth/mfa/setup", headers=login("admin")).status_code == 404
    assert "mfa" not in client.get("/me", headers=login("admin")).text.lower()


def test_new_workbench_and_neighborhood_contract(client, login):
    h = login("supervisor")
    assert client.post("/workbench/new", json={"case_number": "CASE-TEST-WB-430", "title": "Fresh test", "summary": "x"}, headers=h).status_code in (200, 409)
    n = client.get("/graph/neighborhood", params={"case_number": "CASE-TEST-WB-430", "entity_id": "UNKNOWN", "hops": 99}, headers=h)
    assert n.status_code == 200
    data = n.json(); assert data["center_found"] is False and data["hops"] == 5


def test_username_change_kills_old_token_and_is_not_reseeded(client, make_user, login):
    old, pw, h = make_user("auditor")
    new = old + "x"
    assert client.post("/auth/change-username", json={"current_password": pw, "new_username": new}, headers=h).status_code == 200
    assert client.get("/me", headers=h).status_code == 401           # old token is dead
    assert client.post("/auth/login", json={"username": old, "password": pw}).status_code == 401
    assert client.post("/auth/login", json={"username": new, "password": pw}).status_code == 200


def test_renamed_bootstrap_account_is_not_recreated(client, login):
    """Old behaviour re-created 'auditor' with the default password after a rename."""
    from backend.app.db.database import SessionLocal
    from backend.app.models.models import User
    from security.auth import bootstrap_users
    with SessionLocal() as db:
        before = db.query(User).count()
        bootstrap_users(db)
        assert db.query(User).count() == before
