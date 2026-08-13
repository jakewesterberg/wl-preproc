import subprocess
import sys


def test_cli_generates_a_session(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "wl_preproc.cli.main", "synth", "generate",
            "--out", str(tmp_path), "--profile", "ci",
        ],
        capture_output=True,
        text=True,
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
    )
    assert result.returncode != 0
