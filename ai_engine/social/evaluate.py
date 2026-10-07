"""Precision / recall of the content flagger on the bundled synthetic corpus (see docs/models/social-flagging.md)."""
from __future__ import annotations

import json
from pathlib import Path

from ai_engine.social.lexicon import CATEGORIES, flag_text

CORPUS = Path(__file__).with_name("eval_corpus.json")


def load_corpus(split: str | None = None) -> list[dict]:
    posts = json.loads(CORPUS.read_text(encoding="utf-8"))["posts"]
    return [p for p in posts if split in (None, p["split"])]


def evaluate(split: str | None = None) -> dict:
    posts = load_corpus(split)
    tp = dict.fromkeys(CATEGORIES, 0)
    fp = dict.fromkeys(CATEGORIES, 0)
    fn = dict.fromkeys(CATEGORIES, 0)
    errors = []
    for p in posts:
        got = {f["category"] for f in flag_text(p["text"])}
        want = set(p["labels"])
        for c in CATEGORIES:
            if c in got and c in want:
                tp[c] += 1
            elif c in got:
                fp[c] += 1
                errors.append({"id": p["id"], "kind": "false_positive", "category": c, "text": p["text"]})
            elif c in want:
                fn[c] += 1
                errors.append({"id": p["id"], "kind": "missed", "category": c, "text": p["text"]})

    def prf(t, f_p, f_n):
        pr = t / (t + f_p) if t + f_p else None
        rc = t / (t + f_n) if t + f_n else None
        return pr, rc

    per = {}
    for c in CATEGORIES:
        pr, rc = prf(tp[c], fp[c], fn[c])
        per[c] = {"tp": tp[c], "fp": fp[c], "fn": fn[c], "precision": None if pr is None else round(pr, 3), "recall": None if rc is None else round(rc, 3),
                  "support": tp[c] + fn[c]}
    T, F_P, F_N = sum(tp.values()), sum(fp.values()), sum(fn.values())
    pr, rc = prf(T, F_P, F_N)
    f1 = 2 * pr * rc / (pr + rc) if pr and rc else 0.0
    benign = [p for p in posts if not p["labels"]]
    fp_posts = {e["id"] for e in errors if e["kind"] == "false_positive"}
    return {"split": split or "all", "posts": len(posts), "benign_posts": len(benign), "labelled_posts": len(posts) - len(benign),
            "micro": {"precision": round(pr, 3) if pr is not None else None, "recall": round(rc, 3) if rc is not None else None, "f1": round(f1, 3)},
            "benign_false_positive_rate": round(len(fp_posts & {p["id"] for p in benign}) / len(benign), 3) if benign else None,
            "per_category": per, "errors": errors}


if __name__ == "__main__":
    for sp in ("dev", "heldout", None):
        r = evaluate(sp)
        if r["posts"]:
            print(sp or "all", r["posts"], r["micro"], "benign FPR", r["benign_false_positive_rate"])
            for e in r["errors"]:
                print("  ", e["kind"], e["category"], e["text"][:90])
