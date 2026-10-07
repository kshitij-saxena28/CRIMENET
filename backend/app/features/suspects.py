"""Prime suspects / persons of interest: an explainable lead-priority ranking with a human verification workflow.

The scoring itself lives in ``ai_engine/suspects/scorer.py`` (pure functions, unit-tested). This module only
* loads a case (graph, events, alerts, FIR labels) into the scorer's plain input format, masking identifiers for roles
  without ``sensitive_read``;
* stores officers' verification decisions (append-only history, audit-logged) and feeds them back into the ranking;
* exposes the API.

Nothing here states or implies guilt: outputs are "lead priority" / "person of interest" with evidence and a mandatory
verify-or-dismiss step. Victims, complainants and witnesses are excluded from the default list.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Integer, String, Text

from ai_engine.suspects import scorer
from backend.app.db.database import Base
from backend.app.models.models import DocumentRecord

EVAL_PATH = Path(__file__).resolve().parents[3] / "ai_engine" / "suspects" / "evaluation.json"
DECISIONS = ("confirmed_person_of_interest", "dismissed", "needs_more_info")
CAUTION = ("Lead priority for investigators, not a finding about any person. Every entry must be verified against the source records "
           "before any action is taken; a low score never clears a person and a high score never implicates one.")
LIMITATIONS = [
    "The weights are expert-set, not learned: the bundled FIR corpus names no accused person and the demo data carries no role labels, so there was nothing "
    "legitimate to train a supervised model on. The score is a ranking signal, not a calibrated probability.",
    "It can only rank what is in the case. Missing bank, call or location records lower confidence (see the band) but never count as evidence of innocence.",
    "Busy but innocent people (shopkeepers, telecom resellers, payment businesses) look like hubs and high-volume accounts. Volume and network-position signals "
    "are weaker than role, cross-FIR and pass-through signals for that reason.",
    "Recording bias: people who were investigated more have more records and therefore higher scores. Review results with that in mind.",
    "Names and roles come from automated FIR extraction and may be wrong or duplicated; correct them in Ingestion & Review and the ranking updates.",
    "Benchmarks are on a synthetic world written by the same team; they show the model works as designed, not how it performs on real crime.",
]

CANDIDATE_TYPES = scorer.ACTOR_TYPES


class SuspectDecision(Base):
    __tablename__ = "suspect_decisions"
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    entity_id = Column(String(100), index=True)
    decision = Column(String(40))
    reason = Column(Text, default="")
    decided_by = Column(String(100), default="")
    decided_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    score_at_decision = Column(Integer, default=0)
    model_version = Column(String(60), default="")


class DecisionIn(BaseModel):
    decision: str = Field(max_length=40)
    reason: str = Field(min_length=3, max_length=1000)
    case_number: str = Field(default="", max_length=100)


# ----------------------------------------------------------------------------- loading
def _attr_list(d, *keys):
    out = []
    for k in keys:
        v = d.get(k)
        if isinstance(v, (list, tuple)):
            out += [str(x) for x in v if x]
        elif v:
            out.append(str(v))
    return list(dict.fromkeys(out))


def to_inputs(G, events, alerts, fir_labels, mask_node, role, default_case=""):
    """Convert a case graph + rows into scorer inputs. Display names of masked types are masked BEFORE any text is built;
    the unmasked identifier value is kept only as an in-memory matching key (never returned)."""
    ents = []
    for n, d in G.nodes(data=True):
        typ = str(d.get("type", "")).upper()
        raw_name = str(d.get("name") or n)
        node = mask_node({"id": n, "type": typ, "name": raw_name}, role)
        role_val = d.get("role")
        ents.append({"id": n, "type": typ, "name": str(node.get("name") or n), "key": raw_name if typ in scorer.IDENT_TYPES else None,
                     "role": role_val, "docs": _attr_list(d, "source_documents", "document_id", "last_document_id"),
                     "cases": _attr_list(d, "case_numbers") or ([default_case] if default_case else [])})
    rels = [{"source": u, "target": v, "relation": str(d.get("relation", "")), "confidence": d.get("confidence")} for u, v, d in G.edges(data=True)]
    evs = []
    for e in events:
        md = e.get("metadata") or {}
        evs.append({"event_id": e["event_id"], "event_type": e["event_type"], "entity_id": e["entity_id"], "related_entity_id": e.get("related_entity_id") or "",
                    "time": e.get("event_time"), "amount": e.get("amount"), "duration": e.get("duration_seconds"), "source_ref": e.get("source_ref", ""),
                    "location_id": md.get("location_id"), "lat": e.get("latitude"), "lon": e.get("longitude")})
    return ents, rels, evs, alerts, fir_labels


def _latest_decisions(db, cn):
    rows = db.query(SuspectDecision).filter_by(case_number=cn).order_by(SuspectDecision.id.asc()).all()
    latest = {}
    for r in rows:
        latest[r.entity_id] = _dec_dict(r)
    return latest


def _dec_dict(r):
    return {"id": r.id, "case_number": r.case_number, "entity_id": r.entity_id, "decision": r.decision, "reason": r.reason, "decided_by": r.decided_by,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None, "score_at_decision": r.score_at_decision, "model_version": r.model_version}


def _fir_labels(db, G, cn):
    labels = {}
    for d in db.query(DocumentRecord).filter_by(case_number=cn).all():
        try:
            fir = (json.loads(d.structured_json or "{}") or {}).get("fir_number")
        except ValueError:
            fir = None
        labels[d.document_id] = f"FIR {fir}" if fir else (d.filename or d.document_id)
    for n, d in G.nodes(data=True):
        if str(d.get("type", "")).upper() == "CASE":
            labels[n] = str(d.get("name") or n)
    return labels


def _compute(ctx, db, user, case_number, include_victims=False):
    cn = ctx.scope(db, user, case_number)
    ctx.graph_for(db, user, cn)
    G = ctx.svc.graph.g.copy()
    events = ctx.svc.events(db, cn)
    alerts = ctx.svc.alerts(db, cn)
    ents, rels, evs, alerts, labels = to_inputs(G, events, alerts, _fir_labels(db, G, cn), ctx.mask_node, user["role"], cn)
    decisions = _latest_decisions(db, cn)
    out = scorer.score_case(ents, rels, evs, alerts, labels, decisions=decisions, include_victims=include_victims)
    return cn, out


def _compact(r):
    return {"entity_id": r["entity_id"], "name": r["name"], "type": r["type"], "rank": r["rank"], "score": r["score"], "tier": r["tier"], "band": r["band"],
            "role": r["role"], "status": r["status"], "pinned": r.get("pinned", False), "decision": r["decision"], "reasons": r["reasons"],
            "top_factors": [{"key": f["key"], "label": f["label"], "family_label": f["family_label"], "points": f["points"], "sentence": f["sentence"]}
                            for f in r["factors"] if f["contribution"] > 0.05][:3],
            "confidence": r["band"]["width"], "summary": r["summary"], "coverage": r["coverage"]}


def load_evaluation():
    try:
        data = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        data.pop("demo_seed", None)  # names demo-case people; must not show while demo data is off
    return data


# ----------------------------------------------------------------------------- API
def build(ctx):
    router = APIRouter(prefix="/suspects", tags=["suspects"])
    perm = ctx.require_perm

    @router.get("/rank")
    def rank(case_number: str, limit: int = Query(25, ge=1, le=200), include_victims: bool = False, include_dismissed: bool = True,
             db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn, out = _compute(ctx, db, user, case_number, include_victims)
        rows = [out["results"][a] for a in out["order"]]
        if not include_dismissed:
            rows = [r for r in rows if r["status"] != "dismissed"]
        tiers = {t: sum(1 for r in rows if r["tier"] == t and r["status"] != "dismissed") for t in ("High", "Medium", "Low")}
        return {"case_number": cn, "model_version": scorer.MODEL_VERSION, "caution": CAUTION, "generated_at": datetime.now(timezone.utc).isoformat(),
                "candidates": out["meta"]["candidates"], "victims_excluded": out["meta"]["victims_excluded"], "tier_counts": tiers,
                "availability": out["meta"]["availability"], "events": out["meta"]["events"],
                "results": [_compact(r) for r in rows[:limit]], "unattributed_identifiers": out["meta"]["unattributed_identifiers"][:20],
                "include_victims": include_victims}

    @router.get("/decisions")
    def decisions(case_number: str, entity_id: str = "", limit: int = Query(100, ge=1, le=500), db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = ctx.scope(db, user, case_number)
        q = db.query(SuspectDecision).filter_by(case_number=cn)
        if entity_id:
            q = q.filter_by(entity_id=entity_id)
        rows = q.order_by(SuspectDecision.id.desc()).limit(limit).all()
        return {"case_number": cn, "decisions": [_dec_dict(r) for r in rows]}

    @router.get("/model-card")
    def model_card(user=Depends(perm("read"))):
        return {**scorer.MODEL_CARD, "limitations": LIMITATIONS, "caution": CAUTION, "evaluation": load_evaluation(),
                "supervised_component": {"used": False, "reason": "No usable ground-truth labels: the 20,000-report FIR corpus lists every accused as 'Unknown', and the demo "
                                                                   "database carries no role labels. A model trained on labels we generated ourselves would be circular, so only "
                                                                   "the transparent evidence model is shipped."}}

    @router.get("/{entity_id}/profile")
    def profile(entity_id: str, case_number: str, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn, out = _compute(ctx, db, user, case_number, include_victims=False)
        if entity_id not in out["results"]:
            raise HTTPException(404, "Person or organisation not found in this case")
        r = out["results"][entity_id]
        extras = scorer.profile_extras(out["extracted"], entity_id)
        cross = r["factors"] and next((f for f in r["factors"] if f["key"] == "cross_fir_identifiers"), None)
        det = out["extracted"]["DET"][entity_id]
        hist = db.query(SuspectDecision).filter_by(case_number=cn, entity_id=entity_id).order_by(SuspectDecision.id.desc()).limit(50).all()
        return {"case_number": cn, "model_version": scorer.MODEL_VERSION, "caution": CAUTION, **r, "linked_entities": extras["linked"], "timeline": extras["timeline"],
                "cross_fir": {"firs": det["fir_appearances"]["firs"], "cases": det["fir_appearances"]["cases"],
                              "shared_identifiers": det["cross_fir_identifiers"]["identifiers"], "note": (cross or {}).get("sentence", "")},
                "decision_history": [_dec_dict(x) for x in hist], "excluded_reason": r["excluded_reason"], "total_ranked": out["meta"]["candidates"]}

    @router.post("/{entity_id}/decision")
    def decide(entity_id: str, x: DecisionIn, case_number: str = "", db=Depends(ctx.get_db), user=Depends(perm("review"))):
        if x.decision not in DECISIONS:
            raise HTTPException(400, f"decision must be one of: {', '.join(DECISIONS)}")
        reason = re.sub(r"\s+", " ", x.reason).strip()
        if len(reason) < 3:
            raise HTTPException(400, "A reason is required")
        cn, out = _compute(ctx, db, user, x.case_number or case_number, include_victims=True)
        if entity_id not in out["results"]:
            raise HTTPException(404, "Person or organisation not found in this case")
        before = out["results"][entity_id]
        row = SuspectDecision(case_number=cn, entity_id=entity_id, decision=x.decision, reason=reason, decided_by=user["sub"],
                              decided_at=datetime.now(timezone.utc), score_at_decision=int(before["model_score"]), model_version=scorer.MODEL_VERSION)
        db.add(row)
        db.commit()
        ctx.audit(db, user["sub"], "suspect.decision", {"case_number": cn, "entity_id": entity_id, "decision": x.decision, "reason": reason[:300],
                                                       "score_at_decision": row.score_at_decision, "model_version": scorer.MODEL_VERSION})
        _, after = _compute(ctx, db, user, cn, include_victims=True)
        r = after["results"][entity_id]
        return {"status": "recorded", "decision": _dec_dict(row), "rank": r["rank"], "score": r["score"], "tier": r["tier"], "state": r["status"],
                "caution": "Recorded as an officer's verification decision. It changes how the lead is ranked, not any fact in the case."}

    return router
