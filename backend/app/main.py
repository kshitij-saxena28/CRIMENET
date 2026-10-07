import logging
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.app.core.config import settings
from backend.app.db.database import (Base, SessionLocal, engine, ensure_schema_columns, get_db,
                                     migrate_evidence_integrity_states)
from backend.app.models.models import Case, CaseMember, DocumentRecord, Alert, Evidence, Note, User
from backend.app.schemas.schemas import (
    AdminResetPasswordIn, AlertUpdate, CaseIn, CaseMemberIn, CaseVisibilityIn, CopilotIn, DocumentReviewIn,
    EntityIn, EventIn, LoginIn, NoteIn, PasswordChangeIn, RelationshipIn, RoleChangeIn, TaskIn,
    UserCreateIn, UsernameChangeIn,
)
from backend.app.services.app_service import AppService
from security.access import authorize_case
from security.auth import (
    _hash, _verify, authenticate, bootstrap_users, bump_token_version, current_user, issue_token, require,
    require_any_perm, require_perm, sensitive,
)
from security.masking import mask_entity, mask_node
from security.passwords import validate_password
from security.rbac import ROLES as ROLE_NAMES, allowed as _allowed, permissions_for

log = logging.getLogger("dcn.api")

from backend.app import features as _features  # noqa: E402
_features.import_features()  # feature models must exist before create_all
_mfa = _features.LOADED.get("mfa")  # two-step sign-in; login works without it if the feature failed to load
Base.metadata.create_all(bind=engine)
ensure_schema_columns()
migrate_evidence_integrity_states()
with SessionLocal() as _db:
    bootstrap_users(_db)

APP_VERSION = "5.3.0"
app = FastAPI(
    title="DARK CRIMENET | Investigation Intelligence API",
    version=APP_VERSION,
    description="FIR-centric, evidence-backed, human-in-the-loop investigation intelligence platform.",
    # Interactive API docs are only exposed in demo mode.
    docs_url="/docs" if settings.is_demo else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.is_demo else None,
)
svc = AppService()
_demo_seed_lock = threading.Lock()
USERNAME_RE = re.compile(r"[A-Za-z0-9._-]{3,100}")
CASE_RE = re.compile(r"[A-Za-z0-9._-]{3,100}")
ROLES = set(ROLE_NAMES)  # admin, supervisor, investigator, auditor, demo (security/rbac.py)


WEB_DIR = Path(__file__).resolve().parents[2] / "web"
# No inline scripts, no third-party code. Map tiles are the only external resource (images only).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob: https://*.tile.openstreetmap.org; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    path = request.url.path
    if path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-cache"  # revalidate (ETag) so upgrades show up, but stay fast
    else:
        resp.headers.setdefault("Cache-Control", "no-store")
    if not path.startswith(("/docs", "/openapi")):
        resp.headers.setdefault("Content-Security-Policy", CSP)
    return resp


# ------------------------------------------------------------------ helpers
def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "-"


def _scope(db: Session, user: dict, case_number: str | None, required: bool = True) -> str | None:
    return authorize_case(db, user, case_number, required=required)


def _bad(exc: Exception, default: int = 400) -> HTTPException:
    msg = str(exc)
    return HTTPException(404 if "not found" in msg.lower() else default, msg)


async def _read_upload(file: UploadFile) -> bytes:
    """Read an upload in chunks, aborting as soon as it exceeds the size limit."""
    limit = settings.max_upload_bytes
    buf = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(413, f"File exceeds the {settings.max_upload_mb} MB upload limit")
    return bytes(buf)


def _doc_case(db: Session, user: dict, document_id: str) -> str:
    row = db.query(DocumentRecord).filter_by(document_id=document_id).first()
    if not row:
        raise HTTPException(404, "Document not found")
    return _scope(db, user, row.case_number)


def _clean_case(value: str) -> str:
    cn = (value or "").strip().upper()
    if not CASE_RE.fullmatch(cn):
        raise HTTPException(400, "Case number may contain only letters, numbers, dot, underscore and hyphen (3-100 chars)")
    return cn


# ------------------------------------------------------------------ public
@app.get("/", include_in_schema=False)
def root():
    return FileResponse(str(WEB_DIR / "index.html"), media_type="text/html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/health")
def health():
    # Deliberately cheap and unauthenticated (used by container health checks).
    return {"status": "ok", "api_version": APP_VERSION}


@app.get("/health/detail")
def health_detail(user=Depends(require_perm("manage_users"))):
    return {"status": "ok", "environment": settings.app_env, "api_version": APP_VERSION,
            "graph_backend": svc.graph_backend()}


