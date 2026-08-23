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

# 'A', 'B', 'C' or 'D' -- 'pending' retired. Design spec section 8: tier D was
# always fully derivable from timing alone -- any timing check failed -- but
# tiers A/B/C each carry a code-agreement or trial-count term, and event
# decoding was 1c-5's job. 1c-4 stored 'pending' rather than defaulting to a
# passing tier for exactly that reason: a tier derived from absent inputs
# treated as satisfied is a false claim of validation.
#
# 1c-5 Task 9 is what supplies those inputs, and `TimingProvenance.make()` now
# calls `events.agreement.resolve_tier` unconditionally -- so no code path
# writes 'pending' any more, and it is dropped from the enum rather than kept
# as a value nothing can reach. A stored value with no writer left is worse
# than no value at all: the next reader who finds it has to work out whether
# it means "unreachable" or "reachable and I haven't found how yet".
# `PENDING_TIER_INPUTS` below no longer names a wait: Task 10 rewrote it to
# name the three inputs the tier derives from, and to keep the record of what
# `pending_inputs` used to hold.
_TIER_ENUM = "enum('A','B','C','D')"

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

# How close the measured block boundary (`trial.Block`) must land to wl.works'
# own assertion (`core.Block`) to count as agreeing, in seconds. Both are
# session-time doubles built from independent paths -- one decoded, one
# authored -- so exact equality is not the bar; a real disagreement (a
# misassigned block, a boundary off by a trial or more) is orders of magnitude
# larger than this. 1 ms is generous next to `timebase/coverage.py`'s own
# `_FULL_TOLERANCE_S` (1 us, chosen for float accumulation error alone) because
# these two numbers were never fit against the same clock the way a segment's
# offset is -- there is no rate/residual budget to derive this from, so it is
# chosen rather than derived, exactly as `timebase/segments.py`'s own alignment
# durations are guarantees rather than measurements.
_BLOCK_AGREEMENT_TOLERANCE_S = 1e-3


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
    # Session-level timing provenance (spec 4.7), and the tier -- now fully
    # resolved, not merely the part timing alone can derive (1c-5 Task 9).
    # Key: (subject, session_datetime).
    -> pipeline.Session
    ---
    tier                  : {_TIER_ENUM}
    n_barcodes_emitted    : int unsigned  # the sync box's own log is the source
    n_systems_aligned     : int unsigned
    n_segments            : int unsigned
    n_rejected_segments   : int unsigned
    worst_residual_us     : double  # the largest residual over every system
    worst_drift_ppm       : double  # the largest magnitude, signed as measured
    pending_inputs        : varchar(255)  # '' always, now -- see PENDING_TIER_INPUTS
    # Every field `events.agreement.TierInputs` took to reach `tier`, kept on
    # the row: spec section 4.7 requires the tier be "derived, not asserted"
    # and re-derivable under different thresholds later, which a stored
    # verdict with the evidence discarded could never satisfy.
    event_code_agreement=null  : float    # Pi vs NI code-stream fraction; null with <2 full-code records
    trial_count_agreement=null : tinyint(1)  # codes vs task file; null when no task file existed
    camera_trigger_count=null  : int unsigned  # bcam sidecar frames actually received; null with no bcam
    n_full_code_records        : int unsigned  # independent Pi/NI records that decoded content
    n_strobe_witnesses         : int unsigned  # RHS-style witnesses whose edge count matched
    decode_errors               : int unsigned  # DecodeErrors across every full-code record
    block_agreement=null        : tinyint(1)  # measured trial.Block vs wl.works' core.Block; null unasserted
    """

    # The three event-derived inputs the tier is resolved from, named rather
    # than left implicit -- and the record of what `pending_inputs` used to
    # hold. Until 1c-5 this constant WAS the wait: `make()` wrote it into
    # `pending_inputs` verbatim, because none of the three could be computed
    # without event decoding, and section 4.7 makes each a term in tiers A, B
    # and C. That wait is over. `make()` measures all three now --
    # `event_code_agreement` from the Pi's word stream against the NI's,
    # `trial_count_agreement` from the trial count decoded out of the codes
    # against the task file, `camera_trigger_count` from the behaviour-camera
    # sidecar -- and stores each in its own column beside the tier, which is
    # what makes section 4.7's "derived, not asserted" true of the row rather
    # than only of the code that wrote it.
    #
    # Kept as a string constant, and `pending_inputs` kept as a column, for
    # the same reason 1c-2's daily report names the categories it cannot yet
    # count rather than omitting them: a reader must be able to see WHAT is
    # missing, not only that something is. That principle is why the column is
    # emptied rather than dropped -- `''` states "nothing is pending", where an
    # absent column would leave a reader unable to tell that from "nobody
    # checked". This constant is what would be named again if a later phase
    # ever has to defer one of the three.
    PENDING_TIER_INPUTS = "event_code_agreement,trial_count_agreement,camera_trigger_count"

    @property
    def key_source(self):
        """Sessions that landed. Same reasoning as `SystemTimebase.key_source`."""
        from wl_preproc.schema import ingest

        return pipeline.Session & ingest.Ingestion

    def make(self, key: dict) -> None:
        """Record section 4.7's timing metrics, and resolve the tier.

        **1c-5 Task 9: every input `events.agreement.resolve_tier` needs is now
        measured here**, by re-decoding the session's own files rather than by
        reading `pipeline.event.Event` -- which holds only the Pi's already-
        merged decode (`schema/events.py`'s own docstring: "the canonical
        trial list is built from the sync box (the Pi) alone"). The NI's own
        record, the RHS strobe witness, and both cross-checks all need a
        SECOND, independent look at the raw files, which is exactly what
        `decode_errors`, `event_code_agreement`, the witness count, and
        `trial_count_agreement`/`block_agreement` are for.

        **The pre-1c-5 timing check still forces D on its own, unconditionally,
        checked BEFORE `resolve_tier` is consulted at all.** None of
        `TierInputs`' seven fields represents an alignment failure -- folding
        one into (say) `decode_errors` would corrupt a column whose whole
        point is to mean one specific, re-derivable thing. Every measured
        input is still computed and stored even when this fires, for the same
        reason `worst_residual_us`/`worst_drift_ppm` already were: a session
        already known to have failed is not any less worth recording the rest
        of.
        """
        from wl_preproc.contracts.events import DecodeError, decode_stream
        from wl_preproc.contracts.sidecar import BehaviorCameraSidecar
        from wl_preproc.events import agreement
        from wl_preproc.events.extract import extract_nidq_words, extract_rhs_witness
        from wl_preproc.events.taskfile import SyntheticTaskFileReader, UnsupportedTaskFile
        from wl_preproc.schema import core, ingest
        from wl_preproc.schema.events import decode_syncbox_in_session_time
        from wl_preproc.timebase import segments
        from wl_preproc.timebase.fit import fit_offset

        session_key = {k: key[k] for k in pipeline.Session.primary_key}

        fits = (SystemTimebase & key).to_dicts()
        segment_rows = (core.Segment & key).to_dicts()
        rejected = (core.RejectedSegment & key).to_dicts()
        systems = (core.AcquisitionSystem & key).to_dicts()
        system_names = {row["system"] for row in systems}

        # The sync box emitted them; every other system is measured against it.
        emitted = max(
            (fit["n_barcodes_decoded"] for fit in fits if fit["system"] == "syncbox"),
            default=0,
        )
        aligned = [fit for fit in fits if fit["fit_status"] == "fitted"]

        # A timing check failed if any present system could not be fitted, or
        # if any file was rejected. Both are failures of THIS phase's own
        # checks, unrelated to any of TierInputs' own fields and unchanged
        # from before 1c-5.
        failed = len(aligned) < len(systems) or bool(rejected)

        session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))

        # -- The Pi's own canonical code stream, and everything chained off
        # comparing against it. Gated behind `syncbox` actually having a
        # FITTED `SystemTimebase` row -- not merely `"syncbox" in
        # system_names`, which only proves a Manual `AcquisitionSystem` row
        # exists, not that a real file sits behind it.
        #
        # This matters for a session this table's own `key_source`
        # (`pipeline.Session & ingest.Ingestion`) can reach that a REAL
        # landed session never is: `tests/schema/test_guardrails.py`'s blob
        # round-trip sweep inserts a bare `Session`+`Ingestion` pair with a
        # synthetic, non-existent `session_dir` ("blobprobe-session_dir") and
        # no `AcquisitionSystem` row at all, to exercise `Ingestion`'s own
        # blob column in isolation -- and this table's shared, session-scoped
        # database (`tests/conftest.py`'s `dj_conn`/`prefix`) means that row
        # is still sitting there whenever ANY test later calls
        # `TimingProvenance.populate()`. Before 1c-5 this was harmless: the
        # old `make()` touched only already-fetched DB dicts, never a file.
        # Attempting a real decode against a directory that does not exist
        # would crash this table's very first `.populate()` call in the
        # shared suite, on a session belonging to a wholly unrelated test.
        # `fit_status == "fitted"` is the exact same test `Segment.key_source`
        # already relies on to prove a file was genuinely read.
        #
        # A second layer catches the reverse: `SystemTimebase.make()` marks
        # syncbox "fitted" even with zero decoded barcodes (a real log file
        # that happens to be empty), so `decode_syncbox_in_session_time`'s own
        # `session_reference` can still raise `ValueError`. Caught here rather
        # than left to propagate, for the same reason a single trial's decode
        # error does not lose the whole session in `assemble()`.
        syncbox_fitted = any(
            fit["system"] == "syncbox" and fit["fit_status"] == "fitted" for fit in fits
        )
        syncbox_pairs: list[tuple[float, int]] = []
        decode_errors = 0
        n_full_code_records = 0
        event_code_agreement = None
        n_strobe_witnesses = 0
        # True only once the try block below actually completes -- an empty
        # `syncbox_pairs` from a GENUINE decode (a real log with zero code
        # words) must still count as one full-code record, so this can never
        # be conflated with `syncbox_pairs` itself being falsy.
        syncbox_available = False

        if not failed and syncbox_fitted:
            try:
                syncbox_pairs = decode_syncbox_in_session_time(session_dir)
                syncbox_available = True
            except (FileNotFoundError, ValueError):
                syncbox_available = False

        if syncbox_available:
            syncbox_events = decode_stream(syncbox_pairs)
            decode_errors = sum(1 for event in syncbox_events if isinstance(event, DecodeError))
            n_full_code_records = 1

            # -- The NI's independent full-code record, where `spikeglx` is
            # present AND its own barcode fit succeeded (no rate to convert
            # native time with otherwise). A recording whose sidecar declares
            # fewer than two digital words -- barcode only, no code lines --
            # is caught by `extract_nidq_words` itself and treated as absent,
            # not a crash: the same "specified and unavailable, not silently
            # wrong" rule `timebase/extract.py::extract_bcam` already applies
            # to a sidecar missing its own optional fields.
            nidq_pairs: list[tuple[float, int]] | None = None
            if "spikeglx" in system_names:
                spikeglx_fit = next(
                    (
                        fit
                        for fit in fits
                        if fit["system"] == "spikeglx" and fit["fit_status"] == "fitted"
                    ),
                    None,
                )
                if spikeglx_fit is not None:
                    rate = segments.rate_fit_from_row(spikeglx_fit)
                    reference = segments.session_reference(session_dir)
                    pairs: list[tuple[float, int]] = []
                    for scan in segments.scan_system("spikeglx", session_dir / "spikeglx"):
                        try:
                            word_stream = extract_nidq_words(scan.path)
                        except ValueError:
                            continue
                        offset = fit_offset(scan.barcodes, reference, rate)
                        pairs.extend(
                            (native / word_stream.fs_hz / rate.scale + offset.offset_s, code)
                            for native, code in word_stream.words
                        )
                    pairs.sort(key=lambda pair: pair[0])
                    if pairs:
                        nidq_pairs = pairs

            if nidq_pairs is not None:
                n_full_code_records = 2
                nidq_events = decode_stream(nidq_pairs)
                decode_errors += sum(
                    1 for event in nidq_events if isinstance(event, DecodeError)
                )
                # Compared by SEQUENCE POSITION, not by time: the strobe
                # latches whatever value the shared bus carries at that
                # instant, independent of either system's own clock rate --
                # so the codes agree exactly regardless of drift; only their
                # apparent timing is drift-affected.
                codes_pi = [code for _, code in syncbox_pairs]
                codes_ni = [code for _, code in nidq_pairs]
                total = max(len(codes_pi), len(codes_ni))
                matched = sum(1 for a, b in zip(codes_pi, codes_ni) if a == b)
                event_code_agreement = (matched / total) if total else 1.0

            # -- The RHS strobe witness, where present. Section 7's own
            # warning: "The strobe witness must assert a count, not merely
            # that edges exist" -- so an edge count that does not match the
            # Pi's own word count does not count as a witness, rather than
            # being trusted on the strength of existing at all.
            if "rhs" in system_names:
                try:
                    witness = extract_rhs_witness(session_dir / "rhs")
                except FileNotFoundError:
                    witness = None
                if witness is not None and witness.n_edges == len(syncbox_pairs):
                    n_strobe_witnesses = 1

        # -- trial_count_agreement: codes (the already-populated canonical
        # trial list) versus the task file. `None` when there is no task file
        # to read at all, or the one present is not this reader's format --
        # both are "nothing corroborated this session", never a pass.
        trial_count_agreement = None
        task_file = session_dir / "syncbox" / "task.json"
        if task_file.is_file():
            try:
                task_trials = SyntheticTaskFileReader().trials(task_file)
            except UnsupportedTaskFile:
                trial_count_agreement = None
            else:
                n_trials_from_codes = len(pipeline.trial.Trial & session_key)
                trial_count_agreement = len(task_trials) == n_trials_from_codes

        # -- camera_trigger_count: the bcam sidecar versus frames actually
        # received. `None` with no bcam system at all -- nothing measured, not
        # a zero.
        camera_trigger_count = None
        if "bcam" in system_names:
            bcam_dir = session_dir / "bcam"
            sidecar_paths = sorted(bcam_dir.glob("*.yaml")) if bcam_dir.is_dir() else []
            if sidecar_paths:
                sidecar = BehaviorCameraSidecar.from_yaml(
                    sidecar_paths[0].read_text(encoding="utf-8")
                )
                camera_trigger_count = sidecar.frame_count - len(sidecar.dropped_frame_ids)

        # -- block_agreement: the measured boundary (`trial.Block`) against
        # wl.works' own assertion (`core.Block`). Spec section 5: a
        # disagreement here is its own tier-D condition, "not a silent
        # reconciliation" -- `None` when wl.works asserted no blocks at all
        # for this session, the identical convention `trial_count_agreement`
        # already uses for "nothing to compare against". Matched by
        # `block_id`, mirroring how `timebase/fit.py`'s own barcode matching
        # is "by value, never by ordinal position".
        asserted_blocks = {
            row["block_id"]: (row["start_s"], row["end_s"])
            for row in (core.Block & session_key).to_dicts()
        }
        block_agreement = None
        if asserted_blocks:
            measured_blocks = {
                row["block_id"]: (row["block_start_time"], row["block_stop_time"])
                for row in (pipeline.trial.Block & session_key).to_dicts()
            }
            block_agreement = all(
                block_id in measured_blocks
                and abs(measured_blocks[block_id][0] - asserted_start)
                <= _BLOCK_AGREEMENT_TOLERANCE_S
                and abs(measured_blocks[block_id][1] - asserted_end)
                <= _BLOCK_AGREEMENT_TOLERANCE_S
                for block_id, (asserted_start, asserted_end) in asserted_blocks.items()
            )

        inputs = agreement.TierInputs(
            event_code_agreement=event_code_agreement,
            trial_count_agreement=trial_count_agreement,
            camera_trigger_count=camera_trigger_count,
            n_full_code_records=n_full_code_records,
            n_strobe_witnesses=n_strobe_witnesses,
            decode_errors=decode_errors,
            block_agreement=block_agreement,
        )
        tier = "D" if failed else agreement.resolve_tier(inputs)

        self.insert1(
            {
                **key,
                "tier": tier,
                "n_barcodes_emitted": emitted,
                "n_systems_aligned": len(aligned),
                "n_segments": len(segment_rows),
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
                # Always empty now: the wait it used to name is over. See
                # `PENDING_TIER_INPUTS`.
                "pending_inputs": "",
                "event_code_agreement": event_code_agreement,
                "trial_count_agreement": trial_count_agreement,
                "camera_trigger_count": camera_trigger_count,
                "n_full_code_records": n_full_code_records,
                "n_strobe_witnesses": n_strobe_witnesses,
                "decode_errors": decode_errors,
                "block_agreement": block_agreement,
            }
        )


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}timebase`. Idempotent."""
    core.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}timebase", create_tables=True)
