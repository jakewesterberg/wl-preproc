def test_the_threshold_converges_to_the_papers_own_arithmetic():
    """Spec §1.1, from the paper p. 193 and Figure 4. Iterate
    `PTn = mu(n-1) + 6*sigma(n-1)` over samples BELOW the previous threshold,
    stopping when `|PTn - PT(n-1)| < 1 deg/s`.

    On a normal noise floor with a few large peaks, the sub-threshold
    population IS the noise, so the converged value must land at
    `mu + 6*sigma` of that noise -- which is a number this test computes
    independently rather than reading back from the implementation."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, _peak_threshold,
    )

    rng = np.random.default_rng(0)
    noise = np.abs(rng.normal(5.0, 2.0, 5000))
    speed = noise.copy()
    speed[1000:1010] = 400.0          # a saccade, far above any noise level
    usable = np.ones(speed.size, dtype=bool)

    result = _peak_threshold(speed, usable, DEFAULT_NH_PARAMS)

    expected = noise.mean() + 6.0 * noise.std()
    assert abs(result.peak_deg_s - expected) < 2.0, (result.peak_deg_s, expected)
    assert result.converged
    assert result.onset_deg_s < result.peak_deg_s


def test_the_starting_value_does_not_matter():
    """The paper, p. 193: the initial threshold "could be in the range
    100-300 deg/sec, but the choice is not critical as long as there are
    saccades with peak velocities reaching this threshold." A converged
    result that moved with the start would mean the iteration is not
    converging at all."""
    import dataclasses

    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, _peak_threshold,
    )

    rng = np.random.default_rng(1)
    speed = np.abs(rng.normal(5.0, 2.0, 5000))
    speed[2000:2012] = 500.0
    usable = np.ones(speed.size, dtype=bool)

    results = [
        _peak_threshold(
            speed, usable,
            dataclasses.replace(DEFAULT_NH_PARAMS, initial_peak_threshold_deg_s=start),
        ).peak_deg_s
        for start in (100.0, 200.0, 300.0)
    ]

    assert max(results) - min(results) < 1.0, results


def test_a_constant_speed_terminates_by_emptying_not_by_converging():
    """Renamed at review from `test_an_oscillating_distribution_terminates`:
    the fixture below does not oscillate. Traced directly, it terminates
    after 2 iterations via the `below.size == 0` branch (`nystrom_holmqvist.
    py`'s early return when nothing remains below the current threshold) --
    a genuinely different branch from `max_iterations` exhaustion, which
    `test_a_monotone_crawl_exhausts_max_iterations` covers separately.

    An initial round of review asked whether any distribution exhausts
    `max_iterations` at all; an adversarial search (tens of thousands of
    random multi-cluster and heavy-tailed constructions, plus an exhaustive
    sweep of the most direct family for engineering a two-value oscillation)
    found none, and that search is real and stands on its own -- but it was
    a search for OSCILLATION, and the cap turned out to be reachable by a
    different mechanism the search never constructed: a monotone crawl
    through many tiers, no cycling required. See
    `test_a_monotone_crawl_exhausts_max_iterations` and `_peak_threshold`'s
    own docstring. Recorded here so a reader of this test's history does not
    re-derive the same too-narrow conclusion the search first did.

    What this test still checks, and it is worth checking on its own:
    spec §9 item 2's point that the paper states no iteration cap, so
    `converged=False` on a degenerate input must be an honest report
    (uniform speed collapses the threshold onto the data, so no sample is
    ever below it) rather than a crash or a false `converged=True`."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, _peak_threshold,
    )

    # Uniform speed: every sample equals the mean, sigma is 0, so the update
    # collapses the threshold onto the data and no sample ever falls below it.
    speed = np.full(1000, 50.0)
    usable = np.ones(1000, dtype=bool)

    result = _peak_threshold(speed, usable, DEFAULT_NH_PARAMS)

    assert result.iterations <= DEFAULT_NH_PARAMS.max_iterations
    assert not result.converged


