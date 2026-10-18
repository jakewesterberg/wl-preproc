"""What wl-xcon's record says the session was: its kind and display
calibration, from `xcon/config.json`, and the warnings it accepted, from
`xcon/warnings.jsonl` (design spec `2026-10-14-session-kind-and-warnings-design.md`
sections 1 and 2)."""

from __future__ import annotations

import json

CALIBRATION = {"id": "rig-a-2026-10-01", "standard": False, "measured_on": "2026-10-01",
               "cones": {"observer": {"name": "CIE2006_10"}, "via": "spectra", "luminance": None, "dkl": "unit"}}


def _record(tmp_path, config=None, warnings=None):
    (tmp_path / "xcon").mkdir()
    if config is not None:
        (tmp_path / "xcon" / "config.json").write_text(
            config if isinstance(config, str) else json.dumps(config), encoding="utf-8")
    if warnings is not None:
        (tmp_path / "xcon" / "warnings.jsonl").write_text("\n".join(warnings) + "\n", encoding="utf-8")
    return tmp_path


def _warning(code, run=None, **fields):
    row = {"code": code, "detail": f"the {code} sentence", "accepted_in": ["training", "piloting"],
           "session_kind": "training", "by": {"kind": "box", "name": "jw"}, "at": 1760000000.5,
           "at_local": "2026-10-09T09:00:00+02:00 Europe/Berlin", "how": "open", "run": run}
    return json.dumps({**row, **fields})


def test_the_kind_and_the_calibration_are_read_from_config(tmp_path):
    from wl_preproc.events.rigsession import RigCalibration, read_rig_session

    session = read_rig_session(_record(tmp_path, {"subject": "pico", "session_kind": "training",
                                                  "calibration": CALIBRATION}), "pico")
    assert session.kind == "training"
    assert session.calibration == RigCalibration(id="rig-a-2026-10-01", standard=False, measured_on="2026-10-01",
                                                 cones=CALIBRATION["cones"])
    assert (session.warnings, session.problems) == ((), ())


def test_a_config_written_before_build_b_has_no_kind_and_no_problem(tmp_path):
    """wl-xcon's engine build B added both fields; an older `config.json`
    has neither, which is not a problem: they were not recorded."""
    from wl_preproc.events.rigsession import read_rig_session

    session = read_rig_session(_record(tmp_path, {"subject": "pico", "session_id": "2026-09-01_01"}), "pico")
    assert (session.kind, session.calibration, session.problems) == (None, None, ())


def test_a_calibration_that_would_not_load_is_null(tmp_path):
    """wl-xcon writes `null` when the rig's record would not load."""
    from wl_preproc.events.rigsession import read_rig_session

    session = read_rig_session(_record(tmp_path, {"subject": "pico", "session_kind": "piloting",
                                                  "calibration": None}), "pico")
    assert (session.kind, session.calibration, session.problems) == ("piloting", None, ())


def test_a_kind_outside_the_three_is_a_problem_and_no_kind(tmp_path):
    from wl_preproc.events.rigsession import read_rig_session

    session = read_rig_session(_record(tmp_path, {"subject": "pico", "session_kind": "rehearsal"}), "pico")
    assert session.kind is None
    assert session.problems == ("config.json: session kind 'rehearsal' is not one of training, piloting, "
                                "recording",)


def test_a_calibration_that_is_not_an_object_is_a_problem(tmp_path):
    from wl_preproc.events.rigsession import read_rig_session

    session = read_rig_session(_record(tmp_path, {"subject": "pico", "calibration": "rig-a"}), "pico")
    assert session.calibration is None
    assert session.problems == ("config.json: calibration is neither an object nor null",)


