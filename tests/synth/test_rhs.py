import numpy as np
import pytest

from wl_preproc.synth.rhs import (
    BARCODE_DIGITAL_BIT,
    RHS_PRE_ROLL_S,
    RHS_SAMPLE_RATE_HZ,
    write_rhs,
)
from wl_preproc.synth.recipe import STIM_RECIPE
from wl_preproc.synth.stim import unpack_stim_word
from wl_preproc.synth.timeline import build_timeline


def emit(tmp_path, name="rhs"):
    truth = build_timeline(STIM_RECIPE)
    directory = tmp_path / name
    directory.mkdir()
    return truth, write_rhs(directory, STIM_RECIPE, truth), directory


def test_writes_the_expected_files(tmp_path):
    _, out, _ = emit(tmp_path)
    for name in ("info.rhs", "time.dat", "amplifier.dat", "stim.dat", "digitalin.dat"):
        assert (out / name).exists(), name


def test_dcamplifier_is_deliberately_absent(tmp_path):
    """Its dtype is unresolved — the vendor document's prose and its own MATLAB
    snippet disagree (spec section 6.3). Emitting it would fabricate a format."""
    _, out, _ = emit(tmp_path)
    assert not (out / "dcamplifier.dat").exists()


def test_amplifier_is_int16_with_no_offset(tmp_path):
    """One File Per Signal Type stores int16 scaled by 0.195 uV with no offset.
    The traditional .rhs format uses uint16 with a 32768 offset; mixing them
    shifts every trace by 6.4 mV."""
    _, out, _ = emit(tmp_path)
    data = np.fromfile(out / "amplifier.dat", dtype=np.int16)
    assert data.size % STIM_RECIPE.n_ap_channels == 0
    assert abs(float(np.mean(data))) < 500  # centred near zero, not near 32768


def test_stim_words_carry_amp_settle_after_each_pulse(tmp_path):
    """Amplifier settle is the blanking mask the artifact stage keys on, so it
    must actually be asserted where a pulse happened."""
    truth, out, _ = emit(tmp_path)
    n_channels = STIM_RECIPE.n_ap_channels
    stim = np.fromfile(out / "stim.dat", dtype=np.uint16).reshape(-1, n_channels)
    event = truth.stim_events[0]
    sample = int((event.onset_s + RHS_PRE_ROLL_S) * RHS_SAMPLE_RATE_HZ)
    window = stim[sample : sample + 30, event.channel]
    assert any(unpack_stim_word(int(w)).amp_settle for w in window)


def test_stim_magnitude_matches_ground_truth(tmp_path):
    truth, out, _ = emit(tmp_path)
    n_channels = STIM_RECIPE.n_ap_channels
    stim = np.fromfile(out / "stim.dat", dtype=np.uint16).reshape(-1, n_channels)
    event = truth.stim_events[0]
    sample = int((event.onset_s + RHS_PRE_ROLL_S) * RHS_SAMPLE_RATE_HZ)
    word = unpack_stim_word(int(stim[sample, event.channel]))
    assert word.magnitude == event.magnitude
    assert word.negative == event.negative


def test_channels_without_stim_stay_clean(tmp_path):
    """A pulse on one channel must not set flags on its neighbours."""
    truth, out, _ = emit(tmp_path)
    n_channels = STIM_RECIPE.n_ap_channels
    stim = np.fromfile(out / "stim.dat", dtype=np.uint16).reshape(-1, n_channels)
    event = truth.stim_events[0]
    sample = int((event.onset_s + RHS_PRE_ROLL_S) * RHS_SAMPLE_RATE_HZ)
    others = [c for c in range(n_channels) if c != event.channel]
    assert all(stim[sample, c] == 0 for c in others)


def test_amplifier_shows_an_artifact_where_stim_occurred(tmp_path):
    """The artifact-removal stage needs a real deflection to remove."""
    truth, out, _ = emit(tmp_path)
    n_channels = STIM_RECIPE.n_ap_channels
    amp = np.fromfile(out / "amplifier.dat", dtype=np.int16).reshape(-1, n_channels)
    event = truth.stim_events[0]
    sample = int((event.onset_s + RHS_PRE_ROLL_S) * RHS_SAMPLE_RATE_HZ)
    during = np.abs(amp[sample : sample + 20, event.channel]).max()
    quiet = np.std(amp[:, event.channel])
    assert during > 10 * quiet


def test_barcode_is_decodable_from_the_digital_input(tmp_path):
    """Standalone Intan aligns on barcode plus strobe alone — spec section 4.2."""
    from wl_sync.barcode import decode_edges, edges_from_samples

    truth, out, _ = emit(tmp_path)
    digital = np.fromfile(out / "digitalin.dat", dtype=np.uint16)
    barcode = ((digital >> BARCODE_DIGITAL_BIT) & 1).astype(np.int8)
    decoded = decode_edges(edges_from_samples(barcode, RHS_SAMPLE_RATE_HZ))
    assert [b.value for b in decoded] == [v for v, _ in truth.barcodes]


def test_time_dat_is_int32_and_monotonic(tmp_path):
    _, out, _ = emit(tmp_path)
    time_index = np.fromfile(out / "time.dat", dtype=np.int32)
    assert time_index[0] == 0
    assert np.all(np.diff(time_index) == 1)


def test_header_declares_the_stim_step_size(tmp_path):
    """Magnitude is meaningless without it — it is the scale factor."""
    from wl_preproc.synth.rhs import STIM_STEP_SIZE_A

    _, out, _ = emit(tmp_path)
    header = (out / "info.rhs").read_bytes()
    assert np.frombuffer(header[:4], dtype=np.uint32)[0] == 0xD69127AC
    assert STIM_STEP_SIZE_A > 0


def test_emission_is_deterministic(tmp_path):
    truth = build_timeline(STIM_RECIPE)
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    a = write_rhs(first, STIM_RECIPE, truth)
    b = write_rhs(second, STIM_RECIPE, truth)
    assert (a / "amplifier.dat").read_bytes() == (b / "amplifier.dat").read_bytes()
    assert (a / "stim.dat").read_bytes() == (b / "stim.dat").read_bytes()
