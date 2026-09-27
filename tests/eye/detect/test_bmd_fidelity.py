"""BMD against its authors' own code (design spec `2026-09-27-bmd-design.md`
section 5.1): the same settings at every iteration and the same change points
in every sample, printed as the reference prints them. Exact, never within a
tolerance.

Nulls first. Two deliberately broken implementations must fail the check:
- chains seeded with the run seed itself, not with a draw from it;
- the speed settings started from the run's generator, not from the
  `params` object's own unseeded one.

Gated on `WLPP_BMD_REFERENCE`, which names a checkout of
`github.com/basvanopheusden/BMD` at `1fab635`. The comparisons against a
build need clang, libc++ and the Boost headers too. CI provides all of them
(spec section 7)."""

from __future__ import annotations

import numpy as np
import pytest

from tests.eye.detect import _bmd_reference as ref
from tests.eye.detect._bmd_traces import PAPER_1KHZ, simulate
from wl_preproc.eye.detect import bmd, bmd_rng

#: The authors' stored example's seed (`output1.txt`).
EXAMPLE_SEED = 1473448196


def _example():
    return ref.read_trace(ref.checkout() / "x1.txt")


def _ours(x, *, seed, fs_hz=1000.0, drift_cap=np.inf, microsaccade_cap=np.inf):
    result = bmd.run_block([x], drift_rate=4.0 / fs_hz, microsaccade_rate=100.0 / fs_hz,
                           drift_cap=drift_cap, microsaccade_cap=microsaccade_cap, seed=seed,
                           table=ref.authors_table(), record=True)
    return ref.formatted(result.record)


def _stored():
    root = ref.checkout()
    return ((root / "params1.txt").read_text().splitlines(),
            (root / "changepoints1.txt").read_text().splitlines())


def test_the_null_seeding_chains_with_the_run_seed_fails_the_check(monkeypatch):
    def wrong(seed, count):
        return [bmd_rng.new_generator(seed) for _ in range(count)]

    monkeypatch.setattr(bmd, "_chain_generators", wrong)
    assert _ours(_example(), seed=EXAMPLE_SEED) != _stored()


def test_the_null_starting_speeds_from_the_run_generator_fails_the_check(monkeypatch):
    def wrong():
        gen = bmd_rng.new_generator(EXAMPLE_SEED)
        return (bmd_rng.uniform_real(gen, 1.1, 5.0), bmd_rng.uniform_real(gen, 0.0001, 0.005),
                bmd_rng.uniform_real(gen, 0.005, 0.1))

    monkeypatch.setattr(bmd, "_first_speeds", wrong)
    assert _ours(_example(), seed=EXAMPLE_SEED) != _stored()


def test_the_authors_example_is_reproduced_exactly():
    """No build: the authors' own stored `params1.txt` and
    `changepoints1.txt`, all six iterations and all 240 samples."""
    assert _ours(_example(), seed=EXAMPLE_SEED) == _stored()


def test_the_patched_reference_still_reproduces_the_authors_example(tmp_path):
    """The build's patches (`_bmd_reference.py`) change nothing the example
    reads, so the comparisons below are against the authors' algorithm."""
    exe = ref.binary(lambda0=0.004, lambda1=0.1)
    assert ref.run(exe, _example(), EXAMPLE_SEED, tmp_path) == _stored()


@pytest.mark.parametrize("seed", [11, 12])
def test_simulated_data_at_1khz_matches_the_reference(seed, tmp_path):
    x, _ = simulate(12_000, sigmaz=0.002, sigmax=0.01, seed=seed, **PAPER_1KHZ)
    exe = ref.binary(lambda0=0.004, lambda1=0.1)
    assert _ours(x, seed=seed) == ref.run(exe, x, seed, tmp_path)


@pytest.mark.parametrize("seed", [21, 22])
def test_simulated_data_at_500hz_with_the_caps_matches_the_reference(seed, tmp_path):
    """The rig's adaptations (spec 3.3): per-sample rates at 500 Hz, and the
    README's caps converted to per-sample units."""
    fs = 500.0
    x, _ = simulate(8_000, lambda0=4.0 / fs, lambda1=100.0 / fs, sigma0=0.3 / fs, sigma1=30.0 / fs,
                    d1=4.4, sigmaz=0.004, sigmax=0.02, seed=seed)
    exe = ref.binary(lambda0=4.0 / fs, lambda1=100.0 / fs, sigma0_cap=1.3 / fs, sigma1_cap=100.0 / fs)
    ours = _ours(x, seed=seed, fs_hz=fs, drift_cap=1.3 / fs, microsaccade_cap=100.0 / fs)
    assert ours == ref.run(exe, x, seed, tmp_path)


def test_exactly_repeated_positions_match_the_reference(tmp_path):
    """A tracker that repeats one position for many frames drives the
    smoothed position to an exact fixed point. A run inside it then has
    |dz|^2 = 0, and the likelihood divides by zero. The reference carries on
    with IEEE infinities and NaNs; so must the port. It is compiled with
    numba's numpy error model, never Python's, which would raise (found on
    the reference recording's right eye, 2026-09-27)."""
    x, _ = simulate(6_000, sigmaz=0.002, sigmax=0.01, seed=13, **PAPER_1KHZ)
    x[2_000:3_500] = x[2_000]
    exe = ref.binary(lambda0=0.004, lambda1=0.1)
    assert _ours(x, seed=13) == ref.run(exe, x, 13, tmp_path)


def test_a_perfectly_still_stretch_matches_the_reference(tmp_path):
    """A still stretch, as `detect_bmd` prepares one at 500 Hz with the
    caps, drives the motor noise to exactly 0 at its first re-estimate. The
    reference takes C's log(0) = -inf and carries on; the port
    must too, where Python's `math.log` raises (final review C1,
    2026-09-27)."""
    fs = 500.0
    x = np.full((1_000, 2), bmd.ORIGIN_EPSILON_DEG)
    seed = bmd.DEFAULT_BMD_PARAMS.seed
    exe = ref.binary(lambda0=4.0 / fs, lambda1=100.0 / fs, sigma0_cap=1.3 / fs, sigma1_cap=100.0 / fs)
    ours = _ours(x, seed=seed, fs_hz=fs, drift_cap=1.3 / fs, microsaccade_cap=100.0 / fs)
    assert ours == ref.run(exe, x, seed, tmp_path)
