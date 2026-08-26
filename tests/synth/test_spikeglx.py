import numpy as np
import pytest

from wl_preproc.ephys.geometry import electrode_rows
from wl_preproc.synth.recipe import CI_RECIPE, SPATIAL_RECIPE
from wl_preproc.synth.spikeglx import (
    LF_SAMPLE_RATE_HZ,
    LFP_FREQ_HZ,
    SPIKEGLX_PRE_ROLL_S,
    write_spikeglx,
)
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

    from wl_preproc.synth.spikeglx import (
        NIDQ_BARCODE_XD_LINE,
        NIDQ_N_DIGITAL_WORDS,
        NIDQ_SAMPLE_RATE_HZ,
    )

    truth, bin_path, _ = emit(tmp_path)
    raw = np.fromfile(bin_path.parent / f"{CI_RECIPE.session_id}.nidq.bin", dtype=np.int16)
    # Two digital words per sample since Phase 1c5 -- word 0 (barcode, bit 0,
    # and strobe, bit 1), then word 1 (the 16 data lines) -- not the single
    # barcode-only word this test was originally written against.
    word = raw.reshape(-1, NIDQ_N_DIGITAL_WORDS)[:, 0]
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

    Two channels, not one: since Phase 1c5 the NI stream carries two digital
    words (word 0 -- barcode and strobe -- and word 1 -- the 16 data lines),
    and spikeinterface's reader counts one channel per declared digital word.
    """
    _, _, directory = emit(tmp_path)
    from wl_preproc.synth.spikeglx import NIDQ_N_DIGITAL_WORDS, NIDQ_SAMPLE_RATE_HZ

    recording = spikeinterface.read_spikeglx(directory, stream_id="nidq")
    assert recording.get_num_channels() == NIDQ_N_DIGITAL_WORDS
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
    """A large deflection must exist near each planted spike -- on SOME
    channel, not necessarily the one named by truth.spikes[0]. Channel-agnostic
    on purpose: `truth.spikes` now names a unit, not a channel, and Task 4
    replaces this emitter's single-nearest-site render with a real multi-channel
    footprint -- a fixed-channel assertion would break again the day a spike
    stops living on exactly one column.

    CI_RECIPE itself plants no units (n_units defaults to 0, which is what
    every timing-only fixture wants -- recipe.py's own comment), so this test
    asks for 3 of them explicitly rather than relying on the shared `emit()`
    helper.
    """
    recipe = CI_RECIPE.model_copy(update={"n_units": 3})
    truth = build_timeline(recipe)
    bin_path = write_spikeglx(tmp_path, recipe, truth)

    n_channels = recipe.n_ap_channels + 1
    data = np.fromfile(bin_path, dtype=np.int16).reshape(-1, n_channels)
    time_s, unit_id = truth.spikes[0]
    assert unit_id in {u.unit_id for u in truth.units}
    sample = int((time_s + SPIKEGLX_PRE_ROLL_S) * recipe.ap_sample_rate_hz)
    window = data[sample : sample + 30, :]
    baseline = np.std(data, axis=0)
    assert (np.abs(window).max(axis=0) > 3 * baseline).any()


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
    # And the LF stream Task 5 added: CI_RECIPE plants zero units, so this is
    # the timing-only branch of `_write_lf` -- all zeros, trivially
    # deterministic, but only if nothing upstream starts drawing from an
    # unseeded RNG on this path.
    lf = f"{CI_RECIPE.session_id}_imec0.lf.bin"
    assert (first / lf).read_bytes() == (second / lf).read_bytes()


def test_emission_is_deterministic_with_planted_units(tmp_path):
    """`test_emission_is_deterministic` above only exercises CI_RECIPE, which
    plants zero units and takes `write_spikeglx`'s else (timing-only) branch.
    The `if truth.units:` branch -- `render_traces`, and so
    `generate_templates`/`generate_noise` underneath it -- has its own RNG
    usage and needs its own determinism check: the global constraint ("every
    random draw derives from a passed-in seed") is two-sided, not satisfied
    by covering only one branch. Fix round 1, Finding 3.
    """
    recipe = CI_RECIPE.model_copy(update={"n_units": 3})
    truth = build_timeline(recipe)
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    a = write_spikeglx(first, recipe, truth)
    b = write_spikeglx(second, recipe, truth)
    assert a.read_bytes() == b.read_bytes()
    nidq = f"{recipe.session_id}.nidq.bin"
    assert (first / nidq).read_bytes() == (second / nidq).read_bytes()
    # `recipe.n_units=3` also takes `_write_lf`'s laminar-gradient branch --
    # `correlated_noise` again, seeded `recipe.seed + 2` -- so this is this
    # branch's own determinism check, the same reason the units branch above
    # needed one separate from the timing-only test.
    lf = f"{recipe.session_id}_imec0.lf.bin"
    assert (first / lf).read_bytes() == (second / lf).read_bytes()


def test_nidq_carries_the_code_words_not_only_the_barcode(tmp_path):
    """Spec section 4.2 routes 16 data lines plus strobe to the NI, and section
    12 picks the PXIe-6353 for exactly the 32 Port 0 lines that needs.

    Until 2026-08-23 the generator emitted only the barcode here, which made
    tier A -- two independent full-code records, Pi and NI -- impossible to
    produce or test. That left NP+NI, the lab's main recording configuration,
    at the one tier nothing exercised.
    """
    from wl_preproc.contracts.events import TaskTypeCode
    from wl_preproc.synth.recipe import BlockSpec, MontageSpec, SessionRecipe
    from wl_preproc.synth.spikeglx import write_spikeglx

    recipe = SessionRecipe(
        session_id="synth-ni-codes",
        subject="pico",
        rig="rigA",
        systems=("syncbox", "spikeglx"),
        blocks=(
            BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=3, trial_duration_s=3.0),
        ),
        montages=(MontageSpec(start_s=0.0, end_s=9.0),),
        n_ap_channels=4,
        ap_sample_rate_hz=30_000.0,
        seed=7,
    )
    truth = build_timeline(recipe)
    assert truth.code_words, "the fixture must emit code words at all"

    out = tmp_path / "spikeglx"
    out.mkdir()
    write_spikeglx(out, recipe, truth)

    from wl_preproc.timebase._nidq_meta import read_nidq_meta

    meta = read_nidq_meta(out / f"{recipe.session_id}.nidq.meta")
    assert meta.n_digital_words == 2, (
        "18 lines -- barcode, strobe and 16 data -- do not fit in one 16-bit "
        "word. NI saves a 32-line port as TWO digital words, which is what "
        f"section 12's PXIe-6353 is for. Got {meta.n_digital_words}"
    )

    raw = np.fromfile(out / f"{recipe.session_id}.nidq.bin", dtype=np.int16)
    samples = raw.reshape(-1, meta.n_channels)
    control = samples[:, meta.n_analog_channels].astype(np.uint16)  # word 0
    data = samples[:, meta.n_analog_channels + 1].astype(np.uint16)  # word 1

    # The word is latched at the strobe's FAR edge -- section 4.2.1.
    strobe = (control >> 1) & 1
    falling = np.flatnonzero((strobe[:-1] == 1) & (strobe[1:] == 0))
    assert len(falling) == len(truth.code_words), (
        f"{len(falling)} strobe edges for {len(truth.code_words)} emitted words"
    )
    assert [int(data[i]) for i in falling] == [word for _, word in truth.code_words]


def test_geom_map_comes_from_the_probe_table_not_a_format_string(tmp_path):
    """The fabricated map alternated x between 16 and 48. Real NP1000
    electrodes 0-3 sit at (16,0), (48,0), (0,20), (32,20) -- four x values on a
    four-channel period. A geometry the probe does not have is a fixture that
    describes a probe nobody owns."""
    recipe = CI_RECIPE.model_copy(update={"n_ap_channels": 4})
    truth = build_timeline(recipe)
    bin_path = write_spikeglx(tmp_path, recipe, truth)

    meta = bin_path.with_suffix(".meta").read_text()
    geom_line = next(l for l in meta.splitlines() if l.startswith("~snsGeomMap="))
    entries = geom_line.split("=", 1)[1]

    expected = electrode_rows(recipe.probe_part_number)[:4]
    for row in expected:
        assert f"(0:{row['x_coord']:g}:{row['y_coord']:g}:1)" in entries


def test_a_recipe_can_declare_the_nhp_probe(tmp_path):
    """NP1032 is the 4,416-site NHP Long probe with columns 103 um apart. The
    fixture could not express it at all before this."""
    recipe = CI_RECIPE.model_copy(
        update={"probe_part_number": "NP1032", "n_ap_channels": 4}
    )
    truth = build_timeline(recipe)
    bin_path = write_spikeglx(tmp_path, recipe, truth)

    meta = bin_path.with_suffix(".meta").read_text()
    assert "imDatPrb_pn=NP1032" in meta
    assert "(0:0:0:1)" in meta and "(0:103:0:1)" in meta


def test_geometry_round_trips_through_the_independent_reader_for_the_nhp_probe(tmp_path):
    """The two tests above assert on the .meta text we wrote -- a plumbing
    check that the string got built, not that the file we produced decodes to
    the probe we intended. The binding constraint for this whole module is
    that our writer emits the bytes and spikeinterface's `read_spikeglx` is
    the independent oracle, so the oracle needs to be asked, not just the
    text re-read.

    NP1032, not NP1000: this task's whole point is that the NHP probe is now
    nameable, and its 103 um column spacing is a value that would be obvious
    if the reader decoded something else.

    Verified by reading probeinterface 0.3.2's own source
    (`probeinterface/neuropixels_tools.py`): `read_spikeglx` does not consult
    `~snsGeomMap` at all. It rebuilds the full probe from `imDatPrb_pn` via
    `build_neuropixels_probe` -- the same offline table `electrode_rows`
    reads -- then resolves each recorded channel's electrode as
    `bank * 384 + channel` from `~imroTbl` and slices to those. So this test
    exercises a different decode path than the two above (`~imroTbl` plus the
    offline table, not the `~snsGeomMap` text), even though both ultimately
    bottom out at the same table.

    Design spec section 11's open item 2 asks whether `read_spikeglx` derives
    NHP bank geometry correctly, to be verified against `ElectrodeConfig`
    (§4) -- a DataJoint table this phase has not built yet. This test is
    narrower than that: one bank (n_ap_channels=4, bank 0 throughout), no
    `ElectrodeConfig` to check against. It found agreement here. It does not
    retire the open item -- a second bank is still unverified.
    """
    recipe = CI_RECIPE.model_copy(
        update={"probe_part_number": "NP1032", "n_ap_channels": 4}
    )
    truth = build_timeline(recipe)
    directory = tmp_path / "spikeglx"
    directory.mkdir()
    write_spikeglx(directory, recipe, truth)

    recording = spikeinterface.read_spikeglx(directory, stream_id="imec0.ap")
    decoded = recording.get_channel_locations()

    expected = electrode_rows(recipe.probe_part_number)[: recipe.n_ap_channels]
    expected_xy = np.array([[row["x_coord"], row["y_coord"]] for row in expected])

    np.testing.assert_allclose(decoded, expected_xy, atol=1e-6)


def test_an_lf_stream_is_emitted_beside_the_ap_stream(tmp_path):
    recipe = SPATIAL_RECIPE
    truth = build_timeline(recipe)
    write_spikeglx(tmp_path, recipe, truth)

    lf = tmp_path / f"{recipe.session_id}_imec0.lf.bin"
    assert lf.exists()
    meta = lf.with_suffix(".meta").read_text()
    assert "imSampRate=2500" in meta
    assert f"snsApLfSy=0,{recipe.n_ap_channels},1" in meta


def test_the_lf_band_carries_a_depth_varying_signal(tmp_path):
    """2b-8's CSD is a second spatial derivative. An LF band that is the same
    at every depth has a CSD of zero everywhere, so 'the reference preserved
    the laminar gradient' would be unfalsifiable.

    The bin is located from LFP_FREQ_HZ, LF_SAMPLE_RATE_HZ and the actual
    sample count rather than a hardcoded range. At 2500 Hz over ~16,750
    samples a `[1:20]` slice spans roughly 0.15-2.8 Hz -- nowhere near the
    planted 8 Hz signal, which sits around bin 54 -- so it would measure
    background noise instead of the signal this test claims to check
    (confirmed empirically: on SPATIAL_RECIPE's own fixture, `[1:20]` reads a
    12.6x max/min ratio off noise alone, well past this test's own threshold,
    versus 22.5x at the located bin). A test that computes its own bin cannot
    drift from the constant it is testing.
    """
    recipe = SPATIAL_RECIPE
    truth = build_timeline(recipe)
    write_spikeglx(tmp_path, recipe, truth)

    lf = tmp_path / f"{recipe.session_id}_imec0.lf.bin"
    n_chan = recipe.n_ap_channels + 1
    data = np.fromfile(lf, dtype=np.int16).reshape(-1, n_chan)[:, :-1].astype(float)

    low_freq = data - data.mean(axis=0)
    spectrum = np.abs(np.fft.rfft(low_freq, axis=0))
    freq_bin = round(LFP_FREQ_HZ * data.shape[0] / LF_SAMPLE_RATE_HZ)
    profile = spectrum[freq_bin]
    assert profile.max() > 3 * profile.min()
