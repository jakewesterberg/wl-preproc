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


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}nwb`. Idempotent."""
    request.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}nwb", create_tables=True)
