"""Demo-data toggle: load / remove the bundled synthetic dataset without ever touching real data.

Endpoints (permission ``demo_manage``: the demo account and admins)

* GET  /demo/status   -> {enabled, loaded, available, cases, counts, ...}
* POST /demo/enable   -> loads the synthetic dataset if it is not loaded yet (idempotent)
* POST /demo/disable  -> deletes ONLY demo-flagged data (cases with ``is_demo`` and everything that belongs to them)
* POST /demo/load     -> legacy alias of /demo/enable

What "belongs to a demo case" means:

* every row of every table that has a ``case_number`` column (documents, evidence, alerts, tasks, notes,
  members, feature tables ...) whose case number is a demo case;
* relationships and events tagged with a demo case (or, for untagged legacy rows, synthetic rows whose endpoints are
  all demo entities);
* entities whose every case tag is a demo case (plus untagged bundled-seed entities that nothing else references).

Anything that also points at a real case is kept. Stored evidence files are removed only when they are inside the
evidence directory and no remaining record references them. The audit log is never touched (the toggle is logged).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException

from backend.app.db.database import Base
from backend.app.models.models import Alert, Case, DocumentRecord, Entity, Event, Evidence, Note, Relationship, Task

log = logging.getLogger("dcn.demo")
_lock = threading.Lock()
ROOT = Path(__file__).resolve().parents[3]
# External ids produced by scripts/seed_demo.py (people, phones, vehicles, accounts, locations, organisations, FIR nodes).
_SEED_ID = re.compile(r"^(P\d{3}|PH\d{3}|V\d{3}|A\d{3}|L\d{3}|ORG\d{2}|F\d{3})$")
_CHUNK = 400


def _j(text, default):
    try:
        v = json.loads(text or "")
        return v if isinstance(v, type(default)) else default
    except (ValueError, TypeError):
        return default


def _demo_cases(db) -> set[str]:
    return {c.case_number for c in db.query(Case).filter(Case.is_demo.is_(True)).all()}


def _chunks(items, n=_CHUNK):
    items = list(items)
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _plan(db) -> dict:
    """Work out exactly which rows are demo data (read-only)."""
    dcs = _demo_cases(db)
    plan = {"cases": dcs, "entities": set(), "relationships": [], "events": [], "orphans": set()}
    if not dcs:
        return plan
    # -- entities
    for e in db.query(Entity).all():
        a = _j(e.attributes, {})
        tags = set(a.get("case_numbers") or [])
        if a.get("case_number"):
            tags.add(a["case_number"])
        if tags and tags <= dcs:
            plan["entities"].add(e.external_id)
        elif not tags and (a.get("demo_seed") or _SEED_ID.match(e.external_id or "")):
            plan["orphans"].add(e.external_id)  # only removed when nothing real references them
    demo_ent = plan["entities"] | plan["orphans"]
    # -- relationships
    for r in db.query(Relationship).all():
        cn = _j(r.metadata_json, {}).get("case_number")
        if cn in dcs:
            plan["relationships"].append(r.id)
        elif not cn and (r.model_version or "").startswith("synthetic") and r.source_id in demo_ent and r.target_id in demo_ent:
            plan["relationships"].append(r.id)
    # -- events
    for ev in db.query(Event).all():
        cn = _j(ev.metadata_json, {}).get("case_number")
        if cn in dcs:
            plan["events"].append(ev.id)
        elif not cn and ev.entity_id in demo_ent and (not ev.related_entity_id or ev.related_entity_id in demo_ent) \
                and str(ev.source_ref or "").startswith(("CDR-", "BANK-", "CAM-", "SURV-")):
            plan["events"].append(ev.id)
    return plan


def _case_tables() -> list[sa.Table]:
    tabs = [t for t in Base.metadata.sorted_tables if "case_number" in t.c and t.name not in {"audit_events", "users", "ledger_blocks", "ledger_rows"}]
    return sorted(tabs, key=lambda t: t.name == "cases")  # the cases table goes last


def _counts(db, plan) -> dict:
    dcs = list(plan["cases"])

    def n(model):
        return db.query(model).filter(model.case_number.in_(dcs)).count() if dcs else 0
    return {"entities": len(plan["entities"]) + len(plan["orphans"]), "relationships": len(plan["relationships"]),
            "events": len(plan["events"]), "alerts": n(Alert), "evidence": n(Evidence), "documents": n(DocumentRecord),
            "tasks": n(Task), "notes": n(Note)}


def _status(ctx, db) -> dict:
    plan = _plan(db)
    cases = sorted(plan["cases"])
    loaded = bool(cases)
    return {
        "enabled": loaded, "loaded": loaded, "available": bool(ctx.settings.is_demo),
        "case_count": len(cases), "cases": cases,
        "counts": _counts(db, plan) if loaded else {k: 0 for k in ("entities", "relationships", "events", "alerts", "evidence", "documents", "tasks", "notes")},
        "recommended_case": cases[0] if cases else "CASE-2026-101",
        "source": "bundled deterministic synthetic dataset",
    }


def _delete_files(db, paths: set[str]) -> int:
    """Remove stored files that lie inside the evidence directory and are no longer referenced."""
    from backend.app.services import app_service as A
    root = A.EVIDENCE_DIR.resolve()
    still = {p for (p,) in db.query(Evidence.stored_path).all()} | {p for (p,) in db.query(DocumentRecord.stored_path).all()}
    removed = 0
    for stored in paths:
        if not stored or stored in still:
            continue
        try:
            p = A._abs_path(stored).resolve()
            if p.is_file() and p.is_relative_to(root):
                p.unlink(); removed += 1
        except OSError:
            log.warning("could not remove demo file %s", stored)
    return removed


def _remove(db) -> dict:
    plan = _plan(db)
    dcs = list(plan["cases"])
    removed: dict[str, int] = {}
    if not dcs:
        return {"cases": 0}
    files = {p for (p,) in db.query(Evidence.stored_path).filter(Evidence.case_number.in_(dcs)).all()}
    files |= {p for (p,) in db.query(DocumentRecord.stored_path).filter(DocumentRecord.case_number.in_(dcs)).all()}
    for name, table, ids in (("relationships", Relationship.__table__, plan["relationships"]), ("events", Event.__table__, plan["events"])):
        total = 0
        for part in _chunks(ids):
            total += db.execute(table.delete().where(table.c.id.in_(part))).rowcount or 0
        removed[name] = total
    # untagged seed entities (locations, organisations) go only if nothing left points at them
    referenced = {x for row in db.query(Relationship.source_id, Relationship.target_id).all() for x in row}
    referenced |= {x for row in db.query(Event.entity_id, Event.related_entity_id).all() for x in row}
    doomed = set(plan["entities"]) | {x for x in plan["orphans"] if x not in referenced}
    total = 0
    for part in _chunks(doomed):
        total += db.execute(Entity.__table__.delete().where(Entity.__table__.c.external_id.in_(part))).rowcount or 0
    removed["entities"] = total
    for t in _case_tables():
        cnt = 0
        for part in _chunks(dcs, 200):
            cnt += db.execute(t.delete().where(t.c.case_number.in_(part))).rowcount or 0
        removed[t.name] = cnt
    db.commit()
    removed["files"] = _delete_files(db, files)
    return removed


def _run_seed() -> None:
    script = ROOT / "scripts" / "seed_demo.py"
    if not script.exists():
        raise HTTPException(500, "Bundled demo dataset generator is missing from this installation")
    try:
        proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT), capture_output=True, text=True, timeout=180, check=False)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "Demo dataset loading timed out. Check the backend terminal for details.") from exc
    if proc.returncode != 0:
        log.error("Demo loader failed: %s", (proc.stderr or proc.stdout)[-3000:])
        raise HTTPException(500, "Demo dataset failed to load (details are in the server log)")


def build(ctx):
    router = APIRouter(prefix="/demo", tags=["demo"])
    perm = ctx.require_perm("demo_manage")
    get_db = ctx.get_db

    @router.get("/status")
    def demo_status(db=Depends(get_db), user=Depends(perm)):
        return _status(ctx, db)

    def _enable(db, user):
        if not ctx.settings.is_demo:
            raise HTTPException(403, "Demo data can only be loaded when APP_ENV=demo")
        with _lock:
            if _demo_cases(db):
                st = _status(ctx, db)
                st["message"] = "Demo data is already on; existing data was preserved."
                return st
            _run_seed()
            db.expire_all()
            st = _status(ctx, db)
            if not st["loaded"]:
                raise HTTPException(500, "Demo loader completed without creating the expected demo cases")
            ctx.audit(db, user["sub"], "demo.enabled", {"case_count": st["case_count"], "counts": st["counts"], "synthetic_only": True})
            ctx.hooks.emit("demo_enabled", db=db, user=user, detail={"case_count": st["case_count"]})
            st["message"] = "Demo data is now on."
            return st

    @router.post("/enable")
    def demo_enable(db=Depends(get_db), user=Depends(perm)):
        return _enable(db, user)

    @router.post("/load")
    def demo_load(db=Depends(get_db), user=Depends(perm)):
        """Legacy name kept for older clients; identical to /demo/enable."""
        return _enable(db, user)

    @router.post("/disable")
    def demo_disable(db=Depends(get_db), user=Depends(perm)):
        with _lock:
            gone_cases = sorted(_demo_cases(db))
            removed = _remove(db)
            db.expire_all()
            st = _status(ctx, db)
            ctx.audit(db, user["sub"], "demo.disabled", {"removed": removed, "cases": gone_cases, "synthetic_only": True})
            ctx.hooks.emit("demo_disabled", db=db, user=user, cases=gone_cases, detail={k: v for k, v in removed.items() if isinstance(v, int)})
            st["removed"] = removed
            st["message"] = "Demo data is now off. Only demonstration data was removed." if removed.get("cases") else "Demo data was already off."
            return st

    return router
