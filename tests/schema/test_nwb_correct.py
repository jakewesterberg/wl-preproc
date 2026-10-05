"""Subject corrections in built files, end to end on a synthetic session
through the daemon (design spec `2026-10-05-subject-corrections-design.md`).
Date, subject and seed checked unclaimed across `tests/` on 2026-10-05. In the
PAST, as `test_nwb_build.py`'s are: `nwbinspector` calls a future
`session_start_time` critical."""

from __future__ import annotations

import datetime

import pytest

_SESSION_DATETIME = datetime.datetime(2025, 7, 23, 9, 0)
_SUBJECT = "nwbfix1"
_BIRTH = datetime.date(2016, 3, 2)


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon
    from wl_preproc.schema import detect

    daemon.activate_all(prefix=prefix)
    detect.register_default_paramsets()
    return daemon


@pytest.fixture(scope="module")
def shares(tmp_path_factory):
    """A slow and a fast share for the module, each with its `nwb/` folder
    made once, as a person sets a share up."""
    from wl_preproc.nwb.publish import NWB_DIR, Share

    made = {}
    for tier, name in (("slow", "hdd"), ("fast", "nvme")):
        mount = tmp_path_factory.mktemp(f"nwbfix-{tier}")
        (mount / NWB_DIR).mkdir()
        made[tier] = Share(tier=tier, mount=mount, host="wl-nas", name=name)
    return made


@pytest.fixture(scope="module")
def published(daemon_module, prefix, shares, tmp_path_factory):
    """`stepped_session`'s construction, its trials in two runs, through the
    daemon; then wl.works' canonical over its runs, built and published to
    the slow share by the next pass. Yields `(session_key, activation_key,
    runs, nwb_root)`. Its files' records are deleted after the module: a
    placement on this module's shares would read as a missing copy to a later
    module's publishing pass, which looks on its own shares."""
    from tests.schema.test_detect_populate import TRIAL_DURATION_S, _build_stepped_session
    from tests.schema.test_nwb_build import _montage, _request
    from wl_preproc.contracts.events import TaskTypeCode
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core

    split = [{"task_type": TaskTypeCode.RF_MAP, "n_trials": n, "trial_duration_s": TRIAL_DURATION_S} for n in (3, 2)]
    session_key, _segment, _onsets = _build_stepped_session(
        tmp_path_factory, dirname="nwbfix", session_id="2025-07-23_01", subject=_SUBJECT,
        session_datetime=_SESSION_DATETIME, seed=723, recipe_update={"runs": True, "blocks": split},
    )
    daemon_module.run_once(prefix=prefix)
    runs = [{"run_number": row["run_number"], "start_s": row["run_start_time"], "end_s": row["run_stop_time"],
             "works_run_id": f"wr-fix-{row['run_number']}"}
            for row in (core.Run & session_key).to_dicts(order_by="run_number")]
    key = accept(_request(session_key, "nwbfix1-canonical", _montage(runs), runs), prefix=prefix)
    nwb_root = tmp_path_factory.mktemp("nwbfix-root")
    report = daemon_module.run_once(prefix=prefix, nwb_root=nwb_root, nwb_slow=shares["slow"],
                                    nwb_fast=shares["fast"])
    assert report["nwb_published"] >= 1, report["errors"]
    yield session_key, key, runs, nwb_root
    from wl_preproc.schema import nwb as nwb_schema

    (nwb_schema.NwbFile & {"subject": _SUBJECT}).delete(prompt=False)


def _set_birth(date_of_birth):
    from wl_preproc.schema import pipeline

    pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": date_of_birth})


def _is_stale(key) -> bool:
    from wl_preproc.nwb.correct import stale_files
    from wl_preproc.nwb.publish import activation_tuple

    return activation_tuple(key) in {activation_tuple(stale) for stale in stale_files()}


def test_corrected_is_a_kind_of_change(dj_conn, prefix):
    from wl_preproc.schema import nwb as nwb_schema

    nwb_schema.activate(prefix=prefix)
    assert "'corrected'" in nwb_schema.NwbChange.heading.attributes["kind"].type


def test_a_written_file_is_stale_once_its_subjects_details_change(published):
    """Spec section 2: by value, as `invalid` files are found."""
    _session_key, key, _runs, _root = published
    assert not _is_stale(key)
    _set_birth(datetime.date(2016, 3, 1))
    try:
        assert _is_stale(key)
    finally:
        _set_birth(_BIRTH)
    assert not _is_stale(key)


def test_a_derivative_and_a_superseded_file_are_stale_too(published, daemon_module, prefix, shares):
    """Spec section 2: every written file of the animal, whatever its role
    or whether a replacement superseded it."""
    from tests.schema.test_nwb_build import _montage, _request
    from wl_preproc.responder.jobs import accept

    session_key, key, runs, root = published
    derivative = accept(_request(session_key, "nwbfix1-derivative", _montage(runs), runs,
                                 run_numbers=[runs[-1]["run_number"]]), prefix=prefix)
    replacement = _request(session_key, "nwbfix1-replacement", _montage(runs), runs)
    replacement = replacement.model_copy(update={"selection": {
        **replacement.selection, "role": "canonical", "supersedes_activation_id": key["activation_id"]}})
    successor = accept(replacement, prefix=prefix)
    report = daemon_module.run_once(prefix=prefix, nwb_root=root, nwb_slow=shares["slow"], nwb_fast=shares["fast"])
    assert not [error for error in report["errors"] if "NwbFile" in error], report["errors"]
    _set_birth(datetime.date(2016, 3, 1))
    try:
        assert all(_is_stale(each) for each in (key, derivative, successor))
    finally:
        _set_birth(_BIRTH)
