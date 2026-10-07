"""Operational workflow: approvals (maker-checker), statutory deadlines / SLA tracker, watchlists,
notification centre.

Everything here is a *coordination aid*: deadline templates are unit-configured reminders (not legal advice),
watchlist matches are investigative signals.
"""
from __future__ import annotations

import difflib
import hashlib
import logging
import re
import threading
import unicodedata
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Session

from backend.app.db.database import Base
from backend.app.models.models import Case, CaseMember, DocumentRecord, User
from security.access import can_access
from security.ratelimit import AttemptLimiter

log = logging.getLogger("dcn.workflow")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(d) -> str | None:
    return d.isoformat() if d else None


# ================================================================== models
class ApprovalRequest(Base):
    __tablename__ = "wf_approval_requests"
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    kind = Column(String(40), index=True)
    ref_id = Column(String(200), default="")
    title = Column(String(255), default="")
    note = Column(Text, default="")
    requested_by = Column(String(100), index=True)
    status = Column(String(20), default="pending", index=True)
    decided_by = Column(String(100), default="")
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, default="")
    created_at = Column(DateTime, default=_now)


class DeadlineTemplate(Base):
    __tablename__ = "wf_deadline_templates"
    id = Column(Integer, primary_key=True)
    key = Column(String(60), unique=True, index=True)
    label = Column(String(200))
    kind = Column(String(40), default="custom")
    days = Column(Integer, default=30)
    anchor = Column(String(120), default="start date")
    note = Column(Text, default="")
    active = Column(Boolean, default=True)


class CaseDeadline(Base):
    __tablename__ = "wf_case_deadlines"
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    label = Column(String(255))
    kind = Column(String(40), default="custom")
    start_date = Column(String(10), default="")
    due_date = Column(String(10), index=True)
    status = Column(String(20), default="open", index=True)
    done_at = Column(DateTime, nullable=True)
    note = Column(Text, default="")
    created_by = Column(String(100), default="")
    created_at = Column(DateTime, default=_now)


class Watchlist(Base):
    __tablename__ = "wf_watchlists"
    id = Column(Integer, primary_key=True)
    owner = Column(String(100), index=True)
    name = Column(String(150))
    scope = Column(String(10), default="private")
    case_number = Column(String(100), nullable=True, index=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_now)


class WatchlistItem(Base):
    __tablename__ = "wf_watchlist_items"
    id = Column(Integer, primary_key=True)
    watchlist_id = Column(Integer, index=True)
    kind = Column(String(20))
    value = Column(String(255))
    value_norm = Column(String(255), index=True)
    note = Column(String(500), default="")


class Notification(Base):
    __tablename__ = "wf_notifications"
    id = Column(Integer, primary_key=True)
    user = Column(String(100), index=True)
    kind = Column(String(40), default="info")
    title = Column(String(255), default="")
    body = Column(Text, default="")
    case_number = Column(String(100), nullable=True)
    ref_type = Column(String(40), default="")
    ref_id = Column(String(200), default="")
    read = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=_now)


class AccessRequest(Base):
    __tablename__ = "wf_access_requests"
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    requester = Column(String(100), index=True)
    reason = Column(Text, default="")
    status = Column(String(20), default="pending", index=True)
    decided_by = Column(String(100), default="")
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, default="")
    created_at = Column(DateTime, default=_now)


class HitExposure(Base):
    """Remembers which restricted cases a user was told about via a cross-case hit (basis for an access request)."""
    __tablename__ = "wf_hit_exposures"
    __table_args__ = (UniqueConstraint("username", "case_number", name="uq_wf_exposure"),)
    id = Column(Integer, primary_key=True)
    username = Column(String(100), index=True)
    case_number = Column(String(100), index=True)
    created_at = Column(DateTime, default=_now)


# ================================================================== notifications
def notify(db: Session, user: str, kind: str, title: str, body: str = "", case_number: str | None = None,
           ref: tuple | list | None = None, commit: bool = True) -> Notification:
    ref_type, ref_id = (ref[0], str(ref[1])) if ref else ("", "")
    n = Notification(user=user, kind=kind[:40], title=title[:255], body=body[:2000], case_number=case_number,
                     ref_type=ref_type[:40], ref_id=ref_id[:200])
    db.add(n)
    if commit:
        db.commit()
    return n


def _user_dict(db: Session, username: str) -> dict | None:
    u = db.query(User).filter_by(username=username).first()
    if not u or u.active is False:
        return None
    return {"sub": u.username, "role": u.role}


def _has_access(db: Session, username: str, case_number: str) -> bool:
    ud = _user_dict(db, username)
    case = db.query(Case).filter_by(case_number=case_number).first()
    return bool(ud and case and can_access(db, ud, case))


def _users_with_perm(db: Session, perm: str, case_number: str, exclude: str = "") -> list[str]:
    from security.rbac import allowed
    out = []
    for u in db.query(User).filter(User.active.is_(True)).all():
        if u.username != exclude and allowed(u.role, perm) and _has_access(db, u.username, case_number):
            out.append(u.username)
    return out


