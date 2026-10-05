"""Patching a file's subject (design spec
`2026-10-05-subject-corrections-design.md` section 3, step 2), on small files
written here: no database."""

from __future__ import annotations

import datetime

import h5py
import pytest

_DAY = datetime.date(2026, 11, 2)
_STATED = "As wl.works' job request stated it (design spec section 9)."


def _file(path, *, species="Macaca mulatta", sex="F", date_of_birth=datetime.date(2016, 3, 2)):
    """A file with a subject, as `nwb/session.py` writes one, and an
    annotation the lab appended afterwards."""
    from pynwb import NWBFile, NWBHDF5IO
    from pynwb.file import Subject

    start = datetime.datetime(2025, 7, 20, 9, tzinfo=datetime.timezone.utc)
    born = datetime.datetime.combine(date_of_birth, datetime.time(), tzinfo=datetime.timezone.utc)
    nwb = NWBFile(session_description="x", identifier="monk01.2025-07-20_01.montage-0.activation-0",
                  session_start_time=start,
                  subject=Subject(subject_id="monk01", species=species, sex=sex, date_of_birth=born,
                                  description=_STATED))
    with NWBHDF5IO(str(path), "w") as handle:
        handle.write(nwb)
    with h5py.File(path, "r+") as handle:
        handle.require_group("analysis").create_group("lab_note").create_dataset("text", data="seen by JW")
    return path


def _details(species="Macaca mulatta", sex="F", date_of_birth=datetime.date(2016, 3, 2)):
    return {"species": species, "sex": sex, "date_of_birth": date_of_birth}


def test_the_note_names_each_detail_that_changed():
    from wl_preproc.nwb.correct import correction_note

    assert correction_note(_details(), _details(), _DAY) is None
    assert correction_note(_details(), _details(date_of_birth=datetime.date(2016, 3, 1)), _DAY) == (
        "Corrected 2026-11-02: date of birth 2016-03-02 → 2016-03-01.")
    assert correction_note(_details(species=None, sex="U"), _details(sex="M"), _DAY) == (
        "Corrected 2026-11-02: species unknown → Macaca mulatta; sex U → M.")


def test_the_patch_changes_only_the_subjects_datasets_and_pynwb_reads_them(tmp_path):
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.checksums import dataset_checksums
    from wl_preproc.nwb.correct import file_subject, patch_subject

    path = _file(tmp_path / "a.nwb")
    before = {row["dataset_path"]: row["sha256"] for row in dataset_checksums(path)}
    note = patch_subject(path, _details(sex="M", date_of_birth=datetime.date(2016, 3, 1)), _DAY)
    assert note == "Corrected 2026-11-02: sex F → M; date of birth 2016-03-02 → 2016-03-01."
    after = {row["dataset_path"]: row["sha256"] for row in dataset_checksums(path)}
    assert sorted(name for name in before if before[name] != after[name]) == [
        "/general/subject/date_of_birth", "/general/subject/description", "/general/subject/sex"]
    assert file_subject(path) == _details(sex="M", date_of_birth=datetime.date(2016, 3, 1))
    with NWBHDF5IO(str(path), "r") as handle:
        subject = handle.read().subject
        assert (subject.sex, subject.date_of_birth.date()) == ("M", datetime.date(2016, 3, 1))
        assert subject.description == f"{_STATED}\n{note}"
    with h5py.File(path, "r") as handle:
        assert handle["analysis/lab_note/text"][()] == b"seen by JW"


def test_a_second_correction_adds_a_second_line(tmp_path):
    from wl_preproc.nwb.correct import patch_subject

    path = _file(tmp_path / "a.nwb")
    first = patch_subject(path, _details(date_of_birth=datetime.date(2016, 3, 1)), _DAY)
    second = patch_subject(path, _details(date_of_birth=datetime.date(2016, 2, 29)), datetime.date(2026, 12, 1))
    with h5py.File(path, "r") as handle:
        assert handle["general/subject/description"][()].decode() == f"{_STATED}\n{first}\n{second}"
    assert second == "Corrected 2026-12-01: date of birth 2016-03-01 → 2016-02-29."


def test_a_file_that_already_holds_the_details_is_not_opened_for_writing(tmp_path):
    """What a pass finds after a crash that followed the swap (spec
    section 5): nothing to patch, and no empty note."""
    from wl_preproc.nwb.correct import patch_subject

    path = _file(tmp_path / "a.nwb")
    stamp = path.stat().st_mtime_ns
    assert patch_subject(path, _details(), _DAY) is None
    assert path.stat().st_mtime_ns == stamp


@pytest.mark.parametrize("before, after", [(None, "Macaca mulatta"), ("Macaca mulatta", None)])
def test_a_species_is_written_or_removed_where_the_file_had_none_or_one(tmp_path, before, after):
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.correct import file_subject, patch_subject

    path = _file(tmp_path / "a.nwb", species=before)
    patch_subject(path, _details(species=after), _DAY)
    assert file_subject(path)["species"] == after
    with NWBHDF5IO(str(path), "r") as handle:
        assert handle.read().subject.species == after
