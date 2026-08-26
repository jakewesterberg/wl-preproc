"""KS4 on CPU, minutes not seconds, and not part of the default run.

Marked `slow` and excluded from CI. It exists because spec section 7 rules that
the fixture should demonstrate the fault rather than warn about it, and a
demonstration that never runs is a warning with extra steps -- so run it by hand
whenever the probe, the geometry module or the KS4 version changes.

**Fix round 1: the metric changed, not the finding.** The first version of this
test compared raw `len(sorting.get_unit_ids())` between the two settings. That
FAILED, and stayed failed on re-run: at seed 20270317 (60 s, 12 planted units)
the 32 um default produced 56 total units, the derived spacing 57 -- the wrong
direction. Matching sorter output back to ground truth explained why rather
than excusing it: ~44 output clusters trace to no planted unit at all, under
EITHER setting (43 at default, 45 derived) -- almost certainly noise-driven,
since this fixture's spatially correlated background noise (`waveforms.py`'s
`NOISE_SPATIAL_DECAY_UM`) gets about 9x more real time to cross threshold at
60 s than in the un-widened 6.7 s smoke test. A population this large and this
close between the two settings swamps a raw-count difference of one,
regardless of which way that one difference points. Raw cluster count is a
proxy for "planted units get split"; a metric a confound this size dominates
cannot demonstrate the claim either way.

This version measures the claim directly instead of inferring it from a
population count: for each of the 12 planted units, does its own spike train
land substantially in more than one output cluster? See
`_count_fragmented_units` and `FRAGMENT_SHARE_THRESHOLD`. At seed 20270317,
3/12 planted units are fragmented at the default vs 1/12 derived (mean
dominant-cluster purity 0.871 -> 0.903 -- see the fix-round report for the
full per-unit table). That direction is not a threshold artefact: swept
`FRAGMENT_SHARE_THRESHOLD` from 0.10 to 0.30 against this seed's already-
computed output and default fragmented strictly more units than derived at
every value through 0.25 (3-vs-2 at 0.10/0.15, 3-vs-1 at 0.20/0.25),
collapsing to a tie only at 0.30, where the threshold is loose enough to call
almost nothing a fragment.

**Why the derived spacing helps at all** -- verified directly in KS4's own
`kilosort/spikedetect.py`, not assumed. `template_centers()` lays out
candidate template-centre x-positions as
`linspace(xmin, xmax, round((xmax-xmin)/(dminx/2))+1)`. For NP1032 (columns at
0 and 103 um): the default's dminx=32 grid is the 7 columns
`[0, 17.2, 34.3, 51.5, 68.7, 85.8, 103]`, of which
`igood = ds[0,:] <= max_channel_distance**2` (also in `spikedetect.py`) keeps
only the 4 nearest a real column -- `{0, 17.2, 85.8, 103}` -- never the 51.5 um
midpoint. The derived dminx=103 grid collapses to exactly `[0, 51.5, 103]`,
and now the midpoint survives the (103 um) distance filter too.
`nearest_chans` (10, untouched by this task) then picks each surviving grid
column's 10 nearest REAL channels by raw distance: at every x except the exact
midpoint, NP1032's 20 um row pitch means same-column neighbours always win, so
only the midpoint column ever draws channels from BOTH NP1032 columns -- and
post-filter, it exists only under the derived setting. This is a narrow
bridge, not "every channel pair gets compared" -- which is also why the
per-unit effect is not perfectly monotonic (the fix-round report has a unit
that improves under derived and one, at the same seed, that gets worse).

**How many seeds back this, and what they actually showed.** Three:
20270317 (`SPATIAL_RECIPE`'s own seed), 20270318 and 20270319 -- the next two
in this repository's own date-seed convention (`CI_RECIPE`=20270314 through
`SPATIAL_RECIPE`=20270317 in `synth/recipe.py`), chosen before any of the two
new ones was run, not picked afterwards for agreeing with seed 20270317.

The aggregate does NOT hold. Per seed, (fragmented at default, fragmented
derived): 20270317 -> (3, 1), supporting the claim; 20270318 -> (0, 0), no
signal either way; 20270319 -> (1, 3), the OTHER direction. Summed: 4
fragmented at the default, 4 derived -- a tie, not a win. This is reported
exactly as measured, per the instruction that produced this file's second
version: "I would rather learn the claim is wrong than ship a demonstration
that only works on one draw." It did not replicate past one draw.

**Task 8 (2026-08-26) turned this from a directional assertion into a
measurement-recording one, and dropped `xfail(strict=True)`.** `xfail`
idiomatically promises a known bug with a fix pending; this is not that --
there is no pending fix, the tie above is the actual result, and asserting
either direction here would claim more than three seeds settle. The test
below therefore still sorts all six recordings (three seeds x two settings)
for real, and asserts only that the machinery worked: every sort completes
and produces well-formed, ground-truth-matchable output. The per-seed
fragmentation numbers above -- not a pass or fail on them -- are the record
of what actually happened; see the design spec's 2026-08-26 section 7
amendment (`docs/superpowers/specs/2026-08-26-phase-2b2-reader-and-chain-
design.md`) for what they do and do not settle about the underlying claim.

Whether spec section 7's claim needs revising, whether more seeds would
settle it, or whether this fixture's noise regime is the wrong instrument for
this specific comparison, is not this file's call. It measures; it does not
rule.
"""

