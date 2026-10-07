# DARK CRIMENET

## AI-Assisted Criminal Network Investigation Intelligence

DARK CRIMENET is an **AI-assisted, evidence-backed investigation intelligence platform** for connecting, verifying, analyzing, and understanding information across FIRs and related investigation records.

It combines **document intelligence, NLP, entity resolution, knowledge graphs, graph analytics, anomaly detection, timeline intelligence, geographic analysis, communication analysis, evidence verification, and explainable investigation signals** into a case-scoped workflow.

The system is designed around a simple principle:

> **Automate the analysis, but keep investigators in control of verification and decisions.**

> **Data:** Synthetic data for development and demonstration  
> **Backend:** Python / FastAPI  
> **Frontend:** Vanilla JavaScript  
> **Graph:** NetworkX with optional Neo4j support  
> **Database:** SQLite  
> **Deployment:** Local / Docker

---

## What It Solves

Investigation information is often distributed across FIRs, documents, communication records, financial records, vehicles, locations, and other sources.

Working with these records manually makes it difficult to:

- identify recurring entities across FIRs
- discover indirect relationships
- resolve potentially matching identities
- understand how networks evolve over time
- connect communication and financial activity
- identify unusual patterns
- trace analytical signals back to evidence
- distinguish machine-generated candidates from verified information

DARK CRIMENET converts these fragmented records into a **case-scoped investigation intelligence layer** where information can be extracted, matched, reviewed, verified, connected, and analyzed.

---

# System Workflow

```text
FIRs & Investigation Records
            │
            ▼
┌───────────────────────────┐
│ Ingest & Understand       │
│ OCR + Document Processing │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ Extract & Normalize       │
│ Entities + Events         │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ Entity Resolution         │
│ Candidate Matching        │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ Human Verification        │
│ Create / Link / Reject    │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ Verified Knowledge Graph  │
└─────────────┬─────────────┘
              │
       ┌──────┼───────────────┐
       ▼      ▼       ▼       ▼
     Graph  Timeline  Geo   CDR / Finance
    Analysis          Intel   Analysis
       │      │       │       │
       └──────┴───────┴───────┘
              │
              ▼
┌───────────────────────────┐
│ Anomaly & Contradiction   │
│ Detection                 │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ Explainable Signals       │
│ + Investigation Copilot   │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ Evidence / Audit / Report │
└───────────────────────────┘
```

---

# Core Features

## 1. FIR & Document Intelligence

The platform can ingest multiple types of investigation records, including:

- PDF
- DOCX
- TXT
- CSV
- XLSX
- scanned documents and images

The document pipeline combines OCR, preprocessing, extraction, normalization, and validation to convert unstructured records into structured investigation data.

Supported languages include:

- English
- Hindi
- Marathi

---

## 2. Entity Extraction & Resolution

The system extracts investigation-relevant entities such as:

```text
Person
Phone
Vehicle
Account
Location
FIR
Organization
Event
Social Account
```

Potentially matching entities are identified using a combination of:

- normalized identifiers
- lexical similarity
- character n-gram TF-IDF
- KNN / nearest-neighbour matching
- identifier evidence
- contextual scoring

Candidate matches remain separate from verified identities until reviewed.

---

## 3. Human-in-the-Loop Verification

A major design decision is to keep automated predictions separate from verified investigation data.

```text
Extracted
    ↓
Candidate
    ↓
Human Review
    ↓
Verified
    ↓
Knowledge Graph
```

Investigators can:

- create relationships
- link entities
- verify candidates
- reject candidates
- ignore uncertain matches

This prevents an uncertain automated match from silently becoming a verified relationship.

---

# Knowledge Graph

Verified information is represented as a **case-scoped knowledge graph**.

### Entities

```text
Person
Phone
Vehicle
Account
Location
FIR
Organization
Event
Social Account
```

### Relationships

```text
CALLED
OWNED
USED
VISITED
MET
ASSOCIATED
REGISTERED_TO
LOCATED_AT
```

The graph provides a structured representation of how entities and events are connected across an investigation.

The system can also surface relationships across multiple FIRs when they share evidence keys or other verified connections.

---

# Graph Analytics

The graph engine provides several analytical capabilities:

- Degree centrality
- Betweenness centrality
- PageRank
- Eigenvector centrality
- Closeness centrality
- Community detection
- Shortest paths
- Hidden / indirect connections
- Link analysis
- Network evolution
- Network disruption analysis

These analyses help identify important nodes, communities, paths, and structural changes within an investigation network.

---

# Timeline Intelligence

Investigation events can be analyzed chronologically to identify:

- event sequences
- repeated activity
- temporal relationships
- activity surrounding important events
- gaps and unusual timing
- relationships between events across records

---

# Geographic Intelligence

Location and movement-related information can be analyzed to identify:

