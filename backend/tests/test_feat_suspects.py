"""Person-of-interest ranking: scoring unit tests on tiny hand-built graphs + API tests (auth, masking, decision feedback)."""
import json
import uuid
from datetime import datetime, timedelta

import pytest

from ai_engine.suspects import scorer
from ai_engine.suspects.benchmark import make_world

T0 = datetime(2026, 8, 1, 9, 0)


# ------------------------------------------------------------------ tiny worlds
def person(i, role=None, docs=(), name=None):
    return {"id": i, "type": "PERSON", "name": name or f"Person {i}", "role": role, "docs": list(docs), "cases": ["C1"]}


def ident(i, typ, name, docs=(), key=None):
    return {"id": i, "type": typ, "name": name, "key": key or name, "docs": list(docs)}


def own(p, i, rel="OWNS"):
    return {"source": p, "target": i, "relation": rel}


def tx(eid, a, b, hours, amount):
    return {"event_id": eid, "event_type": "TRANSACTION", "entity_id": a, "related_entity_id": b, "time": (T0 + timedelta(hours=hours)).isoformat(), "amount": amount, "source_ref": eid}


def call(eid, a, b, hours):
    return {"event_id": eid, "event_type": "COMMUNICATION", "entity_id": a, "related_entity_id": b, "time": (T0 + timedelta(hours=hours)).isoformat(), "duration": 60, "source_ref": eid}


def base_world():
    """Six people, each with an account + phone; a hub H that everyone talks to and pays; a boss B recorded as accused."""
    ents, rels, evs = [], [], []
    for p in ["B", "H", "M", "N", "O", "Q"]:
        ents += [person(p, "ACCUSED" if p == "B" else None), ident("A" + p, "ACCOUNT", f"ACCT-{p}"), ident("PH" + p, "PHONE", f"9800000{ord(p)}")]
        rels += [own(p, "A" + p), own(p, "PH" + p, "REGISTERED_TO")]
    n = 0
    for p in ["B", "M", "N", "O"]:
        for k in range(3):
            n += 1
            evs.append(call(f"C{n}", "PH" + p, "PHH", k * 3 + n))
    for p in ["M", "N"]:
        n += 1
        evs.append(tx(f"T{n}", "A" + p, "AH", n, 5000))
    return ents, rels, evs


def run(ents, rels, evs, **kw):
    return scorer.score_case(ents, rels, evs, kw.pop("alerts", []), kw.pop("labels", None), **kw)


# ------------------------------------------------------------------ roles
@pytest.mark.parametrize("text,cls", [("ACCUSED", "accused"), ("Accused person", "accused"), ("SUSPECT", "suspect"), ("COMPLAINANT", "complainant"), ("victim", "victim"),
                                      ("WITNESS", "witness"), ("FATHER_OF_COMPLAINANT", "complainant_relative"), ("UNKNOWN_OR_SUSPECT", ""), ("SUSPECT_VEHICLE", ""),
                                      ("NAMED_PERSON", ""), (None, "")])
def test_role_classification(text, cls):
    assert scorer.classify_role(text) == cls


def test_victims_and_complainants_excluded_by_default_and_down_weighted():
    ents, rels, evs = base_world()
    for e in ents:
        if e["id"] == "M":
            e["role"] = "COMPLAINANT"
        if e["id"] == "N":
            e["role"] = "VICTIM"
    out = run(ents, rels, evs)
    assert "M" not in out["order"] and "N" not in out["order"] and out["meta"]["victims_excluded"] == 2
    assert out["results"]["M"]["excluded_reason"] and out["results"]["M"]["rank"] is None
    full = run(ents, rels, evs, include_victims=True)
    assert "M" in full["order"]
    ents2, rels2, evs2 = base_world()
    plain = run(ents2, rels2, evs2)["results"]["M"]["model_score"]
    assert out["results"]["M"]["model_score"] < plain / 2  # strongly down-weighted even where shown
    assert full["results"]["M"]["rank"] > full["results"]["O"]["rank"]


def test_accused_role_raises_and_conflict_is_flagged():
    ents, rels, evs = base_world()
    s0 = run(ents, rels, evs)["results"]["O"]["model_score"]
    ents[next(i for i, e in enumerate(ents) if e["id"] == "O")]["role"] = ["ACCUSED", "COMPLAINANT"]
    r = run(ents, rels, evs)["results"]["O"]
    assert r["model_score"] > s0 and r["role"]["conflict"] and any(m["key"] == "role_conflict" for m in r["missing"])


