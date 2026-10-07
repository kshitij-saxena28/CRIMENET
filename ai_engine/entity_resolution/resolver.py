import re
from functools import lru_cache
from difflib import SequenceMatcher
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors


def normalize(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _compact_identifier(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def similarity(a, b):
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def candidates(value, items, threshold=.72, top_k=5):
    items = list(items)
    if not items or not str(value).strip():
        return []
    texts = [normalize(x) for x in items]
    query = normalize(value)
    if not query:
        return []
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=1, sublinear_tf=True)
    matrix = vectorizer.fit_transform(texts + [query])
    k = min(max(1, int(top_k)), len(items))
    nn = NearestNeighbors(n_neighbors=k, metric="cosine").fit(matrix[:-1])
    distances, indices = nn.kneighbors(matrix[-1])
    out = []
    for d, idx in zip(distances[0], indices[0]):
        lexical = similarity(value, items[idx])
        cosine = max(0.0, 1 - float(d))
        score = round(.68 * cosine + .32 * lexical, 4)
        if score >= threshold:
            out.append((items[int(idx)], score))
    return sorted(out, key=lambda x: x[1], reverse=True)


def _profile(record, field="name"):
    attrs = record.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}
    primary = str(record.get(field) or record.get("name") or "").strip()
    identifiers = []
    for key in ("phone", "number", "plate", "vehicle", "account_number", "serial", "email", "alias", "external_id"):
        if attrs.get(key):
            identifiers.append(str(attrs[key]).strip())
    if record.get("external_id"):
        identifiers.append(str(record.get("external_id")).strip())
    return {"primary": primary, "identifiers": [x for x in identifiers if x]}


@lru_cache(maxsize=8)
def _fit_index(documents: tuple):
    """TF-IDF index over the entity texts, cached so repeated lookups on an unchanged case do not refit."""
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=1, sublinear_tf=True)
    return vectorizer, vectorizer.fit_transform(documents)


def knn_candidates(value, records, field="name", threshold=.60, top_k=8):
    """Explainable KNN entity resolution.

    Ranking uses character n-gram KNN plus lexical similarity and a strong exact/normalized
    identifier boost. No identity is merged automatically; the endpoint only returns
    ranked candidate matches for human confirmation.
    """
    rows = list(records or [])
    query = str(value or "").strip()
    if not rows or not query:
        return []

    profiles = [_profile(r, field) for r in rows]
    documents = []
    for p in profiles:
        documents.append(" | ".join([p["primary"], *p["identifiers"]]))

    norm_query = normalize(query)
    if not norm_query:
        return []

    vectorizer, index_matrix = _fit_index(tuple(normalize(x) for x in documents))
    k = min(max(1, int(top_k)), len(rows))
    nn = NearestNeighbors(n_neighbors=k, metric="cosine").fit(index_matrix)
    distances, indices = nn.kneighbors(vectorizer.transform([norm_query]))

    out = []
    compact_query = _compact_identifier(query)
    for dist, idx in zip(distances[0], indices[0]):
        idx = int(idx)
        row = rows[idx]
        profile = profiles[idx]
        cosine = max(0.0, 1.0 - float(dist))
        lexical = similarity(query, profile["primary"])
        identifier_exact = 1.0 if compact_query and any(compact_query == _compact_identifier(x) for x in profile["identifiers"]) else 0.0
        identifier_partial = max([similarity(query, x) for x in profile["identifiers"]] + [0.0])
        # Name queries favor semantic/text similarity; exact identifiers dominate when present.
        score = min(1.0, .52 * cosine + .28 * lexical + .14 * identifier_partial + .06 * identifier_exact)
        if identifier_exact:
            score = max(score, .93)
        score = round(score, 4)
        if score < threshold:
            continue
        reason_parts = ["char n-gram KNN"]
        if lexical >= .8:
            reason_parts.append("strong name similarity")
        elif lexical >= .6:
            reason_parts.append("moderate name similarity")
        if identifier_partial >= .8:
            reason_parts.append("identifier similarity")
        if identifier_exact:
            reason_parts.append("exact normalized identifier")
        out.append({
            **row,
            "similarity": score,
            "knn_cosine": round(cosine, 4),
            "lexical_similarity": round(lexical, 4),
            "identifier_similarity": round(identifier_partial, 4),
            "match_confidence": "high" if score >= .85 else ("medium" if score >= .70 else "review"),
            "match_reason": " + ".join(reason_parts),
            "human_confirmation_required": True,
        })

    return sorted(out, key=lambda x: (x["similarity"], x.get("name", "")), reverse=True)[:top_k]
