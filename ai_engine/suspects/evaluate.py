"""Honest evaluation of the person-of-interest scorer on the synthetic benchmark (ai_engine/suspects/benchmark.py).

Run:  python -m ai_engine.suspects.evaluate            (prints a table, writes ai_engine/suspects/evaluation.json)

Ground truth (planted ring members) is used ONLY to score rankings, never as a model input. Role labels are given to the
model only in the 'with partial roles' variants and are noisy; the 'behavioural only' variant strips every role label.
"""
from __future__ import annotations

import copy
import json
import math
import statistics as st
import sys
from pathlib import Path

import networkx as nx
import numpy as np
from sklearn.metrics import roc_auc_score

from ai_engine.suspects import scorer
from ai_engine.suspects.benchmark import make_world

OUT = Path(__file__).with_name("evaluation.json")
SETTINGS = {
    "easy": dict(decoys=0, noise=1.0, role_coverage=0.25, alert_noise=0.1, visibility=0.9, ring_signal=1.0),
    "standard": dict(decoys=2, noise=1.0, role_coverage=0.25, alert_noise=0.3, visibility=0.7, ring_signal=0.6),
    "hard": dict(decoys=4, noise=2.0, role_coverage=0.25, alert_noise=1.0, visibility=0.5, ring_signal=0.4),
}


def _strip_roles(world):
    ents = copy.deepcopy(world["entities"])
    for e in ents:
        e.pop("role", None)
    return ents


def _ranked(order, truth):
    return [1 if a in truth else 0 for a in order]


def metrics(scores: dict, truth, victims):
    ids = sorted(a for a in scores if a not in victims)
    y = np.array([1 if a in truth else 0 for a in ids])
    s = np.array([scores[a] for a in ids], dtype=float)
    auc = float(roc_auc_score(y, s))
    order = sorted(ids, key=lambda a: (-scores[a], a))
    hits = _ranked(order, truth)
    k = len(truth)
    return {"auc": auc, "p_at_10": sum(hits[:10]) / 10, "r_precision": sum(hits[:k]) / k, "recall_at_10": sum(hits[:10]) / k}


def baselines(world):
    """Naive scorers built from the same events, no roles: degree, PageRank on the interaction network, money volume, random."""
    x = scorer.extract_features(world["entities"], world["relationships"], world["events"], [], None)
    P = x["P"]
    actors = [a for a in x["actors"]]
    deg = nx.degree_centrality(P) if P.number_of_nodes() > 1 else {}
    pr = nx.pagerank(P) if P.number_of_edges() else {a: 0 for a in actors}
    rng = np.random.RandomState(0)
    return {
        "degree_centrality": {a: deg.get(a, 0.0) for a in actors},
        "pagerank": {a: pr.get(a, 0.0) for a in actors},
        "money_volume": {a: x["F"][a]["money_volume"] for a in actors},
        "random": {a: float(rng.rand()) for a in actors},
    }


def run_world(world):
    out = {}
    truth, victims = world["truth"], world["victims"]
    full = scorer.score_case(world["entities"], world["relationships"], world["events"], world["alerts"], include_victims=True)
    beh = scorer.score_case(_strip_roles(world), world["relationships"], world["events"], world["alerts"], include_victims=True)
    out["evidence_model_partial_roles"] = metrics({a: r["logit"] for a, r in full["results"].items()}, truth, victims)
    out["evidence_model_behavioural_only"] = metrics({a: r["logit"] for a, r in beh["results"].items()}, truth, victims)
    for name, sc in baselines(world).items():
        out[name] = metrics(sc, truth, victims)
    # victims-in-top-10 check for the default list
    d = scorer.score_case(world["entities"], world["relationships"], world["events"], world["alerts"], include_victims=False)
    out["_victims_in_top10"] = sum(1 for a in d["order"][:10] if a in victims)
    # leave-one-family-out (behavioural-only)
    for fam in ("structure", "money", "comm", "coloc", "fir", "alerts"):
        r = scorer.score_case(_strip_roles(world), world["relationships"], world["events"], world["alerts"], include_victims=True, disabled_families=(fam,))
        out[f"ablate_{fam}"] = metrics({a: q["logit"] for a, q in r["results"].items()}, truth, victims)
    return out


def _agg(rows, key):
    v = [r[key] for r in rows]
    return {"mean": round(float(np.mean(v)), 3), "sd": round(float(np.std(v)), 3)}


def evaluate(seeds=range(20), settings=None):
    settings = settings or SETTINGS
    res = {}
    for label, kw in settings.items():
        per = [run_world(make_world(seed=s, **kw)) for s in seeds]
        methods = [k for k in per[0] if not k.startswith("_")]
        block = {m: {mt: _agg([p[m] for p in per], mt) for mt in ("auc", "p_at_10", "r_precision", "recall_at_10")} for m in methods}
        deg = [p["degree_centrality"]["auc"] for p in per]
        ours = [p["evidence_model_behavioural_only"]["auc"] for p in per]
        ours_r = [p["evidence_model_partial_roles"]["auc"] for p in per]
        block["_paired"] = {
            "behavioural_vs_degree_win_rate_auc": round(float(np.mean([o > d for o, d in zip(ours, deg)])), 2),
            "partial_roles_vs_degree_win_rate_auc": round(float(np.mean([o > d for o, d in zip(ours_r, deg)])), 2),
            "victims_in_default_top10_total": int(sum(p["_victims_in_top10"] for p in per)),
        }
        res[label] = block
    return {"benchmark": {"seeds": len(list(seeds)), "settings": {k: v for k, v in settings.items()}, "results": res}}


def _table(res):
    lines = []
    for label, block in res["benchmark"]["results"].items():
        lines.append(f"\n[{label}]  mean±sd over {res['benchmark']['seeds']} worlds")
        lines.append(f"{'method':38s} {'AUC':>13s} {'P@10':>13s} {'R-prec':>13s}")
        for m, v in block.items():
            if m.startswith("_"):
                continue
            f = lambda k: f"{v[k]['mean']:.3f}±{v[k]['sd']:.3f}"
            lines.append(f"{m:38s} {f('auc'):>13s} {f('p_at_10'):>13s} {f('r_precision'):>13s}")
        lines.append(str(block["_paired"]))
    return "\n".join(lines)


if __name__ == "__main__":
    res = evaluate()
    print(_table(res))
    if "--no-write" not in sys.argv:
        OUT.write_text(json.dumps(res, indent=1))
        print("wrote", OUT)
