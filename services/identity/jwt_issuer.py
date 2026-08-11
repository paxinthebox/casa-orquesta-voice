"""
JWT issuer + verifier — re-export shim.

The canonical implementation lives in
`services/_shared/internal_jwt.py` (P4.6) so every service —
not just identity — can verify HS256 internal tokens. This module
preserves the legacy import path `from jwt_issuer import …` used by
`services/identity/main.py` and `services/identity/tests/test_identity.py`.

We load the shared module by *file path* (importlib) rather than the
import system, because identity's own `jwt_issuer` shadows the
shared one on `sys.path` and naive `import` would cause a circular
self-import.
"""
from __future__ import annotations

import importlib.util as _imp_util
import os as _os

_SHARED_PATH = _os.path.normpath(
    _os.path.join(_os.path.dirname(__file__), "..", "_shared", "internal_jwt.py")
)
_spec = _imp_util.spec_from_file_location(
    "casa_internal_jwt", _SHARED_PATH,
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"could not load shared internal_jwt at {_SHARED_PATH}")
_module = _imp_util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

# Re-export the canonical surface unchanged.
issue_internal_jwt = _module.issue_internal_jwt
verify_internal_jwt = _module.verify_internal_jwt
DEFAULT_TTL_SECONDS = _module.DEFAULT_TTL_SECONDS
INTERNAL_HS_SECRET = _module.INTERNAL_HS_SECRET
INTERNAL_ISSUER = _module.INTERNAL_ISSUER
INTERNAL_AUDIENCE = _module.INTERNAL_AUDIENCE

__all__ = [
    "issue_internal_jwt", "verify_internal_jwt",
    "DEFAULT_TTL_SECONDS", "INTERNAL_HS_SECRET",
    "INTERNAL_ISSUER", "INTERNAL_AUDIENCE",
]
