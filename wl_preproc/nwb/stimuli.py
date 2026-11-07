"""Where each stimulus a trial showed was, and when, as rows of two tables
(design spec `2026-11-07-rig-record-fields-design.md` section 4; wl-xcon's
XC-399).

Pure functions over plain data. One row per entry of a trial line's
`stimuli`, and one per object of an array. **Frames, not times**: a frame is
matched to the recording only through wl-xcon's screen log (its build F).
**Checked, never corrected**: each position's eccentricity and polar angle are
recomputed from its `x` and `y` by `eye/detect/geometry.py`, and a disagreement
past `AGREE_DEG` is a note. One formula, two implementations, as the saccade
fixtures pin for the detector."""

from __future__ import annotations

import json
import math

import numpy as np

from wl_preproc.events.rigtrials import Place, RigTrial

AGREE_DEG = 1e-9


def _number(value: float | None) -> float:
    return math.nan if value is None else value


def _whole(value: int | None) -> int:
    return -1 if value is None else value


def _numbers(place: Place) -> dict:
    return {"x_deg": _number(place.x), "y_deg": _number(place.y),
            "eccentricity_deg": _number(place.eccentricity), "polar_deg": _number(place.polar)}


def _disagrees(place: Place) -> bool:
    """Whether the record's eccentricity or polar angle is not its `x` and
    `y`'s, past `AGREE_DEG`; a position without both is not checked."""
    from wl_preproc.eye.detect.geometry import eccentricity_polar_deg

    if place.x is None or place.y is None:
        return False
    eccentricity, polar = (float(value) for value in eccentricity_polar_deg(np.array([place.x, place.y])))
    if place.eccentricity is None or abs(place.eccentricity - eccentricity) > AGREE_DEG:
        return True
    if math.isnan(polar) or place.polar is None:
        return math.isnan(polar) != (place.polar is None)
    turn = abs(place.polar - polar) % 360.0
    return min(turn, 360.0 - turn) > AGREE_DEG


def stimulus_rows(trials: list[dict], matched: dict[int, RigTrial]) -> tuple[list[dict], list[dict], list[str]]:
    """The `stimuli` and `stimulus_objects` rows of `trials`' matched lines,
    in trial and entry order, and the notes on positions that disagree."""
    entries, objects, disagreeing = [], [], []
    for trial in trials:
        rig = matched.get(trial["trial_id"])
        if rig is None or rig.stimuli is None:
            continue
        for number, stimulus in enumerate(rig.stimuli):
            key = {"trial_id": trial["trial_id"], "entry": number}
            entries.append({
                **key, "stimulus": stimulus.name or "", "kind": stimulus.kind or "", "frame": _whole(stimulus.frame),
                "off": _whole(stimulus.off), "changed": ",".join(stimulus.changed),
                "drawn": -1 if stimulus.drawn is None else int(stimulus.drawn), "unresolved": stimulus.unresolved or "",
                **_numbers(stimulus.at), "target": _whole(stimulus.target),
                "n_objects": len(stimulus.objects or ()),
                "left": "" if stimulus.left is None else json.dumps(stimulus.left, sort_keys=True),
                "right": "" if stimulus.right is None else json.dumps(stimulus.right, sort_keys=True)})
            places = [stimulus.at]
            for eye, these in (("", stimulus.objects or ()), *stimulus.eye_objects):
                for index, place in enumerate(these):
                    objects.append({**key, "eye": eye, "object": index, **_numbers(place)})
                    places.append(place)
            if any(_disagrees(place) for place in places):
                disagreeing.append(f"trial {trial['trial_id']} entry {number}")
    notes = []
    if disagreeing:
        notes.append(f"{len(disagreeing)} stimulus entr{'y' if len(disagreeing) == 1 else 'ies'} give an eccentricity "
                     f"or polar angle that is not its x and y's, past {AGREE_DEG:g} degrees; published as written "
                     f"({', '.join(disagreeing[:5])}{', ...' if len(disagreeing) > 5 else ''})")
    return entries, objects, notes
