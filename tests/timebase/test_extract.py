from pathlib import Path

import numpy as np
import pytest
from wl_sync.barcode import BIT_SLOT_US, decode_edges

from wl_preproc.synth.recipe import RECIPES
from wl_preproc.synth.session import generate_session
from wl_preproc.timebase.extract import (
    BitStream,
    extract_rhs,
    extract_spikeglx,
    extract_syncbox,
    min_sample_rate_hz,
)


def test_min_sample_rate_is_derived_from_the_bit_slot_not_written_down():
    """Two samples per bit slot is the Nyquist floor for decoding a sampled
    digital line. Deriving it means a change to `BIT_SLOT_US` in wl-sync moves
    this number rather than silently invalidating it.

    The literal 400.0 appears here ONLY as the value the derivation must
    currently produce. If wl-sync changes the bit slot, this assertion is the
    thing that should fail, and it should fail loudly enough to send someone
    to every system's assumed rate.
    """
    assert min_sample_rate_hz() == 2.0 / (BIT_SLOT_US / 1_000_000.0)
    assert min_sample_rate_hz() == 400.0


def test_bitstream_rejects_a_sample_rate_below_the_floor():
    """A system sampled below the floor cannot decode a barcode at all, so
    constructing a BitStream that claims to is a programming error, not a
    data condition. It fails at construction rather than producing an empty
    decode that reads like "this file had no barcodes"."""
    with pytest.raises(ValueError, match="below the 400.0 Hz floor"):
        BitStream(edges=(), fs_hz=200.0, n_samples=0)


def test_bitstream_accepts_the_floor_exactly():
    """400 Hz is the boundary, and the boundary is inclusive: 2.0 samples per
    bit is decodable in principle. The spec records separately that it is not a
    comfortable operating point."""
    stream = BitStream(edges=(), fs_hz=400.0, n_samples=0)
    assert stream.fs_hz == 400.0


