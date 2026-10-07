"""Per-operation hash chain for append-only surveillance / observation logs.

Every entry stores ``entry_hash = SHA-256(canonical JSON of the entry)`` and the previous entry's hash; the first entry points at a hash of the
operation's immutable header (authority, subject, objective ...). Editing, re-ordering or deleting any row in the middle breaks the chain, and the
verify routine recomputes it from the stored fields. Deleting the last entry is caught by the operation's stored head/count and, when the audit log
is consulted, by the keyed audit chain. A person with full database write access could in principle rewrite everything: that is what the
ledger / audit anchoring is for, and it is stated plainly in the documentation.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

ENTRY_FIELDS = ("uid", "op_number", "case_number", "seq", "version", "root_uid", "supersedes_uid", "observed_at", "location_text", "lat", "lon", "place_matched",
                "observer", "observation", "subject_seen", "vehicles", "persons", "evidence_ids", "source_rating", "info_rating", "amendment_reason",
                "recorded_by", "recorded_at", "prev_hash")
HEADER_FIELDS = ("op_number", "case_number", "subject_entity_id", "subject_text", "objective", "authority_text", "authority_ref", "start_at", "end_at",
                 "supervising_officer", "team", "created_by", "created_at")
SOURCE_RATINGS = {"A": "Completely reliable: officer with a long record of accurate observation, or a verified technical record",
                  "B": "Usually reliable: trained observer, minor doubts in the past",
                  "C": "Fairly reliable: has provided accurate information some of the time",
                  "D": "Not usually reliable: significant doubts",
                  "E": "Unreliable: a history of inaccurate information",
                  "F": "Reliability cannot be judged: new or unknown source"}
INFO_RATINGS = {"1": "Confirmed: corroborated by other independent sources or records",
                "2": "Probably true: consistent with other information, not yet confirmed",
                "3": "Possibly true: not confirmed, reasonably consistent",
                "4": "Doubtful: not consistent with other information",
                "5": "Improbable: contradicted by other information",
                "6": "Truth cannot be judged: no basis for evaluation"}


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def header_hash(op: dict) -> str:
    return sha256_hex(canonical({k: op.get(k) for k in HEADER_FIELDS}))


def entry_hash(entry: dict) -> str:
    return sha256_hex(canonical({k: entry.get(k) for k in ENTRY_FIELDS}))


def closure_hash(op: dict, head: str, count: int) -> str:
    return sha256_hex(canonical({"op_number": op["op_number"], "closing_remarks": op.get("closing_remarks", ""), "closed_by": op.get("closed_by", ""),
                                 "closed_at": op.get("closed_at", ""), "head_hash": head, "entries": count}))


def verify(op: dict, entries: list[dict], stored_head: str = "", stored_count: int | None = None) -> dict:
    """Recompute the chain. ``op`` and ``entries`` are plain dicts holding exactly the stored values."""
    problems: list[dict] = []
    ents = sorted(entries, key=lambda e: e["seq"])
    prev = header_hash(op)
    seen: dict[str, int] = {}
    if op.get("header_hash") and op["header_hash"] != prev:
        problems.append({"seq": 0, "problem": "operation_header_changed", "detail": "The operation's authority, subject or objective no longer matches what was recorded when it was created."})
    for i, e in enumerate(ents, 1):
        if e["seq"] != i:
            problems.append({"seq": e["seq"], "uid": e.get("uid"), "problem": "sequence_gap", "detail": f"Expected entry number {i}; an entry may have been removed or renumbered."})
            i = e["seq"]
        if e.get("prev_hash") != prev:
            problems.append({"seq": e["seq"], "uid": e.get("uid"), "problem": "prev_hash_mismatch", "detail": "This entry does not point at the entry before it."})
        if entry_hash(e) != e.get("entry_hash"):
            problems.append({"seq": e["seq"], "uid": e.get("uid"), "problem": "content_changed", "detail": "The stored content no longer matches the hash recorded when it was written."})
        if e.get("supersedes_uid"):
            if seen.get(e["supersedes_uid"], 10 ** 9) >= e["seq"]:
                problems.append({"seq": e["seq"], "uid": e.get("uid"), "problem": "bad_amendment_link", "detail": "An amendment must point at an earlier entry."})
        seen[e.get("uid")] = e["seq"]
        prev = e.get("entry_hash") or ""
    head = ents[-1]["entry_hash"] if ents else header_hash(op)
    if stored_head and stored_head != head:
        problems.append({"seq": len(ents), "problem": "head_mismatch", "detail": "The operation's recorded latest hash differs from the last entry: entries may have been removed."})
    if stored_count is not None and stored_count != len(ents):
        problems.append({"seq": len(ents), "problem": "count_mismatch", "detail": f"The operation recorded {stored_count} entries but {len(ents)} exist."})
    if op.get("status") == "closed" and op.get("closure_hash"):
        if closure_hash(op, stored_head or head, stored_count if stored_count is not None else len(ents)) != op["closure_hash"]:
            problems.append({"seq": len(ents), "problem": "closure_mismatch", "detail": "The closing record no longer matches the log or the closing remarks."})
    return {"status": "verified" if not problems else "broken", "entries_checked": len(ents), "head_hash": head, "header_hash": header_hash(op), "problems": problems}


REG_RX = re.compile(r"^(?:[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}|\d{2}BH\d{4}[A-Z]{1,2})$")


def normalise_reg(raw: str) -> dict:
    """Indian registration: upper-case, no spaces/hyphens. ``valid`` says whether it fits a known pattern (state+RTO+series+number, or BH series)."""
    s = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    return {"reg": s, "valid": bool(REG_RX.match(s))}
