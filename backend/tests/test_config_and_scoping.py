import json
import os
import subprocess
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.db.database import Base
from backend.app.models.models import Entity, Relationship
from backend.app.services.app_service import AppService


def _run(code, **env):
    e = {k: v for k, v in os.environ.items() if k not in {"SECRET_KEY", "APP_ENV", "DATABASE_URL"}}
    e.update(env)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=e)


def test_production_refuses_missing_or_weak_secret():
    for bad in ("", "short", "darkcrimenet-demo-secret-key-2026", "replace-with-a-long-random-secret-at-least-32-characters"):
        r = _run("import backend.app.core.config", APP_ENV="production", SECRET_KEY=bad)
        assert r.returncode != 0 and "SECRET_KEY" in r.stderr, bad


def test_production_hides_docs_and_bootstraps_only_admin(tmp_path):
    code = ("from fastapi.testclient import TestClient;from backend.app.main import app;c=TestClient(app);"
            "from backend.app.db.database import SessionLocal;from backend.app.models.models import User;"
            "print(c.get('/docs').status_code, c.get('/openapi.json').status_code, sorted(u.username for u in SessionLocal().query(User)))")
    r = _run(code, APP_ENV="production", SECRET_KEY="p" * 48, ADMIN_PASSWORD="Prod-Admin-Pass-1",
             DATABASE_URL=f"sqlite:///{tmp_path / 'p.db'}")
    assert r.stdout.strip().endswith("404 404 ['admin']"), r.stderr


def test_bootstrap_uses_simple_fixed_demo_passwords_when_none_supplied(monkeypatch, tmp_path):
    from security import auth
    from backend.app.models.models import User
    for k in ("ADMIN_PASSWORD", "INVESTIGATOR_PASSWORD", "SUPERVISOR_PASSWORD", "DEMO_PASSWORD", "AUDITOR_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(auth, "ROOT", tmp_path)
    eng = create_engine("sqlite:///:memory:"); Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as db:
        auth.bootstrap_users(db)
        rows = db.query(User).all()
        assert {u.username for u in rows} == {"admin", "demo", "investigator", "supervisor", "auditor"}
        by = {u.username: u for u in rows}
        assert auth._verify("admin123", by["admin"].password_hash) and auth._verify("demo123", by["demo"].password_hash)
    assert not (tmp_path / "initial_credentials.txt").exists()


def test_shared_entities_do_not_leak_relationships_between_cases():
    """Two nodes that merely list a case in `case_numbers` must not pull an unrelated case's edge in."""
    from backend.app.models.models import Case
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}); Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    for c in ("CASE-A", "CASE-B"):
        db.add(Case(case_number=c, title=c))
    j = json.dumps
    db.add_all([Entity(external_id="S1", name="s1", entity_type="PHONE", attributes=j({"case_number": "CASE-A", "case_numbers": ["CASE-A", "CASE-B"]})),
                Entity(external_id="S2", name="s2", entity_type="PHONE", attributes=j({"case_number": "CASE-A", "case_numbers": ["CASE-A", "CASE-B"]})),
                Relationship(source_id="S1", target_id="S2", relation_type="CALLED", verification_state="verified", metadata_json=j({}))])
    db.commit()
    svc = AppService()
    assert len(svc.relationships(db, "CASE-A")) == 1
    assert len(svc.relationships(db, "CASE-B")) == 0


def test_like_wildcards_in_case_numbers_do_not_widen_the_match():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}); Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    j = json.dumps
    db.add_all([Entity(external_id="X1", name="x1", entity_type="PERSON", attributes=j({"case_number": "CASE_1"})),
                Entity(external_id="X2", name="x2", entity_type="PERSON", attributes=j({"case_number": "CASE_1"})),
                Relationship(source_id="X1", target_id="X2", relation_type="KNOWS", verification_state="verified", metadata_json=j({"case_number": "CASE_1"}))])
    db.commit()
    svc = AppService()
    assert len(svc.relationships(db, "CASE_1")) == 1
    assert len(svc.relationships(db, "CASE%1")) == 0
    assert len(svc.relationships(db, "CASE-1")) == 0
