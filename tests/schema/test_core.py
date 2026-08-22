# tests/schema/test_core.py
import datetime

import pytest

from wl_preproc.contracts.paths import SYSTEMS


@pytest.fixture(scope="module")
def core(dj_conn, prefix):
    from wl_preproc.schema import core, pipeline

    pipeline.activate(prefix=prefix)
    core.activate(prefix=prefix)
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


def test_a_block_round_trips_with_and_without_works_block_id(core, a_session):
    """`works_block_id = null : varchar(64)` is exercised in both directions: one
    row that leaves it at its null default, one that sets it."""
    core.Block.insert1(
        {
            **a_session,
            "block_id": 1,
            "task_type": "rf_map",
            "start_s": 0.0,
            "end_s": 300.0,
        },
        skip_duplicates=True,
    )
    core.Block.insert1(
        {
            **a_session,
            "block_id": 2,
            "task_type": "attention",
            "start_s": 300.0,
            "end_s": 900.0,
            "works_block_id": "abc-123",
        },
        skip_duplicates=True,
    )
    without_id = (core.Block & {**a_session, "block_id": 1}).fetch1()
    with_id = (core.Block & {**a_session, "block_id": 2}).fetch1()

    assert without_id["task_type"] == "rf_map"
    assert without_id["start_s"] == pytest.approx(0.0)
    assert without_id["end_s"] == pytest.approx(300.0)
    assert without_id["works_block_id"] is None

    assert with_id["task_type"] == "attention"
    assert with_id["works_block_id"] == "abc-123"


def _segment_row(a_session, **overrides):
    """A complete Segment row.

    Every attribute is named because Segment became `dj.Computed` in 1c-4 and
    gained the five that make the transform reversible (spec 4.5). A helper
    rather than a literal per test: two copies of this dict would let one grow
    an attribute the other lacks, and the failure is an insert error in an
    unrelated test.
    """
    row = {
        **a_session,
        "system": "spikeglx",
        "segment_barcode": 1_000_000,
        "file_path": "2027-03-14_01.nidq.bin",
        "start_s": 0.0,
        "end_s": 12.0,
        "n_samples": 360_000,
        "first_sample": 0,
        "offset_s": -0.7,
        "residual_us": 4.2,
        "n_barcodes": 12,
    }
    row.update(overrides)
    return row


def _insert_segment(core, row):
    """Insert into a Computed table from a test, saying why that is allowed.

    `allow_direct_insert` is DataJoint's documented override and exists for
    exactly this: asserting what a table STORES, separately from what its
    `make()` decides to store. The tests that prove `make()` writes only what it
    should are in `tests/timebase/`, and they populate rather than insert.
    """
    core.Segment.insert1(row, skip_duplicates=True, allow_direct_insert=True)


def test_a_segment_round_trips(core, a_session):
    core.AcquisitionSystem.insert1({**a_session, "system": "spikeglx"}, skip_duplicates=True)
    row = _segment_row(a_session)
    _insert_segment(core, row)
    got = (core.Segment & {k: row[k] for k in core.Segment.primary_key}).fetch1()
    assert got["segment_barcode"] == 1_000_000
    assert got["start_s"] == pytest.approx(0.0)
    assert got["end_s"] == pytest.approx(12.0)
    assert got["n_samples"] == 360_000


def test_a_segment_retains_what_makes_its_transform_reversible(core, a_session):
    """Spec 4.5 requires fit parameters, residuals and native stream timestamps
    be retained so every transform is reversible. Round-tripping them is what
    makes that a property of the data rather than a promise in a document — and
    a `double` that silently became a `float` would lose exactly the digits an
    offset in seconds needs to stay microsecond-faithful."""
    core.AcquisitionSystem.insert1({**a_session, "system": "spikeglx"}, skip_duplicates=True)
    row = _segment_row(
        a_session,
        segment_barcode=1_000_777,
        first_sample=21_000,
        offset_s=-0.700_123_456,
        residual_us=4.25,
        n_barcodes=12,
    )
    _insert_segment(core, row)

    got = (core.Segment & {k: row[k] for k in core.Segment.primary_key}).fetch1()

    assert got["file_path"] == row["file_path"]
    assert got["first_sample"] == 21_000
    assert got["offset_s"] == pytest.approx(-0.700_123_456, abs=1e-12)
    assert got["residual_us"] == pytest.approx(4.25)
    assert got["n_barcodes"] == 12


def test_segment_barcode_holds_the_full_unsigned_32_bit_range(core, a_session):
    """segment_barcode : int unsigned. A signed `int` caps at 2,147,483,647 --
    half the ~136-year window spec section 4.1 claims for a 32-bit counter at
    1 Hz (2**32 seconds), and DataJoint 2.3.2 has no `uint32` core type, so the
    native MySQL passthrough spelling is the only way to get the full range.

    Pins the true maximum, 4_294_967_295 (2**32 - 1), rather than merely "some
    value above the signed boundary", and checks both that it round-trips
    intact and that the declared column type is actually unsigned -- a column
    that silently clipped to signed range would still round-trip small values
    fine, so the boundary value is the assertion that actually proves the
    fix.
    """
    core.AcquisitionSystem.insert1({**a_session, "system": "spikeglx"}, skip_duplicates=True)
    barcode = 4_294_967_295
    row = _segment_row(
        a_session, segment_barcode=barcode, end_s=1.0, n_samples=30_000
    )
    _insert_segment(core, row)
    got = (core.Segment & {k: row[k] for k in core.Segment.primary_key}).fetch1()
    assert got["segment_barcode"] == barcode

    declared = core.Segment.heading["segment_barcode"].type
    assert "unsigned" in declared.lower(), (
        f"segment_barcode declared as {declared!r}; expected an unsigned "
        "integer type covering the full 32-bit range"
    )


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


def test_system_enum_matches_the_frozen_contract(core, enum_values):
    """SYSTEMS is a frozen interface (section 3.5, directory layout). The schema
    must not drift from it — in either direction.

    The enum is parsed and compared as a SET. Until 2026-08-14 this looped
    `assert system in declared` over the raw declaration string, which is a
    substring test wearing an exactness claim: `enum('spikeglxx','rhs')` would
    have passed it, and so would a schema carrying a third acquisition system
    the frozen contract no longer names. Neither is a hypothetical — a typo in
    the enum and a stale value left behind after a contract edit are the two
    ways this drifts.
    """
    declared = core.AcquisitionSystem.heading["system"].type
    assert enum_values(declared) == set(SYSTEMS), (
        f"the schema's enum {enum_values(declared)} and the frozen SYSTEMS "
        f"contract {set(SYSTEMS)} have drifted apart"
    )


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
    got = (
        core.RejectedSegment
        & {**a_session, "system": "rhs", "file_path": "rhs/2027-03-14_03_rhs/amplifier.dat"}
    ).fetch1()
    assert got["reason"] == "no decodable barcode"
