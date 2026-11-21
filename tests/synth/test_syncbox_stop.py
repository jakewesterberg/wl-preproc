"""A sync box that stops partway, as a Pi that lost power does: no device
carries a barcode after the stop, and the box's log holds the records before
it and no trailer (part 3b-3, constraints decision 4)."""

import pytest
from pydantic import ValidationError
from wl_sync.log import read_log

from wl_preproc.synth.recipe import CI_RECIPE, RECIPES, SessionRecipe
from wl_preproc.synth.session import generate_session
from wl_preproc.synth.syncbox import SYNCBOX_PRE_ROLL_S

STOP_S = 9.0
SYSTEMS = ("syncbox", "spikeglx", "ohdpi", "bcam")


def _stopped(tmp_path, stop_s=STOP_S):
    recipe = CI_RECIPE.model_copy(update={"systems": SYSTEMS, "syncbox_stop_s": stop_s})
    truth = generate_session(tmp_path, recipe)
    (log,) = sorted((tmp_path / recipe.session_id / "syncbox").glob("*.log"))
    return truth, log


def test_no_device_carries_a_barcode_after_the_stop(tmp_path):
    truth, _ = _stopped(tmp_path)
    assert truth.barcodes
    assert all(start_s < STOP_S for _, start_s in truth.barcodes)


def test_the_log_holds_nothing_after_the_stop_and_no_trailer(tmp_path):
    _, log = _stopped(tmp_path)
    _, records = read_log(log)
    assert records
    stop_tick_us = round((STOP_S + SYNCBOX_PRE_ROLL_S) * 1e6)
    assert all(record.tick_us < stop_tick_us for record in records)
    assert not log.read_text().splitlines()[-1].startswith("T,")


def test_a_whole_session_log_still_ends_in_its_trailer(tmp_path):
    truth = generate_session(tmp_path, CI_RECIPE)
    (log,) = sorted((tmp_path / CI_RECIPE.session_id / "syncbox").glob("*.log"))
    assert log.read_text().splitlines()[-1].startswith("T,")
    assert truth.barcodes[-1][1] > STOP_S


def test_no_stop_leaves_every_recipe_unchanged():
    assert all(recipe.syncbox_stop_s is None for recipe in RECIPES.values())


@pytest.mark.parametrize(("update", "match"), [
    ({"systems": ("spikeglx",), "syncbox_stop_s": 9.0}, "syncbox_stop_s needs the sync box in systems"),
    ({"syncbox_stop_s": 0.0}, "syncbox_stop_s 0.0 s lies outside the session"),
    ({"syncbox_stop_s": 15.0}, "syncbox_stop_s 15.0 s lies outside the session"),
    ({"syncbox_stop_s": 9.0, "syncbox_restart": {"at_s": 3.0}}, "syncbox_stop_s and syncbox_restart"),
    ({"syncbox_stop_s": 9.0, "syncbox_roll": {"at_s": 3.0}}, "syncbox_stop_s and syncbox_roll"),
])
def test_a_stop_needs_a_sync_box_and_lies_inside_the_session(update, match):
    with pytest.raises(ValidationError, match=match):
        SessionRecipe.model_validate({**CI_RECIPE.model_dump(), **update})
