from __future__ import annotations

import csv
import hashlib
import unicodedata
import io
import hmac
import json
import logging
import math
import re
import threading
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ai_engine.anomaly.detector import multivariate_scores
from ai_engine.copilot.copilot import Copilot
from ai_engine.entity_resolution.resolver import knn_candidates
from ai_engine.explainability.alerts import explain_alert
from ai_engine.nlp.extractor import analyze_document
from backend.app.core.config import settings
from backend.app.models.models import (
    Alert,
    AuditEvent,
    Case,
    CaseMember,
    DocumentRecord,
    Entity,
    Event,
    Evidence,
    Relationship,
    Task,
)
from data_pipeline.ingestion import read_text
from graph_engine.engine import GraphEngine
from graph_engine.neo4j_adapter import Neo4jAdapter
from reports.report_service import investigation_report
from security import evidence_crypto
from security.access import can_access, retired_demo_cases
from security.masking import mask_entity

log = logging.getLogger("dcn.service")
BASE_DIR = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = settings.evidence_path
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
_audit_lock = threading.Lock()

# Uploads must match one of these types AND carry the matching file signature.
ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp", ".csv", ".xlsx"}
_MAGIC = {
    ".pdf": (b"%PDF",), ".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",), ".docx": (b"PK\x03\x04",), ".xlsx": (b"PK\x03\x04",),
}


def validate_upload(filename: str, data: bytes) -> str:
    """Return the safe lowercase suffix or raise ValueError. Enforces size, type and signature."""
    if not data:
        raise ValueError("The uploaded file is empty")
    if len(data) > settings.max_upload_bytes:
        raise ValueError(f"File exceeds the {settings.max_upload_mb} MB upload limit")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"File type '{suffix or 'none'}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_SUFFIXES))}")
    magic = _MAGIC.get(suffix)
    if magic and not any(data.startswith(m) for m in magic):
        raise ValueError(f"File content does not match its '{suffix}' extension")
    if suffix in {".txt", ".csv"} and b"\x00" in data[:4096]:
        raise ValueError("Text/CSV upload contains binary data")
    return suffix


