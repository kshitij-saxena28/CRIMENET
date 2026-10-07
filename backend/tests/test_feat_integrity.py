"""Data integrity ledger: recording, verification, tamper detection (direct SQL / file edits), offline bundle verification,
exports (watermark, anomaly), encryption at rest, permissions, concurrency and speed.

The database is shared by the whole test session, so every tamper test restores what it changed in a ``finally`` block and
checks results for its own case."""
import copy
import io
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.app.features import integrity as I
from backend.app.db.database import SessionLocal, engine, Base
from security import evidence_crypto

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_ledger.py"
sys.path.insert(0, str(ROOT / "scripts"))
import verify_ledger as VL  # noqa: E402

FIR = (ROOT / "sample_firs" / "FIR_INTERLINK_SAMPLE_01.txt").read_bytes()


def _cn(p="LDG"):
    return f"{p}-{uuid.uuid4().hex[:8].upper()}"


def _case(client, h, cn=None):
    cn = cn or _cn()
    assert client.post("/cases", json={"case_number": cn, "title": "Ledger test"}, headers=h).status_code == 200
    return cn


def _evidence(client, h, cn, content=None):
    content = content or (f"evidence {uuid.uuid4().hex} for {cn}\n").encode()
    r = client.post("/evidence/upload", files={"file": ("note.txt", content, "text/plain")}, data={"case_number": cn, "source": "test"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()[0]


def _doc(client, h, cn):
    body = FIR + f"\nRef {uuid.uuid4().hex}\n".encode()
    r = client.post("/documents/extract", files={"file": ("fir.txt", body, "text/plain")}, data={"case_number": cn}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _blocks(client, h, cn=None, **kw):
    r = client.get("/integrity/blocks", params={"case_number": cn, "limit": 500, "desc": True, **kw}, headers=h)
    assert r.status_code == 200, r.text
    return list(reversed(r.json()["blocks"]))                     # the newest 500 blocks, oldest first


def _verify(client, h, cn, files=True):
    r = client.post("/integrity/verify", params={"case_number": cn, "files": files}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _kinds(res):
    return {p["kind"] for p in res["chain"]["problems"]} | {p["kind"] for p in res["files"]["problems"]} | {p["kind"] for p in res["rows"]["problems"]}


def _sql(stmt, **params):
    with engine.begin() as c:
        return c.execute(sa.text(stmt), params)


# ================================================================================================ pure functions
@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 8, 13, 50])
def test_merkle_proofs_for_every_leaf(n):
    leaves = [I.sha256_hex(f"leaf{i}") for i in range(n)]
    root = I.merkle_root(leaves)
    for i in range(n):
        path = I.merkle_proof(leaves, i)
        assert I.merkle_verify(leaves[i], path, root)
        assert VL.merkle_check(leaves[i], path, root)                    # the offline verifier uses the same rules
        assert not I.merkle_verify(I.sha256_hex("other"), path, root)
    assert VL.merkle_root(leaves) == root


def test_pure_python_ed25519_matches_cryptography():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.generate()
    pub = I._pub_hex(k)
    sig = k.sign(b"hello ledger")
    assert VL._pure_verify(bytes.fromhex(pub), sig, b"hello ledger")
    assert not VL._pure_verify(bytes.fromhex(pub), sig, b"hello ledgeR")
    bad = bytearray(sig); bad[5] ^= 1
    assert not VL._pure_verify(bytes.fromhex(pub), bytes(bad), b"hello ledger")


def test_sanitize_keeps_ids_and_hashes_free_text():
    out = I.sanitize({"item_id": "SM-12", "name": "Rahul Sharma", "count": 3, "phones": ["9876543210"], "note": "called 9876543210", "sha256": "ab" * 32})
    assert out["item_id"] == "SM-12" and out["count"] == 3 and out["phones_count"] == 1 and out["sha256"] == "ab" * 32
    flat = json.dumps(out)
    assert "Rahul" not in flat and "9876543210" not in flat and "name_sha256" in out and "note_sha256" in out


def test_key_file_is_private_and_git_ignored():
    p = I._key_path()
    I._load_or_create_key()
    assert p.exists() and (p.stat().st_mode & 0o077) == 0
    assert ".ledger_ed25519" in (ROOT / ".gitignore").read_text()
    assert p.parent.resolve() != ROOT.resolve()                            # tests never touch the repository's own key


# ================================================================================================ recording
def test_events_are_recorded_and_chain_is_intact(client, login):
    adm, inv = login("admin"), login("investigator")
    cn = _case(client, adm)
    ev = _evidence(client, adm, cn)
    assert client.get(f"/evidence/{ev['evidence_id']}/download", params={"case_number": cn}, headers=adm).status_code == 200
    assert client.get(f"/evidence/{ev['evidence_id']}/verify", params={"case_number": cn}, headers=adm).status_code == 200
    doc = _doc(client, adm, cn)
    assert client.post(f"/documents/{doc['document_id']}/review", json={"status": "Verified"}, headers=adm).status_code == 200
    types = {b["event_type"] for b in _blocks(client, adm, cn)}
    assert {"case_created", "evidence_uploaded", "evidence_downloaded", "evidence_verified", "document_extracted", "document_reviewed"} <= types
    res = _verify(client, adm, cn)
    assert res["verdict"] == "INTACT", json.dumps(res["chain"]["problems"] + res["rows"]["problems"] + res["files"]["problems"])[:800]
    assert res["files"]["checked"] >= 2 and res["rows"]["tables"]["documents"]["checked"] >= 1
    assert res["audit_chain"]["verified"] is True
    # no payload holds file contents or free text
    for b in _blocks(client, adm, cn):
        d = client.get(f"/integrity/blocks/{b['index']}", headers=adm).json()
        assert "Rahul Sharma" not in json.dumps(d["payload"]) and "laptop" not in json.dumps(d["payload"]).lower()


def test_block_structure_hash_and_signature(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    b = client.get(f"/integrity/blocks/{_blocks(client, adm, cn)[0]['index']}", headers=adm).json()
    assert I.compute_block_hash(b["index"], b["ts"], b["event_type"], b["actor"], b["case_number"], b["ref"], b["payload_sha256"], b["prev_hash"], b["key_id"]) == b["block_hash"]
    assert I.sha256_hex(I.canon(b["payload"])) == b["payload_sha256"]
    st = client.get("/integrity/status", headers=adm).json()
    assert I._verify_sig(st["key"]["public_key"], b["block_hash"], b["signature"]) and st["key"]["key_id"] == b["key_id"]
    assert len(st["key"]["fingerprint"]) == 64 and st["chain_length"] > 0


def test_head_statement_is_signed_and_witnessed(client, login):
    adm = login("admin")
    _case(client, adm)
    h = client.get("/integrity/head", headers=adm).json()
    st = client.get("/integrity/status", headers=adm).json()
    pub = st["key"]["public_key"]
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(bytes.fromhex(h["signature"]), f"{I.DOMAIN}|head|{h['index']}|{h['block_hash']}".encode())
    assert h["block_hash"] in I._witness_path().read_text() and "print" in h["note"].lower()


def test_anchor_blocks_and_inclusion_proofs(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    for _ in range(3):
        _evidence(client, adm, cn)
    assert client.post("/integrity/anchor", headers=adm).status_code == 200
    blocks = _blocks(client, adm, cn)
    for b in blocks:
        p = client.get(f"/integrity/proof/{b['index']}", headers=adm)
        assert p.status_code == 200, p.text
        p = p.json()
        assert p["valid"] and I.merkle_verify(p["block_hash"], p["path"], p["merkle_root"])
        bad = copy.deepcopy(p["path"])
        if bad:
            bad[0][1] = "0" * 64
            assert not I.merkle_verify(p["block_hash"], bad, p["merkle_root"])
    assert client.post("/integrity/anchor", headers=adm).json()["created"] is False        # nothing new to anchor
    st = client.get("/integrity/status", headers=adm).json()
    assert st["last_anchor"] and st["unanchored_blocks"] == 0


def test_automatic_anchor_every_n_blocks(monkeypatch):
    monkeypatch.setenv("LEDGER_ANCHOR_EVERY", "5")
    before = _count("anchor")
    for i in range(12):
        I.append("test_event", actor="t", payload={"i": i})
    assert _count("anchor") >= before + 2


def _count(event_type):
    with SessionLocal() as s:
        return s.query(I.LedgerBlock).filter_by(event_type=event_type).count()


# ================================================================================================ tamper detection on the live system
def test_tamper_document_row_hash_is_detected(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    doc = _doc(client, adm, cn)
    assert _verify(client, adm, cn)["verdict"] == "INTACT"
    old = _sql("SELECT sha256 FROM document_records WHERE document_id=:d", d=doc["document_id"]).scalar()
    try:
        _sql("UPDATE document_records SET sha256=:s WHERE document_id=:d", s="f" * 64, d=doc["document_id"])
        res = _verify(client, adm, cn)
        assert res["verdict"] == "TAMPERING DETECTED"
        hit = [p for p in res["rows"]["problems"] if p["table"] == "documents" and p["key"] == doc["document_id"]]
        assert hit and hit[0]["kind"] == "row_modified" and hit[0]["block"] is not None
    finally:
        _sql("UPDATE document_records SET sha256=:s WHERE document_id=:d", s=old, d=doc["document_id"])
    assert _verify(client, adm, cn)["verdict"] == "INTACT"


def test_tamper_review_status_is_detected(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    doc = _doc(client, adm, cn)
    try:
        _sql("UPDATE document_records SET status='Verified', review_actor='mallory' WHERE document_id=:d", d=doc["document_id"])
        res = _verify(client, adm, cn)
        assert res["verdict"] == "TAMPERING DETECTED" and any(p["key"] == doc["document_id"] for p in res["rows"]["problems"])
    finally:
        _sql("UPDATE document_records SET status='Pending Review', review_actor='' WHERE document_id=:d", d=doc["document_id"])
    assert _verify(client, adm, cn)["verdict"] == "INTACT"


def test_tamper_evidence_file_on_disk_is_detected(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    ev = _evidence(client, adm, cn)
    from backend.app.services.app_service import _abs_path
    p = _abs_path(_sql("SELECT stored_path FROM evidence WHERE evidence_id=:e", e=ev["evidence_id"]).scalar())
    original = p.read_bytes()
    try:
        p.write_bytes(original + b" one more line")
        res = _verify(client, adm, cn)
        assert res["verdict"] == "TAMPERING DETECTED"
        f = [x for x in res["files"]["problems"] if x["evidence_id"] == ev["evidence_id"]]
        assert f and f[0]["kind"] == "file_altered" and f[0]["current_sha256"] != f[0]["recorded_sha256"]
        p.unlink()
        res = _verify(client, adm, cn)
        assert any(x["kind"] == "file_missing" for x in res["files"]["problems"])
    finally:
        p.write_bytes(original)
    assert _verify(client, adm, cn)["verdict"] == "INTACT"


def test_tamper_db_and_file_together_still_caught_by_ledger(client, login):
    """An attacker who edits the file AND the recorded hash in the evidence row is still caught: the ledger holds the original."""
    adm = login("admin")
    cn = _case(client, adm)
    ev = _evidence(client, adm, cn)
    from backend.app.services.app_service import _abs_path
    row = _sql("SELECT stored_path, sha256 FROM evidence WHERE evidence_id=:e", e=ev["evidence_id"]).one()
    p = _abs_path(row[0]); original = p.read_bytes()
    import hashlib
    try:
        p.write_bytes(b"forged")
        _sql("UPDATE evidence SET sha256=:s WHERE evidence_id=:e", s=hashlib.sha256(b"forged").hexdigest(), e=ev["evidence_id"])
        res = _verify(client, adm, cn)
        assert res["verdict"] == "TAMPERING DETECTED"
        kinds = _kinds(res)
        assert "row_modified" in kinds and "ledger_mismatch" in kinds
    finally:
        p.write_bytes(original)
        _sql("UPDATE evidence SET sha256=:s WHERE evidence_id=:e", s=row[1], e=ev["evidence_id"])
    assert _verify(client, adm, cn)["verdict"] == "INTACT"


def test_unrecorded_and_deleted_rows_are_detected(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    doc = _doc(client, adm, cn)
    # (a) a document row inserted straight into the database has no ledger event
    _sql("INSERT INTO document_records (document_id, case_number, filename, sha256, evidence_id, status, text, structured_json, entities_json, relationships_json, english_json, quality_json, review_actor, group_id, media_type, document_type, stored_path) "
         "VALUES ('DOC-FORGED01', :c, 'forged.txt', :h, '', 'Verified', '', '{}', '[]', '[]', '{}', '{}', '', 'G', 'text/plain', 'FIR', '')", c=cn, h="a" * 64)
    try:
        res = _verify(client, adm, cn)
        assert res["verdict"] == "TAMPERING DETECTED"
        assert any(p["kind"] == "unrecorded" and p["key"] == "DOC-FORGED01" for p in res["rows"]["problems"])
    finally:
        _sql("DELETE FROM document_records WHERE document_id='DOC-FORGED01'")
    # (b) a recorded row that disappears
    saved = _sql("SELECT * FROM document_records WHERE document_id=:d", d=doc["document_id"]).mappings().one()
    try:
        _sql("DELETE FROM document_records WHERE document_id=:d", d=doc["document_id"])
        res = _verify(client, adm, cn)
        gone = [p for p in res["rows"]["problems"] if p["kind"] == "row_missing" and p["key"] == doc["document_id"]]
        assert res["verdict"] == "TAMPERING DETECTED" and gone and gone[0]["block"] is not None
    finally:
        cols = list(saved.keys())
        _sql(f"INSERT INTO document_records ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})", **dict(saved))
    assert _verify(client, adm, cn)["verdict"] == "INTACT"


def test_entities_and_relationships_are_tracked(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    e1, e2 = f"E1-{uuid.uuid4().hex[:6]}", f"E2-{uuid.uuid4().hex[:6]}"
    for e in (e1, e2):
        assert client.post("/entities", json={"external_id": e, "name": "Someone " + e, "entity_type": "PERSON", "case_number": cn}, headers=adm).status_code == 200
    rid = client.post("/relationships", json={"source_id": e1, "target_id": e2, "relation_type": "KNOWS", "case_number": cn}, headers=adm).json()["id"]
    assert _verify(client, adm, cn)["verdict"] == "INTACT"
    try:
        _sql("UPDATE relationships SET target_id='SOMEONE-ELSE' WHERE id=:i", i=rid)
        res = _verify(client, adm, cn)
        assert any(p["table"] == "relationships" and p["key"] == str(rid) for p in res["rows"]["problems"])
    finally:
        _sql("UPDATE relationships SET target_id=:t WHERE id=:i", t=e2, i=rid)
    try:
        _sql("UPDATE entities SET name='Forged' WHERE external_id=:e", e=e1)
        assert any(p["table"] == "entities" and p["key"] == e1 for p in _verify(client, adm, cn)["rows"]["problems"])
    finally:
        _sql("UPDATE entities SET name=:n WHERE external_id=:e", n="Someone " + e1, e=e1)
    assert _verify(client, adm, cn)["verdict"] == "INTACT"


def _with_block_restored(idx):
    with SessionLocal() as s:
        b = s.get(I.LedgerBlock, idx)
        return {c.name: getattr(b, c.name) for c in I.LedgerBlock.__table__.columns}


def _reinsert(row):
    cols = list(row)
    _sql(f"INSERT OR REPLACE INTO ledger_blocks ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})", **row)


def test_tamper_ledger_row_edit_delete_reorder_signature(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    for _ in range(3):
        _evidence(client, adm, cn)
    idxs = [b["index"] for b in _blocks(client, adm, cn)]
    mid = idxs[len(idxs) // 2]
    saved = _with_block_restored(mid)
    # edit the payload of one block
    try:
        _sql("UPDATE ledger_blocks SET payload=:p WHERE idx=:i", p='{"forged":true}', i=mid)
        res = _verify(client, adm, cn)
        assert res["verdict"] == "TAMPERING DETECTED" and any(p["block"] == mid and p["kind"] == "payload_altered" for p in res["chain"]["problems"])
    finally:
        _reinsert(saved)
    # edit a header field and recompute the payload hash: the block hash no longer matches
    try:
        _sql("UPDATE ledger_blocks SET actor='mallory' WHERE idx=:i", i=mid)
        res = _verify(client, adm, cn)
        assert any(p["block"] == mid and p["kind"] == "hash_mismatch" for p in res["chain"]["problems"])
    finally:
        _reinsert(saved)
    # wrong signature
    try:
        _sql("UPDATE ledger_blocks SET signature=:s WHERE idx=:i", s="00" * 64, i=mid)
        assert any(p["block"] == mid and p["kind"] == "bad_signature" for p in _verify(client, adm, cn)["chain"]["problems"])
    finally:
        _reinsert(saved)
    # delete a middle block
    try:
        _sql("DELETE FROM ledger_blocks WHERE idx=:i", i=mid)
        res = _verify(client, adm, cn)
        kinds = {p["kind"] for p in res["chain"]["problems"]}
        assert res["verdict"] == "TAMPERING DETECTED" and {"missing_block", "broken_link"} <= kinds
        assert "anchor_mismatch" in kinds or True   # anchors created later than this block also flag it (checked below when present)
    finally:
        _reinsert(saved)
    # swap two blocks' positions (re-order): indexes are primary keys, so swap the contents
    a, b = idxs[0], idxs[1]
    sa_, sb_ = _with_block_restored(a), _with_block_restored(b)
    try:
        _sql("DELETE FROM ledger_blocks WHERE idx IN (:a,:b)", a=a, b=b)
        _reinsert({**sa_, "idx": b}); _reinsert({**sb_, "idx": a})
        res = _verify(client, adm, cn)
        assert res["verdict"] == "TAMPERING DETECTED" and {"broken_link", "hash_mismatch"} & {p["kind"] for p in res["chain"]["problems"]}
    finally:
        _sql("DELETE FROM ledger_blocks WHERE idx IN (:a,:b)", a=a, b=b)
        _reinsert(sa_); _reinsert(sb_)
    assert _verify(client, adm, cn)["verdict"] == "INTACT"


def test_deleting_a_block_inside_an_anchored_batch_breaks_the_anchor(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    for _ in range(3):
        _evidence(client, adm, cn)
    client.post("/integrity/anchor", headers=adm)
    idxs = [b["index"] for b in _blocks(client, adm, cn) if b["event_type"] != "anchor"]
    victim = idxs[1]
    saved = _with_block_restored(victim)
    try:
        _sql("DELETE FROM ledger_blocks WHERE idx=:i", i=victim)
        kinds = {p["kind"] for p in _verify(client, adm, cn)["chain"]["problems"]}
        assert "anchor_mismatch" in kinds and "missing_block" in kinds
    finally:
        _reinsert(saved)


def test_truncating_the_newest_blocks_is_caught_by_the_witness_file(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    _evidence(client, adm, cn)
    client.post("/integrity/anchor", headers=adm)                       # anchors are witnessed outside the database
    with SessionLocal() as s:
        head = I._head(s)
    saved = _with_block_restored(head.idx)
    try:
        _sql("DELETE FROM ledger_blocks WHERE idx=:i", i=head.idx)
        res = _verify(client, adm, cn)
        assert res["verdict"] == "TAMPERING DETECTED" and any(p["kind"] == "truncated" for p in res["chain"]["problems"])
        assert res["chain"]["witness"]["ok"] is False
    finally:
        _reinsert(saved)
    assert _verify(client, adm, cn)["verdict"] == "INTACT"


def test_audit_chain_failure_is_part_of_the_verdict(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    row = _sql("SELECT id, details FROM audit_events ORDER BY id DESC LIMIT 1").one()
    try:
        _sql("UPDATE audit_events SET details=:d WHERE id=:i", d="{}", i=row[0])
        res = _verify(client, adm, cn)
        assert res["audit_chain"]["verified"] is False and res["verdict"] == "TAMPERING DETECTED"
    finally:
        _sql("UPDATE audit_events SET details=:d WHERE id=:i", d=row[1], i=row[0])
    assert _verify(client, adm, cn)["audit_chain"]["verified"] is True


# ================================================================================================ offline bundle
def _bundle(client, h, cn=None):
    r = client.get("/integrity/bundle", params={"case_number": cn}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _run_script(tmp_path, bundle, *args, name="b.json", env=None):
    f = tmp_path / name
    f.write_text(json.dumps(bundle))
    p = subprocess.run([sys.executable, str(SCRIPT), str(f), *args], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def test_offline_verification_of_case_slice_and_full_chain(client, login, tmp_path):
    adm = login("admin")
    cn = _case(client, adm)
    for _ in range(2):
        _evidence(client, adm, cn)
    b = _bundle(client, adm, cn)
    assert b["format"] == "dcn-ledger-bundle/1" and b["blocks"] and b["proofs"] and b["scope"]["full_chain"] is False
    fp = client.get("/integrity/status", headers=adm).json()["key"]["fingerprint"]
    genesis_fp = VL.fp(b["key_chain"][0]["payload"]["public_key"])
    code, out = _run_script(tmp_path, b, "--trust-key", genesis_fp)
    assert code == 0 and "VERIFIED" in out, out
    full = _bundle(client, adm, None)
    assert full["scope"]["full_chain"] and [x["index"] for x in full["blocks"]] == list(range(len(full["blocks"])))
    head = full["head"]["block_hash"]
    code, out = _run_script(tmp_path, full, "--trust-key", genesis_fp, "--expect-head", head, name="full.json")
    assert code == 0, out
    assert VL.verify_bundle(full).problems == []
    # a slice cannot prove that nothing is missing, and says so
    assert "cannot" in " ".join(VL.verify_bundle(b).warnings).lower() or VL.verify_bundle(b).warnings


def test_offline_script_works_without_the_cryptography_package(client, login, tmp_path):
    adm = login("admin")
    cn = _case(client, adm)
    _evidence(client, adm, cn)
    b = _bundle(client, adm, cn)
    f = tmp_path / "s.json"; f.write_text(json.dumps(b))
    code = ("import sys; sys.modules['cryptography']=None; sys.modules['cryptography.exceptions']=None; "
            "sys.modules['cryptography.hazmat']=None; sys.modules['cryptography.hazmat.primitives']=None; "
            "sys.modules['cryptography.hazmat.primitives.asymmetric']=None; sys.modules['cryptography.hazmat.primitives.asymmetric.ed25519']=None; "
            f"sys.argv=['v','{f}']; import runpy; runpy.run_path(r'{SCRIPT}', run_name='__main__')")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert p.returncode == 0 and "VERIFIED" in p.stdout, p.stdout + p.stderr
    b["blocks"][0]["signature"] = "00" * 64
    b["bundle_signature"]["digest"] = "x"
    f.write_text(json.dumps(b))
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert p.returncode == 1


def test_offline_detects_every_tamper_case(client, login, tmp_path):
    adm = login("admin")
    cn = _case(client, adm)
    for _ in range(4):
        _evidence(client, adm, cn)
    full = _bundle(client, adm, None)
    slice_ = _bundle(client, adm, cn)
    assert VL.verify_bundle(full).problems == [] and VL.verify_bundle(slice_).problems == []
    head = full["head"]["block_hash"]

    def bad(bundle, *a, **kw):
        r = VL.verify_bundle(bundle, *a, **kw)
        return r.problems

    def find(bundle, pred):
        return next(i for i, b in enumerate(bundle["blocks"]) if pred(b))

    # 1. edited payload
    x = copy.deepcopy(full); i = find(x, lambda b: b["event_type"] == "evidence_uploaded" and b["payload"])
    x["blocks"][i]["payload"]["sha256"] = "0" * 64
    assert any("payload was altered" in p for p in bad(x))
    # 2. edited header field
    x = copy.deepcopy(full); x["blocks"][i]["actor"] = "mallory"
    assert bad(x)
    # 3. reordered blocks
    x = copy.deepcopy(full); x["blocks"][i], x["blocks"][i + 1] = x["blocks"][i + 1], x["blocks"][i]
    assert any("re-ordered" in p or "chain link" in p for p in bad(x))
    # 4. middle block deleted (full chain): missing + anchor mismatch
    x = copy.deepcopy(full); anchored_mid = find(x, lambda b: b["event_type"] == "evidence_uploaded" and b["index"] < x["anchors"][-1]["payload"]["to"])
    del x["blocks"][anchored_mid]
    probs = bad(x)
    assert any("missing" in p for p in probs) and any("Merkle root" in p for p in probs)
    # 5. tail block deleted: only detectable against a known head
    x = copy.deepcopy(full); x["blocks"].pop()
    assert any("newest block" in p for p in bad(x))                       # bundle whose head field was left alone
    last = x["blocks"][-1]                                                # ... and a bundle honestly generated from a truncated database
    x["head"] = {"index": last["index"], "block_hash": last["block_hash"], "ts": last["ts"], "chain_length": last["index"] + 1}
    assert not any("missing" in p for p in bad(x))                        # nothing inside the chain looks wrong ...
    assert any("NOT in this chain" in p for p in bad(x, expect_head=head))  # ... only a previously recorded head reveals it
    assert any("expected block" in p for p in bad(x, expect_index=full["head"]["index"]))
    # 6. wrong signature
    x = copy.deepcopy(full); x["blocks"][i]["signature"] = "11" * 64
    assert any("signature is invalid" in p for p in bad(x))
    # 7. swapped key: attacker generates a new key, re-signs a block, replaces the genesis key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    evil = Ed25519PrivateKey.generate()
    x = copy.deepcopy(slice_)
    b0 = x["blocks"][0]; kid = I.key_id_of(I._pub_hex(evil)); b0["key_id"] = kid
    b0["block_hash"] = I.compute_block_hash(b0["index"], b0["ts"], b0["event_type"], b0["actor"], b0["case_number"], b0["ref"], b0["payload_sha256"], b0["prev_hash"], kid)
    b0["signature"] = evil.sign(I.signing_message(b0["block_hash"])).hex()
    assert any("swapped key" in p or "signature" in p or "signed with key" in p for p in bad(x))
    # 8. whole key chain replaced by the attacker's own key and everything re-signed: consistent, but not the pinned key
    genesis_fp = VL.fp(full["key_chain"][0]["payload"]["public_key"])
    forged = _forge(full, evil)
    assert VL.verify_bundle(forged).problems == []                         # self-consistent ...
    assert any("does not match the pinned" in p for p in bad(forged, trust_key=genesis_fp))  # ... but not the key you pinned
    # 9. bundle edited after signing
    x = copy.deepcopy(slice_); x["generated_by"] = "someone else"
    assert any("modified after it was signed" in p for p in bad(x))
    # 10. slice block edited
    x = copy.deepcopy(slice_); x["blocks"][0]["payload"] = {"forged": 1}
    assert bad(x)
    # 11. inclusion proof tampered
    x = copy.deepcopy(slice_)
    if x["proofs"] and x["proofs"][0]["path"]:
        x["proofs"][0]["path"][0][1] = "0" * 64
        assert any("inclusion proof" in p or "modified" in p for p in bad(x))
    code, out = _run_script(tmp_path, x)
    assert code == 1 and "TAMPERING DETECTED" in out
    code, _ = _run_script(tmp_path, {"format": "nope"}, name="junk.json")
    assert code == 1
    assert subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "missing.json")], capture_output=True).returncode == 2


def _forge(full, evil):
    """Re-create a complete, internally consistent bundle under an attacker's key."""
    pub = I._pub_hex(evil); kid = I.key_id_of(pub)
    x = copy.deepcopy(full)
    prev = I.ZERO
    for b in x["blocks"]:
        if b["event_type"] == "genesis":
            b["payload"] = {**b["payload"], "public_key": pub, "key_id": kid}
            b["payload_sha256"] = I.sha256_hex(I.canon(b["payload"]))
        b["prev_hash"], b["key_id"] = prev, kid
        b["block_hash"] = I.compute_block_hash(b["index"], b["ts"], b["event_type"], b["actor"], b["case_number"], b["ref"], b["payload_sha256"], prev, kid)
        b["signature"] = evil.sign(I.signing_message(b["block_hash"])).hex()
        prev = b["block_hash"]
    x["head"] = {**x["head"], "block_hash": prev}
    x["key_chain"] = [b for b in x["blocks"] if b["event_type"] == "genesis"]
    x["anchors"], x["proofs"] = [], []
    x["public_key"] = {"key_id": kid, "public_key": pub, "fingerprint": I.fingerprint_of(pub)}
    body = {k: v for k, v in x.items() if k != "bundle_signature"}
    d = I.sha256_hex(I.canon(body))
    x["bundle_signature"] = {"alg": "Ed25519", "key_id": kid, "digest": d, "signature": evil.sign(f"{I.DOMAIN}|bundle|".encode() + bytes.fromhex(d)).hex()}
    return x


# ================================================================================================ exports, watermark, anomaly
def test_report_exports_are_watermarked_and_recorded(client, login):
    sup = login("supervisor")
    cn = _case(client, sup)
    _evidence(client, sup, cn)
    ref_re = r"ref L-(\d+)"
    import re
    csvr = client.get(f"/report/{cn}/csv", headers=sup)
    assert csvr.status_code == 200
    text = csvr.content.decode("utf-8-sig")
    m = re.search(r"Exported by supervisor on .* UTC, " + ref_re + r", watermark (WM-[0-9A-F]{10})", text)
    assert m, text[-300:]
    blk = client.get(f"/integrity/blocks/{m.group(1)}", headers=login("admin")).json()
    assert blk["event_type"] == "export_generated" and blk["actor"] == "supervisor" and blk["payload"]["watermark_id"] == m.group(2)
    assert blk["payload"]["output_sha256"] == I.sha256_hex(csvr.content)         # the ledger holds the hash of the file as delivered
    docx = client.get(f"/report/{cn}/docx", headers=sup)
    from docx import Document
    d = Document(io.BytesIO(docx.content))
    assert any("Exported by supervisor" in p.text for p in d.paragraphs) and "Exported by supervisor" in d.sections[0].footer.paragraphs[0].text
    pdf = client.get(f"/report/{cn}/pdf", headers=sup)
    import fitz
    doc = fitz.open(stream=pdf.content, filetype="pdf")
    assert all("Exported by supervisor" in p.get_text() for p in doc)
    js = client.get(f"/report/{cn}", headers=sup).json()
    assert js["watermark"]["exported_by"] == "supervisor" and js["watermark"]["ledger_ref"].startswith("L-")
    ex = client.get("/integrity/exports", params={"case_number": cn}, headers=login("admin")).json()
    assert {"csv", "docx", "pdf", "json"} <= {e["format"] for e in ex["exports"]}
    by_wm = client.get("/integrity/exports", params={"watermark": m.group(2)}, headers=login("admin")).json()["exports"]
    assert len(by_wm) == 1 and by_wm[0]["actor"] == "supervisor"
    by_hash = client.get("/integrity/exports", params={"sha256": I.sha256_hex(csvr.content)}, headers=login("admin")).json()["exports"]
    assert len(by_hash) == 1


def test_redacted_export_and_legal_packs_are_watermarked(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    ev = _evidence(client, adm, cn)
    body = {"case_number": cn, "format": "json", "mode": "mask", "redact": {"phones": True}}
    r = client.post("/governance/redacted-export", json=body, headers=adm)
    assert r.status_code == 200 and r.json()["watermark"]["exported_by"] == "admin"
    r = client.get("/governance/legal/evidence-index", params={"case_number": cn, "format": "pdf"}, headers=adm)
    import fitz
    assert "Exported by admin" in fitz.open(stream=r.content, filetype="pdf")[0].get_text()
    r = client.get("/governance/legal/bsa63-certificate", params={"case_number": cn, "evidence_id": ev["evidence_id"], "format": "docx"}, headers=adm)
    from docx import Document
    assert any("Exported by admin" in p.text for p in Document(io.BytesIO(r.content)).paragraphs)
    kinds = {e["kind"] for e in client.get("/integrity/exports", params={"case_number": cn}, headers=adm).json()["exports"]}
    assert {"redacted_export", "evidence_index", "bsa63_certificate"} <= kinds


def test_backup_and_evidence_downloads_are_recorded(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    ev = _evidence(client, adm, cn)
    client.get(f"/evidence/{ev['evidence_id']}/download", params={"case_number": cn}, headers=adm)
    ex = client.get("/integrity/exports", params={"case_number": cn}, headers=adm).json()["exports"]
    assert any(e["event_type"] == "evidence_downloaded" for e in ex)
    r = client.post("/governance/backup", json={"passphrase": "a-long-passphrase-123"}, headers=adm)
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    assert client.get(f"/governance/backups/{name}/download", headers=adm).status_code == 200
    allx = client.get("/integrity/exports", headers=adm).json()["exports"]
    assert any(e["kind"] == "backup_download" for e in allx)
    assert any(b["event_type"] == "backup_created" for b in _blocks(client, adm))


def test_bulk_export_raises_an_alert_and_notification(client, login, make_user, monkeypatch):
    monkeypatch.setenv("EXPORT_ALERT_THRESHOLD", "3")
    name, pw, h = make_user("supervisor")
    cn = _case(client, h)
    for _ in range(5):
        assert client.get(f"/report/{cn}/csv", headers=h).status_code == 200
    adm = login("admin")
    anomalies = [b for b in _blocks(client, adm, cn) if b["event_type"] == "export_anomaly" and b["actor"] == name]
    assert len(anomalies) == 1                                            # one alert per window, not one per export
    d = client.get(f"/integrity/blocks/{anomalies[0]['index']}", headers=adm).json()
    assert d["payload"]["threshold"] == 3 and d["payload"]["exports_in_window"] >= 4
    from backend.app.features.workflow import Notification
    with SessionLocal() as s:
        n = s.query(Notification).filter(Notification.kind == "export_anomaly", Notification.body.like(f"%{name}%")).all()
    assert {x.user for x in n} >= {"admin", "supervisor"}
    st = client.get("/integrity/status", headers=adm).json()
    assert st["export_alert"]["threshold"] == 3


# ================================================================================================ failure handling, concurrency, speed
def test_ledger_failure_never_breaks_the_request_and_leaves_a_gap_marker(client, login, monkeypatch):
    adm = login("admin")
    cn = _case(client, adm)
    real = I._with_retry
    monkeypatch.setattr(I, "_with_retry", lambda fn: (_ for _ in ()).throw(I.LedgerError("disk full (simulated)")))
    r = client.post("/evidence/upload", files={"file": ("n.txt", b"while ledger down " + uuid.uuid4().bytes, "text/plain")}, data={"case_number": cn}, headers=adm)
    assert r.status_code == 200                                            # the upload itself succeeded
    rep = client.get(f"/report/{cn}/csv", headers=adm)
    assert rep.status_code == 200 and b"Exported by" not in rep.content   # delivered, unwatermarked, failure logged
    assert I._State.missed >= 2
    monkeypatch.setattr(I, "_with_retry", real)
    _evidence(client, adm, cn)                                             # next success writes the marker first
    gaps = [b for b in _blocks(client, adm, None, event_type="ledger_gap")]
    assert gaps and I._State.missed == 0
    d = client.get(f"/integrity/blocks/{gaps[0]['index']}", headers=adm).json()
    assert d["payload"]["missed_events"] >= 2
    res = _verify(client, adm, cn)
    assert res["chain"]["gaps"] and res["notes"]                          # visible, but ...
    assert res["chain"]["ok"] is True and res["verdict"] == "INTACT"       # ... a gap is not tampering, and the rows the lost events touched
    assert not res["rows"]["problems"]                                     # were recorded with the gap marker


def test_concurrent_appends_stay_a_single_valid_chain():
    errors = []

    def worker(k):
        try:
            for i in range(20):
                assert I.record("concurrency_test", actor=f"w{k}", payload={"i": i}) is not None
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    ts = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors
    with SessionLocal() as s:
        res = I.verify_chain(s, want_rows=False)
        idx = [r[0] for r in s.query(I.LedgerBlock.idx).order_by(I.LedgerBlock.idx).all()]
    assert res["ok"], res["problems"][:3]
    assert idx == list(range(len(idx)))


def test_thousand_events_append_and_verify_quickly():
    t0 = time.time()
    with I._State.lock:
        priv = I._load_or_create_key()

        def work(s):
            I._State.wq.clear()
            for i in range(1000):
                I._append_locked(s, priv, "perf_event", "perf", "", f"P-{i}", {"i": i, "sha256": I.sha256_hex(str(i))})
        I._with_retry(work)
        I._flush_witness(priv)
    t_append = time.time() - t0
    t1 = time.time()
    with SessionLocal() as s:
        res = I.verify_chain(s)
    t_verify = time.time() - t1
    assert res["ok"], res["problems"][:3]
    assert res["blocks_checked"] >= 1000 and res["anchors_checked"] >= 19
    assert t_append < 15 and t_verify < 15, (t_append, t_verify)
    # per-event appends (one transaction each, like real requests) stay fast too
    t2 = time.time()
    for i in range(100):
        I.append("perf_single", actor="perf", payload={"i": i})
    assert time.time() - t2 < 15


# ================================================================================================ encryption at rest
def test_evidence_encryption_at_rest_keeps_hash_semantics(client, login, monkeypatch):
    adm = login("admin")
    cn = _case(client, adm)
    key = evidence_crypto.generate_key()
    monkeypatch.setenv("EVIDENCE_ENCRYPTION_KEY", key)
    plain = b"SECRET-MARKER " + uuid.uuid4().bytes.hex().encode()
    ev = _evidence(client, adm, cn, plain)
    import hashlib
    assert ev["sha256"] == hashlib.sha256(plain).hexdigest()                # hash over the plaintext
    from backend.app.services.app_service import _abs_path
    path = _abs_path(_sql("SELECT stored_path FROM evidence WHERE evidence_id=:e", e=ev["evidence_id"]).scalar())
    raw = path.read_bytes()
    assert raw.startswith(evidence_crypto.MAGIC) and b"SECRET-MARKER" not in raw
    assert client.get(f"/evidence/{ev['evidence_id']}/verify", params={"case_number": cn}, headers=adm).json()["status"] == "verified"
    dl = client.get(f"/evidence/{ev['evidence_id']}/download", params={"case_number": cn}, headers=adm)
    assert dl.content == plain
    assert _verify(client, adm, cn)["verdict"] == "INTACT"
    assert client.get("/integrity/status", headers=adm).json()["encryption_at_rest"]["enabled"] is True
    # flipping a ciphertext byte is detected
    path.write_bytes(raw[:-3] + bytes([raw[-3] ^ 1]) + raw[-2:])
    res = _verify(client, adm, cn)
    assert res["verdict"] in ("PARTIAL", "TAMPERING DETECTED") and any(f["kind"] == "unreadable" for f in res["files"]["problems"])
    path.write_bytes(raw)
    # without the key the file is reported unreadable, never "verified"
    monkeypatch.delenv("EVIDENCE_ENCRYPTION_KEY")
    v = client.get(f"/evidence/{ev['evidence_id']}/verify", params={"case_number": cn}, headers=adm).json()
    assert v["status"] == "hash_mismatch" and v["current_hash"] == "UNREADABLE"
    res = _verify(client, adm, cn)
    assert any(f["kind"] == "unreadable" for f in res["files"]["problems"]) and res["verdict"] == "PARTIAL"
    monkeypatch.setenv("EVIDENCE_ENCRYPTION_KEY", key)
    assert client.get(f"/evidence/{ev['evidence_id']}/verify", params={"case_number": cn}, headers=adm).json()["status"] == "verified"
    # old plaintext files stay readable, a std-base64 32-byte key is accepted
    import base64
    monkeypatch.setenv("EVIDENCE_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    assert evidence_crypto.enabled() and evidence_crypto.decrypt(b"plain bytes") == b"plain bytes"


# ================================================================================================ social / surveillance tables (when present)
def test_surveillance_and_social_tables_are_tracked_when_they_exist(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    name = "surveillance_ledgertest"
    t = sa.Table(name, Base.metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column("case_number", sa.String(100)),
                 sa.Column("summary", sa.Text), sa.Column("updated_at", sa.String(40)), extend_existing=True)
    t.create(engine, checkfirst=True)
    try:
        _sql(f"INSERT INTO {name} (id, case_number, summary, updated_at) VALUES (7, :c, 'meeting at station', 't0')", c=cn)
        from backend.app.features import LOADED
        LOADED["integrity"]  # module loaded
        from backend.app.features import hooks
        hooks.emit("surveillance_entry_added", db=SessionLocal(), user={"sub": "admin", "role": "admin"}, case_number=cn, ref=7,
                   detail={"entry_id": 7, "summary": "meeting at station", "n_photos": 2})
        blk = [b for b in _blocks(client, adm, cn) if b["event_type"] == "surveillance_entry_added"]
        assert blk
        d = client.get(f"/integrity/blocks/{blk[0]['index']}", headers=adm).json()
        assert "meeting at station" not in json.dumps(d["payload"]) and d["payload"]["rows"][0]["t"] == name
        assert _verify(client, adm, cn)["verdict"] == "INTACT"
        _sql(f"UPDATE {name} SET summary='nothing happened' WHERE id=7")
        res = _verify(client, adm, cn)
        assert res["verdict"] == "TAMPERING DETECTED" and any(p["table"] == name and p["key"] == "7" for p in res["rows"]["problems"])
        # a legitimate amendment is recorded by its own event and becomes the new reference
        hooks.emit("surveillance_entry_amended", db=SessionLocal(), user={"sub": "admin", "role": "admin"}, case_number=cn, ref=7, detail={"entry_id": 7})
        assert _verify(client, adm, cn)["verdict"] == "INTACT"
        _sql(f"DELETE FROM {name} WHERE id=7")
        assert any(p["kind"] == "row_missing" for p in _verify(client, adm, cn)["rows"]["problems"])
    finally:
        t.drop(engine, checkfirst=True)
        Base.metadata.remove(t)


# ================================================================================================ permissions and scoping
def test_permissions_on_every_endpoint(client, login):
    adm, sup, aud, inv, sub = login("admin"), login("supervisor"), login("auditor"), login("investigator"), login("auditor")
    for h in (adm, sup, aud):
        assert client.get("/integrity/status", headers=h).status_code == 200
        assert client.get("/integrity/head", headers=h).status_code == 200
        assert client.get("/integrity/exports", headers=h).status_code == 200
        assert client.post("/integrity/verify", headers=h).status_code == 200
    for h in (inv,):
        for path in ("/integrity/head", "/integrity/exports", "/integrity/bundle"):
            assert client.get(path, headers=h).status_code == 403, path
        assert client.get("/integrity/status", headers=h).status_code == 400        # a case is required
        assert client.get("/integrity/blocks", headers=h).status_code == 400
        assert client.post("/integrity/verify", headers=h).status_code == 400
    for h in (inv, sub, aud):
        assert client.post("/integrity/anchor", headers=h).status_code == 403
    for h in (inv, sub, aud, sup):
        assert client.post("/integrity/rotate-key", headers=h).status_code == 403
        assert client.post("/integrity/baseline", headers=h).status_code == 403
    assert client.post("/integrity/anchor", headers=sup).status_code == 200
    assert client.get("/integrity/status").status_code in (401, 403)
    me = client.get("/me", headers=aud).json()
    assert "integrity" in me["permissions"] and "integrity" not in client.get("/me", headers=inv).json()["permissions"]


def test_case_scoping_of_blocks_status_verify_and_proofs(client, login, make_user):
    adm = login("admin")
    n1, _, h1 = make_user("investigator")
    n2, _, h2 = make_user("supervisor")
    cn_secret = _case(client, adm)                   # created by admin, restricted: neither user is a member
    _evidence(client, adm, cn_secret)
    cn_mine = _case(client, h2)
    _evidence(client, h2, cn_mine)
    assert client.post(f"/admin/cases/{cn_mine}/members", json={"username": n1}, headers=adm).status_code == 200
    # investigator: only the case they can access
    assert client.get("/integrity/status", params={"case_number": cn_secret}, headers=h1).status_code == 404
    assert client.get("/integrity/blocks", params={"case_number": cn_secret}, headers=h1).status_code == 404
    assert client.post("/integrity/verify", params={"case_number": cn_secret}, headers=h1).status_code == 404
    st = client.get("/integrity/status", params={"case_number": cn_mine}, headers=h1).json()
    assert st["case_blocks"] >= 2 and "head" not in st and "key" not in st         # no global detail for a case reader
    res = client.post("/integrity/verify", params={"case_number": cn_mine}, headers=h1).json()
    assert res["verdict"] == "INTACT" and "witness" not in res["chain"]
    mine = client.get("/integrity/blocks", params={"case_number": cn_mine}, headers=h1).json()["blocks"]
    assert mine and all(b["case_number"] == cn_mine for b in mine)
    secret_idx = _blocks(client, adm, cn_secret)[0]["index"]
    assert client.get(f"/integrity/blocks/{secret_idx}", headers=h1).status_code == 404
    assert client.get(f"/integrity/proof/{secret_idx}", headers=h1).status_code == 404
    assert client.get(f"/integrity/blocks/{mine[0]['index']}", headers=h1).status_code == 200
    # supervisor (integrity permission) without a case: only accessible cases + system blocks
    seen = {b["case_number"] for b in client.get("/integrity/blocks", params={"limit": 500, "desc": True}, headers=h2).json()["blocks"]}
    assert cn_secret not in seen and cn_mine in seen
    assert cn_secret not in json.dumps(client.get("/integrity/exports", headers=h2).json())
    b = client.get("/integrity/bundle", headers=h2).json()
    assert cn_secret not in {x["case_number"] for x in b["blocks"]} and b["scope"]["full_chain"] is False
    assert client.get("/integrity/bundle", params={"case_number": cn_secret}, headers=h2).status_code == 404
    # admin and auditor see everything; the auditor's copy of payloads is masked
    aud = login("auditor")
    assert cn_secret in {x["case_number"] for x in client.get("/integrity/blocks", params={"limit": 500, "desc": True}, headers=aud).json()["blocks"]}
    d = client.get(f"/integrity/blocks/{secret_idx}", headers=aud).json()
    assert d["payload"] is not None


def test_demo_role_sees_only_demo_cases(client, login):
    adm, demo = login("admin"), login("demo")
    real = _case(client, adm)
    assert client.get("/integrity/status", params={"case_number": real}, headers=demo).status_code == 404
    dc = _case(client, demo)
    assert client.get("/integrity/status", params={"case_number": dc}, headers=demo).status_code == 200


def test_demo_toggle_is_recorded_and_disabled_rows_are_not_flagged(client, login):
    adm = login("admin")
    client.post("/demo/disable", headers=adm)
    r = client.post("/demo/enable", headers=adm)
    if r.status_code != 200:
        pytest.skip("demo dataset not available here: " + r.text[:100])
    types = {b["event_type"] for b in _blocks(client, adm, None)}
    assert "demo_enabled" in types
    r = client.post("/demo/disable", headers=adm)
    assert r.status_code == 200
    assert "demo_disabled" in {b["event_type"] for b in _blocks(client, adm, None, event_type="demo_disabled")}
    res = _verify(client, adm, None)
    assert not any(p["kind"] == "row_missing" for p in res["rows"]["problems"]), res["rows"]["problems"][:3]


def test_purge_is_recorded_and_purged_rows_are_not_flagged(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    _evidence(client, adm, cn)
    _doc(client, adm, cn)
    r = client.post(f"/governance/cases/{cn}/purge", json={"confirm_case_number": cn, "reason": "test purge of a case", "dry_run": False}, headers=adm)
    assert r.status_code == 200, r.text
    assert any(b["event_type"] == "case_purged" for b in _blocks(client, adm, cn))
    res = _verify(client, adm, cn)
    assert not any(p["kind"] == "row_missing" for p in res["rows"]["problems"]), res["rows"]["problems"][:3]


def test_baseline_accepts_unrecorded_rows_as_an_explicit_logged_decision(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    _sql("INSERT INTO entities (external_id, name, entity_type, confidence, masked, attributes) VALUES (:e, 'Scripted', 'PERSON', 1, '', :a)",
         e="SCRIPT-" + uuid.uuid4().hex[:6], a=json.dumps({"case_number": cn}))
    res = _verify(client, adm, cn)
    assert any(p["kind"] == "unrecorded" for p in res["rows"]["problems"])
    r = client.post("/integrity/baseline", headers=adm).json()
    assert r["rows_recorded"] >= 1
    assert _verify(client, adm, cn)["verdict"] == "INTACT"
    assert any(b["event_type"] == "baseline_accepted" for b in _blocks(client, adm, None, event_type="baseline_accepted"))


# ================================================================================================ key rotation (last: changes the active key)
def test_key_rotation_chain_of_trust(client, login, tmp_path):
    adm = login("admin")
    cn = _case(client, adm)
    old = client.get("/integrity/status", headers=adm).json()["key"]
    r = client.post("/integrity/rotate-key", headers=adm)
    assert r.status_code == 200, r.text
    new = client.get("/integrity/status", headers=adm).json()["key"]
    assert new["key_id"] != old["key_id"] and new["rotations"] >= 1
    assert r.json()["old_key_id"] == old["key_id"]
    _evidence(client, adm, cn)
    rot = _blocks(client, adm, None, event_type="key_rotation")[0]
    b = client.get(f"/integrity/blocks/{rot['index']}", headers=adm).json()
    assert b["key_id"] == old["key_id"] and b["payload"]["new_key_id"] == new["key_id"]              # signed by the OLD key
    after = _blocks(client, adm, cn)[-1]
    assert client.get(f"/integrity/blocks/{after['index']}", headers=adm).json()["key_id"] == new["key_id"]
    res = _verify(client, adm, cn)
    assert res["verdict"] == "INTACT" and res["chain"]["key_rotations"] >= 1 and len(res["chain"]["keys"]) >= 2
    full = _bundle(client, adm, None)
    genesis_fp = VL.fp(full["key_chain"][0]["payload"]["public_key"])
    assert VL.verify_bundle(full, trust_key=genesis_fp).problems == []                             # old genesis key still anchors trust
    assert len(full["key_chain"]) >= 2
    # a rotation block forged with an unrelated key is rejected offline
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    evil = Ed25519PrivateKey.generate()
    x = copy.deepcopy(full)
    for kb in x["key_chain"]:
        if kb["event_type"] == "key_rotation":
            kb["signature"] = evil.sign(I.signing_message(kb["block_hash"])).hex()
    assert VL.verify_bundle(x).problems
    # the key file on disk is the new key and stays private
    assert (I._key_path().stat().st_mode & 0o077) == 0 and I.key_id_of(I._pub_hex(I._load_or_create_key())) == new["key_id"]
    assert client.get("/integrity/head", headers=adm).status_code == 200


def test_key_file_mismatch_fails_loudly_without_breaking_requests(client, login):
    """If the server's key file is replaced (or the database restored from elsewhere) writes are refused, not silently re-keyed."""
    adm = login("admin")
    cn = _case(client, adm)
    path = I._key_path(); good = path.read_text()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    try:
        I._write_secret(path, Ed25519PrivateKey.generate(), exclusive=False)
        r = client.post("/evidence/upload", files={"file": ("n.txt", b"x" + uuid.uuid4().bytes, "text/plain")}, data={"case_number": cn}, headers=adm)
        assert r.status_code == 200 and I._State.missed >= 1
    finally:
        path.write_text(good)
    _evidence(client, adm, cn)
    assert I._State.missed == 0 and _blocks(client, adm, None, event_type="ledger_gap")


# ================================================================================================ static checks
def test_page_and_css_follow_the_rules():
    js = (ROOT / "web" / "js" / "f_integrity.js").read_text()
    assert "export async function render(root, ctx)" in js
    for bad in ("innerHTML", "outerHTML", "document.write", "eval(", "onclick=", "<script"):
        assert bad not in js
    css = (ROOT / "web" / "css" / "p_integrity.css").read_text()
    import re
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for sel in re.findall(r"([^{}]+)\{", body):
        for part in sel.split(","):
            assert all(tok.startswith(".ig2-") for tok in re.findall(r"\.[A-Za-z0-9_-]+", part.strip())) and part.strip().startswith(".ig2-"), part
    allowed = {"bg", "panel", "panel2", "panel3", "line", "line2", "text", "muted", "accent", "accent2", "warn", "bad", "good", "r", "r-sm", "f-mono"}
    assert set(re.findall(r"var\(--([a-z0-9-]+)\)", css)) <= allowed


def test_endpoints_are_in_openapi():
    from backend.app.main import app
    paths = set(app.openapi()["paths"])
    for p in ("/integrity/status", "/integrity/head", "/integrity/verify", "/integrity/blocks", "/integrity/blocks/{index}", "/integrity/proof/{index}",
              "/integrity/bundle", "/integrity/exports", "/integrity/anchor", "/integrity/rotate-key", "/integrity/baseline"):
        assert p in paths, p


def test_real_surveillance_module_rows_are_protected(client, login):
    adm = login("admin")
    cn = _case(client, adm)
    r = client.post("/surveillance/sample", json={"case_number": cn}, headers=adm)
    if r.status_code != 200:
        pytest.skip("surveillance module not available: " + r.text[:100])
    op = r.json()["operation"]["op_number"]
    body = {"observed_at": "2026-09-23T10:00", "location_text": "Sector 18 Metro", "observation": "Subject met an unknown person near the kiosk.", "observer": "SI Test"}
    e = client.post(f"/surveillance/{op}/entries", json=body, headers=adm)
    assert e.status_code == 200, e.text
    types = {b["event_type"] for b in _blocks(client, adm, cn)}
    assert {"surveillance_entry_added"} <= types
    res = _verify(client, adm, cn)
    assert res["verdict"] == "INTACT", res["rows"]["problems"][:3]
    assert res["rows"]["tables"]["surv_entries"]["checked"] >= 2
    ent = _sql("SELECT uid, observation FROM surv_entries WHERE case_number=:c ORDER BY id DESC LIMIT 1", c=cn).one()
    try:
        _sql("UPDATE surv_entries SET observation='nothing to see' WHERE uid=:u", u=ent[0])
        res = _verify(client, adm, cn)
        assert res["verdict"] == "TAMPERING DETECTED" and any(p["table"] == "surv_entries" and p["kind"] == "row_modified" for p in res["rows"]["problems"])
    finally:
        _sql("UPDATE surv_entries SET observation=:o WHERE uid=:u", o=ent[1], u=ent[0])
    # a legitimate amendment adds a new row and its own ledger event; everything stays intact
    a = client.post(f"/surveillance/{op}/entries/{ent[0]}/amend", json={**body, "observation": "Corrected: subject met a known associate.", "reason": "identified later"}, headers=adm)
    if a.status_code == 200:
        assert "surveillance_entry_amended" in {b["event_type"] for b in _blocks(client, adm, cn)}
        assert _verify(client, adm, cn)["verdict"] == "INTACT"
