"""Network AI: link prediction, explainability, roles, disruption simulation and the what-if sandbox."""
import itertools
import uuid

import pytest


def _case(client, headers, edges, extra_nodes=(), types=None, confidence=0.9):
    """Create a case with verified relationships. Returns (case_number, prefix)."""
    tag = uuid.uuid4().hex[:6].upper()
    cn = f"CASE-NA-{tag}"
    assert client.post("/cases", json={"case_number": cn, "title": "netai test"}, headers=headers).status_code == 200
    nodes = sorted({n for e in edges for n in e[:2]} | set(extra_nodes))
    for n in nodes:
        r = client.post("/entities", json={"external_id": f"{tag}-{n}", "name": f"N {n}", "entity_type": (types or {}).get(n, "PERSON"), "case_number": cn}, headers=headers)
        assert r.status_code == 200, r.text
    for e in edges:
        a, b = e[0], e[1]
        rel = e[2] if len(e) > 2 else "ASSOCIATED_WITH"
        r = client.post("/relationships", json={"source_id": f"{tag}-{a}", "target_id": f"{tag}-{b}", "relation_type": rel, "confidence": confidence, "source_ref": f"SRC-{a}-{b}",
                                                "verification_state": "verified", "case_number": cn}, headers=headers)
        assert r.status_code == 200, r.text
    return cn, tag


def _two_cliques():
    A = [f"a{i}" for i in range(7)]; B = [f"b{i}" for i in range(7)]
    edges = [(x, y) for x, y in itertools.combinations(A, 2) if (x, y) != ("a0", "a1")]
    edges += [(x, y) for x, y in itertools.combinations(B, 2) if (x, y) != ("b0", "b1")]
    edges += [("a6", "b6")]
    return edges


@pytest.fixture()
def inv(make_user):
    return make_user("supervisor")[2]


def test_link_prediction_ranks_triangle_closing_edges_high(client, inv):
    cn, tag = _case(client, inv, _two_cliques())
    r = client.get("/netai/link-predictions", params={"case_number": cn, "limit": 10}, headers=inv)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "ok" and d["limits"] and "hypothes" in d["disclaimer"]
    top = [(s["source"], s["target"]) for s in d["suggestions"][:4]]
    missing = {(f"{tag}-a0", f"{tag}-a1"), (f"{tag}-b0", f"{tag}-b1")}
    got = {tuple(sorted(p)) for p in top}
    assert missing <= got, (missing, top)
    best = d["suggestions"][0]
    assert best["probability"] > 0.5 and best["reasons"] and best["common_neighbours"]
    ev = d["evaluation"]
    assert ev and ev["auc"]["logistic_regression"]["mean"] > 0.5
    assert "baseline_common_neighbours" in ev["auc"] and ev["precision_at_k"]["random_guess"] > 0
    # existing edges are never suggested
    assert all(tuple(sorted((s["source"], s["target"]))) not in {(f"{tag}-a0", f"{tag}-a2")} for s in d["suggestions"])


def test_link_prediction_tiny_graph_is_graceful(client, inv):
    cn, _ = _case(client, inv, [("x", "y"), ("y", "z")])
    d = client.get("/netai/link-predictions", params={"case_number": cn}, headers=inv).json()
    assert d["status"] == "too_small" and "too small" in d["message"].lower() and d["suggestions"] == []


def test_accept_creates_unverified_candidate_and_audits(client, inv, login):
    cn, tag = _case(client, inv, _two_cliques())
    before = client.get("/graph/summary", params={"case_number": cn}, headers=inv).json()
    body = {"case_number": cn, "source": f"{tag}-a0", "target": f"{tag}-a1", "relation_type": "possibly linked"}
    r = client.post("/netai/link-predictions/accept", json=body, headers=inv)
    assert r.status_code == 200, r.text
    assert r.json()["verification_state"] == "candidate" and r.json()["confidence"] <= 0.5
    rels = client.get("/relationships", params={"case_number": cn, "include_candidates": True}, headers=inv).json()
    cand = [x for x in rels if x["source_ref"] == "netai-link-prediction"]
    assert len(cand) == 1 and cand[0]["verification_state"] == "candidate" and cand[0]["relation"] == "POSSIBLY_LINKED"
    after = client.get("/graph/summary", params={"case_number": cn}, headers=inv).json()
    assert before == after  # not part of the verified graph
    assert client.post("/netai/link-predictions/accept", json=body, headers=inv).status_code == 409
    log = client.get("/audit", params={"case_number": cn}, headers=inv).json()["events"]
    assert any(e["action"] == "netai.link_candidate_created" for e in log)
    body2 = {**body, "target": f"{tag}-nope"}
    assert client.post("/netai/link-predictions/accept", json=body2, headers=inv).status_code == 404
    assert client.post("/netai/link-predictions/accept", json={**body, "relation_type": "x"}, headers=inv).status_code == 422


