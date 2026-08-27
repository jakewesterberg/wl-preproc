import numpy as np
import pytest

from wl_preproc.archive.layout import LayoutUndetermined, bulk_streams
from wl_preproc.synth.recipe import CI_RECIPE, STIM_RECIPE
from wl_preproc.synth.session import generate_session


def test_spikeglx_streams_are_found_with_their_real_shape(tmp_path):
    """The layout must come from the recording's own sidecar, not a guess:
    reconstruction in Task 3 is exactly this shape read back."""
    generate_session(tmp_path, CI_RECIPE)
    session = tmp_path / CI_RECIPE.session_id
    found = {s.path.name: s for s in bulk_streams(session)}

    ap = found[f"{CI_RECIPE.session_id}_imec0.ap.bin"]
    assert ap.dtype == np.dtype("<i2")
    assert ap.n_channels == CI_RECIPE.n_ap_channels + 1  # + the SY channel
    assert ap.n_samples * ap.n_channels * 2 == ap.path.stat().st_size


def test_the_lf_stream_is_found_too(tmp_path):
    generate_session(tmp_path, CI_RECIPE)
    session = tmp_path / CI_RECIPE.session_id
    names = {s.path.name for s in bulk_streams(session)}
    assert f"{CI_RECIPE.session_id}_imec0.lf.bin" in names


def test_rhs_amplifier_shape_is_derived_from_time_dat(tmp_path):
    """info.rhs has no reader in this repo, and needs none: time.dat is int32,
    one entry per sample, so the channel count falls out of the two sizes."""
    generate_session(tmp_path, STIM_RECIPE)
    session = tmp_path / STIM_RECIPE.session_id
    amp = next(s for s in bulk_streams(session) if s.path.name == "amplifier.dat")
    assert amp.dtype == np.dtype("<i2")
    assert amp.n_channels == STIM_RECIPE.n_ap_channels
    assert amp.n_samples * amp.n_channels * 2 == amp.path.stat().st_size


def test_a_size_that_does_not_divide_is_refused(tmp_path):
    """A wrong channel count that still 'works' is how a silent reconstruction
    bug ships. Refuse before an artifact is ever written."""
    generate_session(tmp_path, CI_RECIPE)
    session = tmp_path / CI_RECIPE.session_id
    ap = next(session.rglob("*_imec0.ap.bin"))
    with ap.open("ab") as fh:
        fh.write(b"\x00")  # one stray byte: size no longer divides
    with pytest.raises(LayoutUndetermined):
        bulk_streams(session)


def test_a_time_dat_size_that_does_not_divide_is_refused(tmp_path):
    """Mirrors the .bin sibling above: a time.dat torn off a 4-byte
    sample-index boundary must be refused before its byte count is trusted
    for anything -- the same discipline _checked already applies to
    amplifier.dat itself. (A time.dat truncated exactly ON a 4-byte boundary
    is a known, documented gap -- see layout.py's module docstring -- and is
    not what this test exercises.)"""
    generate_session(tmp_path, STIM_RECIPE)
    session = tmp_path / STIM_RECIPE.session_id
    time_dat = next(session.rglob("time.dat"))
    with time_dat.open("ab") as fh:
        fh.write(b"\x00")  # one stray byte: size no longer divides by 4
    with pytest.raises(LayoutUndetermined):
        bulk_streams(session)


def test_a_bin_with_no_meta_beside_it_is_refused(tmp_path):
    """The brief calls this the single worst failure mode: a bulk stream
    silently omitted from the archive is data nothing downstream would ever
    notice is missing. Pinned explicitly so a future refactor that turns the
    loop's raise into a skip is caught here rather than in production."""
    generate_session(tmp_path, CI_RECIPE)
    session = tmp_path / CI_RECIPE.session_id
    meta = next(session.rglob("*_imec0.ap.meta"))
    meta.unlink()
    with pytest.raises(LayoutUndetermined):
        bulk_streams(session)


def test_a_non_numeric_channel_count_is_refused(tmp_path):
    """This module's whole public promise is a determined shape or a raised
    LayoutUndetermined -- never a bare crash from a value it was never asked
    to interpret as anything but an integer."""
    generate_session(tmp_path, CI_RECIPE)
    session = tmp_path / CI_RECIPE.session_id
    meta = next(session.rglob("*_imec0.ap.meta"))
    lines = [
        "nSavedChans=five" if line.startswith("nSavedChans=") else line
        for line in meta.read_text(encoding="utf-8").splitlines()
    ]
    meta.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(LayoutUndetermined):
        bulk_streams(session)
