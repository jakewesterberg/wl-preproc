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