def dt(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        # Explicitly handle Indian FIR-style DD/MM/YYYY dates first to avoid pandas
        # guessing warnings and ambiguous month/day interpretation.
        if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{4}", text):
            x = pd.to_datetime(text, dayfirst=True, utc=True, format="mixed")
        else:
            x = pd.to_datetime(text, utc=True, format="mixed")
        return x.to_pydatetime()
    except (ValueError, TypeError, OverflowError):
        log.debug("Unparseable date value: %r", text)
        return None


def j(value):
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def load_json(value, default):
    try:
        return json.loads(value) if value else default
    except (ValueError, TypeError):
        log.warning("Corrupt JSON in database column; using default")
        return default


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def key(value: Any) -> str:
    """Comparison key: letters, digits and combining marks of any script, lower-cased.  (An ASCII-only key made every
    Devanagari / Tamil / Bengali name collapse to the empty string, so different entities shared one id.)"""
    return "".join(c for c in str(value or "").casefold() if c.isalnum() or unicodedata.category(c).startswith("M"))


def safe_name(value: Any, max_len: int = 40) -> str:
    x = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-")
    return (x[:max_len] or "document")


def media_suffix(filename: str) -> str:
    suffix = Path(filename or "upload.bin").suffix.lower()
    return suffix if suffix in ALLOWED_SUFFIXES else ".bin"


def _rel_path(path: Path) -> str:
    """Store evidence paths relative to the project root so the folder can move."""
    try:
        return str(Path(path).resolve().relative_to(BASE_DIR))
    except ValueError:
        return str(path)


def _abs_path(stored: str) -> Path:
    p = Path(stored)
    return p if p.is_absolute() else BASE_DIR / p


def _like_case(column, case_number: str):
    """SQL prefilter for JSON-in-text columns; exact matching still happens in Python."""
    esc = case_number.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.like(f'%"{esc}"%', escape="\\")


class AppService:
    """Case-scoped service layer for the platform.

    The central rule is: extracted documents are persisted first, and nothing is
    promoted into the verified graph until an investigator submits a review decision.
    Case scoping is applied consistently across graph, analytics, timeline, evidence,
    alerts, entity resolution and Copilot queries.
    """

    STABLE_TYPES = {"PHONE", "VEHICLE", "ACCOUNT", "EMAIL", "DIGITAL_IDENTIFIER"}
    ROLE_TO_ENTITY = {
        "PERSON": "P",
        "PHONE": "PH",
        "VEHICLE": "V",
        "ACCOUNT": "A",
        "EMAIL": "EM",
        "LOCATION": "L",
        "ADDRESS": "ADDR",
        "ORGANIZATION": "ORG",
        "CASE": "FIR",
        "POLICE_STATION": "PS",
        "DISTRICT": "DIST",
        "DIGITAL_IDENTIFIER": "DID",
        "PROPERTY": "PROP",
        "LAW_SECTION": "SEC",
    }

    def __init__(self):
        # One graph per worker thread: FastAPI runs each sync request on a single pool
        # thread and every handler re-hydrates it first, so concurrent requests for
        # different cases can no longer overwrite each other's graph.
        self._local = threading.local()
        self.cop = Copilot()
        self.neo = Neo4jAdapter(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)

    @property
    def graph(self) -> GraphEngine:
        g = getattr(self._local, "graph", None)
        if g is None:
            g = self._local.graph = GraphEngine()
        return g

    # -------------------- serialization / scope --------------------

    def _entity_dict(self, e: Entity) -> dict:
        attrs = load_json(e.attributes, {})
        return {
            "external_id": e.external_id,
            "name": e.name,
            "type": e.entity_type,
            "confidence": e.confidence,
            "attributes": attrs,
            "latitude": e.latitude,
            "longitude": e.longitude,
        }

    def _relationship_dict(self, r: Relationship) -> dict:
        return {
            "id": r.id,
            "source": r.source_id,
            "target": r.target_id,
            "relation": r.relation_type,
            "confidence": r.confidence,
            "source_ref": r.source_ref,
            "event_time": r.event_time.isoformat() if r.event_time else None,
            "verification_state": r.verification_state,
            "verified_by": r.verified_by,
            "model_version": r.model_version,
            "metadata": load_json(r.metadata_json, {}),
        }

    def _event_dict(self, e: Event) -> dict:
        return {
            "event_id": e.event_id,
            "event_type": e.event_type,
            "entity_id": e.entity_id,
            "related_entity_id": e.related_entity_id,
            "event_time": e.event_time.isoformat() if e.event_time else None,
            "latitude": e.latitude,
            "longitude": e.longitude,
            "amount": e.amount,
            "duration_seconds": e.duration_seconds,
            "source_ref": e.source_ref,
            "metadata": load_json(e.metadata_json, {}),
        }

    def _document_dict(self, d: DocumentRecord) -> dict:
        structured = load_json(d.structured_json, {})
        entities = load_json(d.entities_json, [])
        relationships = load_json(d.relationships_json, [])
        english = load_json(d.english_json, {})
        quality = load_json(d.quality_json, {})
        return {
            "document_id": d.document_id,
            "filename": d.filename,
            "media_type": d.media_type,
            "document_type": d.document_type,
            "status": d.status,
            "case_number": d.case_number,
            "group_id": d.group_id,
            "evidence_id": d.evidence_id,
            "sha256": d.sha256,
            "stored_path": d.stored_path,
            "structured": structured,
            "entities": entities,
            "relationship_hints": relationships,
            "english_view": english,
            "quality": quality,
            "language_confidence": (structured.get("language", {}) or {}).get("confidence"),
            "entity_count": len(entities),
            "characters": len(d.text or ""),
            "text_preview": (d.text or "")[:5000],
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            "review_actor": d.review_actor,
            "reviewed_at": d.reviewed_at.isoformat() if d.reviewed_at else None,
            "fir_number": structured.get("fir_number", ""),
            "year": structured.get("year", ""),
        }

    def _case_entities(self, db: Session, case_number: str | None = None):
        if not case_number:
            return db.query(Entity).all()
        matched: set[str] = set()
        rows: dict[str, Entity] = {}
        # Entities explicitly tagged with the case (prefiltered in SQL).
        for e in db.query(Entity).filter(_like_case(Entity.attributes, case_number)).all():
            attrs = load_json(e.attributes, {})
            if attrs.get("case_number") == case_number or case_number in (attrs.get("case_numbers") or []):
                matched.add(e.external_id); rows[e.external_id] = e
        # Legacy seeded links carry the case on the relationship / event metadata.
        extra: set[str] = set()
        for r in db.query(Relationship).filter(_like_case(Relationship.metadata_json, case_number)).all():
            if load_json(r.metadata_json, {}).get("case_number") == case_number:
                extra.add(r.source_id); extra.add(r.target_id)
        for ev in db.query(Event).filter(_like_case(Event.metadata_json, case_number)).all():
            if load_json(ev.metadata_json, {}).get("case_number") == case_number:
                extra.add(ev.entity_id)
                if ev.related_entity_id: extra.add(ev.related_entity_id)
        missing = [x for x in extra if x and x not in rows]
        for i in range(0, len(missing), 500):
            for e in db.query(Entity).filter(Entity.external_id.in_(missing[i:i + 500])).all():
                rows[e.external_id] = e
        return list(rows.values())

    def _case_entity_ids(self, db: Session, case_number: str | None) -> set[str]:
        return {e.external_id for e in self._case_entities(db, case_number)}

    @staticmethod
    def _exact_case(entity, case_number) -> bool:
        # Strict primary-owner test. Membership in a multi-case `case_numbers` list is NOT enough:
        # two shared nodes would otherwise drag each other's unrelated relationships into this case.
        return bool(entity) and load_json(entity.attributes, {}).get("case_number") == case_number

    def _case_relationships(self, db: Session, case_number: str | None, include_candidates: bool = False):
        def state_ok(r):
            return include_candidates or r.verification_state not in {"candidate", "unreviewed"}
        if not case_number:
            return [r for r in db.query(Relationship).all() if state_ok(r)]
        entity_rows = {e.external_id: e for e in self._case_entities(db, case_number)}
        seen: dict[int, Relationship] = {}
        # (1) explicitly case-tagged relationship records
        for r in db.query(Relationship).filter(_like_case(Relationship.metadata_json, case_number)).all():
            if state_ok(r) and load_json(r.metadata_json, {}).get("case_number") == case_number:
                seen[r.id] = r
        # (2) legacy rows: BOTH endpoints must be explicitly assigned to this case
        ids = [k for k, e in entity_rows.items() if self._exact_case(e, case_number)]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in db.query(Relationship).filter(Relationship.source_id.in_(chunk)).all():
                if state_ok(r) and r.id not in seen and self._exact_case(entity_rows.get(r.target_id), case_number):
                    seen[r.id] = r
        return sorted(seen.values(), key=lambda r: r.id)

    def _case_events(self, db: Session, case_number: str | None):
        if not case_number:
            return db.query(Event).order_by(Event.event_time).all()
        seen: dict[int, Event] = {}
        for e in db.query(Event).filter(_like_case(Event.metadata_json, case_number)).all():
            if load_json(e.metadata_json, {}).get("case_number") == case_number:
                seen[e.id] = e
        entity_rows = {x.external_id: x for x in self._case_entities(db, case_number)}
        ids = [k for k, e in entity_rows.items() if self._exact_case(e, case_number)]
        for i in range(0, len(ids), 500):
            for e in db.query(Event).filter(Event.entity_id.in_(ids[i:i + 500])).all():
                if e.id in seen:
                    continue
                # Untagged legacy events count only when every endpoint is explicitly this case's.
                if self._exact_case(entity_rows.get(e.entity_id), case_number) and (
                        not e.related_entity_id or self._exact_case(entity_rows.get(e.related_entity_id), case_number)):
                    seen[e.id] = e
        return sorted(seen.values(), key=lambda e: (e.event_time is None, e.event_time))

    def _case_alerts(self, db: Session, case_number: str | None):
        q = db.query(Alert).order_by(Alert.id.desc())
        return q.filter(Alert.case_number == case_number).all() if case_number else q.all()

    def _case_evidence(self, db: Session, case_number: str | None):
        q = db.query(Evidence).order_by(Evidence.id.desc())
        return q.filter(Evidence.case_number == case_number).all() if case_number else q.all()

    def hydrate_graph(self, db: Session, case_number: str | None = None, include_candidates: bool = False):
        self.graph.clear()
        entities = self._case_entities(db, case_number)
        entity_ids = {e.external_id for e in entities}
        for e in entities:
            attrs = load_json(e.attributes, {})
            attrs.pop("type", None); attrs.pop("name", None); attrs.pop("latitude", None); attrs.pop("longitude", None)
            self.graph.add_entity(e.external_id, e.entity_type, e.name, latitude=e.latitude, longitude=e.longitude, **attrs)
        for r in self._case_relationships(db, case_number, include_candidates=include_candidates):
            if r.source_id not in entity_ids and case_number:
                continue
            if r.source_id not in self.graph.g:
                self.graph.add_entity(r.source_id)
            if r.target_id not in self.graph.g:
                self.graph.add_entity(r.target_id)
            self.graph.add_relationship(
                r.source_id, r.target_id, r.relation_type, r.confidence, r.source_ref,
                event_time=r.event_time.isoformat() if r.event_time else "",
                metadata=load_json(r.metadata_json, {}),
                verification_state=r.verification_state,
            )

    def ensure_graph(self, db: Session, case_number: str | None = None, include_candidates: bool = False):
        self.hydrate_graph(db, case_number, include_candidates)

    def graph_backend(self):
        return self.neo.status()

    # -------------------- audit / evidence --------------------

    @staticmethod
    def _audit_digest(body: dict) -> str:
        """Keyed hash: someone with write access to the DB alone cannot recompute the chain."""
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
        return hmac.new(settings.secret_key.encode(), payload, hashlib.sha256).hexdigest()

    def audit(self, db: Session, actor, action, details):
        # The lock + single transaction stops two concurrent requests from reading the
        # same previous hash and forking the chain (single-process deployment).
        with _audit_lock:
            previous = db.query(AuditEvent).order_by(AuditEvent.id.desc()).first()
            previous_hash = previous.event_hash if previous else "GENESIS"
            # SQLite stores naive UTC; hash the same representation that is persisted.
            ts = datetime.now(timezone.utc).replace(tzinfo=None)
            detail_value = details if isinstance(details, str) else j(details)
            body = {"actor": actor, "action": action, "details": detail_value,
                    "timestamp": ts.isoformat(), "previous_hash": previous_hash}
            db.add(AuditEvent(actor=actor, action=action, details=detail_value,
                              previous_hash=previous_hash, event_hash=self._audit_digest(body), created_at=ts))
            db.commit()

    def audit_events(self, db: Session, case_number: str | None = None):
        q = db.query(AuditEvent).order_by(AuditEvent.id.desc())
        if case_number:
            q = q.filter(_like_case(AuditEvent.details, case_number))
        rows = q.limit(2000).all()
        if not case_number:
            gone = retired_demo_cases(db)  # demo switched off: hide what it did (the chain itself is untouched)
            if gone:
                rows = [a for a in rows if (load_json(a.details, {}).get("case_number") or load_json(a.details, {}).get("case")) not in gone]
        if case_number:
            # Audit details are structured JSON for new events; keep only events that cite this case.
            out = []
            for a in rows:
                d = load_json(a.details, {})
                if d.get("case_number") == case_number or d.get("case") == case_number:
                    out.append(a)
            rows = out
        def shown(a):
            if a.action != "demo.disabled":
                return a.details
            d = load_json(a.details, {})  # the toggle stays in the trail; the names of the removed demo cases do not
            if isinstance(d, dict) and "cases" in d:
                d = {k: v for k, v in d.items() if k != "cases"}
                return json.dumps(d, sort_keys=True)
            return a.details
        return [{
            "id": a.id, "actor": a.actor, "action": a.action, "details": shown(a),
            "previous_hash": a.previous_hash, "event_hash": a.event_hash,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        } for a in rows]

    def verify_audit_chain(self, db: Session):
        """Recompute every link. Any mismatch is reported as tampering with the row id."""
        previous_hash = "GENESIS"
        checked = 0
        for row in db.query(AuditEvent).order_by(AuditEvent.id.asc()).all():
            if row.previous_hash != previous_hash:
                return {"verified": False, "checked": checked, "first_bad_event": row.id,
                        "reason": f"Chain broken before audit event {row.id} (missing, reordered or altered row)"}
            body = {"actor": row.actor, "action": row.action, "details": row.details,
                    "timestamp": row.created_at.isoformat() if row.created_at else "", "previous_hash": row.previous_hash}
            if not hmac.compare_digest(self._audit_digest(body), row.event_hash or ""):
                return {"verified": False, "checked": checked, "first_bad_event": row.id,
                        "reason": f"Audit event {row.id} does not match its recorded hash (altered content)"}
            previous_hash = row.event_hash
            checked += 1
        return {"verified": True, "checked": checked, "reason": "OK",
                "note": "Keyed hash chain: detects edits/deletions by anyone without the server secret. Truncation of the newest rows is only detectable against an externally stored head hash."}

    def evidence(self, db: Session, eid=None, case_number: str | None = None):
        q = db.query(Evidence)
        if eid:
            q = q.filter_by(evidence_id=eid)
        # Evidence is always investigation-scoped. A missing case must never
        # turn into a global evidence feed that can leak records between workspaces.
        if case_number:
            q = q.filter(Evidence.case_number == case_number)
        else:
            q = q.filter(Evidence.case_number.isnot(None), Evidence.case_number != "")
        rows = q.order_by(Evidence.id.desc()).all()
        return [{
            "evidence_id": e.evidence_id, "filename": e.filename, "media_type": e.media_type,
            "source": e.source, "sha256": e.sha256, "uploaded_by": e.uploaded_by,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "integrity_status": e.integrity_status, "case_number": getattr(e, "case_number", ""),
            "document_id": getattr(e, "document_id", ""),
        } for e in rows]

    def evidence_upload(self, db, data, filename, media_type, source, actor, case_number="", document_id=""):
        case_number = norm(case_number)
        if not case_number:
            raise ValueError("A case-scoped workspace is required before storing evidence")
        suffix = validate_upload(filename, data)
        digest = hashlib.sha256(data).hexdigest()
        existing = db.query(Evidence).filter_by(sha256=digest, case_number=case_number).first()
        if existing:
            self.audit(db, actor, "evidence.duplicate_upload", {"evidence_id": existing.evidence_id, "sha256": digest, "case_number": case_number, "filename": filename})
            return self.evidence(db, existing.evidence_id, case_number=case_number)
        # Keep evidence IDs isolated by investigation workspace.
        eid = "EV-" + hashlib.sha256((case_number + "|" + digest).encode("utf-8")).hexdigest()[:12].upper()
        path = EVIDENCE_DIR / (eid + suffix)
        evidence_crypto.store(path, data)  # encrypted at rest when EVIDENCE_ENCRYPTION_KEY is set; hash stays over plaintext
        # Read back from disk: the hash we vouch for is the hash of what was actually stored (decrypted).
        stored_hash = evidence_crypto.sha256_plain(path)
        obj = Evidence(
            evidence_id=eid, filename=Path(filename).name[:255], media_type=media_type or "application/octet-stream",
            source=source, sha256=digest, stored_path=_rel_path(path), uploaded_by=actor,
            case_number=case_number, document_id=document_id, integrity_status="pending",
        )
        db.add(obj); db.commit()
        self.audit(db, actor, "evidence.uploaded", {"evidence_id": eid, "sha256": digest, "case_number": case_number, "document_id": document_id})
        if stored_hash == digest:
            obj.integrity_status = "verified"; db.commit()
            self.audit(db, actor, "evidence.verified", {"evidence_id": eid, "status": "verified", "case_number": case_number, "stage": "upload-readback"})
        else:
            obj.integrity_status = "hash_mismatch"; db.commit()
            self.audit(db, actor, "evidence.verified", {"evidence_id": eid, "status": "hash_mismatch", "case_number": case_number, "stage": "upload-readback"})
        return self.evidence(db, eid, case_number=case_number)

    def verify_evidence(self, db, eid, actor, case_number=None):
        query = db.query(Evidence).filter_by(evidence_id=eid)
        if case_number:
            query = query.filter(Evidence.case_number == norm(case_number))
        e = query.first()
        if not e:
            raise ValueError("Evidence not found in the active workspace" if case_number else "Evidence not found")
        stored = _abs_path(e.stored_path)
        try:
            current = evidence_crypto.sha256_plain(stored) if stored.exists() else "MISSING"
        except evidence_crypto.EvidenceCryptoError:
            current = "UNREADABLE"  # encrypted and the key is missing/wrong: never reported as verified
        e.integrity_status = "verified" if current == e.sha256 else "hash_mismatch"
        db.commit()
        self.audit(db, actor, "evidence.verified", {"evidence_id": eid, "status": e.integrity_status, "case_number": getattr(e, "case_number", "")})
        return {"evidence_id": eid, "original_hash": e.sha256, "current_hash": current, "status": e.integrity_status}

    # -------------------- CRUD / read APIs --------------------

    def _require_case(self, db, case_number) -> str:
        cn = norm(case_number)
        if not cn or not db.query(Case).filter_by(case_number=cn).first():
            raise ValueError("Case not found")
        return cn

    def add_entity(self, db, x, actor="system", case_number=""):
        data = x.model_dump() if hasattr(x, "model_dump") else dict(x)
        cn = self._require_case(db, case_number or data.pop("case_number", "") or (data.get("attributes") or {}).get("case_number"))
        data.pop("case_number", None)
        existing = db.query(Entity).filter_by(external_id=data["external_id"]).first()
        if existing:
            return {"status": "exists", "external_id": existing.external_id}
        attrs = dict(data.get("attributes") or {})
        attrs["case_number"] = cn; attrs.setdefault("case_numbers", [cn])
        data["attributes"] = j(attrs)
        obj = Entity(**data); db.add(obj); db.commit()
        self.audit(db, actor, "entity.created", {"external_id": obj.external_id, "case_number": cn})
        return {"status": "created", "external_id": obj.external_id}

    def entities(self, db, case_number=None, role="investigator"):
        rows = self._case_entities(db, case_number)
        out = []
        for e in rows:
            out.append(mask_entity(self._entity_dict(e), role))
        return out

    def relationships(self, db, case_number=None, include_candidates=False):
        return [self._relationship_dict(r) for r in self._case_relationships(db, case_number, include_candidates)]

    def add_relationship(self, db, x, actor="system", case_number=""):
        data = x.model_dump() if hasattr(x, "model_dump") else dict(x)
        cn = self._require_case(db, case_number or data.pop("case_number", "") or (data.get("metadata") or {}).get("case_number"))
        data.pop("case_number", None)
        in_case = self._case_entity_ids(db, cn)
        for end in (data["source_id"], data["target_id"]):
            if end not in in_case:
                raise ValueError(f"Entity '{end}' does not exist in case {cn}")
        data["event_time"] = dt(data.get("event_time"))
        md = dict(data.pop("metadata", {}) or {}); md["case_number"] = cn
        data["metadata_json"] = j(md)
        obj = Relationship(**data); db.add(obj); db.commit()
        self.audit(db, actor, "relationship.created", {"source": obj.source_id, "target": obj.target_id, "relation": obj.relation_type, "source_ref": obj.source_ref, "case_number": cn})
        return {"status": "created", "id": obj.id}

    def events(self, db, case_number=None):
        return [self._event_dict(e) for e in self._case_events(db, case_number)]

    def add_event(self, db, data, actor="system", case_number=""):
        payload = data.model_dump() if hasattr(data, "model_dump") else dict(data)
        cn = self._require_case(db, case_number or payload.pop("case_number", "") or (payload.get("metadata") or {}).get("case_number"))
        payload.pop("case_number", None)
        in_case = self._case_entity_ids(db, cn)
        for end in (payload.get("entity_id"), payload.get("related_entity_id")):
            if end and end not in in_case:
                raise ValueError(f"Entity '{end}' does not exist in case {cn}")
        payload["event_time"] = dt(payload.get("event_time"))
        md = dict(payload.pop("metadata", {}) or {}); md["case_number"] = cn
        payload["metadata_json"] = j(md)
        if not db.query(Event).filter_by(event_id=payload["event_id"]).first():
            db.add(Event(**payload)); db.commit()
            self.audit(db, actor, "event.created", {"event_id": payload["event_id"], "case_number": cn})
        return {"status": "created", "event_id": payload["event_id"]}

    def cases(self, db, user=None):
        from security.access import visible_cases
        rows = visible_cases(db, user) if user else db.query(Case).order_by(Case.id.desc()).all()
        return [{"case_number": c.case_number, "title": c.title, "status": c.status, "summary": c.summary,
                 "visibility": c.visibility, "created_by": c.created_by, "is_demo": bool(c.is_demo),
                 "created_at": c.created_at.isoformat() if c.created_at else None} for c in rows]

    def add_case(self, db, x, actor="system", visibility="restricted"):
        data = x.model_dump() if hasattr(x, "model_dump") else dict(x)
        existing = db.query(Case).filter_by(case_number=data["case_number"]).first()
        if existing:
            # Never overwrite an existing case through the create endpoint.
            return {"status": "exists", "case_number": existing.case_number}
        obj = Case(**data, created_by=actor, visibility=visibility); db.add(obj); db.commit()
        self.audit(db, actor, "case.created", {"case_number": obj.case_number})
        return {"status": "created", "case_number": obj.case_number}

    def case_members(self, db, case_number):
        return [m.username for m in db.query(CaseMember).filter_by(case_number=case_number).order_by(CaseMember.username).all()]

    def tasks(self, db, case_number=None):
        q = db.query(Task)
        if case_number: q = q.filter_by(case_number=case_number)
        return [{"id": t.id, "case_number": t.case_number, "title": t.title, "status": t.status, "assignee": t.assignee} for t in q.order_by(Task.id).all()]

    def add_task(self, db, x, actor="system"):
        payload = x.model_dump() if hasattr(x, "model_dump") else dict(x)
        self._require_case(db, payload.get("case_number"))
        obj = Task(**payload); db.add(obj); db.commit(); self.audit(db, actor, "task.created", {"title": obj.title, "case_number": obj.case_number}); return {"status": "created", "id": obj.id}

    def alerts(self, db, case_number=None):
        return [{"code": a.code, "severity": a.severity, "title": a.title, "status": a.status, "confidence": a.confidence, "entity_id": a.entity_id, "case_number": getattr(a, "case_number", ""), "model_version": a.model_version, "explanation": load_json(a.explanation, []), "source_refs": load_json(a.source_refs, [])} for a in self._case_alerts(db, case_number)]

    ALERT_STATUSES = {"New", "Reviewing", "Under Review", "Reviewed", "Dismissed", "Confirmed", "Escalated", "False Positive"}

    def update_alert(self, db, code, status, actor="system"):
        a = db.query(Alert).filter_by(code=code).first()
        if not a: raise ValueError("Alert not found")
        if status not in self.ALERT_STATUSES: raise ValueError(f"Invalid alert status. Allowed: {', '.join(sorted(self.ALERT_STATUSES))}")
        a.status = status; db.commit(); self.audit(db, actor, "alert.reviewed", {"code": code, "status": status, "case_number": getattr(a, "case_number", "")})
        return {"status": "updated", "code": code, "new_status": status}

    # -------------------- documents / FIR-centric ingestion --------------------

    def _derive_case(self, db: Session, structured: dict, requested_case: str, user: dict | None = None) -> str:
        actor = (user or {}).get("sub", "system")
        requested_case = norm(requested_case)
        if requested_case:
            if not db.query(Case).filter_by(case_number=requested_case).first():
                fir = norm(structured.get("fir_number"))
                title = f"Investigation Workspace — {fir}" if fir else f"Investigation Workspace — {requested_case}"
                db.add(Case(case_number=requested_case, title=title, summary="Case workspace created from uploaded investigation documents.", status="Active", created_by=actor, visibility="restricted")); db.commit()
            return requested_case
        fir = norm(structured.get("fir_number"))
        if fir:
            derived = "CASE-FIR-" + re.sub(r"[^A-Za-z0-9]+", "-", fir).strip("-").upper()
        else:
            derived = "CASE-UPLOAD-" + hashlib.sha256((norm(structured.get("incident_description")) or "unassigned").encode()).hexdigest()[:8].upper()
        existing = db.query(Case).filter_by(case_number=derived).first()
        # A derived number that already belongs to a case this user cannot access must never
        # attach the upload to it: fork a private workspace instead.
        if existing and user and not can_access(db, user, existing):
            derived = f"{derived}-{hashlib.sha256(actor.encode()).hexdigest()[:6].upper()}"
            existing = db.query(Case).filter_by(case_number=derived).first()
        if not existing:
            title = f"FIR {fir}" if fir else "Uploaded FIR Workspace"
            db.add(Case(case_number=derived, title=title, summary="Auto-created FIR-centric case workspace.", status="Active", created_by=actor, visibility="restricted")); db.commit()
        return derived

    def _candidate_id(self, document_id, index):
        return f"CAND-{document_id}-{index:03d}"

    def _candidate_entity_id(self, document_id, case_number, typ, text, index):
        t = str(typ or "ENTITY").upper()
        value = norm(text)
        if t in self.STABLE_TYPES or t in {"LOCATION", "ADDRESS", "ORGANIZATION", "POLICE_STATION", "DISTRICT"}:
            digest = hashlib.sha1((t + "|" + key(value)).encode()).hexdigest()[:10].upper()
            return f"{self.ROLE_TO_ENTITY.get(t, 'ENT')}-{digest}"
        prefix = self.ROLE_TO_ENTITY.get(t, "ENT")
        digest = hashlib.sha1((document_id + "|" + t + "|" + str(index)).encode()).hexdigest()[:8].upper()
        return f"{prefix}-{digest}"

    def _link_fir_auto_identifiers(self, db: Session, case_number: str, actor: str):
        docs = db.query(DocumentRecord).filter_by(case_number=case_number, status="Verified").all()
        if len(docs) < 2:
            return 0
        fir_nodes: dict[str, set[str]] = {}
        for d in docs:
            structured = load_json(d.structured_json, {})
            fir = norm(structured.get("fir_number"))
            if not fir:
                continue
            fir_id = self._fir_entity_id(case_number, fir)
            shared = set()
            for e in self._case_entities(db, case_number):
                attrs = load_json(e.attributes, {})
                if d.document_id in (attrs.get("source_documents") or ([attrs.get("document_id")] if attrs.get("document_id") else [])) and e.entity_type in self.STABLE_TYPES:
                    shared.add(e.external_id)
            fir_nodes[fir_id] = shared
        created = 0
        ids = list(fir_nodes)
        for i in range(len(ids)):
            for k in range(i + 1, len(ids)):
                shared = sorted(fir_nodes[ids[i]] & fir_nodes[ids[k]])
                if not shared:
                    continue
                code = f"{ids[i]}|{ids[k]}|SHARED|{shared[0]}"
                if db.query(Relationship).filter_by(source_id=ids[i], target_id=ids[k], relation_type="INTERLINKED_VIA", source_ref=code).first():
                    continue
                db.add(Relationship(
                    source_id=ids[i], target_id=ids[k], relation_type="INTERLINKED_VIA", confidence=0.97,
                    source_ref=code, verification_state="verified", verified_by=actor,
                    model_version="fir-link-rules-v1", metadata_json=j({"case_number": case_number, "bridge_entities": shared}),
                )); created += 1
        if created:
            db.commit(); self.audit(db, actor, "fir.interlink.updated", {"case_number": case_number, "links_created": created})
        return created

    def _fir_entity_id(self, case_number, fir_number):
        digest = hashlib.sha1((case_number + "|" + fir_number).encode()).hexdigest()[:10].upper()
        return f"FIR-{digest}"

    def _get_or_create_entity(self, db, external_id, name, entity_type, confidence, attrs, lat=None, lon=None):
        obj = db.query(Entity).filter_by(external_id=external_id).first()
        if obj:
            merged = load_json(obj.attributes, {})
            incoming = {k: v for k, v in attrs.items() if v not in (None, "", [], {})}
            # An entity shared by several cases must keep membership in all of them.
            cases = list(dict.fromkeys(list(merged.get("case_numbers") or []) + list(incoming.get("case_numbers") or [])
                                       + [x for x in (merged.get("case_number"), incoming.get("case_number")) if x]))
            if merged.get("case_number"):
                incoming.pop("case_number", None)  # first owner stays primary
            merged.update(incoming)
            if cases: merged["case_numbers"] = cases
            obj.attributes = j(merged)
            obj.confidence = max(float(obj.confidence or 0), float(confidence or 0))
            if lat is not None: obj.latitude = lat
            if lon is not None: obj.longitude = lon
            db.commit()
            return obj
        obj = Entity(external_id=external_id, name=name, entity_type=entity_type, confidence=float(confidence or 0), attributes=j(attrs), latitude=lat, longitude=lon)
        db.add(obj); db.commit(); return obj

    def _build_candidates(self, document_id: str, actual_case: str, evidence_id: str, result: dict):
        """Turn analyser output into saved review candidates and relationship hints (shared by extract and reprocess)."""
        candidates = []
        text_entity_index = {}
        for idx, ent in enumerate(result.get("entities", []), start=1):
            raw_type = str(ent.get("type") or "ENTITY").upper()
            display_type = raw_type
            value = norm(ent.get("text"))
            if not value: continue
            cid = self._candidate_id(document_id, idx)
            eid = self._candidate_entity_id(document_id, actual_case, raw_type, value, idx)
            candidate = dict(ent)
            candidate.update({"candidate_id": cid, "candidate_external_id": eid, "case_number": actual_case, "document_id": document_id})
            candidates.append(candidate)
            text_entity_index.setdefault(key(value), []).append(candidate)

        rel_hints = []
        for rel in result.get("relationship_hints", []):
            item = dict(rel)
            source_text = norm(item.get("source"))
            target_text = norm(item.get("target"))
            src = text_entity_index.get(key(source_text), [])
            tgt = text_entity_index.get(key(target_text), [])
            pick = lambda lst: next((c for c in lst if c.get("graph_worthy", True)), lst[0])
            if src: c = pick(src); item["source_candidate_id"] = c["candidate_id"]; item["source_id"] = c["candidate_external_id"]
            if tgt: c = pick(tgt); item["target_candidate_id"] = c["candidate_id"]; item["target_id"] = c["candidate_external_id"]
            item.update({"document_id": document_id, "case_number": actual_case, "source_ref": evidence_id, "verification_state": "candidate"})
            rel_hints.append(item)

        return candidates, rel_hints

    def extract_document(self, db: Session, data: bytes, filename: str, language="auto", case_number="", group_id="", media_type="application/octet-stream", actor="system", user: dict | None = None):
        suffix = validate_upload(filename, data)
        digest = hashlib.sha256(data).hexdigest()
        existing = db.query(DocumentRecord).filter_by(sha256=digest, case_number=norm(case_number)).first()
        if existing:
            return self._document_dict(existing)

        text, meta = read_text(data, filename, language=language, return_meta=True)
        result = analyze_document(text, meta)
        structured = result.get("structured", {})
        actual_case = self._derive_case(db, structured, case_number, user or {"sub": actor, "role": "investigator"})
        group = norm(group_id) or f"GRP-{hashlib.sha1((actual_case + '|default').encode()).hexdigest()[:10].upper()}"
        document_id = "DOC-" + hashlib.sha256((actual_case + "|" + digest).encode()).hexdigest()[:12].upper()
        # Evidence IDs are case-scoped as well as content-addressed. A globally
        # content-only ID would collide when identical bytes are uploaded into two
        # separate investigation workspaces. The full SHA-256 remains the integrity
        # fingerprint; this short ID is only the UI/database handle.
        evidence_id = "EV-" + hashlib.sha256((actual_case + "|" + digest).encode("utf-8")).hexdigest()[:12].upper()
        evidence_path = EVIDENCE_DIR / (evidence_id + suffix)
        evidence_crypto.store(evidence_path, data)

        # Persist evidence and document atomically enough for the prototype.
        evidence = db.query(Evidence).filter_by(evidence_id=evidence_id).first()
        if not evidence:
            evidence = Evidence(evidence_id=evidence_id, filename=filename, media_type=media_type, source="document-ingestion", sha256=digest, stored_path=_rel_path(evidence_path), uploaded_by=actor, case_number=actual_case, document_id=document_id, integrity_status="pending")
            db.add(evidence); db.flush()
        else:
            evidence.case_number = actual_case; evidence.document_id = document_id

        candidates, rel_hints = self._build_candidates(document_id, actual_case, evidence_id, result)

        quality = result.get("quality", {})
        quality.setdefault("workflow", "AI_PROPOSES_HUMAN_VERIFIES_GRAPH_ACCEPTS")
        quality.setdefault("case_scoped", True)
        quality.setdefault("document_sha256", digest)
        quality.setdefault("evidence_id", evidence_id)

        record = DocumentRecord(
            document_id=document_id, case_number=actual_case, group_id=group,
            filename=filename, media_type=media_type, document_type=result.get("document_type", "INVESTIGATION_DOCUMENT"),
            status="Pending Review", sha256=digest, stored_path=_rel_path(evidence_path), evidence_id=evidence_id,
            text=text or "", structured_json=j(structured), entities_json=j(candidates), relationships_json=j(rel_hints),
            english_json=j(result.get("english_view", {})), quality_json=j(quality),
        )
        db.add(record); db.commit()
        self.audit(db, actor, "document.extracted", {"document_id": document_id, "case_number": actual_case, "group_id": group, "evidence_id": evidence_id, "filename": filename})
        out = self._document_dict(record)
        out["ocr_note"] = "Local OCR uses preprocessing + Tesseract. Findings remain review-first and case-scoped."
        return out


    def related_firs(self, db, case_number: str, document_id: str | None = None):
        """Rank other verified FIR documents in the same workspace by shared evidence keys.

        Uses the same evidence-key resolver as the dedicated FIR Link Center so the
        per-document view and the workspace-wide view cannot disagree simply because
        one structured field was missed by OCR/NLP.
        """
        docs = [d for d in db.query(DocumentRecord).filter_by(case_number=case_number, status="Verified").order_by(DocumentRecord.id).all()]
        selected = next((d for d in docs if d.document_id == document_id), None) if document_id else None
        if selected is None and docs:
            selected = docs[0]
        if not selected:
            return []

        case_entities = self._case_entities(db, case_number)
        base = self._fir_evidence_keys(db, selected, case_entities=case_entities)
        graph_links = [r for r in self.relationships(db, case_number) if r.get("relation") == "INTERLINKED_VIA" and r.get("verification_state") == "verified"]
        link_map = {frozenset((r.get("source"), r.get("target"))): r for r in graph_links}
        weights={"phone":0.36,"vehicle":0.28,"serial":0.14,"account":0.08,"email":0.06,"person":0.04,"place":0.03,"section":0.01}
        results = []
        selected_st = load_json(selected.structured_json,{})
        selected_fir = selected_st.get("fir_number") or selected.document_id
        selected_fir_id = self._fir_entity_id(case_number, selected_fir)
        for d in docs:
            if d.document_id == selected.document_id:
                continue
            other = self._fir_evidence_keys(db, d, case_entities=case_entities)
            reasons=[]; score=0.0
            for typ,w in weights.items():
                shared=sorted(set(base.get(typ,[])) & set(other.get(typ,[])))
                if shared:
                    score += w
                    reasons.append({"type":typ,"shared":shared})
            if score <= 0:
                continue
            st=load_json(d.structured_json,{})
            fir_no=st.get("fir_number") or d.document_id
            pair=frozenset((selected_fir_id,self._fir_entity_id(case_number,fir_no)))
            link=link_map.get(pair)
            results.append({
                "document_id":d.document_id, "filename":d.filename, "fir_number":fir_no,
                "match_score":round(min(score,1.0),3),
                "match_strength":"Strong" if score>=.55 else ("Moderate" if score>=.30 else "Weak"),
                "shared_key_types":len(reasons), "shared_key_count":sum(len(x.get("shared",[])) for x in reasons),
                "reasons":reasons, "interlink_status":"verified graph link" if link else "candidate cross-FIR match",
                "graph_link":link, "status":d.status,
            })
        results.sort(key=lambda x:(-x["match_score"], x["filename"]))
        return results

    def documents(self, db, case_number=None, group_id=None):
        q = db.query(DocumentRecord)
        if case_number: q = q.filter_by(case_number=case_number)
        if group_id: q = q.filter_by(group_id=group_id)
        return [self._document_dict(x) for x in q.order_by(DocumentRecord.id.desc()).all()]

    def document(self, db, document_id):
        obj = db.query(DocumentRecord).filter_by(document_id=document_id).first()
        if not obj: raise ValueError("Document not found")
        return self._document_dict(obj)

    def _resolve_candidate_to_entity(self, db, doc: DocumentRecord, candidate: dict, action: str, actor: str):
        value = norm(candidate.get("name") or candidate.get("text"))
        typ = str(candidate.get("entity_type") or candidate.get("type") or "ENTITY").upper()
        confidence = float(candidate.get("confidence") or 0.8)
        attrs = dict(candidate.get("attributes") or {})
        attrs.update({"case_number": doc.case_number, "document_id": doc.document_id, "group_id": doc.group_id, "evidence_id": doc.evidence_id, "source": "FIR_DOCUMENT", "role": candidate.get("role", "")})
        # FIR numbers are case identifiers, not ordinary generic CASE nodes. Use one
        # deterministic FIR node per (workspace, FIR number) so repeated review/reprocess
        # never creates duplicate case nodes.
        if typ == "CASE" and str(candidate.get("role", "")).upper() == "FIR_NUMBER":
            fir_no = value
            ext_fir = self._fir_entity_id(doc.case_number, fir_no)
            self._get_or_create_entity(
                db, ext_fir, f"FIR {fir_no}", "CASE", confidence,
                {**attrs, "fir_number": fir_no, "source": "FIR_DOCUMENT"}
            )
            return ext_fir
        attrs.setdefault("source_documents", [doc.document_id])
        attrs.setdefault("case_numbers", [doc.case_number])
        ext = norm(candidate.get("external_id") or candidate.get("candidate_external_id"))
        if action == "link":
            if not ext or not db.query(Entity).filter_by(external_id=ext).first():
                raise ValueError(f"Cannot link entity '{value}': existing external_id was not found")
            # Link provenance to the existing entity without replacing its canonical attributes.
            obj = db.query(Entity).filter_by(external_id=ext).first()
            current = load_json(obj.attributes, {}); current.setdefault("source_documents", [])
            if doc.document_id not in current["source_documents"]: current["source_documents"].append(doc.document_id)
            current.setdefault("case_numbers", [])
            if doc.case_number not in current["case_numbers"]: current["case_numbers"].append(doc.case_number)
            obj.attributes = j(current); db.commit(); return obj.external_id
        if action == "ignore": return None
        if typ in self.STABLE_TYPES:
            ext = ext or self._candidate_entity_id(doc.document_id, doc.case_number, typ, value, 1)
            existing = db.query(Entity).filter_by(external_id=ext).first()
            if existing:
                current = load_json(existing.attributes, {})
                docs = list(current.get("source_documents", []))
                if doc.document_id not in docs: docs.append(doc.document_id)
                cases = list(current.get("case_numbers", []))
                if doc.case_number not in cases: cases.append(doc.case_number)
                current.update({"source_documents": docs, "case_numbers": cases})
                current.update({k:v for k,v in attrs.items() if k not in {"document_id","case_number","source_documents","case_numbers"}})
                current["last_document_id"] = doc.document_id
                existing.attributes = j(current); db.commit(); return ext
        ext = ext or self._candidate_entity_id(doc.document_id, doc.case_number, typ, value, int(candidate.get("index") or 1))
        self._get_or_create_entity(db, ext, value, typ, confidence, attrs)
        return ext

    def review_document(self, db, document_id, payload, actor):
        doc = db.query(DocumentRecord).filter_by(document_id=document_id).first()
        if not doc: raise ValueError("Document not found")
        status = str(payload.get("status", "Pending Review")).strip().title()
        if status not in {"Verified", "Rejected"}:
            raise ValueError("status must be Verified or Rejected")
        if status == "Rejected":
            doc.status = status; doc.review_actor = actor; doc.reviewed_at = datetime.now(timezone.utc); doc.updated_at = doc.reviewed_at; db.commit()
            self.audit(db, actor, "document.rejected", {"document_id": document_id, "case_number": doc.case_number})
            return {"status": "rejected", "document_id": document_id, "case_number": doc.case_number}

        entities_from_ui = payload.get("entities") or []
        saved_candidates = load_json(doc.entities_json, [])
        by_candidate = {x.get("candidate_id"): x for x in saved_candidates if x.get("candidate_id")}
        by_ext = {x.get("candidate_external_id"): x for x in saved_candidates if x.get("candidate_external_id")}
        resolved: dict[str, str] = {}
        accepted = []
        for action in entities_from_ui:
            cand_id = action.get("candidate_id")
            source = by_candidate.get(cand_id) or by_ext.get(action.get("candidate_external_id")) or action
            source = {**source, **action}
            act = str(action.get("action") or "create").lower()
            eid = self._resolve_candidate_to_entity(db, doc, source, act, actor)
            if eid:
                resolved[cand_id or source.get("candidate_external_id") or source.get("text") or source.get("name")] = eid
                accepted.append(eid)

        # Backward-compatible behavior: if the UI sends no entity actions, verify all candidates.
        if not entities_from_ui:
            for c in saved_candidates:
                eid = self._resolve_candidate_to_entity(db, doc, c, "create", actor)
                if eid: resolved[c.get("candidate_id") or c.get("candidate_external_id") or c.get("text")] = eid; accepted.append(eid)

        saved_rels = load_json(doc.relationships_json, [])
        relationship_actions = payload.get("relationships") or []
        source_rels = relationship_actions if relationship_actions else saved_rels
        created_relationships = []
        for rel in source_rels:
            if isinstance(rel, dict) and rel.get("action") == "ignore":
                continue
            src_key = rel.get("source_candidate_id") or rel.get("source_id") or rel.get("source")
            tgt_key = rel.get("target_candidate_id") or rel.get("target_id") or rel.get("target")
            source_id = resolved.get(src_key, src_key)
            target_id = resolved.get(tgt_key, tgt_key)
            if not source_id or not target_id or source_id == target_id:
                continue
            # Match textual IDs from saved candidate list.
            if source_id not in {e.external_id for e in self._case_entities(db, doc.case_number)}:
                candidates = by_candidate.get(src_key) or by_ext.get(src_key)
                if candidates: source_id = resolved.get(candidates.get("candidate_id"), source_id)
            if target_id not in {e.external_id for e in self._case_entities(db, doc.case_number)}:
                candidates = by_candidate.get(tgt_key) or by_ext.get(tgt_key)
                if candidates: target_id = resolved.get(candidates.get("candidate_id"), target_id)
            if not source_id or not target_id: continue
            relation_type = str(rel.get("relation_type") or rel.get("relation") or "ASSOCIATED").upper()
            exists = db.query(Relationship).filter_by(source_id=source_id, target_id=target_id, relation_type=relation_type, source_ref=doc.evidence_id).first()
            if exists:
                exists.verification_state = "verified"; exists.verified_by = actor
                continue
            md = dict(rel.get("metadata") or {})
            md.update({"case_number": doc.case_number, "document_id": doc.document_id, "group_id": doc.group_id, "evidence_id": doc.evidence_id, "evidence_text": rel.get("evidence", ""), "rationale": rel.get("rationale", "")})
            db.add(Relationship(source_id=source_id, target_id=target_id, relation_type=relation_type, confidence=float(rel.get("confidence") or .75), source_ref=doc.evidence_id, event_time=dt(rel.get("event_time")), metadata_json=j(md), verification_state="verified", verified_by=actor, model_version="fir-review-v1"))
            created_relationships.append({"source": source_id, "target": target_id, "relation": relation_type})

        # Always attach the FIR node, its station/location, and extracted people to the FIR for traceability.
        structured = load_json(doc.structured_json, {})
        fir_no = norm(structured.get("fir_number")) or doc.document_id
        fir_id = self._fir_entity_id(doc.case_number, fir_no)
        fir_attrs = {"case_number": doc.case_number, "document_id": doc.document_id, "group_id": doc.group_id, "evidence_id": doc.evidence_id, "fir_number": fir_no, "source": "FIR_DOCUMENT"}
        self._get_or_create_entity(db, fir_id, f"FIR {fir_no}", "CASE", .99, fir_attrs)
        for eid in set(accepted):
            if eid == fir_id: continue
            md = {"case_number": doc.case_number, "document_id": doc.document_id, "evidence_id": doc.evidence_id}
            if not db.query(Relationship).filter_by(source_id=eid, target_id=fir_id, relation_type="MENTIONED_IN", source_ref=doc.evidence_id).first():
                db.add(Relationship(source_id=eid, target_id=fir_id, relation_type="MENTIONED_IN", confidence=.99, source_ref=doc.evidence_id, metadata_json=j(md), verification_state="verified", verified_by=actor, model_version="fir-review-v1"))

        # Create timeline events from structured detection plus key FIR milestones.
        # A verified FIR should produce a useful timeline even when its narrative does not
        # contain one of the keyword-based event markers.
        event_rows = list(structured.get("detected_events") or [])
        if structured.get("fir_date") or structured.get("fir_time"):
            event_rows.append({"type":"FIR_REGISTERED","date":structured.get("fir_date"),"time_from":structured.get("fir_time"),"confidence":.99,"rationale":"FIR header"})
        if structured.get("occurrence_date") or structured.get("place_of_occurrence"):
            event_rows.append({"type":"OCCURRENCE_RECORDED","date":structured.get("occurrence_date"),"time_from":structured.get("occurrence_time_from"),"time_to":structured.get("occurrence_time_to"),"confidence":.93,"rationale":"FIR occurrence section"})
        if structured.get("investigating_officer"):
            event_rows.append({"type":"INVESTIGATION_ASSIGNED","date":structured.get("fir_date"),"time_from":structured.get("fir_time"),"confidence":.91,"rationale":"Investigating officer field"})
        seen_event_types=set()
        for idx, ev in enumerate(event_rows, start=1):
            etype=str(ev.get("type") or "FIR_EVENT").upper()
            event_key=(etype, str(ev.get("date") or structured.get("occurrence_date") or ""), str(ev.get("time_from") or ""))
            if event_key in seen_event_types: continue
            seen_event_types.add(event_key)
            ev_id = f"EVT-{doc.document_id}-{idx:02d}"
            if db.query(Event).filter_by(event_id=ev_id).first(): continue
            db.add(Event(event_id=ev_id, event_type=etype, entity_id=fir_id, related_entity_id="", event_time=dt(ev.get("date") or structured.get("occurrence_date")), source_ref=doc.evidence_id, metadata_json=j({"case_number": doc.case_number, "document_id": doc.document_id, "time_from": ev.get("time_from"), "time_to": ev.get("time_to"), "place": structured.get("place_of_occurrence",""), "rationale": ev.get("rationale","Detected from FIR narrative"), "confidence": ev.get("confidence", .8)})))

        doc.status = status; doc.review_actor = actor; doc.reviewed_at = datetime.now(timezone.utc); doc.updated_at = doc.reviewed_at
        db.commit()
        linked = self._link_fir_auto_identifiers(db, doc.case_number, actor)
        self.ensure_graph(db, doc.case_number)
        self.audit(db, actor, "document.verified", {"document_id": document_id, "case_number": doc.case_number, "accepted_entities": len(set(accepted)), "relationships_created": len(created_relationships), "fir_links_created": linked})
        return {"status": "verified", "document_id": document_id, "case_number": doc.case_number, "fir_entity_id": fir_id, "accepted_entities": len(set(accepted)), "relationships_created": len(created_relationships), "interlinked_firs": linked}

    def reprocess_document(self, db, document_id, actor="system"):
        doc = db.query(DocumentRecord).filter_by(document_id=document_id).first()
        if not doc: raise ValueError("Document not found")
        stored = Path(doc.stored_path)
        if not stored.is_absolute(): stored = BASE_DIR / stored
        if not stored.exists(): raise ValueError("Original evidence file is missing")
        try: data = evidence_crypto.read_plain(stored)
        except evidence_crypto.EvidenceCryptoError as exc: raise ValueError(f"Original evidence file cannot be decrypted: {exc}")
        text, meta = read_text(data, doc.filename, language="auto", return_meta=True)
        result = analyze_document(text, meta)
        doc.text = text or ""; doc.document_type = result.get("document_type", doc.document_type)
        candidates, rel_hints = self._build_candidates(doc.document_id, doc.case_number, doc.evidence_id, result)
        doc.entities_json = j(candidates); doc.relationships_json = j(rel_hints)
        doc.structured_json = j(result.get("structured", {})); doc.english_json = j(result.get("english_view", {})); doc.quality_json = j(result.get("quality", {}))
        doc.status = "Pending Review"; doc.updated_at = datetime.now(timezone.utc)
        db.commit(); self.audit(db, actor, "document.reprocessed", {"document_id": document_id, "case_number": doc.case_number})
        return self._document_dict(doc)

    # -------------------- structured-table ingestion --------------------

    def ingest_table(self, db, data, filename, actor="system", case_number=""):
        suffix = validate_upload(filename, data)
        if suffix not in {".csv", ".xlsx"}:
            raise ValueError("Only CSV/XLSX table ingestion is supported")
        raw = io.BytesIO(data)
        df = pd.read_csv(raw) if filename.lower().endswith(".csv") else pd.read_excel(raw)
        df = df.fillna("")
        cols = {str(c).strip().lower(): c for c in df.columns}
        created_entities = created_relationships = created_events = 0

        def val(row, *names):
            for name in names:
                c = cols.get(name)
                if c is not None and str(row.get(c, "")).strip(): return str(row.get(c, "")).strip()
            return ""
        def num(row, *names):
            x = val(row, *names)
            try: return float(x)
            except ValueError: return None

        active_case = self._require_case(db, case_number)
        seen_ids: set[str] = set()
        for idx, row in df.iterrows():
            source = val(row, "source", "source_id", "caller", "from", "sender")
            target = val(row, "target", "target_id", "callee", "to", "receiver")
            relation = val(row, "relation", "relation_type", "relationship") or "ASSOCIATED_WITH"
            event_time = val(row, "date", "datetime", "timestamp", "event_time", "time")
            amount = num(row, "amount", "value", "transaction_amount")
            duration = num(row, "duration", "duration_seconds", "call_duration")
            metadata = {str(k): str(v) for k, v in row.items()}
            metadata.update({"case_number": active_case, "source_file": filename})
            if source and target:
                # Create lightweight entity nodes so table-only records still work in the graph.
                for raw_id, type_col in ((source, "source_type"), (target, "target_type")):
                    typ = (val(row, type_col) or self._guess_entity_type(raw_id)).upper()
                    existed = db.query(Entity).filter_by(external_id=raw_id).first() is not None
                    self._get_or_create_entity(db, raw_id, raw_id, typ, num(row, "confidence") or .85, {"case_number": active_case, "case_numbers": [active_case], "source_file": filename}, None, None)
                    if not existed and raw_id not in seen_ids:
                        created_entities += 1
                    seen_ids.add(raw_id)
                obj = Relationship(source_id=source, target_id=target, relation_type=relation.upper(), confidence=num(row, "confidence") or .85, source_ref=filename, event_time=dt(event_time), metadata_json=j(metadata), verification_state="verified", verified_by=actor, model_version="table-ingest-v1")
                db.add(obj); db.commit(); created_relationships += 1
                if event_time or amount is not None or duration is not None:
                    typ = "TRANSACTION" if amount is not None else ("COMMUNICATION" if duration is not None else "RELATIONSHIP_EVENT")
                    db.add(Event(event_id=f"TBL-{hashlib.sha1((active_case+filename+str(idx)).encode()).hexdigest()[:12]}", event_type=typ, entity_id=source, related_entity_id=target, event_time=dt(event_time), amount=amount, duration_seconds=duration, source_ref=filename, metadata_json=j(metadata))); db.commit(); created_events += 1
        self.ensure_graph(db, active_case)
        self.audit(db, actor, "table.ingested", {"filename": filename, "case_number": active_case, "rows": len(df)})
        return {"status": "ok", "rows": len(df), "created_entities": created_entities, "created_relationships": created_relationships, "created_events": created_events, "case_number": active_case}

    @staticmethod
    def _guess_entity_type(raw: str) -> str:
        """Conservative typing from the *value*, not its first letter. Unknown -> ENTITY."""
        v = raw.strip()
        compact = re.sub(r"[\s-]", "", v)
        if re.fullmatch(r"(?:\+91)?[6-9]\d{9}", compact): return "PHONE"
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v): return "EMAIL"
        if re.fullmatch(r"[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{3,4}", compact.upper()): return "VEHICLE"
        if re.fullmatch(r"\d{9,18}", compact) or re.fullmatch(r"(?:ACCT?|AC)[-_]?\d{4,}", compact.upper()): return "ACCOUNT"
        up = v.upper()
        if re.fullmatch(r"P[-_]?\d+", up): return "PERSON"
        if re.fullmatch(r"A[-_]?\d+", up): return "ACCOUNT"
        if re.fullmatch(r"V[-_]?\d+", up): return "VEHICLE"
        return "ENTITY"

    # -------------------- analytics / intelligence --------------------

    def cdr_analytics(self, db, case_number=None):
        events = [e for e in self.events(db, case_number) if e["event_type"] == "COMMUNICATION"]
        pair_counts = {}
        durations = {}
        late_hour = 0
        by_entity = {}
        for e in events:
            a,b = e["entity_id"],e["related_entity_id"]
            pair = tuple(sorted((a,b)))
            pair_counts[pair] = pair_counts.get(pair,0)+1
            durations[pair] = durations.get(pair,0)+float(e["duration_seconds"] or 0)
            by_entity.setdefault(a, []).append(e); by_entity.setdefault(b, []).append(e)
            if e.get("event_time"):
                try:
                    hour = pd.to_datetime(e["event_time"], utc=True).hour
                    late_hour += int(hour >= 22 or hour <= 5)
                except (ValueError, TypeError): log.debug("Bad CDR timestamp %r", e.get("event_time"))
        rows = [{"source":a,"target":b,"calls":pair_counts[(a,b)],"duration":durations[(a,b)]} for a,b in sorted(pair_counts, key=lambda p: pair_counts[p], reverse=True)[:25]]
        total_duration_seconds = sum(durations.values())
        top_entities = [{"entity_id":k,"calls":len(v),"duration_seconds":sum(float(x.get("duration_seconds") or 0) for x in v)} for k,v in sorted(by_entity.items(), key=lambda kv: len(kv[1]), reverse=True)[:15]]
        return {
            # Canonical API names
            "call_count": len(events),
            "unique_pairs": len(pair_counts),
            "late_hour_calls": late_hour,
            "total_duration_seconds": total_duration_seconds,
            "top_pairs": rows,
            "top_entities": top_entities,
            # UI-compatible aliases retained for backward compatibility
            "total_calls": len(events),
            "top_contacts": rows,
        }

    def financial_analytics(self, db, case_number=None):
        tx = [e for e in self.events(db, case_number) if e["event_type"] == "TRANSACTION"]
        edges = {}
        for e in tx:
            key_pair=(e["entity_id"],e["related_entity_id"]); edges[key_pair]=edges.get(key_pair,0)+float(e["amount"] or 0)
        out_edges={}
        for (a,b),amt in edges.items(): out_edges.setdefault(a,[]).append((b,amt))
        chains=[]
        for (a,b),amt in sorted(edges.items(), key=lambda x:x[1], reverse=True)[:50]:
            for d,amt2 in out_edges.get(b,[]):
                if d!=a: chains.append({"source":a,"middle":b,"target":d,"amount":round(min(amt,amt2),2)})
            if len(chains)>=200: break
        circular=[{"account":a,"intermediary":b,"amount":round(min(amt,edges.get((b,a),0)),2)} for (a,b),amt in edges.items() if (b,a) in edges and a<b]
        return {"transaction_count":len(tx),"total_amount":round(sum(float(e["amount"] or 0) for e in tx),2),"unique_pairs":len(edges),"high_value_transactions":sorted(tx,key=lambda x:float(x["amount"] or 0),reverse=True)[:15],"candidate_chains":chains[:20],"candidate_circular_flows":circular[:20]}

    def network_evolution(self, db, case_number=None):
        rels=self.relationships(db, case_number); buckets={}
        for r in rels:
            if not r.get("event_time"): continue
            day=str(pd.to_datetime(r["event_time"]).date()); buckets.setdefault(day,{"nodes":set(),"relationships":0}); buckets[day]["relationships"]+=1; buckets[day]["nodes"].update([r["source"],r["target"]])
        return {"snapshots":[{"date":d,"active_nodes":len(v["nodes"]),"relationships":v["relationships"]} for d,v in sorted(buckets.items())]}

    def recommendations(self, db, case_number=None):
        self.ensure_graph(db, case_number); central=self.graph.centrality(); candidates=sorted(central.get("betweenness",{}).items(),key=lambda x:x[1],reverse=True)[:5]; alerts=sorted(self.alerts(db,case_number),key=lambda x:x["confidence"],reverse=True)[:5]
        rec=[{"priority":"High","action":f"Review bridge entity {eid}","reason":"High betweenness centrality; inspect supporting relationships and source records.","entity_id":eid} for eid,_ in candidates]
        rec += [{"priority":"High" if a["confidence"]>=.8 else "Review","action":f"Review signal {a['code']}","reason":a["title"],"entity_id":a["entity_id"]} for a in alerts]
        return {"recommendations":rec[:10]}

    def entity_candidates(self, db, value, entity_type=None, case_number=None):
        rows=self.entities(db,case_number); rows=[r for r in rows if not entity_type or r["type"].upper()==entity_type.upper()]
        return knn_candidates(value,rows,"name",threshold=.60)

    def run_analytics(self, db, case_number=None, actor="system"):
        self.ensure_graph(db, case_number); entities=self.entities(db,case_number); events=self.events(db,case_number); central=self.graph.centrality(); rows=[]
        for e in entities:
            eid=e["external_id"]; deg=self.graph.g.degree(eid) if eid in self.graph.g else 0; bet=central.get("betweenness",{}).get(eid,0); pr=central.get("pagerank",{}).get(eid,0); ev=central.get("eigenvector",{}).get(eid,0); related=[x for x in events if x["entity_id"]==eid or x["related_entity_id"]==eid]; tx=sum(float(x["amount"] or 0) for x in related); bursts=len(related); rows.append([deg,bet,pr,ev,tx,bursts])
        if not rows: return {"alerts_created":0,"scores":[]}
        iso, lof = multivariate_scores(rows); created=[]; score_rows=[]
        for i,e in enumerate(entities):
            score=round(100*(.65*iso[i]+.35*lof[i]))
            eid=e["external_id"]
            factors=[]
            if rows[i][0]>=4: factors.append(f"{int(rows[i][0])} graph connections")
            if rows[i][1]>=.15: factors.append("high betweenness / bridge position")
            if rows[i][4]>0: factors.append(f"transaction-linked activity ₹{rows[i][4]:,.0f}")
            if rows[i][5]>=3: factors.append(f"{int(rows[i][5])} observed events")
            if not factors: factors.append("multivariate deviation from network baseline")
            source_refs=sorted({x["source_ref"] for x in events if (x["entity_id"]==eid or x["related_entity_id"]==eid) and x["source_ref"]})
            score_rows.append({"entity_id":eid,"name":e.get("name",eid),"type":e.get("type","Entity"),"iso":iso[i],"lof":lof[i],"score":score,"factors":factors,"source_refs":source_refs})
            if score < settings.anomaly_review_threshold: continue
            case_key=re.sub(r"[^A-Za-z0-9]+", "-", str(case_number or "GLOBAL")).strip("-") or "GLOBAL"; code=f"AL-AI-{case_key}-{eid}"
            payload=explain_alert("Multivariate pattern review signal",factors,score/100,eid,source_refs,"anomaly-iso-lof-v2")
            old=db.query(Alert).filter_by(code=code).first()
            if old:
                old.confidence=score/100; old.explanation=j(factors); old.source_refs=j(source_refs); old.model_version="anomaly-iso-lof-v2"; old.case_number=case_number
            else:
                db.add(Alert(code=code,severity="High" if score>=settings.anomaly_high_threshold else "Review",title=payload["title"],confidence=score/100,explanation=j(factors),entity_id=eid,case_number=case_number,model_version="anomaly-iso-lof-v2",source_refs=j(source_refs))); created.append(code)
        ranked=sorted(score_rows,key=lambda x:x["score"],reverse=True)
        max_score=max((x["score"] for x in score_rows),default=0)
        if not entities:
            baseline_status="no_verified_entities"
        elif max_score <= 0:
            baseline_status="no_meaningful_deviation"
        else:
            baseline_status="case_baseline_scored"
        db.commit(); self.audit(db,actor,"analytics.run",{"case_number":case_number,"alerts_created":len(created),"candidates":len(ranked[:8]),"max_score":max_score})
        return {"alerts_created":len(created),"scores":score_rows,"review_candidates":ranked[:8],"baseline_status":baseline_status,"max_score":max_score,"entity_count":len(entities),
                "calibration":{"status":"uncalibrated","review_threshold":settings.anomaly_review_threshold,"high_threshold":settings.anomaly_high_threshold,"note":"Scores rank entities against this case's own baseline. They are not probabilities and have not been validated on labelled real-world cases."}}

    def contradictions(self, db, case_number=None):
        """Flag a vehicle linked to different people within two hours (type-driven, no ID-prefix guessing)."""
        types = {e["external_id"]: str(e.get("type", "")).upper() for e in self.entities(db, case_number)}
        issues=[]; by_vehicle={}
        for r in self.relationships(db,case_number):
            if r["relation"].upper() not in {"OWNED","REGISTERED_TO","USES","USED"}:
                continue
            ends = (r["source"], r["target"])
            vehicles = [x for x in ends if types.get(x) == "VEHICLE"]
            if vehicles:
                by_vehicle.setdefault(vehicles[0],[]).append(r)
        for vehicle,rs in by_vehicle.items():
            people=[]
            for r in rs:
                person=r["source"] if r["source"] != vehicle else r["target"]
                tm=pd.to_datetime(r.get("event_time"),utc=True,errors="coerce")
                if types.get(person) == "PERSON": people.append((person,tm,r))
            conflict=[]
            for i,(p1,t1,r1) in enumerate(people):
                for p2,t2,r2 in people[i+1:]:
                    if p1==p2 or pd.isna(t1) or pd.isna(t2): continue
                    if abs((t1-t2).total_seconds()) <= 2*3600: conflict.extend([r1,r2])
            if conflict:
                unique={x["id"]:x for x in conflict}; records=sorted(unique.values(),key=lambda x:x["event_time"] or ""); users=sorted({x["source"] if x["source"]!=vehicle else x["target"] for x in records}); issues.append({"type":"vehicle-conflict","entity":vehicle,"records":records,"message":f"Vehicle {vehicle} has overlapping/near-overlapping user associations: {', '.join(users)}."})
        return issues

    def copilot_suggestions(self, db, case_number=None):
        self.ensure_graph(db, case_number)
        ents=self.entities(db,case_number); rels=self.relationships(db,case_number); events=self.events(db,case_number); alerts=self.alerts(db,case_number)
        links=self.fir_relationships(db,case_number).get("relationships",[])
        out=[]
        degree=self.graph.centrality().get("degree",{})
        top=sorted(degree,key=degree.get,reverse=True)[:2]
        if len(top)>=2: out.append({"question":f"How are {top[0]} and {top[1]} connected?","reason":"Compare two highly connected verified entities."})
        if links:
            b=links[0]; out.append({"question":f"How are FIR {b.get('source_fir')} and FIR {b.get('target_fir')} related?","reason":"Explain the shared evidence keys behind the strongest cross-FIR match."})
        else: out.append({"question":"What evidence would help determine whether any FIRs are related?","reason":"Review the identifiers needed for cross-FIR comparison."})
        if events: out.append({"question":"What changed in this investigation timeline?","reason":"Review the latest dated source-linked activity."})
        if rels: out.append({"question":"Who are the most connected entities?","reason":"Rank verified entities by direct connectivity."})
        if alerts: out.append({"question":"Which review signals need attention first?","reason":"Prioritize the strongest explainable review signals."})
        if any(str(e.get("event_type","")).upper()=="COMMUNICATION" for e in events): out.append({"question":"What communication activity is present in this case?","reason":"Summarize indexed communication activity and its endpoints."})
        if any(str(e.get("event_type","")).upper()=="TRANSACTION" for e in events): out.append({"question":"What are the key financial transactions in this case?","reason":"Summarize transaction count, value and high-value records."})
        out.append({"question":"Summarize this investigation","reason":"Give a compact evidence-grounded workspace overview."})
        return out[:8]

    def copilot(self, q, db, case_number=None):
        self.ensure_graph(db, case_number)
        query=q.strip(); upper=query.upper()
        ids=re.findall(r"\b(?:P\d{3}|P-[A-Z0-9]+|A\d{3}|A-[A-Z0-9]+|V\d{3}|V-[A-Z0-9]+|L\d{3}|L-[A-Z0-9]+|PH\d{3}|PH-[A-Z0-9]+|F\d{3}|FIR-[A-Z0-9]+|ORG\d{2}|ORG-[A-Z0-9]+|E\d{3}|EM-[A-Z0-9]+)\b",upper)
        ents=self.entities(db,case_number); rels=self.relationships(db,case_number); events=self.events(db,case_number); alerts=self.alerts(db,case_number); contradictions=self.contradictions(db,case_number)
        if len(ids)<2:
            for e in ents:
                name=norm(e.get("name"))
                if name and len(name)>=4 and name.casefold() in query.casefold() and e.get("external_id") not in ids: ids.append(e.get("external_id"))
        path=self.graph.path_details(ids[0],ids[1]) if len(ids)>=2 else None
        fir_links=self.fir_relationships(db,case_number).get("relationships",[])
        return self.cop.answer(query,self.graph.summary(),path,self.graph.centrality(),self.graph.communities(),ents,rels,events,alerts,contradictions,fir_links=fir_links)

    # -------------------- FIR relationship / timeline intelligence --------------------

    def _fir_evidence_keys(self, db, doc, case_entities=None):
        """Build explainable, display-preserving evidence keys for one verified FIR.

        Structured extraction is the primary source. Verified entity provenance is a
        deliberate fallback so a valid key is still discoverable when OCR/NLP stores a
        value as an entity but misses the corresponding structured field.
        """
        st = load_json(doc.structured_json, {})
        buckets = {
            "phone": {}, "vehicle": {}, "serial": {}, "account": {}, "email": {},
            "person": {}, "place": {}, "section": {},
        }

        def add(bucket, value):
            display = norm(value)
            normalized = key(display)
            if normalized and normalized not in buckets[bucket]:
                buckets[bucket][normalized] = display

        for value in st.get("phones", []) or []: add("phone", value)
        for value in st.get("vehicles", []) or []: add("vehicle", value)
        for value in st.get("serial_numbers", []) or []: add("serial", value)
        for value in st.get("accounts", []) or []: add("account", value)
        for value in st.get("emails", []) or []: add("email", value)
        for value in [st.get("complainant"), st.get("father_name"), st.get("investigating_officer"), st.get("victim"), st.get("accused_name")]:
            add("person", value)
        for value in [st.get("place_of_occurrence"), st.get("complainant_address")]:
            add("place", value)
        for value in st.get("sections", []) or []: add("section", value)

        source_entity_ids = []
        scoped_entities = case_entities if case_entities is not None else self._case_entities(db, doc.case_number)
        for entity in scoped_entities:
            attrs = load_json(entity.attributes, {})
            raw_docs = attrs.get("source_documents", [])
            if isinstance(raw_docs, str): raw_docs = [raw_docs]
            if doc.document_id not in raw_docs and attrs.get("document_id") != doc.document_id:
                continue
            source_entity_ids.append(entity)
        entity_bucket = {
            "PHONE":"phone", "VEHICLE":"vehicle", "ACCOUNT":"account",
            "EMAIL":"email", "DIGITAL_IDENTIFIER":"serial", "PERSON":"person",
            "LOCATION":"place", "ADDRESS":"place",
        }
        for entity in source_entity_ids:
            bucket = entity_bucket.get(str(entity.entity_type or "").upper())
            if bucket:
                add(bucket, entity.name)

        return {bucket: sorted(values) for bucket, values in buckets.items()}

    def fir_relationships(self, db, case_number=None):
        """Return a visible, explainable FIR-to-FIR relationship intelligence board.

        Only verified FIR documents in the selected workspace are compared. Matching
        evidence keys create investigative leads; they never merge identities or assert
        guilt. The response also exposes link-readiness information so an investigator
        can see why a workspace has no link yet instead of receiving a blank panel.
        """
        if not case_number:
            return {"case_number": None, "firs": [], "relationships": [], "summary": {"verified_firs": 0, "candidate_links": 0, "verified_links": 0, "shared_key_types": 0}}
        docs = [d for d in db.query(DocumentRecord).filter_by(case_number=case_number).order_by(DocumentRecord.id).all()]
        fir_docs = [d for d in docs if d.status == "Verified" and str(d.document_type).upper() == "FIR"]

        # Build the case entity scope once. The old implementation rescanned every
        # entity for every FIR, which made a large multi-FIR workspace progressively
        # slower. A single scoped index keeps FIR comparison predictable as the case grows.
        case_entities = self._case_entities(db, case_number)
        rows=[]
        for d in fir_docs:
            st=load_json(d.structured_json,{})
            rows.append({
                "document_id":d.document_id, "filename":d.filename,
                "fir_number":st.get("fir_number") or d.document_id, "status":d.status,
                "evidence_id":d.evidence_id, "values":self._fir_evidence_keys(db, d, case_entities=case_entities),
            })

        graph_links = [r for r in self.relationships(db, case_number) if r.get("relation") == "INTERLINKED_VIA" and r.get("verification_state") == "verified"]
        graph_link_map = {}
        for link in graph_links:
            pair=frozenset((link.get("source"), link.get("target")))
            graph_link_map[pair]=link

        weights={"phone":0.36,"vehicle":0.28,"serial":0.14,"account":0.08,"email":0.06,"person":0.04,"place":0.03,"section":0.01}
        relationships=[]
        for i,a in enumerate(rows):
            for b in rows[i+1:]:
                reasons=[]; score=0.0
                for typ,w in weights.items():
                    shared_keys=sorted(set(a["values"].get(typ,[])) & set(b["values"].get(typ,[])))
                    if shared_keys:
                        score += w
                        reasons.append({"type":typ,"shared":shared_keys})
                if not reasons: continue
                fir_pair=frozenset((self._fir_entity_id(case_number,a["fir_number"]),self._fir_entity_id(case_number,b["fir_number"])))
                link=graph_link_map.get(fir_pair)
                bridge=[]
                if link:
                    bridge=(link.get("metadata") or {}).get("bridge_entities",[]) or []
                relationships.append({
                    "source_document_id":a["document_id"], "source_fir":a["fir_number"],
                    "target_document_id":b["document_id"], "target_fir":b["fir_number"],
                    "match_score":round(min(score,1.0),3),
                    "match_strength":"Strong" if score>=.55 else ("Moderate" if score>=.30 else "Weak"),
                    "reasons":reasons,
                    "shared_key_types":len(reasons),
                    "shared_key_count":sum(len(x.get("shared",[])) for x in reasons),
                    "interlink_status":"verified graph link" if link else "candidate cross-FIR match",
                    "graph_link":link,
                    "bridge_entities":bridge,
                })
        relationships.sort(key=lambda x:(-x["match_score"],x["source_fir"],x["target_fir"]))
        key_types=set()
        for row in relationships:
            key_types.update(x.get("type") for x in row.get("reasons",[]) if x.get("type"))
        fir_cards=[]
        for r in rows:
            counts={k:len(v) for k,v in r["values"].items() if v}
            fir_cards.append({**{k:v for k,v in r.items() if k!="values"}, "key_counts":counts, "total_evidence_keys":sum(counts.values())})
        return {
            "case_number":case_number, "firs":fir_cards, "relationships":relationships,
            "summary":{
                "verified_firs":len(rows), "candidate_links":len(relationships),
                "verified_links":sum(1 for x in relationships if x.get("graph_link")),
                "shared_key_types":len(key_types),
                "shared_key_categories":sorted(key_types),
            },
        }

    def timeline_intelligence(self, db, case_number=None, role="investigator", **filters):
        """Events + daily counts + swimlane data + gaps + map positions (see services/timeline_engine.py)."""
        from backend.app.services.timeline_engine import build_timeline
        events = self.events(db, case_number)
        rel_count = len(self.relationships(db, case_number))
        entities = {e["external_id"]: e for e in self.entities(db, case_number, role=role)}
        # Coordinates are not PII-masked, but identifier names (phones, plates...) are, via mask_entity above.
        fir_docs = {}
        for d in (db.query(DocumentRecord).filter_by(case_number=case_number).all() if case_number else db.query(DocumentRecord).all()):
            fir_docs[d.document_id] = load_json(d.structured_json, {}).get("fir_number") or d.document_id
        from security.masking import needs_masking
        return build_timeline(events, entities, fir_docs, rel_count, case_number, masked=needs_masking(role), **filters)

    def timeline_compare(self, db, case_number, fir_numbers, role="investigator"):
        from backend.app.services.timeline_engine import parse_ts
        base = self.timeline_intelligence(db, case_number, role=role, limit=100000)
        wanted = list(dict.fromkeys(fir_numbers))
        events = [e for e in base.get("events", []) if e.get("fir_number") in set(wanted)]
        groups = {f: [] for f in wanted}
        for e in events:
            groups.setdefault(e.get("fir_number") or "Unknown", []).append(e)
        links = []
        docs = [d for d in db.query(DocumentRecord).filter_by(case_number=case_number, status="Verified").all()] if case_number else []
        by_fir = {load_json(d.structured_json, {}).get("fir_number") or d.document_id: d for d in docs}
        for a in wanted:
            for b in wanted:
                if a >= b:
                    continue
                shared = []
                da, dbb = by_fir.get(a), by_fir.get(b)
                if da and dbb:
                    ka = self._fir_evidence_keys(db, da); kb = self._fir_evidence_keys(db, dbb)
                    for typ in sorted(set(ka) & set(kb)):
                        common = sorted(set(ka[typ]) & set(kb[typ]))
                        if common:
                            shared.append({"type": typ, "values": common})
                if shared:
                    links.append({"source_fir": a, "target_fir": b, "shared_keys": shared})
        summaries = {}
        days_by_fir = {}
        for f, evs in groups.items():
            ts = sorted(t for t in (parse_ts(e.get("event_time")) for e in evs) if t)
            summaries[f] = {"events": len(evs), "first_event": ts[0].isoformat() if ts else None, "last_event": ts[-1].isoformat() if ts else None,
                            "verified": f in by_fir, "categories": dict(Counter(e["category"] for e in evs))}
            days_by_fir[f] = {t.date().isoformat() for t in ts}
        day_owners = {}
        for f, days in days_by_fir.items():
            for d in days:
                day_owners.setdefault(d, []).append(f)
        overlap_days = [{"date": d, "firs": sorted(fs)} for d, fs in sorted(day_owners.items()) if len(fs) > 1]
        return {"case_number": case_number, "firs": fir_numbers, "events": events, "by_fir": groups, "interlinks": links, "event_count": len(events),
                "summaries": summaries, "overlap_days": overlap_days[:200], "masked": base.get("masked", False)}

    def analytics_overview(self, db, case_number=None):
        self.ensure_graph(db, case_number)
        central=self.graph.centrality(); ents=self.entities(db,case_number); rels=self.relationships(db,case_number); events=self.events(db,case_number)
        degree=central.get("degree",{}); bet=central.get("betweenness",{}); pr=central.get("pagerank",{}); eig=central.get("eigenvector",{})
        def top(metric):
            return [{"entity_id":n,"name":next((e["name"] for e in ents if e["external_id"]==n),n),"score":round(float(v),4)} for n,v in sorted(metric.items(),key=lambda x:x[1],reverse=True)[:10]]
        type_counts=Counter(e["type"] for e in ents); rel_counts=Counter(r["relation"] for r in rels)
        return {"case_number":case_number,"graph":self.graph.summary(),"communities":len(self.graph.communities()),
                "entity_types":dict(type_counts),"relationship_types":dict(rel_counts),"timeline_events":len(events),
                "top_degree":top(degree),"top_betweenness":top(bet),"top_pagerank":top(pr),"top_eigenvector":top(eig),
                "cdr":self.cdr_analytics(db,case_number),"financial":self.financial_analytics(db,case_number),
                "contradictions":self.contradictions(db,case_number),"fir_relationships":self.fir_relationships(db,case_number).get("relationships",[]),
                "verified_documents":len([d for d in self.documents(db,case_number) if d.get("status")=="Verified"])}

    # -------------------- model registry / monitoring --------------------

    def model_registry(self):
        metrics = {}
        mp = BASE_DIR / "ai_engine" / "models" / "metrics.json"
        try:
            metrics = json.loads(mp.read_text()) if mp.exists() else {}
        except (OSError, ValueError):
            log.warning("Could not read model metrics file %s", mp)
        return {
            "runtime": "hybrid-local",
            "models": [
                {"name":"FIR structured extraction","version":"fir-structured-rules-v4.3","type":"regex/rule parser + local Tesseract OCR","training":"none (hand-written rules, tested on fixtures)","status":"ready"},
                {"name":"Entity context scorer","version":"entity-context-nlp-v2.1","type":"TF-IDF char n-grams + logistic regression (secondary signal only)","training":"small synthetic labelled snippets","evaluation":metrics.get("entity_context", "run scripts/train_fir_models.py to produce held-out metrics"),"status":"ready"},
                {"name":"Entity resolution","version":"entity-resolution-knn-v3","type":"char n-gram TF-IDF KNN + string similarity","training":"none (fit per query on the case's entities); weights/thresholds are hand-set","status":"ready"},
                {"name":"FIR interlinking","version":"fir-link-rules-v1","type":"shared-identifier matching","training":"none (deterministic rules)","status":"ready"},
                {"name":"Anomaly detector","version":"anomaly-iso-lof-v2","type":"Isolation Forest + LOF on graph features","training":"unsupervised, fit on each case at runtime","calibration":"uncalibrated - thresholds are configurable heuristics, no labelled ground truth","status":"ready"},
                {"name":"Translation","version":"rule-engine-v2","type":"offline glossary + rule-based sentence translation with transliteration (NOT neural machine translation; an optional local IndicTrans2 model is used only if installed and is always labelled)","training":"curated police/legal glossary (Hindi, Marathi, Bengali, Gujarati, Punjabi, Tamil, Telugu, Kannada, Malayalam, Urdu)","status":"limited"},
            ],
            "safety": {"human_review_required": True, "no_guilt_inference": True, "source_citations_required": True},
        }

    # -------------------- reports / workspace --------------------

    def workspace(self, db, case_number, role="investigator"):
        c=db.query(Case).filter_by(case_number=case_number).first()
        if not c: raise ValueError("Case not found")
        from security.masking import needs_masking
        restricted = needs_masking(role)  # masked roles never receive raw FIR text or evidence lists
        return {"case":{"case_number":c.case_number,"title":c.title,"status":c.status,"summary":c.summary,"visibility":c.visibility,"created_at":c.created_at.isoformat() if c.created_at else None},"documents":[] if restricted else self.documents(db,case_number),"entities":self.entities(db,case_number,role=role),"relationships":self.relationships(db,case_number),"events":self.events(db,case_number),"alerts":self.alerts(db,case_number),"tasks":self.tasks(db,case_number),"evidence":[] if restricted else self.evidence(db,case_number=case_number)}

    def report(self,db,case_number):
        c=db.query(Case).filter_by(case_number=case_number).first()
        if not c: raise ValueError("Case not found")
        self.ensure_graph(db,case_number)
        return investigation_report({"case_number":c.case_number,"title":c.title,"status":c.status,"summary":c.summary},self.entities(db,case_number),self.relationships(db,case_number),self.alerts(db,case_number),self.tasks(db,case_number),self.graph.summary(),self.graph.centrality(),self.events(db,case_number),self.evidence(db,case_number=case_number),self.contradictions(db,case_number))

    def report_csv(self, db, case_number):
        report=self.report(db,case_number)
        out=io.StringIO(); writer=csv.writer(out); writer.writerow(["section","id","name","type","relation","source","target","status","confidence","source_ref","event_time"])
        for e in report.get("entities",[]): writer.writerow(["entity",e.get("external_id"),e.get("name"),e.get("type"),"","","","",e.get("confidence"),"",""])
        for r in report.get("relationships",[]): writer.writerow(["relationship",r.get("id"),"","",r.get("relation"),r.get("source"),r.get("target"),r.get("verification_state"),r.get("confidence"),r.get("source_ref"),r.get("event_time")])
        for a in report.get("alerts",[]): writer.writerow(["alert",a.get("code"),a.get("title"),"", "","","",a.get("status"),a.get("confidence"),"",""])
        return out.getvalue().encode("utf-8-sig")

    def report_docx(self, db, case_number):
        from docx import Document
        report=self.report(db,case_number); doc=Document(); case=report.get("case",{})
        doc.add_heading("DARK CRIMENET — Investigation Report", 0); doc.add_paragraph(f"Case: {case.get('case_number','')} · {case.get('title','')}"); doc.add_paragraph(f"Generated: {report.get('generated_at','')}")
        doc.add_heading("Executive Summary", 1); ex=report.get("executive_summary",{}); doc.add_paragraph("Entities: {entities_reviewed} | Relationships: {relationships_reviewed} | Alerts: {alerts} | Tasks: {tasks} | Timeline events: {timeline_events} | Evidence: {evidence_items} | Contradictions: {contradictions}".format(entities_reviewed=ex.get("entities_reviewed",0),relationships_reviewed=ex.get("relationships_reviewed",0),alerts=ex.get("alerts",0),tasks=ex.get("tasks",0),timeline_events=ex.get("timeline_events",0),evidence_items=ex.get("evidence_items",0),contradictions=ex.get("contradictions",0)))
        doc.add_heading("Entities",1); table=doc.add_table(rows=1, cols=4); hdr=table.rows[0].cells; hdr[0].text="ID";hdr[1].text="Name";hdr[2].text="Type";hdr[3].text="Confidence"
        for e in report.get("entities",[])[:150]: cells=table.add_row().cells; cells[0].text=str(e.get("external_id"));cells[1].text=str(e.get("name"));cells[2].text=str(e.get("type"));cells[3].text=f"{float(e.get('confidence') or 0):.0%}"
        doc.add_heading("Relationships",1); table=doc.add_table(rows=1, cols=5); hdr=table.rows[0].cells; [setattr(hdr[i],"text",v) for i,v in enumerate(["Source","Relationship","Target","Confidence","Evidence"]) ]
        for r in report.get("relationships",[])[:200]: cells=table.add_row().cells; cells[0].text=str(r.get("source")); cells[1].text=str(r.get("relation")); cells[2].text=str(r.get("target")); cells[3].text=f"{float(r.get('confidence') or 0):.0%}"; cells[4].text=str(r.get("source_ref"))
        doc.add_heading("AI / Analytics Signals",1)
        for a in report.get("alerts",[]): doc.add_paragraph(f"{a.get('severity')} — {a.get('title')} ({float(a.get('confidence') or 0):.0%})", style="List Bullet")
        doc.add_heading("Responsible AI",1); doc.add_paragraph(report.get("responsible_ai","AI outputs are investigative signals and require authorized human review."))
        buff=io.BytesIO(); doc.save(buff); return buff.getvalue()

    def report_pdf(self, db, case_number):
        # Lightweight dependency-free PDF text report using PyMuPDF, already in requirements.
        import fitz
        report=self.report(db,case_number); pages=[]; lines=[]
        case=report.get("case",{}); lines += ["DARK CRIMENET — Investigation Report",f"Case: {case.get('case_number','')} | {case.get('title','')}",f"Generated: {report.get('generated_at','')}",""]
        ex=report.get("executive_summary",{}); lines += ["EXECUTIVE SUMMARY",f"Entities: {ex.get('entities_reviewed',0)}",f"Relationships: {ex.get('relationships_reviewed',0)}",f"Alerts: {ex.get('alerts',0)}",f"Tasks: {ex.get('tasks',0)}",f"Timeline events: {ex.get('timeline_events',0)}",f"Evidence: {ex.get('evidence_items',0)}",""]
        lines.append("TOP REVIEW SIGNALS")
        for a in report.get("alerts",[])[:10]: lines.append(f"- {a.get('severity')}: {a.get('title')} ({float(a.get('confidence') or 0):.0%})")
        lines += ["","TOP RELATIONSHIPS"]
        for r in report.get("relationships",[])[:25]: lines.append(f"- {r.get('source')} --{r.get('relation')}--> {r.get('target')} | {float(r.get('confidence') or 0):.0%} | {r.get('source_ref')}")
        lines += ["","Responsible AI: analytics and anomaly scores are investigative signals, not determinations of guilt."]
        page=fitz.open(); current=[]
        for line in lines:
            current.append(line)
            if len(current)>=46:
                pages.append(current); current=[]
        if current: pages.append(current)
        for chunk in pages:
            p=page.new_page(); p.insert_textbox(fitz.Rect(54,54,540,770),"\n".join(chunk),fontsize=9,lineheight=1.35,fontname="helv",color=(0.08,0.1,0.14))
        out=page.tobytes(); page.close(); return out
