"""wl-xcon's training records in the database: every row of
`parameter_changes.jsonl` and `controls.jsonl` kept as written, each timed by
the code it strobed or the rig's clock fit (design spec
`2026-10-18-training-records-and-backup-clock-design.md` sections 3.1, 3.3 and
8)."""

from __future__ import annotations

import datetime
import json

import pytest

from wl_preproc.schema import events


@pytest.fixture(scope="module")
def events_activated(dj_conn, prefix):
    events.activate(prefix=prefix)
    return events


def _land(tmp_path, subject, day, mutate=None, changes=(), controls=(), **update):
    """A CI session with runs, its rig record carrying `changes` and
    `controls`, through the events stage."""
    from wl_preproc.synth.recipe import RigRecordSpec

    from tests.schema.test_events import _populate_generated

    rig = RigRecordSpec(changes=changes, controls=controls).model_dump()
    return _populate_generated(tmp_path, subject, datetime.datetime(2027, 8, day, 9, 0), mutate=mutate, runs=True,
                               rig_record=rig, **update)


def _acts():
    from wl_preproc.synth.recipe import RigChangeSpec, RigControlSpec

    member = {"kind": "member", "name": "J. W.", "account": "acct-1", "issuer": "https://wl.works",
              "token_id": "tok-1"}
    changes = (RigChangeSpec(trial=1, by=member), RigChangeSpec(trial=3, name="seed", was=7, now=[8, 9]))
    controls = (RigControlSpec(trial=2), RigControlSpec(trial=2, kind="resume", event_code=4132),
                RigControlSpec(trial=3, kind="note", event_code=None, detail={"note": "lid droop"}),
                RigControlSpec(trial=4, kind="reward", event_code=4134, detail={"ml": 0.1}))
    return changes, controls


def test_every_row_is_kept_as_written_and_timed_by_its_code(events_activated, dj_conn, tmp_path):
    """Section 8, part 1: every row of both files stored as written, each
    change and coded control at its code's session time, and a note, which
    strobes nothing, through the rig's clock."""
    from wl_preproc.schema import core
    from wl_preproc.synth.timeline import build_timeline

    changes, controls = _acts()
    recipe, key = _land(tmp_path, "trrec1", 1, changes=changes, controls=controls)
    truth = build_timeline(recipe)
    xcon = tmp_path / recipe.session_id / "xcon"
    change_rows = (core.ParameterChange & key).to_dicts(order_by="change_line")
    assert [row["row"] for row in change_rows] == (xcon / "parameter_changes.jsonl").read_text().splitlines()
    assert [(row["name"], json.loads(row["was"]), json.loads(row["now"]), row["sequence"], row["event_code"],
             row["run_index"], row["run_number"]) for row in change_rows] == [
        ("reward_ml", 0.2, 0.25, 1, 4130, 0, 1), ("seed", 7, [8, 9], 2, 4130, 0, 1)]
    assert (change_rows[0]["by_kind"], change_rows[0]["by_account"], change_rows[0]["by_token_id"]) == (
        "member", "acct-1", "tok-1")
    assert [row["session_time"] for row in change_rows] == pytest.approx(list(truth.change_times), abs=1e-4)

    control_rows = (core.RigControl & key).to_dicts(order_by="control_line")
    assert [row["row"] for row in control_rows] == (xcon / "controls.jsonl").read_text().splitlines()
    assert [(row["kind"], row["event_code"], row["time_source"], row["run_number"]) for row in control_rows] == [
        ("pause", 4131, "code", 1), ("resume", 4132, "code", 1), ("note", None, "rig_clock", 1),
        ("reward", 4134, "code", 2)]
    assert [json.loads(row["detail"]) for row in control_rows] == [{}, {}, {"note": "lid droop"}, {"ml": 0.1}]
    coded = [row["session_time"] for row in control_rows if row["time_source"] == "code"]
    assert coded == pytest.approx([truth.control_times[0], truth.control_times[1], truth.control_times[3]],
                                  abs=1e-4)
    assert control_rows[2]["session_time"] == pytest.approx(truth.control_times[2], abs=0.01)
    clock = (core.RigClock & key).fetch1()
    assert clock["n_matched"] == 7 and clock["residual_ms_max"] <= 10.0  # 4 run instants, 3 coded controls
    assert len(core.RunRecordProblem & key) == 0


def test_a_value_mysqls_json_type_would_round_or_refuse_is_kept_as_written(events_activated, dj_conn, tmp_path):
    """The plan's Review Focus 2 (amendment 9): MySQL's json type rounds a
    double's seventeenth digit and refuses NaN, which wl-xcon's `json.dumps`
    writes; kept as JSON text, both read back as written."""
    from wl_preproc.schema import core
    from wl_preproc.synth.recipe import RigChangeSpec

    recipe, key = _land(tmp_path, "trrec8", 8, changes=(RigChangeSpec(trial=1, was=float("nan"),
                                                                      now=0.30000000000000004),))
    row = (core.ParameterChange & key).fetch1()
    assert (row["was"], row["now"]) == ("NaN", "0.30000000000000004")
    assert row["row"] == (tmp_path / recipe.session_id / "xcon" / "parameter_changes.jsonl").read_text().strip()


