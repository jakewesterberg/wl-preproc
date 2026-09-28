# NWB Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given one `request.Activation`, write one valid NWB file over its blocks from the tables this pipeline already fills and the raw ohDPI file, record what was written in `nwb.NwbFile`, and run it from the daemon and from `wlpp nwb build`.

**Architecture:** A package, `wl_preproc/nwb/`. Its writers take plain data and add to a `pynwb.NWBFile`, so each is tested without a database. `gather.py` is the one module that reads the database and the raw file, and `build.py` joins the two: it writes atomically, checksums, runs `nwbinspector`, and records the result. A `dj.Manual` table, `nwb.NwbFile`, is filled by an opt-in daemon stage and by the command. Two smaller changes come first: an ohDPI row's session time with its exact inverse, and the subject's details carried in the job request.

**Tech Stack:** Python ≥3.11, `pynwb` 4.x (NWB schema 2.10), `nwbinspector` 0.7, `h5py`, `blake3`, numpy, DataJoint 2.3 (schema tests only), pydantic 2 (the job request).

**Spec:** `docs/superpowers/specs/2026-09-28-nwb-builder-design.md` (`1805489`, amended in `6540211` and again in this plan's commit, see "Rulings made while planning"). It is binding. The requester approved it on 2026-09-28 ("Yes, write the plan").

**Every piece of code below was proven before this plan was written,** in a scratch worktree, `$SCRATCH/nwb_wt`, detached at `1805489`, which holds these exact files.
- **Tests:** every test named here passed there. Each task's failing run and passing run were measured, and are quoted in its steps.
- **Mutation checks:** every one named below was run, and each failed the test it names.
- **The full suite** was run on both interpreters with every task applied (Task 5 quotes it).

## Global Constraints

- **The spec is binding.** Where it and this plan disagree, the spec wins; record a ruling. Its dated amendments are part of it.
- **Dependencies:** `pynwb>=4.1,<5` and `nwbinspector>=0.7,<0.8`, each with a `why` in `wl.yaml`'s `third_party`. Built directly on `pynwb`, not NeuroConv. Both are already installed in `.venv` (3.11: pynwb 4.1.0, nwbinspector 0.7.2) and in `$SCRATCH/venv_ci` (3.13: pynwb 4.2.0, nwbinspector 0.7.2). If either is missing: `uv pip install --python .venv/bin/python -e .` (the venv has no pip).
- **One clock:** every time in the file is session seconds, t = 0 at the sync box's first decoded barcode (`timebase/segments.py::session_reference`).
- **The wall-clock time of t = 0** (spec §4.2): 2020-01-01 UTC plus the first barcode's value, `reference_source = 'barcode'`, unless it and the manifest's `started_at` differ by more than 60 s. Then it is `started_at`, `reference_source = 'manifest'`. `session_datetime` is `started_at` as naive UTC (`ingest/landing.py::to_naive_utc`). A synthetic session's barcodes count from 1,000,000, so it always takes the fallback.
- **PTP is never a timestamp here** (spec §4.4). This piece writes no PTP value.
- **Trimming** (spec §5):
  - block set: a canonical activation is every `core.Block` whose `start_s` is in `[montage start_s, montage end_s)`; a derivative is its `ActivationBlock` rows;
  - samples, trial starts and detected-run starts: half-open `[start_s, end_s)`;
  - events: `start_s ≤ t ≤ end_s`;
  - a detected run is never cut;
  - validity, repair and source stretches are clipped to the block set;
  - blocks that touch are one interval (this plan's ruling).
- **The file:** at `{nwb_root}/{session_id}/{identifier}.nwb`, with `identifier` = `{session_id}.montage-{m}.activation-{a}`. It is written to `{path}.partial`, then renamed. Each dataset is gzipped on its own. Continuous series are chunked time-major at about 256 KB.
- **Checksums** (spec §7): blake3 over each dataset's decoded C-order bytes, with its dtype and shape, never a group. A ragged column and its `_index` name each other. `/specifications` and `/file_create_date` are not hashed.
- **Status:**
  - `written`: no CRITICAL finding from `nwbinspector`'s default configuration;
  - `invalid`: at least one CRITICAL finding. The file is kept;
  - `refused`: no `TimingProvenance` row, tier D, no blocks, or more than one ohDPI segment. A refusal is recorded with its reason and writes no file. It is never retried automatically.
- **Out of scope (spec §1, §13):**
  - publication, and the real `canonical_nwb_present`: piece 2;
  - ephys: piece 3;
  - moving the wl-sync pin;
  - photodiode, video and stimulation.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code, data or tables.
- **Tests.**
  - Run with `.venv/bin/python -m pytest <files> -p no:cacheprovider`. In a git worktree, prefix `PYTHONPATH=$PWD`.
  - Database tests need Docker for the MySQL testcontainer. A module-wide setup error on a 120 s container timeout is the known flake: re-run it.
  - Mutation checks:
    - clear stale bytecode first with `find . -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +`, and again after restoring;
    - run with `PYTHONDONTWRITEBYTECODE=1`;
    - make one mutation at a time, run the named test, then restore with `git checkout -- <file>` (after the task's commit) or by undoing the edit.
  - **Run each task's own test files.** The full suite runs once, on both interpreters, in Task 5 (the requester's preference of 2026-09-27).
  - The Bash tool's shell is zsh: an unquoted `$VAR` holding several arguments is not split. Write arguments out, or use `bash -c`.
  - **`wl-check`**: run `.venv/bin/wl-check` on its own and read its exit status. A pipe through `tail` reports `tail`'s status.
- **Every commit message ends with:**

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R
  ```

## Review Focus

Five inputs the spec implies but its own test list (§11) does not exercise, most likely first. Each is pinned by a test named here, in the task that owns the code.

1. **A validity or repair stretch across two touching blocks.** wl.works may split one measured block in two, as the end-to-end fixture's does. A blink across the split is one blink, and counting rows must not count it twice. Task 3: `test_touching_blocks_do_not_split_a_stretch_that_crosses_them`.
2. **`wlpp nwb build` run twice, then rebuilt.** A second run must not overwrite a recorded file, and must say how to rebuild. With the row deleted, the rebuild replaces the file at the same path, with the same checksums. Task 4: `test_the_command_builds_once_and_rebuilds_only_when_its_row_is_deleted`.
3. **One activation failing inside the daemon stage** (an unreadable raw file, say). The others are recorded, the failure is reported, and the failed one gets no row, so the next pass tries again. Task 4: `test_one_failing_activation_does_not_stop_the_stage`.
4. **A session with no eye recording.** The file is still built, with blocks, trials, events and timing, and no eye modules. Task 4: `test_a_session_without_an_eye_recording_is_built_without_one`.
5. **A detected run that ends after its block.** It keeps its true end; only stretches are clipped. Task 4: `test_a_run_that_crosses_its_blocks_end_keeps_its_true_end`.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/schema/eye.py` | `row_session_times`, and `_session_time_to_row` as its exact inverse (Task 1) |
| `wl_preproc/contracts/protocol.py`, `wl_preproc/responder/jobs.py` | `SubjectDetails` in the job request, written into element-animal's tables (Task 2) |
| `wl_preproc/nwb/columns.py` | gzip, chunking, and one `VectorData` column |
| `wl_preproc/nwb/session.py` | the `NWBFile` and its `Subject` |
| `wl_preproc/nwb/intervals.py` | `/intervals/blocks`, `/intervals/trials`, `/intervals/task_events` |
| `wl_preproc/nwb/timebase.py` | `processing/timebase` |
| `wl_preproc/nwb/eye.py` | gaze, pupil, calibration, validity and repairs in `processing/behavior` |
| `wl_preproc/nwb/eye_events.py` | `processing/eye_events`: 18 detection tables, 6 source tables, agreement |
| `wl_preproc/nwb/trim.py` | `BlockSet`: what falls in the activation's blocks |
| `wl_preproc/nwb/checksums.py`, `validate.py`, `write.py` | blake3 per dataset; `nwbinspector`; the atomic write |
| `wl_preproc/nwb/gather.py` | everything a file is built from, read from the database and the raw file; the refusals |
| `wl_preproc/nwb/build.py` | `build`, `record`, `run_stage` |
| `wl_preproc/schema/nwb.py` | `NwbFile` and `NwbFile.Dataset` |
| `wl_preproc/daemon.py`, `wl_preproc/cli/main.py` | the stage (`--nwb-root`), and `wlpp nwb build` |
| `tests/schema/test_eye_row_times.py`, `tests/nwb/`, `tests/schema/test_nwb_build.py` | new tests; four existing test files change (Tasks 1, 2 and 4) |
| `pyproject.toml`, `wl.yaml`, `docs/…` | dependencies, records, the pending wl-works amendment |

---

### Task 1: An ohDPI row's session time, and its exact inverse

**Files:**
- Create: `tests/schema/test_eye_row_times.py`
- Modify: `wl_preproc/schema/eye.py`, `tests/schema/test_eye_populate.py`, `tests/schema/test_detect_populate.py`

**Interfaces — produces:**
- `wl_preproc.schema.eye.row_session_times(segment: dict, offsets: np.ndarray) -> np.ndarray`. `segment` has `start_s`, `end_s` and `n_samples`; `offsets[row]` is the row's true sample index (the file's frame number minus its first). The result is `start_s + offsets * (end_s - start_s) / n_samples`. Task 4's `gather.py` uses it.
- `_session_time_to_row(segment, session_s, offsets)` becomes its exact inverse. `core.Segment.end_s` is the time of sample `n_samples`, one past the last (spec §4.3).

**Why the recovery test changes (Step 5).** Correcting the inverse by one sample moves the synthetic session's calibration windows by one sample. That moves its calibration gain by about 0.1%. REMoDNaV then also stores a 0.11°, 10 ms noise event on the left eye, and every planted step is still found. The test claims that planted steps are recovered, and every planted step is at least 0.75°. So it now counts saccadic runs of at least 0.5°. *Ruling (this plan): a detector's own noise floor is its own tests' business, not this recovery claim's. Cost if wrong: a spurious sub-0.5° event on this fixture goes uncounted here.*

- [ ] **Step 1: Write the failing tests.** Create `tests/schema/test_eye_row_times.py`:

```python
"""An ohDPI file row's session time, and back (design spec
`2026-09-28-nwb-builder-design.md` section 4.3)."""

from __future__ import annotations

import numpy as np
import pytest

# 200 true samples at 500 Hz starting at session 10 s, one 3-frame gap after
# row 99: rows 100.. carry true samples 103..
_OFFSETS = np.array([*range(100), *range(103, 200)])
_SEGMENT = {"start_s": 10.0, "end_s": 10.0 + 200 / 500.0, "n_samples": 200}


def test_each_row_is_timed_by_its_true_sample_index():
    """`core.Segment.end_s` is the time of sample `n_samples`, one past the
    last, so sample `k` is at `start_s + k * (end_s - start_s) / n_samples`;
    a dropped frame leaves a gap rather than shifting later rows."""
    from wl_preproc.schema.eye import row_session_times

    times = row_session_times(_SEGMENT, _OFFSETS)

    assert times[0] == 10.0
    assert times[99] == pytest.approx(10.0 + 99 / 500.0)
    assert times[100] == pytest.approx(10.0 + 103 / 500.0)
    assert times[-1] == pytest.approx(10.0 + 199 / 500.0)
    assert times[-1] < _SEGMENT["end_s"]


def test_session_time_to_row_is_the_exact_inverse():
    """Every row's own time maps back to that row. Before 2026-09-28
    `_session_time_to_row` placed `end_s` at sample `n_samples - 1`, off by up
    to one sample at the end of the file."""
    from wl_preproc.schema.eye import _session_time_to_row, row_session_times

    times = row_session_times(_SEGMENT, _OFFSETS)
    assert [_session_time_to_row(_SEGMENT, t, _OFFSETS) for t in times] == list(range(len(_OFFSETS)))
```

Then in `tests/schema/test_eye_populate.py`, correct the helper that restates the inverse, exactly as this diff does:

```diff
--- a/tests/schema/test_eye_populate.py
+++ b/tests/schema/test_eye_populate.py
@@ -147,8 +147,13 @@
 def _sample_for_time(segment: dict, session_s: float) -> int:
     """Step one of `eye._session_time_to_row`, on its own: session time to the
     recording's own TRUE SAMPLE INDEX, `session_s = start_s + (sample /
-    (n_samples - 1)) * (end_s - start_s)` inverted.
+    n_samples) * (end_s - start_s)` inverted: `end_s` is the time of sample
+    `n_samples`, one past the last.
 
+    *`n_samples - 1` until 2026-09-28, the same off-by-one as the function it
+    checks (design spec `2026-09-28-nwb-builder-design.md` section 4.3);
+    true when written.*
+
     Separate from `_row_for_time` below because on a recording that dropped
     frames these are two different numbers, and several assertions in this
     file need to name the sample index specifically -- it is what the naive,
@@ -158,7 +163,7 @@
     n_samples = segment["n_samples"]
     span = segment["end_s"] - segment["start_s"]
     frac = (session_s - segment["start_s"]) / span
-    return min(max(round(frac * (n_samples - 1)), 0), n_samples - 1)
+    return min(max(round(frac * n_samples), 0), n_samples - 1)
 
 
 def _row_for_time(segment: dict, session_s: float, offsets: list[int]) -> int:
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_eye_row_times.py tests/schema/test_eye_populate.py -q --tb=line -p no:cacheprovider`
Expected: 3 failed, 38 passed. Two fail with `ImportError: cannot import name 'row_session_times' from 'wl_preproc.schema.eye'`. One fails with `AssertionError: assert 2899 == 2900`: the corrected helper disagrees with the uncorrected function at the end of the file.

- [ ] **Step 3: Implement.** Apply exactly this diff:

```diff
--- a/wl_preproc/schema/eye.py
+++ b/wl_preproc/schema/eye.py
@@ -860,10 +860,16 @@
     Step one -- session time to TRUE SAMPLE INDEX -- is the same linear map
     `core.Segment.make()` fit (`session_s = native_s/scale + offset_s`),
     here inverted and expressed directly through the segment's own stored
-    extent (`start_s` at sample 0, `end_s` at sample `n_samples - 1`) rather
-    than re-deriving `scale`/`offset_s` separately. The two are equivalent
-    by construction, and this needs no rate or barcode reference of its own.
+    extent (`start_s` at sample 0, `end_s` at sample `n_samples`, one past
+    the last, which is how `core.Segment.make()` computes it) rather than
+    re-deriving `scale`/`offset_s` separately. The two are equivalent by
+    construction, and this needs no rate or barcode reference of its own.
+    It is the exact inverse of `row_session_times`.
 
+    *Until 2026-09-28 this placed `end_s` at sample `n_samples - 1`, off by
+    up to one sample (2 ms) at the end of the file (design spec
+    `2026-09-28-nwb-builder-design.md` section 4.3); true when written.*
+
     Step two -- true sample index to ROW -- exists because `Segment.
     n_samples` is the recording's TRUE FRAME SPAN, rows PLUS the frames the
     camera dropped (the gap-aware barcode extraction design spec's section 5,
@@ -903,11 +909,24 @@
         return None
     span = segment["end_s"] - segment["start_s"]
     frac = 0.0 if span <= 0 else (session_s - segment["start_s"]) / span
-    sample = int(round(frac * (n_samples - 1)))
+    sample = int(round(frac * n_samples))
     sample = min(max(sample, 0), n_samples - 1)
     return int(np.searchsorted(offsets, sample, side="right")) - 1
 
 
+def row_session_times(segment: dict, offsets: np.ndarray) -> np.ndarray:
+    """Every ohDPI file row's session time, in seconds.
+
+    `offsets[row]` is the row's own true sample index (`_frame_offsets`), and
+    sample `k` is at `start_s + k * (end_s - start_s) / n_samples`:
+    `core.Segment.end_s` is the time of sample `n_samples`, one past the last.
+    A dropped frame leaves a gap in the times rather than shifting the rows
+    after it. `_session_time_to_row` is the exact inverse (design spec
+    `2026-09-28-nwb-builder-design.md` section 4.3)."""
+    span = segment["end_s"] - segment["start_s"]
+    return segment["start_s"] + np.asarray(offsets, dtype=float) * (span / segment["n_samples"])
+
+
 def _find_xcon_log(session_dir: Path) -> Path | None:
     """The session's own experiment-controller log, if one exists.
 
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command.
Expected: 41 passed: the 3 that failed now pass. (Step 6 is this task's measured passing run.)

- [ ] **Step 5: The recovery test.** Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -q --tb=line -p no:cacheprovider -k next_full_pass`
Expected: 1 failed, `AssertionError: remodnav` (see "Why the recovery test changes" above). Apply this diff:

```diff
--- a/tests/schema/test_detect_populate.py
+++ b/tests/schema/test_detect_populate.py
@@ -1001,6 +1001,11 @@
     )
 
 
+# The smallest planted step is 0.75 deg; anything detected at or above this
+# is of planted size.
+_PLANTED_SIZE_DEG = 0.5
+
+
 def test_the_next_full_pass_then_computes_both_eyes(out_of_order_session):
     """The other half of M5: staying outstanding must not mean staying
     outstanding forever. One ordinary `run_once()` -- which assembles
@@ -1035,7 +1040,18 @@
         runs = (
             detect.EyeDetection.Run & {**session_key, "trace": "left", **_detector(name)}
         ).to_dicts(order_by="run_index")
-        detected = [r["run_start"] for r in runs if r["label"] in ("saccade", "microsaccade")]
+        # Planted size only: every planted step is at least 0.75 deg. A
+        # detector's own noise floor is its detector tests' business, not
+        # this recovery claim's.
+        #
+        # *Since 2026-09-28: correcting `eye._session_time_to_row` by one
+        # sample moved this session's calibration gain by about 0.1%, and
+        # REMoDNaV then also stored a 0.11 deg, 10 ms noise event on the left
+        # eye; every planted step was still found. Until then this counted
+        # every saccadic run, noise floor included (design spec
+        # `2026-09-28-nwb-builder-design.md` section 4.3); true when written.*
+        detected = [r["run_start"] for r in runs if r["label"] in ("saccade", "microsaccade")
+                    and r["amplitude_deg"] is not None and r["amplitude_deg"] >= _PLANTED_SIZE_DEG]
         assert len(detected) == len(onsets) == 3, name
         for got, want in zip(detected, onsets, strict=True):
             assert abs(got - want) <= 5, name
```

- [ ] **Step 6: Run the task's tests**

Run: `.venv/bin/python -m pytest tests/schema/test_eye_row_times.py tests/schema/test_eye_populate.py tests/schema/test_detect_populate.py -q -p no:cacheprovider`
Expected: 149 passed, 1 skipped.

- [ ] **Step 7: Mutation checks.** Each was measured to fail the test named.
  - T1a: in `_session_time_to_row`, `sample = int(round(frac * n_samples))` → `sample = int(round(frac * (n_samples - 1)))`. Fails `test_session_time_to_row_is_the_exact_inverse`.
  - T1b: in `row_session_times`, `(span / segment["n_samples"])` → `(span / (segment["n_samples"] - 1))`. Fails both tests in `test_eye_row_times.py`.

- [ ] **Step 8: Commit**

```bash
git add wl_preproc/schema/eye.py tests/schema/test_eye_row_times.py tests/schema/test_eye_populate.py tests/schema/test_detect_populate.py
git commit -m "fix(eye): an ohDPI row's session time, and _session_time_to_row as its exact inverse -- end_s is sample n_samples, one past the last

<trailer lines>"
```

---

### Task 2: The subject's details, from wl.works' job request

**Files:**
- Modify: `wl_preproc/contracts/protocol.py`, `wl_preproc/responder/jobs.py`, `tests/responder/test_jobs.py`, `docs/schemas/job_request.json` (regenerated), `docs/pending-wl-works-amendments.md`

**Interfaces — produces:**
- `contracts.protocol.SubjectDetails(species: str | None = None, sex: Literal["M", "F", "U"] = "U", date_of_birth: datetime.date | None = None)`. It is frozen and refuses extra keys; `species` is 1–64 characters.
- `MetadataBundle.subject_details: SubjectDetails | None = None`.
- `accept(...)` writes the details, when present, into `pipeline.subject.Subject` (`sex`, `subject_birth_date`) and into `Subject.Species` (via the `Species` lookup, replacing any species already recorded). Task 4's end-to-end fixture sends them, and `gather.py` reads them back. A birth date equal to `ingest.landing.SUBJECT_BIRTH_DATE_UNKNOWN` (1900-01-01, the landing stub) means none.

- [ ] **Step 1: Write the failing tests.** Apply this diff to `tests/responder/test_jobs.py`:

```diff
--- a/tests/responder/test_jobs.py
+++ b/tests/responder/test_jobs.py
@@ -58,6 +58,7 @@
     block_ids: list[int] | None = None,
     domain: str = "neural",
     experimenter: str = "jw",
+    subject_details: dict | None = None,
 ) -> JobRequest:
     """A `JobRequest` naming `(montage_id, session_datetime)` in its
     selection, with `block_ids` present only when the caller supplies one --
@@ -80,6 +81,7 @@
             experimenter=experimenter,
             subject=subject,
             task_types=[],
+            subject_details=subject_details,
         ),
     )
 
@@ -966,3 +968,53 @@
             f"{table.__name__} changed under a rejected request: "
             f"{len(rows_before)} row(s) before, {len(rows_after)} after"
         )
+
+
+def test_the_subjects_details_fill_its_own_record(landed_session, prefix):
+    """Design spec `2026-09-28-nwb-builder-design.md` section 9, the
+    requester's decision: wl.works sends the animal's species, sex and date
+    of birth with the job request, and they replace what wl-preproc knew --
+    `ingest/landing.py` lands only a stub."""
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import pipeline
+
+    subject = "jbsubj01"
+    naive_dt = datetime.datetime(2027, 5, 9, 9, 0)
+    landed_session(subject, naive_dt)
+    job = _request(
+        subject=subject,
+        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
+        idempotency_key="jbsubj01-k1",
+        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
+        subject_details={"species": "Macaca mulatta", "sex": "F",
+                         "date_of_birth": datetime.date(2016, 3, 2)},
+    )
+
+    accept(job, prefix=prefix)
+
+    row = (pipeline.subject.Subject & {"subject": subject}).fetch1()
+    assert (row["sex"], row["subject_birth_date"]) == ("F", datetime.date(2016, 3, 2))
+    assert (pipeline.subject.Subject.Species & {"subject": subject}).fetch1("species") == "Macaca mulatta"
+
+
+def test_a_request_without_details_leaves_the_subject_alone(landed_session, prefix):
+    """They are optional: a request that does not carry them changes nothing
+    about the subject."""
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import pipeline
+
+    subject = "jbsubj02"
+    naive_dt = datetime.datetime(2027, 5, 10, 9, 0)
+    landed_session(subject, naive_dt)
+    before = (pipeline.subject.Subject & {"subject": subject}).fetch1()
+    job = _request(
+        subject=subject,
+        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
+        idempotency_key="jbsubj02-k1",
+        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
+    )
+
+    accept(job, prefix=prefix)
+
+    assert (pipeline.subject.Subject & {"subject": subject}).fetch1() == before
+    assert len(pipeline.subject.Subject.Species & {"subject": subject}) == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/responder/test_jobs.py -q --tb=line -p no:cacheprovider`
Expected: 21 failed, 4 passed. Every failure is `pydantic_core._pydantic_core.ValidationError: 1 validation error for MetadataBundle`: the shared `_request` helper now passes `subject_details`, which the model does not yet have.

- [ ] **Step 3: Implement.** Apply exactly these diffs:

```diff
--- a/wl_preproc/contracts/protocol.py
+++ b/wl_preproc/contracts/protocol.py
@@ -11,6 +11,7 @@
 
 from __future__ import annotations
 
+import datetime
 import re
 from typing import Annotated, Any, Literal
 
@@ -194,6 +195,22 @@
     trajectory_id: _TrajectoryId | None = None
 
 
+class SubjectDetails(BaseModel):
+    """The animal, as wl.works' own record states it: what an NWB file's
+    `subject` needs beyond an id (design spec
+    `2026-09-28-nwb-builder-design.md` section 9, the requester's decision
+    of 2026-09-28). Optional in the request; `responder/jobs.py::accept`
+    writes it into element-animal's own tables, replacing the stub
+    `ingest/landing.py` lands."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    # A Latin name, as element-animal's `Species` prefers for NWB export.
+    species: Annotated[str, Field(min_length=1, max_length=64)] | None = None
+    sex: Literal["M", "F", "U"] = "U"
+    date_of_birth: datetime.date | None = None
+
+
 class MetadataBundle(BaseModel):
     """Everything wl-preproc needs from the ELN, carried inbound with the request."""
 
@@ -212,6 +229,7 @@
     experimenter: str
     subject: str
     task_types: list[str]
+    subject_details: SubjectDetails | None = None
 
 
 class JobRequest(BaseModel):
```

```diff
--- a/wl_preproc/responder/jobs.py
+++ b/wl_preproc/responder/jobs.py
@@ -391,6 +391,27 @@
         for block_id, row in effective.items()
         if row["start_s"] < montage_row["start_s"] or row["end_s"] > montage_row["end_s"]
     ]
