"""Landing a session without a system whose transfer never finished (design spec
`2026-10-18-training-records-and-backup-clock-design.md` section 5, decision 10).

A listed system whose DONE marker never arrives holds its session: INCOMPLETE,
then STALLED in the daily report, the system named. The requester ruled that
such a session waits for a person rather than landing on its own after a wait.
The person lands it by one command, `wlpp land-without`, which records who, why
and when in `ingest.LandedWithout` and then evaluates the session once, as a
scan would. The system's partial files stay in the folder, and so in its
archive; it gets no AcquisitionSystem row. The rig's record (`xcon`), for a
manifest that expects it, can be landed without the same way.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import MANIFEST_FILENAME, SYSTEMS, SessionLayout
from wl_preproc.ingest import landing
from wl_preproc.ingest.sentinel import RIG_RECORD, MarkerState, read_marker, rig_record_closed
from wl_preproc.ingest.watcher import Outcome, _scan_one
from wl_preproc.schema import DEFAULT_PREFIX, ingest

_FINISHED = "its DONE marker landed, so there is nothing to land without"


class Refused(Exception):
    """Landing without this would change nothing, or cannot be recorded; the
    message says why."""


def land_without(session_dir: Path, system: str, *, actor: str, reason: str, prefix: str = DEFAULT_PREFIX,
                 now: datetime.datetime | None = None) -> Outcome:
    """Record that `actor` landed the session at `session_dir` without
    `system`, a listed system or `xcon`, for `reason`, and evaluate the
    session once. Run again, it finds its record and evaluates again."""
    at = now or datetime.datetime.now(datetime.UTC)
    # Absolute: every later stage resolves the recorded path from the daemon's
    # own directory, not the shell's this was typed in (part 3a's final
    # review, I4).
    session_dir = Path(session_dir).absolute()
    try:
        manifest = SessionManifest.from_yaml((session_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    except Exception as exc:
        raise Refused(f"its manifest cannot be read: {exc}") from exc
    if len(manifest.subject) > landing.SUBJECT_MAX_LEN or len(str(session_dir)) > landing.INGESTION_SESSION_DIR_MAX_LEN:
        raise Refused("its subject or its folder's path is too long to land; the watcher quarantines it")
    key = landing.manifest_session_key(manifest)
    if landing.already_ingested(key, prefix=prefix):
        raise Refused("it has already landed")
    layout = SessionLayout(session_dir.parent, manifest.session_id)
    if system == RIG_RECORD:
        if not manifest.expects_rig_record:
            raise Refused("its manifest does not expect the rig's record")
        if rig_record_closed(layout):
            raise Refused(f"the rig's record is closed: {_FINISHED}")
    elif system in SYSTEMS:
        if system not in manifest.expected_systems:
            raise Refused(f"{system} is not listed in its manifest")
        if read_marker(layout, system)[0] in (MarkerState.PARSED, MarkerState.EMPTY):
            raise Refused(f"its transfer finished: {_FINISHED}")
    else:
        raise Refused(f"{system} is neither a system nor {RIG_RECORD}")
    ingest.activate(prefix=prefix)
    ingest.LandedWithout.insert1({
        **key, "system": system, "actor": actor[:64], "reason": reason[:512],
        "recorded_at": landing.to_naive_utc(at).replace(microsecond=0), "session_dir": str(session_dir),
    }, skip_duplicates=True)
    return _scan_one(session_dir, prefix, True, at)
