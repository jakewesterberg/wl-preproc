"""An Intan recording placed on the NI's clock by the rig's strobe line
(part 3b-2, amendment 49): the reading layer's pairs against the simulation's
truth."""

from __future__ import annotations

import pytest

from tests.timebase.intan_helpers import _clear_intan_strobes
from wl_preproc.synth.recipe import RECIPES
from wl_preproc.synth.rhs import RHS_PRE_ROLL_S
from wl_preproc.synth.session import generate_session
from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S
from wl_preproc.timebase._nidq_meta import read_nidq_meta
from wl_preproc.timebase._rhs_header import INFO_FILENAME, read_sample_rate_hz
from wl_preproc.timebase.clock import nidq_files
from wl_preproc.timebase.extract import find_recordings
from wl_preproc.timebase.intan import place_intan
from wl_preproc.timebase.strobes import Unanchored


@pytest.fixture()
def ni_intan(tmp_path):
    recipe = RECIPES["ni-intan"]
    truth = generate_session(tmp_path, recipe)
    session_dir = tmp_path / recipe.session_id
    (recording_dir,) = find_recordings("rhs", session_dir / "rhs")
    return recipe, truth, session_dir, recording_dir


def test_an_intan_recording_is_placed_on_the_ni_by_its_strobes(ni_intan):
    recipe, truth, session_dir, recording_dir = ni_intan
    placement = place_intan(session_dir, recording_dir)
    ni_fs = read_nidq_meta(nidq_files(session_dir)[0].with_suffix(".meta")).sample_rate_hz
    rhs_fs = read_sample_rate_hz(recording_dir / INFO_FILENAME)
    # Every code word is one strobe on each side, so each pair is one word.
    assert len(placement.pairs) == len(truth.code_words)
    ni_true = [t * (1 + 18e-6) + SPIKEGLX_PRE_ROLL_S for t, _ in truth.code_words]
    rhs_true = [t * (1 - 40e-6) + RHS_PRE_ROLL_S for t, _ in truth.code_words]
    for (session_s, device_s), ni_s, rhs_s in zip(placement.pairs, ni_true, rhs_true, strict=True):
        assert session_s == pytest.approx(ni_s, abs=1 / ni_fs + 1e-9)
        assert device_s == pytest.approx(rhs_s, abs=1 / rhs_fs + 1e-9)
    assert placement.anchor_fraction >= 0.95


def test_an_intan_with_no_strobe_refuses(ni_intan):
    recipe, truth, session_dir, recording_dir = ni_intan
    _clear_intan_strobes(recording_dir)
    with pytest.raises(Unanchored, match="Intan recorded 0 strobe edges"):
        place_intan(session_dir, recording_dir)
