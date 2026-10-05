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


# -- Correcting one file (spec section 3), and what stops it (section 5). Each
# test leaves the subject's details as the fixture stated them and its file
# holding them again, so the next one starts where a real pass would.

_DAY = datetime.date(2026, 11, 2)
_ANNOTATION = "analysis/nwbfix_note/text"


def _live(key, shares):
    from wl_preproc.nwb.publish import current_placement

    placement = current_placement(key)
    return shares[placement["tier"]].local(placement["path"])


def _annotate(path):
    import h5py

    with h5py.File(path, "r+") as handle:
        if _ANNOTATION not in handle:
            handle.create_dataset(_ANNOTATION, data="seen by JW")


def _restore(key, shares):
    """The fixture's details back, and the file corrected back to them."""
    from wl_preproc.nwb.correct import correct

    _set_birth(_BIRTH)
    correct(key, shares, _DAY)


def test_a_published_file_is_corrected_where_it_is_with_its_annotations(published, shares, prefix):
    """Spec sections 3 and 4: only the subject's datasets change, the lab's
    annotation travels in the copy, and the records, the description beside
    the file and GET /nwb all say what the file now holds."""
    import json

    import h5py

    from wl_preproc.nwb.correct import correct, file_subject, stale_files
    from wl_preproc.nwb.publish import current_placement, description_path, mismatches
    from wl_preproc.responder.nwb import list_files
    from wl_preproc.schema import nwb as nwb_schema

    session_key, key, _runs, _root = published
    live = _live(key, shares)
    _annotate(live)
    cursor = list_files(None, prefix=prefix)["cursor"]
    changes = len(nwb_schema.NwbChange & key & {"kind": "corrected"})
    _set_birth(datetime.date(2016, 3, 1))
    try:
        note = correct(key, shares, _DAY)
        assert note == "Corrected 2026-11-02: date of birth 2016-03-02 → 2016-03-01."
        assert file_subject(live)["date_of_birth"] == datetime.date(2016, 3, 1)
        with h5py.File(live, "r") as handle:
            assert handle[_ANNOTATION][()] == b"seen by JW"
            assert handle["general/subject/description"][()].decode().endswith("\n" + note)
        assert not live.with_name(live.name + ".partial").exists()
        row = (nwb_schema.NwbFile & key).fetch1()
        assert row["description"]["subject"]["date_of_birth"] == "2016-03-01"
        assert row["description"]["subject"]["age_days"] == (_SESSION_DATETIME.date() - datetime.date(2016, 3, 1)).days
        assert row["description"]["notes"][-1] == note
        recorded = (nwb_schema.NwbFile.Dataset & key).to_dicts()
        assert mismatches(live, recorded) == []
        assert {d["dataset_path"]: d["sha256"] for d in row["description"]["checksums"]["datasets"]} == {
            d["dataset_path"]: d["sha256"] for d in recorded}
        assert json.loads(description_path(live).read_text()) == row["description"]
        assert len(nwb_schema.NwbChange & key & {"kind": "corrected"}) == changes + 1
        # The placement recorded again with the correction: same share and
        # path, the size as it now is (spec section 3, step 5).
        assert current_placement(key)["kind"] == "corrected"
        assert current_placement(key)["n_bytes"] == live.stat().st_size
        (listed,) = [item for item in list_files(cursor, prefix=prefix)["files"]
                     if item["identifier"] == row["nwb_identifier"]]
        assert listed["description"]["subject"]["date_of_birth"] == "2016-03-01"
        assert key not in stale_files()
    finally:
        _restore(key, shares)
    assert file_subject(live)["date_of_birth"] == _BIRTH


def test_a_file_not_yet_published_is_corrected_in_scratch(published, daemon_module, prefix, tmp_path_factory):
    """Spec section 2: a written file waiting to publish is corrected where
    it is, and its recorded size follows."""
    from tests.schema.test_nwb_build import _montage, _request
    from wl_preproc.nwb.correct import correct, file_subject
    from wl_preproc.nwb.publish import current_placement
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, runs, root = published
    waiting = accept(_request(session_key, "nwbfix1-waiting", _montage(runs), runs,
                              run_numbers=[runs[0]["run_number"]]), prefix=prefix)
    daemon_module.run_once(prefix=prefix, nwb_root=root)
    path = __import__("pathlib").Path((nwb_schema.NwbFile & waiting).fetch1("path"))
    assert current_placement(waiting) is None and path.exists()
    _set_birth(datetime.date(2016, 3, 1))
    try:
        assert correct(waiting, {}, _DAY) is not None
        assert file_subject(path)["date_of_birth"] == datetime.date(2016, 3, 1)
        assert (nwb_schema.NwbFile & waiting).fetch1("n_bytes") == path.stat().st_size
        assert current_placement(waiting) is None
    finally:
        _restore(waiting, {})


