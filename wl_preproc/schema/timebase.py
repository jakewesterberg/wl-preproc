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

# Whether this system's clock was actually fitted, and if not, why not. Every
# (system, session) gets exactly one row, including the ones that could not be
# fitted.
#
# Returning from `make()` without inserting was the first design, on the
# argument that a row of nominal values is indistinguishable from a perfect
# fit. That argument stands; the conclusion did not. **DataJoint counts a
# `make()` that inserts nothing as a SUCCESS and leaves the key outstanding**,
# so the daemon re-scanned and re-decoded every unfittable system's recordings
# on every pass, forever, reporting two keys "populated" each time. Measured
# directly: `success_count` stayed at 2 across four consecutive passes with no
# row ever appearing.
#
# An explicit status dissolves the original objection -- the row says it was
# not fitted -- and it is strictly more informative than absence, which cannot
# distinguish "checked, could not fit" from "not reached yet".
_FIT_STATUS_ENUM = "enum('fitted','no_recording','unfittable')"


@schema
class SystemTimebase(dj.Computed):
    definition = f"""
    # The once-per-(system, session) clock fit. Rate is pooled across every
    # barcode in the session; offset is per-segment and lives on Segment.
    # Key: (subject, session_datetime, system).
    -> core.AcquisitionSystem
    ---
    fit_status         : {_FIT_STATUS_ENUM}  # read this before any column below
    time_source        : {_TIME_SOURCE_ENUM}  # the mechanism attempted
    n_barcodes_decoded : int unsigned  # recovered from this system's own files
    n_barcodes_matched : int unsigned  # of those, present in the sync box's log
    nominal_rate_hz=null : double  # what the device believes it samples at
    fitted_rate_hz=null  : double  # nominal * (1 + drift_ppm/1e6)
    drift_ppm=null       : double  # against session time, which the sync box defines
    residual_us_rms=null : double
    residual_us_max=null : double
    """

    # NULL where a fit was not reached, rather than zero. A stored drift of
    # 0.0 ppm with a 0.0 us residual is exactly what a flawless fit looks like,
    # so a system that was never fitted would read as the best-aligned one in
    # the session. `fit_status` is the column to branch on; the rest are its
    # payload.

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
        # 'barcode' unconditionally, because nothing in this pipeline is
        # trigger-timed yet. Whether the sync box can also trigger ohdpi's
        # frames is design spec section 12's open hardware question; until it
        # is answered, claiming 'trigger' would assert a precision that was
        # never measured.
        row = {**key, "time_source": "barcode", "n_barcodes_decoded": 0,
               "n_barcodes_matched": 0}

        if not scans:
            # Device absence never blocks ingest (1c-2's rule) -- but an
            # AcquisitionSystem row says this system WAS present, so finding no
            # recording under it is a real disagreement, and it is recorded as
            # one for the daily report to surface.
            self.insert1({**row, "fit_status": "no_recording"})
            return

        decoded = [barcode for scan in scans for barcode in scan.barcodes]
        nominal_rate_hz = scans[0].stream.fs_hz
        row["n_barcodes_decoded"] = len(decoded)

        if key["system"] == "syncbox":
            # Session time IS this system's timeline, so its fit against itself
            # is exactly identity -- the truth, not a placeholder.
            self.insert1(
                {
                    **row,
                    "fit_status": "fitted",
                    "n_barcodes_matched": len(decoded),
                    "nominal_rate_hz": nominal_rate_hz,
                    "fitted_rate_hz": nominal_rate_hz,
                    "drift_ppm": 0.0,
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
            # "a system with zero decodable barcodes". The row records that it
            # was checked and could not be fitted; the fit columns stay NULL,
            # because zeros there are what a flawless fit looks like.
            self.insert1(
                {**row, "fit_status": "unfittable", "nominal_rate_hz": nominal_rate_hz}
            )
            return

        self.insert1(
            {
                **row,
                "fit_status": "fitted",
                "n_barcodes_matched": fit.n_matched,
                "nominal_rate_hz": nominal_rate_hz,
                "fitted_rate_hz": fit.fitted_rate_hz,
                "drift_ppm": fit.drift_ppm,
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

    # What this phase cannot compute, named rather than defaulted. Every one
    # needs event decoding, which is 1c-5: agreement between the Pi's and NI's
    # event-code records, trial counts from codes versus the task file, and the
    # camera's trigger count against frames received. Section 4.7 makes each a
    # term in tiers A, B and C, so none of those verdicts is reachable here.
    #
    # Stored as a string on the row rather than implied by the tier, for the
    # same reason 1c-2's daily report names the categories it cannot yet count
    # rather than omitting them: a reader must be able to see WHAT is missing,
    # not only that something is.
    PENDING_TIER_INPUTS = "event_code_agreement,trial_count_agreement,camera_trigger_count"

    @property
    def key_source(self):
        """Sessions that landed. Same reasoning as `SystemTimebase.key_source`."""
        from wl_preproc.schema import ingest

        return pipeline.Session & ingest.Ingestion

    def make(self, key: dict) -> None:
        """Record section 4.7's timing metrics, and as much tier as they decide.

        **Tier D is the only verdict reachable here, and 'pending' is the
        honest alternative.** Design spec section 8: D is "any timing check
        failed", which needs nothing this phase lacks. A, B and C each carry a
        code-agreement or trial-count term, so emitting one of them would be a
        claim of validation this phase did not perform. Every measured input is
        stored, which is what section 4.7 means by the tier being derived
        rather than asserted and re-derivable under different thresholds later.
        """
        from wl_preproc.schema import core

        fits = (SystemTimebase & key).to_dicts()
        segments = (core.Segment & key).to_dicts()
        rejected = (core.RejectedSegment & key).to_dicts()
        systems = (core.AcquisitionSystem & key).to_dicts()

        # The sync box emitted them; every other system is measured against it.
        emitted = max(
            (fit["n_barcodes_decoded"] for fit in fits if fit["system"] == "syncbox"),
            default=0,
        )
        aligned = [fit for fit in fits if fit["fit_status"] == "fitted"]

        # A timing check failed if any present system could not be fitted, or
        # if any file was rejected. Both are failures of THIS phase's own
        # checks, which is precisely what tier D is defined over.
        failed = len(aligned) < len(systems) or bool(rejected)

        self.insert1(
            {
                **key,
                "tier": "D" if failed else "pending",
                "n_barcodes_emitted": emitted,
                "n_systems_aligned": len(aligned),
                "n_segments": len(segments),
                "n_rejected_segments": len(rejected),
                # Over the FITTED systems only: the unfitted ones carry NULL,
                # and `max()` over a set containing None raises rather than
                # skipping it.
                "worst_residual_us": max(
                    (fit["residual_us_max"] for fit in aligned), default=0.0
                ),
                # Signed as measured, but selected by magnitude: the worst
                # clock is the one furthest from the reference in either
                # direction, and taking a plain max would report the best
                # fast clock as the worst of a set of slow ones.
                "worst_drift_ppm": max(
                    (fit["drift_ppm"] for fit in aligned),
                    key=abs,
                    default=0.0,
                ),
                # Named even on tier D: knowing a session already failed a
                # timing check does not make the inputs 1c-5 still owes it any
                # less missing, and this column is what tells 1c-5 which
                # sessions it must revisit.
                "pending_inputs": self.PENDING_TIER_INPUTS,
            }
        )


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}timebase`. Idempotent."""
    core.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}timebase", create_tables=True)