# ------------------------------------------------------------------ auth
@app.post("/auth/login")
def auth(x: LoginIn, request: Request, db: Session = Depends(get_db)):
    username = x.username.strip()
    try:
        row = authenticate(db, username, x.password, _ip(request))
    except HTTPException as exc:
        if exc.status_code == 401 and db.query(User).filter_by(username=username).first():
            svc.audit(db, username, "auth.login_failed", {"username": username, "ip": _ip(request)})
        raise
    gate = _mfa.login_gate(db, row) if _mfa else None   # second step (features/mfa.py); None = signed in
    if gate:
        svc.audit(db, username, "auth.login_password_ok", {"username": username, "role": row.role, "next": "setup" if gate.get("mfa_setup_required") else "code"})
        return gate
    svc.audit(db, username, "auth.login", {"username": username, "role": row.role})
    return {"access_token": issue_token(row), "token_type": "bearer", "username": row.username, "role": row.role,
            "permissions": permissions_for(row.role)}


@app.get("/me")
def me(user=Depends(current_user), db: Session = Depends(get_db)):
    row = db.query(User).filter_by(id=user["uid"]).first()
    return {"sub": row.username, "username": row.username, "role": row.role, "permissions": permissions_for(row.role),
            "is_demo_account": row.role == "demo"}


