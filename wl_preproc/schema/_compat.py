# wl_preproc/schema/_compat.py
"""One place where this project patches DataJoint, and why.

DataJoint 2.0 removed the lowercase ``dj.schema`` alias in favour of
``dj.Schema``. Not every adopted Element needs this: ``element_session``
raises ``AttributeError`` on a bare import without it, and
``element_event``'s ``event``/``trial`` submodules do too once imported
directly, but ``element_lab`` and ``element_animal``, at the refs this
project pins, call neither name. Applying the shim unconditionally is still
correct and cheap regardless of which Elements need it today.

Upstream is migrating — ``element-animal`` PR #51 does exactly this rename —
and this shim exists only until those land. It lives in one module rather than
at each import site so that deleting it is a one-line change, and so that a
reader looking for "what do we patch" finds one answer.
"""

from __future__ import annotations

import datajoint as dj


def apply_datajoint_compat() -> None:
    """Restore the names the Elements still expect. Idempotent."""
    if not hasattr(dj, "schema"):
        dj.schema = dj.Schema
