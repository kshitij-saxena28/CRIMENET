# DARK CRIMENET Feature Matrix
1 Unified dashboard — implemented
2 Multi-source ingestion architecture — implemented/adapters
3 AI document intelligence — robust OCR + multilingual FIR understanding + role-aware NLP
4 Entity resolution — implemented
5 Knowledge graph — implemented
6 Advanced graph explorer — implemented
7 Shortest path — implemented
8 Hidden/indirect connections — graph neighborhood/path foundation
9 Network centrality — implemented
10 Community detection — implemented
11 Network evolution — implemented with dated snapshots
12 Timeline — implemented
13 Geographic intelligence — implemented timeline map
14 CDR analytics — implemented
15 Financial network analytics — implemented
16 Vehicle intelligence — vehicle entities/graph
17 Location intelligence — implemented
18 Anomaly detection — Isolation Forest module
19 Explainable alerts — implemented
20 AI copilot — implemented local deterministic mode
21 Natural-language search — copilot foundation
22 Contradiction detection — implemented with temporal conflict logic
23 Evidence management — implemented
24 Evidence provenance — source_ref on relationships
25 Integrity log — keyed HMAC-SHA256 hash-chained audit log (tamper-evident; not a blockchain)
26 Case management — implemented
27 Collaboration — task/assignee foundation
28 Investigation tasks — implemented
29 AI summaries — implemented
30 Report generation — implemented
31 Advanced search — API/dashboard foundations
32 Recommendations — implemented
33 Data quality/confidence — implemented fields
34 Human verification — alert workflow + responsible AI
35 Audit trail — hash-chained, verifiable via /audit/verify
36 RBAC — implemented
37 Privacy masking — implemented
38 Security controls — auth/config/role foundations
39 AI/model monitoring — model-versioned anomaly signals + responsible AI docs


### v4.1 case/FIR integration checks
- FIR-first active workspace scoping across graph, analytics, timeline, map, signals, evidence and Copilot
- Persistent DocumentRecord + SHA-256 evidence association
- Multiple verified FIRs in one workspace with deterministic shared-identifier interlinking
- Related-FIR explainable match endpoint
- Human review Create/Link/Ignore + Verify/Reject workflow
- PDF/DOCX/CSV/JSON investigation exports
- Model registry endpoint and offline contextual extraction scorer
- Seed dataset records tagged by case to prevent global preloaded-data leakage

## v4.3.2 focused upgrades
- Clean **New Workbench** flow — implemented
- **Load Neighborhood** with case-scoped nodes, edges, hops, node-type/relation filters — implemented
- **Hindi/Marathi OCR** with bundled language packs, orientation correction, confidence and side-by-side English interpretation — implemented
- **KNN Entity Resolution Workbench** with KNN + lexical + identifier scoring and human-review requirement — implemented
- **Role-based access** (Admin / Supervisor / Investigator / Auditor / Demo) with role-aware web UI and API permissions — implemented
- **Admin Portal** for user creation, role change, password set, deactivation, case archive and audit review — implemented
- **MFA** — role-based two-step sign-in (TOTP, offline; re-added after 4.5.0): per-role policy, encrypted secrets, replay protection, recovery codes, admin reset. See docs/security.md
- **Timeline swimlanes** with category symbols, filters, range zoom, event inspector, cross-FIR comparison, quiet-period explanation and export — implemented
- **FIR relationship network** and explainable shared-evidence matrix — implemented
- **Clean release database policy** so unrelated seed data is not shipped into the default runtime workspace — implemented


## v5.0.0 additions
- Persons of Interest ranking (explainable, human-verified)
- Roles: supervisor, investigator (verify-only), auditor, demo; access-request workflow; demo data switch
- Offline map; suspect ranking; access-request workflow
- Removed: Link Canvas, insider risk, case-intelligence, model-quality

## v5.2.0 changes

- New "Docket" interface; Priority queue, look-alikes, narrative spread, watch list, English view, notes and profile sheet under Social media; sub-officer role removed; demo-off hides all demo traces from audit views.

## v5.1.0 changes
- New UI ("Console": navy sidebar, light/dark themes), back/forward buttons, create-case pop-up when a FIR is uploaded without a case
- Added: Social Media Intelligence (import-only, lawful), Surveillance Reports, Data Integrity (signed hash-chained ledger, offline verifier, optional evidence encryption), role-based two-step sign-in (TOTP, recovery codes)
- Removed: Pattern Lab, Cyber Forensics, cross-case hits, what-if sandbox, timeline Gaps tab
