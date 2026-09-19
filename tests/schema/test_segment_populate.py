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

The GAPPED case is Task 6's `gapped_session` fixture, which does not exist
yet; `test_a_segment_records_the_gaps_its_recording_had` below is marked
`xfail(strict=True)` until it lands, so that debt shows up in every test
report rather than going quiet.
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


@pytest.mark.xfail(reason="needs the dropped-frame fault, Task 6", strict=True)
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
    assert row["n_barcodes_dropped"] >= 0


def test_a_clean_segment_records_zero_for_all_three(dj_conn, prefix, stepped_session):
    """The columns must be zero rather than null on every recording that works
    today, so a query for 'sessions with gaps' cannot accidentally match one."""
    from wl_preproc.schema import core

    core.Segment.populate(stepped_session, suppress_errors=False)
    for row in (core.Segment & stepped_session).fetch(as_dict=True):
        assert (row["n_frame_gaps"], row["n_frames_missing"], row["n_barcodes_dropped"]) == (0, 0, 0)