def test_a_file_already_holding_the_details_gets_only_its_records(published, shares):
    """Spec section 5: a pass after a crash that followed the swap finds the
    file corrected and its records not; it records them, and does not write
    the file again. Here the same correction is made twice on one day -- A to
    B, back, then A to B once more by the pass that crashed -- so the crashed
    pass's note has the very text of one already recorded: it is recorded
    again, because the description follows the file's correction lines by
    count, not by text (spec amendment 3)."""
    import h5py

    from wl_preproc.nwb.correct import correct, patch_subject
    from wl_preproc.schema import nwb as nwb_schema

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    new = {"species": "Macaca mulatta", "sex": "F", "date_of_birth": datetime.date(2016, 3, 1)}
    _set_birth(new["date_of_birth"])
    try:
        first = correct(key, shares, _DAY)
        _set_birth(_BIRTH)
        correct(key, shares, _DAY)
        _set_birth(new["date_of_birth"])
        again = patch_subject(live, new, _DAY)  # the pass that crashed after its swap
        assert again == first
        stamp = live.stat().st_mtime_ns
        assert correct(key, shares, _DAY) is None
        assert live.stat().st_mtime_ns == stamp
        notes = (nwb_schema.NwbFile & key).fetch1("description")["notes"]
        with h5py.File(live, "r") as handle:
            in_file = [line for line in handle["general/subject/description"][()].decode().split("\n")
                       if line.startswith("Corrected ")]
        assert [line for line in notes if line.startswith("Corrected ")] == in_file
        assert notes[-3:] == [first, in_file[-2], again] and notes.count(first) == in_file.count(first) >= 2
    finally:
        _restore(key, shares)


def test_a_file_written_to_during_the_copy_is_left_and_tried_again(published, shares, monkeypatch):
    """Spec section 5: someone wrote to the file while it was copied, so the
    copy is dropped and nothing recorded."""
    from wl_preproc.nwb import correct as correct_module
    from wl_preproc.schema import nwb as nwb_schema

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    real = correct_module.patch_subject

    def written_meanwhile(path, new, day):
        with open(live, "ab") as handle:  # what a writer does to the live file meanwhile
            handle.write(b"\0")
        return real(path, new, day)

    before = (nwb_schema.NwbFile & key).fetch1("description")
    monkeypatch.setattr(correct_module, "patch_subject", written_meanwhile)
    _set_birth(datetime.date(2016, 3, 1))
    try:
        with pytest.raises(correct_module.ChangedWhileCorrecting):
            correct_module.correct(key, shares, _DAY)
        assert correct_module.file_subject(live)["date_of_birth"] == _BIRTH
        assert (nwb_schema.NwbFile & key).fetch1("description") == before
        assert not live.with_name(live.name + ".partial").exists()
    finally:
        monkeypatch.undo()
        with open(live, "r+b") as handle:  # the byte appended above, removed
            handle.truncate(live.stat().st_size - 1)
        _set_birth(_BIRTH)


def test_changed_written_once_data_stops_the_correction(published, shares):
    """Spec section 5: data that must never change did; a person looks."""
    import h5py

    from wl_preproc.nwb.correct import correct, file_subject
    from wl_preproc.nwb.publish import ChangedData

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    with h5py.File(live, "r+") as handle:
        original = handle["/intervals/trials/start_time"][0]
        handle["/intervals/trials/start_time"][0] = original + 1.0
    _set_birth(datetime.date(2016, 3, 1))
    try:
        with pytest.raises(ChangedData, match="/intervals/trials/start_time"):
            correct(key, shares, _DAY)
        assert file_subject(live)["date_of_birth"] == _BIRTH
    finally:
        with h5py.File(live, "r+") as handle:
            handle["/intervals/trials/start_time"][0] = original
        _set_birth(_BIRTH)


