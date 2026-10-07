# DARK CRIMENET v5.3.0

## v5.3.0 — Account monitoring, working authenticator QR, whole-graph default

**Social: Monitor.** Follow one account: the server fetches its posts through official APIs (X, YouTube, Reddit, Mastodon public API) once or on a schedule and adds only new posts. Needs the administrator's API keys (`.env`); each check is audited with its legal basis; private addresses are refused. Tested with simulated responses only: it has not been exercised against the live platforms. The API also supports keyword, hashtag and feed searches, which the screen does not offer for now.

**Two-step sign-in QR.** Fixed: the code was cropped by the page (no viewBox) and too dense. It is now smaller, scales as a whole and decodes from the rendered page.

**Knowledge graph.** Opens on the whole graph (unlinked entities hidden unless ticked); choosing an entity still shows its neighbourhood.

## v5.2.0 — "Docket" interface, sharper social media intelligence, cleaner demo-off, no sub-officer role

**Design.** A fourth, distinct identity ("Docket"): warm paper by day and graphite by night, a slim icon rail that widens on hover, a case strip that always names the active case, hairline-ruled cards and one vermilion signal colour. Motion is short and purposeful (page fade, tab underline, count-up, sign-in word reveal) and switches off for people who ask for reduced motion. New logo and favicon.

**Close controls.** Every pop-up, dialog, palette, account menu, lead drawer and the mobile menu closes with a visible Close button, with Esc, and by clicking outside.

**Name.** The product is DARK CRIMENET throughout. All references to the earlier competition/programme name are removed from code, screens and current documents (historic release notes below are unchanged).

**Roles.** The sub-officer role is removed. Existing sub-officer accounts become auditors at start-up (their sessions are revoked once). Access requests for that role are treated as auditor requests.

**Two-step sign-in.** Code-based sign-in re-verified end to end (enrolment, recovery codes, login step, lockout, admin reset, per-role policy).

**Social media intelligence.** New Priority queue with an explainable points list per account and post; Look-alike accounts; Narrative spread with burst detection; Watch list (words, hashtags, handles, phones, links); English view for Hindi/Hinglish posts; analyst notes; printable profile sheet sealed in the ledger; places map. Language detection no longer counts links, hashtags or handles.

**Demo data off means off.** Audit views, the ledger view, your own activity list and the model card no longer show anything from a removed demo case. (The cryptographic chains themselves are unchanged so verification still passes.)

**Honest limits.** Priority scores are transparent rules, not a trained model, and are reading aids only. Machine translation is approximate. Look-alike matches are leads, not identification.


## v5.1.0 — New console UI, social media intelligence, surveillance reports, tamper-proof ledger, two-step sign-in

**Design.** Third visual identity: dark navy sidebar, light or dark workspace, white cards, indigo accent, Plus Jakarta Sans. Back and forward buttons in the top bar (browser buttons and Alt + arrows also work). Timeline and Map: the explanatory text is collapsed and the map sits higher; the Gaps tab is removed.

**FIR upload.** If no case is selected (or none exists yet) a pop-up asks for the case number, title and summary, creates the case, then continues the upload.

**New.** Social Media Intelligence: import-only (no scraping, no logins); risk-language flags in English, Hindi and Hinglish; coordinated-account detection; suggested links to case entities for human review. Surveillance Reports: authorised operations with a per-operation hash-chained log and PDF, Word, HTML and JSON reports. Data Integrity: Ed25519-signed, hash-chained ledger with Merkle anchors, one-click check of the ledger and case data, exports with watermarks, an offline verifier (`scripts/verify_ledger.py`), optional encryption of stored evidence. Role-based two-step sign-in (TOTP): admin-set policy per role, QR enrolment at sign-in, recovery codes, lockout, admin reset.

**Removed.** Pattern Lab, Cyber Forensics, cross-case hits and access requests between cases, the what-if sandbox, the timeline Gaps tab.

**Honest limits.** Flagging and coordination accuracy numbers come from small synthetic corpora written for this project. The ledger detects tampering, but removal of the newest blocks can only be proven if an earlier head hash was recorded elsewhere. The ledger does not stop an administrator with database and key access from rewriting everything; keep head hashes off the server. TOTP is offline and has no push or hardware-key support.

## v5.0.0 — New design, roles and access requests, person-of-interest ranking, better extraction

**Design.** Completely new visual identity ("DARK CRIMENET"): warm paper / ink themes, serif headlines, a top masthead with horizontal navigation instead of a sidebar, a landing page with sign-in and access request, ink-wipe transition, animated reveals. A "How this page works" panel on every page plus a glossary make each feature self-explanatory. Fonts are bundled (offline).