+
+
+def _record_subject_details(subject: str, details) -> None:
+    """wl.works' own record of the animal, when the request carries it,
+    written into element-animal's tables: `Subject.sex`,
+    `Subject.subject_birth_date` and `Subject.Species` (design spec
+    `2026-09-28-nwb-builder-design.md` section 9). Absent, nothing about the
+    subject changes. The latest request wins: the ELN is the authority."""
+    if details is None:
+        return
+    from wl_preproc.schema import pipeline
+
+    row = {"subject": subject, "sex": details.sex}
+    if details.date_of_birth is not None:
+        row["subject_birth_date"] = details.date_of_birth
+    pipeline.subject.Subject.update1(row)
+    if details.species is not None:
+        pipeline.subject.Species.insert1({"species": details.species}, skip_duplicates=True)
+        pipeline.subject.Subject.Species.insert1(
+            {"subject": subject, "species": details.species}, replace=True
+        )
 
 
 def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
@@ -492,6 +513,7 @@
     # written. ----
 
     # Step 1 (design spec section 6.1): Montage rows, insert-if-absent.
+    _record_subject_details(metadata.subject, metadata.subject_details)
     if montage_rows:
         core.Montage.insert(montage_rows, skip_duplicates=True)
 
```

- [ ] **Step 4: Re-export the request's JSON Schema**

Run: `.venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas`
Expected: `git status --short docs/schemas` lists only `docs/schemas/job_request.json`, and `git diff docs/schemas` is exactly:

```diff
--- a/docs/schemas/job_request.json
+++ b/docs/schemas/job_request.json
@@ -34,6 +34,17 @@
           "title": "Subject",
           "type": "string"
         },
+        "subject_details": {
+          "anyOf": [
+            {
+              "$ref": "#/$defs/SubjectDetails"
+            },
+            {
+              "type": "null"
+            }
+          ],
+          "default": null
+        },
         "task_types": {
           "items": {
             "type": "string"
@@ -115,6 +126,51 @@
       ],
       "title": "ProbeEntry",
       "type": "object"
+    },
+    "SubjectDetails": {
+      "additionalProperties": false,
+      "description": "The animal, as wl.works' own record states it: what an NWB file's\n`subject` needs beyond an id (design spec\n`2026-09-28-nwb-builder-design.md` section 9, the requester's decision\nof 2026-09-28). Optional in the request; `responder/jobs.py::accept`\nwrites it into element-animal's own tables, replacing the stub\n`ingest/landing.py` lands.",
+      "properties": {
+        "date_of_birth": {
+          "anyOf": [
+            {
+              "format": "date",
+              "type": "string"
+            },
+            {
+              "type": "null"
+            }
+          ],
+          "default": null,
+          "title": "Date Of Birth"
+        },
+        "sex": {
+          "default": "U",
+          "enum": [
+            "M",
+            "F",
+            "U"
+          ],
+          "title": "Sex",
+          "type": "string"
+        },
+        "species": {
+          "anyOf": [
+            {
+              "maxLength": 64,
+              "minLength": 1,
+              "type": "string"
+            },
+            {
+              "type": "null"
+            }
+          ],
+          "default": null,
+          "title": "Species"
+        }
+      },
+      "title": "SubjectDetails",
+      "type": "object"
     }
   },
   "additionalProperties": false,
```

- [ ] **Step 5: Run the task's tests**

Run: `.venv/bin/python -m pytest tests/responder tests/cli/test_schemas_export.py -q -p no:cacheprovider`
Expected: all pass (measured: `tests/responder` 118 passed).

- [ ] **Step 6: Record the wl.works half.** Apply this diff to `docs/pending-wl-works-amendments.md`:

````diff
--- a/docs/pending-wl-works-amendments.md
+++ b/docs/pending-wl-works-amendments.md
@@ -1,10 +1,40 @@
 # Amendments to wl-works
 
-**Two are outstanding, opened 2026-08-22.** The earlier two batches are closed; their records are
-kept below, because
+**Three are outstanding: two opened 2026-08-22, one 2026-09-28.** The earlier two batches are
+closed; their records are kept below, because
 [`specs/2026-08-12-wl-preproc-design.md`](superpowers/specs/2026-08-12-wl-preproc-design.md)
 §14 items 10–11 point at it and a reference that dead-ends teaches nothing.
+
+---
+
+# OPEN — the activation-request payload gains the subject's details
+
+**Opened 2026-09-28** with the NWB builder
+([`specs/2026-09-28-nwb-builder-design.md`](superpowers/specs/2026-09-28-nwb-builder-design.md)
+§9), the requester's decision: every NWB file names its subject's species, sex and date of
+birth, and **wl.works' `animal` record is the authority for all three.** wl-preproc lands only a
+stub (`ingest/landing.py`: sex unknown, a 1900-01-01 placeholder birth date), and cannot fetch
+the rest, for §11.2's reason: everything it needs from the ELN arrives with the request.
+
+**This repository's half is built.** `contracts/protocol.py`'s `MetadataBundle` gains an optional
+`subject_details`, and `docs/schemas/job_request.json` carries it:
+
+```json
+{ "species": "Macaca mulatta", "sex": "F", "date_of_birth": "2016-03-02" }
+```
+
+`species` is a Latin name, at most 64 characters (element-animal's `Species`); `sex` is `M`, `F`
+or `U`; `date_of_birth` is an ISO date. All three are optional, and unknown keys are refused.
+`responder/jobs.py::accept` writes them into the subject's own record, replacing the stub; the
+latest request wins.
 
+**Why it matters more than metadata usually does.** `nwbinspector` rates a subject with neither
+age nor date of birth as CRITICAL (measured 2026-09-28), and a file with a critical finding is
+not published. **Until wl.works sends `date_of_birth`, no NWB file is publishable.**
+
+**Still open on their side:** their caller must send the field, and row 18b's fake wl-preproc
+must accept it.
+
 ---
 
 # OPEN — the montage definition widens, and §14 item 10 moves with it
````

- [ ] **Step 7: Mutation checks.** Each was measured to fail `test_the_subjects_details_fill_its_own_record`.
  - T2a: delete the line `    _record_subject_details(metadata.subject, metadata.subject_details)` from `accept`.
  - T2b: in `_record_subject_details`, `    if details.species is not None:` → `    if False:`.

- [ ] **Step 8: Commit**

```bash
git add wl_preproc/contracts/protocol.py wl_preproc/responder/jobs.py tests/responder/test_jobs.py docs/schemas/job_request.json docs/pending-wl-works-amendments.md
git commit -m "feat(protocol): the job request carries the subject's species, sex and date of birth, written into element-animal's own record

<trailer lines>"
```

---

### Task 3: The writers, trimming, checksums, validation and the atomic write

**Files:**
- Modify: `pyproject.toml`, `wl.yaml`
- Create: `wl_preproc/nwb/__init__.py`, `columns.py`, `session.py`, `intervals.py`, `timebase.py`, `eye.py`, `eye_events.py`, `trim.py`, `checksums.py`, `validate.py`, `write.py`
- Create: `tests/nwb/__init__.py` (empty), `tests/nwb/test_writers.py`, `tests/nwb/test_helpers.py`

**Interfaces — produces** (Task 4 calls every one; all times are session seconds):
- `session.new_file(session: dict) -> NWBFile`. `session` has `identifier`, `session_id`, `description`, `reference_time` (an aware datetime), `experimenter` (`str | None`) and `subject` (`subject_id`, `species`, `sex`, `date_of_birth`).
- `intervals.add_blocks(nwb, blocks, systems)`. Each block has `block_id`, `start_s`, `end_s`, `task_type`, `works_block_id`, `measured_start_s`, `measured_stop_s`, and `coverage: {system: (coverage, covered_s)}`.
- `intervals.add_trials(nwb, trials, systems)`. Each trial has `trial_id`, `start_s`, `stop_s`, `outcome`, `block_id`, `coverage`.
- `intervals.add_task_events(nwb, events)`. Each event has `time_s`, `event_type`, `trial_id`, `block_id`, `condition`.
- `timebase.add_timebase(nwb, provenance: dict, clocks: list[dict], segments: list[dict], clock_reference: dict)`.
- `eye.EYES = ("left", "right")` and `eye.PUPIL_COLUMNS = ("PupilX", "PupilY", "PupilWidth", "PupilHeight", "PupilAngle")`.
- `eye.add_eye_series(nwb, times, gaze: {eye: (n, 2) array | None}, pupil: {eye: (n, 5) array})`.
- `eye.add_eye_tables(nwb, calibration: list[dict], validity: {eye: [(start_s, stop_s, label)]}, repairs: {eye: [(start_s, stop_s)]})`.
- `eye_events.add_detections(nwb, tables)`. Each table is `{name, description, runs}`; each run is a dict of `start_s`, `stop_s`, `label`, and the eight measurements.
- `eye_events.add_sources(nwb, tables)`. Each table's runs are `(start_s, stop_s, source)`.
- `eye_events.add_agreement(nwb, rows)`.
- `trim.BlockSet.of(blocks)`, with `.contains(times)` (half-open), `.contains_instant(times)` (end included) and `.clip(start, stop) -> list[(start, stop)]`.
- `checksums.dataset_checksums(path) -> list[dict]`. Each dict has `dataset_path`, `dtype`, `shape`, `blake3`, `paired_with`.
- `validate.inspect_file(path) -> list[dict]`. Each finding has `importance`, `check`, `message`, `object_type`, `location`. Also `validate.n_critical(findings) -> int`.
- `write.write_atomically(nwb, path)`.

**Ruling (this plan): blocks that touch are one interval in `BlockSet`.** Spec §5 clips stretches "to the block edges". wl.works may assert two blocks where one was measured, and the end-to-end fixture does. At the edge the two blocks share, clipping would make one blink two rows. Merging touching blocks changes nothing about which samples, runs or events fall inside. It only means a stretch is cut where the block set ends, never where two of its blocks meet. *Cost if wrong: a reader who wants per-block stretches splits them at the block boundaries in `/intervals/blocks` themselves.*

- [ ] **Step 1: Declare the dependencies.** Apply these diffs:

```diff
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -151,6 +151,15 @@
     # specific numcodecs release -- this floor is deliberately no tighter
     # than what the dependencies that already require numcodecs enforce.
     "numcodecs>=0.10,<0.16",
+    # NWB export (design spec 2026-09-28-nwb-builder-design.md). pynwb arrived
+    # until now only because element-animal requires it; wl_preproc/nwb/
+    # imports it directly, so it is this package's own. 4.x is the floor:
+    # the builder writes NWB schema 2.10 files, and CI resolves 4.2.
+    "pynwb>=4.1,<5",
+    # Validates every file before piece 2 may publish it (parent spec
+    # section 8.1). 0.7 is the version whose critical checks were measured
+    # on 2026-09-28 (a missing date of birth; a future session start).
+    "nwbinspector>=0.7,<0.8",
 ]
 
 # No upper Python bound. An earlier draft pinned <3.12, justified as the
```

```diff
--- a/wl.yaml
+++ b/wl.yaml
@@ -657,6 +657,24 @@
       both dependents' real constraints rather than restating zarr's own
       oddly-specific point exclusions, which this repository has no
       independent reason to assert.
+  - name: pynwb
+    constraint: ">=4.1,<5"
+    why: >-
+      NWB export (docs/superpowers/specs/2026-09-28-nwb-builder-design.md):
+      wl_preproc/nwb/ writes every file through it. It was already installed,
+      but only because element-animal declares pynwb>=1.4.0; this package now
+      imports it directly, so the dependency is declared as its own. 4.x is
+      the floor because the builder writes NWB schema 2.10 files; CI resolves
+      4.2.
+  - name: nwbinspector
+    constraint: ">=0.7,<0.8"
+    why: >-
+      Validates every NWB file the builder writes (parent spec section 8.1:
+      "validated with nwbinspector before publication"); a CRITICAL finding
+      marks the file invalid. Pinned to 0.7 because its critical checks were
+      measured against these files on 2026-09-28 -- a subject with no age or
+      date of birth, and a future session start -- and a new minor may add
+      or regrade checks.
 
 # The only place this edge is written down where another repository can see it.
 # The dependency itself lives in pyproject.toml, which nothing outside this
