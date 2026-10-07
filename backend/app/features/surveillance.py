"""Surveillance / observation logging and court-usable reporting.

* An **operation** is case-scoped and cannot exist without an authority / permission reference and text.
* A **log entry** is APPEND-ONLY. Amending creates a new entry (a new version) that supersedes the old one and must state a reason; nothing is ever
  overwritten or deleted. Each entry stores a SHA-256 of its canonical JSON and the previous entry's hash, so silent edits are detectable
  (``GET /surveillance/{op}/verify`` recomputes the chain and cross-checks the keyed audit log).
* **Movement analysis** (dwell places, recurring places and times, co-presence, vehicles) is computed on demand from the log.
* **Reports** (pdf / docx / html / json) carry an integrity footer: chain head, entry count, who generated it, when, and a SHA-256 of the report content.

Personal data (persons seen, registrations, observation text, positions) is masked for roles without ``sensitive_read``.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, Float, Integer, String, Text

from ai_engine.social.timeutil import fmt_local, iso_utc, parse_ts, zone_label
from ai_engine.surveillance import chain as ch
from ai_engine.surveillance import movement as mv
from ai_engine.surveillance import reportgen as rg
from backend.app.db.database import Base
from backend.app.models.models import AuditEvent, Evidence
from security.masking import mask_identifier
from security.rbac import allowed

_lock = threading.Lock()
OP_RE = re.compile(r"^OP-\d{4}-\d{4,6}$")
ROLES = ("subject", "associate", "unknown")
NOTICE = ("Surveillance may only be carried out under valid authority. Enter the order or permission reference before you start; the report shows it on every page. "
          "Entries cannot be edited or deleted: to correct one, record an amendment with the reason.")
LIMITS = [
    "The log records what the named observers reported. Its integrity checks show the log has not been altered since it was written; they do not show that an observation was accurate.",
    "The hash chain is stored in the same database as the entries. Someone with full database write access could rewrite everything; the keyed audit log and the tamper-evident ledger "
    "(if enabled) hold independent copies of each entry's hash so that such a rewrite can be detected.",
    "Movement, dwell and co-presence results use only located entries. Positions taken from a place name are approximate. Gaps in observation are not evidence of absence.",
    "Times are stored in UTC and shown in the report's time zone (IST unless stated). Observers should record times from a synchronised clock.",
    "Entries outside the authorised period are flagged in the report; whether they are usable is a legal decision, not one this tool makes.",
    "The PDF report uses a standard Latin font: names written in Devanagari or other scripts may not display in the PDF (they are complete in the DOCX, HTML and JSON versions).",
]


# ----------------------------------------------------------------------------------------------------------------- models
class SurvOperation(Base):
    __tablename__ = "surv_operations"
    id = Column(Integer, primary_key=True)
    op_number = Column(String(30), unique=True, index=True)
    case_number = Column(String(100), index=True)
    codename = Column(String(100), default="")
    subject_entity_id = Column(String(100), default="")
    subject_text = Column(String(300), default="")
    objective = Column(Text, default="")
    authority_text = Column(Text, default="")
    authority_ref = Column(String(200), default="")
    start_at = Column(String(30), default="")
    end_at = Column(String(30), default="")
    supervising_officer = Column(String(150), default="")
    team_json = Column(Text, default="[]")
    created_by = Column(String(100), default="")
    created_at = Column(String(30), default="")
    header_hash = Column(String(64), default="")
    status = Column(String(20), default="active")
    entry_count = Column(Integer, default=0)
    head_hash = Column(String(64), default="")
    closed_by = Column(String(100), default="")
    closed_at = Column(String(30), default="")
    closing_remarks = Column(Text, default="")
    closure_hash = Column(String(64), default="")


class SurvEntry(Base):
    __tablename__ = "surv_entries"
    id = Column(Integer, primary_key=True)
    uid = Column(String(40), unique=True, index=True)
    op_number = Column(String(30), index=True)
    case_number = Column(String(100), index=True)
    seq = Column(Integer, index=True)
    version = Column(Integer, default=1)
    root_uid = Column(String(40), index=True)
    supersedes_uid = Column(String(40), default="")
    observed_at = Column(String(30), default="")
    location_text = Column(String(300), default="")
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    place_json = Column(Text, default="{}")
    observer = Column(String(120), default="")
    observation = Column(Text, default="")
    subject_seen = Column(Boolean, default=True)
    vehicles_json = Column(Text, default="[]")
    persons_json = Column(Text, default="[]")
    evidence_json = Column(Text, default="[]")
    source_rating = Column(String(1), default="F")
    info_rating = Column(String(1), default="6")
    amendment_reason = Column(Text, default="")
    recorded_by = Column(String(100), default="")
    recorded_at = Column(String(30), default="")
    prev_hash = Column(String(64), default="")
    entry_hash = Column(String(64), default="")


# ----------------------------------------------------------------------------------------------------------------- inputs
class OperationIn(BaseModel):
    case_number: str = Field(max_length=100)
    codename: str = Field(default="", max_length=100)
    subject_entity_id: str = Field(default="", max_length=100)
    subject_text: str = Field(default="", max_length=300)
    objective: str = Field(min_length=5, max_length=2000)
    authority_text: str = Field(min_length=10, max_length=2000)
    authority_ref: str = Field(min_length=3, max_length=200)
    start_at: str = Field(max_length=40)
    end_at: str = Field(default="", max_length=40)
    timezone: str = Field(default="IST", max_length=10)
    supervising_officer: str = Field(min_length=2, max_length=150)
    team: list[str] = Field(default_factory=list, max_length=30)


class VehicleIn(BaseModel):
    reg: str = Field(min_length=3, max_length=20)
    description: str = Field(default="", max_length=200)


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(default="unknown", max_length=12)
    entity_id: str = Field(default="", max_length=100)


class EntryIn(BaseModel):
    observed_at: str = Field(max_length=40)
    timezone: str = Field(default="IST", max_length=10)
    location_text: str = Field(min_length=2, max_length=300)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    observer: str = Field(default="", max_length=120)
    observation: str = Field(min_length=5, max_length=5000)
    subject_seen: bool = True
    vehicles: list[VehicleIn] = Field(default_factory=list, max_length=20)
    persons: list[PersonIn] = Field(default_factory=list, max_length=30)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    source_rating: str = Field(default="F", max_length=1)
    info_rating: str = Field(default="6", max_length=1)


class AmendIn(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)
    observed_at: str | None = Field(default=None, max_length=40)
    timezone: str = Field(default="IST", max_length=10)
    location_text: str | None = Field(default=None, min_length=2, max_length=300)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    clear_coordinates: bool = False
    observer: str | None = Field(default=None, max_length=120)
    observation: str | None = Field(default=None, min_length=5, max_length=5000)
    subject_seen: bool | None = None
    vehicles: list[VehicleIn] | None = Field(default=None, max_length=20)
    persons: list[PersonIn] | None = Field(default=None, max_length=30)
    evidence_ids: list[str] | None = Field(default=None, max_length=30)
    source_rating: str | None = Field(default=None, max_length=1)
    info_rating: str | None = Field(default=None, max_length=1)


class CloseIn(BaseModel):
    remarks: str = Field(min_length=5, max_length=3000)


class SampleIn(BaseModel):
    case_number: str = Field(max_length=100)


# ----------------------------------------------------------------------------------------------------------------- helpers
def _now_iso() -> str:
    return iso_utc(datetime.now(timezone.utc).replace(tzinfo=None))


def _jl(text, default):
    try:
        v = json.loads(text or "")
        return v if isinstance(v, type(default)) else default
    except (ValueError, TypeError):
        return default


def _f6(x):
    return None if x is None else float(round(float(x), 6))


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _masked(user) -> bool:
    return not allowed(user["role"], "sensitive_read")


def _mask_name(name: str) -> str:
    return " ".join((w[:1] + "*" * max(2, len(w) - 1)) if w else w for w in (name or "").split())


def _gaz():
    from backend.app.services.timeline_engine import gazetteer
    return gazetteer()


def _header(op: SurvOperation) -> dict:
    return {"op_number": op.op_number, "case_number": op.case_number, "subject_entity_id": op.subject_entity_id, "subject_text": op.subject_text, "objective": op.objective,
            "authority_text": op.authority_text, "authority_ref": op.authority_ref, "start_at": op.start_at, "end_at": op.end_at, "supervising_officer": op.supervising_officer,
            "team": _jl(op.team_json, []), "created_by": op.created_by, "created_at": op.created_at}


def _payload(e: SurvEntry) -> dict:
    return {"uid": e.uid, "op_number": e.op_number, "case_number": e.case_number, "seq": e.seq, "version": e.version, "root_uid": e.root_uid, "supersedes_uid": e.supersedes_uid,
            "observed_at": e.observed_at, "location_text": e.location_text, "lat": e.lat, "lon": e.lon, "place_matched": _jl(e.place_json, {}), "observer": e.observer,
            "observation": e.observation, "subject_seen": bool(e.subject_seen), "vehicles": _jl(e.vehicles_json, []), "persons": _jl(e.persons_json, []),
            "evidence_ids": _jl(e.evidence_json, []), "source_rating": e.source_rating, "info_rating": e.info_rating, "amendment_reason": e.amendment_reason,
            "recorded_by": e.recorded_by, "recorded_at": e.recorded_at, "prev_hash": e.prev_hash}


def _op_for_hash(op: SurvOperation) -> dict:
    return {**_header(op), "header_hash": op.header_hash, "status": op.status, "closed_by": op.closed_by, "closed_at": op.closed_at, "closing_remarks": op.closing_remarks,
            "closure_hash": op.closure_hash}


def verify_operation(db, op: SurvOperation) -> dict:
    rows = db.query(SurvEntry).filter_by(op_number=op.op_number).order_by(SurvEntry.seq.asc()).all()
    dicts = [{**_payload(r), "entry_hash": r.entry_hash} for r in rows]
    res = ch.verify(_op_for_hash(op), dicts, op.head_hash, op.entry_count)
    # cross-check with the keyed audit log: each entry's hash was written there when the entry was created
    anchors: dict[str, str] = {}
    for a in db.query(AuditEvent).filter(AuditEvent.action.in_(["surveillance.entry_added", "surveillance.entry_amended"]), AuditEvent.details.like(f"%{op.op_number}%")).all():
        try:
            d = json.loads(a.details or "{}")
        except ValueError:
            continue
        if d.get("op_number") == op.op_number and d.get("entry_uid"):
            anchors[d["entry_uid"]] = d.get("entry_hash", "")
    mism = [r["seq"] for r in dicts if r["uid"] in anchors and anchors[r["uid"]] != r["entry_hash"]]
    missing = [r["seq"] for r in dicts if r["uid"] not in anchors]
    for s in mism:
        res["problems"].append({"seq": s, "problem": "audit_anchor_mismatch", "detail": "The hash recorded in the audit log when this entry was written differs from the stored entry."})
    if mism:
        res["status"] = "broken"
    res["audit_anchor"] = {"anchored": len(dicts) - len(missing), "missing": len(missing), "mismatched": len(mism)}
    res["checked_at"] = _now_iso()
    res["op_number"] = op.op_number
    res["entry_count_recorded"] = op.entry_count
    res["explain"] = ("Every entry's hash and its link to the entry before it were recomputed from the stored fields. 'Verified' means nothing was changed, re-ordered or removed "
                      "since it was written. 'Broken' lists exactly where the chain fails.")
    return res


def _place_for(location_text: str) -> dict:
    p = _gaz().find_in_text(location_text)
    return {"name": p["name"], "lat": float(p["lat"]), "lon": float(p["lon"]), "precision": "approximate"} if p else {}


def _scales_list():
    return [(f"Source {k}", v) for k, v in ch.SOURCE_RATINGS.items()] + [(f"Information {k}", v) for k, v in ch.INFO_RATINGS.items()]


def _fmt_ts(iso: str, tz: str) -> str:
    d = parse_ts(iso, "UTC")
    return fmt_local(d, tz) if d else ""


# ----------------------------------------------------------------------------------------------------------------- API
def build(ctx):
    router = APIRouter(prefix="/surveillance", tags=["surveillance"])
    perm = ctx.require_perm

    # ------------------------------------------------ loaders
    def load_op(db, user, op_number: str) -> SurvOperation:
        op = db.query(SurvOperation).filter_by(op_number=(op_number or "").strip().upper()).first()
        if op is None:
            raise HTTPException(404, "Operation not found")
        try:
            ctx.scope(db, user, op.case_number)
        except HTTPException:
            raise HTTPException(404, "Operation not found")  # same answer as for a missing operation: no existence oracle
        return op

    def entity_map(db, cn) -> dict:
        return {e["external_id"]: e for e in ctx.svc.entities(db, cn, role="investigator")}

    def check_entity(ents, eid, what):
        if eid and eid not in ents:
            raise HTTPException(400, f"{what} '{eid}' does not exist in this case")

    def check_evidence(db, cn, ids):
        out = []
        for i in dict.fromkeys(str(x).strip() for x in ids if str(x).strip()):
            if not db.query(Evidence).filter_by(evidence_id=i, case_number=cn).first():
                raise HTTPException(400, f"Evidence '{i}' was not found in this case. Upload it in the evidence vault first, then reference its ID.")
            out.append(i)
        return out

    def check_ratings(s, i):
        if s not in ch.SOURCE_RATINGS:
            raise HTTPException(400, "source_rating must be A, B, C, D, E or F")
        if i not in ch.INFO_RATINGS:
            raise HTTPException(400, "info_rating must be 1 to 6")

    def clean_vehicles(vs):
        out, seen = [], set()
        for v in vs:
            n = ch.normalise_reg(v.reg)
            if not n["reg"] or n["reg"] in seen:
                continue
            seen.add(n["reg"])
            out.append({"reg": n["reg"], "valid": n["valid"], "description": v.description.strip()})
        return out

    def clean_persons(db_ents, ps):
        out = []
        for p in ps:
            if p.role not in ROLES:
                raise HTTPException(400, f"person role must be one of: {', '.join(ROLES)}")
            check_entity(db_ents, p.entity_id, "Entity")
            out.append({"name": re.sub(r"\s+", " ", p.name).strip(), "role": p.role, "entity_id": p.entity_id.strip()})
        return out

    # ------------------------------------------------ appending (the only writer of entries)
    def append_entry(db, user, op: SurvOperation, f: dict, *, supersedes: SurvEntry | None = None, reason: str = "") -> SurvEntry:
        with _lock:
            db.refresh(op)
            if op.status == "closed":
                raise HTTPException(409, "This operation is closed and accepts no new entries.")
            last = db.query(SurvEntry).filter_by(op_number=op.op_number).order_by(SurvEntry.seq.desc()).first()
            seq = (last.seq if last else 0) + 1
            uid = "E-" + hashlib.sha256(f"{op.op_number}|{seq}|{_now_iso()}|{user['sub']}".encode()).hexdigest()[:12].upper()
            payload = {"uid": uid, "op_number": op.op_number, "case_number": op.case_number, "seq": seq, "version": (supersedes.version + 1) if supersedes else 1,
                       "root_uid": supersedes.root_uid if supersedes else uid, "supersedes_uid": supersedes.uid if supersedes else "", "observed_at": f["observed_at"],
                       "location_text": f["location_text"], "lat": _f6(f.get("lat")), "lon": _f6(f.get("lon")), "place_matched": f.get("place_matched") or {}, "observer": f["observer"],
                       "observation": f["observation"], "subject_seen": bool(f["subject_seen"]), "vehicles": f["vehicles"], "persons": f["persons"], "evidence_ids": f["evidence_ids"],
                       "source_rating": f["source_rating"], "info_rating": f["info_rating"], "amendment_reason": reason, "recorded_by": user["sub"], "recorded_at": _now_iso(),
                       "prev_hash": last.entry_hash if last else op.header_hash}
            h = ch.entry_hash(payload)
            row = SurvEntry(uid=uid, op_number=op.op_number, case_number=op.case_number, seq=seq, version=payload["version"], root_uid=payload["root_uid"],
                            supersedes_uid=payload["supersedes_uid"], observed_at=payload["observed_at"], location_text=payload["location_text"], lat=payload["lat"], lon=payload["lon"],
                            place_json=json.dumps(payload["place_matched"], ensure_ascii=False), observer=payload["observer"], observation=payload["observation"],
                            subject_seen=payload["subject_seen"], vehicles_json=json.dumps(payload["vehicles"], ensure_ascii=False), persons_json=json.dumps(payload["persons"], ensure_ascii=False),
                            evidence_json=json.dumps(payload["evidence_ids"]), source_rating=payload["source_rating"], info_rating=payload["info_rating"],
                            amendment_reason=reason, recorded_by=payload["recorded_by"], recorded_at=payload["recorded_at"], prev_hash=payload["prev_hash"], entry_hash=h)
            db.add(row)
            op.entry_count, op.head_hash = seq, h
            db.commit()
        det = {"op_number": op.op_number, "entry_uid": uid, "seq": seq, "version": payload["version"], "entry_hash": h, "prev_hash": payload["prev_hash"]}
        if supersedes:
            det["supersedes"] = supersedes.uid
        ctx.audit(db, user["sub"], "surveillance.entry_amended" if supersedes else "surveillance.entry_added", {"case_number": op.case_number, **det, **({"reason": reason[:300]} if reason else {})})
        ctx.hooks.emit("surveillance_entry_amended" if supersedes else "surveillance_entry_added", db=db, user=user, case_number=op.case_number, ref=uid, detail=det)
        return row

    def make_op(db, user, x: OperationIn, cn: str) -> SurvOperation:
        ents = entity_map(db, cn)
        check_entity(ents, x.subject_entity_id.strip(), "Subject entity")
        if not x.subject_entity_id.strip() and not x.subject_text.strip():
            raise HTTPException(400, "Name the subject: pick a case entity or describe them in the subject text.")
        if len(x.authority_text.split()) < 3:
            raise HTTPException(400, "Describe the authority or permission (for example the order, who granted it and its scope).")
        start = parse_ts(x.start_at, x.timezone)
        if not start:
            raise HTTPException(400, "start_at is not a recognised date/time")
        end = parse_ts(x.end_at, x.timezone) if x.end_at.strip() else None
        if x.end_at.strip() and not end:
            raise HTTPException(400, "end_at is not a recognised date/time")
        if end and end <= start:
            raise HTTPException(400, "The end of the operation must be after its start")
        with _lock:
            n = db.query(SurvOperation).count() + 1
            year = datetime.now(timezone.utc).year
            while db.query(SurvOperation).filter_by(op_number=f"OP-{year}-{n:04d}").first():
                n += 1
            op = SurvOperation(op_number=f"OP-{year}-{n:04d}", case_number=cn, codename=x.codename.strip(), subject_entity_id=x.subject_entity_id.strip(),
                               subject_text=x.subject_text.strip(), objective=re.sub(r"\s+", " ", x.objective).strip(), authority_text=re.sub(r"\s+", " ", x.authority_text).strip(),
                               authority_ref=x.authority_ref.strip(), start_at=iso_utc(start), end_at=iso_utc(end) if end else "", supervising_officer=x.supervising_officer.strip(),
                               team_json=json.dumps([t.strip() for t in x.team if t.strip()], ensure_ascii=False), created_by=user["sub"], created_at=_now_iso(),
                               status="planned" if start > datetime.now(timezone.utc).replace(tzinfo=None) else "active")
            op.header_hash = ch.header_hash(_header(op))
            db.add(op)
            db.commit()
        det = {"op_number": op.op_number, "header_hash": op.header_hash, "authority_ref": op.authority_ref[:120]}
        ctx.audit(db, user["sub"], "surveillance.operation_created", {"case_number": cn, **det})
        ctx.hooks.emit("surveillance_operation_created", db=db, user=user, case_number=cn, ref=op.op_number, detail=det)
        return op

    def entry_fields_from(db, user, cn, x: EntryIn) -> dict:
        ents = entity_map(db, cn)
        t = parse_ts(x.observed_at, x.timezone)
        if not t:
            raise HTTPException(400, "observed_at is not a recognised date/time")
        if (x.lat is None) != (x.lon is None):
            raise HTTPException(400, "Give both latitude and longitude, or neither")
        check_ratings(x.source_rating, x.info_rating)
        return {"observed_at": iso_utc(t), "location_text": re.sub(r"\s+", " ", x.location_text).strip(), "lat": x.lat, "lon": x.lon,
                "place_matched": {} if x.lat is not None else _place_for(x.location_text), "observer": (x.observer or user["sub"]).strip(),
                "observation": x.observation.strip(), "subject_seen": x.subject_seen, "vehicles": clean_vehicles(x.vehicles), "persons": clean_persons(ents, x.persons),
                "evidence_ids": check_evidence(db, cn, x.evidence_ids), "source_rating": x.source_rating, "info_rating": x.info_rating}

    # ------------------------------------------------ views
    def entry_view(e: SurvEntry, masked: bool, tz: str, op: SurvOperation, superseded_by: SurvEntry | None) -> dict:
        p = _payload(e)
        t = parse_ts(e.observed_at, "UTC")
        start, end = parse_ts(op.start_at, "UTC"), parse_ts(op.end_at, "UTC") if op.end_at else None
        outside = bool(t and ((start and t < start) or (end and t > end)))
        v = {"uid": e.uid, "seq": e.seq, "version": e.version, "root_uid": e.root_uid, "supersedes_uid": e.supersedes_uid, "current": superseded_by is None,
             "superseded_by_seq": superseded_by.seq if superseded_by else None, "observed_at": e.observed_at, "observed_local": fmt_local(t, tz), "location_text": e.location_text,
             "lat": e.lat, "lon": e.lon, "place_matched": p["place_matched"], "observer": e.observer, "observation": e.observation, "subject_seen": bool(e.subject_seen),
             "vehicles": p["vehicles"], "persons": p["persons"], "evidence_ids": p["evidence_ids"], "source_rating": e.source_rating, "info_rating": e.info_rating,
             "rating": f"{e.source_rating}{e.info_rating}", "amendment_reason": e.amendment_reason, "recorded_by": e.recorded_by, "recorded_at": e.recorded_at,
             "prev_hash": e.prev_hash, "entry_hash": e.entry_hash, "outside_authorised_period": outside}
        if masked:
            v["observation"] = "[hidden for your role]"
            v["location_text"] = (p["place_matched"] or {}).get("name") or "[hidden for your role]"
            v["lat"] = v["lon"] = None
            v["vehicles"] = [{**x, "reg": mask_identifier(x["reg"]), "description": ""} for x in p["vehicles"]]
            v["persons"] = [{**x, "name": _mask_name(x["name"])} for x in p["persons"]]
            v["amendment_reason"] = "[hidden for your role]" if e.amendment_reason else ""
        return v

    def op_view(db, op: SurvOperation, masked: bool, ents: dict | None = None) -> dict:
        subject = op.subject_text
        if op.subject_entity_id and ents is not None and op.subject_entity_id in ents:
            subject = ents[op.subject_entity_id]["name"] or op.subject_entity_id
        if masked:
            subject = _mask_name(subject)
        return {"op_number": op.op_number, "case_number": op.case_number, "codename": op.codename, "subject": subject, "subject_entity_id": op.subject_entity_id,
                "objective": op.objective, "authority_text": op.authority_text, "authority_ref": op.authority_ref, "start_at": op.start_at, "end_at": op.end_at or None,
                "status": op.status, "supervising_officer": op.supervising_officer, "team": _jl(op.team_json, []), "created_by": op.created_by, "created_at": op.created_at,
                "entry_count": op.entry_count, "head_hash": op.head_hash, "header_hash": op.header_hash, "closed_by": op.closed_by or None, "closed_at": op.closed_at or None,
                "closing_remarks": op.closing_remarks or None, "closure_hash": op.closure_hash or None}

    def all_entries(db, op):
        rows = db.query(SurvEntry).filter_by(op_number=op.op_number).order_by(SurvEntry.seq.asc()).all()
        sup = {r.supersedes_uid: r for r in rows if r.supersedes_uid}
        return rows, sup

    # ------------------------------------------------ endpoints
    @router.get("/operations")
    def list_operations(case_number: str, db=Depends(ctx.get_db), user=Depends(perm("read"))):
        cn = ctx.scope(db, user, case_number)
        masked = _masked(user)
        ents = entity_map(db, cn)
        ops = db.query(SurvOperation).filter_by(case_number=cn).order_by(SurvOperation.id.desc()).all()
        return {"case_number": cn, "operations": [op_view(db, o, masked, ents) for o in ops], "notice": NOTICE, "limits": LIMITS,
                "scales": {"source": ch.SOURCE_RATINGS, "information": ch.INFO_RATINGS}, "masked": masked}

    @router.post("/operations")
    def create_operation(x: OperationIn, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        cn = ctx.scope(db, user, x.case_number)
        op = make_op(db, user, x, cn)
        return {"status": "created", "operation": op_view(db, op, False, entity_map(db, cn)), "message": "Operation created. Add log entries as observations are made."}

    @router.post("/sample")
    def create_sample(x: SampleIn, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        cn = ctx.scope(db, user, x.case_number)
        op = make_op(db, user, OperationIn(case_number=cn, codename="SAMPLE-DEMO", subject_text="Subject A (synthetic person)", objective="Training example: establish the subject's movements and associates over three days.",
                                           authority_text="SYNTHETIC EXAMPLE: order granted by a fictional Deputy Commissioner for a training exercise. No real person or operation.",
                                           authority_ref="SYNTHETIC/DEMO-0001", start_at="2026-09-22T06:00", end_at="2026-10-06T22:00", timezone="IST",
                                           supervising_officer="Insp. A. Rao (synthetic)", team=["SI R. Mehta (synthetic)", "HC S. Verma (synthetic)"]), cn)
        S1, S2 = "SI R. Mehta (synthetic)", "HC S. Verma (synthetic)"
        home, sec18, botan, cp, karol, isbt = ("Block C, Sector 62, Noida", (28.5706, 77.3219), (28.5640, 77.3341), (28.6315, 77.2167), (28.6519, 77.1909), (28.6469, 77.3164))
        plan = [
            ("2026-09-22T08:10", home, (28.6270, 77.3649), "Subject A left the building on foot and walked toward the main road.", [], [], "B", "2"),
            ("2026-09-22T09:05", "Botanical Garden metro station, Noida", botan, "Subject A entered the metro station and boarded a train toward Delhi.", [], [], "B", "2"),
            ("2026-09-22T09:50", "Connaught Place, New Delhi", cp, "Subject A came out of Rajiv Chowk station and sat at a cafe in the inner circle.", [], [], "A", "1"),
            ("2026-09-22T10:20", "Connaught Place, New Delhi", cp, "Subject A met an unidentified man in a grey jacket. They talked for about ten minutes and the man handed over a small bag.", [], [("Unknown male, grey jacket, about 35", "unknown")], "B", "3"),
            ("2026-09-22T18:15", "Sector 18 Market, Noida", sec18, "Subject A arrived at the market and waited near the food court.", [], [], "B", "2"),
            ("2026-09-22T18:50", "Sector 18 Market, Noida", sec18, "A white hatchback stopped; Associate B got out and spoke with Subject A. Both left in the car.", [("DL 3C AB 1243", "white hatchback")], [("Associate B (synthetic)", "associate")], "B", "2"),
            ("2026-09-23T08:05", home, (28.6270, 77.3649), "Subject A left the building on foot.", [], [], "B", "2"),
            ("2026-09-23T18:20", "Sector 18 Market, Noida", sec18, "Subject A waited near the food court; the same white hatchback arrived at 18:20.", [("DL3CAB1234", "white hatchback")], [("Associate B (synthetic)", "associate")], "A", "1"),
            ("2026-09-23T19:05", "Sector 18 Market, Noida", sec18, "Subject A and Associate B talked in the car for 30 minutes, then Subject A walked home.", [("DL3CAB1234", "white hatchback")], [("Associate B (synthetic)", "associate")], "B", "2"),
            ("2026-09-24T09:30", "Karol Bagh Market, New Delhi", karol, "Subject A visited two shops and bought a phone.", [], [], "B", "2"),
            ("2026-09-24T12:10", "Anand Vihar ISBT", isbt, "Subject A met a man beside a black SUV; a short exchange followed.", [("UP16BH5678", "black SUV")], [("Unknown male, black cap", "unknown")], "C", "3"),
            ("2026-09-24T18:25", "Sector 18 Market, Noida", sec18, "Subject A again waited near the food court and left at 18:55 on foot.", [], [], "B", "2"),
        ]
        rows = []
        for i, (t, loc, coord, text, veh, per, sr, ir) in enumerate(plan):
            entry = EntryIn(observed_at=t, timezone="IST", location_text=loc if isinstance(loc, str) else "Sector 62, Noida", lat=coord[0], lon=coord[1], observer=(S1, S2)[i % 2],
                            observation=text, vehicles=[VehicleIn(reg=r, description=d) for r, d in veh], persons=[PersonIn(name=n, role=ro) for n, ro in per], source_rating=sr, info_rating=ir)
            rows.append(append_entry(db, user, op, entry_fields_from(db, user, cn, entry)))
        wrong = rows[5]  # a transposed registration digit, corrected by an amendment
        fx = entry_fields_from(db, user, cn, EntryIn(observed_at="2026-09-22T18:50", timezone="IST", location_text="Sector 18 Market, Noida", lat=sec18[0], lon=sec18[1], observer=S1,
                                                    observation=wrong.observation, vehicles=[VehicleIn(reg="DL 3C AB 1234", description="white hatchback")],
                                                    persons=[PersonIn(name="Associate B (synthetic)", role="associate")], source_rating="B", info_rating="2"))
        append_entry(db, user, op, fx, supersedes=wrong, reason="Observer re-checked the photograph: last two digits of the registration were transposed.")
        db.refresh(op)
        return {"status": "created", "operation": op_view(db, op, False, entity_map(db, cn)), "message": "Sample operation loaded: 12 entries and one amendment, all synthetic."}

    @router.get("/{op}")
    def get_operation(op: str, tz: str = "IST", db=Depends(ctx.get_db), user=Depends(perm("read"))):
        o = load_op(db, user, op)
        masked = _masked(user)
        ents = entity_map(db, o.case_number)
        rows, sup = all_entries(db, o)
        return {"operation": op_view(db, o, masked, ents), "entries": [entry_view(r, masked, tz, o, sup.get(r.uid)) for r in rows], "integrity": verify_operation(db, o),
                "timezone": zone_label(tz), "scales": {"source": ch.SOURCE_RATINGS, "information": ch.INFO_RATINGS}, "notice": NOTICE, "masked": masked,
                "can_add": o.status != "closed"}

    @router.post("/{op}/entries")
    def add_entry(op: str, x: EntryIn, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        o = load_op(db, user, op)
        row = append_entry(db, user, o, entry_fields_from(db, user, o.case_number, x))
        rows, sup = all_entries(db, o)
        return {"status": "recorded", "entry": entry_view(row, False, x.timezone, o, None), "message": f"Entry {row.seq} recorded and added to the hash chain. It cannot be edited; use an amendment to correct it."}

    @router.post("/{op}/entries/{uid}/amend")
    def amend_entry(op: str, uid: str, x: AmendIn, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        o = load_op(db, user, op)
        old = db.query(SurvEntry).filter_by(op_number=o.op_number, uid=uid).first()
        if not old:
            raise HTTPException(404, "Entry not found")
        if db.query(SurvEntry).filter_by(op_number=o.op_number, supersedes_uid=old.uid).first():
            raise HTTPException(409, "This entry has already been amended: amend the latest version instead.")
        reason = re.sub(r"\s+", " ", x.reason).strip()
        if len(reason) < 5:
            raise HTTPException(400, "A reason for the amendment is required")
        cur = _payload(old)
        ents = entity_map(db, o.case_number)
        obs_iso = cur["observed_at"]
        if x.observed_at:
            t = parse_ts(x.observed_at, x.timezone)
            if not t:
                raise HTTPException(400, "observed_at is not a recognised date/time")
            obs_iso = iso_utc(t)
        lat, lon = cur["lat"], cur["lon"]
        if x.clear_coordinates:
            lat = lon = None
        elif x.lat is not None or x.lon is not None:
            if (x.lat is None) != (x.lon is None):
                raise HTTPException(400, "Give both latitude and longitude, or neither")
            lat, lon = x.lat, x.lon
        loc = re.sub(r"\s+", " ", x.location_text).strip() if x.location_text is not None else cur["location_text"]
        sr = x.source_rating or cur["source_rating"]
        ir = x.info_rating or cur["info_rating"]
        check_ratings(sr, ir)
        f = {"observed_at": obs_iso, "location_text": loc, "lat": lat, "lon": lon, "place_matched": {} if lat is not None else _place_for(loc),
             "observer": (x.observer if x.observer is not None else cur["observer"]).strip() or cur["observer"], "observation": (x.observation or cur["observation"]).strip(),
             "subject_seen": cur["subject_seen"] if x.subject_seen is None else x.subject_seen,
             "vehicles": clean_vehicles(x.vehicles) if x.vehicles is not None else cur["vehicles"], "persons": clean_persons(ents, x.persons) if x.persons is not None else cur["persons"],
             "evidence_ids": check_evidence(db, o.case_number, x.evidence_ids) if x.evidence_ids is not None else cur["evidence_ids"], "source_rating": sr, "info_rating": ir}
        row = append_entry(db, user, o, f, supersedes=old, reason=reason)
        return {"status": "amended", "entry": entry_view(row, False, x.timezone, o, None), "supersedes_seq": old.seq,
                "message": f"Recorded as entry {row.seq}, version {row.version}, superseding entry {old.seq}. The original stays in the log."}

    @router.get("/{op}/verify")
    def verify(op: str, db=Depends(ctx.get_db), user=Depends(perm("read"))):
        o = load_op(db, user, op)
        return verify_operation(db, o)

    def movement_inputs(db, o, ents):
        rows, sup = all_entries(db, o)
        cur = [r for r in rows if r.uid not in sup]
        out = []
        for r in cur:
            p = _payload(r)
            place = p["place_matched"] or {}
            out.append({"seq": r.seq, "t": parse_ts(r.observed_at, "UTC"), "lat": r.lat, "lon": r.lon, "place": place.get("name", ""), "place_lat": place.get("lat"),
                        "place_lon": place.get("lon"), "location_text": r.location_text, "subject_seen": bool(r.subject_seen),
                        "persons": [{"key": q["entity_id"] or _key(q["name"]), "name": (ents.get(q["entity_id"], {}).get("name") if q["entity_id"] else None) or q["name"], "role": q["role"]} for q in p["persons"]],
                        "vehicles": [{"reg": v["reg"], "valid": v.get("valid", True)} for v in p["vehicles"]]})
        return out

    def subject_name(o, ents):
        if o.subject_entity_id and o.subject_entity_id in ents:
            return ents[o.subject_entity_id]["name"] or "the subject"
        return o.subject_text or "the subject"

    @router.get("/{op}/movement")
    def movement(op: str, tz: str = "IST", window_minutes: int = Query(30, ge=1, le=720), radius_m: int = Query(300, ge=20, le=5000), db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        o = load_op(db, user, op)
        ents = entity_map(db, o.case_number)
        res = mv.analyse(movement_inputs(db, o, ents), tz, window_minutes, radius_m, subject_name(o, ents))
        res["operation"] = o.op_number
        res["subject"] = subject_name(o, ents)
        return res

    @router.post("/{op}/close")
    def close_operation(op: str, x: CloseIn, db=Depends(ctx.get_db), user=Depends(perm("approve"))):
        o = load_op(db, user, op)
        with _lock:
            db.refresh(o)
            if o.status == "closed":
                raise HTTPException(409, "This operation is already closed.")
            v = verify_operation(db, o)
            o.status, o.closed_by, o.closed_at = "closed", user["sub"], _now_iso()
            o.closing_remarks = re.sub(r"\s+", " ", x.remarks).strip()
            o.closure_hash = ch.closure_hash({"op_number": o.op_number, "closing_remarks": o.closing_remarks, "closed_by": o.closed_by, "closed_at": o.closed_at}, o.head_hash, o.entry_count)
            db.commit()
        det = {"op_number": o.op_number, "closure_hash": o.closure_hash, "entries": o.entry_count, "head_hash": o.head_hash, "chain_status_at_close": v["status"]}
        ctx.audit(db, user["sub"], "surveillance.operation_closed", {"case_number": o.case_number, **det})
        ctx.hooks.emit("surveillance_operation_closed", db=db, user=user, case_number=o.case_number, ref=o.op_number, detail=det)
        return {"status": "closed", "closure_hash": o.closure_hash, "chain_status": v["status"], "message": "Operation closed. It accepts no further entries."}

    # ------------------------------------------------ report
    def social_section(db, cn, entity_ids: set, ents: dict) -> dict:
        sec = {"available": False, "note": "The social-media module holds no findings linked to this operation.", "accounts": [], "flags": []}
        try:
            from backend.app.features import social as S
            links = db.query(S.SocialLink).filter_by(case_number=cn).all() if entity_ids else []
            links = [l for l in links if l.entity_id in entity_ids]
            sec["available"] = True
            accts = {a.id: a for a in db.query(S.SocialAccount).filter_by(case_number=cn).all()} if links else {}
            for l in links:
                a = accts.get(l.account_id)
                if a:
                    sec["accounts"].append({"account": f"{a.platform}:@{a.handle}", "entity": (ents.get(l.entity_id, {}).get("name") or l.entity_id), "match": l.match_type,
                                            "status": "accepted as an unverified candidate link, by " + (l.accepted_by or "?")})
            if accts:
                uids = [p.post_uid for p in db.query(S.SocialPost).filter(S.SocialPost.case_number == cn, S.SocialPost.account_id.in_(list(accts))).all()]
                by_uid = {p.post_uid: p for p in db.query(S.SocialPost).filter(S.SocialPost.case_number == cn, S.SocialPost.post_uid.in_(uids or [""])).all()}
                for f in db.query(S.SocialFlag).filter(S.SocialFlag.case_number == cn, S.SocialFlag.item_type == "post", S.SocialFlag.item_ref.in_(uids or [""]),
                                                       S.SocialFlag.review_status == "relevant").limit(15).all():
                    p = by_uid.get(f.item_ref)
                    sec["flags"].append({"account": f"{p.platform}:@{p.handle}" if p else "", "category": f.category, "phrase": f.phrase, "review": f"marked relevant by {f.reviewed_by}"})
            if sec["accounts"]:
                sec["note"] = ("Social-media accounts linked (by an officer) to persons in this operation. Links are unverified candidates until a reviewer verifies them; "
                               "flagged wording is shown only where a reviewer marked it relevant.")
        except Exception:  # noqa: BLE001  (module or tables absent: the report still works)
            sec["note"] = "The social-media module is not available in this installation."
        return sec

    def report_data(db, user, o: SurvOperation, tz: str) -> dict:
        cn = o.case_number
        ents = entity_map(db, cn)
        rows, sup = all_entries(db, o)
        cur = [r for r in rows if r.uid not in sup]
        verify = verify_operation(db, o)
        mvres = mv.analyse(movement_inputs(db, o, ents), tz, 30, 300, subject_name(o, ents))
        case = None
        try:
            from backend.app.models.models import Case
            case = db.query(Case).filter_by(case_number=cn).first()
        except Exception:  # noqa: BLE001
            pass
        views = [entry_view(r, False, tz, o, sup.get(r.uid)) for r in rows]
        cur_views = [v for v in views if v["current"]]
        obs_times = [parse_ts(v["observed_at"], "UTC") for v in cur_views if v["observed_at"]]
        persons: dict[str, dict] = {}
        for v in cur_views:
            for p in v["persons"]:
                k = p["entity_id"] or _key(p["name"])
                nm = (ents.get(p["entity_id"], {}).get("name") if p["entity_id"] else None) or p["name"]
                r = persons.setdefault(k, {"name": nm, "role": p["role"], "entries": [], "first": v["observed_at"], "last": v["observed_at"], "entity": ents.get(p["entity_id"], {}).get("name", "") if p["entity_id"] else ""})
                r["entries"].append(v["seq"])
                r["last"] = max(r["last"], v["observed_at"])
                r["first"] = min(r["first"], v["observed_at"])
        subj = subject_name(o, ents)
        subj_entries = [v["seq"] for v in cur_views if v["subject_seen"]]
        subjects = [{"name": subj + " (operation subject)", "role": "subject", "entries": ", ".join(map(str, subj_entries[:30])) or "-",
                     "first": _fmt_ts(min((v["observed_at"] for v in cur_views if v["subject_seen"]), default=""), tz), "last": _fmt_ts(max((v["observed_at"] for v in cur_views if v["subject_seen"]), default=""), tz),
                     "entity": ents.get(o.subject_entity_id, {}).get("name", "") if o.subject_entity_id else ""}]
        for r in sorted(persons.values(), key=lambda r: (r["role"] != "associate", r["name"])):
            subjects.append({"name": r["name"], "role": r["role"], "entries": ", ".join(map(str, r["entries"][:30])), "first": _fmt_ts(r["first"], tz), "last": _fmt_ts(r["last"], tz), "entity": r["entity"]})
        outside = [v["seq"] for v in cur_views if v["outside_authorised_period"]]
        rat = Counter(v["rating"] for v in cur_views)
        locs = {v["location_text"].lower() for v in cur_views}
        vehs = {x["reg"] for v in cur_views for x in v["vehicles"]}
        observers = sorted({v["observer"] for v in cur_views})
        period = f"{_fmt_ts(o.start_at, tz)} to " + (_fmt_ts(o.end_at, tz) if o.end_at else "open-ended")
        text = (f"Operation {o.op_number} was carried out under authority {o.authority_ref}. {len(cur_views)} current log entr{'y' if len(cur_views) == 1 else 'ies'} "
                f"by {len(observers)} observer(s) cover {(_fmt_ts(iso_utc(min(obs_times)), tz) + ' to ' + _fmt_ts(iso_utc(max(obs_times)), tz)) if obs_times else 'no dated period'}. "
                f"{len(views) - len(cur_views)} earlier version(s) were superseded by amendments and are kept. "
                + ("The operation is closed. " if o.status == "closed" else "The operation is still open. ")
                + ("Entries outside the authorised period: " + ", ".join(map(str, outside)) + ". " if outside else "")
                + "Nothing in this report is a finding about any person's guilt.")
        evrows = {}
        for v in views:
            for eid in v["evidence_ids"]:
                evrows.setdefault(eid, set()).add(v["seq"])
        evidence = []
        for eid, seqs in evrows.items():
            ev = db.query(Evidence).filter_by(evidence_id=eid, case_number=cn).first()
            evidence.append({"evidence_id": eid, "filename": ev.filename if ev else "(not found)", "sha256": ev.sha256 if ev else "", "integrity": ev.integrity_status if ev else "", "entries": ", ".join(map(str, sorted(seqs)))})
        log = []
        for v in views:
            note = []
            if v["version"] > 1:
                note.append(f"Amends no. {next((r.seq for r in rows if r.uid == v['supersedes_uid']), '?')} (v{v['version']}): {v['amendment_reason']}")
            if not v["current"]:
                note.append(f"Superseded by no. {v['superseded_by_seq']}")
            if v["outside_authorised_period"]:
                note.append("OUTSIDE AUTHORISED PERIOD")
            pl = v["location_text"] + (f" ({v['lat']:.5f}, {v['lon']:.5f})" if v["lat"] is not None else (f" [approx. {v['place_matched']['name']}]" if v["place_matched"] else ""))
            log.append({"seq": v["seq"], "when": v["observed_local"], "location": pl, "observer": v["observer"], "observation": v["observation"] + ("" if v["subject_seen"] else " (subject not seen)"),
                        "vehicles": ", ".join(x["reg"] for x in v["vehicles"]), "persons": "; ".join(f"{p['name']} ({p['role']})" for p in v["persons"]), "rating": v["rating"], "note": " | ".join(note)})
        amendments = [{"seq": next((r.seq for r in rows if r.uid == v["supersedes_uid"]), "?"), "superseded_by": v["seq"], "when": _fmt_ts(v["recorded_at"], tz), "by": v["recorded_by"], "reason": v["amendment_reason"]}
                      for v in views if v["supersedes_uid"]]
        vehicles = [{"reg": x["reg"], "valid_format": x["valid_format"], "seen": x["seen"], "places": ", ".join(p["place"] for p in x["places"]), "seen_with": ", ".join(p["person"] for p in x["seen_with"]),
                     "span": f"{_fmt_ts(x['first'], tz)} / {_fmt_ts(x['last'], tz)}" if x["first"] else ""} for x in mvres["vehicles"]]
        dwell = [{"label": x["label"], "observations": x["observations"], "distinct_days": x["distinct_days"], "observed": f"{x['observed_minutes']:.0f} min" if x["observed_minutes"] else "-",
                  "hours": x["typical_hour_window"] or "-", "precision": x["precision"]} for x in mvres["dwell_locations"][:10]]
        entity_ids = {o.subject_entity_id} | {p["entity_id"] for v in cur_views for p in v["persons"] if p["entity_id"]}
        entity_ids.discard("")
        social = social_section(db, cn, entity_ids, ents)
        generated = _now_iso()
        base = {"title": "Surveillance / Observation Report", "classification": "RESTRICTED: for investigation and court use. Contains personal data. Handle under the case's data-protection rules.",
                "header": {"case_number": cn, "case_title": case.title if case else "", "operation_number": o.op_number, "codename": o.codename, "subject": subj + (" (case entity)" if o.subject_entity_id else ""),
                           "objective": o.objective, "authority_text": o.authority_text, "authority_ref": o.authority_ref, "period": period, "status": o.status.capitalize(),
                           "supervising_officer": o.supervising_officer, "team": ", ".join(_jl(o.team_json, [])) or "-", "prepared_by": user["sub"], "generated_at": _fmt_ts(generated, tz), "timezone": zone_label(tz)},
                "summary": {"text": text, "entries_current": len(cur_views), "amendments": len(views) - len(cur_views), "observers": ", ".join(observers) or "-", "locations": len(locs), "vehicles": len(vehs),
                            "persons": len(persons), "outside_period": ", ".join(map(str, outside)) or "none", "ratings": ", ".join(f"{k}: {n}" for k, n in sorted(rat.items())) or "-",
                            "observed_period": (f"{_fmt_ts(iso_utc(min(obs_times)), tz)} to {_fmt_ts(iso_utc(max(obs_times)), tz)}" if obs_times else "-")},
                "log": log, "amendments": amendments, "subjects": subjects, "vehicles": vehicles,
                "movement": {"pattern_of_life": mvres["pattern_of_life"][:14], "dwell": dwell, "meetings": [m["sentence"] for m in mvres["meetings"][:8]], "explain": mvres["explain"]},
                "social": social, "evidence": evidence, "limitations": LIMITS + mvres["caveats"], "scales": _scales_list(),
                "signoff": ["Prepared by (name, rank, signature)", "Reviewed by supervising officer (name, rank, signature)", "Approving authority (name, rank, signature)"],
                "integrity": {"chain_status": verify["status"], "chain_text": ("VERIFIED: every entry's hash and link were recomputed at generation time." if verify["status"] == "verified"
                                                                              else f"BROKEN at generation time: {len(verify['problems'])} problem(s), first at entry {verify['problems'][0].get('seq')}. Do not rely on this report until explained."),
                              "entries": verify["entries_checked"], "head_hash": verify["head_hash"], "header_hash": verify["header_hash"], "closure_hash": o.closure_hash, "generated_by": user["sub"],
                              "generated_at": generated, "problems": verify["problems"],
                              "explain": ("The report content SHA-256 is the hash of the canonical JSON of everything above this statement (its 'content'). Anyone holding the JSON version can recompute it. "
                                          "The chain head is the hash of the last log entry; it changes if any entry is altered. The hash of the downloaded file itself is returned in the X-Report-File-SHA256 header and "
                                          "recorded in the audit log.")}}
        content_hash = hashlib.sha256(ch.canonical(base).encode("utf-8")).hexdigest()
        base["integrity"]["report_sha256"] = content_hash
        return base

    @router.get("/{op}/report")
    def report(op: str, format: str = Query("pdf", pattern="^(pdf|docx|json|html)$"), tz: str = "IST", db=Depends(ctx.get_db), user=Depends(perm("report"))):
        o = load_op(db, user, op)
        data = report_data(db, user, o, tz)
        if format == "json":
            body = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
            media, ext = "application/json", "json"
        elif format == "html":
            body = rg.render_html(data).encode("utf-8")
            media, ext = "text/html; charset=utf-8", "html"
        elif format == "docx":
            body = rg.render_docx(data)
            media, ext = "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
        else:
            body = rg.render_pdf(data)
            media, ext = "application/pdf", "pdf"
        fsha = hashlib.sha256(body).hexdigest()
        det = {"op_number": o.op_number, "format": format, "entries": data["integrity"]["entries"], "chain_head": data["integrity"]["head_hash"], "report_sha256": data["integrity"]["report_sha256"],
               "file_sha256": fsha, "chain_status": data["integrity"]["chain_status"]}
        ctx.audit(db, user["sub"], "surveillance.report_generated", {"case_number": o.case_number, **det})
        ctx.hooks.emit("surveillance_report_generated", db=db, user=user, case_number=o.case_number, ref=o.op_number, detail=det)
        return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="surveillance_{o.op_number}.{ext}"', "X-Report-SHA256": data["integrity"]["report_sha256"],
                                                         "X-Report-File-SHA256": fsha, "X-Chain-Head": data["integrity"]["head_hash"], "Cache-Control": "no-store",
                                                         "X-Content-Type-Options": "nosniff"})

    return router
