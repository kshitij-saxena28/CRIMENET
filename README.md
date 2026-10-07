# DARK CRIMENET

## AI-Assisted Criminal Network Investigation Intelligence

DARK CRIMENET is a case-scoped investigation intelligence platform designed to help investigators connect, verify, and analyse information distributed across multiple First Information Reports (FIRs) and related records.

The system combines document intelligence, OCR, rule-based NLP, classical machine learning, entity resolution, human verification, and graph analytics to build an evidence-backed investigation view.

> **Decision-support prototype — synthetic data only.**
>
> DARK CRIMENET is designed to assist investigation workflows. Its outputs are investigative signals and review priorities, not automatic conclusions of guilt or criminal responsibility.

---

## Overview

Investigations often involve information scattered across multiple FIRs, documents, and records.

The same person, phone number, vehicle, account, location, or event may appear across different records with spelling variations, OCR errors, or incomplete identifiers. Analysing these records independently makes it difficult to discover indirect relationships and understand how events connect over time.

DARK CRIMENET addresses this problem by creating a **case-scoped, evidence-backed knowledge graph** from verified information.

The overall workflow is:

**Ingest → Understand → Connect → Verify → Analyse → Detect → Explain → Report**

The central design principle is that uncertain information is **not automatically promoted to verified investigation data**. Candidate relationships and entity matches are reviewed before they become part of the verified graph.

---

## Problem Statement

Develop a case-scoped investigation system that can:

- Ingest multiple FIRs and related records
- Extract and normalise investigative information
- Identify people, roles, identifiers, locations, events, and other relevant entities
- Detect possible relationships between entities across records
- Resolve recurring entities using identifier and text similarity
- Require human verification before uncertain links enter the verified graph
- Represent verified information in a knowledge graph
- Analyse network, temporal, geographic, communication, and financial relationships
- Surface anomalies, contradictions, and possible missing links
- Preserve evidence references and audit information throughout the investigation

---

## Research Basis

DARK CRIMENET builds on the research direction of:

**"AI-Enabled Smart FIR Drafting and Legal Validation System"**

The base research focuses on improving FIR preparation through AI, NLP, machine learning, and speech-to-text, including structured information extraction, legal-section support, FIR generation, investigation support, and secure evidence handling.

DARK CRIMENET extends this direction from **FIR creation and legal assistance** toward the **investigation stage after FIRs already exist**.

The key research extension is the transformation of separate FIRs and related records into a connected, reviewable, and evidence-backed investigation representation.

### Research Gap Addressed

Existing FIR/document workflows can help process individual records, but investigation often requires connecting information across multiple records.

DARK CRIMENET focuses on:

- Cross-FIR entity connections
- Human-controlled entity resolution
- Evidence-backed relationship verification
- Case-scoped knowledge graphs
- Network and community analysis
- Timeline intelligence
- Geographic and co-location analysis
- Communication and financial analysis
- Explainable investigation signals

The project does **not** claim a new standalone machine-learning algorithm. Its contribution is the integration of these components into one evidence-centred investigation workflow.

---

# Proposed Methodology

The methodology separates automated processing from verified investigation intelligence.

### 1. Ingest and Understand

A case workspace is created or selected and FIRs and supported records are uploaded.

Supported inputs include:

- PDF
- DOCX
- TXT
- CSV
- XLSX
- Images and scanned documents

Scanned documents can be processed using local OCR.

### 2. Extract and Normalise

The document pipeline extracts:

- People
- Roles
- Phone numbers
- Vehicles
- Accounts
- Locations
- Organisations
- Dates and times
- Amounts
- Events
- FIR/case information
- Evidence spans

Language identification, normalisation, and OCR preprocessing are applied where required.

### 3. Entity Resolution

Potentially recurring entities are identified using:

- Normalised identifier matching
- Lexical similarity
- Character n-gram TF-IDF
- KNN similarity
- Identifier evidence

Entity resolution produces **candidates**, not automatic identity decisions.

### 4. Human Verification

Investigators review extracted information and candidate relationships against the original evidence.

Only verified information is promoted into the verified case graph.

### 5. Verified Knowledge Graph

Verified entities and relationships are represented using a case-scoped graph.

The graph can contain:

- Person
- Phone
- Vehicle
- Account
- Location
- Organisation
- FIR
- Event

Relationships remain connected to supporting evidence and source information.

### 6. Investigation Analysis

The verified graph becomes the common source for:

- Network analysis
- Community detection
- Shortest paths
- Hidden relationship analysis
- Timeline analysis
- Geographic analysis
- CDR analysis
- Financial analysis
- Cross-FIR intelligence

### 7. Explainable Findings

The system can surface:

