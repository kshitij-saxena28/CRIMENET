"""Per-case authorization.

* admin            -> every case (real and demo)
* demo             -> demo cases ONLY (Case.is_demo); never a real case
* everyone else    -> never a demo case
* auditor          -> every real case (oversight; the role's permissions keep content masked/limited)
* visibility=shared -> every authenticated user (role permissions still apply)
* visibility=restricted -> the creator and explicit members only
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.app.models.models import Case, CaseMember


def retired_demo_cases(db: Session) -> set[str]:
    """Case numbers of demonstration cases that were switched off and no longer exist.

    Audit / ledger views hide entries about these cases so that "demo data off" really shows nothing from the demo.
    The records themselves stay in the tamper-evident chains, which keeps verification intact.
    """
    import json
    from backend.app.models.models import AuditEvent
    gone: set[str] = set()
    for a in db.query(AuditEvent).filter(AuditEvent.action == "demo.disabled").all():
        try:
            gone |= {str(c) for c in (json.loads(a.details or "{}").get("cases") or [])}
        except (ValueError, TypeError, AttributeError):
            continue
    if not gone:
        return gone
    alive = {c for (c,) in db.query(Case.case_number).filter(Case.case_number.in_(list(gone))).all()}
    return gone - alive


def _clean(case_number) -> str:
    return (case_number or "").strip()


def is_demo_case(case: Case) -> bool:
    return bool(getattr(case, "is_demo", False))


def can_access(db: Session, user: dict, case: Case) -> bool:
    role = user.get("role")
    if role == "admin":
        return True
    if role == "demo":  # the demo account lives entirely inside demo data
        return is_demo_case(case)
    if is_demo_case(case):  # real users never see demo cases
        return False
    if role == "auditor" or (case.visibility or "restricted") == "shared":
        return True
    name = user.get("sub")
    if case.created_by == name:
        return True
    return db.query(CaseMember).filter_by(case_number=case.case_number, username=name).first() is not None


def authorize_case(db: Session, user: dict, case_number, *, required: bool = True) -> str | None:
    """Return the normalized case number or raise 400/403/404.

    ``required=False`` lets *admins* omit the case (global view). Everyone else must
    name a case they are allowed to see; there is no implicit "all cases" scope.
    """
    cn = _clean(case_number)
    if not cn:
        if not required and user.get("role") == "admin":
            return None
        raise HTTPException(400, "case_number is required")
    case = db.query(Case).filter_by(case_number=cn).first()
    # 404 (not 403) for cases the caller cannot see, so existence is not disclosed.
    if case is None or not can_access(db, user, case):
        raise HTTPException(404, "Case not found")
    return cn


def visible_cases(db: Session, user: dict) -> list[Case]:
    rows = db.query(Case).order_by(Case.id.desc()).all()
    if user.get("role") == "admin":
        return rows
    if user.get("role") == "demo":
        return [c for c in rows if is_demo_case(c)]
    rows = [c for c in rows if not is_demo_case(c)]
    if user.get("role") == "auditor":
        return rows
    member_of = {m.case_number for m in db.query(CaseMember).filter_by(username=user.get("sub")).all()}
    return [c for c in rows if (c.visibility or "restricted") == "shared" or c.created_by == user.get("sub") or c.case_number in member_of]
