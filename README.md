# DARK CRIMENET

An AI-assisted, evidence-backed investigation intelligence prototype. v5.3.0 adds social media intelligence, surveillance reports, a tamper-proof signed ledger and role-based two-step sign-in, and gives the interface a new sidebar look. v5.0.0 redesigned the whole interface (landing page, animated workspace, command palette, light/dark themes) on top of the v4.5.0 localhost SPA, which replaced the Streamlit front end with a fast single-page web UI served by the API on one localhost port, and removed MFA and password-reset flows (two-step sign-in returned in v5.1.0). v4.4.0 added a full security and correctness hardening pass on top of v4.3.2, which is centered on clean FIR workspaces, explainable multi-FIR linkage, a clearer timeline, Hindi/Indic document intelligence, KNN entity resolution and hardened role-based security.

## Core workflow
Collect → Understand → Connect → Analyze → Detect → Explain → Investigate → Verify → Report

## v5.3.0 — Docket UI, sharper social media intelligence, ledger, two-step sign-in

- **New pages:** Social Media Intelligence (import-only), Surveillance Reports, Data Integrity (signed hash-chained ledger + offline verifier `scripts/verify_ledger.py`).
- **Two-step sign-in (TOTP):** required per role by the admin; QR enrolment at sign-in; recovery codes. See `docs/guide/two-step-sign-in.md`.
- **UI:** new sidebar design, back/forward buttons, and a create-case pop-up when a FIR is uploaded with no case.
- **Removed:** Pattern Lab, Cyber Forensics, cross-case hits, what-if sandbox, timeline Gaps tab.

## v5.0.0 — DARK CRIMENET

See `RELEASE_NOTES.md` and `docs/USER_GUIDE.md`. Sign in from the landing page (demo accounts in `DEMO_ACCOUNTS.md`); press Ctrl/Cmd+K anywhere for the command palette; the ? button opens the glossary.

## v4.5.0 — Fast localhost UI, simpler accounts

- **One port, no Streamlit.** The UI is a static single-page app (`web/`) served by FastAPI at <http://localhost:8000>. No page reruns, no separate UI process, no build step; Cytoscape (graph) and Leaflet (map) are vendored under `web/vendor/`, so it works offline (map tiles need internet, markers do not).
- **MFA removed entirely** (endpoints, database use, docs, `cryptography` dependency).
- **Password reset simplified.** The forced "change password at first sign-in" and the self-service forgot/reset flow are gone. Any signed-in user can still change their own password or username (Account button, top right); an Admin can set another user's password (Admin Portal → Users); and if nobody can sign in, run `python scripts/set_password.py USERNAME` on the server.
- **Fixed** the `name 'post' is not defined` error on Change password.
- Strict Content-Security-Policy (no inline scripts, no third-party code); all data is rendered via DOM text nodes (no `innerHTML`), enforced by a test.

## v4.4.0 — Hardening release

See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the full list. Highlights: no default passwords or hardcoded secret; DB-backed sessions that die on deactivation/role/password change; login rate limiting; per-case authorization; masked read-only auditor role; upload validation; keyed-hash audit chain with real tamper detection; isolated tests (70+, including regression tests for every reviewed vulnerability).

## v4.3.2 — What changed

- **New Workbench:** Admin/Investigator can start a clean FIR workspace without carrying forward stale graph/timeline state.
- **Load Neighborhood:** interactive, case-scoped nodes + edges with hop/type/relation filters and timeline focus synchronization.
- **Timeline Intelligence Workbench:** FIR swimlanes, category symbols, range zoom, event inspector, gap explanations and filtered CSV/JSON export.
- **Multi-FIR Intelligence:** evidence-key relationship matrix + FIR relationship network. FIRs remain separate and relationships remain explainable.
- **Hindi/Marathi:** bundled `eng`, `hin`, `mar`, OSD and Devanagari Tesseract assets; language confidence, orientation correction and original/translated views.
- **KNN Entity Resolution:** character n-gram KNN with lexical + identifier scoring, reasons and mandatory human confirmation.
- **Five roles:** Admin, Supervisor, Investigator, Auditor and Demo with role-aware UI and API permissions.
- **Account security:** username and password change, admin password set, and chained audit events.
- **Cleaner UI:** dark/light purple workspace themes and role-specific controls.

## One-click Demo Lab

After signing in as **Admin**, open **Admin Portal → Demo Lab** and choose **Load synthetic demo dataset** (Admin only, demo mode). The loader uses the deterministic synthetic generator bundled under `scripts/seed_demo.py`, preserves the three demo accounts, prevents duplicate loading, and automatically exposes the `[DEMO]` workspaces. The clean FIR workspace remains separate.

## Self-contained demonstration dataset

