# DARK CRIMENET

## AI-Assisted Criminal Network Investigation Intelligence

DARK CRIMENET is a case-scoped investigation intelligence platform that helps connect, verify, and analyse information distributed across multiple First Information Reports (FIRs) and related investigation records.

It combines document intelligence, OCR, rule-based NLP, classical machine learning, entity resolution, human verification, knowledge graphs, and explainable analytics into a single investigation workspace.

> **Decision-support prototype — synthetic data only.**
>
> DARK CRIMENET produces investigative signals and review priorities. It does not determine guilt or replace investigator judgement.

---

## Overview

Investigation data is often distributed across FIRs, documents, communication records, financial transactions, vehicle sightings, locations, and other sources.

The same person, phone number, vehicle, account, location, or event may appear across multiple records with spelling variations, OCR errors, incomplete identifiers, or different representations.

DARK CRIMENET creates a **case-scoped, evidence-backed investigation graph** that allows these records to be reviewed together while keeping uncertain relationships separate until they are verified.

### Core Workflow

**Collect → Understand → Connect → Analyse → Detect → Explain → Investigate → Verify → Report**

---

# Key Features

## FIR & Document Intelligence

- Multi-FIR investigation workspaces
- PDF, DOCX, TXT, CSV, XLSX and image ingestion
- Scanned-document OCR using Tesseract
- English, Hindi and Marathi OCR
- OCR preprocessing and confidence information
- Role-aware FIR field extraction
- Original document/text preservation
- Evidence-linked extraction
- Structured intelligence export

---

## Human-in-the-Loop Entity Resolution

Potentially recurring entities are identified using:

- Character n-gram TF-IDF
- KNN / nearest-neighbour matching
- Lexical similarity
- Identifier matching
- Normalised identifiers
- Explainable candidate scoring

Entity resolution produces **candidates rather than automatic identity decisions**.

Investigators can:

- Create relationships
- Link entities
- Ignore candidates
- Verify relationships
- Reject relationships

Only verified information is promoted into the investigation graph.

---

## Knowledge Graph

Verified investigation information is represented as a case-scoped graph.

Supported entities include:

- Person
- Phone
- Vehicle
- Account
- Location
- Organisation
- FIR
- Event

Graph capabilities include:

- Neighbourhood exploration
- 1–3 hop analysis
- Shortest paths
- Hidden indirect connections
- Relationship filtering
- Cross-FIR relationships
- Evidence-key tracing
- Case-scoped graph analysis

The graph uses **NetworkX** locally, with an optional Neo4j configuration path.

---

## Network Intelligence

DARK CRIMENET provides graph-based investigation analytics including:

- Degree centrality
- Betweenness centrality
- PageRank
- Eigenvector centrality
- Community detection
- Shortest paths
- Hidden connections
- Link prediction
- Network relationship analysis

These outputs are intended to help investigators identify relevant structures and relationships for further review.

---

## Timeline Intelligence

Investigation events are organised into a case-scoped timeline.

Supported activity includes:

- Calls
- Transactions
- Meetings
- Vehicle sightings
- Locations
- FIR events
- Other dated investigation events

The timeline supports:

- FIR-based swimlanes
- Event categories
- Range filtering
- Event inspection
- Timeline focus
- Filtered CSV/JSON export
- Related-entity timeline views

---

## Geographic Intelligence

Location-based investigation analysis includes:

- Event mapping
- Repeated locations
- Co-location
- Movement sequences
- Geographic relationships
- Location-based investigation views

The frontend uses **Leaflet** for map visualisation.

---

## CDR & Communication Analysis

Communication intelligence includes:

- Call counts
- Contact pairs
- Communication frequency
- Contact intensity
- Relationship-level call analysis
- CDR event timelines

The same case-scoped representation is used across communication and graph analysis.

---

## Financial Intelligence

Financial investigation analysis includes:

- Transaction relationships
- Transaction amounts
- High-value transfers
- Fan-in/fan-out patterns
- Multi-step transaction flows
- Possible pass-through relationships
- Circular-flow candidates
- Financial network analysis

---

## Anomaly Detection

DARK CRIMENET uses classical unsupervised methods including:

- Isolation Forest
- Local Outlier Factor

Anomaly signals can incorporate:

- Network position
- Transaction activity
- Event frequency
- Communication activity
- Other case-derived features

> Anomaly scores are **investigation review signals**, not probabilities of guilt.

---

## Explainable Investigation Signals

The platform can surface:

