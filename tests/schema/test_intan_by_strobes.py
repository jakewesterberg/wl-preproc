"""Intan on the NI's clock, fitted by its strobes in the database (part 3b-2;
design spec `2026-10-18-training-records-and-backup-clock-design.md`,
amendments 44 to 50). Every session here is generated, hand-landed and taken
through one daemon pass."""

from __future__ import annotations

import shutil

import pytest

from tests.identities import new_animal, new_serial
from tests.schema.test_ni_clock import _errors_of


class _Landed(dict):
    report: dict

    def key(self, name):
        return self[name][2]

    def truth(self, name):
        return self[name][1]


@pytest.fixture(scope="module")
def landed(dj_conn, prefix, tmp_path_factory):
    """Each session by name, `(recipe, truth, key, session_dir)`, after one
    daemon pass: the NI with Intan, the same with Intan's strobes cleared, the
    same with two Intan recordings and the second's cleared, and the same
    beside a sync box. Deleted afterwards."""
    from wl_preproc import daemon
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import NI_INTAN_RECIPE
    from wl_preproc.synth.session import generate_session
    from wl_preproc.timebase.extract import find_recordings

    from tests.schema.test_eye_populate import _land
    from tests.timebase.intan_helpers import _clear_intan_strobes

    daemon.activate_all(prefix=prefix)
    serial = new_serial()
    sessions = _Landed()

    def land(name: str, **update):
        session = new_animal().session()
        recipe = NI_INTAN_RECIPE.model_copy(update={"subject": session.subject, "session_id": session.session_id,
                                                    "probe_serial": serial, **update})
        root = tmp_path_factory.mktemp(name)
        truth = generate_session(root, recipe)
        session_dir = root / recipe.session_id
        if name == "no_strobe":
            (recording,) = find_recordings("rhs", session_dir / "rhs")
            _clear_intan_strobes(recording)
        if name == "two":
            (recording,) = find_recordings("rhs", session_dir / "rhs")
            shutil.move(recording, session_dir / "rhs" / "a")
            shutil.copytree(session_dir / "rhs" / "a", session_dir / "rhs" / "b")
            _clear_intan_strobes(session_dir / "rhs" / "b")
        key = _land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems)
        sessions[name] = (recipe, truth, key, session_dir)

    land("ni_intan")
    land("no_strobe")
    land("two")
    # Its montage covers the waits the recipe keeps.
    land("synced", systems=("syncbox", "spikeglx", "rhs"))
    sessions.report = daemon.run_once(prefix=prefix)
    yield sessions
    for _recipe, _truth, key, _session_dir in sessions.values():
        (pipeline.Session & key).delete(prompt=False)


def test_intan_is_fitted_to_the_ni_by_its_strobes(landed):
    from wl_preproc.schema import timebase

    row = (timebase.SystemTimebase & landed.key("ni_intan") & {"system": "rhs"}).fetch1()
    assert (row["fit_status"], row["time_source"]) == ("fitted", "strobes")
    assert row["n_strobes_matched"] == len(landed.truth("ni_intan").code_words)
    assert (row["n_barcodes_decoded"], row["n_barcodes_matched"]) == (0, 0)
    relative_ppm = ((1 - 40e-6) / (1 + 18e-6) - 1) * 1e6
    assert row["drift_ppm"] == pytest.approx(relative_ppm, abs=5.0)
    assert row["residual_us_max"] <= 100.0


def test_intans_segment_maps_every_code_onto_the_nis_time(landed):
    from wl_preproc.schema import core, timebase
    from wl_preproc.synth.rhs import RHS_PRE_ROLL_S
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S
    from wl_preproc.timebase import segments

    key = {**landed.key("ni_intan"), "system": "rhs"}
    seg = (core.Segment & key).fetch1()
    rate = segments.rate_fit_from_row((timebase.SystemTimebase & key).fetch1())
    assert seg["n_barcodes"] == 0
    for t, _ in landed.truth("ni_intan").code_words:
        intan_s = t * (1 - 40e-6) + RHS_PRE_ROLL_S
        assert intan_s / rate.scale + seg["offset_s"] == pytest.approx(
            t * (1 + 18e-6) + SPIKEGLX_PRE_ROLL_S, abs=50e-6)


def test_an_ni_and_intan_session_is_tier_n(landed):
    from wl_preproc.schema import timebase

    row = (timebase.TimingProvenance & landed.key("ni_intan")).fetch1()
    assert row["tier"] == "N" and row["n_systems_aligned"] == 2 and row["n_rejected_segments"] == 0


def test_an_intan_with_no_strobe_is_left_out_and_named(landed):
    from wl_preproc.schema import core, timebase

    key = landed.key("no_strobe")
    row = (timebase.SystemTimebase & key & {"system": "rhs"}).fetch1()
    assert row["fit_status"] == "unfittable" and row["n_strobes_matched"] is None
    assert (core.RejectedSegment & key & {"system": "rhs"}).fetch1("reason") == "no_strobe_anchor"
    assert (timebase.TimingProvenance & key).fetch1("tier") == "D"


def test_of_two_intan_recordings_the_unanchored_one_is_named(landed):
    from wl_preproc.schema import core

    key = {**landed.key("two"), "system": "rhs"}
    # `Segment.make` names a file relative to its system's directory.
    assert (core.Segment & key).to_arrays("segment_id").tolist() == ["a"]
    assert (core.RejectedSegment & key).fetch1("file_path", "reason") == ("b", "no_strobe_anchor")


def test_under_the_sync_box_intan_is_still_fitted_by_barcodes(landed):
    from wl_preproc.schema import timebase

    row = (timebase.SystemTimebase & landed.key("synced") & {"system": "rhs"}).fetch1()
    assert (row["fit_status"], row["time_source"], row["n_strobes_matched"]) == ("fitted", "barcode", None)


def test_the_passes_report_no_error(landed):
    for name in ("ni_intan", "no_strobe", "two", "synced"):
        assert _errors_of(landed.report, landed.key(name)) == []