The repository ships with a deterministic synthetic investigation generator. **The default release does not preload that world into the active FIR workspace, and no external 8 GB+ dataset is required to run or demonstrate the project.**

Running `python scripts/seed_demo.py --reset` **only when explicitly requested** creates:
- 270 entities: people, phones, vehicles, accounts, locations, organizations and FIR/case entities
- 349 evidence-linked graph relationships
- 2,530 timeline events
- 1,100 synthetic CDR/communication events
- 850 synthetic financial transactions
- vehicle sightings, meetings and geospatial events across Delhi NCR
- 8 synthetic cases, review tasks and seeded investigation signals
- 3 hashed synthetic evidence records
- deliberate patterns for hidden-path discovery, anomaly detection and contradiction detection

The seed is deterministic, so another evaluator gets the same investigation scenario.

## Implemented in v4.1 (FIR-first workspace upgrade)
- Multi-source document/table ingestion: PDF, DOCX, TXT, CSV, XLSX and image OCR (Tesseract when available)
- Local document intelligence with entity extraction and conservative relationship hints
- KNN + character n-gram entity resolution with human confirmation
- Knowledge graph explorer with 1–3 hop neighborhoods, shortest paths and hidden indirect paths
- Degree, betweenness, PageRank and eigenvector centrality
- Community detection
- Timeline and geospatial event views
- Multivariate Isolation Forest + Local Outlier Factor anomaly scoring
- Explainable signals with factors, model version and source references
- Contradiction detection for conflicting vehicle associations
- Evidence vault with SHA-256 integrity verification
- Persistent chained audit trail
- Case/task management
- Graph-aware local investigation copilot
- Structured investigation reports with JSON, PDF, DOCX and CSV downloads
- Optional Neo4j configuration path with NetworkX local fallback
- Responsible-AI and human-in-the-loop framing
- Analytics includes an integrated Cross-FIR Intelligence panel with explainable matching, evidence-key inventory and per-FIR tracing
- `INTERLINKED_VIA` graph visualization plus connection-only shortest-path highlighting
- Robust AI extraction preview with candidate-ID reconciliation and malformed-edge protection
- Case-scoped evidence identifiers and end-to-end verification isolation

### Interconnected FIR sample files
The folder `demo_dataset/cross_fir_samples/` contains two synthetic FIR text files designed to produce an explainable cross-FIR link. Upload **both files into the same FIR investigation workspace**, process them, and verify both. They intentionally share phone `9876543210`, vehicle `UP14 AB 4521`, serial `SN-CRIME-7788` and account `ACCT-445566` while keeping different FIR numbers, complainants and stations.

## Demo accounts and role access

See `DEMO_ACCOUNTS.md` for the exact Admin / Investigator / Supervisor login details, permissions and the suggested demonstration order.

## Run locally

### Windows — recommended one-command launcher

Run `RUN_WINDOWS.bat` from the project folder. It detects Python 3.11+, creates a local `venv`, installs dependencies, starts the app and opens <http://localhost:8000>. Everything (UI + API) runs in one process on one port.

Manual start (any OS): `pip install -r requirements.txt`, then

