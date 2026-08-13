# tests/synth/test_session_rhs.py
import subprocess
import sys

import numpy as np
from wl_sync.session import SessionId

from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.synth.recipe import STIM_RECIPE
from wl_preproc.synth.rhs import RHS_SAMPLE_RATE_HZ
from wl_preproc.synth.session import generate_session
from wl_preproc.synth.stim import AMP_SETTLE_BIT, SETTLE_DURATION_S


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
    """Counted, not merely present. `any(...)` over the whole file passes if a
    single sample anywhere carries the flag, so an assembler that rendered one
    of the eight planted events — or smeared one flag across the recording —
    looks identical to a correct one."""
    truth = generate_session(tmp_path, STIM_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(STIM_RECIPE.session_id))
    out = next(layout.system_dir("rhs").glob("*_rhs"))
    stim = np.fromfile(out / "stim.dat", dtype=np.uint16).reshape(
        -1, STIM_RECIPE.n_ap_channels
    )
    assert truth.stim_events

    flagged = int(np.count_nonzero((stim & AMP_SETTLE_BIT) != 0))
    settle_samples = int(SETTLE_DURATION_S * RHS_SAMPLE_RATE_HZ)
    assert flagged == len(truth.stim_events) * settle_samples


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