def test_a_monotone_crawl_exhausts_max_iterations():
    """Round-2 review finding: `max_iterations` IS reachable, using the
    unmodified `DEFAULT_NH_PARAMS` -- the "unreachable in practice" framing
    this test's docstring (and `_peak_threshold`'s) previously carried was
    wrong, and this replaces it with a construction rather than a search.

    The construction: seed `[1.0, 3.0]`, then repeatedly append
    `nextafter(g, -inf)` where `g` is `mean + 6*sigma` of the array built so
    far. Each new element is placed just BELOW where the next iteration's
    own threshold update will land, so feeding the finished array back
    through `_peak_threshold` from `initial_peak_threshold_deg_s=200.0`
    retraces the exact sequence of thresholds used to build it -- pulling in
    one new element per iteration, a monotone crawl through many tiers
    rather than a cycle. `test_a_constant_speed_terminates_by_emptying_not_
    by_converging`'s closed-form result (no 2-value OSCILLATION can do this,
    for the natural family checked there) is unaffected: this is not an
    oscillation, and does not need to be one.

    Verified directly before writing this assertion, not assumed: with 142
    elements, `below.size` runs 6, 7, 8, ..., 105 across all 100 iterations
    (never empty, never converging), every value is non-negative, and the
    run ends at `peak_deg_s` == 2279318364842877.0 deg/sec -- reproduced
    exactly against the shipped code. That magnitude is the practical
    point: it is far past `max_velocity_deg_s` (1000 deg/sec, Table 2), so a
    later task's rejection step removes input like this before it ever
    reaches this function. The guard is real; it is not expected to fire on
    a real recording."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, _peak_threshold,
    )

    values = [1.0, 3.0]
    for _ in range(140):
        arr = np.array(values)
        g = arr.mean() + DEFAULT_NH_PARAMS.peak_threshold_sigma * arr.std()
        values.append(float(np.nextafter(g, -np.inf)))
    speed = np.array(values)
    usable = np.ones(speed.size, dtype=bool)

    assert speed.size == 142
    assert np.all(speed >= 0)

    result = _peak_threshold(speed, usable, DEFAULT_NH_PARAMS)

    assert result.iterations == DEFAULT_NH_PARAMS.max_iterations
    assert result.converged is False


def test_no_usable_samples_returns_the_stated_zero_default():
    """`nystrom_holmqvist.py`'s `sub.size == 0` early return: every sample
    already excluded by the mask leaves nothing to compute `mu, sigma` from.
    Untested until now -- coverage gap noted at review of this task."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _peak_threshold,
    )

    speed = np.abs(np.random.default_rng(3).normal(5.0, 2.0, 500))
    usable = np.zeros(500, dtype=bool)

    assert _peak_threshold(speed, usable, DEFAULT_NH_PARAMS) == PeakThreshold(0.0, 0.0, 0, False)


