"""Test isolation: every run uses a throw-away database and evidence folder, never the real ones."""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="dcn-tests-"))
os.environ.update({
    "APP_ENV": "demo",
    "DATABASE_URL": f"sqlite:///{_TMP / 'test.db'}",
    "EVIDENCE_DIR": str(_TMP / "evidence"),
    "SECRET_KEY": "test-secret-key-for-pytest-only-0123456789abcdef",
    "ADMIN_PASSWORD": "Admin-Test-Pass-1",
    "INVESTIGATOR_PASSWORD": "Invest-Test-Pass-1",
    "SUPERVISOR_PASSWORD": "Superv-Test-Pass-1",
    "MAX_UPLOAD_MB": "1",
    "SOCIAL_AUTOCOLLECT": "0",
})

import pytest  # noqa: E402

# demo and auditor have no *_PASSWORD override above, so they use the documented fixed demo passwords.
PASSWORDS = {"admin": "Admin-Test-Pass-1", "investigator": "Invest-Test-Pass-1",
             "supervisor": "Superv-Test-Pass-1", "demo": "demo123", "auditor": "audit123"}


@pytest.fixture(autouse=True)
def _reset_limiters():
    from security.auth import ip_limiter, user_limiter
    for lim in (ip_limiter, user_limiter):
        lim.clear_all()
    try:
        from backend.app.features.signup import signup_limiter, global_limiter
        signup_limiter.clear_all(); global_limiter.clear_all()
    except ImportError:
        pass
    yield


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    return TestClient(app)


@pytest.fixture()
def login(client):
    def _login(user, pw=None):
        body = {"username": user, "password": pw or PASSWORDS[user]}
        r = client.post("/auth/login", json=body)
        assert r.status_code == 200, r.text
        return {"Authorization": "Bearer " + r.json()["access_token"]}
    return _login


@pytest.fixture()
def make_user(client, login):
    """Create a ready-to-use account through the admin API; returns (name, password, headers)."""
    import itertools, time
    counter = itertools.count()

    def _make(role="investigator"):
        name = f"u{role[:3]}{int(time.time()*1000) % 10**7}{next(counter)}"
        pw = "Final-Passw0rd-9z"
        assert client.post("/admin/users", json={"username": name, "password": pw, "role": role}, headers=login("admin")).status_code == 200
        r = client.post("/auth/login", json={"username": name, "password": pw}); assert r.status_code == 200
        return name, pw, {"Authorization": "Bearer " + r.json()["access_token"]}
    return _make