def test_authorization_and_cross_case_isolation(client, inv, make_user, login):
    cn, tag = _case(client, inv, _two_cliques())
    sub = make_user("auditor")[2]
    other = make_user("supervisor")[2]
    paths = [("get", "/netai/link-predictions", {"case_number": cn}), ("get", "/netai/roles", {"case_number": cn}), ("get", "/netai/critical-nodes", {"case_number": cn}),
             ("get", "/netai/explain/entity", {"case_number": cn, "entity_id": f"{tag}-a0"})]
    for m, p, q in paths:
        assert client.get(p, params=q).status_code == 401
        assert client.get(p, params=q, headers=sub).status_code == 403
        assert client.get(p, params=q, headers=other).status_code == 404  # existence of the case is not disclosed
        assert client.get(p, params=q, headers=inv).status_code == 200
    acc = {"case_number": cn, "source": f"{tag}-a0", "target": f"{tag}-a1", "relation_type": "LINKED"}
    assert client.post("/netai/link-predictions/accept", json=acc, headers=sub).status_code == 403
    assert client.post("/netai/link-predictions/accept", json=acc, headers=other).status_code == 404
    # a supervisor may analyse and review but not ingest
    from backend.app.db.database import SessionLocal
    from backend.app.models.models import CaseMember
    name, _pw, sup = make_user("supervisor")
    with SessionLocal() as db:
        db.add(CaseMember(case_number=cn, username=name, added_by="test")); db.commit()
    assert client.get("/netai/roles", params={"case_number": cn}, headers=sup).status_code == 200
    assert client.get("/netai/roles", params={"case_number": cn}, headers=login("admin")).status_code == 200


def _star_case(client, inv):
    edges = [("H", "L1"), ("H", "L2"), ("H", "L3"), ("H", "L4"), ("L4", "L5"), ("X", "Y")]
    cn, tag = _case(client, inv, edges)
    return cn, tag


def test_critical_nodes_and_roles(client, inv):
    cn, tag = _star_case(client, inv)
    d = client.get("/netai/critical-nodes", params={"case_number": cn, "k": 2}, headers=inv).json()
    arts = [a["entity_id"] for a in d["articulation_points"]]
    assert arts[0] == f"{tag}-H" and f"{tag}-L4" in arts
    assert d["greedy"][0]["entity_id"] == f"{tag}-H" and d["greedy"][0]["pairs_removed_this_step"] == 14  # 16 connected pairs -> 2
    roles = client.get("/netai/roles", params={"case_number": cn}, headers=inv).json()
    assert "NOT assertions" in roles["disclaimer"]
    by = {r["entity_id"]: r for r in roles["roles"]}
    assert by[f"{tag}-H"]["role"] == "Coordinator / Hub" and by[f"{tag}-H"]["reasons"] and 0 < by[f"{tag}-H"]["confidence"] <= 1
    assert by[f"{tag}-L4"]["role"] == "Courier / Intermediary"


def test_explain_endpoints(client, inv):
    cn, tag = _case(client, inv, _two_cliques())
    e = client.get("/netai/explain/entity", params={"case_number": cn, "entity_id": f"{tag}-a6"}, headers=inv).json()
    assert len(e["features"]) == 6 and e["evidence"]["relationship_count"] >= 6 and e["role"]
    assert all({"robust_z", "case_median", "sentence"} <= set(f) for f in e["features"])
    link = client.get("/netai/explain/link", params={"case_number": cn, "source": f"{tag}-a6", "target": f"{tag}-b6"}, headers=inv).json()
    assert link["linked"] and any("ONLY connection" in s for s in link["sentences"])
    miss = client.get("/netai/explain/link", params={"case_number": cn, "source": f"{tag}-a0", "target": f"{tag}-a1"}, headers=inv).json()
    assert not miss["linked"] and miss["common_neighbours"] and miss["model"]["probability"] > 0.5
    assert client.get("/netai/explain/entity", params={"case_number": cn, "entity_id": "ghost"}, headers=inv).status_code == 404


def test_explain_alert_scope(client, inv, make_user):
    from backend.app.db.database import SessionLocal
    from backend.app.models.models import Alert
    cn, tag = _case(client, inv, _two_cliques())
    code = f"AL-NA-{tag}"
    with SessionLocal() as db:
        db.add(Alert(code=code, severity="Review", title="t", confidence=0.7, explanation='["8 graph connections"]', entity_id=f"{tag}-a6", case_number=cn,
                     model_version="anomaly-iso-lof-v2", source_refs='["SRC-a6-b6"]')); db.commit()
    d = client.get(f"/netai/explain/alert/{code}", params={"case_number": cn}, headers=inv)
    assert d.status_code == 200, d.text
    j = d.json()
    assert j["reproduced"] is True and "reproduced_score" in j and j["evidence"]["source_refs"] and j["features"]
    other = make_user("supervisor")[2]
    assert client.get(f"/netai/explain/alert/{code}", params={"case_number": cn}, headers=other).status_code == 404
    cn2, _ = _case(client, inv, [("p", "q")])
    assert client.get(f"/netai/explain/alert/{code}", params={"case_number": cn2}, headers=inv).status_code == 404  # alert belongs to another case
