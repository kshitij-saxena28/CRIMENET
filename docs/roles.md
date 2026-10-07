# Roles and permissions

Every endpoint declares the permission it needs (`security/rbac.py`); the front end only hides buttons, the server enforces.

| Permission | admin | supervisor | investigator | auditor | demo |
|---|:-:|:-:|:-:|:-:|:-:|
| `read` (see case data) | yes | yes | yes | masked | demo cases only |
| `analyze` (run graph, network, social, AI tools; computes only) | yes | yes | yes | – | yes |
| `review` (accept/reject extracted facts, mark alerts, confirm/dismiss a person of interest) | yes | yes | yes | – | yes |
| `sensitive_read` (unmasked personal data) | yes | yes | yes | – | yes |
| `report` (generate/export reports) | yes | yes | yes | – | yes |
| `write` (upload, edit, create cases, verify custody log, change records) | yes | yes | – | – | yes |
| `assign` (tasks) | yes | yes | – | – | yes |
| `note` (case notes) | yes | yes | – | – | yes |
| `approve` (maker-checker) | yes | yes | – | – | yes |
| `oversight` (management dashboard) | yes | yes | – | yes | yes |
| `signup_review` (first-stage approval of access requests) | – | yes | – | – | – |
| `manage_users`, `manage_cases`, `audit` (whole audit log), `demo_manage` | yes | – | – | audit only | `demo_manage` only |
| `audit_case` (audit events of a case you can access) | yes | yes | yes | yes | – |
| `integrity` (Data Integrity: ledger status, integrity check, signed bundles, exports list; supervisors only for cases they can access) | yes | yes | – | yes (masked, read-only) | – |

Notes
* **Data Integrity.** `integrity` covers the global ledger tools. Investigators and the demo account (anyone with `read`) can still look at the ledger status of a single case they can access and run the case-scoped integrity check; they never see other cases' blocks. Anchoring needs `write` (admin, supervisor); key rotation and accepting a baseline are admin-only (`manage_users`). See [data-integrity](guide/data-integrity.md).
* **Investigator = investigate and verify, never change.** Verifying means accepting or rejecting what the software extracted and recording a decision on a person of interest. Uploading, editing records and creating cases need `write`.
* **Demo** only ever sees cases flagged as demo data; real users (except admin) never see demo cases.
* **Approval requests:** a person can never approve their own request.
* **Access requests:** see [access-and-roles](guide/access-and-roles.md). The supervisor must approve before the administrator can create the account.
