from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, Boolean, UniqueConstraint
from backend.app.db.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Entity(Base):
    __tablename__ = "entities"
    id = Column(Integer, primary_key=True)
    external_id = Column(String(100), unique=True, index=True)
    name = Column(String(255), index=True)
    entity_type = Column(String(60), index=True)
    confidence = Column(Float, default=1.0)
    masked = Column(String(255), default="")
    attributes = Column(Text, default="{}")
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)


class Relationship(Base):
    __tablename__ = "relationships"
    id = Column(Integer, primary_key=True)
    source_id = Column(String(100), index=True)
    target_id = Column(String(100), index=True)
    relation_type = Column(String(80), index=True)
    confidence = Column(Float, default=1.0)
    source_ref = Column(String(255), default="")
    event_time = Column(DateTime, nullable=True)
    metadata_json = Column(Text, default="{}")
    verification_state = Column(String(30), default="unreviewed")
    verified_by = Column(String(100), default="")
    model_version = Column(String(80), default="graph-rules-v1")
    created_at = Column(DateTime, default=utcnow)


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    event_id = Column(String(100), unique=True, index=True)
    event_type = Column(String(80), index=True)
    entity_id = Column(String(100), index=True)
    related_entity_id = Column(String(100), default="")
    event_time = Column(DateTime, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    source_ref = Column(String(255), default="")
    metadata_json = Column(Text, default="{}")


class Evidence(Base):
    __tablename__ = "evidence"
    id = Column(Integer, primary_key=True)
    evidence_id = Column(String(100), unique=True, index=True)
    filename = Column(String(255))
    media_type = Column(String(120), default="application/octet-stream")
    source = Column(String(255), default="")
    sha256 = Column(String(128), index=True)
    stored_path = Column(String(500), default="")
    uploaded_by = Column(String(100), default="")
    created_at = Column(DateTime, default=utcnow)
    integrity_status = Column(String(30), default="pending")
    case_number = Column(String(100), index=True, default="")
    document_id = Column(String(100), index=True, default="")


class Case(Base):
    __tablename__ = "cases"
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), unique=True, index=True)
    title = Column(String(255))
    status = Column(String(50), default="Active")
    summary = Column(Text, default="")
    created_by = Column(String(100), default="")
    # "restricted": creator, members and admins only. "shared": any authenticated user,
    # subject to their role permissions (used for the bundled synthetic demo cases).
    visibility = Column(String(20), default="restricted")
    # True for synthetic demonstration cases (bundled seed data and anything the demo account creates).
    # Demo cases are visible only to the demo role and admins; the demo role sees nothing else.
    is_demo = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=utcnow)


class CaseMember(Base):
    __tablename__ = "case_members"
    __table_args__ = (UniqueConstraint("case_number", "username", name="uq_case_member"),)
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    username = Column(String(100), index=True)
    added_by = Column(String(100), default="")
    created_at = Column(DateTime, default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    code = Column(String(100), unique=True, index=True)
    severity = Column(String(30))
    title = Column(String(255))
    status = Column(String(40), default="New")
    explanation = Column(Text, default="[]")
    confidence = Column(Float, default=0.0)
    entity_id = Column(String(100), default="")
    case_number = Column(String(100), index=True, default="")
    model_version = Column(String(80), default="anomaly-v1")
    source_refs = Column(Text, default="[]")
    created_at = Column(DateTime, default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(Integer, primary_key=True)
    actor = Column(String(100))
    action = Column(String(255))
    details = Column(Text, default="")
    previous_hash = Column(String(128), default="")
    event_hash = Column(String(128))
    created_at = Column(DateTime, default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    title = Column(String(255))
    status = Column(String(40), default="Open")
    assignee = Column(String(100), default="")

class DocumentRecord(Base):
    __tablename__ = "document_records"
    id = Column(Integer, primary_key=True)
    document_id = Column(String(100), unique=True, index=True)
    case_number = Column(String(100), index=True, default="")
    group_id = Column(String(120), index=True, default="")
    filename = Column(String(255))
    media_type = Column(String(120), default="application/octet-stream")
    document_type = Column(String(80), default="INVESTIGATION_DOCUMENT")
    status = Column(String(40), default="Pending Review")
    sha256 = Column(String(128), index=True, default="")
    stored_path = Column(String(500), default="")
    evidence_id = Column(String(100), default="")
    text = Column(Text, default="")
    structured_json = Column(Text, default="{}")
    entities_json = Column(Text, default="[]")
    relationships_json = Column(Text, default="[]")
    english_json = Column(Text, default="{}")
    quality_json = Column(Text, default="{}")
    review_actor = Column(String(100), default="")
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(40), default="investigator", index=True)
    active = Column(Boolean, default=True)
    token_version = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)
    # Two-step sign-in (security/mfa.py, features/mfa.py). The secret is Fernet-encrypted; a value with
    # mfa_enabled = 0 is an enrolment that has not been confirmed yet.
    mfa_enabled = Column(Boolean, default=False)
    mfa_secret_enc = Column(Text, default="")
    mfa_last_step = Column(Integer, default=0)
    mfa_enrolled_at = Column(DateTime, nullable=True)


class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    author = Column(String(100), default="")
    body = Column(Text, default="")
    created_at = Column(DateTime, default=utcnow)


# A case created by a demo-role account is always a demo case, whichever code path created it
# (workbench endpoint, FIR upload that auto-creates a workspace, feature plug-ins ...).
from sqlalchemy import event as _sa_event, select as _sa_select  # noqa: E402


@_sa_event.listens_for(Case, "before_insert")
def _flag_demo_case(mapper, connection, target):
    if getattr(target, "is_demo", None) or not target.created_by:
        return
    role = connection.execute(_sa_select(User.role).where(User.username == target.created_by)).scalar()
    if role == "demo":
        target.is_demo = True
