#!/usr/bin/env python
"""Extraction / translation evaluation harness.

Runs the real ingestion path (data_pipeline.ingestion.read_text -> ai_engine.nlp.extractor.analyze_document)
over the hand-labelled gold set in demo_dataset/extraction_gold/ and reports precision / recall / F1 per
field type, per entity kind, person-role accuracy, relationship P/R, translation-term recall and confidence
calibration (reliability table + ECE).

    python scripts/eval_extraction.py                    # dev + heldout, human-readable table
    python scripts/eval_extraction.py --split heldout    # only the held-out documents
    python scripts/eval_extraction.py --json out.json    # machine-readable
    python scripts/eval_extraction.py --failures         # list every miss / false positive
    python scripts/eval_extraction.py --min-f1 0.8       # exit 1 when micro F1 drops below the bar

Gold format (one .gold file per document) is described in demo_dataset/extraction_gold/README.md.
The scorer has its OWN canonicalisation (not imported from the engine) so it can also score an older
engine that only produced raw strings: point --root at another copy of the project to compare.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCORED_KINDS = ["PHONE", "VEHICLE", "AADHAAR", "PAN", "ACCOUNT", "IFSC", "UPI", "EMAIL", "AMOUNT", "DATE", "SECTION"]
SCORED_FIELDS = ["fir_number", "year", "district", "police_station", "fir_date", "fir_time", "occurrence_date",
                 "complainant", "place_of_occurrence", "investigating_officer"]
SCORED_RELATIONS = {"OWNS", "USED", "CALLED"}

# ----------------------------------------------------------------------------------- gold parsing


def parse_gold(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    head, _, rest = raw.partition("---text---\n")
    text, _, expect = rest.partition("---expect---\n")
    expect = expect.split("---end---")[0]
    meta = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    gold = {"fields": {}, "entities": set(), "persons": {}, "rels": set(), "translate": []}
    for line in expect.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        parts = key.split()
        if parts[0] == "field":
            gold["fields"][parts[1]] = val
        elif parts[0] == "entity":
            gold["entities"].add((parts[1], val))
        elif parts[0] == "person":
            name, _, role = val.partition("|")
            gold["persons"][pnorm(name)] = role.strip()
        elif parts[0] == "rel":
            s, r, t = [x.strip() for x in val.split("|")]
            gold["rels"].add((s, r, t))
        elif parts[0] == "translate":
            gold["translate"].append(val)
    return {"id": meta.get("id", path.stem), "split": meta.get("split", "dev"), "lang": meta.get("lang", ""),
            "tags": meta.get("tags", ""), "text": text.rstrip("\n"), "gold": gold, "file": path.name}


# ----------------------------------------------------------------------------------- scorer-side canon
_DIG = {ord(chr(base + i)): ord(str(i)) for base in (0x0966, 0x09E6, 0x0BE6, 0x0AE6, 0x0660, 0x06F0, 0x0A66, 0x0C66, 0x0CE6, 0x0D66) for i in range(10)}
_TITLES = set("""shri shree sri shrimati smt mr mrs ms miss dr late sh km kumari master
si asi psi api sub inspector insp inspr constable head hc sho io sp dsp acp dcp
श्री श्रीमती स्वर्गीय सब इंस्पेक्टर उपनिरीक्षक निरीक्षक एएसआई एएसआय पोलीस पुलिस हेड कांस्टेबल कॉन्स्टेबल एसआई
श्रीमती कुमारी सुश्री
""".split())


def digits(s: str) -> str:
    return (s or "").translate(_DIG)


def pnorm(name: str) -> str:
    n = digits(name).casefold()
    n = re.sub(r"\b(?:alias|urf|उर्फ|@)\b.*$", "", n)
    n = re.sub(r"[^\w\u0900-\u0DFF\u0600-\u06FF ]", " ", n)
    toks = [t for t in n.split() if t not in _TITLES]
    return " ".join(toks)


def fnorm(name: str, val: str) -> str:
    v = digits(val or "").strip()
    if name in ("complainant", "investigating_officer"):
        return pnorm(v)
    if name in ("fir_date", "occurrence_date"):
        return canon_date(v) or v
    if name == "fir_time":
        m = re.search(r"(\d{1,2}):(\d{2})", v)
        return f"{int(m.group(1)):02d}:{m.group(2)}" if m else v
    if name in ("fir_number", "year"):
        return re.sub(r"\s+", "", v).casefold()
    return re.sub(r"[^\wऀ-ॿঀ-৿஀-௿઀-૿]+", " ", v.casefold()).strip()


def canon_date(v: str) -> str:
    v = digits(v)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    m = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", v.strip())
    if m:
        y = int(m.group(3)); y += 2000 if y < 100 else 0
        return f"{y:04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def canon_kind(kind: str, v: str) -> str:
    v = digits(v or "").strip()
    if kind == "PHONE":
        d = re.sub(r"\D", "", v)
        return d[-10:] if len(d) >= 10 else d
    if kind in ("VEHICLE", "PAN", "IFSC"):
        return re.sub(r"[^A-Za-z0-9]", "", v).upper()
    if kind in ("ACCOUNT", "AADHAAR"):
        return re.sub(r"[\s\-]", "", v).upper() if kind == "AADHAAR" else re.sub(r"[^A-Za-z0-9]", "", v).upper()
    if kind in ("UPI", "EMAIL"):
        return v.lower()
    if kind == "DATE":
        return canon_date(v) or v
    if kind == "AMOUNT":
        try:
            return str(int(round(float(re.sub(r"[^\d.]", "", v)))))
        except ValueError:
            return v
    return v


LEGACY_TYPE_TO_KIND = {"PHONE": "PHONE", "VEHICLE": "VEHICLE", "ACCOUNT": "ACCOUNT", "EMAIL": "EMAIL", "DATE": "DATE", "MONEY": "AMOUNT",
                       "LAW_SECTION": "SECTION"}


def predicted_items(analysis: dict):
    """-> (entity items: {(kind, canon): [confidences]}, persons: {pname: (role, conf)}, sec_act: {number: act})."""
    items: dict[tuple, list] = defaultdict(list)
    persons: dict[str, tuple] = {}
    sec_act: dict[str, str] = {}
    for e in analysis.get("entities", []):
        typ = str(e.get("type", "")).upper()
        conf = float(e.get("confidence") or 0)
        if typ == "PERSON":
            role = str(e.get("role") or "")
            persons.setdefault(pnorm(e.get("normalized") or e.get("text", "")), (role, conf))
            continue
        kind = str(e.get("kind") or LEGACY_TYPE_TO_KIND.get(typ, "")).upper()
        if kind not in SCORED_KINDS:
            continue
        val = e.get("normalized") if e.get("normalized") not in (None, "") else e.get("text", "")
        if kind == "SECTION":
            m = re.match(r"([A-Za-z]+):(.+)$", str(val))
            num, act = (m.group(2), m.group(1).upper()) if m else (str(val), "")
            num = re.sub(r"\(.*", "", digits(num)).upper()
            sec_act[num] = act
            items[(kind, num)].append(conf)
            continue
        items[(kind, canon_kind(kind, str(val)))].append(conf)
    return items, persons, sec_act


ROLE_MAP = {"FATHER_OF_COMPLAINANT": "RELATIVE", "UNKNOWN_OR_SUSPECT": "ACCUSED", "SUSPECT": "ACCUSED", "NAMED_PERSON": ""}


def norm_role(r: str) -> str:
    r = str(r or "").upper()
    return ROLE_MAP.get(r, r)


def canon_rel_end(x: str) -> str:
    x = digits(x or "").strip()
    if re.fullmatch(r"[+\d][\d\s\-+]{9,}", x):
        return canon_kind("PHONE", x)
    if re.search(r"[A-Za-z]{2}\s?\d{1,2}\s?[A-Za-z]{0,3}\s?\d{4}", x) and " " not in x.strip() or re.fullmatch(r"[A-Za-z]{2}[\s\-]?\d{1,2}[\s\-]?[A-Za-z]{1,3}[\s\-]?\d{4}", x):
        return re.sub(r"[^A-Za-z0-9]", "", x).upper()
    if "@" in x:
        return x.lower()
    return pnorm(x)


# ----------------------------------------------------------------------------------- scoring


class PRF:
    def __init__(self):
        self.tp = self.fp = self.fn = 0

    def add(self, tp=0, fp=0, fn=0):
        self.tp += tp; self.fp += fp; self.fn += fn

    @property
    def p(self):
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def r(self):
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def f1(self):
        p, r = self.p, self.r
        if p is None and r is None:
            return None
        p, r = p or 0.0, r or 0.0
        return 2 * p * r / (p + r) if p + r else 0.0

    def dict(self):
        f = lambda x: None if x is None else round(x, 3)
        return {"tp": self.tp, "fp": self.fp, "fn": self.fn, "precision": f(self.p), "recall": f(self.r), "f1": f(self.f1)}


def english_blob(analysis: dict) -> str:
    ev = analysis.get("english_view") or {}
    parts = [str(ev.get("text", "")), str(ev.get("narrative", ""))]

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            parts.append(o)
    walk(ev.get("fields", {}))
    for s in ev.get("sentences", []) or []:
        parts.append(str(s.get("english", "")))
    return " ".join(parts).casefold()


def evaluate(docs: list[dict], analyze) -> dict:
    per_kind = defaultdict(PRF); per_field = defaultdict(PRF)
    persons = PRF(); role_ok = role_tot = 0; rels = PRF(); act_ok = act_tot = 0
    role_stats = defaultdict(PRF)
    trans_hit = trans_tot = 0
    cal = []  # (confidence, correct)
    details = []
    for d in docs:
        a = analyze(d["text"])
        g = d["gold"]
        miss = []
        # structured fields
        st = a.get("structured", {})
        for name, gv in g["fields"].items():
            if name not in SCORED_FIELDS:
                continue
            pv = st.get(name, "")
            ok = fnorm(name, pv) == fnorm(name, gv) and bool(pv)
            if ok:
                per_field[name].add(tp=1)
            else:
                per_field[name].add(fn=1, fp=1 if pv else 0)
                miss.append(("field", name, gv, pv))
        # entities
        items, ppers, sec_act = predicted_items(a)
        gold_ents = {(k, canon_kind(k, v)) if k != "SECTION" else (k, re.sub(r"\(.*", "", v.split(":", 1)[1]).upper()) for k, v in g["entities"]}
        gold_act = {v.split(":", 1)[1].upper(): v.split(":", 1)[0] for k, v in g["entities"] if k == "SECTION"}
        for it, confs in items.items():
            if it in gold_ents:
                per_kind[it[0]].add(tp=1); cal.extend((c, 1) for c in confs[:1])
            else:
                per_kind[it[0]].add(fp=1); cal.extend((c, 0) for c in confs[:1]); miss.append(("extra", it[0], it[1], ""))
        for it in gold_ents:
            if it not in items:
                per_kind[it[0]].add(fn=1); miss.append(("missing", it[0], it[1], ""))
            elif it[0] == "SECTION":
                act_tot += 1; act_ok += int(sec_act.get(it[1], "") == gold_act.get(it[1]))
        # persons / roles
        for name, (role, conf) in ppers.items():
            if not name:
                continue
            if name in g["persons"]:
                persons.add(tp=1); cal.append((conf, 1))
                role_tot += 1; ok = norm_role(role) == g["persons"][name]; role_ok += int(ok)
                role_stats[g["persons"][name]].add(tp=int(ok), fn=int(not ok))
                if not ok:
                    miss.append(("role", name, g["persons"][name], norm_role(role)))
                    role_stats[norm_role(role) or "(none)"].add(fp=1)
            else:
                persons.add(fp=1); cal.append((conf, 0)); miss.append(("extra-person", name, "", role))
        for name, role in g["persons"].items():
            if name not in ppers:
                persons.add(fn=1); role_stats[role].add(fn=1); miss.append(("missing-person", name, role, ""))
        # relationships
        pred_rels = set()
        for r in a.get("relationship_hints", []):
            rel = str(r.get("relation", "")).upper()
            if rel in SCORED_RELATIONS:
                pred_rels.add((canon_rel_end(r.get("source", "")), rel, canon_rel_end(r.get("target", ""))))
        gold_rels = {(canon_rel_end(s), r, canon_rel_end(t)) for s, r, t in g["rels"]}
        rels.add(tp=len(pred_rels & gold_rels), fp=len(pred_rels - gold_rels), fn=len(gold_rels - pred_rels))
        for x in gold_rels - pred_rels:
            miss.append(("missing-rel", "|".join(x), "", ""))
        for x in pred_rels - gold_rels:
            miss.append(("extra-rel", "|".join(x), "", ""))
        # translation
        blob = english_blob(a)
        for tok in g["translate"]:
            trans_tot += 1
            if tok.casefold() in blob:
                trans_hit += 1
            else:
                miss.append(("translate", tok, "", ""))
        details.append({"id": d["id"], "split": d["split"], "lang": d["lang"], "misses": miss})
    # calibration
    bins = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 0.95), (0.95, 1.01)]
    reliab = []; ece = 0.0; n = len(cal)
    for lo, hi in bins:
        xs = [(c, k) for c, k in cal if lo <= c < hi]
        if xs:
            mc = sum(c for c, _ in xs) / len(xs); acc = sum(k for _, k in xs) / len(xs)
            reliab.append({"bin": f"{lo:.2f}-{min(hi, 1):.2f}", "n": len(xs), "mean_conf": round(mc, 3), "accuracy": round(acc, 3)})
            ece += len(xs) / n * abs(mc - acc)
    micro = PRF()
    for m in list(per_kind.values()) + list(per_field.values()) + [persons]:
        micro.add(m.tp, m.fp, m.fn)
    return {
        "docs": len(docs),
        "fields": {k: per_field[k].dict() for k in SCORED_FIELDS if k in per_field},
        "entity_kinds": {k: per_kind[k].dict() for k in SCORED_KINDS if k in per_kind},
        "persons": persons.dict(),
        "role_accuracy": round(role_ok / role_tot, 3) if role_tot else None,
        "roles": {k: v.dict() for k, v in sorted(role_stats.items())},
        "relationships": rels.dict(),
        "section_act_accuracy": round(act_ok / act_tot, 3) if act_tot else None,
        "translation_term_recall": round(trans_hit / trans_tot, 3) if trans_tot else None,
        "calibration": {"ece": round(ece, 3), "bins": reliab, "n": n},
        "micro": micro.dict(),
        "details": details,
    }


def fmt(v):
    return "  -  " if v is None else f"{v:.2f}"


def print_report(title: str, r: dict, failures: bool):
    print(f"\n=== {title}  ({r['docs']} documents) ===")
    print(f"{'item':<24}{'P':>6}{'R':>6}{'F1':>6}   tp/fp/fn")

    def row(name, m):
        print(f"{name:<24}{fmt(m['precision']):>6}{fmt(m['recall']):>6}{fmt(m['f1']):>6}   {m['tp']}/{m['fp']}/{m['fn']}")
    print("-- structured fields")
    for k, m in r["fields"].items():
        row(k, m)
    print("-- entity kinds")
    for k, m in r["entity_kinds"].items():
        row(k, m)
    row("PERSON (name)", r["persons"])
    print(f"person role accuracy (matched persons): {r['role_accuracy']}")
    for k, m in r["roles"].items():
        row("  role " + k, m)
    row("relationships", r["relationships"])
    print(f"section act accuracy: {r['section_act_accuracy']}    translation term recall: {r['translation_term_recall']}")
    c = r["calibration"]
    print(f"calibration ECE={c['ece']} over {c['n']} scored items")
    for b in c["bins"]:
        print(f"   conf {b['bin']}: n={b['n']:<4} mean_conf={b['mean_conf']:.2f} actual_accuracy={b['accuracy']:.2f}")
    m = r["micro"]
    print(f"MICRO (fields+kinds+persons): P={fmt(m['precision'])} R={fmt(m['recall'])} F1={fmt(m['f1'])}")
    if failures:
        print("-- misses")
        for d in r["details"]:
            for x in d["misses"]:
                print(f"  {d['id']:<5} {x[0]:<14} {x[1]:<22} gold={x[2]!s:<28} got={x[3]!s}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "demo_dataset" / "extraction_gold"))
    ap.add_argument("--split", choices=["dev", "heldout", "all"], default="all")
    ap.add_argument("--json")
    ap.add_argument("--failures", action="store_true")
    ap.add_argument("--min-f1", type=float, default=None)
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT))
    from data_pipeline.ingestion import read_text
    from ai_engine.nlp.extractor import analyze_document

    def analyze(text: str) -> dict:
        t, meta = read_text(text.encode("utf-8"), "gold.txt", language="auto", return_meta=True)
        return analyze_document(t, meta)

    docs = [parse_gold(p) for p in sorted(Path(args.gold).glob("*.gold"))]
    out = {}
    splits = ["dev", "heldout"] if args.split == "all" else [args.split]
    for sp in splits:
        sel = [d for d in docs if d["split"] == sp]
        if sel:
            out[sp] = evaluate(sel, analyze)
            print_report(sp.upper(), out[sp], args.failures)
    if len(splits) > 1:
        out["all"] = evaluate(docs, analyze)
        print_report("ALL", out["all"], False)
    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.min_f1 is not None:
        f1 = out.get("all", next(iter(out.values())))["micro"]["f1"] or 0
        sys.exit(0 if f1 >= args.min_f1 else 1)


if __name__ == "__main__":
    main()
