"""Otero-Millan's cluster detector -- threshold-free, and it finds saccades of
any size.

Reimplemented from the published algorithm and from the MATLAB reference the
Martinez-Conde lab distributes (design spec section 3.2). **That reference
carries no licence**, and the download page states no terms, so absent a grant
all rights are reserved: it is readable as a specification of the algorithm --
which is how design spec section 3.1's vocabulary error was found -- and must
never be copied, vendored, or redistributed. Nothing here is transcribed from
it; what follows is this project's own code against the method it describes.

**Vocabulary: `saccade` and `microsaccade`, both** (design spec section 3.1 as
corrected 2026-09-01). The method's only amplitude rule is a LOWER noise floor
on a candidate cluster's mean displacement; there is no upper bound anywhere in
it. The "microsaccade" framing in the reference's bundled example comes from
that script's main-sequence plot limits, not from the detector.

**Why the method is threshold-free.** It does not ask whether a velocity
crosses a line. It takes a fixed density of the largest velocity peaks as
candidates whatever their size, describes each by how fast it went and how hard
it accelerated and braked, and lets k-means decide which of those candidates
form a distinct fast population. The number of clusters is chosen by silhouette
rather than fixed. The one absolute number left is the 0.2 degree floor, and it
exists to refuse a cluster made of noise -- not to size an event.

**No `scipy`, no `scikit-learn`; k-means and silhouette are implemented here.**
Two reasons, both load-bearing:

- Neither is a declared dependency of this package. They arrive transitively
  via `kilosort`, `spikeinterface` and `networkx`, all `where: serv` in
  `wl.yaml`. The eye path needs no container and no GPU, and importing them
  here would give it a dependency that exists only because the ephys stack
  happens to be installed -- invisible until a machine provisioned for eye work
  alone runs it.
- **Determinism.** `sklearn.cluster.KMeans` defaults to random initialisation
  with `n_init=10`. The reference seeds from velocity-sorted quantile means and
  is deterministic, which is what an agreement metric needs: a detector whose
  output varied run to run would make every score irreproducible and every
  disagreement unattributable to a method. The reference's seeding is not a
  limitation to work around; it is the property to preserve.

**Three places this reimplementation must depart from the reference, each
because of a decision made upstream of it:**

1. *Velocity.* The reference differentiates position with a Bartlett-windowed
   FIR of its own. Design spec section 3 fixes ONE velocity estimator for all
   seven detectors -- "the single most consequential preprocessing decision in
   this spec", because seven private differentiators would make every
   between-detector disagreement partly a disagreement about smoothing. So
   velocity arrives as an argument and is never recomputed here. Acceleration
   is derived from it (see `_acceleration`).
2. *No sampling rate.* The detector signature (design spec section 3) carries
   no `fs_hz`, and inventing one would be a fabricated number in the call. The
   reference's two rate-dependent constants are re-expressed without it: see
   `_PEAK_BUDGET_PER_MIN_ISI` and `OteroMillanParams.min_isi_samples`.
3. *No trials.* The reference clusters trial by trial, accumulating trials
   until a chunk holds enough events. This pipeline's detector contract has no
   trial structure, so chunks are formed from the candidate events themselves
   in temporal order -- see `_CLUSTER_CHUNK_EVENTS`.

**What is NOT pinned by a test, as of 2026-09-02.** A 53-mutation sweep over
this module left 25 survivors, 5 of them behaviour-changing. Chasing that to
zero is not convergent, so the survivors are recorded here rather than papered
over with tests that would only appear to check them:

- **The candidate budget's `ceil`, and `_CLUSTER_CHUNK_EVENTS`.** Both change
  the event set on real input. The chunk size is the reference's own 500 and
  the budget's rounding is a one-line arithmetic choice; neither has a fixture
  that would fail informatively rather than just differently.
- **Two tests are canaries, not pins.** `test_whitening_is_what_makes_a_small_
  event_findable_at_all` and the drift test fire broadly, with messages like
  `assert 0 == 3` that say a step broke without saying which. They are worth
  having and they are not diagnostics.
- **Two survivors are genuinely equivalent, and one is dead code in a live
  function.** Mean-centring inside `_whiten` is a no-op on the real call path,
  because features arrive already z-scored and so already have zero column
  means -- it is kept because `_whiten` is written to be correct for any input,
  not only for its one caller. And `_silhouette`'s "minimum over the OTHER
  clusters" is **unreachable from `_cluster_peaks`**: the labels it is handed
  are `np.minimum(assignment, 2)`, so there are at most two classes and the
  minimum is always over exactly one candidate. The general form is retained
  because the function states a general definition, but no call in this module
  exercises it.
- **The eigenvector sign canonicalisation** cannot be asserted on one machine
  at all -- see `_whiten`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from wl_preproc.eye.detect.labels import Run
from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude, classify

# The reference's `SACCADELIMITTH`, in deg/s. **Not a detection threshold**: no
# candidate is accepted or refused by it. It only says where an already-chosen
# velocity peak's event begins and ends, by walking out to the last sample
# below it on each side. Velocity reaches this module in deg/s, so the constant
# transfers directly with no rescaling.
#
# The reference pairs it with a second boundary test, `SACCADELIMITTHACC`, and
# that one is dead: the condition is `acc < SACCADELIMITTHACC` with the
# constant set to 0, while `acc` is a magnitude (`CartToPolar` of the two
# acceleration components) and so never negative. The clause can never fire.
# Not reimplemented, because implementing an always-false disjunct would put a
# line here that no input can reach.
_SACCADE_LIMIT_DEG_S = 5.0

# The reference's candidate budget, re-expressed without a sampling rate.
#
# It takes `RATEOFPEAKS = 5` peaks per second of valid data, and separates them
# by `MINIPI = 20 ms`. Their product is dimensionless -- 5/s x 0.020 s = 0.1 --
# so the budget is **one candidate per ten minimum-separations**, at any
# sampling rate. That is what is written here, because this detector's contract
# carries no `fs_hz` to turn "per second" into "per sample" with.
#
# **The budget is not a tuning knob; the method does not work without it.**
# Measured on this module's own test fixtures, at the shipped `min_isi_samples`
# of 10: with the budget the detector sees 40 candidates, finds every planted
# event, and returns nothing on a noise trace. Taking every local maximum
# instead (an unbounded candidate set) gives 186 candidates and returns
# NOTHING for a 0.4 degree event -- the real events are still in there, but
# they no longer form a cluster against that much noise. Enriching the
# candidate set is what makes a fast cluster exist to be found.
_PEAK_BUDGET_PER_MIN_ISI = 10

# The reference clusters in chunks -- it accumulates trials until a chunk holds
# at least 500 events, and absorbs a trailing remainder of fewer than 100 into
# the chunk before it. Its stated reason is that a recording's properties drift
# over time, so a partition fitted to the whole session fits none of it.
#
# There is a second reason to keep it here. Silhouette is O(n^2) in the events
# it scores: the reference recording is 1,177,799 samples, which at this
# module's own candidate density is 11,778 events, and one un-chunked pairwise
# distance matrix over those is ~1.1 GB. Chunked, it is ~2 MB, and the whole
# detector is linear in the recording's length.
#
# Chunks here are formed from the candidate events in temporal order, since
# this contract has no trials to accumulate (module docstring, departure 3).
_CLUSTER_CHUNK_EVENTS = 500
_CLUSTER_TAIL_EVENTS = 100

# Lloyd's iteration is run to convergence; this only bounds a pathological case
# so the detector cannot hang on a recording nobody is watching.
_KMEANS_MAX_ITER = 300


@dataclass(frozen=True, slots=True)
class OteroMillanParams:
    #: The reference's only amplitude rule, and it is a LOWER bound: a
    #: candidate cluster is accepted when its mean displacement exceeds this.
    #: There is no upper bound in the method -- the "microsaccade" framing in
    #: the reference's own Example.m comes from its plot limits, not from the
    #: detector (design spec section 3.1, corrected 2026-09-01).
    #:
    #: Displacement here is endpoint-to-endpoint, which is exactly what
    #: `measure.amplitude` computes and exactly what the reference's own
    #: `GetDisplacement` computes -- so the shared measurement and the
    #: reference's acceptance rule are the same quantity, not two that happen
    #: to agree. (The reference ALSO carries a bounding-box quantity it calls
    #: `amplitude`; the acceptance rule does not use it.)
    min_cluster_displacement_deg: float
    #: The reference's `NumMaxClusters`. k-means is run for 2..this many and
    #: the count is chosen by silhouette.
    max_clusters: int
    #: Minimum separation between candidate velocity peaks, in samples rather
    #: than milliseconds: this detector's contract (design spec section 3)
    #: carries no sampling rate, and converting here would require inventing
    #: one. It also sets the candidate budget -- see
    #: `_PEAK_BUDGET_PER_MIN_ISI`, which is why the two cannot be tuned
    #: independently.
    #:
    #: **The live constant is `MINIPI`, and it is 20 ms.** In the reference,
    #: `SaccadeDetectorCluster.FindPeaks` sets `this.MINIPI = round(20 *
    #: eyeRecording.samplerate / 1000)` and passes it to `myfindpeaks` as the
    #: neighbourhood width. Twenty milliseconds is **10 samples at 500 Hz**,
    #: which is the default below.
    #:
    #: **`SaccadeDetector.MIN_ISI = 30` is declared and read by nothing** --
    #: `grep -c MIN_ISI` over the whole package returns 1, its own
    #: declaration. (`MINPEAKVEL = 1` is dead in the same way.) It is written
    #: down here because this default was 15 for one round, derived from that
    #: dead constant read as 30 ms; both halves of that derivation were wrong,
    #: and without this note the next reader has every reason to redo it.
    min_isi_samples: int
    #: Declared for the same reason `EngbertKlieglParams` declares it -- this
    #: detector's vocabulary splits by amplitude, so it consumes the SHARED
    #: cut rather than owning one. See `schema/detect.py::_params_for`.
    microsaccade_max_deg: float = MICROSACCADE_MAX_DEG


DEFAULT_OM_PARAMS = OteroMillanParams(
    min_cluster_displacement_deg=0.2,
    max_clusters=4,
    min_isi_samples=10,  # the reference's live MINIPI: 20 ms at 500 Hz
)


def detect_otero_millan(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    available: np.ndarray,
    # Accepted and unused: this detector has no minimum DURATION at all --
    # its one temporal parameter, `min_isi_samples`, is a minimum peak
    # SEPARATION, and it is already in samples (`OteroMillanParams.
    # min_isi_samples`'s own docstring), not time.
    fs_hz: float,
    params: OteroMillanParams,
) -> list[Run]:
    """Labelled half-open `[start, stop)` intervals, in sample indices.

    `available` is the validity mask: `None` where a detector may label, a
    `Label` where the mask has already claimed the sample. **Unavailable
    samples are excluded from candidacy, from every event's extent, and
    therefore from the clustering's feature distribution** -- the reference
    drops any candidate spanning an invalid sample before it computes a single
    feature, and a blink's velocity spike would otherwise be one of the largest
    peaks in a recording and would pull the fast cluster onto itself.

    Returned intervals are disjoint and in ascending order of `start`.
    `schema/detect.py::_insert_trace` writes each interval's label onto one
    mask, so overlapping intervals would let a later one silently overwrite an
    earlier one -- and the exact-span `reliability` lookup that method performs
    is only well defined because these spans are already merged.

    **The saccade/microsaccade split is THIS detector's own work**, through
    `measure.py`'s shared `amplitude` and `classify` rather than a private
    formula, so design spec section 3's guarantee that a disagreement is "never
    a disagreement about measurement" holds literally.
    """
    usable = np.array([entry is None for entry in available], dtype=bool)
    if not usable.any():
        return []

    speed = np.hypot(velocity_deg_s[:, 0], velocity_deg_s[:, 1])
    accel = _acceleration(velocity_deg_s)
    min_isi = max(1, int(params.min_isi_samples))

    spans = _candidate_spans(speed, usable, min_isi)
    if len(spans) < 2:
        # One candidate cannot be clustered against anything, and the method
        # has no way to call a lone peak a saccade without a population to
        # compare it to. Zero is the honest answer, not a guess.
        return []

    peak_velocity, accel_onset, accel_brake, displacement = _features(
        gaze_deg, speed, accel, spans
    )
    # **Z-scoring and whitening are GLOBAL; only the clustering is chunked.**
    # The reference calls `GetFeatures` once over every peak in the recording
    # and chunks `ClusterPeaks` alone. Doing them per chunk instead -- which
    # this module did for one round -- rescales each chunk against its own
    # mean and covariance, which is precisely what defeats the chunking's
    # stated purpose: chunks exist because a recording's properties DRIFT, and
    # renormalising each one hides the drift it was supposed to accommodate.
    whitened = _whiten(
        np.column_stack([
            _zscore(_log_finite(peak_velocity)),
            _zscore(_log_finite(accel_onset)),
            _zscore(_log_finite(accel_brake)),
        ])
    )
    reliability = np.zeros(len(spans))
    accepted = np.zeros(len(spans), dtype=bool)

    for lo, hi in _chunks(len(spans)):
        cluster, silhouette = _cluster_peaks(
            whitened[lo:hi], peak_velocity[lo:hi], max(2, int(params.max_clusters))
        )
        reliability[lo:hi] = silhouette
        accepted[lo:hi] = _accept(
            cluster, displacement[lo:hi], params.min_cluster_displacement_deg
        )

    return [
        Run(
            start=start,
            stop=stop,
            label=classify(displacement[i], params.microsaccade_max_deg),
            reliability=float(reliability[i]),
        )
        for i, (start, stop) in enumerate(spans)
        if accepted[i]
    ]


def _acceleration(velocity_deg_s: np.ndarray) -> np.ndarray:
    """Magnitude of the rate of change of the velocity VECTOR, per sample.

    **This is `velocity.py`'s own five-point estimator, applied a second
    time**, and it is the same argument design spec section 3 makes one level
    up: the subsystem has ONE differentiator, and a detector that brought a
    private one would make its disagreement with another detector partly a
    disagreement about smoothing. The reference differentiates twice with its
    own Bartlett-windowed FIR, and reusing the shared estimator is the local
    form of the decision that already replaced that FIR for velocity.

    **It also matters numerically, which is not a thing this could be assumed
    about.** A naive `np.gradient` here is a much sharper differentiator than
    either the reference's or this one, and the noisier acceleration features
    it produces cost real detections: measured on two populated fixtures
    (`tests/schema/test_detect_populate.py`'s `stepped_session` and
    `out_of_order_session`), `np.gradient` made the mean silhouette at three
    clusters WORSE than at two on one of them, which stops `_cluster_peaks`'
    search one step before the count that isolates the saccades -- 29 accepted
    events where three were planted. The five-point estimator gives results
    identical to the reference's own Bartlett differentiator on both.

    **`fs_hz=1.0` is a choice of unit, not an invented sampling rate.** That
    argument is only a scale factor in `velocity()`, and both acceleration
    features enter the clustering as `zscore(log(a))`: `log(c * a) = log(c) +
    log(a)`, and a z-score subtracts the mean, so the additive `log(c)`
    cancels exactly. Acceleration per sample and acceleration in deg/s^2
    therefore produce the same feature, and no rate needs inventing to get it.

    **The derivative is of the two velocity COMPONENTS, then a magnitude** --
    not the derivative of the speed. The reference is explicit about this
    (`Differenciate` on the cartesian pair, then `CartToPolar`), and it
    matters: the derivative of a speed is blind to a turn at constant speed,
    which is exactly the braking geometry the third feature is there to catch.
    """
    from wl_preproc.eye.detect.velocity import velocity

    rate = velocity(velocity_deg_s, fs_hz=1.0)
    return np.hypot(rate[:, 0], rate[:, 1])


def _usable_segments(usable: np.ndarray) -> list[tuple[int, int]]:
    """Maximal available stretches, half-open. These are this contract's
    stand-in for the reference's trials: a candidate never crosses one, so no
    event can span a blink."""
    padded = np.concatenate(([False], usable, [False]))
    edges = np.diff(padded.astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))


def _sliding_max(values: np.ndarray, half_width: int) -> np.ndarray:
    """`max(values[i - half_width : i + half_width + 1])`, clipped at the ends.

    Computed by folding `2 * half_width` shifted copies rather than by slicing
    per sample: the reference tests every candidate against its own
    neighbourhood, and on a 1.18-million-sample recording the per-sample form
    is quadratic in the recording's length.
    """
    out = values.copy()
    for shift in range(1, half_width + 1):
        np.maximum(out[shift:], values[:-shift], out=out[shift:])
        np.maximum(out[:-shift], values[shift:], out=out[:-shift])
    return out


def _candidate_spans(
    speed: np.ndarray, usable: np.ndarray, min_isi: int
) -> list[tuple[int, int]]:
    """The reference's `FindPeaks` and `FindSaccadeLimits`, merged.

    Local maxima of speed, taken highest-first, each accepted only if it is the
    largest sample within `min_isi` on either side, each suppressing everything
    within `min_isi` of it once accepted, and only up to the candidate budget.
    Each surviving peak is then grown out to the last sample below
    `_SACCADE_LIMIT_DEG_S` on each side, and overlapping or touching spans are
    merged -- so a span is one event even when two peaks fell inside it.
    """
    n = speed.size
    if n < 3:
        return []

    # The largest sample in its own +/- `min_isi` neighbourhood, which is the
    # test the reference applies before it accepts a peak.
    #
    # **This subsumes the local-maximum test and replaces it.** The reference
    # first collects local maxima and then checks each against its
    # neighbourhood, and this module transcribed both -- but the second implies
    # the first: a sample that is at least as large as everything within
    # `min_isi` is in particular at least as large as its two neighbours. The
    # separate predicate contributed exactly one thing, excluding the first and
    # last samples of the trace, so that is kept explicitly below. Verified
    # over 12,000 random arrays (3,000 arrays x 4 neighbourhood widths): the
    # conjunction and this pair never disagree.
    is_peak = speed >= _sliding_max(speed, min_isi)
    # The first and last samples have no two-sided neighbourhood, so neither
    # can be a local maximum in the reference's sense.
    #
    # **Both lines are unreachable given this subsystem's own velocity, and
    # they are kept anyway.** `velocity.py` states ZERO for the two samples at
    # each edge rather than extrapolating, so `speed[0]` and `speed[-1]` are
    # zero and can never be the largest sample in a neighbourhood that contains
    # anything else -- removing either line changes no output and no test, which
    # a mutation sweep confirms. They are what makes this function correct for
    # an arbitrary speed array rather than only for the one array it is called
    # with, and dropping them would move that guarantee into an invariant of a
    # different module.
    is_peak[0] = False
    is_peak[-1] = False
    is_peak &= usable

    # Boundary walking needs, for every sample, the nearest sub-threshold
    # sample on each side. Precomputed once: per-peak searching over an
    # unbounded range is quadratic in the recording's length.
    index = np.arange(n)
    below = speed < _SACCADE_LIMIT_DEG_S
    last_below = np.maximum.accumulate(np.where(below, index, -1))
    next_below = np.minimum.accumulate(np.where(below, index, n)[::-1])[::-1]

    spans: list[tuple[int, int]] = []
    for seg_start, seg_stop in _usable_segments(usable):
        peaks = np.flatnonzero(is_peak[seg_start:seg_stop]) + seg_start
        if peaks.size == 0:
            continue
        budget = max(
            1, int(np.ceil((seg_stop - seg_start) / (_PEAK_BUDGET_PER_MIN_ISI * min_isi)))
        )
        # Descending peak value, ties to the earlier sample -- the order the
        # reference's repeated `max` visits them in, and a stable sort is what
        # keeps the tie-break from depending on numpy's sort implementation.
        order = peaks[np.argsort(-speed[peaks], kind="stable")]
        blocked = np.zeros(seg_stop - seg_start, dtype=bool)
        chosen: list[int] = []
        for peak in order:
            if len(chosen) >= budget:
                break
            if blocked[peak - seg_start]:
                continue
            chosen.append(int(peak))
            lo = max(seg_start, peak - min_isi) - seg_start
            hi = min(seg_stop, peak + min_isi + 1) - seg_start
            blocked[lo:hi] = True
        for peak in chosen:
            spans.append(_limits(peak, seg_start, seg_stop, speed, last_below, next_below))

    return _merge(spans)


def _limits(
    peak: int,
    seg_start: int,
    seg_stop: int,
    speed: np.ndarray,
    last_below: np.ndarray,
    next_below: np.ndarray,
) -> tuple[int, int]:
    """One peak's event extent, half-open.

    The reference's rule, in this repository's exclusive-`stop` convention: the
    event starts one sample after the last sub-threshold sample before the
    peak, and ends at the last supra-threshold sample after it. Where no
    sub-threshold sample exists in range the reference falls back to the
    slowest sample on that side, which is kept here for the same reason -- a
    peak with no quiet sample either side of it still has to be delimited
    somehow, and refusing to delimit it would silently drop a real event at the
    edge of a recording.
    """
    if peak > seg_start:
        j = int(last_below[peak - 1])
        if j >= seg_start:
            start = min(j + 1, peak - 1)
        else:
            start = seg_start + int(np.argmin(speed[seg_start:peak])) + 1
        start = max(seg_start, min(start, peak))
    else:
        start = seg_start

    if peak + 1 < seg_stop:
        k = int(next_below[peak + 1])
        if k < seg_stop:
            stop = max(k, peak + 2)
        else:
            stop = peak + 1 + int(np.argmin(speed[peak + 1 : seg_stop]))
        stop = min(seg_stop, max(stop, peak + 1))
    else:
        stop = seg_stop

    return start, max(stop, start + 1)


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Overlapping or touching spans coalesced, in ascending order.

    The reference merges here too, and before it computes any feature, so a
    merged span is ONE candidate with one feature vector and one silhouette --
    not two whose values would then have to be reconciled. Touching spans are
    merged as well as overlapping ones: `runs_from_labels` downstream produces
    maximal runs, so two touching intervals with the same label would become
    one run there anyway, matching neither span and losing its reliability.
    """
    out: list[tuple[int, int]] = []
    for start, stop in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], stop))
        else:
            out.append((start, stop))
    return out


