"""What the NWB builder wrote, per activation (design spec
`2026-09-28-nwb-builder-design.md` sections 2, 7 and 8).

`dj.Manual`, filled by the daemon's bespoke `_nwb_stage` and by `wlpp nwb
build` -- the archive stage's pattern, because the builder writes a file under
a configured root, which a `dj.Computed.make()` cannot be given."""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import DEFAULT_PREFIX, request

schema = dj.Schema()


@schema
class NwbFile(dj.Manual):
    definition = """
    # One activation's NWB file, or why there is none.
    # Key: (subject, session_datetime, montage_id, activation_id).
    -> request.Activation
    ---
    # written: built and no nwbinspector finding at CRITICAL or above
    # (ERROR, PYNWB_VALIDATION, CRITICAL; `n_critical` counts them).
    # invalid: built, with at least one (kept for inspection; piece 2
    # publishes only `written`). refused: not built, `reason` says why; never
    # retried automatically -- delete the row to rebuild.
    status : enum('written','invalid','refused')
    # path and n_bytes: the scratch copy the builder wrote. Publishing deletes
    # it once the file is placed; where the file is now is its latest
    # NwbPlacement. Deleting a published row to rebuild it is safe: the
    # rebuild takes over the published file when its written-once data
    # matches, and is refused when it does not (design spec
    # `2026-09-29-nwb-publishing-design.md` section 4's amendment).
    path = '' : varchar(1024)
    n_bytes = null : bigint unsigned
    built_at : datetime
    nwb_identifier = '' : varchar(255)
    # The wall-clock time of session t = 0 (section 4.2), naive UTC, and
    # where it came from: the first barcode's own value, or the manifest's
    # started_at when the two disagree by more than 60 s.
    reference_time = null : datetime(6)
    reference_source = null : enum('barcode','manifest')
    started_at_difference_s = null : double
    n_critical = null : int unsigned
    inspector_findings = null : <blob>
    # What the file holds, for wl.works' dataset builder (design spec
    # `2026-09-29-nwb-publishing-design.md` section 2); null when refused.
    description = null : <blob>
    reason = '' : varchar(1024)
    """

    class Dataset(dj.Part):
        definition = """
        # One written-once dataset's checksum (parent spec section 8.2): its
        # decoded contents, never a group. A ragged column names its pair.
        # Key: (subject, session_datetime, montage_id, activation_id, dataset_path).
        -> master
        dataset_path : varchar(512)
        ---
        dtype : varchar(64)
        shape : varchar(64)
        sha256 : char(64)
        paired_with = '' : varchar(512)
        """



@schema
class NwbChange(dj.Manual):
    definition = """
    # Every change to an activation's file that wl.works polls for: built
    # (an NwbFile row), published, or moved between shares. The sequence only
    # increases, and is GET /nwb's cursor (design spec
    # `2026-09-29-nwb-publishing-design.md` sections 6 and 9).
    # Key: (change_seq).
    change_seq : int unsigned auto_increment
    ---
    -> NwbFile
    kind : enum('built','published','moved')
    changed_at : datetime(6)
    """


@schema
class NwbPlacement(dj.Manual):
    definition = """
    # Where an activation's file is, one row per publish or move: append-only,
    # and the latest row for an activation is where the file is now (design
    # spec `2026-09-29-nwb-publishing-design.md` section 9). `path` is
    # relative to the share, the triple wl.works' Plan 23 section 10.1 names.
    # Key: (change_seq).
    -> NwbChange
    ---
    tier : enum('slow','fast')
    host : varchar(64)
    share : varchar(64)
    path : varchar(512)
    n_bytes : bigint unsigned
    """


@schema
class ActiveSet(dj.Manual):
    definition = """
    # Each PUT /nwb/active, as received: the whole set of activations
    # wl.works wants on the fast share. Append-only; the latest row is the
    # desired state (design spec `2026-09-29-nwb-publishing-design.md`
    # section 5). Key: (set_seq).
    set_seq : int unsigned auto_increment
    ---
    received_at : datetime(6)
    requested_by = null : varchar(64)
    activations : <blob>   # [{subject, session_datetime, montage_id, activation_id}, ...]
    """

def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}nwb`. Idempotent."""
    request.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}nwb", create_tables=True)
