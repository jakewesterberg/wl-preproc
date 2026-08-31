"""The arithmetic of alignment, tested without a database or a file.

`fit.py` is pure numeric by design (design spec section 4.5), so everything here
runs on hand-built barcodes. The test that proves the *phase* works — extract,
decode and fit a real generated session — is at the bottom.
"""

from pathlib import Path

import pytest
from wl_sync.barcode import Barcode, decode_edges

from wl_preproc.synth.recipe import RECIPES
from wl_preproc.synth.session import generate_session
from wl_preproc.timebase.fit import fit_offset, fit_rate


def _clean_session_reference() -> dict[int, float]:
    """Barcode `v` at session second `v` — the sync box's own numbering."""
    return {v: float(v) for v in range(0, 600)}


def test_rate_is_fitted_from_pooled_barcodes_to_under_one_ppm():
    """Spec section 4.5: a full session fits rate to well under 1 ppm."""
    reference = _clean_session_reference()
    true_ppm = 12.0
    device = [
        Barcode(value=v, start_us=int(v * 1_000_000 * (1 + true_ppm / 1e6)))
        for v in reference
    ]

    fit = fit_rate(device, reference, nominal_rate_hz=1_000_000.0)

    assert fit.n_matched == 600
    assert abs(fit.drift_ppm - true_ppm) < 1.0


def test_the_fitted_rate_is_the_nominal_rate_scaled_by_the_drift():
    """`drift_ppm` and `fitted_rate_hz` are two views of one number, and a
    consumer that stores only the rate must be able to recover the drift. If
    they can disagree, one of them is decoration."""
    reference = _clean_session_reference()
    true_ppm = 25.0
    device = [
        Barcode(value=v, start_us=int(v * 1_000_000 * (1 + true_ppm / 1e6)))
        for v in reference
    ]

    fit = fit_rate(device, reference, nominal_rate_hz=30_000.0)

    assert fit.fitted_rate_hz == pytest.approx(
        30_000.0 * (1 + fit.drift_ppm / 1e6), rel=1e-12
    )
    assert fit.fitted_rate_hz > 30_000.0


def test_matching_is_by_value_so_a_dropped_barcode_shifts_nothing():
    """One missing barcode must not shift every later one. Ordinal matching
    passes on clean data and is catastrophically wrong on exactly the data that
    matters, which is why this test drops one from the middle."""
    reference = _clean_session_reference()
    device = [Barcode(value=v, start_us=v * 1_000_000) for v in reference if v != 300]

    fit = fit_rate(device, reference, nominal_rate_hz=1_000_000.0)

    assert fit.n_matched == 599
    assert abs(fit.drift_ppm) < 0.1
    assert fit.residual_us_max < 10.0


def test_a_device_barcode_absent_from_the_reference_is_not_matched():
    """The device can see a barcode the sync box's own log did not record. It
    is not evidence about this device's clock — there is nothing to compare it
    against — so it must be dropped rather than paired with a neighbour."""
    reference = {v: float(v) for v in range(0, 10)}
    device = [Barcode(value=v, start_us=v * 1_000_000) for v in range(0, 20)]

    fit = fit_rate(device, reference, nominal_rate_hz=1_000_000.0)

    assert fit.n_matched == 10
    assert abs(fit.drift_ppm) < 0.1


def test_a_rate_cannot_be_fitted_from_fewer_than_two_barcodes():
    """One barcode establishes a position, not a slope. Returning a nominal
    rate and zero drift would be a number that reads like a measurement — the
    exact shape of defect this project's checkpoint records — so it raises.
    """
    reference = _clean_session_reference()

    with pytest.raises(ValueError, match="1 barcode"):
        fit_rate([Barcode(value=5, start_us=5_000_000)], reference, nominal_rate_hz=1.0)
    with pytest.raises(ValueError, match="0 barcodes"):
        fit_rate([], reference, nominal_rate_hz=1.0)


def test_offset_uses_the_session_rate_rather_than_estimating_its_own():
    """A short segment inherits the session rate. Spec section 4.5: fitting rate
    locally from two barcodes spanning ~2 s yields ~16 ppm, WORSE than
    inheriting. So the offset fit takes a RateFit and does not re-estimate."""
    reference = _clean_session_reference()
    rate = fit_rate(
        [Barcode(value=v, start_us=v * 1_000_000) for v in reference],
        reference,
        nominal_rate_hz=1_000_000.0,
    )
    segment = [Barcode(value=300, start_us=300_000_000 + 5_000_000)]

    off = fit_offset(segment, reference, rate)

    assert off.n_barcodes == 1
    assert off.offset_s == pytest.approx(-5.0, abs=1e-6)


