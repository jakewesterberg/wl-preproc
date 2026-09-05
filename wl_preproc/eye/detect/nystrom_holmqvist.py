"""Nyström & Holmqvist's adaptive-threshold detector -- the first of this
subsystem's seven planned detectors that can emit `pso` (post-saccadic
oscillation, this vocabulary's word for a glissade).

Reimplemented from the published algorithm rather than vendored, for the same
reason as this repository's other detectors (design spec
`2026-08-31-saccade-detection-design.md` section 3.2): in a subsystem whose
whole purpose is comparing detectors against each other, "a buggy
reimplementation is indistinguishable from a genuine detector disagreement,"
and a reimplementation defect therefore "does not look like a defect -- it
looks like a finding, and it looks like exactly the finding this subsystem
exists to surface."

Nyström, M., & Holmqvist, K. (2010). An adaptive algorithm for fixation,
saccade, and glissade detection in eyetracking data. *Behavior Research
Methods*, 42(1), 188-204. 10.3758/BRM.42.1.188. Every constant below is the
paper's own, from its Table 2, with the one exception its own field comment
names; nothing here is tuned (design spec
`2026-09-05-nystrom-holmqvist-design.md`, section 7).

**This module carries the whole detector.** It has the twelve published
parameters, the paper's first step (the adaptive peak-velocity threshold,
p. 193, Figure 4, that converges to the data's own noise floor rather than
asking an experimenter to choose one, which is what makes the algorithm
"settings-free for the user"), its second (onset/offset search around a
detected peak, p. 194, Figure 5), its third (glissade detection, p. 195),
and its fourth (fixation detection, p. 196) -- assembled by
`detect_nystrom_holmqvist` and registered as
`registry.DETECTORS["nystrom_holmqvist"]`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from wl_preproc.eye.detect.labels import Label, Run
from wl_preproc.eye.detect.measure import amplitude


@dataclass(frozen=True, slots=True)
class NystromHolmqvistParams:
    """The paper's own constants (Table 2 unless a field says otherwise),
    one field per row of design spec `2026-09-05-nystrom-holmqvist-design.md`
    section 7. Twelve fields; eleven are the paper's own, and the twelfth
    (`max_iterations`) says plainly that it is not.

    **Five of these twelve are read by this module's `_peak_threshold`**:
    `initial_peak_threshold_deg_s`, `peak_threshold_sigma`,
    `onset_threshold_sigma`, `convergence_deg_s`, `max_iterations`. The other
    seven -- `local_noise_sigma`, `offset_alpha`, `offset_beta`,
    `min_saccade_duration_ms`, `min_fixation_duration_ms`,
    `max_velocity_deg_s`, `max_acceleration_deg_s2` -- are declared now,
    together, because spec section 7 puts all twelve in one dataclass; they
    are consumed by later tasks in the same plan (onset/offset search,
    rejection, and glissade/fixation detection), not by anything in this
    file yet.

    `min_duration_samples` is deliberately absent, unlike
    `EngbertKlieglParams`' analogous field: this detector's minimum durations
    are the paper's own, expressed in milliseconds precisely because a
    sample count would be wrong at any `fs_hz` other than the one it was
    computed for (spec section 7).
    """

    #: `PT1`, the seed `_peak_threshold` iterates from. The paper states a
    #: range rather than a value -- "100-300 deg/sec, but the choice is not
    #: critical as long as there are saccades with peak velocities reaching
    #: this threshold" (p. 193) -- so 200.0 is this implementation's own
    #: midpoint of that range, not a number the paper names directly.
    #: `test_the_starting_value_does_not_matter` is what makes "not
    #: critical" a checked claim here rather than a merely quoted one.
    initial_peak_threshold_deg_s: float

    #: Table 2: the converged peak-velocity threshold is
    #: `theta_PT = mu_z + 6*sigma_z` over the noise below the current
    #: threshold. The paper calls 6 "a good robust level," noting it "is
    #: also used in microsaccade detection algorithms (Engbert & Kliegl,
    #: 2003)" -- the same 6 this repository's own `engbert_kliegl.py::
    #: DEFAULT_LAMBDA` carries (p. 193).
    peak_threshold_sigma: float

    #: Table 2: the saccade onset threshold's sigma multiplier,
    #: `theta_ST^onset = mu_z + 3*sigma_z` (p. 194). Onset is searched
    #: backward from a velocity peak to the first sample below this that is
    #: also a local minimum (design spec section 1.2); that search is a
    #: later task, but `_peak_threshold` already reports the threshold
    #: itself as `PeakThreshold.onset_deg_s`, since it comes from the same
    #: converged `mu, sigma`.
    onset_threshold_sigma: float

    #: Table 2: the LOCAL noise estimate's sigma multiplier,
    #: `theta_t = mu_t + 3*sigma_t`, computed over a `min_fixation_duration_
    #: ms`-wide window (the paper's tau_min) immediately PRECEDING the
    #: saccade -- "to avoid contamination from glissadic movements" (p. 194).
    #: A distinct field from `onset_threshold_sigma` even though both are 3,
    #: because the two are computed over different populations (trial-wide
    #: noise vs. this local window); a future change to one must not
    #: silently move the other.
    local_noise_sigma: float

    #: Table 2, alpha: the saccade OFFSET threshold blends the onset
    #: threshold with the local estimate,
    #: `theta_ST^offset = alpha*theta_ST^onset + beta*theta_t` (p. 194).
    offset_alpha: float

    #: Table 2, beta. The paper's own alpha and beta happen to sum to 1.0
    #: (0.7 + 0.3); both are declared as their own fields, rather than one
    #: plus `1 - alpha`, so the paramset states its own values instead of
    #: leaving one implicit.
    offset_beta: float

    #: p. 194: `_peak_threshold` iterates until `|PTn - PT(n-1)|`, in
    #: deg/sec, falls below this (design spec section 1.1, point 4).
    convergence_deg_s: float

    #: **Not from the paper.** It reports convergence "in about two
    #: iterations" and gives the criterion above, but no cap -- a
    #: distribution that never satisfies it would loop forever (design spec
    #: section 9, item 2). This is this implementation's own guard, and
    #: `PeakThreshold.converged=False` is how a caller tells a genuine
    #: convergence apart from one that only hit this limit.
    #:
    #: **Reachable, by construction, with these unmodified defaults**
    #: (`test_a_monotone_crawl_exhausts_max_iterations`) -- see
    #: `_peak_threshold`'s own docstring for the mechanism (a monotone crawl
    #: through many tiers, not an oscillation) and why it needs velocities
    #: no real recording produces.
    max_iterations: int

    #: Table 2: saccades shorter than this are discarded -- "large enough to
    #: avoid noise being falsely categorized as saccades but small enough to
    #: include short saccades (~1 deg)" (p. 194). Milliseconds, not samples:
    #: expressed the way the paper states it, and because a sample count is
    #: wrong at a different `fs_hz`.
    min_saccade_duration_ms: float

    #: Table 2, tau_min. The paper set 40 msec having "manually identified
    #: several oculomotor fixations in the data, especially during reading,
    #: with durations below 50 msec" (pp. 195-197). The same tau_min also
    #: sizes the local-noise window that precedes a saccade (p. 194) and the
    #: glissade search window that follows one (p. 195) -- one field for the
    #: paper's one symbol, reused in three places by later tasks.
    min_fixation_duration_ms: float

    #: Table 2: velocity above this is "not physiologically possible" and is
    #: dropped before detection (p. 194).
    max_velocity_deg_s: float

    #: Table 2: acceleration above this is "not physiologically possible"
    #: and is dropped before detection (p. 194).
    max_acceleration_deg_s2: float


DEFAULT_NH_PARAMS = NystromHolmqvistParams(
    initial_peak_threshold_deg_s=200.0,
    peak_threshold_sigma=6.0,
    onset_threshold_sigma=3.0,
    local_noise_sigma=3.0,
    offset_alpha=0.7,
    offset_beta=0.3,
    convergence_deg_s=1.0,
    max_iterations=100,  # this implementation's own guard -- not the paper's
    min_saccade_duration_ms=10.0,
    min_fixation_duration_ms=40.0,
    max_velocity_deg_s=1000.0,
    max_acceleration_deg_s2=100000.0,
)


@dataclass(frozen=True, slots=True)
class PeakThreshold:
    """The converged thresholds, plus whether the iteration actually got
    there. `converged=False` is not a failure to hide -- it is either of two
    honest outcomes, both distinct from a genuine convergence: the
    sub-threshold population went empty before settling (nothing left to
    estimate `mu, sigma` from), or the paper gives no iteration cap and this
    implementation's own guard (`max_iterations`) was reached first. Either
    way, a caller can tell a converged threshold from one that is not."""

    peak_deg_s: float
    onset_deg_s: float
    iterations: int
    converged: bool


def _peak_threshold(
    speed_deg_s: np.ndarray, usable: np.ndarray, params: NystromHolmqvistParams
) -> PeakThreshold:
    """The adaptive velocity threshold, iterated to convergence.

    The paper's central novelty (p. 193, Figure 4), and what makes the
    algorithm "settings-free for the user": rather than an experimenter
    choosing a velocity threshold, it is derived from the data's own noise.

    `PT1` is `params.initial_peak_threshold_deg_s`. For all samples with
    velocity below the current threshold, the mean and standard deviation are
    computed and the threshold updated as `PTn = mu + 6*sigma`, iterating
    until `|PTn - PT(n-1)| < 1 deg/s`.

    **The 6 is not arbitrary.** The paper calls it "a good robust level" and
    notes it "is also used in microsaccade detection algorithms (Engbert &
    Kliegl, 2003)" -- the same 6 `engbert_kliegl.py::DEFAULT_LAMBDA` already
    carries.

    **`params.max_iterations` IS reachable with the unmodified defaults, by
    construction** (`test_a_monotone_crawl_exhausts_max_iterations`): seed
    `[1.0, 3.0]`, then repeatedly append `nextafter(mean + 6*std, -inf)` of
    the array built so far. Fed back through this function from
    `initial_peak_threshold_deg_s=200.0`, the run retraces the same sequence
    of thresholds used to build it, pulling in exactly one more element per
    iteration -- `below.size` runs 6, 7, 8, ..., 105 across all 100
    iterations, ending `converged=False` at `peak_deg_s` ~2.28e15 deg/sec.
    This is a MONOTONE CRAWL through many tiers, not an oscillation, and it
    needs no cycling to exhaust the cap.

    A 2-value OSCILLATION specifically -- the threshold alternating between
    two regimes forever -- is a narrower claim, and does not need the cap at
    all: below-sets are nested by threshold, so reaching back down to a
    smaller regime after advancing to a larger one requires the larger
    regime's own `mu + 6*sigma` to fall strictly below a value the smaller
    regime's `mu + 6*sigma` already exceeded. For the natural family for
    engineering exactly that -- a population with real spread, plus
    additional mass at the shared boundary -- this cannot happen, checked
    in closed form and by an exhaustive sweep across the added mass's
    relative size (task-2-report.md). That covers one family, not a proof
    that no finite array anywhere can cycle.

    **Practically: this guard protects against pathological input, not
    against a real recording.** The crawl above needs velocities up to
    roughly 2e15 deg/sec, far past anything an eye produces and far past
    `max_velocity_deg_s` (1000 deg/sec, Table 2) -- a later task's rejection
    step would remove every one of these samples before this function ever
    saw them. `converged=False` from a real trace is expected to mean the
    empty-population branch, not this one.

    **Unusable samples are excluded**, for `detect_engbert_kliegl`'s own
    stated reason: a blink's velocity spike would inflate the scale and
    desensitise the detector for the whole recording.

    `speed_deg_s` is a SCALAR speed, `sqrt(vx**2 + vy**2)` -- unlike the
    two-column `velocity_deg_s` most of this subsystem's detectors take. The
    paper's threshold is one-dimensional, so nothing here computes that norm;
    a caller does, and hands the result in.
    """
    sub = speed_deg_s[usable]
    if sub.size == 0:
        return PeakThreshold(0.0, 0.0, 0, False)

    threshold = float(params.initial_peak_threshold_deg_s)
    onset = threshold
    for iteration in range(1, params.max_iterations + 1):
        below = sub[sub < threshold]
        if below.size == 0:
            return PeakThreshold(threshold, onset, iteration, False)
        mu, sigma = float(below.mean()), float(below.std())
        updated = mu + params.peak_threshold_sigma * sigma
        onset = mu + params.onset_threshold_sigma * sigma
        if abs(updated - threshold) < params.convergence_deg_s:
            return PeakThreshold(updated, onset, iteration, True)
        threshold = updated
    return PeakThreshold(threshold, onset, params.max_iterations, False)


def _saccade_bounds(
    speed_deg_s: np.ndarray,
    peak_start: int,
    peak_stop: int,
    thresholds: PeakThreshold,
    fs_hz: float,
    params: NystromHolmqvistParams,
) -> tuple[int, int, float] | None:
    """`(onset, offset, offset_threshold_deg_s)` for one velocity peak found
    between `peak_start` and `peak_stop`, or `None` if the saccade is
    rejected.

    **Onset** (paper p. 194, Figure 5A): search backward from the peak's
    first sample to the first one below `theta_ST^onset = mu_z + 3*sigma_z`
    (`thresholds.onset_deg_s`, already computed by `_peak_threshold`) where
    `(theta_i - theta_(i+1)) >= 0` -- "until the first local minimum is
    found."

    **Offset is the adaptive half, and the reason this algorithm exists.** It
    weights the trial-wide onset threshold against a LOCAL noise estimate:

        theta_t          = mu_t + 3*sigma_t     over tau_min samples PRECEDING onset
        theta_ST^offset  = alpha*theta_ST^onset + beta*theta_t

    with `alpha = 0.7`, `beta = 0.3` (Table 2). Offset is the first sample,
    searching FORWARD from `peak_stop`, below that threshold where
    `(theta_i - theta_(i+1)) <= 0`.

    **The window PRECEDES the saccade, and inverting that is the single
    easiest way to break this detector.** The paper's reason (p. 194): "To
    avoid contamination from glissadic movements." A window placed after the
    saccade would measure the glissade instead of the quiet baseline, raise
    `theta_ST^offset`, and let it accept a still-elevated sample as the
    offset -- cutting the saccade's own decay short
    (`test_the_local_noise_window_precedes_the_saccade` verifies this
    directly, both the correct placement and the broken one).

    **Two rejections, both from p. 194-195:**

    - `mu_t > theta_PT` -- the local window itself is elevated, "indicating
      that there was no period of stillness prior to the saccade onset (most
      often, indicating recording imperfections)." Checked before the offset
      search runs, since a saccade failing this is rejected regardless of
      where its offset would land.
    - A saccade shorter than `min_saccade_duration_ms` (10 msec, Table 2) is
      discarded as noise.

    **The returned threshold is not a diagnostic extra.** Task 4's glissade
    search reuses this same `theta_ST^offset` for its "low-velocity
    glissade" definition (design spec §1.4); returning it here keeps the
    alpha/beta weighting computed in one place rather than a second one it
    could drift from.

    `speed_deg_s` is the same scalar speed `_peak_threshold` takes, not the
    two-column `velocity_deg_s`.
    """
    tau = max(int(round(params.min_fixation_duration_ms * fs_hz / 1000.0)), 1)

    onset = peak_start
    while onset > 0:
        if speed_deg_s[onset] <= thresholds.onset_deg_s and (
            speed_deg_s[onset] - speed_deg_s[onset + 1] >= 0
        ):
            break
        onset -= 1

    window = speed_deg_s[max(onset - tau, 0):onset]
    if window.size == 0:
        return None
    local = float(window.mean()) + params.local_noise_sigma * float(window.std())
    if float(window.mean()) > thresholds.peak_deg_s:
        return None  # mu_t > theta_PT: no stillness before the saccade (p. 195)

    offset_threshold = (
        params.offset_alpha * thresholds.onset_deg_s + params.offset_beta * local
    )
    offset = peak_stop
    limit = speed_deg_s.size - 1
    while offset < limit:
        if speed_deg_s[offset] <= offset_threshold and (
            speed_deg_s[offset] - speed_deg_s[offset + 1] <= 0
        ):
            break
        offset += 1

    min_samples = max(int(round(params.min_saccade_duration_ms * fs_hz / 1000.0)), 1)
    if offset - onset < min_samples:
        return None
    return onset, offset, offset_threshold


def _glissade_bounds(
    speed_deg_s: np.ndarray,
    gaze_deg: np.ndarray,
    saccade_onset: int,
    saccade_offset: int,
    offset_threshold_deg_s: float,
    thresholds: PeakThreshold,
    fs_hz: float,
    params: NystromHolmqvistParams,
) -> tuple[int, int] | None:
    """`[start, stop)` of the glissade following one saccade, or `None`.

    **Both of the paper's criteria, because Table 3's 47.8% is their union**
    (design spec §3, an inference from Figure 10 and marked as one). The two
    are defined as mutually exclusive in how the paper COUNTS them -- "low-
    velocity glissades are not a subset of high-velocity glissades" -- but
    that is a labelling convention over which bucket a qualifying window
    falls into, not a claim that the underlying velocity conditions are
    disjoint in time. Since this vocabulary has no separate label for either
    kind (both emit `pso`), only the union matters here:

    - HIGH-velocity: the curve rises above `theta_PT` within `tau_min` of
      the saccade offset. "A high-velocity glissade has a velocity peak that
      would qualify it for saccadic status" (p. 195).
    - LOW-velocity: identical, but only above `theta_ST^offset`
      (`offset_threshold_deg_s`, the third element `_saccade_bounds`
      returned for this same saccade).

    **A sample qualifies by clearing EITHER threshold, so the window is
    compared against whichever of the two is smaller.** `theta_ST^offset`
    is usually the smaller of the two -- it blends `_saccade_bounds`'
    onset threshold with a LOCAL noise estimate over a window preceding the
    saccade, and that local estimate's mean is checked against `theta_PT`
    (the "no stillness" reject) but its spread is not: a noisy-but-not-
    elevated baseline can push `theta_ST^offset` above `theta_PT` itself.
    Comparing only against `offset_threshold_deg_s` would then miss a real
    high-velocity excursion, so `thresholds.peak_deg_s` is checked too, not
    assumed to already be covered by the low-velocity threshold.
    (`test_the_high_velocity_criterion_survives_an_inflated_low_velocity_
    threshold` is what makes that assumption a checked one rather than a
    silent one.)

    **Onset is the saccade's offset.** Offset is where
    `(theta_i - theta_(i+1)) <= 0` after the last velocity peak in the
    glissade -- a forward walk shaped like `_saccade_bounds`' own offset
    search, but not identical to it: that search re-checks each sample
    against a threshold because it starts at the peak itself, while this one
    starts at the LAST sample already known to be above `qualifying_
    threshold`, so everything from there on is already past the exceeding
    stretch and only the local-minimum condition remains to be found. A
    glissade whose amplitude exceeds its preceding saccade's is omitted
    (p. 196): "Glissades with an amplitude larger than their preceeding
    saccades were omitted."

    `speed_deg_s` is the same scalar speed `_peak_threshold` and
    `_saccade_bounds` take. `gaze_deg` is the two-column position trace
    `amplitude` measures endpoint-to-endpoint displacement from.
    """
    tau = max(int(round(params.min_fixation_duration_ms * fs_hz / 1000.0)), 1)
    end = min(saccade_offset + tau, speed_deg_s.size)
    window = speed_deg_s[saccade_offset:end]
    if window.size == 0:
        return None

    qualifying_threshold = min(offset_threshold_deg_s, thresholds.peak_deg_s)
    above = np.flatnonzero(window > qualifying_threshold)
    if above.size == 0:
        return None

    last_peak = saccade_offset + int(above[-1])
    stop = last_peak
    limit = speed_deg_s.size - 1
    while stop < limit and speed_deg_s[stop] - speed_deg_s[stop + 1] > 0:
        stop += 1
    stop = min(stop + 1, speed_deg_s.size)

    saccade_amp = amplitude(gaze_deg, saccade_onset, saccade_offset)
    if amplitude(gaze_deg, saccade_offset, stop) > saccade_amp:
        return None
    return saccade_offset, stop


def detect_nystrom_holmqvist(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    available: np.ndarray,
    fs_hz: float,
    params: NystromHolmqvistParams,
) -> list[Run]:
    """Labelled half-open `[start, stop)` intervals, in sample indices.

    The first registered detector to emit anything beyond the amplitude
    split. Its saccadic slice is `{saccade}` alone, so its conjunction runs
    take `_conjunction_label`'s DEGENERATE branch and `classify` is never
    asked -- which is why this params dataclass declares no
    `microsaccade_max_deg` and never receives one.

    **Assembles the three pure steps above, in the paper's own order**: the
    adaptive peak threshold, then -- for each velocity peak -- saccade
    bounds and the glissade that may follow it, then fixation over whatever
    is left. `usable` is `available`'s own definition (`entry is None`),
    additionally narrowed by Table 2's two rejections (max velocity, max
    acceleration) before either threshold estimation or peak-finding ever
    sees a sample -- the same reasoning `_peak_threshold`'s own docstring
    gives for excluding unusable samples from the noise estimate, applied
    here to the physiologically-impossible samples Task 2-4's pure functions
    do not themselves see.
    """
    speed = np.hypot(velocity_deg_s[:, 0], velocity_deg_s[:, 1])
    # A LOCAL first difference of the shared estimator's own output, used
    # only for Table 2's acceleration rejection. Not a second shared
    # estimator: design spec section 3.2's "one shared velocity estimator
    # across all seven" is about the velocity every detector reads, and this
    # derives from that one rather than replacing it.
    acceleration = np.abs(np.gradient(speed)) * fs_hz

    usable = np.array([entry is None for entry in available], dtype=bool)
    usable &= speed <= params.max_velocity_deg_s
    usable &= acceleration <= params.max_acceleration_deg_s2
    if not usable.any():
        return []

    thresholds = _peak_threshold(speed, usable, params)
    if thresholds.peak_deg_s <= 0:
        return []

    # **Non-overlap is enforced here, not inherited.** `_saccade_bounds` and
    # `_glissade_bounds` each reason about ONE candidate event in isolation
    # and have no notion of what an earlier candidate already claimed --
    # nothing stops two candidates built from two different peak-runs (a
    # single saccade's velocity dipping and re-crossing `theta_PT`, or two
    # genuinely close saccades whose independent backward/forward searches
    # reach into each other) from returning overlapping `[onset, offset)`
    # spans. `_insert_trace` (schema/detect.py) paints each returned
    # interval's label onto one shared per-sample array in list order and
    # never checks for a collision, so an overlap would silently let
    # whichever interval is painted later erase part of an earlier one --
    # non-overlap is enforced here, by construction, rather than by trusting
    # the two search functions to agree.
    #
    # **The rule is: an overlapping saccade candidate MERGES into whatever
    # it overlaps, rather than being dropped.** This implementation's first
    # version dropped the later candidate outright, and review found that
    # rule wrong in a way worse than undercounting. A saccade's own peak
    # occasionally splits into two `_true_runs` peak-runs -- sensor noise,
    # or Table 2's own max-velocity/acceleration rejection excluding one
    # sample from a real saccade's crest -- and the two candidates'
    # independent onset/offset searches then return OVERLAPPING spans:
    # typically the SECOND one's own backward search, unable to read the
    # brief dip between the two peaks as a genuine local minimum, walks all
    # the way back past the FIRST candidate's own onset, making the second
    # candidate a strict SUPERSET of the first (`test_no_run_overlaps_
    # another`'s own fixture, verified directly: candidates `(595, 612)`
    # and `(595, 626)`). DROPPING that superset kept only the narrower
    # first span and left the region between the two peaks unclaimed, and
    # `_insert_trace` then filled that unclaimed region with `fixation` --
    # turning a REAL second saccade into an explicit, false claim that the
    # eye was still. That is not an omission: per the paper (p. 196)
    # fixations are "everything that is not noise, saccades, or glissades",
    # so labelling a genuine saccade `fixation` contradicts the algorithm's
    # own definition -- and it is exactly the confound parent design spec
    # section 3.2 warns a reimplementation can manufacture, one that "does
    # not look like a defect, it looks like a finding", in the one detector
    # whose entire purpose is to be compared against six others.
    #
    # MERGING instead: two peaks whose independent bounds searches overlap
    # are far more plausibly ONE saccade with a double-peaked velocity
    # profile -- which sensor noise, Table 2's own rejection, and a
    # glissade-like wobble mid-flight can all produce -- than two genuinely
    # separate saccades landing within the same collision-scale window,
    # which the oculomotor refractory period makes implausible. `_merged_
    # bounds` below widens `[onset, offset)` to the union of every run
    # (saccade OR glissade) the new candidate overlaps and removes them, so
    # the result is one wider SACCADE run rather than a gap `_insert_trace`
    # would otherwise paint over as `fixation`.
    #
    # **The residual cost is the mirror image of the one this replaces, and
    # smaller.** A genuinely double-STEP saccade -- two real, distinct hops
    # landing close enough together to collide -- is now reported as ONE
    # merged saccade whose amplitude is the two hops' NET displacement, not
    # two separate ones. That is a real event reported as one instead of
    # two (a measurement error on something that happened), not a genuine
    # event erased and replaced with a fabricated period of stillness.
    runs: list[Run] = []
    claimed = np.zeros(speed.size, dtype=bool)
    for peak_start, peak_stop in _true_runs((speed > thresholds.peak_deg_s) & usable):
        bounds = _saccade_bounds(speed, peak_start, peak_stop, thresholds, fs_hz, params)
        if bounds is None:
            continue
        onset, offset, offset_threshold = bounds
        onset, offset = _merged_bounds(runs, onset, offset)
        runs.append(Run(start=onset, stop=offset, label=Label.SACCADE))
        claimed[onset:offset] = True

        glissade = _glissade_bounds(
            speed, gaze_deg, onset, offset, offset_threshold, thresholds, fs_hz, params
        )
        # **This check is reachable, and it is the glissade-side twin of
        # the saccade-side merge above -- not free insurance against an
        # impossible case.** An earlier version of this comment claimed the
        # overlap branch below was provably unreachable, reasoning that a
        # glissade's own span starts at `offset` and only extends forward,
        # so nothing claimed EARLIER (by a different, prior event) can
        # reach it. That reasoning is correct as far as it goes and still
        # rules out a DIFFERENT event colliding here -- but it missed the
        # SAME saccade being processed twice: a triangular velocity profile
        # occasionally has its own apex replaced by a shallow dip (sensor
        # noise) that splits one `_true_runs` peak-run into two without
        # either half's own `_saccade_bounds` search reading the dip as a
        # genuine local minimum, so both halves resolve to the IDENTICAL
        # `(onset, offset, offset_threshold)`. The saccade side of this is
        # already handled -- `_merged_bounds` merges the second, identical
        # candidate into a no-op -- but `_glissade_bounds` is a pure
        # function of those same three values, so it is called a second
        # time with IDENTICAL inputs and returns the IDENTICAL span the
        # first occurrence already claimed. Without this check that
        # identical span would be appended twice.
        # `test_a_recomputed_glissade_does_not_duplicate_an_already_claimed_
        # one` constructs exactly this, confirms one `pso` run rather than
        # a literal duplicate, and confirms directly (mutating this line to
        # drop the `not claimed[...].any()` half) that removing this check
        # reproduces the duplicate.
        if glissade is not None and not claimed[glissade[0]:glissade[1]].any():
            runs.append(Run(start=glissade[0], stop=glissade[1], label=Label.PSO))
            claimed[glissade[0]:glissade[1]] = True

    # Fixations are "everything that is not noise, saccades, or glissades"
    # (paper p. 196), subject to tau_min. The `min_fixation` floor applied
    # here is invisible once a trace reaches storage -- `_insert_trace`
    # (schema/detect.py) fills every sample no returned interval claims with
    # `fixation` regardless, so a too-short leftover stretch is labelled
    # `fixation` there either way. It is not invisible to a caller that
    # reads this function's OWN return value directly, which is how design
    # spec section 5's validation (a later task) measures "fixation
    # duration" against the paper's reported statistic -- an unfiltered
    # return would count every sub-tau_min leftover as its own fixation and
    # bias that measurement low.
    min_fixation = max(int(round(params.min_fixation_duration_ms * fs_hz / 1000.0)), 1)
    for start, stop in _true_runs(~claimed & usable):
        if stop - start >= min_fixation:
            runs.append(Run(start=start, stop=stop, label=Label.FIXATION))

    return sorted(runs, key=lambda run: run.start)


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal `True` stretches as half-open intervals. Same shape as
    `engbert_kliegl.py`'s own private helper; duplicated rather than shared
    because that one is private to its module and this detector's is the
    second use, not yet a third."""
    padded = np.concatenate(([False], mask, [False]))
    edges = np.diff(padded.astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))


