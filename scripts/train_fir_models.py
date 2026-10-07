"""Train the small, fully local FIR context classifier used as a *secondary* extraction scorer.

Not a replacement for transformer NER. It checks whether an extracted span's local context
resembles the expected FIR field type. This script also:
  * evaluates on a stratified 80/20 held-out split and writes ai_engine/models/metrics.json
  * records the SHA-256 of the saved model in MODEL_HASHES.json (the loader refuses a model
    whose hash does not match, because joblib files are pickles and must not be swapped silently)

IMPORTANT: the training snippets are synthetic and templated, so held-out scores here are an
optimistic upper bound (near-duplicate templates leak across the split). They are NOT a claim
about accuracy on real FIRs.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "scripts" / "training_data" / "fir_entity_context.csv"
OUT_DIR = ROOT / "ai_engine" / "models"
OUT = OUT_DIR / "fir_entity_context.joblib"
VERSION = "entity-context-nlp-v2.1"


def make_pipeline() -> Pipeline:
    return Pipeline([
        ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1, sublinear_tf=True)),
        ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", random_state=26189)),
    ])


def main() -> None:
    rows = list(csv.DictReader(DATA.open(encoding="utf-8")))
    X = [r["text"] for r in rows]
    y = [r["label"] for r in rows]

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, stratify=y, random_state=26189)
    held_out = make_pipeline().fit(X_tr, y_tr)
    rep = classification_report(y_te, held_out.predict(X_te), output_dict=True, zero_division=0)
    n_unique_te = len(set(X_te)); leaked = len(set(X_te) & set(X_tr))

    final = make_pipeline().fit(X, y)  # deployed model uses all data
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": final, "version": VERSION, "labels": sorted(set(y)), "samples": len(y)}, OUT, compress=3)
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    (OUT_DIR / "MODEL_HASHES.json").write_text(json.dumps({OUT.name: digest}, indent=2))
    (OUT_DIR / "metrics.json").write_text(json.dumps({"entity_context": {
        "split": "stratified 80/20 hold-out", "train_samples": len(X_tr), "test_samples": len(X_te),
        "accuracy": round(rep["accuracy"], 4), "macro_f1": round(rep["macro avg"]["f1-score"], 4),
        "per_class_f1": {k: round(v["f1-score"], 4) for k, v in rep.items() if k in set(y)},
        "exact_test_texts_also_in_train": leaked, "unique_test_texts": n_unique_te,
        "caveat": "Synthetic templated data: optimistic upper bound, not real-world accuracy."}}, indent=2))
    print(f"trained={len(y)} output={OUT} sha256={digest[:16]}... heldout_acc={rep['accuracy']:.3f} macro_f1={rep['macro avg']['f1-score']:.3f} leaked_exact_texts={leaked}")


if __name__ == "__main__":
    main()
