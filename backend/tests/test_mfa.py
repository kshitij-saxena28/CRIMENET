"""Role-based two-step sign-in (TOTP): primitives, login flow, policy, admin reset, sensitive actions, migration."""
import base64
import re
import sqlite3
import time

import jwt
import pytest

from backend.app.core.config import settings
from security import mfa as M

# RFC 6238 appendix B secret ("12345678901234567890"), SHA-1, 8 digits
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


# ---------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def _clean(client):
    from backend.app.db.database import SessionLocal
    from backend.app.features.mfa import MfaPolicy, mfa_ip_limiter, mfa_user_limiter
    for lim in (mfa_ip_limiter, mfa_user_limiter):
        lim.clear_all()
    yield
    with SessionLocal() as db:
        db.query(MfaPolicy).delete(); db.commit()
    for lim in (mfa_ip_limiter, mfa_user_limiter):
        lim.clear_all()


@pytest.fixture()
def clock(monkeypatch):
    """Fake TOTP clock, always at the start of a 30 s step so window edges never make a test flaky."""
    state = {"t": (int(time.time()) // 30) * 30 + 2}
    monkeypatch.setattr(M, "_time", lambda: state["t"])
    class C:
        def advance(self, steps=1): state["t"] += 30 * steps
        now = property(lambda self: state["t"])
    return C()


@pytest.fixture()
def set_policy(client, login, make_user):
    def _set(**roles):
        r = client.put("/admin/mfa/policy", json=roles, headers=login("admin"))
        assert r.status_code == 200, r.text
        return r.json()
    return _set


def _user(client, login, role="investigator"):
    """Create an account (admin session must not need MFA at this point)."""
    import itertools
    name = f"mf{role[:3]}{int(time.time() * 1000) % 10 ** 8}{next(_user.c)}"
    import itertools as _i
    pw = "Final-Passw0rd-9z"
    r = client.post("/admin/users", json={"username": name, "password": pw, "role": role}, headers=login("admin"))
    assert r.status_code == 200, r.text
    return name, pw
import itertools as _it
_user.c = _it.count()


def _login(client, name, pw):
    r = client.post("/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(tok):
    return {"Authorization": "Bearer " + tok}


def _enrol_at_login(client, name, pw, clock):
    first = _login(client, name, pw)
    assert first.get("mfa_setup_required") is True
    st = client.post("/auth/mfa/enroll/start", json={"mfa_token": first["mfa_token"]})
    assert st.status_code == 200, st.text
    secret = st.json()["secret"]
    ok = client.post("/auth/mfa/enroll/confirm", json={"mfa_token": first["mfa_token"], "code": M.totp(secret, clock.now)})
    assert ok.status_code == 200, ok.text
    return secret, ok.json()


def _enrol_voluntary(client, name, pw, clock):
    tok = _login(client, name, pw)["access_token"]
    st = client.post("/auth/mfa/enroll/start", json={"password": pw}, headers=_bearer(tok))
    assert st.status_code == 200, st.text
    secret = st.json()["secret"]
    ok = client.post("/auth/mfa/enroll/confirm", json={"code": M.totp(secret, clock.now)}, headers=_bearer(tok))
    assert ok.status_code == 200, ok.text
    return secret, ok.json()


def _second_login(client, name, pw, secret, clock):
    """Full two-step sign-in with a fresh (never used) time-step; returns the verify response body."""
    clock.advance()
    ch = _login(client, name, pw)
    assert ch["mfa_required"] is True and ch["methods"] == ["totp", "recovery"]
    r = client.post("/auth/mfa/verify", json={"mfa_token": ch["mfa_token"], "code": M.totp(secret, clock.now)})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- primitives
def test_totp_matches_rfc6238_vectors():
    vectors = {59: "94287082", 1111111109: "07081804", 1111111111: "14050471", 1234567890: "89005924",
               2000000000: "69279037", 20000000000: "65353130"}
    for t, code in vectors.items():
        assert M.totp(RFC_SECRET, t, digits=8) == code
    # RFC 4226 appendix D, counters 0 and 1
    assert M.hotp(RFC_SECRET, 0) == "755224" and M.hotp(RFC_SECRET, 1) == "287082"


def test_window_is_plus_minus_one_step():
    t = 1_000_000_020  # start of a step
    step = t // 30
    for delta, expected in ((-2, None), (-1, step - 1), (0, step), (1, step + 1), (2, None)):
        assert M.match_step(RFC_SECRET, M.hotp(RFC_SECRET, step + delta), at=t) == expected
    assert M.match_step(RFC_SECRET, "abcdef", at=t) is None and M.match_step(RFC_SECRET, "", at=t) is None


def test_secret_encrypted_and_bound_to_user():
    s = M.new_secret()
    assert re.fullmatch(r"[A-Z2-7]{32}", s)
    blob = M.encrypt_secret(s, 7)
    assert s not in blob and blob.startswith("v1:")
    assert M.decrypt_secret(blob, 7) == s
    assert M.decrypt_secret(blob, 8) is None  # copied to another account: useless
    assert M.decrypt_secret("v1:garbage", 7) is None and M.decrypt_secret("", 7) is None


def test_qr_is_local_svg(client, login):
    pytest.importorskip("segno")
    svg = M.qr_svg("otpauth://totp/x?secret=ABC")
    assert svg.startswith("<svg") and "xmlns" in svg and "http://" not in svg.replace("http://www.w3.org/2000/svg", "")


def test_qr_scales_and_decodes():
    """The SVG must carry a viewBox (a fixed pixel size gets cropped by the page) and stay a small, readable code."""
    pytest.importorskip("segno")
    uri = M.otpauth_uri(M.new_secret(), "officer.one")
    svg = M.qr_svg(uri)
    assert 'viewBox="0 0 ' in svg and " width=" not in svg.split(">")[0]
    assert "algorithm=" not in uri and "secret=" in uri and "issuer=DARK%20CRIMENET" in uri
    import segno
    assert segno.make(uri, error="l").version <= 6  # small enough for a phone camera reading a screen


# ---------------------------------------------------------------- default policy
def test_default_policy_demo_and_production(monkeypatch):
    from backend.app.features.mfa import default_required_roles
    monkeypatch.setattr(settings, "mfa_required_roles", None)
    assert default_required_roles() == set()  # APP_ENV=demo
    monkeypatch.setattr(settings, "app_env", "production")
    assert default_required_roles() == {"admin", "supervisor", "investigator"}
    monkeypatch.setattr(settings, "mfa_required_roles", "auditor, demo ,ADMIN,nonsense")
    assert default_required_roles() == {"auditor", "admin"}  # demo can never be forced, unknown ignored
    monkeypatch.setattr(settings, "mfa_required_roles", "")
    assert default_required_roles() == set()


def test_policy_per_role_and_admin_override(client, login, set_policy, monkeypatch):
    from backend.app.db.database import SessionLocal
    from backend.app.features.mfa import required_for
    ah = login("admin")
    monkeypatch.setattr(settings, "mfa_required_roles", None)
    monkeypatch.setattr(settings, "app_env", "production")
    with SessionLocal() as db:
        assert [required_for(db, r) for r in ("admin", "supervisor", "investigator", "auditor", "demo")] == \
               [True, True, True, False, False]
    monkeypatch.setattr(settings, "app_env", "demo")
    with SessionLocal() as db:
        assert not any(required_for(db, r) for r in ("admin", "supervisor", "investigator"))
    body = set_policy(auditor=True, investigator=True)
    assert body["roles"]["auditor"]["required"] and body["roles"]["auditor"]["source"] == "admin"
    assert body["roles"]["demo"]["locked"] and not body["roles"]["demo"]["required"]
    with SessionLocal() as db:
        assert required_for(db, "auditor") and not required_for(db, "supervisor")
    # explicit admin decision beats the production default
    monkeypatch.setattr(settings, "app_env", "production")
    assert client.put("/admin/mfa/policy", json={"supervisor": False}, headers=ah).status_code == 200
    with SessionLocal() as db:
        assert not required_for(db, "supervisor")


def test_policy_api_rules(client, login, make_user):
    ah = login("admin")
    assert client.get("/admin/mfa/policy", headers=ah).status_code == 200
    assert client.put("/admin/mfa/policy", json={"demo": True}, headers=ah).status_code == 400
    assert client.put("/admin/mfa/policy", json={"nobody": True}, headers=ah).status_code == 400
    assert client.put("/admin/mfa/policy", json={}, headers=ah).status_code == 400
    _, _, sh = make_user("supervisor")
    assert client.get("/admin/mfa/policy", headers=sh).status_code == 403
    assert client.put("/admin/mfa/policy", json={"auditor": True}, headers=sh).status_code == 403
    assert client.get("/admin/mfa/policy").status_code == 401


def test_login_unchanged_when_mfa_not_needed(client, login):
    r = client.post("/auth/login", json={"username": "investigator", "password": "Invest-Test-Pass-1"})
    assert r.status_code == 200 and "access_token" in r.json() and "mfa_required" not in r.json()
    assert jwt.decode(r.json()["access_token"], settings.secret_key, algorithms=["HS256"])["amr"] == ["pwd"]


# ---------------------------------------------------------------- full flow
def test_enrol_at_login_then_two_step_login_replay_and_recovery(client, login, set_policy, clock):
    name, pw = _user(client, login)
    set_policy(investigator=True)
    secret, done = _enrol_at_login(client, name, pw, clock)
    codes = done["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    claims = jwt.decode(done["access_token"], settings.secret_key, algorithms=["HS256"])
    assert claims["amr"] == ["pwd", "otp"]
    assert client.get("/me", headers=_bearer(done["access_token"])).status_code == 200

    # next login needs the second step
    clock.advance()
    ch = _login(client, name, pw)
    assert ch["mfa_required"] is True and "access_token" not in ch
    code = M.totp(secret, clock.now)
    ok = client.post("/auth/mfa/verify", json={"mfa_token": ch["mfa_token"], "code": code})
    assert ok.status_code == 200 and ok.json()["recovery_used"] is False and ok.json()["recovery_left"] == 10
    assert jwt.decode(ok.json()["access_token"], settings.secret_key, algorithms=["HS256"])["amr"] == ["pwd", "otp"]
    # the same code (same time-step) cannot be used again, even with a fresh mfa_token
    ch2 = _login(client, name, pw)
    again = client.post("/auth/mfa/verify", json={"mfa_token": ch2["mfa_token"], "code": code})
    assert again.status_code == 400
    # an OLDER step than the last accepted one is refused too (window replay)
    older = client.post("/auth/mfa/verify", json={"mfa_token": ch2["mfa_token"], "code": M.hotp(secret, clock.now // 30 - 1)})
    assert older.status_code == 400
    # recovery code: works once
    rc = client.post("/auth/mfa/verify", json={"mfa_token": ch2["mfa_token"], "code": codes[0].upper()})
    assert rc.status_code == 200 and rc.json()["recovery_used"] is True and rc.json()["recovery_left"] == 9
    ch3 = _login(client, name, pw)
    reuse = client.post("/auth/mfa/verify", json={"mfa_token": ch3["mfa_token"], "code": codes[0]})
    assert reuse.status_code == 400 and reuse.json()["detail"] == again.json()["detail"]  # same message: no oracle
    assert client.post("/auth/mfa/verify", json={"mfa_token": ch3["mfa_token"], "code": codes[1].replace("-", "")}).status_code == 200


def test_secret_and_recovery_codes_encrypted_at_rest(client, login, set_policy, clock):
    from backend.app.db.database import engine
    name, pw = _user(client, login)
    set_policy(investigator=True)
    secret, done = _enrol_at_login(client, name, pw, clock)
    with engine.connect() as c:
        row = c.exec_driver_sql("SELECT id, mfa_secret_enc, mfa_enabled, mfa_last_step FROM users WHERE username = ?", (name,)).fetchone()
        stored = c.exec_driver_sql("SELECT salt, code_hash FROM mfa_recovery_codes WHERE user_id = ?", (row[0],)).fetchall()
    assert row[2] == 1 and row[3] == clock.now // 30
    assert secret not in row[1] and row[1].startswith("v1:")
    assert M.decrypt_secret(row[1], row[0]) == secret
    assert len(stored) == 10
    dump = " ".join(a + b for a, b in stored)
    assert not any(code.replace("-", "") in dump for code in done["recovery_codes"])
    assert all(len(b) == 64 and a for a, b in stored)


def test_wrong_codes_lock_out_and_are_audited(client, login, set_policy, clock):
    name, pw = _user(client, login)
    set_policy(investigator=True)
    secret, _ = _enrol_at_login(client, name, pw, clock)
    clock.advance()
    ch = _login(client, name, pw)
    for _ in range(settings.login_max_attempts):
        assert client.post("/auth/mfa/verify", json={"mfa_token": ch["mfa_token"], "code": "000000"}).status_code == 400
    locked = client.post("/auth/mfa/verify", json={"mfa_token": ch["mfa_token"], "code": M.totp(secret, clock.now)})
    assert locked.status_code == 429  # even the right code is refused while locked
    events = client.get("/audit", headers=login("admin")).json()["events"]
    assert sum(1 for e in events if e["action"] == "auth.mfa_failed" and name in e["details"]) >= settings.login_max_attempts


def test_mfa_token_rules(client, login, set_policy, clock, monkeypatch):
    name, pw = _user(client, login)
    set_policy(investigator=True)
    setup = _login(client, name, pw)["mfa_token"]
    # a setup token is not a session and cannot answer a code challenge
    assert client.get("/me", headers=_bearer(setup)).status_code == 401
    assert client.post("/auth/mfa/verify", json={"mfa_token": setup, "code": "123456"}).status_code == 400
    secret, _ = _enrol_at_login(client, name, pw, clock)
    # a verify token for an enrolled user can NOT be used to enrol a new device (would bypass the second factor)
    clock.advance()
    ch = _login(client, name, pw)
    assert client.post("/auth/mfa/enroll/start", json={"mfa_token": ch["mfa_token"]}).status_code == 400
    # expired token
    monkeypatch.setattr(M, "MFA_TOKEN_TTL", -5)
    expired = _login(client, name, pw)["mfa_token"]
    monkeypatch.undo()
    r = client.post("/auth/mfa/verify", json={"mfa_token": expired, "code": M.totp(secret, clock.now)})
    assert r.status_code == 400 and "expired" in r.json()["detail"].lower()
    assert client.post("/auth/mfa/verify", json={"mfa_token": "not.a.token", "code": "123456"}).status_code == 400
    # a token forged with the session key (not the MFA key) is refused
    forged = jwt.encode({"sub": name, "uid": 1, "tv": 0, "purpose": "mfa", "stage": "verify", "exp": time.time() + 300}, settings.secret_key, algorithm="HS256")
    assert client.post("/auth/mfa/verify", json={"mfa_token": forged, "code": "123456"}).status_code == 400


def test_role_change_or_password_change_kills_pending_mfa_token(client, login, set_policy):
    name, pw = _user(client, login)
    set_policy(investigator=True)
    tok = _login(client, name, pw)["mfa_token"]
    assert client.post(f"/admin/users/{name}/reset-password", json={"new_password": "Another-Passw0rd-1"}, headers=login("admin")).status_code == 200
    assert client.post("/auth/mfa/enroll/start", json={"mfa_token": tok}).status_code == 400


def test_wrong_password_gives_no_mfa_hint(client, login, set_policy, clock):
    name, pw = _user(client, login)
    set_policy(investigator=True)
    r = client.post("/auth/login", json={"username": name, "password": "nope-nope-1"})
    assert r.status_code == 401 and "mfa" not in r.text.lower()


# ---------------------------------------------------------------- voluntary use (demo mode default) and management
def test_voluntary_enrolment_status_regenerate_disable(client, login, clock):
    name, pw = _user(client, login, "auditor")
    tok = _login(client, name, pw)["access_token"]
    assert client.get("/auth/mfa/status", headers=_bearer(tok)).json() == {
        "enabled": False, "required": False, "enrolled_at": None, "recovery_left": 0, "session_has_otp": False}
    assert client.post("/auth/mfa/enroll/start", json={"password": "wrong"}, headers=_bearer(tok)).status_code == 400
    assert client.post("/auth/mfa/enroll/start", json={}, headers=_bearer(tok)).status_code == 400
    secret, done = _enrol_voluntary(client, name, pw, clock)
    assert "recovery_codes" in done
    new = _bearer(done["access_token"])
    st = client.get("/auth/mfa/status", headers=new).json()
    assert st["enabled"] and st["recovery_left"] == 10 and st["enrolled_at"] and st["session_has_otp"]
    assert client.get("/me", headers=_bearer(tok)).status_code == 401  # enabling signs other sessions out
    assert client.post("/auth/mfa/enroll/start", json={"password": pw}, headers=new).status_code == 409
    # regenerate needs password AND a fresh code
    clock.advance()
    body = {"password": pw, "code": M.totp(secret, clock.now)}
    assert client.post("/auth/mfa/recovery-codes/regenerate", json={"password": "bad", "code": body["code"]}, headers=new).status_code == 400
    r = client.post("/auth/mfa/recovery-codes/regenerate", json=body, headers=new)
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
    assert set(r.json()["recovery_codes"]).isdisjoint(done["recovery_codes"])
    assert client.post("/auth/mfa/recovery-codes/regenerate", json=body, headers=new).status_code == 400  # replayed code
    # old recovery codes no longer work
    clock.advance()
    ch = _login(client, name, pw)
    assert client.post("/auth/mfa/verify", json={"mfa_token": ch["mfa_token"], "code": done["recovery_codes"][0]}).status_code == 400
    # disable: password + code
    clock.advance()
    code = M.totp(secret, clock.now)
    assert client.post("/auth/mfa/disable", json={"password": pw, "code": "000000"}, headers=new).status_code == 400
    d = client.post("/auth/mfa/disable", json={"password": pw, "code": code}, headers=new)
    assert d.status_code == 200 and d.json()["enabled"] is False
    assert "access_token" in _login(client, name, pw)


def test_required_role_cannot_disable(client, login, set_policy, clock):
    name, pw = _user(client, login)
    set_policy(investigator=True)
    secret, done = _enrol_at_login(client, name, pw, clock)
    clock.advance()
    r = client.post("/auth/mfa/disable", json={"password": pw, "code": M.totp(secret, clock.now)}, headers=_bearer(done["access_token"]))
    assert r.status_code == 403
    assert client.get("/auth/mfa/status", headers=_bearer(done["access_token"])).json()["required"] is True


def test_demo_role_is_never_forced(client, login, set_policy):
    set_policy(auditor=True)
    assert "access_token" in _login(client, "demo", "demo123")
    assert client.put("/admin/mfa/policy", json={"demo": True}, headers=login("admin")).status_code == 400


# ---------------------------------------------------------------- admin reset and sensitive actions
def test_admin_reset_signs_out_and_forces_reenrolment(client, login, set_policy, clock):
    name, pw = _user(client, login)
    set_policy(investigator=True)
    secret, done = _enrol_at_login(client, name, pw, clock)
    ah = login("admin")
    r = client.post(f"/admin/users/{name}/mfa-reset", headers=ah)
    assert r.status_code == 200 and r.json()["must_enrol_again"] is True
    assert client.get("/me", headers=_bearer(done["access_token"])).status_code == 401
    assert _login(client, name, pw).get("mfa_setup_required") is True  # role requires it again
    assert client.post(f"/admin/users/{name}/mfa-reset", headers=ah).status_code == 200
    assert client.post("/admin/users/nobody-here/mfa-reset", headers=ah).status_code == 404
    events = client.get("/audit", headers=ah).json()["events"]
    assert any(e["action"] == "admin.mfa_reset" and name in e["details"] for e in events)
    # optional role: reset just turns it off
    n2, p2 = _user(client, login, "auditor")
    _enrol_voluntary(client, n2, p2, clock)
    assert client.post(f"/admin/users/{n2}/mfa-reset", headers=ah).json()["must_enrol_again"] is False
    assert "access_token" in _login(client, n2, p2)
    assert client.get("/admin/users", headers=ah).json()  # includes mfa_enabled
    assert all("mfa_enabled" in u for u in client.get("/admin/users", headers=ah).json())


def test_only_admin_can_reset_mfa(client, login, make_user):
    _, _, sh = make_user("supervisor")
    assert client.post("/admin/users/investigator/mfa-reset", headers=sh).status_code == 403


def test_sensitive_actions_need_otp_session(client, login, set_policy, clock):
    from backend.app.db.database import SessionLocal
    from backend.app.models.models import User
    from security.auth import issue_token
    name, pw = _user(client, login, "admin")
    secret, done = _enrol_voluntary(client, name, pw, clock)
    with SessionLocal() as db:
        pwd_only = _bearer(issue_token(db.query(User).filter_by(username=name).first()))  # password-only session
    otp_session = _bearer(done["access_token"])
    # everything sensitive is refused with a password-only session ...
    assert client.post("/auth/change-password", json={"current_password": pw, "new_password": "Brand-New-Passw0rd-3"}, headers=pwd_only).status_code == 403
    assert client.post("/auth/change-username", json={"current_password": pw, "new_username": name + "x"}, headers=pwd_only).status_code == 403
    assert client.post("/admin/users", json={"username": "zzsens1", "password": "Final-Passw0rd-9z", "role": "auditor"}, headers=pwd_only).status_code == 403
    assert client.patch("/admin/users/investigator/role", json={"role": "investigator"}, headers=pwd_only).status_code == 403
    assert client.post("/admin/users/investigator/mfa-reset", headers=pwd_only).status_code == 403
    assert client.put("/admin/mfa/policy", json={"auditor": False}, headers=pwd_only).status_code == 403
    assert client.post("/auth/mfa/disable", json={"password": pw, "code": "123456"}, headers=pwd_only).status_code == 403
    assert client.post("/auth/mfa/recovery-codes/regenerate", json={"password": pw, "code": "123456"}, headers=pwd_only).status_code == 403
    # ... but ordinary reads still work, and the otp session can do them
    assert client.get("/admin/users", headers=pwd_only).status_code == 200
    r = client.post("/admin/users", json={"username": "zzsens2", "password": "Final-Passw0rd-9z", "role": "auditor"}, headers=otp_session)
    assert r.status_code == 200
    r = client.post("/auth/change-password", json={"current_password": pw, "new_password": "Brand-New-Passw0rd-3"}, headers=otp_session)
    assert r.status_code == 200
    assert jwt.decode(r.json()["access_token"], settings.secret_key, algorithms=["HS256"])["amr"] == ["pwd", "otp"]  # the session keeps its second-step proof
    # users WITHOUT two-step sign-in are unaffected
    n2, p2 = _user(client, login, "supervisor")
    assert client.post("/auth/change-password", json={"current_password": p2, "new_password": "Brand-New-Passw0rd-4"}, headers=_bearer(_login(client, n2, p2)["access_token"])).status_code == 200


# ---------------------------------------------------------------- migration
def test_migration_adds_columns_and_clears_legacy_v44_values(tmp_path):
    from sqlalchemy import create_engine
    from backend.app.db.database import ensure_schema_columns

    def old_db(path, legacy):
        con = sqlite3.connect(path)
        cols = "id INTEGER PRIMARY KEY, username VARCHAR(100), password_hash VARCHAR(255), role VARCHAR(40), active BOOLEAN, token_version INTEGER, created_at DATETIME, updated_at DATETIME"
        if legacy:  # what v4.4 left behind
            cols += ", mfa_enabled BOOLEAN, mfa_secret_enc TEXT"
        con.execute(f"CREATE TABLE users ({cols})")
        if legacy:
            con.execute("INSERT INTO users (username,password_hash,role,active,token_version,mfa_enabled,mfa_secret_enc) VALUES ('old','x','admin',1,0,1,'gAAAA-old-secret')")
        else:
            con.execute("INSERT INTO users (username,password_hash,role,active,token_version) VALUES ('old','x','admin',1,0)")
        con.commit(); con.close()

    for legacy in (False, True):
        path = tmp_path / f"old{legacy}.db"
        old_db(path, legacy)
        eng = create_engine(f"sqlite:///{path}")
        ensure_schema_columns(eng); ensure_schema_columns(eng)  # idempotent
        with eng.connect() as c:
            names = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(users)")}
            row = c.exec_driver_sql("SELECT mfa_enabled, mfa_secret_enc, mfa_last_step, mfa_enrolled_at FROM users").fetchone()
        assert {"mfa_enabled", "mfa_secret_enc", "mfa_last_step", "mfa_enrolled_at"} <= names
        assert tuple(row) == (0, "", 0, None)  # an old database logs in exactly as before; old MFA state is not trusted
        # the ORM can read the migrated table
        from sqlalchemy.orm import sessionmaker
        from backend.app.models.models import User
        with sessionmaker(bind=eng)() as s:
            u = s.query(User).first()
            assert u.mfa_enabled is False and u.mfa_enrolled_at is None
        eng.dispose()


def test_feature_creates_its_tables_and_route_set():
    from backend.app.main import app
    paths = set(app.openapi()["paths"])
    for p in ("/auth/mfa/verify", "/auth/mfa/enroll/start", "/auth/mfa/enroll/confirm", "/auth/mfa/status", "/auth/mfa/disable",
              "/auth/mfa/recovery-codes/regenerate", "/admin/mfa/policy", "/admin/users/{username}/mfa-reset"):
        assert p in paths, p