def _merged_bounds(runs: list[Run], onset: int, offset: int) -> tuple[int, int]:
    """`[onset, offset)` widened to the union of every run in `runs` it
    overlaps, with those runs removed from `runs` in place.

    See `detect_nystrom_holmqvist`'s own comment for why an overlapping
    saccade candidate merges into what it overlaps rather than being
    dropped. A no-op (returns `(onset, offset)` unchanged, removes nothing)
    when nothing in `runs` overlaps -- the ordinary case, and the only one
    on a trace built from well-separated events.

    **One sweep of `runs` is provably enough; a second could never find
    more.** Every run already in `runs` was itself added only after this
    same function found no overlap for it, so `runs` is pairwise
    non-overlapping THROUGHOUT -- for any two runs R and R' in it with R
    before R', non-overlap means `R.stop <= R'.start`. Absorbing R can
    therefore widen `offset` to at most `R.stop`, which is at most
    `R'.start` -- never strictly past it -- so absorbing one run already in
    the candidate's reach can never bring a second, untouched run into
    reach. One pass over `runs`, updating `onset`/`offset` as it goes and
    removing what it absorbs, finds everything any number of passes could.
    """
    for index in range(len(runs) - 1, -1, -1):
        run = runs[index]
        if run.start < offset and onset < run.stop:      # half-open overlap test
            onset = min(onset, run.start)
            offset = max(offset, run.stop)
            del runs[index]
    return onset, offset
