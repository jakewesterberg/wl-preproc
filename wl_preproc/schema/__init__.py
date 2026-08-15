# wl_preproc/schema/__init__.py
"""The schema package, and the one place the database-name prefix is defined.

``DEFAULT_PREFIX`` carries its own separator. Every ``activate()`` in this
package interpolates it as ``f"{prefix}lab"``, ``f"{prefix}core"`` and so on —
there is no separator in those f-strings — so a prefix without a trailing
separator produces ``wlpplab``, not the ``wlpp_lab`` design spec section 3
names. That defect shipped and was invisible for the whole of 1c-1: the suite
runs at ``prefix="t_"``, which carries its own separator, so the default was
never exercised by anything. The first production ``wlpp daemon`` would have
created nine wrongly-named databases, and renaming a schema after the lab
starts is a migration on live foreign-keyed tables.

Defined here rather than in ``pipeline.py`` because all five schema modules and
the daemon and the CLI need it, and ``pipeline`` is a peer of four of them, not
their parent. ``tests/schema/test_pipeline.py`` pins the resulting database
names against the spec so this is enforced by something executable.
"""

DEFAULT_PREFIX = "wlpp_"

__all__: list[str] = ["DEFAULT_PREFIX"]
