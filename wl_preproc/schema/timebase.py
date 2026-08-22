# wl_preproc/schema/timebase.py
"""The clock fits and the timing provenance record.

Two tables, at the two scales design spec section 4.5 splits the problem into.
`SystemTimebase` is the once-per-`(system, session)` rate fit, pooled across
every barcode in the session; the per-segment offset lives on `core.Segment`
beside the extent it positions. `TimingProvenance` is the session-level record
section 4.7 asks for.

**These are the first Computed tables this project has declared.** That turns on
a path that was inert until now: `daemon.count_stale_jobs` reads DataJoint's
internal `~jobs` tables, which only exist once something populates.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import DEFAULT_PREFIX, core, pipeline

schema = dj.Schema()

# 'barcode' or 'trigger', and the distinction is not decoration. A camera
# aligned by barcode is precise to one frame period -- 2 ms at 500 Hz, which
# design spec section 3.1 notes is three orders of magnitude worse than the
# 33 us at 30 kHz and larger than a whole ohdpi frame. One aligned by an
# external trigger is exact, because the sync box then stamps the frame time
# itself and the barcode's job shrinks to identifying which trigger a frame is.
# A downstream analysis that cares about 2 ms has to be able to tell which it
# got, and cannot infer it from the rate.
_TIME_SOURCE_ENUM = "enum('barcode','trigger')"

# 'D' or 'pending', and nothing else yet. Design spec section 8: tier D is
# fully derivable from timing alone -- any timing check failed -- but tiers
# A/B/C each carry a code-agreement or trial-count term, and event decoding is
# 1c-5. Storing 'pending' rather than defaulting to a passing tier is the whole
# point: a tier derived from absent inputs treated as satisfied is a false
# claim of validation. Section 4.7 requires the tier be derived rather than
# asserted, and re-derivable under different thresholds later, which storing
# every measured input satisfies regardless of when the tier resolves.
_TIER_ENUM = "enum('D','pending')"


@schema
class SystemTimebase(dj.Computed):
    definition = f"""
    # The once-per-(system, session) clock fit. Rate is pooled across every
    # barcode in the session; offset is per-segment and lives on Segment.
    # Key: (subject, session_datetime, system).
    -> core.AcquisitionSystem
    ---
    time_source        : {_TIME_SOURCE_ENUM}
    nominal_rate_hz    : double  # what the device believes it samples at
    fitted_rate_hz     : double  # nominal * (1 + drift_ppm/1e6)
    drift_ppm          : double  # against session time, which the sync box defines
    n_barcodes_decoded : int unsigned  # recovered from this system's own files
    n_barcodes_matched : int unsigned  # of those, present in the sync box's log
    residual_us_rms    : double
    residual_us_max    : double
    """


@schema
class TimingProvenance(dj.Computed):
    definition = f"""
    # Session-level timing provenance (spec 4.7), and the part of the tier that
    # timing alone can derive. Key: (subject, session_datetime).
    -> pipeline.Session
    ---
    tier                  : {_TIER_ENUM}
    n_barcodes_emitted    : int unsigned  # the sync box's own log is the source
    n_systems_aligned     : int unsigned
    n_segments            : int unsigned
    n_rejected_segments   : int unsigned
    worst_residual_us     : double  # the largest residual over every system
    worst_drift_ppm       : double  # the largest magnitude, signed as measured
    pending_inputs        : varchar(255)  # tier inputs 1c-5 must supply; '' when none
    """


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}timebase`. Idempotent."""
    core.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}timebase", create_tables=True)
