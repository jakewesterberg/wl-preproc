"""The ohdpi fixture, now in OpenIris's real format.

Until this task, neither the OpenIris repository nor the OpenIrisDPI wiki
documented the recorded file's columns, and no sample recording was
available, so this generator wrote a guessed CSV shape and these tests could
only pin what the fixture itself did. `tests/fixtures/ohdpi/OpenIris-
sample.txt` -- a committed 200-row slice of a genuine recording -- ends that:
these tests now assert against the real format, the same authority
`tests/eye/test_ohdpi_reader.py` pins the production reader against.
"""

from pathlib import Path

import numpy as np
import pytest
from wl_sync.barcode import IDLE_MIN_US, decode_edges, edges_from_samples

from wl_preproc.eye.ohdpi import SYNC_BIT_INDEX, SYNC_WORD_COLUMN, read_columns
from wl_preproc.synth.ohdpi import (
    HEADER,
    OHDPI_FPS,
    OHDPI_PRE_ROLL_S,
    SAMPLE_DTYPE_DECIMALS,
    write_ohdpi,
)
from wl_preproc.synth.recipe import RECIPES
from wl_preproc.synth.timeline import build_timeline
from wl_preproc.timebase.extract import min_sample_rate_hz


def emit(tmp_path):
    recipe = RECIPES["eye"]
    truth = build_timeline(recipe)
    directory = tmp_path / "ohdpi"
    directory.mkdir()
    return recipe, truth, write_ohdpi(directory, recipe, truth)


def test_ohdpi_fps_clears_the_decoding_floor():
    """500 Hz is 2.5 samples per 5 ms bit — the thinnest margin in the design
    (design spec section 3), so the assertion is against the derived floor
    rather than a literal."""
    assert OHDPI_FPS >= min_sample_rate_hz()


def test_ohdpi_emits_one_row_per_frame_with_a_digital_sample(tmp_path):
    """One row per frame, contiguous and monotonic -- this time in OpenIris's
    real shape rather than a guess. A gap in `LeftFrameNumber` would be a
    dropped frame the file does not declare, and a non-monotonic `LeftSeconds`
    would make the frame number stop being a time. The count starts at
    308788, matching the reference recording's own camera counter -- not 0,
    which is exactly what `wl_preproc/eye/ohdpi.py`'s reader depends on NOT
    assuming."""
    recipe, _, path = emit(tmp_path)
    cols = read_columns(path, ["LeftFrameNumber", "LeftSeconds", SYNC_WORD_COLUMN])

    expected_frames = int((recipe.duration_s + OHDPI_PRE_ROLL_S) * OHDPI_FPS)
    frame_numbers = cols["LeftFrameNumber"]
    assert len(frame_numbers) == expected_frames
    first = int(frame_numbers[0])
    assert frame_numbers.tolist() == list(range(first, first + expected_frames))
    seconds = cols["LeftSeconds"]
    assert seconds.tolist() == sorted(seconds.tolist())
    assert set(np.unique(cols[SYNC_WORD_COLUMN])) == {12, 13}


def test_the_digital_column_carries_decodable_barcodes(tmp_path):
    """The whole reason the column exists (design spec section 2.1): the sync
    box's barcode replaces the Arduino pulse train the OpenIrisDPI wiki
    recommends, so every ground-truth barcode must come back out of it --
    through `Int0`, masked to `SYNC_BIT_INDEX` the same way `extract_ohdpi`
    reads it in production, rather than through a bare 0/1 column the real
    format does not have."""
    _, truth, path = emit(tmp_path)
    cols = read_columns(path, [SYNC_WORD_COLUMN])
    bits = (cols[SYNC_WORD_COLUMN] >> SYNC_BIT_INDEX) & 1

    decoded = decode_edges(edges_from_samples(bits.tolist(), OHDPI_FPS))

    assert [b.value for b in decoded] == [v for v, _ in truth.barcodes]