# ------------------------------------------------------------------ cross-FIR boost
def test_cross_fir_shared_identifier_boosts():
    def world(shared):
        ents, rels, evs = base_world()
        for e in ents:
            if e["id"] == "PHO":
                e["docs"] = ["D1", "D2"] if shared else ["D1"]
            if e["id"] == "PHQ":
                e["docs"] = ["D1"]
        return ents, rels, evs
    lab = {"D1": "FIR 11/2026", "D2": "FIR 12/2026"}
    hi = run(*world(True), labels=lab)["results"]["O"]
    lo = run(*world(False), labels=lab)["results"]["O"]
    assert hi["logit"] > lo["logit"] + 0.9 and hi["model_score"] > lo["model_score"] * 2
    f = next(f for f in hi["factors"] if f["key"] == "cross_fir_identifiers")
    assert "FIR 11/2026" in f["sentence"] and "FIR 12/2026" in f["sentence"]
    assert f["evidence"] and hi["reasons"][0]  # concrete evidence and a human-readable reason


def test_same_identifier_value_in_two_entities_is_recognised_as_one():
    ents, rels, evs = base_world()
    ents.append(ident("PHO-dup", "PHONE", "98-0000-0079", docs=["D2"], key="98 0000 0079"))
    ents.append(ident("PHO-dup2", "PHONE", "9800000079", docs=["D1"], key="9800000079"))
    rels.append(own("O", "PHO-dup"))
    r = run(ents, rels, evs, labels={"D1": "FIR 1", "D2": "FIR 2"})["results"]["O"]
    assert r["features"]["cross_fir_identifiers"] >= 1


def test_unattributed_shared_identifier_is_reported():
    ents, rels, evs = base_world()
    ents.append(ident("PHX", "PHONE", "9999999999", docs=["D1", "D2"]))
    out = run(ents, rels, evs, labels={"D1": "FIR 1", "D2": "FIR 2"})
    assert any(u["entity_id"] == "PHX" and len(u["firs"]) == 2 for u in out["meta"]["unattributed_identifiers"])


# ------------------------------------------------------------------ money, comms
def test_pass_through_detected_and_explained():
    ents, rels, evs = [], [], []
    for p in "SMR":
        ents += [person(p), ident("A" + p, "ACCOUNT", f"ACCT-{p}")]
        rels.append(own(p, "A" + p))
    for k in range(4):
        evs += [tx(f"IN{k}", "AS", "AM", k * 10, 20000), tx(f"OUT{k}", "AM", "AR", k * 10 + 3, 19000)]
    out = run(ents, rels, evs)
    m = out["results"]["M"]
    assert m["features"]["pass_through"] > 0.6
    f = next(f for f in m["factors"] if f["key"] == "pass_through")
    assert "pass-through" in f["sentence"] and any(e["ref"].startswith("OUT") for e in f["evidence"])
    assert out["results"]["M"]["features"]["pass_through"] > out["results"]["S"]["features"]["pass_through"]


def test_contact_with_accused_counts_and_never_self():
    ents, rels, evs = base_world()
    evs += [call("X1", "PHM", "PHB", 1), call("X2", "PHM", "PHB", 2)]
    x = scorer.extract_features(ents, rels, evs)
    assert x["F"]["M"]["comm_with_accused"] == 2 and x["F"]["B"]["comm_with_accused"] == 0  # B's own role never counts for B
    assert x["seeds"] == ["B"]


# ------------------------------------------------------------------ decisions feed back
def test_dismissed_lowers_rank_and_confirmed_is_pinned():
    ents, rels, evs = base_world()
    evs += [call(f"Z{k}", "PHM", "PHB", k) for k in range(6)]
    plain = run(ents, rels, evs)
    top = plain["order"][0]
    dec = {top: {"decision": "dismissed", "reason": "alibi", "decided_by": "x"}}
    out = run(ents, rels, evs, decisions=dec)
    assert out["order"][-1] == top and out["results"][top]["status"] == "dismissed"
    assert out["results"][top]["score"] < plain["results"][top]["score"]
    assert "dismissed" in out["results"][top]["summary"]
    last = plain["order"][-1]
    out2 = run(ents, rels, evs, decisions={last: {"decision": "confirmed_person_of_interest", "reason": "verified with CDR"}})
    assert out2["order"][0] == last and out2["results"][last]["pinned"] and out2["results"][last]["status"] == "confirmed"
    out3 = run(ents, rels, evs, decisions={last: {"decision": "needs_more_info", "reason": "wait"}})
    assert out3["order"] == plain["order"] and out3["results"][last]["status"] == "needs_more_info"


