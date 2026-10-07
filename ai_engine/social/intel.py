"""Investigator aids over already-imported social material: priority queue, look-alike accounts, narrative origin tracking.

Pure functions (no database, no network). Everything is descriptive decision support: a score orders a reading list, it is never a
finding about a person. Every result lists the reasons it was raised and what innocent explanations exist.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import timedelta
from difflib import SequenceMatcher

# Transparent, hand-set weights. They are shown to the user (see ``TRIAGE_WEIGHTS``) and can be argued with; nothing is learned.
TRIAGE_WEIGHTS = {
    "unreviewed_flag_high": 25, "unreviewed_flag_medium": 15, "unreviewed_flag_low": 6, "flags_cap": 60, "flag_marked_relevant": 10,
    "watch_hit_high": 30, "watch_hit_normal": 15, "watch_cap": 45,
    "coordinated_high": 20, "coordinated_medium": 12, "coordinated_low": 5, "coordinated_cap": 30,
    "strong_attribution_hint": 12, "attribution_cap": 24,
    "reach_100k": 8, "reach_10k": 5, "reach_1k": 2, "recent_activity": 5,
}
LEVELS = (("high", 60), ("medium", 30), ("low", 0))

_LEET = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "@": "a", "!": "i"})


def canon_handle(handle: str) -> str:
    """Handle stripped of the small tricks people use to make a second account look different (dots, underscores, digits, leet)."""
    h = (handle or "").lower().translate(_LEET)
    return re.sub(r"[^a-z]", "", h)


def canon_name(name: str) -> str:
    return re.sub(r"[^a-z0-9ऀ-ॿ]", "", (name or "").lower())


def level_for(score: float) -> str:
    for name, floor in LEVELS:
        if score >= floor:
            return name
    return "low"


# ---------------------------------------------------------------------------------------------------------------- look-alikes
def lookalike_accounts(accounts: list[dict], min_similarity: float = 0.86) -> dict:
    """Pairs of accounts whose handle or display name look like deliberate variants of each other.

    ``accounts``: dicts with key, platform, handle, display_name, bio, followers. Handles are compared after removing dots, underscores, digits and
    look-alike characters (rahul_sharma92 ~ rahulsharma ~ r4hul.sharma).
    """
    accs = [a for a in accounts if canon_handle(a.get("handle", ""))]
    pairs = []
    for i in range(len(accs)):
        for j in range(i + 1, len(accs)):
            a, b = accs[i], accs[j]
            if a["key"] == b["key"]:
                continue
            ca, cb = canon_handle(a["handle"]), canon_handle(b["handle"])
            reasons, score = [], 0.0
            if len(ca) >= 5 and len(cb) >= 5:
                if ca == cb:
                    reasons.append(f"Handles '{a['handle']}' and '{b['handle']}' are identical once dots, underscores, digits and look-alike characters are ignored.")
                    score += 0.55
                else:
                    r = SequenceMatcher(None, ca, cb).ratio()
                    if r >= min_similarity:
                        reasons.append(f"Handles '{a['handle']}' and '{b['handle']}' are {round(r * 100)}% similar.")
                        score += 0.35 * r
            na, nb = canon_name(a.get("display_name", "")), canon_name(b.get("display_name", ""))
            if len(na) >= 5 and na == nb:
                reasons.append(f"Same display name ('{a.get('display_name')}').")
                score += 0.3
            ba, bb = set(re.findall(r"\w{4,}", (a.get("bio") or "").lower())), set(re.findall(r"\w{4,}", (b.get("bio") or "").lower()))
            if len(ba) >= 4 and len(bb) >= 4:
                jac = len(ba & bb) / len(ba | bb)
                if jac >= 0.6:
                    reasons.append(f"Profile texts share {round(jac * 100)}% of their words.")
                    score += 0.25
            if a["platform"] != b["platform"] and reasons:
                score += 0.05  # the same persona spread over two platforms is the typical pattern
            if reasons and (len(reasons) >= 2 or score >= 0.5):
                strength = "strong" if len(reasons) >= 3 or score >= 0.85 else ("medium" if len(reasons) >= 2 or score >= 0.55 else "weak")
                pairs.append({"a": a["key"], "b": b["key"], "score": round(min(score, 1.0), 2), "strength": strength, "reasons": reasons,
                              "caveat": "Fans, parodies, brands with regional pages, and people who simply chose a popular name look the same. Confirm through lawful process."})
    order = {"strong": 0, "medium": 1, "weak": 2}
    pairs.sort(key=lambda p: (order[p["strength"]], -p["score"]))
    head = (f"{len(pairs)} pair(s) of accounts look like variants of each other." if pairs else "No pair of accounts looks like a deliberate variant of another.")
    return {"pairs": pairs[:150], "headline": head,
            "explain": "Compares handles (ignoring dots, underscores, digits and look-alike characters), display names and profile wording. "
                       "It suggests accounts worth checking for a shared operator; it cannot tell a copycat from the original or a fan from an impersonator."}


# ---------------------------------------------------------------------------------------------------------------- narratives
def narratives(posts: list[dict], min_posts: int = 3, top: int = 30) -> dict:
    """How a hashtag or link spread: who used it first, who pushed it most, and whether its use came in a sudden burst.

    ``posts``: dicts with uid, account, ts, hashtags, urls, likes, shares. "First" means first among the *imported* posts only.
    """
    def build(kind: str, keyf):
        by: dict[str, list[dict]] = defaultdict(list)
        for p in posts:
            for k in set(keyf(p)):
                if k:
                    by[k].append(p)
        out = []
        for k, lst in by.items():
            accounts = {p["account"] for p in lst}
            if len(lst) < min_posts and len(accounts) < 3:
                continue
            dated = sorted((p for p in lst if p.get("ts")), key=lambda p: p["ts"])
            daily = Counter(p["ts"].date().isoformat() for p in dated)
            peak_day, peak = (daily.most_common(1)[0] if daily else ("", 0))
            med = statistics.median(daily.values()) if daily else 0
            burst = bool(dated) and peak >= 3 and len(accounts) >= 2 and (peak / len(dated) >= 0.6 or (med and peak >= 3 * med and len(daily) >= 3))
            amp = Counter(p["account"] for p in lst)
            reach = sum((p.get("likes") or 0) + (p.get("shares") or 0) for p in lst)
            first = dated[0] if dated else None
            out.append({"kind": kind, "key": k, "posts": len(lst), "accounts": len(accounts), "first_seen": first["ts"].isoformat() if first else None,
                        "last_seen": dated[-1]["ts"].isoformat() if dated else None, "first_account": first["account"] if first else None,
                        "first_post_uid": first["uid"] if first else None, "peak_day": peak_day, "peak_posts": peak, "burst": burst, "engagement": reach,
                        "top_amplifiers": [{"account": a, "posts": n} for a, n in amp.most_common(3)],
                        "daily": [{"date": d, "count": c} for d, c in sorted(daily.items())][:60], "post_uids": [p["uid"] for p in lst][:60],
                        "undated_posts": len(lst) - len(dated),
                        "reason": (f"{len(lst)} posts from {len(accounts)} account(s)" + (f", {peak} of them on {peak_day}" if burst else "") +
                                   (f". First seen in the imported material: {first['account']}." if first else "."))})
        out.sort(key=lambda x: (not x["burst"], -x["accounts"], -x["posts"]))
        return out[:top]
    tags = build("hashtag", lambda p: p.get("hashtags") or [])
    links = build("link", lambda p: [u for u in (p.get("urls") or []) if "/" in u])
    n_burst = sum(1 for x in tags + links if x["burst"])
    head = (f"{len(tags)} hashtag(s) and {len(links)} link(s) were used repeatedly; {n_burst} appeared in a sudden burst." if (tags or links)
            else "No hashtag or link is used often enough yet to show a spread pattern.")
    return {"hashtags": tags, "links": links, "headline": head,
            "explain": "For each repeated hashtag or link: the earliest imported post using it, who pushed it most, and whether most use came in a single day. "
                       "'First' only means first in what you imported: the real origin may be older or on another platform. Trending topics, news events and "
                       "fan communities produce the same shape as organised pushes."}


# ---------------------------------------------------------------------------------------------------------------- priority queue
def triage(accounts: list[dict], posts: list[dict], flags: list[dict], watch_hits: list[dict], coordinated: dict, attribution: dict, top: int = 25) -> dict:
    """Order accounts and posts by how much a human should look at them first.

    ``flags``: dicts with post_uid (or account_key), level, review_status, category. ``watch_hits``: dicts with account, post_uid, priority.
    Returns accounts and posts with a 0-100 score and the exact reasons behind it.
    """
    W = TRIAGE_WEIGHTS
    by_key = {a["key"]: a for a in accounts}
    acct_of_post = {p["uid"]: p["account"] for p in posts}
    S: dict[str, dict] = {k: {"score": 0.0, "reasons": [], "parts": defaultdict(float)} for k in by_key}

    def add(key, part, pts, reason, cap=None):
        if key not in S or pts <= 0:
            return
        cur = S[key]["parts"][part]
        pts = min(pts, cap - cur) if cap is not None else pts
        if pts <= 0:
            return
        S[key]["parts"][part] += pts
        S[key]["reasons"].append(reason)

    post_scores: dict[str, dict] = {}

    def bump_post(uid, pts, reason):
        e = post_scores.setdefault(uid, {"score": 0.0, "reasons": []})
        e["score"] += pts
        e["reasons"].append(reason)

    for f in flags:
        key = f.get("account_key") or acct_of_post.get(f.get("post_uid"))
        st = f.get("review_status", "unreviewed")
        if st == "false_positive":
            continue
        lvl = f.get("level") or "low"
        base = W.get(f"unreviewed_flag_{lvl}", W["unreviewed_flag_low"])
        label = f.get("label") or f.get("category", "flag")
        if st == "relevant":
            add(key, "flags", base + W["flag_marked_relevant"], f"Wording flag '{label}' was marked relevant by a reviewer.", W["flags_cap"])
        elif st == "reviewed":
            continue
        else:
            add(key, "flags", base, f"Wording flag '{label}' ({lvl}) has not been reviewed yet.", W["flags_cap"])
        if f.get("post_uid"):
            bump_post(f["post_uid"], base + (W["flag_marked_relevant"] if st == "relevant" else 0), f"Wording flag: {label} ({lvl}, {st.replace('_', ' ')})")
    for hit in watch_hits:
        pts = W["watch_hit_high"] if hit.get("priority") == "high" else W["watch_hit_normal"]
        add(hit["account"], "watch", pts, f"Matches watch-list term '{hit['term']}' ({hit['kind']}).", W["watch_cap"])
        if hit.get("post_uid"):
            bump_post(hit["post_uid"], pts, f"Watch-list term '{hit['term']}'")
    for c in (coordinated.get("clusters", []) + coordinated.get("bursts", [])):
        pts = W[f"coordinated_{c['strength']}"]
        for acc in c["accounts"]:
            add(acc, "coordinated", pts, f"Part of coordinated activity: {c['reason']}", W["coordinated_cap"])
    for hnt in attribution.get("hints", []):
        if hnt["strength"] == "strong":
            for acc in hnt["accounts"]:
                add(acc, "attribution", W["strong_attribution_hint"], f"Shares a {hnt['type']} with {len(hnt['accounts']) - 1} other account(s).", W["attribution_cap"])
    latest = max((p["ts"] for p in posts if p.get("ts")), default=None)
    if latest:
        recent = {p["account"] for p in posts if p.get("ts") and latest - p["ts"] <= timedelta(days=7)}
        for k in recent:
            add(k, "recent", W["recent_activity"], "Posted within the last 7 days of the imported material.")
    for k, a in by_key.items():
        f = a.get("followers") or 0
        if f >= 100_000:
            add(k, "reach", W["reach_100k"], f"Large audience ({f:,} followers).")
        elif f >= 10_000:
            add(k, "reach", W["reach_10k"], f"Sizeable audience ({f:,} followers).")
        elif f >= 1_000:
            add(k, "reach", W["reach_1k"], f"{f:,} followers.")
    items = []
    for k, s in S.items():
        sc = min(100.0, sum(s["parts"].values()))
        if sc <= 0:
            continue
        a = by_key[k]
        items.append({"account": k, "account_id": a.get("id"), "display_name": a.get("display_name", ""), "score": round(sc, 1), "level": level_for(sc),
                      "reasons": s["reasons"][:8], "breakdown": {p: round(v, 1) for p, v in s["parts"].items()}})
    items.sort(key=lambda x: -x["score"])
    post_items = [{"post_uid": u, "account": acct_of_post.get(u, ""), "score": round(min(100.0, e["score"]), 1), "level": level_for(min(100.0, e["score"])), "reasons": e["reasons"][:5]}
                  for u, e in sorted(post_scores.items(), key=lambda kv: -kv[1]["score"])[:top]]
    n_high = sum(1 for i in items if i["level"] == "high")
    head = (f"{len(items)} account(s) have something a human should read; {n_high} are at the top of the list." if items
            else "Nothing in the imported material needs priority attention yet.")
    return {"accounts": items[:top], "posts": post_items, "headline": head, "weights": W,
            "explain": "The order comes from a short list of visible rules (unreviewed wording flags, watch-list matches, coordinated activity, shared identifiers, "
                       "audience size, recent activity). Each line shows the points it added. A high score means 'read this first', never 'this person did something'. "
                       "Nothing is learned from data: the weights are set by hand and can be argued with."}
