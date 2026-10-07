"""PII masking applied to roles without the ``sensitive_read`` permission."""
from security.rbac import allowed

MASKED_TYPES = {"PHONE", "ACCOUNT", "EMAIL", "DIGITAL_IDENTIFIER", "VEHICLE"}
MASKED_ATTRS = {"phone", "number", "plate", "vehicle", "account_number", "serial", "email", "alias", "imei", "imsi", "aadhaar", "pan"}


def mask_identifier(value, start=2, end=2):
    s = str(value or "")
    if len(s) <= start + end:
        return "*" * len(s)
    return s[:start] + "*" * (len(s) - start - end) + s[-end:]


def needs_masking(role) -> bool:
    return not allowed(role, "sensitive_read")


def mask_entity(row: dict, role) -> dict:
    """Return a masked copy of an entity dict (name + identifier-like attributes)."""
    if not needs_masking(role):
        return row
    out = dict(row)
    attrs = dict(out.get("attributes") or {})
    if str(out.get("type", "")).upper() in MASKED_TYPES:
        out["name"] = mask_identifier(out.get("name"))
        attrs["masked"] = True
    for k in list(attrs):
        if k.lower() in MASKED_ATTRS and attrs[k]:
            attrs[k] = mask_identifier(attrs[k])
            attrs["masked"] = True
    out["attributes"] = attrs
    return out


def mask_node(node: dict, role) -> dict:
    """Mask a flattened graph node ({id,type,name,...attrs}) for roles without sensitive_read."""
    if not needs_masking(role):
        return node
    out = dict(node)
    if str(out.get("type", "")).upper() in MASKED_TYPES:
        out["name"] = mask_identifier(out.get("name"))
        out["masked"] = True
    for k in list(out):
        if k.lower() in MASKED_ATTRS and out[k]:
            out[k] = mask_identifier(out[k]); out["masked"] = True
    return out
