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

**This module is one step of several, by design -- not the whole detector.**
It carries the twelve published parameters and the paper's first step: the
adaptive peak-velocity threshold (p. 193, Figure 4) that converges to the
data's own noise floor rather than asking an experimenter to choose one,
which is what makes the algorithm "settings-free for the user." Onset/offset
search, saccade and glissade detection, fixation detection, and registration
with `registry.py` are separate tasks in the same implementation plan and are
not yet in this file -- `nystrom_holmqvist` does not yet appear in
`registry.DETECTORS`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


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
