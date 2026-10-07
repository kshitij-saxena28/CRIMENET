"""Network AI: link prediction, explainability, role hints, disruption simulation and a what-if sandbox.

Everything here is case-scoped and read-only, with one exception: accepting a link suggestion creates an
*unverified candidate* relationship (and audits it). All outputs are investigative hypotheses that a human must
check; none of them is a finding about any person.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
from collections import Counter, OrderedDict
from itertools import combinations

import networkx as nx
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sklearn.decomposition import TruncatedSVD
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from ai_engine.anomaly.detector import multivariate_scores
from graph_engine.engine import GraphEngine

log = logging.getLogger("dcn.netai")

STRUCTURAL_TYPES = {"CASE", "LAW_SECTION", "POLICE_STATION", "DISTRICT", "DOCUMENT"}
ROLE_TYPES_DEFAULT = ("PERSON", "ORGANIZATION")
SOURCE_REF = "netai-link-prediction"
MODEL_VERSION = "netai-linkpred-v1"

FEATURES = [
    ("common_neighbours", "Common neighbours"),
    ("jaccard", "Neighbourhood overlap (Jaccard)"),
    ("adamic_adar", "Adamic-Adar"),
    ("resource_alloc", "Resource allocation"),
    ("pref_attach", "Preferential attachment"),
    ("proximity", "Graph proximity (1/hops)"),
    ("same_community", "Same community"),
    ("embedding_cos", "Spectral embedding similarity"),
    ("type_lift", "Entity-type compatibility"),
    ("shared_attrs", "Shared attributes"),
    ("shared_cases", "Shared case membership"),
    ("event_links", "Recorded events between the pair"),
]
FKEYS = [k for k, _ in FEATURES]
FLABEL = dict(FEATURES)

LIMITS = [
    "Self-supervised: the model learns what hidden *recorded* links look like in this case's own graph, so it inherits every "
    "gap and bias of what investigators have recorded so far.",
    "The score comes from a class-balanced classifier. It ranks candidates; it is not the real-world probability that the link exists.",
    "Small graphs give unstable metrics: read the AUC together with its spread and the number of held-out links.",
    "Evaluation hides links from the same graph the model is then trained on; it cannot detect dataset-level bias.",
    "A suggested link is a hypothesis for a human to check against evidence. It is never a finding about any person.",
]


# ----------------------------------------------------------------------------- input models
class SimulateIn(BaseModel):
    case_number: str = Field(max_length=100)
    entity_ids: list[str] = Field(min_length=1, max_length=50)


class Override(BaseModel):
    source: str = Field(max_length=100)
    target: str = Field(max_length=100)
    confidence: float = Field(ge=0, le=1)


class WhatIfIn(BaseModel):
    case_number: str = Field(max_length=100)
    remove_entities: list[str] = Field(default_factory=list, max_length=200)
    drop_relation_types: list[str] = Field(default_factory=list, max_length=50)
    min_confidence: float = Field(default=0.0, ge=0, le=1)
    exclude_source_refs: list[str] = Field(default_factory=list, max_length=200)
    only_verified: bool = False
    confidence_overrides: list[Override] = Field(default_factory=list, max_length=200)


class AcceptIn(BaseModel):
    case_number: str = Field(max_length=100)
    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)
    relation_type: str = Field(min_length=2, max_length=80)
    note: str = Field(default="", max_length=500)


# ----------------------------------------------------------------------------- graph helpers
def simple_graph(G) -> nx.Graph:
    """Undirected simple graph; edge attrs: confidence (max), relations, refs, states, n."""
    S = nx.Graph()
    for n, d in G.nodes(data=True):
        S.add_node(n, type=str(d.get("type", "")).upper(), name=d.get("name", n))
    for u, v, d in G.edges(data=True):
        if u == v:
            continue
        conf = float(d.get("confidence") or 0)
        if S.has_edge(u, v):
            e = S[u][v]
            e["confidence"] = max(e["confidence"], conf)
            e["relations"].add(str(d.get("relation", "")))
            e["refs"].add(d.get("source_ref", "") or "")
            e["n"] += 1
        else:
            S.add_edge(u, v, confidence=conf, relations={str(d.get("relation", ""))}, refs={d.get("source_ref", "") or ""}, n=1)
    return S


def _engine(G) -> GraphEngine:
    eng = GraphEngine()
    eng.g = G
    return eng


def _names(G, role) -> dict:
    return {n: (str(d.get("name") or n)) for n, d in G.nodes(data=True)}


def _mask_names(ctx, G, role) -> dict:
    out = {}
    for n, d in G.nodes(data=True):
        node = ctx.mask_node({"id": n, **{k: v for k, v in d.items() if k in ("type", "name")}}, role)
        out[n] = str(node.get("name") or n)
    return out


def _r(x, d=4):
    try:
        return round(float(x), d)
    except (TypeError, ValueError):
        return 0.0


def _load(ctx, db, user, case_number):
    cn = ctx.scope(db, user, case_number)
    ctx.graph_for(db, user, cn)
    G = ctx.svc.graph.g.copy()  # never mutate the request graph
    return cn, G


# ----------------------------------------------------------------------------- link prediction
_ATTR_SKIP = {"type", "name", "latitude", "longitude", "case_number", "case_numbers", "masked", "synthetic", "confidence", "id"}


def _node_attr_sets(G):
    out, cases = {}, {}
    for n, d in G.nodes(data=True):
        s = set()
        for k, v in d.items():
            if k in _ATTR_SKIP or isinstance(v, (dict, list, set, tuple)) or v in (None, ""):
                continue
            sv = str(v)
            if len(sv) <= 100:
                s.add(f"{k}={sv}")
        out[n] = s
        cn = d.get("case_numbers") or []
        cases[n] = set(map(str, cn)) if isinstance(cn, (list, tuple)) else set()
    return out, cases


def _event_pairs(events) -> Counter:
    c = Counter()
    for e in events:
        a, b = e.get("entity_id"), e.get("related_entity_id")
        if a and b and a != b:
            c[tuple(sorted((a, b)))] += 1
    return c


class _Ctx:
    """Everything needed to compute pair features on one (possibly edge-hidden) graph."""

    def __init__(self, S, attrs, cases, ev_pairs):
        self.S = S
        self.nodes = list(S.nodes)
        self.idx = {n: i for i, n in enumerate(self.nodes)}
        self.adj = {n: set(S[n]) for n in S}
        self.deg = {n: len(a) for n, a in self.adj.items()}
        self.type = {n: S.nodes[n].get("type", "") for n in S}
        self.attrs, self.cases, self.ev = attrs, cases, ev_pairs
        self.comm = {}
        if S.number_of_edges():
            for i, c in enumerate(nx.community.louvain_communities(S, seed=42)):
                for n in c:
                    self.comm[n] = i
        self.emb = self._embed()
        self.tl = self._type_lift()
        self._dist = {}

    def _embed(self):
        n = len(self.nodes)
        if n < 3 or self.S.number_of_edges() == 0:
            return np.zeros((n, 1))
        A = nx.to_scipy_sparse_array(self.S, nodelist=self.nodes, weight=None, dtype=float)
        k = max(1, min(16, n - 1))
        try:
            U = TruncatedSVD(n_components=k, random_state=42, n_iter=5).fit_transform(A)
        except Exception:  # noqa: BLE001
            return np.zeros((n, 1))
        norm = np.linalg.norm(U, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        return U / norm

    def _type_lift(self):
        n, m = len(self.nodes), self.S.number_of_edges()
        cnt = Counter(self.type.values())
        base = m / max(n * (n - 1) / 2, 1)
        e = Counter(tuple(sorted((self.type[u], self.type[v]))) for u, v in self.S.edges)
        lift = {}
        for (a, b), ne in e.items():
            possible = cnt[a] * (cnt[a] - 1) / 2 if a == b else cnt[a] * cnt[b]
            lift[(a, b)] = math.log((ne + 1) / (possible * base + 1))
        self._cnt, self._base = cnt, base
        return lift

    def lift(self, u, v):
        key = tuple(sorted((self.type[u], self.type[v])))
        if key in self.tl:
            return self.tl[key]
        a, b = key
        possible = self._cnt[a] * (self._cnt[a] - 1) / 2 if a == b else self._cnt[a] * self._cnt[b]
        return math.log(1 / (possible * self._base + 1))

    def dist(self, u, v, cutoff=4):
        d = self._dist.get(u)
        if d is None:
            d = self._dist[u] = nx.single_source_shortest_path_length(self.S, u, cutoff=cutoff)
        return d.get(v)

    def common(self, u, v):
        return self.adj[u] & self.adj[v]

    def features(self, u, v):
        cn = self.common(u, v)
        union = len(self.adj[u] | self.adj[v])
        aa = sum(1 / math.log(self.deg[w] + 1) for w in cn if self.deg[w] > 1)
        ra = sum(1 / self.deg[w] for w in cn if self.deg[w] > 0)
        d = self.dist(u, v)
        cu, cv = self.comm.get(u), self.comm.get(v)
        eu, ev = self.emb[self.idx[u]], self.emb[self.idx[v]]
        cos = float(eu @ ev) if eu.shape == ev.shape else 0.0
        key = tuple(sorted((u, v)))
        return [
            float(len(cn)), len(cn) / union if union else 0.0, aa, ra, math.log1p(self.deg[u] * self.deg[v]),
            (1.0 / d) if d else 0.0, 1.0 if (cu is not None and cu == cv) else 0.0, cos, self.lift(u, v),
            float(len(self.attrs.get(u, set()) & self.attrs.get(v, set()))),
            float(len(self.cases.get(u, set()) & self.cases.get(v, set()))),
            math.log1p(self.ev.get(key, 0)),
        ]

    def matrix(self, pairs):
        return np.array([self.features(u, v) for u, v in pairs], dtype=float).reshape(len(pairs), len(FKEYS))


def _near_pairs(S, edge_set, rng, cap=20000):
    """Non-adjacent pairs with a common neighbour (2 hops apart), capped and deterministic."""
    out = set()
    for w in S.nodes:
        nb = sorted(S[w])
        if len(nb) < 2:
            continue
        if len(nb) > 90:
            nb = sorted(rng.choice(nb, size=90, replace=False).tolist())
        for a, b in combinations(nb, 2):
            p = (a, b) if a < b else (b, a)
            if p not in edge_set:
                out.add(p)
        if len(out) >= cap:
            break
    return sorted(out)


def _sample_negatives(active, edge_set, near_pool, k, rng):
    """~50% hard negatives (2 hops apart) + ~50% random non-edges among active nodes. Returns (pairs, is_near)."""
    pairs, near = [], []
    want_near = min(len(near_pool), k // 2)
    if want_near:
        for i in rng.choice(len(near_pool), size=want_near, replace=False):
            pairs.append(near_pool[i]); near.append(True)
    seen = set(pairs)
    tries = 0
    while len(pairs) < k and tries < k * 60 and len(active) > 1:
        tries += 1
        a, b = active[rng.randint(len(active))], active[rng.randint(len(active))]
        if a == b:
            continue
        p = (a, b) if a < b else (b, a)
        if p in edge_set or p in seen:
            continue
        seen.add(p); pairs.append(p); near.append(False)
    return pairs, near


def _pick_hidden(S, edges, n, rng):
    """Greedily choose up to n edges to hide so that both endpoints keep at least one other link
    (hiding a leaf's only link would create isolated nodes, an artefact the candidate pool never contains)."""
    deg = dict(S.degree())
    hidden = []
    for i in rng.permutation(len(edges)):
        u, v = edges[i]
        if deg[u] >= 2 and deg[v] >= 2:
            hidden.append((u, v)); deg[u] -= 1; deg[v] -= 1
            if len(hidden) >= n:
                break
    return hidden


def _hidden_round(S, edges, edge_set_all, attrs, cases, evp, frac, rng):
    """Hide a fraction of `edges` from S; return (features, labels, is_near) for positives + sampled negatives."""
    hidden = _pick_hidden(S, edges, max(2, int(round(frac * len(edges)))), rng)
    if not hidden:
        return None
    Sh = S.copy()
    Sh.remove_edges_from(hidden)
    active_h = sorted(n for n in Sh if Sh.degree(n) > 0)
    cx = _Ctx(Sh, attrs, cases, evp)
    near_pool = _near_pairs(Sh, edge_set_all, rng)
    neg, near = _sample_negatives(active_h, edge_set_all, near_pool, min(3 * len(hidden), 900), rng)
    X = cx.matrix(hidden + neg)
    y = np.array([1] * len(hidden) + [0] * len(neg))
    return X, y, np.array([False] * len(hidden) + near)


def _train(S, edges, edge_set_all, attrs, cases, evp, rounds, rng, frac=0.2):
    Xs, ys = [], []
    for _ in range(rounds):
        got = _hidden_round(S, edges, edge_set_all, attrs, cases, evp, frac, rng)
        if got:
            Xs.append(got[0]); ys.append(got[1])
    if not Xs:
        return None
    X, y = np.vstack(Xs), np.concatenate(ys)
    sc = StandardScaler().fit(X)
    lr = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000).fit(sc.transform(X), y)
    return sc, lr, X, y


