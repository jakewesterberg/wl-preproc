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

from pathlib import Path

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

    @property
    def key_source(self):
        """Only systems belonging to a session that actually landed.

        `ingest.Ingestion` carries `session_dir`, which is the only record of
        where a session's files are -- so a system whose session has no
        `Ingestion` row cannot be read at all, and attempting it would fail
        inside `make()` for every such key on every daemon pass. Restricting
        here means those keys are simply not yet due.

        Imported locally: `ingest` and `timebase` are peers, and an import-time
        dependency between them would order two schema modules that have no
        ordering.
        """
        from wl_preproc.schema import ingest

        return core.AcquisitionSystem & ingest.Ingestion

    def make(self, key: dict) -> None:
        """Fit this system's clock against session time, pooling the session.

        The sync box is inserted with an identity fit rather than skipped.
        Session time IS its timeline, so a fit of it against itself is exactly
        zero drift with zero residual -- that is the truth, not a placeholder,
        and an absent row would read as "this system was never aligned", which
        is the one thing it certainly was.
        """
        from wl_preproc.schema import ingest
        from wl_preproc.timebase import segments
        from wl_preproc.timebase.fit import fit_rate

        session_dir = Path(
            (ingest.Ingestion & {k: key[k] for k in pipeline.Session.primary_key}).fetch1(
                "session_dir"
            )
        )
        scans = segments.scan_system(key["system"], session_dir / key["system"])
        if not scans:
            # Device absence never blocks ingest (1c-2's rule) -- but an
            # AcquisitionSystem row says this system WAS present, so finding no
            # recording under it is a real disagreement rather than an absent
            # device, and it is left un-populated for the daily report to
            # surface rather than recorded as a fit of nothing.
            return

        decoded = [barcode for scan in scans for barcode in scan.barcodes]
        nominal_rate_hz = scans[0].stream.fs_hz

        if key["system"] == "syncbox":
            self.insert1(
                {
                    **key,
                    "time_source": "barcode",
                    "nominal_rate_hz": nominal_rate_hz,
                    "fitted_rate_hz": nominal_rate_hz,
                    "drift_ppm": 0.0,
                    "n_barcodes_decoded": len(decoded),
                    "n_barcodes_matched": len(decoded),
                    "residual_us_rms": 0.0,
                    "residual_us_max": 0.0,
                }
            )
            return

        reference = segments.session_reference(session_dir)
        try:
            fit = fit_rate(decoded, reference, nominal_rate_hz)
        except ValueError:
            # Design spec section 10 names this as a failure path to exercise:
            # "a system with zero decodable barcodes". It gets NO ROW rather
            # than a row full of nominal values -- a stored rate of "exactly
            # nominal, zero drift, zero residual" is indistinguishable from a
            # perfect fit, and this is the opposite of one.
            #
            # The diagnosis is not lost by returning here. `Segment` keys off
            # `AcquisitionSystem`, not off this table, precisely so that a
            # system with no fit still records why each of its files could not
            # be used.
            return
        self.insert1(
            {
                **key,
                # 'barcode' unconditionally, because nothing in this pipeline
                # is trigger-timed yet. Whether the sync box can also trigger
                # ohdpi's frames is design spec section 12's open hardware
                # question; until it is answered, claiming 'trigger' would
                # assert a precision that was never measured.
                "time_source": "barcode",
                "nominal_rate_hz": nominal_rate_hz,
                "fitted_rate_hz": fit.fitted_rate_hz,
                "drift_ppm": fit.drift_ppm,
                "n_barcodes_decoded": len(decoded),
                "n_barcodes_matched": fit.n_matched,
                "residual_us_rms": fit.residual_us_rms,
                "residual_us_max": fit.residual_us_max,
            }
        )


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
