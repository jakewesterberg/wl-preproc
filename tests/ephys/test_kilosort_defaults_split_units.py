"""KS4 on CPU, minutes not seconds, and not part of the default run.

Marked `slow` and excluded from CI. It exists because spec section 7 rules that
the fixture should demonstrate the fault rather than warn about it, and a
demonstration that never runs is a warning with extra steps -- so run it by hand
whenever the probe, the geometry module or the KS4 version changes.
"""

import pytest

from wl_preproc.contracts.events import TaskTypeCode
from wl_preproc.ephys.sorter_geometry import kilosort_spacing
from wl_preproc.synth.recipe import BlockSpec, MontageSpec, SPATIAL_RECIPE

pytest.importorskip("kilosort")
pytestmark = pytest.mark.slow


# SPATIAL_RECIPE itself is 6 s -- enough to exercise the emitters, not enough
# to sort. KS4 builds its templates from the data it is given, so at 6 s it
# plausibly returns no units at all under EITHER setting, which would make
# `len(at_default) > len(at_derived)` fail for a reason that has nothing to do
# with the 32 um default: too little data, not the wrong spacing. Measured
# directly before this file was written: at 6 s both the default and the
# derived spacing produced the SAME 11 units from the 12 planted, so the
# comparison this test exists to make had nothing to say at that duration.
#
# `.model_copy(update=...)` bypasses `SessionRecipe._coherent` entirely --
# pydantic does not re-validate on a copy -- so its rule that montages must
# cover the session duration does NOT run against what this produces. Both
# fields are therefore widened together, by hand, and the module-level assert
# below checks the one thing `_coherent` would have: 20 trials of 3.0 s is
# exactly 60.0 s, and the montage covers exactly that.
_LONG_RECIPE = SPATIAL_RECIPE.model_copy(
    update={
        "blocks": (
            BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=20, trial_duration_s=3.0),
        ),
        "montages": (MontageSpec(start_s=0.0, end_s=60.0),),
    }
)
assert _LONG_RECIPE.duration_s == sum(m.end_s - m.start_s for m in _LONG_RECIPE.montages), (
    "model_copy bypassed SessionRecipe._coherent's montage-coverage rule, so it is "
    "re-checked here by hand -- nothing else will catch a mismatch"
)

# `probe_part_number`, `n_ap_channels`, `n_units` and `seed` are untouched by
# the widening above, and `build_timeline` draws unit positions from a fresh
# `default_rng(seed)` consumed by nothing before `place_units` (no stim events
# are configured here, so the block loop draws nothing from it) -- so which
# units this recipe plants, and where, does not depend on trial count at all.
# That was checked directly, not assumed: rendering this seed's own 12 units
# through `wl_preproc.synth.waveforms.unit_templates` shows every one of them
# peaks above 100 uV on BOTH NP1032 columns' nearest channel (the closest
# call, unit 9 at x=55.7 um -- nearly the 51.5 um midpoint -- peaks at 135.6
# uV on column 0's side and 148.9 uV on column 103's), against this fixture's
# 8 uV noise floor. The fault this test demonstrates needs at least one unit
# whose spikes genuinely register on both columns; at this seed, every planted
# unit qualifies, so nothing had to be forced.


def _sort(recording_dir, label, **settings):
    """`label` names the output folder explicitly. Deriving it from `settings`
    would collide here: both calls pass exactly two keyword arguments, so the
    second run would silently reuse -- or clobber -- the first one's folder.

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
    import os

    os.environ.setdefault("OMP_NUM_THREADS", "1")

    from spikeinterface.extractors import read_spikeglx
    from spikeinterface.sorters import run_sorter

    recording = read_spikeglx(recording_dir, stream_id="imec0.ap")
    sorting = run_sorter(
        "kilosort4", recording, folder=recording_dir / f"ks_{label}",
        remove_existing_folder=True, do_CAR=False, torch_device="cpu", **settings,
    )
    return sorting.get_unit_ids()


def test_the_default_dminx_splits_a_unit_that_straddles_the_columns(tmp_path):
    from wl_preproc.synth.spikeglx import write_spikeglx
    from wl_preproc.synth.timeline import build_timeline

    recipe = _LONG_RECIPE
    truth = build_timeline(recipe)
    write_spikeglx(tmp_path, recipe, truth)

    derived = kilosort_spacing(recipe.probe_part_number)
    at_default = _sort(tmp_path, "default", dminx=32.0, max_channel_distance=32.0)
    at_derived = _sort(tmp_path, "derived", **derived)

    planted = len(truth.units)
    assert len(at_default) > len(at_derived), (
        f"expected the 32 um default to over-split {planted} planted units; "
        f"got {len(at_default)} at the default and {len(at_derived)} derived"
    )
