"""Otero-Millan's cluster detector, and the vocabulary correction it honours.

Design spec section 3.1/3.2 were CORRECTED on 2026-09-01 by reading the MATLAB
reference: this detector finds saccades of ANY amplitude, its vocabulary is
`saccade / microsaccade`, and its only amplitude rule is a 0.2 degree LOWER
noise floor on a candidate cluster's mean displacement. These tests are written
against that correction, not against the pre-correction spec.
"""

from __future__ import annotations

import numpy as np
import pytest

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.otero_millan import (
    DEFAULT_OM_PARAMS,
    OteroMillanParams,
    detect_otero_millan,
)


def _trace(events, n_samples=4000, seed=0):
    """A gaze trace with planted step events, plus low-amplitude noise.

    Returns `(gaze_deg, velocity_deg_s, available)` in the detector contract's
    own shapes. Velocity is the 5-point estimator the whole subsystem shares,
    so this fixture cannot disagree with production about what velocity is.
    """
    from wl_preproc.eye.detect.velocity import velocity

    rng = np.random.default_rng(seed)
    gaze = rng.normal(0.0, 0.02, size=(n_samples, 2))
    for start, stop, size in events:
        gaze[start:, 0] += size
        ramp = np.linspace(0.0, size, stop - start)
        gaze[start:stop, 0] += ramp - size
    v = velocity(gaze, fs_hz=500.0)
    available = np.full(n_samples, None, dtype=object)
    return gaze, v, available


def test_a_planted_large_saccade_is_detected_and_labelled_saccade():
    """Design spec section 3.1, corrected 2026-09-01: this detector's
    vocabulary is `saccade / microsaccade`, not `microsaccade` alone. The
    reference's only amplitude threshold is a 0.2 degree LOWER noise floor on a
    cluster's mean displacement -- there is no upper bound anywhere in it, and
    a 4 degree event must come back labelled `saccade`.

    **Every planted event at its planted time, not "some event was found"** --
    `test_engbert_kliegl.py::test_it_finds_planted_saccades_at_their_planted_
    times` states the reason and this file inherits it: a detector suite that
    only counts events has the same hole the eye plan's did.

    **The bounds are SIGNED, because the deviation has a direction and an
    unsigned bound would accept things that never happen.** Both effects that
    move an edge move it outward: the shared 5-point velocity estimator is
    CENTRED -- velocity at sample `n` reads `gaze[n + 2]` -- so velocity rises
    before the position ramp does; and this detector then walks the boundary
    out to the last sample below `_SACCADE_LIMIT_DEG_S`, which lands wherever
    noise drops back under 5 deg/s. The span is therefore always a DILATION of
    the planted event, never a shift. Measured over 300 seeds, all 300 giving
    exactly three saccades: `start - planted_start` lies in [-8, -1] and
    `stop - planted_stop` in [+1, +9] -- every one of 900 edges, no exceptions.

    So the assertion is `-10 <= start - planted <= 0` and
    `0 <= stop - planted <= 10`. An `abs(...) <= 10` would additionally accept
    a start ten samples LATE, which no run of this detector has ever produced
    and which would mean it had missed the event's onset entirely. Ten covers
    the measured extremes with margin while staying 100x tighter than the
    1000-sample spacing between planted events, so it cannot silently accept a
    detection of the wrong event.

    This tolerance was +/-3 for one round, borrowed from
    `test_engbert_kliegl.py`. It does not transfer: that detector delimits an
    event by a STATISTICAL threshold (`lambda_` times a median scale), and
    this one by a fixed 5 deg/s walk-out, which is the noisier of the two
    boundaries. At +/-3 only 151 of 200 seeds pass -- and on every one of the
    49 that fail, the detector found exactly three 4-degree saccades in the
    right places. That was a wrong number in the test, not a wrong detection.

    The brief for this task asserted `1000 <= start <= 1030` instead, which no
    correct implementation of this detector can satisfy: measured, the spans
    begin at 997, 1999 and 2999.
    """
    planted = [(1000, 1020), (2000, 2020), (3000, 3020)]
    gaze, v, available = _trace([(start, stop, 4.0) for start, stop in planted])

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    assert len(intervals) == len(planted)
    for interval, (want_start, want_stop) in zip(intervals, planted, strict=True):
        assert -10 <= interval.start - want_start <= 0
        assert 0 <= interval.stop - want_stop <= 10
    assert all(interval.label is Label.SACCADE for interval in intervals)


def test_a_planted_small_event_is_labelled_microsaccade():
    """The other half of the declared vocabulary, and the reason
    `microsaccade_max_deg` is declared on this detector's params."""
    gaze, v, available = _trace([(1000, 1015, 0.4), (2000, 2015, 0.4), (3000, 3015, 0.4)])

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    assert intervals
    assert all(interval.label is Label.MICROSACCADE for interval in intervals)


