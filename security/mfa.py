"""Multi-factor authentication primitives (TOTP, RFC 6238) - standard library + ``cryptography`` only.

Nothing here touches the database; the tables and routes live in backend/app/features/mfa.py.

* TOTP: HMAC-SHA1, 30 s step, 6 digits, +/-1 step window. All candidate windows are compared in constant time.
* Replay protection: ``match_step`` returns the matched time-step; the caller must persist it and refuse any step
  that is not strictly greater than the last accepted one.
* Secrets at rest: Fernet, key derived from the application secret with HKDF-SHA256 and a purpose label. The
  ciphertext also binds the user id so a value copied between rows does not decrypt.
* Recovery codes: 10 random codes, stored only as salted PBKDF2 hashes, single use.
* MFA token: short-lived (5 min) signed JWT proving that the password step succeeded; useless as a session token.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import re
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import jwt
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from backend.app.core.config import settings

ISSUER = "DARK CRIMENET"
STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1
RECOVERY_COUNT = 10
MFA_TOKEN_TTL = 300  # seconds
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o/1/i/l
_RECOVERY_ROUNDS = 30_000
_TOTP_RE = re.compile(r"\d{6}")


def _time() -> float:
    return time.time()  # single clock for TOTP (tests replace it)


# ---------------------------------------------------------------- TOTP (RFC 4226 / 6238)
def new_secret() -> str:
    """160-bit random secret, base32 without padding (32 characters), as authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret_b32: str) -> bytes:
    s = re.sub(r"[\s-]", "", secret_b32).upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def hotp(secret_b32: str, counter: int, digits: int = DIGITS, algo=hashlib.sha1) -> str:
    digest = hmac.new(_key(secret_b32), struct.pack(">Q", counter), algo).digest()
    off = digest[-1] & 0x0F
    num = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(num).zfill(digits)


def totp(secret_b32: str, at: float | None = None, digits: int = DIGITS, step: int = STEP_SECONDS, algo=hashlib.sha1) -> str:
    t = _time() if at is None else at
    return hotp(secret_b32, int(t) // step, digits, algo)


def match_step(secret_b32: str, code: str, at: float | None = None) -> int | None:
    """Return the time-step whose code equals ``code`` (within the +/-1 window) or None. Constant-time compares."""
    code = (code or "").strip().replace(" ", "")
    if not _TOTP_RE.fullmatch(code):
        return None
    now_step = int(_time() if at is None else at) // STEP_SECONDS
    found = None
    for s in range(now_step - WINDOW, now_step + WINDOW + 1):  # no early exit: same work for every input
        if hmac.compare_digest(hotp(secret_b32, s), code):
            found = s
    return found


def otpauth_uri(secret_b32: str, username: str) -> str:
    # Kept short on purpose: SHA1 / 6 digits / 30 s are the defaults every authenticator app assumes, and a shorter
    # address gives a smaller, sparser QR code that phone cameras read reliably from a screen.
    label = quote(f"{ISSUER}:{username}", safe=":")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(ISSUER, safe='')}"


def qr_svg(data: str) -> str:
    """Inline SVG QR code generated locally with the pure-python ``segno`` package ('' if it is not installed)."""
    try:
        import segno
    except ImportError:
        return ""
    buf = io.BytesIO()
    segno.make(data, error="l", micro=False).save(buf, kind="svg", xmldecl=False, svgns=True, nl=False,
                                                    scale=8, border=4, dark="#000000", light="#ffffff")
    svg = buf.getvalue().decode("utf-8")
    # segno writes a fixed pixel size and no viewBox, so the browser cannot scale it and crops it to whatever box it is
    # shown in (this is what made the code unscannable). Swap the fixed size for a viewBox so it scales as a whole.
    import re
    svg = re.sub(r'\swidth="(\d+)"\sheight="(\d+)"', r' viewBox="0 0 \1 \2"', svg, count=1)
    return svg


# ---------------------------------------------------------------- secrets at rest
def _derive(label: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=label).derive(settings.secret_key.encode())


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derive(b"dcn/mfa/secret-at-rest/v1")))


def encrypt_secret(secret_b32: str, user_id: int) -> str:
    return "v1:" + _fernet().encrypt(f"{int(user_id)}|{secret_b32}".encode()).decode()


def decrypt_secret(blob: str, user_id: int) -> str | None:
    """Plain base32 secret, or None if there is none / it cannot be decrypted / it belongs to another user."""
    if not blob or not blob.startswith("v1:"):
        return None
    try:
        text = _fernet().decrypt(blob[3:].encode()).decode()
    except (InvalidToken, ValueError):
        return None
    uid, _, secret = text.partition("|")
    return secret if uid == str(int(user_id)) and secret else None


# ---------------------------------------------------------------- recovery codes
def new_recovery_codes(n: int = RECOVERY_COUNT) -> list[str]:
    out = []
    for _ in range(n):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        out.append(raw[:5] + "-" + raw[5:])
    return out


def normalize_recovery(code: str) -> str:
    return re.sub(r"[\s-]", "", (code or "")).lower()


def hash_recovery(code: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", normalize_recovery(code).encode(), salt.encode(), _RECOVERY_ROUNDS).hex()
    return salt, digest


def recovery_matches(code: str, salt: str, digest: str) -> bool:
    return hmac.compare_digest(hash_recovery(code, salt)[1], digest)


def looks_like_totp(code: str) -> bool:
    return bool(_TOTP_RE.fullmatch((code or "").strip().replace(" ", "")))


# ---------------------------------------------------------------- MFA token (password step done, second factor pending)
def _token_key() -> bytes:
    return _derive(b"dcn/mfa/login-token/v1")


def issue_mfa_token(username: str, user_id: int, token_version: int, stage: str) -> str:
    """stage: 'verify' (user has MFA, must present a code) or 'setup' (role requires MFA, user must enrol)."""
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": username, "uid": int(user_id), "tv": int(token_version or 0), "purpose": "mfa",
                       "stage": stage, "iat": now, "exp": now + timedelta(seconds=MFA_TOKEN_TTL),
                       "jti": secrets.token_hex(8)}, _token_key(), algorithm="HS256")


def read_mfa_token(token: str, stage: str) -> dict | None:
    """Claims of a valid, unexpired MFA token of the given stage, else None."""
    try:
        c = jwt.decode(token or "", _token_key(), algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None
    return c if c.get("purpose") == "mfa" and c.get("stage") == stage else None