- Anomalies
- Contradictions
- Missing-link candidates
- Network bridges
- Repeated activity
- Relationship candidates
- Investigation priorities

Signals retain contributing factors, model information, and source references where available.

---

## Cross-FIR Intelligence

Multiple FIRs can be uploaded into the same investigation workspace.

The system can identify explainable shared evidence keys such as:

- Phone numbers
- Vehicles
- Accounts
- Serial identifiers

Cross-FIR analysis provides:

- FIR relationship candidates
- Evidence-key inventory
- Per-FIR tracing
- Relationship network visualisation
- Connection-only path analysis

The system does **not silently merge uncertain person identities** simply because their names are similar.

---

## Social Media Intelligence

DARK CRIMENET includes a Social Media Intelligence workflow for importing and analysing structured social-media-related investigation information.

The feature is designed as an investigation-support tool and does not perform autonomous identity verification or facial recognition.

---

## Surveillance Reports

The platform provides a dedicated Surveillance Reports workflow for organising and reviewing surveillance-related investigation records within the case context.

---

## Investigation Copilot

DARK CRIMENET includes a local, graph-aware investigation Copilot.

It operates on available case information and can assist with queries such as:

- Case summaries
- Entity relationships
- FIR relationships
- Shortest paths
- Centrality
- Communities
- Calls
- Financial relationships
- Investigation signals
- Contradictions

The Copilot is designed to remain grounded in available investigation data rather than inventing evidence.

---

## Evidence Management

The Evidence Vault provides:

- Evidence records
- SHA-256 integrity verification
- Pending/verified evidence states
- Original/current hash comparison
- Authorised evidence retrieval
- Case-scoped evidence identifiers

Evidence is not automatically treated as verified simply because it was uploaded.

---

## Data Integrity & Audit

DARK CRIMENET includes a signed, hash-chained integrity ledger.

Security and audit capabilities include:

- SHA-256 evidence hashing
- HMAC-based audit integrity
- Signed ledger records
- Tamper detection
- Offline ledger verification
- Persistent audit trails
- Case-scoped access control
- Role-aware permissions

The repository includes an offline ledger verification utility:

~~~text
scripts/verify_ledger.py
~~~

---

# Security

The platform includes multiple security controls:

- Role-based access control
- Case-level authorisation
- Session management
- Password hashing
- Login rate limiting
- TOTP-based two-step sign-in
- Recovery codes
- Evidence hashing
- Signed integrity ledger
- Audit trails
- Upload validation
- Content Security Policy
- Security headers
- Input validation

### Supported Roles

- **Admin** — user, role, and case administration
- **Supervisor** — supervisory investigation access
- **Investigator** — FIR ingestion, review, graph, analytics, evidence, Copilot and reports
- **Auditor** — masked read-only investigation and audit access
- **Demo** — controlled demonstration access

Production deployment should use approved identity management, strong secret management, TLS, least privilege, and environment-specific security controls.

---

# AI / Machine Learning Approach

DARK CRIMENET deliberately uses lightweight, explainable techniques rather than depending on a large language model for the core workflow.

### Rule-Based Intelligence

Used for:

- FIR field extraction
- Identifier detection
- Validation
- Legal terminology
- Multilingual lexicons
- Relationship hints
- Contradiction checks
- Evidence validation

### Classical Machine Learning

Used for:

- Contextual entity scoring
- Entity resolution
- Link prediction
- Anomaly detection

Techniques include:

- TF-IDF character n-grams
- Logistic Regression
- KNN
- Isolation Forest
- Local Outlier Factor

The contextual FIR model is a **secondary confidence signal** for deterministic extraction and is not intended to replace human verification.

### Graph Analytics

NetworkX provides:

- Centrality
- Communities
- Paths
- Relationship analysis
- Cross-FIR graph analysis

> **No external GPT/LLM API is required for the core investigation workflow.**

---

# Multilingual FIR Intelligence

The document pipeline supports:

- English
- Hindi
- Marathi
- Mixed English/Hindi/Marathi documents

The workflow preserves original extracted information and provides an English view for investigator convenience.

It exposes:

- Language candidates
- OCR confidence
- Translation method/confidence
- Critical-field validation status
- Detected events
- Evidence-backed relationship candidates
- Structured JSON intelligence

An optional local IndicTrans2 configuration can provide higher-quality narrative translation.

> Machine OCR and translation are assistive. Critical names, identifiers, dates, locations, and legal sections should always be checked against the original document.

---