def _p_at_k(y, score, rng, k):
    order = np.lexsort((rng.rand(len(score)), -np.asarray(score)))  # random tie-break
    return float(np.mean(np.asarray(y)[order[:k]]))


def _eligible(S):
    return sum(1 for u, v in S.edges if S.degree(u) >= 2 and S.degree(v) >= 2)


def _evaluate(S, attrs, cases, evp, edge_set_all, repeats=3):
    edges = sorted(tuple(sorted(e)) for e in S.edges)
    res = {"lr": [], "gb": [], "cn": [], "aa": [], "lr_hard": [], "cn_hard": [], "p_lr": [], "p_gb": [], "p_cn": [], "p_aa": [], "pos": [], "neg": []}
    for r in range(repeats):
        rng = np.random.RandomState(100 + r)
        test = _pick_hidden(S, edges, max(3, int(round(0.2 * _eligible(S)))), rng)
        if len(test) < 3:
            continue
        S_tr = S.copy(); S_tr.remove_edges_from(test)
        train_edges = sorted(tuple(sorted(e)) for e in S_tr.edges)
        trained = _train(S_tr, train_edges, edge_set_all, attrs, cases, evp, 4, rng)
        if not trained:
            continue
        sc, lr, X, y = trained
        active = sorted(n for n in S_tr if S_tr.degree(n) > 0)
        gb = GradientBoostingClassifier(n_estimators=60, max_depth=2, random_state=42).fit(X, y)
        cx = _Ctx(S_tr, attrs, cases, evp)
        near_pool = _near_pairs(S_tr, edge_set_all, rng)
        neg, near = _sample_negatives(active, edge_set_all, near_pool, min(3 * len(test), 900), rng)
        Xt = cx.matrix(test + neg)
        yt = np.array([1] * len(test) + [0] * len(neg))
        hard = np.array([True] * len(test) + near)  # positives + hard negatives only
        if len(set(yt)) < 2:
            continue
        s_lr = lr.predict_proba(sc.transform(Xt))[:, 1]
        s_gb = gb.predict_proba(sc.transform(Xt))[:, 1]
        s_cn, s_aa = Xt[:, FKEYS.index("common_neighbours")], Xt[:, FKEYS.index("adamic_adar")]
        res["lr"].append(roc_auc_score(yt, s_lr)); res["gb"].append(roc_auc_score(yt, s_gb))
        res["cn"].append(roc_auc_score(yt, s_cn)); res["aa"].append(roc_auc_score(yt, s_aa))
        if hard.sum() > len(test) and len(set(yt[hard])) == 2:
            res["lr_hard"].append(roc_auc_score(yt[hard], s_lr[hard])); res["cn_hard"].append(roc_auc_score(yt[hard], s_cn[hard]))
        k = len(test)
        for key, s in (("p_lr", s_lr), ("p_gb", s_gb), ("p_cn", s_cn), ("p_aa", s_aa)):
            res[key].append(_p_at_k(yt, s, rng, k))
        res["pos"].append(len(test)); res["neg"].append(len(neg))
    if not res["lr"]:
        return None

    def ms(key):
        v = res[key]
        return {"mean": _r(np.mean(v)), "std": _r(np.std(v))} if v else None
    pos, neg = float(np.mean(res["pos"])), float(np.mean(res["neg"]))
    return {
        "repeats": len(res["lr"]), "held_out_links_per_repeat": int(round(pos)), "negatives_per_repeat": int(round(neg)),
        "auc": {"logistic_regression": ms("lr"), "gradient_boosting_reference": ms("gb"),
                "baseline_common_neighbours": ms("cn"), "baseline_adamic_adar": ms("aa"), "random_guess": 0.5},
        "auc_hard_negatives_only": {"logistic_regression": ms("lr_hard"), "baseline_common_neighbours": ms("cn_hard")},
        "precision_at_k": {"k": int(round(pos)), "logistic_regression": ms("p_lr"), "gradient_boosting_reference": ms("p_gb"),
                           "baseline_common_neighbours": ms("p_cn"), "baseline_adamic_adar": ms("p_aa"),
                           "random_guess": _r(pos / (pos + neg))},
        "protocol": "Each repeat hides 20% of recorded links (positives), samples 3x as many non-links (half 2-hops apart, half random), "
                    "trains only on the rest of the graph and scores the hidden links. Repeated 3 times with different splits.",
    }


