# wl_preproc/ephys/__init__.py
"""Pure ephys logic, with no DataJoint import.

Sits beside `wl_preproc/schema/ephys.py` the way `wl_preproc/timebase/` sits
beside `wl_preproc/schema/timebase.py`: the tables are one module, the logic
that fills them is a package, and the logic is testable with no database.
"""
