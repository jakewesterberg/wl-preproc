"""Ingestion and Quarantine.

Quarantine is keyed by directory path, not by (subject, session_datetime),
because the worst failure is an unparseable manifest and the manifest is what
yields that key. A key-addressed quarantine row cannot represent the failures
that most need recording, which is the whole reason the table exists.
"""

from __future__ import annotations

import datetime

import datajoint as dj
import pytest

from wl_preproc.schema import ingest


@pytest.fixture(scope="module")
def activated(dj_conn, prefix):
    ingest.activate(prefix=prefix)
    return ingest


def test_quarantine_is_keyed_by_path_not_by_session(activated):
    assert activated.Quarantine.primary_key == ["session_dir"]


def test_quarantine_records_a_failure_with_no_derivable_session_key(activated):
    """The case the table exists for: a manifest so broken that neither the
    subject nor the datetime can be read out of it."""
    activated.Quarantine.insert1(
        {
            "session_dir": "/scratch/2027-03-14_01",
            "failed_at": datetime.datetime(2027, 3, 14, 20, 0, 0),
            "reason": "manifest_invalid",
            "detail": {"error": "while parsing a block mapping"},
            "subject": None,
            "session_dt": None,
        }
    )
    row = (activated.Quarantine & {"session_dir": "/scratch/2027-03-14_01"}).fetch1()

    assert row["subject"] is None
    assert row["detail"] == {"error": "while parsing a block mapping"}


def test_every_reason_the_code_uses_is_declared(activated, enum_values):
    declared = enum_values(activated.Quarantine.heading.attributes["reason"].type)

    assert declared == set(ingest.QUARANTINE_REASONS)


def test_integrity_states_match_the_verifier(activated, enum_values):
    """The enum and the StrEnum must not drift apart — a state the verifier can
    return but the column cannot store is an insert that fails in production
    and nowhere else."""
    from wl_preproc.ingest.verify import Integrity

    declared = enum_values(activated.Ingestion.heading.attributes["integrity"].type)

    assert declared == {member.value for member in Integrity}


def test_ingestion_hangs_off_session(activated):
    assert activated.Ingestion.primary_key == ["subject", "session_datetime"]


# --- Beyond the brief -------------------------------------------------------
#
# None of the tests above ever insert into `Ingestion`. `Quarantine.detail`
# gets an incidental proof that it round-trips as a genuine dict (the equality
# assertion in test_quarantine_records_a_failure_with_no_derivable_session_key
# above would fail if `<blob>` had returned a string: `str({...}) == {...}` is
# always False in Python) — but `topology`, the column the module docstring
# specifically calls out ("the full per-system state map, read as a unit"),
# gets no such proof anywhere in this file.
#
# tests/schema/test_guardrails.py's generic blob sweep cannot fill that gap
# either: `test_every_blob_attribute_round_trips_an_array` explicitly refuses
# any blob-bearing table with a foreign key (`assert not table.parents()`,
# because its synthetic-row builder has no parent chain to build), and
# `Ingestion` has one (`-> pipeline.Session`). That guard's `all_tables`
# fixture is also hardcoded to `(core, coverage, paramset, request)` and does
# not import this module at all, so today it does not reach `Quarantine`
# either, despite `Quarantine` having no foreign key of its own. See this
# task's report for why that scope was left alone rather than widened here.
#
# So the one round-trip that actually matters for this module — a dict landed
# in `topology` comes back a dict, not the string repr of one — is provable
# only by inserting a real row and reading it back, here.


def test_ingestion_topology_blob_round_trips_as_a_genuine_dict(activated):
    """The failure this guards against, and a correction to how it manifests
    for THIS column specifically: tests/schema/test_guardrails.py's module
    docstring describes a bare `longblob` silently storing an inserted numpy
    array as its Python `repr()`, elided above ~1000 elements, with nothing
    raising on insert or fetch — confirmed for arrays by
    test_harness.py::test_a_bare_longblob_corrupts_silently. `topology` never
    carries an array, only a `dict`, and a `dict` is not the same failure:
    verified directly (mutating this table's `topology` to a bare `longblob`
    and inserting the exact payload below) that pymysql's own encoder registry
    special-cases `dict` and raises `TypeError: dict can not be used as
    parameter` at insert time, rather than silently coercing it to a string
    the way it does for a numpy array. So a regression to a bare `longblob`
    here would crash every `land_session` call in production loudly rather
    than corrupt the record silently — a different failure shape, not a
    smaller one, since it would take down ingest entirely rather than merely
    mis-record it. `<blob>` is what makes this column work at all for a dict,
    not merely what makes it survive intact.

    Every `SystemState` member is represented, not just one, so a codec that
    mishandles any single string value would still be caught.
    """
    from wl_preproc.ingest.discover import SystemState
    from wl_preproc.schema import pipeline

    # A subject distinct from every other schema test's ("pico", used by
    # test_core.py and test_request.py at two different session_datetimes) so
    # this insert cannot collide with a Session row another test file already
    # created against the same shared, session-scoped database.
    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "W", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": "ingtest",
            "sex": "U",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": "ingtest",
        "session_datetime": datetime.datetime(2027, 3, 14, 9, 0, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)

    topology = {
        "spikeglx": str(SystemState.PRESENT),
        "syncbox": str(SystemState.ABSENT),
        "bcam": str(SystemState.UNDECLARED),
        "rhs": str(SystemState.PENDING),
    }

    activated.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 14, 20, 0, 0),
            "session_dir": "/scratch/2027-03-14_01_ingtest",
            "integrity": "verified",
            "topology": topology,
            "manifest_hash": "b" * 64,
        }
    )

    got = (activated.Ingestion & session_key).fetch1("topology")

    assert isinstance(got, dict), (
        f"topology round-tripped as {type(got).__name__}, not dict — this is "
        "exactly the bare-longblob failure the <blob> codec exists to prevent"
    )
    assert not isinstance(got, (str, bytes))
    assert got == topology
    assert set(got) == {"spikeglx", "syncbox", "bcam", "rhs"}
    assert got["spikeglx"] == "present"
    for value in got.values():
        assert isinstance(value, str)


def test_ingestion_topology_is_not_a_bare_longblob(activated):
    """The declaration-side half of the same guarantee, pinned directly rather
    than inferred from the round-trip test passing: `attr.codec` is the signal
    tests/schema/test_guardrails.py's own sweep keys on (`attr.is_blob` is True
    for a bare `longblob` too and cannot discriminate the two, per that
    module's comments), so a regression from `<blob>` back to a bare `longblob`
    in this table's definition fails here even before any insert runs.
    """
    assert activated.Ingestion.heading["topology"].codec is not None
    assert activated.Quarantine.heading["detail"].codec is not None
