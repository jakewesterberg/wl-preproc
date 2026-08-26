from dataclasses import replace

import numpy as np
import pytest

from wl_preproc.synth.rhs import (
    BARCODE_DIGITAL_BIT,
    RHS_PRE_ROLL_S,
    RHS_SAMPLE_RATE_HZ,
    STROBE_DIGITAL_BIT,
    write_rhs,
)
from wl_preproc.synth.recipe import CI_RECIPE, STIM_RECIPE
from wl_preproc.synth.rhs_header import MAGIC
from wl_preproc.synth.stim import SETTLE_DURATION_S, unpack_stim_word
from wl_preproc.synth.timeline import build_timeline

# STIM_RECIPE declares no channels, so write_rhs_header applies its Port A
# default. Spelled out rather than derived from the emitter: a test that asks the
# implementation what to expect agrees with it by construction, including when
# the implementation is wrong.
EXPECTED_CHANNEL_NAMES = ["A-000", "A-001", "A-002", "A-003"]


def emit(tmp_path, name="rhs"):
    truth = build_timeline(STIM_RECIPE)
    directory = tmp_path / name
    directory.mkdir()
    return truth, write_rhs(directory, STIM_RECIPE, truth), directory


def stim_windows(truth):
    """Each planted event as (event, onset, pulse_end, settle_end) in samples.

    Every stim assertion below is against *all* of these, not the first. A
    fixture that rendered only event zero, or rendered the events out of order,
    is indistinguishable from a correct one if the tests only ever look at
    truth.stim_events[0].
    """
    fs = RHS_SAMPLE_RATE_HZ
    settle_samples = int(SETTLE_DURATION_S * fs)
    for event in truth.stim_events:
        onset = int((event.onset_s + RHS_PRE_ROLL_S) * fs)
        pulse_end = onset + max(1, int(event.duration_s * fs))
        yield event, onset, pulse_end, pulse_end + settle_samples


def planted_mask(truth, shape):
    """True wherever some planted event claims a sample, channel by channel."""
    mask = np.zeros(shape, dtype=bool)
    for event, onset, _pulse_end, settle_end in stim_windows(truth):
        mask[onset:settle_end, event.channel] = True
    return mask


def read_stim(out):
    return np.fromfile(out / "stim.dat", dtype=np.uint16).reshape(
        -1, STIM_RECIPE.n_ap_channels
    )


