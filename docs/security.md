# Security & Role Model — v5.3.0

## Roles and permissions

Roles: admin, supervisor, investigator, auditor and demo. The full permission table, with the reasoning for each permission, is in [roles.md](roles.md).

Permissions are checked per endpoint (`security/rbac.py`, `require_perm`). On top of that every case-scoped call is authorized against the case (`security/access.py`): admins see all; `shared` cases are open to all roles; `restricted` cases only to the creator and explicit members. Unauthorized cases return **404**, so their existence is not disclosed. Non-admins can never omit `case_number` to obtain a cross-case feed.

## Authentication and sessions

* PBKDF2-HMAC-SHA256 (210k rounds, per-user salt); dummy hash on unknown accounts to keep timing uniform.
* JWT (HS256) carries only the username and a *token version*. Every request re-reads the user from the database: role, active flag and version come from the DB, so deactivation, role change, password change and username change revoke sessions immediately.
* There is no forced password change and no self-service reset. Users change their own password (current password required); admins set others' passwords; `scripts/set_password.py` recovers a lockout on the server. Any of these signs existing sessions out.
* `SECRET_KEY` is never hardcoded. Production refuses to start with a missing/short/known-default key; demo mode generates one into `.runtime_secret`.
* Sessions default to 60 minutes.
* Brute force: sliding-window limiter per IP+account and per account (in-process; use a shared store for multi-worker deployments).
* **Two-step sign-in (role-based MFA, TOTP RFC 6238, offline).** After the password, roles that require it enter a 6-digit code from an authenticator app (Google/Microsoft Authenticator, Authy). No SMS or e-mail is used.
  * Policy: admins choose per role under *Admin Portal -> Two-step sign-in* (`GET/PUT /admin/mfa/policy`). Precedence: admin decision, then `MFA_REQUIRED_ROLES` (comma-separated; empty = none), then the default. Default: `APP_ENV=demo` -> nobody required (optional for everyone); any other environment -> admin, supervisor and investigator required. The `demo` role can never be forced. A user whose role requires it and who has not enrolled must enrol at sign-in.
  * Login: `POST /auth/login` returns `mfa_required` or `mfa_setup_required` plus a 5-minute signed `mfa_token` (separate key, bound to the user and token version, useless as a session); `POST /auth/mfa/verify` exchanges it plus a code for the session token, which carries `amr: ["pwd","otp"]`.
  * Secrets are stored Fernet-encrypted (key derived from `SECRET_KEY` with HKDF, bound to the user id). Codes are compared in constant time with a +/-1 step window; the last accepted time-step is stored so a code can never be used twice. Ten recovery codes are shown once, stored only as salted PBKDF2 hashes, single use, regenerated with password + code.
  * Wrong codes are rate-limited like passwords (5 per address+account, 20 per account per 15 minutes), give one generic message and are audit-logged. An `mfa_token` of a user who already has MFA can never enrol a new device (that would bypass the second factor).
  * Sensitive actions (change password/username, create user, change role, set password, MFA policy, MFA reset, turn MFA off, new recovery codes) need a session that used the code when the account has MFA on. Turning MFA on/off, admin reset and password changes by others sign the user out everywhere.
  * Lost phone: use a recovery code, or an admin resets the account (audit-logged, signs the user out). It is never self-service without a recovery code. On the server: `python scripts/set_password.py USER --mfa-only`.
  * Plain-language guide for officers: [docs/guide/two-step-sign-in.md](guide/two-step-sign-in.md).
* For a real deployment prefer an approved identity provider (SSO with MFA); this built-in TOTP is the offline fallback.
* Web UI: strict Content-Security-Policy (`script-src 'self'`, no inline script, no third-party code), data rendered as text nodes (no `innerHTML`), token held in `sessionStorage` (cleared when the tab closes).
* Password policy in `security/passwords.py`.

## Data protection

* Uploads: size cap (`MAX_UPLOAD_MB`), extension whitelist, magic-byte check, filename never used for storage paths. Evidence is stored under a hash-derived name, referenced by a project-relative path, re-hashed from disk after writing, and served only as an attachment with `nosniff` and a server-chosen content type.
* Auditor responses mask phone/account/e-mail/vehicle identifiers and never include FIR text, evidence lists or reports.
* Security headers on every response; `/docs` and `/openapi.json` only in demo mode.

## Audit

* Every security-relevant action is written to a **keyed (HMAC-SHA256) hash chain** stored in the database. Editing or deleting a row is detected with the first bad row id (`/audit/verify`). Someone with database write access but without `SECRET_KEY` cannot recompute it.
* Limitations: truncating the *newest* rows cannot be detected without an externally stored head hash; the writer lock is per process. For real evidentiary use, ship the head hash to write-once storage.
* Evidence uploads, duplicate uploads, verifications, downloads and report generation are recorded (chain of custody).
* This is a tamper-*evident* log, not a blockchain.

## Data-integrity ledger (v6)

A second, stronger log sits beside the audit chain: a **signed, hash-chained, append-only ledger** (`backend/app/features/integrity.py`, table `ledger_blocks`). Plain-language guide: [guide/data-integrity.md](guide/data-integrity.md).

