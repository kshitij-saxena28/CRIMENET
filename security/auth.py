"""Authentication: PBKDF2 hashing, DB-backed sessions, brute-force limits."""
import hashlib
import hmac
import logging
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.config import ROOT, settings
from backend.app.db.database import get_db
from backend.app.models.models import User
from security.ratelimit import AttemptLimiter
from security.rbac import ROLES, allowed

log = logging.getLogger("dcn.auth")

ROLE_SET = set(ROLES)

# Fixed, documented passwords for the demonstration accounts (APP_ENV=demo ONLY; see DEMO_ACCOUNTS.md).
DEMO_PASSWORDS = {"admin": "admin123", "demo": "demo123", "investigator": "invest123",
                  "supervisor": "super123", "auditor": "audit123"}
PBKDF2_ROUNDS = 210_000
ip_limiter = AttemptLimiter(settings.login_max_attempts, settings.login_window_seconds)
user_limiter = AttemptLimiter(settings.login_user_max_attempts, settings.login_window_seconds)

_bootstrap_lock = threading.Lock()
_DUMMY_HASH = "pbkdf2_sha256$%d$%s$%s" % (PBKDF2_ROUNDS, "0" * 32, "0" * 64)


# ---------------------------------------------------------------- hashing
def _hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS).hex()
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt}${digest}"


def _verify(password, encoded):
    try:
        _, rounds, salt, digest = encoded.split("$", 3)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(actual, digest)
    except Exception:
        return False


# ---------------------------------------------------------------- bootstrap
def bootstrap_users(db: Session) -> None:
    """Create initial accounts ONLY when the users table is empty.

    * APP_ENV=demo: admin, demo, investigator, supervisor, and auditor with the fixed, documented
      demonstration passwords (DEMO_ACCOUNTS.md). ``<ROLE>_PASSWORD`` environment variables override them.
      Nothing is written to ``initial_credentials.txt`` for these well-known passwords.
    * Any other environment: only ``admin``, from ADMIN_PASSWORD or a random one-time password that is written once
      to ``initial_credentials.txt`` (mode 0600) and the server log.
    Accounts that an admin later renames or deletes are never silently re-created.
    """
    with _bootstrap_lock:
        # The retired "subofficer" role no longer exists; its closest equivalent is the masked read-only auditor role.
        legacy = db.query(User).filter(User.role == "subofficer").all()
        for u in legacy:
            u.role = "auditor"
            bump_token_version(u)
        if legacy:
            db.commit()
        if db.query(User).count() > 0:
            return
        roles = list(DEMO_PASSWORDS) if settings.is_demo else ["admin"]
        generated: list[tuple[str, str]] = []
        for name in roles:
            env_pw = os.getenv(f"{name.upper()}_PASSWORD", "").strip()
            if env_pw:
                pw = env_pw
            elif settings.is_demo:
                pw = DEMO_PASSWORDS[name]
            else:
                pw = secrets.token_urlsafe(12) + "9a"
                generated.append((name, pw))
            db.add(User(username=name, password_hash=_hash(pw), role=name, active=True))
        db.commit()
        if generated:
            path = ROOT / "initial_credentials.txt"
            try:
                path.write_text("Initial accounts (change them from the Account button, then delete this file):\n"
                                + "\n".join(f"{n}: {p}" for n, p in generated) + "\n")
                os.chmod(path, 0o600)
                log.warning("Initial credentials written to %s", path)
            except OSError:
                log.warning("Initial credentials (could not write file): %s", generated)
            print("\n=== DARK CRIMENET initial accounts (shown once) ===")
            for n, p in generated:
                print(f"  {n}: {p}")
            print("===================================================\n")


# ---------------------------------------------------------------- login / tokens
def issue_token(u: User, amr=("pwd",)) -> str:
    """Session token. ``amr`` lists the authentication methods used: "pwd", plus "otp" after a second factor."""
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": u.username, "role": u.role, "tv": int(u.token_version or 0), "amr": list(amr), "iat": now,
         "exp": now + timedelta(minutes=settings.access_token_expire_minutes), "jti": secrets.token_hex(8)},
        settings.secret_key, algorithm="HS256")


def bump_token_version(u: User) -> None:
    u.token_version = int(u.token_version or 0) + 1


def authenticate(db: Session, username: str, password: str, client_ip: str = "-"):
    """Return the User on success. Raises HTTPException (401/429). Failures are rate-limited."""
    username = (username or "").strip()
    ip_key, user_key = f"{client_ip}|{username.lower()}", username.lower()
    ip_limiter.check(ip_key)
    user_limiter.check(user_key)
    u = db.query(User).filter_by(username=username, active=True).first()
    # Always burn one PBKDF2 to keep timing uniform for unknown accounts.
    ok = _verify(password, u.password_hash if u else _DUMMY_HASH) and u is not None
    if not ok:
        ip_limiter.fail(ip_key); user_limiter.fail(user_key)
        raise HTTPException(401, "Invalid credentials")
    if u.role not in ROLE_SET:
        raise HTTPException(401, "Invalid credentials")
    ip_limiter.reset(ip_key); user_limiter.reset(user_key)
    return u


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    try:
        claims = jwt.decode(authorization[7:], settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    # Authoritative state lives in the database, never in the token.
    row = db.query(User).filter_by(username=claims.get("sub"), active=True).first()
    if row is None or int(claims.get("tv", -1)) != int(row.token_version or 0) or row.role not in ROLE_SET:
        raise HTTPException(401, "Session is no longer valid")
    amr = claims.get("amr")
    return {"sub": row.username, "role": row.role, "uid": row.id, "mfa": bool(row.mfa_enabled),
            "amr": list(amr) if isinstance(amr, list) else ["pwd"]}


def sensitive(user=Depends(current_user)):
    """Guard for sensitive actions: a user who has two-step sign-in on must have used it for THIS session."""
    if user.get("mfa") and "otp" not in user.get("amr", []):
        raise HTTPException(403, "This action needs a session signed in with your two-step code. Sign out and sign in again.")
    return user


def require(role=None):
    """Any authenticated user (role=None) or that role / admin. Prefer require_perm()."""
    def dep(user=Depends(current_user)):
        if role and user.get("role") not in {role, "admin"}:
            raise HTTPException(403, "Insufficient permissions")
        return user
    return dep


def require_perm(permission: str):
    def dep(user=Depends(current_user)):
        if not allowed(user.get("role"), permission):
            raise HTTPException(403, "Insufficient permissions")
        return user
    return dep


def require_any_perm(*permissions: str):
    """Allow a caller who holds at least one of the permissions."""
    def dep(user=Depends(current_user)):
        if not any(allowed(user.get("role"), p) for p in permissions):
            raise HTTPException(403, "Insufficient permissions")
        return user
    return dep
