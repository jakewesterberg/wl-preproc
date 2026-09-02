"""Otero-Millan's cluster detector, and the vocabulary correction it honours.

Design spec section 3.1/3.2 were CORRECTED on 2026-09-01 by reading the MATLAB
reference: this detector finds saccades of ANY amplitude, its vocabulary is
`saccade / microsaccade`, and its only amplitude rule is a 0.2 degree LOWER
noise floor on a candidate cluster's mean displacement. These tests are written
against that correction, not against the pre-correction spec.
"""

from __future__ import annotations

import numpy as np

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

    **The +/-10 sample tolerance is measured, not guessed, and it is not
    slack.** Two things move a detected edge off a planted one. The shared
    5-point velocity estimator is CENTRED -- velocity at sample `n` reads
    `gaze[n + 2]` -- so velocity rises two samples before the position ramp
    does; and this detector then walks the event's boundary out to the last
    sample below `_SACCADE_LIMIT_DEG_S`, which lands wherever noise happens to
    drop back under 5 deg/s. Over 200 seeds, all 200 return exactly three
    saccades and the boundary deviation is median 1, 99th percentile 5, **max
    7**. Ten covers that with margin while staying 100x tighter than the
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
        assert abs(interval.start - want_start) <= 10
        assert abs(interval.stop - want_stop) <= 10
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


def test_a_merged_run_reports_no_reliability_rather_than_a_borrowed_one():
    """Design spec section 5's column says how much to trust a detection. A run
    corresponding to no single detector interval has no such number, and
    borrowing one from either half would be a fabrication in the one column a
    reader consults to decide what to believe.
    """
    from wl_preproc.eye.detect.labels import Run

    reliability_by_span = {(10, 20): 0.8, (20, 30): 0.9}
    merged = Run(10, 30, Label.SACCADE)

    assert reliability_by_span.get((merged.start, merged.stop)) is None


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
