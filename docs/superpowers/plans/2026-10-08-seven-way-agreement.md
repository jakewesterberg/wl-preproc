# Seven-Way Detector Agreement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** score every registered detector's agreement on each detection trace with Krippendorff's α, store it beside the pairwise agreement rows, and show each session's score against the same animal's earlier sessions in the daily report.

**Architecture:**
- **Pure functions** (Task 1): `wl_preproc/eye/detect/consensus.py` gains Krippendorff's α for nominal ratings with missing values, the vocabulary every detector can be scored in, and the blend of one trace's labels from every detector, a detector abstaining on the saccades it copies. No DataJoint, so `tests/eye/` tests it alone.
- **The table** (Task 2): `DetectionQuality` in `wl_preproc/schema/consensus.py`, beside `DetectorAgreement`. One key per session and validity paramset; a row per trace, metric, vocabulary and glissade convention, written only for a trace every registered detector the code has computed. The daemon runs it after `DetectorAgreement`.
- **The report** (Task 3): `cli/report.py` gains "Seven-way agreement per session per eye (24 h)", the history computed when the report is built.
- **The noise check** (Task 4): a validation test on the lab's reference recording, gated on `WLPP_OHDPI_REFERENCE`.
- **The records** (Task 5): the spec's amendments 1–4, the handoff, the checkpoint, `wl.yaml`, and the full suite.

**Tech Stack:** Python ≥3.11; numpy; DataJoint 2.3 with MySQL; pytest. No dependency is added.

**Spec:** `docs/superpowers/specs/2026-10-08-seven-way-agreement-design.md` (`a9a47a0`), an addendum to `docs/superpowers/specs/2026-08-31-saccade-detection-design.md` §6 and §7. It is binding. The requester approved the design in chat, then the written spec ("Approved, write the plan"), on 2026-10-08. Task 5 adds its dated amendments 1–4, which record rulings 1, 2, 4 and 5 below.

**Every piece of code below was proven before this plan was written.** It was built on a scratch branch, `proof/seven-way`, from `spec/seven-way-agreement` at `a9a47a0`.
- **Each task's failing run** was measured on the earlier tasks' files plus this task's tests, in a worktree that took each task's files from the proof's final commit in order.
- **Its passing runs** were measured on the same worktree with the task's code added.
- **Every mutation check named here** was run against the final tree, one at a time, and each failed the tests named beside it.
- **The full suite** was run on both interpreters with every task applied (Task 5 quotes it).
- **The plan replays exactly:** its diffs, applied in order to `a9a47a0` (and this plan's own commit, which touches nothing else), reproduce the proof's final tree.

## Global Constraints

- **The spec is binding,** including the dated amendments Task 5 adds. Where it and this plan disagree, the spec wins; record a ruling.
- **The blended score sits beside the pairwise rows, never instead of them** (spec, "Why it exists"). Nothing here changes `DetectorAgreement`.
- **The history is never stored.** The report computes it; the table stores each session's own score.
- **`tests/eye/` imports nothing from `wl_preproc.schema`.** The pure functions import no DataJoint, and Task 4's test repairs gaze and runs each detector as `schema/detect.py` does, restated.
- **Each new database test takes its animal from `tests/identities.py`** (`new_animal().session()`), never a hand-written subject or date.
- **A planted `EyeDetection` row's runs tile its trace,** as a real detection's do: a later daemon pass in the suite scores planted pairs in `DetectorAgreement`, and an untiled trace makes that pass fail.
- **A test that registers a paramset no production code registers deletes it afterwards,** so no later daemon pass in the suite computes for it.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code, data or tables.
- **Leave the stray symlink `wl-preproc` at the repository root alone,** and keep it out of commits.
- **Tests.**
  - Run with `.venv/bin/python -m pytest <files> -q -p no:cacheprovider` from the repository root. In a git worktree, prefix `PYTHONPATH=$PWD`.
  - Database tests need Docker (OrbStack on this machine: `open -a OrbStack` after a reboot). A module-wide setup error on a 120 s container timeout is the known flake: re-run it.
  - `test_detection_quality_populate.py` builds `test_detect_populate.py`'s stepped session and runs it through the daemon, and its last test runs a real `wlpp daemon` in a subprocess: about 80 s.
  - The reference recording has a copy at `~/.cache/wl-preproc-references/OpenIris-2024Jul31-114628.txt`; the original under `~/Downloads` is not readable from this session.
  - Mutation checks:
    - clear `__pycache__` first, and again after restoring;
    - run with `PYTHONDONTWRITEBYTECODE=1`;
    - make one mutation at a time, and restore the file afterwards.
  - **Run each task's own test files.** The full suite runs once, on both interpreters, in Task 5.
  - The shell is zsh:
    - an unquoted `$VAR` holding several arguments is not split;
    - a pipe through `tail` or `grep` hides the test command's exit status, so gate on the command's own status.
- **`wl-check` after `wl.yaml` changes** (Task 5).
- **Every commit message ends with** the trailer lines the harness gives the executing session.
- **Date what Task 5 records** (the checkpoint's and `wl.yaml`'s lines, the handoff) with the day the plan is executed; this plan proved them on 2026-10-10.

## Review Focus

Five inputs a real session can bring that the spec implies but its §7 does not list, most likely first. Each is pinned by a test named here, in the task that owns the code.

1. **A detector that never finishes on a session,** its job errored or unable to run on this machine: no trace is blended from the others, and the report says "not computed yet" rather than a figure over six. Task 2: `test_a_trace_is_blended_only_when_every_registered_detector_computed_it` (mutation Q3); Task 3: `test_a_detection_without_its_rows_is_not_computed_yet`.
2. **A session with no saccades at all,** every detector labelling the whole trace fixation: the score is undefined and stored as NULL, never 1.0. Task 2: `test_a_trace_with_no_saccades_is_undefined_and_stored_as_null` (Q11).
3. **A detector removed from the code** while its paramset stays registered, perhaps with detections it computed before: neither waited for nor blended. Left alone, it would stop the table for every later session. Task 2: `test_a_paramset_for_a_detector_the_code_no_longer_has_is_not_waited_for` (Q9, Q10).
4. **Earlier sessions scored differently:** by another set of detectors (after one is added), under another validity paramset, in another vocabulary or metric. Each is left out of the history. Task 3: `test_the_history_is_earlier_sessions_scored_alike` (R1, R2, R3, R14).
5. **Tracking noise the validity mask does not catch:** the score falls while the mask keeps the same samples. That is what makes it a data-quality signal (spec §1.3). Task 4: `test_noise_the_mask_keeps_lowers_the_seven_way_score` (N1).

## The rulings this plan carries

The first two, the fourth and the fifth are dated amendments Task 5 adds to the spec.
- **1 (Task 2): one key per session and validity paramset.** DataJoint keys a job queue on the primary-key attributes a table inherits, and `trace` is this table's own, so `key_source` collapses it with `dj.U`, as `EyeDetection.key_source` collapses `eye`; one `make()` writes every complete trace. No trace is stranded, since `EyeDetection.make()` writes all three traces of a detector at once. But a session once blended is never revisited: a detector registered later gives older sessions no new row (amendment 1).
- **2 (Task 2): only paramsets whose detector the code has are waited for, and blended** (`_live_detectors`, amendment 4). The full suite found it: `test_detect_populate.py` leaves a paramset registered for a detector it removes, and counting it stopped the table for every later session. The NWB stage met the same paramset and no longer waits on it either.
- **3 (Task 2): the planted tests register the default detectors themselves.** Run alone, no daemon pass has, and a test planting one detection per registered detector would plant none and pass for nothing.
- **4 (Task 3): the history also matches `detectors`** (amendment 2). Leaving one of the seven out moved the reference recording's α by as much as 0.035 (spec §1.2).
- **5 (Task 3): "no history yet, N earlier" stands in the parentheses beside the session's own score** (amendment 3), which is shown either way.
- **6 (Task 3): one line per session, eye, validity paramset and metric; the line does not name the metric.** `BLENDED_METRICS` holds one; a second would need its name on the line. The spec's example line names none.
- **7 (Task 3): `test_detect_report.py`'s events-count test reads its own subsection.** It looked for the session's one line across the whole Detection section; the vigor subsection already named the session once any main-sequence paramset was registered, and the seven-way subsection always does.
- **8 (Task 4): the noise test gives each detector `Detector.defaults`,** which is what its registered paramset holds: the shared `microsaccade_max_deg` equals every detector's own default today. It repairs the gaze as production does, takes the first ten minutes of the left eye, gives the mask no frame gaps (the recording has none, and the test asserts it), and asks for a drop of more than 0.05 with the mask's offered share within 0.001. Measured: 0.6845 with no noise, 0.5936 under 0.05°; at 0.01° the test fails.
- **Measured, not pinned:** on the reference recording's stored labels (1.15 million samples per eye, seven detectors, about 92,000 runs), rebuilding each detector's labels and both conventions' blends takes under a second per eye beyond the database reads, and gives exactly the spec's §1.1 figures (0.6152–0.6285 left, 0.6212–0.6309 right).

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/eye/detect/consensus.py` | `blended_vocabulary`, `krippendorff_alpha`, `Blended`, `blended_agreement`, `BLENDED_METRICS` (Task 1) |
| `wl_preproc/schema/consensus.py` | `_live_detectors` and `DetectionQuality` (Task 2) |
| `wl_preproc/daemon.py` | the table as a daemon stage (Task 2) |
| `wl_preproc/cli/report.py` | `_QUALITY_MIN_HISTORY`, `_quality_lines` and the new subsection (Task 3) |
| `tests/eye/detect/test_blended_agreement.py` (new) | the pure functions (Task 1) |
| `tests/schema/test_detection_quality_populate.py` (new) | the table, end to end and on planted rows (Task 2) |
| `tests/cli/test_quality_report.py` (new) | the report's lines (Task 3) |
| `tests/cli/test_detect_report.py` | the events-count test reads its own subsection (Task 3) |
| `tests/eye/detect/test_seven_way_validation.py` (new) | the noise check on the reference recording (Task 4) |
| docs, `wl.yaml` | the amendments, the handoff, the records (Task 5) |

---

### Task 1: The seven-way agreement, as pure functions

**Files:**
- Modify: `wl_preproc/eye/detect/consensus.py`
- Create: `tests/eye/detect/test_blended_agreement.py`

**Interfaces — consumes:** from the same module, `shared_vocabulary(a, b, pso_as)`, `coarsen(label, vocabulary, pso_as)`, `PSO_AS_SACCADE`, `PSO_AS_FIXATION`; `labels.Label`; `registry.DETECTORS` (the test only).

**Interfaces — produces:**
- `blended_vocabulary(vocabularies: Iterable[frozenset[Label]], pso_as: str) -> frozenset[Label]`: `shared_vocabulary` folded across them.
- `krippendorff_alpha(ratings: np.ndarray) -> float`: nominal α over a (raters × units) array of category codes, `-1` where a rater gave none; units fewer than two raters rated are left out; NaN where every rating is one value.
- `Blended(value: float | None, n_samples_compared: int, vocabulary: frozenset[Label])`, frozen.
- `blended_agreement(labels: dict[str, np.ndarray], vocabularies: dict[str, frozenset[Label]], copies: dict[str, str | None], pso_as: str, compute) -> Blended`: each detector's per-sample labels coarsened into the folded vocabulary, the mask's samples no one's rating, a detector abstaining wherever the detector it copies stored `saccade`; `value` None where `compute` gives NaN.
- `BLENDED_METRICS: dict[str, Callable[[np.ndarray], float]] = {"krippendorff_alpha": krippendorff_alpha}`.

- [ ] **Step 1: Write the failing tests.** Apply this diff:

```diff
--- /dev/null
+++ b/tests/eye/detect/test_blended_agreement.py
@@ -0,0 +1,121 @@
+"""The seven-way agreement as pure functions (design spec
+`2026-10-08-seven-way-agreement-design.md` section 4): Krippendorff's alpha,
+the vocabulary every detector can be scored in, and the copied saccades a
+detector abstains on."""
+
+from __future__ import annotations
+
+import itertools
+
+import numpy as np
+import pytest
+
+from wl_preproc.eye.detect.consensus import (
+    BLENDED_METRICS,
+    PSO_AS_FIXATION,
+    PSO_AS_SACCADE,
+    blended_agreement,
+    blended_vocabulary,
+    krippendorff_alpha,
+)
+from wl_preproc.eye.detect.labels import Label
+
+S, M, F, P, B = Label.SACCADE, Label.MICROSACCADE, Label.FIXATION, Label.PSO, Label.BLINK
+_ = -1  # a rating not given
+
+
+def test_alpha_matches_krippendorffs_own_worked_example():
+    """Krippendorff (2011), "Computing Krippendorff's Alpha-Reliability": four
+    coders, twelve units, values missing, nominal alpha 0.743."""
+    ratings = [[1, 2, 3, 3, 2, 1, 4, 1, 2, _, _, _],
+               [1, 2, 3, 3, 2, 2, 4, 1, 2, 5, _, 3],
+               [_, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, _],
+               [1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, _]]
+    assert krippendorff_alpha(np.array(ratings)) == pytest.approx(0.743, abs=0.001)
+
+
+def test_alpha_is_one_for_perfect_agreement():
+    ratings = np.tile(np.array([0, 1, 1, 0, 0, 1]), (5, 1))
+    assert krippendorff_alpha(ratings) == pytest.approx(1.0)
+
+
+def test_alpha_is_near_zero_for_raters_agreeing_by_chance():
+    rng = np.random.default_rng(0)
+    ratings = (rng.random((7, 200_000)) < 0.1).astype(int)
+    assert abs(krippendorff_alpha(ratings)) < 0.01
+
+
+def test_a_unit_only_one_rater_rated_is_left_out():
+    ratings = np.array([[0, 1, 1, 0, 1], [0, 1, 0, 0, 1], [1, 1, 1, 0, 0]])
+    single = np.array([[1], [_], [_]])
+    assert krippendorff_alpha(np.hstack([ratings, single])) == pytest.approx(krippendorff_alpha(ratings))
+
+
+def test_alpha_is_undefined_where_every_rating_is_one_value():
+    """No disagreement is expected, so alpha is 0/0: nan, never 1."""
+    assert np.isnan(krippendorff_alpha(np.zeros((3, 10), dtype=int)))
+
+
+def test_the_seven_registered_detectors_share_saccade_and_fixation_whatever_the_order():
+    from wl_preproc.eye.detect.registry import DETECTORS
+
+    vocabularies = [detector.vocabulary for detector in DETECTORS.values()]
+    for pso_as in (PSO_AS_SACCADE, PSO_AS_FIXATION):
+        for order in itertools.islice(itertools.permutations(vocabularies), 0, None, 97):
+            assert blended_vocabulary(list(order), pso_as) == frozenset({S, F})
+
+
+def _traces():
+    """Three detectors over 12 samples: `a` splits by size, `b` sees
+    glissades, `c` copies `a`'s saccades and adds microsaccades of its own.
+    The last two samples are a blink."""
+    labels = {
+        "a": np.array([F, S, S, F, F, M, F, F, S, F, B, B], dtype=object),
+        "b": np.array([F, S, P, F, F, F, F, S, S, F, B, B], dtype=object),
+        "c": np.array([F, S, S, F, M, M, F, F, S, F, B, B], dtype=object),
+    }
+    vocabularies = {"a": frozenset({S, M}), "b": frozenset({S, P, F}), "c": frozenset({S, M})}
+    return labels, vocabularies, {"a": None, "b": None, "c": "a"}
+
+
+def test_the_blend_coarsens_each_trace_and_leaves_out_the_mask():
+    """In `{saccade, fixation}`: a microsaccade is a saccade, and a glissade
+    is whichever the convention says. The blink's two samples are no one's
+    rating."""
+    labels, vocabularies, _copies = _traces()
+    no_copies = {"a": None, "b": None, "c": None}
+    for pso_as, glissade in ((PSO_AS_SACCADE, 1), (PSO_AS_FIXATION, 0)):
+        result = blended_agreement(labels, vocabularies, no_copies, pso_as, krippendorff_alpha)
+        expected = np.array([[0, 1, 1, 0, 0, 1, 0, 0, 1, 0],
+                             [0, 1, glissade, 0, 0, 0, 0, 1, 1, 0],
+                             [0, 1, 1, 0, 1, 1, 0, 0, 1, 0]])
+        assert result.vocabulary == frozenset({S, F})
+        assert result.n_samples_compared == 10
+        assert result.value == pytest.approx(krippendorff_alpha(expected))
+
+
+def test_a_detector_abstains_on_the_saccades_it_copies():
+    """`c` copies `a`'s `saccade` samples, so it is no witness there: its
+    ratings are left out on samples 1, 2 and 8, as `DetectorAgreement` leaves
+    them out of that pair."""
+    labels, vocabularies, copies = _traces()
+    result = blended_agreement(labels, vocabularies, copies, PSO_AS_SACCADE, krippendorff_alpha)
+    expected = np.array([[0, 1, 1, 0, 0, 1, 0, 0, 1, 0],
+                         [0, 1, 1, 0, 0, 0, 0, 1, 1, 0],
+                         [0, _, _, 0, 1, 1, 0, 0, _, 0]])
+    assert result.value == pytest.approx(krippendorff_alpha(expected))
+    no_copies = blended_agreement(labels, vocabularies, {"a": None, "b": None, "c": None}, PSO_AS_SACCADE,
+                                  krippendorff_alpha)
+    assert result.value != pytest.approx(no_copies.value)
+
+
+def test_an_undefined_blend_is_none():
+    labels = {"a": np.array([F, F, F], dtype=object), "b": np.array([F, F, F], dtype=object)}
+    vocabularies = {"a": frozenset({S}), "b": frozenset({S})}
+    result = blended_agreement(labels, vocabularies, {"a": None, "b": None}, PSO_AS_SACCADE, krippendorff_alpha)
+    assert (result.value, result.n_samples_compared) == (None, 3)
+
+
+def test_the_registry_names_krippendorffs_alpha():
+    assert list(BLENDED_METRICS) == ["krippendorff_alpha"]
+    assert BLENDED_METRICS["krippendorff_alpha"] is krippendorff_alpha
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_blended_agreement.py -q --tb=line -p no:cacheprovider`
Expected: 1 error during collection: cannot import name `BLENDED_METRICS` from `wl_preproc.eye.detect.consensus`.

- [ ] **Step 3: Implement.** Apply this diff:

```diff
--- a/wl_preproc/eye/detect/consensus.py
+++ b/wl_preproc/eye/detect/consensus.py
@@ -20,6 +20,7 @@ one graph does both jobs.
 
 from __future__ import annotations
 
+import math
 from collections.abc import Callable
 from dataclasses import dataclass
 
@@ -274,3 +275,89 @@ CONSENSUS_METRICS: dict[str, Metric] = {
         compute=lambda a, b, mask, _tol: cohen_kappa(a, b, mask),
     ),
 }
