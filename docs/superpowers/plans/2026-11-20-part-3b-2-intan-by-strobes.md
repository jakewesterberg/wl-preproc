# Part 3b-2: Intan by Strobes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Under the NI's clock, place Intan on session time by matching its strobe edges to the NI's, so that
a recording session with the NI and Intan and no sync box can reach tier N.

**Architecture:**
- A pure matcher (`timebase/strobes.py`) finds the anchor from the intervals' pattern, then extends the map
  through the recording, refitting as it goes.
- A small reading layer (`timebase/intan.py`) feeds it the NI's and each Intan recording's strobe rises, and
  returns `(session_s, device_s)` pairs.
- The existing least squares in `timebase/fit.py`, refactored to take pairs, turns those pairs into the same
  `RateFit` and `OffsetFit` that barcodes give. So `SystemTimebase`, `Segment`, `TimingProvenance` and the NWB
  file need only their NI branches changed.

**Tech Stack:** Python 3.11 and 3.13, numpy, DataJoint 2.3, pynwb; pytest with the MySQL testcontainer.

**Spec:** `docs/superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md`.
- It binds through amendment 49 (3b-2), with §6.2, §6.4 and §7.
- Amendment 40 scopes the branch.
- Amendment 51 says a device with no way onto the NI's clock makes the session tier D.

The spike that amendment 49 reports is `~/.cache/wl-preproc-scratch/2026-10-30-part3b/spike/strobes.py`. It is a
throwaway, and reading it is optional.

## Global Constraints

- **Edges.** "Both sides read the strobe's rising edge, the NI's from its strobe bit, not the word's latch"
  (amendment 49). On the NI that is bit 1 of digital word 0 (`events/extract.py::_NIDQ_STROBE_BIT`). On Intan it
  is bit 1 of `digitalin.dat` (`RHS_STROBE_DIGITAL_BIT`).
- **Anchor.** "Candidate anchors from a 2 s window of Intan's first edges, each verified by extending the map over
  the first 120 s."
- **Extension.** "The whole recording in time order. Each edge is matched to the NI's nearest within 0.5 ms, and
  rate and offset are refitted as it goes. The extension starts with small steps, about 10 edges, and grows."
- **Fit.** "A residual limit of 100 µs, then a final fit, with residuals kept as for barcodes;
  `time_source = 'strobes'`."
- **Ambiguity refuses.** "The best anchor must match at least 95% of edges, and at least 10 points more than the
  runner-up. A strictly periodic code stream cannot anchor."
- **Left out.** "An Intan that cannot be anchored is left out of the file and named (§6.4)."
- **Unchanged under the sync box.** Intan under the sync box is fitted by barcodes exactly as today. A sync-box
  NWB file stays byte-identical: `tests/nwb/test_writers.py::test_a_sync_box_nwb_file_is_unchanged` passes
  unedited.
- **Unchanged synthetic sessions.** Every existing synthetic recipe's output is byte-identical: new recipe fields
  default to off.
