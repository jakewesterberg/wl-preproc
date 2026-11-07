"""The rig's record, read whole, through the builder (design spec
`2026-11-07-rig-record-fields-design.md` sections 3-8 and 10): what each trial
paid and where its stimuli were, how each run and block ended and what each
run owed, a condition's own values apart from drawn and person-held ones, and
the change log by kind and act, in the NWB file, its description and the
listing. Subject and date checked unclaimed across `tests/` on 2026-11-07, the
date in the PAST, since `nwbinspector` calls a future `session_start_time`
critical."""

from __future__ import annotations

import pytest

from tests.schema.test_nwb_probes import _canonical
from tests.schema.test_spikeglx_restart import _session as _restart_session


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


@pytest.fixture(scope="module")
def recorded(daemon_module, prefix, tmp_path_factory):
    """Two runs whose rig wrote every field of wl-xcon's engine builds C2a,
    C2b and C3 and its XC-399 (`synth/peripherals.py`, `fields`): a person's
    value strobed in trial 1, a clear (strobing nothing) in trial 2, and a
    pause."""
    recipe, key = _restart_session(
        tmp_path_factory, None, runs=True, subject="rfnwb1", session_id="2025-07-01_01",
        rig_record={"fields": True, "controls": [{"trial": 2}],
                    "changes": [{"trial": 1}, {"trial": 2, "kind": "clear", "name": "owed", "was": 2, "now": 0,
                                               "event_code": None}]})
    daemon_module.run_once(prefix=prefix)
    return recipe, key, _canonical(daemon_module, prefix, key, "rfnwb1-k1", [])


@pytest.fixture(scope="module")
def built(recorded, tmp_path_factory):
    from wl_preproc.nwb.build import build

    return build(recorded[2], tmp_path_factory.mktemp("nwb-rig-fields"))


def _file(built):
    from pynwb import NWBHDF5IO

    return NWBHDF5IO(str(built.path), "r")


def test_each_trial_says_what_it_paid_and_where_its_stimuli_were(built):
    with _file(built) as io:
        nwb = io.read()
        trials = nwb.trials.to_dataframe()
        first = trials[trials["run_number"] == 1]
        assert first["counting"].tolist() == ["counted", "owed", "counted"]
        assert first["repeat_of"].tolist() == [-1, int(first["trial_id"].iloc[0]), -1]
        assert first["planned_lead_in"].tolist() == [1, 0, 0]
        assert first["live"].tolist() == ["", "", "contrast"] and first["fluid_ml"].tolist() == [0.1, 0.1, 0.1]
        stimuli = nwb.processing["rig_record"]["stimuli"].to_dataframe()
        assert stimuli["stimulus"].value_counts().to_dict() == {"fix": 4, "target": 4, "search": 1}
        target = stimuli[stimuli["stimulus"] == "target"].iloc[0]
        # The angles pass through atan2, which Linux and macOS round apart in
        # the last places (the v3 fixture's CI failure of 2026-11-07).
        assert (target["x_deg"], target["y_deg"]) == (0.0, 5.0)
        assert target["polar_deg"] == pytest.approx(90.0, abs=1e-9)
        objects = nwb.processing["rig_record"]["stimulus_objects"].to_dataframe()
        assert objects["x_deg"].tolist() == [8.0, -8.0]


def test_each_run_and_block_says_how_it_ended_and_what_it_owed(built):
    with _file(built) as io:
        nwb = io.read()
        runs = nwb.intervals["runs"].to_dataframe()
        assert runs["plan"].tolist() == ["synthetic", "synthetic"]
        assert runs["end_reason"].tolist() == ["plan_done", "plan_done"]
        assert runs["stop_kind"].tolist() == ["completed", "completed"]
        assert runs["task_hash"].tolist() == ["sha256:synthetic", "sha256:synthetic"]
        blocks = nwb.intervals["blocks"].to_dataframe()
        assert blocks["ended"].tolist() == ["rule", "rule"]
        counts = nwb.processing["rig_record"]["condition_counts"].to_dataframe()
        trials = nwb.trials.to_dataframe()
        for run in (1, 2):
            assert (set(counts[counts["run_number"] == run]["condition"])
                    == set(trials[trials["run_number"] == run]["condition"]))


def test_the_change_log_is_read_by_kind_and_a_clear_is_timed_by_the_rigs_clock(recorded, built):
    from wl_preproc.schema import core

    _recipe, key, _activation = recorded
    with _file(built) as io:
        changes = io.read().processing["rig_record"]["parameter_changes"].to_dataframe()
        assert changes["kind"].tolist() == ["set", "clear"]
        assert changes["time_source"].tolist() == ["code", "rig_clock"]
    stored = (core.ParameterChange & key).to_dicts(order_by="change_line")
    assert all(row["session_time"] is not None for row in stored)


def test_the_description_and_the_listing_say_it(recorded, built):
    """A condition's own values apart from drawn and held ones; each run's
    plan and end; the count of a person's acts, the clear not among them."""
    from wl_preproc.listing.entry import session_entry

    description = built.description
    first = description["runs"][0]
    assert (first["plan"], first["end_reason"], first["blocks"][0]["ended"]) == ("synthetic", "plan_done", "rule")
    held = [condition for condition in first["conditions"] if condition["live"]]
    assert held and all(condition["trials"]["eased"] >= 1 for condition in held)
    assert all("fix_hold_s" in condition["drawn"] for condition in first["conditions"])
    assert all("fix_hold_s" not in (condition["settings"] or {}) for condition in first["conditions"])
    assert description["training_records"]["parameter_changes"] == 1
    assert not [note for note in description["notes"] if "rig's" in note or "stimulus" in note]
    _recipe, key, _activation = recorded
    assert session_entry(key)["training_records"]["parameter_changes"] == 1