def test_unusable_samples_are_excluded_from_the_estimate():
    """The same reasoning `detect_engbert_kliegl`'s own docstring gives: a
    contaminating segment's velocity would inflate the noise scale and
    desensitise the detector for the whole recording -- if it survived into
    the estimate at all.

    **This fixture is not the task brief's original one, and the reason is
    load-bearing.** The brief's first draft set a 900 deg/s, 100-sample
    "blink" at `usable=False` and a 400 deg/s "real saccade" at `usable=True`,
    then compared masked against unmasked. Run against the implementation
    below, that fixture gives `masked == unmasked` bit-for-bit (verified
    directly), so the test as first drafted would fail -- not because the
    exclusion is broken, but because the fixture never exercises it.

    The reason: `DEFAULT_NH_PARAMS.initial_peak_threshold_deg_s` is 200.0
    (spec §7), and the iteration's threshold only ever falls from there
    toward the noise floor (`test_the_threshold_converges_to_the_papers_own_
    arithmetic` lands it near `mu + 6*sigma` of noise like this test's own,
    far below 200). A sample at 400 or 900 deg/s is therefore excluded by
    `sub[sub < threshold]` on the very first iteration regardless of
    `usable` -- the iteration is already robust to huge excursions on its
    own, masked or not, which is a genuine strength of the method and not a
    bug. What the mask actually has to rescue the estimate from is
    contamination that would otherwise survive INTO the converged
    threshold's own defining population: something elevated but not extreme,
    sitting below where the noise floor would settle. That is what this
    fixture contains instead. The 400 deg/s saccade is kept below as a
    control: usable, and excluded by the iteration itself either way, which
    is what shows the mask is doing real work only on the moderate
    contamination, not doing the whole job by itself."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, _peak_threshold,
    )

    rng = np.random.default_rng(2)
    speed = np.abs(rng.normal(5.0, 2.0, 3000))
    speed[500:800] = 20.0                      # moderate, sustained contamination
    usable = np.ones(3000, dtype=bool)
    usable[500:800] = False                    # ... masked here
    speed[1500:1512] = 400.0                   # a real saccade, kept usable: excluded
                                                # by the iteration itself either way

    masked = _peak_threshold(speed, usable, DEFAULT_NH_PARAMS).peak_deg_s
    unmasked = _peak_threshold(
        speed, np.ones(3000, dtype=bool), DEFAULT_NH_PARAMS
    ).peak_deg_s

    assert masked < unmasked, (masked, unmasked)


def test_the_local_noise_window_precedes_the_saccade():
    """Spec §1.2, from the paper p. 194: the local noise factor is computed
    "over the velocity samples within a window with size tau_min msec ... and
    PRECEDING the saccade currently being processed. To avoid contamination
    from glissadic movements."

    **This fixture is not the task brief's original one, and the reason is
    load-bearing.** The brief's first draft had the saccade's velocity jump
    directly from its plateau (300 deg/s) to the glissade's plateau (40
    deg/s) with no decay between them -- a pure step function. Run against
    the brief's own reference implementation (verified directly), that
    fixture gives `offset == 340` -- the far side of the glissade -- for
    BOTH a correctly-preceding window and a deliberately-inverted
    following one, because neither window's threshold (14.6 or 26.0) ever
    exceeds the glissade's flat 40 deg/s, so the forward search skips
    through the whole glissade regardless of which window computed the
    threshold. A test that reaches the same wrong answer both ways is not
    testing the direction it names, and 340 also falls outside that draft's
    own asserted range (318-325).

    The fixture below gives the saccade an actual decay: a shoulder at 25
    deg/s, then a true low point at 8 deg/s, and only then the glissade at
    40 deg/s. A window taken from BEFORE the saccade measures the quiet
    baseline (mean 2, std 0), giving a low offset threshold (14.6) that is
    below the shoulder and skips past it, stopping at the true low point
    (325) right where the glissade begins. A window taken from AFTER the
    saccade instead measures the shoulder and part of the glissade
    (mean ~34.6, std ~8.9), inflating the threshold to ~32.4 -- high enough
    to accept the shoulder itself (320) as the offset, cutting the saccade's
    own decay short. Both outcomes were verified directly against this
    task's implementation before this assertion was written."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _saccade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)               # quiet baseline
    speed[300:320] = 300.0                  # the saccade
    speed[320:325] = 25.0                   # a shoulder in its decay
    speed[325] = 8.0                        # the true low point, right after
    speed[326:346] = 40.0                   # a glissade right after that
    thresholds = PeakThreshold(
        peak_deg_s=100.0, onset_deg_s=20.0, iterations=2, converged=True
    )

    bounds = _saccade_bounds(speed, 300, 320, thresholds, fs, DEFAULT_NH_PARAMS)

    assert bounds is not None
    onset, offset, offset_threshold = bounds
    assert offset_threshold > 0.0
    assert offset_threshold < 25.0, offset_threshold  # low enough to skip the shoulder
    # The offset lands at the saccade's own true low point, NOT at the
    # shoulder a contaminated (post-saccade) window would have stopped at
    # (320), and not extended through the glissade.
    assert 295 <= onset <= 300, onset
    assert 323 <= offset <= 327, offset


def test_a_saccade_shorter_than_the_minimum_is_rejected():
    """Table 2: minimum saccade duration 10 msec -- "large enough to avoid
    noise being falsely categorized as saccades but small enough to include
    short saccades (~1 deg)". At 500 Hz that is 5 samples."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _saccade_bounds,
    )

    fs = 500.0
    speed = np.full(400, 2.0)
    speed[200:202] = 300.0                  # 2 samples = 4 ms, below 10 ms
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    assert _saccade_bounds(speed, 200, 202, thresholds, fs, DEFAULT_NH_PARAMS) is None


def test_a_saccade_not_preceded_by_stillness_is_rejected():
    """The paper, p. 195: "we exclude saccades that are preceded by a period
    where mu_t > theta_PT, since this indicates that there was no period of
    stillness prior to the saccade onset (most often, indicating recording
    imperfections)".

    **This fixture is not the task brief's original one, and the reason is
    load-bearing.** The brief's first draft set the elevated ("no
    stillness") stretch flush against the saccade's own rise, with no gap
    at all. Run against the brief's own reference implementation (verified
    directly), that fixture returns `(258, 320, 14.6)` -- a valid result,
    not the `None` the test asserts. The reason: the onset search walks
    backward through anything above the onset threshold regardless of how
    long the stretch is, so it walks straight through the whole elevated
    stretch and lands on the genuine baseline before it (258); the tau_min
    window immediately preceding THAT point then looks even further back,
    past the elevated stretch entirely, and measures only quiet baseline --
    the contamination is never seen by the check meant to catch it.

    The fixture below leaves a two-sample gap of genuine quiet between the
    elevated stretch and the saccade's rise -- just enough for the onset
    search to find its local minimum inside that gap (298) rather than
    behind the whole elevated stretch, so the tau_min window immediately
    preceding onset lands on the elevated stretch itself (mean 250, far
    above `theta_PT` = 100) rather than skipping past it."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _saccade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)
    speed[260:298] = 250.0                  # no stillness before the saccade
    speed[298:300] = 2.0                    # too brief a gap to count as rest
    speed[300:320] = 300.0
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    assert _saccade_bounds(speed, 300, 320, thresholds, fs, DEFAULT_NH_PARAMS) is None


