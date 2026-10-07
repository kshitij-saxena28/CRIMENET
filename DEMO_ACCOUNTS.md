# DARK CRIMENET v5.3.0 — Accounts & Roles

## Demonstration accounts (`APP_ENV=demo` only)

When the user table is empty and `APP_ENV=demo`, the API creates these five accounts with **fixed, well-known passwords**.
They exist so that a demonstration works without setup. **Never use them with real data.**

| Account ID | Password | Role | What it is for |
|---|---|---|---|
| `admin` | `admin123` | Admin | Controls everything: users, roles, cases, data, demo switch, backups, audit |
| `demo` | `demo123` | Demo | Supervisor-level, but **only on demo data**; can switch demo data on/off; sees no real case, user or audit log |
| `investigator` | `invest123` | Investigator | Investigates and verifies; never changes data |
| `supervisor` | `super123` | Supervisor | Investigator + changes data, creates/assigns cases and tasks, approves; first approver of account requests |
| `auditor` | `audit123` | Auditor | Read-only, masked; audit log and oversight dashboard |

* Set `ADMIN_PASSWORD`, `DEMO_PASSWORD`, `INVESTIGATOR_PASSWORD`, `SUPERVISOR_PASSWORD` or `AUDITOR_PASSWORD` in `.env` before the first start to override any of them.
* Nothing is written to `initial_credentials.txt` for these fixed passwords.
* **Two-step sign-in is optional in demo mode:** no role is required to use it, so the accounts above sign in with just the password. Anyone can turn it on under Account (top right); admins can require it per role under Admin Portal -> Two-step sign-in. In any other environment `admin`, `supervisor` and `investigator` must use it by default (`MFA_REQUIRED_ROLES` overrides; `demo` is never forced) - the first admin sign-in then asks to set up an authenticator app. See [docs/guide/two-step-sign-in.md](docs/guide/two-step-sign-in.md).
* In demo mode the password policy is relaxed to **at least 6 characters** (not equal to the account ID) so these passwords are accepted.

## Any other environment (`APP_ENV=production`)

Only `admin` is created, with `ADMIN_PASSWORD` or a random one-time password that is printed once in the API console and saved to `initial_credentials.txt` (git-ignored, mode 0600; delete it after first sign-in). The strict policy applies: at least 10 characters, letters and digits, not a common password, not containing the account ID. There are no other built-in accounts.

Accounts are created **only if the user table is empty**. Renaming or deleting a bootstrap account is permanent; it is never silently re-created. A database created before the `demo` and `auditor` roles existed keeps its users: create them under **Admin Portal → Users** or with `python scripts/set_password.py demo --create --role demo`.

## Roles

The complete permission table is in [docs/roles.md](docs/roles.md); a plain-language guide is in [docs/guide/access-and-roles.md](docs/guide/access-and-roles.md).

## Case access

* New workbenches are **restricted**: visible to their creator, explicit members and admins. Auditors see every real case (masked, read-only).
* Admins grant access with `POST /admin/cases/{case}/members` (`DELETE …/members/{user}` to revoke) and can flip a case to `shared` with `PATCH /admin/cases/{case}/visibility`.
* **Demo cases** (`is_demo`) belong to the demo dataset: visible to the `demo` role and admins only. Real users never see them, and the demo role never sees a real case.
* Non-admin API calls must always name a case; there is no implicit "all cases" view.

## Account lifecycle

* There is **no self sign-up**. A person files an *account request* on the sign-in page (`POST /auth/signup-request`); a **supervisor** approves or rejects it; only then can an **admin** create the account (or the admin rejects it). Every step is in the audit log.
* Users change their own password or username from the **Account** button (top right). Admins create users, activate/deactivate them and set passwords under **Admin Portal → Users**.
* Changing a password, username or role, or deactivating an account, immediately invalidates that user's existing sessions.
* Login is rate-limited (5 failures / 15 min per IP+account; 20 per account); account requests are limited to 5 per hour per address.
* Two-step sign-in (authenticator app) is role-based: optional in demo mode, required for admin, supervisor and investigator elsewhere by default. Admins can reset it for a user who lost their phone (Admin Portal -> Users -> Reset two-step).

## Forgotten password

There is no self-service reset. Ask an Admin to set a new password (Admin Portal → Users → Set password), or, if no one can sign in, run on the server:

```bash
python scripts/set_password.py USERNAME            # prompts for the new password
python scripts/set_password.py NEWADMIN --create --role admin
```

## Demo data switch

`GET /demo/status`, `POST /demo/enable`, `POST /demo/disable` (demo account and admin). *On* loads the bundled synthetic dataset (idempotent); *off* removes only demo-flagged cases and everything that belongs to them, and is written to the audit log. Real data is never touched. With demo data off, the demo account simply sees empty screens.

## Suggested demo order

1. Sign in as `admin` (or `demo`) and switch **Demo data** on.
2. Sign in as `investigator`? It cannot see demo data: use `demo` to walk through cases, graph, analytics, timeline and evidence, and `supervisor` on a real workbench to ingest FIRs.
3. Show `auditor` (masked, read-only, audit log, dashboard).
4. File an account request from the sign-in page, approve it as `supervisor`, create the account as `admin`.

The application never treats OCR, translation, KNN similarity, anomaly scores or cross-FIR linkage as proof of guilt. Human verification remains mandatory.