def test_the_offset_is_what_converts_device_time_into_session_time():
    """The sign convention, asserted rather than left to a docstring. A sign
    error here is a session-long shift that every residual reports as zero,
    because the fit is self-consistent in the wrong frame."""
    reference = _clean_session_reference()
    true_ppm = 40.0
    device_all = [
        Barcode(value=v, start_us=int(v * 1_000_000 * (1 + true_ppm / 1e6)))
        for v in reference
    ]
    rate = fit_rate(device_all, reference, nominal_rate_hz=1_000_000.0)
    scale = 1.0 + rate.drift_ppm / 1e6

    segment = [b for b in device_all if 100 <= b.value <= 110]
    off = fit_offset(segment, reference, rate)

    for barcode in segment:
        session_s = barcode.start_us / 1e6 / scale + off.offset_s
        assert session_s == pytest.approx(reference[barcode.value], abs=1e-4)


def test_a_segment_with_no_barcodes_cannot_be_offset():
    """Zero barcodes is not an offset of zero. It is unalignable, and spec
    section 4.1 sends it to RejectedSegment — so this raises rather than
    returning a number that reads like a measurement.

    The RateFit passed in is a VALID one, so the refusal under test is the
    offset fit's own and cannot be satisfied by the rate fit raising first.
    """
    reference = _clean_session_reference()
    rate = fit_rate(
        [Barcode(value=v, start_us=v * 1_000_000) for v in reference],
        reference,
        nominal_rate_hz=1_000_000.0,
    )

    with pytest.raises(ValueError, match="no barcodes"):
        fit_offset([], reference, rate)


def test_residuals_report_a_barcode_that_does_not_fit():
    """The free integrity check of spec section 4.5: a segment whose local
    barcodes disagree with the device-level rate indicates a mis-assigned file
    or a device clock reset, and must surface as a number rather than be
    absorbed into the fit."""
    reference = _clean_session_reference()
    device = [Barcode(value=v, start_us=v * 1_000_000) for v in reference]
    device[300] = Barcode(value=300, start_us=300_000_000 + 3_000)  # 3 ms out

    fit = fit_rate(device, reference, nominal_rate_hz=1_000_000.0)

    assert fit.residual_us_max > 2_000.0
    assert fit.residual_us_rms < fit.residual_us_max


# --- The test that proves the phase works, rather than the arithmetic. ---

# Where each system's one recording sits, relative to the session directory.
# `None` means "glob for it", because SpikeGLX names its file after the run.
_RECORDING_PATHS: dict[str, str | None] = {
    "syncbox": "syncbox/syncbox.log",
    "spikeglx": None,
    "rhs": "rhs",
    "ohdpi": "ohdpi/OpenIris-synthetic.txt",
    "bcam": "bcam/frames.yaml",
}


def _recording(session_dir: Path, system: str) -> Path:
    relative = _RECORDING_PATHS[system]
    if relative is None:
        return next((session_dir / "spikeglx").glob("*.nidq.bin"))
    return session_dir / relative


def _resolvable_ppm(fs_hz: float, span_s: float) -> float:
    """The drift this system's own rate can resolve over this span.

    A sampled edge is known only to within one sample period (design spec
    section 3.1), so a slope measured across `span_s` cannot be better than one
    period divided by that span. The factor of two is for the systematic case:
    truncation to sample boundaries is a staircase, not noise, so it does not
    average down with barcode count. Measured on this fixture — RHS's fit
    misses its planted drift by exactly one 30 kHz sample per second.

    Derived rather than written down, for the reason `min_sample_rate_hz()` is:
    a flat "within 1 ppm" was the first version of this assertion and it asks
    for something the fixture's own sampling forbids. Parent spec section 4.5's
    "a full session fits rate to well under 1 ppm" is a claim about a session
    lasting an hour, where this quantity is 0.01 ppm — not about a 15 s
    fixture, where it is 2.4 ppm at 30 kHz and 143 ppm at 500 Hz.
    """
    return 2.0 * (1.0 / fs_hz) / span_s * 1e6


def _session_reference(session_dir: Path) -> dict[int, float]:
    """Session time, from the sync box's own log and nothing else.

    Deliberately NOT from `GroundTruth`: t=0 is the sync box's first barcode
    (spec section 4.5), and in January there is no ground truth to fall back
    on. A reference taken from the oracle would test the fit against a timeline
    the pipeline will never have.
    """
    from wl_preproc.timebase.extract import extract_syncbox

    decoded = decode_edges(list(extract_syncbox(_recording(session_dir, "syncbox")).edges))
    origin_us = decoded[0].start_us
    return {b.value: (b.start_us - origin_us) / 1e6 for b in decoded}


