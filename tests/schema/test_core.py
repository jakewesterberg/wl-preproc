# tests/schema/test_core.py
import datetime

import pytest

from wl_preproc.contracts.paths import SYSTEMS

PREFIX = "t_"


@pytest.fixture(scope="module")
def core(dj_conn):
    from wl_preproc.schema import core, pipeline

    pipeline.activate(prefix=PREFIX)
    core.activate(prefix=PREFIX)
    return core


@pytest.fixture(scope="module")
def a_session(core):
    from wl_preproc.schema import pipeline

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": "pico",
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    key = {"subject": "pico", "session_datetime": datetime.datetime(2027, 3, 14, 9, 0)}
    pipeline.Session.insert1(key, skip_duplicates=True)
    return key


def test_every_table_declares_and_documents_its_key(core):
    for table in (
        core.Montage,
        core.Block,
        core.AcquisitionSystem,
        core.Segment,
        core.RejectedSegment,
    ):
        assert table.primary_key, table.__name__
        assert table.definition.strip().startswith("#"), (
            f"{table.__name__} has no in-schema comment; section 10 requires keys "
            "to be documented where they are declared"
        )


def test_montage_is_keyed_under_session(core):
    assert set(core.Montage.primary_key) == {"subject", "session_datetime", "montage_id"}


def test_segment_is_keyed_on_system_and_barcode(core):
    assert set(core.Segment.primary_key) == {
        "subject",
        "session_datetime",
        "system",
        "segment_barcode",
    }


def test_a_segment_round_trips(core, a_session):
    core.AcquisitionSystem.insert1({**a_session, "system": "spikeglx"}, skip_duplicates=True)
    row = {
        **a_session,
        "system": "spikeglx",
        "segment_barcode": 1_000_000,
        "start_s": 0.0,
        "end_s": 12.0,
        "n_samples": 360_000,
    }
    core.Segment.insert1(row, skip_duplicates=True)
    got = (core.Segment & {k: row[k] for k in core.Segment.primary_key}).fetch1()
    assert got["segment_barcode"] == 1_000_000
    assert got["end_s"] == pytest.approx(12.0)


def test_only_known_systems_are_accepted(core, a_session):
    """`system` is an enum over SYSTEMS, so a typo fails at insert rather than
    creating a silent third acquisition system.

    Raises pymysql.err.DataError, not dj.DataJointError: DataJoint 2.3.2's MySQL
    error translator (datajoint.adapters.mysql.MySQLAdapter.translate_error) maps
    a fixed set of MySQL errnos to DataJointError subclasses (1062, 1217/1451/
    1452/3730, 1064, 1146, 1364, 1054, plus the connection-loss codes) and passes
    everything else through unchanged. Errno 1265 ("Data truncated for column"),
    MySQL strict mode's response to an out-of-range enum value, is not in that
    set, so the raw pymysql exception propagates. Verified against a real
    MySQL 8.0 container, not assumed.
    """
    import pymysql

    with pytest.raises(pymysql.err.DataError):
        core.AcquisitionSystem.insert1({**a_session, "system": "spikeglex"})


def test_system_enum_matches_the_frozen_contract(core):
    """SYSTEMS is a frozen interface (section 3.5, directory layout). The schema
    must not drift from it."""
    declared = core.AcquisitionSystem.heading["system"].type
    for system in SYSTEMS:
        assert system in declared, f"{system} missing from the schema's enum"


def test_rejected_segment_records_why(core, a_session):
    core.AcquisitionSystem.insert1({**a_session, "system": "rhs"}, skip_duplicates=True)
    core.RejectedSegment.insert1(
        {
            **a_session,
            "system": "rhs",
            "file_path": "rhs/2027-03-14_03_rhs/amplifier.dat",
            "reason": "no decodable barcode",
        },
        skip_duplicates=True,
    )
    assert len(core.RejectedSegment & a_session) == 1