def test_both_sizes_in_one_trace_are_split_at_the_shared_threshold():
    """The split comes from `measure.classify` against the SHARED
    `microsaccade_max_deg`, not from anything private to this detector --
    design spec section 3's argument for measuring centrally applies to the
    threshold that measurement is compared against.

    **The two sizes are planted at MATCHED PEAK VELOCITY, which is what makes
    this a test of the split rather than of the clustering.** 0.7 degrees over
    8 samples and 1.5 degrees over 16 both ramp at ~50 deg/s, so every planted
    event lands in ONE velocity cluster and nothing in `ClusterPeaks` can be
    what separates them: the only thing that could put `saccade` on one and
    `microsaccade` on the other is `classify` reading the amplitude. Events
    differing in velocity as well as amplitude would leave the assertion
    passing for the wrong reason -- and, measured, would not pass at all: two
    0.4 degree events among 27 candidate spans are two points, not a cluster,
    and this detector's stopping rule (mean silhouette over the BINARISED
    partition) keeps the cluster count that isolates the fast events and stops
    there. The brief's original fixture for this test asserted both labels
    from such a trace and returned only the large ones, on every seed tried.
    """
    gaze, v, available = _trace(
        [
            (700, 708, 0.7), (1300, 1316, 1.5), (1900, 1908, 0.7),
            (2500, 2516, 1.5), (3100, 3108, 0.7), (3500, 3516, 1.5),
        ]
    )

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)
    labels = {interval.label for interval in intervals}

    assert Label.SACCADE in labels
    assert Label.MICROSACCADE in labels


def test_it_returns_nothing_on_a_trace_with_no_events():
    """Pure noise has no cluster whose mean displacement clears 0.2 degrees.
    A detector that returns events from noise would make every agreement score
    meaningless, and this is the cheapest place to catch it."""
    gaze, v, available = _trace([])

    assert detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS) == []


def test_unavailable_samples_are_never_claimed():
    """The validity mask (design spec section 2) owns those samples. A blink's
    velocity spike must not become an event, and must not inflate the
    clustering's feature distribution either."""
    gaze, v, available = _trace([(1000, 1020, 4.0), (2000, 2020, 4.0), (3000, 3020, 4.0)])
    available[990:1030] = Label.BLINK

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    for interval in intervals:
        assert not (interval.start < 1030 and 990 < interval.stop)


def test_it_is_deterministic():
    """**The property that makes an agreement metric meaningful.** The
    reference seeds k-means from velocity-sorted quantile means rather than
    randomly, and this reimplementation preserves that: a detector whose output
    varied run to run would make every score irreproducible and every
    disagreement unattributable to a method."""
    gaze, v, available = _trace([(1000, 1020, 4.0), (2000, 2020, 4.0), (3000, 3020, 4.0)])

    first = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)
    second = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    assert [(i.start, i.stop, i.label) for i in first] == [
        (i.start, i.stop, i.label) for i in second
    ]


def test_determinism_extends_to_the_reliability_value_itself():
    """`test_it_is_deterministic` compares spans and labels, which survive a
    k-means that converged to a different partition of the NOISE cluster. The
    stored `reliability` does not: it is that span's own silhouette against
    whatever partition was chosen, so a run-to-run difference invisible in the
    spans is visible here. Byte-identical is the claim, so `==` on floats is
    the right comparison rather than `pytest.approx`."""
    gaze, v, available = _trace([(1000, 1020, 4.0), (2000, 2020, 4.0), (3000, 3020, 4.0)])

    first = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)
    second = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    assert [i.reliability for i in first] == [i.reliability for i in second]


def test_reliability_is_populated_per_detection():
    """`EyeDetection.Run.reliability` exists for this detector (design spec
    section 5) and has been null for every row so far. `silhouette()` is
    inherently per-observation; the MATLAB reference computes it per peak and
    keeps only `mean(...)` as a session statistic, so the per-detection value
    is available in the method and this reimplementation retains it."""
    gaze, v, available = _trace([(1000, 1020, 4.0), (2000, 2020, 4.0), (3000, 3020, 4.0)])

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    assert intervals
    assert all(interval.reliability is not None for interval in intervals)
    assert all(-1.0 <= interval.reliability <= 1.0 for interval in intervals)


def test_a_lone_accepted_detection_is_maximally_confident_not_minimally():
    """**The wrong end of the scale, and nothing caught it.** `reliability` is
    a silhouette, and a point alone in its cluster has no within-cluster
    distance -- so `a = 0` and `s = (b - 0) / b = 1`. Maximum confidence. This
    module skipped `count <= 1` and left the array's zero for one round, which
    stored the value meaning "no confidence" for exactly the detections the
    clustering was most certain about.

    It was invisible because the only assertions anywhere were `is not None`
    and `-1 <= r <= 1`, both of which 0.0 satisfies. Measured on this fixture,
    36 of 40 seeds yield a single accepted detection and all 36 stored 0.0.

    This is the column design spec section 3.2's paper-statistic validation
    will run on, so an inverted value here is not a cosmetic defect -- it is a
    validation that would be wrong in precisely the confident cases.
    """
    gaze, v, available = _trace([(2000, 2020, 4.0)])

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    assert len(intervals) == 1
    assert intervals[0].reliability == 1.0


def test_the_singleton_convention_is_the_divisor_not_a_special_case():
    """The value above comes from the reference's `max(count - 1, 1)` divisor,
    which is one expression covering both cases -- not a branch that a later
    edit could drop while leaving the ordinary path working. Asserted directly
    on `_silhouette` so the convention is pinned where it lives.

    A cluster of one against a cluster of many: the singleton scores 1, and the
    members of the larger cluster score by the ordinary formula.
    """
    from wl_preproc.eye.detect.otero_millan import _silhouette

    whitened = np.array([[10.0], [0.0], [0.1], [-0.1]])
    labels = np.array([1, 2, 2, 2])

    scores = _silhouette(whitened, labels)

    assert scores[0] == 1.0
    assert all(0.0 < value < 1.0 for value in scores[1:])