- repeated locations
- co-location
- geographic relationships
- vehicle sightings
- location-based connections
- activity around significant places

---

# Communication & Financial Analysis

### Communication Records

Communication data can be analyzed for:

- call volumes
- repeated contacts
- communication relationships
- top communication pairs
- temporal communication patterns

### Financial Records

Synthetic financial records can be analyzed to identify:

- transaction relationships
- connected accounts
- unusual transaction patterns
- financial links between entities

---

# AI & Machine Learning

DARK CRIMENET uses a combination of **rule-based intelligence, classical machine learning, and graph algorithms** rather than depending on an LLM for the core workflow.

| Component | Approach |
|---|---|
| Document extraction | Rule-based / structured extraction |
| OCR | Tesseract + image preprocessing |
| Entity normalization | Identifier and text normalization |
| Entity resolution | Character n-grams + TF-IDF + KNN + similarity evidence |
| Context scoring | Offline contextual ML models |
| Anomaly detection | Isolation Forest + Local Outlier Factor |
| Graph analysis | NetworkX |
| Community analysis | Graph community algorithms |
| Contradiction detection | Rule-based consistency checks |
| Investigation signals | Evidence-backed analytical scoring |

The architecture prioritizes **inspectability, reproducibility, and evidence-backed reasoning**.

---

# Explainable Investigation Signals

The platform does not treat an anomaly score or model prediction as a conclusion.

Investigation signals can include:

- anomaly scores
- contributing factors
- evidence references
- source records
- relationship context
- model/version information
- contradiction indicators

This allows an investigator to understand **why a signal was generated and where the underlying information came from**.

---

# Investigation Copilot

The platform includes a local, source-aware investigation Copilot for working with verified case information.

It supports investigation-oriented interactions such as:

- graph-oriented queries
- relationship exploration
- case summaries
- evidence-aware investigation questions
- analytical explanations

The Copilot operates within the available case information and does not replace the underlying verification workflow.

---

# Evidence & Audit

DARK CRIMENET treats evidence integrity and auditability as first-class parts of the system.

Implemented mechanisms include:

- SHA-256 evidence hashing
- evidence integrity verification
- hash-chained audit records
- signed integrity ledger
- Ed25519 signatures
- offline ledger verification
- source references for analytical signals

This creates a traceable connection between investigation information, analytical outputs, and their underlying evidence.

---

# Security

The platform includes application-level security controls such as:

- Role-based access control
- Case-level authorization
- Password management
- TOTP-based two-step authentication
- Session controls
- Login protection
- Evidence access controls
- Audit logging
- Masked auditor views
- Configurable application secrets

The architecture is designed so that investigation data is accessed within the context of an authorized case workspace.

---

# Technology Stack

### Backend

- Python
- FastAPI
- Uvicorn
- Pydantic
- SQLAlchemy
- SQLite

### AI / Data

- scikit-learn
- NumPy
- pandas
- NetworkX

### Document Intelligence

- Tesseract OCR
- pytesseract
- OpenCV
- Pillow
- PyMuPDF
- pypdf
- python-docx
- openpyxl

### Frontend

- HTML
- CSS
- JavaScript
- Cytoscape.js
- Leaflet

The frontend is a lightweight single-page application served through the FastAPI application.

### Security

- SHA-256
- HMAC
- PBKDF2
- Fernet
- JWT
- TOTP
- Ed25519

---

# Project Structure

```text
CRIMENET/
│
├── ai_engine/          # NLP and machine-learning components
├── backend/             # FastAPI application
├── data_pipeline/       # Document ingestion and processing
├── demo_dataset/        # Synthetic demonstration data
├── docs/                # Documentation
├── evidence_store/      # Evidence storage
├── graph_engine/        # Knowledge graph and graph analytics
├── reports/             # Report generation
├── sample_firs/         # Sample FIR/document inputs
├── scripts/             # Setup, demo and utility scripts
├── security/            # Authentication and security
├── tesseract_data/      # OCR language resources
├── web/                 # Frontend application
│
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── RUN_WINDOWS.bat
├── run.sh
├── QUICK_START_WINDOWS.md
├── VALIDATION_REPORT.md
├── RELEASE_NOTES.md
└── README.md
```

Runtime databases, secrets, private keys, generated evidence, and other local state are excluded from version control.

---

# Dataset

Real criminal investigation datasets are difficult to use because of privacy, legal, security, and access restrictions.

DARK CRIMENET therefore uses **synthetic data for development and demonstration**.

The synthetic environment can contain:

- people
- phone numbers
- vehicles
- accounts
- locations
- organizations
- FIRs
- events
- communication records
- financial transactions
- vehicle sightings
- evidence-linked relationships

The demonstration data is deterministic so that the investigation workflow can be reproduced consistently.

**No real personal or criminal investigation data is required to run the demonstration.**

---

# Cross-FIR Analysis