+
+
+# -- Seven-way agreement ------------------------------------------------------
+#
+# Design spec `2026-10-08-seven-way-agreement-design.md`: one score across
+# every registered detector, kept beside the pairwise suite and never instead
+# of it, since the pairs are what diagnose.
+
+
+def blended_vocabulary(vocabularies, pso_as: str) -> frozenset[Label]:
+    """The vocabulary every one of `vocabularies` can be scored in:
+    `shared_vocabulary` folded across them. `{saccade, fixation}` for the
+    seven registered detectors, under either convention."""
+    folded, *rest = list(vocabularies)
+    for vocabulary in rest:
+        folded = shared_vocabulary(folded, vocabulary, pso_as)
+    return folded
+
+
+def krippendorff_alpha(ratings: np.ndarray) -> float:
+    """Krippendorff's alpha for nominal ratings, `(raters, units)`, each a
+    category code from 0, or -1 for a rating not given.
+
+    `1 - D_o / D_e`, from the coincidences of the units at least two raters
+    rated; a unit rated once pairs with nothing and is left out. `nan` where
+    no disagreement is expected, every pairable rating one value: 0/0, never
+    1 (as `cohen_kappa` returns)."""
+    ratings = np.asarray(ratings)
+    present = ratings >= 0
+    rated = present.sum(axis=0)
+    pairable = rated >= 2
+    ratings, rated = ratings[:, pairable], rated[pairable]
+    categories = np.unique(ratings[ratings >= 0])
+    if len(categories) < 2:
+        return float("nan")
+    counts = np.stack([(ratings == category).sum(axis=0) for category in categories])
+    observed = float(np.sum((rated**2 - (counts**2).sum(axis=0)) / (rated - 1)))
+    per_category = counts.sum(axis=1)
+    n = float(per_category.sum())
+    return 1.0 - (n - 1.0) * observed / float(n**2 - (per_category**2).sum())
+
+
+@dataclass(frozen=True, slots=True)
+class Blended:
+    """One blended score: `value` None where the metric is undefined."""
+
+    value: float | None
+    n_samples_compared: int
+    vocabulary: frozenset[Label]
+
+
+def blended_agreement(labels, vocabularies, copies, pso_as: str, compute) -> Blended:
+    """Every detector's labels, by name, scored together with `compute`.
+
+    - **In the vocabulary all can express** (`blended_vocabulary`), each
+      trace's labels coarsened into it; a label that cannot be, `blink` and
+      `invalid` included, is no rating.
+    - **A detector copying another's saccades abstains on them:** where
+      `copies[name]` names its source, its rating is left out on every sample
+      the source called `saccade`, as `DetectorAgreement` leaves those samples
+      out of that pair (`registry.Detector.copies_saccades_from`).
+    - **`n_samples_compared`** counts the samples at least two detectors
+      rated."""
+    names = list(labels)
+    vocabulary = blended_vocabulary([vocabularies[name] for name in names], pso_as)
+    code = {label: index for index, label in enumerate(sorted(vocabulary, key=list(Label).index))}
+    ratings = np.full((len(names), len(labels[names[0]])), -1, dtype=np.int64)
+    for row, name in enumerate(names):
+        trace = labels[name]
+        for value in set(trace.tolist()):
+            coarsened = coarsen(Label(value), vocabulary, pso_as)
+            if coarsened is not None:
+                ratings[row, trace == value] = code[coarsened]
+        source = copies.get(name)
+        if source is not None and source in labels:
+            ratings[row, labels[source] == Label.SACCADE] = -1
+    n_compared = int(((ratings >= 0).sum(axis=0) >= 2).sum())
+    value = compute(ratings)
+    return Blended(None if math.isnan(value) else float(value), n_compared, vocabulary)
+
+
+#: The blended metrics, by name: `DetectionQuality.metric`. A registry, as
+#: `CONSENSUS_METRICS` is, so a second one is rows, not a migration.
+BLENDED_METRICS: dict[str, Callable[[np.ndarray], float]] = {
+    "krippendorff_alpha": krippendorff_alpha,
+}
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 10 passed.

