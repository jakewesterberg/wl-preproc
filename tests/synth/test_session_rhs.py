# tests/synth/test_session_rhs.py
import subprocess
import sys

import numpy as np
from wl_sync.session import SessionId

from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.synth.recipe import STIM_RECIPE
from wl_preproc.synth.session import generate_session
from wl_preproc.synth.stim import unpack_stim_word


def test_rhs_directory_is_written_and_marked_done(tmp_path):
    generate_session(tmp_path, STIM_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(STIM_RECIPE.session_id))
    assert layout.system_dir("rhs").is_dir()
    assert layout.done_marker("rhs").exists()


def test_standalone_intan_session_has_no_spikeglx(tmp_path):
    """Tier B provenance: Pi codes plus an Intan strobe witness, no NI at all
    (spec section 4.7). This is the fixture that case has never had."""
    generate_session(tmp_path, STIM_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(STIM_RECIPE.session_id))
    assert not layout.system_dir("spikeglx").exists()
    assert layout.system_dir("syncbox").is_dir()


def test_stim_words_survive_assembly(tmp_path):
    truth = generate_session(tmp_path, STIM_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(STIM_RECIPE.session_id))
    out = next(layout.system_dir("rhs").glob("*_rhs"))
    stim = np.fromfile(out / "stim.dat", dtype=np.uint16).reshape(
        -1, STIM_RECIPE.n_ap_channels
    )
    assert any(unpack_stim_word(int(w)).amp_settle for w in stim.flatten())
    assert truth.stim_events


def test_cli_generates_the_stim_profile(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "wl_preproc.cli.main", "synth", "generate",
            "--out", str(tmp_path), "--profile", "stim",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / STIM_RECIPE.session_id / "rhs").is_dir()
