# wl_preproc/schema/_compat.py
"""One place where this project patches DataJoint, and why.

DataJoint 2.0 removed the lowercase ``dj.schema`` alias in favour of
``dj.Schema``. Every DataJoint Element still calls the lowercase name, so
importing any of them under 2.x raises ``AttributeError`` before a single line
of their module bodies runs.

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