- Anomalies
- Contradictions
- Missing-link candidates
- Network bridges
- Repeated activity
- Review priorities
- Evidence-backed investigation signals

Each signal is intended to remain explainable and traceable to its contributing information.

### 8. Reporting

Investigation information can be exported into:

- PDF
- DOCX
- CSV
- JSON

---

# System Workflow

~~~text
                    ┌─────────────────────────┐
                    │       CASE INPUTS       │
                    │ FIRs • CDR • Transactions│
                    │ Vehicles • Events • Files│
                    └────────────┬────────────┘
                                 ↓
                    ┌─────────────────────────┐
                    │ INGESTION & DOCUMENT     │
                    │ INTELLIGENCE             │
                    │ PDF • DOCX • TXT • CSV   │
                    │ XLSX • Images • OCR      │
                    └────────────┬────────────┘
                                 ↓
                    ┌─────────────────────────┐
                    │ INFORMATION EXTRACTION   │
                    │ People • Roles • Phones  │
                    │ Vehicles • Accounts      │
                    │ Places • Events • Dates  │
                    └────────────┬────────────┘
                                 ↓
                    ┌─────────────────────────┐
                    │ ENTITY RESOLUTION        │
                    │ TF-IDF • KNN • Similarity│
                    │ Identifier Matching      │
                    └────────────┬────────────┘
                                 ↓
                    ┌─────────────────────────┐
                    │ HUMAN VERIFICATION       │
                    │ Review against original  │
                    │ evidence before linking  │
                    └────────────┬────────────┘
                                 ↓
                    ┌─────────────────────────┐
                    │ VERIFIED CASE            │
                    │ KNOWLEDGE GRAPH          │
                    │ Entities • Relationships│
                    │ Events • Provenance      │
                    └────────────┬────────────┘
                                 ↓
             ┌───────────────────┼───────────────────┐
             ↓                   ↓                   ↓
      ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
      │   NETWORK   │     │    TIME +   │     │    CDR +    │
      │   ANALYSIS  │     │  GEOGRAPHY  │     │   FINANCE   │
      │             │     │             │     │             │
      │ Centrality  │     │ Timeline    │     │ Calls       │
      │ Communities │     │ Locations   │     │ Contacts    │
      │ Paths       │     │ Co-location │     │ Transactions│
      │ Link        │     │ Movement    │     │ Money flows │
      │ Prediction  │     │             │     │             │
      └──────┬──────┘     └──────┬──────┘     └──────┬──────┘
             └───────────────────┼───────────────────┘
                                 ↓
                    ┌─────────────────────────┐
                    │ EXPLAINABLE INVESTIGATION│
                    │ FINDINGS                 │
                    │ Anomalies • Contradictions│
                    │ Missing links • Evidence │
                    │ Review priorities        │
                    └─────────────────────────┘
~~~

---

# Key Features

## FIR & Document Intelligence

- Multi-FIR upload
- PDF, DOCX, TXT, CSV, and XLSX ingestion
- Image and scanned-document OCR
- English, Hindi, and Marathi document support
- OCR confidence information
- Original-text preservation
- Evidence-linked extraction
- Structured FIR information extraction

## Entity Resolution

- Character n-gram similarity
- TF-IDF
- KNN candidate matching
- Lexical similarity
- Identifier matching
- Explainable candidate scoring
- Mandatory human verification

## Knowledge Graph

The case graph connects verified entities and relationships.

It supports:

- Neighbourhood exploration
- Shortest paths
- Indirect connections
- Centrality analysis
- Community detection
- Cross-FIR relationships
- Evidence-backed graph relationships

## Network Intelligence

Implemented graph analysis includes:

- Degree centrality
- Betweenness centrality
- PageRank
- Eigenvector centrality
- Closeness centrality
- Community detection
- Shortest paths
- Hidden/indirect connection analysis
- Link prediction
- Network disruption analysis

## Timeline Intelligence

The system combines dated events into a case timeline.

Supported activity includes:

- Calls
- Transactions
- Meetings
- Sightings
- Events
- Other dated investigation records

## Geographic Intelligence

The system supports:

- Location-based analysis
- Repeated locations
- Co-location
- Movement sequences
- Event mapping
- Geographic relationships

Leaflet is used for map visualisation.

## CDR & Communication Analysis

Communication analysis provides:

- Call counts
- Contact pairs
- Contact intensity
- Communication patterns
- Relationship-level call analysis

## Financial Analysis

Financial investigation views include:

- Transaction relationships
- Transaction amounts
- High-value transfers
- Fan-in/fan-out patterns
- Possible pass-through flows
- Multi-step transaction chains
- Circular-flow candidates

## Anomaly Detection

Case-level anomaly signals use:

