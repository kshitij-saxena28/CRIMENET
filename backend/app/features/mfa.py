"""Role-based two-step sign-in (TOTP, RFC 6238), fully offline. Primitives live in security/mfa.py.

Login contract (POST /auth/login itself is in main.py and calls ``login_gate``):
  password ok + user has MFA          -> {"mfa_required": true, "mfa_token", "methods": ["totp", "recovery"]}
  password ok + role requires, no MFA -> {"mfa_setup_required": true, "mfa_token"}
  otherwise                           -> the normal {"access_token": ...}
Endpoints: /auth/mfa/verify, /auth/mfa/enroll/start, /auth/mfa/enroll/confirm, /auth/mfa/status, /auth/mfa/disable,
/auth/mfa/recovery-codes/regenerate, GET|PUT /admin/mfa/policy, POST /admin/users/{username}/mfa-reset.

Failures of the MFA endpoints are HTTP 400 (429 when locked out), never 401, so the SPA does not mistake a wrong code
for an expired session. A wrong code, a replayed code and an unknown recovery code all give the same message.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, String, func, update
from sqlalchemy.orm import Session

from backend.app.db.database import Base
from backend.app.core.config import settings
from backend.app.models.models import User
from security import mfa as M
from security.auth import _verify, bump_token_version, current_user, issue_token, sensitive
from security.ratelimit import AttemptLimiter
from security.rbac import ROLES, permissions_for

log = logging.getLogger("dcn.mfa")

DEFAULT_REQUIRED = ("admin", "supervisor", "investigator")
GENERIC_BAD_CODE = "That code is not valid. Check the code in your app (or a recovery code) and try again."
BAD_TOKEN = "Your sign-in step has expired. Enter your account ID and password again."

# 5 wrong codes per address+account, 20 per account, in the login window; same limits as the password step.
mfa_ip_limiter = AttemptLimiter(settings.login_max_attempts, settings.login_window_seconds)
mfa_user_limiter = AttemptLimiter(settings.login_user_max_attempts, settings.login_window_seconds)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MfaRecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    salt = Column(String(64), default="")
    code_hash = Column(String(128), default="")  # salted PBKDF2 only; the code itself is shown once and never stored
    created_at = Column(DateTime, default=_now)
    used_at = Column(DateTime, nullable=True)


class MfaPolicy(Base):
    __tablename__ = "mfa_policy"
    role = Column(String(40), primary_key=True)
    required = Column(Boolean, default=False)
    updated_by = Column(String(100), default="")
    updated_at = Column(DateTime, default=_now)


# ------------------------------------------------------------------ policy
def default_required_roles() -> set[str]:
    """Environment/default policy: MFA_REQUIRED_ROLES if set (empty = none), else none in demo, else DEFAULT_REQUIRED."""
    env = settings.mfa_required_roles
    if env is not None:
        names = {x.strip().lower() for x in env.split(",") if x.strip()}
    else:
        names = set() if settings.is_demo else set(DEFAULT_REQUIRED)
    return (names & set(ROLES)) - {"demo"}


def policy_table(db: Session) -> dict:
    """{role: {required, source, locked, updated_by, updated_at}} (admin decisions beat environment beats default)."""
    base = default_required_roles()
    src = "environment" if settings.mfa_required_roles is not None else "default"
    try:
        rows = {r.role: r for r in db.query(MfaPolicy).all()}
    except Exception:  # noqa: BLE001 - table missing must not break login
        log.exception("mfa_policy unreadable; using the environment/default policy")
        rows = {}
    out = {}
    for role in ROLES:
        r = rows.get(role)
        if role == "demo":
            out[role] = {"required": False, "source": "fixed", "locked": True, "updated_by": "", "updated_at": None}
        elif r is not None:
            out[role] = {"required": bool(r.required), "source": "admin", "locked": False, "updated_by": r.updated_by or "",
                         "updated_at": r.updated_at.isoformat() if r.updated_at else None}
        else:
            out[role] = {"required": role in base, "source": src, "locked": False, "updated_by": "", "updated_at": None}
    return out


def required_for(db: Session, role: str) -> bool:
    return bool(policy_table(db).get(role, {}).get("required"))


def login_gate(db: Session, u: User) -> dict | None:
    """Second step needed after a correct password? Returns the response for /auth/login, or None to sign in."""
    if u.mfa_enabled:
        return {"mfa_required": True, "mfa_token": M.issue_mfa_token(u.username, u.id, u.token_version, "verify"),
                "methods": ["totp", "recovery"]}
    if required_for(db, u.role):
        return {"mfa_setup_required": True, "mfa_token": M.issue_mfa_token(u.username, u.id, u.token_version, "setup")}
    return None


# ------------------------------------------------------------------ second-factor checking
def _pending_or_active_secret(u: User) -> str | None:
    return M.decrypt_secret(u.mfa_secret_enc or "", u.id)


def _recovery_left(db: Session, uid: int) -> int:
    return db.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id == uid, MfaRecoveryCode.used_at.is_(None)).count()


def _try_totp(db: Session, u: User, code: str) -> bool:
    secret = _pending_or_active_secret(u)
    step = M.match_step(secret, code) if secret else None
    if step is None:
        return False
    # Replay guard: the step must be newer than the last accepted one; the UPDATE makes this race-safe.
    n = db.execute(update(User).where(User.id == u.id, func.coalesce(User.mfa_last_step, 0) < step)
                   .values(mfa_last_step=step)).rowcount
    db.commit()
    return n == 1


def _try_recovery(db: Session, u: User, code: str) -> bool:
    rows = db.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id == u.id, MfaRecoveryCode.used_at.is_(None)).all()
    hit = None
    for r in rows:  # check every unused code so the time does not depend on which one matches
        if M.recovery_matches(code, r.salt, r.code_hash):
            hit = r
    if hit is None:
        return False
    n = db.execute(update(MfaRecoveryCode).where(MfaRecoveryCode.id == hit.id, MfaRecoveryCode.used_at.is_(None))
                   .values(used_at=_now())).rowcount
    db.commit()
    return n == 1


def check_second_factor(db: Session, u: User, code: str) -> str | None:
    """'totp', 'recovery' or None. Consumes the time-step / recovery code on success."""
    if not u.mfa_enabled:
        return None
    if M.looks_like_totp(code):
        return "totp" if _try_totp(db, u, code) else None
    return "recovery" if _try_recovery(db, u, code) else None


# ------------------------------------------------------------------ request bodies
class VerifyIn(BaseModel):
    mfa_token: str = Field(max_length=4000)
    code: str = Field(min_length=1, max_length=64)

class EnrollStartIn(BaseModel):
    mfa_token: str | None = Field(default=None, max_length=4000)
    password: str | None = Field(default=None, max_length=200)  # required when signed in (no mfa_token)

class EnrollConfirmIn(BaseModel):
    mfa_token: str | None = Field(default=None, max_length=4000)
    code: str = Field(min_length=1, max_length=64)

class PasswordCodeIn(BaseModel):
    password: str = Field(max_length=200)
    code: str = Field(min_length=1, max_length=64)


def build(ctx):
    router = APIRouter(tags=["mfa"])
    get_db = ctx.get_db

    def audit(db, actor, action, details):
        try:
            ctx.audit(db, actor, action, details)
        except Exception:  # noqa: BLE001
            log.exception("audit %s failed", action)

    def ip_of(request: Request) -> str:
        return request.client.host if request.client else "-"

    # ---- throttle helpers
    def throttle_check(username: str, ip: str):
        mfa_ip_limiter.check(f"{ip}|{username.lower()}")
        mfa_user_limiter.check(username.lower())

    def throttle_fail(username: str, ip: str):
        mfa_ip_limiter.fail(f"{ip}|{username.lower()}")
        mfa_user_limiter.fail(username.lower())

    def throttle_ok(username: str, ip: str):
        mfa_ip_limiter.reset(f"{ip}|{username.lower()}")
        mfa_user_limiter.reset(username.lower())

    def fail(db, u: User, ip: str, where: str, message: str = GENERIC_BAD_CODE):
        throttle_fail(u.username, ip)
        audit(db, u.username, "auth.mfa_failed", {"username": u.username, "ip": ip, "step": where})
        raise HTTPException(400, message)

    def login_payload(u: User, **extra) -> dict:
        return {"access_token": issue_token(u, amr=("pwd", "otp")), "token_type": "bearer", "username": u.username,
                "role": u.role, "permissions": permissions_for(u.role), **extra}

    def user_from_mfa_token(db: Session, token: str, stage: str) -> User:
        c = M.read_mfa_token(token, stage)
        u = db.query(User).filter_by(id=c.get("uid"), active=True).first() if c else None
        if u is None or u.username != c.get("sub") or int(c.get("tv", -1)) != int(u.token_version or 0) or u.role not in ROLES:
            raise HTTPException(400, BAD_TOKEN)
        return u

    def resolve_enroller(db: Session, mfa_token: str | None, authorization: str | None) -> tuple[User, bool]:
        """(user, began_at_login): an MFA setup token from the login step, or a normal Bearer session."""
        if mfa_token:
            return user_from_mfa_token(db, mfa_token, "setup"), True
        cu = current_user(authorization, db)  # raises 401 when there is no valid session either
        return db.query(User).filter_by(id=cu["uid"]).first(), False

    def issue_recovery(db: Session, u: User) -> list[str]:
        db.query(MfaRecoveryCode).filter_by(user_id=u.id).delete()
        codes = M.new_recovery_codes()
        for c in codes:
            salt, digest = M.hash_recovery(c)
            db.add(MfaRecoveryCode(user_id=u.id, salt=salt, code_hash=digest))
        return codes

    # ---- login step 2
    @router.post("/auth/mfa/verify")
    def mfa_verify(x: VerifyIn, request: Request, db: Session = Depends(get_db)):
        ip = ip_of(request)
        u = user_from_mfa_token(db, x.mfa_token, "verify")
        throttle_check(u.username, ip)
        left_before = _recovery_left(db, u.id)
        how = check_second_factor(db, u, x.code)
        if how is None:
            fail(db, u, ip, "verify")
        throttle_ok(u.username, ip)
        db.refresh(u)
        used = how == "recovery"
        audit(db, u.username, "auth.login", {"username": u.username, "role": u.role, "mfa": how})
        if used:
            audit(db, u.username, "auth.mfa_recovery_used", {"username": u.username, "left": left_before - 1})
        return login_payload(u, recovery_used=used, recovery_left=_recovery_left(db, u.id))

    # ---- enrolment
    @router.post("/auth/mfa/enroll/start")
    def enroll_start(x: EnrollStartIn, request: Request, authorization: str | None = Header(default=None),
                     db: Session = Depends(get_db)):
        ip = ip_of(request)
        u, at_login = resolve_enroller(db, x.mfa_token, authorization)
        if u.mfa_enabled:
            raise HTTPException(409, "Two-step sign-in is already on for this account.")
        if not at_login:
            throttle_check(u.username, ip)
            if not _verify(x.password or "", u.password_hash):
                fail(db, u, ip, "enroll_start", "Current password is incorrect")
        secret = M.new_secret()
        u.mfa_secret_enc = M.encrypt_secret(secret, u.id)  # pending until confirmed; replaces an earlier pending one
        u.mfa_enrolled_at = None
        db.commit()
        uri = M.otpauth_uri(secret, u.username)
        return {"secret": secret, "otpauth_uri": uri, "qr_svg": M.qr_svg(uri), "issuer": M.ISSUER, "account": u.username}

    @router.post("/auth/mfa/enroll/confirm")
    def enroll_confirm(x: EnrollConfirmIn, request: Request, authorization: str | None = Header(default=None),
                       db: Session = Depends(get_db)):
        ip = ip_of(request)
        u, at_login = resolve_enroller(db, x.mfa_token, authorization)
        if u.mfa_enabled:
            raise HTTPException(409, "Two-step sign-in is already on for this account.")
        throttle_check(u.username, ip)
        secret = _pending_or_active_secret(u)
        if not secret:
            raise HTTPException(400, "Start again: no set-up is waiting for a code.")
        step = M.match_step(secret, x.code) if M.looks_like_totp(x.code) else None
        if step is None:
            fail(db, u, ip, "enroll_confirm")
        n = db.execute(update(User).where(User.id == u.id, User.mfa_enabled.isnot(True))
                       .values(mfa_enabled=True, mfa_last_step=step, mfa_enrolled_at=_now())).rowcount
        if n != 1:
            db.rollback()
            raise HTTPException(409, "Two-step sign-in is already on for this account.")
        db.refresh(u)
        codes = issue_recovery(db, u)
        bump_token_version(u)  # other sessions were signed in without the second step
        db.commit()
        throttle_ok(u.username, ip)
        audit(db, u.username, "auth.mfa_enrolled", {"username": u.username, "role": u.role, "at_login": at_login})
        return login_payload(u, recovery_codes=codes, recovery_left=len(codes))

    # ---- signed-in management
    @router.get("/auth/mfa/status")
    def mfa_status(user=Depends(current_user), db: Session = Depends(get_db)):
        u = db.query(User).filter_by(id=user["uid"]).first()
        return {"enabled": bool(u.mfa_enabled), "required": required_for(db, u.role),
                "enrolled_at": u.mfa_enrolled_at.isoformat() if u.mfa_enrolled_at else None,
                "recovery_left": _recovery_left(db, u.id) if u.mfa_enabled else 0,
                "session_has_otp": "otp" in user.get("amr", [])}

    def password_and_code(db: Session, request: Request, user: dict, x: PasswordCodeIn) -> tuple[User, str]:
        ip = ip_of(request)
        u = db.query(User).filter_by(id=user["uid"]).first()
        if not u.mfa_enabled:
            raise HTTPException(400, "Two-step sign-in is not on for this account.")
        throttle_check(u.username, ip)
        if not _verify(x.password, u.password_hash):
            fail(db, u, ip, "password", "Current password is incorrect")
        how = check_second_factor(db, u, x.code)
        if how is None:
            fail(db, u, ip, "code")
        throttle_ok(u.username, ip)
        db.refresh(u)
        return u, how

    @router.post("/auth/mfa/disable")
    def mfa_disable(x: PasswordCodeIn, request: Request, user=Depends(sensitive), db: Session = Depends(get_db)):
        u0 = db.query(User).filter_by(id=user["uid"]).first()
        if required_for(db, u0.role):
            raise HTTPException(403, "Your role must use two-step sign-in, so it cannot be turned off. Ask an administrator if you have a problem.")
        u, _how = password_and_code(db, request, user, x)
        u.mfa_enabled = False; u.mfa_secret_enc = ""; u.mfa_last_step = 0; u.mfa_enrolled_at = None
        db.query(MfaRecoveryCode).filter_by(user_id=u.id).delete()
        bump_token_version(u); db.commit()
        audit(db, u.username, "auth.mfa_disabled", {"username": u.username})
        return {"status": "ok", "enabled": False, "access_token": issue_token(u), "token_type": "bearer"}

    @router.post("/auth/mfa/recovery-codes/regenerate")
    def mfa_regenerate(x: PasswordCodeIn, request: Request, user=Depends(sensitive), db: Session = Depends(get_db)):
        u, _how = password_and_code(db, request, user, x)
        codes = issue_recovery(db, u); db.commit()
        audit(db, u.username, "auth.mfa_recovery_regenerated", {"username": u.username})
        return {"recovery_codes": codes, "recovery_left": len(codes)}

    # ---- admin
    def policy_view(db: Session) -> dict:
        counts = {}
        for role, total, on in (db.query(User.role, func.count(User.id), func.sum(User.mfa_enabled.cast(Integer)))
                                .filter(User.active.is_(True)).group_by(User.role).all()):
            counts[role] = {"users": int(total or 0), "enrolled": int(on or 0)}
        table = policy_table(db)
        for role, v in table.items():
            v.update(counts.get(role, {"users": 0, "enrolled": 0}))
        return {"roles": table, "environment": settings.app_env,
                "note": "Turning a role on makes everyone in it set up two-step sign-in the next time they sign in."}

    @router.get("/admin/mfa/policy")
    def get_policy(user=Depends(ctx.require_perm("manage_users")), db: Session = Depends(get_db)):
        return policy_view(db)

    @router.put("/admin/mfa/policy")
    def put_policy(x: dict[str, bool], user=Depends(ctx.require_perm("manage_users")), _s=Depends(sensitive),
                   db: Session = Depends(get_db)):
        if not x:
            raise HTTPException(400, "Send at least one role, for example {\"supervisor\": true}.")
        for role in x:
            if role not in ROLES:
                raise HTTPException(400, "Unknown role: " + role[:40])
        if x.get("demo"):
            raise HTTPException(400, "The demo role can never be forced to use two-step sign-in.")
        before = {r: v["required"] for r, v in policy_table(db).items()}
        for role, req in x.items():
            if role == "demo":
                continue
            row = db.get(MfaPolicy, role)
            if row is None:
                row = MfaPolicy(role=role); db.add(row)
            row.required = bool(req); row.updated_by = user["sub"]; row.updated_at = _now()
        db.commit()
        changes = {r: bool(v) for r, v in x.items() if r != "demo" and before.get(r) != bool(v)}
        audit(db, user["sub"], "admin.mfa_policy_changed", {"changes": changes})
        return policy_view(db)

    @router.post("/admin/users/{username}/mfa-reset")
    def admin_mfa_reset(username: str, user=Depends(ctx.require_perm("manage_users")), _s=Depends(sensitive),
                        db: Session = Depends(get_db)):
        u = db.query(User).filter_by(username=username).first()
        if not u:
            raise HTTPException(404, "User not found")
        was_on = bool(u.mfa_enabled)
        u.mfa_enabled = False; u.mfa_secret_enc = ""; u.mfa_last_step = 0; u.mfa_enrolled_at = None
        db.query(MfaRecoveryCode).filter_by(user_id=u.id).delete()
        bump_token_version(u); db.commit()  # signs the user out everywhere
        must = required_for(db, u.role)
        audit(db, user["sub"], "admin.mfa_reset", {"username": username, "was_enabled": was_on, "must_enrol_again": must})
        return {"status": "ok", "username": username, "must_enrol_again": must}

    return router
