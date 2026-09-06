"""Nystrom-Holmqvist against the paper's own numbers -- nulls first.

Design spec `2026-09-05-nystrom-holmqvist-design.md`, section 5 (Table 3) and
section 9. The paper:

    Nystrom, M., & Holmqvist, K. (2010). An adaptive algorithm for fixation,
    saccade, and glissade detection in eyetracking data. Behavior Research
    Methods, 42(1), 188-204. 10.3758/BRM.42.1.188

**The rule that governs this whole file, carried forward from the
Otero-Millan round** (`test_otero_millan_validation.py`'s own module
docstring, and design spec section 5's own closing paragraph):

    an oracle-free statistic is worthless until a null has been run against
    it.

That file shipped a main-sequence bound for one commit on the reasoning that
a log-log velocity/amplitude correlation "is a property of real saccades
that no artefact reproduces" -- then measured that a duration-matched
random-span control AND a detector with both acceptance gates removed both
scored HIGHER than the correct detector. The check was WITHDRAWN as invalid,
not relaxed. So here: the null (`_random_span_null`, `_glissadic_fraction`,
`test_the_null_fails_the_glissade_rate_check`) is written and run FIRST, and
everything below it depends on that test passing.

**What transfers and what does not** (spec section 5). The paper's data are
HUMAN, reading and scene perception, at 1250 Hz on an SMI HiSpeed. This rig
is NHP at 500 Hz on a dual-Purkinje tracker. Fixation and saccade DURATIONS
are behaviour and would not transfer -- neither is checked here. The
GLISSADE statistics (rate, duration) have a mechanistic reason to transfer:
a glissade is lens wobble, a property of the eye and the instrument rather
than the task, and spec section 2.5 argues a DPI should show MORE of it, not
less. Those are the two paper statistics this file checks.

**Do not validate the glissade rate against a synthetic fixture.** Task 6
found that on this repository's own `stepped_session` fixture, this
detector's conjunction carries only `{fixation, saccade}` -- no `pso` at
all -- because that fixture's planted transitions are constant-velocity
ramps with a hard stop and no post-saccadic excursion: a glissade is
impossible there BY CONSTRUCTION. A zero rate on a fixture built without any
wobble to find is the fixture, not the detector, and would look identical to
the velocity-estimator failure spec section 9 item 1 predicts. The rate
check below runs only against the real reference recording.

**Gated on `WLPP_OHDPI_REFERENCE`**, following `test_otero_millan_
validation.py`'s own idiom exactly -- see `_skip_reason()`. Never commit
that file.

**This file lives in `tests/eye/`, so it imports nothing from
`wl_preproc.schema`** -- same constraint `test_otero_millan_validation.py`
records, for the same reason (the 3.13 cross-check runs `tests/eye` and
`tests/contracts` with `--noconftest`, in a venv with no DataJoint). The
conjunction trace is therefore not measured here: every check below reads
`detect_nystrom_holmqvist`'s own per-eye output directly, which is also the
correct thing for the glissade rate specifically -- design spec section 6
requires the KIND-disagreement measurement to come from the per-eye traces,
never the conjunction, since `_insert_trace` paints every un-intersected
sample `fixation` and a rate measured off the stored conjunction could
silently look like zero for a reason that has nothing to do with the
velocity estimator.

**REMoDNaV** (`test_remodnav_finds_a_comparable_number_of_saccades`) is a
`dev`-only, PyPI, MIT-licensed dependency (`pyproject.toml`), never a
runtime dependency and never shipped (parent design spec section 3.2). It is
an oracle, not a specification: it deliberately changed parts of
Nystrom-Holmqvist's own method, so agreement is evidence and disagreement is
not automatically a defect. Its actual Python API (`remodnav.
EyegazeClassifier`, `.preproc`, `.__call__`, the event `label` vocabulary)
was read directly from the installed package's own `clf.py` (PyPI remodnav
1.1.2) before being called here, not recalled -- the same rule this
repository applies to a paper applies to a dependency's API surface.
"""

from __future__ import annotations

import os
import time
from collections import namedtuple