* **Block** = index, UTC timestamp, event type, actor, case, ref, canonical payload JSON, `payload_sha256`, `prev_hash`, `block_hash = SHA-256(all of those + key id)`, and an **Ed25519 signature** of the block hash. Payloads contain ids, hashes, counts and status only: never file content or personal data (free text is replaced by a hash by `sanitize()`).
* **Chain:** each block carries the previous block's hash. Editing, deleting or re-ordering a block breaks every later link.
* **Anchors:** every `LEDGER_ANCHOR_EVERY` (50) blocks, after `LEDGER_ANCHOR_SECONDS`, on demand (`POST /integrity/anchor`) and when a bundle is downloaded, an `anchor` block records the Merkle root of the batch (leaf `sha256(0x00||block_hash)`, node `sha256(0x01||L||R)`, odd node promoted). `GET /integrity/proof/{index}` returns the inclusion proof.
* **Key:** `.ledger_ed25519` (mode 0600, git-ignored, created on first use; isolated installs keep it next to their database; `LEDGER_KEY_PATH` overrides). Key id = first 16 hex of SHA-256 of the public key; the full SHA-256 is the fingerprint to pin. **Rotation** (`POST /integrity/rotate-key`, admin) is a `key_rotation` block signed by the OLD key that names the new key and carries the new key's proof of possession; later blocks use the new key. Everything needed to walk the key chain is inside the ledger.
* **Witness file:** every anchor (and every `GET /integrity/head`) appends a signed copy of the head to `.ledger_heads.log` beside the key, outside the database. Verification compares it with the database, so cutting the newest blocks off the DB is detected. Someone who controls both the database and the server's file system can remove both; only a head hash published elsewhere (paper, case file, another system) is proof against that.
* **Recording:** subscribed to the hook bus in `on_startup` (document extracted / reviewed / reprocessed, table ingested, evidence uploaded / downloaded / verified, case created, entity and relationship created, social and surveillance events, exports, backups, demo on/off, purge). Writes use their own transaction under a process lock and retry on index clashes (multi-worker safe). If the ledger cannot be written the request still succeeds; the failure is logged at ERROR and a `ledger_gap` block (which also records the row hashes the lost events touched) is written with the next success. A gap is reported as a note, not as tampering.
* **Row hashes:** when documents, evidence, entities, relationships (and tables named `surveillance*` / `social*` that carry `case_number`) are recorded, the ledger stores a hash of the row's stable fields. Evidence, entity and relationship hashes cover immutable fields (first record wins; a later different record is a conflict); documents include review status (latest record wins).
* **`POST /integrity/verify`** (permission `integrity` or `audit`; case-scoped for anyone who can `read` a case) checks: the whole chain (links, payload/block hashes, signatures, key rotations, Merkle anchors, witness heads); every evidence file re-hashed (decrypting if needed) against the database and the hash the ledger recorded at upload; every recorded row against the live database (modified, missing, **unrecorded**); the HMAC audit chain. Verdict `INTACT`, `PARTIAL` (part could not be checked, e.g. encrypted file without key) or `TAMPERING DETECTED`, with the exact blocks, rows and files. `POST /integrity/baseline` (admin) explicitly accepts unrecorded rows (data loaded by scripts) and is itself logged; the first start of a ledger records existing data as baseline.
* **Offline:** `GET /integrity/bundle` returns a signed JSON bundle (key chain, blocks, anchors, proofs, head); `python scripts/verify_ledger.py bundle.json [--trust-key FP] [--expect-head HASH]` checks it with the standard library only (pure-Python Ed25519 fallback). Case slices prove no edit and correct signature; only a full-chain bundle can show that no block is missing; removal of the newest blocks needs `--expect-head`.
* **Leak prevention:** every report (CSV/DOCX/PDF/JSON), redacted export, legal pack, backup download and evidence download writes an `export_generated` / `evidence_downloaded` block (who, case, format, size, output hash). Generated documents carry a visible watermark, "Exported by USER on TIME UTC, ref L-n, watermark WM-XXXX" (footer line, CSV comment row, JSON `watermark` member); the ledger holds the hash of the delivered file so a leaked copy can be traced (`GET /integrity/exports?watermark=` or `?sha256=`). Original evidence files are downloaded unmodified (a watermark would alter them); the download is recorded. More than `EXPORT_ALERT_THRESHOLD` (10) exports by one user within `EXPORT_ALERT_WINDOW_MINUTES` (10) writes an `export_anomaly` block and notifies admins and supervisors. **Limits:** a determined insider can still photograph a screen or retype data. These controls make leaks attributable and detectable, not impossible.
* **Encryption at rest (optional):** with `EVIDENCE_ENCRYPTION_KEY` set, newly stored evidence files are envelope-encrypted (per-file Fernet data key wrapped by the master key; `security/evidence_crypto.py`). The evidence SHA-256 stays the hash of the plaintext recorded at upload; verification and downloads decrypt. Older plaintext files keep working. Losing the key means losing the files; backups contain the ciphertext, so keep the key separate from them.
* **What it cannot prove:** that data was true when entered; anything before the ledger existed (existing data is only baselined); changes to tables the ledger does not track (cases, notes, tasks, alerts, users); deletion of the newest blocks without an external head; anything about a screen photograph.
* Not done: login-failure bursts and user/role changes are not written to the ledger (the audit log holds them; the hooks for them are subscribed but not yet emitted). Rows deleted by any path other than purge or demo-off are reported as missing until an administrator investigates.

## Still required for a real deployment

TLS termination, a managed secret store, a shared rate-limit store, database encryption and backups, an approved identity provider for SSO and account recovery (built-in offline TOTP MFA exists), external audit anchoring, dependency and vulnerability scanning, and the organisation's own security review. Entity IDs remain globally unique across cases (a case-isolation limit of the current data model), so avoid reusing generic IDs across unrelated cases.