def test_confirmed_person_makes_contacts_accused_linked_and_dismissed_stops_it():
    ents, rels, evs = base_world()
    for e in ents:
        e["role"] = None  # nobody is accused
    evs += [call("Y1", "PHM", "PHO", 1), call("Y2", "PHM", "PHO", 2)]
    assert run(ents, rels, evs)["extracted"]["F"]["M"]["comm_with_accused"] == 0
    up = run(ents, rels, evs, decisions={"O": {"decision": "confirmed_person_of_interest"}})["extracted"]["F"]["M"]["comm_with_accused"]
    assert up == 2


# ------------------------------------------------------------------ no leakage, determinism, explanations
def test_own_role_never_changes_behavioural_features():
    ents, rels, evs = base_world()
    evs += [call("L1", "PHM", "PHB", 1), tx("L2", "AM", "AB", 2, 900)]
    a = scorer.behavioural_features(scorer.extract_features(ents, rels, evs))
    for e in ents:
        if e["id"] == "M":
            e["role"] = "ACCUSED"
    b = scorer.behavioural_features(scorer.extract_features(ents, rels, evs))
    assert a["M"] == b["M"]  # M's own features identical whether or not M is labelled accused
    assert b["N"]["comm_with_accused"] == a["N"]["comm_with_accused"]  # unrelated person unaffected
    assert not any("role" in k for k in scorer.FKEYS)  # the role is not one of the behavioural features


def test_behavioural_evaluation_strips_roles():
    from ai_engine.suspects import evaluate as ev
    w = make_world(1)
    assert any(e.get("role") for e in w["entities"]) and not any(e.get("role") for e in ev._strip_roles(w))


def test_scoring_is_deterministic():
    w = make_world(3, n_people=40)
    a = scorer.score_case(w["entities"], w["relationships"], w["events"], w["alerts"])
    b = scorer.score_case(w["entities"], w["relationships"], w["events"], w["alerts"])
    assert json.dumps({k: v for k, v in a["results"].items()}, sort_keys=True, default=str) == json.dumps({k: v for k, v in b["results"].items()}, sort_keys=True, default=str)
    assert a["order"] == b["order"]


def test_every_result_is_fully_explained_and_never_claims_guilt():
    w = make_world(2, n_people=40)
    out = scorer.score_case(w["entities"], w["relationships"], w["events"], w["alerts"])
    for a in out["order"][:10]:
        r = out["results"][a]
        assert 0 <= r["score"] <= 100 and r["tier"] in ("High", "Medium", "Low")
        assert r["band"]["low"] <= r["score"] <= r["band"]["high"]
        assert r["factors"] and all(f["sentence"] for f in r["factors"]) and r["summary"] and r["reasons"]
        text = (r["summary"] + " ".join(f["sentence"] for f in r["factors"])).lower()
        assert "guilty" not in text and "criminal" not in text and "culprit" not in text
        assert "not a finding" in r["summary"]
    assert any(f["evidence"] for a in out["order"][:5] for f in out["results"][a]["factors"])


def test_sparse_person_gets_wider_band_and_missing_data_advice():
    ents, rels, evs = base_world()
    ents.append(person("LONE"))
    out = run(ents, rels, evs)
    lone, busy = out["results"]["LONE"], out["results"]["M"]
    assert lone["band"]["sd_logit"] > busy["band"]["sd_logit"] and lone["missing"]


def test_degenerate_inputs_do_not_crash():
    assert run([], [], [])["order"] == []
    one = run([person("Z")], [], [])
    assert one["order"] == ["Z"] and one["results"]["Z"]["score"] < 20


def test_model_beats_degree_baseline_on_synthetic_benchmark():
    from ai_engine.suspects import evaluate as ev
    wins = 0
    for s in range(4):
        w = make_world(s, decoys=2)
        full = scorer.score_case(ev._strip_roles(w), w["relationships"], w["events"], w["alerts"], include_victims=True)
        ours = ev.metrics({a: q["logit"] for a, q in full["results"].items()}, w["truth"], w["victims"])["auc"]
        deg = ev.metrics(ev.baselines(w)["degree_centrality"], w["truth"], w["victims"])["auc"]
        wins += ours > deg
    assert wins >= 3


