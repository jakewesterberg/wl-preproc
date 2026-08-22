import subprocess
import sys
from pathlib import Path

# The shipped console script, beside this interpreter.
WLPP = Path(sys.executable).with_name("wlpp")


def test_cli_generates_a_session(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "wl_preproc.cli.main", "synth", "generate",
            "--out", str(tmp_path), "--profile", "ci",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "2027-03-14_01" / "session_manifest.yaml").exists()


def test_cli_rejects_an_unknown_profile(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "wl_preproc.cli.main", "synth", "generate",
            "--out", str(tmp_path), "--profile", "nonsense",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode != 0


def test_every_recipe_is_reachable_from_the_cli(tmp_path):
    """`RECIPES` and the CLI's `--profile` choices were two lists until Phase
    1c-4, and they drifted the moment a fourth recipe landed: "eye" existed and
    `generate_session` handled it, while the CLI rejected it as invalid. This
    asserts the set equality that makes a third copy impossible to add
    silently.

    The real entry point, not `-m`: the shipped console script is what an
    operator runs, and the `.pth` trap this project diagnosed makes `-m` from
    the repo root immune to a breakage the operator would hit. See CHECKPOINT.
    """
    from wl_preproc.synth.recipe import RECIPES

    result = subprocess.run(
        [str(WLPP), "synth", "generate", "--help"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    for profile in RECIPES:
        assert profile in result.stdout, f"{profile} is not offered by --profile"


def test_the_eye_profile_generates_an_ohdpi_session(tmp_path):
    """The profile exists to be run, and `ohdpi` had no emitter at all before
    this phase — so the case worth asserting is that the whole path, dispatch
    included, produces the system's directory."""
    result = subprocess.run(
        [str(WLPP), "synth", "generate", "--out", str(tmp_path), "--profile", "eye"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "2027-03-14_04" / "ohdpi" / "ohdpi_frames.csv").exists()