**Access.** Roles: admin, supervisor (can change data), investigator (investigates and verifies only), auditor, sub-officer, demo. No self sign-up: people send an access request; a supervisor must approve it; then an administrator creates the account. Demo data switch for the demo account and admins; demo cases are invisible to real users. Simple fixed passwords in demo mode (see DEMO_ACCOUNTS.md).

**New: Persons of Interest.** Explainable lead-priority ranking with evidence, uncertainty band and a required confirm/dismiss decision (docs/models/suspect-model-card.md). Honest limits: weights are hand-set, evaluated only on a synthetic simulator.

**Better extraction and translation.** New FIR parser (Indian digits, OCR-noise repair, Aadhaar/PAN/IFSC/UPI/vehicle validation, IPC/BNS sections, roles, relationships with evidence spans), larger Hindi/Marathi/Hinglish glossary with transliteration and per-sentence quality flags, review screen with side-by-side original | English. Measured on a 30-document gold set written by the developers (held-out split included); expect lower on real scans.

**Fixed.** Timeline & Map (works offline, clustered map, time slider), Pattern Lab (double counting, currency mixing, time zones, misleading labels), Cyber Forensics (rebuilt scam-message analyzer, link checker, email-header analyzer, file forensics, custody verifier), several buttons that did nothing.

**Removed** to keep the product focused: Link Canvas, insider-risk tool, case-intelligence and model-quality modules.

**Known gaps.** No backend tests specific to timeline/map endpoints; other Indian scripts translate word-for-word only; the suspect model is not fitted to real labelled data; access requests do not send email.

## v4.5.0 — Fast localhost UI; MFA and reset flows removed

