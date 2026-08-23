# wl_preproc/schema/core.py
"""The custom core tables: montages, blocks, acquisition systems and segments.

Segments and blocks are orthogonal and both are required (spec section 5.2.1).
A block is one run of one task; a segment is one recording file's extent, forced
by an RHS stim-parameter change, a crash or a restart. A block can span segments
and a segment can span blocks, so neither is derivable from the other.
"""

from __future__ import annotations

import pathlib

import datajoint as dj

from wl_preproc.contracts.paths import SYSTEMS
from wl_preproc.schema import DEFAULT_PREFIX, pipeline

schema = dj.Schema()

_SYSTEM_ENUM = "enum(" + ",".join(f"'{s}'" for s in SYSTEMS) + ")"


@schema
class Montage(dj.Manual):
    definition = """
    # A maximal interval with no probe movement; the grain of unit identity.
    # Key: (subject, session_datetime, montage_id). Sourced from wl.works
    # item_insertion and nothing else — no insertion record, no canonical.
    -> pipeline.Session
    montage_id : tinyint
    ---
    start_s : double  # session-time seconds
    end_s   : double
    """


@schema
class Block(dj.Manual):
    definition = """
    # One run of one task, mirroring wl.works animal_session_block.
    # start_s/end_s are WL.WORKS' ASSERTION, recorded here through accept() --
    # recording an assertion is not authoring it. Closed open item 9: block rows
    # are authored by wl.works' session planner and wl-preproc never writes
    # them; it cross-validates and quarantines on absence. The MEASURED boundary
    # is a different quantity and lives in element-event's `trial.Block`, written
    # by `schema/events.py::populate_session` (1c-5). This repo declares no table
    # of its own for it: design spec section 5's adoption table assigns "Events,
    # trials, blocks" to `element-event`. A disagreement between the two is its
    # own tier-D condition on `timebase.TimingProvenance.block_agreement`, not a
    # silent reconciliation. Key: (subject, session_datetime, block_id).
    -> pipeline.Session
    block_id : smallint
    ---
    task_type   : varchar(32)
    start_s     : double
    end_s       : double
    works_block_id = null : varchar(64)  # the wl.works row this was matched to
    """


@schema
class AcquisitionSystem(dj.Manual):
    definition = f"""
    # One acquisition system present at a session. The segment unit is an
    # acquisition *run*: one SpikeGLX run stops imec0, imec1 and nidq together,
    # while RHS stops independently. Key: (subject, session_datetime, system).
    -> pipeline.Session
    system : {_SYSTEM_ENUM}
    """