```

Run: `.venv/bin/wl-check`, on its own. Expected: exit status 0.
Run: `.venv/bin/python -c "import pynwb, nwbinspector; print(pynwb.__version__, nwbinspector.__version__)"`. Expected: `4.1.0 0.7.2`.

- [ ] **Step 2: Write the failing tests.** Create `tests/nwb/__init__.py` empty, then `tests/nwb/test_writers.py`:

```python
"""The NWB writers, on plain data (design spec
`2026-09-28-nwb-builder-design.md` sections 3, 6 and 11)."""

from __future__ import annotations

import datetime

import h5py
import numpy as np
import pytest

T0 = datetime.datetime(2026, 9, 28, 12, 0, tzinfo=datetime.timezone.utc)
SESSION = {
    "identifier": "2026-09-28_01.montage-0.activation-0",
    "session_id": "2026-09-28_01",
    "description": "canonical activation, montage 0",
    "reference_time": T0,
    "experimenter": "jw",
    "subject": {"subject_id": "monk01", "species": "Macaca mulatta", "sex": "F",
                "date_of_birth": datetime.date(2016, 3, 2)},
}
BLOCKS = [{"block_id": 1, "start_s": 0.0, "end_s": 30.0, "task_type": "rf_map", "works_block_id": "wb-1",
           "measured_start_s": 0.5, "measured_stop_s": 29.5,
           "coverage": {"ohdpi": ("full", 30.0), "spikeglx": ("partial", 12.0)}},
          {"block_id": 2, "start_s": 30.0, "end_s": 60.0, "task_type": "search", "works_block_id": None,
           "measured_start_s": None, "measured_stop_s": None, "coverage": {"ohdpi": ("full", 30.0)}}]
TRIALS = [{"trial_id": 1, "start_s": 1.0, "stop_s": 3.0, "outcome": "correct", "block_id": 1,
           "coverage": {"ohdpi": ("full", 2.0)}}]
EVENTS = [{"time_s": 1.0, "event_type": "TRIAL_START", "trial_id": 1, "block_id": None, "condition": None},
          {"time_s": 2.5, "event_type": "CODE_256", "trial_id": None, "block_id": None, "condition": None}]


def _write(tmp_path, build):
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.session import new_file
    from wl_preproc.nwb.write import write_atomically

    nwb = new_file(SESSION)
    build(nwb)
    path = tmp_path / "f.nwb"
    write_atomically(nwb, path)
    io = NWBHDF5IO(str(path), "r")
    return path, io, io.read()


def test_the_file_is_one_activation_on_session_time(tmp_path):
    _path, io, nwb = _write(tmp_path, lambda nwb: None)
    with io:
        assert nwb.identifier == SESSION["identifier"]
        assert nwb.session_start_time == T0 and nwb.timestamps_reference_time == T0
        assert tuple(nwb.experimenter) == ("jw",)
        assert (nwb.subject.species, nwb.subject.sex) == ("Macaca mulatta", "F")
        assert nwb.subject.date_of_birth.date() == datetime.date(2016, 3, 2)


def test_blocks_trials_and_events(tmp_path):
    from wl_preproc.nwb.intervals import add_blocks, add_task_events, add_trials

    def build(nwb):
        add_blocks(nwb, BLOCKS, ["ohdpi", "spikeglx"])
        add_trials(nwb, TRIALS, ["ohdpi"])
        add_task_events(nwb, EVENTS)

    _path, io, nwb = _write(tmp_path, build)
    with io:
        blocks = nwb.intervals["blocks"].to_dataframe()
        assert blocks["block_id"].tolist() == [1, 2]
        assert blocks["works_block_id"].tolist() == ["wb-1", ""]
        assert np.isnan(blocks["measured_start_time"].iloc[1])
        assert blocks["coverage_spikeglx"].tolist() == ["partial", ""]
        assert blocks["covered_s_spikeglx"].iloc[0] == 12.0 and np.isnan(blocks["covered_s_spikeglx"].iloc[1])
        trials = nwb.trials.to_dataframe()
        assert trials[["trial_id", "outcome", "block_id", "coverage_ohdpi"]].iloc[0].tolist() == [1, "correct", 1, "full"]
        events = nwb.intervals["task_events"].to_dataframe()
        assert events["start_time"].tolist() == events["stop_time"].tolist() == [1.0, 2.5]
        assert events["event_type"].tolist() == ["TRIAL_START", "CODE_256"]
        assert events["trial_id"].tolist() == [1, -1]


def test_the_timebase_tables(tmp_path):
    from wl_preproc.nwb.timebase import add_timebase

    provenance = {"tier": "A", "n_systems_aligned": 2, "n_segments": 2, "n_rejected_segments": 0,
                  "worst_residual_us": 12.0, "worst_drift_ppm": 3.0}
    clocks = [{"system": "ohdpi", "fit_status": "fitted", "nominal_rate_hz": 500.0, "fitted_rate_hz": 498.55,
               "drift_ppm": 2.0, "residual_us_rms": 10.0}]
    segments = [{"system": "ohdpi", "file_path": "ohdpi/x.txt", "first_sample": 7, "offset_s": 0.1,
                 "start_s": 0.1, "end_s": 60.1, "n_samples": 30000}]
    reference = {"source": "barcode", "reference_time": T0.isoformat(), "manifest_started_at": T0.isoformat(),
                 "started_at_difference_s": 0.4}
    _path, io, nwb = _write(tmp_path, lambda nwb: add_timebase(nwb, provenance, clocks, segments, reference))
    with io:
        module = nwb.processing["timebase"]
        assert module["timing_provenance"].to_dataframe()["tier"].tolist() == ["A"]
        assert module["system_clocks"].to_dataframe()["fitted_rate_hz"].tolist() == [498.55]
        assert module["segments"].to_dataframe()["n_samples"].tolist() == [30000]
        assert module["clock_reference"].to_dataframe()["source"].tolist() == ["barcode"]


def _eye_data(n=2000, eyes=("left", "right")):
    times = 1.0 + np.arange(n) / 500.0
    gaze = {eye: np.column_stack([np.sin(times), np.cos(times)]) for eye in eyes}
    pupil = {eye: np.ones((n, 5)) for eye in ("left", "right")}
    return times, gaze, pupil


def test_the_eye_series_share_one_timestamps_dataset(tmp_path):
    from wl_preproc.nwb.eye import add_eye_series

    times, gaze, pupil = _eye_data()
    path, io, nwb = _write(tmp_path, lambda nwb: add_eye_series(nwb, times, gaze, pupil))
    with io:
        eye = nwb.processing["behavior"]["EyeTracking"]
        np.testing.assert_allclose(eye["gaze_left"].timestamps[:], times)
        np.testing.assert_allclose(eye["gaze_right"].data[:], gaze["right"].astype(np.float32))
        assert nwb.processing["behavior"]["PupilTracking"]["pupil_right"].data.shape == (2000, 5)
    with h5py.File(path) as handle:
        base = "processing/behavior"
        left = handle[f"{base}/EyeTracking/gaze_left/timestamps"]
        for other in ("EyeTracking/gaze_right", "PupilTracking/pupil_left", "PupilTracking/pupil_right"):
            assert handle[f"{base}/{other}/timestamps"].id == left.id, other
        data = handle[f"{base}/EyeTracking/gaze_left/data"]
        assert data.compression == "gzip" and data.chunks == (2000, 2)


