#!/usr/bin/env python3
"""Offline restore of a DARK CRIMENET encrypted backup (*.dcnbak) into a NEW directory.

The running server deliberately has no restore endpoint. This tool:
  * decrypts the backup (Fernet key derived with scrypt from your passphrase + the salt in the file header),
  * verifies every file against the SHA-256 manifest BEFORE writing anything,
  * refuses to write into a non-empty target directory (it never overwrites anything),
  * writes  <target>/database.sqlite  and  <target>/evidence/...,
  * by default rewrites the restored copy's evidence file paths to point at <target>/evidence (the audit chain is untouched),
  * optionally re-verifies the hash-chained audit log when the original server secret is supplied (env SECRET_KEY).

Usage:
  python scripts/restore_backup.py BACKUP.dcnbak --target /path/to/new_dir [--passphrase-env DCN_BACKUP_PASSPHRASE]
                                   [--keep-paths] [--verify-only]
Then start the server with:
  DATABASE_URL=sqlite:////path/to/new_dir/database.sqlite EVIDENCE_DIR=/path/to/new_dir/evidence SECRET_KEY=<original secret> ...

Requires only the 'cryptography' package. Exit code 0 = restored and verified, non-zero = refused / failed.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import io
import json
import os
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

MAGIC = b"DCNBAK1\n"


def fail(msg: str, code: int = 2):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def decrypt(blob: bytes, passphrase: str) -> bytes:
    from cryptography.fernet import Fernet, InvalidToken
    if not blob.startswith(MAGIC):
        fail("not a DARK CRIMENET backup file")
    nl = blob.find(b"\n", len(MAGIC))
    if nl < 0 or nl > 2048:
        fail("backup header is corrupt")
    try:
        header = json.loads(blob[len(MAGIC):nl])
        salt = base64.b64decode(header["salt"])
        n, r, p = int(header["n"]), int(header["r"]), int(header["p"])
    except Exception:  # noqa: BLE001
        fail("backup header is corrupt")
    if header.get("kdf") != "scrypt" or n < 2 ** 12 or n > 2 ** 20 or n & (n - 1) or not 1 <= r <= 16 or not 1 <= p <= 4 or len(salt) < 8:
        fail("unsupported or unsafe key-derivation parameters in header")
    key = base64.urlsafe_b64encode(hashlib.scrypt(passphrase.encode(), salt=salt, n=n, r=r, p=p, maxmem=256 * 1024 * 1024, dklen=32))
    try:
        return Fernet(key).decrypt(blob[nl + 1:])
    except InvalidToken:
        fail("decryption failed: wrong passphrase, or the file was modified/corrupted")


def safe(name: str) -> bool:
    parts = Path(name.replace("\\", "/")).parts
    return bool(name) and not name.startswith(("/", "\\")) and ".." not in parts and ":" not in name


def chain_ok(db_path: Path, secret: str) -> tuple[bool, str, int]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        prev, n = "GENESIS", 0
        for rid, actor, action, details, ph, eh, created in conn.execute(
                "SELECT id, actor, action, details, previous_hash, event_hash, created_at FROM audit_events ORDER BY id"):
            if ph != prev:
                return False, f"chain broken before event {rid}", n
            try:
                ts = datetime.fromisoformat(created).isoformat() if created else ""
            except ValueError:
                ts = created or ""
            body = {"actor": actor, "action": action, "details": details, "timestamp": ts, "previous_hash": ph}
            digest = hmac.new(secret.encode(), json.dumps(body, sort_keys=True, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(digest, eh or ""):
                return False, f"event {rid} hash mismatch (altered, or a different SECRET_KEY)", n
            prev, n = eh, n + 1
        return True, "OK", n
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Restore an encrypted DARK CRIMENET backup into a new directory (offline).")
    ap.add_argument("backup", help="path to the .dcnbak file")
    ap.add_argument("--target", required=True, help="directory to restore into (must not exist or be empty)")
    ap.add_argument("--passphrase-env", help="read the passphrase from this environment variable instead of prompting")
    ap.add_argument("--keep-paths", action="store_true", help="do not rewrite evidence file paths in the restored database")
    ap.add_argument("--verify-only", action="store_true", help="decrypt and verify, write nothing")
    a = ap.parse_args(argv)

    src = Path(a.backup)
    if not src.is_file():
        fail(f"backup file not found: {src}")
    target = Path(a.target).expanduser().resolve()
    if not a.verify_only and target.exists() and (not target.is_dir() or any(target.iterdir())):
        fail(f"target {target} exists and is not empty; refusing to overwrite. Choose a fresh directory.")
    passphrase = os.environ.get(a.passphrase_env, "") if a.passphrase_env else getpass.getpass("Backup passphrase: ")
    if not passphrase:
        fail("empty passphrase")

    plain = decrypt(src.read_bytes(), passphrase)
    try:
        zf = zipfile.ZipFile(io.BytesIO(plain))
    except zipfile.BadZipFile:
        fail("backup archive is corrupt")
    names = zf.namelist()
    if any(not safe(n) for n in names):
        fail("archive contains unsafe paths")
    if "manifest.json" not in names:
        fail("archive has no manifest")
    manifest = json.loads(zf.read("manifest.json"))
    print(f"Backup created {manifest.get('created_at')} by {manifest.get('created_by')} (app {manifest.get('app_version')}); "
          f"{len(manifest.get('files', []))} files")
    bad = []
    listed = set()
    for f in manifest.get("files", []):
        if not safe(f.get("path", "")):
            fail("manifest contains unsafe paths")
        listed.add(f["path"])
        if f["path"] not in names:
            bad.append(f"missing: {f['path']}"); continue
        data = zf.read(f["path"])
        if hashlib.sha256(data).hexdigest() != f.get("sha256") or len(data) != f.get("bytes"):
            bad.append(f"hash mismatch: {f['path']}")
    for n in names:
        if n != "manifest.json" and n not in listed:
            bad.append(f"unexpected file not in manifest: {n}")
    if bad:
        for b in bad[:20]:
            print("  FAIL", b, file=sys.stderr)
        fail("manifest verification failed; nothing was written", 3)
    print("Manifest verification: OK (all SHA-256 hashes match)")
    if a.verify_only:
        return 0

    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        fail("target became non-empty; aborting")
    db_path = target / "database.sqlite"
    db_path.write_bytes(zf.read("database/dcn.sqlite"))
    ev_root = target / "evidence"
    ev_root.mkdir()
    count = 0
    for n in names:
        if n.startswith("evidence/") and not n.endswith("/"):
            dest = (target / n).resolve()
            if not dest.is_relative_to(ev_root.resolve()):
                fail("unsafe evidence path in archive")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(n))
            count += 1
    print(f"Restored database and {count} evidence file(s) into {target}")

    secret = os.environ.get("SECRET_KEY", "")
    conn = sqlite3.connect(str(db_path))
    try:
        print("SQLite integrity_check:", conn.execute("PRAGMA integrity_check").fetchone()[0])
        if not a.keep_paths:
            changed = 0
            for table in ("evidence", "document_records"):
                try:
                    rows = conn.execute(f"SELECT id, stored_path FROM {table}").fetchall()
                except sqlite3.Error:
                    continue
                for rid, stored in rows:
                    if not stored:
                        continue
                    new = ev_root / Path(str(stored).replace("\\", "/")).name
                    if new.is_file():
                        conn.execute(f"UPDATE {table} SET stored_path=? WHERE id=?", (str(new), rid)); changed += 1
            conn.commit()
            print(f"Rewrote {changed} evidence path(s) to the restored evidence folder (audit log untouched)")
    finally:
        conn.close()
    if secret:
        ok, why, n = chain_ok(db_path, secret)
        print(f"Audit chain: {'VERIFIED' if ok else 'NOT VERIFIED'} ({n} events checked) - {why}")
        if not ok:
            return 4
    else:
        print("Audit chain: not checked (set SECRET_KEY to the original server secret to verify it)")
    print("\nStart the server against the restored data with:")
    print(f"  DATABASE_URL=sqlite:///{db_path} EVIDENCE_DIR={ev_root} SECRET_KEY=<original secret> python -m uvicorn backend.app.main:app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