def test_the_noise_floor_is_a_lower_bound_and_is_the_only_amplitude_rule():
    """Guards the correction this task exists to honour. Raising the floor
    above a planted event's size must silence it; there is no upper bound that
    could silence a large one."""
    gaze, v, available = _trace([(1000, 1015, 0.4), (2000, 2015, 0.4), (3000, 3015, 0.4)])
    strict = OteroMillanParams(
        min_cluster_displacement_deg=2.0,
        max_clusters=DEFAULT_OM_PARAMS.max_clusters,
        min_isi_samples=DEFAULT_OM_PARAMS.min_isi_samples,
        microsaccade_max_deg=DEFAULT_OM_PARAMS.microsaccade_max_deg,
    )

    assert detect_otero_millan(gaze, v, available, strict) == []


def test_the_slowest_cluster_is_never_accepted_however_large_its_displacement():
    """**A mutation check found this untested.** `_accept` iterates clusters
    `1 .. n-1`, never `1 .. n`, because the method's structure is that there is
    always a non-saccadic population to separate the saccades FROM. Widening
    that range by one passed every other test in this file, because on these
    fixtures the slowest cluster's mean displacement is ~0.06 degrees and the
    floor refuses it anyway -- so the floor was silently doing the last
    cluster's job, and the rule itself was unasserted.

    Asserted on a constructed partition rather than on a trace, because the
    case that distinguishes the two rules is a slowest cluster whose
    displacement is LARGE, and a detector working correctly never produces
    one. If it ever did -- a recording of nothing but saccades -- accepting it
    would return the whole trace as events.
    """
    from wl_preproc.eye.detect.otero_millan import _accept

    assignment = np.array([1, 1, 2, 2, 3, 3])
    displacement = np.array([4.0, 4.0, 3.0, 3.0, 9.0, 9.0])

    accepted = _accept(assignment, displacement, 0.2)

    assert accepted.tolist() == [True, True, True, True, False, False]


def test_a_cluster_is_accepted_or_refused_whole_on_its_mean():
    """The floor is on the cluster's MEAN, not on each event -- which is what
    keeps it a statement about a population rather than an amplitude threshold
    applied one event at a time. A cluster whose mean clears the floor carries
    its sub-floor members with it, and one whose mean does not loses its large
    ones. Both directions, because only asserting the first would leave
    per-event filtering indistinguishable from this rule."""
    from wl_preproc.eye.detect.otero_millan import _accept

    assignment = np.array([1, 1, 2, 2, 2, 3])
    # Cluster 1 clears the floor on its mean (2.005) while holding a member of
    # 0.01; cluster 2 fails on its mean (0.173) while holding one of 0.5. Both
    # means are stated because getting them wrong is how this test would pass
    # for the wrong reason -- the first draft gave cluster 2 a mean of 0.26 and
    # asserted it was refused.
    displacement = np.array([4.0, 0.01, 0.01, 0.01, 0.5, 9.0])

    accepted = _accept(assignment, displacement, 0.2)

    assert accepted.tolist() == [True, True, False, False, False, False]


def test_a_large_saccade_survives_a_floor_that_silences_a_small_one():
    """The other direction of the same rule, and the one the correction is
    actually about. `test_the_noise_floor_is_a_lower_bound...` shows a floor
    silencing something; only this shows that raising it does NOT silence a
    large event, which is what distinguishes a lower bound from a band."""
    gaze, v, available = _trace([(1000, 1020, 4.0), (2000, 2020, 4.0), (3000, 3020, 4.0)])
    strict = OteroMillanParams(
        min_cluster_displacement_deg=2.0,
        max_clusters=DEFAULT_OM_PARAMS.max_clusters,
        min_isi_samples=DEFAULT_OM_PARAMS.min_isi_samples,
        microsaccade_max_deg=DEFAULT_OM_PARAMS.microsaccade_max_deg,
    )

    intervals = detect_otero_millan(gaze, v, available, strict)

    assert intervals
    assert all(interval.label is Label.SACCADE for interval in intervals)


def test_the_returned_intervals_are_disjoint_and_ordered():
    """`schema/detect.py::_insert_trace` writes each interval's label onto one
    mask, so two overlapping intervals would silently let the later one
    overwrite the earlier. The reference merges overlapping peak limits before
    it ever computes a feature, and this reimplementation keeps that -- which
    is also what makes an exact-span `reliability` lookup well defined."""
    gaze, v, available = _trace(
        [(700, 708, 0.7), (1300, 1316, 1.5), (1900, 1908, 0.7), (2500, 2516, 1.5)]
    )

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    assert intervals
    assert intervals == sorted(intervals, key=lambda i: i.start)
    for earlier, later in zip(intervals, intervals[1:], strict=False):
        assert earlier.stop <= later.start
        assert earlier.start < earlier.stop


def test_it_is_registered_with_the_corrected_vocabulary():
    """Design spec section 3.1 as amended 2026-09-01. Declaring `microsaccade`
    alone here would make `registry.Detector.detect` refuse every large saccade
    this detector legitimately finds."""
    from wl_preproc.eye.detect.registry import DETECTORS

    detector = DETECTORS["otero_millan"]
    assert detector.vocabulary == frozenset({Label.SACCADE, Label.MICROSACCADE})


