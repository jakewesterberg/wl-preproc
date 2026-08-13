from pathlib import Path

import pytest
from wl_sync.session import SessionId

from wl_preproc.contracts.paths import SYSTEMS, SessionLayout


def layout():
    return SessionLayout(Path("/scratch"), SessionId.parse("2027-03-14_01"))


def test_session_dir():
    assert layout().dir == Path("/scratch/2027-03-14_01")


def test_manifest_path():
    assert layout().manifest_path == Path("/scratch/2027-03-14_01/session_manifest.yaml")


def test_system_dir():
    assert layout().system_dir("spikeglx") == Path("/scratch/2027-03-14_01/spikeglx")


def test_done_marker():
    assert layout().done_marker("spikeglx") == Path("/scratch/2027-03-14_01/spikeglx/DONE")


def test_unknown_system_rejected():
    with pytest.raises(ValueError):
        layout().system_dir("telepathy")


def test_systems_are_the_spec_five():
    assert SYSTEMS == ("syncbox", "spikeglx", "rhs", "ohdpi", "bcam")
