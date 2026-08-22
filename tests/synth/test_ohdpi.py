"""The ohdpi fixture, and what of its shape is proposed rather than known.

Design spec section 12.1: neither the OpenIris repository nor the OpenIrisDPI
wiki documents the recorded file's columns, and no sample file was available.
So these tests pin what the FIXTURE does — they are not claims about what
OpenIris does, and a real recording is expected to move them.
"""

import pytest
from wl_sync.barcode import IDLE_MIN_US, decode_edges, edges_from_samples

from wl_preproc.synth.ohdpi import OHDPI_FPS, OHDPI_PRE_ROLL_S, read_ohdpi_rows, write_ohdpi
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
    """The proposed shape: frame index, native timestamp, digital sample — one
    row per frame, contiguous and monotonic. A gap in the indices would be a
    dropped frame the file does not declare, and a non-monotonic timestamp
    would make the frame index stop being a time."""
    recipe, _, path = emit(tmp_path)
    rows = read_ohdpi_rows(path)

    expected_frames = int((recipe.duration_s + OHDPI_PRE_ROLL_S) * OHDPI_FPS)
    assert len(rows) == expected_frames
    assert [row.frame_index for row in rows] == list(range(expected_frames))
    timestamps = [row.timestamp_us for row in rows]
    assert timestamps == sorted(timestamps)
    assert set(row.digital for row in rows) == {0, 1}


def test_the_digital_column_carries_decodable_barcodes(tmp_path):
    """The whole reason the column exists (design spec section 2.1): the sync
    box's barcode replaces the Arduino pulse train the OpenIrisDPI wiki
    recommends, so every ground-truth barcode must come back out of it."""
    _, truth, path = emit(tmp_path)
    rows = read_ohdpi_rows(path)

    decoded = decode_edges(edges_from_samples([row.digital for row in rows], OHDPI_FPS))

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
    rows = read_ohdpi_rows(path)
    first = decode_edges(edges_from_samples([row.digital for row in rows], OHDPI_FPS))[0]
    assert first.start_us == pytest.approx(
        OHDPI_PRE_ROLL_S * 1_000_000, abs=1e6 / OHDPI_FPS
    )


def test_native_timestamps_agree_with_the_frame_rate(tmp_path):
    """The file carries both a frame index and a native timestamp, so the two
    can disagree. They must not: an extractor is entitled to derive the rate
    from the timestamps rather than assume `OHDPI_FPS`, and a fixture whose
    columns disagreed would make that derivation wrong while every value-based
    assertion still passed."""
    _, _, path = emit(tmp_path)
    rows = read_ohdpi_rows(path)

    intervals = [
        b.timestamp_us - a.timestamp_us for a, b in zip(rows[:-1], rows[1:], strict=True)
    ]
    assert intervals == [pytest.approx(1e6 / OHDPI_FPS, abs=1.0)] * len(intervals)


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
