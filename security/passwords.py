"""Password policy shared by every endpoint that sets a password.

Strict policy (any APP_ENV other than ``demo``): >= 10 characters, letters and digits, not a common password,
must not contain the account ID. Demo policy (APP_ENV=demo): >= 6 characters and not equal to the account ID, so
that the documented demonstration passwords (admin123 ...) work. Never use the demo policy for real data.
"""
from fastapi import HTTPException

from backend.app.core.config import settings

_COMMON = {
    "password", "password1", "password123", "admin123", "administrator", "letmein123",
    "qwerty123", "welcome123", "changeme123", "iloveyou123",
}
MIN_LENGTH = 10
DEMO_MIN_LENGTH = 6


def policy_text() -> str:
    if settings.is_demo:
        return f"at least {DEMO_MIN_LENGTH} characters (demo mode)"
    return f"at least {MIN_LENGTH} characters, letters and digits"


def validate_password(password: str, username: str = "") -> None:
    p = password or ""
    problems = []
    if settings.is_demo:
        if len(p) < DEMO_MIN_LENGTH:
            problems.append(f"at least {DEMO_MIN_LENGTH} characters")
        if username and p.lower() == username.lower():
            problems.append("must not be the same as the account ID")
    else:
        if len(p) < MIN_LENGTH:
            problems.append(f"at least {MIN_LENGTH} characters")
        if not any(c.isalpha() for c in p) or not any(c.isdigit() for c in p):
            problems.append("both letters and digits")
        if username and username.lower() in p.lower():
            problems.append("must not contain the account ID")
        if p.lower() in _COMMON or p.lower().strip("0123456789!@#$") in _COMMON:
            problems.append("must not be a common password")
    if problems:
        raise HTTPException(400, "Password must have: " + "; ".join(problems))