def test_a_high_velocity_glissade_is_found():
    """Paper p. 195: the high-velocity criterion "requires that the velocity
    curve within a tau_min (40) msec window after the saccadic offset raises
    above the peak saccade threshold, theta_PT, and down below it, at least
    once. In other words, a high-velocity glissade has a velocity peak that
    would qualify it for saccadic status".

    **`assert stop > start` was this test's original, and only, check on
    `stop` -- too loose to discriminate the direction of the exceeds-
    threshold comparison.** Verified directly: mutating `_glissade_bounds`
    to compare `window < qualifying_threshold` instead of `>` and re-running
    left this test passing, because the inverted comparison still returns a
    `stop` past `start` for this fixture -- it picks up the quiet baseline
    beyond the excursion (index 339) rather than the excursion's own decay,
    landing at `stop == 340` instead of the correct 331. `331` is computed
    independently of the implementation: the last sample above 25 deg/s is
    index 329 (`speed[320:330]`'s final 150), the forward walk immediately
    finds `speed[330] - speed[331] == 0` (the baseline past the excursion is
    already flat), stopping at 330, and `stop` is one past that to stay
    exclusive like `Run`."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _glissade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)
    speed[300:320] = 300.0                       # saccade
    speed[320:330] = 150.0                       # above peak threshold (100)
    gaze = np.zeros((600, 2))
    gaze[320:, 0] = 0.4                          # small, smaller than the saccade
    gaze[300:320, 0] = np.linspace(0.0, 0.4, 20)
    gaze[:300, 0] = 0.0
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    bounds = _glissade_bounds(
        speed, gaze, 300, 320, 25.0, thresholds, fs, DEFAULT_NH_PARAMS
    )

    assert bounds is not None
    start, stop = bounds
    assert start == 320, "the glissade's onset IS the saccade's offset"
    assert stop == 331, stop


def test_a_low_velocity_glissade_is_found():
    """Same criterion, except the curve need only rise above the saccade
    OFFSET threshold rather than the peak threshold (paper p. 195, Figure
    5B). This is the one that catches the small post-saccadic wobbles that
    §2.5 argues a dual-Purkinje tracker shows after every saccade.

    **This test originally checked only `bounds[0]`, leaving `stop`
    unchecked at all** -- the same comparison-direction gap as the
    high-velocity test above, and verified the same way: mutating
    `_glissade_bounds` to compare `<` instead of `>` still returned a
    non-`None` result starting at 320, so an assertion that stopped at
    `bounds[0]` would not have caught it. `329` is computed independently:
    the last sample above 25 deg/s is index 327 (`speed[320:328]`'s final
    40), the forward walk immediately finds `speed[328] - speed[329] == 0`,
    and `stop` is one past that to stay exclusive."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _glissade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)
    speed[300:320] = 300.0
    speed[320:328] = 40.0                        # above offset (25), below peak (100)
    gaze = np.zeros((600, 2))
    gaze[300:320, 0] = np.linspace(0.0, 0.4, 20)
    gaze[320:, 0] = 0.45
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    bounds = _glissade_bounds(
        speed, gaze, 300, 320, 25.0, thresholds, fs, DEFAULT_NH_PARAMS
    )

    assert bounds is not None
    start, stop = bounds
    assert start == 320
    assert stop == 329, stop