def test_a_date_of_birth_now_unknown_is_not_written(published, shares):
    """Spec section 5: a file without a date of birth fails validation, so
    no copy is made and the file keeps the details it has."""
    from wl_preproc.ingest.landing import SUBJECT_BIRTH_DATE_UNKNOWN
    from wl_preproc.nwb.correct import CorrectionRefused, correct

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    _set_birth(SUBJECT_BIRTH_DATE_UNKNOWN)
    try:
        with pytest.raises(CorrectionRefused, match="date of birth is now unknown"):
            correct(key, shares, _DAY)
        assert not live.with_name(live.name + ".partial").exists()
    finally:
        _set_birth(_BIRTH)


def test_a_critical_finding_in_the_corrected_copy_keeps_the_old_file(published, shares, monkeypatch):
    from wl_preproc.nwb import correct as correct_module

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    monkeypatch.setattr(correct_module, "n_critical", lambda findings: 1)
    _set_birth(datetime.date(2016, 3, 1))
    try:
        with pytest.raises(correct_module.CorrectionRefused, match="critical"):
            correct_module.correct(key, shares, _DAY)
        assert correct_module.file_subject(live)["date_of_birth"] == _BIRTH
    finally:
        _set_birth(_BIRTH)


def test_an_unreachable_share_fails_the_file(published, tmp_path_factory):
    from wl_preproc.nwb.correct import CorrectionRefused, correct
    from wl_preproc.nwb.publish import Share

    _session_key, key, _runs, _root = published
    gone = Share(tier="slow", mount=tmp_path_factory.mktemp("nwbfix-gone") / "gone", host="wl-nas", name="hdd")
    _set_birth(datetime.date(2016, 3, 1))
    try:
        with pytest.raises(CorrectionRefused, match="not reachable"):
            correct(key, {"slow": gone}, _DAY)
    finally:
        _set_birth(_BIRTH)


# -- The daemon's stage (spec section 2), and what one share's history means
# for its leftovers once a file is corrected.

def _written_of_subject() -> int:
    from wl_preproc.schema import nwb as nwb_schema

    return len(nwb_schema.NwbFile & {"subject": _SUBJECT, "status": "written"})


def test_a_daemon_pass_corrects_every_stale_file_and_says_how_many(published, daemon_module, prefix, shares):
    from wl_preproc.nwb.correct import file_subject, stale_files

    _session_key, key, _runs, root = published
    passes = {"nwb_root": root, "nwb_slow": shares["slow"], "nwb_fast": shares["fast"]}
    daemon_module.run_once(prefix=prefix, **passes)  # anything still waiting is published first
    written = _written_of_subject()  # with the earlier tests, its replacement and derivatives too
    _set_birth(datetime.date(2016, 3, 1))
    try:
        report = daemon_module.run_once(prefix=prefix, **passes)
        assert report["nwb_corrected"] == written, report["errors"]
        assert not [error for error in report["errors"] if "NwbCorrection" in error]
        assert not [stale for stale in stale_files() if stale["subject"] == _SUBJECT]
        assert file_subject(_live(key, shares))["date_of_birth"] == datetime.date(2016, 3, 1)
    finally:
        _set_birth(_BIRTH)
        assert daemon_module.run_once(prefix=prefix, **passes)["nwb_corrected"] == written
    assert daemon_module.run_once(prefix=prefix, **passes)["nwb_corrected"] == 0


def test_one_failing_file_does_not_stop_the_others(published, shares, monkeypatch):
    from wl_preproc.nwb import correct as correct_module

    _session_key, key, _runs, _root = published
    real = correct_module.correct

    def failing(each, shares_, day):
        if each == key:
            raise OSError("the share refused the copy")
        return real(each, shares_, day)

    monkeypatch.setattr(correct_module, "correct", failing)
    _set_birth(datetime.date(2016, 3, 1))
    try:
        corrected, errors = correct_module.run_corrections(shares["slow"], shares["fast"])
        assert corrected == _written_of_subject() - 1
        assert [error for error in errors if "the share refused the copy" in error and error.startswith(
            "NwbCorrection ")]
    finally:
        monkeypatch.undo()
        _set_birth(_BIRTH)
        correct_module.run_corrections(shares["slow"], shares["fast"])


