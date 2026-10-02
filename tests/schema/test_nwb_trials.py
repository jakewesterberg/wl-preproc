"""What the file says about trials the canonical trial list leaves out
(design spec `2026-10-01-runs-and-trials-design.md` sections 3.2 and 3.4).
Subject and date checked unclaimed across `tests/` on 2026-10-01; the date is
in the PAST, since `nwbinspector` calls a future `session_start_time`
critical."""

from __future__ import annotations

import pytest

from tests.schema.test_nwb_probes import _canonical, _session


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


def _no_task_file(spikeglx_directory):
    """A real wl-xcon session has no task file in the synthetic format, so
    `TimingProvenance.trial_count_agreement` is null for it. Kept, the
    synthetic one lists four trials against two stored, and the session would
    fall to tier D, which no real session meets for this reason."""
    (spikeglx_directory.parent / "syncbox" / "task.json").unlink()


def test_the_description_names_a_repeated_number_and_one_too_large(daemon_module, prefix, tmp_path_factory):
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.build import build

    _recipe, key = _session(tmp_path_factory, _no_task_file, subject="rtnwb1", session_id="2025-06-28_01",
                            trial_numbers=[1, 2, 2, 40000])
    daemon_module.run_once(prefix=prefix)
    activation = _canonical(daemon_module, prefix, key, "rtnwb1-k1", [])

    result = build(activation, tmp_path_factory.mktemp("nwb-trials"))

    assert result.status == "written", (result.reason, result.findings)
    notes = result.description["notes"]
    assert [note for note in notes if note.startswith(("trial number ", "1 trial(s) numbered"))] == [
        "trial number 2 appears 2 times in the recording; only the first is stored",
        "1 trial(s) numbered above 32,767 are not stored: element-event's trial_id holds no larger number"]
    with NWBHDF5IO(str(result.path), "r") as handle:
        assert sorted(handle.read().trials["trial_id"][:]) == [1, 2]
