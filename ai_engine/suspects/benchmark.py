"""Synthetic benchmark for the person-of-interest scorer.

A small, fully deterministic world with *planted* structure used ONLY as evaluation ground truth (never as a feature):
a laundering/fraud ring (kingpins, lieutenants, mules) hidden among ordinary people, decoy hubs that look busy but are
innocent (a shop, a telecom reseller), and victims/complainants who send money to the ring.

It is written by the same team as the scorer, so results say "the model behaves as designed on plausible structure",
not "the model is accurate on real crime". The knobs (noise, decoys, role coverage) exist to show how quickly the
advantage shrinks as the world gets harder.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

T0 = datetime(2026, 8, 1, 8, 0)


def make_world(seed=0, n_people=60, noise=1.0, decoys=2, role_coverage=0.25, role_error=0.05, alert_noise=0.3, visibility=0.7, ring_signal=0.6, fir_noise=0.2):
    """Return dict(entities, relationships, events, alerts, truth:set(ids), victims:set, kingpins, mules)."""
    rng = random.Random(seed)
    people = [f"P{i:03d}" for i in range(n_people)]
    rng.shuffle(people)
    king, lieut, mules = people[:2], people[2:5], people[5:9]
    decoy = people[9:9 + decoys]
    victims = people[9 + decoys:9 + decoys + 6]
    rest = [p for p in people if p not in set(king + lieut + mules + decoy + victims)]
    ring = king + lieut + mules
    truth = set(ring)
    ents, rels, evs, alerts = [], [], [], []
    firs = [f"FIR {100 + i}/2026" for i in range(10)]
    # ring members appear in several FIRs only some of the time (0.6); innocent people appear in several with prob `fir_noise`
    # (witnesses, neighbours, repeat complainants), so multi-FIR presence is a noisy signal, not a giveaway.
    ring_firs = {p: sorted(rng.sample(firs, 3 if p in king + lieut else 2)) for p in ring if rng.random() < 0.6}
    for p in people:
        if p not in ring_firs and rng.random() < fir_noise:
            ring_firs[p] = sorted(rng.sample(firs, 2))
    acct, phone = {}, {}
    for p in people:
        acct[p], phone[p] = "A" + p[1:], "PH" + p[1:]
        docs = ring_firs.get(p) or [rng.choice(firs)]
        ents.append({"id": p, "type": "PERSON", "name": f"Person {p}", "docs": docs, "cases": ["CASE-B"]})
        ents.append({"id": acct[p], "type": "ACCOUNT", "name": f"ACCT-{p[1:]}", "key": f"ACCT-{p[1:]}", "docs": [docs[0]]})
        ents.append({"id": phone[p], "type": "PHONE", "name": f"98{int(p[1:]):08d}", "key": f"98{int(p[1:]):08d}",
                     "docs": list(ring_firs[p]) if p in ring_firs and rng.random() < 0.5 else [docs[0]]})
        rels.append({"source": p, "target": acct[p], "relation": "OWNS"})
        rels.append({"source": p, "target": phone[p], "relation": "REGISTERED_TO"})
    ents += [{"id": f, "type": "CASE", "name": f} for f in firs]
    # roles: partial and noisy, like real FIR coverage
    for e in ents:
        p = e["id"]
        if e["type"] != "PERSON":
            continue
        if p in victims:
            e["role"] = "COMPLAINANT" if rng.random() < 0.8 else ""
        elif p in ring and rng.random() < role_coverage:
            e["role"] = "ACCUSED"
        elif p not in ring and rng.random() < role_error * 0.4:
            e["role"] = "ACCUSED"  # a mistaken label
    eid = [0]

    def ev(typ, a, b, t, amount=None, dur=None, loc=None):
        eid[0] += 1
        evs.append({"event_id": f"E{eid[0]:05d}", "event_type": typ, "entity_id": a, "related_entity_id": b, "time": (T0 + t).isoformat(),
                    "amount": amount, "duration": dur, "source_ref": f"SRC-{eid[0]}", **({"location_id": loc} if loc else {})})

    hours = lambda: timedelta(hours=rng.uniform(0, 24 * 28))
    everyone = king + lieut + mules + decoy + victims + rest
    # background noise: calls, payments, meetings among everyone
    for _ in range(int(500 * noise)):
        a, b = rng.sample(everyone, 2)
        ev("COMMUNICATION", phone[a], phone[b], hours(), dur=rng.randint(10, 600))
    for _ in range(int(220 * noise)):
        a, b = rng.sample(everyone, 2)
        ev("TRANSACTION", acct[a], acct[b], hours(), amount=round(rng.uniform(500, 30000), 2))
    for _ in range(int(60 * noise)):
        a, b = rng.sample(everyone, 2)
        ev("MEETING", a, b, hours(), loc=f"L{rng.randint(1, 20)}")
    # decoy hubs: busy, high-volume, innocent. Even-numbered decoys are also fast pass-through businesses (e.g. a payment
    # aggregator), the hardest negative for a pass-through signal.
    outsiders = victims + rest
    for di, d in enumerate(decoy):
        for _ in range(int(80 * noise) + 20):
            o = rng.choice(outsiders)
            ev("COMMUNICATION", phone[o], phone[d], hours(), dur=rng.randint(10, 300))
        for _ in range(int(40 * noise) + 10):
            o = rng.choice(outsiders)
            ev("TRANSACTION", acct[o], acct[d], hours(), amount=round(rng.uniform(2000, 60000), 2))
        if di % 2 == 0:
            for _ in range(25):
                t, o1, o2 = hours(), rng.choice(outsiders), rng.choice(outsiders)
                amt = round(rng.uniform(3000, 40000), 2)
                ev("TRANSACTION", acct[o1], acct[d], t, amount=amt)
                ev("TRANSACTION", acct[d], acct[o2], t + timedelta(hours=rng.uniform(0.5, 10)), amount=round(amt * 0.98, 2))
    # the ring. Only a fraction of ring activity is observed (`visibility`) and its strength is scaled by `ring_signal`;
    # amounts are drawn from the SAME range as ordinary traffic so that volume alone does not give the ring away.
    keep = lambda: rng.random() < visibility
    cnt = lambda n: max(1, int(round(n * ring_signal)))
    ring_ev = lambda *a, **k: ev(*a, **k) if keep() else None
    for v in victims:  # victims pay mules
        for m in rng.sample(mules, 2):
            ring_ev("TRANSACTION", acct[v], acct[m], hours(), amount=round(rng.uniform(2000, 30000), 2))
    for m in mules:  # mules pass money on within hours
        t = hours()
        for _ in range(cnt(5)):
            t = t + timedelta(hours=rng.uniform(1, 60))
            amt = round(rng.uniform(2000, 30000), 2)
            src = rng.choice(victims + rest)
            ring_ev("TRANSACTION", acct[src], acct[m], t, amount=amt)
            ring_ev("TRANSACTION", acct[m], acct[rng.choice(lieut)], t + timedelta(hours=rng.uniform(1, 20)), amount=round(amt * 0.93, 2))
    for l in lieut:
        for k in king:
            for _ in range(cnt(3)):
                ring_ev("TRANSACTION", acct[l], acct[k], hours(), amount=round(rng.uniform(2000, 30000), 2))
    for a in ring:
        for b in ring:
            if a < b and (a in king + lieut or b in king + lieut):
                for _ in range(cnt(rng.randint(3, 8))):
                    ring_ev("COMMUNICATION", phone[a], phone[b], hours(), dur=rng.randint(20, 900))
                if rng.random() < 0.5 * ring_signal:
                    t0 = hours()
                    for j in range(4):
                        ring_ev("COMMUNICATION", phone[a], phone[b], t0 + timedelta(minutes=20 * j), dur=60)
    for _ in range(cnt(14)):
        a, b = rng.sample(ring, 2)
        ring_ev("MEETING", a, b, hours(), loc=f"L{rng.randint(1, 4)}")
    # alerts: sensitive but noisy detector
    for p in ring:
        if rng.random() < 0.6:
            alerts.append({"code": f"AL-{p}", "severity": rng.choice(["High", "Review"]), "entity_id": p, "confidence": rng.uniform(0.6, 0.9), "title": "Anomalous activity", "status": "New"})
    for p in rest + decoy:
        if rng.random() < alert_noise * 0.25:
            alerts.append({"code": f"AL-{p}", "severity": "Review", "entity_id": p, "confidence": rng.uniform(0.5, 0.8), "title": "Anomalous activity", "status": "New"})
    return {"entities": ents, "relationships": rels, "events": evs, "alerts": alerts, "truth": truth, "victims": set(victims), "kingpins": set(king), "mules": set(mules),
            "decoys": set(decoy)}
