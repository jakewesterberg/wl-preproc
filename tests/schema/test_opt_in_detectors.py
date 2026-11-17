"""BMD is off by default (scaling design spec section 5, amendment 6): `registry.running_detectors()` is what runs and
what the NWB file reads; BMD runs when the daemon is asked for it; a paramset registered for BMD before this change
computes nothing unasked."""

from __future__ import annotations

import pytest

from tests.identities import new_animal


@pytest.fixture
def default_set():
    """The detectors as a daemon started without `--with-detector` has them. The suite as a whole asks for BMD
    (`tests/conftest.py`), so this clears that request and puts it back."""
    from wl_preproc.eye.detect import registry

    held = registry.requested()
    registry.clear_requests()
    try:
        yield registry
    finally:
        registry.clear_requests()
        registry.request(held)


def test_the_default_set_leaves_bmd_out(default_set):
    assert "bmd" in default_set.DETECTORS
    assert "bmd" not in default_set.running_detectors()
    assert set(default_set.running_detectors()) == set(default_set.DETECTORS) - {"bmd"}
    assert list(default_set.running_detectors()) == [name for name in default_set.DETECTORS if name != "bmd"]


def test_requesting_bmd_runs_it(default_set):
    default_set.request(["bmd"])
    assert set(default_set.running_detectors()) == set(default_set.DETECTORS)


def test_an_unknown_request_is_refused(default_set):
    with pytest.raises(ValueError, match="nslr"):
        default_set.request(["nslr"])  # registered, but on by default: nothing to request
    with pytest.raises(ValueError, match="nonesuch"):
        default_set.request(["nonesuch"])


def test_daemon_takes_with_detector(default_set, monkeypatch, capsys):
    """`wlpp daemon --with-detector bmd` asks for BMD before the pass, and before the check that every detector
    that runs can run here; a detector on by default cannot be asked for."""
    from wl_preproc import daemon
    from wl_preproc.cli.main import main

    seen = {}

    def fake_run_once(**_kwargs):
        seen["running"] = set(default_set.running_detectors())
        return {"populated": 0, "stale_jobs_reaped": 0, "freed_skipped": 0, "archived": None, "nwb": None,
                "nwb_published": None, "nwb_moved": None, "nwb_corrected": None, "errors": []}

    monkeypatch.setattr(daemon, "run_once", fake_run_once)
    monkeypatch.setattr(default_set, "unavailable_detectors", lambda: {})
    assert main(["daemon", "--with-detector", "bmd"]) == 0
    assert "bmd" in seen["running"]
    default_set.clear_requests()
    assert main(["daemon"]) == 0
    assert "bmd" not in seen["running"]
    assert main(["daemon", "--with-detector", "nslr"]) == 2


def test_an_unrequested_detector_does_not_stop_the_daemon(default_set, monkeypatch):
    """Only the detectors that run are checked for their libraries: BMD's missing on a host that never asked for it
    refuses nothing."""
    import dataclasses

    from wl_preproc.eye.detect.registry import DETECTORS

    missing = dataclasses.replace(DETECTORS["bmd"], unavailable=lambda: "BMD's libraries are missing")
    monkeypatch.setitem(DETECTORS, "bmd", missing)
    assert "bmd" not in default_set.unavailable_detectors()
    default_set.request(["bmd"])
    assert default_set.unavailable_detectors() == {"bmd": "BMD's libraries are missing"}


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


def test_an_old_bmd_paramset_is_not_run_unasked(default_set, daemon_module, prefix, tmp_path_factory):
    """A database that registered BMD's paramset before this change keeps its rows and computes no more: no
    `EyeDetection` key with it, `DetectionQuality` blends the default set, and the NWB file neither waits for nor
    reads it."""
    from tests.schema.test_detect_populate import _build_stepped_session
    from wl_preproc.nwb import gather
    from wl_preproc.schema import consensus, detect, paramset

    bmd_idx = paramset.register("eye_detection", detect._eye_detection_params(default_set.DETECTORS["bmd"]))
    animal = new_animal().session()
    session_key, _segment, _onsets = _build_stepped_session(
        tmp_path_factory, dirname="optin", session_id=animal.session_id, subject=animal.subject,
        session_datetime=animal.session_datetime, seed=1409)
    daemon_module.run_once(prefix=prefix)

    keys = (detect.EyeDetection().key_source & session_key).to_dicts()
    assert keys and bmd_idx not in {key["paramset_idx"] for key in keys}
    assert not (detect.EyeDetection & session_key & {"paramset_idx": bmd_idx})
    computed = {row["paramset_idx"] for row in (detect.EyeDetection & session_key).to_dicts()}
    assert computed == set(detect.register_default_paramsets().values())
    assert "bmd" not in consensus._live_detectors().values()
    _validity, detection_idx = gather._paramsets()
    assert "bmd" not in detection_idx and bmd_idx not in detection_idx.values()
