"""Role -> permission map. Endpoints declare the permission they need (require_perm).

The full table (with the reasoning behind every permission) lives in docs/roles.md.

Summary
* admin        - controls everything (users, roles, cases, data, demo toggle, backups, audit).
* supervisor   - everything an investigator can do PLUS changing data; first-stage approver of sign-up requests.
* investigator - investigates and verifies only; never changes case data.
* auditor      - read-only, masked, audit log + oversight dashboard.
* demo         - supervisor-level abilities, but only on cases flagged ``is_demo`` (see security/access.py).
"""

ROLES = ("admin", "supervisor", "investigator", "auditor", "demo")

# What an investigator may do: look, compute, verify, export. Nothing that edits case records.
_INVESTIGATE = {"read", "analyze", "review", "sensitive_read", "report", "basic_search", "audit_case"}
# What supervisors (and the demo account) add on top: changing data and approving.
_CHANGE = {"write", "assign", "note", "approve", "oversight"}

PERMISSIONS = {
    "admin": _INVESTIGATE | _CHANGE | {"manage_users", "manage_cases", "audit", "demo_manage", "integrity"},
    "supervisor": _INVESTIGATE | _CHANGE | {"signup_review", "integrity"},
    "investigator": set(_INVESTIGATE),
    # Auditors: masked, read-only, audit log and management dashboard; no analysis, documents or evidence.
    "auditor": {"read", "basic_search", "audit", "oversight", "integrity"},
    # Demo account: supervisor-level, but confined to demo cases by security/access.py; no audit, no user admin,
    # no sign-up review, never touches real data.
    "demo": (_INVESTIGATE - {"audit_case"}) | _CHANGE | {"demo_manage"},
}


def allowed(role, permission):
    return permission in PERMISSIONS.get(role, set())


def permissions_for(role) -> list[str]:
    return sorted(PERMISSIONS.get(role, set()))