def test_ohdpi_has_its_own_tick_origin(tmp_path):
    """A fifth distinct origin (design spec section 10). It must also clear
    `IDLE_MIN_US`, or the first barcode silently fails to decode for want of a
    preceding idle — the trap this project has paid for twice."""
    from wl_preproc.synth.peripherals import BCAM_PRE_ROLL_S
    from wl_preproc.synth.rhs import RHS_PRE_ROLL_S
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S
    from wl_preproc.synth.syncbox import SYNCBOX_PRE_ROLL_S

    origins = (
        SYNCBOX_PRE_ROLL_S,
        SPIKEGLX_PRE_ROLL_S,
        RHS_PRE_ROLL_S,
        BCAM_PRE_ROLL_S,
        OHDPI_PRE_ROLL_S,
    )
    assert len(set(origins)) == len(origins)
    assert OHDPI_PRE_ROLL_S > IDLE_MIN_US / 1_000_000.0

    _, _, path = emit(tmp_path)
    cols = read_columns(path, [SYNC_WORD_COLUMN])
    bits = (cols[SYNC_WORD_COLUMN] >> SYNC_BIT_INDEX) & 1
    first = decode_edges(edges_from_samples(bits.tolist(), OHDPI_FPS))[0]
    assert first.start_us == pytest.approx(
        OHDPI_PRE_ROLL_S * 1_000_000, abs=1e6 / OHDPI_FPS
    )


def test_native_timestamps_agree_with_the_frame_rate(tmp_path):
    """The file carries both a frame number and a native timestamp
    (`LeftSeconds`), so the two can disagree. They must not: an extractor is
    entitled to derive the rate from the timestamps rather than assume
    `OHDPI_FPS`, and a fixture whose columns disagreed would make that
    derivation wrong while every value-based assertion still passed.

    Tolerance comes from the fixture's own rounding (`SAMPLE_DTYPE_DECIMALS`
    decimal places on `LeftSeconds`), not an arbitrary margin: two adjacent,
    independently-rounded samples can differ from the true frame interval by
    up to two such rounding quanta.
    """
    _, _, path = emit(tmp_path)
    seconds = read_columns(path, ["LeftSeconds"])["LeftSeconds"]

    intervals = list(np.diff(seconds) * 1e6)
    quantum_us = 0.5 * 10 ** (-SAMPLE_DTYPE_DECIMALS) * 1e6
    assert intervals == [
        pytest.approx(1e6 / OHDPI_FPS, abs=2 * quantum_us)
    ] * len(intervals)


def test_the_eye_profile_exists_and_includes_ohdpi():
    """`ohdpi` was in the SYSTEMS tuple and nowhere else — no emitter, no
    profile, no fixture. The profile is what makes it reachable from
    `generate_session`."""
    recipe = RECIPES["eye"]
    assert "ohdpi" in recipe.systems


def test_emission_is_deterministic(tmp_path):
    recipe = RECIPES["eye"]
    truth = build_timeline(recipe)
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    a = write_ohdpi(first, recipe, truth)
    b = write_ohdpi(second, recipe, truth)
    assert a.read_bytes() == b.read_bytes()


def test_the_fixture_is_readable_by_the_production_reader(tmp_path):
    """The generator and the reader must agree because both match OpenIris,
    not because they share constants. `wl_preproc/eye/ohdpi.py` restates its
    own column names and is additionally pinned by a slice of a real
    recording, so this passing means the fixture matches the real format.

    Goes through `generate_session`, not a hand-built `GroundTruth`:
    `GroundTruth.for_recipe` does not exist -- it is a plain dataclass
    assembled inside `synth/timeline.py`, and `generate_session` is the public
    entry point that both builds it and writes the files.
    """
    from wl_preproc.eye.ohdpi import read_ohdpi
    from wl_preproc.synth.ohdpi import FILENAME
    from wl_preproc.synth.session import generate_session

    recipe = RECIPES["eye"]
    generate_session(tmp_path, recipe)
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    path = session_dir / "ohdpi" / FILENAME

    rec = read_ohdpi(path)
    assert rec.n_frames > 0
    assert set(rec.digital.tolist()) <= {12, 13}


def test_it_emits_purkinje_and_pupil_columns(tmp_path):
    """Gaze needs P1 (CR1) and P4 (CR4). A fixture carrying only the sync line
    cannot exercise calibration at all."""
    from wl_preproc.synth.ohdpi import FILENAME
    from wl_preproc.synth.session import generate_session

    recipe = RECIPES["eye"]
    generate_session(tmp_path, recipe)
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    path = session_dir / "ohdpi" / FILENAME

    cols = read_columns(
        path, ["LeftCR1X", "LeftCR1Y", "LeftCR4X", "LeftCR4Y", "LeftDataQuality"]
    )
    assert cols["LeftCR1X"].std() > 0, "P1 must move"
    assert cols["LeftCR4X"].std() > 0, "P4 must move"
    assert set(cols["LeftDataQuality"].tolist()) <= {0.0, 50.0, 100.0}


