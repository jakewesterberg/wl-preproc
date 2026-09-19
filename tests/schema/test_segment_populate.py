# tests/schema/test_segment_populate.py
"""`Segment` records what its recording's gaps cost: `n_frame_gaps`,
`n_frames_missing` and `n_barcodes_dropped` (Task 4), populated for real
through `Segment.populate()` rather than asserted by constructing a row by
hand -- the same discipline `test_detect_populate.py`'s own module docstring
states, and for the identical reason: a permanently empty `key_source`
passes every test that calls `make()` directly while proving nothing about
production.

Reuses `test_detect_populate.py`'s own `_build_stepped_session` helper for
the CLEAN case, following the precedent `test_consensus_populate.py`
already sets for importing another test module's helpers. Fixtures
themselves are NOT imported -- pytest fixture objects refuse direct
invocation outside pytest's own resolution machinery -- so this file
defines its own `daemon_module`/`stepped_session`, mirroring the shape
rather than the object, exactly as every other file sharing those names
already does.

The GAPPED cases are Task 6's `gapped_session` and `heavily_gapped_session`
fixtures, which live in `tests/schema/conftest.py` because they are session
builders rather than assertions -- see their own docstrings for how each
one's gaps are placed and why placement is the only thing that separates
them. Both tests below carried `xfail(strict=True)` until those fixtures
landed, so the debt showed up in every test report rather than going quiet;
the markers are gone now that the tests pass for real.
"""

from __future__ import annotations

import datetime

import pytest

from tests.schema.test_detect_populate import _build_stepped_session


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    """Activation only -- mirrors `test_consensus_populate.py`'s own fixture
    of the same name rather than `test_detect_populate.py`'s. `Segment.make`
    needs every schema activated (`AcquisitionSystem`, `pipeline.Session`,
    `ingest.Ingestion`, `timebase.SystemTimebase`) to insert into, but
    nothing in this file ever reaches `EyeDetection`/`EyeValidity`, so unlike
    `test_detect_populate.py`'s own `daemon_module` there is no detection
    paramset to register here.
    """
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


@pytest.fixture(scope="module")
def stepped_session(daemon_module, prefix, tmp_path_factory):
    """A clean recording -- no gap, no dropped barcode -- built from
    `test_detect_populate.py`'s own `_build_stepped_session`. Only the
    session key is returned: unlike that module's own `stepped_session`
    (which also hands back a fitted segment row and three planted onset
    times, for its own eye-detection tests), nothing here needs more than
    the key `Segment.populate` and `&` both take directly.
    """
    session_key, _segment, _onset_times = _build_stepped_session(
        tmp_path_factory,
        dirname="segmentstep", session_id="2027-08-19_01", subject="segstep1",
        session_datetime=datetime.datetime(2027, 8, 19, 9, 0), seed=819,
    )
    return session_key


def test_a_segment_records_the_gaps_its_recording_had(dj_conn, prefix, gapped_session):
    """A session that previously produced NOTHING now produces eye data, and
    its alignment rests on a trace with holes. A consumer must be able to
    separate those sessions from clean ones -- spec section 5, and the same
    'derived, not asserted' rule Phase 1c-5's TimingProvenance follows."""
    from wl_preproc.schema import core

    core.Segment.populate(gapped_session, suppress_errors=False)
    row = (core.Segment & gapped_session).fetch1()

    assert row["n_frame_gaps"] == 1
    assert row["n_frames_missing"] == 3
    # Exactly zero, not `>= 0`. The gap sits in the idle between two barcode
    # words, so it costs no word -- and every one of the clean session's
    # three expected values below is also 0, which leaves THIS test as the
    # only one in the suite whose three values differ from each other and so
    # the only one that can catch a transposition of the three columns in
    # `Segment.make`. `>= 0` is trivially true of an `int unsigned` and would
    # pin nothing: it passes just as happily on a transposition that put
    # `n_frames_missing`'s 3 in this column.
    assert row["n_barcodes_dropped"] == 0


def test_a_clean_segment_records_zero_for_all_three(dj_conn, prefix, stepped_session):
    """The columns must be zero rather than null on every recording that works
    today, so a query for 'sessions with gaps' cannot accidentally match one."""
    from wl_preproc.schema import core

    core.Segment.populate(stepped_session, suppress_errors=False)
    # `to_dicts()`, not `fetch(as_dict=True)`: the latter raises a real
    # DeprecationWarning under the installed DataJoint 2.3.3, and this
    # repository holds itself to zero warnings.
    for row in (core.Segment & stepped_session).to_dicts():
        assert (row["n_frame_gaps"], row["n_frames_missing"], row["n_barcodes_dropped"]) == (0, 0, 0)


def test_a_file_gapped_below_the_floor_names_the_gaps_as_the_reason(
    dj_conn, prefix, heavily_gapped_session
):
    """A file whose surviving barcodes fall below the alignment floor is
    already rejected. Without this it is rejected as `no_barcode`, which is
    true of the file and wrong about the cause -- the barcodes were there and
    the gaps removed them."""
    from wl_preproc.schema import core
    from wl_preproc.timebase import segments

    core.Segment.populate(heavily_gapped_session, suppress_errors=False)
    reason = (core.RejectedSegment & heavily_gapped_session).fetch1("reason")

    assert reason == segments.GAP_CORRUPTED
