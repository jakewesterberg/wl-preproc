# Scaling to Long Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a 12-hour session processed in no longer than it took to record, every result unchanged, with BMD off by
default.

**Architecture:** each hot spot is fixed in the function that has the cost today. Today's implementation is copied
verbatim into a test-only oracle, and the new one must equal it. A ratio test (time at `n` against `4n`) holds each
fix at linear, and a `tracemalloc` test holds each memory fix flat. BMD leaves the default set through one registry
function that every consumer reads.

**Tech Stack:** Python 3.11 and 3.13, numpy (`np.memmap`, `np.diff`, `np.searchsorted`), PyYAML's `CSafeLoader`,
torch (U'n'Eye's vendored UNet), DataJoint, pytest.

**Spec:** `docs/superpowers/specs/2026-11-14-scaling-long-sessions-design.md`, including its amendments of
2026-11-16, which this plan's proofs produced and which override the sections they name.

## Global Constraints

- Results identical: labels, runs, edges, words, alignments and counts `==`. U'n'Eye's run reliability is compared
  to 1e-5 (amendment 4); nothing else gets a tolerance.
- No table changes, no `ALTER`, nothing stored that is not stored today.
- Today's code is copied verbatim into `tests/scaling/_oracles.py`, test-only, never imported by `wl_preproc`.
- Scaling test: the best of 3 timings at `n` and at `4n`, ratio `<= 6`. Memory test: `tracemalloc` peak at `4n`
  `<= 1.5x` the peak at `n`. Sizes are chosen so the `4n` run takes about a second.
- The suite grows by under a minute.
- Compare floats that pass through sin, cos or atan2 to a tolerance (Linux CI rounds otherwise).
- 48 GB per session is a target, not a cap: an overrun goes to the requester as a question and is never fixed by
  changing a result.
- Suite command (once, at the end): see Task 9 Step 4. Targeted tests during tasks.

## Review Focus

1. **A cached array written into by a caller.** The eye file's columns are shared once cached; a caller that changes
   one in place would change the next caller's input. Expected: such a write fails loudly. Pinned by Task 1's
   `test_cached_columns_are_read_only`.
2. **An edge exactly on a chunk boundary, and a file whose first sample is high.** Expected: each edge found once,
   and the tick-0 edge always present, as wl-sync's `edges_from_samples` gives. Pinned by Task 2's boundary tests.
3. **A U'n'Eye stretch whose length is not a multiple of 25, or shorter than one window.** Expected: U'n'Eye's own
   tail rule, and labels equal to the single call. Pinned by Task 3's `test_windowed_equals_whole_at_odd_lengths`.
4. **A code stream whose trial-number escape loses a word.** Expected: that anchor goes unpaired, the two segments
   merge, and the count equals the whole-stream count. Pinned by Task 5's `test_a_damaged_anchor_merges_segments`.
5. **A database that registered BMD before this change.** Expected: its rows stay, no new BMD row is computed
   unasked, and the NWB file neither waits for nor holds BMD. Pinned by Task 8's
   `test_an_old_bmd_paramset_is_not_run_unasked`.

---

### Task 1: The eye file and the camera sidecar, read once per process

**Files:**
- Modify: `wl_preproc/eye/ohdpi.py:98-127` (`read_columns`)
- Modify: `wl_preproc/contracts/sidecar.py:108-110` (`from_yaml`), add `read_sidecar`
- Modify: `wl_preproc/timebase/extract.py:340`, `wl_preproc/schema/timebase.py:705` (call `read_sidecar`)
- Create: `tests/scaling/__init__.py`, `tests/scaling/_oracles.py`, `tests/scaling/_bounds.py`
- Test: `tests/scaling/test_read_once.py`

**Interfaces:**
- Produces: `tests/scaling/_bounds.py::time_ratio(fn: Callable[[int], object], n: int) -> float` (best of 3 at `n`
  and `4n`) and `peak_ratio(fn, n) -> float` (`tracemalloc` peaks), used by every later task.
- Produces: `wl_preproc.contracts.sidecar.read_sidecar(path: Path) -> BehaviorCameraSidecar`, cached on
  `(path, mtime_ns, size)`, `functools.lru_cache(maxsize=4)` behind it; `read_sidecar.cache_clear` exposed for tests.
- Produces: `read_columns` unchanged in signature; behind it, `_columns_cache(path, mtime_ns, size) -> dict[str,
  np.ndarray]` holding one file (`maxsize=1`), filled with the columns asked for and missing so far.

- [ ] **Step 1: Write the failing tests** in `tests/scaling/test_read_once.py`:
  - `test_columns_are_parsed_once`: patch `pandas.read_csv` with a counting wrapper; three `read_columns` calls for
    overlapping column sets on one synthetic ohDPI file (`wl_preproc.synth.ohdpi`) → arrays `==` to the oracle's
    (`_oracles.read_columns`, today's body), and `read_csv` called only for columns not yet read (1 then 1 then 0 for
    the sets `[a, b]`, `[b, c]`, `[a, c]`).
  - `test_a_changed_file_is_read_again`: rewrite the file (new size), call again → fresh values.
  - `test_cached_columns_are_read_only`: `read_columns(...)[name][0] = 0` raises `ValueError`.
  - `test_sidecar_loaders_agree`: for every sidecar under `tests/fixtures/` and one synthetic 12-hour sidecar
    (2.6M-frame `digital_line`), `read_sidecar(p) == _oracles.sidecar_from_yaml(p.read_text())`.
  - `test_sidecar_uses_libyaml`: `yaml.__with_libyaml__` implies the loader used is `yaml.CSafeLoader` (patch and
    record).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/scaling/test_read_once.py -q`. Expected: FAIL (read_csv is
  called on every call; `read_sidecar` is not defined).
- [ ] **Step 3: Implement.** `read_columns` stats the file, takes the cache dict for `(path, mtime_ns, size)`, reads
  only `[c for c in columns if c not in cached]` with today's `read_csv` call and missing-header handling, sets
  `flags.writeable = False` on each new array, returns `{c: cached[c] for c in columns}`. `from_yaml` loads with
  `yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))`. `read_sidecar` reads the text and calls
  `from_yaml`. Replace both `from_yaml(path.read_text(...))` callers with `read_sidecar(path)`.
- [ ] **Step 4: Run** the new tests and `tests/eye tests/contracts tests/timebase -q`. Expected: PASS. If any
  existing caller writes into a returned array, that test fails here: copy at that caller and say so in the ledger.
- [ ] **Step 5: Commit** `perf(eye,contracts): the eye file's columns and the camera sidecar are read once per
  process -- cached on the file's identity, arrays read-only, the sidecar by libyaml (spec 4.4, 4.7, amendment 2)`.