- Streamlit and Plotly are gone. The UI is a static single-page app (`web/`) served by FastAPI on the same port as the API (http://localhost:8000). Navigation no longer re-runs a server-side script. Cytoscape and Leaflet are vendored (offline-capable).
- Same functionality by tab: Command Center, Ingestion & Review (upload, table ingest, entity/relationship review, quick verify, English view, related FIRs), Knowledge Graph (neighbourhood, paths, hidden connections, communities, centrality, entity resolution), Analytics, Timeline & Map (swimlanes, filters, FIR compare, map), Signals & Evidence (alerts, FIR board, evidence vault), Cases & Notes (workbenches, tasks, notes), AI Copilot, Reports & Audit (JSON/CSV/DOCX/PDF, audit verify), Admin Portal (users, roles, case access, demo lab), masked read-only Sub-Officer view, Account dialog, light/dark theme.
- **Removed:** all MFA endpoints and storage, `/auth/forgot-password`, `/auth/reset-password`, and the forced change-password-at-first-login flow (`must_change_password`). The `cryptography` dependency is no longer needed.
- **Kept:** voluntary change of own password/username, admin password set (signs the user out), and a new offline CLI, `scripts/set_password.py`, for lockout recovery.
- **Fixed:** `name 'post' is not defined` on Change password (the whole Streamlit front end that contained it was replaced).
- New Content-Security-Policy (no inline scripts, no third-party code); tests ensure the UI never uses `innerHTML`, only calls existing endpoints, and is served with a strict CSP.
- Docker: a single service. `RUN_WINDOWS.bat` and `run.sh` start one process.
- **Upgrade notes:** old `users` columns for MFA/recovery stay in an existing database but are ignored. Sessions from 4.4.x remain valid.

## v4.4.0 — Security & correctness hardening

**Security**
- Removed the anonymous password-recovery takeover: the recovery code is never returned by the API; MFA is no longer cleared by self-service reset; codes are single-use, expire in 10 min and are burned after 5 wrong guesses.
- `SECRET_KEY` no longer hardcoded; production refuses missing/weak keys.
- Sessions are DB-backed with a token version: deactivation, role/password/username change and forced password change take effect immediately and are enforced by the API.
- Removed the plaintext default-password fallback and the silent re-creation of renamed accounts; bootstrap only on an empty user table with random one-time passwords.
- Login/MFA/recovery rate limiting; password policy; MFA secrets encrypted at rest; TOTP replay protection; MFA re-enrolment guard.
- Per-case authorization (creator/members/admin, `shared` for demo cases); no cross-case feeds for non-admins; permission-based endpoint guards; case create endpoint can no longer overwrite existing cases.
- Sub-Officer role now really is masked and read-only (PII masking, no documents/evidence/reports/Copilot/audit); dedicated UI view.
- Upload hardening (size, type, signature), safe evidence download, project-relative evidence paths, read-back hashing at upload, chain-of-custody audit events.
- Audit log is now a keyed HMAC chain with real tamper detection (no more "legacy" masking), serialized writes; the unused `blockchain/` package was removed.
- `/health` is minimal; `/docs` only in demo mode; security headers; graph path enumeration capped.
- Front end (since replaced in 4.5.0): escaped dynamic content, no credentials on the login page, fail-closed roles, session-expiry handling.

**Correctness / performance**
- Per-thread graph engine (concurrent requests no longer overwrite each other's graph).
- Case queries prefilter in SQL instead of scanning whole tables; results verified identical to v4.3.2 on the demo dataset.
- Fixed duplicate `analytics_overview`; validated `add_entity/relationship/event/task`; type-based (not first-letter) entity typing in table ingest with accurate counts; type-driven vehicle-conflict detection; linear-time financial chain detection; entity case membership is merged instead of overwritten; evidence ID derived from the actual case.
- Errors: 404/400 instead of 200-with-error or 500; narrower exception handling with logging.
- Seed `--reset` now needs `--yes-delete-everything` and demo mode.

**AI honesty**
- Held-out metrics, model hash check, KNN index cache, uncalibrated-threshold disclosure, honest model registry, `docs/responsible-ai.md` rewritten.

**Packaging**
- Removed shipped DB, `.pyc`, source dumps and evidence; added `.gitignore`/`.dockerignore`, `requirements-dev.txt`, `run.sh`; Docker runs non-root with a data volume and shared `.env`.
- Tests run against a throw-away DB (70+ tests, incl. regression tests for each reviewed vulnerability).

**Upgrade notes**: existing databases are migrated in place (new columns; existing cases become `shared`; existing users keep their passwords but their sessions must sign in again). Passwords set before 4.4.0 that are shorter than the new policy keep working until changed. Set `SECRET_KEY` before upgrading production.


## v4.3.2 — Light Theme + Timeline Runtime Hardening

- Fixed the Timeline `category_symbols` NameError by moving the shared category vocabulary above all legend/chart usage.
- Reworked the Light console into a high-contrast white + purple interface across sidebar, headings, labels, inputs, selects, multiselects, expanders, alerts, metrics, buttons and code blocks.
- Replaced hardcoded dark Plotly text colors with theme-aware text colors for analytics, graph, cross-FIR matrix, timeline and map charts.
- Light is the default first-run console theme; Dark remains available from Workspace appearance.
- Fixed demo-user bootstrap for partially populated legacy databases: missing bundled roles are created without overwriting or reactivating existing accounts.
- Verified fresh-database Admin/Investigator/Sub-Officer login and idempotent Demo Lab loading.

# DARK CRIMENET v4.3.1

## v4.3.1 — Light Theme + Explicit Demo Lab Fix

- Fixed Light workspace styling so labels, inputs, selects, expanders, tabs, alerts, uploaders, metrics and code blocks stay readable.
- Added authenticated **Demo Lab → Load Demo Dataset**.
- Added backend `/demo/status` and `/demo/load` endpoints.
- Demo loading is idempotent and never drops user accounts.
- Added demo account reference on the login screen.
- Loading the demo automatically exposes synthetic reference workspaces and selects a recommended demo case.
- Bundled demo generator prevents duplicate synthetic rows on repeated CLI/API loads.
- Streamlit defaults to the light-purple workspace palette for a readable console.

# DARK CRIMENET — v4.3.0

## v4.3.0 — FIR Intelligence Workbench & Security Hardening

This release focuses on the four problem areas that were most visible in the previous build: neighborhood exploration, clean FIR workbenches, Hindi/Indic extraction, and KNN entity resolution. It also tightens the three-role security model and timeline/FIR relationship UX.

### Knowledge Graph / Load Neighborhood
- Reworked `GET /graph/neighborhood` to return complete case-scoped node/edge payloads instead of only node IDs.
- Added center-entity picker, hop control (1–5), node-type and relationship filters.
- Added an actual interactive neighborhood visualization with labeled nodes, relationship hover details and an entity table.
- Added payload caps with explicit truncation metadata so dense graphs remain responsive.
- Timeline event focus can now set the Knowledge Graph center entity.

### New Workbench
- Added a dedicated **＋ New Workbench** launcher for Admin/Investigator users.
- A new workspace starts clean and is isolated from unrelated demo/reference datasets.
- Creating a fresh workbench clears stale timeline/graph/entity-resolution state before the new upload batch is processed.
- `POST /workbench/new` rejects duplicate workspace identifiers.

### Timeline Intelligence
- FIR swimlanes make multi-FIR chronology visually explicit.
- Category symbols distinguish FIR/legal, incident, investigation, communication, financial, vehicle, location, meeting and evidence events.
- Added date-window filtering, FIR/category/type filters, geocoded-only filtering, source-reference counts and event inspector.
- Added **Timeline → Knowledge Graph** focus synchronization.
- Added filtered timeline CSV and JSON exports.
- Added quiet-period/gap explanations with an explicit warning that a gap is not evidence of absence.
- Added a synchronized geographic view using the same filters as the timeline.
- Timeline rendering avoids application-wide `st.stop()` calls for invalid date ranges.

### Multiple FIR Relationships
- Cross-FIR links remain explainable and evidence-key driven.
- Added an FIR relationship matrix in Analytics.
- Added a dedicated FIR relationship network view in Signals + Evidence.
- Relationship score, shared evidence keys and verification status remain visible; the system does not silently merge different FIRs into one person/entity.

### Hindi / Marathi extraction
- Bundled local Tesseract language packs for English, Hindi and Marathi.
- Added bundled Tesseract OSD for automatic page-rotation correction on phone photos/scans.
- Bundled Devanagari script data for local diagnostic/future script coverage.
- Language detection now exposes a confidence score.
- Hindi/Indic extraction UI shows original structured fields, English/transliterated view, translation method and confidence, plus the original OCR text for human verification.
- Offline glossary translation remains privacy-preserving; optional local IndicTrans2 can be enabled for higher-quality narrative translation with `INDICTRANS2_MODEL`.

### KNN Entity Resolution
- Replaced the simpler matcher with a character n-gram KNN candidate ranker.
- Combines KNN cosine similarity, lexical name similarity and identifier similarity.
- Exact normalized identifiers receive a strong score boost.
- Every candidate includes match confidence, rationale and `human_confirmation_required=true`.
- No automatic entity merge is performed by the KNN resolver.

### Security / Roles
Exactly three roles are supported:
- **Admin** — full investigation plus user/case administration.
- **Investigator** — upload/review/verify, graph analytics, timeline, Copilot, evidence and reports.
- **Sub-Officer** — read/search/timeline/graph review and case notes.

Security upgrades:
- PBKDF2-SHA256 password hashing with per-user salts.
- Username/account ID change with current-password verification and safe character validation.
- Password change and demo recovery-token reset flow.
- Optional TOTP MFA using the Python standard library (no third-party MFA package required).
- MFA setup/enable/disable endpoints and login challenge support.
- Admin user creation, role changes, password resets and account deactivation.
- Last-active-admin safeguard prevents accidental removal of the final administrator.
- Chained audit entries now include login, MFA and admin lifecycle actions.
- Renamed/deactivated demo accounts are not silently recreated on later logins.

### UI/UX
- Dark and light workspace themes now use a consistent purple investigation-console palette.
- Role-specific sidebar guidance and write permissions reduce confusing controls for Sub-Officers.
- Admin Portal is only created as a tab for Admin users.
- Evidence write controls are hidden/disabled for Sub-Officers while read/download access remains visible.
- Case/task creation is limited to Admin/Investigator users in the UI.

### Validation
- Automated test suite: 21 tests passing in this build.
- `py_compile` validation completed for the modified backend, frontend, OCR, translation and security modules.
- Full package validation excludes runtime caches and the mutable SQLite demo database from the clean release bundle.

### Important deployment note
This is an prototype platform, not a certified law-enforcement production system. Real deployment still requires approved identity infrastructure, encrypted secrets, TLS, hardened database/storage configuration, penetration testing, privacy/legal review, operational access controls and validated domain-specific models.

AI outputs are investigative signals, not determinations of guilt. Human review remains mandatory for evidence promotion, identity resolution and cross-FIR interpretation.

## v4.3.2 — Light Theme + Runtime Hardening
- Fixed Timeline `category_symbols` load-order NameError.
- Centralized timeline category symbols/palette above all legend/chart usage.
- Reworked light theme for high-contrast white/purple readability across inputs, dropdowns, multiselect tags, alerts, metrics, expanders, sidebar, buttons, cards and charts.
- Timeline/analytics/graph Plotly charts now use the active theme text color instead of dark-only text.
- First-run console theme defaults to Light for reliable visibility; Dark remains selectable.
- Demo identity bootstrap now creates any missing bundled demo account without silently reactivating or overwriting existing accounts.
- Bumped frontend/backend expected API version to 4.3.2.
