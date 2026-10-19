"""wl-xcon's training records as it writes them today: `xcon/parameter_changes.jsonl`
and `xcon/controls.jsonl` (design spec
`2026-10-18-training-records-and-backup-clock-design.md` section 3.1, amendment 2)."""

from __future__ import annotations

import json

MEMBER = {"kind": "member", "name": "J. W.", "account": "acct-1", "issuer": "https://wl.works", "token_id": "tok-1"}
BOX = {"kind": "box", "name": "rig-a"}


def _record(tmp_path, changes=None, controls=None):
    (tmp_path / "xcon").mkdir()
    for name, lines in (("parameter_changes.jsonl", changes), ("controls.jsonl", controls)):
        if lines is not None:
            (tmp_path / "xcon" / name).write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return tmp_path


def _change(name="reward_ml", was=0.2, now=0.25, **fields):
    return json.dumps({"sequence": 1, "name": name, "was": was, "now": now, "by": MEMBER, "run": 0,
                       "event_code": 4130, **fields}, sort_keys=True)


def _control(kind="pause", **fields):
    return json.dumps({"kind": kind, "by": BOX, "at": 1773478800.25, "at_local": "2027-03-14T10:00:00.250000+01:00",
                       "trial_index": 3, "event_code": 4131, "run": 0, **fields}, sort_keys=True)


def test_every_change_is_kept_as_written(tmp_path):
    """Its line as written, not as JSON would write it again; its values as
    JSON text; its actor as `core.AcceptedWarning` holds one."""
    from wl_preproc.events.rigrecords import read_rig_records

    line = _change(was=[1, 2.5], now={"x": None, "y": "ten"}).replace('": ', '":')
    (change,) = read_rig_records(_record(tmp_path, changes=[line])).changes
    assert (change.line, change.sequence, change.name, change.run_index, change.event_code, change.coded) == (
        1, 1, "reward_ml", 0, 4130, True)
    assert (json.loads(change.was), json.loads(change.now)) == ([1, 2.5], {"x": None, "y": "ten"})
    assert (change.by_kind, change.by_name, change.by_account, change.by_issuer, change.by_token_id) == (
        "member", "J. W.", "acct-1", "https://wl.works", "tok-1")
    assert change.row == line


def test_every_control_is_kept_with_its_own_fields_as_its_detail(tmp_path):
    """`detail` is what the row carries beyond a control's common fields: a
    manual reward's mL, a mark's frame and number."""
    from wl_preproc.events.rigrecords import read_rig_records

    line = _control("reward", event_code=4134, ml=0.1, entry="reward_ml", where="paused before trial 3")
    (control,) = read_rig_records(_record(tmp_path, controls=[line])).controls
    assert (control.line, control.kind, control.at, control.at_local, control.trial_index, control.run_index,
            control.event_code, control.coded) == (1, "reward", 1773478800.25, "2027-03-14T10:00:00.250000+01:00",
                                                    3, 0, 4134, True)
    assert json.loads(control.detail) == {"ml": 0.1, "entry": "reward_ml", "where": "paused before trial 3"}
    assert (control.by_kind, control.by_name, control.by_account) == ("box", "rig-a", None)
    assert control.row == line


def test_a_row_that_strobed_nothing_says_so_and_one_written_before_the_field_does_not_say(tmp_path):
    """Amendment 2: `null` is a row whose act strobed nothing; a row without
    the key was written before wl-xcon carried it, and is unknown, never
    null. A value that is not a code says nothing either."""
    from wl_preproc.events.rigrecords import read_rig_records

    lines = [_control("note", event_code=None),
             json.dumps({k: v for k, v in json.loads(_control()).items() if k != "event_code"}),
             _control(event_code="4131"), _control(event_code=0), _control(event_code=70000)]
    controls = read_rig_records(_record(tmp_path, controls=lines)).controls
    assert [(control.event_code, control.coded) for control in controls] == [
        (None, True), (None, False), (None, False), (None, False), (None, False)]


def test_a_control_by_nobody_has_no_actor(tmp_path):
    """wl-xcon writes `by: null` for a control nobody sent: a mark, a
    scheduled stop firing."""
    from wl_preproc.events.rigrecords import read_rig_records

    (control,) = read_rig_records(_record(tmp_path, controls=[_control("mark", by=None)])).controls
    assert (control.by_kind, control.by_name, control.by_account, control.by_issuer, control.by_token_id) == (
        None, None, None, None, None)


def test_an_unreadable_line_is_named_by_file_and_line_and_left_out(tmp_path):
    from wl_preproc.events.rigrecords import read_rig_records

    records = read_rig_records(_record(
        tmp_path, changes=[_change(), "{not json", json.dumps({"was": 1}), "", _change(name="seed")],
        controls=["[1, 2]", _control(), json.dumps({"kind": ""})]))
    assert [change.line for change in records.changes] == [1, 5]
    assert [control.line for control in records.controls] == [2]
    assert records.problems == (
        "parameter_changes.jsonl line 2: not JSON",
        "parameter_changes.jsonl line 3: not a change naming its setting",
        "controls.jsonl line 1: not a control naming its kind",
        "controls.jsonl line 3: not a control naming its kind",
    )


def test_a_line_torn_by_a_killed_process_is_named_and_the_rows_before_it_kept(tmp_path):
    """The plan's Review Focus 3: a process killed while appending leaves
    half a last line and no newline."""
    from wl_preproc.events.rigrecords import read_rig_records

    (tmp_path / "xcon").mkdir()
    (tmp_path / "xcon" / "parameter_changes.jsonl").write_text(
        _change() + "\n" + _change(name="seed") + "\n" + '{"sequence": 3, "na', encoding="utf-8")
    records = read_rig_records(tmp_path)
    assert [change.line for change in records.changes] == [1, 2]
    assert records.problems == ("parameter_changes.jsonl line 3: not JSON",)


def test_a_value_that_is_not_what_its_field_holds_is_read_as_none(tmp_path):
    """As `events/rigsession.py` reads one: a number that is not finite, a
    count that is not a whole number, read as none rather than failing the
    session's event stage on every pass."""
    from wl_preproc.events.rigrecords import read_rig_records

    records = read_rig_records(_record(
        tmp_path, changes=[_change(sequence="7", run="0")],
        controls=[_control(at=float("nan"), trial_index=2.5, run=True, at_local=5)]))
    (change,), (control,) = records.changes, records.controls
    assert (change.sequence, change.run_index) == (None, None)
    assert (control.at, control.trial_index, control.run_index, control.at_local) == (None, None, None, "5")
    assert records.problems == ()


def test_a_session_without_either_file_has_no_records_and_no_problem(tmp_path):
    from wl_preproc.events.rigrecords import RigRecords, read_rig_records

    assert read_rig_records(_record(tmp_path)) == RigRecords(changes=(), controls=(), problems=())
