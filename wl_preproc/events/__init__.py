# wl_preproc/events/__init__.py
"""Pure event-code logic, with no DataJoint import.

Sits beside `wl_preproc/schema/events.py` the way `wl_preproc/timebase/` sits
beside `wl_preproc/schema/timebase.py`: the tables are one module, the logic
that fills them is a package, and the logic is testable with no database.

The CODEC is not here. `wl_preproc/contracts/events.py` is a frozen interface
(design spec section 3.5 item 4) and owns Marker, Escape, encode_payload and
decode_stream. This package extracts words from recordings, feeds them to that
decoder, and assembles what comes back.
"""
