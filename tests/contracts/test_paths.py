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


# --- The experiment controller's log directory ------------------------------


def test_the_expcontroller_directory_is_named_for_the_role_not_the_vendor():
    """MonkeyLogic writes a `.bhv2` here today and `wl-expcontroller` will
    write whatever it writes. A directory called `monkeylogic/` would need
    renaming the day the second controller lands, and every path already
    written against it would be wrong -- the same role/format split
    `CalibrationSource.ONLINE` draws against `eye/bhv2.py`."""
    from wl_preproc.contracts.paths import EXPCONTROLLER_DIRNAME

    assert EXPCONTROLLER_DIRNAME == "expcontroller"
    assert "monkeylogic" not in EXPCONTROLLER_DIRNAME


def test_the_expcontroller_directory_is_not_an_acquisition_system():
    """A real distinction, not a naming preference.

    `SYSTEMS` members are acquisition systems: `ingest/discover.py` expects a
    `DONE` marker under each, `core.AcquisitionSystem` records one row per
    member, and `timebase/extract.py` asserts `set(EXTRACTORS) == set(SYSTEMS)`
    as its completeness claim. An experiment controller's log carries no
    barcode and needs no alignment, so listing it there would demand an
    extractor that cannot exist and break that assertion.
    """
    from wl_preproc.contracts.paths import EXPCONTROLLER_DIRNAME, SYSTEMS

    assert EXPCONTROLLER_DIRNAME not in SYSTEMS


def test_the_expcontroller_directory_sits_beside_the_system_directories():
    from wl_preproc.contracts.paths import EXPCONTROLLER_DIRNAME

    session = layout()

    assert session.expcontroller_dir == session.dir / EXPCONTROLLER_DIRNAME
    assert session.expcontroller_dir.parent == session.dir
    # Reached by its own property, never through `system_dir`, which validates
    # against SYSTEMS and correctly refuses this name.
    with pytest.raises(ValueError, match="unknown system"):
        session.system_dir(EXPCONTROLLER_DIRNAME)