# ================================================================== normalisation
KINDS = ("phone", "vehicle", "account", "email", "name", "other")
_HONORIFICS = {"mr", "mrs", "ms", "shri", "smt", "sri", "dr", "sh", "kumari", "km", "alias", "urf", "s/o", "d/o", "w/o"}


def _ascii_fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).casefold()


def _name_tokens(s: str) -> list[str]:
    s = _ascii_fold(s or "")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return [t for t in s.split() if t not in _HONORIFICS]


def _norm_name(s: str) -> str:
    return " ".join(sorted(_name_tokens(s)))


def normalise(kind: str, value: str) -> str:
    v = str(value or "").strip()
    if kind == "phone":
        digits = re.sub(r"\D", "", v)
        return digits[-10:] if len(digits) >= 10 else digits
    if kind == "vehicle":
        return re.sub(r"[^A-Za-z0-9]", "", v).upper()
    if kind == "account":
        return re.sub(r"[\s\-]", "", v).upper()
    if kind == "email":
        return v.lower()
    if kind == "name":
        return _norm_name(v)
    return _ascii_fold(re.sub(r"\s+", " ", v)).strip()


def names_match(a: str, b: str, threshold: float = 0.88) -> tuple[bool, float]:
    """Light fuzzy match: token-order-insensitive, accent/case-insensitive, tolerant of small spelling variants
    (e.g. 'Mohammad' vs 'Mohamed'). Heuristic only; each side needs >=2 tokens or an exact normalised match."""
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False, 0.0
    na, nb = " ".join(sorted(ta)), " ".join(sorted(tb))
    if na == nb:
        return True, 1.0
    if len(ta) < 2 or len(tb) < 2:
        return False, 0.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    return ratio >= threshold, round(ratio, 2)


_ENTITY_KIND = {"PHONE": "phone", "VEHICLE": "vehicle", "ACCOUNT": "account", "EMAIL": "email", "PERSON": "name"}


def _valid_item(kind: str, value: str) -> str:
    n = normalise(kind, value)
    minimum = {"phone": 7, "vehicle": 4, "account": 5, "email": 5, "name": 3, "other": 3}[kind]
    if len(n) < minimum:
        raise HTTPException(400, f"That {kind} value is too short to watch reliably")
    if kind == "email" and "@" not in n:
        raise HTTPException(400, "Email must contain @")
    return n


# ================================================================== deadlines: pure helpers
AT_RISK_DAYS = 7
DEFAULT_TEMPLATES = [
    ("chargesheet_60", "Chargesheet - 60 days from arrest", "chargesheet", 60, "date of arrest",
     "Typical limit for offences not punishable with death/life/10+ years."),
    ("chargesheet_90", "Chargesheet - 90 days from arrest (grave offences)", "chargesheet", 90, "date of arrest",
     "Typical limit for grave offences."),
    ("fir_review", "Preliminary review of FIR", "review", 7, "FIR registration date",
     "Supervisory first review of the FIR."),
    ("fsl_followup", "Evidence to FSL - follow-up", "fsl", 30, "date evidence was sent to FSL",
     "Chase the forensic laboratory report."),
    ("court_hearing", "Court hearing", "court", 14, "date the hearing was notified",
     "Set the explicit due date to the hearing date where known."),
]
TEMPLATE_DISCLAIMER = ("Configured by the unit - not legal advice. Day counts are reminders the unit can change; "
                       "confirm statutory limits with the legal officer.")