- [ ] **Step 5: Mutation checks.** Each was measured, against the final tree, to fail exactly the tests in brackets.
  - T1a: units one detector rated kept, `pairable = rated >= 1` [`test_alpha_matches_krippendorffs_own_worked_example`, `test_a_unit_only_one_rater_rated_is_left_out`].
  - T1b: an undefined α read as perfect, `return 1.0` in place of `return float("nan")` after `if len(categories) < 2:` [`test_alpha_is_undefined_where_every_rating_is_one_value`, `test_an_undefined_blend_is_none`].
  - T1c: no small-sample correction, `1.0 - n * observed` in place of `1.0 - (n - 1.0) * observed` [`test_alpha_matches_krippendorffs_own_worked_example`].
  - T1d: no abstention, the line `ratings[row, labels[source] == Label.SACCADE] = -1` becomes `pass` [`test_a_detector_abstains_on_the_saccades_it_copies`].
  - T1e: the mask rated as fixation, `if True:` and `code.get(coarsened, 0)` in place of `if coarsened is not None:` and `code[coarsened]` [`test_the_blend_coarsens_each_trace_and_leaves_out_the_mask`, `test_a_detector_abstains_on_the_saccades_it_copies`].
  - T1f: the vocabulary not folded, `for vocabulary in rest[:0]:` [`test_the_seven_registered_detectors_share_saccade_and_fixation_whatever_the_order`, `test_the_blend_coarsens_each_trace_and_leaves_out_the_mask`, `test_a_detector_abstains_on_the_saccades_it_copies`, `test_an_undefined_blend_is_none`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/eye/detect/consensus.py tests/eye/detect/test_blended_agreement.py
git commit -m "feat(consensus): the seven-way agreement as pure functions -- Krippendorff's alpha for nominal ratings with missing values, the vocabulary every detector can be scored in (shared_vocabulary folded), each trace coarsened into it with the mask left out, and a detector abstaining on the saccades it copies; a BLENDED_METRICS registry

<trailer lines>"
```

---

### Task 2: `DetectionQuality`, the stored score

**Files:**
- Modify: `wl_preproc/schema/consensus.py`, `wl_preproc/daemon.py`
- Create: `tests/schema/test_detection_quality_populate.py`

**Interfaces — consumes:** Task 1's `BLENDED_METRICS` and `blended_agreement`. From the codebase: `schema/consensus.py`'s `PSO_AS_VALUES`, `_PSO_AS_ENUM` and `vocabulary_text`; `labels.Label`, `Run`, `labels_from_runs`, `runs_from_labels`; `registry.DETECTORS`, `get_detector`, `Detector.vocabulary`, `Detector.copies_saccades_from`; `detect.EyeDetection` and `.Run`; `detect.register_default_paramsets`; `paramset.register`; `tests/schema/test_detect_populate.py::_build_stepped_session`; `tests/identities.py::new_animal`.

**Interfaces — produces:**
- `consensus._live_detectors() -> dict[int, str]`: each `eye_detection` paramset index whose detector is in `registry.DETECTORS`, with the detector's name.
- `consensus.DetectionQuality`, keyed by `-> pipeline.Session`, `trace : enum('left','right','conjunction')`, the validity paramset (`validity_paramset_type`, `validity_paramset_idx`), `metric : varchar(32)`, `vocabulary : varchar(128)`, `pso_as`; columns `value=null : double`, `n_samples_compared : int unsigned`, `detectors : varchar(255)` (the blended paramset indices, ascending, comma-separated).
- A daemon stage, after `consensus.DetectorAgreement`.
- In the test file: `TRACES`, the fixtures `daemon_module`, `stepped` and `planted` (returns `(session_key, validity_idx)`), and the helpers `_registered()`, `_stored_labels(detection)`, `_plant(key, validity_idx, trace, paramset_idx, labels, status="computed")`.

- [ ] **Step 1: Write the failing tests.** Apply this diff:

```diff
--- /dev/null
+++ b/tests/schema/test_detection_quality_populate.py
@@ -0,0 +1,287 @@
+"""`DetectionQuality` populates (design spec
+`docs/superpowers/specs/2026-10-08-seven-way-agreement-design.md` sections 3,
+4 and 7): end to end through `daemon.run_once()` on `test_detect_populate.py`'s
+stepped session, and on planted detection rows where a hand-set trace shows
+what a rule does. A planted session is deleted after its test, with everything
+hanging from it, so no later daemon pass in the suite meets it."""
+
+from __future__ import annotations
+
+import datetime
+
+import pytest
+
+from tests.identities import new_animal
+from tests.schema.test_detect_populate import _build_stepped_session
+from wl_preproc.eye.detect.labels import Label, Run, labels_from_runs, runs_from_labels
+
+TRACES = ("left", "right", "conjunction")
+S, F = Label.SACCADE, Label.FIXATION
+
+
+@pytest.fixture(scope="module")
+def daemon_module(dj_conn, prefix):
+    """Activation only: `daemon.run_once()` registers the paramsets."""
+    from wl_preproc import daemon
+
+    daemon.activate_all(prefix=prefix)
+    return daemon
+
+
+@pytest.fixture(scope="module")
+def stepped(daemon_module, prefix, tmp_path_factory):
+    session = new_animal().session()
+    session_key, _segment, _onsets = _build_stepped_session(
+        tmp_path_factory, dirname="qualitystep", session_id=session.session_id, subject=session.subject,
+        session_datetime=session.session_datetime, seed=1101)
+    daemon_module.run_once(prefix=prefix)
+    return session_key
+
+
+def _registered() -> dict[int, str]:
+    """Each `eye_detection` paramset whose detector the code has. Another
+    module leaves one registered for a detector it has since removed."""
+    from wl_preproc.eye.detect.registry import DETECTORS
+    from wl_preproc.schema import paramset
+
+    return {row["paramset_idx"]: row["params"]["detector"]
+            for row in (paramset.ParamSet & {"paramset_type": "eye_detection"}).to_dicts()
+            if row["params"]["detector"] in DETECTORS}
+
+
+def _stored_labels(detection: dict):
+    from wl_preproc.schema import detect
+
+    key = {name: detection[name] for name in detect.EyeDetection.primary_key}
+    runs = (detect.EyeDetection.Run & key).to_dicts(order_by="run_index")
+    return labels_from_runs([Run(run["run_start"], run["run_stop"], Label(run["label"])) for run in runs],
+                            detection["n_samples"])
+
+
+def test_every_trace_gets_a_row_per_metric_and_convention(stepped):
+    from wl_preproc.eye.detect.consensus import BLENDED_METRICS
+    from wl_preproc.schema import consensus
+
+    rows = (consensus.DetectionQuality & stepped).to_dicts()
+    assert sorted((row["trace"], row["metric"], row["pso_as"]) for row in rows) == sorted(
+        (trace, metric, pso_as) for trace in TRACES for metric in BLENDED_METRICS for pso_as in consensus.PSO_AS_VALUES)
+    assert {row["detectors"] for row in rows} == {",".join(str(idx) for idx in sorted(_registered()))}
+    assert {row["vocabulary"] for row in rows} == {"saccade,fixation"}
+
+
+def test_each_value_is_the_blend_of_the_stored_labels(stepped):
+    """Every stored value is `blended_agreement` over the stored runs, BMD
+    abstaining on Engbert-Kliegl's saccades."""
+    from wl_preproc.eye.detect.consensus import BLENDED_METRICS, blended_agreement
+    from wl_preproc.eye.detect.registry import get_detector
+    from wl_preproc.schema import consensus, detect
+
+    registered = _registered()
+    engbert_kliegl = min(idx for idx, name in registered.items() if name == "engbert_kliegl")
+    for row in (consensus.DetectionQuality & stepped).to_dicts():
+        detections = (detect.EyeDetection & stepped & {"trace": row["trace"], "paramset_type": "eye_detection",
+                                                       "validity_paramset_idx": row["validity_paramset_idx"]}).to_dicts()
+        labels = {str(d["paramset_idx"]): _stored_labels(d) for d in detections}
+        vocabularies = {str(d["paramset_idx"]): get_detector(registered[d["paramset_idx"]]).vocabulary for d in detections}
+        copies = {str(d["paramset_idx"]): str(engbert_kliegl) if registered[d["paramset_idx"]] == "bmd" else None
+                  for d in detections}
+        result = blended_agreement(labels, vocabularies, copies, row["pso_as"], BLENDED_METRICS[row["metric"]])
+        assert row["n_samples_compared"] == result.n_samples_compared
+        assert row["value"] == pytest.approx(result.value)
+        assert 0.0 < row["value"] < 1.0
+
+
+@pytest.fixture
+def planted(daemon_module):
+    """A bare session, deleted with everything hanging from it afterwards.
+    Returns its key and the default validity paramset's index. Registers the
+    detectors itself: run alone, no daemon pass has registered them, and a
+    test planting one detection per registered detector would plant none."""
+    from dataclasses import asdict
+
+    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
+    from wl_preproc.schema import detect, paramset, pipeline
+
+    detect.register_default_paramsets()
+    session = new_animal().session()
+    pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
+                             skip_duplicates=True)
+    pipeline.subject.Subject.insert1({"subject": session.subject, "sex": "M", "subject_description": "",
+                                      "subject_birth_date": datetime.date(2020, 1, 1)})
+    pipeline.Session.insert1(session.key)
+    yield session.key, paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS))
+    (pipeline.Session & session.key).delete()
+
+
+def _plant(key, validity_idx, trace, paramset_idx, labels, status="computed"):
+    """One detection of `trace`, its runs tiling `labels`."""
+    from wl_preproc.schema import detect
+
+    row = {**key, "trace": trace, "validity_paramset_type": "eye_validity", "validity_paramset_idx": validity_idx,
+           "paramset_type": "eye_detection", "paramset_idx": paramset_idx, "status": status,
+           "n_samples": len(labels) if status == "computed" else None, "reason": "" if status == "computed" else "planted"}
+    detect.EyeDetection.insert1(row, allow_direct_insert=True)
+    if status == "computed":
+        detect.EyeDetection.Run.insert(
+            {**{name: row[name] for name in detect.EyeDetection.primary_key}, "run_index": index,
+             "run_start": run.start, "run_stop": run.stop, "label": run.label.value}
+            for index, run in enumerate(runs_from_labels(labels)))
+
+
+def test_a_trace_is_blended_only_when_every_registered_detector_computed_it(planted):
+    """Left: every registered detector, identical traces, so agreement is
+    perfect. Right: one detector missing. Both-eyes: every detector refused."""
+    from wl_preproc.schema import consensus
+
+    key, validity_idx = planted
+    registered = sorted(_registered())
+    trace = [F] * 20 + [S] * 5 + [F] * 20
+    for idx in registered:
+        _plant(key, validity_idx, "left", idx, trace)
+        _plant(key, validity_idx, "conjunction", idx, trace, status="refused")
+    for idx in registered[1:]:
+        _plant(key, validity_idx, "right", idx, trace)
+
+    consensus.DetectionQuality.populate(key)
+    rows = (consensus.DetectionQuality & key).to_dicts()
+    assert sorted((row["trace"], row["pso_as"]) for row in rows) == [("left", "fixation"), ("left", "saccade")]
+    assert all(row["value"] == pytest.approx(1.0) and row["n_samples_compared"] == 45 for row in rows)
+
+
+def test_a_session_with_no_complete_trace_is_not_a_candidate(planted):
+    from wl_preproc.schema import consensus
+
+    key, validity_idx = planted
+    registered = sorted(_registered())
+    trace = [F] * 20 + [S] * 5 + [F] * 20
+    for idx in registered[1:]:
+        _plant(key, validity_idx, "left", idx, trace)
+    for idx in registered:
+        _plant(key, validity_idx, "right", idx, trace, status="refused")
+
+    assert len(consensus.DetectionQuality().key_source & key) == 0
+    consensus.DetectionQuality.populate(key)
+    assert len(consensus.DetectionQuality & key) == 0
+
+
+def test_a_trace_with_no_saccades_is_undefined_and_stored_as_null(planted):
+    """Every detector labels the whole trace fixation, as for a session the
+    animal slept through: no disagreement is expected, so the score is
+    undefined. NULL, never 1.0."""
+    from wl_preproc.schema import consensus
+
+    key, validity_idx = planted
+    for idx in sorted(_registered()):
+        _plant(key, validity_idx, "left", idx, [F] * 45)
+    consensus.DetectionQuality.populate(key)
+    rows = (consensus.DetectionQuality & key).to_dicts()
+    assert sorted((row["pso_as"], row["value"], row["n_samples_compared"]) for row in rows) == [
+        ("fixation", None, 45), ("saccade", None, 45)]
+
+
+def test_a_paramset_for_a_detector_the_code_no_longer_has_is_not_waited_for(planted):
+    """Its detection can only ever error, so waiting for it would stop the
+    table for every later session (spec amendment 4). Found in the full
+    suite, where another module leaves such a paramset registered. A
+    detection it computed before its detector was removed is not blended
+    either."""
+    from wl_preproc.schema import consensus, paramset
+
+    key, validity_idx = planted
+    registered = sorted(_registered())
+    gone = paramset.register("eye_detection", {"detector": "not_a_detector_quality"})
+    try:
+        trace = [F] * 20 + [S] * 5 + [F] * 20
+        for idx in [*registered, gone]:
+            _plant(key, validity_idx, "left", idx, trace)
+        consensus.DetectionQuality.populate(key)
+        rows = (consensus.DetectionQuality & key).to_dicts()
+        assert sorted((row["trace"], row["pso_as"]) for row in rows) == [("left", "fixation"), ("left", "saccade")]
+        assert {row["detectors"] for row in rows} == {",".join(str(idx) for idx in registered)}
+    finally:
+        (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": gone}).delete()
+
+
+_QUALITY_PROBE = """
+import json
+import os
+import sys
+from pathlib import Path
+
+import datajoint as dj
+
+sys.path.insert(0, os.getcwd())
+
+from wl_preproc.schema._compat import apply_datajoint_compat
+
+apply_datajoint_compat()
+dj.config["database.host"] = os.environ["WLPP_PROBE_HOST"]
+dj.config["database.port"] = int(os.environ["WLPP_PROBE_PORT"])
+dj.config["database.user"] = os.environ["WLPP_PROBE_USER"]
+dj.config["database.password"] = os.environ["WLPP_PROBE_PASSWORD"]
+dj.config["safemode"] = False
+dj.logger.setLevel("ERROR")
+
+from wl_preproc import daemon
+from wl_preproc.cli.main import main
+from wl_preproc.schema import consensus
+
+from tests.identities import new_animal
+from tests.schema.test_detect_populate import _build_stepped_session
+
+
+class _Factory:
+    def __init__(self, root):
+        self.root = Path(root)
+
+    def mktemp(self, name):
+        made = self.root / name
+        made.mkdir(parents=True, exist_ok=True)
+        return made
+
+
+prefix = os.environ["WLPP_PROBE_PREFIX"]
+# Activation only. Nothing here registers a paramset: that is the question.
+daemon.activate_all(prefix=prefix)
+session = new_animal().session()
+_build_stepped_session(
+    _Factory(os.environ["WLPP_PROBE_ROOT"]), dirname="probe", session_id=session.session_id,
+    subject=session.subject, session_datetime=session.session_datetime, seed=1102,
+)
+assert main(["daemon", "--prefix", prefix]) == 0
+rows = consensus.DetectionQuality().to_dicts()
+print("PROBE " + json.dumps({"rows": len(rows), "traces": sorted({row["trace"] for row in rows})}))
+"""
+
+
+def test_a_real_wlpp_daemon_pass_writes_quality_rows_registering_nothing_itself(dj_conn, tmp_path):
+    """`wlpp daemon`, the real entry point, against a prefix where nothing has
+    ever registered a paramset: a row for each trace, metric and convention.
+    It fails if the table is not a daemon stage or runs before the
+    detections it blends."""
+    import json
+    import os
+    import subprocess
+    import sys
+
+    import datajoint as dj
+
+    from wl_preproc.eye.detect.consensus import BLENDED_METRICS
+    from wl_preproc.schema import consensus
+
+    result = subprocess.run(
+        [sys.executable, "-c", _QUALITY_PROBE], capture_output=True, text=True,
+        env={**os.environ,
+             "WLPP_PROBE_HOST": str(dj.config["database.host"]),
+             "WLPP_PROBE_PORT": str(dj.config["database.port"]),
+             "WLPP_PROBE_USER": str(dj.config["database.user"]),
+             "WLPP_PROBE_PASSWORD": str(dj.config["database.password"]),
+             "WLPP_PROBE_PREFIX": "dq_",
+             "WLPP_PROBE_ROOT": str(tmp_path),
+             "PYTHONDONTWRITEBYTECODE": "1"},
+    )
+    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
+    marker = next((line for line in result.stdout.splitlines() if line.startswith("PROBE ")), None)
+    assert marker is not None, f"probe printed no result:\n{result.stdout}\n{result.stderr}"
+    assert json.loads(marker[len("PROBE "):]) == {
+        "rows": len(TRACES) * len(BLENDED_METRICS) * len(consensus.PSO_AS_VALUES), "traces": sorted(TRACES)}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_detection_quality_populate.py tests/schema/test_daemon.py -q --tb=line -p no:cacheprovider -o faulthandler_timeout=1800`
Expected: 7 failed, 17 passed. The seven are this file's, on `consensus.DetectionQuality` not existing (the probe's subprocess reports the same).

- [ ] **Step 3: Implement the table.** Apply this diff:

```diff
--- a/wl_preproc/schema/consensus.py
+++ b/wl_preproc/schema/consensus.py
@@ -52,16 +52,18 @@ import datajoint as dj
 import numpy as np
 
 from wl_preproc.eye.detect.consensus import (
+    BLENDED_METRICS,
     CONSENSUS_METRICS,
     DEFAULT_EVENT_F1_TOLERANCE_SAMPLES,
     PSO_AS_FIXATION,
     PSO_AS_SACCADE,
+    blended_agreement,
     coarsen,
     comparison_mask,
     shared_vocabulary,
 )
 from wl_preproc.eye.detect.labels import Label, Run, labels_from_runs
-from wl_preproc.schema import DEFAULT_PREFIX, detect, paramset
+from wl_preproc.schema import DEFAULT_PREFIX, detect, paramset, pipeline
 
 schema = dj.Schema()
 
@@ -472,8 +474,109 @@ class DetectorAgreement(dj.Computed):
         self.insert(rows)
 
 
+def _live_detectors() -> dict[int, str]:
+    """Each `eye_detection` paramset whose detector the code still has, by
+    index, with that detector's name. A paramset for a detector since removed
+    from the registry can only ever error, so waiting for it would stop
+    `DetectionQuality` for every later session (seven-way design spec
+    amendment 4)."""
+    from wl_preproc.eye.detect.registry import DETECTORS
+
+    return {row["paramset_idx"]: row["params"]["detector"]
+            for row in (paramset.ParamSet & {"paramset_type": "eye_detection"}).to_dicts()
+            if row["params"].get("detector") in DETECTORS}
+
+
+@schema
+class DetectionQuality(dj.Computed):
+    definition = f"""
+    # Every registered detector's agreement on one trace, in one score, beside
+    # the pairwise rows and never instead of them (design spec
+    # `2026-10-08-seven-way-agreement-design.md` section 3).
+    # Key: (subject, session_datetime, trace, validity_paramset_type,
+    # validity_paramset_idx, metric, vocabulary, pso_as).
+    -> pipeline.Session
+    trace : enum('left','right','conjunction')
+    -> paramset.ParamSet.proj(validity_paramset_type='paramset_type', validity_paramset_idx='paramset_idx')
+    # `varchar` in the key, as `DetectorAgreement`'s are: a second blended
+    # metric, or a detector that changes the shared vocabulary, adds rows and
+    # needs no migration after January.
+    metric     : varchar(32)
+    vocabulary : varchar(128)
+    pso_as     : enum({_PSO_AS_ENUM})
+    ---
+    # NULL where the metric is undefined: every compared sample one label, so
+    # no disagreement is expected.
+    value=null         : double
+    # Samples at least two detectors rated.
+    n_samples_compared : int unsigned
+    # The `eye_detection` paramset indices blended, ascending, comma-separated.
+    detectors          : varchar(255)
+    """
+
+    @property
+    def key_source(self):
+        """A session and validity paramset with at least one trace computed
+        by every registered `eye_detection` paramset whose detector the code
+        has (`_live_detectors`). `make()` blends each such trace.
+
+        **Never a partial set** (spec section 3): a row written while one
+        detector's job was pending or had errored would never be recomputed
+        when it caught up, since DataJoint never revisits a populated key. A
+        trace whose detection was refused has no computed rows and gets no
+        row, as `DetectorAgreement`'s rule is.
+
+        Collapsed over `trace` with `dj.U`, as `EyeDetection.key_source`
+        collapses over `eye`: `trace` is this table's own attribute, not one
+        it inherits, so DataJoint keys its job queue without it and one
+        `make()` writes every complete trace."""
+        live = _live_detectors()
+        computed = (detect.EyeDetection & 'status = "computed"' & {"paramset_type": "eye_detection"}
+                    & [{"paramset_idx": idx} for idx in live])
+        complete = dj.U("subject", "session_datetime", "trace", "validity_paramset_type",
+                        "validity_paramset_idx").aggr(computed, n_detectors="count(*)") & f"n_detectors = {len(live)}"
+        return dj.U("subject", "session_datetime", "validity_paramset_type", "validity_paramset_idx") & complete
+
+    def make(self, key: dict) -> None:
+        """Each complete trace's score under each blended metric and both
+        `pso` conventions."""
+        from wl_preproc.eye.detect.registry import get_detector
+
+        registered = _live_detectors()
+        detections = (detect.EyeDetection & key & {"paramset_type": "eye_detection"}).to_dicts()
+        rows = []
+        for trace in ("left", "right", "conjunction"):
+            members = sorted((row for row in detections if row["trace"] == trace and row["paramset_idx"] in registered),
+                             key=lambda row: row["paramset_idx"])
+            if (sorted(row["paramset_idx"] for row in members) != sorted(registered)
+                    or any(row["status"] != "computed" for row in members)):
+                continue
+            labels, vocabularies, copies = {}, {}, {}
+            for row in members:
+                detection_key = {name: row[name] for name in detect.EyeDetection.primary_key}
+                runs = (detect.EyeDetection.Run & detection_key).to_dicts(order_by="run_index")
+                index = str(row["paramset_idx"])
+                labels[index] = labels_from_runs(
+                    [Run(run["run_start"], run["run_stop"], Label(run["label"])) for run in runs], row["n_samples"])
+                detector = get_detector(registered[row["paramset_idx"]])
+                vocabularies[index] = detector.vocabulary
+                # The source's lowest-index paramset, where several run the
+                # same detector (registry.Detector.copies_saccades_from).
+                sources = sorted(idx for idx, name in registered.items() if name == detector.copies_saccades_from)
+                copies[index] = str(sources[0]) if sources else None
+            detectors = ",".join(str(row["paramset_idx"]) for row in members)
+            for pso_as in PSO_AS_VALUES:
+                for metric, compute in BLENDED_METRICS.items():
+                    result = blended_agreement(labels, vocabularies, copies, pso_as, compute)
+                    rows.append({**key, "trace": trace, "metric": metric,
+                                 "vocabulary": vocabulary_text(result.vocabulary), "pso_as": pso_as,
+                                 "value": result.value, "n_samples_compared": result.n_samples_compared,
+                                 "detectors": detectors})
+        self.insert(rows)
+
+
 def activate(prefix: str = DEFAULT_PREFIX) -> None:
-    """Bind this table to `{prefix}consensus`. Idempotent."""
+    """Bind these tables to `{prefix}consensus`. Idempotent."""
     detect.activate(prefix=prefix)
     if not schema.is_activated():
         schema.activate(f"{prefix}consensus", create_tables=True)
```

- [ ] **Step 4: Run the daemon's discovering tests to verify they catch the missing wiring**

Run: `.venv/bin/python -m pytest tests/schema/test_daemon.py -q --tb=line -p no:cacheprovider`
Expected: 1 failed, 16 passed: `test_every_computed_table_is_a_daemon_stage` finds `DetectionQuality` missing from `_computed_tables()`.

- [ ] **Step 5: Wire it into the daemon.** Apply this diff:

```diff
--- a/wl_preproc/daemon.py
+++ b/wl_preproc/daemon.py
@@ -204,6 +204,10 @@ def _computed_tables() -> list:
         # session rather than self-correcting within a pass.
         consensus.DetectorAgreement,
         # BELOW `detect.EyeDetection` for `DetectorAgreement`'s reason: its
+        # `key_source` is that table's computed rows, so above it a session's
+        # first pass would name no key and its rows would wait a whole pass.
+        consensus.DetectionQuality,
+        # BELOW `detect.EyeDetection` for `DetectorAgreement`'s reason: its
         # `key_source` is that table's rows, so above it a session's first
         # pass would name no key and its fits would wait a whole pass.
         main_sequence.SaccadeMainSequence,
```

- [ ] **Step 6: Run them to verify they pass**

Run: the Step 2 command. Expected: 24 passed.

- [ ] **Step 7: Mutation checks.** Each was measured, against the final tree, to fail the tests in brackets.
  - Q1: `key_source` takes any computed trace, ` & f"n_detectors = {len(live)}"` removed [`test_a_session_with_no_complete_trace_is_not_a_candidate`].
  - Q2: `key_source` counts refused detections, the `'status = "computed"'` restriction removed [same].
  - Q3: `make()` blends a partial set, its membership check `if (False` [`test_a_trace_is_blended_only_when_every_registered_detector_computed_it`].
  - Q4: BMD does not abstain, `copies[index] = None` [`test_each_value_is_the_blend_of_the_stored_labels`].
  - Q5: one glissade convention, `PSO_AS_VALUES[:1]` [`test_every_trace_gets_a_row_per_metric_and_convention`].
  - Q6: the both-eyes trace never blended, `for trace in ("left", "right"):` [same].
  - Q7: `consensus.DetectionQuality` removed from `_computed_tables()` [`test_every_computed_table_is_a_daemon_stage`, `test_a_real_wlpp_daemon_pass_writes_quality_rows_registering_nothing_itself`].
  - Q8: the stage moved above `detect.EyeValidity` [`test_a_real_wlpp_daemon_pass_writes_quality_rows_registering_nothing_itself`].
  - Q9: a removed detector's paramset waited for, `_live_detectors`' filter `if True` [`test_a_paramset_for_a_detector_the_code_no_longer_has_is_not_waited_for`].
  - Q10: a removed detector's detection blended, ` and row["paramset_idx"] in registered` removed from `make()`'s members [same].
  - Q11: an undefined score stored as 1.0, `"value": 1.0 if result.value is None else result.value` [`test_a_trace_with_no_saccades_is_undefined_and_stored_as_null`].

- [ ] **Step 8: Commit**

```bash
git add wl_preproc/schema/consensus.py wl_preproc/daemon.py tests/schema/test_detection_quality_populate.py
git commit -m "feat(consensus): DetectionQuality -- every registered detector's agreement on each trace in one score, per glissade convention and blended metric, beside the pairwise rows; run only on a trace every registered detector the code has computed, so a partial set is never blended and a refused trace gets no row, and a paramset for a removed detector is neither waited for nor blended; a daemon stage below the detections it reads

<trailer lines>"
```

---

### Task 3: The seven-way line in the daily report

**Files:**
- Modify: `wl_preproc/cli/report.py`, `tests/cli/test_detect_report.py`
- Create: `tests/cli/test_quality_report.py`

**Interfaces — consumes:** Task 2's `DetectionQuality`; `schema/consensus.py`'s `PSO_AS_VALUES`. From the codebase: `tests/cli/test_detect_report.py`'s `_detection_row`, `_land_session`, `_line_for`, `_section`, `_subsection`; `detect.register_default_paramsets`; `main_sequence.register_default_paramsets` and `SaccadeMainSequence` (the tests plant a fit row so no daemon pass tries to fit their detections).

**Interfaces — produces:** `report._QUALITY_MIN_HISTORY = 3`; `report._quality_lines(ingested_keys: set, prefix) -> list[str]`; in `build_report` the subsection `### Seven-way agreement per session per eye (24 h) — N`, after the detector agreement and before the saccade vigor. Each line: ``- `<subject>` @ <YYYY-mm-dd HH:MM> — <left|right> (validity paramset V): glissades as saccade <figure>; as fixation <figure>``, the figure `0.63 (usual 0.68, 3 earlier)`, `0.63 (no history yet, 2 earlier)` or `undefined (…)`; or in place of both, `detection refused` or `not computed yet`.

- [ ] **Step 1: Write the failing tests.** Apply this diff:

```diff
--- a/tests/cli/test_detect_report.py
+++ b/tests/cli/test_detect_report.py
@@ -366,7 +366,9 @@ def test_the_detection_section_reports_counts_per_session(detect_schema, tmp_pat
 
     section = _section(build_report(root, prefix=prefix), "Detection")
 
-    line = _line_for(section, subject)
+    # The Events subsection's line: the seven-way and vigor subsections name
+    # the session too, the second only once a main-sequence paramset exists.
+    line = _line_for(_subsection(section, "Events per session per trace (24 h)"), subject)
     # Not a bare substring check on each label alone: `n_saccades=5` and
     # `n_microsaccades=2` are deliberately DIFFERENT numbers, so a mutation
     # that swapped which count feeds which label in the f-string would still
--- /dev/null
+++ b/tests/cli/test_quality_report.py
@@ -0,0 +1,191 @@
+"""The report's `### Seven-way agreement per session per eye (24 h)`
+subsection (design spec `2026-10-08-seven-way-agreement-design.md` section 5).
+
+Rows are planted directly, as `test_vigor_report.py` plants its own;
+`tests/schema/test_detection_quality_populate.py` drives the table end to end.
+Each test plants its own animal, so no line is another test's. Earlier
+sessions are ingested long ago, outside the window, so only the session under
+test gets a line of its own.
+"""
+
+from __future__ import annotations
+
+import datetime
+from types import SimpleNamespace
+
+import pytest
+
+from tests.cli.test_detect_report import _detection_row, _land_session, _line_for, _section, _subsection
+from tests.identities import new_animal
+from wl_preproc.cli.report import build_report
+
+_LONG_AGO = datetime.datetime(2025, 1, 1)
+_HEADING = "Seven-way agreement per session per eye (24 h)"
+_VOCABULARY = "saccade,fixation"
+
+
+@pytest.fixture(scope="module")
+def quality_schema(dj_conn, prefix):
+    from dataclasses import asdict
+
+    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
+    from wl_preproc.schema import consensus, detect, ingest, main_sequence, paramset, timebase
+
+    consensus.activate(prefix=prefix)
+    main_sequence.activate(prefix=prefix)
+    ingest.activate(prefix=prefix)
+    timebase.activate(prefix=prefix)
+    detections = detect.register_default_paramsets()
+    return SimpleNamespace(
+        detection=detections["engbert_kliegl"],
+        detectors=",".join(str(idx) for idx in sorted(detections.values())),
+        validity=paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS)),
+        fit=main_sequence.register_default_paramsets()["default"],
+    )
+
+
+def _detect(schema, session, trace, *, status="computed"):
+    """Engbert-Kliegl's detection of `trace`: one `fixation` run over 100
+    samples, so it tiles, and its `SaccadeMainSequence` row, so no later
+    daemon pass in the suite tries to fit it. One detector is no pair, and
+    not every registered one, so `DetectorAgreement` and `DetectionQuality`
+    never take it up."""
+    from wl_preproc.schema import detect, main_sequence
+
+    row = _detection_row(session.subject, session.session_datetime, trace, schema.validity, schema.detection,
+                         status=status, n_samples=100 if status == "computed" else None,
+                         reason="planted refusal" if status == "refused" else "")
+    detect.EyeDetection.insert1(row, allow_direct_insert=True)
+    key = {name: row[name] for name in detect.EyeDetection.primary_key}
+    if status == "computed":
+        detect.EyeDetection.Run.insert1({**key, "run_index": 0, "run_start": 0, "run_stop": 100, "label": "fixation",
+                                         "amplitude_deg": None, "peak_velocity_deg_s": None})
+    main_sequence.SaccadeMainSequence.insert1(
+        {**key, "fit_paramset_type": "main_sequence", "fit_paramset_idx": schema.fit, "fit_status": "refused",
+         "n_saccades": 0, "reason": "planted"}, allow_direct_insert=True)
+
+
+def _quality(schema, session, trace, as_saccade, as_fixation, *, metric="krippendorff_alpha", vocabulary=_VOCABULARY,
+             detectors=None, validity=None):
+    """`DetectionQuality`'s two rows for `trace`, one per glissade
+    convention; a value of None is an undefined score."""
+    from wl_preproc.schema import consensus
+
+    consensus.DetectionQuality.insert(
+        ({**session.key, "trace": trace, "validity_paramset_type": "eye_validity",
+          "validity_paramset_idx": schema.validity if validity is None else validity, "metric": metric,
+          "vocabulary": vocabulary,
+          "pso_as": pso_as, "value": value, "n_samples_compared": 100,
+          "detectors": detectors or schema.detectors}
+         for pso_as, value in (("saccade", as_saccade), ("fixation", as_fixation))),
+        allow_direct_insert=True)
+
+
+def _animal_with_history(schema, history):
+    """An animal with one session ingested long ago per `(as_saccade,
+    as_fixation)` pair in `history`, each with both eyes' rows, and a session
+    ingested now. Returns that session, landed but not yet detected."""
+    animal = new_animal()
+    for as_saccade, as_fixation in history:
+        earlier = animal.session()
+        _land_session(earlier.subject, earlier.session_datetime, ingested_at=_LONG_AGO)
+        for trace in ("left", "right"):
+            _quality(schema, earlier, trace, as_saccade, as_fixation)
+    session = animal.session()
+    _land_session(session.subject, session.session_datetime)
+    return animal, session
+
+
+_HISTORY = [(0.70, 0.66), (0.68, 0.67), (0.60, 0.65)]
+
+
+def _line(root, prefix, session, trace) -> str:
+    subsection = _subsection(_section(build_report(root, prefix=prefix), "Detection"), _HEADING)
+    return _line_for(subsection, f"`{session.subject}` @ {session.session_datetime:%Y-%m-%d %H:%M} — {trace} ")
+
+
+def test_a_session_gets_its_agreement_against_its_earlier_sessions(quality_schema, tmp_path, prefix):
+    _animal, session = _animal_with_history(quality_schema, _HISTORY)
+    _detect(quality_schema, session, "left")
+    _quality(quality_schema, session, "left", 0.63, 0.62)
+    assert _line(tmp_path, prefix, session, "left").endswith(
+        f"— left (validity paramset {quality_schema.validity}): glissades as saccade 0.63 (usual 0.68, 3 earlier); "
+        "as fixation 0.62 (usual 0.66, 3 earlier)")
+
+
+def test_fewer_than_three_earlier_sessions_is_no_history_yet(quality_schema, tmp_path, prefix):
+    _animal, session = _animal_with_history(quality_schema, _HISTORY[:2])
+    _detect(quality_schema, session, "left")
+    _quality(quality_schema, session, "left", 0.63, 0.62)
+    assert _line(tmp_path, prefix, session, "left").endswith(
+        "glissades as saccade 0.63 (no history yet, 2 earlier); as fixation 0.62 (no history yet, 2 earlier)")
+
+
+def test_an_undefined_score_says_so(quality_schema, tmp_path, prefix):
+    _animal, session = _animal_with_history(quality_schema, _HISTORY)
+    _detect(quality_schema, session, "left")
+    _quality(quality_schema, session, "left", None, 0.62)
+    assert _line(tmp_path, prefix, session, "left").endswith(
+        "glissades as saccade undefined (usual 0.68, 3 earlier); as fixation 0.62 (usual 0.66, 3 earlier)")
+
+
+def test_a_refused_detection_says_so(quality_schema, tmp_path, prefix):
+    _animal, session = _animal_with_history(quality_schema, _HISTORY)
+    _detect(quality_schema, session, "right", status="refused")
+    assert _line(tmp_path, prefix, session, "right").endswith(
+        f"— right (validity paramset {quality_schema.validity}): detection refused")
+
+
+def test_a_detection_without_its_rows_is_not_computed_yet(quality_schema, tmp_path, prefix):
+    _animal, session = _animal_with_history(quality_schema, _HISTORY)
+    _detect(quality_schema, session, "left")
+    assert _line(tmp_path, prefix, session, "left").endswith(
+        f"— left (validity paramset {quality_schema.validity}): not computed yet")
+
+
+def test_the_both_eyes_trace_is_left_out(quality_schema, tmp_path, prefix):
+    _animal, session = _animal_with_history(quality_schema, _HISTORY)
+    _detect(quality_schema, session, "conjunction")
+    _quality(quality_schema, session, "conjunction", 0.63, 0.62)
+    subsection = _subsection(_section(build_report(tmp_path, prefix=prefix), "Detection"), _HEADING)
+    assert f"`{session.subject}` @" not in subsection
+
+
+def test_the_history_is_earlier_sessions_scored_alike(quality_schema, tmp_path, prefix):
+    """The median reads only the same animal's earlier sessions with the same
+    trace, validity paramset, metric, vocabulary and detectors, and a
+    defined score. Each session that must not count scores 0.10, so any one
+    let in moves the count. The second validity paramset is deleted
+    afterwards, with its row, so no later daemon pass computes a mask for
+    it."""
+    from dataclasses import asdict
+
+    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
+    from wl_preproc.schema import paramset
+
+    animal = new_animal()
+
+    def past(trace, as_saccade, as_fixation, **unlike):
+        earlier = animal.session()
+        _land_session(earlier.subject, earlier.session_datetime, ingested_at=_LONG_AGO)
+        _quality(quality_schema, earlier, trace, as_saccade, as_fixation, **unlike)
+
+    other_mask = paramset.register("eye_validity", {**asdict(DEFAULT_VALIDITY_PARAMS), "max_glitch_ms": 99.0})
+    try:
+        for as_saccade, as_fixation in _HISTORY:
+            past("left", as_saccade, as_fixation)
+        past("right", 0.10, 0.10)
+        past("left", 0.10, 0.10, metric="fleiss_kappa")
+        past("left", 0.10, 0.10, vocabulary="saccade,microsaccade,fixation")
+        past("left", 0.10, 0.10, detectors="0,1")
+        past("left", 0.10, 0.10, validity=other_mask)
+        past("left", None, None)
+        session = animal.session()
+        _land_session(session.subject, session.session_datetime)
+        _detect(quality_schema, session, "left")
+        _quality(quality_schema, session, "left", 0.63, 0.62)
+        past("left", 0.10, 0.10)
+        assert _line(tmp_path, prefix, session, "left").endswith(
+            "glissades as saccade 0.63 (usual 0.68, 3 earlier); as fixation 0.62 (usual 0.66, 3 earlier)")
+    finally:
+        (paramset.ParamSet & {"paramset_type": "eye_validity", "paramset_idx": other_mask}).delete()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/cli/test_quality_report.py tests/cli/test_detect_report.py -q --tb=line -p no:cacheprovider`
Expected: 7 failed, 12 passed. The seven are the new file's: no subsection headed `Seven-way agreement per session per eye (24 h)`. `test_detect_report.py`'s change passes before and after Step 3; without it, Step 3's subsection would fail `test_the_detection_section_reports_counts_per_session` (ruling 7).

- [ ] **Step 3: Implement.** Apply this diff:

```diff
--- a/wl_preproc/cli/report.py
+++ b/wl_preproc/cli/report.py
@@ -1088,6 +1088,69 @@ def _vigor_lines(ingested_keys: set, prefix: str = DEFAULT_PREFIX) -> list[str]:
     return lines
 
 
+# The fewest earlier sessions a seven-way median is shown over (seven-way
+# agreement design spec `2026-10-08-seven-way-agreement-design.md` section 5).
+_QUALITY_MIN_HISTORY = 3
+
+
+def _quality_lines(ingested_keys: set, prefix: str = DEFAULT_PREFIX) -> list[str]:
+    """`### Seven-way agreement per session per eye (24 h)`'s lines (design
+    spec `2026-10-08-seven-way-agreement-design.md` section 5): for each
+    session in `ingested_keys`, each eye and each validity paramset, the
+    session's `DetectionQuality` score under each glissade convention beside
+    the median of the same animal's earlier sessions.
+
+    **Computed here, never stored**, for `_vigor_lines`' reason. The history
+    is the earlier sessions with the same trace, validity paramset, metric,
+    vocabulary and `detectors`, and a defined score: another set of detectors
+    is another score (the spec's amendment 2). A line per metric, and the
+    line does not name it: `BLENDED_METRICS` holds one, and a second would
+    need its name on the line. In place of the figures: "detection refused"
+    where a detector's detection of the trace was, "not computed yet" where
+    no row exists. The both-eyes trace is left out, as vigor's is."""
+    import statistics
+
+    from wl_preproc.schema import consensus
+    from wl_preproc.schema import detect as detect_schema
+
+    consensus.activate(prefix=prefix)
+    table = consensus.DetectionQuality
+
+    def figure(row: dict) -> str:
+        value = "undefined" if row["value"] is None else f"{row['value']:.2f}"
+        like = {name: row[name] for name in ("subject", "trace", "validity_paramset_type", "validity_paramset_idx",
+                                               "metric", "vocabulary", "pso_as", "detectors")}
+        before = f"session_datetime < '{row['session_datetime']:%Y-%m-%d %H:%M:%S}'"
+        earlier = [past["value"] for past in (table & like & before).to_dicts() if past["value"] is not None]
+        if len(earlier) < _QUALITY_MIN_HISTORY:
+            return f"{value} (no history yet, {len(earlier)} earlier)"
+        return f"{value} (usual {statistics.median(earlier):.2f}, {len(earlier)} earlier)"
+
+    lines = []
+    for subject, session_datetime in sorted(ingested_keys):
+        session_key = {"subject": subject, "session_datetime": session_datetime}
+        detections = (detect_schema.EyeDetection & session_key & {"paramset_type": "eye_detection"}
+                      & 'trace in ("left", "right")').to_dicts()
+        groups: dict = {}
+        for detection in detections:
+            groups.setdefault((detection["trace"], detection["validity_paramset_idx"]), []).append(detection)
+        for (trace, validity_idx), members in sorted(groups.items()):
+            by_metric: dict = {}
+            for row in (table & session_key & {"trace": trace, "validity_paramset_idx": validity_idx}).to_dicts():
+                by_metric.setdefault(row["metric"], {})[row["pso_as"]] = row
+            if by_metric:
+                texts = ["glissades " + "; ".join(f"as {pso_as} {figure(conventions[pso_as])}"
+                                                  for pso_as in consensus.PSO_AS_VALUES if pso_as in conventions)
+                         for _metric, conventions in sorted(by_metric.items())]
+            elif any(member["status"] == "refused" for member in members):
+                texts = ["detection refused"]
+            else:
+                texts = ["not computed yet"]
+            lines += [f"- `{subject}` @ {session_datetime:%Y-%m-%d %H:%M} — {trace} (validity paramset "
+                      f"{validity_idx}): {text}" for text in texts]
+    return lines
+
+
 @dataclasses.dataclass(frozen=True, slots=True)
 class Readings:
     """Everything both renderings need, computed once.
@@ -1796,6 +1859,14 @@ def build_report(
         "- none"
     ]
 
+    # Seven-way agreement design spec section 5: each eye's score against the
+    # same animal's earlier sessions, both glissade conventions on one line,
+    # beside the pairwise rows above and never instead of them. Windowed to
+    # the 24 h `ingested_keys` the per-session lists above use.
+    quality_lines = _quality_lines(ingested_keys, prefix=prefix)
+    lines += ["", f"### Seven-way agreement per session per eye (24 h) — {len(quality_lines)}", ""]
+    lines += quality_lines or ["- none"]
+
     # Main-sequence design spec section 5: each eye's vigor against the same
     # animal's earlier sessions, every detector on one line. Windowed to the
     # 24 h `ingested_keys` the per-session lists above use.
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/cli/test_quality_report.py -q -p no:cacheprovider`. Expected: 7 passed.

Then the report's other tests: `.venv/bin/python -m pytest tests/cli -q -p no:cacheprovider -o faulthandler_timeout=1800`. Expected: 171 passed.

- [ ] **Step 5: Mutation checks.** Each was measured, against the final tree, to fail the tests in brackets.
  - R1: the history ignores `detectors` [`test_the_history_is_earlier_sessions_scored_alike`].
  - R2: the history ignores `vocabulary` [same].
  - R3: the history ignores `metric` [same].
  - R4: the history ignores `trace` [`test_a_session_gets_its_agreement_against_its_earlier_sessions`, `test_fewer_than_three_earlier_sessions_is_no_history_yet`, `test_an_undefined_score_says_so`, `test_the_history_is_earlier_sessions_scored_alike`].
  - R5: the history across animals, `subject` dropped [`test_fewer_than_three_earlier_sessions_is_no_history_yet`, `test_an_undefined_score_says_so`, `test_the_history_is_earlier_sessions_scored_alike`].
  - R6: later sessions in the history, `session_datetime !=` in place of `<` [`test_the_history_is_earlier_sessions_scored_alike`].
  - R7: undefined scores counted, each as 0.68 [same].
  - R8: two earlier sessions enough, `_QUALITY_MIN_HISTORY = 2` [`test_fewer_than_three_earlier_sessions_is_no_history_yet`].
  - R9: a mean for the median [`test_a_session_gets_its_agreement_against_its_earlier_sessions`, `test_an_undefined_score_says_so`, `test_the_history_is_earlier_sessions_scored_alike`].
  - R10: a refusal not told apart, `elif False:` [`test_a_refused_detection_says_so`].
  - R11: the both-eyes trace shown, the `trace in ("left", "right")` restriction dropped [`test_the_both_eyes_trace_is_left_out`].
  - R12: the conventions in the other order, `reversed(consensus.PSO_AS_VALUES)` [`test_a_session_gets_its_agreement_against_its_earlier_sessions`, `test_fewer_than_three_earlier_sessions_is_no_history_yet`, `test_an_undefined_score_says_so`, `test_the_history_is_earlier_sessions_scored_alike`].
  - R13: the lines left out of the subsection, `lines += quality_lines or ["- none"]` removed [every test but `test_the_both_eyes_trace_is_left_out`].
  - R14: the history ignores the validity paramset, `"validity_paramset_idx"` dropped [`test_the_history_is_earlier_sessions_scored_alike`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/cli/report.py tests/cli/test_quality_report.py tests/cli/test_detect_report.py
git commit -m "feat(report): seven-way agreement per session per eye -- each eye's DetectionQuality score under both glissade conventions beside the median of the same animal's earlier sessions scored alike (trace, validity paramset, metric, vocabulary, detectors), \"no history yet\" under three; \"undefined\", \"detection refused\" and \"not computed yet\" in place of figures; the both-eyes trace left out. The events-count test reads its own subsection, since two others now name the session

<trailer lines>"
```

---

### Task 4: The noise check on the reference recording

**Files:**
- Create: `tests/eye/detect/test_seven_way_validation.py`

**Interfaces — consumes:** Task 1's `blended_agreement`, `krippendorff_alpha`, `PSO_AS_SACCADE`. From the codebase: `ohdpi.read_ohdpi` and `read_columns`; `gaze.gaze_trace` and `purkinje_vector`; `glitch.repair_glitches`; `validity.validity_labels` and `DEFAULT_VALIDITY_PARAMS`; `velocity.velocity`; `registry.DETECTORS` with each `Detector.detect`, `defaults`, `vocabulary` and `copies_saccades_from`; `calibration.CalibrationMap` and `CalibrationModel`.

**Interfaces — produces:** nothing a later task reads.

This task adds a test and no code: it checks Tasks 1–3's score against spec §1.3's measurement. Its failing run is mutation N1.

- [ ] **Step 1: Write the test.** Apply this diff:

```diff
--- /dev/null
+++ b/tests/eye/detect/test_seven_way_validation.py
@@ -0,0 +1,94 @@
+"""The seven-way agreement on the lab's reference recording (design spec
+`2026-10-08-seven-way-agreement-design.md` sections 1.3 and 7): white noise
+the validity mask does not catch lowers the score.
+
+Gated on `WLPP_OHDPI_REFERENCE`, so skipped in CI; the recording is never
+committed. Imports nothing from `wl_preproc.schema`: the gaze is repaired and
+each detector run as `schema/detect.py::EyeDetection.make()` does, restated
+here. Each detector gets `Detector.defaults`, which is what its registered
+paramset holds: the shared `microsaccade_max_deg` equals every detector's own
+default today (`schema/detect.py::_eye_detection_params`)."""
+
+from __future__ import annotations
+
+import os
+
+import numpy as np
+import pytest
+
+from wl_preproc.eye.detect.consensus import PSO_AS_SACCADE, blended_agreement, krippendorff_alpha
+from wl_preproc.eye.detect.labels import Label
+
+MINUTES = 10
+NOISE_SD_DEG = 0.05
+_SCALE_P99_AT_DEG = 15.0
+
+
+def _scaled_affine_map(scale: float):
+    """Restated from `test_remodnav_validation.py`, not imported."""
+    from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel
+
+    return CalibrationMap(model=CalibrationModel.AFFINE, x=(0.0, scale, 0.0), y=(0.0, 0.0, scale))
+
+
+@pytest.fixture(scope="module")
+def left_eye():
+    """The first ten minutes of the left eye's glitch-repaired gaze, as
+    `schema/detect.py::_repaired_gaze` builds it, its quality flags and the
+    rate. The recording is uncalibrated, so its 99th percentile is put at 15
+    degrees, as the other validation tests do."""
+    sample = os.environ.get("WLPP_OHDPI_REFERENCE")
+    if not sample:
+        pytest.skip("WLPP_OHDPI_REFERENCE is not set -- see test_nystrom_holmqvist_validation.py")
+    from wl_preproc.eye.detect.glitch import repair_glitches
+    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
+    from wl_preproc.eye.gaze import gaze_trace, purkinje_vector
+    from wl_preproc.eye.ohdpi import read_columns, read_ohdpi
+
+    rec = read_ohdpi(sample)
+    # The mask below is given no frame gaps: they are rows of the whole
+    # recording, and this one has none.
+    assert not rec.frame_gaps
+    raw = {eye: purkinje_vector(sample, eye) for eye in ("Left", "Right")}
+    pooled_x = np.concatenate([np.abs(raw["Left"][:, 0]), np.abs(raw["Right"][:, 0])])
+    pooled_y = np.concatenate([np.abs(raw["Left"][:, 1]), np.abs(raw["Right"][:, 1])])
+    scale = _SCALE_P99_AT_DEG / max(float(np.percentile(pooled_x, 99)), float(np.percentile(pooled_y, 99)))
+    gaze, _repaired = repair_glitches(gaze_trace(sample, "Left", _scaled_affine_map(scale)), rec.fs_hz,
+                                      DEFAULT_VALIDITY_PARAMS.max_speed_deg_s, DEFAULT_VALIDITY_PARAMS.max_glitch_ms)
+    n = int(MINUTES * 60 * rec.fs_hz)
+    quality = read_columns(sample, ["LeftDataQuality"])["LeftDataQuality"]
+    return gaze[:n], quality[:n], rec.fs_hz
+
+
+def _seven_way(gaze, quality, fs_hz) -> tuple[float, float]:
+    """Krippendorff's alpha over every registered detector, glissades as
+    saccade, BMD abstaining on the saccades it copies; and the share of
+    samples the mask offered."""
+    from wl_preproc.eye.detect.registry import DETECTORS
+    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS, validity_labels
+    from wl_preproc.eye.detect.velocity import velocity
+
+    v = velocity(gaze, fs_hz)
+    offered = np.asarray(validity_labels(gaze, v, quality, (), DEFAULT_VALIDITY_PARAMS).labels, dtype=object)
+    labels = {}
+    for name, detector in DETECTORS.items():
+        trace = offered.copy()
+        for run in detector.detect(gaze, v, offered, fs_hz, detector.defaults):
+            trace[run.start:run.stop] = run.label
+        labels[name] = np.where(trace == None, Label.FIXATION, trace)  # noqa: E711
+    result = blended_agreement(labels, {name: detector.vocabulary for name, detector in DETECTORS.items()},
+                               {name: detector.copies_saccades_from for name, detector in DETECTORS.items()},
+                               PSO_AS_SACCADE, krippendorff_alpha)
+    return result.value, float(np.mean(offered == None))  # noqa: E711
+
+
+def test_noise_the_mask_keeps_lowers_the_seven_way_score(left_eye):
+    """Spec section 1.3 measured 0.684 with no noise and 0.594 under 0.05
+    degrees, the mask keeping 0.991 of samples at both. A drop of more than
+    0.05 is asked for, with the mask's share unmoved."""
+    gaze, quality, fs_hz = left_eye
+    clean, clean_share = _seven_way(gaze, quality, fs_hz)
+    noisy_gaze = gaze + np.random.default_rng(0).normal(0.0, NOISE_SD_DEG, gaze.shape)
+    noisy, noisy_share = _seven_way(noisy_gaze, quality, fs_hz)
+    assert noisy_share == pytest.approx(clean_share, abs=0.001)
+    assert noisy < clean - 0.05, (clean, noisy)
```

- [ ] **Step 2: Run it without the recording**

Run: `env -u WLPP_OHDPI_REFERENCE .venv/bin/python -m pytest tests/eye/detect/test_seven_way_validation.py -q -p no:cacheprovider`
Expected: 1 skipped, as in CI.

- [ ] **Step 3: Run it on the recording**

Run: `WLPP_OHDPI_REFERENCE=$HOME/.cache/wl-preproc-references/OpenIris-2024Jul31-114628.txt .venv/bin/python -m pytest tests/eye/detect/test_seven_way_validation.py -q -p no:cacheprovider -o faulthandler_timeout=1800`
Expected: 1 passed, in about three minutes: every detector runs twice over 299,132 samples. Measured α 0.6845 with no noise and 0.5936 under 0.05°, the mask offering 0.9907 and 0.9906 of samples.

- [ ] **Step 4: Mutation check.** Measured against the final tree:
  - N1: noise too small to matter, `NOISE_SD_DEG = 0.01` [`test_noise_the_mask_keeps_lowers_the_seven_way_score`]. Spec §1.3 measured a drop of 0.003 at that level.

- [ ] **Step 5: Commit**

```bash
git add tests/eye/detect/test_seven_way_validation.py
git commit -m "test(consensus): the seven-way score falls under noise the mask keeps -- on the reference recording's first ten minutes, glitch-repaired as production repairs it, alpha under 0.05 degrees of white noise must fall more than 0.05 below alpha with none while the mask's offered share stays put; gated on WLPP_OHDPI_REFERENCE

<trailer lines>"
```

---

### Task 5: The records, and the full suite

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-seven-way-agreement-design.md`, `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-10-08-seven-way-agreement.md`

- [ ] **Step 1: The spec's amendments.** Apply this diff:

```diff
--- a/docs/superpowers/specs/2026-10-08-seven-way-agreement-design.md
+++ b/docs/superpowers/specs/2026-10-08-seven-way-agreement-design.md
@@ -96,6 +96,7 @@ been run against it.*
     comma-separated, so a row says which detectors it rests on.
 - **Which keys it runs on.** A (session, trace, validity paramset) whose `EyeDetection` rows are
   computed for every registered `eye_detection` paramset. Two rules follow:
+  *(Amendment 4: every registered paramset whose detector the code still has.)*
   - **It never blends a partial set.** A row written while one detector's job was still pending
     or had errored would never be recomputed when that detector caught up, since DataJoint never
     revisits a populated key.
@@ -105,6 +106,9 @@ been run against it.*
     for the new set until they are detected by it too. Their existing rows stay, each naming its
     own `detectors`.
 
+    *Corrected by amendment 1: an older session gets no row for the new set, even once detected
+    by it.*
+
 ## 4. Computing it
 
 In `wl_preproc/eye/detect/consensus.py`, beside the pairwise metrics:
@@ -137,6 +141,8 @@ session per eye (24 h)"**. Computed in `build_report`, never `gather_readings`.
   "not computed yet" where no row exists.
 - **The both-eyes trace is stored and left out of the report,** as vigor's is.
 
+*Amendment 2 adds `detectors` to what the history matches; amendment 3 places "no history yet".*
+
 ## 6. Wiring
 
 `daemon._computed_tables()` runs `DetectionQuality` after `DetectorAgreement`, below the
@@ -172,3 +178,24 @@ Each carries a dated pointer in the parent:
 - **§6, N-way:** the blended score is §2's Krippendorff's α, per trace and convention.
 - **§7:** `DetectionQuality`'s key and columns are §3's.
 - **§9:** the report's seven-way line is §5's.
+
+## Amendments, 2026-10-08, made while proving the plan
+
+1. **A session is blended once** (§3; plan Task 2). DataJoint keys a table's job queue on the
+   primary-key attributes it inherits, and `trace` is this table's own, so `key_source` collapses
+   it: one key, and one `make()`, per session and validity paramset, writing every trace that is
+   complete. No trace is stranded by that, since `EyeDetection.make()` writes all three traces of
+   one detector at once. But a session once blended is never revisited, so a paramset registered
+   later, an eighth detector say, leaves older sessions with their rows, each naming its own
+   `detectors`, and gives them no row for the new set even once they are detected by it. Blending
+   one anew means deleting its rows. §3 had said they would get one.
+2. **The history also matches `detectors`** (§5; plan Task 3). Another set of detectors is another
+   score: leaving one of the seven out moved the reference recording's α by as much as 0.035
+   (§1.2).
+3. **"No history yet" stands beside the session's own score** (§5; plan Task 3), which is shown
+   either way: *glissades as saccade 0.63 (no history yet, 2 earlier)*.
+4. **Only paramsets whose detector the code still has are waited for** (§3; plan Task 2). A
+   paramset registered for a detector since removed from the registry can only ever error, so
+   counting it would stop the table for every later session, with the report saying "not computed
+   yet" for good. The full suite found it: another module leaves such a paramset registered. The
+   NWB stage met the same paramset in the full suite, and no longer waits on it either.
```

- [ ] **Step 2: The handoff, the checkpoint and `wl.yaml`.** Apply these diffs, dated the day the plan is executed, and with the execution method the requester chose:

```diff
--- a/docs/CHECKPOINT.md
+++ b/docs/CHECKPOINT.md
@@ -199,12 +199,21 @@ requester chose to merge the same day; true when written.*
 >   `request.ActivationProbeRun`). It is the bulk of what remains, and outranks
 >   every hardware-free item once the machine exists.
 >
-> **Hardware-free:** the requester's choices of 2026-10-07 and 2026-10-08 are
-> merged: the saccade main sequence and vigor (`ff8aeb9`), the U'n'Eye minors
-> branch's three deferred minors (`7dcf37f`), and the main sequence's four
-> (`b22af97`). Ask what comes next. Open and small: M4 (left open by the
-> requester's choice). Fine-tuning U'n'Eye to the DPI tracker waits for
-> hand-labelled lab data.
+> **Hardware-free:** seven-way detector agreement (the saccade spec's §6 and
+> §7), the requester's choice of 2026-10-08: built on
+> `spec/seven-way-agreement`, not merged
+> (`handoffs/2026-10-08-seven-way-agreement.md`). Each detection trace's
+> Krippendorff's α across every registered detector, per glissade convention,
+> and the report's line for each eye against the animal's own history. Next:
+> the whole-branch review, then the merge question. Open and small: M4 (left
+> open by the requester's choice). Fine-tuning U'n'Eye to the DPI tracker
+> waits for hand-labelled lab data.
+>
+> *Until 2026-10-10 this said the requester's choices of 2026-10-07 and
+> 2026-10-08 were merged: the saccade main sequence and vigor (`ff8aeb9`), the
+> U'n'Eye minors branch's three deferred minors (`7dcf37f`), and the main
+> sequence's four (`b22af97`); and to ask what comes next. True when
+> written.*
 >
 > *Until 2026-10-08 this also listed the main sequence's four deferred minors
 > as open; true when written.*
--- /dev/null
+++ b/docs/handoffs/2026-10-08-seven-way-agreement.md
@@ -0,0 +1,88 @@
+# Seven-way detector agreement
+
+**Branch:** `spec/seven-way-agreement`, forked from `main` at `95c8b54`.
+- **Design:** `docs/superpowers/specs/2026-10-08-seven-way-agreement-design.md`, an addendum to the
+  saccade-detection spec's §6 and §7, with amendments 1–4.
+- **Plan:** `docs/superpowers/plans/2026-10-08-seven-way-agreement.md`.
+- **The requester's choices,** on 2026-10-08:
+  - this work next, from what was open;
+  - Krippendorff's α as the score, over every registered detector;
+  - each session shown against the same animal's earlier sessions;
+  - the design, then the written spec;
+  - the plan run in one session ("Native"), with one whole-branch review.
+
+## 1. What was built
+
+- **`eye/detect/consensus.py`,** pure functions beside the pairwise metrics:
+  - `krippendorff_alpha`: nominal, over samples at least two detectors rated, NaN where every
+    rating is one value;
+  - `blended_vocabulary`: `shared_vocabulary` folded across every detector's declared vocabulary;
+  - `blended_agreement`: each trace coarsened into that vocabulary, the mask's samples no one's
+    rating, and a detector abstaining on the saccades it copies (BMD on Engbert–Kliegl's);
+  - `BLENDED_METRICS`, a registry holding Krippendorff's α.
+- **`schema/consensus.py`:** `DetectionQuality`, a row per trace, validity paramset, metric,
+  vocabulary and glissade convention, with the samples compared and the detectors blended.
+  - A trace is blended only when every registered `eye_detection` paramset computed it; a refused
+    trace gets no row.
+  - One key per session and validity paramset (amendment 1).
+- **The daemon** runs it after `DetectorAgreement`.
+- **The report** has a new subsection, "Seven-way agreement per session per eye (24 h)": one line
+  per session per eye, both conventions, each against the median of the animal's earlier sessions
+  scored alike (amendment 2), or the reason there is no figure.
+
+## 2. What it measured
+
+- **On the reference recording** (spec §1): α 0.615–0.631 across eyes and conventions; untuned
+  U'n'Eye pulls it down by 0.006–0.015; under 0.05° of added noise it falls from 0.684 to 0.594
+  while the mask keeps the same 0.991 of samples.
+- **The gated noise test,** on the same recording's first ten minutes (299,132 samples of the left
+  eye): α 0.6845 with no noise and 0.5936 under 0.05°, the mask offering 0.9907 and 0.9906 of
+  samples. At 0.01° the test fails, as §1.3's drop of 0.003 says it should.
+
+## 3. Tests
+
+- `tests/eye/detect/test_blended_agreement.py` (10): α against Krippendorff's own worked example
+  (0.743), perfect and chance agreement, a unit one detector rated, the undefined case; the folded
+  vocabulary of the seven in any order; coarsening, the mask and abstention.
+- `tests/schema/test_detection_quality_populate.py` (7): on the stepped session, a row for every
+  trace, metric and convention, each value the blend of the stored labels; on planted rows, a
+  trace blended only when every registered detector computed it, a session with no complete
+  trace not a candidate, a trace with no saccades stored as NULL, and a paramset for a detector
+  the code no longer has neither waited for nor blended (amendment 4); a real `wlpp daemon` pass
+  writing rows with nothing registered beforehand.
+- `tests/cli/test_quality_report.py` (7): a line against history, too little history, an
+  undefined score, a refused detection, a missing row, the both-eyes trace left out, and a
+  history of earlier sessions scored alike (trace, validity paramset, metric, vocabulary and
+  detectors).
+- `tests/eye/detect/test_seven_way_validation.py` (1, gated on `WLPP_OHDPI_REFERENCE`): noise the
+  mask keeps lowers the score.
+- `tests/cli/test_detect_report.py`: the events-count test reads its own subsection, since the
+  seven-way and vigor subsections name the session too.
+
+## 4. The full suite
+
+Run on both interpreters, with every reference variable set except `WLPP_OHDPI_REFERENCE`:
+- **3.11:** 2300 passed, 27 skipped, 1 deselected, 1 xfailed.
+- **3.13:** 2300 passed, 28 skipped, 1 xfailed.
+
+That is 24 more passed on each than `main`'s 2276 at `b22af97`: this branch's new tests, 10 + 7 +
+7. The one more skipped is the gated noise test.
+
+**Found by the full suite:** `test_detect_populate.py` leaves an `eye_detection` paramset
+registered for a detector it has removed from the registry. `DetectionQuality` counted it among
+the detectors to wait for, so no trace was ever complete after it, and two of the new tests failed
+in the suite's order on both interpreters. In production the same would follow from removing a
+detector. Only paramsets whose detector the code has are waited for now (amendment 4);
+`test_a_paramset_for_a_detector_the_code_no_longer_has_is_not_waited_for` failed first, then
+passed.
+
+## 5. What is next
+
+- **The whole-branch review, then the merge question.** After the merge: CI on both interpreters,
+  and the pointer commit (the checkpoint's header and `wl.yaml`'s `describes`).
+- **Not in this round** (spec §8): the score in the NWB file, an event-level seven-way score, and
+  any fixed threshold.
+- **A detector registered later** gives older sessions no new row (amendment 1); their rows stay,
+  naming the detectors they blend.
+- **The numbers mean degrees only once sessions are calibrated.** The reference recording's
+  degrees are a guessed scale.
--- a/wl.yaml
+++ b/wl.yaml
@@ -33,9 +33,12 @@ status:
     rig: record a few minutes with the sync box live, since the barcode and
     timebase alignment path has never met real data and cannot with the
     recording the lab has. With the compute machine: Phase 2b and the NWB's
-    units come before everything below. Hardware-free: the requester's
-    choices of 2026-10-07 and 2026-10-08 are merged; ask what comes next. Open
-    and small: M4. Fine-tuning U'n'Eye waits for hand-labelled lab data.
+    units come before everything below. Hardware-free: seven-way detector
+    agreement, the requester's choice of 2026-10-08, built on
+    spec/seven-way-agreement and not merged
+    (docs/handoffs/2026-10-08-seven-way-agreement.md); its whole-branch review,
+    then the merge question. Open and small: M4. Fine-tuning U'n'Eye waits for
+    hand-labelled lab data.
     Done: the main sequence's four deferred minors (b22af97): a condition held
     to its own block's span, and the four untested paths tested. Before it,
     the U'n'Eye minors branch's three deferred minors (7dcf37f): numba
```

- [ ] **Step 3: Check the manifest.** Run `.venv/bin/wl-check`. Expected: `wl.yaml: no findings`.

- [ ] **Step 4: The full suite, on both interpreters,** with every reference variable set except `WLPP_OHDPI_REFERENCE`:

```bash
R=$HOME/.cache/wl-preproc-references
export WLPP_NSLR_REFERENCE=$R WLPP_BMD_REFERENCE=$R/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$R/bmd/boost_1_86_0 WLPP_ANDERSSON_DATA=$R/andersson
unset WLPP_OHDPI_REFERENCE
.venv/bin/python -m pytest -q -p no:cacheprovider -o faulthandler_timeout=1800
~/.cache/wl-preproc-venv313/bin/python -m pytest -q -p no:cacheprovider -o faulthandler_timeout=1800
```

A test still running after 30 minutes prints every thread's stack to the log, so a hang shows itself.

Expected: 3.11, 2300 passed, 27 skipped, 1 deselected, 1 xfailed; 3.13, 2300 passed, 28 skipped, 1 xfailed. That is 24 more passed on each than `main`'s 2276 at `b22af97`: this plan's new tests, 10 + 7 + 7. The one more skipped is Task 4's gated test.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-10-08-seven-way-agreement-design.md docs/handoffs/2026-10-08-seven-way-agreement.md docs/CHECKPOINT.md wl.yaml
git commit -m "docs: seven-way detector agreement -- the spec's amendments 1-4, the handoff, and the checkpoint's and wl.yaml's lines

<trailer lines>"
```