import os
from collections import Counter

import numpy as np
import pytest

from wl_preproc.contracts.events import TaskTypeCode
from wl_preproc.ephys.sorter_geometry import kilosort_spacing
from wl_preproc.synth.recipe import BlockSpec, MontageSpec, SPATIAL_RECIPE

pytest.importorskip("kilosort")
pytestmark = pytest.mark.slow


SEEDS = (20270317, 20270318, 20270319)

# A planted unit counts as "fragmented" once at least two output clusters each
# capture this share of its true spike train. Below that, a cluster is most
# likely a handful of coincidental-timing matches against a NEIGHBOURING
# unit's spikes -- every unit shows a few of these regardless of setting (see
# the fix-round report's per-unit table) -- rather than a second, real piece
# of the same unit. 0.20 sits in the middle of the range (0.10-0.25) where the
# default-fragments-more-than-derived direction holds; see the module
# docstring.
FRAGMENT_SHARE_THRESHOLD = 0.20

# How close, in samples at 30 kHz, a sorted spike must land to a planted one
# to count as the same event. 15 samples is 0.5 ms -- a quarter of
# `units.REFRACTORY_S` (2 ms), so it cannot itself span two of one unit's own
# spikes, while comfortably covering KS4's own detection/alignment jitter.
MATCH_TOLERANCE_SAMPLES = 15


def _long_recipe(seed):
    """SPATIAL_RECIPE's own probe/channel/unit-count geometry, widened to 60 s
    and re-seeded.

    SPATIAL_RECIPE itself is 6 s -- enough to exercise the emitters, not
    enough to sort. KS4 builds its templates from the data it is given, so at
    6 s it plausibly returns no units at all under EITHER setting. Measured
    directly: at 6 s, seed 20270317 produced the SAME 11 units under both
    settings, so the comparison this test makes had nothing to say at that
    duration.

    `.model_copy(update=...)` bypasses `SessionRecipe._coherent` entirely --
    pydantic does not re-validate a copy -- so its montage-coverage rule does
    NOT run against what this produces. Both fields are therefore widened
    together, by hand, and the assert below re-checks the one thing
    `_coherent` would have: 20 trials of 3.0 s is exactly 60.0 s, and the one
    montage must cover exactly that, for every seed this builds.

    Re-seeding changes which 12 unit positions get planted (see the module
    docstring for how many seeds this file checks and why), but not whether
    they straddle the columns: `place_units` draws x uniformly over the full
    [0, 103] span, and `TEMPLATE_SPATIAL_DECAY_UM` (60 um) is generous enough
    relative to the 103 um gap that amplitude on the FAR column never drops
    below ~100 uV for any placement, against this fixture's 8 uV noise floor
    -- checked directly for all three seeds via
    `wl_preproc.synth.waveforms.unit_templates`, not assumed: worst-case
    weakest-column peak amplitude was 107.1 uV (seed 20270317), 97.1 uV
    (20270318) and 128.1 uV (20270319).

    That is ONE criterion -- clears the noise floor on both columns -- and
    12/12 planted units meet it at every seed. It is not the criterion the
    mechanism needs. Fragmenting a unit needs comparable amplitude on both
    columns, not merely detectable amplitude: the design spec's section 7
    amendment measures a best-channel-peak-per-column ratio and finds only
    3, 4 and 4 of the 12 units per seed within its 1.5x parity bound -- the
    rest are detectable on the far column without being anywhere near equal
    on it, which the mechanism cannot fragment regardless of what the
    template grid allows. That narrower count, not the 12/12 noise-floor
    figure above, is what actually bounds this test's power; see section 7
    for the full accounting.
    """
    recipe = SPATIAL_RECIPE.model_copy(
        update={
            "seed": seed,
            "blocks": (
                BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=20, trial_duration_s=3.0),
            ),
            "montages": (MontageSpec(start_s=0.0, end_s=60.0),),
        }
    )
    assert recipe.duration_s == sum(m.end_s - m.start_s for m in recipe.montages), (
        f"seed {seed}: model_copy bypassed SessionRecipe._coherent's montage-coverage "
        "rule, so it is re-checked here by hand -- nothing else will catch a mismatch"
    )
    return recipe