_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _graph_key(cn, S, evp):
    h = hashlib.sha1()
    for u, v in sorted(tuple(sorted(e)) for e in S.edges):
        h.update(f"{u}|{v};".encode())
    for n in sorted(S.nodes):
        h.update(f"{n}:{S.nodes[n].get('type')};".encode())
    for p, c in sorted(evp.items()):
        h.update(f"{p}:{c};".encode())
    return (cn, h.hexdigest())


def link_model(cn, G, events) -> dict:
    """Train (or fetch from cache) the link model for this case graph."""
    S = simple_graph(G)
    evp = _event_pairs(events)
    key = _graph_key(cn, S, evp)
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    attrs, cases = _node_attr_sets(G)
    edges = sorted(tuple(sorted(e)) for e in S.edges)
    active = sorted(n for n in S.nodes if S.degree(n) > 0)
    info = {"S": S, "evp": evp, "attrs": attrs, "cases": cases, "edges": len(edges), "nodes": S.number_of_nodes(), "active": len(active), "eligible_links": _eligible(S)}
    elig = _eligible(S)
    if len(active) < 8 or len(edges) < 12 or elig < 8:
        info.update(status="too_small",
                    message=f"Graph too small to train a link model ({len(active)} connected entities, {len(edges)} relationships, {elig} of them "
                            "between entities that have other links; need at least 8 connected entities, 12 relationships and 8 such links). "
                            "Add and verify more relationships first.")
        model = info
    else:
        edge_set = set(edges)
        rng = np.random.RandomState(7)
        evaluation = _evaluate(S, attrs, cases, evp, edge_set)
        trained = _train(S, edges, edge_set, attrs, cases, evp, 6, rng)
        if not trained:
            info.update(status="too_small", message="Graph too small to train a link model.")
            with _CACHE_LOCK:
                _CACHE[key] = info
            return info
        sc, lr, X, y = trained
        cx = _Ctx(S, attrs, cases, evp)
        info.update(status="ok", scaler=sc, lr=lr, cx=cx, edge_set=edge_set, evaluation=evaluation,
                    train={"samples": int(len(y)), "positives": int(y.sum()), "negatives": int(len(y) - y.sum())})
        if evaluation is None:
            info["message"] = "Model trained, but there were too few links to run a held-out evaluation."
        model = info
    with _CACHE_LOCK:
        _CACHE[key] = model
        while len(_CACHE) > 8:
            _CACHE.popitem(last=False)
    return model


def _contribs(model, x):
    z = (np.asarray(x) - model["scaler"].mean_) / model["scaler"].scale_
    c = model["lr"].coef_[0] * z
    return c


def _sigmoid_prob(model, x):
    return float(model["lr"].predict_proba(model["scaler"].transform(np.asarray(x).reshape(1, -1)))[0, 1])


def _reason_text(key, x, u, v, cx, names):
    val = x[FKEYS.index(key)]
    if key == "common_neighbours":
        cn = sorted(cx.common(u, v), key=lambda w: cx.deg[w])
        shown = ", ".join(names.get(w, w) for w in cn[:4]) + (" ..." if len(cn) > 4 else "")
        return f"They share {int(val)} neighbour(s): {shown}" if cn else "No shared neighbours"
    if key == "jaccard":
        return f"{val * 100:.0f}% of their combined neighbours are shared (Jaccard {val:.2f})"
    if key == "adamic_adar":
        return f"Shared neighbours are selective (few other links), Adamic-Adar {val:.2f}" if val > 0 else "No selective shared neighbours"
    if key == "resource_alloc":
        return f"Resource-allocation score {val:.2f} (shared neighbours with few other connections)"
    if key == "pref_attach":
        return f"Both are well connected (degrees {cx.deg[u]} and {cx.deg[v]})"
    if key == "proximity":
        d = cx.dist(u, v)
        return f"They are {d} hop(s) apart in the current graph" if d else "They are not connected within 4 hops"
    if key == "same_community":
        return "They fall in the same detected community" if val else "They fall in different communities"
    if key == "embedding_cos":
        return f"Similar position in the network (spectral embedding cosine {val:.2f})"
    if key == "type_lift":
        a, b = cx.type[u], cx.type[v]
        return f"{a}-{b} links are {'more' if val > 0 else 'less'} common than average in this case"
    if key == "shared_attrs":
        sh = sorted(cx.attrs.get(u, set()) & cx.attrs.get(v, set()))
        return "They share attribute value(s): " + ", ".join(sh[:3]) if sh else "No shared attributes"
    if key == "shared_cases":
        return f"They belong to {int(val)} common case(s)"
    if key == "event_links":
        n = int(round(math.expm1(val)))
        return f"{n} recorded event(s) (calls, transactions, meetings) connect them"
    return FLABEL[key]


