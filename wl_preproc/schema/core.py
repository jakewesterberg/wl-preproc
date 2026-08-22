# wl_preproc/schema/core.py
"""The custom core tables: montages, blocks, acquisition systems and segments.

Segments and blocks are orthogonal and both are required (spec section 5.2.1).
A block is one run of one task; a segment is one recording file's extent, forced
by an RHS stim-parameter change, a crash or a restart. A block can span segments
and a segment can span blocks, so neither is derivable from the other.
"""

from __future__ import annotations

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
    # is a different quantity and belongs to whatever decodes event codes (1c-5),
    # in its own Computed table. Key: (subject, session_datetime, block_id).
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
