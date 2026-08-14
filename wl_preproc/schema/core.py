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
    # One run of one task. Mirrors wl.works animal_session_block; boundaries are
    # decoded from event codes and cross-validated against those rows.
    # Key: (subject, session_datetime, block_id).
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
class Segment(dj.Manual):
    definition = """
    # One recording file's extent. Keyed on the first barcode value in the
    # segment, which is globally unique by construction (32-bit counter at 1 Hz).
    # Key: (subject, session_datetime, system, segment_barcode).
    -> AcquisitionSystem
    segment_barcode : int unsigned  # spec 4.1: 32-bit counter, full 0..2**32-1 range
    ---
    start_s   : double
    end_s     : double
    n_samples : bigint
    """


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