def test_a_file_not_yet_published_in_a_freed_session_waits(published, daemon_module, prefix, shares):
    """As publishing does, the stage leaves a file still in scratch alone
    while its session is freed (spec amendment 4); once it is not, the file
    is corrected."""
    from pathlib import Path

    from tests.schema.test_nwb_build import _montage, _request
    from wl_preproc.nwb.correct import file_subject, run_corrections
    from wl_preproc.nwb.publish import current_placement
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, runs, root = published
    waiting = accept(_request(session_key, "nwbfix1-freed", _montage(runs), runs,
                              run_numbers=[run["run_number"] for run in runs]), prefix=prefix)
    daemon_module.run_once(prefix=prefix, nwb_root=root)
    path = Path((nwb_schema.NwbFile & waiting).fetch1("path"))
    assert current_placement(waiting) is None
    session = {"subject": session_key["subject"], "session_datetime": session_key["session_datetime"]}
    _set_birth(datetime.date(2016, 3, 1))
    try:
        run_corrections(shares["slow"], shares["fast"], freed=[session])
        assert file_subject(path)["date_of_birth"] == _BIRTH and _is_stale(waiting)
        run_corrections(shares["slow"], shares["fast"])
        assert file_subject(path)["date_of_birth"] == datetime.date(2016, 3, 1)
    finally:
        _set_birth(_BIRTH)
        run_corrections(shares["slow"], shares["fast"])


def test_a_leftover_written_after_its_move_is_kept_though_the_file_was_corrected_since(
        published, shares, prefix, monkeypatch):
    """A move whose old copy could not be deleted leaves it for the sweep,
    which deletes it only if nobody wrote to it after the move. A correction
    records the placement again, later; the sweep still dates the move by
    the move, not by the correction. On the fast share, a correction also
    respects its headroom (spec section 3, step 1)."""
    import os

    from tests.schema.test_nwb_build import _set_active
    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.nwb.correct import CorrectionRefused, correct
    from wl_preproc.nwb.publish import Share

    _session_key, key, _runs, _root = published
    old = _live(key, shares)
    real = publish_module._remove_old_copy

    def in_use(path):
        raise PermissionError(f"{path} is in use")

    monkeypatch.setattr(publish_module, "_remove_old_copy", in_use)
    _set_active(key)
    try:
        publish_module.run_placement(shares["slow"], shares["fast"])
        monkeypatch.setattr(publish_module, "_remove_old_copy", real)
        moved = publish_module.current_placement(key)
        assert moved["tier"] == "fast" and old.exists()
        written_at = moved["changed_at"].replace(tzinfo=datetime.timezone.utc).timestamp() + 0.001
        os.utime(old, (written_at, written_at))  # someone wrote to the old copy after the move
        _set_birth(datetime.date(2016, 3, 1))
        full = Share(tier="fast", mount=shares["fast"].mount, host="wl-nas", name="nvme", headroom_bytes=10**18)
        with pytest.raises(CorrectionRefused, match="headroom"):
            correct(key, {"slow": shares["slow"], "fast": full}, _DAY)
        correct(key, shares, _DAY)
        _moved, errors = publish_module.run_placement(shares["slow"], shares["fast"])
        assert old.exists()
        assert [error for error in errors if "written to after the file was moved from it" in error]
    finally:
        monkeypatch.setattr(publish_module, "_remove_old_copy", real)
        _set_birth(_BIRTH)
        correct(key, shares, _DAY)
        old.unlink(missing_ok=True)
        publish_module.description_path(old).unlink(missing_ok=True)
        _set_active()
        publish_module.run_placement(shares["slow"], shares["fast"])
    assert publish_module.current_placement(key)["tier"] == "slow"


# -- The final review's fixes.

