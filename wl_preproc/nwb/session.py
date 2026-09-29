"""The file itself: identity, clock and subject (design spec
`2026-09-28-nwb-builder-design.md` section 3, file-level metadata)."""

from __future__ import annotations

import datetime

from pynwb import NWBFile
from pynwb.file import Subject


def new_file(session: dict) -> NWBFile:
    """An empty `NWBFile` for one activation.

    `session` carries `identifier`, `session_id`, `description`,
    `reference_time` (timezone-aware: the wall-clock time of session t = 0,
    section 4.2), `experimenter` (or None) and `subject` (`subject_id`,
    `species`, `sex`, `date_of_birth`; species and date of birth may be
    None). `session_start_time` and `timestamps_reference_time` are both the
    reference time, so every time in the file is session seconds."""
    reference = session["reference_time"]
    if reference.tzinfo is None:
        raise ValueError("reference_time must be timezone-aware")
    subject = session["subject"]
    date_of_birth = subject.get("date_of_birth")
    if isinstance(date_of_birth, datetime.date) and not isinstance(date_of_birth, datetime.datetime):
        date_of_birth = datetime.datetime.combine(date_of_birth, datetime.time(), tzinfo=datetime.timezone.utc)
    return NWBFile(
        session_description=session["description"],
        identifier=session["identifier"],
        session_start_time=reference,
        timestamps_reference_time=reference,
        session_id=session["session_id"],
        experimenter=[session["experimenter"]] if session.get("experimenter") else None,
        subject=Subject(
            subject_id=subject["subject_id"],
            species=subject.get("species"),
            sex=subject.get("sex") or "U",
            date_of_birth=date_of_birth,
            description="As wl.works' job request stated it (design spec section 9).",
        ),
    )