Multiple FIRs can be loaded into a single investigation workspace.

The system can identify shared evidence keys and candidate relationships across records, for example:

```text
FIR 01 ─────┐
            │
            ├── Shared Phone
            ├── Shared Vehicle
            ├── Shared Account
            └── Shared Identifier
            │
FIR 02 ─────┘
```

These relationships are surfaced as candidates and can be reviewed before becoming verified graph information.

The objective is **not** to automatically assume that matching records represent the same person.

---

# Multilingual FIR Intelligence

The document pipeline supports:

- English
- Hindi
- Marathi

OCR is supported for scanned and photographed documents using local Tesseract language resources.

The system can expose:

- OCR confidence
- extracted fields
- detected language
- translated information
- critical-field validation
- detected events
- relationship candidates
- structured output

Critical identifiers such as names, phone numbers, dates, and case numbers should always be checked against the original document.

---

# Running the Project

## Windows

The simplest option is:

```text
RUN_WINDOWS.bat
```

Or use the manual setup below.

### Create the environment

```bash
python -m venv venv
```

### Activate it

```powershell
.\venv\Scripts\Activate.ps1
```

### Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Start the application

```bash
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://localhost:8000
```

---

## Docker

Create the environment file:

```bash
cp .env.example .env
```

Then:

```bash
docker compose up --build
```

Open:

```text
http://localhost:8000
```

---

# OCR Setup

For local installations, Tesseract OCR and the required language data should be installed.

Supported language resources include:

```text
eng
hin
mar
```

Check OCR availability with:

```powershell
.\venv\Scripts\python.exe scripts\check_ocr.py
```

If Tesseract is installed in a non-standard location, configure:

```text
TESSERACT_CMD
```

The Docker environment provides the required OCR resources automatically.

---

# Demo Environment

The bundled synthetic dataset can be initialized with:

```bash
python scripts/seed_demo.py --reset
```

The generated environment is intended for demonstration and testing.

---

# Responsible Use

DARK CRIMENET is designed as an **investigation decision-support system**, not an autonomous decision-maker.

Its outputs should be interpreted as investigative signals and must not be treated as:

- proof of criminal activity
- proof of identity
- proof of guilt
- proof of innocence

Human review is required for important investigative decisions.

Any real-world deployment would require appropriate legal authorization, privacy controls, security review, governance, validation, and operational safeguards.

---

# Limitations

This project is a research and development prototype.

Important limitations include:

- Demonstration data is synthetic.
- Real-world documents can contain significantly more variation.
- OCR accuracy depends on document quality.
- Automated extraction can produce incorrect or incomplete information.
- Entity resolution produces candidate matches and is not identity proof.
- Analytical signals require human interpretation.
- Model performance depends on the quality and completeness of available records.
- Production deployment would require additional infrastructure, security, governance, and validation.

---

# Why This Architecture

The project is intentionally built as more than an isolated ML model.

The system connects:

```text
Documents
   ↓
NLP / Extraction
   ↓
Entity Resolution
   ↓
Human Verification
   ↓
Knowledge Graph
   ↓
Graph + Temporal + Geographic Analysis
   ↓
Anomaly Detection
   ↓
Explainable Signals
   ↓
Evidence + Audit
   ↓
Reports
```

This architecture allows the individual AI and data components to contribute to a larger end-to-end application rather than operating independently.

---

# Research Basis

The project builds on the problem area explored in:

**“AI-Enabled Smart FIR Drafting and Legal Validation System”**

DARK CRIMENET extends that FIR-focused problem toward investigation intelligence by focusing on:

- cross-FIR connections
- entity resolution
- evidence-backed relationship discovery
- case-scoped knowledge graphs
- network analysis
- timeline intelligence
- geographic intelligence
- communication and financial analysis
- anomaly detection
- explainable investigation signals
- human verification

---

# Project Status

**Current release:** `v5.3.0`

The current implementation provides an end-to-end investigation intelligence workflow covering:

- document ingestion
- multilingual OCR
- entity extraction
- entity resolution
- human verification
- case-scoped knowledge graphs
- cross-FIR analysis
- graph analytics
- timeline intelligence
- geographic intelligence
- communication analysis
- financial analysis
- anomaly detection
- contradiction detection
- explainable investigation signals
- evidence integrity
- audit trails
- signed ledger verification
- investigation Copilot
- report generation
- role-based access
- two-step authentication

Detailed implementation history is maintained in `RELEASE_NOTES.md`.

---

# Disclaimer

DARK CRIMENET is a research and development prototype.

All investigation data included with the project is synthetic or generated for demonstration purposes.

The system is intended to assist with organizing and analyzing investigation information. It does not establish criminal liability, identity, guilt, or innocence.

Any real-world use would require appropriate legal authorization, privacy protections, security controls, validation, governance, and human oversight.
