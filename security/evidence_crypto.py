"""Optional envelope encryption of stored evidence files (encryption at rest).

Enabled only when ``EVIDENCE_ENCRYPTION_KEY`` is set (a Fernet key, or any base64 text that decodes to 32 bytes).
Every NEW file gets its own random data key; the data key is wrapped with the master key and stored in the file
header:  MAGIC | 4-byte wrapped-key length | wrapped data key | Fernet(data key, plaintext).

Hash semantics are unchanged: the evidence SHA-256 is always the hash of the PLAINTEXT, recorded at upload time.
Verification decrypts and re-hashes. Files written before encryption was switched on stay readable (no magic header
=> plain). Switching the option off does not lose data as long as the key is still available to read old files.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

MAGIC = b"DCNENC1\n"


class EvidenceCryptoError(Exception):
    """Encrypted file that cannot be read (missing/wrong key, corrupted ciphertext)."""


def _master() -> Fernet | None:
    raw = (os.environ.get("EVIDENCE_ENCRYPTION_KEY") or "").strip()
    if not raw:
        return None
    try:
        key = raw.encode()
        Fernet(key)  # already a valid Fernet key (urlsafe base64 of 32 bytes)
        return Fernet(key)
    except (ValueError, TypeError):
        pass
    try:
        b = base64.b64decode(raw + "=" * (-len(raw) % 4), validate=False)
        if len(b) == 32:
            return Fernet(base64.urlsafe_b64encode(b))
    except (ValueError, TypeError):
        pass
    raise EvidenceCryptoError("EVIDENCE_ENCRYPTION_KEY is not a valid base64 32-byte key")


def enabled() -> bool:
    return _master() is not None


def key_fingerprint() -> str | None:
    m = os.environ.get("EVIDENCE_ENCRYPTION_KEY")
    return hashlib.sha256(m.strip().encode()).hexdigest()[:12] if m and m.strip() else None


def generate_key() -> str:
    return Fernet.generate_key().decode()


def encrypt(data: bytes) -> bytes:
    m = _master()
    if m is None:
        return data
    dk = Fernet.generate_key()
    wrapped = m.encrypt(dk)
    return MAGIC + len(wrapped).to_bytes(4, "big") + wrapped + Fernet(dk).encrypt(data)


def is_encrypted_bytes(blob: bytes) -> bool:
    return blob.startswith(MAGIC)


def decrypt(blob: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        return blob
    m = _master()
    if m is None:
        raise EvidenceCryptoError("file is encrypted but EVIDENCE_ENCRYPTION_KEY is not set")
    try:
        n = int.from_bytes(blob[len(MAGIC):len(MAGIC) + 4], "big")
        wrapped = blob[len(MAGIC) + 4:len(MAGIC) + 4 + n]
        dk = m.decrypt(wrapped)
        return Fernet(dk).decrypt(blob[len(MAGIC) + 4 + n:])
    except (InvalidToken, ValueError) as exc:
        raise EvidenceCryptoError("cannot decrypt file (wrong key or corrupted ciphertext)") from exc


def store(path: Path, data: bytes) -> None:
    """Write evidence bytes, encrypted when the option is on."""
    Path(path).write_bytes(encrypt(data))


def is_encrypted(path: Path) -> bool:
    with Path(path).open("rb") as fh:
        return fh.read(len(MAGIC)) == MAGIC


def read_plain(path: Path) -> bytes:
    return decrypt(Path(path).read_bytes())


def sha256_plain(path: Path) -> str:
    return hashlib.sha256(read_plain(path)).hexdigest()
