"""A registered detector's libraries missing from this host, caught before a
pass (the U'n'Eye review's minor 3). Without the check its jobs error
quietly, and every NWB waits on them for ever (`nwb/gather.py::readiness`)."""

from __future__ import annotations

import types

import pytest

_WHY = "its code does not import here (ModuleNotFoundError: No module named 'torch')"
_REPORT = {"populated": 0, "stale_jobs_reaped": 0, "freed_skipped": 0, "archived": None, "nwb": None,
           "nwb_published": None, "nwb_moved": None, "nwb_corrected": None, "errors": []}


def test_the_daemon_refuses_a_pass_while_a_detectors_libraries_are_missing(monkeypatch, capsys):
    from wl_preproc import daemon
    from wl_preproc.cli.main import main
    from wl_preproc.eye.detect import registry

    passes = []
    monkeypatch.setattr(daemon, "run_once", lambda **kwargs: passes.append(kwargs) or _REPORT)
    monkeypatch.setattr(registry, "unavailable_detectors", lambda: {"uneye": _WHY})
    assert main(["daemon"]) == 1
    out = capsys.readouterr().out
    assert passes == []
    assert f"refusing to run: uneye: {_WHY}" in out


def test_the_daemon_runs_its_pass_when_nothing_is_missing(monkeypatch, capsys):
    from wl_preproc import daemon
    from wl_preproc.cli.main import main
    from wl_preproc.eye.detect import registry

    passes = []
    monkeypatch.setattr(daemon, "run_once", lambda **kwargs: passes.append(kwargs) or _REPORT)
    monkeypatch.setattr(registry, "unavailable_detectors", lambda: {})
    assert main(["daemon"]) == 0
    assert len(passes) == 1
    assert "populated: 0" in capsys.readouterr().out


@pytest.fixture
def offline(monkeypatch):
    """Doctor's two database steps, stood in for: these tests are about the
    detector-libraries check, and must open no connection (the U'n'Eye-minors
    review's deferred minor). Each test asserts the stand-ins' lines, so a
    real connection, or its failure, cannot pass for them."""
    import datajoint

    from wl_preproc import daemon

    monkeypatch.setattr(datajoint, "conn", lambda reset=False: types.SimpleNamespace(is_connected=True))
    monkeypatch.setattr(daemon, "count_stale_jobs", lambda: 0)


def _offline_lines(out: str) -> bool:
    return "[ok] database" in out and "[ok] stale jobs: 0 stale reservation(s) found" in out


def test_doctor_fails_the_detector_libraries_check_while_one_is_missing(monkeypatch, capsys, offline):
    from wl_preproc.cli.doctor import run_checks
    from wl_preproc.eye.detect import registry

    monkeypatch.setattr(registry, "unavailable_detectors", lambda: {"uneye": _WHY})
    assert "detector libraries" in run_checks()
    out = capsys.readouterr().out
    assert f"[FAIL] detector libraries: uneye: {_WHY}" in out
    assert _offline_lines(out)


def test_doctor_passes_the_detector_libraries_check_when_all_are_present(monkeypatch, capsys, offline):
    from wl_preproc.cli.doctor import run_checks
    from wl_preproc.eye.detect import registry

    monkeypatch.setattr(registry, "unavailable_detectors", lambda: {})
    assert "detector libraries" not in run_checks()
    out = capsys.readouterr().out
    assert "[ok] detector libraries" in out
    assert _offline_lines(out)


def test_doctor_reports_a_check_that_cannot_run_rather_than_raising(monkeypatch, capsys, offline):
    """As its database and stale-jobs checks do."""
    from wl_preproc.cli.doctor import run_checks
    from wl_preproc.eye.detect import registry

    def broken():
        raise RuntimeError("the registry would not load")

    monkeypatch.setattr(registry, "unavailable_detectors", broken)
    assert "detector libraries" in run_checks()
    out = capsys.readouterr().out
    assert "[FAIL] detector libraries: the registry would not load" in out
    assert _offline_lines(out)