@schema
class Segment(dj.Computed):
    definition = """
    # One recording file's extent, in session time. Keyed on the first barcode
    # value in the segment, which is globally unique by construction (32-bit
    # counter at 1 Hz) -- so discovering a previously-missed file shifts no
    # existing key. Key: (subject, session_datetime, system, segment_barcode).
    #
    # Computed, not Manual: it was declared Manual in 1c-1 when nothing computed
    # it. Every attribute below is derived from the file and the sync box's log.
    -> AcquisitionSystem
    segment_barcode : int unsigned  # spec 4.1: 32-bit counter, full 0..2**32-1 range
    ---
    file_path    : varchar(255)  # relative to the system directory
    start_s      : double
    end_s        : double
    n_samples    : bigint
    first_sample : bigint  # this segment's first sample index in NATIVE time
    offset_s     : double  # session_s = native_s / timebase.scale + offset_s
    residual_us  : double  # RMS about this segment's own offset
    n_barcodes   : int unsigned
    """

    # first_sample, offset_s and residual_us are here because spec 4.5 requires
    # that "fit parameters, residuals, and native stream timestamps [are]
    # retained so every transform is reversible and auditable". On the row, that
    # is a property of the data; in a document, it is a promise. The rate half
    # of the transform is on SystemTimebase, once per session rather than
    # copied onto every segment -- a per-segment copy is a second definition
    # free to drift from the one the fit produced.

    @property
    def key_source(self):
        """Systems whose clock fit has been ATTEMPTED and which had recordings.

        `SystemTimebase` rather than `AcquisitionSystem`, and it is safe to key
        off it precisely because that table records an attempt for every system
        -- `fit_status` of `unfittable` as readily as `fitted`. So a system with
        no fit still reaches here and still records why each of its files could
        not be used, which was the whole objection to keying off it.

        `no_recording` is excluded because there is nothing to scan: including
        it would leave a key that can never insert a row, and DataJoint
        re-attempts such a key on every pass forever (measured -- see
        `SystemTimebase`'s `_FIT_STATUS_ENUM` comment).

        > A system whose files are ALL rejected is still re-scanned each pass,
        > for the same reason: no `Segment` row appears, so the key stays
        > outstanding. That one is deliberate. It is what lets a corrected or
        > re-transferred file be picked up with no manual step, which is the
        > property closed open item 9 requires of anything waiting on data that
        > lands late.

        Imported locally because `timebase` imports this module.
        """
        from wl_preproc.schema import timebase

        return timebase.SystemTimebase & "fit_status != 'no_recording'"

    def make(self, key: dict) -> None:
        """One row per alignable recording, and a RejectedSegment for the rest.

        Both halves are written here, from one scan. Splitting them into two
        populates would extract and decode every recording twice -- the largest
        files in the pipeline -- to reach the same verdict, and would let the
        two disagree about which files exist.

        A rejected file structurally CANNOT become a Segment: this table is
        keyed on `segment_barcode`, "the first barcode value in the segment",
        and a file yielding zero barcodes has no such value. An implementation
        that wants to invent a placeholder has found the rule, not a limitation.
        """
        from wl_preproc.schema import ingest, timebase
        from wl_preproc.timebase import segments
        from wl_preproc.timebase.fit import fit_offset

        session_key = {k: key[k] for k in pipeline.Session.primary_key}
        key = {k: key[k] for k in AcquisitionSystem.primary_key}
        session_dir = pathlib.Path(
            (ingest.Ingestion & session_key).fetch1("session_dir")
        )
        system_dir = session_dir / key["system"]
        scans = segments.scan_system(key["system"], system_dir)
        if not scans:
            return

        fit = (timebase.SystemTimebase & key).fetch1()
        # A row exists for every attempted system, fitted or not, so the check
        # is on the status rather than on the row's presence. An unfitted
        # system's files are each unusable for a reason worth recording.
        rate = (
            segments.rate_fit_from_row(fit) if fit["fit_status"] == "fitted" else None
        )
        reference = segments.session_reference(session_dir)

        accepted, rejected = [], []
        for scan in scans:
            # Relative to the system directory, matching DoneMarker's own
            # convention, so the two name the same file the same way.
            file_path = str(scan.path.relative_to(system_dir)) if scan.path != system_dir else "."
            if scan.verdict != segments.ALIGNABLE:
                rejected.append({**key, "file_path": file_path, "reason": scan.verdict})
                continue
            if rate is None:
                rejected.append(
                    {**key, "file_path": file_path, "reason": segments.UNFITTED_SYSTEM}
                )
                continue
            offset = fit_offset(scan.barcodes, reference, rate)
            first_barcode_us = min(barcode.start_us for barcode in scan.barcodes)
            accepted.append(
                {
                    **key,
                    "segment_barcode": scan.barcodes[0].value,
                    "file_path": file_path,
                    # Native extent, converted with this segment's own offset.
                    "start_s": offset.offset_s,
                    "end_s": scan.duration_s / rate.scale + offset.offset_s,
                    "n_samples": scan.stream.n_samples,
                    # The NATIVE sample index of this segment's first
                    # barcode -- the instant the offset was anchored to.
                    # Spec 4.5 requires native stream timestamps be retained,
                    # and this is the auditable one: it says which sample the
                    # row's own offset was computed from, so the transform can
                    # be checked against the sync box's log rather than only
                    # against itself.
                    "first_sample": round(first_barcode_us * 1e-6 * scan.stream.fs_hz),
                    "offset_s": offset.offset_s,
                    "residual_us": offset.residual_us,
                    "n_barcodes": offset.n_barcodes,
                }
            )

        self.insert(accepted)
        # RejectedSegment is Manual and is written from here rather than by its
        # own populate, because its key -- file_path -- is the one thing a
        # computation over a barcode-keyed table cannot produce. It is the
        # negative half of this same scan, and 1c-1's comment says why it
        # exists at all: recorded rather than dropped, so "why is this session
        # short" has an answer.
        RejectedSegment.insert(rejected, skip_duplicates=True)


@schema
class RejectedSegment(dj.Manual):
    definition = """
    # A file that looked like a segment and was not usable, with the reason.
    # Recorded rather than dropped so that "why is this session short" has an
    # answer. Key: (subject, session_datetime, system, file_path).
    -> AcquisitionSystem
    file_path : varchar(255)
    ---
    reason : varchar(255)
    """


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}core`. Idempotent."""
    pipeline.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}core", create_tables=True)