def test_the_registered_detector_accepts_what_this_one_emits():
    """`Detector.detect` is the checked entry point, and the check it performs
    is exactly the one the vocabulary correction exists to satisfy. Calling
    `detect_otero_millan` directly everywhere above would never exercise it."""
    from wl_preproc.eye.detect.registry import DETECTORS

    gaze, v, available = _trace(
        [(700, 708, 0.7), (1300, 1316, 1.5), (1900, 1908, 0.7), (2500, 2516, 1.5)]
    )

    intervals = DETECTORS["otero_millan"].detect(gaze, v, available, DEFAULT_OM_PARAMS)

    assert intervals
    assert {interval.label for interval in intervals} <= DETECTORS["otero_millan"].vocabulary


# **Two claims this file cannot make, and where they live instead.**
#
# `set(register_default_paramsets()) == set(DETECTORS)` -- this subsystem's
# completeness claim, now over two detectors -- and `_params_for` handing this
# detector the shared `microsaccade_max_deg` are both assertions about
# SCHEMA-layer code. No other test under `tests/eye/` imports
# `wl_preproc.schema`, and one that did would make this whole tier require
# DataJoint: measured, it fails outright in the 3.13 cross-check environment,
# which installs numpy and pytest but no database driver.
#
# Both therefore live in `tests/schema/test_detect_populate.py`, which already
# has a database:
#   - `test_the_registered_paramsets_match_the_detector_registry`
#   - `test_the_shared_amplitude_cut_reaches_otero_millan_through_the_paramset`
# The first needed no change to cover this detector -- it reads `DETECTORS`
# rather than naming its members, which is the point of writing it that way.


def test_every_detector_s_paramset_carries_the_shared_amplitude_cut():
    """`register_default_paramsets` writes `microsaccade_max_deg` LAST, after
    each detector's own defaults, so a detector can never shadow it. With two
    detectors both declaring a field of that name, the ordering is now load
    bearing in a way one detector could not show: merged the other way, each
    would quietly get to pick the amplitude cut its own rows are split at.
    """
    from dataclasses import asdict

    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS
    from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG

    assert "microsaccade_max_deg" in asdict(DEFAULT_EK_PARAMS)
    assert "microsaccade_max_deg" in asdict(DEFAULT_OM_PARAMS)
    assert DEFAULT_OM_PARAMS.microsaccade_max_deg == MICROSACCADE_MAX_DEG


def test_moving_the_shared_cut_moves_this_detector_s_labels():
    """The consequence of the previous test, at the only place it can be
    observed: a 4 degree event is `saccade` at the conventional cut and
    `microsaccade` above it, with the detection itself unchanged."""
    gaze, v, available = _trace([(1000, 1020, 4.0), (2000, 2020, 4.0), (3000, 3020, 4.0)])
    lifted = OteroMillanParams(
        min_cluster_displacement_deg=DEFAULT_OM_PARAMS.min_cluster_displacement_deg,
        max_clusters=DEFAULT_OM_PARAMS.max_clusters,
        min_isi_samples=DEFAULT_OM_PARAMS.min_isi_samples,
        microsaccade_max_deg=10.0,
    )

    default = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)
    raised = detect_otero_millan(gaze, v, available, lifted)

    assert [(i.start, i.stop) for i in default] == [(i.start, i.stop) for i in raised]
    assert all(i.label is Label.SACCADE for i in default)
    assert all(i.label is Label.MICROSACCADE for i in raised)


def test_the_default_peak_separation_is_the_references_live_twenty_milliseconds():
    """**A mutation check found this untested, and it is how the wrong value
    shipped.** Reverting `min_isi_samples` to 15 passed every other test in
    this file -- which is exactly the hole the first round fell into: 15 came
    from `SaccadeDetector.MIN_ISI = 30` read as milliseconds, and that
    constant is declared and read by nothing in the reference (`grep -c
    MIN_ISI` returns 1, its own declaration). The live separation is
    `SaccadeDetectorCluster.MINIPI = round(20 * samplerate / 1000)`.

    Asserted against that DERIVATION rather than against the literal 10,
    following `test_engbert_kliegl.py::test_the_default_threshold_is_the_
    shared_constant_not_a_second_copy`: a bare `== 10` would have been just as
    green at 15 before anyone read the source, and pins nothing about why.
    Written this way, changing the default means confronting the claim that it
    is no longer 20 ms.

    The 500 Hz here is the rig's nominal rate, used ONLY to turn the
    reference's millisecond constant into samples for this assertion. The
    detector itself never sees a sampling rate -- design spec section 3's
    signature carries none -- which is precisely why the parameter has to be
    stated in samples and why this derivation is worth writing down.
    """
    reference_min_ipi_s = 0.020
    rig_fs_hz = 500.0

    assert DEFAULT_OM_PARAMS.min_isi_samples == round(reference_min_ipi_s * rig_fs_hz)