def _features(
    gaze_deg: np.ndarray,
    speed: np.ndarray,
    accel: np.ndarray,
    spans: list[tuple[int, int]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per candidate: peak velocity, peak acceleration at onset and at braking,
    and displacement.

    **Displacement is `measure.amplitude`, not a private formula**, which is
    what lets the acceptance rule below and the label above be the same
    measurement the rest of the subsystem stores (design spec section 3).

    **Amplitude is computed by the reference and deliberately is not a
    clustering feature** -- its own `featureSelection` names log peak velocity
    and the two log accelerations and nothing else. Feeding amplitude to the
    clustering would make the method partly an amplitude threshold again, which
    is the property design spec section 3.1's correction exists to record it
    does not have.
    """
    n = len(spans)
    peak_velocity = np.zeros(n)
    accel_onset = np.zeros(n)
    accel_brake = np.zeros(n)
    displacement = np.zeros(n)
    for i, (start, stop) in enumerate(spans):
        window = speed[start:stop]
        at = int(np.argmax(window))
        peak_velocity[i] = float(window[at])
        # Onset runs from the event's start to ONE SAMPLE PAST the velocity
        # peak; braking runs from that same sample to the end. **They overlap
        # by exactly one sample, and that is the reference's own arithmetic**
        # -- `GetPeakAccelerationStart` takes `sac(i,1):(sac(i,1)+idxmax)` and
        # `GetPeakAccelerationBrake` takes `min(sac(i,1)+idxmax, sac(i,2)):
        # sac(i,2)`, with `idxmax` the 1-based offset of the velocity peak, so
        # both windows include absolute sample `start + at + 1`.
        #
        # The shared sample is the first of the deceleration, and it belongs to
        # both phases because the peak itself is the boundary: an event whose
        # braking is one sample long would otherwise have an empty window.
        # This module used disjoint windows for one round and described the
        # disjointness as a design property; it was a departure. Re-measured
        # with the onset window as the ONLY difference, over the large and
        # small fixtures at seeds 0-59 (120 traces): it moves the event set on
        # **22** of 120 and `reliability` on **117** of 120. An earlier version
        # of this comment said 11 and 89, which were neither of those figures.
        #
        # The upper clamp to `stop` is this module's own and is a real
        # departure: the reference does not clamp, so a velocity peak on an
        # event's last sample makes it read one sample BEYOND the event -- a
        # sample belonging to no event. **That is the whole of the reason.**
        # This comment used to add "and past the array end for an event at the
        # end of a recording", which is a MATLAB hazard, not a Python one:
        # numpy truncates an over-long slice silently.
        #
        # `min(..., len(accel))` would be the other defensible choice -- it
        # would match the reference on every span except a recording's last,
        # where clamping to `stop` differs on every span whose peak lands last.
        # Clamping to the event is a choice between two defensible rules, not
        # the only safe one.
        onset_stop = min(start + at + 2, stop)
        accel_onset[i] = float(accel[start:onset_stop].max())
        brake_start = min(start + at + 1, stop - 1)
        accel_brake[i] = float(accel[brake_start:stop].max())
        displacement[i] = amplitude(gaze_deg, start, stop)
    return peak_velocity, accel_onset, accel_brake, displacement


def _log_finite(values: np.ndarray) -> np.ndarray:
    """`log`, with non-positive entries pulled up to the smallest finite log.

    The reference does exactly this (`f(f==-Inf) = min(f(f~=-Inf))`), and it
    has to: a stationary sample can have zero acceleration, `log(0)` is `-inf`,
    and one `-inf` makes the whole feature's mean and standard deviation `nan`
    -- which would propagate silently through the whitening into every cluster
    assignment rather than raising anywhere.
    """
    out = np.full(values.shape, -np.inf)
    positive = values > 0
    out[positive] = np.log(values[positive])
    if not positive.all():
        out[~positive] = out[positive].min() if positive.any() else 0.0
    return out


def _zscore(values: np.ndarray) -> np.ndarray:
    """Centred and scaled by the SAMPLE standard deviation (`ddof=1`), matching
    the reference's `zscore`. A zero-variance feature returns zeros rather than
    `nan`: it carries no information, and `nan` would silently destroy the two
    features that do."""
    if values.size < 2:
        return np.zeros_like(values)
    spread = float(values.std(ddof=1))
    return np.zeros_like(values) if spread == 0.0 else (values - float(values.mean())) / spread


def _whiten(features: np.ndarray) -> np.ndarray:
    """The reference's whitening: `P = V @ diag(sqrt(1/(D + 0.1)))` from the
    eigendecomposition of the covariance, keeping components whose eigenvalue
    is more than 5% of the largest.

    The `+ 0.1` is a ridge. Without it a near-degenerate direction -- three
    features of which two are strongly correlated, which log velocity and log
    onset acceleration certainly are -- would be divided by a near-zero
    eigenvalue and blown up to dominate every distance the clustering computes.

    **Eigenvector signs are canonicalised, and that is a determinism
    guarantee rather than tidiness.** `eigh` fixes each eigenvector only up to
    sign, and which sign LAPACK returns can differ between builds. Within one
    process the seeds are computed from the same whitened matrix, so k-means is
    unaffected -- but the per-detection `reliability` this detector stores is a
    real number computed in that space, and a lab that cannot reproduce it on
    another machine cannot audit it. Pinning the sign so each eigenvector's
    largest-magnitude component is positive costs nothing and removes the
    question.

    **No test asserts this, and none can.** Removing the canonicalisation is
    an equivalent mutation on any single machine: one LAPACK build returns one
    set of signs, so both versions agree with themselves everywhere the suite
    can look. The claim is about agreement BETWEEN builds, and a second build
    is what it would take to observe it. Recorded here rather than pinned by a
    test that would only appear to check it.
    """
    covariance = np.atleast_2d(np.cov(features, rowvar=False))
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    for column in range(eigenvectors.shape[1]):
        dominant = int(np.argmax(np.abs(eigenvectors[:, column])))
        if eigenvectors[dominant, column] < 0:
            eigenvectors[:, column] = -eigenvectors[:, column]

    eigenvalues = np.maximum(eigenvalues, 0.0)
    scale = eigenvectors @ np.diag(np.sqrt(1.0 / (eigenvalues + 0.1)))
    rotated = (features - features.mean(axis=0)) @ scale

    largest = eigenvalues[-1]
    keep = (eigenvalues / largest) > 0.05 if largest > 0 else np.ones_like(eigenvalues, bool)
    if not keep.any():
        keep = np.zeros_like(eigenvalues, dtype=bool)
        keep[-1] = True
    return rotated[:, keep]


def _chunks(n_events: int) -> list[tuple[int, int]]:
    """Consecutive blocks of at least `_CLUSTER_CHUNK_EVENTS` events, with a
    trailing remainder shorter than `_CLUSTER_TAIL_EVENTS` absorbed into the
    block before it -- the reference's own rule, over events instead of trials
    (module docstring, departure 3)."""
    if n_events <= _CLUSTER_CHUNK_EVENTS:
        return [(0, n_events)]
    bounds = list(range(0, n_events, _CLUSTER_CHUNK_EVENTS))
    out = [(lo, min(lo + _CLUSTER_CHUNK_EVENTS, n_events)) for lo in bounds]
    if out[-1][1] - out[-1][0] < _CLUSTER_TAIL_EVENTS and len(out) > 1:
        out[-2] = (out[-2][0], out[-1][1])
        out.pop()
    return out


def _seeds(whitened: np.ndarray, peak_velocity: np.ndarray, n_clusters: int) -> np.ndarray:
    """The reference's deterministic seeding: cluster `i` starts at the mean of
    the `i`-th equal-sized block of candidates sorted by ascending peak
    velocity.

    **This is the whole determinism story**, and it is the reason no clustering
    library is used here. A random or k-means++ start makes the partition, and
    therefore every stored `reliability` and every pairwise agreement score
    computed from these events, a different number on every run.
    """
    order = np.argsort(peak_velocity, kind="stable")
    block = len(peak_velocity) // n_clusters
    seeds = np.zeros((n_clusters, whitened.shape[1]))
    for i in range(n_clusters):
        members = order[i * block : (i + 1) * block]
        seeds[i] = whitened[members].mean(axis=0) if members.size else whitened.mean(axis=0)
    return seeds


def _kmeans(whitened: np.ndarray, n_clusters: int, seeds: np.ndarray) -> np.ndarray:
    """Lloyd's iteration on squared Euclidean distance, from fixed seeds.

    An empty cluster keeps its previous centroid rather than being reseeded:
    reseeding is where every library implementation reaches for a random state,
    and holding the centroid still is both deterministic and stable -- the
    cluster is simply unused, which the silhouette then scores accordingly.

    **A stated deviation: MATLAB's `kmeans` runs a batch phase AND an online
    phase**, the latter moving single points between clusters when that lowers
    the total within-cluster sum of squares. This is batch only. Tested rather
    than assumed: the online phase was implemented and run against both
    populated fixtures, and the total SSE was identical with and without it
    (97.044 at two clusters on the one that motivated the check), so the batch
    result was already a fixed point of the online criterion there. That is
    evidence on two recordings, not a proof -- a partition this reaches and the
    online phase would improve is possible, and would show up as a different
    cluster assignment rather than as any error.
    """
    centroids = seeds.copy()
    assignment = np.full(whitened.shape[0], -1)
    for _ in range(_KMEANS_MAX_ITER):
        distance = ((whitened[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
        updated = np.argmin(distance, axis=1)
        if np.array_equal(updated, assignment):
            break
        assignment = updated
        for cluster in range(n_clusters):
            members = assignment == cluster
            if members.any():
                centroids[cluster] = whitened[members].mean(axis=0)
    return assignment


def _rank_by_velocity(
    assignment: np.ndarray, peak_velocity: np.ndarray, n_clusters: int
) -> np.ndarray:
    """Renumber clusters `1..n` by DESCENDING mean peak velocity, so cluster 1
    holds the fastest candidates.

    The reference sorts by velocity, and its own comment beside the line calls
    it magnitude -- the code is what is followed here. The numbering is not
    cosmetic: the acceptance rule below takes clusters `1 .. n-1` and never the
    last, so which cluster is last is which cluster is treated as noise.
    """
    means = np.array([
        peak_velocity[assignment == cluster].mean() if (assignment == cluster).any() else -np.inf
        for cluster in range(n_clusters)
    ])
    rank = np.argsort(-means, kind="stable")
    renumbered = np.zeros(n_clusters, dtype=int)
    for position, cluster in enumerate(rank):
        renumbered[cluster] = position + 1
    return renumbered[assignment]


def _silhouette(whitened: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Per-observation silhouette on squared Euclidean distance, the metric the
    reference's `silhouette` and its k-means both use.

    Returned per observation, not averaged. The reference computes it per peak
    and keeps only `mean(...)` as a session statistic -- the per-detection value
    exists in the method and is what `EyeDetection.Run.reliability` was reserved
    for (design spec section 5).

    **A lone member of its own cluster scores 1, not 0.** It has no
    within-cluster distance, so `a = 0` and `s = (b - 0) / b = 1` -- maximum
    confidence, which is what the reference's `max(count - 1, 1)` divisor
    produces and what the loop below implements.

    This paragraph said the opposite for one round ("scores 0, which is the
    convention"), with a justification attached, twelve lines above the code
    that already disagreed with it. Recorded rather than quietly rewritten: a
    comment that argues for a defect is worse than no comment, because a
    maintainer reading top-down meets the wrong rule with a reason to keep it.
    """
    n = whitened.shape[0]
    out = np.zeros(n)
    present = np.unique(labels)
    if present.size < 2:
        return out
    distance = ((whitened[:, None, :] - whitened[None, :, :]) ** 2).sum(axis=2)
    for i in range(n):
        own = labels == labels[i]
        count = int(own.sum())
        # `max(count - 1, 1)`, the reference's own divisor, and it is what
        # gives a SINGLETON the right answer. A point alone in its cluster has
        # no within-cluster distance: the sum is zero, so `within` is zero and
        # the silhouette is `(between - 0) / between == 1`. Skipping the point
        # and leaving zero -- which this did for one round -- stores the value
        # meaning "no confidence" for the detection the clustering was most
        # certain about, and 0.0 passes a `-1 <= r <= 1` range check happily.
        within = float(distance[i][own].sum()) / max(count - 1, 1)
        between = min(
            float(distance[i][labels == other].mean())
            for other in present
            if other != labels[i]
        )
        widest = max(within, between)
        out[i] = 0.0 if widest == 0 else (between - within) / widest
    return out


def _cluster_peaks(
    whitened: np.ndarray, peak_velocity: np.ndarray, max_clusters: int
) -> tuple[np.ndarray, np.ndarray]:
    """One chunk's cluster assignment and its per-candidate silhouette.

    k-means for 2, 3, ... clusters, stopping as soon as the mean silhouette
    fails to beat the best so far by more than 1%, and keeping the previous
    count when it does.

    **The silhouette is computed on the BINARISED partition `min(index, 2)`**
    -- saccades against everything else -- which is the reference's own choice
    and has a consequence worth stating, because it is visible in this
    detector's output. Splitting the SLOW mass finer does not change the
    binarised partition at all, so its silhouette is unchanged and the search
    stops. The count therefore only grows while the FAST cluster's membership
    is still changing. A population that is real but small relative to the
    noise it sits among will not be given its own cluster, and will be judged
    by the mean displacement of whatever cluster absorbs it -- see this
    module's own test for both sizes in one trace, which is why that test
    plants its two sizes at matched peak velocity.
    """
    best_silhouette = 0.0
    best: tuple[np.ndarray, np.ndarray] | None = None
    for n_clusters in range(2, max_clusters + 1):
        if n_clusters > whitened.shape[0]:
            break
        assignment = _rank_by_velocity(
            _kmeans(whitened, n_clusters, _seeds(whitened, peak_velocity, n_clusters)),
            peak_velocity,
            n_clusters,
        )
        per_candidate = _silhouette(whitened, np.minimum(assignment, 2))
        mean_silhouette = float(per_candidate.mean())
        # The reference tests `smax * 1.01 > s` with `smax` starting at 0, so
        # a first pass whose silhouette is not positive leaves its own result
        # variable unassigned and the function raises. Keeping the first pass
        # unconditionally is the same rule with that hole closed: two clusters
        # is the fewest this method considers, so there is always an answer.
        if best is not None and best_silhouette * 1.01 > mean_silhouette:
            break
        best_silhouette = mean_silhouette
        best = (assignment, per_candidate)
    if best is None:
        return np.ones(whitened.shape[0], dtype=int), np.zeros(whitened.shape[0])
    return best


def _accept(
    assignment: np.ndarray, displacement: np.ndarray, min_displacement_deg: float
) -> np.ndarray:
    """Clusters `1 .. n-1` whose MEAN displacement exceeds the floor.

    **The last cluster is never accepted**, whatever its displacement: it is
    the slowest, and the method's structure is that there is always a
    non-saccadic population to be separated from. Accepting it would mean a
    recording of pure fixation returned every velocity ripple in it as an
    event, which is exactly what would make an agreement metric meaningless.

    **The floor is on the cluster's mean, not on each event.** A cluster is
    accepted or refused whole. That is what keeps the rule a statement about a
    population rather than an amplitude threshold applied one event at a time,
    and it is why a small real population absorbed into a noisy cluster is lost
    rather than partially kept.
    """
    accepted = np.zeros(assignment.shape[0], dtype=bool)
    for cluster in range(1, int(assignment.max())):
        members = assignment == cluster
        if members.any() and float(displacement[members].mean()) > min_displacement_deg:
            accepted |= members
    return accepted