def _supports(key, x, cx, u, v):
    """True when the feature value is genuinely evidence *for* a link (not merely 'less negative than average')."""
    val = x[FKEYS.index(key)]
    if key == "pref_attach":
        return cx.deg[u] >= 2 and cx.deg[v] >= 2
    if key == "embedding_cos":
        return val >= 0.3
    if key == "type_lift":
        return val > 0
    return val > 0


def _explain_pair(model, u, v, names):
    cx = model["cx"]
    x = cx.features(u, v)
    contribs = _contribs(model, x)
    rows = [{"feature": k, "label": FLABEL[k], "value": _r(x[i]), "contribution": _r(contribs[i]), "text": _reason_text(k, x, u, v, cx, names)}
            for i, k in enumerate(FKEYS)]
    return x, sorted(rows, key=lambda r: -abs(r["contribution"])), _sigmoid_prob(model, x)


def _suggest_relation(G, cx, u, v):
    c = Counter()
    for w in cx.common(u, v):
        for a, b in ((u, w), (v, w)):
            for x, y in ((a, b), (b, a)):
                for d in (G.get_edge_data(x, y) or {}).values():
                    c[str(d.get("relation", ""))] += 1
    return c.most_common(1)[0][0] if c else None


# ----------------------------------------------------------------------------- explainability (anomaly)
ANOMALY_FEATURES = [
    ("degree", "Graph connections (degree)"), ("betweenness", "Bridge position (betweenness)"), ("pagerank", "Influence (PageRank)"),
    ("eigenvector", "Connection to well-connected entities (eigenvector)"), ("transaction_amount", "Transaction-linked amount"),
    ("event_count", "Observed events"),
]


def anomaly_rows(G, entities, events, eng=None):
    """Reproduce the feature matrix used by AppService.run_analytics for `entities` (list of entity dicts)."""
    eng = eng or _engine(G)
    central = eng.centrality()
    by_ent = {}
    for e in events:
        by_ent.setdefault(e["entity_id"], []).append(e)
        if e.get("related_entity_id") and e["related_entity_id"] != e["entity_id"]:
            by_ent.setdefault(e["related_entity_id"], []).append(e)
    rows = []
    for e in entities:
        eid = e["external_id"]
        deg = G.degree(eid) if eid in G else 0
        rel = by_ent.get(eid, [])
        tx = sum(float(x["amount"] or 0) for x in rel)
        rows.append([deg, central["betweenness"].get(eid, 0), central["pagerank"].get(eid, 0), central["eigenvector"].get(eid, 0), tx, len(rel)])
    return rows


def _combined(rows):
    iso, lof = multivariate_scores(rows)
    return [round(100 * (0.65 * i + 0.35 * l)) for i, l in zip(iso, lof)], iso, lof


def _robust_z(col, x):
    a = np.asarray(col, dtype=float)
    med = float(np.median(a))
    mad = float(np.median(np.abs(a - med))) * 1.4826
    if mad < 1e-12:
        sd = float(a.std())
        return med, (float(x) - med) / sd if sd > 1e-12 else 0.0
    return med, (float(x) - med) / mad


def anomaly_breakdown(rows, idx, compute_effects=True):
    """Per-feature explanation for row idx: robust z vs case baseline + leave-one-feature-out score effect."""
    rows_a = np.asarray(rows, dtype=float)
    scores, _i, _l = _combined(rows) if len(rows) else ([], [], [])
    full = scores[idx] if scores else 0
    out = []
    for j, (k, label) in enumerate(ANOMALY_FEATURES):
        col = rows_a[:, j]
        med, z = _robust_z(col, rows_a[idx, j])
        pctl = float((col < rows_a[idx, j]).sum() + 0.5 * (col == rows_a[idx, j]).sum()) / len(col)
        effect = 0
        if compute_effects and abs(rows_a[idx, j] - med) > 1e-12 and len(rows) >= 6:
            alt = rows_a.copy(); alt[idx, j] = med
            effect = full - _combined(alt.tolist())[0][idx]
        out.append({"feature": k, "label": label, "value": _r(rows_a[idx, j], 6), "case_median": _r(med, 6), "robust_z": _r(z, 2),
                    "percentile": _r(pctl, 3), "score_effect": int(effect)})
    return full, out


def _feature_sentence(f):
    v, m, z = f["value"], f["case_median"], f["robust_z"]
    fmt = (lambda x: f"{x:,.0f}") if f["feature"] in ("degree", "transaction_amount", "event_count") else (lambda x: f"{x:.3f}")
    if abs(z) < 1 and f["score_effect"] == 0:
        return f"{f['label']}: {fmt(v)}, close to the case median ({fmt(m)})."
    side = "above" if z > 0 else "below"
    txt = f"{f['label']}: {fmt(v)} versus case median {fmt(m)} ({abs(z):.1f} robust deviations {side})."
    if f["score_effect"]:
        txt += f" Neutralising it would change the score by {-f['score_effect']:+d} points."
    return txt


def _entity_evidence(G, events, eid, names, limit=12):
    rels = []
    for u, v, d in list(G.in_edges(eid, data=True)) + list(G.out_edges(eid, data=True)):
        rels.append({"source": u, "target": v, "source_name": names.get(u, u), "target_name": names.get(v, v), "relation": d.get("relation"),
                     "confidence": _r(d.get("confidence"), 3), "source_ref": d.get("source_ref", ""), "verification_state": d.get("verification_state", "")})
    rels.sort(key=lambda r: -r["confidence"])
    evs = [e for e in events if e["entity_id"] == eid or e.get("related_entity_id") == eid]
    refs = sorted({e["source_ref"] for e in evs if e.get("source_ref")} | {r["source_ref"] for r in rels if r["source_ref"]})
    return {"relationships": rels[:limit], "relationship_count": len(rels), "event_count": len(evs), "source_refs": refs[:40],
            "sample_events": [{"event_id": e["event_id"], "event_type": e["event_type"], "event_time": e["event_time"], "amount": e["amount"],
                               "counterparty": names.get(e["related_entity_id"] if e["entity_id"] == eid else e["entity_id"], ""), "source_ref": e["source_ref"]}
                              for e in evs[:8]]}


# ----------------------------------------------------------------------------- roles
ROLE_NOTE = ("Roles are structural hints derived from network position (who is central, who bridges groups, who sits between others). "
             "They are NOT assertions about anyone's real-world or criminal role and must be corroborated with evidence.")


def _pctile(values: dict, active):
    arr = np.array([values[n] for n in active], dtype=float)
    out = {}
    for n in active:
        x = values[n]
        out[n] = float((arr < x).sum() + 0.5 * (arr == x).sum()) / len(arr)
    return out