def test_only_the_four_subject_datasets_are_spared_or_taken_into_the_records(published, shares):
    """Final review I2: a correction rewrites four datasets and no others. A
    changed `subject_id`, which the build wrote once, stops it; a dataset the
    lab appended under the subject is not taken into the records, so the lab
    may change it again without a move refusing the file."""
    import h5py

    from wl_preproc.nwb.correct import correct
    from wl_preproc.nwb.publish import ChangedData, mismatches
    from wl_preproc.schema import nwb as nwb_schema

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    with h5py.File(live, "r+") as handle:
        original = handle["general/subject/subject_id"][()].decode()
        handle["general/subject/subject_id"][()] = "someone-else"
    _set_birth(datetime.date(2016, 3, 1))
    try:
        with pytest.raises(ChangedData, match="subject_id"):
            correct(key, shares, _DAY)
    finally:
        with h5py.File(live, "r+") as handle:
            handle["general/subject/subject_id"][()] = original
        _set_birth(_BIRTH)
    with h5py.File(live, "r+") as handle:
        handle["general/subject"].create_dataset("weight", data="8.1 kg")
    _set_birth(datetime.date(2016, 3, 1))
    try:
        correct(key, shares, _DAY)
        recorded = (nwb_schema.NwbFile.Dataset & key).to_dicts()
        described = (nwb_schema.NwbFile & key).fetch1("description")["checksums"]["datasets"]
        assert "/general/subject/weight" not in {row["dataset_path"] for row in recorded + described}
        with h5py.File(live, "r+") as handle:
            handle["general/subject/weight"][()] = "8.4 kg"
        assert mismatches(live, recorded) == []
    finally:
        _restore(key, shares)
        with h5py.File(live, "r+") as handle:
            del handle["general/subject/weight"]


def test_a_description_file_that_could_not_be_written_is_written_next_pass(published, shares, monkeypatch):
    """Final review I3: the description beside a published file is written
    before the records, so a failure leaves the file stale and the next pass
    writes it (spec section 5: retried next pass)."""
    import json

    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.nwb.correct import run_corrections

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    real = publish_module.write_description

    def failing(path, description):
        raise OSError("[Errno 5] Input/output error")

    monkeypatch.setattr(publish_module, "write_description", failing)
    _set_birth(datetime.date(2016, 3, 1))
    try:
        _corrected, errors = run_corrections(shares["slow"], shares["fast"])
        assert [error for error in errors if "Input/output error" in error]
        monkeypatch.setattr(publish_module, "write_description", real)
        run_corrections(shares["slow"], shares["fast"])
        described = json.loads(publish_module.description_path(live).read_text())
        assert described["subject"]["date_of_birth"] == "2016-03-01"
    finally:
        monkeypatch.undo()
        _set_birth(_BIRTH)
        run_corrections(shares["slow"], shares["fast"])


def test_a_write_while_the_copy_is_flushed_stops_the_swap(published, shares, monkeypatch):
    """Final review M2: the copy is flushed before the live file's stamp is
    checked, so a write landing during the flush is seen and the copy
    dropped, rather than swapped over what was written."""
    import os

    from wl_preproc.nwb import correct as correct_module

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    real = os.fsync

    def written_meanwhile(descriptor):
        real(descriptor)
        with open(live, "ab") as handle:
            handle.write(b"\0")

    monkeypatch.setattr(correct_module.os, "fsync", written_meanwhile)
    _set_birth(datetime.date(2016, 3, 1))
    try:
        with pytest.raises(correct_module.ChangedWhileCorrecting):
            correct_module.correct(key, shares, _DAY)
    finally:
        monkeypatch.undo()
        with open(live, "r+b") as handle:  # the byte appended above, removed
            handle.truncate(live.stat().st_size - 1)
        _set_birth(_BIRTH)
    assert correct_module.file_subject(live)["date_of_birth"] == _BIRTH


def test_a_database_without_the_corrected_kind_corrects_nothing_and_says_how_to_fix_it(
        published, shares, monkeypatch):
    """Final review M5: a development database declared before `corrected`
    would fail each correction's records after its swap, every pass. The
    stage checks the column first, swaps nothing, and names the statement."""
    from wl_preproc.nwb import correct as correct_module

    _session_key, key, _runs, _root = published
    assert "'corrected'" in correct_module._change_kinds()
    monkeypatch.setattr(correct_module, "_change_kinds",
                        lambda: "enum('built','published','moved','superseded')")
    _set_birth(datetime.date(2016, 3, 1))
    try:
        corrected, errors = correct_module.run_corrections(shares["slow"], shares["fast"])
        assert corrected == 0
        (error,) = [error for error in errors if "ALTER TABLE" in error and "'corrected'" in error]
        assert correct_module.file_subject(_live(key, shares))["date_of_birth"] == _BIRTH
    finally:
        _set_birth(_BIRTH)
    # The statement it names is the column as this branch declares it: on a
    # database that already has it, running it changes nothing.
    import datajoint as dj

    from wl_preproc.schema import nwb as nwb_schema

    show = f"SHOW FULL COLUMNS FROM {nwb_schema.NwbChange.full_table_name} LIKE 'kind'"
    before = dj.conn().query(show).fetchone()
    dj.conn().query(error[error.index("ALTER TABLE"):])
    assert dj.conn().query(show).fetchone() == before