- Isolation Forest
- Local Outlier Factor
- Graph-derived features
- Transaction activity
- Event frequency
- Network position

These outputs are **review signals, not probability-of-guilt scores**.

## Contradiction Detection

Rule-based checks can identify potentially inconsistent information such as conflicting associations between entities, vehicles, and events.

## Investigation Copilot

DARK CRIMENET includes a local, source-aware investigation Copilot.

It operates over verified case information and supports investigation-oriented queries such as:

- Case summaries
- Entity relationships
- Shortest paths
- Centrality
- Communities
- Cross-FIR links
- Calls
- Money flows
- Alerts
- Contradictions

The Copilot is designed to answer from available case information with source references rather than inventing evidence.

## Evidence & Audit

The system provides:

- Evidence hashing
- SHA-256 integrity verification
- Evidence verification workflow
- Audit trails
- Hash-chained audit records
- Signed integrity ledger
- Role-based access
- Case-level access control
- TOTP-based two-step sign-in

---

# AI / Machine Learning Approach

DARK CRIMENET intentionally uses lightweight and explainable techniques rather than relying on a large language model.

### Rule-Based Intelligence

Used for:

- FIR field extraction
- Identifier detection
- Validation
- Legal/section dictionaries
- Multilingual lexicons
- Language handling
- Contradiction rules
- Evidence checks

### Classical Machine Learning

Used for:

- Entity-context classification
- Entity resolution
- Link prediction
- Anomaly detection

Models and techniques include:

- TF-IDF character n-grams
- Logistic Regression
- KNN / Nearest Neighbours
- Gradient Boosting where configured
- Isolation Forest
- Local Outlier Factor

### Graph Algorithms

NetworkX is used for:

- Centrality
- Communities
- Paths
- Network relationships
- Graph-based investigation analysis

> **No GPT/LLM is required for the core investigation workflow.**

The system is designed to run locally and keep investigation data within the deployment environment.

---

# Technology Stack

## Backend

- Python
- FastAPI
- Uvicorn
- Pydantic
- SQLAlchemy
- SQLite

## NLP / Machine Learning

- scikit-learn
- NumPy
- pandas
- TF-IDF
- Logistic Regression
- KNN
- Isolation Forest
- Local Outlier Factor

## Graph & Analytics

- NetworkX

## Document Processing

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
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt
├── RUN_WINDOWS.bat
├── QUICK_START_WINDOWS.md
├── DEMO_ACCOUNTS.md
└── README.md
~~~

---

# Dataset

DARK CRIMENET uses **synthetic datasets** for development, testing, and demonstration.

Real criminal investigation datasets are difficult to obtain because of privacy, legal, and security restrictions.

The demo environment can generate:

- Synthetic cases
- People
- Phones
- Vehicles
- Accounts
- Locations
- Organisations
- FIRs
- Graph relationships
- Timeline events
- CDR events
- Financial transactions
- Vehicle sightings
- Meetings
- Geospatial events
- Review tasks
- Investigation signals
- Synthetic evidence records

The generated dataset is deterministic so the same demonstration scenario can be reproduced.

> **No real personal or criminal-investigation data is included in the repository.**

---

# Cross-FIR Demonstration

The repository includes synthetic FIR samples under:

~~~text
demo_dataset/cross_fir_samples/
~~~

The sample FIRs are designed to demonstrate explainable cross-FIR intelligence.

The sample records intentionally share identifiers such as:

- Phone number
- Vehicle
- Account
- Serial identifier

while retaining different FIR information.

### Demonstration Flow

1. Start DARK CRIMENET.
2. Create or select an FIR investigation workspace.
3. Upload multiple FIR samples into the same workspace.
4. Process the documents.
5. Review extracted information.
6. Verify the documents and candidate relationships.
7. Open the Knowledge Graph.
8. Open Analytics / Cross-FIR Intelligence.
9. Inspect the shared evidence keys and resulting relationships.
10. Use the Copilot to query the verified relationship.

The system does not silently merge uncertain people merely because their names look similar.

---

# Installation

## Windows — Recommended

The repository includes a Windows launcher:

~~~text
RUN_WINDOWS.bat
~~~

Run it from the project directory.

It creates the local environment, installs dependencies, and starts the application.

The application is served locally at:

~~~text
http://localhost:8000
~~~

API documentation:

~~~text
http://localhost:8000/docs
~~~

---

# Manual Setup

### 1. Create a virtual environment

~~~bash
python -m venv venv
~~~

### 2. Activate it

Windows PowerShell:

~~~powershell
.\venv\Scripts\Activate.ps1
~~~

If PowerShell blocks script execution:

~~~powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
~~~

Then activate again:

~~~powershell
.\venv\Scripts\Activate.ps1
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