def test_a_count_that_disagrees_leaves_its_rows_untimed_and_names_both_counts(events_activated, dj_conn,
                                                                              tmp_path):
    """A change row lost from the file: two codes, one row, so that number's
    rows stay untimed; the controls are timed as before."""
    from wl_preproc.schema import core

    def lose_a_change(session_dir):
        path = session_dir / "xcon" / "parameter_changes.jsonl"
        path.write_text(path.read_text().splitlines()[0] + "\n")

    changes, controls = _acts()
    _recipe, key = _land(tmp_path, "trrec2", 2, mutate=lose_a_change, changes=changes, controls=controls)
    assert (core.ParameterChange & key).to_arrays("session_time").tolist() == [None]
    assert set((core.RigControl & key).to_arrays("time_source")) == {"code", "rig_clock"}
    assert [row["problem"] for row in (core.RunRecordProblem & key).to_dicts(order_by="problem_number")] == [
        "parameter_changes.jsonl: 1 row carries code 4130 and the recording holds 2, so it is not timed"]


def test_an_unreadable_line_is_named_by_file_and_line(events_activated, dj_conn, tmp_path):
    from wl_preproc.schema import core

    def damage(session_dir):
        path = session_dir / "xcon" / "controls.jsonl"
        lines = path.read_text().splitlines()
        path.write_text("\n".join([lines[0], "{not json", *lines[1:]]) + "\n")

    changes, controls = _acts()
    _recipe, key = _land(tmp_path, "trrec3", 3, mutate=damage, changes=changes, controls=controls)
    assert (core.RigControl & key).to_arrays("control_line").tolist() == [1, 3, 4, 5]
    assert [row["problem"] for row in (core.RunRecordProblem & key).to_dicts(order_by="problem_number")] == [
        "controls.jsonl line 2: not JSON"]


def test_another_animals_record_writes_no_training_records(events_activated, dj_conn, tmp_path):
    """Ask 5's rule: a `config.json` naming another subject is not this
    session's, and nothing of it is kept but its problem."""
    from wl_preproc.schema import core

    def another_animal(session_dir):
        path = session_dir / "xcon" / "config.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "subject": "other"}))

    changes, controls = _acts()
    _recipe, key = _land(tmp_path, "trrec4", 4, mutate=another_animal, changes=changes, controls=controls)
    assert (len(core.ParameterChange & key), len(core.RigControl & key), len(core.RigClock & key)) == (0, 0, 0)


def test_a_run_that_will_not_fit_is_a_problem_and_names_no_run(events_activated, dj_conn, tmp_path):
    from wl_preproc.schema import core

    def far_run(session_dir):
        path = session_dir / "xcon" / "controls.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]["run"] = 70000
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    _changes, controls = _acts()
    _recipe, key = _land(tmp_path, "trrec5", 5, mutate=far_run, controls=controls[:1])
    row = (core.RigControl & key).fetch1()
    assert (row["run_index"], row["run_number"]) == (None, None)
    assert [row["problem"] for row in (core.RunRecordProblem & key).to_dicts(order_by="problem_number")] == [
        "controls.jsonl line 1: run 70000 cannot be stored (0 to 65535), so the control names no run"]


def test_a_session_read_again_keeps_one_of_each_record(events_activated, dj_conn, tmp_path):
    from wl_preproc.schema import core

    changes, controls = _acts()
    recipe, key = _land(tmp_path, "trrec6", 6, changes=changes, controls=controls)
    events.populate_session(key, tmp_path / recipe.session_id)
    assert (len(core.ParameterChange & key), len(core.RigControl & key), len(core.RigClock & key)) == (2, 4, 1)


def test_a_session_without_training_records_fits_the_rigs_clock_from_its_runs(events_activated, dj_conn,
                                                                             tmp_path):
    """Section 3.3: the fit is made on every session whose codes and rig
    record allow it; two closed runs give four instants."""
    from wl_preproc.schema import core

    _recipe, key = _land(tmp_path, "trrec7", 7)
    assert (len(core.ParameterChange & key), len(core.RigControl & key)) == (0, 0)
    assert (core.RigClock & key).fetch1("n_matched") == 4
    assert len(core.RunRecordProblem & key) == 0


def test_a_lone_surrogate_in_a_controls_text_is_kept_and_the_stage_completes(events_activated, dj_conn,
                                                                            tmp_path):
    """The final review's I1: wl-xcon writes ASCII lines, so half an emoji,
    split at a field's limit, arrives as `\\ud83d`, which `json.loads` makes a
    lone surrogate that utf8mb4 cannot store, failing the session's whole
    events stage on every pass. The detail's JSON text keeps it escaped, as
    exact JSON; a text column keeps its escape."""
    from wl_preproc.schema import core
    from wl_preproc.synth.recipe import RigControlSpec

    _recipe, key = _land(tmp_path, "trsurr1", 9, controls=(RigControlSpec(
        trial=2, kind="note", event_code=None, detail={"note": "lid \ud83d"},
        by={"kind": "box", "name": "J\ud83d"}),))
    row = (core.RigControl & key).fetch1()
    assert json.loads(row["detail"]) == {"note": "lid \ud83d"}
    assert row["by_name"] == "J\\ud83d"
