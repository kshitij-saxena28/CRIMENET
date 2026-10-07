"""Application settings.

Secrets are never hardcoded. ``SECRET_KEY`` must come from the environment / ``.env``.
In ``APP_ENV=demo`` a random key is generated once and persisted to ``.runtime_secret``
(git-ignored, mode 0600) so restarts do not invalidate sessions. In any other
environment a missing or weak key aborts start-up.
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]
log = logging.getLogger("dcn.config")

# Values that have shipped in earlier releases or in examples; never accept them.
_WEAK_SECRETS = {
    "",
    "darkcrimenet-demo-secret-key-2026",
    "replace-with-a-long-random-secret-at-least-32-characters",
    "change-me",
    "secret",
}


class Settings(BaseSettings):
    app_name: str = "DARK CRIMENET"
    app_env: str = "demo"  # "demo" or "production"
    database_url: str = f"sqlite:///{ROOT / 'darkcrimenet.db'}"
    secret_key: str = ""
    access_token_expire_minutes: int = 60

    # Social-media collection through official APIs (all optional; a provider without its key is simply unavailable).
    x_bearer_token: str = ""
    youtube_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = ""
    mastodon_instance: str = "mastodon.social"
    mastodon_token: str = ""
    social_autocollect: bool = True          # run saved recurring searches in the background
    social_collect_min_minutes: int = 15     # a saved search may not repeat more often than this
    evidence_dir: str = ""
    max_upload_mb: int = 25

    # Optional Neo4j connection
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""

    # Brute-force protection
    login_max_attempts: int = 5
    login_window_seconds: int = 900
    login_user_max_attempts: int = 20

    # Two-step sign-in: comma-separated roles that must use it (MFA_REQUIRED_ROLES; empty = none).
    # Unset = none in demo mode, admin, supervisor and investigator otherwise. Admins can override per role in the UI.
    mfa_required_roles: str | None = None

    # Anomaly signal thresholds (uncalibrated; see docs/responsible-ai.md)
    anomaly_review_threshold: int = 62
    anomaly_high_threshold: int = 80

    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    @property
    def is_demo(self) -> bool:
        return self.app_env.lower() == "demo"

    @property
    def evidence_path(self) -> Path:
        return Path(self.evidence_dir) if self.evidence_dir else ROOT / "evidence_store"

    @property
    def max_upload_bytes(self) -> int:
        return max(1, int(self.max_upload_mb)) * 1024 * 1024


def _resolve_secret(s: Settings) -> str:
    key = (s.secret_key or "").strip()
    if key not in _WEAK_SECRETS and len(key) >= 32:
        return key
    if not s.is_demo:
        raise RuntimeError(
            "SECRET_KEY is missing, too short (<32 chars) or a known default. "
            "Set a strong random SECRET_KEY (e.g. `python -c \"import secrets;print(secrets.token_urlsafe(48))\"`)."
        )
    path = ROOT / ".runtime_secret"
    try:
        if path.exists():
            saved = path.read_text().strip()
            if len(saved) >= 32:
                return saved
        generated = secrets.token_urlsafe(48)
        path.write_text(generated)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        log.warning("Generated a random demo SECRET_KEY at %s. Set SECRET_KEY explicitly for real use.", path)
        return generated
    except OSError:
        log.warning("Could not persist a demo secret; using an ephemeral one (sessions reset on restart).")
        return secrets.token_urlsafe(48)


settings = Settings()
settings.secret_key = _resolve_secret(settings)
