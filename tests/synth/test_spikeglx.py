import numpy as np
import pytest

from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S, write_spikeglx
from wl_preproc.synth.timeline import build_timeline

spikeinterface = pytest.importorskip("spikeinterface.extractors")


def emit(tmp_path, name="spikeglx"):
    truth = build_timeline(CI_RECIPE)
    directory = tmp_path / name
    directory.mkdir()
    return truth, write_spikeglx(directory, CI_RECIPE, truth), directory


def test_bin_and_meta_are_written(tmp_path):
    _, bin_path, _ = emit(tmp_path)
    assert bin_path.exists()
    assert bin_path.with_suffix(".meta").exists()


def test_bin_size_matches_the_declared_shape(tmp_path):
    _, bin_path, _ = emit(tmp_path)
    expected_samples = int(
        (CI_RECIPE.duration_s + SPIKEGLX_PRE_ROLL_S) * CI_RECIPE.ap_sample_rate_hz
    )
    expected_bytes = expected_samples * (CI_RECIPE.n_ap_channels + 1) * 2
    assert bin_path.stat().st_size == expected_bytes


def test_spikeinterface_can_open_it(tmp_path):
    """The real acceptance criterion: the file is format-correct if the reader
    the pipeline will use can read it."""
    _, _, directory = emit(tmp_path)
    recording = spikeinterface.read_spikeglx(directory, stream_id="imec0.ap")
    assert recording.get_num_channels() == CI_RECIPE.n_ap_channels
    assert recording.get_sampling_frequency() == pytest.approx(CI_RECIPE.ap_sample_rate_hz)
    assert recording.get_total_duration() == pytest.approx(
        CI_RECIPE.duration_s + SPIKEGLX_PRE_ROLL_S, rel=1e-3
    )


def test_the_nidq_word_carries_decodable_barcodes(tmp_path):
    """The NI digital line is how the pipeline aligns this system to session
    time (spec section 4.5), so the barcodes must survive a round trip through
    the emitted word — every value, in order.
    """
    from wl_sync.barcode import decode_edges, edges_from_samples

    from wl_preproc.synth.spikeglx import NIDQ_BARCODE_XD_LINE, NIDQ_SAMPLE_RATE_HZ

    truth, bin_path, _ = emit(tmp_path)
    word = np.fromfile(bin_path.parent / f"{CI_RECIPE.session_id}.nidq.bin", dtype=np.int16)
    trace = ((word >> NIDQ_BARCODE_XD_LINE) & 1).astype(np.int8)
    decoded = decode_edges(edges_from_samples(trace, NIDQ_SAMPLE_RATE_HZ))
    assert [b.value for b in decoded] == [v for v, _ in truth.barcodes]


def test_the_imec_sync_channel_is_not_the_barcode_carrier(tmp_path):
    """Spec section 4.5 keeps the imec SMA free and aligns SpikeGLX through NI
    instead. The SY column still exists — `snsApLfSy` declares it and a reader
    reshapes by it — but nothing drives it.

    This is the assertion whose opposite was true, and passing, from Phase 1a
    until 1c-4: the fixture put the barcode on SY while both specs said NI, and
    no code read a SpikeGLX barcode in between to notice. Asserting the silence
    is what stops it drifting back.
    """
    _, bin_path, _ = emit(tmp_path)
    n_channels = CI_RECIPE.n_ap_channels + 1
    data = np.fromfile(bin_path, dtype=np.int16).reshape(-1, n_channels)
    assert not data[:, -1].any()


def test_spikeinterface_can_open_the_nidq_stream(tmp_path):
    """The same acceptance criterion the imec stream is held to, applied to the
    stream the pipeline actually aligns on. Guessing at .meta fields does not
    survive contact with the reader — `snsMnMaXaDw` and `niXDChans1` in
    particular are indexed unconditionally.
    """
    _, _, directory = emit(tmp_path)
    from wl_preproc.synth.spikeglx import NIDQ_SAMPLE_RATE_HZ

    recording = spikeinterface.read_spikeglx(directory, stream_id="nidq")
    assert recording.get_num_channels() == 1
    assert recording.get_sampling_frequency() == pytest.approx(NIDQ_SAMPLE_RATE_HZ)
    assert recording.get_total_duration() == pytest.approx(
        CI_RECIPE.duration_s + SPIKEGLX_PRE_ROLL_S, rel=1e-3
    )


def test_spikeglx_pre_roll_differs_from_the_sync_box(tmp_path):
    """Different tick origins per system is the point: identical ones would let
    a pipeline that never computes an offset pass every alignment test."""
    from wl_preproc.synth.syncbox import SYNCBOX_PRE_ROLL_S

    assert SPIKEGLX_PRE_ROLL_S != SYNCBOX_PRE_ROLL_S


def test_planted_spikes_are_present_where_promised(tmp_path):
    """A large deflection must exist near each planted spike, or the fixture is
    not testing what it claims to."""
    truth, bin_path, _ = emit(tmp_path)
    n_channels = CI_RECIPE.n_ap_channels + 1
    data = np.fromfile(bin_path, dtype=np.int16).reshape(-1, n_channels)
    time_s, channel = truth.spikes[0]
    sample = int((time_s + SPIKEGLX_PRE_ROLL_S) * CI_RECIPE.ap_sample_rate_hz)
    window = data[sample : sample + 30, channel]
    baseline = np.std(data[:, channel])
    assert np.abs(window).max() > 3 * baseline


def test_emission_is_deterministic(tmp_path):
    truth = build_timeline(CI_RECIPE)
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    a = write_spikeglx(first, CI_RECIPE, truth)
    b = write_spikeglx(second, CI_RECIPE, truth)
    assert a.read_bytes() == b.read_bytes()
    # Both streams, not just the returned one: the NI word is the stream every
    # alignment result is derived from, so a nondeterministic one would move
    # every fit while the imec bytes stayed identical.
    nidq = f"{CI_RECIPE.session_id}.nidq.bin"
    assert (first / nidq).read_bytes() == (second / nidq).read_bytes()