```bash
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

and open <http://localhost:8000>. Linux/macOS: `./run.sh`. Docker: `docker compose up --build` (copy `.env.example` to `.env` first).

**Fast local reuse:** on the same Windows machine and Python installation you can copy an existing `venv` into the extracted folder to save setup time; otherwise recreate it with `python -m venv venv`.

### Clean release behavior and first run

The ZIP does **not** ship a database, secrets or evidence. On first start the API creates a clean database and, **only if the users table is empty**, bootstraps accounts (`admin`; plus `investigator` when `APP_ENV=demo`). Passwords come from `ADMIN_PASSWORD` / `INVESTIGATOR_PASSWORD`; anything not set is generated randomly, printed **once** in the API console, written to `initial_credentials.txt` (mode 0600, git-ignored) There is no built-in default password. Change it any time from the Account button.

Configure via `.env` (see `.env.example`). `SECRET_KEY` is mandatory in production; in demo mode a random one is generated into `.runtime_secret`. Load the synthetic dataset from **Admin Portal → Demo Lab** (demo mode only). `python scripts/seed_demo.py --reset --yes-delete-everything` is destructive and refuses to run outside `APP_ENV=demo`.

To pre-load the synthetic dataset from the command line: `python scripts/seed_demo.py --reset --yes-delete-everything` (demo mode only, destructive).

## Docker

```bash
docker compose up --build
```

One container serves the UI and API on port 8000 (bound to 127.0.0.1). Data lives in the `dcn-data` volume; load the demo dataset from Admin Portal → Demo Lab.

## Notes
- The included data is synthetic and intended for demonstration only.
- AI outputs are investigative signals, not findings of guilt.
- Production deployment should replace demo credentials, use strong secret management, TLS, an approved identity provider (SSO/MFA), least privilege and approved government security controls.
- Neo4j is optional; the demo runs on SQLite + NetworkX without external infrastructure.

## Real-world FIR / scanned-document intelligence

The document pipeline is designed for non-perfect investigation documents. Image and scanned-PDF input uses local Tesseract OCR plus preprocessing (EXIF orientation handling, document/perspective correction when OpenCV is available, contrast enhancement, sharpening, thresholding and fallback OCR passes). OCR confidence and validation warnings remain visible so uncertain fields are explicitly sent to human review instead of silently corrected.

First supported languages: English, Hindi and Marathi, including mixed English/Hindi/Marathi OCR. FIR understanding is role-aware and extracts fields such as FIR/case number, jurisdiction, police station, dates/times, complainant, relative, investigating officer, suspect description, occurrence location, stolen property, law sections and conservative incident-event candidates.

Docker installs the `eng`, `hin` and `mar` Tesseract language packs automatically. Local installations must have those Tesseract trained-data packs available. On Windows, the app automatically checks common Tesseract installation paths, so Tesseract does not have to be on PATH. If installed elsewhere, set `TESSERACT_CMD` to the full path of `tesseract.exe`.

### Windows OCR check
After installing the Python requirements, run:

```powershell
.\venv\Scripts\python.exe scripts\check_ocr.py
```

If Tesseract is installed in the normal `C:\Program Files\Tesseract-OCR\` location, DARK CRIMENET finds it automatically. If it is installed elsewhere, set `TESSERACT_CMD` to the full `tesseract.exe` path. English, Hindi and Marathi require `eng`, `hin` and `mar` trained-data packs respectively.

## Multilingual FIR intelligence (English + Hindi + Marathi)

The document-intelligence workflow supports English, Hindi and Marathi OCR. It preserves the original extracted values and also returns an **English view** for investigator convenience.

In the **Ingestion & NLP** tab, use **View extracted information** to switch between:
- Original FIR language
- English translation
- Side-by-side

The default translation path is privacy-preserving and offline. It translates common FIR/legal terminology and transliterates remaining Devanagari names/addresses; identifiers, phone numbers, dates and case numbers are preserved. It is intentionally marked as assistive and review-first.

For higher-quality full narrative translation, a local IndicTrans2 model can be configured with `INDICTRANS2_MODEL`. The application does **not** auto-download a translation model because investigative documents may be sensitive and the project should remain runnable offline. AI4Bharat's IndicTrans2 family supports Hindi and Marathi to English; see the official project documentation for model setup.

The extraction result also exposes:
- language candidates
- OCR confidence
- translation method/confidence
- critical-field validation queue
- detected events
- evidence-backed relationship candidates
- JSON export of structured intelligence

Never treat machine OCR/translation as independently verified evidence. Verify critical names, identifiers, dates, locations and legal sections against the original document.


## Multilingual FIR intelligence (v4.1 refinement)
- English, Hindi and Marathi OCR with language-specific ensemble passes.
- Scanned/photographed FIR preprocessing, orientation correction and OCR confidence.
- Role-aware FIR fields with explicit missing/verification status.
- English view preserves identifiers and transliterates names instead of translating them.
- Offline field-aware Hindi/Marathi translation; optional local IndicTrans2 can be configured with `INDICTRANS2_MODEL` for higher-quality narrative translation.
- Original OCR text remains available for evidence comparison; AI extraction is never silently promoted to verified evidence.

## FIR-first investigation workspaces

The active investigation selector scopes the graph, relationships, timeline, map, alerts, evidence, entity resolution, analytics, Copilot and reports to one case. Uploaded FIRs are persisted as document records plus hashed evidence records. Multiple verified FIRs in the same workspace are interlinked through explainable shared identifiers such as phone, vehicle and account references; no uncertain person identity is silently merged.

### Multi-FIR demo workflow
1. Select or create an investigation case (top bar / **Cases & Notes**).
2. Open **Ingestion & Review** and upload several FIRs/documents together.
3. Keep them in the same investigation case/group.
4. Inspect extracted fields, OCR confidence, translation and relationship candidates.
5. Submit human verification.
6. Re-open **Knowledge Graph**, **Analytics**, **Timeline & Map**, **Signals & Evidence**, **AI Copilot** and **Reports & Audit** — each view now reads the active case workspace rather than the global seed dataset.
7. Use **Related FIRs** in a document's review panel to inspect evidence-key matches.

## Local model / extraction validation

`python scripts/train_fir_models.py` trains the small offline contextual scorer stored at `ai_engine/models/fir_entity_context.joblib`. It is a secondary confidence signal for the deterministic FIR extractor, not a replacement for human verification or a claim of transformer-level NER accuracy.

## API v4.1

Key endpoints added/refined for the FIR-first flow include:
- `/documents/extract` — persist extraction, evidence hash and case/group association
- `/documents/{document_id}/review` — human Create/Link/Ignore + Verify/Reject flow
- `/documents/{document_id}/related` — explainable related-FIR ranking within the case
- `/graph/summary`, `/graph/centrality`, `/graph/communities`, `/graph/hidden-connections` — case-scoped graph intelligence
- `/models/status` — local model/version/safety registry
- `/report/{case_number}/pdf`, `/docx`, `/csv` — export formats

The demo database is intentionally synthetic/authorized-only.

## v4.1.3 investigation-workspace behavior

The investigator console opens in a selected FIR investigation workspace rather than the global seeded graph. Upload batches can create a dedicated workspace automatically; multiple uploaded FIRs remain together for review and are interlinked only by explainable shared evidence keys after verification. The UI refreshes to the resulting workspace after processing.

CDR analytics accepts both canonical and legacy field names, and the API exposes a friendly `/` status endpoint in addition to `/health` and `/docs`.


## Investigation intelligence endpoints
- `GET /timeline` — case-scoped enriched timeline and daily event rollups.
- `GET /fir-relations` — explainable verified-FIR relationship candidates and graph-link status.
- `GET /analytics/overview` — consolidated case analytics.
- `GET /evidence/{evidence_id}/download` — authorized evidence retrieval.

## v4.2.0 focused verification workflow

For a clean FIR-first test, upload documents into the `[FIR]` workspace (for example `CASE-FIR-WORKSPACE`), **not** a `[DEMO]` seeded case. The files under `demo_dataset/cross_fir_samples/` are synthetic source documents intended to be uploaded into an FIR workspace; they are not pre-indexed into that workspace.

### Single-FIR test
1. Start the app and open http://localhost:8000.
2. Create a workbench under **Cases & Notes** (e.g. `CASE-FIR-WORKSPACE`) and select it in the top bar.
3. Open **Ingestion & Review**.
4. Upload one FIR (`.txt`, `.pdf`, `.docx` or supported image).
5. Process/extract the document.
6. Inspect the Extraction Preview and critical fields.
7. Verify the document and approve the extracted entities/relationships.
8. Confirm the entity counts change and inspect Knowledge Graph, Timeline, Analytics and Copilot.

### Two-FIR interlink test
1. Keep the same `[FIR]` workspace selected.
2. Upload both `demo_dataset/cross_fir_samples/FIR_LINK_01.txt` and `FIR_LINK_02.txt` together.
3. Process both documents.
4. Review and verify both FIR documents.
5. Open **Signals & Evidence → FIR board** or **Analytics → Cross-FIR** and inspect **FIR ↔ FIR relationship intelligence**.
6. The synthetic pair intentionally shares phone, vehicle, account and serial identifiers. The UI should show those shared keys and a strong candidate/verified link rather than merging the two FIRs into one person.
7. Ask Copilot: `Show FIR relationships` to verify the same relationship is available through the grounded Copilot.

### Timeline related-entity test
Select one entity and choose **Related entities (1-hop)**. The timeline includes the selected entity plus only its directly connected verified graph neighbors and their events. It does not expand to the whole workspace. **Primary entity only** is available when only the selected entity's own event rows should appear. 

### Evidence integrity test
Upload an evidence file in **Signals & Evidence → Evidence vault**. A new record starts as **Pending verification**, not Verified. Use **Verify** only after reviewing it; a successful SHA-256 match changes the status to Verified and shows the original/current hash comparison.

### Performance note
The UI is a static single-page app that talks to the JSON API directly, so navigation does not re-run any server-side script; heavy analytics are computed only when a tab is opened.


## v4.2.0 roles
- **Admin**: user lifecycle, role assignment, case administration and full investigation access.
- **Investigator**: FIR ingestion/review, graph, analytics, timeline, evidence, Copilot and reports.
- **Auditor**: masked read-only case views, audit log and oversight dashboard.

Bootstrap accounts are described under *Clean release behavior and first run*. Case access is per-case: restricted cases are visible only to their creator, explicit members and admins (admins manage members/visibility under `/admin/cases/{case}/…`); the bundled synthetic cases are `shared`.


## Security notes (v4.5.0)

Admin, Supervisor, Investigator, Auditor and Demo are the only roles; see [docs/security.md](docs/security.md) for the permission matrix, threat notes and what is still required for production. There is no self-service password reset. Two-step sign-in (authenticator app, TOTP) is role-based: optional in demo mode, required for admin/supervisor/investigator by default elsewhere (see docs/security.md); a production deployment should still prefer an approved identity provider. Passwords are changed by the user (Account), set by an Admin, or set on the server with `scripts/set_password.py`.