def test_acceleration_is_the_shared_estimator_applied_twice():
    """Design spec section 3 fixes ONE differentiator for the whole subsystem,
    and this detector's acceleration features are the second application of
    it. Pinned because substituting a sharper one (`np.gradient`) is a
    one-line change that alters what this detector stores.

    **What it alters is `reliability`, and that is the whole of the case.**
    Measured against a re-derivation of the reference's own differentiator (a
    6-tap Bartlett FIR, then a difference), the shared 5-point estimator
    reproduces it EXACTLY -- identical spans, identical labels, and identical
    `reliability` floats -- on both populated fixtures and on 30 of 30
    synthetic seeds. `np.gradient` finds the same spans and stores different
    numbers: 0.707 against 0.834, and 0.134 against 0.377, on
    `stepped_session`'s three events. `reliability` is a stored, auditable
    column, and design spec section 3.2's plan for validating this detector is
    to check it against the paper's own reported statistics -- which cannot
    mean anything if the number depends on a differentiator the reference does
    not use.

    **What it no longer justifies is the event set, and that is worth saying
    plainly.** For one round this docstring cited a measured failure --
    `np.gradient` returning 29 events on `out_of_order_session` where three
    were planted, by making the silhouette at three clusters worse than at two
    and stopping `_cluster_peaks` a step early. That measurement was taken at
    `min_isi_samples=15`, which was itself wrong (it came from a constant the
    reference declares and never reads). At the corrected value of 10 the
    failure does not reproduce: `np.gradient` returns the correct three. So
    the estimator change was, on that one case, covering for a parameter
    error. It keeps its place on fidelity and on design spec section 3, not on
    that failure.
    """
    from wl_preproc.eye.detect.otero_millan import _acceleration
    from wl_preproc.eye.detect.velocity import velocity

    _gaze, v, _available = _trace([(1000, 1020, 4.0)])

    rate = velocity(v, fs_hz=1.0)

    assert np.array_equal(_acceleration(v), np.hypot(rate[:, 0], rate[:, 1]))


def test_the_acceleration_features_do_not_depend_on_the_missing_sampling_rate():
    """The claim `_acceleration` makes to justify `fs_hz=1.0`: a constant
    factor on acceleration cancels in `zscore(log(a))`, so no rate needs
    inventing. Asserted rather than argued, because the whole reason that
    argument is load-bearing is that the detector contract carries no rate to
    check it against."""
    from wl_preproc.eye.detect.otero_millan import _log_finite, _zscore

    rng = np.random.default_rng(3)
    accel = np.abs(rng.normal(5.0, 2.0, size=200)) + 0.1

    per_sample = _zscore(_log_finite(accel))
    per_second = _zscore(_log_finite(accel * 500.0))

    assert np.allclose(per_sample, per_second)


# --- Below: the steps the module argues hardest for, made observable.
#
# A whole-suite mutation sweep found eight behaviour-changing mutations that no
# test noticed -- whitening deleted outright, the component cut moved to 0.0 or
# 0.5, the silhouette margin to 0% or 20%, the ridge to 1.0, `_zscore`'s ddof to
# 0, and the onset window shortened. Every one changes the event set or the
# stored reliability on real input. The tests below pin what each step is FOR,
# rather than pinning one number per mutation.


def _correlated_features():
    """Three CORRELATED columns, which is what the detector actually whitens.

    **The previous fixture here was three mutually orthogonal sinusoids, and
    that made the decorrelation assertion vacuous**: `cov` of orthogonal
    columns is already diagonal, so the identity transform passed it and only
    the second assertion did any work -- as a numpy shape crash, not as a
    statement about whitening. Log peak velocity and the two log accelerations
    are strongly correlated on real input, which is the module's own argument
    for the ridge, so a fixture without correlation tests a case the detector
    never sees.

    Pairwise correlations here are 0.93 / 0.53 / 0.40, and the covariance
    eigenvalues 0.056 / 0.595 / 2.082 give ratios 0.027 / 0.286 / 1.0 -- placed
    to straddle the 5% component cut on BOTH sides.
    """
    rng = np.random.default_rng(5)
    base = rng.normal(size=60)
    return np.column_stack([
        base,
        0.9 * base + 0.44 * rng.normal(size=60),
        0.6 * base + 0.8 * rng.normal(size=60),
    ])


def test_whitening_decorrelates_and_the_ridge_sets_how_far():
    """What whitening is for, stated as a property of its output.

    `W = (X - mean) @ V @ diag(sqrt(1 / (d + ridge)))` where `V, d` diagonalise
    `cov(X)`. So `cov(W) = diag(d / (d + ridge))` **exactly**: off-diagonals
    vanish (that is the decorrelation) and each diagonal entry is set by the
    ridge (that is how far the rescaling goes).

    On a correlated fixture both assertions do real work. The input's largest
    off-diagonal covariance is 0.86, so an identity transform fails the first
    outright; and moving the ridge to 1.0 turns the diagonal 0.856/0.954 into
    0.373/0.676, which fails the second.
    """
    from wl_preproc.eye.detect.otero_millan import _whiten

    features = _correlated_features()
    covariance_in = np.cov(features, rowvar=False)
    assert np.abs(covariance_in - np.diag(np.diag(covariance_in))).max() > 0.5

    eigenvalues = np.linalg.eigvalsh(covariance_in)
    kept = eigenvalues[(eigenvalues / eigenvalues[-1]) > 0.05]

    covariance = np.cov(_whiten(features), rowvar=False)

    assert np.allclose(covariance - np.diag(np.diag(covariance)), 0.0, atol=1e-12)
    assert np.allclose(np.diag(covariance), kept / (kept + 0.1))


