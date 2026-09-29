"""Trimming, checksums, validation and the atomic write (design spec
`2026-09-28-nwb-builder-design.md` sections 5-8)."""

from __future__ import annotations

import datetime

import numpy as np
import pytest

from tests.nwb.test_writers import SESSION, T0


def test_the_block_set_keeps_what_starts_inside_it():
    from wl_preproc.nwb.trim import BlockSet

    blocks = BlockSet.of([{"start_s": 30.0, "end_s": 60.0}, {"start_s": 0.0, "end_s": 10.0}])
    assert blocks.contains([0.0, 9.999, 10.0, 29.0, 30.0, 59.9, 60.0]).tolist() == [
        True, True, False, False, True, True, False]
    assert blocks.contains_instant([10.0, 60.0, 60.001]).tolist() == [True, True, False]
    assert blocks.clip(5.0, 35.0) == [(5.0, 10.0), (30.0, 35.0)]
    assert blocks.clip(12.0, 20.0) == []


def test_touching_blocks_do_not_split_a_stretch_that_crosses_them():
    """A blink across the boundary of two adjacent blocks is one blink: one
    row in the file, not one per block (a stretch is clipped to the block
    set's edges, and two touching blocks share no edge a stretch can stop
    at)."""
    from wl_preproc.nwb.trim import BlockSet

    blocks = BlockSet.of([{"start_s": 10.0, "end_s": 20.0}, {"start_s": 0.0, "end_s": 10.0},
                          {"start_s": 30.0, "end_s": 40.0}])
    assert blocks.clip(8.0, 12.0) == [(8.0, 12.0)]
    assert blocks.clip(18.0, 35.0) == [(18.0, 20.0), (30.0, 35.0)]
    assert blocks.contains([9.999, 10.0, 19.999, 20.0]).tolist() == [True, True, True, False]
    assert blocks.contains_instant([10.0, 20.0]).tolist() == [True, True]

def _file(tmp_path, name, values):
    from wl_preproc.nwb.intervals import add_task_events
    from wl_preproc.nwb.session import new_file
    from wl_preproc.nwb.write import write_atomically

    nwb = new_file(SESSION)
    add_task_events(nwb, [{"time_s": t, "event_type": "TRIAL_START"} for t in values])
    path = tmp_path / name
    write_atomically(nwb, path)
    return path


def test_checksums_are_of_contents_and_stable_across_rebuilds(tmp_path):
    from wl_preproc.nwb.checksums import dataset_checksums

    first = dataset_checksums(_file(tmp_path, "a.nwb", [1.0, 2.0]))
    again = dataset_checksums(_file(tmp_path, "b.nwb", [1.0, 2.0]))
    changed = dataset_checksums(_file(tmp_path, "c.nwb", [1.0, 2.5]))

    assert first == again
    by_path = {row["dataset_path"]: row for row in first}
    assert "/intervals/task_events/start_time" in by_path
    assert not any(row["dataset_path"].startswith(("/specifications", "/file_create_date")) for row in first)
    changed_paths = {row["dataset_path"] for row in changed} & set(by_path)
    assert [p for p in sorted(changed_paths) if {r["dataset_path"]: r for r in changed}[p]["blake3"] != by_path[p]["blake3"]] == [
        "/intervals/task_events/start_time", "/intervals/task_events/stop_time"]


def test_a_ragged_column_is_recorded_as_a_pair(tmp_path):
    from pynwb.file import Subject  # noqa: F401 -- pynwb's experimenter is a ragged-free list; build one below
    from hdmf.common import VectorData, VectorIndex
    from pynwb.core import DynamicTable

    from wl_preproc.nwb.checksums import dataset_checksums
    from wl_preproc.nwb.session import new_file
    from wl_preproc.nwb.write import write_atomically

    nwb = new_file(SESSION)
    values = VectorData(name="spikes", description="d", data=[1.0, 2.0, 3.0])
    index = VectorIndex(name="spikes_index", data=[2, 3], target=values)
    module = nwb.create_processing_module("scratch", "d")
    module.add(DynamicTable(name="ragged", description="d", columns=[values, index]))
    path = tmp_path / "r.nwb"
    write_atomically(nwb, path)

    rows = {row["dataset_path"]: row for row in dataset_checksums(path)}
    assert rows["/processing/scratch/ragged/spikes"]["paired_with"] == "/processing/scratch/ragged/spikes_index"
    assert rows["/processing/scratch/ragged/spikes_index"]["paired_with"] == "/processing/scratch/ragged/spikes"


def test_a_subject_without_a_date_of_birth_is_a_critical_finding(tmp_path):
    """Measured 2026-09-28: `nwbinspector`'s `check_subject_age` is CRITICAL
    under its default configuration, so a file built without wl.works'
    subject details is `invalid` (design spec sections 8 and 9)."""
    from wl_preproc.nwb.session import new_file
    from wl_preproc.nwb.validate import inspect_file, n_critical
    from wl_preproc.nwb.write import write_atomically

    for dob, expected in ((datetime.date(2016, 3, 2), 0), (None, 1)):
        nwb = new_file({**SESSION, "subject": {**SESSION["subject"], "date_of_birth": dob}})
        path = tmp_path / f"{dob}.nwb"
        write_atomically(nwb, path)
        findings = inspect_file(path)
        assert n_critical(findings) == expected, findings
        if expected:
            assert [f["check"] for f in findings if f["importance"] == "CRITICAL"] == ["check_subject_age"]


def test_a_failed_write_leaves_no_file_under_the_final_name(tmp_path, monkeypatch):
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.session import new_file
    from wl_preproc.nwb.write import write_atomically

    def broken(self, container, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(NWBHDF5IO, "write", broken)
    path = tmp_path / "f.nwb"
    with pytest.raises(RuntimeError):
        write_atomically(new_file(SESSION), path)
    assert list(tmp_path.iterdir()) == []


def test_findings_ranked_above_critical_also_block(tmp_path):
    """The final review's I3: nwbinspector ranks PYNWB_VALIDATION (the file
    fails the NWB schema) and ERROR above CRITICAL. An ERROR that the file
    cannot be read blocks it; an ERROR that one of the inspector's own checks
    raised does not -- nwbinspector 0.7's checks raise on any empty table --
    and is kept with the other findings."""
    import h5py

    from wl_preproc.nwb.validate import inspect_file, n_critical

    levels = ("ERROR", "PYNWB_VALIDATION", "CRITICAL", "BEST_PRACTICE_VIOLATION", "BEST_PRACTICE_SUGGESTION")
    assert n_critical([{"importance": level, "check": "check_x"} for level in levels]) == 3
    crashed = {"importance": "ERROR", "check": "During evaluation of 'check_col_not_nan' - <class 'IndexError'>"}
    assert n_critical([crashed]) == 0
    path = tmp_path / "not_nwb.nwb"
    with h5py.File(path, "w") as handle:
        handle["data"] = [1, 2, 3]
    assert n_critical(inspect_file(path)) >= 1