import numpy as np
import pytest

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.nystrom_holmqvist import (
    DEFAULT_NH_PARAMS,
    detect_nystrom_holmqvist,
)

# --------------------------------------------------------------------------
# What the paper reports (design spec section 5, Table 3, reading / scene
# perception), restated here only for the print statements below -- the
# actual asserted bounds are wider bands, not these point values, and the
# reason is stated at each assertion rather than assumed.
# --------------------------------------------------------------------------
PAPER_GLISSADIC_FRACTION_READING = 0.478
PAPER_GLISSADIC_FRACTION_SCENE = 0.591
PAPER_GLISSADE_DURATION_MS_READING = (22.2, 9.8)  # mean, sd
PAPER_GLISSADE_DURATION_MS_SCENE = (25.0, 9.8)  # mean, sd

#: A band, not the paper's point value -- see `test_the_glissade_rate_is_in_
#: the_papers_band`'s own docstring for why.
GLISSADE_RATE_MIN = 0.20
GLISSADE_RATE_MAX = 0.90

#: The low tens of milliseconds -- see `test_the_glissade_duration_is_in_
#: the_low_tens_of_milliseconds`'s own docstring for both failure directions.
GLISSADE_DURATION_MS_MIN = 5.0
GLISSADE_DURATION_MS_MAX = 60.0

#: `test_the_null_fails_the_glissade_rate_check`'s own ceiling: far below the
#: paper's 47.8%, and measured (see that test and this task's report) to sit
#: at 0.008-0.030 across seeds 0-19 with this same construction -- comfortable
#: headroom under 0.10, not a bound tuned to just barely clear it.
NULL_GLISSADE_RATE_CEILING = 0.10

#: REMoDNaV oracle comparison, restricted to a leading slice of the
#: recording rather than the full ~39 minutes / 1.17M samples -- following
#: REMoDNaV's own test suite's precedent (`remodnav/tests/test_detect.py::
#: test_real_data` slices a ~1000 Hz recording to `p[:50000]` for the same
#: reason): its classifier is a pure-Python, windowed algorithm with no
#: vectorised fast path, and running it end-to-end on this rig's full
#: recording would make this one test dominate the suite's wall clock for a
#: comparison that does not need the whole recording to be meaningful.
REMODNAV_COMPARISON_SAMPLES = 120_000

#: The larger axis's 99th percentile of the raw Purkinje difference, placed
#: here in degrees. Restated from `test_otero_millan_validation.py`, which
#: documents the choice at length, for that file's own stated reason: two
#: independent definitions of one heuristic is how they drift apart.
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


# --------------------------------------------------------------------------
# Step 1: the null. Written and run FIRST -- see module docstring.
# --------------------------------------------------------------------------

_Span = namedtuple("_Span", "start stop")


def _random_span_null(runs, n_samples, rng):
    """A duration-matched random-span control: the same number of spans, with
    the same durations, placed uniformly at random.

    **This exists before the checks below, not after them.** Stage 2A adopted
    a main-sequence statistic because it "is a property of real saccades that
    no artefact reproduces", then measured that a random control AND a
    detector with both acceptance gates removed both scored HIGHER than the
    correct detector. The check was withdrawn as invalid rather than relaxed.
    The rule that episode left: an oracle-free statistic is worthless until a
    null has been run against it, and building the null is cheap.

    **Returns `_Span`s, not the bare `(start, start + duration)` tuples a
    first reading of this function's own name might suggest** -- a
    documented, deliberate choice (task 7 report): `_glissadic_fraction`
    below also has to accept the real `Run`s `detect_nystrom_holmqvist`
    returns, and `Run` (`labels.py`) is a frozen dataclass with `.start`/
    `.stop` attributes and no `__getitem__` at all. A bare 2-tuple supports
    only index access, which would force `_glissadic_fraction` into either
    two incompatible implementations or a runtime `isinstance` branch; a
    namedtuple with the same field names as `Run` lets one function read
    both.
    """
    durations = [run.stop - run.start for run in runs]
    spans = []
    for duration in durations:
        start = int(rng.integers(0, max(n_samples - duration, 1)))
        spans.append(_Span(start, start + duration))
    return sorted(spans)