def test_the_component_cut_drops_a_degenerate_direction():
    """The cut keeps components whose eigenvalue exceeds 5% of the largest.

    `_correlated_features`' covariance eigenvalues have ratios 1 : 0.286 :
    0.027, placed to straddle the cut on both sides: at 0.05 exactly two
    survive, at 0.0 all three would, at 0.5 only one would. One assertion
    therefore pins the cut from above and below rather than merely recording
    that some cut exists.

    Dropping the third is the point -- it is a direction the data has almost no
    extent in, and dividing it by its own near-zero eigenvalue is what would
    blow it up to dominate every distance the clustering computes.
    """
    from wl_preproc.eye.detect.otero_millan import _whiten

    assert _whiten(_correlated_features()).shape[1] == 2


def test_whitening_is_what_makes_a_small_event_findable_at_all():
    """The consequence the property test above cannot show: on this trace,
    clustering the RAW z-scored features accepts nothing, and clustering the
    whitened ones finds all three planted microsaccades.

    Seed 1 rather than 0 because at seed 0 both happen to succeed -- which is
    exactly how deleting `_whiten` passed a suite whose fixtures all used seed
    0. **Seed 1 is not a lucky draw**: over seeds 0-199 of this same small
    fixture, whitening changes the accepted set on 104 of 200, and this test's
    exact 3-versus-0 outcome holds on 36 of 200. An earlier version of this
    docstring said "18 of 200", which was measured over a different mix of
    fixtures and was wrong for this one.
    """
    from wl_preproc.eye.detect.otero_millan import (
        _accept, _acceleration, _candidate_spans, _cluster_peaks, _features,
        _log_finite, _whiten, _zscore,
    )

    gaze, v, available = _trace(
        [(1000, 1015, 0.4), (2000, 2015, 0.4), (3000, 3015, 0.4)], seed=1
    )
    speed = np.hypot(v[:, 0], v[:, 1])
    spans = _candidate_spans(speed, np.ones(len(gaze), dtype=bool), 10)
    peak_velocity, onset, brake, displacement = _features(
        gaze, speed, _acceleration(v), spans
    )
    raw = np.column_stack([
        _zscore(_log_finite(peak_velocity)), _zscore(_log_finite(onset)),
        _zscore(_log_finite(brake)),
    ])

    def accepted(matrix):
        cluster, _ = _cluster_peaks(matrix, peak_velocity, 4)
        return int(_accept(cluster, displacement, 0.2).sum())

    assert accepted(_whiten(raw)) == 3
    assert accepted(raw) == 0


def test_the_silhouette_margin_is_what_chooses_the_cluster_count():
    """The 1% margin is a real decision, not a rounding guard.

    On this trace the mean binarised silhouette improves from two clusters to
    three by more than 1%, and from three to four by less. So the margin picks
    THREE: a 0% margin would take any improvement and run to four, and a 20%
    margin would refuse the first improvement and stop at two -- 4 / 3 / 2, all
    three distinct, which is why one fixture pins both mutations.

    Traces that separate all three values are rare, and an earlier version of
    this docstring badly overstated how rare they are not: it said 35 of 200,
    measured 7 (large/small/matched/noise fixtures, seeds 0-49 each). What IS
    common is a count that moves at all -- 167 of those same 200. The stopping
    rule decides the cluster count far more often than it converges to one; see
    the design spec section 3.1 note this round added.

    Asserted through `_cluster_peaks`' returned assignment, whose maximum IS
    the count it settled on, rather than by reaching into the loop.
    """
    from wl_preproc.eye.detect.otero_millan import (
        _acceleration, _candidate_spans, _cluster_peaks, _features, _log_finite,
        _whiten, _zscore,
    )

    gaze, v, _available = _trace(
        [(1000, 1020, 4.0), (2000, 2020, 4.0), (3000, 3020, 4.0)], seed=26
    )
    speed = np.hypot(v[:, 0], v[:, 1])
    spans = _candidate_spans(speed, np.ones(len(gaze), dtype=bool), 10)
    peak_velocity, onset, brake, _displacement = _features(
        gaze, speed, _acceleration(v), spans
    )
    whitened = _whiten(np.column_stack([
        _zscore(_log_finite(peak_velocity)), _zscore(_log_finite(onset)),
        _zscore(_log_finite(brake)),
    ]))

    cluster, _silhouette = _cluster_peaks(whitened, peak_velocity, 4)

    assert int(cluster.max()) == 3


def test_zscore_uses_the_sample_standard_deviation_like_the_reference():
    """MATLAB's `zscore` normalises by `N - 1`, and this must match.

    It is not a cosmetic difference. Every feature is scaled by
    `sqrt(N / (N - 1))` under the wrong divisor, which scales the covariance
    eigenvalues -- but NOT the `+ 0.1` ridge they are added to. The whitening
    therefore changes by a different factor in each component, and the
    clustering with it.
    """
    from wl_preproc.eye.detect.otero_millan import _zscore

    values = np.array([1.0, 2.0, 4.0, 8.0, 16.0])

    assert _zscore(values).std(ddof=1) == pytest.approx(1.0)
    assert _zscore(values).std(ddof=0) == pytest.approx(np.sqrt(4 / 5))