def parse_date(value) -> date:
    s = str(value or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", s):
            return date.fromisoformat(s[:10])
        m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        pass
    raise HTTPException(400, f"Invalid date '{s[:20]}'. Use YYYY-MM-DD.")


def deadline_state(due: date, status: str, today: date | None = None) -> tuple[int, str]:
    """(days_left, state). state: overdue | at_risk (<=7 days) | on_track | done | waived."""
    today = today or _now().date()
    left = (due - today).days
    if status in ("done", "waived"):
        return left, status
    if left < 0:
        return left, "overdue"
    return left, "at_risk" if left <= AT_RISK_DAYS else "on_track"


def _deadline_dict(d: CaseDeadline, today: date | None = None) -> dict:
    try:
        left, state = deadline_state(date.fromisoformat(d.due_date), d.status, today)
    except ValueError:
        left, state = None, "unknown"
    return {"id": d.id, "case_number": d.case_number, "label": d.label, "kind": d.kind, "start_date": d.start_date,
            "due_date": d.due_date, "status": d.status, "state": state, "days_left": left,
            "done_at": _iso(d.done_at), "note": d.note, "created_by": d.created_by}


def _ics_escape(s: str) -> str:
    return (str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def _fold(line: str) -> str:
    out, cur, cur_len = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if cur_len + n > 74:
            out.append(cur); cur, cur_len = " " + ch, 1 + n
        else:
            cur += ch; cur_len += n
    out.append(cur)
    return "\r\n".join(out)


def build_ics(deadlines: list[dict]) -> str:
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//DARK CRIMENET//Workflow Deadlines//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Case deadlines"]
    for d in deadlines:
        due = date.fromisoformat(d["due_date"])
        lines += ["BEGIN:VEVENT", f"UID:dcn-deadline-{d['id']}@darkcrimenet.local", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{due.strftime('%Y%m%d')}",
                  f"DTEND;VALUE=DATE:{(due + timedelta(days=1)).strftime('%Y%m%d')}",
                  f"SUMMARY:{_ics_escape('[' + d['case_number'] + '] ' + d['label'])}",
                  f"DESCRIPTION:{_ics_escape('Reminder configured by the unit (not legal advice). ' + (d.get('note') or ''))}",
                  "TRANSP:TRANSPARENT", "BEGIN:VALARM", "ACTION:DISPLAY",
                  f"DESCRIPTION:{_ics_escape(d['label'])}", "TRIGGER:-P1D", "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


# ================================================================== watchlist scanning
def _entity_hits(item: WatchlistItem, name: str, etype: str) -> tuple[bool, str]:
    kind = _ENTITY_KIND.get(str(etype).upper(), "other")
    if item.kind == "other":
        return (normalise("other", name) == item.value_norm, "exact match on a free-text value") if item.value_norm else (False, "")
    if kind != item.kind:
        return False, ""
    if kind == "name":
        ok, score = names_match(name, item.value)
        return ok, f"name match (similarity {score:.2f}, heuristic)"
    return normalise(kind, name) == item.value_norm, f"normalised {kind} matches"


def _text_hits(item: WatchlistItem, text: str, cache: dict) -> bool:
    n = item.value_norm
    if item.kind == "phone":
        digits = cache.setdefault("digits", re.sub(r"\D", "", text))
        return len(n) >= 10 and n in digits
    if item.kind == "vehicle":
        return len(n) >= 6 and n in cache.setdefault("veh", re.sub(r"[^A-Za-z0-9]", "", text).upper())
    if item.kind == "account":
        return len(n) >= 8 and n in cache.setdefault("acc", re.sub(r"[\s\-]", "", text).upper())
    if item.kind == "email":
        return n in cache.setdefault("mail", text.lower())
    if item.kind == "name":
        toks = cache.setdefault("toks", set(_name_tokens(text)))
        want = n.split()
        return len(want) >= 2 and all(t in toks for t in want)
    return len(n) >= 5 and n in cache.setdefault("oth", _ascii_fold(text))


def collect_matches(ctx, db: Session, case_number: str, watchlists: list[Watchlist]) -> list[dict]:
    """Match watchlist items against the case's entities and document text/candidate entities."""
    by_wl = {w.id: w for w in watchlists}
    if not by_wl:
        return []
    items = db.query(WatchlistItem).filter(WatchlistItem.watchlist_id.in_(list(by_wl))).all()
    if not items:
        return []
    hits: list[dict] = []
    try:
        entities = ctx.svc.entities(db, case_number, role="admin")
    except Exception:  # noqa: BLE001
        log.exception("entity read failed"); entities = []
    for e in entities:
        for it in items:
            ok, why = _entity_hits(it, e.get("name") or "", e.get("type") or "")
            if ok:
                hits.append({"item": it, "watchlist": by_wl[it.watchlist_id], "source_type": "entity",
                             "source_key": f"E:{e.get('external_id')}", "matched": e.get("name"),
                             "entity_type": e.get("type"), "reason": f"Entity in the case graph: {why}"})
    docs = db.query(DocumentRecord).filter_by(case_number=case_number).order_by(DocumentRecord.id.desc()).limit(200).all()
    for d in docs:
        cache: dict = {}
        text = (d.text or "")[:200_000]
        try:
            import json as _json
            cands = _json.loads(d.entities_json or "[]")
        except Exception:  # noqa: BLE001
            cands = []
        for it in items:
            found = None
            for c in cands if isinstance(cands, list) else []:
                if not isinstance(c, dict):
                    continue
                ok, why = _entity_hits(it, str(c.get("name") or c.get("text") or ""), str(c.get("entity_type") or c.get("type") or ""))
                if ok:
                    found = (str(c.get("name") or c.get("text")), f"Candidate extracted from {d.filename} ({d.status}): {why}")
                    break
            if not found and text and _text_hits(it, text, cache):
                found = (it.value, f"Value appears in the text of {d.filename} ({d.status})")
            if found:
                hits.append({"item": it, "watchlist": by_wl[it.watchlist_id], "source_type": "document",
                             "source_key": f"D:{d.document_id}", "matched": found[0], "entity_type": it.kind.upper(),
                             "reason": found[1]})
    return hits


def deliver_watchlist_hits(db: Session, case_number: str, hits: list[dict]) -> int:
    """Create de-duplicated notifications for watchlist owners who can access the case."""
    created = 0
    for h in hits:
        w, it = h["watchlist"], h["item"]
        if not w.active or not _has_access(db, w.owner, case_number):
            continue
        ref_id = f"{it.id}:{h['source_key']}"
        if db.query(Notification).filter_by(user=w.owner, kind="watchlist_hit", case_number=case_number,
                                            ref_type="watchlist_item", ref_id=ref_id[:200]).first():
            continue
        notify(db, w.owner, "watchlist_hit", f"Watchlist hit: {w.name}",
               f"{it.kind} '{h['matched']}' matched your watchlist item '{it.value}' in {case_number}. {h['reason']}. "
               "This is an investigative signal to review, not a finding.", case_number, ("watchlist_item", ref_id), commit=False)
        created += 1
    if created:
        db.commit()
    return created


def _relevant_watchlists(db: Session, case_number: str) -> list[Watchlist]:
    return [w for w in db.query(Watchlist).filter(Watchlist.active.is_(True)).all()
            if w.case_number in (None, "", case_number)]


def approved(db: Session, case_number: str, kind: str, ref_id) -> bool:
    """True when a maker-checker request for (case, kind, ref) has been approved. Other modules gate actions on this."""
    return db.query(ApprovalRequest).filter_by(case_number=case_number, kind=kind, ref_id=str(ref_id or ""),
                                               status="approved").first() is not None


# ================================================================== schemas
APPROVAL_KINDS = ("document_verification", "report_release", "evidence_export", "case_closure", "legal_hold_release", "custom")


class ApprovalIn(BaseModel):
    case_number: str = Field(min_length=1, max_length=100)
    kind: str = Field(max_length=40)
    ref_id: str = Field(default="", max_length=200)
    title: str = Field(min_length=3, max_length=255)
    note: str = Field(default="", max_length=2000)


class DecideIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=2000)


class TemplateIn(BaseModel):
    label: str | None = Field(default=None, min_length=3, max_length=200)
    days: int | None = Field(default=None, ge=1, le=3650)
    active: bool | None = None
    note: str | None = Field(default=None, max_length=1000)


class TemplateCreateIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]{3,50}$")
    label: str = Field(min_length=3, max_length=200)
    kind: str = Field(default="custom", max_length=40)
    days: int = Field(ge=1, le=3650)
    anchor: str = Field(default="start date", max_length=120)
    note: str = Field(default="", max_length=1000)


class DeadlineIn(BaseModel):
    case_number: str = Field(min_length=1, max_length=100)
    template_key: str | None = Field(default=None, max_length=60)
    label: str | None = Field(default=None, max_length=255)
    start_date: str | None = Field(default=None, max_length=20)
    due_date: str | None = Field(default=None, max_length=20)
    note: str = Field(default="", max_length=1000)


class DeadlinePatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(open|done|waived)$")
    due_date: str | None = Field(default=None, max_length=20)
    note: str = Field(default="", max_length=1000)


class WatchlistIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    scope: str = Field(default="private", pattern="^(private|unit)$")
    case_number: str | None = Field(default=None, max_length=100)


class WatchlistPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    active: bool | None = None


class ItemIn(BaseModel):
    kind: str = Field(max_length=20)
    value: str = Field(min_length=1, max_length=255)
    note: str = Field(default="", max_length=500)


class ScanIn(BaseModel):
    case_number: str = Field(min_length=1, max_length=100)


class AccessIn(BaseModel):
    case_number: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=5, max_length=1000)


class AccessDecideIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=1000)


# ================================================================== router
def build(ctx):
    r = APIRouter(prefix="/workflow", tags=["workflow"])
    get_db, perm, audit = ctx.get_db, ctx.require_perm, ctx.audit
    hit_limiter = AttemptLimiter(max_attempts=40, window_seconds=60)

    # ------------------------------------------------ hooks
    def _scan_hook(db=None, case_number=None, **kw):
        if db is None or not case_number:
            return
        try:
            hits = collect_matches(ctx, db, case_number, _relevant_watchlists(db, case_number))
            deliver_watchlist_hits(db, case_number, hits)
        except Exception:  # noqa: BLE001
            db.rollback(); log.exception("watchlist hook failed")

    def _doc_hook(db=None, document_id=None, **kw):
        if db is None or not document_id:
            return
        d = db.query(DocumentRecord).filter_by(document_id=document_id).first()
        if d:
            _scan_hook(db=db, case_number=d.case_number)

    for ev in ("document_extracted", "table_ingested", "evidence_uploaded"):
        ctx.hooks.on(ev, _scan_hook)
    ctx.hooks.on("document_reviewed", _doc_hook)

    # ------------------------------------------------ approvals
    def _appr(a: ApprovalRequest) -> dict:
        return {"id": a.id, "case_number": a.case_number, "kind": a.kind, "ref_id": a.ref_id, "title": a.title,
                "note": a.note, "requested_by": a.requested_by, "status": a.status, "decided_by": a.decided_by,
                "decided_at": _iso(a.decided_at), "decision_note": a.decision_note, "created_at": _iso(a.created_at)}

    @r.post("/approvals")
    def create_approval(x: ApprovalIn, db: Session = Depends(get_db), user=Depends(perm("review"))):
        cn = ctx.scope(db, user, x.case_number)
        if x.kind not in APPROVAL_KINDS:
            raise HTTPException(400, "kind must be one of: " + ", ".join(APPROVAL_KINDS))
        if db.query(ApprovalRequest).filter_by(case_number=cn, kind=x.kind, ref_id=x.ref_id, status="pending").first():
            raise HTTPException(409, "An identical request is already pending")
        a = ApprovalRequest(case_number=cn, kind=x.kind, ref_id=x.ref_id, title=x.title.strip(), note=x.note,
                            requested_by=user["sub"])
        db.add(a); db.commit()
        audit(db, user["sub"], "approval.requested", {"case_number": cn, "approval_id": a.id, "kind": x.kind, "ref_id": x.ref_id})
        for u in _users_with_perm(db, "approve", cn, exclude=user["sub"]):
            notify(db, u, "approval_requested", f"Approval requested: {a.title}",
                   f"{user['sub']} asks for {x.kind.replace('_', ' ')} approval on {cn}.", cn, ("approval", a.id))
        return _appr(a)

    @r.get("/approvals")
    def list_approvals(case_number: str | None = None, status: str | None = None, mine: bool = False,
                       db: Session = Depends(get_db), user=Depends(perm("read"))):
        q = db.query(ApprovalRequest)
        if case_number:
            q = q.filter_by(case_number=ctx.scope(db, user, case_number))
        else:
            allowed_cases = {c["case_number"] for c in ctx.svc.cases(db, user)}
            q = q.filter(ApprovalRequest.case_number.in_(allowed_cases or {""}))
        if status:
            if status not in ("pending", "approved", "rejected", "withdrawn"):
                raise HTTPException(400, "invalid status")
            q = q.filter_by(status=status)
        if mine:
            q = q.filter_by(requested_by=user["sub"])
        return {"approvals": [_appr(a) for a in q.order_by(ApprovalRequest.id.desc()).limit(300).all()],
                "can_decide": ctx_allowed(user, "approve")}

    def ctx_allowed(user, p):
        from security.rbac import allowed
        return allowed(user.get("role"), p)

    def _get_approval(db, user, aid):
        a = db.query(ApprovalRequest).filter_by(id=aid).first()
        if not a:
            raise HTTPException(404, "Approval request not found")
        ctx.scope(db, user, a.case_number)  # 404 when the case is not accessible
        return a

    @r.post("/approvals/{aid}/decide")
    def decide_approval(aid: int, x: DecideIn, db: Session = Depends(get_db), user=Depends(perm("approve"))):
        a = _get_approval(db, user, aid)
        if a.requested_by == user["sub"]:
            audit(db, user["sub"], "approval.self_decision_blocked", {"case_number": a.case_number, "approval_id": a.id})
            raise HTTPException(403, "Maker-checker: you cannot decide your own request")
        if a.status != "pending":
            raise HTTPException(409, f"Request is already {a.status}")
        if x.decision == "reject" and len(x.note.strip()) < 3:
            raise HTTPException(400, "A decision note is required when rejecting")
        a.status = "approved" if x.decision == "approve" else "rejected"
        a.decided_by, a.decided_at, a.decision_note = user["sub"], _now(), x.note.strip()
        db.commit()
        audit(db, user["sub"], f"approval.{a.status}", {"case_number": a.case_number, "approval_id": a.id, "kind": a.kind,
                                                      "ref_id": a.ref_id, "requested_by": a.requested_by})
        notify(db, a.requested_by, f"approval_{a.status}", f"Request {a.status}: {a.title}",
               f"{user['sub']} {a.status} your {a.kind.replace('_', ' ')} request on {a.case_number}."
               + (f" Note: {a.decision_note}" if a.decision_note else ""), a.case_number, ("approval", a.id))
        return _appr(a)

    @r.post("/approvals/{aid}/withdraw")
    def withdraw_approval(aid: int, db: Session = Depends(get_db), user=Depends(perm("read"))):
        a = _get_approval(db, user, aid)
        if a.requested_by != user["sub"]:
            raise HTTPException(403, "Only the requester can withdraw a request")
        if a.status != "pending":
            raise HTTPException(409, f"Request is already {a.status}")
        a.status = "withdrawn"; a.decided_by, a.decided_at = user["sub"], _now(); db.commit()
        audit(db, user["sub"], "approval.withdrawn", {"case_number": a.case_number, "approval_id": a.id})
        return _appr(a)

    # ------------------------------------------------ deadline templates
    def _templates(db) -> list[DeadlineTemplate]:
        if db.query(DeadlineTemplate).count() == 0:
            for k, label, kind, days, anchor, note in DEFAULT_TEMPLATES:
                db.add(DeadlineTemplate(key=k, label=label, kind=kind, days=days, anchor=anchor, note=note))
            try:
                db.commit()
            except Exception:  # noqa: BLE001  (concurrent seed)
                db.rollback()
        return db.query(DeadlineTemplate).order_by(DeadlineTemplate.id).all()

    def _tpl(t) -> dict:
        return {"key": t.key, "label": t.label, "kind": t.kind, "days": t.days, "anchor": t.anchor, "note": t.note,
                "active": bool(t.active)}

    def on_seed(_ctx=None):
        try:
            with ctx.SessionLocal() as db:
                _templates(db)
        except Exception:  # noqa: BLE001
            log.exception("template seed failed")

    on_seed()

    @r.get("/deadline-templates")
    def deadline_templates(db: Session = Depends(get_db), user=Depends(perm("read"))):
        return {"disclaimer": TEMPLATE_DISCLAIMER, "templates": [_tpl(t) for t in _templates(db)],
                "can_configure": ctx_allowed(user, "manage_cases")}

    @r.put("/deadline-templates/{key}")
    def update_template(key: str, x: TemplateIn, db: Session = Depends(get_db), user=Depends(perm("manage_cases"))):
        _templates(db)
        t = db.query(DeadlineTemplate).filter_by(key=key).first()
        if not t:
            raise HTTPException(404, "Template not found")
        before = _tpl(t)
        for f in ("label", "days", "active", "note"):
            v = getattr(x, f)
            if v is not None:
                setattr(t, f, v)
        db.commit()
        audit(db, user["sub"], "deadline_template.updated", {"key": key, "before": before, "after": _tpl(t)})
        return _tpl(t)

    @r.post("/deadline-templates")
    def create_template(x: TemplateCreateIn, db: Session = Depends(get_db), user=Depends(perm("manage_cases"))):
        _templates(db)
        if db.query(DeadlineTemplate).filter_by(key=x.key).first():
            raise HTTPException(409, "Template key already exists")
        t = DeadlineTemplate(key=x.key, label=x.label, kind=x.kind, days=x.days, anchor=x.anchor, note=x.note)
        db.add(t); db.commit()
        audit(db, user["sub"], "deadline_template.created", {"key": x.key, "days": x.days})
        return _tpl(t)

    # ------------------------------------------------ deadlines
    @r.post("/deadlines")
    def create_deadline(x: DeadlineIn, db: Session = Depends(get_db), user=Depends(perm("assign"))):
        cn = ctx.scope(db, user, x.case_number)
        label, kind, start, due = (x.label or "").strip(), "custom", None, None
        if x.template_key:
            _templates(db)
            t = db.query(DeadlineTemplate).filter_by(key=x.template_key, active=True).first()
            if not t:
                raise HTTPException(404, "Template not found")
            label, kind = label or t.label, t.kind
            if x.due_date:
                due = parse_date(x.due_date)
            else:
                if not x.start_date:
                    raise HTTPException(400, "start_date is required with a template (or give an explicit due_date)")
                start = parse_date(x.start_date)
                due = start + timedelta(days=t.days)
        else:
            if not x.due_date or not label:
                raise HTTPException(400, "Give a template_key, or both label and due_date")
            due = parse_date(x.due_date)
        if x.start_date and start is None:
            start = parse_date(x.start_date)
        d = CaseDeadline(case_number=cn, label=label[:255], kind=kind, start_date=_iso(start) or "", due_date=due.isoformat(),
                         note=x.note, created_by=user["sub"])
        db.add(d); db.commit()
        audit(db, user["sub"], "deadline.created", {"case_number": cn, "deadline_id": d.id, "label": d.label, "due_date": d.due_date})
        return _deadline_dict(d)

    @r.get("/deadlines/upcoming")
    def upcoming(days: int = Query(60, ge=1, le=730), db: Session = Depends(get_db), user=Depends(perm("read"))):
        cases = {c["case_number"]: c for c in ctx.svc.cases(db, user)}
        horizon = (_now().date() + timedelta(days=days)).isoformat()
        rows = (db.query(CaseDeadline).filter(CaseDeadline.status == "open", CaseDeadline.case_number.in_(list(cases) or [""]),
                                              CaseDeadline.due_date <= horizon).order_by(CaseDeadline.due_date).limit(500).all())
        out = []
        for d in rows:
            item = _deadline_dict(d); item["case_title"] = cases[d.case_number]["title"]; out.append(item)
        return {"deadlines": out, "summary": {s: sum(1 for i in out if i["state"] == s) for s in ("overdue", "at_risk", "on_track")},
                "disclaimer": TEMPLATE_DISCLAIMER}

    @r.get("/deadlines/suggest")
    def suggest(case_number: str, db: Session = Depends(get_db), user=Depends(perm("read"))):
        cn = ctx.scope(db, user, case_number)
        best = None
        import json as _json
        for d in db.query(DocumentRecord).filter_by(case_number=cn, status="Verified").all():
            try:
                s = _json.loads(d.structured_json or "{}")
                fd = parse_date(s.get("fir_date")) if s.get("fir_date") else None
            except Exception:  # noqa: BLE001
                continue
            if fd and (best is None or fd < best[0]):
                best = (fd, d, s)
        if not best:
            return {"case_number": cn, "suggested_start_date": None,
                    "reason": "No verified document with a FIR date yet. Enter the start date manually."}
        return {"case_number": cn, "suggested_start_date": best[0].isoformat(), "document_id": best[1].document_id,
                "fir_number": best[2].get("fir_number"),
                "reason": f"FIR date on verified document {best[1].filename}. Statutory limits often run from the date of arrest instead; confirm with the legal officer."}

    @r.get("/deadlines/ics")
    def deadlines_ics(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(perm("read"))):
        if case_number:
            cns = [ctx.scope(db, user, case_number)]
        else:
            cns = [c["case_number"] for c in ctx.svc.cases(db, user)]
        rows = db.query(CaseDeadline).filter(CaseDeadline.status == "open", CaseDeadline.case_number.in_(cns or [""])).order_by(CaseDeadline.due_date).limit(1000).all()
        body = build_ics([_deadline_dict(d) for d in rows])
        audit(db, user["sub"], "deadline.ics_exported", {"case_number": case_number or "", "count": len(rows)})
        name = re.sub(r"[^A-Za-z0-9._-]", "_", case_number or "all_cases")
        return Response(body, media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="deadlines_{name}.ics"'})

    @r.get("/deadlines")
    def list_deadlines(case_number: str, db: Session = Depends(get_db), user=Depends(perm("read"))):
        cn = ctx.scope(db, user, case_number)
        rows = db.query(CaseDeadline).filter_by(case_number=cn).order_by(CaseDeadline.due_date).all()
        return {"case_number": cn, "deadlines": [_deadline_dict(d) for d in rows], "disclaimer": TEMPLATE_DISCLAIMER}

    @r.patch("/deadlines/{did}")
    def patch_deadline(did: int, x: DeadlinePatch, db: Session = Depends(get_db), user=Depends(perm("assign"))):
        d = db.query(CaseDeadline).filter_by(id=did).first()
        if not d:
            raise HTTPException(404, "Deadline not found")
        ctx.scope(db, user, d.case_number)
        changes = {}
        if x.due_date:
            if not x.note.strip():
                raise HTTPException(400, "A note explaining the reschedule is required")
            new_due = parse_date(x.due_date).isoformat()
            changes["due_date"] = [d.due_date, new_due]; d.due_date = new_due
        if x.status and x.status != d.status:
            if x.status == "waived" and not x.note.strip():
                raise HTTPException(400, "A note is required to waive a deadline")
            changes["status"] = [d.status, x.status]; d.status = x.status
            d.done_at = _now() if x.status == "done" else None
        if not changes and not x.note.strip():
            raise HTTPException(400, "Nothing to change")
        if x.note.strip():
            d.note = (d.note + "\n" if d.note else "") + f"[{_now().date()} {user['sub']}] {x.note.strip()}"
        db.commit()
        audit(db, user["sub"], "deadline.updated", {"case_number": d.case_number, "deadline_id": d.id, "changes": changes, "note": x.note[:200]})
        return _deadline_dict(d)

    # ------------------------------------------------ watchlists
    def _wl(db, w: Watchlist, items=True) -> dict:
        out = {"id": w.id, "owner": w.owner, "name": w.name, "scope": w.scope, "case_number": w.case_number,
               "active": bool(w.active), "created_at": _iso(w.created_at)}
        if items:
            out["items"] = [{"id": i.id, "kind": i.kind, "value": i.value, "note": i.note}
                            for i in db.query(WatchlistItem).filter_by(watchlist_id=w.id).order_by(WatchlistItem.id).limit(500).all()]
        return out

    def _wl_owned(db, user, wid) -> Watchlist:
        w = db.query(Watchlist).filter_by(id=wid).first()
        if not w or (w.owner != user["sub"] and user["role"] != "admin" and w.scope != "unit"):
            raise HTTPException(404, "Watchlist not found")
        if w.owner != user["sub"] and user["role"] != "admin":
            raise HTTPException(403, "Only the owner or an admin can change this watchlist")
        return w

    @r.post("/watchlists")
    def create_watchlist(x: WatchlistIn, db: Session = Depends(get_db), user=Depends(perm("analyze"))):
        cn = ctx.scope(db, user, x.case_number) if x.case_number else None
        w = Watchlist(owner=user["sub"], name=x.name.strip(), scope=x.scope, case_number=cn)
        db.add(w); db.commit()
        audit(db, user["sub"], "watchlist.created", {"case_number": cn or "", "watchlist_id": w.id, "scope": w.scope})
        return _wl(db, w)

    @r.get("/watchlists")
    def list_watchlists(db: Session = Depends(get_db), user=Depends(perm("analyze"))):
        q = db.query(Watchlist)
        if user["role"] != "admin":
            q = q.filter((Watchlist.owner == user["sub"]) | (Watchlist.scope == "unit"))
        return {"watchlists": [_wl(db, w) for w in q.order_by(Watchlist.id.desc()).limit(200).all()],
                "kinds": list(KINDS)}

    @r.patch("/watchlists/{wid}")
    def patch_watchlist(wid: int, x: WatchlistPatch, db: Session = Depends(get_db), user=Depends(perm("analyze"))):
        w = _wl_owned(db, user, wid)
        if x.name is not None:
            w.name = x.name.strip()
        if x.active is not None:
            w.active = x.active
        db.commit()
        audit(db, user["sub"], "watchlist.updated", {"watchlist_id": w.id, "case_number": w.case_number or ""})
        return _wl(db, w)

    @r.delete("/watchlists/{wid}")
    def delete_watchlist(wid: int, db: Session = Depends(get_db), user=Depends(perm("analyze"))):
        w = _wl_owned(db, user, wid)
        db.query(WatchlistItem).filter_by(watchlist_id=w.id).delete(); db.delete(w); db.commit()
        audit(db, user["sub"], "watchlist.deleted", {"watchlist_id": wid, "case_number": w.case_number or ""})
        return {"status": "deleted"}

    @r.post("/watchlists/scan")
    def scan(x: ScanIn, db: Session = Depends(get_db), user=Depends(perm("analyze"))):
        cn = ctx.scope(db, user, x.case_number)
        visible = [w for w in _relevant_watchlists(db, cn) if w.owner == user["sub"] or w.scope == "unit" or user["role"] == "admin"]
        hits = collect_matches(ctx, db, cn, visible)
        created = deliver_watchlist_hits(db, cn, hits)
        audit(db, user["sub"], "watchlist.scanned", {"case_number": cn, "hits": len(hits), "notifications": created})
        return {"case_number": cn, "watchlists_checked": len(visible), "notifications_created": created,
                "hits": [{"watchlist": h["watchlist"].name, "owner": h["watchlist"].owner, "kind": h["item"].kind,
                          "watched_value": h["item"].value, "matched": h["matched"], "source": h["source_type"],
                          "reason": h["reason"]} for h in hits[:200]],
                "note": "Matches are investigative signals (normalised identifiers, light fuzzy name match) for human review."}

    @r.post("/watchlists/{wid}/items")
    def add_item(wid: int, x: ItemIn, db: Session = Depends(get_db), user=Depends(perm("analyze"))):
        w = _wl_owned(db, user, wid)
        if x.kind not in KINDS:
            raise HTTPException(400, "kind must be one of: " + ", ".join(KINDS))
        norm = _valid_item(x.kind, x.value)
        if db.query(WatchlistItem).filter_by(watchlist_id=w.id).count() >= 500:
            raise HTTPException(400, "Watchlist is full (500 items)")
        if db.query(WatchlistItem).filter_by(watchlist_id=w.id, kind=x.kind, value_norm=norm).first():
            raise HTTPException(409, "That value is already on the list")
        it = WatchlistItem(watchlist_id=w.id, kind=x.kind, value=x.value.strip(), value_norm=norm, note=x.note)
        db.add(it); db.commit()
        audit(db, user["sub"], "watchlist.item_added", {"watchlist_id": w.id, "kind": x.kind, "case_number": w.case_number or ""})
        return {"id": it.id, "kind": it.kind, "value": it.value, "value_norm": norm, "note": it.note}

    @r.delete("/watchlists/{wid}/items/{iid}")
    def delete_item(wid: int, iid: int, db: Session = Depends(get_db), user=Depends(perm("analyze"))):
        w = _wl_owned(db, user, wid)
        it = db.query(WatchlistItem).filter_by(id=iid, watchlist_id=w.id).first()
        if not it:
            raise HTTPException(404, "Item not found")
        db.delete(it); db.commit()
        audit(db, user["sub"], "watchlist.item_removed", {"watchlist_id": w.id, "kind": it.kind, "case_number": w.case_number or ""})
        return {"status": "deleted"}

    # ------------------------------------------------ notifications
    def _notif(n: Notification) -> dict:
        return {"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "case_number": n.case_number,
                "ref_type": n.ref_type, "ref_id": n.ref_id, "read": bool(n.read), "created_at": _iso(n.created_at)}

    @r.get("/notifications/unread-count")
    def unread_count(db: Session = Depends(get_db), user=Depends(perm("read"))):
        return {"unread": db.query(Notification).filter_by(user=user["sub"], read=False).count()}

    @r.post("/notifications/read-all")
    def read_all(db: Session = Depends(get_db), user=Depends(perm("read"))):
        n = db.query(Notification).filter_by(user=user["sub"], read=False).update({"read": True})
        db.commit()
        return {"marked": n}

    @r.get("/notifications")
    def list_notifications(unread: bool = False, limit: int = Query(100, ge=1, le=300), db: Session = Depends(get_db),
                           user=Depends(perm("read"))):
        q = db.query(Notification).filter_by(user=user["sub"])
        if unread:
            q = q.filter_by(read=False)
        return {"notifications": [_notif(n) for n in q.order_by(Notification.id.desc()).limit(limit).all()],
                "unread": db.query(Notification).filter_by(user=user["sub"], read=False).count()}

    @r.post("/notifications/{nid}/read")
    def mark_read(nid: int, db: Session = Depends(get_db), user=Depends(perm("read"))):
        n = db.query(Notification).filter_by(id=nid, user=user["sub"]).first()
        if not n:
            raise HTTPException(404, "Notification not found")
        n.read = True; db.commit()
        return _notif(n)


    return r
