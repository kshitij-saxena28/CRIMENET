#!/usr/bin/env python3
"""Verify a DARK CRIMENET ledger bundle OFFLINE - no server, no database, no network.

    python scripts/verify_ledger.py bundle.json [--expect-head HASH] [--expect-index N] [--trust-key FINGERPRINT] [--json]

Download the bundle from the Data Integrity page ("Download signed bundle") or GET /integrity/bundle.

What is checked
  * the bundle's own signature and the key chain (genesis key -> every key rotation, each signed by the previous key);
  * for every block: payload hash, block hash, key, Ed25519 signature;
  * that consecutive blocks link to each other (prev_hash), the indexes run without gaps in a full-chain bundle, and
    nothing was re-ordered;
  * Merkle anchors: recomputed from the blocks in a full-chain bundle; for a case slice every block's inclusion proof is
    checked against its anchor block;
  * --expect-head: a head hash you recorded earlier (printed / exported / from the witness file). The chain must contain it.
  * --trust-key: the key fingerprint you pinned when the system was set up. Without it the script can only say that the
    bundle is self-consistent, not that it comes from YOUR server.

What it cannot prove: that the newest blocks were not removed unless you supply --expect-head (or --expect-index); that
recorded facts were true when entered; or anything about data that was never recorded.

Exit status: 0 = verified, 1 = tampering / inconsistency found, 2 = bad usage or unreadable file.
Needs only the Python standard library (uses the 'cryptography' package for Ed25519 if present, otherwise a built-in
pure-Python implementation of RFC 8032 verification).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys

DOMAIN = "DCN-LEDGER-v1"
ZERO = "0" * 64


# ------------------------------------------------------------------ Ed25519 (RFC 8032, verification only)
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _inv(x):
    return pow(x, _P - 2, _P)


def _xrecover(y):
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = x * _I % _P
    if x % 2 != 0:
        x = _P - x
    return x


_BY = 4 * _inv(5) % _P
_B = (_xrecover(_BY), _BY)


def _add(p, q):
    x1, y1 = p
    x2, y2 = q
    t = _D * x1 * x2 * y1 * y2
    return ((x1 * y2 + x2 * y1) * _inv(1 + t) % _P, (y1 * y2 + x1 * x2) * _inv(1 - t) % _P)


def _mul(p, e):
    q = (0, 1)
    while e:
        if e & 1:
            q = _add(q, p)
        p = _add(p, p)
        e >>= 1
    return q


def _decode_point(s: bytes):
    y = int.from_bytes(s, "little") & ((1 << 255) - 1)
    x = _xrecover(y)
    if x & 1 != (s[31] >> 7):
        x = _P - x
    pt = (x, y)
    if (-pt[0] * pt[0] + pt[1] * pt[1] - 1 - _D * pt[0] * pt[0] * pt[1] * pt[1]) % _P != 0:
        raise ValueError("point not on curve")
    return pt


def _pure_verify(pub: bytes, sig: bytes, msg: bytes) -> bool:
    try:
        if len(sig) != 64 or len(pub) != 32:
            return False
        R = _decode_point(sig[:32])
        A = _decode_point(pub)
        S = int.from_bytes(sig[32:], "little")
        if S >= _L:
            return False
        h = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little") % _L
        return _mul(_B, S) == _add(R, _mul(A, h))
    except Exception:  # noqa: BLE001
        return False


def _verify_ed25519(pub_hex: str, sig_hex: str, msg: bytes) -> bool:
    try:
        pub, sig = bytes.fromhex(pub_hex), bytes.fromhex(sig_hex)
    except ValueError:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        try:
            Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
            return True
        except InvalidSignature:
            return False
        except ValueError:
            return False
    except ImportError:
        return _pure_verify(pub, sig, msg)


# ------------------------------------------------------------------ ledger rules (must match backend/app/features/integrity.py)
def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def sha(data) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def block_hash(b) -> str:
    return sha(canon([DOMAIN, b["index"], b["ts"], b["event_type"], b.get("actor") or "", b.get("case_number") or "", b.get("ref") or "",
                      b["payload_sha256"], b["prev_hash"], b["key_id"]]))


def fp(pub_hex: str) -> str:
    return sha(bytes.fromhex(pub_hex))


def _leaf(h):
    return hashlib.sha256(b"\x00" + bytes.fromhex(h)).digest()


def _node(a, b):
    return hashlib.sha256(b"\x01" + a + b).digest()


def merkle_root(leaves):
    level = [_leaf(h) for h in leaves]
    while len(level) > 1:
        level = [_node(level[i], level[i + 1]) if i + 1 < len(level) else level[i] for i in range(0, len(level), 2)]
    return level[0].hex()


def merkle_check(leaf_hash, path, root) -> bool:
    cur = _leaf(leaf_hash)
    for side, sib in path:
        s = bytes.fromhex(sib)
        cur = _node(s, cur) if side == "L" else _node(cur, s)
    return cur.hex() == root


class Report:
    def __init__(self):
        self.problems: list[str] = []
        self.warnings: list[str] = []
        self.facts: list[str] = []

    def bad(self, msg):
        self.problems.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def ok(self, msg):
        self.facts.append(msg)


def verify_bundle(bundle: dict, expect_head: str | None = None, expect_index: int | None = None, trust_key: str | None = None) -> Report:
    R = Report()
    if bundle.get("format") != "dcn-ledger-bundle/1":
        R.bad(f"unknown bundle format {bundle.get('format')!r}")
        return R
    # ---- key chain: genesis key, then each rotation signed by the previous key
    keys: list[tuple[int, str, str]] = []   # (valid_after_block_index, key_id, public_hex); signs blocks with idx > valid_after
    cur_pub = cur_id = None
    prev_kc = None
    for kb in bundle.get("key_chain", []):
        i = kb.get("index")
        if sha(canon(kb.get("payload"))) != kb.get("payload_sha256"):
            R.bad(f"key-chain block {i}: payload does not match its hash")
        if block_hash(kb) != kb.get("block_hash"):
            R.bad(f"key-chain block {i}: block hash mismatch (edited)")
        pl = kb.get("payload") or {}
        if kb.get("event_type") == "genesis":
            if i != 0 or prev_kc is not None:
                R.bad("genesis block must be block 0 and first in the key chain")
            cur_pub, cur_id = pl.get("public_key"), None
            if not cur_pub or fp(cur_pub)[:16] != pl.get("key_id"):
                R.bad("genesis key id does not match its public key")
                continue
            cur_id = pl["key_id"]
            keys.append((-1, cur_id, cur_pub))
            if kb.get("key_id") != cur_id or not _verify_ed25519(cur_pub, kb["signature"], f"{DOMAIN}|block|".encode() + bytes.fromhex(kb["block_hash"])):
                R.bad("genesis block is not signed by its own key")
            genesis_fp = fp(cur_pub)
        elif kb.get("event_type") == "key_rotation":
            if cur_pub is None:
                R.bad("key rotation appears before any genesis block")
                continue
            if kb.get("key_id") != cur_id or not _verify_ed25519(cur_pub, kb["signature"], f"{DOMAIN}|block|".encode() + bytes.fromhex(kb["block_hash"])):
                R.bad(f"key rotation block {i} is not signed by the previous key (swapped or forged key)")
                continue
            newp = pl.get("new_public_key", "")
            pop_ok = _verify_ed25519(newp, pl.get("proof_of_possession", ""),
                                     f"{DOMAIN}|rotate|{cur_id}|{pl.get('new_key_id')}|{pl.get('prev_hash_at_rotation')}".encode()) if newp else False
            if pl.get("old_key_id") != cur_id or not newp or fp(newp)[:16] != pl.get("new_key_id") or not pop_ok or pl.get("prev_hash_at_rotation") != kb.get("prev_hash"):
                R.bad(f"key rotation block {i} is malformed (ids, proof of possession or link)")
                continue
            cur_pub, cur_id = newp, pl["new_key_id"]
            keys.append((i, cur_id, cur_pub))
        prev_kc = kb
    if not keys:
        R.bad("bundle has no valid genesis key")
        return R
    R.ok(f"key chain: {len(keys)} key(s); genesis key fingerprint {genesis_fp}")
    if trust_key:
        if trust_key.lower().replace(" ", "") == genesis_fp:
            R.ok("genesis key matches the fingerprint you pinned (--trust-key)")
        else:
            R.bad(f"genesis key fingerprint {genesis_fp} does not match the pinned {trust_key}: this ledger was not produced by your server (swapped key)")
    else:
        R.warn("no --trust-key given: the bundle is checked for self-consistency only, not against a key you pinned earlier")

    def key_for(idx):
        chosen = keys[0]
        for k in keys:
            if k[0] < idx:
                chosen = k
        return chosen

    def check_block(b, label) -> bool:
        ok = True
        i = b.get("index")
        if b.get("payload") is not None and sha(canon(b["payload"])) != b.get("payload_sha256"):
            R.bad(f"{label} {i}: payload was altered (hash mismatch)")
            ok = False
        if block_hash(b) != b.get("block_hash"):
            R.bad(f"{label} {i}: content does not match its block hash (edited)")
            ok = False
        _, kid, kpub = key_for(i)
        if b.get("key_id") != kid:
            R.bad(f"{label} {i}: signed with key {b.get('key_id')} but the key chain requires {kid} (swapped key)")
            ok = False
        elif not _verify_ed25519(kpub, b.get("signature", ""), f"{DOMAIN}|block|".encode() + bytes.fromhex(b.get("block_hash", "00"))):
            R.bad(f"{label} {i}: signature is invalid")
            ok = False
        return ok

    # ---- bundle signature
    bs = dict(bundle.get("bundle_signature") or {})
    body = {k: v for k, v in bundle.items() if k != "bundle_signature"}
    digest = sha(canon(body))
    kmatch = next((k for k in keys if k[1] == bs.get("key_id")), None)
    if bs.get("digest") != digest:
        R.bad("the bundle was modified after it was signed (content digest mismatch)")
    elif kmatch is None or not _verify_ed25519(kmatch[2], bs.get("signature", ""), f"{DOMAIN}|bundle|".encode() + bytes.fromhex(digest)):
        R.bad("the bundle signature is invalid or made by an unknown key")
    else:
        R.ok("bundle signature is valid")

    blocks = bundle.get("blocks", [])
    full = bool(bundle.get("scope", {}).get("full_chain"))
    good = 0
    prev = None
    for b in blocks:
        if check_block(b, "block"):
            good += 1
        if prev is not None:
            if b["index"] <= prev["index"]:
                R.bad(f"block {b['index']} appears after block {prev['index']}: blocks are re-ordered or duplicated")
            elif b["index"] == prev["index"] + 1:
                if b["prev_hash"] != prev["block_hash"]:
                    R.bad(f"block {b['index']} does not follow block {prev['index']}: the chain link is broken")
            elif full:
                R.bad(f"blocks {prev['index'] + 1}..{b['index'] - 1} are missing from a full-chain bundle (deleted)")
        elif full:
            if b["index"] != 0:
                R.bad(f"a full-chain bundle must start at block 0, but starts at {b['index']}")
            elif b["prev_hash"] != ZERO:
                R.bad("block 0 does not have the genesis previous-hash")
        prev = b
    R.ok(f"{good} of {len(blocks)} block(s) have a valid hash and signature")

    # ---- anchors and inclusion proofs
    anchors = {a["index"]: a for a in bundle.get("anchors", [])}
    for a in anchors.values():
        check_block(a, "anchor")
    by_index = {b["index"]: b for b in blocks}
    if full:
        for a in anchors.values():
            pl = a.get("payload") or {}
            leaves = [by_index[i]["block_hash"] for i in range(pl.get("from", 0), pl.get("to", -1) + 1)
                      if i in by_index and by_index[i]["event_type"] != "anchor"]
            expected_n = pl.get("count")
            if len(leaves) != expected_n or (leaves and merkle_root(leaves) != pl.get("merkle_root")):
                R.bad(f"anchor {a['index']}: the Merkle root does not match the blocks {pl.get('from')}..{pl.get('to')} (a block was changed or deleted)")
        R.ok(f"{len(anchors)} anchor(s) recomputed from the blocks")
    proved = 0
    proofs = {p["index"]: p for p in bundle.get("proofs", [])}
    for i, b in by_index.items():
        if b["event_type"] == "anchor":
            continue
        p = proofs.get(i)
        if p is None:
            (R.warn if not full else R.warn)(f"block {i}: no Merkle inclusion proof in the bundle (not anchored yet)")
            continue
        a = anchors.get(p["anchor_index"])
        if a is None:
            R.bad(f"block {i}: its anchor block {p['anchor_index']} is not in the bundle")
            continue
        pl = a.get("payload") or {}
        if not (pl.get("from", 1) <= i <= pl.get("to", 0)):
            R.bad(f"block {i}: anchor {a['index']} does not cover it")
        elif not merkle_check(b["block_hash"], p.get("path", []), pl.get("merkle_root", "")):
            R.bad(f"block {i}: Merkle inclusion proof does not match anchor {a['index']}")
        else:
            proved += 1
    R.ok(f"{proved} block(s) proven included in an anchored batch")

    # ---- head
    head = bundle.get("head", {})
    if expect_head:
        eh = expect_head.strip().lower()
        if any(b.get("block_hash") == eh for b in blocks) or (not full and eh == head.get("block_hash")):
            R.ok("the head hash you supplied is part of this chain")
        else:
            R.bad("the head hash you supplied is NOT in this chain: the newest blocks were removed, or the chain was rewritten "
                  "(or the bundle is older than your record and does not reach it)" if not full else
                  "the head hash you supplied is NOT in this chain: the chain was truncated or rewritten")
    if full and blocks and head.get("block_hash") != blocks[-1].get("block_hash"):
        R.bad("the head recorded in the bundle is not its newest block (blocks were removed from the end)")
    if expect_index is not None and head.get("chain_length", 0) <= expect_index:
        R.bad(f"the chain has {head.get('chain_length')} block(s) but you expected block {expect_index} to exist: newest blocks were removed")
    if not expect_head and expect_index is None:
        R.warn("no --expect-head given: removal of the newest blocks cannot be detected without a head hash recorded elsewhere")
    R.ok(f"bundle head: block {head.get('index')} {head.get('block_hash')}")
    return R


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify a ledger bundle offline.")
    ap.add_argument("bundle")
    ap.add_argument("--expect-head", help="head block hash you recorded earlier")
    ap.add_argument("--expect-index", type=int, help="a block index that must exist")
    ap.add_argument("--trust-key", help="genesis key fingerprint you pinned")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    try:
        with open(a.bundle, "r", encoding="utf-8") as fh:
            bundle = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"cannot read bundle: {exc}", file=sys.stderr)
        return 2
    try:
        R = verify_bundle(bundle, a.expect_head, a.expect_index, a.trust_key)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        R = Report()
        R.bad(f"bundle is malformed ({exc.__class__.__name__}: {exc})")
    verdict = "TAMPERING DETECTED" if R.problems else "VERIFIED"
    if a.json:
        print(json.dumps({"verdict": verdict, "problems": R.problems, "warnings": R.warnings, "facts": R.facts}, indent=1))
    else:
        for f in R.facts:
            print("  ok   ", f)
        for w in R.warnings:
            print("  note ", w)
        for p in R.problems:
            print("  FAIL ", p)
        print(f"\nRESULT: {verdict}" + ("" if R.problems else " (see the notes above for what this does not prove)"))
    return 1 if R.problems else 0


if __name__ == "__main__":
    sys.exit(main())
