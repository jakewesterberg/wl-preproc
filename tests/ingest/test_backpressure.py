"""Backpressure at ingest (design spec section 8.4): refuse new sessions when
scratch is too tight to safely take on another, rather than admitting one and
stalling mid-sort hours later.

`refuses_new_sessions` reuses `doctor.scratch_headroom()` rather than owning a
second threshold -- see that function's own docstring, and
`responder/health.py::_featured_key`'s, for why a second definition of "is
scratch low" is a defect this project has already found in four separate
shapes.
"""

from __future__ import annotations

from unittest.mock import patch

from wl_preproc.contracts.paths import MANIFEST_FILENAME
from wl_preproc.ingest import watcher


def test_new_sessions_are_refused_when_headroom_is_low(tmp_path):
    """Design spec section 8.4: refuse rather than fill scratch and stall
    mid-sort."""
    with patch(
        "wl_preproc.ingest.watcher.scratch_headroom", return_value=(3, False)
    ) as mock_headroom:
        assert watcher.refuses_new_sessions(tmp_path) is True
    # Ruling A: the root must actually reach `scratch_headroom`, not be
    # dropped in favour of its own "/" default -- a mock returns the same
    # tuple regardless of its arguments, so only checking the call itself,
    # not just the return value, proves the root was passed through.
    mock_headroom.assert_called_once_with(str(tmp_path))


def test_new_sessions_are_accepted_when_headroom_is_fine(tmp_path):
    with patch(
        "wl_preproc.ingest.watcher.scratch_headroom", return_value=(4000, True)
    ) as mock_headroom:
        assert watcher.refuses_new_sessions(tmp_path) is False
    mock_headroom.assert_called_once_with(str(tmp_path))


def test_the_threshold_is_not_redefined_here():
    """A second definition of "is scratch low" is the drift `_featured_key`'s
    docstring warns about. This module must own no threshold of its own."""
    source = (watcher.__file__ and open(watcher.__file__).read()) or ""
    assert "_MIN_SCRATCH_FREE_GIB" not in source
    assert "high_water" not in source


def test_a_second_threshold_would_be_caught_even_if_differently_named(tmp_path):
    """The source scan above only catches a duplicate threshold that reuses
    one of two specific names; a third name would slip past it unnoticed.
    This instead pins the BEHAVIOUR: `free_gib=1` paired with
    `headroom_ok=True` can never come from the real `scratch_headroom`
    (whose own floor is 800 GiB) -- it is deliberately contradictory, chosen
    so that any second, independent free_gib comparison, however named,
    would flip this result to True instead of the correct False. Only
    `headroom_ok` may decide this."""
    with patch("wl_preproc.ingest.watcher.scratch_headroom", return_value=(1, True)):
        assert watcher.refuses_new_sessions(tmp_path) is False


def test_an_unmeasurable_disk_refuses_rather_than_raising(tmp_path):
    """Ruling B: `scratch_headroom` raises whatever `os.statvfs` raises for a
    missing or unsearchable path. Unhandled, that would escape straight out
    of `scan_once` and abort the whole scan -- the exact blast radius
    `_scan_one`'s boundary exists to prevent, arriving through a door that
    boundary does not cover. Fail closed instead: an unmeasurable scratch is
    no evidence of headroom."""
    with patch(
        "wl_preproc.ingest.watcher.scratch_headroom",
        side_effect=OSError("simulated statvfs failure"),
    ):
        assert watcher.refuses_new_sessions(tmp_path) is True


def test_scan_once_refuses_every_candidate_without_evaluating_it(tmp_path, monkeypatch):
    """Wiring test: `refuses_new_sessions` must actually gate `scan_once`'s
    admission of a session, not merely exist unused beside it. Two
    independent signals would catch a wrong implementation that still
    evaluates the candidate: `_scan_one` is patched to raise `AssertionError`
    if it runs at all, and the candidate's manifest is deliberately garbage,
    so if `_scan_one` ran anyway it would quarantine as `manifest_invalid`
    rather than land as `REFUSED` -- either signal alone would fail this
    test."""
    candidate = tmp_path / "would-be-session"
    candidate.mkdir()
    (candidate / MANIFEST_FILENAME).write_bytes(b"not a real manifest")

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("_scan_one must not run once refuses_new_sessions is True")

    monkeypatch.setattr(watcher, "_scan_one", _must_not_run)

    with patch("wl_preproc.ingest.watcher.scratch_headroom", return_value=(3, False)):
        result = watcher.scan_once(tmp_path)

    resolved_candidate = tmp_path.resolve() / candidate.name
    assert result.outcomes == {str(resolved_candidate): watcher.Outcome.REFUSED}
    assert result.root_error is None


def test_root_error_still_surfaces_when_refusing(tmp_path, monkeypatch):
    """Ruling C: `refuses_new_sessions` overrides only the per-candidate
    outcomes; `root_error` must come through unchanged, exactly as
    `_candidate_dirs` computed it -- not swallowed just because every
    candidate is being marked REFUSED instead of scanned. `_candidate_dirs`
    is monkeypatched directly to produce a candidate and a fault together --
    the same technique `tests/ingest/test_watcher.py::
    test_cli_ingest_still_prints_outcomes_found_before_a_root_fault` uses one
    level up, on `scan_once` itself, for the identical reason that test's own
    docstring gives: a real mid-walk fault (`_candidate_dirs`' own docstring:
    `root.iterdir()`'s `next()` can raise after already yielding some
    children) is not reliably constructible on a real filesystem. This
    proves `scan_once`'s own handling of that combination, in isolation from
    `_candidate_dirs`'s real mechanics."""
    candidate = tmp_path / "would-be-session"
    candidate.mkdir()
    (candidate / MANIFEST_FILENAME).write_bytes(b"not a real manifest")

    def fake_candidate_dirs(root):
        return [candidate], OSError("simulated mid-walk fault")

    monkeypatch.setattr(watcher, "_candidate_dirs", fake_candidate_dirs)

    with patch("wl_preproc.ingest.watcher.scratch_headroom", return_value=(3, False)):
        result = watcher.scan_once(tmp_path)

    assert result.outcomes == {str(candidate): watcher.Outcome.REFUSED}
    assert result.root_error == "OSError: simulated mid-walk fault"


def test_cli_ingest_exits_non_zero_when_refused(monkeypatch, capsys):
    """Ruling C: a refused scan must not exit 0 as if it had ingested
    cleanly. `main.py`'s exit-code logic previously looked only at
    `root_error`, so a scan that refused every candidate but hit no walk
    fault used to exit 0, identically to a genuinely clean scan. Patches
    `wl_preproc.ingest.watcher.scan_once` specifically, matching
    `tests/ingest/test_watcher.py::
    test_cli_ingest_still_prints_outcomes_found_before_a_root_fault`'s own
    documented reason: `main()`'s `from wl_preproc.ingest.watcher import ...`
    is a local import inside the `ingest` branch, re-resolved from the
    module's own namespace every time that branch runs."""
    from wl_preproc.cli.main import main

    def fake_scan_once(root, prefix, verify):
        return watcher.ScanResult(
            outcomes={"/scratch/2027-03-14_01": watcher.Outcome.REFUSED},
            root_error=None,
        )

    monkeypatch.setattr(watcher, "scan_once", fake_scan_once)

    exit_code = main(["ingest", "--root", "/scratch"])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "refused" in captured.out
