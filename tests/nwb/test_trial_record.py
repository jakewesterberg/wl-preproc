"""What the rig's record says of each trial beyond its condition and settings,
as trials-table columns (design spec `2026-11-07-rig-record-fields-design.md`
section 3)."""

from __future__ import annotations

import math

from wl_preproc.events.rigtrials import Planned, RigRecord, RigTrial


def _rig(number, index, run=0, **fields):
    return RigTrial(number=number, index=index, outcome="correct", block="main", condition="c", params={}, run=run,
                    **fields, carries=frozenset(fields))


def _trials(*numbers):
    return [{"trial_id": number} for number in numbers]


def test_a_column_is_written_only_where_some_line_carries_its_field():
    """A record from before wl-xcon's engine build C2a gains its fluid alone,
    the one such field its lines carried (the final review's I2); a trial
    with no line has -1, '' or NaN."""
    from wl_preproc.nwb.trial_record import record_columns

    assert record_columns(_trials(1, 2), {1: _rig(1, 0)}, {}) == ({}, [])
    columns, notes = record_columns(_trials(1, 2), {1: _rig(1, 0, fluid_ml=0.0)}, {})
    assert sorted(columns) == ["fluid_ml"] and columns["fluid_ml"][0] == 0.0 and notes == []
    matched = {1: _rig(1, 0, counting="spent", shown=False, aids=("drumming", "clear"), fluid_ml=0.1)}
    columns, notes = record_columns(_trials(1, 2), matched, {})
    assert sorted(columns) == ["aids", "counting", "fluid_ml", "shown"]
    assert (columns["counting"], columns["shown"], columns["aids"]) == (["spent", ""], [0, -1], ["clear,drumming", ""])
    assert columns["fluid_ml"][0] == 0.1 and math.isnan(columns["fluid_ml"][1])
    assert notes == []


def test_a_repeat_names_the_trial_it_shows_again_by_its_trial_id():
    """Section 2's ruling: the rig names it by its index in the run; the
    column gives that trial's trial_id. One its run's lines do not name is
    -1, and the notes say so."""
    from wl_preproc.nwb.trial_record import places, record_columns

    lines = [_rig(11, 0, run=1), _rig(12, 1, run=1, repeat_of=0), _rig(13, 2, run=1, repeat_of=9),
             _rig(14, 0, run=2, repeat_of=None)]
    record = RigRecord(trials=tuple(lines), problems=())
    columns, notes = record_columns(_trials(11, 12, 13, 14), {line.number: line for line in lines}, places(record))
    assert columns["repeat_of"] == [-1, 11, -1, -1]
    assert notes == ["1 trial(s) show again a trial no line of their run names, so their repeat_of is -1 (trial 13)"]


def test_the_planned_place_is_six_columns_where_a_designed_block_ran():
    """Written only when some trial's planned place is not null: a
    sequence-balanced block's cycle, position and lead-in, a priming block's
    run, switch and value."""
    from wl_preproc.nwb.trial_record import record_columns

    plain = {1: _rig(1, 0, planned=None)}
    assert "planned_cycle" not in record_columns(_trials(1), plain, {})[0]
    matched = {1: _rig(1, 0, planned=Planned(cycle=0, position=0, lead_in=True)),
               2: _rig(2, 1, planned=Planned(run=3, switch=False, value=270.0)), 3: _rig(3, 2, planned=None)}
    columns, _notes = record_columns(_trials(1, 2, 3), matched, {})
    assert columns["planned_cycle"] == [0, -1, -1] and columns["planned_position"] == [0, -1, -1]
    assert columns["planned_lead_in"] == [1, -1, -1]
    assert (columns["planned_run"], columns["planned_switch"], columns["planned_value"]) == (
        [-1, 3, -1], [-1, 0, -1], ["", "270.0", ""])


def test_lists_are_sorted_and_comma_joined_and_draws_are_json_text():
    from wl_preproc.nwb.trial_record import record_columns

    matched = {1: _rig(1, 0, overridden=("b", "a"), live=("hold", "contrast"), eased=(),
                       drawn={"jitter": 7, "hold": "task"}, structure="default", previous=None, drumming=None)}
    columns, _notes = record_columns(_trials(1), matched, {})
    assert (columns["overridden"], columns["live"], columns["eased"]) == (["a,b"], ["contrast,hold"], [""])
    assert columns["drawn"] == ['{"hold": "task", "jitter": 7}']
    assert (columns["structure"], columns["previous"], columns["drumming"]) == (["default"], [""], [""])


def test_an_empty_draw_is_published_as_its_description_says():
    """A line since C3 that drew nothing carries `drawn: {}`: published as
    '{}', apart from '' for a trial whose line does not say (the final
    review's M1)."""
    from wl_preproc.nwb.trial_record import COLUMNS, record_columns

    columns, _notes = record_columns(_trials(1, 2), {1: _rig(1, 0, drawn={})}, {})
    assert columns["drawn"] == ["{}", ""]
    assert "'{}'" in COLUMNS["drawn"] and "''" in COLUMNS["drawn"]
