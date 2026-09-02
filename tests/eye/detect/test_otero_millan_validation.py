"""Otero-Millan against the paper, because there is no oracle to check it with.

Design spec section 3.2, corrected 2026-09-01: this detector's reference is
**MATLAB and carries no licence**, so it cannot be a test-time oracle the way
REMoDNaV (MIT, on PyPI) can. It falls into BMD's bucket -- validated against
its own paper's reported statistics -- and section 3.2 says why that matters
rather than being a formality:

    "a buggy reimplementation is indistinguishable from a genuine detector
    disagreement ... it looks like exactly the finding this subsystem exists
    to surface."

**Against the real recording, gated on `WLPP_OHDPI_REFERENCE`**, following
`tests/eye/test_bhv2.py`'s own env-var idiom and `tests/schema/test_detect_
populate.py::test_the_run_count_measured_against_the_reference_recording`'s
shape. CI never has the file; these tests skip there and nowhere else.

**This file lives in `tests/eye/`, so it imports nothing from
`wl_preproc.schema`.** That tier is cross-checked on 3.13 with
`--noconftest tests/eye tests/contracts`, in a venv with no DataJoint, and one
schema import would make the whole tier require it -- the same constraint
`tests/eye/detect/test_otero_millan.py` records. Two consequences, both
deliberate:

- The **conjunction trace is not measured here.** It is built by
  `schema/detect.py::_overlapping`, which is on the far side of that line.
  Both eyes are measured; the conjunction's own statistics are a schema-tier
  question.
- The **scale heuristic below is restated, not imported**, from
  `test_detect_populate.py`'s own. Restating a number is how two definitions
  of one fact get made, so it is written here as a derivation from the
  recording (a percentile placed at a stated degree value) rather than as a
  literal, and the comment says where the other copy is.

**The rule this file exists to establish, which outlives it: an oracle-free
statistic is worthless until a null has been run against it.** This file
shipped a `r > 0.9` main-sequence bound for one commit. Measured, arbitrary
random spans of this same recording score *higher* than the detector on it,
and a detector with both of its acceptance gates removed scores higher still
-- so the bound rejected the working detector and would have licensed a broken
one. It was withdrawn, not widened, and the measurement it made is still
printed (`test_the_main_sequence_statistic_is_measured_and_not_asserted`).

The general lesson is not about the main sequence. It is that a statistic
adopted because it "is a property of real X that no artefact reproduces" is a
claim about a NULL, and the null is cheap to build and must be built first:
draw spans the detector did not choose, measure the same statistic on them,
and check the detector actually beats them. Design spec section 3.2 puts BMD
in this same no-oracle bucket; whoever validates it should build the null
before the check, not after.

**What the scale is, and why every number here has to be read with it in
hand.** This recording has no `.bhv2` and no known fixation-target positions,
so nothing has fit a calibration for it and there is no measured degrees-per-
pixel. The scale used here is *chosen*: the pooled 99th percentile of the raw
Purkinje difference is placed at 15 degrees. That is the same choice
`test_detect_populate.py` documents and for the same reason -- it puts the
bulk of the trace inside `ValidityParams`' plausible region with margin. It is
a plausible scale, not a measured one, so any check whose answer moves with it
is a check on the heuristic as much as on the detector. The rate test below
sweeps the scale for exactly that reason.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

from wl_preproc.eye.detect.labels import Label

# --------------------------------------------------------------------------
# What Otero-Millan et al. (2014) actually reports, read on 2026-09-02 from
# the published version of record rather than recalled.
#
#   Otero-Millan J, Castro JLA, Macknik SL, Martinez-Conde S (2014).
#   "Unsupervised clustering method to detect microsaccades." Journal of
#   Vision 14(2):18. doi:10.1167/14.2.18. PMID 24569984.
#
# Abstract read from PubMed; body read from the ARVO typeset PDF (ARVO's own
# site refuses automated fetches; the version of record was reached through a
# repository copy). This repository's rule is that a claim about a source is
# written only after opening the passage it rests on, and the reason this
# block is long is that **two of the three checks this task was set are not in
# the paper at all**, which is only sayable by having looked.
#
# **The rate.** The abstract's first sentence:
#
#     "Microsaccades, small involuntary eye movements that occur once or twice
#     per second during attempted visual fixation, are relevant to perception,
#     cognition, and oculomotor control"
#
# and the Methods, citing the literature it builds on:
#
#     "Microsaccade rates range typically between one and four per second
#     (Martinez-Conde et al., 2004; ...), a feature not used by current
#     detection methods."
#
# Neither is a measurement from this paper's own data -- searched, and the
# paper reports no measured microsaccade rate anywhere. They are the field's
# range as this paper states it, which is still the closest thing to a
# checkable published prediction about this detector's output. The band below
# spans both statements' union, loosened downward: this recording is not known
# to be a fixation task (see the rate test's docstring).
#
# **Do not confuse the paper's five-per-second with a rate.** "we select only
# the highest velocity peaks necessary to obtain a rate of microsaccade
# candidates of five per second, so as to ensure the inclusion of both true
# microsaccades and nonmicrosaccadic eye movements." That is the CANDIDATE
# budget -- `otero_millan._PEAK_BUDGET_PER_MIN_ISI`'s own `RATEOFPEAKS = 5`,
# independently confirmed here against the paper after Task 3 derived it from
# the MATLAB -- and it is held fixed by construction, so it can never be a
# finding.
#
# **The paper's one reported output statistic**, Results, on which
# `test_the_main_sequence...` prints a measurement:
#
#     "Most of the microsaccades in the recordings (>90%) were larger than
#     0.2 degrees, with 0.2 degrees already being in the tail end of the
#     distribution"
#
# **What the paper's data are**, which bounds every comparison above: 20 human
# subjects, 24 recordings, "EyeLink II or EyeLink 1000 ... at 500 samples per
# second", fixating "a small target on a computer monitor at a distance of 57
# cm for 30- or 45-s trials". A calibrated video tracker on a fixation task.
# The reference recording here is an uncalibrated DPI over 39 unconstrained
# minutes; the sampling rates match and nothing else is known to.
# --------------------------------------------------------------------------
PAPER_MICROSACCADE_RATE_MIN_PER_S = 0.5
PAPER_MICROSACCADE_RATE_MAX_PER_S = 3.0

#: The paper's ">90% larger than 0.2 degrees", as a fraction. **Measured and
#: printed, never asserted**, and that is a judgement this file has to defend
#: rather than assume: the fraction is a monotone function of the scale
#: heuristic (0.73 at half the reference scale to 0.92 at double it, measured),
#: and Engbert-Kliegl reproduces it to within a percentage point at every
#: scale. A bound on it would therefore be a bound on the chosen scale, failing
#: or passing for both detectors together, and would say nothing about this
#: reimplementation. Recorded because it is the only output statistic the paper
#: states, and a reader comparing the two deserves the number.
PAPER_FRACTION_ABOVE_FLOOR = 0.90

#: **A bound this file used to assert, removed 2026-09-02 as invalid.** Kept
#: as a named constant for one reason: the correlation is still measured and
#: printed, and a reader seeing the number needs to know which line it was
#: once held to and that the line was withdrawn rather than relaxed.
#:
#: It was never the paper's statistic. The phrase "main sequence" does not
#: appear in Otero-Millan et al. 2014, which reports no amplitude/peak-velocity
#: correlation in any space and does not use one as a validation display. (Its
#: own displays are the bimodality of the amplitude and peak-velocity
#: distributions, and cluster scatterplots of peak velocity against initial
#: acceleration peak -- a different pair of axes, and there peak velocity is a
#: clustering INPUT rather than a regressor.) It was the implementation plan's
#: own bound, on its own reasoning that a log-log velocity/amplitude
#: correlation is "a property of real saccades that no clustering artefact
#: reproduces".
#:
#: **It is not merely imprecise. It is anti-correlated with correctness** --
#: it rejects the working detector and licenses a broken one. See
#: `test_the_main_sequence_statistic_is_measured_and_not_asserted` for the
#: three measurements that establish that, and the module docstring for the
#: general rule they imply.
_WITHDRAWN_MAIN_SEQUENCE_BOUND = 0.9

#: `event_f1` between the two detectors. Both bounds matter and they say
#: different things -- see that test's own docstring.
EK_OM_OVERLAP_MIN_F1 = 0.5
EK_OM_OVERLAP_MAX_F1 = 1.0

#: The larger axis's 99th percentile of the raw Purkinje difference is placed
#: here, in degrees, so the bulk of the trace sits inside `ValidityParams`'
#: plausible region rather than beyond it.
#:
#: **Which axis gets placed decides which limit it is checked against, and it
#: is not the one this number looks like it names.** On this recording the
#: pooled p99 is (218.6, 209.0) px, so `x` is the larger and lands at exactly
#: 15.0 deg -- against `region_half_width_deg = 20.0`, five degrees of margin.
#: `y` follows at 209.0/218.6 x 15 = 14.34 deg, against the tighter
#: `region_half_height_deg = 15.0`: inside, but by two thirds of a degree. So
#: "inside with margin" is true on both axes and thin on one, and the
#: coincidence between this constant and `region_half_height_deg` is a
#: coincidence.
#:
#: Restated from `tests/schema/test_detect_populate.py`, which documents the
#: same choice at length; see this module's docstring for why it is restated
#: rather than imported.
_SCALE_P99_AT_DEG = 15.0


def _skip_reason() -> str:
    return (
        "WLPP_OHDPI_REFERENCE is not set. Point it at a real OpenIrisDPI "
        "recording's raw .txt file to run these tests -- the reference "
        "recording this repository's design spec measures against is "
        "OpenIris-2024Jul31-114628.txt (633 MB, 1,177,799 rows, ~39.4 "
        "minutes at ~498.55 Hz), from this lab's own "
        "~/Downloads/Tutorial/OpenIris-2024Jul31-114628/ tutorial materials "
        "-- see design spec section 5. Never commit that file."
    )


def _scaled_affine_map(scale: float):
    """`degrees = scale * raw_px` on both axes, no cross terms, no offset.

    A `CalibrationMap` rather than a bare tuple, so `apply_map` is exercised
    exactly as every real caller exercises it."""
    from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel

    return CalibrationMap(model=CalibrationModel.AFFINE, x=(0.0, scale, 0.0), y=(0.0, 0.0, scale))


def _gaze_velocity_mask(raw_xy, quality, fs_hz, frame_gaps, scale):
    """One eye's gaze at `scale`, its velocity, and its validity mask -- the
    three real functions `EyeValidity.make()` itself calls, run without a
    database. Returns the mask's `labels` array, which is what a detector
    takes."""
    from wl_preproc.eye.calibration import apply_map
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS, validity_labels
    from wl_preproc.eye.detect.velocity import velocity

    gaze = apply_map(_scaled_affine_map(scale), raw_xy)
    v = velocity(gaze, fs_hz)
    return gaze, v, validity_labels(gaze, v, quality, frame_gaps, DEFAULT_VALIDITY_PARAMS).labels


def _measured(runs, gaze, v, fs_hz):
    """`(amplitude_deg, peak_velocity_deg_s, duration_samples)` per event run,
    measured exactly as `EyeDetection._insert_trace::_run_row` measures it.

    **The STORED pair**, in other words: design spec section 6.5 fits the main
    sequence from the stored `amplitude_deg`/`peak_velocity_deg_s` columns, so
    a main-sequence check computed from anything else would be checking a
    quantity this pipeline never writes down."""
    from wl_preproc.eye.detect.measure import measure

    rows = [measure(gaze, v, run.start, run.stop, fs_hz) for run in runs]
    return (
        np.array([row.amplitude_deg for row in rows]),
        np.array([row.peak_velocity_deg_s for row in rows]),
        np.array([run.stop - run.start for run in runs]),
    )


def _log_log_fit(amplitude_deg: np.ndarray, peak_velocity_deg_s: np.ndarray):
    """`(r, n, slope, residual_sd)` for `log(peak velocity)` on
    `log(amplitude)`.

    **Zero-amplitude events are dropped, and that is a real exclusion rather
    than float hygiene.** `measure.amplitude` is endpoint-to-endpoint, so an
    event that ends exactly where it began measures 0.0 and has no logarithm.
    They exist on this recording -- the count is printed, not hidden -- and
    they are events whose two endpoints coincide, which on a DPI is a round
    trip rather than a saccade.
    """
    usable = (amplitude_deg > 0.0) & (peak_velocity_deg_s > 0.0)
    if int(usable.sum()) < 3:
        return float("nan"), 0, float("nan"), float("nan")
    log_a = np.log(amplitude_deg[usable])
    log_v = np.log(peak_velocity_deg_s[usable])
    slope, intercept = np.polyfit(log_a, log_v, 1)
    residual = log_v - (slope * log_a + intercept)
    return (
        float(np.corrcoef(log_a, log_v)[0, 1]),
        int(usable.sum()),
        float(slope),
        float(residual.std()),
    )


def _usable_segments(mask: np.ndarray, minimum: int) -> list[tuple[int, int]]:
    """Maximal stretches of detector-available samples at least `minimum`
    long. The random-span control draws from these so a control span never
    straddles a blink -- a span that did would be measuring the mask, not the
    trace."""
    available = np.flatnonzero(mask == None)  # noqa: E711
    if available.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(available) != 1)
    bounds, start = [], 0
    for stop in breaks:
        bounds.append((int(available[start]), int(available[stop]) + 1))
        start = int(stop) + 1
    bounds.append((int(available[start]), int(available[-1]) + 1))
    return [(lo, hi) for lo, hi in bounds if hi - lo >= minimum]


def _random_span_control(gaze, v, mask, durations, fs_hz, seed=0):
    """The same number of spans, with the same durations, at random usable
    positions. Returns `(amplitude, peak_velocity)`.

    **This is the null the main-sequence check's own justification names.**
    The plan's reasoning for that check is that a log-log velocity/amplitude
    correlation is "a property of real saccades that no clustering artefact
    reproduces". That is a claim about a null, and this is the null: spans of
    the same lengths, in the same trace, chosen without looking at the data.
    If it reproduces the correlation, the correlation is a property of spans
    rather than of what the detector selected.

    **What this control cannot do, stated because the number is easy to
    over-read.** It is drawn from the same recording, so it is not a clean
    null at large amplitude: this detector's median event is ~34 ms, and
    0.2 degrees in 34 ms is a mean speed of 5.9 deg/s -- above
    `otero_millan._SACCADE_LIMIT_DEG_S`, the reference's own boundary between
    an event and what surrounds it. So a control span that measures large
    amplitude is one that landed on a real fast movement, and above the
    0.2 degree floor the control is a second (crude) detector rather than a
    null; its agreement there says nothing. Below the floor it is a genuine
    null, and the comparison there is informative.

    **Second limit: the segments are the long ones.** Every segment has to
    admit the LONGEST event, so on this recording the control is drawn only
    from usable stretches of ~350 samples or more. That biases it toward
    uninterrupted, quiet trace -- which is conservative for the use it is put
    to here, since a quieter draw should if anything correlate less, and it
    still out-correlates the detector.

    Seeded, and the seed is stated: an unseeded control would make a
    reported figure irreproducible, which is the same objection this
    subsystem raises to `sklearn`'s random k-means initialisation.
    """
    from wl_preproc.eye.detect.measure import measure

    segments = _usable_segments(mask, minimum=int(durations.max()) + 2)
    if not segments:
        return np.array([]), np.array([])
    weight = np.array([hi - lo for lo, hi in segments], dtype=float)
    weight /= weight.sum()
    rng = np.random.default_rng(seed)
    amplitude, peak_velocity = [], []
    for duration in durations:
        index = int(rng.choice(len(segments), p=weight))
        lo, hi = segments[index]
        start = int(rng.integers(lo, hi - int(duration)))
        row = measure(gaze, v, start, start + int(duration), fs_hz)
        amplitude.append(row.amplitude_deg)
        peak_velocity.append(row.peak_velocity_deg_s)
    return np.array(amplitude), np.array(peak_velocity)


def _label_trace(mask: np.ndarray, runs) -> np.ndarray:
    """Each interval's own label written onto the mask, unclaimed samples
    `fixation` -- `EyeDetection._insert_trace`'s own label step, minus the
    inserts. No `classify` call: the labels come from the detector, and
    `_insert_trace` assigns none of its own."""
    labels = mask.copy()
    for run in runs:
        labels[run.start : run.stop] = run.label
    return np.where(labels == None, Label.FIXATION, labels)  # noqa: E711


class _Trace:
    """One eye, detected by both methods, with everything measured once."""

    def __init__(self, name, gaze, v, mask, fs_hz):
        from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS
        from wl_preproc.eye.detect.otero_millan import DEFAULT_OM_PARAMS
        from wl_preproc.eye.detect.registry import get_detector

        self.name, self.gaze, self.v, self.mask, self.fs_hz = name, gaze, v, mask, fs_hz
        self.om_detector = get_detector("otero_millan")
        self.ek_detector = get_detector("engbert_kliegl")
        self.om_runs = self.om_detector.detect(gaze, v, mask, DEFAULT_OM_PARAMS)
        self.ek_runs = self.ek_detector.detect(gaze, v, mask, DEFAULT_EK_PARAMS)
        self.om_amplitude, self.om_peak_velocity, self.om_duration = _measured(
            self.om_runs, gaze, v, fs_hz
        )
        self.ek_amplitude, self.ek_peak_velocity, self.ek_duration = _measured(
            self.ek_runs, gaze, v, fs_hz
        )
        self.usable_samples = int(np.sum(mask == None))  # noqa: E711
        self.usable_s = self.usable_samples / fs_hz

    @property
    def n_microsaccade(self):
        return sum(1 for run in self.om_runs if run.label is Label.MICROSACCADE)

    @property
    def n_saccade(self):
        return sum(1 for run in self.om_runs if run.label is Label.SACCADE)

    @property
    def microsaccade_rate_per_s(self):
        return self.n_microsaccade / self.usable_s

    @property
    def event_rate_per_s(self):
        return len(self.om_runs) / self.usable_s

    def f1_against_baseline(self):
        from wl_preproc.eye.detect.consensus import (
            DEFAULT_EVENT_F1_TOLERANCE_SAMPLES,
            PSO_AS_SACCADE,
            comparison_mask,
            event_f1,
        )

        a, b = _label_trace(self.mask, self.om_runs), _label_trace(self.mask, self.ek_runs)
        # `pso_as` has no default by design (design spec section 2.5) and so
        # must be named here. It is IMMATERIAL to this pair -- neither
        # detector declares `pso`, so no sample ever carries it and no
        # coarsening edge is followed -- and it is stated anyway rather than
        # picked, because the module's whole argument is that a defaulted
        # glissade assignment is how a comparison silently acquires one.
        keep = comparison_mask(
            a, b, self.om_detector.vocabulary, self.ek_detector.vocabulary, PSO_AS_SACCADE
        )
        return event_f1(a, b, keep, DEFAULT_EVENT_F1_TOLERANCE_SAMPLES), int(keep.sum())


@pytest.fixture(scope="module")
def reference():
    """The recording, read once, with both eyes detected by both methods.

    Module-scoped because the read is four column-selective passes over 633 MB
    (~8 s) and three tests need the same answer. A per-test fixture would
    triple that for nothing; a per-test *recomputation* would additionally
    risk three tests reporting three different numbers, which is the failure
    mode this whole file exists to guard against one level up.
    """
    sample = os.environ.get("WLPP_OHDPI_REFERENCE")
    if not sample:
        pytest.skip(_skip_reason())

    from wl_preproc.eye.gaze import purkinje_vector
    from wl_preproc.eye.ohdpi import read_columns, read_ohdpi

    started = time.monotonic()
    recording = read_ohdpi(sample)
    raw = {
        "left": purkinje_vector(sample, "Left"),
        "right": purkinje_vector(sample, "Right"),
    }
    quality = read_columns(sample, ["LeftDataQuality", "RightDataQuality"])
    read_s = time.monotonic() - started

    # The scale heuristic, stated rather than merely used (this file's own
    # honesty requirement, and `test_detect_populate.py`'s before it).
    pooled_x = np.concatenate([np.abs(raw["left"][:, 0]), np.abs(raw["right"][:, 0])])
    pooled_y = np.concatenate([np.abs(raw["left"][:, 1]), np.abs(raw["right"][:, 1])])
    p99_x = float(np.percentile(pooled_x, 99))
    p99_y = float(np.percentile(pooled_y, 99))
    scale = _SCALE_P99_AT_DEG / max(p99_x, p99_y)

    def build(eye_name, column):
        gaze, v, mask = _gaze_velocity_mask(
            raw[eye_name], quality[column], recording.fs_hz, recording.frame_gaps, scale
        )
        return _Trace(eye_name, gaze, v, mask, recording.fs_hz)

    return {
        "sample": sample,
        "recording": recording,
        "raw": raw,
        "quality": quality,
        "read_s": read_s,
        "scale": scale,
        "p99": (p99_x, p99_y),
        "traces": [build("left", "LeftDataQuality"), build("right", "RightDataQuality")],
    }


def test_the_microsaccade_rate_is_the_rate_the_paper_reports(reference, capsys):
    """Otero-Millan et al. 2014's own opening sentence: microsaccades "occur
    once or twice per second during attempted visual fixation", with its
    Methods putting the field's range at "between one and four per second".
    This measures that rate on a real recording and asserts a band spanning
    both.

    **What this establishes.** It is the closest thing to a checkable
    published prediction about this detector's output, and it is a real one: a
    detector whose clustering had collapsed -- accepting the noise cluster, or
    refusing the fast one -- would miss it by an order of magnitude in one
    direction or the other, whatever else passed. Passing it means the
    detector is emitting events at a physiologically possible density, over
    39 real minutes rather than over a fixture.

    **What it does not establish.** Not that the events are the right ones: a
    detector that found the correct NUMBER of wrong spans passes this
    identically.

    **And the source is a stated range, not a measurement.** The paper
    reports no measured microsaccade rate from its own data -- both figures
    above are the literature's range as this paper states it. Its data are
    also human subjects on a calibrated EyeLink holding fixation on a target
    at 57 cm; this recording is an uncalibrated DPI over 39 unconstrained
    minutes with no task record in the tree. So the band asserted is wider
    than either statement, on both sides, and a reading inside the narrower
    1-2/s is a bonus rather than the claim.

    **The saccade/microsaccade split is scale-dependent, so the scale is
    swept.** `classify` cuts at an absolute 1.0 degrees, and this recording's
    degrees-per-pixel is chosen rather than fitted (module docstring). A rate
    that held only at the chosen scale would be a fact about the heuristic.
    The sweep is printed, and the assertion runs over every swept scale from
    half to double the reference -- which is what makes this a check on the
    detector rather than on the choice.
    """
    traces = reference["traces"]
    recording = reference["recording"]

    with capsys.disabled():
        print(f"\n  reference recording: {reference['sample']}")
        print(
            f"  {recording.n_frames} frames, {recording.fs_hz:.2f} Hz, "
            f"{recording.n_frames / recording.fs_hz / 60:.1f} min, "
            f"{len(recording.frame_gaps)} frame gap(s); read in "
            f"{reference['read_s']:.1f}s over 4 column-selective passes"
        )
        print(
            f"  scale heuristic: {reference['scale']:.6g} deg/px "
            f"({1 / reference['scale']:.2f} px/deg) -- pooled p99 |raw| is "
            f"({reference['p99'][0]:.1f}, {reference['p99'][1]:.1f}) px, placed at "
            f"{_SCALE_P99_AT_DEG} deg. CHOSEN, not fitted."
        )
        print(
            f"  Otero-Millan rates at the reference scale (paper: 1-2 microsaccades/s "
            f"during attempted fixation; band asserted here "
            f"{PAPER_MICROSACCADE_RATE_MIN_PER_S}-{PAPER_MICROSACCADE_RATE_MAX_PER_S}/s):"
        )
        for trace in traces:
            print(
                f"    {trace.name:5s} {len(trace.om_runs):5d} events over "
                f"{trace.usable_s:.0f}s usable ({trace.usable_samples} of "
                f"{recording.n_frames} samples) -- "
                f"microsaccade {trace.microsaccade_rate_per_s:.3f}/s, "
                f"saccade {trace.n_saccade / trace.usable_s:.3f}/s, "
                f"all events {trace.event_rate_per_s:.3f}/s"
            )

    for trace in traces:
        assert PAPER_MICROSACCADE_RATE_MIN_PER_S <= trace.microsaccade_rate_per_s <= (
            PAPER_MICROSACCADE_RATE_MAX_PER_S
        ), (
            f"{trace.name}: {trace.microsaccade_rate_per_s:.3f} microsaccades/s over "
            f"{trace.usable_s:.0f}s of usable trace, outside the "
            f"{PAPER_MICROSACCADE_RATE_MIN_PER_S}-{PAPER_MICROSACCADE_RATE_MAX_PER_S}/s band "
            f"around the paper's own 1-2/s"
        )

    # The sweep. Half to double the reference scale -- wide enough that the
    # heuristic would have to be wrong by a factor of two for the rate to be
    # an artifact of it, and narrow enough that the validity mask still
    # admits most of the trace (at 4x it admits 3%, which measures the mask).
    swept = []
    for multiplier in (0.5, 1.0, 2.0):
        scale = reference["scale"] * multiplier
        gaze, v, mask = _gaze_velocity_mask(
            reference["raw"]["left"],
            reference["quality"]["LeftDataQuality"],
            recording.fs_hz,
            recording.frame_gaps,
            scale,
        )
        trace = _Trace("left", gaze, v, mask, recording.fs_hz)
        swept.append((multiplier, scale, trace))

    with capsys.disabled():
        print("  scale sweep, left eye, mask and detectors recomputed per scale:")
        for multiplier, scale, trace in swept:
            invalid = float(np.mean(trace.mask == Label.INVALID))
            print(
                f"    x{multiplier:<4.2f} scale={scale:.6g}  frac_invalid={invalid:.4f}  "
                f"microsaccade {trace.microsaccade_rate_per_s:.3f}/s  "
                f"all events {trace.event_rate_per_s:.3f}/s"
            )

    for multiplier, _scale, trace in swept:
        assert PAPER_MICROSACCADE_RATE_MIN_PER_S <= trace.microsaccade_rate_per_s <= (
            PAPER_MICROSACCADE_RATE_MAX_PER_S
        ), (
            f"left eye at x{multiplier} scale: {trace.microsaccade_rate_per_s:.3f} "
            f"microsaccades/s, outside the band -- the rate depends on the scale "
            f"heuristic, which is chosen rather than fitted"
        )


def test_the_main_sequence_statistic_is_measured_and_not_asserted(reference, capsys):
    """`log(peak velocity)` against `log(amplitude)`, measured and printed --
    and **deliberately not asserted on**.

    **This file asserted `r > 0.9` here for one commit, and the bound was
    withdrawn as invalid rather than relaxed.** The distinction is the whole
    point: a widened bound, or an `xfail`, leaves a threshold in the tree for
    a future reader to nudge until it passes. A withdrawn one with its
    refutation beside it cannot be misread.

    **The refutation, in three measurements.** Each was taken at the reference
    scale on this recording; where a figure comes from a configuration this
    test does not itself reproduce, that is said.

    1. **Engbert-Kliegl, the always-on baseline nobody is validating, scores
       LOWER on the identical trace** -- 0.7536 / 0.7415 against
       otero_millan's 0.8213 / 0.7750, same mask, same `measure` call. A bound
       the baseline fails harder is not evidence about the detector under
       test.
    2. **A random-span null out-correlates the detector.** Duration-matched:
       0.8719 / 0.8537. Drawn with durations spread uniformly over the
       detector's own range instead of matched to it: **0.9004** -- arbitrary
       spans of this recording *clear the withdrawn bound* while the working
       detector does not. (The wide-duration variant is measured in this
       task's report, not by this test, which ships the duration-matched
       control.) An independent measurement by the plan's author, at a
       configuration not recorded here, put the same pair at 0.657 for the
       detector and 0.905 for random spans -- the values move with
       configuration, the ordering does not.
    3. **A broken detector scores higher than a working one.** Removing both
       of `_accept`'s gates -- accepting the noise cluster and dropping the
       0.2 degree floor -- yields 11,590 left-eye events where the correct
       detector yields 4,700, and r = 0.9491 / 0.9398. **It passes the bound
       the correct detector fails.**

    **The mechanism, measured rather than assumed, because a wrong mechanism
    recorded confidently is worse than none.** Two channels, and only the
    first is the one the plan's author identified:

    - **Span-length spread inflates r.** Widening the control's duration
      range (sd of log duration 0.502 -> 0.910) lifts it 0.8719 -> 0.9004. A
      longer span has both a larger endpoint displacement and a higher
      maximum velocity, mechanically. A real detector's events are
      duration-selected, which compresses that spread and lowers r -- so on
      this axis the statistic rewards *unselective* detection, which is
      exactly why 11,590 spans of every length beat 4,700 chosen ones.
    - **But duration is not the whole story, and the data say so.**
      Partialling log duration out of the duration-matched control barely
      moves it (0.8719 -> 0.8670), and within narrow duration strata the
      control still beats the detector at every stratum -- 0.816 vs 0.747,
      0.865 vs 0.832, 0.880 vs 0.842, 0.898 vs 0.581 over spans of 10-14,
      14-18, 18-24 and 24-32 samples. Holding duration fixed does not rescue
      the statistic. The residue is the direct coupling: over any span,
      endpoint displacement and peak speed are both monotone in how far the
      eye moved, whether or not anything saccade-shaped happened.

    So the honest general form is the weaker and more useful one, and it is
    in the module docstring: **an oracle-free statistic is worthless until a
    null has been run against it.**

    **What is still asserted here.** Only that the measurement is not vacuous
    -- that both detectors and the control produced populations large enough
    for the printed correlations to mean anything. Without that, this test
    could silently print `nan` forever and look like it was watching
    something. It is not a bound on r, and nothing here should become one
    without a calibrated recording to define it against.
    """
    traces = reference["traces"]
    fitted = {}
    for trace in traces:
        om = _log_log_fit(trace.om_amplitude, trace.om_peak_velocity)
        ek = _log_log_fit(trace.ek_amplitude, trace.ek_peak_velocity)
        control_amplitude, control_velocity = _random_span_control(
            trace.gaze, trace.v, trace.mask, trace.om_duration, trace.fs_hz
        )
        control = _log_log_fit(control_amplitude, control_velocity)
        fitted[trace.name] = (om, ek, control)

    with capsys.disabled():
        print(
            "\n  main sequence: log(peak velocity) on log(amplitude) -- MEASURED, "
            f"NOT ASSERTED (the withdrawn bound was r > {_WITHDRAWN_MAIN_SEQUENCE_BOUND}; "
            "see this test's docstring for why it was withdrawn rather than widened)"
        )
        for trace in traces:
            om, ek, control = fitted[trace.name]
            floor = 0.2
            sub_floor = int(np.sum(trace.om_amplitude < floor))
            zero = int(np.sum(trace.om_amplitude == 0.0))
            above = trace.om_amplitude >= floor
            om_above = _log_log_fit(
                trace.om_amplitude[above], trace.om_peak_velocity[above]
            )
            print(f"    {trace.name}:")
            print(
                f"      otero_millan   r={om[0]:.4f}  n={om[1]}  slope={om[2]:.3f}  "
                f"log-residual sd={om[3]:.4f}"
            )
            print(
                f"      engbert_kliegl r={ek[0]:.4f}  n={ek[1]}  slope={ek[2]:.3f}  "
                f"log-residual sd={ek[3]:.4f}   <- the baseline, same trace, same measure"
            )
            print(
                f"      random spans   r={control[0]:.4f}  n={control[1]}  "
                f"slope={control[2]:.3f}  log-residual sd={control[3]:.4f}   "
                f"<- the null; it out-correlates the detector"
            )
            print(
                f"      otero_millan above the {floor} deg floor: r={om_above[0]:.4f}  "
                f"n={om_above[1]}  slope={om_above[2]:.3f}"
            )
            ek_above = float(np.mean(trace.ek_amplitude >= floor))
            print(
                f"      fraction at or above the {floor} deg floor: "
                f"otero_millan {float(np.mean(above)):.3f}, engbert_kliegl "
                f"{ek_above:.3f}, paper >{PAPER_FRACTION_ABOVE_FLOOR:.2f} -- "
                f"scale-determined, and the two detectors track each other"
            )
            print(
                f"      {sub_floor} of {len(trace.om_amplitude)} events below that "
                f"floor ({sub_floor / len(trace.om_amplitude):.1%}), {zero} at exactly "
                f"0.0 deg; amplitude median {np.median(trace.om_amplitude):.3f} deg, "
                f"duration median "
                f"{np.median(trace.om_duration) / trace.fs_hz * 1000:.0f} ms, "
                f"longest {trace.om_duration.max() / trace.fs_hz * 1000:.0f} ms"
            )

    # Non-vacuity only. Every population above has to be large enough that the
    # printed correlations describe something -- `_log_log_fit` returns `nan`
    # below three usable points, and a test that printed `nan` forever would
    # look like a measurement while being none. 1000 is far below the ~4,300
    # measured and far above the 3 the fit needs, so it fails on a collapse
    # rather than on ordinary variation.
    for trace in traces:
        om, ek, control = fitted[trace.name]
        for label, (r_value, n, _slope, _sd) in (
            ("otero_millan", om), ("engbert_kliegl", ek), ("random-span control", control)
        ):
            assert n > 1000, (
                f"{trace.name}: the {label} population is {n} events, too few for the "
                f"correlation printed above to describe anything -- this test measures a "
                f"statistic it does not assert, so an empty measurement is its only "
                f"real failure mode"
            )
            assert not np.isnan(r_value), (
                f"{trace.name}: the {label} correlation is nan over {n} events"
            )



def test_otero_millan_and_engbert_kliegl_agree_substantially_without_collapsing(
    reference, capsys
):
    """`event_f1` between the two detectors, strictly between 0.5 and 1.0.

    **Both bounds matter, and they are different claims.** Below 0.5 the two
    methods are not finding the same recording's events at all, which on two
    detectors sharing one velocity estimator, one validity mask and one
    measurement would mean something is broken rather than that the methods
    differ. At exactly 1.0 the reimplementation has collapsed into the
    baseline -- and that is the worse failure, because it would make the whole
    consensus suite vacuous: design spec section 3.1 makes this pair "the
    cleanest possible first exercise of section 6" precisely because they are
    two *independent* methods with identical expressive power, and a
    reimplementation that had quietly become a copy of Engbert-Kliegl would
    agree perfectly and prove nothing.

    **What this establishes**: that the two are neither the same detector nor
    unrelated ones. **What it does not**: that either is correct. Two
    detectors wrong in the same way agree; two wrong in different ways
    disagree. This bounds their relationship, not their accuracy -- which is
    the whole reason design spec section 3.2 asks for paper statistics as
    well, and the reason this file has three tests rather than one.
    """
    from wl_preproc.eye.detect.consensus import DEFAULT_EVENT_F1_TOLERANCE_SAMPLES

    scored = {trace.name: trace.f1_against_baseline() for trace in reference["traces"]}

    with capsys.disabled():
        print(
            f"\n  event_f1(otero_millan, engbert_kliegl), tolerance "
            f"{DEFAULT_EVENT_F1_TOLERANCE_SAMPLES} samples "
            f"({DEFAULT_EVENT_F1_TOLERANCE_SAMPLES / reference['recording'].fs_hz * 1000:.0f} "
            f"ms at this recording's rate), bound "
            f"{EK_OM_OVERLAP_MIN_F1} < f1 < {EK_OM_OVERLAP_MAX_F1}:"
        )
        for trace in reference["traces"]:
            f1, compared = scored[trace.name]
            print(
                f"    {trace.name:5s} f1={f1:.4f} over {compared} compared samples -- "
                f"otero_millan {len(trace.om_runs)} events, engbert_kliegl "
                f"{len(trace.ek_runs)} events"
            )

    for trace in reference["traces"]:
        f1, _compared = scored[trace.name]
        assert EK_OM_OVERLAP_MIN_F1 < f1, (
            f"{trace.name}: event_f1 = {f1:.4f}, at or below {EK_OM_OVERLAP_MIN_F1} -- two "
            f"detectors sharing one velocity estimator, one mask and one measurement should "
            f"not disagree this far"
        )
        assert f1 < EK_OM_OVERLAP_MAX_F1, (
            f"{trace.name}: event_f1 = {f1:.4f}, at {EK_OM_OVERLAP_MAX_F1} -- the "
            f"reimplementation has collapsed into engbert_kliegl and every consensus row "
            f"comparing them is vacuous"
        )
