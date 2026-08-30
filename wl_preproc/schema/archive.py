# wl_preproc/schema/archive.py
"""Archival state: the artifact, its per-file verification, holds and
reclamations.

**No status column anywhere.** Plan 10 section 1 forbids one and design spec
section 8 gives the reason: a stored verdict is a second answer free to drift
from the facts it came from. Whether a session is reclaimable is COMPUTED from
these rows (`archive/reclaim.py`), never stored.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import DEFAULT_PREFIX, pipeline

schema = dj.Schema()


@schema
class ArchiveArtifact(dj.Manual):
    definition = """
    # One compressed artifact per session -- wl.works Plan 25 section 1.2's
    # grain, where per-block was designed, recommended and withdrawn.
    # Key: (subject, session_datetime).
    -> pipeline.Session
    ---
    # The settled triple (Plan 23 section 4.3): host + share + relative path.
    # Never an opaque string -- an agent must be able to open the file rather
    # than a human read a path out of a field.
    archive_host  : varchar(64)
    archive_share : varchar(64)
    archive_path  : varchar(255)
    codec         : varchar(32)   # what actually ran, not what the code defaults to
    clevel        : tinyint
    compressed_bytes : bigint
    # blake3 over sorted (relative path, blake3) pairs. A Zarr store is a tree
    # and has no single hash; a digest over concatenated bytes would depend on
    # walk order and differ between two identical copies.
    manifest_digest : varchar(64)
    compressed_at   : datetime
    """


@schema
class ArchiveVerification(dj.Manual):
    definition = """
    # One row per original file. Per file because when verification fails the
    # question is immediately WHICH file, and a per-session boolean cannot
    # answer it (design spec section 4).
    # Key: (subject, session_datetime, relative_path).
    -> ArchiveArtifact
    relative_path : varchar(255)
    ---
    expected_blake3 : varchar(64)   # from the DONE marker, written by the rig
    actual_blake3   : varchar(64)   # from reconstructing the artifact
    matched         : tinyint
    verified_at     : datetime
    """


@schema
class ReclamationHold(dj.Manual):
    definition = """
    # A human blocking or forcing reclamation. The ONLY place a person appears
    # in this subsystem: design spec section 5.3 inverts section 8.5's gate from
    # "wait unless approved" to "proceed unless held".
    # Key: (subject, session_datetime, held_at) -- a session accrues a HISTORY
    # of holds and forces, never one row overwritten in place.
    -> pipeline.Session
    held_at : datetime
    ---
    actor   : varchar(64)
    verdict : enum('hold','force')
    reason  : varchar(512)
    """


@schema
class ScratchReclamation(dj.Manual):
    definition = """
    # What was freed, and when. Key: (subject, session_datetime).
    -> pipeline.Session
    ---
    reclaimed_at : datetime
    bytes_freed  : bigint
    """


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}archive`. Idempotent."""
    pipeline.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}archive", create_tables=True)