API documentation:

~~~text
http://localhost:8000/docs
~~~

---

# Linux / macOS

~~~bash
./run.sh
~~~

Or manually:

~~~bash
pip install -r requirements.txt
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
~~~

---

# Docker

Create the environment file:

~~~bash
cp .env.example .env
~~~

Then:

~~~bash
docker compose up --build
~~~

The application will be available at:

~~~text
http://localhost:8000
~~~

---

# Loading the Synthetic Demo Dataset

The synthetic dataset can be loaded from the application's:

~~~text
Admin Portal → Demo Lab
~~~

Or from the command line:

~~~bash
python scripts/seed_demo.py --reset
~~~

For the destructive seed option:

~~~bash
python scripts/seed_demo.py --reset --yes-delete-everything
~~~

Use destructive seed commands only in the intended demo environment.

---

# OCR Setup

DARK CRIMENET uses Tesseract for scanned-document OCR.

The project supports:

- English
- Hindi
- Marathi
- Devanagari text

Check the OCR installation using:

~~~powershell
.\venv\Scripts\python.exe scripts\check_ocr.py
~~~

If Tesseract is installed outside the expected Windows location, configure:

~~~text
TESSERACT_CMD
~~~

to point to the `tesseract.exe` installation.

---

# Multilingual FIR Intelligence

The document intelligence workflow supports:

- English
- Hindi
- Marathi
- Mixed English/Hindi/Marathi OCR

The interface can display:

- Original FIR language
- English view
- Side-by-side comparison

The original extracted text is retained so investigators can compare translated or normalised information against the source document.

Optional local IndicTrans2 configuration can be used for higher-quality narrative translation.

> Machine OCR and translation should never be treated as independently verified evidence. Critical names, identifiers, dates, locations, and legal information must be checked against the original document.

---

# Human-in-the-Loop Design

Human verification is a core part of DARK CRIMENET.

The system does **not** automatically turn every similarity result into a verified identity.

The workflow is:

~~~text
Extraction
    ↓
Candidate relationship
    ↓
Similarity / identifier evidence
    ↓
Investigator review
    ↓
Confirm / Reject / Ignore
    ↓
Verified knowledge graph
~~~

This approach helps prevent text similarity or OCR errors from becoming unverified investigative claims.

---

# Security & Responsible Use

DARK CRIMENET includes security and integrity mechanisms such as:

- Role-based access control
- Case-level access control
- Password hashing
- Signed sessions/tokens
- TOTP two-step sign-in
- Evidence SHA-256 hashing
- Hash-chained audit records
- Ed25519 signatures
- Evidence verification
- Security headers
- Content Security Policy
- Input and upload validation

The system is intended as an **investigation decision-support prototype**.

It should not be used to:

- Automatically determine guilt
- Automatically identify a criminal
- Replace investigator judgement
- Treat anomaly scores as proof
- Treat machine-generated relationships as verified without review
- Treat OCR or translation output as unquestionable evidence

Production deployment would require approved security controls, identity management, infrastructure hardening, legal review, and validated real-world datasets.

---

# Limitations

The current prototype has several important limitations:

- Demonstration datasets are synthetic.
- Real-world criminal investigation accuracy has not been established.
- OCR performance depends on document quality.
- Entity-resolution suggestions require human verification.
- Anomaly scores are investigative signals rather than probabilities.
- Link prediction ranks structurally plausible relationships rather than proving relationships.
- The local translation system is assistive rather than a certified legal translation system.
- Production deployment would require additional security, governance, and validation work.

---

# Research Contribution

The main contribution of DARK CRIMENET is not a new standalone machine-learning algorithm.

The contribution is the integration of:

~~~text
Source Documents
      ↓
Evidence-linked Extraction
      ↓
Entity Resolution
      ↓
Human Verification
      ↓
Verified Knowledge Graph
      ↓
Network + Timeline + Geography
      ↓
CDR + Financial Analysis
      ↓
Explainable Investigation Signals
~~~

This creates a traceable path from the original source record to the resulting analytical signal.

---

# Project Status

**Current Release:** v5.3.0

**Project Type:** Investigation intelligence / decision-support prototype

**Data:** Synthetic demonstration data

**Deployment:** Local / Docker

**Backend:** FastAPI

**Frontend:** Vanilla JavaScript SPA

**Graph:** NetworkX

**Database:** SQLite

---

# License

This project is developed as a research-oriented decision-support prototype.

The included synthetic data is intended for demonstration and testing only.

---

## Disclaimer

DARK CRIMENET is a research-oriented decision-support prototype.

It does not make legal determinations, establish criminal responsibility, or replace trained investigators and legal authorities.

All investigative signals should be independently reviewed against the original evidence.
