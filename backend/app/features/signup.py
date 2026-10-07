"""Sign-up REQUEST workflow (there is no self sign-up: nobody can create an account for themselves).

    pending_supervisor --supervisor approves--> supervisor_approved --admin creates user--> created
            |                                          |
            +--supervisor rejects--> supervisor_rejected   +--admin rejects--> rejected
            +--admin rejects (with reason)--> rejected

* POST /auth/signup-request                     public, rate limited, generic answer (no user enumeration)
* GET  /signup-requests                         supervisor (queue + own history) and admin (everything)
* POST /signup-requests/{id}/supervisor-decision  supervisor only (permission ``signup_review``); admin cannot bypass
* POST /signup-requests/{id}/create-user        admin only, only after supervisor approval
* POST /signup-requests/{id}/reject             admin only, any open state, reason required

The requested password is stored only as a PBKDF2 hash and is never returned or logged; the hash is wiped as soon as
the request is closed. Every state change is written to the tamper-evident audit log.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import Session

from backend.app.db.database import Base
from backend.app.models.models import User
from security.auth import _hash, current_user
from security.passwords import validate_password
from security.ratelimit import AttemptLimiter
from security.rbac import ROLES, allowed

log = logging.getLogger("dcn.signup")

REQUESTABLE_ROLES = ("investigator", "supervisor", "auditor")
OPEN_STATES = ("pending_supervisor", "supervisor_approved")
USERNAME_RE = re.compile(r"[A-Za-z0-9._-]{3,100}")
EMAIL_RE = re.compile(r"[^@\s]{1,120}@[^@\s]{1,120}\.[^@\s]{2,20}")
GENERIC_ANSWER = ("Your request has been received. A supervisor will review it and an administrator will create the account "
                  "if it is approved. You will be contacted by your unit; do not send your password to anyone.")
MAX_OPEN_REQUESTS = 500

# 5 requests per hour per address, plus a global brake so the queue cannot be flooded from many addresses.
signup_limiter = AttemptLimiter(5, 3600)
global_limiter = AttemptLimiter(60, 3600)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SignupRequest(Base):
    __tablename__ = "signup_requests"
    id = Column(Integer, primary_key=True)
    username = Column(String(100), index=True)
    full_name = Column(String(200), default="")
    email = Column(String(255), default="")
    designation = Column(String(200), default="")
    badge_no = Column(String(100), default="")
    requested_role = Column(String(40), default="investigator")
    reason = Column(Text, default="")
    password_hash = Column(String(255), default="")  # PBKDF2 hash only; cleared when the request is closed
    status = Column(String(30), default="pending_supervisor", index=True)
    created_at = Column(DateTime, default=_now)
    supervisor_by = Column(String(100), default="")
    supervisor_at = Column(DateTime, nullable=True)
    supervisor_note = Column(Text, default="")
    admin_by = Column(String(100), default="")
    admin_at = Column(DateTime, nullable=True)
    admin_note = Column(Text, default="")
    final_role = Column(String(40), default="")


class SignupIn(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    full_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=5, max_length=255)
    designation: str = Field(min_length=2, max_length=200)
    badge_no: str = Field(default="", max_length=100)
    requested_role: Literal["investigator", "supervisor", "auditor"]
    reason: str = Field(min_length=3, max_length=2000)
    password: str = Field(min_length=6, max_length=200)


class DecisionIn(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=1000)


class CreateUserIn(BaseModel):
    role: str | None = Field(default=None, max_length=40)


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


def _iso(d):
    return d.isoformat() if d else None


def _view(r: SignupRequest) -> dict:
    """Public shape of a request. The password hash is never included."""
    return {"id": r.id, "username": r.username, "full_name": r.full_name, "email": r.email, "designation": r.designation,
            "badge_no": r.badge_no, "requested_role": r.requested_role, "reason": r.reason, "status": r.status,
            "created_at": _iso(r.created_at), "supervisor_by": r.supervisor_by, "supervisor_at": _iso(r.supervisor_at),
            "supervisor_note": r.supervisor_note, "admin_by": r.admin_by, "admin_at": _iso(r.admin_at),
            "admin_note": r.admin_note, "final_role": r.final_role}


def build(ctx):
    router = APIRouter(tags=["signup"])
    get_db = ctx.get_db

    def notify(db: Session, users, kind, title, body):
        """Best effort in-app notification through the workflow feature (never blocks the request)."""
        try:
            from backend.app.features.workflow import notify as _notify
            for u in users:
                _notify(db, u, kind, title, body)
        except Exception:  # noqa: BLE001
            log.exception("signup notification failed")
            db.rollback()

    def usernames(db, role):
        return [u.username for u in db.query(User).filter_by(role=role, active=True).all()]

    def load(db, rid) -> SignupRequest:
        r = db.query(SignupRequest).filter_by(id=rid).first()
        if not r:
            raise HTTPException(404, "Request not found")
        return r

    # ------------------------------------------------------------ public
    @router.post("/auth/signup-request", status_code=202)
    def signup_request(x: SignupIn, request: Request, db: Session = Depends(get_db)):
        ip = request.client.host if request.client else "-"
        signup_limiter.check(ip); global_limiter.check("all")
        signup_limiter.fail(ip); global_limiter.fail("all")
        username = x.username.strip()
        if not USERNAME_RE.fullmatch(username):
            raise HTTPException(400, "Account ID may contain only letters, numbers, dot, underscore and hyphen (3-100 characters)")
        if not EMAIL_RE.fullmatch(x.email.strip()):
            raise HTTPException(400, "Please enter a valid email address")
        validate_password(x.password, username)
        pw_hash = _hash(x.password)  # always hash first so timing does not reveal whether the ID exists
        taken = db.query(User).filter(User.username == username).first() is not None
        pending = db.query(SignupRequest).filter(SignupRequest.username == username, SignupRequest.status.in_(OPEN_STATES)).first() is not None
        open_total = db.query(SignupRequest).filter(SignupRequest.status.in_(OPEN_STATES)).count()
        if taken or pending or open_total >= MAX_OPEN_REQUESTS:
            ctx.audit(db, "anonymous", "signup.request_ignored", {"username": username, "ip": ip})
            return {"status": "received", "message": GENERIC_ANSWER}
        row = SignupRequest(username=username, full_name=x.full_name.strip(), email=x.email.strip(), designation=x.designation.strip(),
                            badge_no=x.badge_no.strip(), requested_role=x.requested_role, reason=x.reason.strip(), password_hash=pw_hash)
        db.add(row); db.commit()
        ctx.audit(db, "anonymous", "signup.requested", {"request_id": row.id, "username": username, "requested_role": row.requested_role, "ip": ip})
        notify(db, usernames(db, "supervisor"), "signup_request", "New account request",
               f"{row.full_name} ({row.designation}) asked for a {row.requested_role} account. Open Account requests to review.")
        return {"status": "received", "message": GENERIC_ANSWER}

    # ------------------------------------------------------------ staff
    def viewer(user=Depends(current_user)):
        if not (allowed(user["role"], "signup_review") or allowed(user["role"], "manage_users")):
            raise HTTPException(403, "Insufficient permissions")
        return user

    @router.get("/signup-requests")
    def list_requests(db: Session = Depends(get_db), user=Depends(viewer)):
        q = db.query(SignupRequest).order_by(SignupRequest.id.desc())
        rows = q.limit(1000).all()
        if user["role"] != "admin":  # supervisors: what awaits them plus what they decided themselves
            rows = [r for r in rows if r.status == "pending_supervisor" or r.supervisor_by == user["sub"]]
        out = []
        for r in rows:
            d = _view(r)
            d["can_decide"] = bool(allowed(user["role"], "signup_review") and r.status == "pending_supervisor")
            d["can_create"] = bool(allowed(user["role"], "manage_users") and r.status == "supervisor_approved")
            d["can_reject"] = bool(allowed(user["role"], "manage_users") and r.status in OPEN_STATES)
            out.append(d)
        counts: dict = {}
        for r in rows:
            counts[r.status] = counts.get(r.status, 0) + 1
        return {"requests": out, "counts": counts, "requestable_roles": list(REQUESTABLE_ROLES)}

    @router.post("/signup-requests/{rid}/supervisor-decision")
    def supervisor_decision(rid: int, x: DecisionIn, db: Session = Depends(get_db), user=Depends(ctx.require_perm("signup_review"))):
        r = load(db, rid)
        if r.status != "pending_supervisor":
            raise HTTPException(409, f"This request is already {r.status.replace('_', ' ')}")
        r.status = "supervisor_approved" if x.approve else "supervisor_rejected"
        r.supervisor_by, r.supervisor_at, r.supervisor_note = user["sub"], _now(), x.note.strip()
        if not x.approve:
            r.password_hash = ""
        db.commit()
        ctx.audit(db, user["sub"], "signup.supervisor_approved" if x.approve else "signup.supervisor_rejected",
                  {"request_id": r.id, "username": r.username, "requested_role": r.requested_role})
        if x.approve:
            notify(db, usernames(db, "admin"), "signup_approved", "Account request approved by supervisor",
                   f"{r.username} ({r.requested_role}) was approved by {user['sub']}. An administrator can now create the account.")
        return _view(r)

    @router.post("/signup-requests/{rid}/create-user")
    def create_user(rid: int, x: CreateUserIn | None = None, db: Session = Depends(get_db), user=Depends(ctx.require_perm("manage_users"))):
        r = load(db, rid)
        if r.status != "supervisor_approved":
            raise HTTPException(409, "A supervisor must approve this request before an account can be created")
        role = ((x.role if x and x.role else None) or r.requested_role or "investigator").lower().strip()
        if role == "subofficer":  # retired role: requests filed before its removal become auditor requests
            role = "auditor"
        if role not in ROLES:
            raise HTTPException(400, "Role must be one of: " + ", ".join(ROLES))
        if not r.password_hash:
            raise HTTPException(409, "This request has no stored credential")
        if db.query(User).filter(User.username == r.username).first():
            raise HTTPException(409, "An account with this ID already exists; reject this request instead")
        db.add(User(username=r.username, password_hash=r.password_hash, role=role, active=True))
        r.status, r.final_role, r.admin_by, r.admin_at = "created", role, user["sub"], _now()
        r.password_hash = ""
        db.commit()
        ctx.audit(db, user["sub"], "signup.user_created", {"request_id": r.id, "username": r.username, "role": role, "requested_role": r.requested_role})
        return _view(r)

    @router.post("/signup-requests/{rid}/reject")
    def reject(rid: int, x: RejectIn, db: Session = Depends(get_db), user=Depends(ctx.require_perm("manage_users"))):
        r = load(db, rid)
        if r.status not in OPEN_STATES:
            raise HTTPException(409, f"This request is already {r.status.replace('_', ' ')}")
        r.status, r.admin_by, r.admin_at, r.admin_note = "rejected", user["sub"], _now(), x.reason.strip()
        r.password_hash = ""
        db.commit()
        ctx.audit(db, user["sub"], "signup.rejected", {"request_id": r.id, "username": r.username})
        return _view(r)

    return router