def classify_roles(G, types=ROLE_TYPES_DEFAULT, eng=None):
    S = simple_graph(G)
    active = [n for n in S if S.degree(n) > 0]
    thresholds = {"hub": "degree pct >= 85 and PageRank pct >= 80 and degree >= 3", "broker": "betweenness pct >= 85, spans >= 2 communities or is a cut-vertex",
                  "courier": "degree 2-3, lies on shortest paths (betweenness > 0), is a cut-vertex or betweenness pct >= 50, not a hub",
                  "core": "k-core is the maximum and clustering >= 0.3", "peripheral": "degree <= 1 or degree pct <= 35"}
    if len(active) < 6:
        return {"status": "too_small", "message": "Fewer than 6 connected entities: role hints need more network structure.", "roles": [], "summary": {}, "thresholds": thresholds, "disclaimer": ROLE_NOTE}
    eng = eng or _engine(G)
    cent = eng.centrality()
    comm = {}
    for i, c in enumerate(nx.community.louvain_communities(S, seed=42)):
        for n in c:
            comm[n] = i
    arts = set(nx.articulation_points(S))
    Sc = S.copy(); Sc.remove_edges_from(nx.selfloop_edges(Sc))
    core = nx.core_number(Sc)
    maxcore = max(core.values()) if core else 0
    clus = nx.clustering(S)
    deg = {n: S.degree(n) for n in S}
    p_deg, p_bet, p_pr, p_clo = (_pctile(d, active) for d in (deg, cent["betweenness"], cent["pagerank"], cent["closeness"]))
    want = {t.upper() for t in types}
    roles = []
    for n in S:
        if want and S.nodes[n].get("type") not in want:
            continue
        d = deg[n]
        m = {"degree": d, "betweenness": _r(cent["betweenness"].get(n, 0)), "pagerank": _r(cent["pagerank"].get(n, 0), 5),
             "closeness": _r(cent["closeness"].get(n, 0)), "clustering": _r(clus.get(n, 0), 3), "k_core": core.get(n, 0),
             "neighbour_communities": len({comm.get(w) for w in S[n]}), "is_articulation_point": n in arts}
        if d == 0:
            role, conf, why = "Isolated", 0.99, ["No recorded relationships in this case graph"]
        else:
            pd_, pb, pp = p_deg[n], p_bet[n], p_pr[n]
            ncom = m["neighbour_communities"]
            if pd_ >= 0.85 and pp >= 0.80 and d >= 3:
                role = "Coordinator / Hub"
                conf = 0.5 + 0.45 * min(1.0, (min(pd_, pp) - 0.8) / 0.2)
                why = [f"Degree {d} (top {100 - pd_ * 100:.0f}% of connected entities)", f"PageRank in the top {100 - pp * 100:.0f}%", f"{ncom} neighbouring communit{'y' if ncom == 1 else 'ies'}"]
            elif pb >= 0.85 and m["betweenness"] > 0 and (ncom >= 2 or n in arts):
                role = "Broker / Bridge"
                conf = 0.5 + 0.45 * min(1.0, (pb - 0.85) / 0.15)
                why = [f"Betweenness in the top {100 - pb * 100:.0f}% (sits on many shortest paths)", f"Connects {ncom} communities" if ncom >= 2 else "Removing it would split the graph"]
            elif 2 <= d <= 3 and m["betweenness"] > 0 and (n in arts or pb >= 0.5) and pd_ < 0.85:
                role = "Courier / Intermediary"
                conf = 0.45 + 0.4 * min(1.0, pb)
                why = [f"Only {d} links but it lies between others (betweenness {m['betweenness']})", "Is a cut-vertex: its removal disconnects part of the graph" if n in arts else "On many shortest paths for its size"]
            elif d >= 3 and core.get(n, 0) >= max(2, maxcore) and clus.get(n, 0) >= 0.3:
                role = "Core member"
                conf = 0.5 + 0.3 * min(1.0, clus.get(n, 0))
                why = [f"In the densest core (k-core {core.get(n, 0)})", f"Clustering {clus.get(n, 0):.2f}: its contacts also know each other"]
            elif d <= 1 or pd_ <= 0.35:
                role = "Peripheral"
                conf = 0.6 + 0.3 * (1 - pd_)
                why = [f"Only {d} recorded link(s)", "Low centrality on every measure"]
            else:
                role = "Member"
                conf = 0.4
                why = [f"Degree {d}, no single dominant structural signature"]
        roles.append({"entity_id": n, "type": S.nodes[n].get("type"), "role": role, "confidence": _r(conf, 2), "reasons": why, "metrics": m,
                      "percentiles": {"degree": _r(p_deg.get(n, 0), 2), "betweenness": _r(p_bet.get(n, 0), 2), "pagerank": _r(p_pr.get(n, 0), 2)}})
    order = {"Coordinator / Hub": 0, "Broker / Bridge": 1, "Courier / Intermediary": 2, "Core member": 3, "Member": 4, "Peripheral": 5, "Isolated": 6}
    roles.sort(key=lambda r: (order.get(r["role"], 9), -r["confidence"], r["entity_id"]))
    return {"status": "ok", "roles": roles, "summary": dict(Counter(r["role"] for r in roles)), "thresholds": thresholds, "method": "rule-based on centrality percentiles (heuristic)",
            "disclaimer": ROLE_NOTE}


# ----------------------------------------------------------------------------- disruption
def graph_metrics(S, exact_limit=1200):
    n, m = S.number_of_nodes(), S.number_of_edges()
    comps = sorted((len(c) for c in nx.connected_components(S)), reverse=True)
    largest = comps[0] if comps else 0
    avg_path = diameter = None
    approx = False
    if largest >= 2:
        big = S.subgraph(max(nx.connected_components(S), key=len))
        if largest <= exact_limit:
            avg_path = nx.average_shortest_path_length(big)
            diameter = nx.diameter(big)
        else:
            approx = True
            rng = np.random.RandomState(1)
            srcs = rng.choice(list(big.nodes), size=100, replace=False)
            tot = cnt = 0; diameter = 0
            for s in srcs:
                for d in nx.single_source_shortest_path_length(big, s).values():
                    tot += d; cnt += 1; diameter = max(diameter, d)
            avg_path = tot / max(cnt - len(srcs), 1)
    ncomm = 0
    if m:
        ncomm = sum(1 for c in nx.community.louvain_communities(S, seed=42) if len(c) > 1)
    return {"nodes": n, "edges": m, "components": len(comps), "isolated": sum(1 for c in comps if c == 1), "largest_component": largest,
            "largest_share": _r(largest / n, 4) if n else 0.0, "avg_path_length": _r(avg_path, 3) if avg_path is not None else None,
            "diameter": diameter, "communities": ncomm, "approximate_paths": approx}