# Demonstration Dataset

DARK CRIMENET uses a deterministic **synthetic investigation dataset** for demonstration and testing.

The default demo generator can create:

- 270 entities
- 349 evidence-linked graph relationships
- 2,530 timeline events
- 1,100 synthetic CDR/communication events
- 850 synthetic financial transactions
- Vehicle sightings
- Meetings
- Geospatial events
- 8 synthetic cases
- Review tasks
- Investigation signals
- Synthetic evidence records

The generated scenario is deterministic, allowing the same investigation scenario to be reproduced.

> **No real criminal-investigation dataset is required to run the demonstration.**

---

# Cross-FIR Demo

Synthetic interconnected FIR samples are available under:

~~~text
demo_dataset/cross_fir_samples/
~~~

The sample FIRs intentionally share synthetic identifiers while retaining different FIR information.

### Demo Flow

1. Start DARK CRIMENET.
2. Sign in using the demo account.
3. Create or select an FIR investigation workspace.
4. Open **Ingestion & Review**.
5. Upload multiple FIR samples.
6. Process and inspect the extraction results.
7. Review OCR confidence and relationship candidates.
8. Submit human verification.
9. Open **Knowledge Graph**.
10. Open **Analytics → Cross-FIR**.
11. Inspect the shared evidence keys and relationships.
12. Use the Copilot to query the verified relationship.

---

# Technology Stack

## Backend

- Python
- FastAPI
- Uvicorn
- Pydantic
- SQLAlchemy
- SQLite

## Machine Learning / Data Processing

- scikit-learn
- NumPy
- pandas

## Graph Analytics

- NetworkX
- Optional Neo4j configuration

## Document Intelligence

- Tesseract OCR
- pytesseract
- OpenCV
- Pillow
- PyMuPDF
- pypdf
- python-docx
- openpyxl

## Frontend

- HTML
- CSS
- JavaScript
- Cytoscape.js
- Leaflet

## Security

- SHA-256
- HMAC
- Ed25519
- PBKDF2
- Fernet
- PyJWT
- TOTP

---

# Architecture

~~~text
                    ┌──────────────────────┐
                    │      FIR / DATA      │
                    │ PDF DOCX CSV XLSX    │
                    │ Images CDR Finance   │
                    └──────────┬───────────┘
                               ↓
                    ┌──────────────────────┐
                    │  INGESTION + OCR     │
                    │ Document Intelligence │
                    └──────────┬───────────┘
                               ↓
                    ┌──────────────────────┐
                    │ EXTRACTION + NLP     │
                    │ Entities / Events     │
                    └──────────┬───────────┘
                               ↓
                    ┌──────────────────────┐
                    │ ENTITY RESOLUTION    │
                    │ TF-IDF / KNN / Rules │
                    └──────────┬───────────┘
                               ↓
                    ┌──────────────────────┐
                    │ HUMAN VERIFICATION   │
                    └──────────┬───────────┘
                               ↓
                    ┌──────────────────────┐
                    │ VERIFIED CASE GRAPH  │
                    │ NetworkX / Neo4j     │
                    └──────────┬───────────┘
                               ↓
          ┌────────────────────┼────────────────────┐
          ↓                    ↓                    ↓
     ┌──────────┐        ┌───────────┐       ┌───────────┐
     │ Network  │        │ Timeline  │       │ CDR /     │
     │ Analysis │        │ Geography │       │ Finance   │
     └────┬─────┘        └─────┬─────┘       └─────┬─────┘
          └────────────────────┼────────────────────┘
                               ↓
                    ┌──────────────────────┐
                    │ EXPLAINABLE SIGNALS  │
                    │ Anomaly / Links /    │
                    │ Contradictions       │
                    └──────────┬───────────┘
                               ↓
                    ┌──────────────────────┐
                    │ REPORTS + AUDIT      │
                    │ PDF DOCX CSV JSON    │
                    └──────────────────────┘
~~~

---

# Project Structure

~~~text
CRIMENET/
│
├── ai_engine/
├── backend/
├── data_pipeline/
├── demo_dataset/
├── docs/
├── evidence_store/
├── graph_engine/
├── reports/
├── sample_firs/
├── scripts/
├── security/
├── tesseract_data/
├── web/
│
├── .dockerignore
├── .env.example
├── .gitignore
├── DEMO_ACCOUNTS.md
├── Dockerfile
├── QUICK_START_WINDOWS.md
├── RELEASE_NOTES.md
├── RUN_WINDOWS.bat
├── VALIDATION_REPORT.md
├── VERSION.txt
├── docker-compose.yml
├── pyproject.toml
├── requirements-dev.txt
├── requirements.txt
├── run.sh
└── README.md
~~~

