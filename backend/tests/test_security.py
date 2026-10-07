"""Regression tests for every vulnerability found in the v4.3.2 review."""
import logging
import time

import jwt
import pytest

from backend.app.core.config import settings


# ---------------------------------------------------------------- account takeover / tokens
def test_self_service_recovery_endpoints_are_gone(client):
    """The log-code recovery flow (and its takeover surface) was removed; admins reset passwords."""
    assert client.post("/auth/forgot-password", json={"username": "admin"}).status_code == 404
    assert client.post("/auth/reset-password", json={"username": "admin", "recovery_token": "x", "new_password": "Whatever-Pass-11"}).status_code == 404
    assert client.get("/openapi.json").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if "forgot" in p or "reset-password" in p and "/admin/" not in p]
    assert "/auth/mfa/verify" in paths  # role-based two-step sign-in (v5.1); the old self-service MFA reset stays gone


def test_cli_can_set_password_and_revokes_sessions(client, make_user):
    import subprocess, sys, os
    name, pw, h = make_user("supervisor")
    r = subprocess.run([sys.executable, "scripts/set_password.py", name, "--password", "Cli-Changed-Pass-42"], capture_output=True, text=True, env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    assert client.get("/me", headers=h).status_code == 401
    assert client.post("/auth/login", json={"username": name, "password": "Cli-Changed-Pass-42"}).status_code == 200
    assert client.post("/auth/login", json={"username": name, "password": pw}).status_code == 401


def test_admin_password_reset_signs_user_out_and_needs_no_forced_change(client, login, make_user):
    name, pw, h = make_user("supervisor")
    assert client.post(f"/admin/users/{name}/reset-password", json={"new_password": "Reset-By-Admin-77"}, headers=login("admin")).status_code == 200
    assert client.get("/me", headers=h).status_code == 401
    r = client.post("/auth/login", json={"username": name, "password": "Reset-By-Admin-77"})
    assert r.status_code == 200 and "must_change_password" not in r.json()
    assert client.get("/cases", headers={"Authorization": "Bearer " + r.json()["access_token"]}).status_code == 200


def test_secret_is_not_the_old_hardcoded_default():
    assert settings.secret_key != "darkcrimenet-demo-secret-key-2026"
    assert len(settings.secret_key) >= 32


def test_token_signed_with_old_default_secret_is_rejected(client):
    forged = jwt.encode({"sub": "admin", "role": "admin", "tv": 0, "exp": time.time() + 999},
                        "darkcrimenet-demo-secret-key-2026", algorithm="HS256")
    assert client.get("/admin/users", headers={"Authorization": "Bearer " + forged}).status_code == 401


def test_validly_signed_token_for_nonexistent_user_is_rejected(client):
    forged = jwt.encode({"sub": "ghost", "role": "admin", "tv": 0, "exp": time.time() + 999}, settings.secret_key, algorithm="HS256")
    assert client.get("/admin/users", headers={"Authorization": "Bearer " + forged}).status_code == 401


def test_role_in_token_is_ignored_database_role_wins(client, make_user):
    name, _, _ = make_user("auditor")
    from backend.app.db.database import SessionLocal
    from backend.app.models.models import User
    with SessionLocal() as db:
        tv = db.query(User).filter_by(username=name).first().token_version
    forged = jwt.encode({"sub": name, "role": "admin", "tv": tv, "exp": time.time() + 999}, settings.secret_key, algorithm="HS256")
    assert client.get("/admin/users", headers={"Authorization": "Bearer " + forged}).status_code == 403


def test_deactivated_user_token_dies_immediately(client, login, make_user):
    name, _, h = make_user("supervisor")
    assert client.get("/cases", headers=h).status_code == 200
    assert client.delete(f"/admin/users/{name}", headers=login("admin")).status_code == 200
    assert client.get("/cases", headers=h).status_code == 401


def test_role_change_takes_effect_on_existing_token(client, login, make_user):
    name, _, h = make_user("supervisor")
    client.patch(f"/admin/users/{name}/role", json={"role": "auditor"}, headers=login("admin"))
    assert client.get("/cases", headers=h).status_code == 401  # must re-login, then gets the new role


def test_password_change_signs_out_other_sessions_and_returns_fresh_token(client, make_user):
    name, pw, h_old = make_user("supervisor")
    r = client.post("/auth/change-password", json={"current_password": pw, "new_password": "Yet-Another-Pass-5"}, headers=h_old)
    assert r.status_code == 200
    assert client.get("/me", headers=h_old).status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer " + r.json()["access_token"]}).status_code == 200


def test_old_default_password_is_not_a_backdoor(client, login):
    assert client.post("/auth/login", json={"username": "admin", "password": "OLDPASS123"}).status_code == 401


@pytest.mark.parametrize("pw", ["abc", "12345"])
def test_weak_passwords_are_rejected(client, login, pw):
    r = client.post("/admin/users", json={"username": "weakpw-user", "password": pw, "role": "auditor"}, headers=login("admin"))
    assert r.status_code in (400, 422)


# ---------------------------------------------------------------- brute force
def test_login_is_rate_limited(client):
    codes = [client.post("/auth/login", json={"username": "admin", "password": f"wrong-{i}"}).status_code for i in range(8)]
    assert codes[:5] == [401] * 5 and 429 in codes[5:]
    # a correct password is also refused while locked (no oracle)
    assert client.post("/auth/login", json={"username": "admin", "password": "Admin-Test-Pass-1"}).status_code == 429


# ---------------------------------------------------------------- per-case authorization
def _mk_case(client, h, number):
    r = client.post("/workbench/new", json={"case_number": number, "title": "t"}, headers=h); assert r.status_code == 200, r.text


def test_restricted_case_is_invisible_to_other_investigators(client, login, make_user):
    _, _, owner = make_user("supervisor")
    _, _, other = make_user("supervisor")
    _mk_case(client, owner, "CASE-PRIVATE-1")
    assert "CASE-PRIVATE-1" in [c["case_number"] for c in client.get("/cases", headers=owner).json()]
    assert "CASE-PRIVATE-1" not in [c["case_number"] for c in client.get("/cases", headers=other).json()]
    for path in ["/entities", "/events", "/relationships", "/evidence", "/alerts", "/tasks", "/documents", "/timeline", "/analytics/overview", "/fir-relations"]:
        assert client.get(path, params={"case_number": "CASE-PRIVATE-1"}, headers=other).status_code == 404, path
    assert client.get("/report/CASE-PRIVATE-1", headers=other).status_code == 404
    assert client.get("/cases/CASE-PRIVATE-1/workspace", headers=other).status_code == 404
    assert client.post("/notes", json={"case_number": "CASE-PRIVATE-1", "body": "x"}, headers=other).status_code == 404
    assert client.get("/entities", params={"case_number": "CASE-PRIVATE-1"}, headers=login("admin")).status_code == 200


def test_omitting_case_number_is_not_a_global_feed_for_non_admins(client, login):
    assert client.get("/entities", headers=login("supervisor")).status_code == 400
    assert client.get("/entities", headers=login("admin")).status_code == 200


def test_admin_can_grant_and_revoke_membership(client, login, make_user):
    _, _, owner = make_user("supervisor")
    name, _, other = make_user("supervisor")
    _mk_case(client, owner, "CASE-PRIVATE-2")
    ah = login("admin")
    assert client.post("/admin/cases/CASE-PRIVATE-2/members", json={"username": name}, headers=ah).status_code == 200
    assert client.get("/entities", params={"case_number": "CASE-PRIVATE-2"}, headers=other).status_code == 200
    client.delete(f"/admin/cases/CASE-PRIVATE-2/members/{name}", headers=ah)
    assert client.get("/entities", params={"case_number": "CASE-PRIVATE-2"}, headers=other).status_code == 404


def test_note_on_nonexistent_case_is_rejected(client, login):
    assert client.post("/notes", json={"case_number": "NOPE-NOPE", "body": "x"}, headers=login("supervisor")).status_code == 404


def test_existing_case_cannot_be_overwritten_via_create_endpoint(client, login, make_user):
    _, _, owner = make_user("supervisor")
    _mk_case(client, owner, "CASE-KEEP-1")
    r = client.post("/cases", json={"case_number": "CASE-KEEP-1", "title": "HIJACKED"}, headers=login("supervisor"))
    assert r.json()["status"] == "exists"
    titles = [c["title"] for c in client.get("/cases", headers=owner).json() if c["case_number"] == "CASE-KEEP-1"]
    assert titles == ["t"]


def test_derived_case_number_never_attaches_to_someone_elses_case(client, make_user):
    _, _, a = make_user("supervisor"); _, _, b = make_user("supervisor")
    fir = b"FIRST INFORMATION REPORT\nFIR No: 7777/2026\nDate of FIR: 05/09/2026\nPhone: 9876543210\n"
    ra = client.post("/documents/extract", files={"file": ("a.txt", fir, "text/plain")}, headers=a).json()
    rb = client.post("/documents/extract", files={"file": ("b.txt", fir, "text/plain")}, headers=b).json()
    assert ra["case_number"] != rb["case_number"]


# ---------------------------------------------------------------- roles / masking
def test_auditor_permissions_and_masking(client, login, make_user):
    _, _, inv = make_user("supervisor")
    _mk_case(client, inv, "CASE-MASK-1")
    ah = login("admin")
    sub_name, _, sub = make_user("auditor")
    client.post("/admin/cases/CASE-MASK-1/members", json={"username": sub_name}, headers=ah)
    assert client.post("/entities", json={"external_id": "PH-MASK-1", "name": "9876543210", "entity_type": "PHONE", "case_number": "CASE-MASK-1"}, headers=inv).status_code == 200
    seen = client.get("/entities", params={"case_number": "CASE-MASK-1"}, headers=sub).json()
    phone = next(e for e in seen if e["external_id"] == "PH-MASK-1")
    assert phone["name"] != "9876543210" and "*" in phone["name"]
    assert next(e for e in client.get("/entities", params={"case_number": "CASE-MASK-1"}, headers=inv).json() if e["external_id"] == "PH-MASK-1")["name"] == "9876543210"
    assert "9876543210" not in client.get("/graph/nodes", params={"case_number": "CASE-MASK-1"}, headers=sub).text
    assert "9876543210" not in client.get("/search", params={"q": "PH-MASK", "case_number": "CASE-MASK-1"}, headers=sub).text
    ws = client.get("/cases/CASE-MASK-1/workspace", headers=sub).json()
    assert ws["documents"] == [] and ws["evidence"] == []
    for path in ["/report/CASE-MASK-1", "/evidence", "/documents", "/timeline", "/analytics/overview"]:
        assert client.get(path, params={"case_number": "CASE-MASK-1"}, headers=sub).status_code == 403, path
    assert client.post("/copilot", json={"query": "summary", "case_number": "CASE-MASK-1"}, headers=sub).status_code == 403
    assert client.post("/entities", json={"external_id": "X", "name": "x", "entity_type": "PERSON", "case_number": "CASE-MASK-1"}, headers=sub).status_code == 403


def test_audit_log_is_case_scoped_for_non_admins(client, login, make_user):
    _, _, owner = make_user("supervisor"); _, _, other = make_user("supervisor")
    _mk_case(client, owner, "CASE-AUD-1")
    assert client.get("/audit", headers=owner).status_code == 400                                   # no global log
    assert client.get("/audit", params={"case_number": "CASE-AUD-1"}, headers=owner).status_code == 200
    assert client.get("/audit", params={"case_number": "CASE-AUD-1"}, headers=other).status_code == 404
    assert client.get("/audit", headers=login("admin")).status_code == 200                          # admin: global
    assert client.get("/audit", headers=login("demo")).status_code == 403
    assert client.get("/audit/verify", headers=login("demo")).status_code == 403
    assert client.get("/audit/verify", headers=owner).status_code == 200


# ---------------------------------------------------------------- audit chain
def test_audit_chain_detects_tampering_and_deletion(client, login):
    from sqlalchemy import text
    from backend.app.db.database import SessionLocal
    ah = login("admin")
    assert client.get("/audit/verify", headers=ah).json()["verified"] is True
    with SessionLocal() as db:
        first_login = db.execute(text("select id from audit_events where action='auth.login' order by id limit 1")).scalar()
        db.execute(text("update audit_events set actor='mallory' where id=:i"), {"i": first_login}); db.commit()
    res = client.get("/audit/verify", headers=ah).json()
    assert res["verified"] is False and res["first_bad_event"] == first_login
    with SessionLocal() as db:  # restore
        db.execute(text("update audit_events set actor='admin' where id=:i"), {"i": first_login}); db.commit()
    assert client.get("/audit/verify", headers=ah).json()["verified"] is True
    with SessionLocal() as db:
        victim = db.execute(text("select id from audit_events order by id limit 1 offset 2")).scalar()
        row = db.execute(text("select actor,action,details,previous_hash,event_hash,created_at from audit_events where id=:i"), {"i": victim}).mappings().first()
        db.execute(text("delete from audit_events where id=:i"), {"i": victim}); db.commit()
    assert client.get("/audit/verify", headers=ah).json()["verified"] is False
    with SessionLocal() as db:
        db.execute(text("insert into audit_events(id,actor,action,details,previous_hash,event_hash,created_at) values (:i,:a,:b,:c,:d,:e,:f)"),
                   {"i": victim, "a": row["actor"], "b": row["action"], "c": row["details"], "d": row["previous_hash"], "e": row["event_hash"], "f": row["created_at"]}); db.commit()
    assert client.get("/audit/verify", headers=ah).json()["verified"] is True


def test_chain_cannot_be_recomputed_without_the_server_secret():
    import hashlib, json
    body = {"actor": "a", "action": "x", "details": "{}", "timestamp": "t", "previous_hash": "GENESIS"}
    plain = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    from backend.app.services.app_service import AppService
    assert AppService._audit_digest(body) != plain


# ---------------------------------------------------------------- uploads / evidence
def test_upload_limits_types_and_signatures(client, login, make_user):
    _, _, h = make_user("supervisor")
    _mk_case(client, h, "CASE-UP-1")
    up = lambda name, data, ctype="application/octet-stream": client.post("/evidence/upload", files={"file": (name, data, ctype)}, data={"case_number": "CASE-UP-1"}, headers=h)
    assert up("big.txt", b"a" * (1024 * 1024 + 10)).status_code == 413            # MAX_UPLOAD_MB=1 in tests
    assert up("x.html", b"<script>alert(1)</script>", "text/html").status_code == 400
    assert up("x.exe", b"MZ....").status_code == 400
    assert up("fake.png", b"this is not a png").status_code == 400                # signature check
    assert up("empty.txt", b"").status_code == 400
    assert up("ok.txt", b"legitimate evidence").status_code == 200


def test_evidence_is_stored_relative_verified_and_download_is_hardened(client, login, make_user):
    _, _, h = make_user("supervisor")
    _mk_case(client, h, "CASE-UP-2")
    ev = client.post("/evidence/upload", files={"file": ("note.txt", b"chain of custody", "text/html")}, data={"case_number": "CASE-UP-2"}, headers=h).json()
    ev = ev[0] if isinstance(ev, list) else ev
    assert ev["integrity_status"] == "verified"
    from backend.app.db.database import SessionLocal
    from backend.app.models.models import Evidence
    from backend.app.services.app_service import BASE_DIR, _abs_path, _rel_path
    with SessionLocal() as db:
        assert _abs_path(db.query(Evidence).filter_by(evidence_id=ev["evidence_id"]).first().stored_path).exists()
    inside = BASE_DIR / "evidence_store" / "EV-X.txt"
    assert _rel_path(inside) == "evidence_store/EV-X.txt" and _abs_path("evidence_store/EV-X.txt") == inside
    r = client.get(f"/evidence/{ev['evidence_id']}/download", params={"case_number": "CASE-UP-2"}, headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/octet-stream")      # uploader-supplied text/html is never echoed
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"
    actions = [e["action"] for e in client.get("/audit", headers=login("admin")).json()["events"]]
    assert "evidence.downloaded" in actions                                      # chain of custody


def test_evidence_cannot_be_downloaded_through_another_case(client, make_user):
    _, _, a = make_user("supervisor"); _, _, b = make_user("supervisor")
    _mk_case(client, a, "CASE-UP-3"); _mk_case(client, b, "CASE-UP-4")
    ev = client.post("/evidence/upload", files={"file": ("n.txt", b"secret", "text/plain")}, data={"case_number": "CASE-UP-3"}, headers=a).json()
    ev = ev[0] if isinstance(ev, list) else ev
    assert client.get(f"/evidence/{ev['evidence_id']}/download", params={"case_number": "CASE-UP-4"}, headers=b).status_code == 404
    assert client.get(f"/evidence/{ev['evidence_id']}/download", params={"case_number": "CASE-UP-3"}, headers=b).status_code == 404


def test_tampered_evidence_file_is_detected(client, make_user):
    from backend.app.services.app_service import EVIDENCE_DIR
    _, _, h = make_user("supervisor")
    _mk_case(client, h, "CASE-UP-5")
    ev = client.post("/evidence/upload", files={"file": ("n.txt", b"original bytes", "text/plain")}, data={"case_number": "CASE-UP-5"}, headers=h).json()
    ev = ev[0] if isinstance(ev, list) else ev
    next(EVIDENCE_DIR.glob(ev["evidence_id"] + "*")).write_bytes(b"tampered")
    r = client.get(f"/evidence/{ev['evidence_id']}/verify", params={"case_number": "CASE-UP-5"}, headers=h).json()
    assert r["status"] == "hash_mismatch"


# ---------------------------------------------------------------- data integrity / correctness
def test_entities_and_relationships_are_validated_and_case_stamped(client, make_user):
    _, _, h = make_user("supervisor")
    _mk_case(client, h, "CASE-VAL-1")
    assert client.post("/entities", json={"external_id": "P-V1", "name": "A", "entity_type": "PERSON", "case_number": "CASE-VAL-1"}, headers=h).status_code == 200
    assert client.post("/entities", json={"external_id": "P-V2", "name": "B", "entity_type": "PERSON", "case_number": "CASE-VAL-1"}, headers=h).status_code == 200
    assert client.post("/relationships", json={"source_id": "P-V1", "target_id": "GHOST", "relation_type": "KNOWS", "case_number": "CASE-VAL-1"}, headers=h).status_code == 400
    assert client.post("/relationships", json={"source_id": "P-V1", "target_id": "P-V2", "relation_type": "KNOWS", "case_number": "CASE-VAL-1", "verification_state": "verified"}, headers=h).status_code == 200
    assert len(client.get("/relationships", params={"case_number": "CASE-VAL-1"}, headers=h).json()) == 1
    assert client.post("/entities", json={"external_id": "P-V3", "name": "C", "entity_type": "PERSON", "case_number": "NO-SUCH-CASE"}, headers=h).status_code == 404
    assert client.post("/events", json={"event_id": "E-V1", "event_type": "COMMUNICATION", "entity_id": "P-V1", "related_entity_id": "GHOST", "case_number": "CASE-VAL-1"}, headers=h).status_code == 400


def test_alert_status_is_validated(client, login):
    assert client.patch("/alerts/NOPE", json={"status": "Reviewing"}, headers=login("supervisor")).status_code == 404


def test_ingest_table_types_by_value_and_counts_real_creations(client, make_user):
    _, _, h = make_user("supervisor")
    _mk_case(client, h, "CASE-TBL-1")
    csv = b"source,target,relation,amount\n9876543210,Anil,CALLED,\nAnil,ACC12345678901,PAID,500\nAnil,9876543210,CALLED,\n"
    r = client.post("/ingest/table", files={"file": ("t.csv", csv, "text/csv")}, data={"case_number": "CASE-TBL-1"}, headers=h).json()
    assert r["created_entities"] == 3                     # 3 distinct entities, not one per row endpoint
    types = {e["external_id"]: e["type"] for e in client.get("/entities", params={"case_number": "CASE-TBL-1"}, headers=h).json()}
    assert types["9876543210"] == "PHONE" and types["Anil"] == "ENTITY" and types["ACC12345678901"] == "ACCOUNT"


def test_graph_is_per_thread_so_cases_cannot_bleed_between_requests():
    import threading
    from backend.app.main import svc
    graphs = []
    t = threading.Thread(target=lambda: graphs.append(svc.graph)); t.start(); t.join()
    assert graphs[0] is not svc.graph


def test_seed_reset_is_guarded():
    import subprocess, sys
    out = subprocess.run([sys.executable, "scripts/seed_demo.py", "--reset"], capture_output=True, text=True)
    assert out.returncode != 0 and "yes-delete-everything" in (out.stderr + out.stdout)


def test_docs_and_openapi_exposed_only_in_demo(client):
    assert client.get("/docs").status_code == 200  # demo mode in tests