def _glissadic_fraction(saccades, glissades, tau_samples):
    """The fraction of `saccades` followed within `tau_samples` by a
    glissade starting at or after that saccade's own offset.

    Stated generically over anything exposing `.start`/`.stop` -- `_Span`s
    from the null above, or real `Run`s `detect_nystrom_holmqvist` returns --
    so the reference-recording checks run the IDENTICAL statistic the null
    is shown to fail, not a second, similar-looking one.

    Half-open, matching `Run`'s own convention: a glissade starting anywhere
    in `[saccade.stop, saccade.stop + tau_samples)` counts as a hit.

    **On real detector output this reduces to `len(glissades) /
    len(saccades)` exactly**, and that is expected rather than a loss of
    precision: `_glissade_bounds` (`nystrom_holmqvist.py`) always sets a
    glissade's own onset to its saccade's offset, one saccade produces at
    most one glissade, and two saccades' spans never overlap -- so every
    stored glissade is a hit for exactly the one saccade it followed, for
    any `tau_samples >= 1`. The generality is what lets the SAME function
    measure a null built from spans with no such relationship at all.
    """
    if not saccades:
        return 0.0
    starts = np.asarray(sorted(glissade.start for glissade in glissades), dtype=np.int64)
    hits = 0
    for saccade in saccades:
        lo, hi = saccade.stop, saccade.stop + tau_samples
        index = int(np.searchsorted(starts, lo, side="left"))
        if index < starts.size and starts[index] < hi:
            hits += 1
    return hits / len(saccades)


def test_the_null_fails_the_glissade_rate_check():
    """If the null passes, the check does not discriminate and must be
    WITHDRAWN, not relaxed. This test is what says the checks below mean
    something."""
    rng = np.random.default_rng(7)
    # A random control has no saccade-glissade adjacency at all, so the
    # fraction of its "saccades" followed within tau_min by a "glissade" is
    # chance-level, far below the paper's 47.8%.
    n_samples = 500_000
    fake_saccades = _random_span_null([_Span(0, 20)] * 500, n_samples, rng)
    fake_glissades = _random_span_null([_Span(0, 12)] * 500, n_samples, rng)

    rate = _glissadic_fraction(fake_saccades, fake_glissades, tau_samples=20)

    assert rate < NULL_GLISSADE_RATE_CEILING, (
        f"a random control scored {rate:.3f} on the glissade-rate check; the "
        "check does not discriminate and must be withdrawn, not relaxed"
    )


# --------------------------------------------------------------------------
# Steps 3-5: against the reference recording. Every test below is gated on
# `WLPP_OHDPI_REFERENCE` and depends on the null above having already passed.
# --------------------------------------------------------------------------


def _scaled_affine_map(scale: float):
    """`degrees = scale * raw_px` on both axes, no cross terms, no offset --
    restated from `test_otero_millan_validation.py`, not imported, per that
    file's own stated reason."""
    from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel

    return CalibrationMap(model=CalibrationModel.AFFINE, x=(0.0, scale, 0.0), y=(0.0, 0.0, scale))


def _gaze_velocity_mask(raw_xy, quality, fs_hz, frame_gaps, scale):
    """One eye's gaze at `scale`, its velocity, and its validity mask.
    Restated from `test_otero_millan_validation.py`, not imported."""
    from wl_preproc.eye.calibration import apply_map
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS, validity_labels
    from wl_preproc.eye.detect.velocity import velocity

    gaze = apply_map(_scaled_affine_map(scale), raw_xy)
    v = velocity(gaze, fs_hz)
    return gaze, v, validity_labels(gaze, v, quality, frame_gaps, DEFAULT_VALIDITY_PARAMS).labels


