"""Pure analysis functions over already-imported posts and accounts (no database, no network).

Input ``posts``: dicts with keys uid, account (``platform:handle``), platform, handle, ts (naive UTC datetime or None), text, mentions, reply_to,
repost_of, hashtags, urls (normalised strings), lang. Input ``accounts``: dicts with key, platform, handle, display_name, bio, ids (list of
(kind, value)), urls.

All results are descriptive. They point at patterns worth a human look; they never say who someone is or what they meant.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from itertools import combinations

from ai_engine.social.timeutil import to_local, zone_label

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
COMMON_DOMAINS = {"twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com", "youtu.be", "google.com", "wikipedia.org", "whatsapp.com",
                  "linkedin.com", "reddit.com", "t.me", "wa.me", "bit.ly", "tinyurl.com", "t.co", "goo.gl"}


def _hour_range(h: int) -> str:
    return f"{h:02d}:00-{(h + 1) % 24:02d}:00"


# ---------------------------------------------------------------------------------------------------------------- activity
def _activity(posts: list[dict], tz: str) -> dict:
    dated = [p for p in posts if p.get("ts")]
    heat = [[0] * 24 for _ in range(7)]
    daily: Counter = Counter()
    hours: Counter = Counter()
    for p in dated:
        loc = to_local(p["ts"], tz)
        heat[loc.weekday()][loc.hour] += 1
        daily[loc.date().isoformat()] += 1
        hours[loc.hour] += 1
    out = {"posts": len(posts), "dated": len(dated), "undated": len(posts) - len(dated), "heatmap": heat,
           "daily": [{"date": d, "count": c} for d, c in sorted(daily.items())], "hours": [hours.get(h, 0) for h in range(24)], "headline": ""}
    if dated:
        # busiest three-hour window (wraps around midnight)
        best = max(range(24), key=lambda s: sum(hours.get((s + i) % 24, 0) for i in range(3)))
        share = sum(hours.get((best + i) % 24, 0) for i in range(3)) / len(dated)
        wd = Counter(loc for loc in (to_local(p["ts"], tz).weekday() for p in dated))
        night = sum(hours.get(h, 0) for h in range(0, 5)) / len(dated)
        top_day = daily.most_common(1)[0]
        out.update(busiest_window=f"{best:02d}:00-{(best + 3) % 24:02d}:00", busiest_share=round(share, 3), busiest_weekday=WEEKDAYS[wd.most_common(1)[0][0]],
                   night_share=round(night, 3), first=dated and min(p["ts"] for p in dated).isoformat(), last=max(p["ts"] for p in dated).isoformat(),
                   busiest_day={"date": top_day[0], "count": top_day[1]})
        out["headline"] = (f"{round(share * 100)}% of dated posts fall between {out['busiest_window']} {zone_label(tz)}; the most active weekday is "
                           f"{out['busiest_weekday']}." + (f" {round(night * 100)}% are posted between midnight and 05:00." if night >= 0.2 else ""))
    return out


def timeline(posts: list[dict], tz: str = "IST", max_accounts: int = 25) -> dict:
    by = defaultdict(list)
    for p in posts:
        by[p["account"]].append(p)
    accs = []
    for k, lst in sorted(by.items(), key=lambda kv: -len(kv[1]))[:max_accounts]:
        a = _activity(lst, tz)
        a["account"] = k
        accs.append(a)
    overall = _activity(posts, tz)
    return {"timezone": zone_label(tz), "overall": overall, "accounts": accs,
            "explain": "Each cell counts posts by weekday and hour in the chosen time zone. Timestamps are those recorded by the platform export; if the "
                       "export had no time zone, the import's assumed zone was used. Patterns of posting hours can suggest a time zone or a shared "
                       "schedule but are not proof of anything."}


# ---------------------------------------------------------------------------------------------------------------- network
def network(posts: list[dict], top: int = 10) -> dict:
    edges: dict[tuple[str, str, str], int] = Counter()
    posts_by = Counter(p["account"] for p in posts)
    for p in posts:
        src, plat = p["account"], p["platform"]
        for m in set(p.get("mentions") or []):
            if m != p["handle"]:
                edges[(src, f"{plat}:{m}", "mention")] += 1
        if p.get("reply_to") and p["reply_to"] != p["handle"]:
            edges[(src, f"{plat}:{p['reply_to']}", "reply")] += 1
        if p.get("repost_of") and p["repost_of"] != p["handle"]:
            edges[(src, f"{plat}:{p['repost_of']}", "repost")] += 1
    import networkx as nx
    G = nx.DiGraph()
    for (u, v, kind), n in edges.items():
        if G.has_edge(u, v):
            G[u][v]["kinds"][kind] = G[u][v]["kinds"].get(kind, 0) + n
            G[u][v]["weight"] += n
        else:
            G.add_edge(u, v, weight=n, kinds={kind: n})
    known = set(posts_by)
    nodes = []
    und = G.to_undirected()
    comm_of: dict[str, int] = {}
    if und.number_of_edges():
        try:
            for i, c in enumerate(sorted(nx.community.greedy_modularity_communities(und, weight="weight"), key=len, reverse=True)):
                for n in c:
                    comm_of[n] = i
        except Exception:  # noqa: BLE001
            pass
    for n in G.nodes:
        nodes.append({"id": n, "posts": posts_by.get(n, 0), "in_import": n in known, "out_degree": G.out_degree(n), "in_degree": G.in_degree(n),
                      "degree": und.degree(n), "weighted_degree": sum(d["weight"] for _, _, d in und.edges(n, data=True)), "community": comm_of.get(n, 0),
                      "kind": "account" if n in known else "referenced"})
    nodes.sort(key=lambda x: (-x["weighted_degree"], x["id"]))
    E = [{"source": u, "target": v, "weight": d["weight"], "kinds": d["kinds"]} for u, v, d in G.edges(data=True)]
    n_comm = len({v for v in comm_of.values()}) if comm_of else 0
    head = "No mentions, replies or reposts were found between accounts yet."
    if nodes:
        t = nodes[0]
        head = (f"{len(nodes)} accounts are connected by {len(E)} interactions in {n_comm} group(s). The most-connected account is {t['id']} "
                f"({t['degree']} links, {t['weighted_degree']} interactions).")
    return {"nodes": nodes[:300], "edges": sorted(E, key=lambda e: -e["weight"])[:600], "top": nodes[:top], "communities": n_comm, "headline": head,
            "explain": "Accounts are dots; a line means one account mentioned, replied to or reposted another. 'Referenced' accounts were named in posts but "
                       "were not part of your imports. Being well-connected can mean a hub, a public figure, a news account or a bot: it is a place to start "
                       "reading, not a conclusion."}


# ---------------------------------------------------------------------------------------------------------------- coordination
_STRIP = re.compile(r"https?://\S+|www\.\S+|@\w+|#\w+|\brt\b\s*:?|[^\w\sऀ-ॿ]", re.U | re.I)


def norm_text(t: str) -> list[str]:
    return [w for w in _STRIP.sub(" ", (t or "").lower()).split() if w]


def shingles(tokens: list[str], k: int = 3) -> set:
    if len(tokens) < k:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i:i + k]) for i in range(len(tokens) - k + 1)}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def coordinated(posts: list[dict], window_minutes: int = 60, similarity: float = 0.7, min_tokens: int = 6, burst_accounts: int = 3, max_posts: int = 20000) -> dict:
    W = timedelta(minutes=max(1, int(window_minutes)))
    dated = sorted((p for p in posts if p.get("ts")), key=lambda p: p["ts"])[:max_posts]
    prep = []
    for p in dated:
        toks = norm_text(p.get("text", ""))
        prep.append((p, toks, shingles(toks) if len(toks) >= min_tokens else set()))
    parent = list(range(len(prep)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    sims: dict[tuple[int, int], float] = {}
    for i in range(len(prep)):
        pi, _ti, si = prep[i]
        if not si:
            continue
        j = i + 1
        while j < len(prep) and prep[j][0]["ts"] - pi["ts"] <= W and j - i < 3000:
            pj, _tj, sj = prep[j]
            if sj and pj["account"] != pi["account"]:
                s = jaccard(si, sj)
                if s >= similarity:
                    sims[(i, j)] = s
                    parent[find(i)] = find(j)
            j += 1
    groups: dict[int, list[int]] = defaultdict(list)
    for (i, j) in sims:
        groups[find(i)] += [i, j]
    clusters = []
    for _root, idx in groups.items():
        members = sorted(set(idx))
        ps = [prep[i][0] for i in members]
        accounts = sorted({p["account"] for p in ps})
        if len(accounts) < 2:
            continue
        span = (ps[-1]["ts"] - ps[0]["ts"]).total_seconds() / 60
        mins = min(v for (i, j), v in sims.items() if i in members and j in members)
        strength = "high" if (len(accounts) >= 4 and span <= 15) or (mins >= 0.9 and len(accounts) >= 3) else ("medium" if len(accounts) >= 3 or mins >= 0.85 else "low")
        clusters.append({"kind": "near_duplicate_text", "strength": strength, "accounts": accounts, "posts": len(ps), "span_minutes": round(span, 1),
                         "min_similarity": round(mins, 2), "first": ps[0]["ts"].isoformat(), "last": ps[-1]["ts"].isoformat(),
                         "sample": ps[0]["text"][:240], "post_uids": [p["uid"] for p in ps][:40],
                         "reason": f"{len(accounts)} different accounts posted near-identical text ({round(mins * 100)}%+ similar) within {round(span)} minutes."})
    bursts = []
    for kind, keyf in (("link_burst", lambda p: p.get("urls") or []), ("hashtag_burst", lambda p: p.get("hashtags") or [])):
        by: dict[str, list[dict]] = defaultdict(list)
        for p in dated:
            for k in set(keyf(p)):
                if kind == "link_burst" and k.split("/")[0] in COMMON_DOMAINS and "/" not in k:
                    continue
                by[k].append(p)
        for k, lst in by.items():
            best: list[dict] = []
            lo = 0
            for hi in range(len(lst)):
                while lst[hi]["ts"] - lst[lo]["ts"] > W:
                    lo += 1
                win = lst[lo:hi + 1]
                if len({p["account"] for p in win}) > len({p["account"] for p in best}):
                    best = win
            accs = sorted({p["account"] for p in best})
            if len(accs) >= burst_accounts:
                span = (best[-1]["ts"] - best[0]["ts"]).total_seconds() / 60
                bursts.append({"kind": kind, "key": k, "strength": "high" if len(accs) >= burst_accounts + 2 and span <= 15 else ("medium" if span <= 30 else "low"),
                               "accounts": accs, "posts": len(best), "span_minutes": round(span, 1), "first": best[0]["ts"].isoformat(), "last": best[-1]["ts"].isoformat(),
                               "post_uids": [p["uid"] for p in best][:40],
                               "reason": f"{len(accs)} different accounts used the same {'link' if kind == 'link_burst' else 'hashtag'} ({k}) within {round(span)} minutes."})
    order = {"high": 0, "medium": 1, "low": 2}
    clusters.sort(key=lambda c: (order[c["strength"]], -len(c["accounts"])))
    bursts.sort(key=lambda c: (order[c["strength"]], -len(c["accounts"])))
    if clusters or bursts:
        head = f"{len(clusters)} group(s) of near-duplicate posts and {len(bursts)} shared link/hashtag burst(s) found within {window_minutes} minutes."
    else:
        head = f"No near-duplicate posts by different accounts, and no shared link/hashtag bursts, were found within {window_minutes} minutes."
    return {"window_minutes": window_minutes, "similarity_threshold": similarity, "clusters": clusters[:100], "bursts": bursts[:100], "headline": head,
            "posts_examined": len(prep),
            "explain": "Looks for the same (or almost the same) wording posted by different accounts close together in time, and for several accounts pushing the "
                       "same link or hashtag in a short window. Real campaigns, news outlets, fan groups and officials also copy text. Treat a match as a reason to "
                       "look at who is behind the accounts, not as proof that they are the same person or working together."}


# ---------------------------------------------------------------------------------------------------------------- attribution hints
def _bio_shingles(bio: str, k: int = 5) -> set:
    return shingles(norm_text(bio), k) if len(norm_text(bio)) >= k else set()


def attribution(accounts: list[dict]) -> dict:
    by_id: dict[tuple[str, str], dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for a in accounts:
        for kind, val, where in a.get("ids", []):
            by_id[(kind, val)][a["key"]].append(where)
    hints = []
    for (kind, val), accs in by_id.items():
        if len(accs) >= 2:
            strong = kind in ("phone", "email", "upi", "wallet")
            hints.append({"type": kind, "value": val, "accounts": sorted(accs), "where": {a: sorted(set(w)) for a, w in accs.items()}, "strength": "strong" if strong else "medium",
                          "reason": f"{len(accs)} accounts show the same {kind.replace('upi', 'UPI ID')} {'in their bio or posts' if strong else ''}".strip(),
                          "caveat": "A shared public contact (helpline, shop, group admin, news desk) can appear on many unrelated accounts."})
    # same handle on different platforms
    hs: dict[str, set] = defaultdict(set)
    for a in accounts:
        hs[a["handle"]].add(a["key"])
    for h, keys in hs.items():
        if len(keys) >= 2 and len(h) >= 5:
            hints.append({"type": "handle_reuse", "value": h, "accounts": sorted(keys), "where": {}, "strength": "weak",
                          "reason": f"The handle '{h}' exists on {len(keys)} platforms in this case.",
                          "caveat": "Common words and popular names are reused by unrelated people."})
    # shared distinctive bio phrase
    sh: dict[tuple, set] = defaultdict(set)
    for a in accounts:
        for s in _bio_shingles(a.get("bio", "")):
            sh[s].add(a["key"])
    seen_pairs = set()
    for s, keys in sh.items():
        if len(keys) >= 2:
            pair = tuple(sorted(keys))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            hints.append({"type": "bio_phrase", "value": " ".join(s), "accounts": list(pair), "where": {}, "strength": "weak",
                          "reason": f"{len(pair)} accounts share the bio wording '{' '.join(s)}'.",
                          "caveat": "Slogans, templates and copied bios are common and prove nothing on their own."})
    order = {"strong": 0, "medium": 1, "weak": 2}
    hints.sort(key=lambda h: (order[h["strength"]], -len(h["accounts"])))
    linked = set()
    for h in hints:
        for x, y in combinations(h["accounts"], 2):
            linked.add((x, y))
    head = (f"{len(hints)} attribution hint(s): {sum(1 for h in hints if h['strength'] == 'strong')} strong (same phone, e-mail, UPI ID or wallet), "
            f"{sum(1 for h in hints if h['strength'] == 'medium')} medium, {sum(1 for h in hints if h['strength'] == 'weak')} weak.") if hints else \
        "No identifier, link or bio wording is shared between different accounts yet."
    return {"hints": hints[:200], "headline": head, "account_pairs": len(linked),
            "explain": "These hints say 'these accounts share something'. They can indicate one operator behind several accounts, or just a common contact. "
                       "Each hint lists why it was raised and what innocent explanations exist. Confirm through lawful process before treating accounts as linked."}
