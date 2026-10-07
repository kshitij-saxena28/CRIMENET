# Data Integrity: how the system shows that records were not changed

For police officers, supervisors, auditors and anyone who has to defend the records in a review or in court.

## What is protected

The system keeps a **ledger**: a list of entries (called blocks) that is written to, never edited. A block is added when someone:

- uploads, downloads or verifies **evidence**;
- extracts, reviews or re-processes a **document (FIR)**;
- ingests a table, creates a case, an entity or a relationship;
- imports social-media material or records / amends / reports surveillance;
- **exports** anything: reports (Word, PDF, CSV), redacted exports, legal packs, backups;
- switches the demonstration data on or off, or purges a case.

A block holds who, what, when, which case and **fingerprints** (hashes) of the data. It never holds the file itself or personal details.

## The ideas in one line each

| Word | Meaning |
|---|---|
| **Block** | One recorded event. |
| **Hash** | A fingerprint of data. Change one character and the fingerprint is completely different. |
| **Chain** | Every block contains the fingerprint of the block before it, so altering, deleting or re-ordering one block breaks all the ones after it. |
| **Anchor** | Every so often the system records one combined fingerprint of a whole batch of blocks. It lets a single event be proven part of the batch with a short proof. |
| **Signature** | The server signs every block with a private key that never leaves the server. Anyone with the public key can check it; nobody can forge it without the key. |
| **Head** | The newest block. Its hash is the "receipt" for everything before it. |

## Running the check

Open **Data Integrity**, then **Run integrity check**. The system:

1. re-computes the whole chain and checks every signature and anchor;
2. re-reads every evidence file and compares it with the fingerprint taken when it was stored;
3. compares the important database records (documents, evidence, entities, relationships, surveillance and social entries) with what the ledger recorded, and looks for records that **exist but were never recorded**;
4. checks the older audit log chain too.

The answer is one of:

- **INTACT**: nothing differs from what was recorded.
- **TAMPERING DETECTED**: something was changed, removed or added outside the recorded channels. The page lists the exact blocks, records and files. Treat those items as unreliable until the cause is explained (a real edit made directly in the database looks exactly like tampering, which is the point).
- **PARTIAL**: no tampering found, but part of the check could not run (for example an encrypted file whose key is not available). The reasons are listed.

If you are an investigator you see the same check for **your case only**.

## What it cannot prove

- That a fact was **true** when it was entered. It only shows that it was not changed afterwards.
- Anything about data that existed **before the ledger started** (that data is recorded as the starting point) or about tables it does not track (notes, tasks, alerts, user accounts).
- That the **newest** blocks were not silently cut off, **unless** you have a head hash recorded somewhere the server cannot change. **Record the head** (the button on the Overview page shows one line to write down) after important milestones: put it in the case file, on paper, or e-mail it to the supervisor. Later, a bundle that does not contain that hash shows that blocks were removed or rewritten. The server also keeps signed copies of the head in a separate file, which catches a database-only attack, but not someone who controls the whole server.
- That nobody **photographed a screen** or retyped a document. See below.

## Checking without the server (for a court, an auditor, another department)

1. Download the **signed bundle** from Data Integrity (a bundle of the current case; administrators and auditors can also download the **full chain**).
2. Copy `scripts/verify_ledger.py` and the bundle to any computer with Python (no other software or network needed).
3. Run:

```
python scripts/verify_ledger.py bundle.json --trust-key <key fingerprint> --expect-head <head hash>
```

4. It prints `VERIFIED` or `TAMPERING DETECTED` with the exact blocks. Exit code 0 means verified, 1 means a problem.

Write down the **key fingerprint** shown on the Overview page when the system is first set up: without it the script can only say the bundle is consistent with itself, not that it came from your server. A case bundle proves that its blocks were not edited and were signed by the server; only a full-chain bundle can also show that no block is missing.

## Leaks: watermarks and export records

Every export (report, redacted export, legal pack, backup, evidence download) is written to the ledger with the person, time, case and a fingerprint of the file. Word, PDF, CSV and JSON documents also carry a visible line such as:

> Exported by SI.Rao on 2026-09-30 10:22:41 UTC, ref L-1843, watermark WM-9A31C07B2E

If a copy turns up somewhere it should not be, an administrator can find who exported it: **Data Integrity > Exports**, search by the watermark id, or pick the file: its fingerprint is calculated in the browser and matched against the export list (the file is not uploaded). If the same person exports or downloads unusually many items in a short time (default: more than 10 in 10 minutes), the administrators and supervisors are notified and the ledger records an alert.

**Be clear about the limit.** Someone who is allowed to see a screen can photograph it, and someone who can read a document can retype it. These controls make leaks **traceable and detectable**; they do not make them impossible. Original evidence files are given out unchanged (a watermark would alter them); the download itself is recorded.

## Encryption of stored evidence (optional)

If the administrator sets `EVIDENCE_ENCRYPTION_KEY`, new evidence files are encrypted on disk. The evidence fingerprint is still that of the original file, and checks decrypt automatically. Keep the key safe and apart from the backups: without it the encrypted files cannot be read.

## For administrators

- **Anchor now**: creates an anchor immediately (also happens every 50 blocks, hourly, and when a bundle is downloaded).
- **Rotate signing key**: introduces a new key with a block signed by the old one. Old bundles remain valid.
- **Accept unrecorded rows as baseline**: use only when you know the data is legitimate (for example loaded by a script while the server was stopped). The decision is logged.
- If a **ledger gap** is shown, some events could not be written at the time (for example a full disk). The audit log has them; the ledger records the gap and continues.
- Settings: `LEDGER_ANCHOR_EVERY`, `LEDGER_ANCHOR_SECONDS`, `EXPORT_ALERT_THRESHOLD`, `EXPORT_ALERT_WINDOW_MINUTES`, `EVIDENCE_ENCRYPTION_KEY`, `LEDGER_KEY_PATH`. The signing key `.ledger_ed25519` and the head copies `.ledger_heads.log` are never committed; back them up separately from the database.
