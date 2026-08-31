# tests/schema/test_detect_schema.py
"""EyeValidity and EyeDetection: the validity mask and the detected-event
trace, both stored as runs -- the first stored derived array in this
pipeline, and the reason it is not a blob (`tests/schema/test_guardrails.py`).

Follows `tests/schema/test_eye_schema.py`'s own house pattern: a small,
module-scoped `schemas` fixture that activates exactly the module this file
needs, and `enum_values` taken as the `tests/schema/conftest.py` fixture
(never imported -- no such importable module exists).

Both tables ship with an empty `key_source` in this task; `make()` is
Task 7. See `EyeValidity.key_source`'s own docstring for why an empty
`key_source` is deliberate here rather than a placeholder to fix later.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def schemas(dj_conn, prefix):
    from wl_preproc.schema import detect

    detect.activate(prefix=prefix)
    return detect


def test_no_bare_longblob(schemas):
    """The repo-wide sweep covers this, but state it here: under DataJoint 2.x
    a bare longblob stores a numpy array as its truncated string repr --
    measured, 31,488 float32 values became 488 bytes, unrecoverable. This
    subsystem is the first to store a derived array, so it is the first that
    could reintroduce it."""
    assert "longblob" not in schemas.EyeValidity.definition
    assert "longblob" not in schemas.EyeValidity.Run.definition
    assert "longblob" not in schemas.EyeDetection.definition
    assert "longblob" not in schemas.EyeDetection.Run.definition


def test_the_label_enum_declares_all_eight_values(schemas, enum_values):
    """Complete from the start even though stage 1 produces five. Adding a
    value later is a schema change and the migration window closes January
    2027."""
    from wl_preproc.eye.detect.labels import Label

    attr = schemas.EyeDetection.Run.heading.attributes["label"]
    assert enum_values(attr.type) == {label.value for label in Label}


def test_the_detection_key_carries_both_paramsets(schemas):
    """Two traces are comparable only if masked identically, so which mask was
    used belongs in the key (design spec section 2)."""
    key = schemas.EyeDetection.primary_key
    assert "validity_paramset_idx" in key
    assert "paramset_idx" in key


def test_trace_is_not_called_eye_and_admits_a_conjunction(schemas, enum_values):
    """A conjunction is honestly not an eye."""
    attr = schemas.EyeDetection.heading.attributes["trace"]
    assert enum_values(attr.type) == {"left", "right", "conjunction"}
    assert "eye" not in schemas.EyeDetection.primary_key


def test_validity_is_keyed_per_real_eye_not_per_trace(schemas, enum_values):
    """The mask is a property of one eye's recording; there is no conjunction
    of masks."""
    attr = schemas.EyeValidity.heading.attributes["eye"]
    assert enum_values(attr.type) == {"left", "right"}


def test_a_run_row_carries_its_measurements_nullably(schemas):
    """A saccade run IS an event, so it carries amplitude and peak velocity;
    fixation, blink and invalid runs leave them null."""
    for name in ("amplitude_deg", "peak_velocity_deg_s", "reliability"):
        assert schemas.EyeDetection.Run.heading.attributes[name].nullable


def test_both_tables_are_daemon_stages():
    """`TrialCoverage` was missing from `_computed_tables()` for a whole phase
    and silently returned tier D for every session."""
    from wl_preproc import daemon

    names = {table.__name__ for table in daemon._computed_tables()}
    assert {"EyeValidity", "EyeDetection"} <= names


def test_a_refusal_is_expressible(schemas):
    """A session whose calibration was refused has no gaze, so detection is
    refused too -- with a reason, never an error and never a fabricated run."""
    for table in (schemas.EyeValidity, schemas.EyeDetection):
        assert table.heading.attributes["status"] is not None
        assert table.heading.attributes["reason"] is not None


# --- Beyond the brief's own floor -------------------------------------------
#
# The brief's own `test_the_detection_key_carries_both_paramsets` above checks
# only membership of `validity_paramset_idx`/`paramset_idx`. That is not
# enough to prove the two `ParamSet` references are actually independent:
# DataJoint accepts `-> paramset.ParamSet.proj(validity_paramset_idx=
# 'paramset_idx')` followed by a bare `-> paramset.ParamSet` with NO error at
# declaration time -- confirmed directly against a live MySQL 8 (this
# project's pinned DataJoint 2.3.2) -- and the result SHARES one physical
# `paramset_type` column between the two foreign keys rather than giving each
# its own. A version with that defect still reports `validity_paramset_idx`
# and `paramset_idx` in `primary_key`, so the brief's own test above would
# stay green while the table quietly forced a validity mask and a detector to
# share the identical `paramset_type` string -- which design spec section 2
# never asks for and directly contradicts ("a validity mask... rather than
# living inside each detector's paramset"). The two tests below are the
# regression guard that catches exactly that: the first structurally, the
# second by actually inserting two different `paramset_type` values and
# proving neither is silently forced to match the other.


def test_the_detection_key_has_no_shared_paramset_type_column(schemas):
    """Both `ParamSet` references must contribute their OWN `paramset_type`
    column -- not one column fed by two different foreign keys, which is what
    a projection renaming only `paramset_idx` (and leaving `paramset_type`
    bare on both references) produces with no declaration-time error. See
    `test_the_two_paramset_references_can_name_different_paramset_types`
    below for the live insert that a purely structural check like this one
    cannot give by itself.
    """
    key = set(schemas.EyeDetection.primary_key)
    assert {"paramset_type", "validity_paramset_type"} <= key, (
        "EyeDetection.primary_key is missing an independent paramset_type "
        f"column for one or both ParamSet references; got {sorted(key)}"
    )


def test_the_two_paramset_references_can_name_different_paramset_types(
    schemas, dj_conn, prefix
):
    """The live proof. Design spec section 2's whole reason for two paramset
    columns is that a validity mask (`eye_validity`) and a detector paramset
    are unrelated vocabularies that happen to share one lookup table -- nothing
    requires them to share a `paramset_type` string, and nothing should.

    Reproduced directly against the brief's own literal projection (rename
    `paramset_idx` only, leave `paramset_type` bare on both references):
    DataJoint declares the table without error, then this exact insert raises
    `IntegrityError`, because the shared `paramset_type` column cannot equal
    two different strings at once.

    `EyeDetection` is `dj.Computed` with no `make()` yet (Task 7), so
    `insert1` is refused outside a `make()` call -- see
    `tests/schema/test_guardrails.py`'s own `_build_parents` comment. A
    `dj.FreeTable` carries no such restriction (a generic wrapper around a
    connection and a table name, with no `_allow_insert` attribute of its
    own), which is the identical bypass that function already relies on to
    insert through an auto-populated ancestor.
    """
    import datetime

    import datajoint as dj

    from wl_preproc.schema import paramset, pipeline

    paramset.activate(prefix=prefix)

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    # `element_animal.Subject.subject` is `varchar(8)` (confirmed against the
    # live heading; `tests/schema/test_guardrails.py`'s own `_synthetic_value`
    # comment records the same limit) -- "detectprobe" (11 chars) overflows it
    # and raises `pymysql.err.DataError` under this project's strict-mode
    # MySQL 8, caught directly the first time this test ran. "detprobe" fits
    # exactly at 8, the same margin `test_eye_schema.py`'s own "eyeprobe"
    # sentinel uses.
    pipeline.subject.Subject.insert1(
        {
            "subject": "detprobe",
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    # A sentinel date outside every other fixture in this shared,
    # session-scoped database, matching `test_eye_schema.py`'s own
    # `test_registering_them_does_not_break_a_clean_daemon_pass` precedent.
    session_key = {
        "subject": "detprobe",
        "session_datetime": datetime.datetime(2098, 1, 1, 0, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)

    validity_idx = paramset.register("detect_schema_probe_validity", {"a": 1})
    detector_idx = paramset.register("detect_schema_probe_detector", {"b": 2})

    free_table = dj.FreeTable(dj_conn, schemas.EyeDetection.full_table_name)
    free_table.insert1(
        {
            **session_key,
            "trace": "left",
            "validity_paramset_type": "detect_schema_probe_validity",
            "validity_paramset_idx": validity_idx,
            "paramset_type": "detect_schema_probe_detector",
            "paramset_idx": detector_idx,
            "status": "computed",
        }
    )

    row = (
        schemas.EyeDetection
        & {**session_key, "trace": "left", "paramset_idx": detector_idx}
    ).fetch1()
    assert row["validity_paramset_type"] == "detect_schema_probe_validity"
    assert row["paramset_type"] == "detect_schema_probe_detector"