def test_the_onset_window_reaches_one_sample_past_the_velocity_peak():
    """The reference's own arithmetic, and the two windows really do overlap.

    `GetPeakAccelerationStart` takes `sac(i,1):(sac(i,1)+idxmax)` and
    `GetPeakAccelerationBrake` takes `min(sac(i,1)+idxmax, sac(i,2)):sac(i,2)`,
    with `idxmax` the 1-based offset of the velocity peak -- so both include
    absolute sample `start + at + 1`. Here the largest acceleration sits
    exactly on that shared sample, so BOTH features must report it. A window
    stopping at the velocity peak (which this module used for one round, while
    describing the disjointness as a design property) reports 3.0 for the
    onset instead of 99.0.
    """
    from wl_preproc.eye.detect.otero_millan import _features

    speed = np.array([0.0, 10.0, 50.0, 20.0, 5.0])       # velocity peak at index 2
    accel = np.array([1.0, 2.0, 3.0, 99.0, 4.0])         # max at index 3 == peak + 1
    gaze = np.column_stack([np.arange(5) * 0.5, np.zeros(5)])

    _pv, onset, brake, _disp = _features(gaze, speed, accel, [(0, 5)])

    assert onset[0] == 99.0
    assert brake[0] == 99.0


def _drifting_half_trace(n=90_000, seed=0, quiet=0.004, loud=0.12, every=500, size=4.0):
    """Events in the FIRST half only, with the noise scale ramping up across
    the whole recording -- so the second half is loud and genuinely eventless.

    Long enough to span more than one clustering chunk, which is the entire
    point: every other fixture in this file is a single chunk, and a single
    chunk cannot tell global normalisation from per-chunk normalisation.
    """
    from wl_preproc.eye.detect.velocity import velocity

    rng = np.random.default_rng(seed)
    sigma = np.linspace(quiet, loud, n)[:, None]
    gaze = rng.normal(0.0, 1.0, size=(n, 2)) * sigma
    onsets = list(range(1000, n // 2, every))
    for start in onsets:
        stop = start + 15
        gaze[start:, 0] += size
        gaze[start:stop, 0] += np.linspace(0.0, size, stop - start) - size
    return gaze, velocity(gaze, fs_hz=500.0), np.full(n, None, dtype=object), onsets


def test_normalisation_is_global_so_a_late_noisy_chunk_is_not_flattered():
    """**Z-scoring and whitening happen once over the whole recording; only the
    clustering is chunked**, which is what the reference does -- `GetFeatures`
    is called once over every peak and only `ClusterPeaks` is chunked.

    Normalising per chunk instead defeats the reason chunks exist. Chunks are
    there because a recording's properties DRIFT; rescaling each chunk against
    its own mean and covariance erases exactly the drift they were meant to
    accommodate, and every chunk then looks equally eventful. Measured on this
    fixture: 88 events are planted, all in the first half, and the second half
    is pure noise that has grown 30x louder. Global normalisation accepts **88
    detections, none of them after the events stop**. Per-chunk normalisation
    accepts **98, ten of them hallucinated out of the eventless second half**
    -- a chunk of loud noise, renormalised against itself, looks like a chunk
    full of saccades.

    The +/-30 sample match tolerance is wider than the quiet fixtures' +/-10
    because the noise here reaches 0.12 degrees, so the 5 deg/s boundary walk
    wanders further before it finds a quiet sample. At that tolerance the
    correspondence is exactly one-to-one in both directions.
    """
    gaze, v, available, onsets = _drifting_half_trace()

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)
    half = len(gaze) // 2

    assert len(intervals) == len(onsets)
    assert not [i for i in intervals if i.start > half]
    assert all(any(abs(i.start - onset) <= 30 for onset in onsets) for i in intervals)
    assert all(any(abs(i.start - onset) <= 30 for i in intervals) for onset in onsets)


def test_the_fixture_really_does_span_more_than_one_chunk():
    """Guards the test above from passing for the wrong reason. If the drifting
    fixture ever produced a single chunk, global and per-chunk normalisation
    would be the same computation and the assertion would hold no matter which
    one the module used."""
    from wl_preproc.eye.detect.otero_millan import _candidate_spans, _chunks

    gaze, v, _available, _onsets = _drifting_half_trace()
    speed = np.hypot(v[:, 0], v[:, 1])

    spans = _candidate_spans(speed, np.ones(len(gaze), dtype=bool), 10)

    assert len(_chunks(len(spans))) >= 2


def test_merge_coalesces_touching_spans_not_only_overlapping_ones():
    """`_merge`'s `start <= out[-1][1]` is a `<=`, and the difference matters.

    Two spans that merely TOUCH -- one ending where the next begins -- must
    become one. `runs_from_labels` downstream produces maximal runs, so two
    touching intervals carrying the same label would be merged into one run
    there anyway, matching neither span; `_insert_trace`'s exact-span
    `reliability` lookup would then miss and store `None` for a real detection.

    **Nothing pinned this.** Weakening the comparison to `<` survived the whole
    suite, including `test_neither_registered_detector_can_produce_a_merged_run`
    -- that test inspects only ACCEPTED runs, and the touching spans this rule
    coalesces land in the noise cluster and are never returned. `_merge` was
    called by no test at all. Asserted here, at the invariant's source.
    """
    from wl_preproc.eye.detect.otero_millan import _merge

    assert _merge([(0, 10), (10, 20)]) == [(0, 20)]      # touching
    assert _merge([(0, 10), (5, 20)]) == [(0, 20)]       # overlapping
    assert _merge([(0, 10), (11, 20)]) == [(0, 10), (11, 20)]   # a real gap
    assert _merge([(10, 20), (0, 10)]) == [(0, 20)]      # and it sorts first


def test_a_velocity_peak_on_an_events_last_sample_is_handled_at_both_ends():
    """The precondition both clamps in `_features` exist for, and neither was
    reachable from any other test in this file.

    When the velocity peak lands on an event's LAST sample, the reference's
    unclamped windows do two different wrong things. The brake window becomes
    `accel[stop:stop]` -- empty -- and `.max()` on it raises `ValueError:
    zero-size array to reduction operation maximum which has no identity`. The
    onset window runs to `stop + 1`, which numpy silently truncates to one
    sample PAST the event: a sample belonging to no event.

    Here `accel[5] = 999.0` sits just outside the span, so a missing onset
    clamp is visible as a value rather than only as a crash.

    **This is reachable from real input, not a defensive hypothetical.** A
    validity mask ending a usable segment just after a velocity peak produces
    it: with a blink planted at sample 1005 of this file's own large fixture,
    `_candidate_spans` returns the span [997, 1005) whose peak is its last
    sample. The end-to-end test below runs exactly that.
    """
    from wl_preproc.eye.detect.otero_millan import _features

    speed = np.array([0.0, 10.0, 20.0, 30.0, 40.0, 1.0])   # span max at index 4
    accel = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 999.0])     # 999 is OUTSIDE [0, 5)
    gaze = np.column_stack([np.arange(6) * 0.5, np.zeros(6)])

    peak_velocity, onset, brake, _displacement = _features(gaze, speed, accel, [(0, 5)])

    assert peak_velocity[0] == 40.0
    assert onset[0] == 5.0    # unclamped this reads accel[5] and returns 999.0
    assert brake[0] == 5.0    # unclamped this is accel[5:5] and raises