class _Trace:
    """One eye's own runs from `detect_nystrom_holmqvist`, at the reference
    recording's real sampling rate -- everything the checks below need,
    computed once.

    **Per-eye, not the conjunction** -- this module's own docstring states
    why: design spec section 6 requires it, and reading it off the
    conjunction instead would risk measuring `_insert_trace`'s own
    fixation-fill rather than this detector's real output.
    """

    def __init__(self, name: str, runs: list, fs_hz: float):
        self.name = name
        self.runs = runs
        self.fs_hz = fs_hz
        self.saccades = [run for run in runs if run.label == Label.SACCADE]
        self.glissades = [run for run in runs if run.label == Label.PSO]
        # The paper's own tau_min (Table 2), in samples at this recording's
        # rate -- the same window `_glissade_bounds` itself searches after a
        # saccade, so this asks the identical question the detector already
        # answered rather than a looser one.
        self.tau_samples = max(
            int(round(DEFAULT_NH_PARAMS.min_fixation_duration_ms * fs_hz / 1000.0)), 1
        )

    @property
    def glissade_rate(self) -> float:
        return _glissadic_fraction(self.saccades, self.glissades, self.tau_samples)

    @property
    def glissade_durations_ms(self) -> np.ndarray:
        return np.array(
            [(run.stop - run.start) / self.fs_hz * 1000.0 for run in self.glissades]
        )


