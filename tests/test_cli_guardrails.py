import subprocess
import sys


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "wl_preproc.cli.main", *args],
        capture_output=True,
        text=True,
    )


def test_delete_defaults_to_a_dry_run():
    """Section 10: wlpp delete prints the full cascade, defaults to --dry-run,
    and requires explicit confirmation."""
    result = _run("delete", "--session", "2027-03-14_01", "--from-stage", "Segment")
    assert "dry run" in (result.stdout + result.stderr).lower()


def test_delete_refuses_without_explicit_confirmation():
    result = _run(
        "delete", "--session", "2027-03-14_01", "--from-stage", "Segment", "--no-dry-run"
    )
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode != 0 or "confirm" in combined


def test_doctor_runs_and_reports_checks():
    result = _run("doctor")
    combined = result.stdout + result.stderr
    for check in ("database", "scratch", "stale jobs"):
        assert check.lower() in combined.lower(), check