def _sort(recording_dir, label, **settings):
    """`label` names the output folder explicitly. Deriving it from `settings`
    would collide here: both calls pass exactly two keyword arguments, so the
    second run would silently reuse -- or clobber -- the first one's folder.

    Returns the live `BaseSorting`, not just its unit ids: the fragmentation
    count needs each unit's own spike train (`get_unit_spike_train`), not
    only how many units KS4 produced.

    `torch_device="cpu"` is pinned rather than left at KS4's "auto" default.
    On this machine `torch.cuda.is_available()` is False, and
    spikeinterface's kilosort4 wrapper resolves "auto" to `"cuda" if
    torch.cuda.is_available() else "cpu"` -- it never inspects MPS, even
    though `torch.backends.mps.is_available()` is True here -- so "auto"
    already lands on CPU on this machine today. Pinning states that
    explicitly rather than leaving it to an implementation detail of a
    library this file does not own: MPS support in KS4's own torch code is
    unverified here, and the module docstring above already promises "KS4 on
    CPU". Confirmed by reading
    spikeinterface/sorters/external/kilosort4.py directly, not assumed.

    `OMP_NUM_THREADS` is pinned to 1 before KS4's own imports happen, for a
    reason that cost real wall-clock time to find: at SPATIAL_RECIPE's own
    6 s duration this is unnecessary and was not present during that smoke
    test, but at 60 s the first attempt at this test HUNG -- not slow,
    genuinely stuck: `ps` showed 0.01 CPU-seconds consumed across a clean,
    independently-timed 10 s window, and a `sample` stack trace caught every
    one of 2500+ samples inside `faiss::knn_L2sqr` under
    `__kmpc_fork_call` (OpenMP), never advancing. `kilosort/clustering_qr.py`
    imports both `torch` and `faiss` -- two separate OpenMP runtimes in one
    process is a known deadlock class on macOS, and it is exactly the kind of
    bug that only surfaces once a parallel region has enough real work to
    race on, which is why the short smoke test never hit it. Pinning the
    thread count to 1 removes the second runtime's multi-threading rather
    than papering over the conflict with `KMP_DUPLICATE_LIB_OK=TRUE`, and it
    is set here -- inside the function that is only called once the test
    itself is selected -- rather than at module level, so a normal `pytest
    -v` that merely COLLECTS this file (to read its marker) does not force
    every other, unrelated test in the same process onto one thread.
    """
    os.environ.setdefault("OMP_NUM_THREADS", "1")

    from spikeinterface.extractors import read_spikeglx
    from spikeinterface.sorters import run_sorter

    recording = read_spikeglx(recording_dir, stream_id="imec0.ap")
    return run_sorter(
        "kilosort4", recording, folder=recording_dir / f"ks_{label}",
        remove_existing_folder=True, do_CAR=False, torch_device="cpu", **settings,
    )


def _truth_samples_by_unit(truth, fs, pre_roll_s):
    """Ground-truth spike sample indices per unit, in the same integer sample
    frame the emitted `.bin` file -- and therefore KS4's own output -- uses:
    `waveforms.render_traces` (which `write_spikeglx` calls) places a spike at
    `int((time_s + pre_roll_s) * fs)`, truncating rather than rounding. This
    function's own `round()` below can therefore differ from the written
    sample by at most one, well inside `MATCH_TOLERANCE_SAMPLES`.
    """
    by_unit = {u.unit_id: [] for u in truth.units}
    for time_s, unit_id in truth.spikes:
        by_unit[unit_id].append(int(round((time_s + pre_roll_s) * fs)))
    return {uid: np.array(sorted(samples)) for uid, samples in by_unit.items()}