def test_a_blink_ending_a_segment_on_a_velocity_peak_does_not_crash():
    """The same geometry, reached the way a recording reaches it.

    `_limits` clamps an event to its usable segment, so a mask that ends the
    segment at `peak + 1` yields a span whose velocity peak is its last sample.
    Without the `brake_start` clamp this raises rather than returning; the
    suite otherwise never builds a trace that gets there.
    """
    gaze, v, available = _trace([(1000, 1020, 4.0), (2000, 2020, 4.0), (3000, 3020, 4.0)])
    available[1005:1085] = Label.BLINK

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    assert intervals
    assert all(interval.stop <= 1005 or interval.start >= 1085 for interval in intervals)


def test_neither_registered_detector_can_produce_a_merged_run():
    """**The honest version of a test that used to assert `dict.get` misses.**

    `_insert_trace` maps `reliability` onto its re-derived runs by exact
    `(start, stop)` match, so a run that merged two detector intervals gets
    `None` rather than a borrowed number -- design spec section 5's column says
    how much to trust a detection, and a value belonging to neither half would
    be a fabrication in the one column a reader consults to decide what to
    believe.

    That path **cannot be reached by either registered detector today**, which
    is what this asserts instead of pretending otherwise. A merge needs two
    adjacent intervals carrying the same label with no sample between them, and
    both detectors guarantee a gap: `otero_millan._merge` coalesces touching
    spans before returning, and `engbert_kliegl._true_runs` returns maximal
    True runs, which are separated by at least one False sample by
    construction. The previous version of this test built a literal dict and
    asserted a miss on it -- it exercised `dict.get`, not `_insert_trace`, while
    its name and docstring claimed production coverage.

    So the guarantee is asserted at its source, on real detector output. If a
    future detector drops the gap, this fails and the `None` branch becomes
    reachable -- which is the moment someone should write the test the old one
    pretended to be.
    """
    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS, detect_engbert_kliegl

    gaze, v, available = _trace(
        [(700, 708, 0.7), (1300, 1316, 1.5), (1900, 1908, 0.7), (2500, 2516, 1.5)]
    )

    for intervals in (
        detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS),
        detect_engbert_kliegl(gaze, v, available, DEFAULT_EK_PARAMS),
    ):
        assert intervals
        for earlier, later in zip(intervals, intervals[1:], strict=False):
            assert earlier.stop < later.start, (
                "adjacent intervals touch, so runs_from_labels could merge them "
                "and _insert_trace's exact-span reliability map would miss"
            )


def test_run_defaults_reliability_to_none_and_stays_equal_to_itself():
    """`None` rather than `float('nan')`, and the reason is recorded in this
    repository's own history: a dataclass field defaulting to `nan` compares
    unequal to itself on 3.13, where `__eq__` stopped going through
    `tuple.__eq__`'s identity shortcut (`27917b4`, again in `ea9b94b`). Every
    comparison this subsystem makes between runs would silently stop matching.
    """
    from wl_preproc.eye.detect.labels import Run

    plain = Run(10, 20, Label.SACCADE)

    assert plain.reliability is None
    assert plain == Run(10, 20, Label.SACCADE)
    assert plain == plain


def test_runs_from_labels_gives_reconstructed_runs_no_reliability():
    """A run this subsystem re-derives from a label array was not detected by
    anything, so there is no per-detection index to attach to it -- which is
    also why `_insert_trace` has to map reliability back on rather than
    carrying it through."""
    import numpy as np

    from wl_preproc.eye.detect.labels import runs_from_labels

    labels = np.array([Label.FIXATION] * 3 + [Label.SACCADE] * 2, dtype=object)

    assert all(run.reliability is None for run in runs_from_labels(labels))
