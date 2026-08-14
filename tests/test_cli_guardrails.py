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
    """`returncode != 0 or "confirm" in combined` was the assertion here until
    2026-08-14, which any crash satisfies — an ImportError, a traceback, a
    missing subcommand. The exit code is pinned exactly (2, the refusal, not 1
    and not an interpreter error) and the message is required alongside it, so
    a broken CLI cannot pass as a careful one."""
    result = _run(
        "delete", "--session", "2027-03-14_01", "--from-stage", "Segment", "--no-dry-run"
    )
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 2, f"expected the refusal exit code 2, got {result.returncode}"
    assert "confirm" in combined
    assert "traceback" not in combined


def test_delete_accepts_a_matching_confirmation():
    """The accept path, absent entirely until 2026-08-14. A guard is only shown
    to be a guard if the thing it guards can also be reached: without this, a
    `delete` that refused unconditionally — or crashed on every invocation —
    would pass the refusal test above."""
    result = _run(
        "delete",
        "--session",
        "2027-03-14_01",
        "--from-stage",
        "Segment",
        "--no-dry-run",
        "--confirm",
        "2027-03-14_01",
    )
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 0, f"expected success, got {result.returncode}: {combined}"
    assert "refusing" not in combined
    # This build still never performs a real delete (design spec section 10);
    # the accepted path prints the cascade and says so.
    assert "preview-only" in combined


def test_doctor_runs_and_reports_checks():
    result = _run("doctor")
    combined = result.stdout + result.stderr
    for check in ("database", "scratch", "stale jobs"):
        assert check.lower() in combined.lower(), check