def test_a_rebuilt_row_takes_over_its_corrected_published_file(published, shares, daemon_module, prefix):
    """Final review I1(a): `wlpp nwb build`'s remedy -- delete the row and
    rebuild -- after a correction. The rebuild carries no correction note, so
    the published file's subject differs from it; the file is still this
    activation's, annotations and all, and is adopted, its subject records
    taken from it. The montage's current canonical: a superseded activation
    is never rebuilt (canonical-lifecycle spec section 4), and an earlier
    test here replaced the fixture's."""
    import h5py

    from wl_preproc.nwb.correct import correct, run_corrections
    from wl_preproc.nwb.publish import current_placement
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema.request import current_canonical

    session_key, _key, _runs, root = published
    montage = {"subject": session_key["subject"], "session_datetime": session_key["session_datetime"], "montage_id": 0}
    key = {k: current_canonical(montage)[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")}
    live = _live(key, shares)
    _annotate(live)
    _set_birth(datetime.date(2016, 3, 1))
    try:
        correct(key, shares, _DAY)
        (nwb_schema.NwbFile & key).delete(prompt=False)
        report = daemon_module.run_once(prefix=prefix, nwb_root=root, nwb_slow=shares["slow"],
                                        nwb_fast=shares["fast"])
        assert not [error for error in report["errors"] if "not overwritten" in error], report["errors"]
        assert current_placement(key) is not None and _live(key, shares) == live
        with h5py.File(live, "r") as handle:
            assert handle[_ANNOTATION][()] == b"seen by JW"
        notes = (nwb_schema.NwbFile & key).fetch1("description")["notes"]
        assert notes and notes[-1].startswith("Corrected ") and not _is_stale(key)
    finally:
        # The pass corrected every file of the animal, so all go back.
        _set_birth(_BIRTH)
        run_corrections(shares["slow"], shares["fast"])


def test_a_file_left_by_a_failed_publish_is_adopted_after_its_scratch_copy_was_corrected(published, shares):
    """Final review I1(b): a publish renamed the file onto its share and
    failed before recording it; the correction stage then corrected the
    scratch copy and its records. The next publishing pass adopts the shared
    file all the same -- its subject records taken from it -- and the next
    correction brings it up to the current details."""
    import shutil
    from pathlib import Path

    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.nwb.correct import file_subject, run_corrections
    from wl_preproc.schema import nwb as nwb_schema

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    assert file_subject(live)["date_of_birth"] == _BIRTH and not _is_stale(key)
    scratch = Path((nwb_schema.NwbFile & key).fetch1("path"))
    scratch.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(live, scratch)
    # What a publish that failed after its rename leaves: the file on the
    # share, its scratch copy, and no placement recorded.
    (nwb_schema.NwbChange & key & "kind IN ('published', 'moved', 'corrected')").delete(prompt=False)
    assert publish_module.current_placement(key) is None
    _set_birth(datetime.date(2016, 3, 1))
    try:
        run_corrections(shares["slow"], shares["fast"])
        assert file_subject(scratch)["date_of_birth"] == datetime.date(2016, 3, 1)
        _published, errors = publish_module.run_publish(shares["slow"], shares["fast"])
        assert not [error for error in errors if "not overwritten" in error], errors
        assert publish_module.current_placement(key) is not None and _live(key, shares) == live
        assert _is_stale(key)
        run_corrections(shares["slow"], shares["fast"])
        assert file_subject(live)["date_of_birth"] == datetime.date(2016, 3, 1) and not _is_stale(key)
    finally:
        _restore(key, shares)