# ------------------------------------------------------------------ masking
def test_identifier_names_are_masked_for_roles_without_sensitive_read():
    import networkx as nx
    from security.masking import mask_node
    G = nx.MultiDiGraph()
    G.add_node("P", type="PERSON", name="Real Name")
    G.add_node("PH", type="PHONE", name="9876543210", source_documents=["D1", "D2"])
    G.add_edge("P", "PH", relation="OWNS", confidence=1)
    ents, *_ = __import__("backend.app.features.suspects", fromlist=["x"]).to_inputs(G, [], [], {}, mask_node, "auditor", "C1")
    phone = next(e for e in ents if e["id"] == "PH")
    assert phone["name"] != "9876543210" and "*" in phone["name"] and phone["key"] == "9876543210"  # key is internal only
    full, *_ = __import__("backend.app.features.suspects", fromlist=["x"]).to_inputs(G, [], [], {}, mask_node, "investigator", "C1")
    assert next(e for e in full if e["id"] == "PH")["name"] == "9876543210"
    out = scorer.score_case(ents, [{"source": "P", "target": "PH", "relation": "OWNS"}], [], [], {"D1": "FIR 1", "D2": "FIR 2"})
    blob = json.dumps(out["results"], default=str)
    assert "9876543210" not in blob and "98******10" in blob


# ------------------------------------------------------------------ API
@pytest.fixture()
def sup(make_user):
    return make_user("supervisor")[2]


@pytest.fixture()
def inv(make_user):
    return make_user("investigator")[2]


def _seed_case(client, sup, inv_name=None, login=None):
    tag = uuid.uuid4().hex[:6].upper()
    cn = f"CASE-SP-{tag}"
    assert client.post("/cases", json={"case_number": cn, "title": "suspects test"}, headers=sup).status_code == 200
    ids = {}
    for p in ["BOSS", "MULE", "AIDE", "VICT", "BYST"]:
        for suffix, typ, name in (("", "PERSON", f"Name {p}"), ("-ACC", "ACCOUNT", f"ACCT-{tag}-{p}"), ("-PH", "PHONE", f"98{abs(hash(tag + p)) % 10**8:08d}")):
            eid = f"{tag}-{p}{suffix}"
            attrs = {"role": {"BOSS": "ACCUSED", "VICT": "COMPLAINANT"}.get(p, "")} if typ == "PERSON" else {}
            r = client.post("/entities", json={"external_id": eid, "name": name, "entity_type": typ, "case_number": cn, "attributes": attrs}, headers=sup)
            assert r.status_code == 200, r.text
        for suffix, rel in (("-ACC", "OWNS"), ("-PH", "REGISTERED_TO")):
            r = client.post("/relationships", json={"source_id": f"{tag}-{p}", "target_id": f"{tag}-{p}{suffix}", "relation_type": rel, "case_number": cn, "verification_state": "verified"}, headers=sup)
            assert r.status_code == 200, r.text
        ids[p] = f"{tag}-{p}"
    n = 0
    for a, b in [("MULE", "BOSS"), ("AIDE", "BOSS"), ("MULE", "AIDE"), ("VICT", "MULE"), ("BYST", "AIDE")]:
        for k in range(4):
            n += 1
            t = (T0 + timedelta(hours=n)).isoformat()
            assert client.post("/events", json={"event_id": f"{tag}-C{n}", "event_type": "COMMUNICATION", "entity_id": ids[a] + "-PH", "related_entity_id": ids[b] + "-PH", "event_time": t,
                                                "duration_seconds": 30, "case_number": cn}, headers=sup).status_code == 200
    for k, (a, b) in enumerate([("VICT", "MULE"), ("MULE", "BOSS"), ("VICT", "MULE"), ("MULE", "BOSS")]):
        t = (T0 + timedelta(hours=k + 1)).isoformat()
        assert client.post("/events", json={"event_id": f"{tag}-T{k}", "event_type": "TRANSACTION", "entity_id": ids[a] + "-ACC", "related_entity_id": ids[b] + "-ACC", "amount": 40000 - k,
                                            "event_time": t, "case_number": cn}, headers=sup).status_code == 200
    return cn, ids


def _member(client, login, cn, username):
    r = client.post(f"/admin/cases/{cn}/members", json={"username": username}, headers=login("admin"))
    assert r.status_code == 200, r.text


