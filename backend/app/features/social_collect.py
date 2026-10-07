"""Social media intelligence: automatic collection (saved searches) through official APIs and public feeds.

An officer states WHAT to look for (an account, a keyword, a hashtag or a feed address), WHICH provider to ask, and the LEGAL BASIS. The server then fetches
the posts itself, on demand or on a repeat schedule, and hands them to the same import pipeline as a manual upload (reading, de-duplication, flagging, ledger).

Safeguards: official APIs and public feeds only (no scraping, no logins, no fake accounts); a provider is unavailable until the administrator sets its key;
every run is written to the audit trail and shows in the case's import history with its authority; repeat searches cannot run more often than the configured
minimum; the server refuses to contact private or local addresses. Table names start with ``sx_`` (working data, not evidence).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from ai_engine.social import collectors as col
from ai_engine.social import ingest
from backend.app.core.config import settings
from backend.app.db.database import Base
from backend.app.features import social as S

log = logging.getLogger("dcn.collect")
MAX_JOBS = 25
HTTP = None  # tests replace this with a fake ``http(method, url, ...) -> (status, body)``; None = real network


class CollectJob(Base):
    __tablename__ = "sx_collect_jobs"
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    provider = Column(String(20))
    kind = Column(String(10))
    query = Column(String(300))
    max_posts = Column(Integer, default=50)
    every_minutes = Column(Integer, default=0)  # 0 = only when an officer presses Run
    legal_basis = Column(Text, default="")
    active = Column(Boolean, default=True)
    created_by = Column(String(100), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    last_run_at = Column(DateTime, nullable=True)
    last_status = Column(String(300), default="")
    last_added = Column(Integer, default=0)
    total_added = Column(Integer, default=0)
    cursor = Column(String(60), default="")


class JobIn(BaseModel):
    provider: str = Field(max_length=20)
    kind: Literal["user", "keyword", "hashtag", "feed"]
    query: str = Field(min_length=2, max_length=300)
    max_posts: int = Field(default=50, ge=5, le=100)
    every_minutes: int = Field(default=0, ge=0, le=10080)
    legal_basis: str = Field(min_length=10, max_length=1000)
    run_now: bool = True


class WatchRunIn(BaseModel):
    provider: str = Field(max_length=20)
    max_posts: int = Field(default=30, ge=5, le=100)
    legal_basis: str = Field(min_length=10, max_length=1000)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cfg() -> dict:
    return {k: getattr(settings, k, "") for k in ("x_bearer_token", "youtube_api_key", "reddit_client_id", "reddit_client_secret", "reddit_user_agent",
                                                 "mastodon_instance", "mastodon_token")}


def _job_dict(j: CollectJob) -> dict:
    p = col.PROVIDERS.get(j.provider)
    return {"id": j.id, "provider": j.provider, "provider_label": p.label if p else j.provider, "kind": j.kind, "kind_label": col.KIND_LABEL.get(j.kind, j.kind),
            "query": j.query, "max_posts": j.max_posts, "every_minutes": j.every_minutes, "active": bool(j.active), "created_by": j.created_by,
            "created_at": j.created_at.isoformat() + "Z" if j.created_at else None, "last_run_at": j.last_run_at.isoformat() + "Z" if j.last_run_at else None,
            "last_status": j.last_status or "", "last_added": j.last_added or 0, "total_added": j.total_added or 0, "legal_basis": j.legal_basis,
            "synthetic": bool(p and p.synthetic)}


def collect_once(ctx, db, user, cn: str, provider: str, kind: str, query: str, max_posts: int, basis: str, cursor: str = "", note: str = "") -> tuple[dict, str]:
    """Fetch, normalise and import. Returns (import result, next cursor). Raises col.CollectError."""
    raw, nxt = col.fetch(provider, kind, query, max_posts, cursor, _cfg(), HTTP)
    recs = []
    for r in raw:
        rec = ingest.normalise_record(r, "UTC")
        if rec:
            rec["handle"] = rec["handle"] or "unknown"
            recs.append(rec)
    p = col.PROVIDERS[provider]
    if not recs:
        return {"posts_added": 0, "duplicates_skipped": 0, "accounts_added": 0, "flags_raised": 0, "import_id": "", "fetched": 0}, nxt
    blob = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    meta = {"source": f"{p.label}: {col.KIND_LABEL.get(kind, kind).lower()} '{query}' (collected by the server)" + (" [synthetic]" if p.synthetic else ""),
            "source_type": "collected", "filename": "", "legal_basis": " ".join(basis.split()), "note": note,
            "sha256": hashlib.sha256(blob).hexdigest(), "bytes_in": len(blob), "assumed_timezone": "UTC", "collected_at": _now(), "warnings": []}
    res = S.run_import(ctx, db, user, cn, recs, meta)
    res["fetched"] = len(recs)
    return res, nxt


def _execute(ctx, db, user, job: CollectJob) -> dict:
    """Run one saved search and record the outcome on the job (errors are stored, not raised, when ``quiet``)."""
    try:
        res, nxt = collect_once(ctx, db, user, job.case_number, job.provider, job.kind, job.query, job.max_posts, job.legal_basis, job.cursor or "", f"Saved search #{job.id}")
    except col.CollectError as exc:
        job.last_run_at, job.last_status, job.last_added = _now(), f"Failed: {exc}", 0
        db.commit()
        raise
    job.last_run_at, job.cursor = _now(), nxt or job.cursor
    job.last_added = res["posts_added"]
    job.total_added = (job.total_added or 0) + res["posts_added"]
    job.last_status = (f"OK: {res['posts_added']} new post(s), {res['duplicates_skipped']} already held" if res["fetched"] else "OK: nothing found")
    db.commit()
    ctx.audit(db, user["sub"], "social.collected", {"case_number": job.case_number, "job_id": job.id, "provider": job.provider, "kind": job.kind,
                                                       "query": job.query, "fetched": res["fetched"], "posts_added": res["posts_added"]})
    return res


def run_due(ctx) -> int:
    """Run every active repeat search that is due. Called by the background thread (and by tests). Returns how many ran."""
    from backend.app.models.models import User
    ran = 0
    with ctx.SessionLocal() as db:
        now = _now()
        for job in db.query(CollectJob).filter(CollectJob.active.is_(True), CollectJob.every_minutes > 0).all():
            if job.last_run_at and now - job.last_run_at < timedelta(minutes=max(job.every_minutes, settings.social_collect_min_minutes)):
                continue
            u = db.query(User).filter_by(username=job.created_by).first()
            if not u or not getattr(u, "active", True) or u.role not in ("admin", "supervisor", "demo"):
                job.active, job.last_status = False, "Paused: the officer who set this up can no longer run it"
                db.commit()
                continue
            user = {"sub": u.username, "role": u.role}
            try:
                ctx.scope(db, user, job.case_number)
                _execute(ctx, db, user, job)
                ran += 1
            except col.CollectError:
                ran += 1
            except HTTPException:
                job.active, job.last_status = False, "Paused: no access to this case any more"
                db.commit()
            except Exception:  # noqa: BLE001
                log.exception("collect job %s failed", job.id)
                db.rollback()
    return ran


_thread = None


def on_startup(ctx):
    global _thread
    if not settings.social_autocollect or _thread is not None:
        return

    def loop():
        while True:
            time.sleep(60)
            try:
                run_due(ctx)
            except Exception:  # noqa: BLE001
                log.exception("collect loop")

    _thread = threading.Thread(target=loop, name="social-collect", daemon=True)
    _thread.start()


def build(ctx):
    router = APIRouter(prefix="/social", tags=["social"])
    perm = ctx.require_perm

    def fail(exc: col.CollectError):
        raise HTTPException(502, str(exc))

    @router.get("/collect/providers")
    def providers(user=Depends(perm("read"))):
        return {"providers": col.status(_cfg()), "kinds": col.KIND_LABEL, "min_minutes": settings.social_collect_min_minutes, "autocollect": settings.social_autocollect,
                "notice": ("Posts are fetched only through official APIs or public feeds, with keys your administrator has set. Nothing is scraped and no account is logged into. "
                           "Every run is recorded with your legal basis. Collected posts are ordinary imports: read, de-duplicated and flagged the same way.")}

    @router.get("/{case}/collect/jobs")
    def jobs(case: str, db=Depends(ctx.get_db), user=Depends(perm("read"))):
        cn = ctx.scope(db, user, case)
        return {"case_number": cn, "jobs": [_job_dict(j) for j in db.query(CollectJob).filter_by(case_number=cn).order_by(CollectJob.id.desc()).all()]}

    @router.post("/{case}/collect/jobs")
    def add_job(case: str, x: JobIn, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        cn = ctx.scope(db, user, case)
        p = col.PROVIDERS.get(x.provider)
        if not p:
            raise HTTPException(400, "Unknown provider")
        if x.kind not in p.kinds:
            raise HTTPException(400, f"{p.label} cannot search by {col.KIND_LABEL.get(x.kind, x.kind).lower()}")
        missing = [k.upper() for k in p.env if not _cfg().get(k)]
        if missing:
            raise HTTPException(409, f"{p.label} is not set up on this server. The administrator must set {', '.join(missing)}.")
        if len(x.legal_basis.split()) < 3:
            raise HTTPException(400, "State the legal basis or authority for collecting this material (at least a short sentence).")
        if x.every_minutes and x.every_minutes < settings.social_collect_min_minutes:
            raise HTTPException(400, f"A repeat search cannot run more often than every {settings.social_collect_min_minutes} minutes")
        if db.query(CollectJob).filter_by(case_number=cn).count() >= MAX_JOBS:
            raise HTTPException(400, f"A case can hold at most {MAX_JOBS} saved searches. Remove one first.")
        job = CollectJob(case_number=cn, provider=x.provider, kind=x.kind, query=x.query.strip(), max_posts=x.max_posts, every_minutes=x.every_minutes,
                         legal_basis=" ".join(x.legal_basis.split()), created_by=user["sub"], last_status="Not run yet")
        db.add(job)
        db.commit()
        ctx.audit(db, user["sub"], "social.collect_job_created", {"case_number": cn, "job_id": job.id, "provider": x.provider, "kind": x.kind, "query": job.query,
                                                                    "every_minutes": x.every_minutes})
        out = {"job": None, "result": None}
        if x.run_now:
            try:
                out["result"] = _execute(ctx, db, user, job)
            except col.CollectError as exc:
                if not x.every_minutes:  # a one-off that failed is not worth keeping
                    db.delete(job)
                    db.commit()
                    fail(exc)
        out["job"] = _job_dict(job) if x.every_minutes or not x.run_now else None
        if out["result"] is not None and not x.every_minutes:
            db.delete(job)  # a one-off leaves no saved search behind; the import history keeps the record
            db.commit()
        return out

    def _get(db, cn, job_id):
        j = db.query(CollectJob).filter_by(case_number=cn, id=job_id).first()
        if not j:
            raise HTTPException(404, "Saved search not found")
        return j

    @router.post("/{case}/collect/jobs/{job_id}/run")
    def run_job(case: str, job_id: int, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        cn = ctx.scope(db, user, case)
        j = _get(db, cn, job_id)
        try:
            res = _execute(ctx, db, user, j)
        except col.CollectError as exc:
            fail(exc)
        return {"job": _job_dict(j), "result": res}

    @router.post("/{case}/collect/jobs/{job_id}/toggle")
    def toggle_job(case: str, job_id: int, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        cn = ctx.scope(db, user, case)
        j = _get(db, cn, job_id)
        j.active = not j.active
        db.commit()
        ctx.audit(db, user["sub"], "social.collect_job_paused" if not j.active else "social.collect_job_resumed", {"case_number": cn, "job_id": j.id})
        return {"job": _job_dict(j)}

    @router.delete("/{case}/collect/jobs/{job_id}")
    def delete_job(case: str, job_id: int, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        cn = ctx.scope(db, user, case)
        j = _get(db, cn, job_id)
        db.delete(j)
        db.commit()
        ctx.audit(db, user["sub"], "social.collect_job_removed", {"case_number": cn, "job_id": job_id})
        return {"removed": job_id}

    @router.post("/{case}/collect/watchlist")
    def from_watchlist(case: str, x: WatchRunIn, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        """One pass over the case's active watch-list terms (handles, hashtags, keywords) against one provider."""
        from backend.app.features.social_plus import WatchTerm
        cn = ctx.scope(db, user, case)
        p = col.PROVIDERS.get(x.provider)
        if not p:
            raise HTTPException(400, "Unknown provider")
        if len(x.legal_basis.split()) < 3:
            raise HTTPException(400, "State the legal basis or authority for collecting this material (at least a short sentence).")
        missing = [k.upper() for k in p.env if not _cfg().get(k)]
        if missing:
            raise HTTPException(409, f"{p.label} is not set up on this server. The administrator must set {', '.join(missing)}.")
        kmap = {"handle": "user", "hashtag": "hashtag", "keyword": "keyword"}
        terms = [t for t in db.query(WatchTerm).filter_by(case_number=cn, active=True).order_by(WatchTerm.id.asc()).all() if t.kind in kmap and kmap[t.kind] in p.kinds][:10]
        if not terms:
            raise HTTPException(400, f"No active watch-list terms that {p.label} can search (handles, hashtags or keywords).")
        rows, added = [], 0
        for t in terms:
            try:
                res, _ = collect_once(ctx, db, user, cn, x.provider, kmap[t.kind], t.value, x.max_posts, x.legal_basis, "", "From the watch list")
                rows.append({"term": t.value, "kind": t.kind, "fetched": res["fetched"], "posts_added": res["posts_added"], "error": ""})
                added += res["posts_added"]
            except col.CollectError as exc:
                rows.append({"term": t.value, "kind": t.kind, "fetched": 0, "posts_added": 0, "error": str(exc)})
        ctx.audit(db, user["sub"], "social.collected_watchlist", {"case_number": cn, "provider": x.provider, "terms": len(terms), "posts_added": added})
        return {"case_number": cn, "results": rows, "posts_added": added}

    return router