def test_every_accepted_warning_is_read_as_written(tmp_path):
    from wl_preproc.events.rigsession import RigWarning, read_rig_session

    member = {"kind": "member", "name": "J. W.", "account": "acct-1", "issuer": "https://wl.works",
              "token_id": "tok-1"}
    session = read_rig_session(_record(tmp_path, {"subject": "pico", "session_kind": "training"}, [
        _warning("default-calibration"),
        _warning("luminance-step", run=1, how="start", by=member),
    ]), "pico")
    assert session.warnings == (
        RigWarning(code="default-calibration", detail="the default-calibration sentence",
                   accepted_in=("training", "piloting"), session_kind="training", how="open", run_index=None,
                   at=1760000000.5, at_local="2026-10-09T09:00:00+02:00 Europe/Berlin",
                   by_kind="box", by_name="jw", by_account=None, by_issuer=None, by_token_id=None),
        RigWarning(code="luminance-step", detail="the luminance-step sentence",
                   accepted_in=("training", "piloting"), session_kind="training", how="start", run_index=1,
                   at=1760000000.5, at_local="2026-10-09T09:00:00+02:00 Europe/Berlin",
                   by_kind="member", by_name="J. W.", by_account="acct-1", by_issuer="https://wl.works",
                   by_token_id="tok-1"),
    )
    assert session.problems == ()


def test_a_warning_accepted_by_nobody_has_no_one(tmp_path):
    """`by` is null for a warning `wlx run` accepted with no name given."""
    from wl_preproc.events.rigsession import read_rig_session

    (warning,) = read_rig_session(_record(tmp_path, {"subject": "pico"}, [
        _warning("head-free", by=None, how="--accept-warnings")]), "pico").warnings
    assert (warning.by_kind, warning.by_name, warning.how) == (None, None, "--accept-warnings")


def test_a_warning_line_it_cannot_read_is_reported_by_file_and_line_and_left_out(tmp_path):
    from wl_preproc.events.rigsession import read_rig_session

    session = read_rig_session(_record(tmp_path, {"subject": "pico"}, [
        _warning("default-calibration"), "{not json", json.dumps({"detail": "no code"}), json.dumps([1]),
    ]), "pico")
    assert [warning.code for warning in session.warnings] == ["default-calibration"]
    assert session.problems == ("warnings.jsonl line 2: not JSON",
                                "warnings.jsonl line 3: not a warning naming its code",
                                "warnings.jsonl line 4: not a warning naming its code")


def test_a_record_of_another_animal_is_not_read(tmp_path):
    from wl_preproc.events.rigsession import read_rig_session

    session = read_rig_session(_record(tmp_path, {"subject": "zorro", "session_kind": "recording"},
                                       [_warning("default-calibration")]), "pico")
    assert (session.kind, session.calibration, session.warnings) == (None, None, ())
    assert session.problems == ("config.json names subject 'zorro', not 'pico', so its kind, calibration and "
                                "warnings are not this session's",)


def test_a_config_it_cannot_read_is_a_problem_and_its_warnings_are_still_read(tmp_path):
    """As the run record is read when `config.json` is not: nothing there
    says the record is another animal's."""
    from wl_preproc.events.rigsession import read_rig_session

    session = read_rig_session(_record(tmp_path, "{not json", [_warning("default-calibration")]), "pico")
    assert session.kind is None
    assert [warning.code for warning in session.warnings] == ["default-calibration"]
    assert session.problems == ("config.json: not a JSON object",)


def test_a_session_without_a_config_has_no_record(tmp_path):
    """The record is the session's `config.json`: without one, wl-xcon did
    not run the session, and its warnings, if any, are not read."""
    from wl_preproc.events.rigsession import read_rig_session

    assert read_rig_session(_record(tmp_path, warnings=[_warning("default-calibration")]), "pico") is None
    assert read_rig_session(tmp_path / "nowhere", "pico") is None


def test_a_config_naming_no_subject_is_read_as_this_sessions(tmp_path):
    """As the run record is (`events/rigruns.py`): only a subject that differs
    makes the record another animal's (Review Focus 2)."""
    from wl_preproc.events.rigsession import read_rig_session

    session = read_rig_session(_record(tmp_path, {"session_kind": "recording"}, [_warning("head-free")]), "pico")
    assert (session.kind, [warning.code for warning in session.warnings], session.problems) == (
        "recording", ["head-free"], ())


def test_a_warning_whose_kinds_are_not_a_list_is_acceptable_in_none(tmp_path):
    """Read, never repaired: `accepted_in` is kept only as wl-xcon writes it,
    a list (Review Focus 5)."""
    from wl_preproc.events.rigsession import read_rig_session

    (warning,) = read_rig_session(_record(tmp_path, {"subject": "pico"}, [
        _warning("head-free", accepted_in="training")]), "pico").warnings
    assert warning.accepted_in == ()
