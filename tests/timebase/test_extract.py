from pathlib import Path

import pytest
from wl_sync.barcode import BIT_SLOT_US, decode_edges

from wl_preproc.synth.recipe import RECIPES
from wl_preproc.synth.session import generate_session
from wl_preproc.timebase.extract import BitStream, extract_syncbox, min_sample_rate_hz


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
