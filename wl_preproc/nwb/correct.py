"""Subject corrections in built files (design spec
`2026-10-05-subject-corrections-design.md`).

An animal's species, sex or date of birth corrected in wl.works reaches this
host in the next request, which writes it into element-animal's `Subject` for
the whole animal. Every `written` file of the animal built with other details
is then corrected where it is: a copy is patched, checked and swapped in, so
the live file is never written into and the lab's annotations travel inside
the copy. Only the subject's datasets change, with a note of what did."""

from __future__ import annotations


def stale_files() -> list[dict]:
    """Every `written` activation whose stored description names subject
    details other than the subject's current ones (spec section 2): by value,
    as `build.resolved_invalid` finds `invalid` files, since subject details
    carry no timestamp. Published or not, canonical or derivative, current or
    superseded."""
    from wl_preproc.nwb.gather import described_subject
    from wl_preproc.schema import nwb as nwb_schema

    stale, subjects = [], {}
    for row in (nwb_schema.NwbFile & {"status": "written"}).proj("description").to_dicts():
        if row["subject"] not in subjects:
            subjects[row["subject"]] = described_subject(row["subject"])
        now = subjects[row["subject"]]
        built_with = (row["description"] or {}).get("subject") or {}
        if {field: built_with.get(field) for field in now} != now:
            stale.append({k: row[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")})
    return stale