def _count_fragmented_units(sorting, truth_by_unit):
    """How many planted units (keys of `truth_by_unit`) have their spikes
    split across 2+ output clusters, each holding at least
    `FRAGMENT_SHARE_THRESHOLD` of that unit's true spike count.

    This is spec section 7's claim -- planted units get split -- measured
    directly against ground truth, rather than inferred from how many output
    clusters exist in total (see the module docstring for why that proxy
    does not work on this fixture: a large, roughly setting-independent
    population of clusters traces to no planted unit at all).
    """
    cluster_ids = sorting.get_unit_ids()
    if len(cluster_ids) == 0:
        return 0

    samples, clusters = [], []
    for cluster_id in cluster_ids:
        spike_samples = sorting.get_unit_spike_train(cluster_id)
        samples.append(spike_samples)
        clusters.append(np.full(len(spike_samples), cluster_id))
    samples = np.concatenate(samples)
    clusters = np.concatenate(clusters)
    order = np.argsort(samples)
    samples, clusters = samples[order], clusters[order]

    n_fragmented = 0
    for true_samples in truth_by_unit.values():
        idx = np.searchsorted(samples, true_samples)
        matched = []
        for true_sample, i in zip(true_samples, idx):
            for j in (i - 1, i, i + 1):
                if 0 <= j < len(samples) and abs(samples[j] - true_sample) <= MATCH_TOLERANCE_SAMPLES:
                    matched.append(clusters[j])
                    break
        if not matched:
            continue
        counts = Counter(matched)
        total = sum(counts.values())
        big_fragments = sum(1 for n in counts.values() if n / total >= FRAGMENT_SHARE_THRESHOLD)
        if big_fragments >= 2:
            n_fragmented += 1
    return n_fragmented


def test_planted_unit_fragmentation_under_default_and_derived_dminx(tmp_path):
    """Sorts each of `SEEDS` twice -- once at KS4's 32 um default, once at the
    NP1032-derived spacing -- and counts, via `_count_fragmented_units`, how
    many of the 12 planted units land fragmented across output clusters under
    each setting.

    This is a measurement, not a directional assertion (Task 8, 2026-08-26):
    the measured 3-seed aggregate is an exact tie (4 fragmented at default, 4
    derived -- see the module docstring's "How many seeds back this"), and
    asserting either direction would claim more than that tie supports. What
    is checked is that the machinery itself worked: all six sorts (3 seeds x
    2 settings) complete and produce well-formed, ground-truth-matchable
    output, with each seed's fragmentation count a sane integer in [0, 12].
    See the module docstring for the raw-count metric this replaced, the
    mechanism verified in KS4's own source, and the design spec's section 7
    amendment for what the tie does and does not settle.
    """
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S, write_spikeglx
    from wl_preproc.synth.timeline import build_timeline

    per_seed = []
    n_sorts_completed = 0

    for seed in SEEDS:
        recipe = _long_recipe(seed)
        truth = build_timeline(recipe)
        seed_dir = tmp_path / f"seed_{seed}"
        seed_dir.mkdir()
        write_spikeglx(seed_dir, recipe, truth)
        truth_by_unit = _truth_samples_by_unit(truth, recipe.ap_sample_rate_hz, SPIKEGLX_PRE_ROLL_S)

        derived_spacing = kilosort_spacing(recipe.probe_part_number)
        default_sorting = _sort(seed_dir, "default", dminx=32.0, max_channel_distance=32.0)
        n_sorts_completed += 1
        derived_sorting = _sort(seed_dir, "derived", **derived_spacing)
        n_sorts_completed += 1

        # Well-formed output: a content-free sanity check, not a directional
        # claim. Each sort must produce at least one cluster spikeinterface
        # can enumerate and fetch a spike train from -- the minimum
        # `_count_fragmented_units` needs to mean anything, independent of
        # which way the fragmentation count itself comes out.
        assert len(default_sorting.get_unit_ids()) > 0, f"seed {seed}: default sort produced no clusters"
        assert len(derived_sorting.get_unit_ids()) > 0, f"seed {seed}: derived sort produced no clusters"

        n_default = _count_fragmented_units(default_sorting, truth_by_unit)
        n_derived = _count_fragmented_units(derived_sorting, truth_by_unit)
        assert 0 <= n_default <= len(truth.units), (
            f"seed {seed}: default fragmentation count {n_default} outside [0, {len(truth.units)}]"
        )
        assert 0 <= n_derived <= len(truth.units), (
            f"seed {seed}: derived fragmentation count {n_derived} outside [0, {len(truth.units)}]"
        )
        per_seed.append((seed, n_default, n_derived))

    # Printed rather than only asserted on: this is the actual result, not a
    # pass/fail condition. `pytest -m slow -s` shows it; the module docstring
    # is where it is permanently recorded.
    print(
        f"fragmentation per seed (seed, default, derived): {per_seed}; summed: "
        f"{sum(n for _, n, _ in per_seed)} default, {sum(n for _, _, n in per_seed)} derived"
    )

    # Content-free: confirms the demonstration ran end-to-end (six real KS4
    # sorts, all well-formed), not a direction. The actual measurement -- a
    # tie -- lives in the printed line above, the module docstring, and the
    # design spec's section 7 amendment, not in this assertion.
    assert n_sorts_completed == 2 * len(SEEDS), (
        f"expected {2 * len(SEEDS)} sorts to complete (default + derived per seed); "
        f"got {n_sorts_completed}. per_seed so far: {per_seed}"
    )
    assert len(per_seed) == len(SEEDS)