def test_cr2_cr3_cr5_stay_zero_while_p1_and_p4_differ(tmp_path):
    """The real fixture only ever populates CR1 (P1) and CR4 (P4); CR2, CR3
    and CR5 are zero in every one of `OpenIris-sample.txt`'s 200 rows. P1 must
    also differ from P4 -- a calibration has nothing to recover from the
    rotation term if the two Purkinje images never separate."""
    from wl_preproc.synth.ohdpi import FILENAME
    from wl_preproc.synth.session import generate_session

    recipe = RECIPES["eye"]
    generate_session(tmp_path, recipe)
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    path = session_dir / "ohdpi" / FILENAME

    cols = read_columns(
        path,
        [
            "LeftCR1X", "LeftCR1Y", "LeftCR2X", "LeftCR2Y",
            "LeftCR3X", "LeftCR3Y", "LeftCR4X", "LeftCR4Y",
            "LeftCR5X", "LeftCR5Y",
        ],
    )
    for name in ("LeftCR2X", "LeftCR2Y", "LeftCR3X", "LeftCR3Y", "LeftCR5X", "LeftCR5Y"):
        assert (cols[name] == 0.0).all(), name
    assert (cols["LeftCR1X"] != cols["LeftCR4X"]).any()
    assert (cols["LeftCR1Y"] != cols["LeftCR4Y"]).any()


def test_the_two_eyes_purkinje_traces_are_not_identical(tmp_path):
    """Design spec section 3.7 treats binocular agreement between the two
    eyes' independently-calibrated gaze estimates as a free quality signal --
    meaningless if the two eyes' raw traces agree by construction rather than
    by measurement. Before the fix rounds of 2026-08-30, one P1/P4 pair was
    computed and written to both eyes (only `Seconds` differed), so
    `purkinje_vector(path, "Left")` and `purkinje_vector(path, "Right")`
    would have returned the same array, and any test or downstream
    calibration step comparing the two eyes would pass, or run, without
    exercising anything -- this fixture's own instance of the defect this
    project keeps finding.

    A first fix gave the right eye its own X-axis rotation term but left Y,
    and P1 itself, still shared -- a metric or test isolating elevation, or
    reading P1 at all, would still have found the two eyes indistinguishable,
    and a whole-array check like the one below alone would not have caught
    it (X differing is enough to make the combined array unequal even with Y
    collapsed). So every axis of both CR1 and CR4 is pinned separately here,
    not just the combined trace.
    """
    from wl_preproc.eye.ohdpi import read_columns
    from wl_preproc.synth.ohdpi import FILENAME
    from wl_preproc.synth.session import generate_session

    recipe = RECIPES["eye"]
    generate_session(tmp_path, recipe)
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    path = session_dir / "ohdpi" / FILENAME

    parts = ("CR1X", "CR1Y", "CR4X", "CR4Y")
    cols = read_columns(path, [f"{eye}{part}" for eye in ("Left", "Right") for part in parts])
    left = np.stack([cols[f"Left{part}"] for part in parts], axis=1)
    right = np.stack([cols[f"Right{part}"] for part in parts], axis=1)

    assert not np.array_equal(left, right)
    # Each of the four columns pinned separately: a regression that
    # collapsed any ONE axis or ONE Purkinje image back to shared between
    # eyes has to break its own line here, not merely something anywhere in
    # the eight-column comparison above. CR4's two axes are guaranteed
    # unequal at every frame by construction (a fixed offset can never
    # coincide with zero); CR1's are independent per-eye noise, verified
    # empirically to differ at every one of this fixture's frames for this
    # recipe's seed, not merely almost always.
    for part in parts:
        assert (cols[f"Left{part}"] != cols[f"Right{part}"]).all(), part


def test_header_matches_the_real_fixtures_first_line():
    """Closes the one circularity `usecols`-validated tests cannot touch.

    Every column a consumer actually reads is already cross-checked against
    real bytes: `read_columns`' `usecols` fails loudly on a name the header
    does not have, and the tests above read real columns out of THIS
    module's own emitted file. But `HEADER` also carries columns nothing
    reads yet -- `PupilX`, `PupilY`, the IMU triad, `Double0`-`Double7` --
    and no `usecols` call ever names them, so nothing notices if one of
    those drifts from what OpenIris actually writes. This pins the literal
    text instead: the same single-space join `write_ohdpi` emits
    (`" ".join(HEADER)`), compared against `OpenIris-sample.txt`'s own first
    line, verbatim.
    """
    fixture = Path(__file__).parent.parent / "fixtures" / "ohdpi" / "OpenIris-sample.txt"
    with fixture.open(encoding="utf-8") as handle:
        fixture_header = handle.readline().rstrip("\n")

    assert " ".join(HEADER) == fixture_header