@pytest.fixture(scope="module")
def reference():
    """The recording, read once, with both eyes run through
    `detect_nystrom_holmqvist`. Module-scoped so the (skipped, in this
    environment) three tests below share one 633 MB read rather than three.

    Mirrors `test_otero_millan_validation.py`'s own `reference` fixture --
    same recording, same restated scale heuristic -- detecting with
    `nystrom_holmqvist` instead, since this file's checks are about
    glissades, which only this detector emits.
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

    pooled_x = np.concatenate([np.abs(raw["left"][:, 0]), np.abs(raw["right"][:, 0])])
    pooled_y = np.concatenate([np.abs(raw["left"][:, 1]), np.abs(raw["right"][:, 1])])
    p99_x = float(np.percentile(pooled_x, 99))
    p99_y = float(np.percentile(pooled_y, 99))
    scale = _SCALE_P99_AT_DEG / max(p99_x, p99_y)

    def build(eye_name: str, column: str) -> _Trace:
        gaze, v, mask = _gaze_velocity_mask(
            raw[eye_name], quality[column], recording.fs_hz, recording.frame_gaps, scale
        )
        runs = detect_nystrom_holmqvist(gaze, v, mask, recording.fs_hz, DEFAULT_NH_PARAMS)
        return _Trace(eye_name, runs, recording.fs_hz)

    return {
        "sample": sample,
        "recording": recording,
        "raw": raw,
        "read_s": read_s,
        "scale": scale,
        "p99": (p99_x, p99_y),
        "traces": [build("left", "LeftDataQuality"), build("right", "RightDataQuality")],
    }


@pytest.mark.skipif(
    not os.environ.get("WLPP_OHDPI_REFERENCE"),
    reason="needs the real reference recording",
)
def test_the_glissade_rate_is_in_the_papers_band(reference, capsys):
    """Spec section 5, Table 3: 47.8% of saccades carry a glissade in
    reading, 59.1% in scene perception, as the union of both criteria.

    A BAND, not the point value, and the reason is stated rather than
    assumed: the paper's data are HUMAN, reading and scene perception, at
    1250 Hz on an SMI HiSpeed. This rig is NHP at 500 Hz on a dual-Purkinje
    tracker. What has a mechanistic reason to transfer is the glissade
    statistics -- a glissade is lens wobble, a property of the eye and the
    instrument rather than the task -- and section 2.5 argues a DPI should
    show MORE of it, not less.

    **A rate near zero indicts the velocity estimator first** (spec section
    2, section 9 item 1): the shared five-point differentiator may be
    smoothing ~20 ms wobbles away, which is the one consequence of not using
    the paper's Savitzky-Golay. Do not switch the estimator on any other
    evidence.

    **Adapted from the task brief's own no-argument sketch to this module's
    shared `reference` fixture** (task 7 report) -- the assertion and its
    message are unchanged; only the wiring that avoids re-reading a 633 MB
    file once per test is new, matching `test_otero_millan_validation.py`'s
    own established convention.
    """
    recording = reference["recording"]
    with capsys.disabled():
        print(f"\n  reference recording: {reference['sample']}")
        print(
            f"  {recording.n_frames} frames, {recording.fs_hz:.2f} Hz, "
            f"scale {reference['scale']:.6g} deg/px (pooled p99 raw "
            f"{reference['p99']} px)"
        )
        print(
            f"  glissade rate -- paper: {PAPER_GLISSADIC_FRACTION_READING:.3f} "
            f"reading / {PAPER_GLISSADIC_FRACTION_SCENE:.3f} scene perception "
            f"(spec section 5); band asserted here {GLISSADE_RATE_MIN}-"
            f"{GLISSADE_RATE_MAX}:"
        )
        for trace in reference["traces"]:
            print(
                f"    {trace.name:5s} {len(trace.saccades):6d} saccades, "
                f"{len(trace.glissades):6d} glissades -- rate "
                f"{trace.glissade_rate:.3f}"
            )

    for trace in reference["traces"]:
        rate = trace.glissade_rate
        assert GLISSADE_RATE_MIN <= rate <= GLISSADE_RATE_MAX, (
            f"measured {rate:.3f}; the paper reports 0.478 (reading) and 0.591 "
            "(scene perception). Near zero indicts the velocity estimator "
            "(spec §2); far above 0.9 suggests the offset threshold is too low."
        )


@pytest.mark.skipif(
    not os.environ.get("WLPP_OHDPI_REFERENCE"),
    reason="needs the real reference recording",
)
def test_the_glissade_duration_is_in_the_low_tens_of_milliseconds(reference, capsys):
    """Spec section 5: glissade duration 22.2 +/- 9.8 ms (reading), 25.0 +/-
    9.8 ms (scene perception) -- the low tens of milliseconds. Same gating
    and the same transfer argument as the rate check above: a glissade's
    DURATION, like its rate, is a property of the eye and the tracker
    rather than the task.

    Both failure directions are stated, the way the rate check's is:
    hundreds of milliseconds means glissades are being merged with the
    fixations that follow them; single-digit means they are being truncated
    at the offset threshold.
    """
    with capsys.disabled():
        print(
            f"\n  glissade duration -- paper: "
            f"{PAPER_GLISSADE_DURATION_MS_READING[0]}+/-"
            f"{PAPER_GLISSADE_DURATION_MS_READING[1]} ms reading, "
            f"{PAPER_GLISSADE_DURATION_MS_SCENE[0]}+/-"
            f"{PAPER_GLISSADE_DURATION_MS_SCENE[1]} ms scene perception "
            f"(spec section 5); band asserted here {GLISSADE_DURATION_MS_MIN}-"
            f"{GLISSADE_DURATION_MS_MAX} ms:"
        )
        for trace in reference["traces"]:
            durations = trace.glissade_durations_ms
            if durations.size:
                print(
                    f"    {trace.name:5s} n={durations.size:5d}  "
                    f"mean={durations.mean():.1f} ms  sd={durations.std():.1f} ms"
                )
            else:
                print(f"    {trace.name:5s} no glissades measured")

    for trace in reference["traces"]:
        durations = trace.glissade_durations_ms
        assert durations.size > 0, (
            f"{trace.name}: no glissades measured at all -- the rate check "
            "above is the one that should catch this; seeing it here too "
            "means the population is empty rather than merely out of band"
        )
        mean_ms = float(durations.mean())
        assert GLISSADE_DURATION_MS_MIN <= mean_ms <= GLISSADE_DURATION_MS_MAX, (
            f"{trace.name}: mean glissade duration {mean_ms:.1f} ms outside "
            f"{GLISSADE_DURATION_MS_MIN}-{GLISSADE_DURATION_MS_MAX} ms. Hundreds "
            "of milliseconds means glissades are being merged with the "
            "fixations that follow them; single-digit means they are being "
            "truncated at the offset threshold."
        )


def _remodnav_saccade_count(remodnav_module, raw_xy: np.ndarray, fs_hz: float, px2deg: float) -> int:
    """Run REMoDNaV's own `EyegazeClassifier` on `raw_xy` (raw pixels, the
    same trace `reference`'s own `gaze` is calibrated from) and count its
    saccade-labelled events.

    **Read directly from the installed package's own `remodnav/clf.py`
    (PyPI remodnav 1.1.2) before being written, not recalled** -- design
    spec section 3.2's rule about verifying a source before writing a claim
    about it applies to a dependency's API exactly as it does to a paper.
    `EyegazeClassifier(px2deg, sampling_rate)` takes a scalar, isotropic
    `px2deg` -- exactly what `reference`'s own `scale` already is, since it
    too maps raw pixels to degrees with one factor on both axes
    (`_scaled_affine_map`). `.preproc(data)` wants a structured array with
    `x`/`y` fields in raw pixels and returns one with `vel`/`med_vel` added;
    calling the classifier on that result returns a list of event dicts
    whose `label` is one of `{FIXA, PURS, SACC, ISAC, HPSO, IHPS, LPSO,
    ILPS}` -- `SACC` and `ISAC` are its own two saccade classes (a
    major-pass saccade and one its own intersaccade-refinement pass finds;
    both are "saccades" in its own vocabulary, per its `tests/test_detect.py
    ::test_real_data`, which asserts both labels are present in real data).
    """
    data = np.rec.fromarrays([raw_xy[:, 0], raw_xy[:, 1]], names=["x", "y"])
    classifier = remodnav_module.EyegazeClassifier(px2deg=px2deg, sampling_rate=fs_hz)
    preprocessed = classifier.preproc(data)
    events = classifier(preprocessed)
    return sum(1 for event in events if event["label"] in ("SACC", "ISAC"))


@pytest.mark.skipif(
    not os.environ.get("WLPP_OHDPI_REFERENCE"),
    reason="needs the real reference recording",
)
def test_remodnav_finds_a_comparable_number_of_saccades(reference, capsys):
    """Parent design spec section 3.2 / this spec's section 5: REMoDNaV is
    "a genuine runnable oracle" -- MIT, on PyPI, a `dev`-only dependency,
    never shipped and never a runtime dependency (`pyproject.toml`).

    **It is an oracle, not a specification.** REMoDNaV deliberately changed
    parts of Nystrom-Holmqvist's own method, so this does not compare event
    BOUNDARIES, only that the two find a COMPARABLE NUMBER of saccades on
    the identical raw trace -- within a factor of two. Disagreement beyond
    that is evidence, not automatically a defect; a tighter assertion would
    pin this detector's own output to REMoDNaV's choices, which is exactly
    the confound design spec section 3.2 warns a reimplementation must not
    manufacture.

    Restricted to a leading slice of the recording -- see
    `REMODNAV_COMPARISON_SAMPLES`'s own comment for why -- with this
    detector's own count taken over the SAME slice (saccades wholly
    contained in it), not its full-recording count, so both sides of the
    comparison see the same trace.
    """
    remodnav = pytest.importorskip("remodnav")
    recording = reference["recording"]
    fs_hz = recording.fs_hz
    limit = min(REMODNAV_COMPARISON_SAMPLES, recording.n_frames)

    with capsys.disabled():
        print(
            f"\n  REMoDNaV oracle comparison over the first {limit} samples "
            f"({limit / fs_hz:.0f}s of {recording.n_frames / fs_hz:.0f}s), "
            f"px2deg={reference['scale']:.6g}:"
        )

    for trace in reference["traces"]:
        raw_xy = reference["raw"][trace.name][:limit]
        theirs = _remodnav_saccade_count(remodnav, raw_xy, fs_hz, reference["scale"])
        ours = sum(1 for run in trace.saccades if run.stop <= limit)
        with capsys.disabled():
            print(
                f"    {trace.name:5s} nystrom_holmqvist={ours:5d}  remodnav={theirs:5d}"
            )
        ratio = max(ours, theirs) / max(min(ours, theirs), 1)
        assert ratio <= 2.0, (
            f"{trace.name}: nystrom_holmqvist found {ours} saccades, REMoDNaV "
            f"found {theirs}, over the same {limit}-sample slice -- more than "
            "a factor of two apart. REMoDNaV is an oracle, not a specification "
            "(spec section 5), so this is not automatically a defect, but a "
            "gap this wide is worth looking at before trusting either count."
        )
