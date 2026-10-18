"""What wl-xcon's record says the session was, in the NWB file and its
description, through the builder (design spec
`2026-10-14-session-kind-and-warnings-design.md` section 5). Subjects and
dates checked unclaimed across `tests/` on 2026-10-14; the dates are in the
PAST, since `nwbinspector` calls a future `session_start_time` critical."""

from __future__ import annotations

import pytest

from tests.schema.test_nwb_probes import _canonical
from tests.schema.test_spikeglx_restart import _session as _restart_session

_MEMBER = {"kind": "member", "name": "Jay Dubya", "account": "acct-rk1", "issuer": "https://wl.works",
           "token_id": "tok-rk1"}


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


@pytest.fixture(scope="module")
def recorded(daemon_module, prefix, tmp_path_factory):
    """A training session wl-xcon ran, two runs, one warning accepted at the
    open by a name typed at the rig and one at run 2's start by a wl.works
    member; its training records, a change and two controls in run 1 and a
    change and a manual reward by the member in run 2; a canonical over both
    runs and a derivative over run 1."""
    recipe, key = _restart_session(
        tmp_path_factory, None, runs=True, subject="rknwb1", session_id="2025-06-29_01",
        rig_record={"session_kind": "training", "warnings": [
            {"code": "default-calibration", "by": {"kind": "box", "name": "Typed Name"}},
            {"code": "luminance-step", "run": 1, "how": "start", "by": _MEMBER}],
            "changes": [{"trial": 1}, {"trial": 4, "name": "seed", "was": 1, "now": [2, 3], "by": _MEMBER}],
            "controls": [{"trial": 2}, {"trial": 2, "kind": "note", "event_code": None, "detail": {"note": "ok"}},
                         {"trial": 4, "kind": "reward", "event_code": 4134, "detail": {"ml": 0.1}, "by": _MEMBER}]})
    daemon_module.run_once(prefix=prefix)
    canonical = _canonical(daemon_module, prefix, key, "rknwb1-k1", [])
    derivative = _canonical(daemon_module, prefix, key, "rknwb1-k2", [], run_numbers=[1])
    return recipe, canonical, derivative


@pytest.fixture(scope="module")
def built(recorded, tmp_path_factory):
    from wl_preproc.nwb.build import build

    _recipe, canonical, derivative = recorded
    return (build(canonical, tmp_path_factory.mktemp("nwb-rig-canonical")),
            build(derivative, tmp_path_factory.mktemp("nwb-rig-derivative")))


def test_the_file_holds_the_kind_the_calibrations_and_the_warnings(recorded, built):
    import json

    from pynwb import NWBHDF5IO

    from wl_preproc.synth.peripherals import RIG_CONES

    recipe, _canonical_key, _derivative_key = recorded
    canonical, _derivative = built
    assert canonical.status == "written", (canonical.reason, canonical.findings)
    calibration = recipe.rig_record.calibration_id
    with NWBHDF5IO(str(canonical.path), "r") as handle:
        nwb = handle.read()
        assert nwb.intervals["runs"].to_dataframe()["calibration"].tolist() == [calibration, calibration]
        module = nwb.processing["rig_record"]
        (session,) = module["session"].to_dataframe().to_dict("records")
        assert (session["session_kind"], session["calibration_id"]) == ("training", calibration)
        # The colour model, in the shape wl-xcon writes it (floats, lists, a
        # degree sign), through the database's blob and into JSON unchanged
        # (the session kind's final review's I4).
        assert json.loads(session["calibration_cones"]) == RIG_CONES
        warnings = module["accepted_warnings"].to_dataframe()
        assert warnings[["code", "how", "run_number"]].values.tolist() == [
            ["default-calibration", "open", -1], ["luminance-step", "start", 2]]


def test_the_file_names_no_one_who_accepted_a_warning(built):
    """Decision 3 of the spec's section 0: the database keeps who, and the
    listing gives wl.works the name and account; the file names no one. Nor
    anyone who made a change or sent a control (design spec
    `2026-10-18-training-records-and-backup-clock-design.md` decision 6): the
    member made run 2's change and gave its manual reward."""
    canonical, _derivative = built
    raw = canonical.path.read_bytes()
    for text in ("Typed Name", "Jay Dubya", "acct-rk1", "tok-rk1", "https://wl.works"):
        assert text.encode() not in raw, text


def test_the_description_selects_the_file_by_kind(recorded, built):
    recipe, _canonical_key, _derivative_key = recorded
    canonical, _derivative = built
    calibration = recipe.rig_record.calibration_id
    assert canonical.description["session_kind"] == "training"
    assert canonical.description["calibration"] == {"id": calibration, "standard": False, "measured_on": "2027-03-01"}
    assert canonical.description["accepted_warnings"] == [{"code": "default-calibration", "run_number": None},
                                                          {"code": "luminance-step", "run_number": 2}]
    assert [run["calibration"] for run in canonical.description["runs"]] == [calibration, calibration]


def test_a_file_of_some_runs_holds_the_warnings_of_the_open_and_of_its_own_runs(built):
    """A warning accepted for a run the file does not hold is about that run,
    and stays out of the file (design spec amendment 1)."""
    from pynwb import NWBHDF5IO

    _canonical_built, derivative = built
    assert derivative.status == "written", (derivative.reason, derivative.findings)
    with NWBHDF5IO(str(derivative.path), "r") as handle:
        warnings = handle.read().processing["rig_record"]["accepted_warnings"].to_dataframe()
        assert warnings["code"].tolist() == ["default-calibration"]
    assert derivative.description["accepted_warnings"] == [{"code": "default-calibration", "run_number": None}]


def test_the_file_holds_the_training_records_of_its_runs_at_their_session_times(recorded, built):
    """Design spec `2026-10-18-training-records-and-backup-clock-design.md`
    section 3.2: the canonical holds every change and control, timed as the
    database holds them; the derivative over run 1 holds run 1's only
    (amendment 10)."""
    from pynwb import NWBHDF5IO

    from wl_preproc.schema import core

    _recipe, canonical_key, _derivative_key = recorded
    canonical, derivative = built
    session_key = {name: canonical_key[name] for name in ("subject", "session_datetime")}
    stored = (core.RigControl & session_key).to_arrays("session_time", order_by="control_line")
    with NWBHDF5IO(str(canonical.path), "r") as handle:
        module = handle.read().processing["rig_record"]
        changes = module["parameter_changes"].to_dataframe()
        controls = module["controls"].to_dataframe()
        assert changes[["run_number", "setting", "now", "by_kind"]].values.tolist() == [
            [1, "reward_ml", "0.25", "box"], [2, "seed", "[2, 3]", "member"]]
        # The run beside each control's trial, which wl-xcon counts within it
        # (the final review's M3): run 2's first trial is its 0.
        assert controls[["kind", "time_source", "by_kind", "run_number", "trial_index"]].values.tolist() == [
            ["pause", "code", "box", 1, 1], ["note", "rig_clock", "box", 1, 1], ["reward", "code", "member", 2, 0]]
        assert controls["session_time"].tolist() == pytest.approx(stored.tolist())
    with NWBHDF5IO(str(derivative.path), "r") as handle:
        module = handle.read().processing["rig_record"]
        assert module["parameter_changes"].to_dataframe()["setting"].tolist() == ["reward_ml"]
        assert module["controls"].to_dataframe()["kind"].tolist() == ["pause", "note"]
    assert canonical.description["training_records"] == {"parameter_changes": 2, "controls": 3}
    assert derivative.description["training_records"] == {"parameter_changes": 1, "controls": 2}
