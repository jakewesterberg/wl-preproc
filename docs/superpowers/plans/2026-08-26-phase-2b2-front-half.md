# Phase 2b-2 Front Half — The Generator Gains Spatial Reality

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the synthetic session generator the spatial structure it has never had — real probe geometry, units with locations, multi-channel spike footprints, spatially correlated noise and an LF band — so that the preprocessing questions Phase 2b asks can be measured rather than argued.

**Architecture:** Ground truth inverts: a unit has a position, and the channels it appears on are a consequence of that position rather than an input. Waveform and noise physics are borrowed from `spikeinterface.generation`; the file format and the ground truth both stay ours, so the pipeline never reads with the same library that wrote. Probe geometry stops being fabricated in a format string and comes from `wl_preproc.ephys.geometry` instead.

**Tech Stack:** Python 3.11, NumPy, `spikeinterface` 0.104.8 (`generation` and `extractors`), `probeinterface` (offline table, via `ephys.geometry`), `pydantic` v2, pytest. `kilosort` 4.1.7 for Task 7 only.

**Spec:** `docs/superpowers/specs/2026-08-26-phase-2b2-reader-and-chain-design.md`

**Scope:** This is the **front half only** — §3 of the spec. The back half (reader seam §4, chain §5, Kilosort seam §6, paramsets §8) is gated on 2b-1, which the decomposition forces before every processing stage and which is hardware-blocked. It gets its own plan when the compute machine lands.

## Global Constraints

- **Python `>=3.11`.** CI tests 3.11 and 3.13 (`wl.yaml`); nothing may require a newer floor.
- **`spikeinterface >=0.101`**, installed 0.104.8. It is a `dev` dependency today and stays one for this plan — the front half is test infrastructure. Making it a *runtime* dependency belongs to the back half's reader seam.
- **The file format stays ours** (spec §3.3). Our writer emits the bytes; `read_spikeglx` is the independent oracle. Never write a fixture through a SpikeInterface writer.
- **Ground truth stays ours** (spec §3.3). Unit ids, spike times and unit locations are planted by `synth/timeline.py` and never read back out of SpikeInterface. `synth/truth.py`: *"A test that recomputes expectations from generator internals tests nothing — it agrees with itself."*
- **No network in tests.** Offline and reproducible; `fetch_template_object_from_database` is out of scope for this plan.
- **Deterministic.** Every random draw derives from `recipe.seed`. Two generations of one recipe must be byte-identical.
- **Run tests with** `.venv/bin/python -m pytest` — the venv is `uv`-managed and has no `pip`; install with `VIRTUAL_ENV=.venv uv pip install <pkg>`.