- **Database migration.** Development databases are rebuilt, never migrated (3b-1's ruling 16). The enum
  `SystemTimebase.time_source` gains `'strobes'`, and the table gains one nullable column.
- **Float comparisons.** A float that passes through a fit is compared to a tolerance, never as text (Linux CI
  rounds otherwise).
- **No new dependency.**
- **Test discipline.**
  - Each test is seen to fail first.
  - Run only targeted tests during tasks.
  - The full suite runs once, at the merge, on both interpreters, with every reference set.
  - Never commit while a test run is going on the same checkout.
- **Commit trailer.** Every commit ends:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
  ```

## Decisions this plan makes (the spec is silent)

1. **Which windows are tried.** The anchor's 2 s windows are tried one after another, from Intan's first edge
   through Intan's first 600 s (`ANCHOR_SEARCH_S`). The first window whose verified best candidate passes the
   ambiguity rule anchors the map.
   - Amendment 44 says the rig may strobe before SpikeGLX starts. Then Intan's first window can hold no edge the
     NI saw, and a first-window-only search would refuse a healthy session.
   - The extension then runs forward from the anchoring window to the last edge, and backward from it to the
     first. The backward pass is seeded with the forward pass's rate and offset.
2. **What the 95% is a fraction of.** It counts Intan's edges in the verification span whose predicted NI time
   lies within the NI's recording, give or take 0.5 ms. An edge strobed before the NI started is no evidence
   against the anchor.
3. **A floor.** A candidate needs at least 10 matched edges (`MIN_MATCHED_EDGES`) to be scored at all. Fewer
   cannot be told from chance, and amendment 49's first step is "about 10 edges".
4. **Each Intan recording separately.** Each recording (`find_recordings("rhs", ...)`) is anchored on its own
   against the NI's first file, the only one on session time (amendment 50).
   - The system's rate is pooled across the anchored recordings, each with its own intercept, as barcodes are
     (`fit_rate_across`).
   - A recording that cannot be anchored is rejected as `no_strobe_anchor`.
   - The others are kept.
5. **What is recorded.** `RejectedSegment.reason` is a fixed vocabulary, so it holds `no_strobe_anchor`. The full
   sentence (best and runner-up fractions, or the edge count) goes to the module's logger at INFO.
   `SystemTimebase` gains `n_strobes_matched` (null except under strobes), the evidence a strobe fit stands on.
6. **The NWB file.** Under the NI, `system_clocks` gains a `time_source` column (`clock`, `strobes`, `none`), and
   the NI texts no longer say every residual is 0. This is §7's "records each device's map with its source".
   Sync-box files are untouched.
7. **The synthetic sessions.** Synthetic trials run back to back at fixed lengths. A strobe stream with that
   timing is the periodic case the spike showed cannot anchor, unlike the rig, whose trials start on the animal.
   - The fixture is corrected rather than the design: a recipe field `trial_waits_s` gives an animal-paced wait
     before each trial.
   - It is empty by default, so every existing recipe is unchanged.
   - A new recipe `NI_INTAN_RECIPE` uses it.
8. **Strobe witnesses.** `TimingProvenance.n_strobe_witnesses` stays 0 under the NI. Under the NI Intan is
   fitted, not counted as a witness, and tier N does not read the column.

## Review Focus

1. **Intan and the NI starting at different times.** If either started more than one 2 s window before the
   other's first strobe, the map must still anchor, from a later window. Test: Task 3,
   `test_an_ni_started_late_still_anchors` and `test_an_intan_started_late_still_anchors`.
2. **A sparse or empty strobe line** (a loose cable, a very short recording): refuse, never fit two chance
   matches. Test: Task 3, `test_fewer_than_ten_edges_refuses`; Task 5, the `no_strobe` session.
3. **A glitch beside a dropped edge** must not stay matched; the residual limit removes it. Test: Task 3,
   `test_drops_and_glitches_match_no_wrong_edge` asserts the largest residual is at most 100 µs.
4. **An Intan with two recordings, one unanchorable:** keep the good one and name the other. Test: Task 5, the
   `two` session.
5. **A 12-hour session's memory.** The candidate scoring is chunked, so it never builds an
   (NI edges × window edges) array whole. Test: Task 3, `test_chunked_scoring_gives_the_same_anchor`.

---

### Task 1: Animal-paced waits in the synthetic sessions

**Files:**
- Modify: `wl_preproc/synth/recipe.py` (a field beside `trial_numbers`, the `duration_s` property, `_coherent`'s
  checks, a new recipe after `NI_CLOCK_RECIPE`, and `RECIPES`)
- Modify: `wl_preproc/synth/timeline.py` (`build_timeline`'s trial loop, about line 137)
- Test: `tests/synth/test_trial_waits.py` (create)

**Interfaces:**
- Produces:
  - `SessionRecipe.trial_waits_s: tuple[float, ...] = ()`: the wait before each trial, by position across the
    session. When non-empty it has one entry per trial, each `>= 0`.
  - `animal_paced_waits(n_trials: int, mean_s: float, seed: int) -> tuple[float, ...]` in `synth/recipe.py`:
    each wait is `0.2 + exponential(mean_s)`, rounded to 1 ms, drawn from `np.random.default_rng(seed)`.
  - `NI_INTAN_RECIPE`, registered as `RECIPES["ni-intan"]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_without_waits_trials_run_back_to_back():
    for name, recipe in RECIPES.items():
        if name == "ni-intan":
            continue
        assert recipe.trial_waits_s == ()
        trials = build_timeline(recipe).trials
        assert all(later.start_s == earlier.end_s for earlier, later in zip(trials, trials[1:]))

def test_each_trial_starts_after_its_wait():
    waits = (0.5, 1.25, 0.2, 2.0)
    recipe = CI_RECIPE.model_copy(update={"trial_waits_s": waits,
                                          "montages": (MontageSpec(start_s=0.0, end_s=15.0 + sum(waits)),)})
    truth = build_timeline(recipe)
    plain = build_timeline(CI_RECIPE)
    for position, (trial, before) in enumerate(zip(truth.trials, plain.trials, strict=True)):
        assert trial.start_s == pytest.approx(before.start_s + sum(waits[: position + 1]))
        assert trial.end_s - trial.start_s == pytest.approx(before.end_s - before.start_s)
    assert recipe.duration_s == pytest.approx(15.0 + sum(waits))

def test_waits_must_name_every_trial():
    with pytest.raises(ValueError, match="trial_waits_s names 2 trials, and the session has 4"):
        CI_RECIPE.model_copy(update={"trial_waits_s": (1.0, 1.0)})._coherent()

def test_a_wait_cannot_be_negative():
    with pytest.raises(ValueError, match="trial_waits_s holds a negative wait"):
        CI_RECIPE.model_copy(update={"trial_waits_s": (1.0, -0.1, 1.0, 1.0)})._coherent()

def test_animal_paced_waits_are_irregular_and_repeatable():
    waits = animal_paced_waits(40, 1.0, 7)
    assert waits == animal_paced_waits(40, 1.0, 7)
    assert min(waits) >= 0.2 and len(set(waits)) > 30

def test_the_ni_intan_recipe_records_both_without_the_sync_box():
    recipe = RECIPES["ni-intan"]
    assert recipe.systems == ("spikeglx", "rhs") and len(recipe.trial_waits_s) == 4
```

  - If `_coherent` is not how this module's checks are invoked, call the recipe's validator the way
    `tests/synth/` already tests `faulted_trials`' message. Keep the messages exactly as written.
  - Waits are added inside each block, before each trial's start. A block's first wait is inside the block,
    since `block_start` is taken before it.

- [ ] **Step 2: Run them to see them fail**

  Run: `.venv/bin/python -m pytest tests/synth/test_trial_waits.py -q`
  Expected: FAIL. `trial_waits_s` and `animal_paced_waits` are not defined.

- [ ] **Step 3: Implement**

  - `duration_s` adds `sum(self.trial_waits_s)`.
  - `build_timeline` advances `cursor` by the trial's wait before `trial_start = cursor`.
  - `NI_INTAN_RECIPE` is `NI_CLOCK_RECIPE` with these values:
    - `systems=("spikeglx", "rhs")`
    - `session_id="2027-03-14_08"`
    - `seed=20270320`
    - `trial_waits_s=animal_paced_waits(4, 1.0, 20270320)`
    - `system_drift_ppm=(("spikeglx", 18.0), ("rhs", -40.0))`
    - `montages` covering `15.0 + sum(waits)`

- [ ] **Step 4: Run them to see them pass, then the synth suite**

  Run: `.venv/bin/python -m pytest tests/synth -q`
  Expected: all pass.

- [ ] **Step 5: Commit**: `feat(synth): an animal-paced wait before each trial, and the ni-intan recipe`

---

### Task 2: The least squares, from pairs

**Files:**
- Modify: `wl_preproc/timebase/fit.py`
- Test: `tests/timebase/test_fit_pairs.py` (create)

**Interfaces:**
- Produces:
  - `fit_pairs_across(groups: Sequence[Sequence[tuple[float, float]]], nominal_rate_hz: float) -> RateFit`.
    Each group is one recording's `(session_s, device_s)` pairs, with its own intercept. It raises `ValueError`
    exactly as `fit_rate_across` does, with "pair" in place of "barcode" in its messages.
  - `fit_offset_to_pairs(pairs: Sequence[tuple[float, float]], rate_fit: RateFit) -> OffsetFit`. It raises
    `ValueError` on no pairs.
  - `fit_rate_across` and `fit_offset` become `_matched_pairs` followed by these two. Their messages say
    "barcode" as before.

- [ ] **Step 1: Write the failing tests**

```python
def test_pairs_fit_as_the_barcodes_they_came_from():
    # The same pairs, given as barcodes against a reference and as pairs, fit identically.
    barcodes, reference = _barcodes_on_a_drifting_clock(drift_ppm=37.0, n=40)  # helper in this file
    by_barcode = fit_rate_across([barcodes], reference, 30_000.0)
    by_pair = fit_pairs_across([_matched_pairs(barcodes, reference)], 30_000.0)
    assert by_pair == by_barcode
    assert fit_offset_to_pairs(_matched_pairs(barcodes, reference), by_pair) == fit_offset(barcodes, reference, by_barcode)

def test_two_groups_keep_their_own_intercepts():
    pairs_a = [(t, t * (1 + 50e-6) + 3.0) for t in (1.0, 2.0, 5.0)]
    pairs_b = [(t, (t - 100.0) * (1 + 50e-6)) for t in (101.0, 104.0, 110.0)]
    fit = fit_pairs_across([pairs_a, pairs_b], 20_000.0)
    assert fit.drift_ppm == pytest.approx(50.0, abs=1e-6)

def test_one_pair_is_a_position_not_a_slope():
    with pytest.raises(ValueError, match="1 pair matched"):
        fit_pairs_across([[(1.0, 1.0)]], 30_000.0)
```

- [ ] **Step 2: Run them to see them fail**

  Run: `.venv/bin/python -m pytest tests/timebase/test_fit_pairs.py -q`
  Expected: FAIL with an ImportError (`fit_pairs_across`).

- [ ] **Step 3: Implement the refactor.** Move the arithmetic; change none of it.

- [ ] **Step 4: Run these tests and the existing fit tests**

  Run: `.venv/bin/python -m pytest tests/timebase -q -k "fit"`
  Expected: all pass. Every barcode fit is unchanged.

- [ ] **Step 5: Commit**: `refactor(fit): the rate and offset least squares take matched pairs`

---

### Task 3: The strobe matcher

**Files:**
- Create: `wl_preproc/timebase/strobes.py` (pure numpy: no DataJoint, no file I/O, no `wl_preproc.synth`)
- Create: `tests/timebase/strobe_streams.py`. It is a test helper, the spike's `simulate`:
  - **Inputs:** scenario fields for hours, burst size, periodic or trial-paced timing, relative ppm, drop, extra,
    Intan's late start and early end, an NI late start, both sample rates, and the seed.
  - **Returns:** `(ni_s, intan_s, true_id, truth_map)`.
- Test: `tests/timebase/test_strobes.py` (create)

**Interfaces:**
- Produces:
  - **Constants:**
    - `ANCHOR_WINDOW_S = 2.0`
    - `ANCHOR_SEARCH_S = 600.0`
    - `VERIFY_S = 120.0`
    - `MATCH_TOLERANCE_S = 0.0005`
    - `RESIDUAL_LIMIT_S = 100e-6`
    - `MIN_ANCHOR_FRACTION = 0.95`
    - `MIN_ANCHOR_MARGIN = 0.10`
    - `MIN_MATCHED_EDGES = 10`
    - `FIRST_STEP_EDGES = 10`
    - `MAX_STEP_EDGES = 500`
    - `N_CANDIDATES = 8`
    - `SCORING_CELLS = 1 << 20`
  - `class Unanchored(ValueError)`: its message is one of the four sentences below.
  - `@dataclass(frozen=True) class StrobeMatch`, with these fields:
    - `reference_index: np.ndarray`
    - `device_index: np.ndarray`: both int arrays of equal length, increasing in `device_index`, the pairs left
      after the residual limit.
    - `anchor_fraction: float`
    - `runner_up_fraction: float`
  - `match_strobes(reference_s: np.ndarray, device_s: np.ndarray, *, scoring_cells: int = SCORING_CELLS) -> StrobeMatch`.
    Both arrays are increasing edge times in seconds: `reference_s` on the NI's clock, `device_s` on Intan's.
- **The algorithm.** It is the spike's `anchor2` plus `extend`, with these changes.
  - **Windows.** They are tried in turn from `device_s[0]`, each `ANCHOR_WINDOW_S` long, until the window's start
    passes `device_s[0] + ANCHOR_SEARCH_S`.
  - **Candidates.** For each window, score every reference edge as the image of the window's first edge. Do it
    in chunks of reference edges, so that `chunk × window edges <= scoring_cells`. The top `N_CANDIDATES` give
    offsets.
  - **Verification.** Extend each candidate over `VERIFY_S` from the window's start. Its fraction is matched over
    eligible (Decision 2). A candidate with fewer than `MIN_MATCHED_EDGES` matched scores 0.
  - **Best and runner-up.** The runner-up is the best candidate whose offset differs from the best's by more than
    `MATCH_TOLERANCE_S`.
  - **A window passes** when best ≥ `MIN_ANCHOR_FRACTION` and best − runner-up ≥ `MIN_ANCHOR_MARGIN`.
  - **Extension.**
    - Steps start at `FIRST_STEP_EDGES` and grow as `min(MAX_STEP_EDGES, max(FIRST_STEP_EDGES, matched // 2))`.
    - Refit with `np.polyfit(device, reference, 1)` after each step once 20 pairs exist, over the last 20,000.
    - Run forward from the window's start to the end, then backward from it, seeded with the forward fit.
  - **Then** drop pairs whose residual exceeds `RESIDUAL_LIMIT_S`, refit, and return the pairs.
- **Refusal sentences**, exactly:
  - `"the NI recorded no strobe edge"`
  - `f"Intan recorded {n} strobe edge{'s' if n != 1 else ''}, and anchoring needs at least {MIN_MATCHED_EDGES}"`
  - `f"no anchor matched {MIN_ANCHOR_FRACTION:.0%} of Intan's edges: the best matched {best:.0%}"`
  - `f"the strobes' pattern is ambiguous: the best anchor matched {best:.0%} and the runner-up {runner:.0%}, within {MIN_ANCHOR_MARGIN:.0%}; a strictly periodic code stream cannot anchor"`

  When no window passes, the reason is the one for the window whose best scored highest. The ambiguous sentence
  is used when that window's best reached `MIN_ANCHOR_FRACTION`.

- [ ] **Step 1: Write the failing tests.** Each asserts these four things:
  - no wrong match: `true_id[device_index] == reference_id[reference_index]` wherever `true_id >= 0`;
  - matched is at least 98% of real edges, less the drop;
  - the largest residual of `np.polyfit` over the pairs is ≤ 100 µs;
  - the map is within 10 µs of `truth_map` at both ends.

  Each scenario's simulated session is 1 hour unless named. The tests:
  - `test_clean_60_ppm_matches_every_edge`
  - `test_drops_and_glitches_match_no_wrong_edge` (10% dropped, 0.5% extra)
  - `test_200_ppm_matches` (`rel_ppm=200`)
  - `test_20_and_25_khz_match` (`fs_intan=20_000`, `fs_ni=25_000`, 1% dropped)
  - `test_single_words_match` (`burst_max=1`, 1% dropped)
  - `test_trial_paced_fixed_delays_match` (`trial_offsets=(0, 0.3, 0.8, 1.3, 1.5)`, `burst_max=3`)
  - `test_an_intan_started_late_still_anchors` (`intan_late_s=600`)
  - `test_an_ni_started_late_still_anchors` (`ni_late_s=30`: the NI's first 30 s of edges are absent)
  - `test_a_periodic_stream_refuses`:
    ```python
    with pytest.raises(Unanchored, match="ambiguous|no anchor matched"):
        match_strobes(*periodic(2.0, jitter=0.0005, burst_max=4))
    ```
  - `test_fewer_than_ten_edges_refuses`: nine edges, `match=r"Intan recorded 9 strobe edges, and anchoring needs at least 10"`
  - `test_no_ni_edge_refuses`: `match="the NI recorded no strobe edge"`
  - `test_chunked_scoring_gives_the_same_anchor`: `scoring_cells=64` and the default give equal `StrobeMatch`
    arrays (`np.array_equal`).

- [ ] **Step 2: Run them to see them fail**

  Run: `.venv/bin/python -m pytest tests/timebase/test_strobes.py -q`
  Expected: FAIL. `wl_preproc.timebase.strobes` is not found.

- [ ] **Step 3: Implement `match_strobes`**, as above.

- [ ] **Step 4: Run them to see them pass**

  Run: `.venv/bin/python -m pytest tests/timebase/test_strobes.py -q --durations=5`
  Expected: all pass. No test takes over 20 s; if one does, shorten its session, not the algorithm.

- [ ] **Step 5: Mutation checks.** Prove each fails a named test, then restore:
  - remove the residual limit: `test_drops_and_glitches_match_no_wrong_edge`;
  - start at `MAX_STEP_EDGES`: any `rel_ppm=200` test;
  - try only the first window: `test_an_ni_started_late_still_anchors`;
  - set `MIN_ANCHOR_MARGIN = 0`: `test_a_periodic_stream_refuses`.

  Make each mutation at this task's commit. Clear `__pycache__` before each run, since a stale `.pyc` defeats a
  same-length mutation.

- [ ] **Step 6: Commit**: `feat(timebase): match Intan's strobe edges to the NI's by their pattern (amendment 49)`

---

### Task 4: Reading the strobes, and an Intan recording's pairs

**Files:**
- Modify: `wl_preproc/events/extract.py`: add `extract_nidq_strobe_rises`.
- Create: `wl_preproc/timebase/intan.py`
- Test: `tests/timebase/test_intan_pairs.py` (create)

**Interfaces:**
- Consumes: `match_strobes`, `Unanchored` (Task 3); `clock.nidq_files` (3b-1); `channels.column_chunks` and
  `channels.strobe_rises`.
- Produces:
  - `extract_nidq_strobe_rises(nidq_bin: Path) -> StrobeWitness`. It reads the rising edges of
    `_NIDQ_STROBE_BIT` in digital word 0 (column `meta.n_analog_channels`), in chunks, at the `.meta`'s rate, and
    raises as `extract_nidq_words` does on a sidecar with fewer than two words.
  - `@dataclass(frozen=True) class IntanPlacement`:
    - `pairs: tuple[tuple[float, float], ...]`: `(session_s, device_s)` with `session_s = ni_sample / ni_fs` and
      `device_s = intan_sample / intan_fs`.
    - `first_sample: int`: the native Intan index of the first pair.
    - `anchor_fraction: float`
    - `runner_up_fraction: float`
  - `place_intan(session_dir: Path, recording_dir: Path) -> IntanPlacement`. It raises `Unanchored` and logs its
    sentence at INFO, naming the recording.
- **Caching.** The edges of each file are read through one `functools.lru_cache(maxsize=8)` function keyed by
  `(str(path), st_size, st_mtime_ns, kind)`. `SystemTimebase` and `Segment` then read each file once per pass.

- [ ] **Step 1: Write the failing tests.** Use a synthetic `NI_INTAN_RECIPE` session made with
  `generate_session` in `tmp_path`.

```python
def test_an_intan_recording_is_placed_on_the_ni_by_its_strobes(ni_intan):
    recipe, truth, session_dir = ni_intan
    placement = place_intan(session_dir, session_dir / "rhs")
    # Every code word is one strobe on each side, so each pair is one word.
    assert len(placement.pairs) == len(truth.code_words)
    ni_true = [t * (1 + 18e-6) + SPIKEGLX_PRE_ROLL_S for t, _ in truth.code_words]
    rhs_true = [t * (1 - 40e-6) + RHS_PRE_ROLL_S for t, _ in truth.code_words]
    for (session_s, device_s), ni_s, rhs_s in zip(placement.pairs, ni_true, rhs_true, strict=True):
        assert session_s == pytest.approx(ni_s, abs=1 / 30_000 + 1e-9)
        assert device_s == pytest.approx(rhs_s, abs=1 / 30_000 + 1e-9)
    assert placement.anchor_fraction >= 0.95

def test_an_intan_with_no_strobe_refuses(ni_intan):
    recipe, truth, session_dir = ni_intan
    _clear_intan_strobes(session_dir / "rhs")  # helper: zero bit 1 of every digitalin.dat word
    with pytest.raises(Unanchored, match="Intan recorded 0 strobe edges"):
        place_intan(session_dir, session_dir / "rhs")
```

  - Read the two pre-rolls from `synth/spikeglx.py` and `synth/rhs.py`.
  - Read the Intan rate from the recipe as the synth writes it. Do not hard-code it: 30 kHz above is a
    placeholder. Use `1 / fs` of each side.
  - Put `_clear_intan_strobes` in `tests/timebase/intan_helpers.py`; Task 5 reuses it.

- [ ] **Step 2: Run them to see them fail**

  Run: `.venv/bin/python -m pytest tests/timebase/test_intan_pairs.py -q`
  Expected: FAIL. `wl_preproc.timebase.intan` is not found.

- [ ] **Step 3: Implement** `extract_nidq_strobe_rises`, the cached reader and `place_intan`. The NI file is
  `nidq_files(session_dir)[0]`.

- [ ] **Step 4: Run them to see them pass**

  Run: `.venv/bin/python -m pytest tests/timebase/test_intan_pairs.py tests/events -q`
  Expected: all pass.

- [ ] **Step 5: Commit**: `feat(timebase): read both strobe lines and place an Intan recording on the NI`

---

### Task 5: Intan on the NI's clock in the database

**Files:**
- Modify: `wl_preproc/schema/timebase.py`:
  - `_TIME_SOURCE_ENUM` gains `'strobes'`;
  - `SystemTimebase` gains `n_strobes_matched=null : int unsigned  # Intan's edges matched to the NI's; null unless time_source is strobes`;
  - `SystemTimebase.make`'s NI branch;
  - its docstring's "Intan for 3b-2".
- Modify: `wl_preproc/timebase/segments.py`: `NO_STROBE_ANCHOR = "no_strobe_anchor"`, with a comment in the
  neighbours' style, in `REJECTION_REASONS` and its docstring.
- Modify: `wl_preproc/schema/core.py`: `Segment.make`'s NI branch.
- Test: `tests/schema/test_intan_by_strobes.py` (create; a module fixture in `test_ni_clock.py`'s `landed`
  pattern)

**Interfaces:**
- Consumes: `place_intan`, `IntanPlacement`, `Unanchored` (Task 4); `fit_pairs_across` and `fit_offset_to_pairs`
  (Task 2).
- **Behaviour under the NI, for `system == "rhs"`:**
  - `SystemTimebase` sets `time_source='strobes'`. It runs `place_intan` on each scan's path, and the recordings
    that anchor are fitted by `fit_pairs_across`.
  - On success: `fit_status='fitted'`, with the fit's columns, `n_strobes_matched` the total number of pairs, and
    `n_barcodes_decoded` and `n_barcodes_matched` 0.
  - With none anchored: `unfittable`, with the fit columns and `n_strobes_matched` null.
  - Every other non-SpikeGLX system is as in 3b-1 (`none`, `unfittable`).
- **`Segment.make`** under the NI, for `rhs`, on each scan:
  - It calls `place_intan`. `Unanchored` rejects the file as `NO_STROBE_ANCHOR`; this is checked before `rate is
    None`.
  - Otherwise `offset = fit_offset_to_pairs(placement.pairs, rate)`, giving:
    - `start_s = offset.offset_s`
    - `end_s = scan.duration_s / rate.scale + offset.offset_s`
    - `first_sample = placement.first_sample`
    - `offset_s` and `residual_us` from the fit
    - `n_barcodes = 0`
    - the frame-gap columns as today
  - The sync-box branch is untouched.

- [ ] **Step 1: Write the failing tests.** The fixture lands these sessions with `daemon.run_once` and removes
  them at teardown:
  - `ni_intan`: `NI_INTAN_RECIPE`
  - `no_strobe`: the same, with `_clear_intan_strobes` before landing
  - `two`: the same, with `rhs/` split into `rhs/a/` and `rhs/b/` copies, and `b`'s strobes cleared
  - `synced`: `NI_INTAN_RECIPE` with `systems=("syncbox", "spikeglx", "rhs")` and no waits

```python
def test_intan_is_fitted_to_the_ni_by_its_strobes(landed):
    row = (timebase.SystemTimebase & landed.key("ni_intan") & {"system": "rhs"}).fetch1()
    assert (row["fit_status"], row["time_source"]) == ("fitted", "strobes")
    assert row["n_strobes_matched"] == len(landed.truth("ni_intan").code_words)
    relative_ppm = ((1 - 40e-6) / (1 + 18e-6) - 1) * 1e6
    assert row["drift_ppm"] == pytest.approx(relative_ppm, abs=5.0)
    assert row["residual_us_max"] <= 100.0

def test_intans_segment_maps_every_code_onto_the_nis_time(landed):
    seg = (core.Segment & landed.key("ni_intan") & {"system": "rhs"}).fetch1()
    rate = segments.rate_fit_from_row((timebase.SystemTimebase & landed.key("ni_intan") & {"system": "rhs"}).fetch1())
    for t, _ in landed.truth("ni_intan").code_words:
        intan_s = t * (1 - 40e-6) + RHS_PRE_ROLL_S
        assert intan_s / rate.scale + seg["offset_s"] == pytest.approx(t * (1 + 18e-6) + SPIKEGLX_PRE_ROLL_S, abs=50e-6)

def test_an_ni_and_intan_session_is_tier_n(landed):
    row = (timebase.TimingProvenance & landed.key("ni_intan")).fetch1()
    assert row["tier"] == "N" and row["n_systems_aligned"] == 2 and row["n_rejected_segments"] == 0

def test_an_intan_with_no_strobe_is_left_out_and_named(landed):
    key = landed.key("no_strobe")
    assert (timebase.SystemTimebase & key & {"system": "rhs"}).fetch1("fit_status") == "unfittable"
    assert (core.RejectedSegment & key & {"system": "rhs"}).fetch1("reason") == "no_strobe_anchor"
    assert (timebase.TimingProvenance & key).fetch1("tier") == "D"

def test_of_two_intan_recordings_the_unanchored_one_is_named(landed):
    key = {**landed.key("two"), "system": "rhs"}
    assert (core.Segment & key).fetch("segment_id").tolist() == ["a"]
    assert (core.RejectedSegment & key).fetch1("file_path", "reason") == ("b", "no_strobe_anchor")

def test_under_the_sync_box_intan_is_still_fitted_by_barcodes(landed):
    row = (timebase.SystemTimebase & landed.key("synced") & {"system": "rhs"}).fetch1()
    assert (row["fit_status"], row["time_source"], row["n_strobes_matched"]) == ("fitted", "barcode", None)

def test_the_passes_report_no_error(landed):
    # `_errors_of` as `test_ni_clock.py` defines it: drop only errors naming another subject.
    for name in ("ni_intan", "no_strobe", "two", "synced"):
        assert _errors_of(landed.report, landed.key(name)) == []
```

  - The `two` session's file paths follow `Segment.make`'s `relative_to(system_dir)` rule; assert what it gives
    (`"a"`).
  - Use the module's own `landed.key`/`landed.truth` accessors, or the tuple shape `test_ni_clock.py` uses.

- [ ] **Step 2: Run them to see them fail**

  Run: `.venv/bin/python -m pytest tests/schema/test_intan_by_strobes.py -q`
  Expected: FAIL. `rhs` is `unfittable` with `time_source` `none` under the NI.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run them to see them pass, with the neighbours**

  Run: `.venv/bin/python -m pytest tests/schema/test_intan_by_strobes.py tests/schema/test_ni_clock.py tests/schema/test_timebase.py tests/schema/test_session_clock.py -q`
  Expected: all pass.

- [ ] **Step 5: Mutation checks.** Prove each fails a named test, then restore:
  - under the NI, fit `rhs` by barcodes as before: `test_intan_is_fitted_to_the_ni_by_its_strobes`;
  - check `rate is None` before `place_intan`: in the `no_strobe` session the file then reads `unfitted_system`,
    so `test_an_intan_with_no_strobe_is_left_out_and_named` fails;
  - drop `NO_STROBE_ANCHOR` from `REJECTION_REASONS`: `test_an_intan_with_no_strobe_is_left_out_and_named`.

- [ ] **Step 6: Commit**: `feat(timebase): under the NI, Intan is fitted by its strobes; one that cannot anchor is named`

---

### Task 6: The NWB file says how Intan was placed

**Files:**
- Modify: `wl_preproc/nwb/timebase.py`:
  - under `nidq`, `system_clocks` gains `time_source`;
  - the `nidq` texts `worst_residual`, `fitted_rate`, `residual_rms` and `first_sample` are rewritten.
- Modify: `wl_preproc/nwb/gather.py`: the system clocks it gathers carry `time_source`.
- Test: `tests/schema/test_intan_by_strobes.py` (append; an `ni_intan_nwb` fixture builds the file as
  `test_ni_clock.py`'s `ni_nwb` does); `tests/nwb/test_writers.py` (an NI case).

**Interfaces:**
- `add_timebase` adds the `time_source` column only when `clock == "nidq"`. Its text:
  `"How the system was placed on the NI's clock: clock, SpikeGLX on the identity map; strobes, Intan fitted by its strobe edges matched to the NI's; none, not placed."`
- **The NI texts, exactly:**
  - `worst_residual`: `"Largest residual, microseconds: under the NI, 0 for SpikeGLX on the identity map, and Intan's largest strobe residual where it was fitted."`
  - `fitted_rate`: `"The rate on the session's clock: SpikeGLX's nominal rate on the identity map; Intan's rate fitted against the NI's strobes; NaN for a system not placed on the NI's clock."`
  - `residual_rms`: `"RMS residual, microseconds: 0 for SpikeGLX on the identity map; Intan's RMS strobe residual; NaN for a system not placed on the NI's clock."`
  - `first_sample`: `"Native index the segment's offset is anchored to: 0 for SpikeGLX, the NI's first file being session time; for Intan, the first strobe edge matched to the NI's."`

- [ ] **Step 1: Write the failing tests**:
  - `test_the_nwb_file_says_intan_was_placed_by_strobes`: the file's `processing/timebase/system_clocks` has rows
    `spikeglx`/`clock` and `rhs`/`strobes`, and the four texts above verbatim.
  - `test_an_ni_only_nwb_file_names_spikeglx_on_the_clock`: the 3b-1 NI file's `system_clocks.time_source` is
    `["clock"]`.

  `test_a_sync_box_nwb_file_is_unchanged` must keep passing unedited.

- [ ] **Step 2: Run them to see them fail**

  Run: `.venv/bin/python -m pytest tests/schema/test_intan_by_strobes.py -k nwb tests/nwb/test_writers.py -q`
  Expected: FAIL. There is no `time_source` column.

- [ ] **Step 3: Implement.** Update every test that pins the old NI texts (`git grep` each old sentence under
  `tests/`) to the new wording, and no other text.

- [ ] **Step 4: Run them to see them pass**

  Run: `.venv/bin/python -m pytest tests/nwb tests/schema/test_intan_by_strobes.py tests/schema/test_ni_clock.py -q`
  Expected: all pass.

- [ ] **Step 5: Commit**: `feat(nwb): under the NI, each system's clock says how it was placed`

---

### Task 7: Records

**Files:**
- Modify: `docs/superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md`: a new section,
  `## Amendments, 2026-11-20, made while planning part 3b-2`. It holds amendment 52, this plan's Decisions 1-8,
  one line each.
- Modify: `docs/ops/lab-host-protocol.md`: tier `N`'s line now says Intan is placed by its strobes, and that a
  session with the eye tracker or cameras under the NI is still `D`.
- Modify: `docs/pending-wl-works-amendments.md`: the "OPEN — the NI keeps time" entry gains one line. Under the
  NI, a session with Intan can now be tier N, and nothing in the listing's or the description's shape changes.
- Create: `docs/handoffs/2026-11-20-part-3b-2.md`, in 3b-1's handoff shape, with these sections:
  - What was built
  - Rulings
  - The review (`FINAL_REVIEW`)
  - Deferred minors
  - Carried to later parts
  - The suite (`SUITE_RESULTS`)
  - Deploy: nothing beyond 3b-1's; a rebuilt database
  - Told to

  `wl.yaml` and `docs/CHECKPOINT.md` wait for the pointer commit after the merge (3b-1's ruling 17).

- [ ] **Step 1: Write them.**
- [ ] **Step 2:** Check that every claim about another repository is read from that repository's own documents.
- [ ] **Step 3: Commit**: `docs: part 3b-2's amendment, wl.works' line and the handoff`

---

## Self-review

- **Coverage.**
  - Amendment 49's anchor, extension, fit, ambiguity and refusal: Tasks 3-5.
  - §6.2's Intan line: Task 5.
  - §6.4's "left out and named": Task 5.
  - §7's device map with its source: Task 6.
  - §8's "Intan fitted by strobes … tier N, an NWB file": Tasks 5 and 6.
  - Amendment 51's "Intan for 3b-2": Task 5's docstring and Task 7.
- **Types.**
  - `IntanPlacement.pairs` is `(session_s, device_s)`, the shape `fit_pairs_across` takes and `_matched_pairs`
    returns.
  - `fit_offset_to_pairs` takes the `RateFit` that `segments.rate_fit_from_row` rebuilds.
- **Not covered, on purpose.**
  - Lab time's Intan (§6.3): part 4.
  - The partial sync box: 3b-3.
  - An NI restart: 3c.
