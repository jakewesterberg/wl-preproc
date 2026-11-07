"""Where each stimulus a trial showed was, and when (design spec
`2026-11-07-rig-record-fields-design.md` section 4)."""

from __future__ import annotations

import math

from wl_preproc.events.rigtrials import Place, RigTrial, Stimulus


def _stimulus(name="target", at=Place(20.0, 20.0, 27.23631347517073, 45.0), **fields):
    return Stimulus(**{"name": name, "kind": "show", "frame": 85, "off": None, "changed": (), "drawn": True,
                       "unresolved": None, "at": at, **fields})


def _rig(number, stimuli):
    return RigTrial(number=number, index=0, outcome="correct", block="b", condition="c", params={},
                    stimuli=stimuli, carries=frozenset({"stimuli"}))


def test_one_row_per_entry_and_one_per_array_object():
    """Frames as frames (-1 for none), positions as numbers (NaN for none),
    an array's objects in their own rows, a per-eye array's by eye."""
    from wl_preproc.nwb.stimuli import stimulus_rows

    fix = _stimulus("fix", at=Place(0.0, 0.0, 0.0, None), frame=1, off=61)
    array = _stimulus("search", at=Place(0.0, 0.0, 0.0, None), target=1,
                      objects=(Place(8.0, 0.0, 8.0, 0.0), Place(-8.0, 0.0, 8.0, 180.0)))
    per_eye = _stimulus("dot", at=Place(), left={"x": 1.0}, eye_objects=(("left", (Place(2.0, 0.0, 2.0, 0.0),)),))
    blank = _stimulus("catch", drawn=False, kind="update", changed=("at", "opacity"))
    matched = {3: _rig(3, (fix, array)), 4: _rig(4, (per_eye, blank)), 5: _rig(5, None)}
    entries, objects, notes = stimulus_rows([{"trial_id": number} for number in (3, 4, 5, 6)], matched)
    assert [(row["trial_id"], row["entry"], row["stimulus"]) for row in entries] == [
        (3, 0, "fix"), (3, 1, "search"), (4, 0, "dot"), (4, 1, "catch")]
    assert (entries[0]["frame"], entries[0]["off"], entries[1]["off"]) == (1, 61, -1)
    assert math.isnan(entries[0]["polar_deg"]) and entries[0]["eccentricity_deg"] == 0.0
    assert (entries[1]["target"], entries[1]["n_objects"], entries[0]["target"]) == (1, 2, -1)
    assert math.isnan(entries[2]["x_deg"]) and entries[2]["left"] == '{"x": 1.0}' and entries[2]["right"] == ""
    assert (entries[3]["drawn"], entries[3]["kind"], entries[3]["changed"]) == (0, "update", "at,opacity")
    assert [(row["trial_id"], row["entry"], row["eye"], row["object"], row["x_deg"]) for row in objects] == [
        (3, 1, "", 0, 8.0), (3, 1, "", 1, -8.0), (4, 0, "left", 0, 2.0)]
    assert notes == []


def test_a_position_whose_angles_are_not_its_x_and_ys_is_noted_never_corrected():
    """One formula, two implementations: the record's eccentricity and polar
    angle are checked against `eye/detect/geometry.py`, past 1e-9 degrees. A
    polar angle a hair under 360 agrees with 0."""
    from wl_preproc.nwb.stimuli import stimulus_rows

    right = _stimulus("right", at=Place(8.0, -1e-13, 8.0, 0.0))
    wrong_size = _stimulus("off", at=Place(20.0, 20.0, 28.28, 45.0))
    wrong_angle = _stimulus("turned", at=Place(20.0, 20.0, 27.23631347517073, 44.0))
    centre = _stimulus("centre", at=Place(0.0, 0.0, 0.0, 90.0))
    unplaced = _stimulus("nowhere", at=Place(None, None, None, None), unresolved="setting 'x' has no value")
    matched = {1: _rig(1, (right, wrong_size)), 2: _rig(2, (wrong_angle, centre, unplaced))}
    entries, _objects, notes = stimulus_rows([{"trial_id": 1}, {"trial_id": 2}], matched)
    assert entries[1]["eccentricity_deg"] == 28.28
    assert notes == ["3 stimulus entries give an eccentricity or polar angle that is not its x and y's, past 1e-09 "
                     "degrees; published as written (trial 1 entry 1, trial 2 entry 0, trial 2 entry 1)"]


def test_a_per_eye_arrays_objects_are_counted_and_each_eyes_place_checked():
    """A stimulus drawn by per-eye positions counts both eyes' objects, as
    `stimulus_objects` lists them, and each eye's own position is checked
    against our geometry as a cyclopean one is (the rig record fields' final
    review, M4)."""
    from wl_preproc.nwb.stimuli import stimulus_rows

    dot = Place(2.0, 0.0, 2.0, 0.0)
    per_eye = _stimulus("dot", at=Place(), left={"x": 1.0}, right={"x": -1.0},
                        eye_at=(("left", Place(1.0, 0.0, 1.0, 0.0)), ("right", Place(-1.0, 0.0, 1.0, 90.0))),
                        eye_objects=(("left", (dot, dot)), ("right", (dot,))))
    entries, objects, notes = stimulus_rows([{"trial_id": 4}], {4: _rig(4, (per_eye,))})
    assert entries[0]["n_objects"] == len(objects) == 3
    assert notes == ["1 stimulus entry give an eccentricity or polar angle that is not its x and y's, past 1e-09 "
                     "degrees; published as written (trial 4 entry 0)"]