### Task 2: NI and Intan edges and words from one memory-mapped channel

**Files:**
- Create: `wl_preproc/timebase/channels.py`
- Modify: `wl_preproc/timebase/extract.py:123-140` (`_edges_from_bit`), `:167-206` (`extract_rhs`), `:210-256`
  (`extract_spikeglx`)
- Modify: `wl_preproc/events/extract.py:89-165` (`extract_nidq_words`, `extract_rhs_witness`)
- Test: `tests/scaling/test_channels.py`

**Interfaces:**
- Produces, in `wl_preproc/timebase/channels.py`:
  - `CHUNK_ROWS = 8_000_000`
  - `column_chunks(path: Path, n_channels: int, column: int, dtype, chunk_rows: int = CHUNK_ROWS) ->
    Iterator[np.ndarray]`: `np.memmap(..., mode="r")` reshaped to `(-1, n_channels)`; each chunk a copy of one
    column. Raises today's `ValueError` text when the file size does not divide into `n_channels` samples.
  - `n_rows(path: Path, n_channels: int, dtype) -> int`
  - `bit_edges(chunks: Iterable[np.ndarray], bit: int, fs_hz: float) -> tuple[tuple[int, int], ...]`: equal to
    `wl_sync.barcode.edges_from_samples(((words >> bit) & 1).tolist(), fs_hz)`: index 0 always an edge, then each
    index where the level changes, carried across chunks; tick `round(index / fs_hz * 1_000_000)` computed as
    `np.rint(idx / fs_hz * 1_000_000).astype(np.int64)` (the same IEEE operations, half to even like `round`).
  - `strobe_falls(control_chunks, data_chunks, bit) -> tuple[tuple[int, int], ...]` and
    `strobe_rises(chunks, bit) -> tuple[int, ...]`: today's `falling`/`rising` rules, across chunks.

