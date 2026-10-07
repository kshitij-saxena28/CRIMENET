"""Feature plug-ins.

Each module in this package may define:

* SQLAlchemy models on the shared ``Base`` (imported before ``create_all`` runs);
* ``build(ctx) -> fastapi.APIRouter``: the feature's endpoints (mounted by ``mount_features``);
* optional ``on_startup(ctx)``.

``ctx`` (see main.py) exposes: svc, scope, get_db, require_perm, bad, settings, SessionLocal,
mask_entity, mask_node, doc_case, audit. A broken feature never stops the API from starting; the
error is recorded in ``LOAD_ERRORS`` and a test asserts that list is empty.
"""
import importlib
import logging
import pkgutil

log = logging.getLogger("dcn.features")
LOADED: dict = {}


class Hooks:
    """Tiny in-process event bus. Core code emits; features subscribe (failures never break the request).

    Events (keyword args): document_extracted(db, user, result, case_number), document_reviewed(db, user, document_id,
    payload, result), evidence_uploaded(db, user, result, case_number), evidence_verified(db, user, evidence_id, result,
    case_number), evidence_downloaded(db, user, evidence_id, case_number), table_ingested(db, user, result, case_number),
    case_created(db, user, case_number).
    """
    def __init__(self):
        self._subs: dict = {}

    def on(self, event, fn):
        self._subs.setdefault(event, []).append(fn)

    def emit(self, event, **kw):
        for fn in self._subs.get(event, []):
            try:
                fn(**kw)
            except Exception:  # noqa: BLE001
                log.exception("hook %s failed", event)


hooks = Hooks()
LOAD_ERRORS: dict = {}


def import_features() -> None:
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name in LOADED:
            continue
        try:
            LOADED[info.name] = importlib.import_module(f"{__name__}.{info.name}")
        except Exception as exc:  # noqa: BLE001
            LOAD_ERRORS[info.name] = f"import: {exc!r}"
            log.exception("Feature %s failed to import", info.name)


CTX = None  # the context features were mounted with (used by background jobs and tests)


def mount_features(app, ctx) -> None:
    global CTX
    CTX = ctx
    for name, mod in list(LOADED.items()):
        try:
            if hasattr(mod, "build"):
                app.include_router(mod.build(ctx))
            if hasattr(mod, "on_startup"):
                mod.on_startup(ctx)
        except Exception as exc:  # noqa: BLE001
            LOAD_ERRORS[name] = f"mount: {exc!r}"
            log.exception("Feature %s failed to mount", name)