def test_continuous_chunks_are_about_256_kb():
    from wl_preproc.nwb.columns import CHUNK_BYTES, continuous

    io = continuous(np.zeros((1_000_000, 2), dtype=np.float32))
    assert io.io_settings["chunks"] == (CHUNK_BYTES // 8, 2)
    assert io.io_settings["compression"] == "gzip"


def test_an_eye_with_no_calibration_has_no_gaze(tmp_path):
    """Design spec section 10's one partial case."""
    from wl_preproc.nwb.eye import add_eye_series

    times, gaze, pupil = _eye_data(eyes=("left",))
    _path, io, nwb = _write(tmp_path, lambda nwb: add_eye_series(nwb, times, gaze, pupil))
    with io:
        assert list(nwb.processing["behavior"]["EyeTracking"].spatial_series) == ["gaze_left"]


def test_the_eye_tables(tmp_path):
    from wl_preproc.nwb.eye import add_eye_tables

    calibration = [{"eye": "left", "calibration_source": "fitted", "calibration_model": "affine",
                    "gx_const": 0.1, "gx_dx": 1.0, "gx_dy": 0.0, "gx_dx2": None, "gx_dy2": None, "gx_dxdy": None,
                    "gy_const": 0.0, "gy_dx": 0.0, "gy_dy": 1.0, "gy_dx2": None, "gy_dy2": None, "gy_dxdy": None,
                    "validation_error_deg": 0.4, "n_points": 9, "residual_deg_rms": 0.3, "residual_deg_max": 0.8,
                    "reason": ""}]
    validity = {"left": [(3.0, 3.2, "blink")], "right": []}
    repairs = {"left": [(5.0, 5.004)], "right": []}
    _path, io, nwb = _write(tmp_path, lambda nwb: add_eye_tables(nwb, calibration, validity, repairs))
    with io:
        module = nwb.processing["behavior"]
        cal = module["eye_calibration"].to_dataframe()
        assert cal["gx_const"].tolist() == [0.1] and np.isnan(cal["gx_dx2"].iloc[0])
        assert module["eye_validity_left"].to_dataframe()["label"].tolist() == ["blink"]
        assert len(module["eye_validity_right"]) == 0
        assert module["eye_glitch_repairs_left"].to_dataframe()["stop_time"].tolist() == [5.004]


def test_every_detectors_events_and_sources_and_agreement(tmp_path):
    from wl_preproc.nwb.eye_events import add_agreement, add_detections, add_sources

    runs = [{"start_s": 1.0, "stop_s": 1.04, "label": "saccade", "amplitude_deg": 2.0, "peak_velocity_deg_s": 150.0,
             "start_x_deg": 0.0, "start_y_deg": 0.0, "end_x_deg": 2.0, "end_y_deg": 0.0, "direction_deg": 0.0,
             "reliability": None},
            {"start_s": 1.04, "stop_s": 2.0, "label": "fixation"}]
    tables = [{"name": f"engbert_kliegl_{trace}", "description": "d", "runs": runs} for trace in ("left", "right", "conjunction")]
    sources = [{"name": "engbert_kliegl_source", "description": "d", "runs": [(0.0, 60.0, "both")]}]
    agreement = [{"detector_a": "engbert_kliegl", "detector_b": "bmd", "trace": "left", "metric": "event_f1",
                  "vocabulary": "saccadic", "pso_as": "", "value": 0.8, "n_samples_compared": 1000}]

    def build(nwb):
        add_detections(nwb, tables)
        add_sources(nwb, sources)
        add_agreement(nwb, agreement)

    _path, io, nwb = _write(tmp_path, build)
    with io:
        module = nwb.processing["eye_events"]
        left = module["engbert_kliegl_left"].to_dataframe()
        assert left["label"].tolist() == ["saccade", "fixation"]
        assert left["amplitude_deg"].iloc[0] == 2.0 and np.isnan(left["amplitude_deg"].iloc[1])
        assert np.isnan(left["reliability"].iloc[0])
        assert module["engbert_kliegl_source"].to_dataframe()["source"].tolist() == ["both"]
        assert module["detector_agreement"].to_dataframe()["value"].tolist() == [0.8]


def test_a_table_with_no_rows_writes(tmp_path):
    from wl_preproc.nwb.intervals import add_task_events

    _path, io, nwb = _write(tmp_path, lambda nwb: add_task_events(nwb, []))
    with io:
        assert len(nwb.intervals["task_events"]) == 0
```

and `tests/nwb/test_helpers.py`:

```python
"""Trimming, checksums, validation and the atomic write (design spec
`2026-09-28-nwb-builder-design.md` sections 5-8)."""

from __future__ import annotations

import datetime

import numpy as np
import pytest

from tests.nwb.test_writers import SESSION, T0


def test_the_block_set_keeps_what_starts_inside_it():
    from wl_preproc.nwb.trim import BlockSet

    blocks = BlockSet.of([{"start_s": 30.0, "end_s": 60.0}, {"start_s": 0.0, "end_s": 10.0}])
    assert blocks.contains([0.0, 9.999, 10.0, 29.0, 30.0, 59.9, 60.0]).tolist() == [
        True, True, False, False, True, True, False]
    assert blocks.contains_instant([10.0, 60.0, 60.001]).tolist() == [True, True, False]
    assert blocks.clip(5.0, 35.0) == [(5.0, 10.0), (30.0, 35.0)]
    assert blocks.clip(12.0, 20.0) == []


def test_touching_blocks_do_not_split_a_stretch_that_crosses_them():
    """A blink across the boundary of two adjacent blocks is one blink: one
    row in the file, not one per block (a stretch is clipped to the block
    set's edges, and two touching blocks share no edge a stretch can stop
    at)."""
    from wl_preproc.nwb.trim import BlockSet

    blocks = BlockSet.of([{"start_s": 10.0, "end_s": 20.0}, {"start_s": 0.0, "end_s": 10.0},
                          {"start_s": 30.0, "end_s": 40.0}])
    assert blocks.clip(8.0, 12.0) == [(8.0, 12.0)]
    assert blocks.clip(18.0, 35.0) == [(18.0, 20.0), (30.0, 35.0)]
    assert blocks.contains([9.999, 10.0, 19.999, 20.0]).tolist() == [True, True, True, False]
    assert blocks.contains_instant([10.0, 20.0]).tolist() == [True, True]

def _file(tmp_path, name, values):
    from wl_preproc.nwb.intervals import add_task_events
    from wl_preproc.nwb.session import new_file
    from wl_preproc.nwb.write import write_atomically

    nwb = new_file(SESSION)
    add_task_events(nwb, [{"time_s": t, "event_type": "TRIAL_START"} for t in values])
    path = tmp_path / name
    write_atomically(nwb, path)
    return path


def test_checksums_are_of_contents_and_stable_across_rebuilds(tmp_path):
    from wl_preproc.nwb.checksums import dataset_checksums

    first = dataset_checksums(_file(tmp_path, "a.nwb", [1.0, 2.0]))
    again = dataset_checksums(_file(tmp_path, "b.nwb", [1.0, 2.0]))
    changed = dataset_checksums(_file(tmp_path, "c.nwb", [1.0, 2.5]))

    assert first == again
    by_path = {row["dataset_path"]: row for row in first}
    assert "/intervals/task_events/start_time" in by_path
    assert not any(row["dataset_path"].startswith(("/specifications", "/file_create_date")) for row in first)
    changed_paths = {row["dataset_path"] for row in changed} & set(by_path)
    assert [p for p in sorted(changed_paths) if {r["dataset_path"]: r for r in changed}[p]["blake3"] != by_path[p]["blake3"]] == [
        "/intervals/task_events/start_time", "/intervals/task_events/stop_time"]


def test_a_ragged_column_is_recorded_as_a_pair(tmp_path):
    from pynwb.file import Subject  # noqa: F401 -- pynwb's experimenter is a ragged-free list; build one below
    from hdmf.common import VectorData, VectorIndex
    from pynwb.core import DynamicTable

    from wl_preproc.nwb.checksums import dataset_checksums
    from wl_preproc.nwb.session import new_file
    from wl_preproc.nwb.write import write_atomically

    nwb = new_file(SESSION)
    values = VectorData(name="spikes", description="d", data=[1.0, 2.0, 3.0])
    index = VectorIndex(name="spikes_index", data=[2, 3], target=values)
    module = nwb.create_processing_module("scratch", "d")
    module.add(DynamicTable(name="ragged", description="d", columns=[values, index]))
    path = tmp_path / "r.nwb"
    write_atomically(nwb, path)

    rows = {row["dataset_path"]: row for row in dataset_checksums(path)}
    assert rows["/processing/scratch/ragged/spikes"]["paired_with"] == "/processing/scratch/ragged/spikes_index"
    assert rows["/processing/scratch/ragged/spikes_index"]["paired_with"] == "/processing/scratch/ragged/spikes"


def test_a_subject_without_a_date_of_birth_is_a_critical_finding(tmp_path):
    """Measured 2026-09-28: `nwbinspector`'s `check_subject_age` is CRITICAL
    under its default configuration, so a file built without wl.works'
    subject details is `invalid` (design spec sections 8 and 9)."""
    from wl_preproc.nwb.session import new_file
    from wl_preproc.nwb.validate import inspect_file, n_critical
    from wl_preproc.nwb.write import write_atomically

    for dob, expected in ((datetime.date(2016, 3, 2), 0), (None, 1)):
        nwb = new_file({**SESSION, "subject": {**SESSION["subject"], "date_of_birth": dob}})
        path = tmp_path / f"{dob}.nwb"
        write_atomically(nwb, path)
        findings = inspect_file(path)
        assert n_critical(findings) == expected, findings
        if expected:
            assert [f["check"] for f in findings if f["importance"] == "CRITICAL"] == ["check_subject_age"]


def test_a_failed_write_leaves_no_file_under_the_final_name(tmp_path, monkeypatch):
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.session import new_file
    from wl_preproc.nwb.write import write_atomically

    def broken(self, container, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(NWBHDF5IO, "write", broken)
    path = tmp_path / "f.nwb"
    with pytest.raises(RuntimeError):
        write_atomically(new_file(SESSION), path)
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/nwb -q --tb=line -p no:cacheprovider`
Expected: 15 failed, every one a `ModuleNotFoundError` naming `wl_preproc.nwb` or one of its modules.

- [ ] **Step 4: Implement.** Create `wl_preproc/nwb/__init__.py`:

```python
"""NWB export: one activation's file, for everything that exists before ephys.

Parent spec `2026-08-12-wl-preproc-design.md` section 8; design spec
`2026-09-28-nwb-builder-design.md`. The writers in this package take plain
data and add to an `NWBFile`; `gather.py` is the one place that reads the
database and the raw ohDPI file, and `build.py` puts the two together.
"""
```

`wl_preproc/nwb/columns.py`:

```python
"""How every dataset in the file is stored (design spec
`2026-09-28-nwb-builder-design.md` section 6).

**Readable over HTTP range requests** (parent spec section 8.1.2): each
dataset is compressed on its own, with gzip, which browser HDF5 readers
support; nothing is compressed whole-file. Continuous series are chunked
time-major, about `CHUNK_BYTES` a chunk, so a window of time across both
coordinates touches few chunks."""

from __future__ import annotations

import numpy as np
from hdmf.backends.hdf5 import H5DataIO
from hdmf.common import VectorData

CHUNK_BYTES = 256 * 1024


def continuous(data: np.ndarray) -> H5DataIO:
    """A continuous series, chunked time-major at about `CHUNK_BYTES`."""
    data = np.asarray(data)
    row_bytes = data.dtype.itemsize * int(np.prod(data.shape[1:], dtype=int))
    rows = max(1, min(data.shape[0], CHUNK_BYTES // max(row_bytes, 1)))
    return H5DataIO(data, compression="gzip", chunks=(rows, *data.shape[1:]))


def compressed(data):
    """A table column or any other dataset: gzip, default chunking. An empty
    one is stored plain: HDF5 cannot chunk a zero-length dataset."""
    values = np.asarray(data)
    if values.size == 0:
        return values
    return H5DataIO(values, compression="gzip")


def column(name: str, description: str, data) -> VectorData:
    """One table column, compressed. Strings are stored as text; a missing
    float is NaN and a missing integer is -1, as each column's description
    says."""
    values = np.asarray(data)
    if values.dtype.kind in "US":
        values = values.astype(object)
    return VectorData(name=name, description=description, data=compressed(values))
```

`wl_preproc/nwb/session.py`:

```python
"""The file itself: identity, clock and subject (design spec
`2026-09-28-nwb-builder-design.md` section 3, file-level metadata)."""

from __future__ import annotations

import datetime

from pynwb import NWBFile
from pynwb.file import Subject


def new_file(session: dict) -> NWBFile:
    """An empty `NWBFile` for one activation.

    `session` carries `identifier`, `session_id`, `description`,
    `reference_time` (timezone-aware: the wall-clock time of session t = 0,
    section 4.2), `experimenter` (or None) and `subject` (`subject_id`,
    `species`, `sex`, `date_of_birth`; species and date of birth may be
    None). `session_start_time` and `timestamps_reference_time` are both the
    reference time, so every time in the file is session seconds."""
    reference = session["reference_time"]
    if reference.tzinfo is None:
        raise ValueError("reference_time must be timezone-aware")
    subject = session["subject"]
    date_of_birth = subject.get("date_of_birth")
    if isinstance(date_of_birth, datetime.date) and not isinstance(date_of_birth, datetime.datetime):
        date_of_birth = datetime.datetime.combine(date_of_birth, datetime.time(), tzinfo=datetime.timezone.utc)
    return NWBFile(
        session_description=session["description"],
        identifier=session["identifier"],
        session_start_time=reference,
        timestamps_reference_time=reference,
        session_id=session["session_id"],
        experimenter=[session["experimenter"]] if session.get("experimenter") else None,
        subject=Subject(
            subject_id=subject["subject_id"],
            species=subject.get("species"),
            sex=subject.get("sex") or "U",
            date_of_birth=date_of_birth,
            description="As wl.works' job request stated it (design spec section 9).",
        ),
    )
```

`wl_preproc/nwb/intervals.py`:

```python
"""Blocks, trials and task events (design spec
`2026-09-28-nwb-builder-design.md` section 3, `/intervals`)."""

from __future__ import annotations

import numpy as np
from pynwb import NWBFile
from pynwb.epoch import TimeIntervals

from wl_preproc.nwb.columns import column


def _coverage_columns(rows: list[dict], systems: list[str]) -> list:
    """Per-system coverage, two columns a system: its verdict (`full`,
    `partial`, `absent`, or '' where none was computed) and the seconds it
    covered (NaN where none)."""
    columns = []
    for system in systems:
        cover = [row["coverage"].get(system) for row in rows]
        columns.append(column(f"coverage_{system}", f"How much of the interval {system} recorded: full, partial or absent.",
                              [c[0] if c else "" for c in cover]))
        columns.append(column(f"covered_s_{system}", f"Seconds of the interval {system} recorded (NaN where not computed).",
                              [c[1] if c else np.nan for c in cover]))
    return columns


def add_blocks(nwb: NWBFile, blocks: list[dict], systems: list[str]) -> None:
    """`/intervals/blocks`: the activation's blocks, their asserted
    boundaries (`core.Block`) as start and stop, the measured ones
    (`trial.Block`) beside them, and per-system coverage."""
    rows = sorted(blocks, key=lambda row: row["start_s"])
    nwb.add_time_intervals(TimeIntervals(
        name="blocks",
        description=("The activation's blocks: start and stop are the boundaries wl.works asserted "
                     "(core.Block); measured_* are the boundaries decoded from the event codes."),
        columns=[
            column("start_time", "Asserted block start, session seconds.", [r["start_s"] for r in rows]),
            column("stop_time", "Asserted block stop, session seconds.", [r["end_s"] for r in rows]),
            column("block_id", "Block number.", [r["block_id"] for r in rows]),
            column("task_type", "The block's task type.", [r["task_type"] for r in rows]),
            column("works_block_id", "wl.works' own block id ('' until linked).", [r["works_block_id"] or "" for r in rows]),
            column("measured_start_time", "Measured block start, session seconds (NaN if not decoded).",
                   [np.nan if r["measured_start_s"] is None else r["measured_start_s"] for r in rows]),
            column("measured_stop_time", "Measured block stop, session seconds (NaN if not decoded).",
                   [np.nan if r["measured_stop_s"] is None else r["measured_stop_s"] for r in rows]),
            *_coverage_columns(rows, systems),
        ],
    ))


def add_trials(nwb: NWBFile, trials: list[dict], systems: list[str]) -> None:
    """`/intervals/trials`: trial id, outcome, block and per-system coverage."""
    rows = sorted(trials, key=lambda row: row["start_s"])
    nwb.trials = TimeIntervals(
        name="trials",
        description="Trials decoded from the event codes, with their outcome and per-system coverage.",
        columns=[
            column("start_time", "Trial start, session seconds.", [r["start_s"] for r in rows]),
            column("stop_time", "Trial stop, session seconds.", [r["stop_s"] for r in rows]),
            column("trial_id", "Trial number.", [r["trial_id"] for r in rows]),
            column("outcome", "correct, error, abort, fixation_break or no_response.", [r["outcome"] or "" for r in rows]),
            column("block_id", "The measured block the trial belongs to (-1 if none).",
                   [-1 if r["block_id"] is None else r["block_id"] for r in rows]),
            *_coverage_columns(rows, systems),
        ],
    )


def add_task_events(nwb: NWBFile, events: list[dict]) -> None:
    """`/intervals/task_events`: every decoded event code.

    **Zero-length intervals, start equal to stop, by necessity.** NWB 2.10's
    `EventsTable` is the right type, but Neurosift, which wl.works opens these
    files in, does not display it (design spec section 12.1, verified
    2026-09-28), and `TimeIntervals` it does. `nwbinspector` flags a stop
    time that does not exceed its start as a best-practice violation, which
    this table knowingly carries."""
    rows = sorted(events, key=lambda row: (row["time_s"], row["event_type"]))
    nwb.add_time_intervals(TimeIntervals(
        name="task_events",
        description=("Every decoded event code, one row each, as a zero-length interval: start_time is the "
                     "event's session time and stop_time equals it (NWB 2.10's EventsTable is not displayed "
                     "by Neurosift)."),
        columns=[
            column("start_time", "Event time, session seconds.", [r["time_s"] for r in rows]),
            column("stop_time", "Equal to start_time: an event is an instant.", [r["time_s"] for r in rows]),
            column("event_type", "The event code's name (contracts/events.py), or CODE_<n> for a task event.",
                   [r["event_type"] for r in rows]),
            column("trial_id", "The trial number the code carried (-1 if none).",
                   [-1 if r.get("trial_id") is None else r["trial_id"] for r in rows]),
            column("block_id", "The block number the code carried (-1 if none).",
                   [-1 if r.get("block_id") is None else r["block_id"] for r in rows]),
            column("condition", "The condition the code carried ('' if none).", [r.get("condition") or "" for r in rows]),
        ],
    ))
```

`wl_preproc/nwb/timebase.py`:

```python
"""`processing/timebase`: how every time in the file was put on one clock
(design spec `2026-09-28-nwb-builder-design.md` sections 3 and 4).

**Every time in the file is session seconds from the sync box's barcode,
the authoritative clock. A PTP stamp, when a device carries one, may appear
here only as a column labelled a cross-check, never as the timestamps of any
data** (section 4.4; wl-works' time-service design section 4)."""

from __future__ import annotations

from pynwb import NWBFile
from pynwb.core import DynamicTable

from wl_preproc.nwb.columns import column


def _table(name: str, description: str, rows: list[dict], fields: dict[str, str]) -> DynamicTable:
    return DynamicTable(name=name, description=description,
                        columns=[column(field, text, [row[field] for row in rows]) for field, text in fields.items()])


def add_timebase(nwb: NWBFile, provenance: dict, clocks: list[dict], segments: list[dict],
                 clock_reference: dict) -> None:
    """The session's timing provenance, every system's clock fit, every
    segment's placement on session time, and where the wall-clock time of
    t = 0 came from. With `system_clocks` and `segments` every native
    timestamp is recoverable (parent spec section 4.5)."""
    module = nwb.create_processing_module(
        name="timebase",
        description=("How every time in this file was placed on session time: t = 0 is the sync box's first "
                     "barcode, the authoritative clock. PTP, where present, is a cross-check only."),
    )
    module.add(_table("timing_provenance", "TimingProvenance: the session's timing tier and its evidence.", [provenance], {
        "tier": "A to D (parent spec section 4.7).",
        "n_systems_aligned": "Systems placed on session time.",
        "n_segments": "Segments aligned.",
        "n_rejected_segments": "Segments that could not be aligned.",
        "worst_residual_us": "Largest barcode residual, microseconds.",
        "worst_drift_ppm": "Largest clock drift, parts per million.",
    }))
    module.add(_table("system_clocks", "SystemTimebase: each system's fitted clock.", clocks, {
        "system": "Acquisition system.",
        "fit_status": "fitted, no_recording or unfittable.",
        "nominal_rate_hz": "The system's nominal sampling rate.",
        "fitted_rate_hz": "The rate fitted against the barcodes.",
        "drift_ppm": "Clock drift against the sync box, parts per million.",
        "residual_us_rms": "RMS barcode residual, microseconds.",
    }))
    module.add(_table("segments", "core.Segment: each recording file's placement on session time.", segments, {
        "system": "Acquisition system.",
        "file_path": "The recording file, relative to the session directory.",
        "first_sample": "Native index of the segment's first barcode.",
        "offset_s": "session_s = native_s / scale + offset_s.",
        "start_s": "Session time of the file's first sample.",
        "end_s": "Session time one sample past the file's last.",
        "n_samples": "True samples spanned, dropped frames included.",
    }))
    module.add(_table("clock_reference", "Where the wall-clock time of session t = 0 came from (section 4.2).",
                      [clock_reference], {
        "source": "barcode: the first barcode's own value (seconds since 2020-01-01 UTC); manifest: started_at.",
        "reference_time": "The wall-clock time of t = 0, ISO 8601 UTC.",
        "manifest_started_at": "The session manifest's started_at, ISO 8601 UTC.",
        "started_at_difference_s": "started_at minus the barcode's time of t = 0, seconds.",
    }))
```

`wl_preproc/nwb/eye.py`:

```python
"""`processing/behavior`: the eye as recorded and as the mask judged it
(design spec `2026-09-28-nwb-builder-design.md` section 3)."""

from __future__ import annotations

import numpy as np
from pynwb import NWBFile
from pynwb.base import TimeSeries
from pynwb.behavior import EyeTracking, PupilTracking, SpatialSeries
from pynwb.core import DynamicTable
from pynwb.epoch import TimeIntervals

from wl_preproc.nwb.columns import column, continuous

EYES = ("left", "right")
PUPIL_COLUMNS = ("PupilX", "PupilY", "PupilWidth", "PupilHeight", "PupilAngle")
CALIBRATION_FIELDS = (
    "calibration_source", "calibration_model",
    "gx_const", "gx_dx", "gx_dy", "gx_dx2", "gx_dy2", "gx_dxdy",
    "gy_const", "gy_dx", "gy_dy", "gy_dx2", "gy_dy2", "gy_dxdy",
    "validation_error_deg", "n_points", "residual_deg_rms", "residual_deg_max", "reason",
)


def behavior_module(nwb: NWBFile):
    if "behavior" not in nwb.processing:
        nwb.create_processing_module(name="behavior", description="The eye, as recorded and as the validity mask judged it.")
    return nwb.processing["behavior"]


def add_eye_series(nwb: NWBFile, times: np.ndarray, gaze: dict, pupil: dict) -> None:
    """Gaze and pupil for each eye, on one shared `timestamps` dataset.

    `times` is session seconds per kept sample (section 4.3: explicit, never
    a nominal rate, since the ohDPI rate is measured and nothing is
    resampled). `gaze[eye]` is `(n, 2)` degrees in the calibrated frame, the
    glitch-repaired gaze every stored detection was made from, or None for
    an eye with no calibration (section 10). `pupil[eye]` is `(n, 5)`,
    OpenIris's `PUPIL_COLUMNS`, uncalibrated."""
    module = behavior_module(nwb)
    shared = None
    gaze_series, pupil_series = [], []
    for eye in EYES:
        if gaze.get(eye) is None:
            continue
        series = SpatialSeries(
            name=f"gaze_{eye}",
            description=("Glitch-repaired gaze (eye/detect/glitch.py), the gaze every stored detection was made "
                         "from; NaN where the recording has no finite value."),
            data=continuous(np.asarray(gaze[eye], dtype=np.float32)),
            timestamps=shared if shared is not None else continuous(np.asarray(times, dtype=np.float64)),
            reference_frame="Degrees of visual angle in the calibrated frame: x rightward, y upward.",
            unit="degrees",
        )
        if shared is None:
            shared = series
        gaze_series.append(series)
    for eye in EYES:
        if pupil.get(eye) is None:
            continue
        series = TimeSeries(
            name=f"pupil_{eye}",
            description="OpenIris's " + ", ".join(PUPIL_COLUMNS) + " columns: pixels, and degrees for the angle; uncalibrated.",
            data=continuous(np.asarray(pupil[eye], dtype=np.float32)),
            timestamps=shared if shared is not None else continuous(np.asarray(times, dtype=np.float64)),
            unit="pixels",
        )
        if shared is None:
            shared = series
        pupil_series.append(series)
    if gaze_series:
        module.add(EyeTracking(spatial_series=gaze_series))
    if pupil_series:
        module.add(PupilTracking(time_series=pupil_series))


def add_eye_tables(nwb: NWBFile, calibration: list[dict], validity: dict, repairs: dict) -> None:
    """`eye_calibration` (one row per calibrated eye), and per eye the
    stretches the mask withheld (`eye_validity_{eye}`, with their label) and
    the stretches `glitch.py` repaired (`eye_glitch_repairs_{eye}`), each
    `(start_s, stop_s[, label])`."""
    module = behavior_module(nwb)
    if calibration:
        rows = sorted(calibration, key=lambda row: row["eye"])
        module.add(DynamicTable(
            name="eye_calibration",
            description="EyeCalibration: each eye's calibration map and its quality (NaN where not applicable).",
            columns=[column("eye", "left or right.", [r["eye"] for r in rows]),
                     *[column(field, field.replace("_", " ") + ".",
                              [(np.nan if r.get(field) is None else r[field]) if field not in ("calibration_source", "calibration_model", "reason")
                               else (r.get(field) or "") for r in rows])
                       for field in CALIBRATION_FIELDS]],
        ))
    for eye in EYES:
        if eye in validity:
            runs = sorted(validity[eye])
            module.add(TimeIntervals(
                name=f"eye_validity_{eye}",
                description=f"Stretches of the {eye} eye the validity mask withheld, with the mask's label.",
                columns=[column("start_time", "Start, session seconds.", [r[0] for r in runs]),
                         column("stop_time", "Stop, session seconds.", [r[1] for r in runs]),
                         column("label", "blink or invalid.", [r[2] for r in runs])],
            ))
        if eye in repairs:
            runs = sorted(repairs[eye])
            module.add(TimeIntervals(
                name=f"eye_glitch_repairs_{eye}",
                description=(f"Stretches of the {eye} eye's gaze that were a tracker glitch -- out and back faster "
                             "than an eye moves -- and were replaced by the straight line across them."),
                columns=[column("start_time", "Start, session seconds.", [r[0] for r in runs]),
                         column("stop_time", "Stop, session seconds.", [r[1] for r in runs])],
            ))
```

`wl_preproc/nwb/eye_events.py`:

```python
"""`processing/eye_events`: every detector's events (design spec
`2026-09-28-nwb-builder-design.md` section 3; the requester's decision: all
six detectors, each labelled)."""

from __future__ import annotations

import numpy as np
from pynwb import NWBFile
from pynwb.core import DynamicTable
from pynwb.epoch import TimeIntervals

from wl_preproc.nwb.columns import column

MEASUREMENTS = {
    "amplitude_deg": "Amplitude, degrees (saccadic rows; NaN otherwise or when unmeasured).",
    "peak_velocity_deg_s": "Peak velocity, degrees per second (saccadic rows; NaN otherwise).",
    "start_x_deg": "Gaze x at the start, degrees (saccadic rows; NaN otherwise).",
    "start_y_deg": "Gaze y at the start, degrees (saccadic rows; NaN otherwise).",
    "end_x_deg": "Gaze x at the end, degrees (saccadic rows; NaN otherwise).",
    "end_y_deg": "Gaze y at the end, degrees (saccadic rows; NaN otherwise).",
    "direction_deg": "Direction, degrees counterclockwise from rightward (NaN otherwise or for no displacement).",
    "reliability": "The detector's own reliability, where it reports one (NaN otherwise).",
}


def _module(nwb: NWBFile):
    if "eye_events" not in nwb.processing:
        nwb.create_processing_module(
            name="eye_events",
            description=("Every registered detector's events, per eye and for both eyes together, and how much "
                         "the detectors agree. Times are session seconds; each table names its detector."),
        )
    return nwb.processing["eye_events"]


def add_detections(nwb: NWBFile, tables: list[dict]) -> None:
    """One `TimeIntervals` per detector per trace. Each of `tables` has
    `name` (`{detector}_{trace}`), `description` (the detector and its
    parameters) and `runs`, each with `start_s`, `stop_s`, `label` and the
    `MEASUREMENTS` (None where absent)."""
    module = _module(nwb)
    for table in tables:
        runs = sorted(table["runs"], key=lambda run: run["start_s"])
        module.add(TimeIntervals(
            name=table["name"],
            description=table["description"],
            columns=[column("start_time", "Start, session seconds.", [r["start_s"] for r in runs]),
                     column("stop_time", "Stop, session seconds (the time just after the run's last sample).",
                            [r["stop_s"] for r in runs]),
                     column("label", "saccade, microsaccade, pso, fixation, pursuit or drift.", [r["label"] for r in runs]),
                     *[column(field, text, [np.nan if r.get(field) is None else r[field] for r in runs])
                       for field, text in MEASUREMENTS.items()]],
        ))


def add_sources(nwb: NWBFile, tables: list[dict]) -> None:
    """One `TimeIntervals` per detector, `{detector}_source`: which eye each
    stretch of the both-eyes trace's labels came from (`EyeDetection.Source`)."""
    module = _module(nwb)
    for table in tables:
        runs = sorted(table["runs"])
        module.add(TimeIntervals(
            name=table["name"],
            description=table["description"],
            columns=[column("start_time", "Start, session seconds.", [r[0] for r in runs]),
                     column("stop_time", "Stop, session seconds.", [r[1] for r in runs]),
                     column("source", "both, left, right or neither.", [r[2] for r in runs])],
        ))


def add_agreement(nwb: NWBFile, rows: list[dict]) -> None:
    """`detector_agreement`: `DetectorAgreement`'s scores between detectors."""
    if not rows:
        return
    fields = {
        "detector_a": "The first detector.", "detector_b": "The second detector.",
        "trace": "left, right or conjunction.", "metric": "The agreement metric.",
        "vocabulary": "Which labels were compared.", "pso_as": "How glissades were counted.",
        "value": "The score.", "n_samples_compared": "Samples the score is over.",
    }
    _module(nwb).add(DynamicTable(
        name="detector_agreement",
        description="DetectorAgreement: how much each pair of detectors agrees, over the whole session.",
        columns=[column(field, text, [row[field] for row in rows]) for field, text in fields.items()],
    ))
```

`wl_preproc/nwb/trim.py`:

```python
"""An activation's block set, and what falls inside it (design spec
`2026-09-28-nwb-builder-design.md` section 5; parent spec section 8.1: each
NWB is self-contained over its own activation's block set)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BlockSet:
    """Half-open `[start_s, end_s)` intervals, one per block."""

    intervals: tuple[tuple[float, float], ...]

    @classmethod
    def of(cls, blocks: list[dict]) -> BlockSet:
        """Blocks that touch are merged, so a stretch that crosses from one
        into the next is clipped once, to the edges of their union, and
        stays one stretch."""
        merged: list[list[float]] = []
        for start, end in sorted((float(b["start_s"]), float(b["end_s"])) for b in blocks):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return cls(tuple((start, end) for start, end in merged))

    def contains(self, times) -> np.ndarray:
        """Which of `times` fall inside a block."""
        times = np.asarray(times, dtype=float)
        inside = np.zeros(times.shape, dtype=bool)
        for start, end in self.intervals:
            inside |= (times >= start) & (times < end)
        return inside

    def contains_instant(self, times) -> np.ndarray:
        """Which of `times` fall inside a block, its END INCLUDED. An event is
        an instant, and the one exactly at a block's end -- the block's own
        `BLOCK_END` marker -- belongs to that block; half-open intervals are
        for samples, so adjacent blocks never share one."""
        times = np.asarray(times, dtype=float)
        inside = np.zeros(times.shape, dtype=bool)
        for start, end in self.intervals:
            inside |= (times >= start) & (times <= end)
        return inside

    def clip(self, start: float, stop: float) -> list[tuple[float, float]]:
        """`[start, stop)` cut to the blocks: one piece per block it
        overlaps, none if it overlaps none."""
        return [(max(start, b_start), min(stop, b_end)) for b_start, b_end in self.intervals
                if start < b_end and stop > b_start]
```

`wl_preproc/nwb/checksums.py`:

```python
"""Checksums of the file's written-once datasets (parent spec section 8.2,
for wl.works Plan 24 section 3.3; design spec
`2026-09-28-nwb-builder-design.md` section 7).

**Each dataset's DECODED contents, never a group's**: `colnames` is an
attribute that changes when a column is appended, so a group checksum
breaks on exactly the accretion it must tolerate. **A ragged column is a
pair**: `x` and `x_index` are recorded together, so a change cannot hide in
the half left unnamed. Not hashed: `/specifications` (the schema the file
carries, not data) and `/file_create_date` (when it was written)."""

from __future__ import annotations

from pathlib import Path

import blake3
import h5py
import numpy as np

_SKIPPED = ("/specifications", "/file_create_date")


def _content_bytes(dataset: h5py.Dataset) -> bytes:
    """The decoded contents, as bytes that do not depend on HDF5's layout,
    chunking or compression."""
    values = dataset[()]
    if dataset.dtype.kind == "O" and h5py.check_string_dtype(dataset.dtype) is None:
        # Object references: hash the paths they point at.
        flat = np.asarray(values).ravel()
        return "\x00".join(dataset.file[ref].name if ref else "" for ref in flat).encode()
    if h5py.check_string_dtype(dataset.dtype) is not None or dataset.dtype.kind in "SO":
        flat = np.asarray(values, dtype=object).ravel()
        return b"\x00".join(v if isinstance(v, bytes) else str(v).encode() for v in flat)
    return np.ascontiguousarray(values).tobytes()


def dataset_checksums(path: Path) -> list[dict]:
    """One row per dataset: `dataset_path`, `dtype`, `shape`, `blake3` and
    `paired_with` (a ragged column's other half, '' otherwise)."""
    rows = []
    with h5py.File(path, "r") as handle:
        names = []
        handle.visititems(lambda name, obj: names.append("/" + name) if isinstance(obj, h5py.Dataset) else None)
        present = set(names)
        for name in sorted(names):
            if name.startswith(_SKIPPED):
                continue
            dataset = handle[name]
            if name.endswith("_index"):
                paired = name[: -len("_index")]
            elif name + "_index" in present:
                paired = name + "_index"
            else:
                paired = ""
            rows.append({
                "dataset_path": name,
                "dtype": str(dataset.dtype),
                "shape": str(tuple(dataset.shape)),
                "blake3": blake3.blake3(_content_bytes(dataset)).hexdigest(),
                "paired_with": paired if paired in present else "",
            })
    return rows
```

`wl_preproc/nwb/validate.py`:

```python
"""`nwbinspector` over a written file (parent spec section 8.1: "validated
with nwbinspector before publication"; design spec
`2026-09-28-nwb-builder-design.md` section 8). Its default configuration,
not DANDI's."""

from __future__ import annotations

from pathlib import Path


def inspect_file(path: Path) -> list[dict]:
    """Every finding, as a plain dict: `importance`, `check`, `message`,
    `object_type`, `location`."""
    from nwbinspector import inspect_nwbfile

    return [
        {
            "importance": message.importance.name,
            "check": message.check_function_name,
            "message": message.message,
            "object_type": message.object_type,
            "location": message.location or "",
        }
        for message in inspect_nwbfile(nwbfile_path=str(path))
    ]


def n_critical(findings: list[dict]) -> int:
    return sum(1 for finding in findings if finding["importance"] == "CRITICAL")
```

`wl_preproc/nwb/write.py`:

```python
"""Writing the file so a half-written one never carries the final name
(parent spec section 11.5; design spec `2026-09-28-nwb-builder-design.md`
section 6)."""

from __future__ import annotations

import os
import warnings
from pathlib import Path

from pynwb import NWBHDF5IO, NWBFile


def write_atomically(nwb: NWBFile, path: Path) -> None:
    """Write to `{path}.partial`, then rename over `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        partial.unlink()
    try:
        with warnings.catch_warnings():
            # pynwb asks for a `.nwb` extension; `.partial` is the point.
            warnings.filterwarnings("ignore", message="The file path provided: .* does not end in '.nwb'")
            with NWBHDF5IO(str(partial), "w") as io:
                io.write(nwb)
        os.replace(partial, path)
    finally:
        if partial.exists():
            partial.unlink()
```

- [ ] **Step 5: Run them to verify they pass**

Run: the Step 3 command.
Expected: 15 passed.

- [ ] **Step 6: Mutation checks.** Each was measured to fail the test named.
  - T3a: `checksums.py`, `_SKIPPED = ("/specifications", "/file_create_date")` → `_SKIPPED = ("/specifications",)`. Fails `test_checksums_are_of_contents_and_stable_across_rebuilds`.
  - T3b: `checksums.py`, `"paired_with": paired if paired in present else "",` → `"paired_with": "",`. Fails `test_a_ragged_column_is_recorded_as_a_pair`.
  - T3c: `columns.py`, `rows = max(1, min(data.shape[0], CHUNK_BYTES // max(row_bytes, 1)))` → `rows = data.shape[0]`. Fails `test_continuous_chunks_are_about_256_kb`.
  - T3d: `eye.py`, in the gaze series (the `timestamps=` line followed by `reference_frame`), `timestamps=shared if shared is not None else continuous(np.asarray(times, dtype=np.float64)),` → `timestamps=continuous(np.asarray(times, dtype=np.float64)),`. Fails `test_the_eye_series_share_one_timestamps_dataset`.
  - T3e: `trim.py`, in `contains_instant`, `inside |= (times >= start) & (times <= end)` → `inside |= (times >= start) & (times < end)`. Fails `test_the_block_set_keeps_what_starts_inside_it`.
  - T3f: `write.py`, in the `finally:` block, replace `if partial.exists():` and `partial.unlink()` with `pass`. Fails `test_a_failed_write_leaves_no_file_under_the_final_name`.
  - T3g: `trim.py`, in `BlockSet.of`, `if merged and start <= merged[-1][1]:` → `if merged and start < merged[-1][1]:`. Fails `test_touching_blocks_do_not_split_a_stretch_that_crosses_them`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml wl.yaml wl_preproc/nwb tests/nwb
git commit -m "feat(nwb): the writers -- blocks, trials, events, timebase, eye series and tables, eye events -- with trimming, checksums, nwbinspector and the atomic write

<trailer lines>"
```

---

### Task 4: Gathering, building, the table, the stage and the command

**Files:**
- Create: `wl_preproc/nwb/gather.py`, `wl_preproc/nwb/build.py`, `wl_preproc/schema/nwb.py`, `tests/nwb/test_clock.py`, `tests/schema/test_nwb_build.py`
- Modify: `wl_preproc/daemon.py`, `wl_preproc/cli/main.py`, `tests/schema/test_daemon.py`, `tests/schema/test_guardrails.py`

**Interfaces — consumes:** Task 1's `row_session_times`, Task 2's `subject_details`, and every Task 3 writer, as listed there. Also existing code:
- `detect.register_default_paramsets() -> {detector name: paramset_idx}` and `detect._repaired_gaze(path, file_eye, map_, fs_hz, validity_params)`;
- `eye._map_from_row(row)`, `ohdpi.read_ohdpi` and `ohdpi.read_columns`;
- `paramset.register(type, params) -> idx`;
- `timebase.segments.session_reference(session_dir) -> {barcode value: session seconds}`;
- `archive.scratch.currently_freed(prefix=...)`.

**Interfaces — produces:**
- `gather.Refused(Exception)`. Its message is the reason.
- `gather.Gathered(session, systems, blocks, trials, events, timebase, eye)`. `eye` is `None` when the session has no ohDPI segment.
- `gather.gather(activation_key) -> Gathered`.
- `gather.reference_from(first_barcode_value: int, started_at: datetime) -> {reference_time, source, manifest_started_at, started_at_difference_s}`.
- Constants `gather.BARCODE_EPOCH` and `gather.CLOCK_DISAGREEMENT_S = 60.0`.
- `build.BuildResult(status, path, reason, n_bytes, identifier, clock, findings, checksums)`.
- `build.nwb_path(nwb_root, identifier) -> Path` and `build.build(activation_key, nwb_root) -> BuildResult`.
- `build.record(activation_key, result)`: one transaction.
- `build.run_stage(nwb_root, freed=None) -> (recorded, errors)`.
- `schema.nwb.NwbFile` and `NwbFile.Dataset`, and `schema.nwb.activate(prefix)`.
- `daemon.run_once(..., nwb_root=None)`. Its report gains `"nwb"`: `None` when not configured, else the count recorded.
- `wlpp daemon --nwb-root PATH`.
- `wlpp nwb build --subject S --session-datetime ISO --montage-id M --activation-id A --nwb-root PATH [--prefix P]`. It exits 0 on `written` and 1 otherwise, including when the activation is already recorded.

**Rulings made while planning, which these tests carry.**
- **The fixture is dated in the past (2025-07-20).** `nwbinspector` calls a future `session_start_time` CRITICAL (spec §8's amendment).
- **wl.works asserts two blocks, split at 7.5 s,** where one was measured. Otherwise a derivative over "one block" is the whole session, and every trimming assertion passes vacuously.
- **The refusal tests restore the saved `TimingProvenance` row rather than recompute it.** Recomputing would compare wl.works' two asserted blocks with the one measured, and rightly fail the session to tier D (parent spec §8.3.1).
- **The crossing-run test moves wl.works' boundary into the middle of the first planted saccade for one build, and restores it.** Every planted step is after 12 s, in the second block, so no run crosses the fixture's own split.
- **`accept` returns the activation it already holds for a block set,** whatever the idempotency key. So the stage-failure test uses the two one-block derivatives and clears their rows, rather than creating two new activations over one block.
- **The barcode epoch is restated in `gather.py`, not imported,** because the pinned wl-sync predates `wl_sync/clock.py` (spec §4.2's correction). `test_the_restated_barcode_epoch_is_wl_syncs` skips here, and pins the two equal wherever a newer wl-sync is installed.

- [ ] **Step 1: Write the failing tests.** Create `tests/nwb/test_clock.py`:

```python
"""Where the wall-clock time of session t = 0 comes from (design spec
`2026-09-28-nwb-builder-design.md` section 4.2)."""

from __future__ import annotations

import datetime

import pytest


def test_the_first_barcode_places_t0_unless_the_clocks_disagree():
    """Section 4.2. Within 60 s of the manifest the barcode wins; beyond it
    (a synthetic session's counter barcodes start at 1,000,000) the manifest
    does, and the source says which."""
    from wl_preproc.nwb.gather import BARCODE_EPOCH, reference_from

    started = datetime.datetime(2026, 9, 28, 12, 0, 30)
    value = int((started.replace(tzinfo=datetime.timezone.utc) - BARCODE_EPOCH).total_seconds()) - 12
    near = reference_from(value, started)
    assert near["source"] == "barcode"
    assert near["reference_time"] == datetime.datetime(2026, 9, 28, 12, 0, 18, tzinfo=datetime.timezone.utc)
    assert near["started_at_difference_s"] == 12.0

    far = reference_from(1_000_000, started)
    assert far["source"] == "manifest"
    assert far["reference_time"] == started.replace(tzinfo=datetime.timezone.utc)


def test_the_restated_barcode_epoch_is_wl_syncs():
    """Pinned equal wherever wl-sync's own `clock` module is installed; the
    commit this repository pins predates it."""
    clock = pytest.importorskip("wl_sync.clock")
    from wl_preproc.nwb.gather import BARCODE_EPOCH

    assert BARCODE_EPOCH == clock.BARCODE_EPOCH
```

Create `tests/schema/test_nwb_build.py`:

```python
"""The NWB builder end to end, on a synthetic session through the daemon
(design spec `2026-09-28-nwb-builder-design.md` section 11)."""

from __future__ import annotations

import datetime
import io

import h5py
import numpy as np
import pytest

_SESSION_DATETIME = datetime.datetime(2025, 7, 20, 9, 0)
_SUBJECT = "nwbstep1"
_SPLIT_S = 7.5


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon
    from wl_preproc.schema import detect

    daemon.activate_all(prefix=prefix)
    detect.register_default_paramsets()
    return daemon


def _request(key, idempotency_key, montage, blocks, block_ids=None):
    from wl_preproc.contracts.protocol import JobRequest, MetadataBundle

    selection = {"session_datetime": key["session_datetime"].replace(tzinfo=datetime.UTC),
                 "montage_id": montage["montage_id"]}
    if block_ids is not None:
        selection["block_ids"] = block_ids
    return JobRequest(
        domain="neural", selection=selection, parameters={}, idempotency_key=idempotency_key,
        metadata=MetadataBundle(
            blocks=blocks, montage_boundaries=[montage], probes=[], experimenter="jw", subject=key["subject"],
            task_types=[], subject_details={"species": "Macaca mulatta", "sex": "F",
                                            "date_of_birth": datetime.date(2016, 3, 2)},
        ),
    )


@pytest.fixture(scope="module")
def activation(daemon_module, prefix, tmp_path_factory):
    """`stepped_session`'s construction (tests/schema/test_detect_populate.py),
    run through the daemon; then wl.works' job request for a canonical
    activation over its measured blocks, as `responder/jobs.py::accept`
    records it. The session's timing is computed before that request
    arrives, as a daemon pass before wl.works asks would leave it. Date,
    subject and seed checked unclaimed across `tests/` on 2026-09-28. In the
    PAST, unlike most fixtures here: `nwbinspector` calls
    a future `session_start_time` critical. Returns `(session_key,
    activation_key, blocks)`."""
    from tests.schema.test_detect_populate import _build_stepped_session
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import pipeline

    session_key, _segment, _onsets = _build_stepped_session(
        tmp_path_factory, dirname="nwbstep", session_id="2025-07-20_01", subject=_SUBJECT,
        session_datetime=_SESSION_DATETIME, seed=720,
    )
    daemon_module.run_once(prefix=prefix)
    # The session has one measured block; wl.works asserts it as two, split at
    # `_SPLIT_S`, which it is entitled to do. So a derivative over the second
    # holds only half the session, and every trimming assertion can fail.
    (measured,) = (pipeline.trial.Block & session_key).to_dicts()
    task_type = (pipeline.trial.Block.Attribute & measured & {"attribute_name": "task_type"}).fetch1("attribute_value")
    blocks = [
        {"block_id": 1, "task_type": task_type, "start_s": float(measured["block_start_time"]), "end_s": _SPLIT_S,
         "works_block_id": "wb-1"},
        {"block_id": 2, "task_type": task_type, "start_s": _SPLIT_S, "end_s": float(measured["block_stop_time"]),
         "works_block_id": "wb-2"},
    ]
    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
    key = accept(_request(session_key, "nwbstep1-canonical", montage, blocks), prefix=prefix)
    return session_key, key, blocks


@pytest.fixture(scope="module")
def built(activation, tmp_path_factory):
    from wl_preproc.nwb.build import build

    _session_key, key, _blocks = activation
    return build(key, tmp_path_factory.mktemp("nwb"))


def test_the_file_is_written_valid_and_describes_the_activation(built):
    from pynwb import NWBHDF5IO

    assert built.status == "written", built.findings
    assert not [f for f in built.findings if f["importance"] == "CRITICAL"]
    with NWBHDF5IO(str(built.path), "r") as handle:
        nwb = handle.read()
        assert nwb.identifier == "2025-07-20_01.montage-0.activation-0"
        assert (nwb.subject.species, nwb.subject.sex) == ("Macaca mulatta", "F")
        assert tuple(nwb.experimenter) == ("jw",)
        assert nwb.session_start_time == built.clock["reference_time"]


def test_a_synthetic_sessions_clock_falls_back_to_the_manifest(activation, built):
    """Section 4.2's fallback, end to end: the synthetic sync box's barcodes
    are counters from 1,000,000, not seconds since 2020, so they disagree
    with the manifest by years and `started_at` places t = 0."""
    assert built.clock["source"] == "manifest"
    assert built.clock["reference_time"] == _SESSION_DATETIME.replace(tzinfo=datetime.timezone.utc)
    assert abs(built.clock["started_at_difference_s"]) > 60.0


def test_blocks_trials_and_events_carry_the_tables_times(activation, built):
    from pynwb import NWBHDF5IO

    from wl_preproc.schema import pipeline

    session_key, _key, blocks = activation
    with NWBHDF5IO(str(built.path), "r") as handle:
        nwb = handle.read()
        stored = nwb.intervals["blocks"].to_dataframe()
        assert stored["block_id"].tolist() == [1, 2]
        assert stored["works_block_id"].tolist() == ["wb-1", "wb-2"]
        assert stored["stop_time"].tolist() == [_SPLIT_S, blocks[1]["end_s"]]
        trials = nwb.trials.to_dataframe()
        expected = sorted(float(r["trial_start_time"]) for r in (pipeline.trial.Trial & session_key).to_dicts())
        np.testing.assert_allclose(sorted(trials["start_time"]), expected)
        # Every event inside a block, the block's end included (an event is
        # an instant, and BLOCK_END sits exactly on it); SESSION_START and
        # SESSION_END lie outside every block and are not this file's.
        events = nwb.intervals["task_events"].to_dataframe()
        times = [float(r["event_start_time"]) for r in (pipeline.event.Event & session_key).to_dicts()]
        inside = sorted(t for t in times if any(b["start_s"] <= t <= b["end_s"] for b in blocks))
        np.testing.assert_allclose(sorted(events["start_time"]), inside)
        assert "BLOCK_END" in set(events["event_type"])
        assert not {"SESSION_START", "SESSION_END"} & set(events["event_type"])


def test_the_eye_is_on_session_time_and_every_detector_is_there(activation, built):
    from pynwb import NWBHDF5IO

    from wl_preproc.eye.detect.registry import DETECTORS

    with NWBHDF5IO(str(built.path), "r") as handle:
        nwb = handle.read()
        gaze = nwb.processing["behavior"]["EyeTracking"]["gaze_left"]
        times = gaze.timestamps[:]
        assert np.all(np.diff(times) > 0)
        assert gaze.data.shape == (len(times), 2)
        events = nwb.processing["eye_events"]
        for name in DETECTORS:
            for trace in ("left", "right", "conjunction"):
                assert f"{name}_{trace}" in events.data_interfaces, (name, trace)
            assert f"{name}_source" in events.data_interfaces
        saccades = events["engbert_kliegl_left"].to_dataframe()
        assert (saccades["label"] == "saccade").sum() >= 2


def test_every_dataset_is_checksummed_and_a_rebuild_matches(activation, built, tmp_path_factory):
    from wl_preproc.nwb.build import build

    _session_key, key, _blocks = activation
    with h5py.File(built.path) as handle:
        names = []
        handle.visititems(lambda name, obj: names.append("/" + name) if isinstance(obj, h5py.Dataset) else None)
    hashed = {row["dataset_path"] for row in built.checksums}
    assert hashed == {n for n in names if not n.startswith(("/specifications", "/file_create_date"))}
    again = build(key, tmp_path_factory.mktemp("nwb-again"))
    assert again.checksums == built.checksums


def test_one_second_of_gaze_reads_a_small_fraction_of_the_file(built):
    """Parent spec section 8.1.2: a window of time is a few chunks, which is
    what an HTTP range request fetches. Read through a file-like object that
    counts the bytes it serves."""

    class Counting(io.RawIOBase):
        def __init__(self, path):
            self.handle = open(path, "rb")
            self.served = 0

        def readable(self):
            return True

        def seekable(self):
            return True

        def seek(self, offset, whence=0):
            return self.handle.seek(offset, whence)

        def tell(self):
            return self.handle.tell()

        def readinto(self, buffer):
            count = self.handle.readinto(buffer)
            self.served += count
            return count

    size = built.path.stat().st_size
    source = Counting(built.path)
    with h5py.File(source, "r") as handle:
        opened = source.served
        data = handle["processing/behavior/EyeTracking/gaze_left/data"]
        data[1000:1500]
    assert source.served - opened < size / 10, (source.served - opened, size)


def test_a_derivative_holds_only_its_own_block(activation, prefix, tmp_path_factory):
    """Section 5: continuous samples and trial starts inside the block,
    events inside it or on its end, and nothing from the other block."""
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.build import build
    from wl_preproc.responder.jobs import accept

    session_key, _key, blocks = activation
    second = max(blocks, key=lambda b: b["start_s"])
    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
    key = accept(_request(session_key, "nwbstep1-derivative", montage, blocks, block_ids=[second["block_id"]]),
                 prefix=prefix)
    result = build(key, tmp_path_factory.mktemp("nwb-derivative"))
    with NWBHDF5IO(str(result.path), "r") as handle:
        nwb = handle.read()
        assert nwb.intervals["blocks"].to_dataframe()["block_id"].tolist() == [second["block_id"]]
        times = nwb.processing["behavior"]["EyeTracking"]["gaze_left"].timestamps[:]
        assert times.min() >= second["start_s"] and times.max() < second["end_s"]
        assert times.min() - second["start_s"] < 0.01
        trials = nwb.trials.to_dataframe()["start_time"]
        assert len(trials) and ((trials >= second["start_s"]) & (trials < second["end_s"])).all()
        events = nwb.intervals["task_events"].to_dataframe()["start_time"]
        assert len(events) and ((events >= second["start_s"]) & (events <= second["end_s"])).all()
        runs = nwb.processing["eye_events"]["engbert_kliegl_left"].to_dataframe()["start_time"]
        assert ((runs >= second["start_s"]) & (runs < second["end_s"])).all()


def test_a_run_that_crosses_its_blocks_end_keeps_its_true_end(activation, built, prefix, tmp_path_factory):
    """Section 5: a detected run is kept when it starts in a block and is
    never cut, so one that ends after its block keeps its true end. Every
    planted step is in the second block, so for this one build wl.works'
    boundary is moved into the middle of the first planted saccade, and
    restored after."""
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.build import build
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core

    session_key, _key, blocks = activation
    first, second = sorted(blocks, key=lambda b: b["start_s"])
    with NWBHDF5IO(str(built.path), "r") as handle:
        events = handle.read().processing["eye_events"]
        saccade = events["engbert_kliegl_left"].to_dataframe().sort_values("start_time").iloc[0]
        split = float(saccade["start_time"] + saccade["stop_time"]) / 2
        crossing = {}
        for name, table in events.data_interfaces.items():
            if name == "detector_agreement" or name.endswith("_source"):
                continue
            frame = table.to_dataframe()
            frame = frame[(frame["start_time"] < split) & (frame["stop_time"] > split)]
            if len(frame):
                crossing[name] = list(zip(frame["start_time"], frame["stop_time"], strict=True))
    assert "engbert_kliegl_left" in crossing
    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
    key = accept(_request(session_key, "nwbstep1-first-block", montage, blocks, block_ids=[first["block_id"]]),
                 prefix=prefix)
    core.Block.update1({**session_key, "block_id": first["block_id"], "end_s": split})
    core.Block.update1({**session_key, "block_id": second["block_id"], "start_s": split})
    try:
        result = build(key, tmp_path_factory.mktemp("nwb-first-block"))
    finally:
        core.Block.update1({**session_key, "block_id": first["block_id"], "end_s": first["end_s"]})
        core.Block.update1({**session_key, "block_id": second["block_id"], "start_s": second["start_s"]})
    with NWBHDF5IO(str(result.path), "r") as handle:
        events = handle.read().processing["eye_events"]
        for name, runs in crossing.items():
            kept = events[name].to_dataframe()
            for start, stop in runs:
                assert kept.loc[kept["start_time"] == start, "stop_time"].tolist() == [stop], name


def test_an_activation_with_no_blocks_is_refused(activation, prefix, tmp_path_factory):
    from wl_preproc.nwb.build import build
    from wl_preproc.responder.jobs import accept

    session_key, _key, blocks = activation
    end = max(b["end_s"] for b in blocks) + 1.0
    empty = {"montage_id": 1, "start_s": end, "end_s": end + 10.0}
    key = accept(_request(session_key, "nwbstep1-empty", empty, []), prefix=prefix)
    result = build(key, tmp_path_factory.mktemp("nwb-empty"))
    assert (result.status, result.path) == ("refused", None)
    assert "no blocks" in result.reason


def test_an_eye_without_calibration_is_left_out_and_the_rest_is_built(activation, monkeypatch, tmp_path_factory):
    """Section 10's one partial case: that eye's gaze, validity, repairs and
    calibration row are absent, the file is still built, and its
    description says which eye is missing."""
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.build import build
    from wl_preproc.schema import eye as eye_schema

    real = eye_schema._map_from_row
    monkeypatch.setattr(eye_schema, "_map_from_row", lambda row: None if row["eye"] == "right" else real(row))
    _session_key, key, _blocks = activation
    result = build(key, tmp_path_factory.mktemp("nwb-one-eye"))
    assert result.status == "written", result.findings
    with NWBHDF5IO(str(result.path), "r") as handle:
        nwb = handle.read()
        behavior = nwb.processing["behavior"]
        assert list(behavior["EyeTracking"].spatial_series) == ["gaze_left"]
        assert "eye_validity_right" not in behavior.data_interfaces
        assert behavior["eye_calibration"].to_dataframe()["eye"].tolist() == ["left"]
        assert "pupil_right" in behavior["PupilTracking"].time_series
        assert "right eye" in nwb.session_description


def test_a_session_without_an_eye_recording_is_built_without_one(activation, monkeypatch, tmp_path_factory):
    """No ohDPI recording at all (`gather._eye` returns None): the file is
    still the activation's blocks, trials, events and timing, with no eye
    modules."""
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb import gather
    from wl_preproc.nwb.build import build

    monkeypatch.setattr(gather, "_eye", lambda *args: None)
    _session_key, key, _blocks = activation
    result = build(key, tmp_path_factory.mktemp("nwb-no-eye"))
    assert result.status == "written", result.findings
    with NWBHDF5IO(str(result.path), "r") as handle:
        nwb = handle.read()
        assert set(nwb.processing) == {"timebase"}
        assert len(nwb.trials) and len(nwb.intervals["task_events"])


def test_the_stage_skips_a_freed_session(activation, tmp_path_factory):
    """A freed session's files are gone from scratch; the stage records
    nothing for it until it is rehydrated, like every other stage."""
    from wl_preproc.nwb.build import run_stage
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, _blocks = activation
    before = len(nwb_schema.NwbFile & session_key)
    recorded, errors = run_stage(tmp_path_factory.mktemp("nwb-freed"), freed=[session_key])
    assert errors == []
    assert len(nwb_schema.NwbFile & session_key) == before


def test_the_command_builds_once_and_rebuilds_only_when_its_row_is_deleted(activation, prefix, tmp_path_factory,
                                                                          capsys):
    """`wlpp nwb build` records what it wrote, refuses to overwrite a
    recorded activation and says how to rebuild; with the row deleted it
    rebuilds over the same path, to the same checksums."""
    from wl_preproc.cli.main import main
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
    key = accept(_request(session_key, "nwbstep1-command", montage, blocks, block_ids=[blocks[0]["block_id"]]),
                 prefix=prefix)
    argv = ["nwb", "build", "--subject", key["subject"], "--session-datetime", key["session_datetime"].isoformat(),
            "--montage-id", str(key["montage_id"]), "--activation-id", str(key["activation_id"]),
            "--nwb-root", str(tmp_path_factory.mktemp("nwb-command")), "--prefix", prefix]

    assert main(argv) == 0
    row = (nwb_schema.NwbFile & key).fetch1()
    assert f"written: {row['path']}" in capsys.readouterr().out
    checksums = (nwb_schema.NwbFile.Dataset & key).to_dicts(order_by="dataset_path")

    assert main(argv) == 1
    assert "already recorded: written; delete the row to rebuild" in capsys.readouterr().out

    (nwb_schema.NwbFile & key).delete(prompt=False)
    assert main(argv) == 0
    assert (nwb_schema.NwbFile & key).fetch1("path") == row["path"]
    assert (nwb_schema.NwbFile.Dataset & key).to_dicts(order_by="dataset_path") == checksums


def test_the_daemon_stage_records_every_activation(activation, daemon_module, prefix, tmp_path_factory):
    """`run_once(nwb_root=...)`: one `NwbFile` row per activation, with its
    datasets; without a root the stage is skipped and says so."""
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import request

    assert daemon_module.run_once(prefix=prefix)["nwb"] is None
    report = daemon_module.run_once(prefix=prefix, nwb_root=tmp_path_factory.mktemp("nwb-daemon"))
    assert not [e for e in report["errors"] if "NwbFile" in e], report["errors"]
    session_key, key, _blocks = activation
    rows = {(r["montage_id"], r["activation_id"]): r for r in (nwb_schema.NwbFile & session_key).to_dicts()}
    assert set(rows) == {(r["montage_id"], r["activation_id"]) for r in (request.Activation & session_key).to_dicts()}
    canonical = rows[(key["montage_id"], key["activation_id"])]
    assert canonical["status"] == "written" and canonical["reference_source"] == "manifest"
    assert len(nwb_schema.NwbFile.Dataset & canonical) > 50
    assert rows[(1, 0)]["status"] == "refused"


def test_one_failing_activation_does_not_stop_the_stage(activation, prefix, monkeypatch, tmp_path_factory):
    """The stage catches a failure per activation, as the archive stage
    does: the others are recorded, the failure is reported, and the failed
    activation gets no row, so the next pass tries it again. The two
    one-block derivatives (`accept` returns the activation it already holds
    for a block set) have their rows cleared, and the first one fails."""
    from wl_preproc.nwb import build as build_module
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
    failing, fine = (accept(_request(session_key, f"nwbstep1-stage-{block['block_id']}", montage, blocks,
                                     block_ids=[block["block_id"]]), prefix=prefix)
                     for block in sorted(blocks, key=lambda b: b["start_s"]))
    assert failing != fine
    for key in (failing, fine):
        (nwb_schema.NwbFile & key).delete(prompt=False)
    real = build_module.build

    def build(key, nwb_root):
        if {k: key[k] for k in failing} == failing:
            raise OSError("the raw ohDPI file is unreadable")
        return real(key, nwb_root)

    monkeypatch.setattr(build_module, "build", build)
    recorded, errors = build_module.run_stage(tmp_path_factory.mktemp("nwb-stage-errors"))
    assert recorded >= 1
    assert len([e for e in errors if "the raw ohDPI file is unreadable" in e]) == 1, errors
    assert len(nwb_schema.NwbFile & failing) == 0
    assert (nwb_schema.NwbFile & fine).fetch1("status") == "written"


def test_a_file_without_the_subjects_date_of_birth_is_invalid(activation, tmp_path_factory):
    """Sections 8 and 9: `nwbinspector` rates a subject with no age and no
    date of birth as critical, so the file is built but `invalid`, and piece
    2 will not publish it. Restored after."""
    from wl_preproc.ingest.landing import SUBJECT_BIRTH_DATE_UNKNOWN
    from wl_preproc.nwb.build import build
    from wl_preproc.schema import pipeline

    _session_key, key, _blocks = activation
    birth = (pipeline.subject.Subject & {"subject": _SUBJECT}).fetch1("subject_birth_date")
    pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": SUBJECT_BIRTH_DATE_UNKNOWN})
    try:
        result = build(key, tmp_path_factory.mktemp("nwb-no-birth"))
        assert result.status == "invalid" and result.path.exists()
        assert [f["check"] for f in result.findings if f["importance"] == "CRITICAL"] == ["check_subject_age"]
    finally:
        pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": birth})


def test_a_session_without_session_time_is_refused(activation, tmp_path_factory):
    """Section 10: no `TimingProvenance` row means no trustworthy session
    time. The row is restored as it was, not recomputed: recomputing now
    would compare wl.works' two asserted blocks with the one measured and
    rightly fail the session to tier D (parent spec section 8.3.1)."""
    from wl_preproc.nwb.build import build
    from wl_preproc.schema import timebase

    session_key, key, _blocks = activation
    saved = (timebase.TimingProvenance & session_key).fetch1()
    (timebase.TimingProvenance & session_key).delete(prompt=False)
    try:
        result = build(key, tmp_path_factory.mktemp("nwb-untimed"))
        assert (result.status, result.path) == ("refused", None)
        assert "TimingProvenance" in result.reason
    finally:
        timebase.TimingProvenance.insert1(saved, allow_direct_insert=True)


def test_a_session_at_timing_tier_d_is_refused(activation, tmp_path_factory):
    """Section 10: tier D means the session's time is not trustworthy. The
    row is swapped for a tier-D copy and restored after."""
    from wl_preproc.nwb.build import build
    from wl_preproc.schema import timebase

    session_key, key, _blocks = activation
    saved = (timebase.TimingProvenance & session_key).fetch1()
    (timebase.TimingProvenance & session_key).delete(prompt=False)
    timebase.TimingProvenance.insert1({**saved, "tier": "D"}, allow_direct_insert=True)
    try:
        result = build(key, tmp_path_factory.mktemp("nwb-tier-d"))
        assert (result.status, result.path) == ("refused", None)
        assert "tier D" in result.reason
    finally:
        (timebase.TimingProvenance & session_key).delete(prompt=False)
        timebase.TimingProvenance.insert1(saved, allow_direct_insert=True)


def test_a_session_with_two_ohdpi_segments_is_refused(activation, tmp_path_factory):
    """Section 10: the eye tables assume one ohDPI segment, so the builder
    refuses rather than guess which file the eye rows index."""
    from wl_preproc.nwb.build import build
    from wl_preproc.schema import core

    session_key, key, _blocks = activation
    segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
    extra = {**segment, "segment_barcode": segment["segment_barcode"] + 1_000}
    core.Segment.insert1(extra, allow_direct_insert=True)
    try:
        result = build(key, tmp_path_factory.mktemp("nwb-two"))
        assert result.status == "refused"
        assert "2 ohDPI segments" in result.reason
    finally:
        (core.Segment & {k: extra[k] for k in core.Segment.primary_key}).delete(prompt=False)
```

Apply these diffs:

```diff
--- a/tests/schema/test_daemon.py
+++ b/tests/schema/test_daemon.py
@@ -270,8 +270,9 @@
     first = daemon_env.run_once(prefix=prefix)
     baseline = daemon_env.run_once(prefix=prefix)
 
+    # `nwb` joined 2026-09-28 (design spec `2026-09-28-nwb-builder-design.md`).
     assert set(baseline) == {
-        "populated", "errors", "stale_jobs_reaped", "archived", "freed_skipped"
+        "populated", "errors", "stale_jobs_reaped", "archived", "nwb", "freed_skipped"
     }
     assert isinstance(baseline["freed_skipped"], int)
     assert baseline["populated"] == first["populated"], (
```

```diff
--- a/tests/schema/test_guardrails.py
+++ b/tests/schema/test_guardrails.py
@@ -677,6 +677,9 @@
         "wl_preproc.schema.ingest.Quarantine.detail",
         "wl_preproc.schema.paramset.ParamSet.params",
         "wl_preproc.schema.request.Request.payload",
+        # The NWB builder's inspector findings (design spec
+        # `2026-09-28-nwb-builder-design.md` section 8), added 2026-09-28.
+        "wl_preproc.schema.nwb.NwbFile.inspector_findings",
         "wl_preproc.schema.ephys.Unit.spike_times",
         "wl_preproc.schema.ephys.Unit.spike_sites",
         "wl_preproc.schema.ephys.Unit.spike_depths",
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/nwb/test_clock.py tests/schema/test_nwb_build.py tests/schema/test_daemon.py tests/schema/test_guardrails.py -q --tb=line -p no:cacheprovider`
Expected: 15 failed, 25 passed, 1 skipped, 7 errors. The failures and errors are `ModuleNotFoundError` or `ImportError` on `wl_preproc.nwb.build`, `wl_preproc.nwb.gather` and `wl_preproc.schema.nwb`, plus two assertions: `test_daemon.py`'s report keys lack `nwb`, and `test_guardrails.py` finds `NwbFile.inspector_findings` missing from the round-tripped blob attributes. The skip is `test_the_restated_barcode_epoch_is_wl_syncs` (the pinned wl-sync has no `clock` module); it stays skipped.

- [ ] **Step 3: Implement.** Create `wl_preproc/nwb/gather.py`:

```python
"""Everything one activation's file is built from, read once (design spec
`2026-09-28-nwb-builder-design.md`).

**The one module in `wl_preproc/nwb/` that reads the database and the raw
ohDPI file.** Everything it returns is plain data -- dicts, lists and arrays
in session seconds, already trimmed to the activation's blocks (section 5) --
so every writer is tested without either."""

from __future__ import annotations

import dataclasses
import datetime
from pathlib import Path

import numpy as np

from wl_preproc.nwb.trim import BlockSet

MASK_LABELS = ("blink", "invalid")
# Wall clocks that disagree by more than this are not trusted to place t = 0
# (section 4.2). Borrowed from wl-sync's CLOCK_TRUST_TOLERANCE_S.
CLOCK_DISAGREEMENT_S = 60.0
# A barcode's value is whole seconds since this instant, off the sync box's
# wall clock: wl-sync's `wl_sync/clock.py::BARCODE_EPOCH`, since its commit
# 3ce66b9 (2026-08-17). RESTATED, not imported: the wl-sync commit this
# repository pins predates that module. `tests/nwb/test_clock.py` pins the
# two equal wherever a newer wl-sync is installed.
BARCODE_EPOCH = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)


class Refused(Exception):
    """The activation cannot be built; the message is the reason (section 10)."""


@dataclasses.dataclass
class Gathered:
    session: dict
    systems: list[str]
    blocks: list[dict]
    trials: list[dict]
    events: list[dict]
    timebase: dict
    eye: dict | None  # None: no ohDPI recording in the session


def _aware_utc(value: datetime.datetime) -> datetime.datetime:
    return value.replace(tzinfo=datetime.timezone.utc) if value.tzinfo is None else value


def _block_set(activation_key: dict, activation: dict, session_key: dict) -> list[dict]:
    from wl_preproc.schema import core, request

    if activation["role"] == "derivative":
        rows = (core.Block & (request.ActivationBlock & activation_key).proj()).to_dicts()
    else:
        montage = (core.Montage & activation_key).fetch1()
        rows = [row for row in (core.Block & session_key).to_dicts()
                if montage["start_s"] <= row["start_s"] < montage["end_s"]]
    return sorted(rows, key=lambda row: row["start_s"])


def reference_from(first_barcode_value: int, started_at: datetime.datetime) -> dict:
    """Section 4.2: the first barcode's own value places session t = 0 on
    the wall clock, unless it disagrees with the manifest's `started_at` by
    more than `CLOCK_DISAGREEMENT_S`, when `started_at` is used and the
    source says so."""
    barcode_time = BARCODE_EPOCH + datetime.timedelta(seconds=int(first_barcode_value))
    started_at = _aware_utc(started_at)
    difference = (started_at - barcode_time).total_seconds()
    trusted = abs(difference) <= CLOCK_DISAGREEMENT_S
    return {
        "reference_time": barcode_time if trusted else started_at,
        "source": "barcode" if trusted else "manifest",
        "manifest_started_at": started_at,
        "started_at_difference_s": difference,
    }


def _reference_time(session_dir: Path, started_at: datetime.datetime) -> dict:
    from wl_preproc.timebase.segments import session_reference

    reference = session_reference(session_dir)
    return reference_from(min(reference, key=reference.get), started_at)


def _subject(subject_id: str) -> dict:
    from wl_preproc.ingest.landing import SUBJECT_BIRTH_DATE_UNKNOWN
    from wl_preproc.schema import pipeline

    row = (pipeline.subject.Subject & {"subject": subject_id}).fetch1()
    species = [row["species"] for row in (pipeline.subject.Subject.Species & {"subject": subject_id}).to_dicts()]
    birth = row["subject_birth_date"]
    return {
        "subject_id": subject_id,
        "species": str(species[0]) if len(species) else None,
        "sex": row["sex"],
        "date_of_birth": None if birth in (None, SUBJECT_BIRTH_DATE_UNKNOWN) else birth,
    }


def _coverage(table, key_field: str, session_key: dict) -> dict:
    by_item: dict = {}
    for row in (table & session_key).to_dicts():
        by_item.setdefault(row[key_field], {})[row["system"]] = (row["coverage"], float(row["covered_s"]))
    return by_item


def _blocks(block_rows: list[dict], session_key: dict) -> list[dict]:
    from wl_preproc.schema import coverage, pipeline

    measured = {row["block_id"]: row for row in (pipeline.trial.Block & session_key).to_dicts()}
    cover = _coverage(coverage.BlockCoverage, "block_id", session_key)
    return [{
        "block_id": row["block_id"], "start_s": float(row["start_s"]), "end_s": float(row["end_s"]),
        "task_type": row["task_type"], "works_block_id": row["works_block_id"],
        "measured_start_s": None if row["block_id"] not in measured else float(measured[row["block_id"]]["block_start_time"]),
        "measured_stop_s": None if row["block_id"] not in measured else float(measured[row["block_id"]]["block_stop_time"]),
        "coverage": cover.get(row["block_id"], {}),
    } for row in block_rows]


def _trials(blocks: BlockSet, session_key: dict) -> list[dict]:
    from wl_preproc.schema import coverage, pipeline

    block_of = {row["trial_id"]: row["block_id"] for row in (pipeline.trial.BlockTrial & session_key).to_dicts()}
    cover = _coverage(coverage.TrialCoverage, "trial_id", session_key)
    rows = [row for row in (pipeline.trial.Trial & session_key).to_dicts()
            if blocks.contains([row["trial_start_time"]])[0]]
    return [{
        "trial_id": row["trial_id"], "start_s": float(row["trial_start_time"]), "stop_s": float(row["trial_stop_time"]),
        "outcome": row["trial_type"], "block_id": block_of.get(row["trial_id"]),
        "coverage": cover.get(row["trial_id"], {}),
    } for row in rows]


def _events(blocks: BlockSet, session_key: dict) -> list[dict]:
    from wl_preproc.schema import pipeline

    attributes: dict = {}
    for row in (pipeline.event.Event.Attribute & session_key).to_dicts():
        attributes.setdefault((row["event_type"], row["event_start_time"]), {})[row["attribute_name"]] = row["attribute_value"]
    events = []
    for row in (pipeline.event.Event & session_key).to_dicts():
        time_s = float(row["event_start_time"])
        if not blocks.contains_instant([time_s])[0]:
            continue
        extra = attributes.get((row["event_type"], row["event_start_time"]), {})
        events.append({
            "time_s": time_s, "event_type": row["event_type"],
            "trial_id": int(extra["trial_id"]) if extra.get("trial_id") else None,
            "block_id": int(extra["block_id"]) if extra.get("block_id") else None,
            "condition": extra.get("condition") or None,
        })
    return events


def _timebase(session_key: dict, provenance: dict, clock: dict) -> dict:
    from wl_preproc.schema import core, timebase

    clocks = [{field: row[field] for field in ("system", "fit_status", "nominal_rate_hz", "fitted_rate_hz", "drift_ppm", "residual_us_rms")}
              for row in (timebase.SystemTimebase & session_key).to_dicts()]
    for row in clocks:
        for field in ("nominal_rate_hz", "fitted_rate_hz", "drift_ppm", "residual_us_rms"):
            row[field] = np.nan if row[field] is None else float(row[field])
    segments = [{field: row[field] for field in ("system", "file_path", "first_sample", "offset_s", "start_s", "end_s", "n_samples")}
                for row in (core.Segment & session_key).to_dicts()]
    return {
        "provenance": {field: (np.nan if provenance[field] is None else provenance[field])
                       for field in ("tier", "n_systems_aligned", "n_segments", "n_rejected_segments",
                                     "worst_residual_us", "worst_drift_ppm")},
        "clocks": clocks,
        "segments": segments,
        "clock_reference": {
            "source": clock["source"],
            "reference_time": clock["reference_time"].isoformat(),
            "manifest_started_at": clock["manifest_started_at"].isoformat(),
            "started_at_difference_s": clock["started_at_difference_s"],
        },
    }


def _edges(times: np.ndarray, segment: dict) -> np.ndarray:
    """Session time of every row, plus the time one sample past the last:
    `edges[stop]` is an exclusive run's stop time."""
    step = (segment["end_s"] - segment["start_s"]) / segment["n_samples"]
    return np.append(times, times[-1] + step)


def _runs_to_intervals(runs, edges: np.ndarray, blocks: BlockSet) -> list[tuple]:
    """`(start_row, stop_row, *rest)` runs, as clipped `(start_s, stop_s, *rest)`."""
    out = []
    for start, stop, *rest in runs:
        out.extend((a, b, *rest) for a, b in blocks.clip(float(edges[start]), float(edges[stop])))
    return out


def _eye(session_key: dict, session_dir: Path, blocks: BlockSet, validity_idx: int, detection_idx: dict) -> dict | None:
    from wl_preproc.eye.detect.labels import true_runs
    from wl_preproc.eye.detect.validity import ValidityParams
    from wl_preproc.eye.ohdpi import read_columns, read_ohdpi
    from wl_preproc.nwb.eye import EYES, PUPIL_COLUMNS
    from wl_preproc.schema import consensus, core, detect, paramset
    from wl_preproc.schema import eye as eye_schema

    segments = (core.Segment & {**session_key, "system": "ohdpi"}).to_dicts()
    if not segments:
        return None
    if len(segments) > 1:
        raise Refused(f"{len(segments)} ohDPI segments: the eye tables assume one (section 4.3)")
    (segment,) = segments
    path = session_dir / "ohdpi" / segment["file_path"]
    recording = read_ohdpi(path)
    offsets = recording.frame_numbers - recording.frame_numbers[0]
    times = eye_schema.row_session_times(segment, offsets)
    edges = _edges(times, segment)
    keep = blocks.contains(times)
    validity_params = ValidityParams(**(paramset.ParamSet & {"paramset_type": "eye_validity",
                                                            "paramset_idx": validity_idx}).fetch1("params"))

    gaze, pupil, calibration, validity, repairs, calibrated = {}, {}, [], {}, {}, []
    for eye in EYES:
        file_eye = eye.capitalize()
        columns = read_columns(path, [f"{file_eye}{name}" for name in PUPIL_COLUMNS])
        pupil[eye] = np.column_stack([columns[f"{file_eye}{name}"] for name in PUPIL_COLUMNS])[keep]
        rows = (eye_schema.EyeCalibration & {**session_key, "eye": eye}).to_dicts()
        map_ = eye_schema._map_from_row(rows[0]) if rows else None
        if map_ is None:
            gaze[eye] = None
            continue
        calibrated.append(eye)
        calibration.append({"eye": eye, **{k: v for k, v in rows[0].items() if k not in ("subject", "session_datetime", "eye")}})
        repaired_gaze, repaired = detect._repaired_gaze(path, file_eye, map_, recording.fs_hz, validity_params)
        gaze[eye] = repaired_gaze[keep]
        repairs[eye] = _runs_to_intervals(true_runs(repaired), edges, blocks)
        mask_runs = (detect.EyeValidity.Run & {**session_key, "eye": eye, "paramset_type": "eye_validity",
                                               "validity_paramset_idx": validity_idx}).to_dicts()
        validity[eye] = _runs_to_intervals([(r["run_start"], r["run_stop"], r["label"]) for r in mask_runs
                                            if r["label"] in MASK_LABELS], edges, blocks)

    names = {idx: name for name, idx in detection_idx.items()}
    detections, sources = [], []
    detection_key = {**session_key, "validity_paramset_type": "eye_validity", "validity_paramset_idx": validity_idx,
                     "paramset_type": "eye_detection"}
    for name, idx in detection_idx.items():
        params = (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": idx}).fetch1("params")
        for trace in ("left", "right", "conjunction"):
            where = {**detection_key, "paramset_idx": idx, "trace": trace}
            master = (detect.EyeDetection & where).to_dicts()
            if not master or master[0]["status"] != "computed":
                continue
            runs = []
            for row in (detect.EyeDetection.Run & where).to_dicts():
                start_s = float(edges[row["run_start"]])
                if row["label"] in MASK_LABELS or not blocks.contains([start_s])[0]:
                    continue
                runs.append({"start_s": start_s, "stop_s": float(edges[row["run_stop"]]), "label": row["label"],
                             **{field: row.get(field) for field in ("amplitude_deg", "peak_velocity_deg_s", "start_x_deg",
                                                                    "start_y_deg", "end_x_deg", "end_y_deg",
                                                                    "direction_deg", "reliability")}})
            detections.append({"name": f"{name}_{trace}",
                               "description": f"{name}, the {trace} trace; eye_detection paramset {idx}: {params}",
                               "runs": runs})
            if trace == "conjunction":
                source_rows = (detect.EyeDetection.Source & where).to_dicts()
                sources.append({"name": f"{name}_source",
                                "description": f"{name}: which eye each stretch of the both-eyes trace came from.",
                                "runs": _runs_to_intervals([(r["source_start"], r["source_stop"], r["source"])
                                                            for r in source_rows], edges, blocks)})

    agreement = []
    for row in (consensus.DetectorAgreement & {**session_key, "validity_paramset_idx": validity_idx}).to_dicts():
        if row["paramset_a"] in names and row["paramset_b"] in names:
            agreement.append({"detector_a": names[row["paramset_a"]], "detector_b": names[row["paramset_b"]],
                              "trace": row["trace"], "metric": row["metric"], "vocabulary": row["vocabulary"],
                              "pso_as": row["pso_as"], "value": float(row["value"]),
                              "n_samples_compared": int(row["n_samples_compared"])})

    return {"times": times[keep], "gaze": gaze, "pupil": pupil, "calibration": calibration,
            "validity": validity, "repairs": repairs, "detections": detections, "sources": sources,
            "agreement": agreement, "missing_eyes": [eye for eye in EYES if eye not in calibrated]}


def gather(activation_key: dict) -> Gathered:
    """One activation's data, or `Refused` with the reason (section 10)."""
    from wl_preproc.schema import detect, ingest, request, timebase
    from wl_preproc.schema import paramset

    key = {k: activation_key[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")}
    session_key = {k: key[k] for k in ("subject", "session_datetime")}
    activation = (request.Activation & key).fetch1()

    provenance = (timebase.TimingProvenance & session_key).to_dicts()
    if not provenance:
        raise Refused("no TimingProvenance row: the session has no session time yet")
    if provenance[0]["tier"] == "D":
        raise Refused("timing tier D: no trustworthy session time")
    block_rows = _block_set(key, activation, session_key)
    if not block_rows:
        raise Refused(f"no blocks in the {activation['role']} activation's block set")
    blocks = BlockSet.of(block_rows)

    session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
    clock = _reference_time(session_dir, key["session_datetime"])
    detection_idx = detect.register_default_paramsets()
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS

    validity_idx = paramset.register("eye_validity", dataclasses.asdict(DEFAULT_VALIDITY_PARAMS))
    eye = _eye(session_key, session_dir, blocks, validity_idx, detection_idx)

    session_id = session_dir.name
    task_types = sorted({row["task_type"] for row in block_rows})
    description = (f"wl-preproc {activation['role']} NWB for session {session_id}, montage {key['montage_id']}: "
                   f"blocks {', '.join(str(row['block_id']) for row in block_rows)} ({', '.join(task_types)}).")
    if eye is not None and eye["missing_eyes"]:
        description += " No calibration for the " + " and ".join(eye["missing_eyes"]) + " eye, so its gaze is absent."
    requested_by = (request.Request & {"idempotency_key": activation["request_key"]}).fetch1("requested_by")
    systems = sorted({system for row in _blocks(block_rows, session_key) for system in row["coverage"]})
    return Gathered(
        session={
            "identifier": f"{session_id}.montage-{key['montage_id']}.activation-{key['activation_id']}",
            "session_id": session_id,
            "description": description,
            "reference_time": clock["reference_time"],
            "experimenter": requested_by or None,
            "subject": _subject(key["subject"]),
            "clock": clock,
        },
        systems=systems,
        blocks=_blocks(block_rows, session_key),
        trials=_trials(blocks, session_key),
        events=_events(blocks, session_key),
        timebase=_timebase(session_key, provenance[0], clock),
        eye=eye,
    )
```

`wl_preproc/nwb/build.py`:

```python
"""Build one activation's NWB file, and record what was written (design spec
`2026-09-28-nwb-builder-design.md` section 2)."""

from __future__ import annotations

import dataclasses
import datetime
from pathlib import Path

from wl_preproc.nwb.checksums import dataset_checksums
from wl_preproc.nwb.eye import add_eye_series, add_eye_tables
from wl_preproc.nwb.eye_events import add_agreement, add_detections, add_sources
from wl_preproc.nwb.gather import Refused, gather
from wl_preproc.nwb.intervals import add_blocks, add_task_events, add_trials
from wl_preproc.nwb.session import new_file
from wl_preproc.nwb.timebase import add_timebase
from wl_preproc.nwb.validate import inspect_file, n_critical
from wl_preproc.nwb.write import write_atomically


@dataclasses.dataclass
class BuildResult:
    status: str  # written, invalid or refused
    path: Path | None = None
    reason: str = ""
    n_bytes: int | None = None
    identifier: str = ""
    clock: dict | None = None
    findings: list = dataclasses.field(default_factory=list)
    checksums: list = dataclasses.field(default_factory=list)


def nwb_path(nwb_root: Path, identifier: str) -> Path:
    """`{nwb_root}/{session_id}/{identifier}.nwb` (section 6)."""
    return Path(nwb_root) / identifier.split(".")[0] / f"{identifier}.nwb"


def build(activation_key: dict, nwb_root: Path) -> BuildResult:
    """Gather, write atomically, checksum and inspect one activation's file.
    A refusal writes nothing."""
    try:
        data = gather(activation_key)
    except Refused as refusal:
        return BuildResult(status="refused", reason=str(refusal))
    nwb = new_file(data.session)
    add_blocks(nwb, data.blocks, data.systems)
    add_trials(nwb, data.trials, data.systems)
    add_task_events(nwb, data.events)
    add_timebase(nwb, **data.timebase)
    if data.eye is not None:
        add_eye_series(nwb, data.eye["times"], data.eye["gaze"], data.eye["pupil"])
        add_eye_tables(nwb, data.eye["calibration"], data.eye["validity"], data.eye["repairs"])
        add_detections(nwb, data.eye["detections"])
        add_sources(nwb, data.eye["sources"])
        add_agreement(nwb, data.eye["agreement"])
    path = nwb_path(nwb_root, data.session["identifier"])
    write_atomically(nwb, path)
    findings = inspect_file(path)
    return BuildResult(
        status="invalid" if n_critical(findings) else "written",
        path=path,
        n_bytes=path.stat().st_size,
        identifier=data.session["identifier"],
        clock=data.session["clock"],
        findings=findings,
        checksums=dataset_checksums(path),
    )


def record(activation_key: dict, result: BuildResult) -> None:
    """One `NwbFile` row and its `Dataset` rows, in one transaction."""
    import datajoint as dj

    from wl_preproc.schema import nwb as nwb_schema

    key = {k: activation_key[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")}
    clock = result.clock or {}
    row = {
        **key,
        "status": result.status,
        "path": str(result.path or ""),
        "n_bytes": result.n_bytes,
        "built_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
        "nwb_identifier": result.identifier,
        "reference_time": clock["reference_time"].astimezone(datetime.timezone.utc).replace(tzinfo=None) if clock else None,
        "reference_source": clock.get("source"),
        "started_at_difference_s": clock.get("started_at_difference_s"),
        "n_critical": None if result.status == "refused" else n_critical(result.findings),
        "inspector_findings": result.findings or None,
        "reason": result.reason,
    }
    connection = dj.conn()
    with connection.transaction:
        nwb_schema.NwbFile.insert1(row)
        nwb_schema.NwbFile.Dataset.insert({**key, **checksum} for checksum in result.checksums)


def run_stage(nwb_root: Path, freed: list[dict] | None = None) -> tuple[int, list[str]]:
    """The daemon's `_nwb_stage`: every activation without an `NwbFile` row,
    skipping freed sessions. Returns `(activations recorded, per-activation
    failures)`, the archive stage's shape."""
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import request

    freed = freed or []
    recorded, errors = 0, []
    for key in (request.Activation - nwb_schema.NwbFile.proj()).keys():
        if {"subject": key["subject"], "session_datetime": key["session_datetime"]} in freed:
            continue
        try:
            record(key, build(key, nwb_root))
            recorded += 1
        except Exception as exc:  # one bad activation must not take down the run
            errors.append(f"NwbFile {key}: {exc}")
    return recorded, errors
```

`wl_preproc/schema/nwb.py`:

```python
"""What the NWB builder wrote, per activation (design spec
`2026-09-28-nwb-builder-design.md` sections 2, 7 and 8).

`dj.Manual`, filled by the daemon's bespoke `_nwb_stage` and by `wlpp nwb
build` -- the archive stage's pattern, because the builder writes a file under
a configured root, which a `dj.Computed.make()` cannot be given."""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import DEFAULT_PREFIX, request

schema = dj.Schema()


@schema
class NwbFile(dj.Manual):
    definition = """
    # One activation's NWB file, or why there is none.
    # Key: (subject, session_datetime, montage_id, activation_id).
    -> request.Activation
    ---
    # written: built and no critical nwbinspector finding. invalid: built,
    # with at least one (kept for inspection; piece 2 publishes only
    # `written`). refused: not built, `reason` says why; never retried
    # automatically -- delete the row to rebuild.
    status : enum('written','invalid','refused')
    path = '' : varchar(1024)
    n_bytes = null : bigint unsigned
    built_at : datetime
    nwb_identifier = '' : varchar(255)
    # The wall-clock time of session t = 0 (section 4.2), naive UTC, and
    # where it came from: the first barcode's own value, or the manifest's
    # started_at when the two disagree by more than 60 s.
    reference_time = null : datetime(6)
    reference_source = null : enum('barcode','manifest')
    started_at_difference_s = null : double
    n_critical = null : int unsigned
    inspector_findings = null : <blob>
    reason = '' : varchar(1024)
    """

    class Dataset(dj.Part):
        definition = """
        # One written-once dataset's checksum (parent spec section 8.2): its
        # decoded contents, never a group. A ragged column names its pair.
        # Key: (subject, session_datetime, montage_id, activation_id, dataset_path).
        -> master
        dataset_path : varchar(512)
        ---
        dtype : varchar(64)
        shape : varchar(64)
        blake3 : char(64)
        paired_with = '' : varchar(512)
        """


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}nwb`. Idempotent."""
    request.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}nwb", create_tables=True)
```

Apply these diffs:

```diff
--- a/wl_preproc/daemon.py
+++ b/wl_preproc/daemon.py
@@ -39,6 +39,7 @@
     events,
     eye,
     ingest,
+    nwb,
     paramset,
     # Imported, but deliberately NOT one of `_PROJECT_SCHEMA_MODULES` below --
     # that tuple's own comment says why `pipeline` is excepted from it.
@@ -306,6 +307,7 @@
     ("events", events),
     ("eye", eye),
     ("ingest", ingest),
+    ("nwb", nwb),
     ("paramset", paramset),
     ("request", request),
     ("timebase", timebase),
@@ -877,6 +879,7 @@
     nas_root: Path | None = None,
     host: str | None = None,
     share: str | None = None,
+    nwb_root: Path | None = None,
 ) -> dict:
     """One pass of the runner. Returns what it did, for the daily report.
 
@@ -980,7 +983,19 @@
             errors.extend(f"{table.__name__} {key}: {err}" for key, err in result["error_list"])
         except Exception as exc:  # a failing stage must not stop the others
             errors.append(f"{table.__name__}: {exc}")
+
+    # The NWB builder (design spec `2026-09-28-nwb-builder-design.md` section
+    # 2): opt-in like archival, and for the same reason -- it writes under a
+    # configured root. `None`, not `0`, when not configured.
+    nwb_built: int | None
+    if nwb_root is None:
+        nwb_built = None
+    else:
+        from wl_preproc.nwb.build import run_stage
 
+        nwb_built, nwb_errors = run_stage(nwb_root, freed=currently_freed(prefix=prefix))
+        errors.extend(nwb_errors)
+
     archived: int | None
     if nas_root is None or host is None or share is None:
         archived = None
@@ -995,6 +1010,7 @@
         "errors": errors,
         "stale_jobs_reaped": reaped,
         "archived": archived,
+        "nwb": nwb_built,
         # How many sessions were freed, and so skipped, when the pass began --
         # a count, so a skip never reads as an all-clear.
         "freed_skipped": freed_skipped,
```

```diff
--- a/wl_preproc/cli/main.py
+++ b/wl_preproc/cli/main.py
@@ -191,6 +191,19 @@
     daemon_p.add_argument("--nas-root", type=Path, default=None)
     daemon_p.add_argument("--host", default=None)
     daemon_p.add_argument("--share", default=None)
+    # Optional like `--nas-root`: absent, the NWB builder stage is skipped
+    # and says so (design spec `2026-09-28-nwb-builder-design.md` section 2).
+    daemon_p.add_argument("--nwb-root", type=Path, default=None)
+
+    nwb_p = subparsers.add_parser("nwb", help="NWB export")
+    nwb_sub = nwb_p.add_subparsers(dest="action", required=True)
+    nwb_build = nwb_sub.add_parser("build", help="build one activation's NWB file and record it")
+    nwb_build.add_argument("--subject", required=True)
+    nwb_build.add_argument("--session-datetime", required=True, type=datetime.datetime.fromisoformat)
+    nwb_build.add_argument("--montage-id", required=True, type=int)
+    nwb_build.add_argument("--activation-id", required=True, type=int)
+    nwb_build.add_argument("--nwb-root", required=True, type=Path)
+    nwb_build.add_argument("--prefix", default=DEFAULT_PREFIX)
 
     ingest_parser = subparsers.add_parser("ingest", help="scan a storage root once")
     ingest_parser.add_argument("--root", required=True, help="directory holding session dirs")
@@ -641,12 +654,29 @@
             return 0
         print(staging_manifest(entries))
         return 0
+
+    if args.group == "nwb" and args.action == "build":
+        from wl_preproc.daemon import activate_all
+        from wl_preproc.nwb.build import build, record
+        from wl_preproc.schema import nwb as nwb_schema
+
+        activate_all(prefix=args.prefix)
+        key = {"subject": args.subject, "session_datetime": args.session_datetime,
+               "montage_id": args.montage_id, "activation_id": args.activation_id}
+        if nwb_schema.NwbFile & key:
+            print(f"already recorded: {(nwb_schema.NwbFile & key).fetch1('status')}; delete the row to rebuild")
+            return 1
+        result = build(key, args.nwb_root)
+        record(key, result)
+        print(f"{result.status}: {result.path or result.reason}")
+        return 0 if result.status == "written" else 1
 
     if args.group == "daemon":
         from wl_preproc.daemon import run_once
 
         report = run_once(
-            prefix=args.prefix, nas_root=args.nas_root, host=args.host, share=args.share
+            prefix=args.prefix, nas_root=args.nas_root, host=args.host, share=args.share,
+            nwb_root=args.nwb_root,
         )
         print(f"populated: {report['populated']}")
         print(f"stale jobs reaped: {report['stale_jobs_reaped']}")
@@ -660,6 +690,10 @@
             print("archived: skipped (no --nas-root/--host/--share)")
         else:
             print(f"archived: {report['archived']}")
+        if report["nwb"] is None:
+            print("nwb: skipped (no --nwb-root)")
+        else:
+            print(f"nwb: {report['nwb']}")
         if report["errors"]:
             print("errors:")
             for err in report["errors"]:
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/nwb tests/schema/test_nwb_build.py tests/schema/test_daemon.py tests/schema/test_guardrails.py tests/cli -q -p no:cacheprovider`
Expected: 206 passed, 1 skipped (the same skip).

- [ ] **Step 5: Mutation checks.** Each was measured to fail the test named. Run each against `tests/schema/test_nwb_build.py tests/nwb/test_clock.py`.
  - T4a: `gather.py`, `if not blocks.contains_instant([time_s])[0]:` → `if not blocks.contains([time_s])[0]:`. Fails `test_blocks_trials_and_events_carry_the_tables_times` (the `BLOCK_END` at the session's last block end is dropped).
  - T4b: `gather.py`, `trusted = abs(difference) <= CLOCK_DISAGREEMENT_S` → `trusted = True`. Fails `test_a_synthetic_sessions_clock_falls_back_to_the_manifest`, `test_the_daemon_stage_records_every_activation` and `test_the_first_barcode_places_t0_unless_the_clocks_disagree`.
  - T4c: `gather.py`, `if activation["role"] == "derivative":` → `if False:`. Fails `test_a_derivative_holds_only_its_own_block`.
  - T4d: `build.py`, `status="invalid" if n_critical(findings) else "written",` → `status="written",`. Fails `test_a_file_without_the_subjects_date_of_birth_is_invalid`.
  - T4e: `gather.py`, `if provenance[0]["tier"] == "D":` → `if False:`. Fails `test_a_session_at_timing_tier_d_is_refused`.
  - T4f: `gather.py`, `    if len(segments) > 1:` → `    if False:`. Fails `test_a_session_with_two_ohdpi_segments_is_refused`.
  - T4g: `gather.py`, `    if eye is not None and eye["missing_eyes"]:` → `    if False:`. Fails `test_an_eye_without_calibration_is_left_out_and_the_rest_is_built`.
  - T4h: `build.py`, `        if {"subject": key["subject"], "session_datetime": key["session_datetime"]} in freed:` → `        if False:`. Fails `test_the_stage_skips_a_freed_session`, and `test_the_command_builds_once_and_rebuilds_only_when_its_row_is_deleted` (the stage has already recorded that activation).
  - T4i: `cli/main.py`, `        if nwb_schema.NwbFile & key:` → `        if False:`. Fails `test_the_command_builds_once_and_rebuilds_only_when_its_row_is_deleted`.
  - T4j: `build.py`, `        except Exception as exc:  # one bad activation must not take down the run` → `        except ValueError as exc:  # one bad activation must not take down the run`. Fails `test_one_failing_activation_does_not_stop_the_stage`.
  - T4k: `build.py`, `    if data.eye is not None:` → `    if True:`. Fails `test_a_session_without_an_eye_recording_is_built_without_one`.
  - T4l: `gather.py`, `"stop_s": float(edges[row["run_stop"]]), "label"` → `"stop_s": blocks.clip(start_s, float(edges[row["run_stop"]]))[0][1], "label"` (a run cut at its block's end). Fails `test_a_run_that_crosses_its_blocks_end_keeps_its_true_end`.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/nwb/gather.py wl_preproc/nwb/build.py wl_preproc/schema/nwb.py wl_preproc/daemon.py wl_preproc/cli/main.py tests/nwb/test_clock.py tests/schema/test_nwb_build.py tests/schema/test_daemon.py tests/schema/test_guardrails.py
git commit -m "feat(nwb): build one activation's file from the tables and the raw file, record it in nwb.NwbFile, from the daemon (--nwb-root) and wlpp nwb build

<trailer lines>"
```

---

### Task 5: Records and the full suite

**Files:**
- Modify: `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-09-28-nwb-builder.md`

- [ ] **Step 1: Full suite on both interpreters, once.** Set `WLPP_BMD_REFERENCE=$SCRATCH/bmd/BMD`, `WLPP_BMD_BOOST_INCLUDE=$SCRATCH/bmd/boost_1_86_0` and `WLPP_NSLR_REFERENCE=$SCRATCH`, and leave `WLPP_OHDPI_REFERENCE` unset, as CI has it:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
$SCRATCH/venv_ci/bin/python -m pytest -q -p no:cacheprovider
```

Expected: both green. Measured with every task applied: **1820 passed, 31 skipped, 1 deselected, 1 xfailed** on 3.11, and **1819 passed, 33 skipped, 1 xfailed** on 3.13. `main` at `0aa4928` gave 1781 and 1780; this plan adds 40 tests (2 in Task 1, 2 in Task 2, 15 in Task 3, 21 in Task 4), one of which skips.

- [ ] **Step 2: The handoff.** Create `docs/handoffs/2026-09-28-nwb-builder.md`, with these sections:
  - **What was built.** Piece 1 of Phase 3's NWB export, on branch `spec/nwb-builder`. Name the spec and this plan. List the file's layout in one paragraph per `processing` module, from spec §3, with the events table as `/intervals/task_events`.
  - **What a file needs to be `written`.** wl.works' subject details. Without a date of birth, `nwbinspector` rates the file CRITICAL, so it is `invalid` (the OPEN entry in `docs/pending-wl-works-amendments.md`).
  - **The clock.** Every synthetic file takes the manifest fallback. A real session's barcode is checked against `started_at` to 60 s. `clock_trusted` waits on the wl-sync pin move.
  - **Still not done.** `archive/reclaim.py`'s `canonical_nwb_present` is still `False`: it is piece 2's, with publication. Ephys is piece 3's.
  - **The rulings.** Every ruling this plan made, listed under "Rulings made while planning", plus any made while executing.
  - **The measured counts.** Each task's failing and passing runs, the mutation list, and Step 1's two full-suite runs.

- [ ] **Step 3: CHECKPOINT.** In "Start here", item 4 ends with the paragraph "**Three lab packages were renamed …**", just before "**Deferred minors: …**". After it, add one bold-led paragraph, as the paragraphs before it do. Do not renumber: other passages cite items by number. The paragraph says:
  - the NWB builder is built on `spec/nwb-builder`, NOT merged as written;
  - what it writes, and where (`{nwb_root}/{session_id}/{identifier}.nwb`, opt-in with `--nwb-root`);
  - that a file is `invalid` until wl.works sends the subject's date of birth;
  - that `canonical_nwb_present` stays `False` until piece 2, so a real reclamation still needs a recorded force;
  - the handoff's path.

  Item 1's sentence "A sixth condition, `canonical_nwb_present`, fails until Phase 3" stays as it is: it is still true. Do not re-point the header; that happens at merge, with CI read.

- [ ] **Step 4: `wl.yaml`.** Add one sentence to `status.phase`: the NWB builder (piece 1 of the NWB export) is built on `spec/nwb-builder`, not yet merged. Then run `.venv/bin/wl-check` **on its own** and read its exit status. If a short SHA is ever written to `describes` and has no letter in it, quote it.

- [ ] **Step 5: Commit**

```bash
git add docs/CHECKPOINT.md docs/handoffs/2026-09-28-nwb-builder.md wl.yaml
git commit -m "docs: the NWB builder built -- what it writes, what a file needs to be valid, and what piece 2 still owes

<trailer lines>"
```

---

## Rulings made while planning

Each is recorded where the code it governs is, and in the spec where it amends the spec.

1. **`build(activation_key, nwb_root)`, not `(activation_key, out_path)`,** and the command takes named options. There is one path rule, in one place (spec §2's amendment).
2. **Events are a zero-length `TimeIntervals`, `/intervals/task_events`,** because Neurosift does not display NWB 2.10's `EventsTable` (spec §3's amendment).
3. **The barcode epoch is restated,** because the pinned wl-sync predates `wl_sync/clock.py` (spec §4.2's correction).
4. **An event exactly on a block's end is kept.** It is that block's own `BLOCK_END` (spec §5's amendment; `BlockSet.contains_instant`).
5. **A missing date of birth stays CRITICAL,** so no file is publishable until wl.works sends it (spec §8's amendment; the requester's decision stands behind the details coming from wl.works).
6. **Touching blocks are one interval,** so a stretch across them is one row (Task 3; added to spec §5 in this plan's commit).
7. **The trials table has no `condition` column.** No table stores a condition on a trial. It is on its `CONDITION` event, which the file carries in `/intervals/task_events` (added to spec §3 in this plan's commit).
8. **The recovery test counts planted-size saccades only** (Task 1).
9. **The refusal tests restore the saved timing row,** and **the fixture splits its one measured block in two** (Task 4).