---

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/synth/units.py` *(create)* | **Ours.** Unit identity, placement along the probe, and spike-train synthesis. Knows nothing about waveforms. |
| `wl_preproc/synth/waveforms.py` *(create)* | **Borrowed physics.** Templates from unit locations + channel geometry, spatially correlated noise, and rendering both into a trace matrix. |
| `wl_preproc/synth/truth.py` *(modify)* | `UnitTruth`; `GroundTruth.units`; `spikes` becomes `(time_s, unit_id)`. |
| `wl_preproc/synth/timeline.py` *(modify)* | Plants units and per-unit spike trains instead of random `(time, channel)` pairs. |
| `wl_preproc/synth/recipe.py` *(modify)* | `probe_part_number`, `n_units`, and a recipe with enough channels to have spatial structure. |
| `wl_preproc/synth/spikeglx.py` *(modify)* | Geometry from the real table; traces from `waveforms.py`; emits an LF stream beside the AP stream. |
| `wl_preproc/synth/rhs.py` *(modify)* | Same treatment on a declared linear geometry, so `truth.spikes` means one thing regardless of emitter. |

The split that matters is `units.py` versus `waveforms.py`: **what fires, when and where** is ours and is ground truth; **what that looks like in the data** is borrowed and is not. Keeping them in separate files is what makes the containment auditable rather than a claim.

---

## Task 1: The probe becomes real

The fixture fabricates its geometry in a format string, and the geometry it fabricates is wrong for the probe it names. `spikeglx.py:_meta_text` builds `~snsGeomMap` as `(0:{16 if c % 2 else 48}:{20 * (c // 2)}:1)` — x alternating between 16 and 48 only — while `electrode_rows("NP1000")` gives electrodes 0–3 at `(16, 0)`, `(48, 0)`, **`(0, 20)`, `(32, 20)`**: four distinct x values on a four-channel period, not two on a two-channel period.

Nothing downstream can express NP1032 at all, which is why spec §7's column-splitting fault cannot currently be reproduced.

**Files:**
- Modify: `wl_preproc/synth/recipe.py` (add `probe_part_number` to `SessionRecipe`)
- Modify: `wl_preproc/synth/spikeglx.py:84-129` (`_meta_text`)
- Test: `tests/synth/test_spikeglx.py`

**Interfaces:**
- Consumes: `wl_preproc.ephys.geometry.electrode_rows(part_number) -> list[dict]`, each dict with keys `electrode`, `shank`, `shank_col`, `shank_row`, `x_coord`, `y_coord`.
- Produces: `SessionRecipe.probe_part_number: str` (default `"NP1000"`), read by `_meta_text` and by Task 4's `waveforms.unit_templates`.

- [ ] **Step 1: Write the failing test**

In `tests/synth/test_spikeglx.py`:

```python
from wl_preproc.ephys.geometry import electrode_rows


def test_geom_map_comes_from_the_probe_table_not_a_format_string(tmp_path):
    """The fabricated map alternated x between 16 and 48. Real NP1000
    electrodes 0-3 sit at (16,0), (48,0), (0,20), (32,20) -- four x values on a
    four-channel period. A geometry the probe does not have is a fixture that
    describes a probe nobody owns."""
    recipe = CI_RECIPE.model_copy(update={"n_ap_channels": 4})
    truth = build_timeline(recipe)
    bin_path = write_spikeglx(tmp_path, recipe, truth)

    meta = bin_path.with_suffix(".meta").read_text()
    geom_line = next(l for l in meta.splitlines() if l.startswith("~snsGeomMap="))
    entries = geom_line.split("=", 1)[1]

    expected = electrode_rows(recipe.probe_part_number)[:4]
    for row in expected:
        assert f"(0:{row['x_coord']:g}:{row['y_coord']:g}:1)" in entries


def test_a_recipe_can_declare_the_nhp_probe(tmp_path):
    """NP1032 is the 4,416-site NHP Long probe with columns 103 um apart. The
    fixture could not express it at all before this."""
    recipe = CI_RECIPE.model_copy(
        update={"probe_part_number": "NP1032", "n_ap_channels": 4}
    )
    truth = build_timeline(recipe)
    bin_path = write_spikeglx(tmp_path, recipe, truth)

    meta = bin_path.with_suffix(".meta").read_text()
    assert "imDatPrb_pn=NP1032" in meta
    assert "(0:0:0:1)" in meta and "(0:103:0:1)" in meta
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/synth/test_spikeglx.py -k "geom_map_comes_from or declare_the_nhp" -v`

Expected: both FAIL — the first on the fabricated coordinates not matching, the second with `pydantic_core.ValidationError` for the unknown field `probe_part_number`.

- [ ] **Step 3: Add the field**

In `wl_preproc/synth/recipe.py`, inside `SessionRecipe`, after `n_ap_channels`:

```python
    # Which probe this session was recorded through, as a probeinterface part
    # number. Defaulted to NP1000 (Neuropixels 1.0) because every recipe
    # predating Phase 2b-2 described one implicitly -- `_meta_text` hardcoded
    # `imDatPrb_pn=NP1000`. It is a field rather than a constant because this
    # lab's probe is NP1032, whose columns sit 103 um apart, and spec section 7
    # turns on a fixture being able to say so.
    probe_part_number: str = "NP1000"
```

And in `_coherent`, before `return self`:

```python
        try:
            available = len(electrode_rows(self.probe_part_number))
        except UnknownProbeType as exc:
            raise ValueError(str(exc)) from exc
        if self.n_ap_channels > available:
            raise ValueError(
                f"n_ap_channels is {self.n_ap_channels} but "
                f"{self.probe_part_number} has {available} sites; a recording "
                "cannot have more channels than the probe has electrodes"
            )
```

with the import at the top of `recipe.py`:

```python
from wl_preproc.ephys.geometry import UnknownProbeType, electrode_rows
```

- [ ] **Step 4: Derive the geometry**

In `wl_preproc/synth/spikeglx.py`, replace the `imro`, `chan_map` and `geom` construction in `_meta_text` with:

```python
    n_ap = recipe.n_ap_channels
    file_bytes = n_samples * n_channels * 2
    sites = electrode_rows(recipe.probe_part_number)[:n_ap]

    imro = f"({n_ap},{n_ap})" + "".join(
        f"({c} 0 0 {int(AP_GAIN)} 250 1)" for c in range(n_ap)
    )
    chan_map = (
        f"({n_ap},0,1)"
        + "".join(f"(AP{c};{c}:{c})" for c in range(n_ap))
        + f"(SY0;{n_ap}:{n_ap})"
    )
    # Read from probeinterface's offline table rather than fabricated. The
    # fabricated version alternated x between 16 and 48, which is not the
    # layout of any probe -- NP1000's first four sites are (16,0), (48,0),
    # (0,20), (32,20). Nothing caught it because nothing read the map back.
    geom = f"({recipe.probe_part_number},1,0,70)" + "".join(
        f"({s['shank']}:{s['x_coord']:g}:{s['y_coord']:g}:1)" for s in sites
    )
```

and change the `imDatPrb_pn` line to:

```python
        f"imDatPrb_pn={recipe.probe_part_number}",
```

Add the import:

```python
from wl_preproc.ephys.geometry import electrode_rows
```

- [ ] **Step 5: Run the whole synth and ingest suites**

Run: `.venv/bin/python -m pytest tests/synth tests/ingest tests/timebase -q`

Expected: PASS. If a test asserts the old fabricated coordinates, it was asserting a wrong geometry — update it to read from `electrode_rows` and say so in the test's docstring.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/synth/recipe.py wl_preproc/synth/spikeglx.py tests/synth/test_spikeglx.py
git commit -m "synth: geometry comes from the probe table, and a recipe can name NP1032"
```

---

## Task 2: Ground truth gains units

`GroundTruth.spikes` is `(time_s, channel)`, which makes the channel an input. A unit has a position; the channels it appears on must be a consequence of it.

**Files:**
- Create: `wl_preproc/synth/units.py`
- Modify: `wl_preproc/synth/truth.py:37`
- Modify: `wl_preproc/synth/timeline.py:18,177-180`
- Modify: `wl_preproc/synth/spikeglx.py:143`, `wl_preproc/synth/rhs.py:110`
- Test: `tests/synth/test_units.py` *(create)*, `tests/synth/test_spikeglx.py:120`, `tests/synth/test_rhs.py:174`

**Interfaces:**
- Produces:
  - `UnitTruth` — frozen dataclass, fields `unit_id: int`, `x_um: float`, `y_um: float`, `z_um: float`, `firing_rate_hz: float`.
  - `GroundTruth.units: tuple[UnitTruth, ...]`
  - `GroundTruth.spikes: tuple[tuple[float, int], ...]` — now `(session-time seconds, unit_id)`.
  - `units.place_units(sites, n_units, rng) -> tuple[UnitTruth, ...]`
  - `units.spike_train(unit, duration_s, rng) -> list[float]`
- Consumes: Task 1's `recipe.probe_part_number`.

- [ ] **Step 1: Write the failing test**

Create `tests/synth/test_units.py`:

```python
import numpy as np
import pytest

from wl_preproc.ephys.geometry import electrode_rows
from wl_preproc.synth.units import REFRACTORY_S, place_units, spike_train


def test_units_sit_within_the_span_of_the_recorded_sites():
    """A unit outside the recorded span contributes nothing to any channel, so
    it would be ground truth nothing could recover -- worse than absent,
    because a recall metric would score it as a miss."""
    sites = electrode_rows("NP1032")[:64]
    units = place_units(sites, n_units=8, rng=np.random.default_rng(0))

    ys = [s["y_coord"] for s in sites]
    assert len(units) == 8
    assert {u.unit_id for u in units} == set(range(8))
    for unit in units:
        assert min(ys) <= unit.y_um <= max(ys)


def test_two_units_never_share_an_identity():
    sites = electrode_rows("NP1032")[:64]
    units = place_units(sites, n_units=12, rng=np.random.default_rng(1))
    assert len({u.unit_id for u in units}) == 12


def test_a_spike_train_honours_a_refractory_period():
    """A Poisson train without one produces inter-spike intervals no neuron can
    make, and ISI-violation metrics computed against it are meaningless -- the
    fixture would be scoring the pipeline against impossible data."""
    sites = electrode_rows("NP1032")[:64]
    unit = place_units(sites, n_units=1, rng=np.random.default_rng(2))[0]
    times = spike_train(unit, duration_s=30.0, rng=np.random.default_rng(3))

    assert len(times) > 10
    assert np.all(np.diff(times) >= REFRACTORY_S)


def test_a_spike_train_is_reproducible_from_its_seed():
    sites = electrode_rows("NP1032")[:64]
    unit = place_units(sites, n_units=1, rng=np.random.default_rng(4))[0]
    first = spike_train(unit, duration_s=10.0, rng=np.random.default_rng(5))
    second = spike_train(unit, duration_s=10.0, rng=np.random.default_rng(5))
    assert first == second
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/synth/test_units.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.synth.units'`.

- [ ] **Step 3: Implement `units.py`**

Create `wl_preproc/synth/units.py`:

```python
"""What fires, when, and where it sits.

**This is ground truth and it is ours.** Nothing here consults SpikeInterface:
a unit's identity, its position and its spike times are planted by this module
and by `timeline.py`, so a test asserting recovery crosses a real boundary.
`waveforms.py` holds the borrowed half -- what a spike planted here looks like
once it reaches a channel -- and the two are separate files so the containment
is auditable rather than asserted. See `truth.py`'s own rule.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Absolute refractory period. A Poisson train without one produces intervals no
# neuron can make, and any ISI-violation metric computed against such a fixture
# measures the generator rather than the pipeline.
REFRACTORY_S = 0.002

# Firing rates are drawn from this range rather than fixed, so unit yield is not
# an artifact of every unit being equally easy to find.
_RATE_RANGE_HZ = (2.0, 20.0)

# How far a unit may sit from the plane of the shank. Zero would place every
# unit exactly on the probe, which makes amplitude a function of depth alone and
# removes the one axis a sorter uses to separate units at the same depth.
_MAX_DISTANCE_UM = 60.0


@dataclass(frozen=True, slots=True)
class UnitTruth:
    """One planted neuron. Position is in the probe's own coordinate frame --
    the same frame `ephys.geometry.electrode_rows` reports."""

    unit_id: int
    x_um: float
    y_um: float
    z_um: float
    firing_rate_hz: float


def place_units(sites: list[dict], n_units: int, rng: np.random.Generator) -> tuple[UnitTruth, ...]:
    """`n_units` neurons distributed across the span of the RECORDED sites.

    Bounded by the sites rather than by the probe: a unit outside the recorded
    span contributes nothing to any channel, so it would be ground truth that
    nothing could recover -- which scores as a miss and blames the pipeline for
    the fixture's choice.
    """
    xs = [s["x_coord"] for s in sites]
    ys = [s["y_coord"] for s in sites]
    return tuple(
        UnitTruth(
            unit_id=index,
            x_um=float(rng.uniform(min(xs), max(xs))),
            y_um=float(rng.uniform(min(ys), max(ys))),
            z_um=float(rng.uniform(-_MAX_DISTANCE_UM, _MAX_DISTANCE_UM)),
            firing_rate_hz=float(rng.uniform(*_RATE_RANGE_HZ)),
        )
        for index in range(n_units)
    )


def spike_train(unit: UnitTruth, duration_s: float, rng: np.random.Generator) -> list[float]:
    """Spike times for one unit, in session-time seconds.

    Exponential intervals with the refractory period added rather than rejected:
    rejection changes the effective rate by an amount that depends on the rate,
    so a 20 Hz unit and a 2 Hz unit would end up further apart than declared.
    Adding it shifts every interval by the same constant, which is what a real
    absolute refractory period does.
    """
    times: list[float] = []
    cursor = 0.0
    while True:
        cursor += REFRACTORY_S + float(rng.exponential(1.0 / unit.firing_rate_hz))
        if cursor >= duration_s:
            return times
        times.append(cursor)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/synth/test_units.py -v`

Expected: PASS.

- [ ] **Step 5: Rewire ground truth**

In `wl_preproc/synth/truth.py`, add the import `from wl_preproc.synth.units import UnitTruth`, and change `GroundTruth`:

```python
    units: tuple[UnitTruth, ...]
    spikes: tuple[tuple[float, int], ...]        # (session-time seconds, UNIT id)
```

> Note for the implementer: `spikes` changes meaning, not shape — it was `(time_s, channel)`. That is why every consumer below must be visited rather than left to type-check clean.

In `wl_preproc/synth/timeline.py`, replace lines 177-180 with:

```python
    sites = electrode_rows(recipe.probe_part_number)[: recipe.n_ap_channels]
    units = place_units(sites, recipe.n_units, rng)
    spikes = tuple(
        (time_s, unit.unit_id)
        for unit in units
        for time_s in spike_train(unit, recipe.duration_s, rng)
    )
    spikes = tuple(sorted(spikes))
```

and add `units=units,` to the `GroundTruth(...)` construction. Delete the now-unused `SPIKE_RATE_HZ` constant at line 18. Add the imports:

```python
from wl_preproc.ephys.geometry import electrode_rows
from wl_preproc.synth.units import place_units, spike_train
```

In `wl_preproc/synth/recipe.py`, add to `SessionRecipe` beside `probe_part_number`:

```python
    # How many neurons this session contains. Zero is legal and is what every
    # timing-only fixture wants: Phase 1c's recipes care about barcodes and
    # event codes, and paying for template rendering to validate a barcode is
    # waste. The spatial recipes below set it deliberately.
    n_units: int = Field(default=0, ge=0)
```

- [ ] **Step 6: Bridge the two emitters, deliberately and visibly**

The emitters still render one channel per spike. That is replaced in Task 4; here they only stop reading a field that has changed meaning.

In `wl_preproc/synth/spikeglx.py`, replace the loop at line 143:

```python
    # INTERIM, replaced in Task 4: each unit renders on the single site nearest
    # to it. That is a real position-derived channel rather than the random one
    # this replaced, but it is still not a footprint -- a spike lands on exactly
    # one channel, so nothing spatial can be measured yet.
    sites = electrode_rows(recipe.probe_part_number)[: recipe.n_ap_channels]
    nearest = {
        unit.unit_id: int(
            np.argmin(
                [
                    (s["x_coord"] - unit.x_um) ** 2 + (s["y_coord"] - unit.y_um) ** 2
                    for s in sites
                ]
            )
        )
        for unit in truth.units
    }
    template = SPIKE_TEMPLATE_UV / UV_PER_BIT
    for time_s, unit_id in truth.spikes:
        start = int((apply_drift(time_s, drift_ppm) + SPIKEGLX_PRE_ROLL_S) * fs)
        stop = start + template.size
        if stop < n_samples:
            data[start:stop, nearest[unit_id]] += template
```

In `wl_preproc/synth/rhs.py`, replace the loop at line 110:

```python
    # INTERIM, replaced in Task 6. A modulo rather than a nearest-site lookup
    # because an Intan headstage has no probe part number and so no geometry to
    # be nearest to -- Task 6 declares one. The modulo is deliberately obvious
    # about being a placeholder.
    template = SPIKE_TEMPLATE_UV / UV_PER_BIT
    for time_s, unit_id in truth.spikes:
        start = int((apply_drift(time_s, drift_ppm) + RHS_PRE_ROLL_S) * fs)
        stop = start + template.size
        if stop < n_samples:
            amplifier[start:stop, unit_id % n_channels] += template
```

Update `tests/synth/test_spikeglx.py:120` and `tests/synth/test_rhs.py:174-175` to unpack `(time_s, unit_id)` and assert `unit_id` is a member of `{u.unit_id for u in truth.units}`.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q`

Expected: PASS, 781+ tests. Every timing fixture has `n_units=0` and so plants no spikes at all — confirm no timing test depended on spikes existing.

- [ ] **Step 8: Commit**

```bash
git add wl_preproc/synth/units.py wl_preproc/synth/truth.py wl_preproc/synth/timeline.py \
        wl_preproc/synth/recipe.py wl_preproc/synth/spikeglx.py wl_preproc/synth/rhs.py \
        tests/synth/test_units.py tests/synth/test_spikeglx.py tests/synth/test_rhs.py
git commit -m "synth: ground truth carries units, and a spike names one"
```

---

## Task 3: Noise gains spatial structure

`spikeglx.py:140` draws noise independently per channel. With spatially uncorrelated noise a common reference has nothing common to remove, so every reference mode in spec §5.2 would measure as pure harm.

**Files:**
- Create: `wl_preproc/synth/waveforms.py`
- Test: `tests/synth/test_waveforms.py` *(create)*

**Interfaces:**
- Produces: `waveforms.correlated_noise(sites, n_samples, sampling_rate_hz, noise_uv, seed) -> np.ndarray` of shape `(n_samples, len(sites))`, float64, in µV.

- [ ] **Step 1: Write the failing test**

Create `tests/synth/test_waveforms.py`:

```python
import numpy as np

from wl_preproc.ephys.geometry import electrode_rows
from wl_preproc.synth.waveforms import correlated_noise


def test_neighbouring_channels_share_more_noise_than_distant_ones():
    """The property the whole reference question turns on. With independent
    noise every reference mode measures as pure harm, because there is nothing
    common to remove."""
    sites = electrode_rows("NP1032")[:64]
    noise = correlated_noise(sites, n_samples=30_000, sampling_rate_hz=30_000.0,
                             noise_uv=8.0, seed=0)

    corr = np.corrcoef(noise.T)
    near = np.mean([corr[i, i + 2] for i in range(0, 60, 2)])
    far = np.mean([corr[i, i + 40] for i in range(0, 20, 2)])
    assert near > far + 0.1


def test_a_common_median_reference_removes_variance_it_could_not_if_independent():
    sites = electrode_rows("NP1032")[:64]
    noise = correlated_noise(sites, n_samples=30_000, sampling_rate_hz=30_000.0,
                             noise_uv=8.0, seed=1)

    referenced = noise - np.median(noise, axis=1, keepdims=True)
    assert referenced.var() < 0.9 * noise.var()


def test_noise_is_reproducible_from_its_seed():
    sites = electrode_rows("NP1032")[:16]
    kwargs = dict(sites=sites, n_samples=3000, sampling_rate_hz=30_000.0, noise_uv=8.0, seed=7)
    assert np.array_equal(correlated_noise(**kwargs), correlated_noise(**kwargs))
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/synth/test_waveforms.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.synth.waveforms'`.

- [ ] **Step 3: Implement**

Create `wl_preproc/synth/waveforms.py`:

```python
"""What a planted unit looks like once it reaches a channel.

**This is the borrowed half, and the boundary is deliberate.** `units.py` holds
ground truth -- who fires and when -- and consults nothing. This module calls
`spikeinterface.generation` for waveform and noise physics we have no business
inventing. The two are separate files so that the containment spec section 3.3
requires is auditable: if this module ever plants ground truth, or `units.py`
ever imports SpikeInterface, the separation has been lost.

The format is never borrowed. `spikeglx.py` writes the bytes and
`read_spikeglx` reads them back as an independent oracle, so a bug in
SpikeInterface's model of a Neuropixels probe still fails loudly.
"""

from __future__ import annotations

import numpy as np
from probeinterface import Probe

from wl_preproc.synth.units import UnitTruth

# How fast noise decorrelates with distance, in microns. Passed to
# `generate_noise`, which builds a covariance matrix from it. Without a value
# here the noise is independent per channel and no reference mode can be
# measured at all -- which is the state this module exists to end.
NOISE_SPATIAL_DECAY_UM = 30.0


def _probe_from_sites(sites: list[dict]) -> Probe:
    """A probeinterface `Probe` over exactly the recorded sites.

    Built from our own geometry rows rather than fetched, so the probe the
    generator plants against is the same object the .meta will describe.
    """
    positions = np.array([[s["x_coord"], s["y_coord"]] for s in sites], dtype=float)
    probe = Probe(ndim=2, si_units="um")
    probe.set_contacts(positions=positions, shapes="square", shape_params={"width": 12.0})
    probe.set_device_channel_indices(np.arange(len(sites)))
    return probe


def correlated_noise(
    sites: list[dict],
    n_samples: int,
    sampling_rate_hz: float,
    noise_uv: float,
    seed: int,
) -> np.ndarray:
    """Background noise in µV, shape `(n_samples, len(sites))`, correlated
    across space."""
    from spikeinterface.generation import generate_noise

    recording = generate_noise(
        probe=_probe_from_sites(sites),
        sampling_frequency=sampling_rate_hz,
        durations=[n_samples / sampling_rate_hz],
        noise_levels=noise_uv,
        spatial_decay=NOISE_SPATIAL_DECAY_UM,
        seed=seed,
    )
    traces = recording.get_traces(start_frame=0, end_frame=n_samples, return_scaled=False)
    return np.asarray(traces, dtype=np.float64)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/synth/test_waveforms.py -v`

Expected: PASS. If `generate_noise` returns fewer than `n_samples` frames, the duration rounded down — request `(n_samples + 1) / sampling_rate_hz` and slice.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/waveforms.py tests/synth/test_waveforms.py
git commit -m "synth: noise that is correlated across space, so a reference has something to remove"
```

---

## Task 4: Multi-channel footprints

**Files:**
- Modify: `wl_preproc/synth/waveforms.py`
- Modify: `wl_preproc/synth/spikeglx.py` (replace Task 2's interim loop and the noise draw)
- Test: `tests/synth/test_waveforms.py`, `tests/synth/test_spikeglx.py`

**Interfaces:**
- Produces:
  - `waveforms.unit_templates(sites, units, sampling_rate_hz, seed) -> np.ndarray` of shape `(n_units, n_template_samples, n_channels)`, µV.
  - `waveforms.render_traces(sites, units, spikes, n_samples, sampling_rate_hz, noise_uv, seed, time_offset_s, drift_ppm) -> np.ndarray` of shape `(n_samples, n_channels)`, µV.
  - `waveforms.TEMPLATE_MS_BEFORE = 1.0`, `TEMPLATE_MS_AFTER = 2.0`.

- [ ] **Step 1: Write the failing test**

Append to `tests/synth/test_waveforms.py`:

```python
from wl_preproc.synth.units import place_units
from wl_preproc.synth.waveforms import render_traces, unit_templates


def test_a_unit_appears_on_several_channels_with_amplitude_falling_off():
    """The property that makes sorting possible at all, and the one the fixture
    has never had: a spike's spatial footprint."""
    sites = electrode_rows("NP1032")[:64]
    units = place_units(sites, n_units=1, rng=np.random.default_rng(0))
    templates = unit_templates(sites, units, sampling_rate_hz=30_000.0, seed=0)

    peak_per_channel = np.abs(templates[0]).max(axis=0)
    loud = np.flatnonzero(peak_per_channel > 0.2 * peak_per_channel.max())
    assert len(loud) > 1, "a single-channel template is what this task removes"

    unit = units[0]
    distance = np.array(
        [np.hypot(s["x_coord"] - unit.x_um, s["y_coord"] - unit.y_um) for s in sites]
    )
    assert distance[int(np.argmax(peak_per_channel))] == distance.min()


def test_rendered_traces_carry_the_planted_spikes_above_the_noise():
    sites = electrode_rows("NP1032")[:64]
    units = place_units(sites, n_units=2, rng=np.random.default_rng(1))
    spikes = ((0.100, 0), (0.200, 1))
    traces = render_traces(
        sites, units, spikes, n_samples=30_000, sampling_rate_hz=30_000.0,
        noise_uv=8.0, seed=2, time_offset_s=0.0, drift_ppm=0.0,
    )

    assert traces.shape == (30_000, 64)
    at_spike = np.abs(traces[2_900:3_100]).max()
    quiet = np.abs(traces[20_000:20_200]).max()
    assert at_spike > 2 * quiet
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/synth/test_waveforms.py -k "several_channels or above_the_noise" -v`

Expected: FAIL — `ImportError: cannot import name 'unit_templates'`.

- [ ] **Step 3: Implement**

Append to `wl_preproc/synth/waveforms.py`:

```python
TEMPLATE_MS_BEFORE = 1.0
TEMPLATE_MS_AFTER = 2.0

# **A recorded bias, not an oversight.** `generate_templates` is documented as
# "very naive: it generates a mono channel waveform ... and duplicates this same
# waveform on all channel given a simple decay law per unit" -- amplitude decays
# with distance, waveform SHAPE does not. Interpolating a dead channel is
# spatial averaging of its neighbours, so against these templates interpolation
# reconstructs a dead site almost perfectly. Every interpolation result measured
# on this fixture is therefore an UPPER BOUND, and spec section 3.4 requires
# that caveat to travel with any such number. Real templates
# (`fetch_template_object_from_database`) would remove the bias and need the
# network, which CI and offline reproducibility both rule out here.


def unit_templates(
    sites: list[dict],
    units: tuple[UnitTruth, ...],
    sampling_rate_hz: float,
    seed: int,
) -> np.ndarray:
    """One multi-channel template per unit, µV, `(n_units, n_samples, n_channels)`.

    **Indexed by `unit_id`, which `place_units` assigns as 0..n-1.** If unit ids
    ever stop being contiguous and zero-based, `render_traces` indexes the wrong
    template rather than failing, so the assumption is asserted here.
    """
    if [u.unit_id for u in units] != list(range(len(units))):
        raise ValueError(
            "unit ids must be contiguous from zero; render_traces indexes "
            f"templates by unit_id, and got {[u.unit_id for u in units]}"
        )
    from spikeinterface.generation import generate_templates

    channel_locations = np.array(
        [[s["x_coord"], s["y_coord"]] for s in sites], dtype=float
    )
    unit_locations = np.array(
        [[u.x_um, u.y_um, u.z_um] for u in units], dtype=float
    )
    return np.asarray(
        generate_templates(
            channel_locations=channel_locations,
            units_locations=unit_locations,
            sampling_frequency=sampling_rate_hz,
            ms_before=TEMPLATE_MS_BEFORE,
            ms_after=TEMPLATE_MS_AFTER,
            seed=seed,
        ),
        dtype=np.float64,
    )


def render_traces(
    sites: list[dict],
    units: tuple,
    spikes: tuple[tuple[float, int], ...],
    n_samples: int,
    sampling_rate_hz: float,
    noise_uv: float,
    seed: int,
    time_offset_s: float,
    drift_ppm: float,
) -> np.ndarray:
    """Correlated noise with every planted spike summed onto it, in µV.

    `drift_ppm` and `time_offset_s` are applied here rather than by the caller
    so that a spike lands at the same session time in every emitter that renders
    the same ground truth.
    """
    from wl_preproc.synth.timeline import apply_drift

    traces = correlated_noise(sites, n_samples, sampling_rate_hz, noise_uv, seed)
    templates = unit_templates(sites, units, sampling_rate_hz, seed)
    before = int(TEMPLATE_MS_BEFORE * sampling_rate_hz / 1000.0)

    for time_s, unit_id in spikes:
        peak = int((apply_drift(time_s, drift_ppm) + time_offset_s) * sampling_rate_hz)
        start = peak - before
        stop = start + templates.shape[1]
        if start < 0 or stop >= n_samples:
            continue
        traces[start:stop, :] += templates[unit_id]
    return traces
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/synth/test_waveforms.py -v`

Expected: PASS.

- [ ] **Step 5: Use it in the emitter**

In `wl_preproc/synth/spikeglx.py::write_spikeglx`, replace the noise draw at line 140 and the whole interim loop from Task 2 with:

```python
    sites = electrode_rows(recipe.probe_part_number)[: recipe.n_ap_channels]
    data = np.zeros((n_samples, n_channels), dtype=np.float64)
    if truth.units:
        data[:, :-1] = (
            render_traces(
                sites,
                truth.units,
                truth.spikes,
                n_samples=n_samples,
                sampling_rate_hz=fs,
                noise_uv=NOISE_UV,
                seed=recipe.seed + 1,
                time_offset_s=SPIKEGLX_PRE_ROLL_S,
                drift_ppm=drift_ppm,
            )
            / UV_PER_BIT
        )
    else:
        # Timing-only fixtures plant no units. Paying for template rendering to
        # validate a barcode is waste, and every Phase 1c recipe is this case.
        data[:, :-1] = rng.normal(0.0, NOISE_UV / UV_PER_BIT, (n_samples, n_channels - 1))
```

Keep `data[:, -1] = 0` (the SY channel) exactly as it is.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add wl_preproc/synth/waveforms.py wl_preproc/synth/spikeglx.py tests/synth/test_waveforms.py
git commit -m "synth: spikes have a spatial footprint, and the interpolation bias is recorded"
```

---

## Task 5: The LF stream

`write_spikeglx` emits `_imec0.ap.bin` alone. NP 1.0 digitises *"the action potential band (10 bits, 30 kHz…) and local field potential (LFP) band (10 bits, 2.5 kHz)"* separately, so 2b-3 and 2b-8 have nothing to read.

**Files:**
- Modify: `wl_preproc/synth/spikeglx.py`
- Test: `tests/synth/test_spikeglx.py`

**Interfaces:**
- Produces: `spikeglx.LF_SAMPLE_RATE_HZ = 2500.0`; `write_spikeglx` additionally writes `<session>_imec0.lf.bin` and `.meta`. Return value is unchanged — the AP path, which every existing caller means by "the SpikeGLX file".

- [ ] **Step 1: Write the failing test**

Append to `tests/synth/test_spikeglx.py`:

```python
def test_an_lf_stream_is_emitted_beside_the_ap_stream(tmp_path):
    recipe = SPATIAL_RECIPE
    truth = build_timeline(recipe)
    write_spikeglx(tmp_path, recipe, truth)

    lf = tmp_path / f"{recipe.session_id}_imec0.lf.bin"
    assert lf.exists()
    meta = lf.with_suffix(".meta").read_text()
    assert "imSampRate=2500" in meta
    assert f"snsApLfSy=0,{recipe.n_ap_channels},1" in meta


def test_the_lf_band_carries_a_depth_varying_signal(tmp_path):
    """2b-8's CSD is a second spatial derivative. An LF band that is the same
    at every depth has a CSD of zero everywhere, so 'the reference preserved
    the laminar gradient' would be unfalsifiable."""
    recipe = SPATIAL_RECIPE
    truth = build_timeline(recipe)
    write_spikeglx(tmp_path, recipe, truth)

    lf = tmp_path / f"{recipe.session_id}_imec0.lf.bin"
    n_chan = recipe.n_ap_channels + 1
    data = np.fromfile(lf, dtype=np.int16).reshape(-1, n_chan)[:, :-1].astype(float)

    low_freq = data - data.mean(axis=0)
    profile = np.abs(np.fft.rfft(low_freq, axis=0)[1:20]).sum(axis=0)
    assert profile.max() > 3 * profile.min()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/synth/test_spikeglx.py -k "lf_stream or depth_varying" -v`

Expected: FAIL — the `.lf.bin` does not exist, and `SPATIAL_RECIPE` is undefined.

- [ ] **Step 3: Add the recipe**

In `wl_preproc/synth/recipe.py`, after `DRIFT_RECIPE`:

```python
SPATIAL_RECIPE = SessionRecipe(
    session_id="2027-03-14_06",
    subject="pico",
    rig="rig-a",
    systems=("syncbox", "spikeglx"),
    blocks=(BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=2, trial_duration_s=3.0),),
    montages=(MontageSpec(start_s=0.0, end_s=6.0),),
    # NP1032 because its columns sit 103 um apart, which is what makes spec
    # section 7's Kilosort default reproducible. 64 sites span 640 um of shank
    # -- enough for a footprint to decay inside the recorded window, and small
    # enough that a rendered trace matrix stays a few tens of megabytes.
    probe_part_number="NP1032",
    n_ap_channels=64,
    n_units=12,
    ap_sample_rate_hz=30_000.0,
    seed=20270317,
)
```

and add `"spatial": SPATIAL_RECIPE,` to `RECIPES`.

- [ ] **Step 4: Implement the LF stream**

In `wl_preproc/synth/spikeglx.py`, add near the other constants:

```python
# NP 1.0 digitises the two bands separately -- the NHP probe paper: "the action
# potential band (10 bits, 30 kHz, 5.7 uV mean input-referred noise) and local
# field potential (LFP) band (10 bits, 2.5 kHz)". Emitting only AP left 2b-3
# and 2b-8 with nothing to read.
LF_SAMPLE_RATE_HZ = 2500.0

# Amplitude of the planted laminar gradient, and the depth over which it turns
# over. A CSD is a second spatial derivative, so an LF band identical at every
# depth has a CSD of zero everywhere and "the reference preserved the laminar
# gradient" becomes unfalsifiable.
LFP_UV = 120.0
LFP_FREQ_HZ = 8.0
```

and the import `write_spikeglx` will also need, if Task 4 has not already added it:

```python
from wl_preproc.synth.waveforms import correlated_noise, render_traces
```

Change `_meta_text`'s signature and the three band-dependent lines:

```python
def _meta_text(
    recipe: SessionRecipe,
    n_samples: int,
    n_channels: int,
    bin_name: str,
    band: str = "ap",
) -> str:
```

and inside it, replace the `imSampRate`, `fileTimeSecs`, `acqApLfSy` and `snsApLfSy` lines with:

```python
    rate = recipe.ap_sample_rate_hz if band == "ap" else LF_SAMPLE_RATE_HZ
    # SpikeInterface picks the stream apart on this pair: (n_ap, 0, 1) is an AP
    # file and (0, n_ap, 1) is an LF one. Getting it wrong yields a file the
    # reader opens as the wrong band rather than one it refuses.
    ap_lf_sy = f"{n_ap},0,1" if band == "ap" else f"0,{n_ap},1"
```

```python
        f"imSampRate={rate:g}",
        ...
        f"fileTimeSecs={n_samples / rate:.6f}",
        f"acqApLfSy={ap_lf_sy}",
        f"snsApLfSy={ap_lf_sy}",
```

Then, at the end of `write_spikeglx` and before the `return`:

```python
    _write_lf(dir_path, recipe, truth, drift_ppm=drift_ppm)
```

with:

```python
def _write_lf(dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float) -> Path:
    """The LFP band: a depth-varying low-frequency field, no spikes.

    Spikes are deliberately absent rather than low-passed away. The AP band is
    where they are ground truth; a filtered copy of them here would be a second
    representation of one fact, free to disagree with the first.
    """
    fs = LF_SAMPLE_RATE_HZ
    n_samples = int((recipe.duration_s + SPIKEGLX_PRE_ROLL_S) * fs)
    n_channels = recipe.n_ap_channels + 1
    sites = electrode_rows(recipe.probe_part_number)[: recipe.n_ap_channels]

    data = np.zeros((n_samples, n_channels), dtype=np.float64)
    if recipe.n_units:
        data[:, :-1] = correlated_noise(
            sites, n_samples, fs, NOISE_UV, seed=recipe.seed + 2
        )
        ys = np.array([s["y_coord"] for s in sites], dtype=float)
        # np.ptp(), not ys.ptp(): NumPy 2.0 removed the method, and this repo runs 2.4.
        depth = (ys - ys.min()) / max(float(np.ptp(ys)), 1.0)
        t = np.arange(n_samples, dtype=float) / fs
        phase = np.sin(2 * np.pi * LFP_FREQ_HZ * apply_drift(t, drift_ppm))
        data[:, :-1] += np.outer(phase, LFP_UV * np.cos(np.pi * depth))

    data /= UV_PER_BIT
    data[:, -1] = 0
    bin_path = dir_path / f"{recipe.session_id}_imec0.lf.bin"
    data.astype(np.int16).tofile(bin_path)
    bin_path.with_suffix(".meta").write_text(
        _meta_text(recipe, n_samples, n_channels, bin_path.name, band="lf"),
        encoding="utf-8",
    )
    return bin_path
```

- [ ] **Step 5: Run and verify against the oracle**

Run: `.venv/bin/python -m pytest tests/synth -q`

Then confirm the oracle opens it — this is the check that matters, not that the bytes exist:

```bash
.venv/bin/python -c "
import tempfile, pathlib
from spikeinterface.extractors import read_spikeglx
from wl_preproc.synth.recipe import SPATIAL_RECIPE
from wl_preproc.synth.timeline import build_timeline
from wl_preproc.synth.spikeglx import write_spikeglx
d = pathlib.Path(tempfile.mkdtemp())
write_spikeglx(d, SPATIAL_RECIPE, build_timeline(SPATIAL_RECIPE))
for stream in ('imec0.ap', 'imec0.lf'):
    r = read_spikeglx(d, stream_id=stream)
    print(stream, r.get_num_channels(), r.get_sampling_frequency())
"
```

Expected: `imec0.ap 64 30000.0` and `imec0.lf 64 2500.0`.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/synth/spikeglx.py wl_preproc/synth/recipe.py tests/synth/test_spikeglx.py
git commit -m "synth: emit the LF band, with a laminar gradient CSD can find"
```

---

## Task 6: RHS keeps up

`rhs.py` still renders one channel per spike via `unit_id % n_channels`. Left alone, `truth.spikes` would mean a footprint in one emitter and a modulo in the other.

**Files:**
- Modify: `wl_preproc/synth/rhs.py:27,49,101-115`
- Test: `tests/synth/test_rhs.py`

**Interfaces:**
- Produces: `rhs.RHS_SITE_PITCH_UM = 50.0`, `rhs.linear_sites(n_channels) -> list[dict]` — rows in the same shape `electrode_rows` returns, so `waveforms.py` needs no RHS-specific branch.

- [ ] **Step 1: Write the failing test**

Append to `tests/synth/test_rhs.py`:

```python
def test_an_intan_spike_has_a_footprint_too(tmp_path):
    """`truth.spikes` must mean one thing regardless of which emitter reads it.
    A footprint in SpikeGLX and a modulo here is exactly the kind of drift
    `synth/rhs.py` importing SPIKE_TEMPLATE_UV was written to prevent."""
    recipe = STIM_RECIPE.model_copy(update={"n_units": 3, "n_ap_channels": 16})
    truth = build_timeline(recipe)
    write_rhs(tmp_path, recipe, truth)

    amp = np.fromfile(tmp_path / "amplifier.dat", dtype=np.int16)
    data = amp.reshape(-1, recipe.n_ap_channels).astype(float)
    peak_per_channel = np.abs(data - data.mean(axis=0)).max(axis=0)
    assert np.count_nonzero(peak_per_channel > 0.3 * peak_per_channel.max()) > 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/synth/test_rhs.py -k footprint -v`

Expected: FAIL — a single-channel render leaves exactly one loud channel.

- [ ] **Step 3: Implement**

In `wl_preproc/synth/rhs.py`, drop the `SPIKE_TEMPLATE_UV` import and add:

```python
# Intan headstages carry no probe part number, so there is no offline table to
# read. A linear array is declared here instead -- and declared rather than
# borrowed from Neuropixels, because an RHS session on NP geometry would be a
# fixture describing hardware this lab does not have.
RHS_SITE_PITCH_UM = 50.0


def linear_sites(n_channels: int) -> list[dict]:
    """Rows in `electrode_rows`' shape, so `waveforms.py` needs no RHS branch."""
    return [
        {
            "electrode": index,
            "shank": 0,
            "shank_col": 0,
            "shank_row": index,
            "x_coord": 0.0,
            "y_coord": index * RHS_SITE_PITCH_UM,
        }
        for index in range(n_channels)
    ]
```

Replace the noise draw at line 101 and the interim loop from Task 2 with:

```python
    sites = linear_sites(n_channels)
    if truth.units:
        amplifier = (
            render_traces(
                sites,
                truth.units,
                truth.spikes,
                n_samples=n_samples,
                sampling_rate_hz=fs,
                noise_uv=NOISE_UV,
                seed=recipe.seed + 3,
                time_offset_s=RHS_PRE_ROLL_S,
                drift_ppm=drift_ppm,
            )
            / UV_PER_BIT
        )
    else:
        amplifier = rng.normal(0.0, NOISE_UV / UV_PER_BIT, (n_samples, n_channels))
```

Keep the comment above the old loop — it explains why RHS renders the same
planted spikes at all, and that reason is unchanged. Add the import:

```python
from wl_preproc.synth.waveforms import render_traces
```

Note the distinct seed offset (`+3`; SpikeGLX AP uses `+1` and LF `+2`). Sharing
one would give the two systems identical noise, and a timebase test that passes
only because both devices heard the same sample is a test of nothing.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/synth -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/rhs.py tests/synth/test_rhs.py
git commit -m "synth: Intan spikes get the same footprint treatment"
```

---

## Task 7: Prove the Kilosort geometry fault

Spec §7 rules that a KS4 default splits units on this lab's probe. This task demonstrates it rather than asserting it.

**Files:**
- Create: `wl_preproc/ephys/sorter_geometry.py`
- Test: `tests/ephys/test_sorter_geometry.py` *(create)*, `tests/ephys/test_kilosort_defaults_split_units.py` *(create)*

**Interfaces:**
- Produces: `sorter_geometry.kilosort_spacing(part_number) -> dict` returning `{"dminx": float, "max_channel_distance": float}` derived from the probe's own x spacing.

- [ ] **Step 1: Write the failing test for the derivation**

Create `tests/ephys/test_sorter_geometry.py`:

```python
import pytest

from wl_preproc.ephys.sorter_geometry import kilosort_spacing


@pytest.mark.parametrize(
    "part_number,expected_dminx",
    [("NP1000", 16.0), ("NP1032", 103.0), ("NP1030", 16.0)],
)
def test_spacing_is_the_probes_own_smallest_horizontal_step(part_number, expected_dminx):
    """KS4's dminx is documented in microns as the horizontal spacing of
    template centres, defaulting to 32 because that suits Neuropixels 1 and 2.
    It is a property of the probe, so it is read from the probe."""
    assert kilosort_spacing(part_number)["dminx"] == expected_dminx


def test_max_channel_distance_spans_the_columns():
    """At KS4's default of 32 um, no channel in NP1032's first column is ever
    compared with the second, 103 um away -- so a spike straddling both splits
    into two units and nothing reports it."""
    assert kilosort_spacing("NP1032")["max_channel_distance"] >= 103.0
    assert kilosort_spacing("NP1000")["max_channel_distance"] >= 48.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/ephys/test_sorter_geometry.py -v`

Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `wl_preproc/ephys/sorter_geometry.py`:

```python
"""Kilosort spacing parameters, derived from the probe rather than defaulted.

**Why this lives at the reader seam and not in the sorting phase.** KS4's
`dminx` and `max_channel_distance` both default to 32 um, and its own parameter
documentation says that "should work well for Neuropixels 1 and Neuropixels 2
probes". This lab's probe is NP1032, whose two columns sit 103 um apart. At the
default, a channel in one column is never compared with any channel in the
other: a spike straddling both becomes two units, silently. The geometry is
known here, where the probe is read; leaving the constant to 2b-5 would put a
probe-dependent number in the phase furthest from the probe.
"""

from __future__ import annotations

import numpy as np

from wl_preproc.ephys.geometry import electrode_rows


def kilosort_spacing(part_number: str) -> dict[str, float]:
    """`dminx` and `max_channel_distance`, in microns, for `part_number`."""
    xs = np.unique([row["x_coord"] for row in electrode_rows(part_number)])
    steps = np.diff(xs)
    return {
        # The smallest real horizontal step: template centres closer together
        # than the sites themselves buy nothing.
        "dminx": float(steps.min()) if steps.size else 1.0,
        # Wide enough that the outermost columns are still compared, which is
        # the failure the default produces.
        "max_channel_distance": float(xs.max() - xs.min()) if xs.size > 1 else 32.0,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/ephys/test_sorter_geometry.py -v`

Expected: PASS.

- [ ] **Step 5: Write the demonstration, marked slow**

Create `tests/ephys/test_kilosort_defaults_split_units.py`:

```python
"""KS4 on CPU, minutes not seconds, and not part of the default run.

Marked `slow` and excluded from CI. It exists because spec section 7 rules that
the fixture should demonstrate the fault rather than warn about it, and a
demonstration that never runs is a warning with extra steps -- so run it by hand
whenever the probe, the geometry module or the KS4 version changes.
"""

import numpy as np
import pytest

from wl_preproc.ephys.sorter_geometry import kilosort_spacing

pytest.importorskip("kilosort")
pytestmark = pytest.mark.slow


def _sort(recording_dir, label, **settings):
    """`label` names the output folder explicitly. Deriving it from the settings
    would collide here: both calls pass exactly two keyword arguments, so the
    second run would silently reuse -- or clobber -- the first one's folder."""
    from spikeinterface.extractors import read_spikeglx
    from spikeinterface.sorters import run_sorter

    recording = read_spikeglx(recording_dir, stream_id="imec0.ap")
    sorting = run_sorter(
        "kilosort4", recording, folder=recording_dir / f"ks_{label}",
        remove_existing_folder=True, do_CAR=False, **settings,
    )
    return sorting.get_unit_ids()


def test_the_default_dminx_splits_a_unit_that_straddles_the_columns(tmp_path):
    from wl_preproc.synth.recipe import SPATIAL_RECIPE
    from wl_preproc.synth.spikeglx import write_spikeglx
    from wl_preproc.synth.timeline import build_timeline

    recipe = SPATIAL_RECIPE
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
```

Register the marker in `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
markers = ["slow: runs a real sorter; excluded from CI, run by hand"]
addopts = "-m 'not slow'"
```

- [ ] **Step 6: Run it by hand once and record the numbers**

Run: `.venv/bin/python -m pytest tests/ephys/test_kilosort_defaults_split_units.py -v -m slow`

Expected: PASS. **Record the two unit counts in the commit message** — they are the measurement spec §7 promises, and a passing assertion alone does not carry them.

- [ ] **Step 7: Run the default suite and confirm the marker excludes it**

Run: `.venv/bin/python -m pytest -q`

Expected: PASS, with the slow test deselected.

- [ ] **Step 8: Commit**

```bash
git add wl_preproc/ephys/sorter_geometry.py tests/ephys/test_sorter_geometry.py \
        tests/ephys/test_kilosort_defaults_split_units.py pyproject.toml
git commit -m "ephys: derive Kilosort spacing from the probe, and demonstrate the default splitting units"
```

---

## Task 8: Record what this changed

Spec §12 lists what this work emits. Most of it lands with the back half, but three statements become false the moment Task 7 commits, and this repository's convention is that a superseded claim is corrected rather than left standing.

**Files:**
- Modify: `docs/CHECKPOINT.md`
- Modify: `docs/superpowers/specs/2026-08-23-phase-2b-decomposition-design.md:53-67` (the §1 table)
- Modify: `wl.yaml`

- [ ] **Step 1: Correct the decomposition's §1 entry**

Under the table in §1, append:

```markdown
> **2b-2 roughly doubled on 2026-08-26**, and the reason is recorded rather
> than absorbed. Designing it found that the synthetic generator has no spatial
> structure at all — one channel per spike, one template, no unit identity,
> uncorrelated noise, no LF band — so none of §6.1's questions were measurable
> against it. Correcting the fixture is now this piece's front half; see
> `2026-08-26-phase-2b2-reader-and-chain-design.md` §1 and §3. The dependency
> column is unchanged: the front half needs neither container nor GPU and was
> built while 2b-1 was still hardware-blocked.
```

- [ ] **Step 2: Correct `CHECKPOINT.md`**

In "What is next", under the Phase 2b entry, append a dated note recording that 2b-2 is designed, that its front half is built, and that the back half waits on 2b-1. State plainly that **`wl_preproc/ephys/` is no longer only `geometry.py`** — the decomposition's §0.1 claim that *"no signal reading exists yet"* remains true (nothing reads signal), but `sorter_geometry.py` now exists beside it and a reader would otherwise be surprised.

- [ ] **Step 3: Correct `wl.yaml`'s status**

`status.next` currently names the 2b-2 design spec. Replace it with the back half, and set `describes` to this task's commit. Leave `third_party`'s `spikeinterface` entry alone: it is still the format oracle, and it becomes a runtime dependency at the reader seam, not here. `kilosort` stays `where: serv` — it is installed locally for Task 7's demonstration, which is not deployment.

- [ ] **Step 4: Verify the manifest still checks**

Run: `wl-check`
Expected: `wl.yaml: no findings`

- [ ] **Step 5: Commit**

```bash
git add docs/CHECKPOINT.md docs/superpowers/specs/2026-08-23-phase-2b-decomposition-design.md wl.yaml
git commit -m "docs: 2b-2's front half is built, and the decomposition records why it doubled"
```

---

## Not in this plan

- **The reader seam, the chain, the Kilosort seam and the paramset work** (spec §4, §5, §6, §8) — gated on 2b-1.
- **`ephys.Preprocessing` and its `.Channel` part** (spec §8.2) — it records the outcome of a chain that does not exist yet.
- **The real-template mode** (spec §3.4) — needs network; the amplitude-decay bias is documented in `waveforms.py` instead, which is what makes any interpolation number read as an upper bound.
- **Bad-channel faults in `synth/faults.py`** — spec §10 needs planted dead and noisy channels, but they are only measurable once the chain can detect them. They belong with the back half's Task for §5.1.