---

# Installation

## Windows — Recommended

Run:

~~~text
RUN_WINDOWS.bat
~~~

The launcher detects Python 3.11+, creates a local virtual environment, installs dependencies, starts the application, and opens the local interface.

The application runs as a **single FastAPI-served web application**:

~~~text
http://localhost:8000
~~~

API documentation:

~~~text
http://localhost:8000/docs
~~~

---

## Manual Setup

### 1. Create a virtual environment

~~~bash
python -m venv venv
~~~

### 2. Activate it

Windows PowerShell:

~~~powershell
.\venv\Scripts\Activate.ps1
~~~

If required:

~~~powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
~~~

### 3. Install dependencies

~~~bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
~~~

### 4. Start the application

~~~bash
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
~~~

Open:

~~~text
http://localhost:8000
~~~

---

## Linux / macOS

~~~bash
./run.sh
~~~

Or manually:

~~~bash
pip install -r requirements.txt
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
~~~

---

## Docker

Copy the environment template:

~~~bash
cp .env.example .env
~~~

Then run:

~~~bash
docker compose up --build
~~~

The container serves both the frontend and API on:

~~~text
http://localhost:8000
~~~

---

# Demo Dataset

After signing in as an Admin:

**Admin Portal → Demo Lab → Load synthetic demo dataset**

The demo loader uses the deterministic generator:

~~~text
scripts/seed_demo.py
~~~

Alternatively:

~~~bash
python scripts/seed_demo.py --reset
~~~

For a full demo reset:

~~~bash
python scripts/seed_demo.py --reset --yes-delete-everything
~~~

The destructive reset command is restricted to demo mode.

---

# OCR Setup

Check the local Tesseract installation using:

~~~powershell
.\venv\Scripts\python.exe scripts\check_ocr.py
~~~

DARK CRIMENET supports:

- `eng`
- `hin`
- `mar`

If Tesseract is installed outside the standard Windows location, configure:

~~~text
TESSERACT_CMD
~~~

with the full path to `tesseract.exe`.

---

# Model / Extraction Validation

The repository includes an offline contextual scorer used as a secondary signal for FIR extraction.

Train it with:

~~~bash
python scripts/train_fir_models.py
~~~

The resulting model is stored under:

~~~text
ai_engine/models/fir_entity_context.joblib
~~~

This model supports the deterministic extraction workflow and does not replace human verification.

---

# Reports

Investigation reports can be generated in:

- PDF
- DOCX
- CSV
- JSON

Reports remain scoped to the active investigation workspace.

---

# Responsible Use

DARK CRIMENET is intended as an investigation decision-support prototype.

It should not be used to:

- Automatically determine guilt
- Automatically establish criminal responsibility
- Replace trained investigators
- Treat anomaly scores as proof
- Treat predicted links as verified facts
- Treat OCR or translation as unquestionable evidence
- Make decisions without reviewing the underlying evidence

All significant investigative signals should be reviewed against their source evidence.

---

# Limitations

- Demonstration data is synthetic.
- Real-world investigative accuracy has not been established.
- OCR performance depends on document quality.
- Entity-resolution candidates require human verification.
- Anomaly detection produces review signals rather than guilt probabilities.
- Link prediction identifies structurally plausible relationships rather than proving relationships.
- Translation is assistive and should be checked against the original document.
- Production deployment would require additional infrastructure, security, governance, identity, and validation controls.

---

# Project Status

**Current Release:** v5.3.0

**Application:** FastAPI + single-page web interface

**Graph:** NetworkX with optional Neo4j configuration

**Database:** SQLite

**Data:** Deterministic synthetic investigation dataset

**Deployment:** Local / Docker

---

# Documentation

Additional project documentation is available in the repository:

- `QUICK_START_WINDOWS.md` — Windows setup
- `DEMO_ACCOUNTS.md` — demo roles and access
- `RELEASE_NOTES.md` — version history and detailed changes
- `VALIDATION_REPORT.md` — validation information
- `docs/` — security, user guide, and supporting documentation

---

# Disclaimer

DARK CRIMENET is a research-oriented decision-support prototype.

It does not make legal determinations, establish criminal responsibility, or replace trained investigators or legal authorities.

All investigative signals should be independently reviewed against the original evidence.
