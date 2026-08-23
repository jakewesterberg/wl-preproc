"""Strobe-witness extraction from the Intan RHS (spec section 4.2).

The RHS gets the strobe ONLY -- 16 digital inputs cannot fit 16 data lines
plus strobe plus barcode -- so its whole contribution is a COUNT and its
timing, never content. `StrobeWitness`, not a `WordStream` with no words: a
correct strobe-only recording must not be representable as a decode failure.
"""

from __future__ import annotations


def test_the_rhs_witness_counts_edges_rather_than_merely_finding_them():
    """Spec section 4.2 gives the Intan RHS the strobe ONLY -- 16 digital
    inputs cannot fit 16 data lines plus strobe plus barcode -- so it is a
    witness for tier B, never a decoder.

    The assertion is on the COUNT, and that is the whole point. Phase 1b shipped
    a 1 ms strobe at 1 ms word spacing, so consecutive strobes were contiguous:
    they merged into one long high with no falling edge between, and 31 code
    words rendered as 5 countable edges. A test that only asked whether edges
    exist would have passed on that. Section 4.2.1 now pins T1 = 500 us against
    1 ms spacing precisely so the edges stay countable.
    """
    import numpy as np

    from wl_preproc.events.extract import StrobeWitness

    # Three 500 us pulses at 1 ms spacing, 30 kHz: 15 samples high, 15 low.
    fs = 30_000.0
    digital = np.zeros(100, dtype=np.uint16)
    for start in (10, 40, 70):
        digital[start : start + 15] |= 1 << 1

    strobe = (digital >> 1) & 1
    rising = tuple(int(i) + 1 for i in np.flatnonzero((strobe[1:] == 1) & (strobe[:-1] == 0)))
    witness = StrobeWitness(edge_samples=rising, fs_hz=fs)

    assert witness.n_edges == 3, (
        "three pulses must yield three edges; a merged strobe yields one, which "
        "is the Phase 1b defect this assertion exists to catch"
    )


def test_rhs_witness_matches_the_emitted_word_count(tmp_path):
    """The witness's whole value is that its count equals the number of codes
    the session emitted. Asserted against a real synthetic session rather than
    a hand-built array, because the emitter is what a real recording resembles.

    The task brief that specified this test named `Recipe` and `build_truth`.
    Neither exists: the recipe type is `SessionRecipe` (many required fields,
    cross-validated by its own `_coherent()`), and the timeline builder is
    `build_timeline`. `STIM_RECIPE` is used rather than a hand-built
    `SessionRecipe` because it is already this repo's "standalone Intan:
    syncbox + rhs, tier B" fixture (`wl_preproc/synth/recipe.py`) -- the same
    recipe `tests/synth/test_rhs.py::test_every_code_word_gets_a_strobe_edge_in_the_rhs_digital_line`
    uses for the identical strobe-count regression one layer down, against
    `digitalin.dat` directly rather than through this module's extractor.

    `write_rhs` writes into `dir_path / f"{recipe.session_id}_rhs"`, one level
    below the directory it is given, and returns that path -- but the return
    value is not needed here: `extract_rhs_witness` calls `find_recording_dir`,
    which itself accepts a session directory containing the recording one
    level down, so `out` (pre-`mkdir`'d, since `write_rhs` only creates its own
    nested directory and not its parent) is exactly what `write_rhs` expects
    and exactly what `extract_rhs_witness` expects, without threading the
    return value through.
    """
    from wl_preproc.events import extract
    from wl_preproc.synth.recipe import STIM_RECIPE
    from wl_preproc.synth.rhs import write_rhs
    from wl_preproc.synth.timeline import build_timeline

    truth = build_timeline(STIM_RECIPE)
    out = tmp_path / "rhs"
    out.mkdir()
    write_rhs(out, STIM_RECIPE, truth)

    witness = extract.extract_rhs_witness(out)
    assert witness.n_edges == len(truth.code_words)