def read_amplifier(out):
    return np.fromfile(out / "amplifier.dat", dtype=np.int16).reshape(
        -1, STIM_RECIPE.n_ap_channels
    )


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
    shifts every trace by 6.4 mV. The bound is 20 counts, not 500: at 500 a
    400-count offset — a real mis-scaling, just not the 32768 one — would sail
    through.

    20, not the 5 this bound was before Task 6: `correlated_noise` alone
    measures near-perfectly zero-mean (<0.1 counts), but a real
    `generate_templates` waveform is not -- spikeinterface's own "naive"
    per-unit shape averages tens of uV negative over its capture window, a
    documented property of that model rather than a defect introduced here.
    Task 6 now spends that bias on every channel a unit's footprint reaches
    instead of confining it to one placeholder channel, so the whole-file
    mean moved from about -1 count to about -6. 20 stays comfortably clear
    of that measured value while remaining three orders of magnitude inside
    the 32768 this test actually guards against.
    """
    _, out, _ = emit(tmp_path)
    data = np.fromfile(out / "amplifier.dat", dtype=np.int16)
    assert data.size % STIM_RECIPE.n_ap_channels == 0
    assert abs(float(np.mean(data))) < 20  # centred near zero, not near 32768


def test_stim_words_carry_amp_settle_after_each_pulse(tmp_path):
    """Amplifier settle is the blanking mask the artifact stage keys on, so it
    must be asserted across the whole settle window of every pulse — and not
    during the pulse itself, where the word carries the magnitude instead."""
    truth, out, _ = emit(tmp_path)
    stim = read_stim(out)
    assert truth.stim_events
    for event, onset, pulse_end, settle_end in stim_windows(truth):
        settle = stim[pulse_end:settle_end, event.channel]
        assert settle.size == int(SETTLE_DURATION_S * RHS_SAMPLE_RATE_HZ)
        assert all(unpack_stim_word(int(w)).amp_settle for w in settle)
        pulse = stim[onset:pulse_end, event.channel]
        assert not any(unpack_stim_word(int(w)).amp_settle for w in pulse)


def test_stim_magnitude_matches_ground_truth(tmp_path):
    """Every planted event, not just the first: an emitter that rendered one
    event, or rendered them in the wrong order, passes a first-event check."""
    truth, out, _ = emit(tmp_path)
    stim = read_stim(out)
    assert truth.stim_events
    for event, onset, pulse_end, _settle_end in stim_windows(truth):
        for sample in range(onset, pulse_end):
            word = unpack_stim_word(int(stim[sample, event.channel]))
            assert word.magnitude == event.magnitude
            assert word.negative == event.negative


def test_channels_without_stim_stay_clean(tmp_path):
    """A pulse on one channel must not set flags on its neighbours, for the
    whole time that pulse and its settle window occupy."""
    truth, out, _ = emit(tmp_path)
    n_channels = STIM_RECIPE.n_ap_channels
    stim = read_stim(out)
    assert truth.stim_events
    for event, onset, _pulse_end, settle_end in stim_windows(truth):
        others = [c for c in range(n_channels) if c != event.channel]
        window = stim[onset:settle_end][:, others]
        assert not window.any()


def test_no_stim_word_is_set_outside_a_planted_window(tmp_path):
    """The complement of the assertions above, and the one Phase 2's blanking
    mask actually depends on: the union of the planted windows accounts for
    every nonzero sample in stim.dat. Without it a fixture could flag samples
    where nothing was planted, and a blanking stage tuned against it would
    silently over-blank."""
    truth, out, _ = emit(tmp_path)
    stim = read_stim(out)
    mask = planted_mask(truth, stim.shape)
    assert stim[~mask].max(initial=0) == 0
    assert stim[mask].any()  # the mask is not vacuously covering everything


def test_amplifier_shows_an_artifact_where_stim_occurred(tmp_path):
    """The artifact-removal stage needs a real deflection to remove, at every
    planted event.

    The baseline is measured away from the planted stim windows. Taking the
    whole channel's standard deviation instead — as this test first did — lets
    the artifacts inflate their own reference: on the busiest channel that
    baseline is about 310 counts rather than the 36 the recording actually
    sits at, so the ratio being asserted is roughly ten times too forgiving."""
    truth, out, _ = emit(tmp_path)
    amp = read_amplifier(out)
    mask = planted_mask(truth, amp.shape)
    assert truth.stim_events
    for event, onset, pulse_end, _settle_end in stim_windows(truth):
        during = np.abs(amp[onset:pulse_end, event.channel]).max()
        quiet = np.std(amp[~mask[:, event.channel], event.channel])
        assert during > 10 * quiet


def test_planted_spikes_are_present_where_promised(tmp_path):
    """Before this, planted spikes existed in the SpikeGLX stream and nowhere
    in the RHS one, so generate_session handed callers a truth its own files
    contradicted. Mirrors the SpikeGLX check.

    Channel-agnostic on purpose: `truth.spikes` names a unit, not a channel,
    and Task 6 replaces this emitter's `unit_id % n_channels` placeholder with
    a real multi-channel footprint -- an assertion pinned to one particular
    channel would break again the day a spike stops living on exactly one.
    """
    truth, out, _ = emit(tmp_path)
    amp = read_amplifier(out)
    mask = planted_mask(truth, amp.shape)
    assert truth.spikes
    time_s, unit_id = truth.spikes[0]
    assert unit_id in {u.unit_id for u in truth.units}
    sample = int((time_s + RHS_PRE_ROLL_S) * RHS_SAMPLE_RATE_HZ)
    window = amp[sample : sample + 30, :]
    baseline = np.array([np.std(amp[~mask[:, c], c]) for c in range(amp.shape[1])])
    assert (np.abs(window).max(axis=0) > 3 * baseline).any()


def test_a_pulse_straddling_the_end_of_the_buffer_is_truncated_not_fatal(tmp_path):
    """`if settle_end <= onset` catches an event wholly past the buffer but not
    one that starts inside it and ends outside: pulse_end then runs past
    n_samples, the settle tail is a linspace over a negative count, and
    write_rhs raises. STIM_RECIPE's guard bands make it unreachable, but any
    hand-built GroundTruth — a fault injector, a shortened recipe — reaches it.
    """
    truth = build_timeline(STIM_RECIPE)
    end_s = STIM_RECIPE.duration_s
    straddling = replace(truth.stim_events[0], onset_s=end_s - 0.0002, channel=0)
    truth = replace(truth, stim_events=(straddling,))

    directory = tmp_path / "straddle"
    directory.mkdir()
    out = write_rhs(directory, STIM_RECIPE, truth)
    stim = read_stim(out)
    assert stim[:, 0].any()  # the part that fits is still rendered


def test_every_code_word_gets_its_own_strobe_edge(tmp_path):
    """The strobe is tier B's only independent witness of the code bus (spec
    section 4.7), so each planted word must produce a countable rising edge.

    At a 1 ms width against timeline.CODE_WORD_SPACING_S = 1 ms, consecutive
    pulses were contiguous and merged into one long high: 31 planted words
    rendered as 5 rising edges, and nothing tested it. STROBE_WIDTH_S is half
    the spacing, so a low always separates them.

    Until the digital buffer was sized from the actual last code word rather
    than from `recipe.duration_s`, two of the 31 words -- BLOCK_END and
    SESSION_END, which build_timeline places at and just after duration_s --
    fell past the buffer's last sample and got no edge at all: a second,
    different way to lose a word, alongside the merge above. This test used
    to assert that loss as the expected count (`len(truth.code_words) - 2`);
    that assertion's premise was the defect, not something to preserve. See
    `test_every_code_word_gets_a_strobe_edge_in_the_rhs_digital_line` for the
    regression test written against that bug.
    """
    truth, out, _ = emit(tmp_path)
    digital = np.fromfile(out / "digitalin.dat", dtype=np.uint16)
    strobe = ((digital >> STROBE_DIGITAL_BIT) & 1).astype(np.int8)
    rising = int(np.count_nonzero(np.diff(np.concatenate(([0], strobe))) == 1))

    assert rising == len(truth.code_words)


def test_digital_input_uses_only_the_barcode_and_strobe_bits(tmp_path):
    """The barcode and the strobe share one 16-bit word. If either writer ever
    reached a bit it does not own, the other's decode would pick it up as its
    own signal — and both decodes would still look plausible."""
    _, out, _ = emit(tmp_path)
    digital = np.fromfile(out / "digitalin.dat", dtype=np.uint16)
    owned = np.uint16((1 << BARCODE_DIGITAL_BIT) | (1 << STROBE_DIGITAL_BIT))
    assert not np.any(digital & ~owned)
    assert np.any(digital & np.uint16(1 << BARCODE_DIGITAL_BIT))
    assert np.any(digital & np.uint16(1 << STROBE_DIGITAL_BIT))


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
    """Magnitude is meaningless without it — it is the scale factor a reader
    applies to every stim current, so a wrong value silently rescales every
    stimulation amplitude the pipeline reads.

    Asserted through the reader's Stim-stream gain, which is where the value is
    actually consumed. The earlier version of this test checked the magic number
    and that `STIM_STEP_SIZE_A > 0` — a constant compared against zero — and so
    never read the step size from the header at all. It did not notice when this
    branch moved the field from offset 12 to offset 60, because it never looked.

    `approx` is mandatory, not defensive: the field is float32, so a constant of
    1e-05 comes back as 9.99999975e-06.
    """
    extractors = pytest.importorskip("spikeinterface.extractors")

    from wl_preproc.synth.rhs import STIM_STEP_SIZE_A

    _, out, _ = emit(tmp_path)
    header = (out / "info.rhs").read_bytes()
    assert np.frombuffer(header[:4], dtype=np.uint32)[0] == MAGIC

    stim_stream = extractors.read_intan(
        file_path=out / "info.rhs", stream_name="Stim channel"
    )
    assert stim_stream.get_channel_gains()[0] == pytest.approx(STIM_STEP_SIZE_A)


def test_spikeinterface_can_open_the_emitted_session(tmp_path):
    """The reader-as-oracle test, matching the one that verifies SpikeGLX.

    Phase 1b could not have this: info.rhs was a 20-byte identification stub and
    read_intan failed parsing channel definitions. This passing is the whole
    point of writing a real header.
    """
    extractors = pytest.importorskip("spikeinterface.extractors")

    truth, out, _ = emit(tmp_path)
    recording = extractors.read_intan(
        file_path=out / "info.rhs", stream_name="RHS2000 amplifier channel"
    )

    assert recording.get_num_channels() == STIM_RECIPE.n_ap_channels
    assert recording.get_sampling_frequency() == pytest.approx(RHS_SAMPLE_RATE_HZ)
    assert list(recording.get_channel_ids()) == EXPECTED_CHANNEL_NAMES


def test_the_reader_returns_the_samples_we_wrote(tmp_path):
    """Opening is not enough — a header that parses but describes the array
    wrongly would slice amplifier.dat into the wrong shape."""
    extractors = pytest.importorskip("spikeinterface.extractors")

    truth, out, _ = emit(tmp_path)
    recording = extractors.read_intan(
        file_path=out / "info.rhs", stream_name="RHS2000 amplifier channel"
    )
    raw = np.fromfile(out / "amplifier.dat", dtype=np.int16).reshape(
        -1, STIM_RECIPE.n_ap_channels
    )
    assert recording.get_num_samples() == raw.shape[0]

    event = truth.stim_events[0]
    sample = int((event.onset_s + RHS_PRE_ROLL_S) * RHS_SAMPLE_RATE_HZ)
    channel_name = EXPECTED_CHANNEL_NAMES[event.channel]
    through_reader = recording.get_traces(
        start_frame=sample,
        end_frame=sample + 1,
        channel_ids=[channel_name],
    )
    assert int(through_reader[0, 0]) == int(raw[sample, event.channel])


def test_emission_is_deterministic(tmp_path):
    truth = build_timeline(STIM_RECIPE)
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    a = write_rhs(first, STIM_RECIPE, truth)
    b = write_rhs(second, STIM_RECIPE, truth)
    assert (a / "amplifier.dat").read_bytes() == (b / "amplifier.dat").read_bytes()
    assert (a / "stim.dat").read_bytes() == (b / "stim.dat").read_bytes()


def test_emission_is_deterministic_with_no_units(tmp_path):
    """`test_emission_is_deterministic` above uses STIM_RECIPE, which plants
    units and so takes `write_rhs`'s `if truth.units:` (spatial) branch --
    `render_traces`, and so `generate_templates`/`generate_noise` underneath
    it. That `if`/`else` fork is new as of Task 6; before it there was one
    unconditional path. The `else` (timing-only) branch has its own RNG draw
    and needs its own determinism check: the global constraint ("every
    random draw derives from a passed-in seed") is two-sided, not satisfied
    by covering only one branch.

    Same reasoning
    `test_spikeglx.test_emission_is_deterministic_with_planted_units`
    already states for the mirror-image gap in that module. There the base
    test covers the timing-only branch and the added one covers spatial,
    because CI_RECIPE is that file's default fixture; here it is reversed,
    because STIM_RECIPE -- not CI_RECIPE -- is the fixture the existing test
    above already uses. Task 6 fix round 1's finding.
    """
    recipe = CI_RECIPE
    assert recipe.n_units == 0  # else branch requires this -- CI_RECIPE's own default
    truth = build_timeline(recipe)
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    a = write_rhs(first, recipe, truth)
    b = write_rhs(second, recipe, truth)
    assert (a / "amplifier.dat").read_bytes() == (b / "amplifier.dat").read_bytes()
    assert (a / "stim.dat").read_bytes() == (b / "stim.dat").read_bytes()


@pytest.mark.parametrize("recipe", [CI_RECIPE, STIM_RECIPE], ids=["ci", "stim"])
def test_every_code_word_gets_a_strobe_edge_in_the_rhs_digital_line(tmp_path, recipe):
    """The RHS carries the strobe ONLY -- spec section 4.2, because 16 digital
    inputs cannot fit 16 data lines plus strobe plus barcode -- so its entire
    contribution is a COUNT. A dropped word is therefore invisible unless
    something counts.

    This shipped broken: the digital buffer was sized on recipe.duration_s
    while build_timeline places SESSION_END about a millisecond after it, so
    the last code word fell off the end of every session. Nothing caught it
    because this file only checked barcode values.

    Same class as the Phase 1b strobe defect in CHECKPOINT: a witness that
    silently stopped witnessing.
    """
    truth = build_timeline(recipe)
    directory = tmp_path / "rhs"
    directory.mkdir()
    out = write_rhs(directory, recipe, truth)

    digital = np.fromfile(out / "digitalin.dat", dtype=np.uint16)
    strobe = ((digital >> STROBE_DIGITAL_BIT) & 1).astype(np.int8)
    rising = int(np.count_nonzero(np.diff(np.concatenate(([0], strobe))) == 1))

    assert rising == len(truth.code_words), (
        f"{rising} strobe edges for {len(truth.code_words)} emitted words"
    )


def test_an_intan_spike_has_a_footprint_too(tmp_path):
    """`truth.spikes` must mean one thing regardless of which emitter reads it.
    A footprint in SpikeGLX and a modulo here is exactly the kind of drift
    `synth/rhs.py` importing SPIKE_TEMPLATE_UV was written to prevent.

    n_units=1, not STIM_RECIPE's default 3: with 3 units and no modulo
    collision (3 < n_ap_channels=16), the OLD placeholder already put each
    unit on its own distinct channel, so "more than one loud channel" held
    trivially -- three single-channel spikes, not evidence that any one of
    them has a footprint. One unit isolates the actual claim under test, the
    same way
    test_waveforms.test_a_unit_appears_on_several_channels_with_amplitude_falling_off
    does. Stim windows are excluded via `planted_mask`: STIM_RECIPE's own
    ARTIFACT_UV artifacts reach the low tens of thousands of counts against a
    spike's low thousands, so measured unmasked, the artifact channels alone
    clear "more than one loud channel" regardless of how spikes render.
    """
    recipe = STIM_RECIPE.model_copy(update={"n_units": 1, "n_ap_channels": 16})
    truth = build_timeline(recipe)
    out = write_rhs(tmp_path, recipe, truth)

    amp = np.fromfile(out / "amplifier.dat", dtype=np.int16)
    data = amp.reshape(-1, recipe.n_ap_channels).astype(float)
    clean = np.where(planted_mask(truth, data.shape), np.nan, data)
    peak_per_channel = np.nanmax(np.abs(clean - np.nanmean(clean, axis=0)), axis=0)
    assert np.count_nonzero(peak_per_channel > 0.3 * peak_per_channel.max()) > 1
