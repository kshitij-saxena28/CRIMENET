from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import declarative_base, sessionmaker
from backend.app.core.config import settings

connect_args={"check_same_thread":False} if settings.database_url.startswith("sqlite") else {}
engine=create_engine(settings.database_url,connect_args=connect_args)
SessionLocal=sessionmaker(bind=engine,autocommit=False,autoflush=False)
Base=declarative_base()

def ensure_schema_columns(eng=None):
    """Small SQLite-compatible migration for fields added after the demo DB was seeded."""
    eng = eng or engine
    insp = inspect(eng)
    # Databases from v4.4 still carry unused MFA columns from the removed implementation. Their values are not
    # trusted: they are cleared below, before the new two-step sign-in starts to use the same column names.
    legacy_mfa = False
    if "users" in insp.get_table_names():
        _cols = {c["name"] for c in insp.get_columns("users")}
        legacy_mfa = "mfa_enabled" in _cols and "mfa_last_step" not in _cols
    required = {
        "evidence": {
            "case_number": "VARCHAR(100)",
            "document_id": "VARCHAR(100)",
        },
        "alerts": {
            "case_number": "VARCHAR(100)",
        },
        # Cases that pre-date access control stay visible to everyone ("shared") so an
        # upgrade does not lock people out; new cases default to "restricted".
        "cases": {
            "created_by": "VARCHAR(100) DEFAULT ''",
            "visibility": "VARCHAR(20) DEFAULT 'shared'",
            "is_demo": "BOOLEAN DEFAULT 0",
        },
        "users": {
            "username": "VARCHAR(100)",
            "password_hash": "VARCHAR(255)",
            "role": "VARCHAR(40) DEFAULT 'investigator'",
            "active": "BOOLEAN DEFAULT 1",
            "token_version": "INTEGER DEFAULT 0",
            "created_at": "DATETIME",
            "updated_at": "DATETIME",
            "mfa_enabled": "BOOLEAN DEFAULT 0",
            "mfa_secret_enc": "TEXT DEFAULT ''",
            "mfa_last_step": "INTEGER DEFAULT 0",
            "mfa_enrolled_at": "DATETIME DEFAULT NULL",
        },
    }
    with eng.begin() as conn:
        for table, columns in required.items():
            if table not in insp.get_table_names():
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    suffix = "" if " DEFAULT " in sql_type.upper() else " DEFAULT ''"
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}{suffix}")
        if legacy_mfa:
            conn.exec_driver_sql("UPDATE users SET mfa_enabled = 0, mfa_secret_enc = '', mfa_last_step = 0, mfa_enrolled_at = NULL")
        # Flag the bundled synthetic seed cases of pre-existing databases as demo data (idempotent, never
        # touches anything the seed script did not create).
        if "cases" in insp.get_table_names():
            conn.exec_driver_sql("UPDATE cases SET is_demo = 1 WHERE (is_demo IS NULL OR is_demo = 0) "
                                 "AND created_by = 'seed_demo' AND title LIKE 'Synthetic Network Investigation%'")
            conn.exec_driver_sql("UPDATE cases SET is_demo = 0 WHERE is_demo IS NULL")

def get_db():
    db=SessionLocal()
    try:
        yield db
    finally:
        db.close()


def migrate_evidence_integrity_states():
    """Do not call unverified evidence verified on startup."""
    from backend.app.models.models import Evidence, AuditEvent
    import json
    db = SessionLocal()
    try:
        verified_by_audit = set()
        for row in db.query(AuditEvent).filter(AuditEvent.action == "evidence.verified").all():
            try: details = json.loads(row.details or "{}")
            except Exception: details = {}
            if details.get("evidence_id"): verified_by_audit.add(str(details["evidence_id"]))
        changed = 0
        for row in db.query(Evidence).filter(Evidence.integrity_status == "verified").all():
            if row.evidence_id not in verified_by_audit:
                row.integrity_status = "pending"; changed += 1
        if changed: db.commit()
    finally: db.close()