@app.post("/auth/change-username")
def change_username(x: UsernameChangeIn, db: Session = Depends(get_db), user=Depends(sensitive)):
    row = db.query(User).filter_by(id=user["uid"]).first()
    if not _verify(x.current_password, row.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    new_name = x.new_username.strip()
    if not USERNAME_RE.fullmatch(new_name):
        raise HTTPException(400, "Account ID may contain only letters, numbers, dot, underscore and hyphen")
    if db.query(User).filter(User.username == new_name, User.id != row.id).first():
        raise HTTPException(409, "Username already exists")
    old = row.username
    row.username = new_name; row.updated_at = _now()
    db.query(CaseMember).filter_by(username=old).update({"username": new_name})
    db.query(Case).filter_by(created_by=old).update({"created_by": new_name})
    bump_token_version(row)  # old tokens carry the old name and must die
    db.commit()
    svc.audit(db, old, "auth.username_changed", {"old_username": old, "new_username": new_name})
    return {"status": "ok", "old_username": old, "new_username": new_name, "message": "Username changed. Sign in again with the new username."}


@app.post("/auth/change-password")
def change_password(x: PasswordChangeIn, db: Session = Depends(get_db), user=Depends(sensitive)):
    row = db.query(User).filter_by(id=user["uid"]).first()
    if not _verify(x.current_password, row.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    if x.current_password == x.new_password:
        raise HTTPException(400, "New password must differ from the current one")
    validate_password(x.new_password, row.username)
    row.password_hash = _hash(x.new_password); row.updated_at = _now()
    bump_token_version(row)  # every other session is signed out
    db.commit()
    svc.audit(db, row.username, "auth.password_changed", {"username": row.username})
    return {"status": "ok", "message": "Password changed successfully", "access_token": issue_token(row, amr=user.get("amr") or ["pwd"]), "token_type": "bearer"}


# Demo-data endpoints (/demo/status, /demo/enable, /demo/disable, /demo/load) live in backend/app/features/demo.py.
# Sign-up requests (/auth/signup-request, /signup-requests...) live in backend/app/features/signup.py.


# ------------------------------------------------------------------ admin
@app.get("/admin/users")
def admin_users(db: Session = Depends(get_db), user=Depends(require_perm("manage_users"))):
    return [{"username": u.username, "role": u.role, "active": u.active, "mfa_enabled": bool(u.mfa_enabled), "created_at": u.created_at.isoformat() if u.created_at else None}
            for u in db.query(User).order_by(User.username).all()]


@app.post("/admin/users")
def admin_create_user(x: UserCreateIn, db: Session = Depends(get_db), user=Depends(require_perm("manage_users")), _otp=Depends(sensitive)):
    role = x.role.lower().strip(); username = x.username.strip()
    if role not in ROLES:
        raise HTTPException(400, "Role must be one of: " + ", ".join(ROLE_NAMES))
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(400, "Username may contain only letters, numbers, dot, underscore and hyphen")
    if db.query(User).filter_by(username=username).first():
        raise HTTPException(409, "Username already exists")
    validate_password(x.password, username)
    row = User(username=username, password_hash=_hash(x.password), role=role, active=True)
    db.add(row); db.commit()
    svc.audit(db, user["sub"], "admin.user_created", {"username": row.username, "role": role})
    return {"username": row.username, "role": row.role, "active": row.active}


@app.delete("/admin/users/{username}")
def admin_delete_user(username: str, db: Session = Depends(get_db), user=Depends(require_perm("manage_users"))):
    row = db.query(User).filter_by(username=username).first()
    if not row: raise HTTPException(404, "User not found")
    if username == user["sub"]: raise HTTPException(400, "Admin cannot deactivate the current account")
    if row.role == "admin" and row.active and db.query(User).filter_by(role="admin", active=True).count() <= 1:
        raise HTTPException(400, "At least one active admin must remain")
    row.active = False; bump_token_version(row); db.commit()
    svc.audit(db, user["sub"], "admin.user_deactivated", {"username": username})
    return {"status": "ok", "username": username, "active": False}


@app.post("/admin/users/{username}/activate")
def admin_activate_user(username: str, db: Session = Depends(get_db), user=Depends(require_perm("manage_users"))):
    row = db.query(User).filter_by(username=username).first()
    if not row: raise HTTPException(404, "User not found")
    row.active = True; row.updated_at = _now(); db.commit()
    svc.audit(db, user["sub"], "admin.user_activated", {"username": username})
    return {"status": "ok", "username": username, "active": True}


@app.patch("/admin/users/{username}/role")
def admin_change_role(username: str, x: RoleChangeIn, db: Session = Depends(get_db), user=Depends(require_perm("manage_users")), _otp=Depends(sensitive)):
    role = x.role.lower().strip()
    if role not in ROLES: raise HTTPException(400, "Invalid role")
    row = db.query(User).filter_by(username=username).first()
    if not row: raise HTTPException(404, "User not found")
    if row.role == "admin" and role != "admin" and row.active and db.query(User).filter_by(role="admin", active=True).count() <= 1:
        raise HTTPException(400, "At least one active admin must remain")
    row.role = role; bump_token_version(row); db.commit()
    svc.audit(db, user["sub"], "admin.role_changed", {"username": username, "role": role})
    return {"username": username, "role": role}


@app.post("/admin/users/{username}/reset-password")
def admin_reset_password(username: str, x: AdminResetPasswordIn, db: Session = Depends(get_db), user=Depends(require_perm("manage_users")), _otp=Depends(sensitive)):
    row = db.query(User).filter_by(username=username).first()
    if not row: raise HTTPException(404, "User not found")
    validate_password(x.new_password, username)
    row.password_hash = _hash(x.new_password)
    bump_token_version(row); db.commit()          # signs the user out everywhere
    svc.audit(db, user["sub"], "admin.password_reset", {"username": username})
    return {"status": "ok", "username": username}


@app.post("/admin/cases/{case_number}/archive")
def admin_archive_case(case_number: str, db: Session = Depends(get_db), user=Depends(require_perm("manage_cases"))):
    row = db.query(Case).filter_by(case_number=case_number).first()
    if not row: raise HTTPException(404, "Case not found")
    row.status = "Archived"; db.commit()
    svc.audit(db, user["sub"], "admin.case_archived", {"case_number": case_number})
    return {"status": "ok", "case_number": case_number, "case_status": row.status}


@app.get("/admin/cases/{case_number}/members")
def admin_case_members(case_number: str, db: Session = Depends(get_db), user=Depends(require_perm("manage_cases"))):
    case = db.query(Case).filter_by(case_number=case_number).first()
    if not case: raise HTTPException(404, "Case not found")
    return {"case_number": case_number, "visibility": case.visibility, "created_by": case.created_by, "members": svc.case_members(db, case_number)}


@app.post("/admin/cases/{case_number}/members")
def admin_add_case_member(case_number: str, x: CaseMemberIn, db: Session = Depends(get_db), user=Depends(require_perm("manage_cases"))):
    if not db.query(Case).filter_by(case_number=case_number).first(): raise HTTPException(404, "Case not found")
    if not db.query(User).filter_by(username=x.username, active=True).first(): raise HTTPException(404, "User not found")
    if not db.query(CaseMember).filter_by(case_number=case_number, username=x.username).first():
        db.add(CaseMember(case_number=case_number, username=x.username, added_by=user["sub"])); db.commit()
        svc.audit(db, user["sub"], "admin.case_member_added", {"case_number": case_number, "username": x.username})
    return {"case_number": case_number, "members": svc.case_members(db, case_number)}


@app.delete("/admin/cases/{case_number}/members/{username}")
def admin_remove_case_member(case_number: str, username: str, db: Session = Depends(get_db), user=Depends(require_perm("manage_cases"))):
    n = db.query(CaseMember).filter_by(case_number=case_number, username=username).delete(); db.commit()
    if n: svc.audit(db, user["sub"], "admin.case_member_removed", {"case_number": case_number, "username": username})
    return {"case_number": case_number, "members": svc.case_members(db, case_number)}


@app.patch("/admin/cases/{case_number}/visibility")
def admin_case_visibility(case_number: str, x: CaseVisibilityIn, db: Session = Depends(get_db), user=Depends(require_perm("manage_cases"))):
    case = db.query(Case).filter_by(case_number=case_number).first()
    if not case: raise HTTPException(404, "Case not found")
    case.visibility = x.visibility; db.commit()
    svc.audit(db, user["sub"], "admin.case_visibility", {"case_number": case_number, "visibility": x.visibility})
    return {"case_number": case_number, "visibility": x.visibility}


# ------------------------------------------------------------------ notes / core records
@app.post("/notes")
def add_note(x: NoteIn, db: Session = Depends(get_db), user=Depends(require_perm("note"))):
    cn = _scope(db, user, x.case_number)
    row = Note(case_number=cn, author=user["sub"], body=x.body.strip()); db.add(row); db.commit()
    svc.audit(db, user["sub"], "note.created", {"case_number": cn, "note_id": row.id})
    return {"id": row.id, "case_number": row.case_number, "author": row.author, "body": row.body, "created_at": row.created_at.isoformat() if row.created_at else None}


@app.get("/notes")
def notes(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    cn = _scope(db, user, case_number, required=False)
    q = db.query(Note)
    if cn: q = q.filter_by(case_number=cn)
    hide = not _allowed(user["role"], "note") and not _allowed(user["role"], "sensitive_read")  # e.g. auditor: no case narrative
    return [{"id": n.id, "case_number": n.case_number, "author": n.author, "body": "[hidden for your role]" if hide else n.body, "created_at": n.created_at.isoformat() if n.created_at else None} for n in q.order_by(Note.id.desc()).limit(500).all()]


@app.post("/entities")
def add_entity(x: EntityIn, db: Session = Depends(get_db), user=Depends(require_perm("write"))):
    cn = _scope(db, user, x.case_number or (x.attributes or {}).get("case_number"))
    try: result = svc.add_entity(db, x, user["sub"], case_number=cn)
    except ValueError as exc: raise _bad(exc)
    if result.get("status") == "created":
        _features.hooks.emit("entity_created", db=db, user=user, case_number=cn, ref=result.get("external_id"))
    return result


@app.get("/entities")
def entities(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    return svc.entities(db, _scope(db, user, case_number, required=False), role=user["role"])


@app.post("/relationships")
def add_relationship(x: RelationshipIn, db: Session = Depends(get_db), user=Depends(require_perm("write"))):
    cn = _scope(db, user, x.case_number or (x.metadata or {}).get("case_number"))
    try: result = svc.add_relationship(db, x, user["sub"], case_number=cn)
    except ValueError as exc: raise _bad(exc)
    _features.hooks.emit("relationship_created", db=db, user=user, case_number=cn, ref=result.get("id"))
    return result


@app.get("/relationships")
def relationships(case_number: str | None = None, include_candidates: bool = False, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    return svc.relationships(db, _scope(db, user, case_number, required=False), include_candidates=include_candidates)


@app.post("/events")
def add_event(x: EventIn, db: Session = Depends(get_db), user=Depends(require_perm("write"))):
    cn = _scope(db, user, x.case_number or (x.metadata or {}).get("case_number"))
    try: return svc.add_event(db, x, user["sub"], case_number=cn)
    except ValueError as exc: raise _bad(exc)


@app.get("/events")
def events(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    return svc.events(db, _scope(db, user, case_number, required=False))


# ------------------------------------------------------------------ graph (masked for restricted roles)
def _graph(db, user, case_number, include_candidates=False):
    cn = _scope(db, user, case_number, required=False)
    svc.ensure_graph(db, cn, include_candidates=include_candidates)
    return cn


@app.get("/graph/summary")
def summary(case_number: str | None = None, include_candidates: bool = False, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    _graph(db, user, case_number, include_candidates)
    return svc.graph.summary()


@app.get("/graph/path")
def path(source: str, target: str, case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    _graph(db, user, case_number)
    return svc.graph.path_details(source, target)


@app.get("/graph/hidden-connections")
def hidden_connections(source: str, target: str, max_hops: int = 4, case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    _graph(db, user, case_number)
    return {"paths": svc.graph.hidden_connections(source, target, max_hops)}


@app.get("/graph/centrality")
def centrality(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    _graph(db, user, case_number)
    return svc.graph.centrality()


@app.get("/graph/communities")
def communities(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    _graph(db, user, case_number)
    return {"communities": svc.graph.communities()}


@app.get("/graph/neighborhood")
def neighborhood(entity_id: str, hops: int = 2, entity_type: str | None = None, relation: str | None = None, case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    active_case = _graph(db, user, case_number)
    hops = max(0, min(int(hops), 5))
    node_ids = svc.graph.subgraph(entity_id, hops, [entity_type] if entity_type else None, relation)
    node_set = set(node_ids)
    all_nodes = [n for n in svc.graph.nodes() if n.get("id") in node_set]
    all_edges = [e for e in svc.graph.edges() if e.get("source") in node_set and e.get("target") in node_set]
    truncated = len(all_nodes) > 180 or len(all_edges) > 360
    nodes = [mask_node(n, user["role"]) for n in all_nodes[:180]]
    visible_ids = {n.get("id") for n in nodes}
    edges = [e for e in all_edges if e.get("source") in visible_ids and e.get("target") in visible_ids][:360]
    return {"entity": entity_id, "case_number": active_case, "hops": hops, "center_found": entity_id in svc.graph.g,
            "nodes": nodes, "edges": edges, "truncated": truncated, "total_nodes": len(all_nodes), "total_edges": len(all_edges)}


@app.get("/graph/nodes")
def graph_nodes(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    _graph(db, user, case_number)
    return {"nodes": [mask_node(n, user["role"]) for n in svc.graph.nodes()]}


@app.get("/graph/edges")
def graph_edges(case_number: str | None = None, include_candidates: bool = False, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    _graph(db, user, case_number, include_candidates)
    return {"edges": svc.graph.edges()}


@app.get("/search")
def search(q: str, case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("basic_search"))):
    cn = _scope(db, user, case_number, required=False)
    q = q.lower().strip()[:100]
    if len(q) < 2:
        raise HTTPException(400, "Search text must be at least 2 characters")
    ents = [e for e in svc.entities(db, cn, role=user["role"]) if q in e["external_id"].lower() or q in e["name"].lower() or q in e["type"].lower()]
    rels = [r for r in svc.relationships(db, cn) if q in r["source"].lower() or q in r["target"].lower() or q in r["relation"].lower() or q in r["source_ref"].lower()]
    docs = []
    if _allowed(user["role"], "sensitive_read"):
        docs = [d for d in svc.documents(db, cn) if q in d["filename"].lower() or q in d["document_id"].lower() or q in (d.get("fir_number") or "").lower()]
    return {"entities": ents[:100], "relationships": rels[:100], "documents": docs[:50]}


# ------------------------------------------------------------------ documents / ingestion
@app.get("/documents")
def documents(case_number: str | None = None, group_id: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("sensitive_read"))):
    return {"documents": svc.documents(db, _scope(db, user, case_number, required=False), group_id=group_id)}


@app.get("/documents/{document_id}")
def document(document_id: str, db: Session = Depends(get_db), user=Depends(require_perm("sensitive_read"))):
    _doc_case(db, user, document_id)
    try: return svc.document(db, document_id)
    except ValueError as exc: raise _bad(exc)


@app.post("/documents/extract")
async def extract_document(file: UploadFile = File(...), language: str = Form("auto"), case_number: str = Form(""), group_id: str = Form(""),
                           db: Session = Depends(get_db), user=Depends(require_perm("write"))):
    cn = _scope(db, user, case_number, required=True) if case_number.strip() else ""
    data = await _read_upload(file)
    try:
        result = await run_in_threadpool(svc.extract_document, db, data, file.filename or "upload.txt", language,
                                         case_number=cn, group_id=group_id, media_type=file.content_type or "application/octet-stream",
                                         actor=user["sub"], user=user)
    except ValueError as exc:
        raise _bad(exc)
    _features.hooks.emit("document_extracted", db=db, user=user, result=result, case_number=cn)
    return result


@app.post("/documents/{document_id}/review")
def review_document(document_id: str, x: DocumentReviewIn, db: Session = Depends(get_db), user=Depends(require_perm("review"))):
    _doc_case(db, user, document_id)
    try: result = svc.review_document(db, document_id, x.model_dump(), user["sub"])
    except ValueError as exc: raise _bad(exc)
    _features.hooks.emit("document_reviewed", db=db, user=user, document_id=document_id, payload=x.model_dump(), result=result)
    return result


@app.post("/documents/{document_id}/reprocess")
def reprocess_document(document_id: str, db: Session = Depends(get_db), user=Depends(require_perm("write"))):
    cn = _doc_case(db, user, document_id)
    try: result = svc.reprocess_document(db, document_id, user["sub"])
    except ValueError as exc: raise _bad(exc)
    _features.hooks.emit("document_reprocessed", db=db, user=user, document_id=document_id, case_number=cn)
    return result


@app.post("/ingest/table")
async def ingest_table(file: UploadFile = File(...), case_number: str = Form(""), db: Session = Depends(get_db), user=Depends(require_perm("write"))):
    cn = _scope(db, user, case_number)
    data = await _read_upload(file)
    try:
        result = await run_in_threadpool(svc.ingest_table, db, data, file.filename or "upload.csv", user["sub"], cn)
    except ValueError as exc:
        raise _bad(exc)
    _features.hooks.emit("table_ingested", db=db, user=user, result=result, case_number=cn)
    return result


@app.get("/entity-resolution")
def entity_resolution(value: str, entity_type: str | None = None, case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    cn = _scope(db, user, case_number)
    return {"query": value[:200], "matches": svc.entity_candidates(db, value[:200], entity_type, cn)}


# ------------------------------------------------------------------ analytics
@app.get("/analytics/cdr")
def cdr_analytics(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    return svc.cdr_analytics(db, _scope(db, user, case_number, required=False))


@app.get("/analytics/financial")
def financial_analytics(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    return svc.financial_analytics(db, _scope(db, user, case_number, required=False))


@app.get("/analytics/network-evolution")
def network_evolution(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    return svc.network_evolution(db, _scope(db, user, case_number, required=False))


@app.get("/analytics/overview")
def analytics_overview(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    return svc.analytics_overview(db, _scope(db, user, case_number, required=False))


@app.get("/recommendations")
def recommendations(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    return svc.recommendations(db, _scope(db, user, case_number, required=False))


@app.post("/analytics/run")
def analytics(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    return svc.run_analytics(db, _scope(db, user, case_number), user["sub"])


@app.get("/analytics/contradictions")
def contradictions(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    return {"contradictions": svc.contradictions(db, _scope(db, user, case_number, required=False))}


# ------------------------------------------------------------------ cases / tasks / alerts
@app.post("/cases")
def add_case(x: CaseIn, db: Session = Depends(get_db), user=Depends(require_perm("write"))):
    x = CaseIn(case_number=_clean_case(x.case_number), title=x.title.strip() or "Untitled case", summary=x.summary, status="Active")
    result = svc.add_case(db, x, user["sub"])
    _features.hooks.emit("case_created", db=db, user=user, case_number=x.case_number)
    return result


@app.post("/workbench/new")
def new_workbench(x: CaseIn, db: Session = Depends(get_db), user=Depends(require_perm("write"))):
    cn = _clean_case(x.case_number)
    if db.query(Case).filter_by(case_number=cn).first():
        raise HTTPException(409, "A workbench with this identifier already exists")
    result = svc.add_case(db, CaseIn(case_number=cn, title=x.title.strip() or "Fresh FIR Investigation", summary=x.summary.strip(), status="Active"), user["sub"])
    _features.hooks.emit("case_created", db=db, user=user, case_number=cn)
    return result


@app.get("/cases")
def cases(db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    return svc.cases(db, user)


@app.get("/cases/{case_number}/workspace")
def workspace(case_number: str, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    cn = _scope(db, user, case_number)
    try: return svc.workspace(db, cn, role=user["role"])
    except ValueError as exc: raise _bad(exc)


@app.post("/tasks")
def add_task(x: TaskIn, db: Session = Depends(get_db), user=Depends(require_perm("assign"))):
    _scope(db, user, x.case_number)
    try: return svc.add_task(db, x, user["sub"])
    except ValueError as exc: raise _bad(exc)


@app.get("/tasks")
def tasks(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    return svc.tasks(db, _scope(db, user, case_number, required=False))


@app.get("/alerts")
def alerts(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("read"))):
    return svc.alerts(db, _scope(db, user, case_number, required=False))


@app.patch("/alerts/{code}")
def update_alert(code: str, x: AlertUpdate, db: Session = Depends(get_db), user=Depends(require_perm("review"))):
    row = db.query(Alert).filter_by(code=code).first()
    if not row: raise HTTPException(404, "Alert not found")
    _scope(db, user, row.case_number)
    try: return svc.update_alert(db, code, x.status, user["sub"])
    except ValueError as exc: raise _bad(exc)


# ------------------------------------------------------------------ evidence
_INLINE_SAFE = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


@app.post("/evidence/upload")
async def evidence_upload(file: UploadFile = File(...), source: str = Form("Investigator upload"), case_number: str = Form(""), document_id: str = Form(""),
                          db: Session = Depends(get_db), user=Depends(require_perm("write"))):
    cn = _scope(db, user, case_number)
    data = await _read_upload(file)
    try:
        result = await run_in_threadpool(svc.evidence_upload, db, data, file.filename or "evidence.bin", file.content_type or "application/octet-stream",
                                         source[:255], user["sub"], case_number=cn, document_id=document_id[:100])
    except ValueError as exc:
        raise _bad(exc)
    _features.hooks.emit("evidence_uploaded", db=db, user=user, result=result, case_number=cn)
    return result


@app.get("/evidence")
def evidence(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("sensitive_read"))):
    return {"evidence": svc.evidence(db, case_number=_scope(db, user, case_number, required=False))}


@app.get("/evidence/{evidence_id}/download")
def download_evidence(evidence_id: str, case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("sensitive_read"))):
    cn = _scope(db, user, case_number)
    row = db.query(Evidence).filter_by(evidence_id=evidence_id, case_number=cn).first()
    if not row: raise HTTPException(404, "Evidence not found")
    from backend.app.services.app_service import _abs_path
    stored = _abs_path(row.stored_path)
    if not stored.exists(): raise HTTPException(404, "Stored evidence file is missing")
    svc.audit(db, user["sub"], "evidence.downloaded", {"evidence_id": evidence_id, "case_number": cn})  # chain of custody
    _features.hooks.emit("evidence_downloaded", db=db, user=user, evidence_id=evidence_id, case_number=cn)
    # Never echo an uploader-controlled content type: only known-safe types may render inline.
    media = _INLINE_SAFE.get(stored.suffix.lower(), "application/octet-stream")
    from security import evidence_crypto
    if evidence_crypto.is_encrypted(stored):  # encrypted at rest: decrypt for the authorised download
        try: plain = evidence_crypto.read_plain(stored)
        except evidence_crypto.EvidenceCryptoError as exc: raise HTTPException(500, f"Stored evidence cannot be decrypted: {exc}")
        return Response(content=plain, media_type=media, headers={"Content-Disposition": f'attachment; filename="{Path(row.filename or evidence_id).name}"'})
    return FileResponse(str(stored), media_type=media, filename=Path(row.filename or evidence_id).name, content_disposition_type="attachment")


@app.get("/evidence/{evidence_id}/verify")
def verify_evidence(evidence_id: str, case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("sensitive_read"))):
    cn = _scope(db, user, case_number)
    try: result = svc.verify_evidence(db, evidence_id, user["sub"], case_number=cn)
    except ValueError as exc: raise _bad(exc)
    _features.hooks.emit("evidence_verified", db=db, user=user, evidence_id=evidence_id, result=result, case_number=cn)
    return result


# ------------------------------------------------------------------ FIR intelligence / timeline
@app.get("/documents/{document_id}/related")
def related_documents(document_id: str, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    cn = _doc_case(db, user, document_id)
    return {"document_id": document_id, "case_number": cn, "related_firs": svc.related_firs(db, cn, document_id)}


@app.get("/fir-relations")
def fir_relations(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    return svc.fir_relationships(db, _scope(db, user, case_number, required=False))


@app.get("/timeline")
def timeline(case_number: str | None = None, category: str | None = None, fir_number: str | None = None, q: str | None = None,
             date_from: str | None = None, date_to: str | None = None, geo_only: bool = False,
             gap_hours: float = Query(24, ge=0.5, le=24 * 365), limit: int = Query(5000, ge=1, le=20000),
             db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    """Events with category, FIR, map position (recorded / linked location / offline gazetteer) and quiet gaps. Names are masked per role."""
    return svc.timeline_intelligence(db, _scope(db, user, case_number, required=False), role=user["role"], category=category, fir_number=fir_number,
                                     q=(q or "")[:200] or None, date_from=date_from, date_to=date_to, geo_only=geo_only, gap_hours=gap_hours, limit=limit)


@app.get("/timeline/compare")
def timeline_compare(fir_numbers: str, case_number: str, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    cn = _scope(db, user, case_number)
    return svc.timeline_compare(db, cn, [x.strip() for x in fir_numbers.split(",") if x.strip()][:20], role=user["role"])


@app.get("/gazetteer/search")
def gazetteer_search(q: str, limit: int = Query(10, ge=1, le=25), user=Depends(require_perm("read"))):
    """Offline place-name lookup (Indian cities, district HQs, states, Delhi/NCR localities) with approximate coordinates."""
    from backend.app.services.timeline_engine import gazetteer
    return {"query": q[:100], "places": [{"name": p["name"], "state": p.get("state", ""), "kind": p.get("kind", ""), "lat": p["lat"], "lon": p["lon"]}
                                         for p in gazetteer().search(q[:100], limit)]}


@app.get("/models/status")
def models_status(user=Depends(require_perm("read"))):
    return svc.model_registry()


# ------------------------------------------------------------------ audit
@app.get("/audit")
def audit(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_any_perm("audit", "audit_case"))):
    # Admins and auditors may read the global log; investigators/supervisors only the events of a case they can access.
    # The demo role has neither permission: it never sees the audit log.
    if not case_number and user["role"] == "auditor":
        return {"events": svc.audit_events(db, None)}
    cn = _scope(db, user, case_number, required=False)
    return {"events": svc.audit_events(db, cn)}


@app.get("/audit/verify")
def audit_verify(db: Session = Depends(get_db), user=Depends(require_any_perm("audit", "audit_case"))):
    return svc.verify_audit_chain(db)


# ------------------------------------------------------------------ copilot / reports
@app.post("/copilot")
def copilot(x: CopilotIn, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    return svc.copilot(x.query, db, _scope(db, user, x.case_number))


@app.get("/copilot/suggestions")
def copilot_suggestions(case_number: str | None = None, db: Session = Depends(get_db), user=Depends(require_perm("analyze"))):
    return {"suggestions": svc.copilot_suggestions(db, _scope(db, user, case_number))}


def _stamp(db, user, case_number, kind, fmt, body):
    """Ledger record + visible watermark for an export (see features/integrity.py); returns the body unchanged if that module is off."""
    mod = _features.LOADED.get("integrity")
    return mod.stamp_export(db, user, case_number, kind, fmt, body) if mod else body


def _report_case(db, user, case_number):
    cn = _scope(db, user, case_number)
    svc.audit(db, user["sub"], "report.generated", {"case_number": cn})
    return cn


@app.get("/report/{case_number}")
def report(case_number: str, db: Session = Depends(get_db), user=Depends(require_perm("report"))):
    cn = _report_case(db, user, case_number)
    try: return _stamp(db, user, cn, "report", "json", svc.report(db, cn))
    except ValueError as exc: raise _bad(exc)


@app.get("/report/{case_number}/csv")
def report_csv(case_number: str, db: Session = Depends(get_db), user=Depends(require_perm("report"))):
    cn = _report_case(db, user, case_number)
    try: content = _stamp(db, user, cn, "report", "csv", svc.report_csv(db, cn))
    except ValueError as exc: raise _bad(exc)
    return Response(content=content, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=investigation_report_{cn}.csv"})


@app.get("/report/{case_number}/docx")
def report_docx(case_number: str, db: Session = Depends(get_db), user=Depends(require_perm("report"))):
    cn = _report_case(db, user, case_number)
    try: content = _stamp(db, user, cn, "report", "docx", svc.report_docx(db, cn))
    except ValueError as exc: raise _bad(exc)
    return Response(content=content, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition": f"attachment; filename=investigation_report_{cn}.docx"})


@app.get("/report/{case_number}/pdf")
def report_pdf(case_number: str, db: Session = Depends(get_db), user=Depends(require_perm("report"))):
    cn = _report_case(db, user, case_number)
    try: content = _stamp(db, user, cn, "report", "pdf", svc.report_pdf(db, cn))
    except ValueError as exc: raise _bad(exc)
    return Response(content=content, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=investigation_report_{cn}.pdf"})


# ------------------------------------------------------------------ feature plug-ins
from types import SimpleNamespace  # noqa: E402

_features.mount_features(app, SimpleNamespace(
    svc=svc, scope=_scope, get_db=get_db, require_perm=require_perm, bad=_bad, settings=settings,
    SessionLocal=SessionLocal, mask_entity=mask_entity, mask_node=mask_node, doc_case=_doc_case,
    audit=lambda db, actor, action, details: svc.audit(db, actor, action, details),
    stamp=_stamp, web_dir=WEB_DIR, app=app, hooks=_features.hooks, graph_for=_graph,  # graph_for(db,user,case)->case; then ctx.svc.graph is that case's GraphEngine (.g = networkx)
))