def test_the_whole_chain_recovers_each_systems_clock(tmp_path: Path):
    """Extract, decode and fit every system of a real generated session, and
    check each fitted drift against the drift the recipe planted.

    **This is the test that proves the phase works**; everything above it
    proves the arithmetic. It runs on the `drift` profile because that is the
    only one carrying all five systems and the only one where the clocks
    actually differ — four distinct drifts, none of them the reference's.
    """
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.timebase.extract import EXTRACTORS

    recipe = RECIPES["drift"]
    planted = dict(recipe.system_drift_ppm)
    generate_session(tmp_path, recipe)
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    reference = _session_reference(session_dir)

    span_s = max(reference.values()) - min(reference.values())

    fitted: dict[str, tuple[float, float]] = {}
    for system in recipe.systems:
        if system == "syncbox":
            continue
        stream = EXTRACTORS[system](_recording(session_dir, system))
        decoded = decode_edges(list(stream.edges))
        fitted[system] = (
            fit_rate(decoded, reference, stream.fs_hz).drift_ppm,
            _resolvable_ppm(stream.fs_hz, span_s),
        )

    assert set(fitted) == set(planted)
    for system, expected_ppm in planted.items():
        drift_ppm, tolerance_ppm = fitted[system]
        assert drift_ppm == pytest.approx(expected_ppm, abs=tolerance_ppm), (
            f"{system}: planted {expected_ppm} ppm, fitted {drift_ppm:.2f} ppm, "
            f"resolvable to {tolerance_ppm:.2f} ppm at {stream.fs_hz:g} Hz"
        )


def test_the_syncbox_has_no_drift_against_itself(tmp_path: Path):
    """Session time is the sync box's own timeline, so it cannot drift against
    it. Until Phase 1c-4 every emitter INCLUDING the sync box received the same
    `drift_ppm`, which cancelled exactly and left every fixture with no
    relative drift at all — the rate fit had nothing to fit and no test could
    have seen it, because every shipped recipe left the value at zero.
    """
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.timebase.extract import extract_syncbox

    recipe = RECIPES["drift"].model_copy(update={"drift_ppm": 250.0})
    generate_session(tmp_path, recipe)
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    reference = _session_reference(session_dir)

    decoded = decode_edges(list(extract_syncbox(_recording(session_dir, "syncbox")).edges))
    fit = fit_rate(decoded, reference, nominal_rate_hz=1_000_000.0)

    assert fit.drift_ppm == pytest.approx(0.0, abs=1e-6)


def test_a_devices_offset_places_it_at_its_own_tick_origin(tmp_path: Path):
    """Each emitter starts recording a different amount of time before session
    t=0 (design spec section 10 asks for five distinct origins), and the offset
    fit is what recovers that. Checking it against the emitters' own pre-roll
    constants is what makes the offset a measurement rather than a number that
    merely makes the residual small.
    """
    from wl_preproc.synth.ohdpi import OHDPI_PRE_ROLL_S
    from wl_preproc.synth.peripherals import BCAM_PRE_ROLL_S
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.rhs import RHS_PRE_ROLL_S
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S
    from wl_preproc.synth.syncbox import SYNCBOX_PRE_ROLL_S
    from wl_preproc.timebase.extract import EXTRACTORS

    # Session time is rebased to the sync box's FIRST barcode, so the sync
    # box's own pre-roll cancels and does not appear here — only the device's
    # does. Device native time = session time + this system's pre-roll, so the
    # offset that converts back is its negation.
    assert SYNCBOX_PRE_ROLL_S > 0.0, "the cancellation below is only interesting if it is"
    expected_offset_s = {
        "spikeglx": -SPIKEGLX_PRE_ROLL_S,
        "rhs": -RHS_PRE_ROLL_S,
        "ohdpi": -OHDPI_PRE_ROLL_S,
        "bcam": -BCAM_PRE_ROLL_S,
    }

    recipe = RECIPES["drift"]
    generate_session(tmp_path, recipe)
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    reference = _session_reference(session_dir)

    for system, expected in expected_offset_s.items():
        stream = EXTRACTORS[system](_recording(session_dir, system))
        decoded = decode_edges(list(stream.edges))
        rate = fit_rate(decoded, reference, stream.fs_hz)
        offset = fit_offset(decoded, reference, rate)
        # One camera sample period, which is 2 ms at 500 Hz and the dominant
        # term for the two camera systems (design spec section 3.1).
        assert offset.offset_s == pytest.approx(expected, abs=2.5e-3), (
            f"{system}: expected {expected:+.3f} s, fitted {offset.offset_s:+.3f} s"
        )
