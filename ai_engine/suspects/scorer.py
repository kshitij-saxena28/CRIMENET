"""Person-of-interest lead-priority scorer: a transparent, weighted *evidence* model.

This module is pure (no database, no HTTP) so it can be unit-tested on tiny hand-built graphs.

What it is
----------
For every PERSON / ORGANIZATION in a case it combines many independent signals into a log-odds style
evidence sum, squashes it to a 0-100 *lead-priority* score, and explains every point of it:

    logit = bias + role_prior + sum_f  w_f * s_f            (families saturate with tanh, see FAMILY_CAP)
    score = 100 * sigmoid(logit)

* ``s_f`` in [0,1] is the feature after normalisation: a blend of an absolute saturating transform and the
  actor's percentile among the case's candidates (the blend leans on the absolute part in small cases).
* The weights are *expert-elicited*, documented in MODEL_CARD and NOT fitted to labels: the bundled datasets
  contain no usable ground-truth labels (see docs/models/suspect-model-card.md), so the model never claims to
  be a calibrated probability.
* The role prior comes from FIR role labels. Complainants, victims and witnesses are strongly down-weighted and
  hidden by default.
* ``behavioural_features`` (everything except the role) never look at the subject's own role. Neighbour roles are
  used only for "contact with accused-linked people" signals, and never the subject's own, so there is no
  label leakage in the evaluation of the behavioural component (ai_engine/suspects/evaluate.py).

The output is decision SUPPORT: a lead priority with evidence and a required human verification step. It is not a
finding about any person.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import networkx as nx

MODEL_VERSION = "suspect-evidence-v1"

ACTOR_TYPES = {"PERSON", "ORGANIZATION"}
IDENT_TYPES = {"PHONE", "VEHICLE", "ACCOUNT", "EMAIL", "DIGITAL_IDENTIFIER", "DEVICE", "IMEI", "WALLET"}
COMM_TYPES = {"COMMUNICATION", "CALL", "SMS", "MESSAGE", "CDR", "VOICE_CALL"}
FIN_TYPES = {"TRANSACTION", "TRANSFER", "PAYMENT", "UPI", "NEFT", "IMPS"}
LOC_TYPES = {"LOCATION_EVENT", "MEETING", "VEHICLE_SIGHTING", "SIGHTING", "CHECK_IN"}
OWN_RELS = {"OWNS", "OWNED_BY", "REGISTERED_TO", "USED", "USES", "HOLDS", "HAS", "HAS_ACCOUNT", "HAS_PHONE", "OPERATES", "DRIVES",
            "SUBSCRIBER_OF", "ACCOUNT_OF", "PHONE_OF", "VEHICLE_OF", "SUSPECT_VEHICLE"}
MONEY_RELS = ("TRANSFER", "PAID", "SENT", "PAYMENT", "DEPOSIT", "WITHDRAW")
COMM_RELS = ("CALL", "MESSAGE", "SMS", "CONTACT", "COMMUNICAT", "TEXTED", "EMAILED")
MEET_RELS = ("MET", "MEETING", "SEEN_WITH", "SEEN_NEAR", "CO_LOCATED")

# ----------------------------------------------------------------------------- roles
NEGATIVE_ROLES = {"victim", "complainant", "witness", "complainant_relative"}
ROLE_PRIOR = {"accused": 2.2, "suspect": 1.6, "": 0.0, "witness": -2.5, "complainant_relative": -2.0, "complainant": -3.5, "victim": -4.0}
ROLE_LABEL = {"accused": "Named as accused", "suspect": "Named as suspect", "": "Role not recorded", "witness": "Witness",
              "complainant_relative": "Relative of complainant", "complainant": "Complainant / informant", "victim": "Victim"}
ROLE_UNKNOWN_SD = 1.0  # logit sd contributed by not knowing the role


def classify_role(text) -> str:
    """Map a free-text role label from FIR extraction to one of accused|suspect|victim|complainant|witness|complainant_relative|''.

    'UNKNOWN_OR_SUSPECT', 'SUSPECT_VEHICLE' and other description labels describe an *unidentified* person or an
    object, so they do not count as a role of this entity."""
    t = re.sub(r"[^a-z]+", "_", str(text or "").lower()).strip("_")
    if not t or "unknown" in t or "description" in t or t.endswith("_vehicle") or "vehicle" in t or t in ("named_person", "person", "other", "officer"):
        return ""
    if "father_of" in t or "relative" in t or "mother_of" in t:
        return "complainant_relative"
    if any(k in t for k in ("accused", "offender", "perpetrator", "arrested", "absconding")):
        return "accused"
    if "suspect" in t:
        return "suspect"
    if "victim" in t:
        return "victim"
    if "complainant" in t or "informant" in t:
        return "complainant"
    if "witness" in t:
        return "witness"
    return ""


def _roles_of(entity, rel_labels) -> set:
    raw = entity.get("role") if isinstance(entity, dict) else ""
    vals = raw if isinstance(raw, (list, tuple, set)) else [raw]
    found = {classify_role(v) for v in list(vals) + list(rel_labels)}
    found.discard("")
    return found


def resolve_role(roles: set) -> tuple[str, bool]:
    """(effective role class, conflict?). Accused/suspect wins over a victim/complainant label but is flagged for review."""
    pos = [r for r in ("accused", "suspect") if r in roles]
    neg = [r for r in ("victim", "complainant", "witness", "complainant_relative") if r in roles]
    if pos and neg:
        return pos[0], True
    if pos:
        return pos[0], False
    if neg:
        return neg[0], False
    return "", False


# ----------------------------------------------------------------------------- features
# key, family, label, weight (log-odds at s=1), abs scale kind, use percentile blend
def _sat(x, scale):
    return 1.0 - math.exp(-max(x, 0.0) / scale)


FEATURES = [
    # graph structure
    ("pagerank", "structure", "Network influence (PageRank)", 0.6, lambda r: _sat(r - 1.0, 2.0), True),
    ("betweenness", "structure", "Bridge position (betweenness)", 0.8, lambda r: _sat(r, 0.05), True),
    ("degree", "structure", "Number of distinct associates", 0.5, lambda r: _sat(r, 6.0), True),
    ("kcore", "structure", "Membership of a dense core (k-core)", 0.4, lambda r: _sat(r - 1.0, 3.0), True),
    ("bridging", "structure", "Links between separate groups", 0.6, lambda r: _sat(r, 2.0), True),
    # money
    ("money_volume", "money", "Money moved (in + out)", 0.5, lambda r: _sat(r, 500000.0), True),
    ("money_fan", "money", "Fan-in / fan-out of payments", 0.5, lambda r: _sat(r, 5.0), True),
    ("pass_through", "money", "Rapid pass-through of funds", 1.0, lambda r: min(1.0, r), False),
    ("money_to_accused", "money", "Payments with accused-linked people", 1.2, lambda r: _sat(r, 3.0), False),
    # communication
    ("comm_with_accused", "comm", "Calls / messages with accused-linked people", 1.2, lambda r: _sat(r, 5.0), False),
    ("comm_burst", "comm", "Burst of communication", 0.5, lambda r: _sat(r - 2.0, 6.0), True),
    ("comm_reach", "comm", "Number of distinct contacts", 0.3, lambda r: _sat(r, 8.0), True),
    # co-location
    ("meet_accused", "coloc", "Meetings / co-presence with accused-linked people", 1.0, lambda r: _sat(r, 3.0), False),
    ("copresence", "coloc", "Distinct people met or co-located with", 0.5, lambda r: _sat(r, 6.0), True),
    # FIR corpus
    ("fir_appearances", "fir", "Appears in several FIRs / cases", 1.0, lambda r: _sat(r - 1.0, 1.5), False),
    ("cross_fir_identifiers", "fir", "Same identifier recurs across FIRs", 1.6, lambda r: _sat(r, 1.0), False),
    # alerts and recency
    ("alerts", "alerts", "Anomaly alerts raised", 1.0, lambda r: _sat(r, 1.5), False),
    ("recency", "recency", "Recent activity", 0.4, lambda r: min(1.0, r), False),
]
FKEYS = [f[0] for f in FEATURES]
FMETA = {f[0]: {"family": f[1], "label": f[2], "weight": f[3]} for f in FEATURES}
FAMILY_CAP = {"structure": 2.0, "money": 3.0, "comm": 2.0, "coloc": 1.6, "fir": 2.4, "alerts": 1.5, "recency": 0.4}
FAMILY_LABEL = {"structure": "Network position", "money": "Money flow", "comm": "Communication", "coloc": "Co-location", "fir": "FIR corpus",
                "alerts": "Anomaly alerts", "recency": "Recency", "role": "FIR role"}
BIAS = -4.0
DISMISS_PENALTY = 3.0
TIER_HIGH, TIER_MEDIUM = 65, 40
PASS_WINDOW_H = 48
BURST_WINDOW_H = 6
COPRESENCE_H = 3
MAX_EVIDENCE = 8


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, x))))


def _t(v):
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo else v
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def _norm_key(v):
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def _pct(vals, x):
    if x <= 0 or not vals:
        return 0.0
    lo = sum(1 for v in vals if v < x)
    eq = sum(1 for v in vals if v == x)
    return (lo + 0.5 * eq) / len(vals)


class _Ev(list):
    """Bounded evidence list."""

    def add(self, item):
        if len(self) < MAX_EVIDENCE:
            self.append(item)


# ----------------------------------------------------------------------------- extraction
def extract_features(entities, relationships, events, alerts=None, fir_labels=None, seeds_extra=(), seeds_exclude=(), reference_time=None):
    """Compute the raw behavioural feature matrix.

    entities: list of dicts {id, type, name, role?, docs?, cases?, key?}. ``name`` is what will be displayed (already masked
    by the caller if needed); ``key`` (optional, identifiers only) is the unmasked value used to recognise the same
    identifier across FIRs.
    relationships: [{source, target, relation, confidence?, ref?}]
    events: [{event_id, event_type, entity_id, related_entity_id, time, amount, duration, source_ref, location_id?, lat?, lon?}]
    alerts: [{code, severity, entity_id, confidence, title, status}]
    fir_labels: {document_id or FIR-node id: 'FIR 123/2026'}

    Returns dict with actors, feature rows, evidence, details, availability and observation counts.
    """
    fir_labels = fir_labels or {}
    alerts = alerts or []
    ents = {e["id"]: e for e in entities}
    actors = sorted(i for i, e in ents.items() if str(e.get("type", "")).upper() in ACTOR_TYPES)
    aset = set(actors)
    etype = {i: str(e.get("type", "")).upper() for i, e in ents.items()}
    name = {i: str(e.get("name") or i) for i, e in ents.items()}

    def label_of(i):
        return name.get(i, i)

    # -- identifier groups (same value across FIRs) and ownership
    group_of, groups = {}, defaultdict(list)
    for i, e in sorted(ents.items()):
        if etype[i] in IDENT_TYPES:
            k = (etype[i], _norm_key(e.get("key") or e.get("name") or i) or i)
            group_of[i] = k
            groups[k].append(i)
    owners_g = defaultdict(set)
    rels_by_ent = defaultdict(list)
    for r in relationships:
        s, t = r.get("source"), r.get("target")
        if s not in ents or t not in ents:
            continue
        rels_by_ent[s].append(r); rels_by_ent[t].append(r)
        rel = str(r.get("relation", "")).upper()
        for a, b in ((s, t), (t, s)):
            if a in aset and b in group_of and rel in OWN_RELS:
                owners_g[group_of[b]].add(a)
    for k, members in groups.items():  # weak fallback: a lone actor neighbour over a non-transactional relation
        if owners_g[k]:
            continue
        cand = set()
        for m in members:
            for r in rels_by_ent[m]:
                o = r["target"] if r["source"] == m else r["source"]
                rel = str(r.get("relation", "")).upper()
                if o in aset and not any(x in rel for x in MONEY_RELS + COMM_RELS + MEET_RELS + ("MENTIONED",)):
                    cand.add(o)
        if len(cand) == 1:
            owners_g[k] = cand

    def owners(i):
        if i in aset:
            return [i]
        g = group_of.get(i)
        return sorted(owners_g[g]) if g else []

    assets = defaultdict(set)
    for k, o in owners_g.items():
        for a in o:
            assets[a].add(k)

    # -- FIR / case appearances
    def appearances(i):
        e = ents[i]
        docs = list(e.get("docs") or [])
        fir = {fir_labels.get(d, d) for d in docs}
        for r in rels_by_ent.get(i, []):
            o = r["target"] if r["source"] == i else r["source"]
            if etype.get(o) == "CASE":
                fir.add(fir_labels.get(o) or name[o])
        cases = set(e.get("cases") or [])
        return fir, cases

    fir_of, cases_of = {}, {}
    for i in list(actors) + list(group_of):
        fir_of[i], cases_of[i] = appearances(i)
    gfir = {}
    for k, members in groups.items():
        f, c = set(), set()
        for m in members:
            f |= fir_of[m]; c |= cases_of[m]
        gfir[k] = (f, c)

    # -- roles
    role_labels = defaultdict(list)
    for r in relationships:
        rel = str(r.get("relation", "")).upper()
        if any(x in rel for x in ("ACCUSED", "SUSPECT_OF", "SUSPECT_IN", "COMPLAINANT", "VICTIM", "WITNESS")):
            for i in (r.get("source"), r.get("target")):
                if i in aset:
                    role_labels[i].append(rel)
    role_class, role_conflict = {}, {}
    for a in actors:
        role_class[a], role_conflict[a] = resolve_role(_roles_of(ents[a], role_labels.get(a, [])))
    seeds = {a for a in actors if role_class[a] in ("accused", "suspect")} | (set(seeds_extra) & aset)
    seeds -= set(seeds_exclude)

    # -- attribute events to actors
    money_in, money_out = defaultdict(list), defaultdict(list)
    comm = defaultdict(list)
    coloc_events = []
    times = []
    last_seen = {}
    ev_by_actor = defaultdict(list)
    n_fin = n_comm = n_loc = 0
    for ev in sorted(events, key=lambda x: (str(x.get("time") or ""), str(x.get("event_id")))):
        typ = str(ev.get("event_type", "")).upper()
        t = _t(ev.get("time"))
        A, B = owners(ev.get("entity_id")), owners(ev.get("related_entity_id") or "")
        eid = ev.get("event_id") or ""
        if t:
            times.append(t)
            for a in set(A) | set(B):
                if a not in last_seen or t > last_seen[a]:
                    last_seen[a] = t
        if typ in FIN_TYPES and ev.get("amount") is not None:
            n_fin += 1
            pairs = [(a, b) for a in A for b in B if a != b]
            for a, b in pairs:
                amt = float(ev["amount"]) / max(1, len(pairs))
                money_out[a].append((t, amt, eid, b)); money_in[b].append((t, amt, eid, a))
                for x, o, d in ((a, b, "out"), (b, a, "in")):
                    ev_by_actor[x].append({"event_id": eid, "type": "TRANSACTION", "time": t, "amount": float(ev["amount"]), "other": o, "dir": d, "ref": ev.get("source_ref", "")})
        elif typ in COMM_TYPES:
            n_comm += 1
            for a in A:
                for b in B:
                    if a != b:
                        comm[a].append((t, b, eid, ev.get("duration")))
                        comm[b].append((t, a, eid, ev.get("duration")))
                        for x, o in ((a, b), (b, a)):
                            ev_by_actor[x].append({"event_id": eid, "type": "COMMUNICATION", "time": t, "amount": None, "other": o, "dir": "", "ref": ev.get("source_ref", "")})
        elif typ in LOC_TYPES:
            n_loc += 1
            parts = sorted(set(A) | set(B))
            loc = ev.get("location_id") or (ev.get("related_entity_id") if etype.get(ev.get("related_entity_id")) == "LOCATION" else None)
            if not loc and ev.get("lat") is not None and ev.get("lon") is not None:
                loc = f"{round(float(ev['lat']), 3)},{round(float(ev['lon']), 3)}"
            if parts:
                coloc_events.append((t, loc, parts, eid, typ, ev.get("source_ref", "")))
                for x in parts:
                    others = [p for p in parts if p != x]
                    ev_by_actor[x].append({"event_id": eid, "type": typ, "time": t, "amount": None, "other": others[0] if others else "", "dir": "", "ref": ev.get("source_ref", "")})
    ref_time = _t(reference_time) or (max(times) if times else None)

    # -- graph projection over actors (behavioural network)
    P = nx.Graph()
    P.add_nodes_from(actors)
    inter = defaultdict(int)

    def link(a, b, w=1):
        if a != b and a in aset and b in aset:
            inter[tuple(sorted((a, b)))] += w

    for a, lst in comm.items():
        for (_, b, _, _) in lst:
            if a < b:
                link(a, b)
    for a, lst in money_out.items():
        for (_, _, _, b) in lst:
            link(a, b)
    for (_, _, parts, _, _, _) in coloc_events:
        for i in range(len(parts)):
            for j in range(i + 1, len(parts)):
                link(parts[i], parts[j])
    for r in relationships:
        s, t = r.get("source"), r.get("target")
        if s not in ents or t not in ents:
            continue
        rel = str(r.get("relation", "")).upper()
        if s in aset and t in aset or (s in group_of and t in group_of) or (s in aset and t in group_of and rel not in OWN_RELS) or (t in aset and s in group_of and rel not in OWN_RELS):
            for a in owners(s):
                for b in owners(t):
                    link(a, b)
    for (a, b), w in inter.items():
        P.add_edge(a, b, weight=math.log1p(w), n=w)

    n = len(actors)
    struct_ok = P.number_of_edges() >= 3
    pr, bt, core, comm_of = {}, {}, {}, {}
    if struct_ok:
        pr = nx.pagerank(P, weight="weight")
        bt = nx.betweenness_centrality(P, k=300 if n > 800 else None, normalized=True, seed=42)
        core = nx.core_number(P)
        try:
            comms = nx.community.louvain_communities(P, seed=42)
        except (nx.NetworkXException, ValueError):
            comms = []
        for ci, c in enumerate(sorted(comms, key=lambda c: sorted(c)[0])):
            for a in c:
                comm_of[a] = ci

    def bridging(a):
        if not comm_of:
            return 0
        others = {comm_of[b] for b in P[a] if comm_of.get(b) != comm_of.get(a)}
        return len(others) if P.degree(a) >= 2 else 0

    # -- per-actor features + evidence
    F, EV, DET, NOBS = {}, {}, {}, {}
    for a in actors:
        f = {k: 0.0 for k in FKEYS}
        ev, det = defaultdict(_Ev), {}
        deg = P.degree(a)
        f["degree"] = float(deg)
        if struct_ok:
            f["pagerank"] = pr.get(a, 0.0) * n
            f["betweenness"] = bt.get(a, 0.0)
            f["kcore"] = float(core.get(a, 0))
            f["bridging"] = float(bridging(a))
            nb = sorted(P[a], key=lambda b: (-P[a][b]["n"], b))
            det["degree"] = {"partners": [label_of(b) for b in nb[:5]]}
            for b in nb[:4]:
                ev["degree"].add({"kind": "link", "entity_id": b, "text": f"linked to {label_of(b)} ({P[a][b]['n']} recorded interactions)"})
            if f["bridging"]:
                names = sorted({label_of(b) for b in P[a] if comm_of.get(b) != comm_of.get(a)})[:4]
                det["bridging"] = {"n": int(f["bridging"]), "examples": names}
                for b in names:
                    ev["bridging"].add({"kind": "link", "text": f"connects to another group through {b}"})
        # money
        ins, outs = money_in.get(a, []), money_out.get(a, [])
        tot_in, tot_out = sum(x[1] for x in ins), sum(x[1] for x in outs)
        f["money_volume"] = tot_in + tot_out
        f["money_fan"] = float(max(len({x[3] for x in ins}), len({x[3] for x in outs})))
        det["money_volume"] = {"in": tot_in, "out": tot_out, "n_in": len(ins), "n_out": len(outs)}
        for x in sorted(ins + outs, key=lambda x: -x[1])[:3]:
            ev["money_volume"].add({"kind": "transaction", "ref": x[2], "text": f"{x[2]}: INR {x[1]:,.0f} {'to' if x in outs else 'from'} {label_of(x[3])}"})
        if f["money_fan"]:
            det["money_fan"] = {"fan_in": len({x[3] for x in ins}), "fan_out": len({x[3] for x in outs})}
        # rapid pass-through: how much of what came in left again within the window (to someone other than the sender)
        if tot_in > 0 and outs:
            outs_sorted = sorted(((x[0], x[1], x[2], x[3]) for x in outs if x[0]), key=lambda x: x[0])
            used = defaultdict(float)
            passed, matched_refs = 0.0, []
            for (ti, amt, tid, src) in sorted(((x[0], x[1], x[2], x[3]) for x in ins if x[0]), key=lambda x: x[0]):
                need = amt
                for (to, oamt, oid, dst) in outs_sorted:
                    if need <= 1e-9:
                        break
                    if to < ti or to - ti > timedelta(hours=PASS_WINDOW_H) or dst == src:
                        continue
                    avail = oamt - used[oid]
                    if avail <= 1e-9:
                        continue
                    take = min(avail, need)
                    used[oid] += take; need -= take; passed += take
                    if len(matched_refs) < MAX_EVIDENCE:
                        matched_refs.append((tid, oid))
            ratio = passed / tot_in if tot_in else 0.0
            f["pass_through"] = ratio * (1.0 - math.exp(-len(ins) / 2.0))
            if ratio > 0:
                det["pass_through"] = {"ratio": ratio, "passed": passed}
                for tid, oid in matched_refs[:4]:
                    ev["pass_through"].add({"kind": "transaction", "ref": oid, "text": f"{oid} left within {PASS_WINDOW_H}h of incoming {tid}"})
        seed_tx = [x for x in ins + outs if x[3] in seeds and x[3] != a]
        f["money_to_accused"] = float(len(seed_tx))
        if seed_tx:
            det["money_to_accused"] = {"n": len(seed_tx), "amount": sum(x[1] for x in seed_tx), "partners": sorted({label_of(x[3]) for x in seed_tx})[:4]}
            for x in sorted(seed_tx, key=lambda x: -x[1])[:4]:
                ev["money_to_accused"].add({"kind": "transaction", "ref": x[2], "entity_id": x[3], "text": f"{x[2]}: INR {x[1]:,.0f} with {label_of(x[3])}, who is linked to an accused"})
        # communication
        cl = comm.get(a, [])
        contacts = {x[1] for x in cl}
        f["comm_reach"] = float(len(contacts))
        seed_c = [x for x in cl if x[1] in seeds and x[1] != a]
        f["comm_with_accused"] = float(len(seed_c))
        ts = sorted(x[0] for x in cl if x[0])
        peak, j = 0, 0
        for i in range(len(ts)):
            while ts[i] - ts[j] > timedelta(hours=BURST_WINDOW_H):
                j += 1
            peak = max(peak, i - j + 1)
        f["comm_burst"] = float(peak)
        det["comm_reach"] = {"contacts": len(contacts), "events": len(cl)}
        if seed_c:
            byp = defaultdict(int)
            for x in seed_c:
                byp[x[1]] += 1
            det["comm_with_accused"] = {"n": len(seed_c), "partners": {label_of(k): v for k, v in sorted(byp.items())}}
            for x in seed_c[:MAX_EVIDENCE]:
                ev["comm_with_accused"].add({"kind": "communication", "ref": x[2], "entity_id": x[1], "text": f"{x[2]}: contact with {label_of(x[1])}, who is linked to an accused"})
        if peak >= 3:
            det["comm_burst"] = {"peak": peak, "window_h": BURST_WINDOW_H}
            ev["comm_burst"].add({"kind": "communication", "text": f"{peak} calls/messages within {BURST_WINDOW_H} hours"})
        if contacts:
            ev["comm_reach"].add({"kind": "communication", "text": f"{len(cl)} calls/messages with {len(contacts)} distinct contacts"})
        # alerts
        aw, aev = 0.0, []
        for al in alerts:
            if str(al.get("status", "")).lower() in ("false positive", "dismissed"):
                continue
            if a in owners(al.get("entity_id") or "") or al.get("entity_id") == a:
                sev = {"high": 1.0, "critical": 1.0, "review": 0.6, "medium": 0.6}.get(str(al.get("severity", "")).lower(), 0.3)
                aw += sev * float(al.get("confidence") or 0.5)
                aev.append(al)
        f["alerts"] = aw
        det["alerts"] = {"n": len(aev)}
        for al in aev[:4]:
            ev["alerts"].add({"kind": "alert", "ref": al.get("code"), "text": f"alert {al.get('code')}: {al.get('title')} ({al.get('severity')})"})
        # FIR corpus
        firs, cases = fir_of[a], cases_of[a]
        n_app = max(len(firs), len(cases))
        f["fir_appearances"] = float(n_app)
        det["fir_appearances"] = {"firs": sorted(firs), "cases": sorted(cases)}
        if len(firs) >= 1:
            ev["fir_appearances"].add({"kind": "fir", "text": "appears in " + ", ".join(sorted(firs)[:6])})
        shared = []
        for k in sorted(assets.get(a, ())):
            fset, cset = gfir[k]
            if max(len(fset), len(cset)) >= 2:
                mem = groups[k][0]
                shared.append({"type": k[0], "name": label_of(mem), "firs": sorted(fset) or sorted(cset)})
        f["cross_fir_identifiers"] = float(len(shared))
        det["cross_fir_identifiers"] = {"identifiers": shared}
        for s_ in shared[:4]:
            ev["cross_fir_identifiers"].add({"kind": "identifier", "text": f"{s_['type'].lower()} {s_['name']} appears in " + ", ".join(s_["firs"][:5])})
        F[a], EV[a], DET[a] = f, ev, det

    # -- co-location
    seeds_set = seeds
    meet_partners = defaultdict(lambda: defaultdict(int))
    meet_ev = defaultdict(list)
    seen_pairs = set()

    def co(x, y, tag, ref, t):
        key = (x, y, ref, tag)
        if key in seen_pairs:
            return
        seen_pairs.add(key)
        meet_partners[x][y] += 1
        if len(meet_ev[x]) < 60:
            meet_ev[x].append((y, ref, tag, t))

    by_loc = defaultdict(list)
    for (t, loc, parts, eid, typ, ref) in coloc_events:
        for x in parts:
            for y in parts:
                if x != y:
                    co(x, y, "meeting" if typ == "MEETING" else "same-event", eid, t)
        if loc and t:
            by_loc[loc].append((t, parts, eid))
    for loc, lst in by_loc.items():
        lst = sorted(lst, key=lambda x: x[0])[:2000]
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                if lst[j][0] - lst[i][0] > timedelta(hours=COPRESENCE_H):
                    break
                for x in lst[i][1]:
                    for y in lst[j][1]:
                        if x != y:
                            co(x, y, "co-present", f"{lst[i][2]}+{lst[j][2]}", lst[j][0])
    for a in actors:
        mp = meet_partners.get(a, {})
        F[a]["copresence"] = float(len(mp))
        F[a]["meet_accused"] = float(sum(v for y, v in mp.items() if y in seeds_set and y != a))
        DET[a]["copresence"] = {"partners": len(mp), "events": sum(mp.values())}
        for (y, ref, tag, t) in meet_ev.get(a, [])[:MAX_EVIDENCE]:
            EV[a]["copresence"].add({"kind": "meeting", "ref": ref, "entity_id": y, "text": f"{tag} with {label_of(y)} ({ref})"})
        DET[a]["meet_accused"] = {"n": int(F[a]["meet_accused"]), "partners": sorted({label_of(y) for y in mp if y in seeds_set and y != a})[:4]}
        for (y, ref, tag, t) in [x for x in meet_ev.get(a, []) if x[0] in seeds_set][:4]:
            EV[a]["meet_accused"].add({"kind": "meeting", "ref": ref, "entity_id": y, "text": f"{tag} with {label_of(y)}, who is linked to an accused ({ref})"})
        # recency
        if ref_time and last_seen.get(a):
            days = max(0.0, (ref_time - last_seen[a]).total_seconds() / 86400.0)
            F[a]["recency"] = math.exp(-days / 45.0)
            DET[a]["recency"] = {"days_since_last": round(days, 1), "last": last_seen[a].isoformat()}
            EV[a]["recency"].add({"kind": "time", "text": f"last recorded activity {last_seen[a].date().isoformat()} ({days:.0f} days before the newest record in the case)"})

    # -- observation counts and availability (for uncertainty and "what would change this")
    avail = {
        "structure": struct_ok,
        "money": n_fin > 0,
        "comm": n_comm > 0,
        "coloc": n_loc > 0,
        "fir": bool(fir_labels) or any(fir_of[a] or cases_of[a] for a in actors) or any(t == "CASE" for t in etype.values()),
        "alerts": bool(alerts),
        "recency": bool(times),
    }
    for a in actors:
        NOBS[a] = {
            "structure": P.degree(a),
            "money": len(money_in.get(a, [])) + len(money_out.get(a, [])),
            "comm": len(comm.get(a, [])),
            "coloc": int(DET[a]["copresence"]["events"]),
            "fir": max(len(fir_of[a]), len(cases_of[a])),
            "alerts": 5 if avail["alerts"] else 0,
            "recency": 1 if a in last_seen else 0,
        }
    unattributed = []
    for k, members in sorted(groups.items()):
        f, c = gfir[k]
        if not owners_g[k] and max(len(f), len(c)) >= 2:
            unattributed.append({"entity_id": members[0], "type": k[0], "name": label_of(members[0]), "firs": sorted(f) or sorted(c)})
    return {"actors": actors, "F": F, "EV": EV, "DET": DET, "NOBS": NOBS, "avail": avail, "role_class": role_class, "role_conflict": role_conflict,
            "seeds": sorted(seeds), "P": P, "name": name, "etype": etype, "ev_by_actor": ev_by_actor, "unattributed": unattributed, "ref_time": ref_time,
            "n_events": {"transactions": n_fin, "communications": n_comm, "location": n_loc}}


def behavioural_features(x):
    """Feature rows WITHOUT the role: what the leakage-free behavioural model sees."""
    return {a: dict(x["F"][a]) for a in x["actors"]}


# ----------------------------------------------------------------------------- scoring
def normalise(F, subjects, population=None):
    """Per-feature s in [0,1]: blend of the absolute saturating transform and the percentile within the case's candidates
    (``population``, default = subjects). Returns {subject: {feature: s}}."""
    pop = list(population if population is not None else subjects)
    alpha = 0.5 * min(1.0, len(pop) / 20.0)
    S = {a: {} for a in subjects}
    for key, _fam, _lab, _w, absf, use_pct in FEATURES:
        vals = [F[a][key] for a in pop]
        for a in subjects:
            raw = F[a][key]
            ab = absf(raw) if raw > 0 else 0.0
            S[a][key] = ((1 - alpha) * ab + alpha * _pct(vals, raw)) if (use_pct and raw > 0) else ab
    return S


def _evidence_logit(S, a, role_cls):
    """Return (logit, per-feature contribution dict after family saturation, role contribution)."""
    raw_c = {k: FMETA[k]["weight"] * S[a][k] for k in FKEYS}
    fam_sum = defaultdict(float)
    for k, c in raw_c.items():
        fam_sum[FMETA[k]["family"]] += c
    other = sum(v for f_, v in fam_sum.items() if f_ != "recency")
    contrib = {}
    for k, c in raw_c.items():
        fam = FMETA[k]["family"]
        cap = FAMILY_CAP[fam]
        tot = fam_sum[fam]
        scaled = c * (cap * math.tanh(tot / cap) / tot) if tot > 1e-12 else 0.0
        if fam == "recency":  # recency only amplifies people who already show other evidence
            scaled *= min(1.0, max(0.0, other) / 1.5)
        contrib[k] = scaled
    role_c = ROLE_PRIOR.get(role_cls, 0.0)
    return BIAS + role_c + sum(contrib.values()), contrib, role_c


def _sd(x, a, role_cls, S):
    var = 0.0
    for k in FKEYS:
        fam = FMETA[k]["family"]
        w = FMETA[k]["weight"]
        n = x["NOBS"][a][fam] if x["avail"][fam] else 0
        var += (w * 0.5 / math.sqrt(1.0 + n)) ** 2
    if not role_cls:
        var += ROLE_UNKNOWN_SD ** 2
    return math.sqrt(var)


def _fmt_money(v):
    return f"INR {v:,.0f}"


def _sentence(key, raw, det, name):
    d = det.get(key, {}) or {}
    if key == "pagerank":
        return f"{name} sits at the centre of the recorded network (influence {raw:.1f}x the average person)."
    if key == "betweenness":
        return f"{name} lies on many of the shortest routes between other people, which is what go-betweens look like."
    if key == "degree":
        return f"{name} has {int(raw)} distinct associates in the recorded data" + (f" (for example {', '.join(d.get('partners', [])[:3])})." if d.get("partners") else ".")
    if key == "kcore":
        return f"{name} belongs to a tightly knit group in which everyone is linked to at least {int(raw)} others."
    if key == "bridging":
        return f"{name} links {int(raw)} other group(s) that are otherwise separate" + (f" (via {', '.join(d.get('examples', [])[:3])})." if d.get("examples") else ".")
    if key == "money_volume":
        return f"{_fmt_money(d.get('in', 0) + d.get('out', 0))} moved through {name}'s accounts ({d.get('n_in', 0)} in, {d.get('n_out', 0)} out)."
    if key == "money_fan":
        return f"Payments fan out to {d.get('fan_out', 0)} and in from {d.get('fan_in', 0)} different parties."
    if key == "pass_through":
        return f"About {d.get('ratio', 0) * 100:.0f}% of the money received left again within {PASS_WINDOW_H} hours ({_fmt_money(d.get('passed', 0))}), a pass-through pattern."
    if key == "money_to_accused":
        return f"{d.get('n', 0)} transaction(s) worth {_fmt_money(d.get('amount', 0))} with people linked to an accused ({', '.join(d.get('partners', [])[:3])})."
    if key == "comm_with_accused":
        return f"{d.get('n', 0)} call(s)/message(s) with people linked to an accused ({', '.join(list(d.get('partners', {}))[:3])})."
    if key == "comm_burst":
        return f"{d.get('peak', int(raw))} calls/messages inside {BURST_WINDOW_H} hours, a burst of activity."
    if key == "comm_reach":
        return f"{d.get('events', 0)} calls/messages with {d.get('contacts', int(raw))} distinct contacts."
    if key == "meet_accused":
        return f"{d.get('n', int(raw))} recorded meeting/co-presence event(s) with people linked to an accused ({', '.join(d.get('partners', [])[:3])})."
    if key == "copresence":
        return f"Recorded together with {d.get('partners', int(raw))} different people at meetings or the same place and time."
    if key == "fir_appearances":
        firs = d.get("firs") or d.get("cases") or []
        return f"Appears in {int(raw)} separate FIRs/cases" + (f" ({', '.join(firs[:4])})." if firs else ".")
    if key == "cross_fir_identifiers":
        ids = d.get("identifiers", [])
        return f"{len(ids)} identifier(s) tied to {name} recur across different FIRs (" + "; ".join(f"{i['type'].lower()} {i['name']} in {', '.join(i['firs'][:3])}" for i in ids[:2]) + ")."
    if key == "alerts":
        return f"{d.get('n', 0)} anomaly alert(s) raised on {name} or their accounts, phones or vehicles."
    if key == "recency":
        return f"Activity was recorded recently ({d.get('days_since_last', 0)} days before the newest record)."
    return FMETA[key]["label"]


MISSING_TEXT = {
    "money": "No bank or payment records could be attributed to this person; financial data could raise or lower the priority.",
    "comm": "No call/message records could be attributed to this person; call-record data could raise or lower the priority.",
    "coloc": "No meeting or location records involve this person; sightings or co-presence data could change the picture.",
    "structure": "The recorded network is too sparse for network measures to be meaningful; more linked records would help.",
    "fir": "Only one FIR/case mentions this person; a second FIR sharing a phone, vehicle or account would matter.",
    "alerts": "No anomaly alerts have been generated for this case yet.",
}


def score_case(entities, relationships, events, alerts=None, fir_labels=None, decisions=None, include_victims=False, reference_time=None, extracted=None, disabled_families=()):
    """Score every actor. ``decisions``: {entity_id: {"decision": ..., ...}} from human verification (latest per entity)."""
    decisions = decisions or {}
    confirmed = {i for i, d in decisions.items() if d.get("decision") == "confirmed_person_of_interest"}
    dismissed = {i for i, d in decisions.items() if d.get("decision") == "dismissed"}
    x = extracted or extract_features(entities, relationships, events, alerts, fir_labels, seeds_extra=confirmed, seeds_exclude=dismissed, reference_time=reference_time)
    actors = x["actors"]
    excluded = [a for a in actors if x["role_class"][a] in NEGATIVE_ROLES]
    cand = actors if include_victims else [a for a in actors if a not in set(excluded)]
    pop = [a for a in actors if a not in set(excluded)] or actors  # normalise against the plausible-subject population
    S = normalise(x["F"], actors, pop)
    for a in actors:
        for k in FKEYS:
            if FMETA[k]["family"] in disabled_families:
                S[a][k] = 0.0
    results = {}
    for a in actors:
        role_cls = x["role_class"][a]
        logit, contrib, role_c = _evidence_logit(S, a, role_cls)
        sd = _sd(x, a, role_cls, S)
        score = sigmoid(logit) * 100
        dec = decisions.get(a)
        adj = -DISMISS_PENALTY if a in dismissed else 0.0
        eff_score = sigmoid(logit + adj) * 100
        lo, hi = sigmoid(logit + adj - 1.28 * sd) * 100, sigmoid(logit + adj + 1.28 * sd) * 100
        nm = x["name"][a]
        factors = []
        for k in FKEYS:
            c = contrib[k]
            if c <= 0.02 and x["F"][a][k] <= 0:
                continue
            drop = sigmoid(logit + adj) * 100 - sigmoid(logit + adj - c) * 100
            factors.append({"key": k, "family": FMETA[k]["family"], "family_label": FAMILY_LABEL[FMETA[k]["family"]], "label": FMETA[k]["label"],
                            "raw": round(x["F"][a][k], 4), "normalised": round(S[a][k], 3), "contribution": round(c, 3), "points": round(drop, 1),
                            "sentence": _sentence(k, x["F"][a][k], x["DET"][a], nm), "evidence": list(x["EV"][a].get(k, []))})
        factors.sort(key=lambda f: (-f["contribution"], f["key"]))
        role_factor = {"key": "role", "family": "role", "family_label": FAMILY_LABEL["role"], "label": ROLE_LABEL[role_cls], "raw": role_cls, "normalised": None,
                       "contribution": round(role_c, 3), "points": round(sigmoid(logit + adj) * 100 - sigmoid(logit + adj - role_c) * 100, 1),
                       "sentence": {"accused": f"{nm} is recorded as an accused in an FIR.", "suspect": f"{nm} is recorded as a suspect in an FIR.",
                                    "": f"{nm}'s role in the FIR is not recorded, so no role evidence is applied.",
                                    "victim": f"{nm} is recorded as a victim, so the priority is strongly reduced.",
                                    "complainant": f"{nm} is the complainant/informant, so the priority is strongly reduced.",
                                    "witness": f"{nm} is recorded as a witness, so the priority is reduced.",
                                    "complainant_relative": f"{nm} is a relative of the complainant, so the priority is reduced."}[role_cls],
                       "evidence": []}
        if x["role_conflict"][a]:
            role_factor["sentence"] += " The FIRs also record this person with a victim/complainant role: check for a role conflict."
        allf = ([role_factor] if role_c != 0 else []) + factors
        allf.sort(key=lambda f: -abs(f["contribution"]))
        positive = [f for f in allf if f["contribution"] > 0.05]
        reasons = [f["sentence"] for f in positive[:3]]
        # what would change the ranking
        missing = []
        cur = sigmoid(logit + adj) * 100
        if not role_cls:
            up = sigmoid(logit + adj + ROLE_PRIOR["accused"]) * 100 - cur
            dn = cur - sigmoid(logit + adj + ROLE_PRIOR["witness"]) * 100
            missing.append({"key": "role", "text": f"The FIR role is not recorded. If this person is recorded as an accused the priority would rise by about {up:.0f} points; "
                                                    f"as a witness it would fall by about {dn:.0f}.", "potential_uplift": round(up, 1)})
        for fam, txt in MISSING_TEXT.items():
            if fam == "fir" and (x["NOBS"][a]["fir"] >= 2):
                continue
            if x["NOBS"][a][fam] == 0 or not x["avail"][fam]:
                w = sum(FMETA[k]["weight"] for k in FKEYS if FMETA[k]["family"] == fam)
                cap = FAMILY_CAP[fam]
                up = sigmoid(logit + adj + 0.5 * min(w, cap)) * 100 - cur
                missing.append({"key": fam, "text": txt, "potential_uplift": round(up, 1)})
        if x["role_conflict"][a]:
            missing.insert(0, {"key": "role_conflict", "text": "The person is recorded both as accused/suspect and as victim/complainant; confirm which is right.", "potential_uplift": 0.0})
        tier = "High" if eff_score >= TIER_HIGH else ("Medium" if eff_score >= TIER_MEDIUM else "Low")
        width = hi - lo
        conf = "Narrow" if sd < 0.9 else ("Moderate" if sd < 1.5 else "Wide")
        results[a] = {
            "entity_id": a, "name": nm, "type": x["etype"][a], "role": {"class": role_cls, "label": ROLE_LABEL[role_cls], "conflict": x["role_conflict"][a]},
            "model_score": round(score, 1), "score": int(round(eff_score)), "logit": round(logit + adj, 3), "tier": tier,
            "band": {"low": int(round(lo)), "high": int(round(hi)), "width": conf, "level": 0.8, "sd_logit": round(sd, 2)},
            "decision": dec, "status": ("confirmed" if a in confirmed else "dismissed" if a in dismissed else "needs_more_info" if (dec or {}).get("decision") == "needs_more_info" else "open"),
            "factors": allf, "reasons": reasons, "missing": missing, "coverage": {k: int(v) for k, v in x["NOBS"][a].items()},
            "features": {k: round(x["F"][a][k], 4) for k in FKEYS},
            "excluded_reason": ("victim/complainant/witness role" if a in set(excluded) else ""),
        }
    order = sorted(cand, key=lambda a: (0 if results[a]["status"] == "confirmed" else (2 if results[a]["status"] == "dismissed" else 1), -results[a]["logit"], a))
    for i, a in enumerate(order, 1):
        results[a]["rank"] = i
        results[a]["pinned"] = results[a]["status"] == "confirmed"
    for a in results:
        results[a].setdefault("rank", None)
        results[a]["summary"] = summarise(results[a], len(order), x)
    return {"results": results, "order": order, "excluded": [a for a in excluded if a not in set(order)], "extracted": x,
            "meta": {"candidates": len(order), "victims_excluded": 0 if include_victims else len(excluded), "availability": x["avail"], "seeds": x["seeds"],
                     "model_version": MODEL_VERSION, "unattributed_identifiers": x["unattributed"], "events": x["n_events"]}}


def summarise(r, total, x=None):
    """One plain-English paragraph. Uses lead-priority wording only."""
    nm = r["name"]
    rank = f"ranked #{r['rank']} of {total}" if r.get("rank") else "not ranked (role suggests they are not a subject)"
    parts = [f"{nm} is {rank} as a lead, with a priority score of {r['score']}/100 ({r['tier']}; plausible range {r['band']['low']}-{r['band']['high']} given the data available)."]
    if r["reasons"]:
        parts.append("Main reasons: " + " ".join(s if s.endswith(".") else s + "." for s in r["reasons"]))
    else:
        parts.append("No strong evidence signal was found in the recorded data.")
    neg = [f for f in r["factors"] if f["contribution"] < -0.05]
    if neg:
        parts.append(neg[0]["sentence"])
    if r["missing"]:
        parts.append("What could change this: " + r["missing"][0]["text"])
    st = r["status"]
    if st == "confirmed":
        parts.append("An officer has verified this person as a person of interest.")
    elif st == "dismissed":
        parts.append("An officer has dismissed this lead; it is shown lower in the list.")
    elif st == "needs_more_info":
        parts.append("An officer has asked for more information before deciding.")
    parts.append("This is a lead priority to help decide where to look first. It is not a finding about the person and must be verified against the source records.")
    return " ".join(parts)


# ----------------------------------------------------------------------------- profile helpers
def profile_extras(x, entity_id, limit_events=12):
    """Linked entities, timeline highlights and cross-FIR appearances for one actor (from an extract_features result)."""
    a = entity_id
    P, name = x["P"], x["name"]
    linked = []
    if a in P:
        for b in sorted(P[a], key=lambda b: (-P[a][b]["n"], b))[:15]:
            linked.append({"entity_id": b, "name": name[b], "type": x["etype"][b], "interactions": int(P[a][b]["n"]), "accused_linked": b in set(x["seeds"])})
    evs = x["ev_by_actor"].get(a, [])
    seeds = set(x["seeds"])
    evs = sorted(evs, key=lambda e: (0 if e["other"] in seeds else 1, -(e["amount"] or 0), -(e["time"].timestamp() if e["time"] else 0), e["event_id"]))
    seen, tl = set(), []
    for e in evs:
        if e["event_id"] in seen:
            continue
        seen.add(e["event_id"])
        tl.append({"event_id": e["event_id"], "type": e["type"], "time": e["time"].isoformat() if e["time"] else None, "amount": e["amount"],
                   "with": name.get(e["other"], e["other"]), "with_id": e["other"], "with_accused_linked": e["other"] in seeds, "source_ref": e["ref"]})
        if len(tl) >= limit_events:
            break
    tl.sort(key=lambda e: e["time"] or "")
    return {"linked": linked, "timeline": tl}


MODEL_CARD = {
    "version": MODEL_VERSION,
    "purpose": "Rank persons and organisations in a case by lead priority so investigators know where to look first. Decision support only.",
    "families": [{"family": fam, "label": FAMILY_LABEL[fam], "cap": FAMILY_CAP.get(fam)} for fam in ("structure", "money", "comm", "coloc", "fir", "alerts", "recency")],
    "features": [{"key": k, "family": FMETA[k]["family"], "label": FMETA[k]["label"], "weight": FMETA[k]["weight"]} for k in FKEYS],
    "role_prior": [{"role": ROLE_LABEL[r], "log_odds": v} for r, v in ROLE_PRIOR.items()],
    "formula": "score = 100 * sigmoid(bias + role_prior + sum_family cap * tanh(sum_f weight_f * s_f / cap)); bias = %.1f; s_f blends an absolute saturating "
               "transform with the person's percentile among the case's candidates." % BIAS,
    "tiers": {"High": f">= {TIER_HIGH}", "Medium": f">= {TIER_MEDIUM}", "Low": f"< {TIER_MEDIUM}"},
    "uncertainty": "80% band = sigmoid(logit +/- 1.28 sd); Narrow/Moderate/Wide = sd < 0.9 / < 1.5 / higher; sd grows when a signal family has few or no records for the person, or when the FIR role is unknown.",
    "feedback": f"Dismissed leads lose {DISMISS_PENALTY} log-odds and are listed last; confirmed persons of interest are pinned first and their contacts count as accused-linked; "
                "'needs more info' changes nothing but is flagged.",
}