def connected_pairs(S):
    return sum(len(c) * (len(c) - 1) // 2 for c in nx.connected_components(S))


def simulate_removal(S, remove):
    remove = set(remove)
    before = graph_metrics(S)
    keep = [n for n in S if n not in remove]
    A = S.subgraph(keep).copy()
    after = graph_metrics(A)
    # pairs among remaining entities that were connected before and are not any more
    pairs_before = sum(len([x for x in c if x not in remove]) * (len([x for x in c if x not in remove]) - 1) // 2 for c in nx.connected_components(S))
    pairs_after = connected_pairs(A)
    newly_isolated = [n for n in keep if S.degree(n) > 0 and A.degree(n) == 0]
    comm_before = nx.community.louvain_communities(S, seed=42) if S.number_of_edges() else []
    comp_of = {}
    for i, c in enumerate(nx.connected_components(A)):
        for n in c:
            comp_of[n] = i
    affected = []
    for i, c in enumerate(comm_before):
        if len(c) < 2:
            continue
        removed_in = [n for n in c if n in remove]
        left = [n for n in c if n not in remove]
        split = len({comp_of[n] for n in left}) > 1
        iso = [n for n in left if n in set(newly_isolated)]
        if removed_in or split or iso:
            affected.append({"community": i, "size": len(c), "removed": removed_in, "fragments_after": len({comp_of[n] for n in left}), "newly_isolated": iso})
    bb, ba = nx.betweenness_centrality(S), (nx.betweenness_centrality(A) if A.number_of_nodes() else {})
    rank_b = {n: i + 1 for i, n in enumerate(sorted(bb, key=lambda x: (-bb[x], x)))}
    rank_a = {n: i + 1 for i, n in enumerate(sorted(ba, key=lambda x: (-ba[x], x)))}
    top = sorted(set([n for n, r in rank_b.items() if r <= 10 and n not in remove] + [n for n, r in rank_a.items() if r <= 10]), key=lambda n: rank_a.get(n, 999))
    reshuffle = [{"entity_id": n, "rank_before": rank_b.get(n), "rank_after": rank_a.get(n), "betweenness_before": _r(bb.get(n, 0)), "betweenness_after": _r(ba.get(n, 0))} for n in top]
    common = [n for n in keep if n in bb]
    rho = None
    if len(common) >= 4:
        a = np.array([bb[n] for n in common]); b = np.array([ba.get(n, 0) for n in common])
        if a.std() > 0 and b.std() > 0:
            from scipy.stats import spearmanr
            rho = _r(spearmanr(a, b).correlation, 3)
    return {"removed": sorted(remove), "before": before, "after": after,
            "broken_shortest_paths": int(pairs_before - pairs_after), "connected_pairs_before": int(pairs_before), "connected_pairs_after": int(pairs_after),
            "newly_isolated": newly_isolated, "communities_affected": affected, "centrality_reshuffle": reshuffle, "betweenness_rank_correlation": rho,
            "remaining_nodes": keep}


def critical_nodes(S, k=5, allow_types=None):
    cand_all = [n for n in S if S.degree(n) > 0 and (allow_types is None or S.nodes[n].get("type") in allow_types)]
    arts = set(nx.articulation_points(S))
    base_pairs = connected_pairs(S)
    n_total = S.number_of_nodes()

    def score(nodes):
        A = S.subgraph([n for n in S if n not in nodes])
        comps = [len(c) for c in nx.connected_components(A)]
        return connected_pairs(A), (max(comps) if comps else 0), len(comps)

    single = []
    for n in cand_all:
        if n in arts:
            pairs, largest, ncomp = score({n})
            single.append({"entity_id": n, "type": S.nodes[n].get("type"), "degree": S.degree(n), "pairs_after": pairs, "largest_after": largest,
                           "components_after": ncomp, "pairs_lost": base_pairs - pairs})
    single.sort(key=lambda r: (-r["pairs_lost"], r["entity_id"]))
    # greedy: at each step remove the node that minimises the number of still-connected pairs
    pool = cand_all
    if len(pool) > 300:
        bc = nx.betweenness_centrality(S)
        pool = sorted(pool, key=lambda n: -(bc[n] + S.degree(n) / 1000))[:300]
    chosen, steps, cur = [], [], base_pairs
    for _ in range(max(1, min(k, 10))):
        best = None
        for n in pool:
            if n in chosen:
                continue
            pairs, largest, ncomp = score(set(chosen) | {n})
            key = (pairs, largest, -ncomp, -S.degree(n), n)
            if best is None or key < best[0]:
                best = (key, n, pairs, largest, ncomp)
        if best is None or best[2] >= cur:
            break
        _k, n, pairs, largest, ncomp = best
        chosen.append(n)
        steps.append({"step": len(chosen), "entity_id": n, "type": S.nodes[n].get("type"), "degree": S.degree(n), "pairs_after": pairs, "pairs_removed_this_step": cur - pairs,
                      "largest_after": largest, "largest_share_after": _r(largest / max(n_total - len(chosen), 1), 3), "components_after": ncomp})
        cur = pairs
    return {"articulation_points": single, "greedy": steps, "connected_pairs_baseline": base_pairs}


# ----------------------------------------------------------------------------- what-if
def _rank(scores: dict):
    return {n: i + 1 for i, n in enumerate(sorted(scores, key=lambda x: (-scores[x], x)))}


def _scenario_summary(G, entities, events, settings_):
    S = simple_graph(G)
    eng = _engine(G)
    cent = eng.centrality() if G.number_of_nodes() else {"pagerank": {}, "betweenness": {}}
    m = graph_metrics(S)
    flagged = []
    ent = [e for e in entities if e["external_id"] in G]
    if len(ent) >= 6:
        rows = anomaly_rows(G, ent, events, eng)
        sc, _i, _l = _combined(rows)
        flagged = [{"entity_id": e["external_id"], "score": s} for e, s in zip(ent, sc) if s >= settings_.anomaly_review_threshold]
        flagged.sort(key=lambda x: -x["score"])
    return S, m, cent, flagged


def _movement(base: dict, scen: dict, removed: set, n=10):
    rb, rs = _rank(base), _rank(scen)
    ids = [x for x in rb if rb[x] <= n] + [x for x in rs if rs[x] <= n and x not in rb]
    rows = []
    for x in dict.fromkeys(ids):
        if x in removed:
            status = "removed"
        elif x not in rs:
            status = "gone"
        elif rb.get(x, 10 ** 9) > n and rs[x] <= n:
            status = "entered top 10"
        elif rs[x] > n:
            status = "left top 10"
        elif rs[x] < rb[x]:
            status = "up"
        elif rs[x] > rb[x]:
            status = "down"
        else:
            status = "same"
        rows.append({"entity_id": x, "rank_before": rb.get(x), "rank_after": rs.get(x), "score_before": _r(base.get(x, 0), 5), "score_after": _r(scen.get(x, 0), 5) if x in scen else None, "status": status})
    rows.sort(key=lambda r: (r["rank_before"] or 999, r["rank_after"] or 999))
    return rows


# ----------------------------------------------------------------------------- router
def build(ctx):
    router = APIRouter(tags=["network-ai"])
    perm = ctx.require_perm
    svc = ctx.svc

    def context(db, user, case_number):
        cn, G = _load(ctx, db, user, case_number)
        return cn, G, _mask_names(ctx, G, user["role"])

    def not_found_entity(G, eid):
        if eid not in G:
            raise HTTPException(404, f"Entity '{eid}' not found in this case")

    # ---------------- link prediction
    @router.get("/netai/link-predictions")
    def link_predictions(case_number: str, limit: int = Query(25, ge=1, le=200), db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn, G, names = context(db, user, case_number)
        events = svc.events(db, cn)
        model = link_model(cn, G, events)
        base = {"case_number": cn, "status": model["status"], "graph": {"nodes": model["nodes"], "connected": model["active"], "relationships": model["edges"]},
                "limits": LIMITS, "disclaimer": "Suggestions are hypotheses for a human to check, not findings.", "features": [{"key": k, "label": l} for k, l in FEATURES]}
        if model["status"] != "ok":
            return {**base, "message": model["message"], "suggestions": [], "evaluation": None}
        cx, S, edge_set = model["cx"], model["S"], model["edge_set"]
        existing = {tuple(sorted((r["source"], r["target"]))) for r in svc.relationships(db, cn, include_candidates=True)}
        # candidate pool: 2-3 hops apart + pairs with recorded events but no relationship
        pairs = set()
        rng = np.random.RandomState(3)
        for n in cx.nodes:
            if cx.deg[n] == 0:
                continue
            for m2, d in nx.single_source_shortest_path_length(S, n, cutoff=3).items():
                if d >= 2 and n < m2:
                    pairs.add((n, m2))
        pairs |= {p for p in model["evp"] if p[0] in S and p[1] in S and p not in edge_set and cx.deg[p[0]] and cx.deg[p[1]]}
        pairs = sorted(p for p in pairs if p not in existing and cx.type[p[0]] not in STRUCTURAL_TYPES and cx.type[p[1]] not in STRUCTURAL_TYPES)
        if len(pairs) > 25000:
            pairs = sorted(map(tuple, (pairs[i] for i in rng.choice(len(pairs), 25000, replace=False))))
        X = cx.matrix(pairs) if pairs else np.zeros((0, len(FKEYS)))
        probs = model["lr"].predict_proba(model["scaler"].transform(X))[:, 1] if len(pairs) else np.array([])
        order = np.argsort(-probs, kind="stable")[:limit]
        out = []
        for rank, i in enumerate(order, 1):
            u, v = pairs[i]
            x = X[i]
            c = _contribs(model, x)
            top = sorted(range(len(FKEYS)), key=lambda j: -c[j])
            reasons = [{"feature": FKEYS[j], "label": FLABEL[FKEYS[j]], "contribution": _r(c[j]), "text": _reason_text(FKEYS[j], x, u, v, cx, names)} for j in top if c[j] > 0 and _supports(FKEYS[j], x, cx, u, v)][:3]
            out.append({"rank": rank, "source": u, "target": v, "source_name": names[u], "target_name": names[v], "source_type": cx.type[u], "target_type": cx.type[v],
                        "probability": _r(probs[i], 3), "reasons": reasons, "common_neighbours": [{"id": w, "name": names[w]} for w in sorted(cx.common(u, v))[:6]],
                        "suggested_relation": _suggest_relation(G, cx, u, v), "hops": cx.dist(u, v),
                        "features": {k: _r(x[j]) for j, k in enumerate(FKEYS)}})
        return {**base, "candidates_scored": len(pairs), "suggestions": out, "evaluation": model["evaluation"], "training": model["train"],
                "message": model.get("message", ""), "model": {"type": "logistic regression on 12 structural/attribute features", "version": MODEL_VERSION,
                                                              "note": "Probability is the classifier's output on a class-balanced training set: a ranking signal, not a calibrated likelihood."}}

    @router.post("/netai/link-predictions/accept")
    def accept_link(x: AcceptIn, db=Depends(ctx.get_db), user=Depends(perm("review"))):
        cn, G, names = context(db, user, x.case_number)
        for e in (x.source, x.target):
            not_found_entity(G, e)
        if x.source == x.target:
            raise HTTPException(400, "A link needs two different entities")
        rel = re.sub(r"[^A-Z0-9_]+", "_", x.relation_type.strip().upper()).strip("_")
        if len(rel) < 2:
            raise HTTPException(400, "relation_type must contain letters or digits")
        pair = tuple(sorted((x.source, x.target)))
        for r in svc.relationships(db, cn, include_candidates=True):
            if tuple(sorted((r["source"], r["target"]))) == pair and (r["verification_state"] not in ("candidate", "unreviewed") or r["relation"] == rel):
                raise HTTPException(409, "These entities are already linked (or a candidate for this relation already exists)")
        prob, reasons = 0.1, []
        model = link_model(cn, G, svc.events(db, cn))
        if model["status"] == "ok":
            _x, rows, p = _explain_pair(model, x.source, x.target, names)
            xx = model["cx"].features(x.source, x.target)
            prob, reasons = p, [r["text"] for r in rows if r["contribution"] > 0 and _supports(r["feature"], xx, model["cx"], x.source, x.target)][:3]
        conf = round(min(prob, 0.5), 3)
        try:
            res = svc.add_relationship(db, {"source_id": x.source, "target_id": x.target, "relation_type": rel, "confidence": conf, "source_ref": SOURCE_REF,
                                            "event_time": None, "verification_state": "candidate", "model_version": MODEL_VERSION,
                                            "metadata": {"netai": {"model_probability": round(prob, 3), "reasons": reasons, "note": x.note, "proposed_by": user["sub"]}}},
                                       user["sub"], case_number=cn)
        except ValueError as exc:
            raise ctx.bad(exc)
        ctx.audit(db, user["sub"], "netai.link_candidate_created", {"case_number": cn, "relationship_id": res.get("id"), "source": x.source, "target": x.target,
                                                                      "relation": rel, "model_probability": round(prob, 3), "verification_state": "candidate"})
        return {"status": "candidate_created", "id": res.get("id"), "verification_state": "candidate", "confidence": conf, "relation": rel,
                "message": "Saved as an UNVERIFIED candidate relationship. It stays out of the verified graph until a reviewer verifies it."}

    # ---------------- explainability
    @router.get("/netai/explain/alert/{code}")
    def explain_alert_ep(code: str, case_number: str, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        from backend.app.models.models import Alert
        cn, G, names = context(db, user, case_number)
        row = db.query(Alert).filter_by(code=code[:100]).first()
        if not row or row.case_number != cn:
            raise HTTPException(404, "Alert not found")
        import json as _json
        try:
            stored_expl = _json.loads(row.explanation or "[]")
            stored_refs = _json.loads(row.source_refs or "[]")
        except ValueError:
            stored_expl, stored_refs = [], []
        events = svc.events(db, cn)
        out = {"case_number": cn, "alert": {"code": row.code, "severity": row.severity, "title": row.title, "status": row.status, "confidence": row.confidence,
                                             "model_version": row.model_version, "entity_id": row.entity_id, "stored_factors": stored_expl},
               "disclaimer": "Investigative signal only; requires authorized human verification.", "features": [], "sentences": [], "reproduced": False}
        eid = row.entity_id
        entities = svc.entities(db, cn, role="investigator")
        ids = [e["external_id"] for e in entities]
        if eid and eid in ids and str(row.model_version).startswith("anomaly-iso-lof"):
            rows = anomaly_rows(G, entities, events)
            full, feats = anomaly_breakdown(rows, ids.index(eid), compute_effects=len(rows) <= 2500)
            stored = round((row.confidence or 0) * 100)
            for f in feats:
                f["sentence"] = _feature_sentence(f)
            feats.sort(key=lambda f: -abs(f["robust_z"]))
            out.update(reproduced=True, reproduced_score=full, stored_score=stored, matches_stored=abs(full - stored) <= 2, features=feats,
                       sentences=[f["sentence"] for f in feats[:4]],
                       method="Contributions are robust z-scores (median/MAD) against this case's own baseline, plus a leave-one-feature-out effect: "
                              "the feature is replaced by the case median and the Isolation-Forest + LOF score recomputed.",
                       evidence=_entity_evidence(G, events, eid, names))
            out["evidence"]["source_refs"] = sorted(set(out["evidence"]["source_refs"]) | set(stored_refs))
            if not out["matches_stored"]:
                out["note"] = "The graph has changed since this alert was generated, so the recomputed score differs from the stored one."
        else:
            out.update(note="This alert was not produced by the multivariate anomaly model (or has no entity), so no feature vector can be reproduced.",
                       sentences=[str(s) for s in stored_expl], evidence={"source_refs": stored_refs, "relationships": [], "relationship_count": 0, "event_count": 0, "sample_events": []})
            if eid and eid in G:
                out["evidence"] = {**_entity_evidence(G, events, eid, names), "source_refs": sorted(set(stored_refs))}
        return out

    @router.get("/netai/explain/entity")
    def explain_entity(entity_id: str = Query(max_length=100), case_number: str = Query(max_length=100), db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn, G, names = context(db, user, case_number)
        not_found_entity(G, entity_id)
        events = svc.events(db, cn)
        entities = svc.entities(db, cn, role="investigator")
        ids = [e["external_id"] for e in entities]
        out = {"case_number": cn, "entity": {"id": entity_id, "name": names[entity_id], "type": G.nodes[entity_id].get("type")}, "features": [], "sentences": [],
               "disclaimer": "Structural and statistical signals for review; not a finding about the entity."}
        if entity_id in ids and len(ids) >= 6:
            rows = anomaly_rows(G, entities, events)
            full, feats = anomaly_breakdown(rows, ids.index(entity_id), compute_effects=len(rows) <= 2500)
            for f in feats:
                f["sentence"] = _feature_sentence(f)
            feats.sort(key=lambda f: -abs(f["robust_z"]))
            out.update(anomaly_score=full, features=feats, sentences=[f["sentence"] for f in feats[:4]],
                       method="Robust z-scores against the case baseline and leave-one-feature-out effect on the anomaly score (uncalibrated, case-relative).")
        else:
            out["note"] = "Fewer than 6 entities in this case: the anomaly model is not run."
        role = classify_roles(G, types=(G.nodes[entity_id].get("type", ""),))
        mine = next((r for r in role.get("roles", []) if r["entity_id"] == entity_id), None)
        out["role"] = mine
        S = simple_graph(G)
        out["neighbours"] = [{"id": w, "name": names[w], "type": S.nodes[w].get("type"), "relations": sorted(S[entity_id][w]["relations"]), "confidence": _r(S[entity_id][w]["confidence"], 3)}
                             for w in sorted(S[entity_id], key=lambda w: -S[entity_id][w]["confidence"])[:15]]
        out["evidence"] = _entity_evidence(G, events, entity_id, names)
        return out

    @router.get("/netai/explain/link")
    def explain_link(source: str = Query(max_length=100), target: str = Query(max_length=100), case_number: str = Query(max_length=100),
                     db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn, G, names = context(db, user, case_number)
        for e in (source, target):
            not_found_entity(G, e)
        if source == target:
            raise HTTPException(400, "Choose two different entities")
        S = simple_graph(G)
        rels = []
        for a, b in ((source, target), (target, source)):
            for d in (G.get_edge_data(a, b) or {}).values():
                rels.append({"source": a, "target": b, "relation": d.get("relation"), "confidence": _r(d.get("confidence"), 3), "source_ref": d.get("source_ref", ""),
                             "verification_state": d.get("verification_state", ""), "event_time": d.get("event_time") or None})
        common = sorted(set(S[source]) & set(S[target]))
        out = {"case_number": cn, "source": {"id": source, "name": names[source]}, "target": {"id": target, "name": names[target]}, "linked": bool(rels), "relationships": rels,
               "common_neighbours": [{"id": w, "name": names[w], "type": S.nodes[w].get("type"), "degree": S.degree(w)} for w in common[:15]],
               "evidence": {"source_refs": sorted({r["source_ref"] for r in rels if r["source_ref"]}), "relationships": rels},
               "disclaimer": "Structural explanation for a human reviewer; not a finding."}
        sentences = []
        if rels:
            best = max(rels, key=lambda r: r["confidence"])
            sentences.append(f"{len(rels)} recorded relationship(s); strongest is {best['relation']} with confidence {best['confidence']:.2f} (source {best['source_ref'] or 'n/a'}).")
            refs = out["evidence"]["source_refs"]
            sentences.append(f"Backed by {len(refs)} distinct source reference(s)." if refs else "No source reference is attached to these relationships.")
            H = S.copy(); H.remove_edge(source, target)
            if nx.has_path(H, source, target):
                sentences.append(f"Corroborated indirectly: another route of {nx.shortest_path_length(H, source, target)} hops connects them without this link.")
            else:
                sentences.append("No alternative route exists: this link is the ONLY connection between the two (a bridge), so its evidence matters.")
        if common:
            sentences.append(f"They share {len(common)} neighbour(s): " + ", ".join(names[w] for w in common[:4]) + ("..." if len(common) > 4 else "") + ".")
        events = svc.events(db, cn)
        n_ev = sum(1 for e in events if {e["entity_id"], e.get("related_entity_id")} == {source, target})
        if n_ev:
            sentences.append(f"{n_ev} recorded event(s) directly between them.")
        model = link_model(cn, G, events)
        if model["status"] == "ok":
            _x, rows, p = _explain_pair(model, source, target, names)
            out["model"] = {"probability": _r(p, 3), "contributions": rows, "note": "Link-prediction model score for this pair (computed on the full graph, so an existing link contributes only through structure)."}
            if not rels:
                sentences.append(f"Link-prediction score {p:.2f}: " + "; ".join(r["text"] for r in rows if r["contribution"] > 0 and _supports(r["feature"], _x, model["cx"], source, target))[:400])
        else:
            out["model"] = {"status": "too_small", "message": model["message"]}
        out["sentences"] = sentences
        return out

    # ---------------- roles / critical nodes / simulation
    @router.get("/netai/roles")
    def roles(case_number: str, types: str = Query(",".join(ROLE_TYPES_DEFAULT), max_length=200), db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn, G, names = context(db, user, case_number)
        res = classify_roles(G, tuple(t.strip() for t in types.split(",") if t.strip()))
        for r in res["roles"]:
            r["name"] = names.get(r["entity_id"], r["entity_id"])
        res["case_number"] = cn
        return res

    @router.get("/netai/critical-nodes")
    def critical(case_number: str, k: int = Query(5, ge=1, le=10), include_structural: bool = False, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn, G, names = context(db, user, case_number)
        S = simple_graph(G)
        allow = None if include_structural else {t for _n, t in S.nodes(data="type") if t not in STRUCTURAL_TYPES}
        res = critical_nodes(S, k, allow)
        for r in res["articulation_points"] + res["greedy"]:
            r["name"] = names[r["entity_id"]]
        res["articulation_points"] = res["articulation_points"][:30]
        res.update(case_number=cn, baseline=graph_metrics(S), objective="Fewest still-connected entity pairs after removal (greedy). Excludes structural nodes (case/FIR, sections, stations) unless include_structural=true.",
                   disclaimer="Disruption analysis describes the recorded graph only. It is a what-if for planning and does not recommend any action against a person.")
        return res

    return router