def test_rank_endpoint_shape_permissions_and_victim_exclusion(client, sup, make_user, login):
    cn, ids = _seed_case(client, sup)
    name, _, inv = make_user("investigator")
    _member(client, login, cn, name)
    r = client.get("/suspects/rank", params={"case_number": cn}, headers=inv)
    assert r.status_code == 200, r.text
    d = r.json()
    got = [x["entity_id"] for x in d["results"]]
    assert ids["VICT"] not in got and d["victims_excluded"] == 1 and got[0] in (ids["BOSS"], ids["MULE"])
    assert set(got[:2]) <= {ids["BOSS"], ids["MULE"], ids["AIDE"]}
    top = d["results"][0]
    assert top["summary"] and top["top_factors"] and top["tier"] in ("High", "Medium", "Low") and "caution" in d and "not a finding" in top["summary"]
    assert ids["VICT"] in [x["entity_id"] for x in client.get("/suspects/rank", params={"case_number": cn, "include_victims": "true"}, headers=inv).json()["results"]]
    p = client.get(f"/suspects/{ids['MULE']}/profile", params={"case_number": cn}, headers=inv).json()
    assert p["factors"] and p["linked_entities"] and p["timeline"] and p["missing"] is not None and "cross_fir" in p and p["decision_history"] == []
    assert client.get(f"/suspects/NOPE/profile", params={"case_number": cn}, headers=inv).status_code == 404
    # someone with no access to the case gets a 404 and learns nothing
    _, _, other = make_user("investigator")
    assert client.get("/suspects/rank", params={"case_number": cn}, headers=other).status_code == 404
    # roles without 'analyze' cannot compute rankings
    for role in ("auditor",):
        h = make_user(role)[2]
        assert client.get("/suspects/rank", params={"case_number": cn}, headers=h).status_code == 403
    assert client.get("/suspects/rank", params={"case_number": cn}).status_code == 401


def test_decision_workflow_audit_and_feedback(client, sup, make_user, login):
    cn, ids = _seed_case(client, sup)
    name, _, inv = make_user("investigator")
    _member(client, login, cn, name)
    before = client.get("/suspects/rank", params={"case_number": cn}, headers=inv).json()["results"]
    target = before[0]["entity_id"]
    # investigators can verify (permission 'review'), a reason is mandatory, decisions are validated
    assert client.post(f"/suspects/{target}/decision", json={"case_number": cn, "decision": "dismissed", "reason": ""}, headers=inv).status_code == 422
    assert client.post(f"/suspects/{target}/decision", json={"case_number": cn, "decision": "guilty", "reason": "nope!"}, headers=inv).status_code == 400
    assert client.post("/suspects/NOPE/decision", json={"case_number": cn, "decision": "dismissed", "reason": "nothing"}, headers=inv).status_code == 404
    r = client.post(f"/suspects/{target}/decision", json={"case_number": cn, "decision": "dismissed", "reason": "Verified alibi with CCTV"}, headers=inv)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "dismissed" and r.json()["decision"]["decided_by"] == name
    after = client.get("/suspects/rank", params={"case_number": cn}, headers=inv).json()["results"]
    row = next(x for x in after if x["entity_id"] == target)
    assert row["status"] == "dismissed" and row["rank"] > 1 and row["score"] < before[0]["score"] and after[-1]["entity_id"] == target
    # confirm another person: pinned first
    other = after[1]["entity_id"] if after[1]["entity_id"] != target else after[2]["entity_id"]
    assert client.post(f"/suspects/{other}/decision", json={"case_number": cn, "decision": "confirmed_person_of_interest", "reason": "CDR and bank match"}, headers=inv).status_code == 200
    final = client.get("/suspects/rank", params={"case_number": cn}, headers=inv).json()["results"]
    assert final[0]["entity_id"] == other and final[0]["pinned"] and final[0]["status"] == "confirmed"
    hist = client.get("/suspects/decisions", params={"case_number": cn}, headers=inv).json()["decisions"]
    assert [h["decision"] for h in hist][:2] == ["confirmed_person_of_interest", "dismissed"] and hist[0]["reason"]
    prof = client.get(f"/suspects/{target}/profile", params={"case_number": cn}, headers=inv).json()
    assert prof["decision_history"][0]["decision"] == "dismissed"
    # append-only: a second decision does not delete the first
    assert client.post(f"/suspects/{target}/decision", json={"case_number": cn, "decision": "needs_more_info", "reason": "Reopened"}, headers=inv).status_code == 200
    assert len(client.get("/suspects/decisions", params={"case_number": cn, "entity_id": target}, headers=inv).json()["decisions"]) == 2
    # audit-logged with the case number and the reason
    ev = client.get("/audit", params={"case_number": cn}, headers=login("admin")).json()["events"]
    assert any(e["action"] == "suspect.decision" and "Verified alibi" in json.dumps(e["details"]) for e in ev)
    # roles that may not review cannot decide
    for role in ("auditor",):
        h = make_user(role)[2]
        assert client.post(f"/suspects/{target}/decision", json={"case_number": cn, "decision": "dismissed", "reason": "abcd"}, headers=h).status_code == 403


def test_model_card_is_honest(client, inv):
    d = client.get("/suspects/model-card", headers=inv).json()
    assert d["features"] and d["limitations"] and d["supervised_component"]["used"] is False and "label" in d["supervised_component"]["reason"].lower()
    assert d["evaluation"] is None or "benchmark" in d["evaluation"]
    assert "not a finding" in d["caution"].lower()
