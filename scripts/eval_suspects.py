"""Evaluate the person-of-interest scorer and write ai_engine/suspects/evaluation.json.

    python scripts/eval_suspects.py            # synthetic benchmark (20 worlds x 3 difficulty settings) + demo seed check

The demo-seed check needs the bundled demo dataset loaded in the configured database (python scripts/seed_demo.py).
Planted "story" entities of scripts/seed_demo.py (the P001 hub, the P001-P021-P041-P061 call/transfer chain and the P003
vehicle contradiction) are used ONLY as ground truth for scoring; the model never sees them as labels. That truth set is tiny
(5 people) so this number is a smoke test, not a benchmark.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import networkx as nx  # noqa: E402

from ai_engine.suspects import evaluate as ev  # noqa: E402
from ai_engine.suspects import scorer  # noqa: E402

STORY = {"P001", "P003", "P021", "P041", "P061"}
STORY_CASE = "CASE-2026-101"


def demo_check():
    from backend.app.db.database import SessionLocal
    from backend.app.features import suspects as fs
    from backend.app.models.models import Case
    from backend.app.services.app_service import AppService
    db = SessionLocal()
    try:
        if not db.query(Case).filter_by(case_number=STORY_CASE).first():
            return {"available": False, "reason": "demo dataset not loaded"}
        svc = AppService()
        svc.ensure_graph(db, STORY_CASE)
        G = svc.graph.g.copy()
        ents, rels, evs, alerts, labels = fs.to_inputs(G, svc.events(db, STORY_CASE), svc.alerts(db, STORY_CASE), fs._fir_labels(db, G, STORY_CASE), lambda n, r: n, "admin", STORY_CASE)
        out = scorer.score_case(ents, rels, evs, alerts, labels)
        order = out["order"]
        dc = nx.degree_centrality(svc.graph._simple())
        actors = {e["id"] for e in ents if e["type"] in ("PERSON", "ORGANIZATION")}
        base = sorted(actors, key=lambda a: (-dc.get(a, 0), a))
        hits = lambda o, k: sum(1 for a in o[:k] if a in STORY)
        return {"available": True, "case": STORY_CASE, "candidates": len(order), "truth": sorted(STORY),
                "truth_in_case": sorted(STORY & actors),
                "evidence_model": {"p_at_5": hits(order, 5) / 5, "p_at_10": hits(order, 10) / 10, "ranks": {a: order.index(a) + 1 for a in sorted(STORY) if a in order}},
                "degree_centrality_baseline": {"p_at_5": hits(base, 5) / 5, "p_at_10": hits(base, 10) / 10, "ranks": {a: base.index(a) + 1 for a in sorted(STORY) if a in base}},
                "note": "5-person planted story; smoke test only. Role labels are absent from the seed, so this is the behavioural model."}
    finally:
        db.close()


if __name__ == "__main__":
    res = ev.evaluate()
    print(ev._table(res))
    res["demo_seed"] = demo_check()
    print(json.dumps(res["demo_seed"], indent=1))
    ev.OUT.write_text(json.dumps(res, indent=1))
    print("wrote", ev.OUT)
