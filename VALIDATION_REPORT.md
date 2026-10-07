# DARK CRIMENET v5.3.0 — Validation Report

Date: 2026-09-29

## What was run

| Check | Result |
|---|---|
| `python -m pytest` (throw-away DB; 70+ tests incl. regression tests for the vulnerabilities found in the v4.3.2 review) | passed |
| Exploit re-checks against a live API: anonymous admin takeover, forged/default-secret tokens, deactivated-user token, brute force, cross-case reads, auditor PII, audit tampering, malicious uploads | all blocked (covered by `backend/tests/test_security.py`) |
| Legacy v4.3.2 database opened by v4.4.x/v5.0.0 (in-place migration) | passed; users kept, cases become `shared` |
| Demo dataset: CASE-2026-101 graph (116 nodes / 57 relationships / 73 components), CASE-2026-103, CASE-2026-108 | identical to v4.3.2 output after the SQL-prefilter rewrite |
| Web UI in headless Chromium against a live server as admin / investigator / supervisor: every tab rendered without console errors; upload + review + verify a FIR; change password (wrong current, success, still signed in); tampered token returns to login; auditor graph shows masked identifiers | passed |

## Not validated (be honest in the demo)

* Visual polish on other browsers/devices (tested in Chromium at desktop width only); map tiles need internet access and were not exercised offline.
* OCR accuracy on real scans was not measured. The context scorer's metrics are on synthetic, templated data (optimistic).
* Anomaly thresholds are uncalibrated; no labelled ground truth exists.
* Load/performance beyond the demo dataset (~300 entities/case) was not tested; graph analytics are in-memory per request.
* Multi-worker deployments: rate limiter and audit lock are per-process.
