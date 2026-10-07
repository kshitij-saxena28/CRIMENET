"""Set or reset any account's password from the command line (also the way back in if every admin is locked out).

    python scripts/set_password.py admin            # prompts for the new password
    python scripts/set_password.py admin --create   # create the account (as admin) if it does not exist
    python scripts/set_password.py admin --mfa-only # lost phone, no recovery code, no other admin: reset two-step sign-in
    python scripts/set_password.py alice --create --role auditor   # roles: admin, supervisor, investigator, auditor, demo

The password policy depends on APP_ENV: demo = at least 6 characters; anything else = at least 10, letters and digits.

Runs on the machine that hosts the database, so it needs no login. Existing sessions of that user are revoked.
"""
import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.db.database import Base, SessionLocal, engine, ensure_schema_columns  # noqa: E402
from backend.app.models.models import User  # noqa: E402
from security.auth import _hash, bump_token_version  # noqa: E402
from security.passwords import validate_password  # noqa: E402
from security.rbac import ROLES  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def _clear_mfa(db, u) -> None:
    from sqlalchemy import text
    u.mfa_enabled = False; u.mfa_secret_enc = ""; u.mfa_last_step = 0; u.mfa_enrolled_at = None
    try:
        db.execute(text("DELETE FROM mfa_recovery_codes WHERE user_id = :i"), {"i": u.id})
    except Exception:  # table not created yet
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("username")
    ap.add_argument("--role", default="admin", choices=list(ROLES), help="role when using --create")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--password", help="non-interactive (visible in shell history; prefer the prompt)")
    ap.add_argument("--reset-mfa", action="store_true", help="also switch off two-step sign-in for the account (lost phone and no recovery code)")
    ap.add_argument("--mfa-only", action="store_true", help="only reset two-step sign-in; leave the password unchanged")
    args = ap.parse_args()
    Base.metadata.create_all(bind=engine); ensure_schema_columns()
    if args.mfa_only:
        with SessionLocal() as db:
            u = db.query(User).filter_by(username=args.username).first()
            if u is None:
                print(f"No such user '{args.username}'."); return 1
            _clear_mfa(db, u); bump_token_version(u); db.commit()
        print(f"Two-step sign-in reset for '{args.username}'. Sessions were signed out; enrol again at the next sign-in.")
        return 0
    pw = args.password or getpass.getpass("New password: ")
    if not args.password and pw != getpass.getpass("Repeat: "):
        print("Passwords do not match."); return 1
    try:
        validate_password(pw, args.username)
    except HTTPException as exc:
        print(exc.detail); return 1
    with SessionLocal() as db:
        u = db.query(User).filter_by(username=args.username).first()
        if u is None:
            if not args.create:
                print(f"No such user '{args.username}'. Use --create to add it."); return 1
            u = User(username=args.username, role=args.role, active=True, password_hash=_hash(pw)); db.add(u)
        else:
            u.password_hash = _hash(pw); u.active = True
        if args.reset_mfa:
            _clear_mfa(db, u)
        bump_token_version(u); db.commit()
    print(f"Password set for '{args.username}'. Existing sessions were signed out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