def test_syncbox_extraction_recovers_every_ground_truth_barcode(tmp_path: Path):
    """The sync box is the reference: session time is t=0 at its first barcode
    (spec section 4.5), so its own log needs no decode — the values and times
    are already in it.

    Checked against GroundTruth rather than against a re-decode of the same
    file, because a reader that mis-parses consistently agrees with itself.
    """
    truth = generate_session(tmp_path, RECIPES["ci"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    stream = extract_syncbox(session_dir / "syncbox" / "syncbox.log")

    assert stream.n_samples > 0
    assert len(stream.edges) > 0
    # Every ground-truth barcode is present, by value.
    recovered = {b.value for b in decode_edges(list(stream.edges))}
    expected = {value for value, _ in truth.barcodes}
    assert expected - recovered == set(), f"missing barcodes: {expected - recovered}"


def test_rhs_extraction_recovers_ground_truth_barcodes(tmp_path: Path):
    """The standalone-Intan topology: no NI, no SpikeGLX (`--profile stim`).
    Its tick origin is 0.45 s, a third distinct value, so a pipeline that never
    computes an offset fails.

    Checked against GroundTruth rather than against a re-decode of the same
    file, for the reason the syncbox test gives: a reader that mis-parses
    consistently agrees with itself.
    """
    truth = generate_session(tmp_path, RECIPES["stim"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())

    stream = extract_rhs(session_dir / "rhs")

    recovered = {b.value for b in decode_edges(list(stream.edges))}
    expected = {value for value, _ in truth.barcodes}
    assert expected - recovered == set(), f"missing barcodes: {expected - recovered}"


def test_rhs_rate_is_read_from_the_header_not_assumed(tmp_path: Path):
    """The RHS controller has its own clock and `recipe.ap_sample_rate_hz`
    describes the Neuropixels stream, so the two are independent by
    construction (`synth/rhs.py` says so at length). A rate taken as a constant
    here is a fit wrong by exactly the ratio nobody checked.

    **The rate under test is deliberately NOT 30 kHz.** Asserting against the
    emitter's own `RHS_SAMPLE_RATE_HZ` was written first and proved nothing: a
    hardcoded `fs_hz = 30_000.0` passes it, because every fixture in the repo
    happens to carry that rate. The only version of this test that can fail is
    one whose header declares something else.
    """
    from wl_preproc.synth.recipe import STIM_RECIPE
    from wl_preproc.synth.rhs import RHS_SAMPLE_RATE_HZ, STIM_STEP_SIZE_A
    from wl_preproc.synth.rhs_header import write_rhs_header

    unusual_rate_hz = 20_000.0
    assert unusual_rate_hz != RHS_SAMPLE_RATE_HZ
    recording = tmp_path / "rhs" / "2027-03-14_03_rhs"
    recording.mkdir(parents=True)
    write_rhs_header(
        recording / "info.rhs",
        STIM_RECIPE,
        sample_rate_hz=unusual_rate_hz,
        stim_step_size_a=STIM_STEP_SIZE_A,
        digital_input_bits=(0, 1),
    )
    np.zeros(1024, dtype=np.uint16).tofile(recording / "digitalin.dat")

    stream = extract_rhs(tmp_path / "rhs")

    assert stream.fs_hz == unusual_rate_hz


def test_rhs_edges_are_in_native_time_not_session_time(tmp_path: Path):
    """0.45 s of pre-roll sits before the first barcode, and it must survive
    extraction: converting to session time is the fit's job. An extractor that
    silently rebased to its own first edge would recover every value and land
    them all at the wrong native time — which no value-set assertion can see.
    """
    from wl_preproc.synth.rhs import RHS_PRE_ROLL_S

    generate_session(tmp_path, RECIPES["stim"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())

    stream = extract_rhs(session_dir / "rhs")

    first = decode_edges(list(stream.edges))[0]
    assert first.start_us == pytest.approx(RHS_PRE_ROLL_S * 1_000_000, rel=1e-3)


def _spikeglx_nidq(tmp_path: Path) -> Path:
    """The one `.nidq.bin` of a generated `ci` session."""
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    return next((session_dir / "spikeglx").glob("*.nidq.bin"))


def test_spikeglx_extraction_recovers_ground_truth_barcodes(tmp_path: Path):
    """SpikeGLX carries the barcode on one NI digital line (spec section 4.5),
    so extraction reads the `.nidq.bin` — not the imec stream, whose SMA that
    section deliberately leaves free.

    Recovery is checked against GroundTruth, not against a re-decode of the
    same file: a reader that mis-parses consistently agrees with itself.
    """
    truth = generate_session(tmp_path, RECIPES["ci"])

    stream = extract_spikeglx(_spikeglx_nidq(tmp_path))

    recovered = {b.value for b in decode_edges(list(stream.edges))}
    expected = {value for value, _ in truth.barcodes}
    assert expected - recovered == set(), f"missing barcodes: {expected - recovered}"


def test_spikeglx_edges_are_in_native_time_not_session_time(tmp_path: Path):
    """The generator gives SpikeGLX a 0.7 s tick origin, distinct from the sync
    box's 1.0 s and the RHS's 0.45 s. An extractor that ignored the device's own
    clock would still recover every value and land them all at the wrong native
    time — which no value-set assertion can see.
    """
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S

    generate_session(tmp_path, RECIPES["ci"])

    stream = extract_spikeglx(_spikeglx_nidq(tmp_path))

    first = decode_edges(list(stream.edges))[0]
    assert first.start_us > 0
    assert first.start_us == pytest.approx(SPIKEGLX_PRE_ROLL_S * 1_000_000, rel=1e-3)


def test_spikeglx_rate_is_read_from_the_sidecar_not_assumed(tmp_path: Path):
    """Every fixture in this repo samples at 30 kHz, so asserting the emitted
    rate proves nothing — a hardcoded 30_000.0 passes it. The rate here is
    deliberately something else, which is the only version of this test that can
    fail.

    The `.bin` is rewritten to match, because the extractor checks that the
    sample count divides by the declared channel count: a sidecar edited alone
    would fail on the shape before it ever reached the rate.
    """
    generate_session(tmp_path, RECIPES["ci"])
    bin_path = _spikeglx_nidq(tmp_path)
    meta_path = bin_path.with_suffix(".meta")
    unusual_rate_hz = 25_000.0

    meta_path.write_text(
        "\n".join(
            line if not line.startswith("niSampRate=") else f"niSampRate={unusual_rate_hz:g}"
            for line in meta_path.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )

    stream = extract_spikeglx(bin_path)

    assert stream.fs_hz == unusual_rate_hz


def test_spikeglx_reshapes_by_the_declared_channel_count(tmp_path: Path):
    """NI writes every analog channel ahead of every digital one, so the
    barcode's word sits at a stride the sidecar's census determines. A guessed
    count does not raise — it reads a strided mixture of analog channels as if
    it were a digital line — so the failure this test forces is the one that
    would otherwise be silent.

    Two analog channels are prepended to a real session's NI stream and the
    census is corrected to match. The barcodes must still come out.
    """
    from wl_preproc.synth.spikeglx import NIDQ_BARCODE_XD_LINE

    truth = generate_session(tmp_path, RECIPES["ci"])
    bin_path = _spikeglx_nidq(tmp_path)
    meta_path = bin_path.with_suffix(".meta")

    word = np.fromfile(bin_path, dtype=np.int16)
    analog = np.full((word.size, 2), 1 << NIDQ_BARCODE_XD_LINE, dtype=np.int16)
    np.column_stack([analog, word]).ravel().tofile(bin_path)
    meta_path.write_text(
        "\n".join(
            line if not line.startswith("snsMnMaXaDw=") else "snsMnMaXaDw=0,0,2,1"
            for line in meta_path.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )

    stream = extract_spikeglx(bin_path)

    recovered = {b.value for b in decode_edges(list(stream.edges))}
    expected = {value for value, _ in truth.barcodes}
    assert expected - recovered == set(), f"missing barcodes: {expected - recovered}"