def test_no_glissade_when_the_window_stays_quiet():
    """Below the offset threshold is not a glissade -- it is the fixation
    that follows the saccade."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _glissade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)
    speed[300:320] = 300.0
    gaze = np.zeros((600, 2))
    gaze[300:320, 0] = np.linspace(0.0, 0.4, 20)
    gaze[320:, 0] = 0.4
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    assert _glissade_bounds(
        speed, gaze, 300, 320, 25.0, thresholds, fs, DEFAULT_NH_PARAMS
    ) is None


def test_one_saccade_yields_at_most_one_glissade():
    """Spec §8 item 4 asks that the two criteria be "mutually exclusive."
    Under §3's resolution that is not a test of two code paths: both criteria
    emit `pso`, and their UNION is what Table 3's 47.8% measures. What
    remains checkable, and what matters to storage, is that one saccade never
    yields two overlapping glissade runs -- `_insert_trace` paints intervals
    onto one array, so a duplicate would silently overwrite itself.

    A window holding both a high-velocity excursion and a later low-velocity
    one must still produce a single run.

    **`stop >= 330` was this test's original check -- a floor, not a pin,
    and too loose to discriminate the comparison-direction bug the two
    tests above were fixed for.** Verified directly: a `<`-for-`>` mutation
    also lands past 330 for this fixture (index 339, the trailing baseline,
    rather than either excursion), so the floor was satisfied anyway. 331 is
    the same excursion-decay endpoint as `test_a_high_velocity_glissade_
    is_found`: the low-velocity plateau's last sample above 25 deg/s is
    index 329, and `stop` is one past that."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _glissade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)
    speed[300:320] = 300.0
    speed[320:324] = 150.0                       # above peak (100)
    speed[324:330] = 40.0                        # above offset (25), below peak
    gaze = np.zeros((600, 2))
    gaze[300:320, 0] = np.linspace(0.0, 0.4, 20)
    gaze[320:, 0] = 0.45
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    bounds = _glissade_bounds(
        speed, gaze, 300, 320, 25.0, thresholds, fs, DEFAULT_NH_PARAMS
    )

    assert bounds is not None
    start, stop = bounds
    assert start == 320
    assert stop == 331, "one run must span both excursions, not two"


def test_a_glissade_larger_than_its_saccade_is_omitted():
    """Paper p. 196: "Glissades with an amplitude larger than their
    preceeding saccades were omitted." A post-saccadic movement bigger than
    the saccade it follows is not lens wobble."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _glissade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)
    speed[300:320] = 300.0
    speed[320:330] = 150.0
    gaze = np.zeros((600, 2))
    gaze[300:320, 0] = np.linspace(0.0, 0.2, 20)   # a 0.2 deg saccade
    gaze[320:330, 0] = np.linspace(0.2, 3.0, 10)   # a 2.8 deg "glissade"
    gaze[330:, 0] = 3.0
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    assert _glissade_bounds(
        speed, gaze, 300, 320, 25.0, thresholds, fs, DEFAULT_NH_PARAMS
    ) is None


def test_the_high_velocity_criterion_survives_an_inflated_low_velocity_threshold():
    """The two criteria are genuinely independent, not merely nested by
    coincidence of typical magnitudes. `_saccade_bounds` blends a LOCAL
    noise estimate into `theta_ST^offset` (`alpha*theta_ST^onset +
    beta*theta_t`), and that local estimate's variance term is unbounded even
    though its MEAN is checked against `theta_PT` (the "no stillness" reject
    in `_saccade_bounds`) -- a pre-saccade baseline that is noisy but not
    elevated can push `theta_ST^offset` above `theta_PT` itself. When that
    happens, the paper's HIGH-velocity criterion is still a peak "that would
    qualify it for saccadic status" against the untouched, global `theta_PT`
    (p. 195) -- it does not inherit the inflated local number. Implementing
    the union as "above `offset_threshold_deg_s`" alone, rather than above
    WHICHEVER of the two thresholds is smaller, would silently lose every
    high-velocity glissade whenever this happens -- which spec §3's own
    inference (both criteria genuinely implemented, not one standing in for
    both by coincidence) requires not to happen.

    `offset_threshold_deg_s=500.0` here stands in for that inflated-local-
    noise case, deliberately far above `thresholds.peak_deg_s=100.0`; the
    post-saccadic excursion (150 deg/s) clears the peak threshold but not
    this inflated one."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _glissade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)
    speed[300:320] = 300.0
    speed[320:330] = 150.0                       # clears peak (100), not offset (500)
    gaze = np.zeros((600, 2))
    gaze[300:320, 0] = np.linspace(0.0, 0.4, 20)
    gaze[320:, 0] = 0.45
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    bounds = _glissade_bounds(
        speed, gaze, 300, 320, 500.0, thresholds, fs, DEFAULT_NH_PARAMS
    )

    assert bounds is not None, (
        "a genuine high-velocity excursion must not be hidden behind an "
        "inflated low-velocity threshold"
    )
    start, stop = bounds
    assert start == 320
    # Same excursion-decay endpoint as `test_a_high_velocity_glissade_is_
    # found` (the effective threshold here is also 100, since `min(500,
    # 100) == 100`): last sample above it is index 329, one past which is
    # 331. Pinned, rather than left as a bare not-None check, so this test
    # also discriminates a comparison-direction inversion in `_glissade_
    # bounds`, not only the union-drops-the-peak-threshold bug it was
    # written for.
    assert stop == 331, stop