- [ ] **Step 1: Write the failing tests:** `test_bit_edges_equal_wl_sync` (seeded random traces, run lengths 1 to
  50, `chunk_rows` in `{1, 7, 64, len}`, first sample low and high), `test_an_edge_on_a_chunk_boundary_is_found_once`,
  `test_words_and_witness_equal_today` (a synthetic SpikeGLX and Intan session from `wl_preproc.synth`;
  `extract_spikeglx`, `extract_rhs`, `extract_nidq_words`, `extract_rhs_witness` `==` the `_oracles` copies),
  `test_a_truncated_file_still_refuses` (today's message), `test_edges_scale_linearly` (`time_ratio <= 6`),
  `test_edges_memory_is_flat` (`peak_ratio <= 1.5` at a `chunk_rows` far below `n`).
- [ ] **Step 2: Run** `tests/scaling/test_channels.py`. Expected: FAIL (`channels` does not exist).
- [ ] **Step 3: Implement** `channels.py`, then make the four extractors read through it. The size checks use
  `n_rows`; `n_samples` in each `BitStream` is `n_rows`.
- [ ] **Step 4: Run** the new tests and `tests/timebase tests/events -q`. Expected: PASS.
- [ ] **Step 5: Commit** `perf(timebase,events): NI and Intan edges and words from one memory-mapped channel in
  chunks, equal to wl-sync's edges_from_samples (spec 4.1; 4.3 by amendment 3)`.

### Task 3: U'n'Eye in windows on its pooling grid

**Files:**
- Modify: `wl_preproc/eye/detect/uneye.py:127-175` (`detect_uneye`); add `_predict_windowed`
- Test: `tests/scaling/test_uneye_windows.py` (skipped where torch is unavailable, as `tests/eye/detect`'s U'n'Eye
  tests are)

**Interfaces:**
- Produces: `_predict_windowed(network, x: np.ndarray, y: np.ndarray, window: int = 50_000, overlap: int = 500)
  -> tuple[np.ndarray, np.ndarray]`, returning what `network.predict(x, y)` returns for one 1-D stretch.
  Constants `WINDOW_SAMPLES = 50_000`, `OVERLAP_SAMPLES = 500` (both multiples of `mp ** 2 = 25`).

- [ ] **Step 1: Write the failing tests:** `test_windowed_equals_whole` (seeded saccadic random walks of 60,013 and
  250,000 samples: labels `==`, probabilities within 1e-5 of `network.predict`),
  `test_windowed_equals_whole_at_odd_lengths` (lengths 25, 49, 50,000, 50,001, 100,024),
  `test_the_overlap_covers_the_receptive_field` (perturb one input sample mid-stretch; changed outputs lie within
  ±`OVERLAP_SAMPLES`, measured today at ±89), `test_detect_uneye_runs_unchanged` (`detect_uneye` against
  `_oracles.detect_uneye` on the reference recording where `WLPP_OHDPI_REFERENCE` is set, and on a synthetic stretch:
  runs' bounds `==`, reliability within 1e-5), `test_uneye_memory_is_flat` (`peak_ratio <= 1.5`).
- [ ] **Step 2: Run** them. Expected: FAIL (`_predict_windowed` is not defined).
- [ ] **Step 3: Implement** `_predict_windowed`. It loads the weights once into the vendored `UNet`, builds U'n'Eye's
  own input for the whole stretch (`np.diff`, leading zero, its inf and NaN rules, as `classifier.py:384-403`), runs
  the network under `torch.no_grad()` on windows `[start - overlap, stop + overlap)` clipped to
  `[0, first)` with `first = n // 25 * 25`, keeps each window's `[start, stop)`, evaluates the last 25 samples alone
  for the tail as `classifier.py:430-437` does, then calls `binary_prediction` on the stitched output with the
  network's `min_sacc_dist` and `min_sacc_dur` as `classifier.py:446-449` does. `detect_uneye` calls it in place of
  `network.predict`.
- [ ] **Step 4: Run** the new tests and `tests/eye -q -k uneye`. Expected: PASS.
- [ ] **Step 5: Commit** `perf(eye): U'n'Eye predicts each stretch in windows on its 25-sample pooling grid, with
  500 samples of overlap; labels equal, reliability within 1e-5 (spec 4.2, amendment 4)`.

### Task 4: The binocular conjunction in one pass

**Files:**
- Modify: `wl_preproc/schema/detect.py:1318-1325` (the nested loop in `_overlapping`)
- Test: `tests/scaling/test_conjunction.py`

**Interfaces:**
- Produces: `_intersections(left: list[Run], right: list[Run], floor: int) -> list[tuple[int, int]]` in
  `schema/detect.py`, the same list (as a set) the nested loop builds.

- [ ] **Step 1: Write the failing tests:** `test_sweep_equals_the_nested_loop` (seeded random disjoint sorted runs per
  eye, including touching and identical runs, floors 1, 3, 6: `_overlapping` `==` `_oracles.overlapping`),
  `test_overlapping_runs_take_the_nested_loop` (one eye's runs overlap each other: still `==`),
  `test_conjunction_scales_linearly` (`time_ratio <= 6` at 2,000 runs per eye).
- [ ] **Step 2: Run** them. Expected: FAIL on the scaling test (14 s at 8,000 today).
- [ ] **Step 3: Implement** `_intersections`: when both lists are sorted by `start` and each run's `start` is at or
  after the previous run's `stop`, a two-pointer sweep (advance whichever run ends first); otherwise today's nested
  loop. `_overlapping` sorts and coalesces as today.
- [ ] **Step 4: Run** the new tests and `tests/schema -q -k "conjunction or overlap or detect"`. Expected: PASS.
- [ ] **Step 5: Commit** `perf(schema): the binocular conjunction in one pass where both eyes' runs are sorted and
  disjoint, today's loop otherwise (spec 4.5, amendment 5)`.

### Task 5: The code agreement anchored on trial numbers

**Files:**
- Modify: `wl_preproc/events/agreement.py:93-135` (`code_agreement`); add `_anchors`, `_matched`
- Test: `tests/scaling/test_code_agreement.py`

**Interfaces:**
- Produces: `_anchors(codes: Sequence[int]) -> list[tuple[int, tuple]]`: `(index, (run_payload, trial_payload))`
  for each `Escape.TRIAL_NUMBER`, found by walking with `PAYLOAD_WORD_COUNTS`. `code_agreement`'s signature and
  return unchanged: matched over `max(len(reference), len(other))`.

- [ ] **Step 1: Write the failing tests:** `test_anchored_equals_whole` (synthetic sessions' Pi and NI streams from
  `wl_preproc.synth`, seeds 0 to 11: drop 0, 1, 10 or 50 words and insert 0 or 5 random words in either stream;
  `code_agreement == _oracles.code_agreement`), `test_a_damaged_anchor_merges_segments` (delete one word of one
  `TRIAL_NUMBER` payload: still `==`), `test_a_payload_word_equal_to_an_escape_is_not_an_anchor` (a trial number
  whose low word is `0x8006`), `test_out_of_order_anchors_align_the_whole_stream` (swap two trials in `other`:
  `==` the oracle), `test_code_agreement_scales_linearly` (`time_ratio <= 6` at 10,000 words), and the existing
  `tests/events/test_agreement.py` unchanged.
- [ ] **Step 2: Run** them. Expected: FAIL on the scaling test.
- [ ] **Step 3: Implement** as amendment 1 says: pair keys that occur once in each stream, in `reference` order;
  if their indices in `other` do not increase, return the whole-stream result; else sum `_matched` over the
  segments between `(0, 0)`, the pairs, and `(len(reference), len(other))`. Keep the docstring's `autojunk` paragraph
  and add one on the anchors.
- [ ] **Step 4: Run** the new tests and `tests/events -q`. Expected: PASS.
- [ ] **Step 5: Commit** `perf(events): the Pi-vs-NI code agreement aligned between trial-number anchors, the
  whole stream only where they are out of order (spec 4.6, amendment 1)`.

### Task 6: Detector agreement as array operations

**Files:**
- Modify: `wl_preproc/eye/detect/consensus.py:104-123` (`comparison_mask`), `:139-149` (`_event_starts`)
- Test: `tests/scaling/test_consensus.py`

**Interfaces:** signatures unchanged.

- [ ] **Step 1: Write the failing tests:** `test_comparison_mask_equals_today` and `test_event_starts_equal_today`
  (seeded traces drawn from `Label` values, every registered detector's vocabulary pair, `pso_as` both ways, masks
  random: `==` `_oracles`), `test_agreement_scales_linearly` (`time_ratio <= 6` at 200,000 samples).
- [ ] **Step 2: Run** them. Expected: FAIL on the scaling tests.
- [ ] **Step 3: Implement.** `comparison_mask`: `np.unique(..., return_inverse=True)` on each trace, `comparable`
  evaluated once per distinct value, gathered back by the inverse. `_event_starts`: `is_event = mask.astype(bool) &
  np.isin(labels, list of _EVENT_LABELS values)`; starts where `is_event` and not the previous sample's, as a list of
  ints.
- [ ] **Step 4: Run** the new tests and `tests/eye tests/schema -q -k "consensus or agreement or quality"`. Expected:
  PASS, scores unchanged.
- [ ] **Step 5: Commit** `perf(eye): detector agreement compares label codes as arrays, each distinct label judged
  once (spec 4.8)`.

### Task 7: The NWB eye events trimmed by search

**Files:**
- Modify: `wl_preproc/nwb/trim.py:31-48` (`contains`, `contains_instant`)
- Modify: `wl_preproc/nwb/gather.py:552-556` (one `contains` over every row's start instead of one per row)
- Test: `tests/scaling/test_trim.py`

**Interfaces:** signatures unchanged.

- [ ] **Step 1: Write the failing tests:** `test_contains_equals_today` and `test_contains_instant_equals_today`
  (random merged block sets; times on starts, on ends, inside, outside, NaN: `==` `_oracles`),
  `test_trim_scales_linearly` (`time_ratio <= 6` at 5,000 blocks x 50,000 times), and the NWB gather tests unchanged.
- [ ] **Step 2: Run** them. Expected: FAIL on the scaling test.
- [ ] **Step 3: Implement** with `np.searchsorted(starts, times, side="right") - 1` and the end test (`<` or `<=`).
  The intervals are merged and sorted by `BlockSet.of`.
- [ ] **Step 4: Run** the new tests and `tests/nwb -q`. Expected: PASS.
- [ ] **Step 5: Commit** `perf(nwb): an activation's blocks hold an event by binary search, every run's start
  tested at once (spec 4.9)`.

### Task 8: BMD off by default

**Files:**
- Modify: `wl_preproc/eye/detect/registry.py` (add `OPT_IN`, `request`, `running_detectors`)
- Modify: `wl_preproc/schema/detect.py:1891-1928` (`register_default_paramsets`), `:554-582`
  (`EyeDetection.key_source`)
- Modify: `wl_preproc/schema/consensus.py:490-511` (`_live_detectors`)
- Modify: `wl_preproc/cli/main.py:181-210` (`daemon --with-detector`, repeatable, choices `sorted(OPT_IN)`) and its
  dispatch
- Modify: `wl_preproc/cli/report.py:1145, 2137` (heading "Agreement across the detectors that ran per session per eye
  (24 h)"), `wl_preproc/nwb/eye_events.py:46` ("Each detector that ran's events ...")
- Test: `tests/schema/test_opt_in_detectors.py`; update tests asserting set equality with `DETECTORS`

**Interfaces:**
- Produces: `registry.OPT_IN: frozenset[str] = frozenset({"bmd"})`;
  `registry.request(names: Iterable[str]) -> None` (refuses a name not in `OPT_IN`; process-level);
  `registry.running_detectors() -> dict[str, Detector]` (`DETECTORS` minus unrequested `OPT_IN`, in `DETECTORS`'
  order); `registry.clear_requests()` for tests.

- [ ] **Step 1: Write the failing tests:** `test_the_default_set_leaves_bmd_out`, `test_requesting_bmd_runs_it`
  (after `request(["bmd"])`, `register_default_paramsets()` names it and `_live_detectors()` blends it),
  `test_an_old_bmd_paramset_is_not_run_unasked` (register BMD's paramset as today's code did, then call
  `EyeDetection.key_source` unrequested: no key with that paramset; `nwb.gather._paramsets()` omits it),
  `test_an_unknown_request_is_refused`, `test_daemon_takes_with_detector` (the parser), and a report test for the
  new heading.
- [ ] **Step 2: Run** them. Expected: FAIL (`running_detectors` is not defined).
- [ ] **Step 3: Implement.** `register_default_paramsets` and `_live_detectors` iterate `running_detectors()`.
  `EyeDetection.key_source` restricts the paramsets to those whose `params["detector"]` is not in
  `OPT_IN - requested`, computed in Python from `ParamSet & {"paramset_type": "eye_detection"}` and applied as
  `paramset_idx IN (...)`. The daemon calls `registry.request(args.with_detector or [])` before `run_once`. Update
  the docstrings that say "every registered detector" where they mean what runs.
- [ ] **Step 4: Run** the new tests and `tests/schema tests/nwb tests/cli -q`. Expected: PASS; tests that asserted
  equality with `DETECTORS` now assert it with `running_detectors()`, each change noted in the ledger.
- [ ] **Step 5: Commit** `feat(eye): BMD is off by default -- registry.running_detectors() is what runs and what the
  NWB file reads, BMD by the daemon's --with-detector bmd; an old BMD paramset computes nothing unasked (spec 5,
  amendment 6)`.

### Task 9: The 2- and 12-hour runs, the suite, and the records

**Files:**
- Scratch only (never committed): the spike harness, copied from
  `~/.cache/wl-preproc-scratch/2026-10-30-scaling/spike/test_zz_scaling_spike.py` into the worktree's `tests/` for
  the runs and removed after.
- Create: `docs/handoffs/2026-11-16-scaling-long-sessions.md`
- Modify: `docs/CHECKPOINT.md`, `wl.yaml` (status; BMD's speed as a future task)

- [ ] **Step 1: The 2-hour run.** With load under 40: `SPIKE_MINUTES=120 SPIKE_TAG=120m-after` through the
  harness. Compare with `compare.py run-120m-b run-120m-after`. Expected: every stage at or below its 2026-10-31
  time; no stage over 16 GB; `TimingProvenance`'s peak recorded (amendment 3).
- [ ] **Step 2: The 12-hour run.** `SPIKE_MINUTES=720 SPIKE_TAG=720m SPIKE_CAP_S=43200`, its data (~42 GB) deleted
  after. Expected: total wall time under 12 hours; eye detection about an hour; no stage over 16 GB and the session
  under 48 GB. **A miss is reported to the requester, naming the stage, before the merge question.** Record the
  per-trial database stages' time (spec 4.10): over about ten minutes, ask whether to add batching here.
- [ ] **Step 3: Write the records:** the handoff (what was built, both runs' tables, the amendments, the review), the
  checkpoint's and `wl.yaml`'s lines, and BMD's future task (vectorize `bmd.py::_grid_up`, batch the calls into
  `_sweep`). Run `.venv/bin/wl-check`. Expected: no findings.
- [ ] **Step 4: The full suite on both interpreters**, load under 40, nothing else running on the checkout:
  `env PYTHONPATH=$PWD PYTHONDONTWRITEBYTECODE=1 WLPP_NSLR_REFERENCE=$R WLPP_BMD_REFERENCE=$R/bmd/BMD
  WLPP_BMD_BOOST_INCLUDE=$R/bmd/boost_1_86_0 WLPP_ANDERSSON_DATA=$R/andersson` with `R=~/.cache/wl-preproc-references`
  and `WLPP_OHDPI_REFERENCE` unset, `.venv/bin/python` and `~/.cache/wl-preproc-venv313/bin/python`, each `-m pytest
  -q -p no:cacheprovider -o faulthandler_timeout=1800`. Expected: 2815 passed plus this branch's new tests on each
  (count them with `--collect-only` against main), skipped 26 on 3.11 (1 deselected) and 27 on 3.13, no errors.
- [ ] **Step 5: Commit** `docs: the scaling piece's records -- both runs, the amendments, the review; BMD's speed a
  future task`.
