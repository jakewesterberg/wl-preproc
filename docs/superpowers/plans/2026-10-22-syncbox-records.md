# Reading the Sync Box's Logs as It Writes Them Today (Part 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Read the box's logs as it writes them today: the pin moved, its barcodes from its `B` records, each
segment its own clock, a restart never mixed into session time and recorded, so a real session gets a session
time.

**Architecture:** The pin moves; the log's one reader takes `B` records and each segment's header; session time
comes from the first segment holding a barcode; a recording sharing no barcode with it is rejected with a
reason; `timebase.SyncBoxSegment` records every segment, and the daily report names a restart. Synthetic logs
are written as the box writes them.

**Tech Stack:** Python 3.11/3.13, wl-sync, DataJoint 2.3.3 on MySQL (testcontainers in tests), pydantic.

**Spec:** `docs/superpowers/specs/2026-10-22-syncbox-records-and-segments-design.md`: §0-§6, §8, §9; its
amendments 1-6 are Task 6's, made while proving this plan. Part 2, the bridges, is a separate plan.

**Proven before written.** Every step below was run on a scratch branch and replayed from this plan's own
text to an identical tree: 19 mutation checks caught, and the full suite on both interpreters as
Task 6 states.

## Global Constraints

- **The barcode delay is corrected once measured** (decision 1): part 1 uses each `B` tick as it is and records
  the delay as null, unmeasured and uncorrected.
- **Every sync box segment is its own clock** (§4): no tick is compared across two segments; a restart is never
  mixed into session time. Part 2 bridges it, through a device, then the task PC's clock (decision 2).
- **The pin is wl-sync's `main` at `27c0e9780a388cbc120ecfadcb54b6f288740a98`** (amendment 4).
- **No schema change to an existing table:** `timebase.SyncBoxSegment` is new; `RejectedSegment.reason` is free
  text; nothing else's key changes.
- **Synthetic logs are the box's** (§3.4): the fixture is corrected, never the design bent to it.
- **Execute on a branch `feat/syncbox-records`** cut from `plan/syncbox-records` once this plan is committed
  there.
- **Python 3.11 and 3.13**: the full suite runs once on each, at the end (Task 6).
- **A test that lands a session deletes it**, so no later module's unrestricted populate meets it.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code,
  data or tables; leave the stray `wl-preproc` symlink at the repository root out of every commit.
- **Commands run from the repository root.** In a git worktree, `.venv/bin/python` is the main checkout's;
  every test command sets `PYTHONPATH=$PWD` and `PYTHONDONTWRITEBYTECODE=1`.
- **Patches apply with `git apply` and must apply cleanly**; `docs/schemas` is regenerated with
  `wlpp schemas export`, never edited.
- **Neighbour runs name their files in the suite's order.**
- **Every commit ends with:**
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
  ```

## Review Focus

The five inputs the spec implies that a person is most likely to meet, most likely first; each is pinned by a
test in the task that owns the code:
1. **A box that restarted mid-session:** the stage completes, every device is timed, nothing of the later
   segment is mixed in. `test_the_stage_completes_and_every_device_is_timed` (Task 4),
   `test_the_boxs_codes_after_a_restart_are_left_out_not_mixed_in` (Task 3).
2. **A box whose clock is untrusted after a power-up** (wl-sync's known false flag): its barcodes read as any
   other's, the flag recorded and acted on nowhere.
   `test_the_barcodes_are_the_boxs_own_records_and_nothing_on_its_barcode_pin` (Task 2), whose log says
   `clock_trusted: false`.
3. **A box that crashed within its first second:** a first segment with no barcode, session time from the next.
   `test_a_first_segment_with_no_barcode_leaves_session_time_to_the_next` (Task 3).
4. **A box with its barcode line looped back to an input** (the bench jumper): edges there are never read as
   barcodes. `test_the_barcodes_are_the_boxs_own_records_and_nothing_on_its_barcode_pin` (Task 2).
5. **A folder with a thousand segments or more:** ordered by `segment_index`, not by name.
   `test_segments_are_ordered_by_their_index_not_their_names` (Task 3).

## The mutation checks


Each task names mutations as `[name, file, old, new, test]`. Save this helper once, outside the repository,
as `mutate.py`, and run it from the repository root with a task's list saved as JSON:
`.venv/bin/python /path/to/mutate.py /path/to/mutations.json`. Each mutation is applied alone, caches
cleared, its test run, and the file restored; every line must print `CAUGHT`, and `git status` must be
clean afterwards.

```python
"""Run the mutations in a JSON list [[name, file, old, new, test], ...] from the
current directory: each alone, caches cleared, restored after."""
import json, os, pathlib, shutil, subprocess, sys
root = pathlib.Path.cwd()
def clear():
    for cache in root.rglob("__pycache__"):
        if ".venv" not in cache.parts:
            shutil.rmtree(cache, ignore_errors=True)
for name, rel, old, new, test in json.loads(pathlib.Path(sys.argv[1]).read_text()):
    path = root / rel
    original = path.read_text()
    assert original.count(old) == 1, (name, "old text not found once")
    clear()
    path.write_text(original.replace(old, new))
    try:
        result = subprocess.run([sys.executable, "-m", "pytest", *test.split(), "-q", "-p", "no:cacheprovider",
                                 "-o", "faulthandler_timeout=1800"],
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root)},
                                capture_output=True, text=True)
    finally:
        path.write_text(original)
        clear()
    tail = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr[-200:]
    print(f"{name}: {'CAUGHT' if result.returncode else 'SURVIVED'} -- {tail}", flush=True)
```

## Files

| File | Responsibility | Task |
|---|---|---|
| `pyproject.toml`, `wl.yaml`, `docs/schemas/syncbox_log_header.json` | the pin | 1, 6 |
| `wl_preproc/timebase/_syncbox_log.py`, `timebase/extract.py` | the log's one reader: `B` records, a segment's header | 2, 3 |
| `wl_preproc/synth/syncbox.py`, `synth/session.py`, `synth/recipe.py`, `synth/timeline.py` | synthetic logs as the box writes them; the restart | 2, 3 |
| `wl_preproc/timebase/segments.py`, `timebase/clock.py` | each segment its own clock; the rejection; the roles | 3, 4, 5 |
| `wl_preproc/schema/core.py` | `Segment.make`'s rejection | 4 |
| `wl_preproc/schema/timebase.py`, `daemon.py`, `cli/report.py` | `SyncBoxSegment`, its stage, the report | 5 |
| the spec, `HANDOVER-wl-sync.md`, `docs/handoffs/2026-10-22-syncbox-records.md` (new), `docs/CHECKPOINT.md`, `wl.yaml` | the records | 6 |

---

### Task 1: The wl-sync pin moves

§1 and §3.1: the wl-sync pinned until now (`90b931e`, 2026-08-13) refuses the box's header since 2026-08-17 and its `B` and `T` records, so no real log can be read. The pin moves to wl-sync's `main` at `27c0e97` (amendment 4), in `pyproject.toml` and in `wl.yaml`'s `third_party`, whose `why` says so; both environments install it; the exported header schema is regenerated.

**Files:**
- Modify: `pyproject.toml` (the wl-sync commit and its comment)
- Modify: `wl.yaml` (`third_party`'s wl-sync: `pinned_at`, `why`)
- Modify: `docs/schemas/syncbox_log_header.json` (exported)
- Test: `tests/cli/test_schemas_export.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: wl-sync at `27c0e97` installed in `.venv` and `~/.cache/wl-preproc-venv313`: `wl_sync.log.BarcodeEmitted`
  (`tick_us`, `value`); `SyncBoxLogHeader.segment_index`, `.clock_trusted`, `.clock_reason`;
  `wl_sync.pins.GPIO_MAP`, `BARCODE_PIN`; `wl_sync.segment.segment_name(index) -> str`;
  `wl_sync.clock.BARCODE_EPOCH`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/cli/test_schemas_export.py b/tests/cli/test_schemas_export.py
index ccd7fde..f42306b 100644
--- a/tests/cli/test_schemas_export.py
+++ b/tests/cli/test_schemas_export.py
@@ -120,3 +120,17 @@ def test_job_request_schema_carries_the_aim_and_the_assignment(tmp_path):
     assignment = schema["$defs"]["InsertionAreaAssignment"]
     assert set(assignment["properties"]["source"]["enum"]) == {
         "histology", "functional_mapping", "waveform_depth", "structural_imaging", "at_rig", "other"}
+
+
+def test_the_committed_syncbox_header_is_the_one_wl_sync_writes_today(tmp_path):
+    """The box's logs since 2026-08-17 carry four header fields the wl-sync
+    pinned until 2026-10-22 did not know, so it could not read them
+    (`HANDOVER-wl-sync.md`, 2026-10-19). The committed export is the installed
+    wl-sync's, and that wl-sync knows them."""
+    from pathlib import Path
+
+    committed = Path(__file__).resolve().parents[2] / "docs" / "schemas" / "syncbox_log_header.json"
+    export_schemas(tmp_path)
+    exported = json.loads((tmp_path / "syncbox_log_header.json").read_text())
+    assert json.loads(committed.read_text()) == exported
+    assert {"segment_index", "ordering", "clock_trusted", "clock_reason"} <= set(exported["properties"])
PATCH
```

- [ ] **Step 2: Run them and see them fail.** Run before Step 4's install, so with the wl-sync pinned until now: it exports the old header, which lacks today's four fields.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli/test_schemas_export.py -q -p no:cacheprovider
```

Expected: `1 failed, 13 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/pyproject.toml b/pyproject.toml
index 3548fd7..99e6729 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -25,8 +25,11 @@ dependencies = [
     # current" step re-runs that export and `git diff --exit-code`s the
     # directory. On a moving ref, a commit to wl-sync main changes this repo's
     # published contract -- or reds CI on an unrelated PR -- with no change in
-    # this repo at all. Resolved 2026-08-14 with `git ls-remote <url> main`.
-    "wl-sync @ git+https://github.com/jakewesterberg/wl-sync.git@90b931e8d4714a0b199510d5ccb8813114bb076e",  # main
+    # this repo at all. Resolved 2026-08-14 with `git ls-remote <url> main`;
+    # moved 2026-10-22 to wl-sync's main of that day, because the box's logs
+    # since 2026-08-17 carry header fields and `B`/`T` records the old pin
+    # refused (`HANDOVER-wl-sync.md`, 2026-10-19).
+    "wl-sync @ git+https://github.com/jakewesterberg/wl-sync.git@27c0e9780a388cbc120ecfadcb54b6f288740a98",  # main
     "datajoint>=2.3,<3",
     # `wl_preproc/eye/ohdpi.py` imports pandas directly, for `read_ohdpi`'s
     # column-selective CSV reads. It already arrives unconditionally with
diff --git a/wl.yaml b/wl.yaml
index 9e337a5..fa81dfe 100644
--- a/wl.yaml
+++ b/wl.yaml
@@ -538,7 +538,7 @@ third_party:
 # something here.
 requires:
   - name: wl-sync
-    pinned_at: 90b931e8d4714a0b199510d5ccb8813114bb076e
+    pinned_at: 27c0e9780a388cbc120ecfadcb54b6f288740a98
     for: session identity, the barcode codec, and the log format
     why: >-
       The sync box owns everything it produces, and vendoring copies here would
@@ -549,7 +549,11 @@ requires:
       directory. On a moving ref, a commit to wl-sync main would change this
       repository's exported contract with nobody here touching it. The
       dependency runs one way only — wl-sync must never import from wl-preproc,
-      and CI asserts it.
+      and CI asserts it. Moved on 2026-10-22 from 90b931e (2026-08-13) to
+      wl-sync's main of that day: the box's logs since 2026-08-17 carry header
+      fields (segment_index, ordering, clock_trusted, clock_reason) and B and T
+      records the old pin refused, so no real log could be read
+      (HANDOVER-wl-sync.md, 2026-10-19).
 
 publishes:
   - name: session-manifest
PATCH
```

- [ ] **Step 4: Install the pinned wl-sync** in both environments.

```bash
uv pip install --python .venv/bin/python "wl-sync @ git+https://github.com/jakewesterberg/wl-sync.git@27c0e9780a388cbc120ecfadcb54b6f288740a98"
uv pip install --python $HOME/.cache/wl-preproc-venv313/bin/python "wl-sync @ git+https://github.com/jakewesterberg/wl-sync.git@27c0e9780a388cbc120ecfadcb54b6f288740a98"
.venv/bin/python -c "import wl_sync.log as log; print(log.BarcodeEmitted.__name__)"
```

Expected: `BarcodeEmitted`.

- [ ] **Step 5: Export the schemas.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas
git status --short docs/schemas
```

Expected: ` M docs/schemas/syncbox_log_header.json`, and no other file.

- [ ] **Step 6: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli/test_schemas_export.py -q -p no:cacheprovider
```

Expected: `14 passed`

- [ ] **Step 7: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli/test_schemas_export.py tests/contracts tests/events tests/ingest tests/synth tests/timebase -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `638 passed, 1 skipped`

- [ ] **Step 8: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "the committed header is not today's",
  "docs/schemas/syncbox_log_header.json",
  "\"segment_index\": {",
  "\"segment_indexx\": {",
  "tests/cli/test_schemas_export.py::test_the_committed_syncbox_header_is_the_one_wl_sync_writes_today"
 ]
]
```

Expected: 1 line, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 9: Commit.**

```bash
git add -A tests wl_preproc docs pyproject.toml wl.yaml
git commit -F - <<'MSG'
build: the wl-sync pin moves to its main of 2026-10-22 (27c0e97) -- the box's logs since 2026-08-17 carry header fields and B and T records the old pin refused, so no real log could be read; the exported header schema is regenerated (design spec 2026-10-22-syncbox-records-and-segments-design.md, section 3)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 2: The barcodes are the box's `B` records

§3.2 and §3.4: `read_barcode_entries` returns each `B` record's value and tick and reads no edge; `extract_syncbox` already renders that list as every device's edge form, so nothing downstream changes. Synthetic logs are written as the box writes them: `B` records, the board's pin map, named `seg-000.log` (amendment 2). Five tests that named or stripped the old shape follow it (amendment 6).

**Files:**
- Modify: `wl_preproc/timebase/_syncbox_log.py` (`read_barcode_entries`)
- Modify: `wl_preproc/synth/syncbox.py` (`write_syncbox_log`), `wl_preproc/synth/session.py`, `wl_preproc/timebase/extract.py` (two comments)
- Create: `tests/timebase/test_syncbox_log.py`
- Modify: `tests/synth/test_syncbox.py`, `tests/timebase/test_clock.py`, `tests/timebase/test_extract.py`, `tests/timebase/test_fit.py`, `tests/schema/test_eye_populate.py`

**Interfaces:**
- Consumes: Task 1's wl-sync.
- Produces: `read_barcode_entries(path) -> list[tuple[int, int]]`, `(value, tick_us)` from `B` records;
  `write_syncbox_log(path, recipe, truth, drift_ppm=0.0)` writes `B` and `W` records, `gpio_map=GPIO_MAP`,
  `segment_index=0`; a synthetic session's sync box log is `syncbox/seg-000.log`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/schema/test_eye_populate.py b/tests/schema/test_eye_populate.py
index 30a9ed5..1fdef13 100644
--- a/tests/schema/test_eye_populate.py
+++ b/tests/schema/test_eye_populate.py
@@ -115,7 +115,7 @@ def _write_fixations(
 
     merged = tuple(sorted((*truth.code_words, *extra), key=lambda pair: pair[0]))
     new_truth = dataclasses.replace(truth, code_words=merged)
-    write_syncbox_log(session_dir / "syncbox" / "syncbox.log", recipe, new_truth, drift_ppm=0.0)
+    write_syncbox_log(session_dir / "syncbox" / "seg-000.log", recipe, new_truth, drift_ppm=0.0)
 
 
 def _inject_fixations(session_dir, recipe, truth, targets_deg: list[tuple[float, float]]) -> None:
diff --git a/tests/synth/test_syncbox.py b/tests/synth/test_syncbox.py
index 66bcc53..f8ce0ee 100644
--- a/tests/synth/test_syncbox.py
+++ b/tests/synth/test_syncbox.py
@@ -1,25 +1,21 @@
 import pytest
-from wl_sync.barcode import decode_edges
-from wl_sync.log import CodeWord, Edge, read_log
+from wl_sync.log import BarcodeEmitted, CodeWord, Edge, read_log
+from wl_sync.pins import BARCODE_PIN, GPIO_MAP
 
 from wl_preproc.synth.recipe import CI_RECIPE
-from wl_preproc.synth.syncbox import BARCODE_GPIO, SYNCBOX_PRE_ROLL_S, write_syncbox_log
+from wl_preproc.synth.syncbox import SYNCBOX_PRE_ROLL_S, write_syncbox_log
 from wl_preproc.synth.timeline import build_timeline
 
 
-def emit(tmp_path, drift_ppm=0.0, name="syncbox.log"):
+def emit(tmp_path, drift_ppm=0.0, name="seg-000.log"):
     truth = build_timeline(CI_RECIPE)
     path = tmp_path / name
     write_syncbox_log(path, CI_RECIPE, truth, drift_ppm=drift_ppm)
     return truth, read_log(path)
 
 
-def barcode_edges(records):
-    return [
-        (r.tick_us, r.level)
-        for r in records
-        if isinstance(r, Edge) and r.gpio == BARCODE_GPIO
-    ]
+def emitted(records):
+    return [r for r in records if isinstance(r, BarcodeEmitted)]
 
 
 def test_header_validates_and_names_the_session(tmp_path):
@@ -29,30 +25,29 @@ def test_header_validates_and_names_the_session(tmp_path):
     assert header.written_at.tzinfo is not None
 
 
-def test_barcodes_round_trip_through_the_real_decoder(tmp_path):
-    """Written with wl-sync's encoder, read with wl-sync's decoder. If the
-    generator and the pipeline ever disagree about the format, this fails."""
-    truth, (_, records) = emit(tmp_path)
-    decoded = decode_edges(barcode_edges(records), start_us=0)
-    assert [b.value for b in decoded] == [v for v, _ in truth.barcodes]
+def test_header_is_the_boards_and_the_first_segment(tmp_path):
+    """The box's own pin map, not names of this project's: the synthetic log
+    is the log the box writes (design spec
+    `2026-10-22-syncbox-records-and-segments-design.md` section 3)."""
+    _, (header, _) = emit(tmp_path)
+    assert header.gpio_map == GPIO_MAP
+    assert header.segment_index == 0
 
 
-def test_first_barcode_is_decodable(tmp_path):
-    """Without a pre-roll the first frame sits at tick 0 with no preceding idle,
-    and wl-sync's decoder correctly refuses it."""
+def test_every_barcode_is_a_record_of_the_box_and_none_is_drawn_on_its_pin(tmp_path):
+    """The box logs each barcode it emits and never captures its own barcode
+    line (wl-sync's `log.py`; its GPIO17 is an output)."""
     truth, (_, records) = emit(tmp_path)
-    assert decode_edges(barcode_edges(records), start_us=0)[0].value == truth.barcodes[0][0]
+    assert [r.value for r in emitted(records)] == [v for v, _ in truth.barcodes]
+    assert not [r for r in records if isinstance(r, Edge) and r.gpio == BARCODE_PIN]
 
 
 def test_barcode_times_match_ground_truth_after_removing_the_pre_roll(tmp_path):
     """The log's tick origin is deliberately not session time. A fixture where
     they coincide lets a pipeline bug that ignores the offset pass."""
     truth, (_, records) = emit(tmp_path)
-    decoded = decode_edges(barcode_edges(records), start_us=0)
-    for barcode, (_, expected_s) in zip(decoded, truth.barcodes):
-        assert barcode.start_us / 1e6 - SYNCBOX_PRE_ROLL_S == pytest.approx(
-            expected_s, abs=1e-3
-        )
+    for record, (_, expected_s) in zip(emitted(records), truth.barcodes, strict=True):
+        assert record.tick_us / 1e6 - SYNCBOX_PRE_ROLL_S == pytest.approx(expected_s, abs=1e-6)
 
 
 def test_every_code_word_is_logged(tmp_path):
@@ -80,3 +75,10 @@ def test_emission_is_deterministic(tmp_path):
     write_syncbox_log(first, CI_RECIPE, truth)
     write_syncbox_log(second, CI_RECIPE, truth)
     assert first.read_text() == second.read_text()
+
+
+def test_a_session_names_its_log_as_the_box_names_its_first_segment(tmp_path):
+    from wl_preproc.synth.session import generate_session
+
+    generate_session(tmp_path, CI_RECIPE)
+    assert sorted(p.name for p in (tmp_path / CI_RECIPE.session_id / "syncbox").glob("*.log")) == ["seg-000.log"]
diff --git a/tests/timebase/test_clock.py b/tests/timebase/test_clock.py
index fc5b165..40018be 100644
--- a/tests/timebase/test_clock.py
+++ b/tests/timebase/test_clock.py
@@ -90,17 +90,18 @@ def test_only_the_sync_box_clock_is_built_yet(tmp_path):
 
 
 def test_a_sync_box_log_with_no_barcode_is_the_clock_and_cannot_time_the_session(tmp_path):
-    """A log whose barcode line is dead is still the sync box's: the log is
+    """A log holding no barcode record is still the sync box's: the log is
     there, so the sync box is the clock, and asking it the time raises, as the
     events stage raised before. Nothing chooses another clock (the plan's
-    Review Focus 1)."""
+    Review Focus 1). The barcodes are its `B` records (design spec
+    `2026-10-22-syncbox-records-and-segments-design.md` section 3)."""
     from wl_preproc.timebase.clock import Clock, choose
     from wl_preproc.timebase.extract import find_recordings
 
     session_dir = _session(tmp_path)
     (log,) = find_recordings("syncbox", session_dir / "syncbox")
     header, *records = log.read_text().splitlines()
-    log.write_text("\n".join([header, *(record for record in records if not record.startswith("E,"))]) + "\n")
+    log.write_text("\n".join([header, *(record for record in records if not record.startswith("B,"))]) + "\n")
     assert choose(session_dir).source == "syncbox"
     with pytest.raises(ValueError, match="the sync box log decoded no barcodes"):
         Clock(source="syncbox", session_dir=session_dir).codes()
diff --git a/tests/timebase/test_extract.py b/tests/timebase/test_extract.py
index b9e1d3b..fd47b18 100644
--- a/tests/timebase/test_extract.py
+++ b/tests/timebase/test_extract.py
@@ -61,7 +61,7 @@ def test_syncbox_extraction_recovers_every_ground_truth_barcode(tmp_path: Path):
     """
     truth = generate_session(tmp_path, RECIPES["ci"])
     session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
-    stream = extract_syncbox(session_dir / "syncbox" / "syncbox.log")
+    stream = extract_syncbox(session_dir / "syncbox" / "seg-000.log")
 
     assert stream.n_samples > 0
     assert len(stream.edges) > 0
@@ -379,7 +379,7 @@ def test_decode_reliability_is_measured_on_every_system(tmp_path: Path, capsys):
 
     measured: dict[str, tuple[int, int]] = {}
     for profile, cases in (
-        ("ci", (("syncbox", "syncbox/syncbox.log"), ("bcam", "bcam/frames.yaml"))),
+        ("ci", (("syncbox", "syncbox/seg-000.log"), ("bcam", "bcam/frames.yaml"))),
         ("stim", (("rhs", "rhs"),)),
         ("eye", (("ohdpi", f"ohdpi/{FILENAME}"), ("spikeglx", None))),
     ):
diff --git a/tests/timebase/test_fit.py b/tests/timebase/test_fit.py
index 36b0a15..3581ae6 100644
--- a/tests/timebase/test_fit.py
+++ b/tests/timebase/test_fit.py
@@ -210,7 +210,7 @@ def test_residuals_report_a_barcode_that_does_not_fit():
 # Where each system's one recording sits, relative to the session directory.
 # `None` means "glob for it", because SpikeGLX names its file after the run.
 _RECORDING_PATHS: dict[str, str | None] = {
-    "syncbox": "syncbox/syncbox.log",
+    "syncbox": "syncbox/seg-000.log",
     "spikeglx": None,
     "rhs": "rhs",
     "ohdpi": "ohdpi/OpenIris-synthetic.txt",
diff --git a/tests/timebase/test_syncbox_log.py b/tests/timebase/test_syncbox_log.py
new file mode 100644
index 0000000..2a7178d
--- /dev/null
+++ b/tests/timebase/test_syncbox_log.py
@@ -0,0 +1,63 @@
+"""The sync box's barcodes are read from its `B` records, as the box writes
+them today (design spec `2026-10-22-syncbox-records-and-segments-design.md`
+section 3): the box never captures its own barcode line, so nothing is read
+from it, whatever a log's edges hold."""
+
+from __future__ import annotations
+
+import datetime
+
+from wl_sync.barcode import encode
+from wl_sync.log import SCHEMA_VERSION, BarcodeEmitted, CodeWord, Edge, SyncBoxLogHeader, write_log
+from wl_sync.pins import BARCODE_PIN, GPIO_MAP
+
+from wl_preproc.timebase._syncbox_log import read_barcode_entries
+from wl_preproc.timebase.extract import extract_syncbox
+
+
+def _real_shaped_log(path):
+    """Written by wl-sync's own writer: the board's pin map, a photodiode's
+    edges, code words, three barcodes the box emitted, and a trailer. Edges on
+    the barcode pin carry a different value, which a reader of that pin would
+    report and a reader of the box's records never does."""
+    header = SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id="2027-03-14_01", rig="rig0",
+                              boot_id="b0", written_at=datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC),
+                              gpio_map=GPIO_MAP, segment_index=0, clock_trusted=False,
+                              clock_reason="ntp_unsynchronized")
+    records = [Edge(tick_us=5_000_000, gpio=GPIO_MAP["pd1_comp"], level=1),
+               CodeWord(tick_us=5_100_000, word=0x8001)]
+    tick = 9_000_000
+    for level, duration_us in encode(4242):
+        records.append(Edge(tick_us=tick, gpio=BARCODE_PIN, level=level))
+        tick += duration_us
+    records += [BarcodeEmitted(tick_us=t, value=v)
+                for t, v in ((2_000_000, 912_345_678), (3_000_000, 912_345_679), (4_000_000, 912_345_680))]
+    write_log(path, header, records)
+    with path.open("a", encoding="utf-8") as handle:
+        handle.write("T,4500000,clean\n")
+    return path
+
+
+def test_the_barcodes_are_the_boxs_own_records_and_nothing_on_its_barcode_pin(tmp_path):
+    path = _real_shaped_log(tmp_path / "seg-000.log")
+    assert read_barcode_entries(path) == [(912_345_678, 2_000_000), (912_345_679, 3_000_000),
+                                          (912_345_680, 4_000_000)]
+
+
+def test_the_sync_box_stream_decodes_to_the_boxs_records(tmp_path):
+    """`extract_syncbox` renders them into the edge form every device has,
+    so every stage downstream reads them as it reads any device."""
+    from wl_sync.barcode import decode_edges
+
+    stream = extract_syncbox(_real_shaped_log(tmp_path / "seg-000.log"))
+    assert [(b.value, b.start_us) for b in decode_edges(list(stream.edges))] == [
+        (912_345_678, 2_000_000), (912_345_679, 3_000_000), (912_345_680, 4_000_000)]
+
+
+def test_a_log_with_no_barcode_record_has_no_barcodes(tmp_path):
+    path = tmp_path / "seg-000.log"
+    header = SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id="2027-03-14_01", rig="rig0",
+                              boot_id="b0", written_at=datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC),
+                              gpio_map=GPIO_MAP)
+    write_log(path, header, [CodeWord(tick_us=1_000_000, word=0x8001)])
+    assert read_barcode_entries(path) == []
PATCH
```

- [ ] **Step 2: Run them and see them fail.** `test_a_log_with_no_barcode_record_has_no_barcodes` passes already: the old reader found no barcode either, on a log with no edges.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/synth/test_syncbox.py tests/timebase/test_clock.py tests/timebase/test_extract.py tests/timebase/test_fit.py tests/timebase/test_syncbox_log.py -q -p no:cacheprovider
```

Expected: `12 failed, 46 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/synth/session.py b/wl_preproc/synth/session.py
index 168db1a..922c63b 100644
--- a/wl_preproc/synth/session.py
+++ b/wl_preproc/synth/session.py
@@ -6,6 +6,7 @@ import datetime
 from pathlib import Path
 
 import numpy as np
+from wl_sync.segment import segment_name
 from wl_sync.session import SessionId
 
 from wl_preproc.contracts.done import DONE_SCHEMA_VERSION, DoneMarker, FileEntry, blake3_file
@@ -82,7 +83,9 @@ def generate_session(root: Path, recipe: SessionRecipe) -> GroundTruth:
             # drifting it alongside every device, which this did until Phase
             # 1c-4, cancels the drift exactly and leaves every fixture with no
             # relative drift for the rate fit to find.
-            write_syncbox_log(directory / "syncbox.log", recipe, truth, drift_ppm=0.0)
+            # Named as the box names a session's first segment (wl-sync's
+            # `segment.py`), so the synthetic folder is the box's.
+            write_syncbox_log(directory / segment_name(0), recipe, truth, drift_ppm=0.0)
             write_task_file(directory / "task.json", truth)
         elif system == "spikeglx":
             bin_path = write_spikeglx(
diff --git a/wl_preproc/synth/syncbox.py b/wl_preproc/synth/syncbox.py
index 4af4244..d5bbf4c 100644
--- a/wl_preproc/synth/syncbox.py
+++ b/wl_preproc/synth/syncbox.py
@@ -1,25 +1,26 @@
-"""Emit a sync box log for a planted timeline.
-
-Barcodes go through wl-sync's own encoder rather than a local reimplementation,
-so a format change there breaks these fixtures loudly instead of letting the
-generator and the pipeline drift into disagreeing.
+"""Emit a sync box log for a planted timeline, as the box writes one.
+
+The box logs each barcode it emits as a `BarcodeEmitted` ('B') record and
+never captures its own barcode line, and its header's `gpio_map` is the
+board's (`wl_sync.pins.GPIO_MAP`). Until 2026-10-22 this drew each barcode as
+edges on GPIO17 and named three pins of this project's own: a log the box does
+not write, read by a reader no real log could satisfy (design spec
+`2026-10-22-syncbox-records-and-segments-design.md` section 3). Every type
+comes from `wl_sync.log`, so a change of format there breaks these fixtures
+loudly instead of letting the generator and the pipeline drift apart.
 """
 
 from __future__ import annotations
 
 from pathlib import Path
 
-from wl_sync.barcode import encode
-from wl_sync.log import SCHEMA_VERSION, CodeWord, Edge, Record, SyncBoxLogHeader, write_log
+from wl_sync.log import SCHEMA_VERSION, BarcodeEmitted, CodeWord, Record, SyncBoxLogHeader, write_log
+from wl_sync.pins import GPIO_MAP
 
 from wl_preproc.synth.recipe import SYNTH_EPOCH, SessionRecipe
 from wl_preproc.synth.timeline import apply_drift
 from wl_preproc.synth.truth import GroundTruth
 
-BARCODE_GPIO = 17
-CODE_STROBE_GPIO = 18
-CODE_DATA_BASE_GPIO = 2
-
 # The log's tick origin is not session time, deliberately. Two reasons: the
 # decoder needs an idle before the first frame or it correctly refuses it, and a
 # fixture where tick == session time would let a pipeline bug that ignores the
@@ -31,25 +32,11 @@ SYNCBOX_PRE_ROLL_S = 1.0
 def write_syncbox_log(
     path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
 ) -> None:
-    records: list[Record] = []
-
-    for value, start_s in truth.barcodes:
-        tick = int(round((apply_drift(start_s, drift_ppm) + SYNCBOX_PRE_ROLL_S) * 1e6))
-        for level, duration_us in encode(value):
-            records.append(Edge(tick_us=tick, gpio=BARCODE_GPIO, level=level))
-            tick += duration_us
-        records.append(Edge(tick_us=tick, gpio=BARCODE_GPIO, level=0))
-
-    for time_s, word in truth.code_words:
-        records.append(
-            CodeWord(
-                tick_us=int(
-                    round((apply_drift(time_s, drift_ppm) + SYNCBOX_PRE_ROLL_S) * 1e6)
-                ),
-                word=word,
-            )
-        )
+    def tick(time_s: float) -> int:
+        return int(round((apply_drift(time_s, drift_ppm) + SYNCBOX_PRE_ROLL_S) * 1e6))
 
+    records: list[Record] = [BarcodeEmitted(tick_us=tick(start_s), value=value) for value, start_s in truth.barcodes]
+    records += [CodeWord(tick_us=tick(time_s), word=word) for time_s, word in truth.code_words]
     records.sort(key=lambda record: record.tick_us)
 
     header = SyncBoxLogHeader(
@@ -58,10 +45,7 @@ def write_syncbox_log(
         rig=recipe.rig,
         boot_id=f"synth{recipe.seed:08x}",
         written_at=SYNTH_EPOCH,
-        gpio_map={
-            "barcode_out": BARCODE_GPIO,
-            "code_strobe": CODE_STROBE_GPIO,
-            "code_data_base": CODE_DATA_BASE_GPIO,
-        },
+        gpio_map=GPIO_MAP,
+        segment_index=0,
     )
     write_log(path, header, records)
diff --git a/wl_preproc/timebase/_syncbox_log.py b/wl_preproc/timebase/_syncbox_log.py
index 06b34ed..81bc24b 100644
--- a/wl_preproc/timebase/_syncbox_log.py
+++ b/wl_preproc/timebase/_syncbox_log.py
@@ -1,12 +1,20 @@
 """The sync box log's one reader.
 
 `wl_sync.log` defines the on-disk shape: a JSON header line, then one CSV
-line per record, type-tagged 'E' (a GPIO transition) or 'W' (a code word
-latched on a strobe edge). The barcode is carried on whichever GPIO the
-header's own `gpio_map` names as the barcode line -- not a hardcoded pin
-number, because the header exists precisely so a reader never has to assume
-one. Recovering "barcode entries" from that line means running wl-sync's own
-frame decoder over its edges, never re-deriving the frame shape by hand.
+line per record, type-tagged 'E' (a transition on an input line), 'W' (a code
+word latched on a strobe edge), 'B' (a barcode the box emitted) or 'T' (a
+segment's trailer, which `read_log` skips). **The barcodes are the 'B'
+records**: the box never captures its own barcode line, which is an output
+(wl-sync's `pins.py`, GPIO17; its edge inputs are GPIO20-27), and logs each
+frame it emits instead, "the emitter's own account of what it put on the wire,
+not a measurement of it" (`wl_sync/log.py`). Until 2026-10-22 this decoded
+edges on the line the header's `gpio_map` named `barcode_out`, the shape only
+this project's synthetic logs had: a real log yielded no barcodes (design spec
+`2026-10-22-syncbox-records-and-segments-design.md` section 1).
+
+A 'B' tick is read before the frame is sent, so it leads the frame's edge on
+the wire by a delay not yet measured; it is used as it is, and recorded as
+uncorrected (that spec's section 5).
 
 Kept in its own module, separate from `extract.py`, so the log format's shape
 has exactly one reader.
@@ -16,48 +24,18 @@ from __future__ import annotations
 
 from pathlib import Path
 
-from wl_sync.barcode import decode_edges
-from wl_sync.log import Edge, read_log
-
-# `wl_preproc.synth.syncbox.write_syncbox_log` is the log's one writer today,
-# and this is the key it stamps into the header's gpio_map for the barcode
-# line. wl_sync treats gpio_map as an opaque dict[str, int] -- the name is
-# this project's own convention, not wl-sync's, so it is restated here as a
-# constant rather than imported from the synth package: a production reader
-# has no business depending on the synthetic fixture generator, even one that
-# happens to share its vocabulary.
-_BARCODE_GPIO_KEY = "barcode_out"
+from wl_sync.log import BarcodeEmitted, read_log
 
 
 def read_barcode_entries(path: Path) -> list[tuple[int, int]]:
-    """Decode the sync box log's barcode line into (value, timestamp_us) pairs.
+    """`(value, tick_us)` for every barcode the box logged emitting, in the
+    order the log holds them.
 
     `wl_sync.log.read_log` validates the header as it parses it (a malformed
-    header raises there, via `SyncBoxLogHeader`'s own pydantic validators) and
-    returns every record the log carries -- interleaving whatever GPIO lines
-    the sync box captured plus any strobed code words. Filtering to the line
-    the header names as the barcode, then handing those edges to
-    `wl_sync.barcode.decode_edges`, is the decode this function exists to own,
-    so every caller gets it for free instead of re-parsing the log itself.
-
-    `start_us=0` is passed to `decode_edges`: tick 0 is the log's own native
-    time origin, and the RP1 tick counter is known LOW there (nothing has
-    driven the line yet). Without it, `decode_edges` cannot verify the idle
-    preceding the very first frame and correctly refuses to decode it -- see
-    that function's own docstring.
+    header raises there, via `SyncBoxLogHeader`'s own pydantic validators),
+    drops a torn last line, and skips the trailer. A log holding no 'B' record
+    yields no barcodes, and a session whose sync box logged none has no
+    session time (`timebase/segments.py::session_reference`).
     """
-    header, records = read_log(path)
-    try:
-        barcode_gpio = header.gpio_map[_BARCODE_GPIO_KEY]
-    except KeyError as exc:
-        raise ValueError(
-            f"{path}: gpio_map has no {_BARCODE_GPIO_KEY!r} entry; found "
-            f"{sorted(header.gpio_map)}"
-        ) from exc
-
-    edges = [
-        (record.tick_us, record.level)
-        for record in records
-        if isinstance(record, Edge) and record.gpio == barcode_gpio
-    ]
-    return [(barcode.value, barcode.start_us) for barcode in decode_edges(edges, start_us=0)]
+    _header, records = read_log(path)
+    return [(record.value, record.tick_us) for record in records if isinstance(record, BarcodeEmitted)]
diff --git a/wl_preproc/timebase/extract.py b/wl_preproc/timebase/extract.py
index e01a3d3..8db5578 100644
--- a/wl_preproc/timebase/extract.py
+++ b/wl_preproc/timebase/extract.py
@@ -157,8 +157,9 @@ def _edges_from_bit(
 
 # Which digital-in line carries the barcode is this project's wiring
 # convention, not something the Intan header records: its digital channels are
-# named DIGITAL-IN-00.. and carry no semantics. So it is stated here, the way
-# `_syncbox_log.py` states the sync box's gpio_map key, rather than discovered.
+# named DIGITAL-IN-00.. and carry no semantics. So it is stated here rather than
+# discovered. (The sync box needs no such constant: it logs the barcodes it
+# emits as records of their own, `_syncbox_log.py`.)
 # Zero-based, and it is bit 0 that `wl_preproc/synth/rhs.py` drives -- but the
 # authority for January is the rig's wiring, so this constant is the one place
 # to change when that is measured rather than assumed.
@@ -215,8 +216,7 @@ def extract_rhs(session_dir: Path) -> BitStream:
 # The consuming half of `wl_preproc/synth/spikeglx.py`'s wiring convention:
 # which Port 0 line the barcode is on. Zero-based. SpikeGLX records the digital
 # lines' existence (`niXDChans1`) but nothing about their meaning, so this is
-# stated here rather than discovered -- the same shape as the sync box's
-# gpio_map key and the RHS digital bit.
+# stated here rather than discovered -- the same shape as the RHS digital bit.
 NIDQ_BARCODE_XD_LINE = 0
 
 _NIDQ_META_SUFFIX = ".meta"
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/synth/test_syncbox.py tests/timebase/test_clock.py tests/timebase/test_extract.py tests/timebase/test_fit.py tests/timebase/test_syncbox_log.py -q -p no:cacheprovider
```

Expected: `58 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events tests/schema/test_eye_populate.py tests/synth tests/timebase -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `408 passed, 1 skipped`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "values and ticks swapped",
  "wl_preproc/timebase/_syncbox_log.py",
  "barcodes=tuple((record.value, record.tick_us) for record in records",
  "barcodes=tuple((record.tick_us, record.value) for record in records",
  "tests/timebase/test_syncbox_log.py"
 ],
 [
  "the synthetic box logs no barcode",
  "wl_preproc/synth/syncbox.py",
  "for value, start_s in truth.barcodes if kept(start_s)]",
  "for value, start_s in truth.barcodes if False]",
  "tests/synth/test_syncbox.py"
 ],
 [
  "the synthetic header names pins of its own",
  "wl_preproc/synth/syncbox.py",
  "        gpio_map=GPIO_MAP,",
  "        gpio_map={\"barcode_out\": 17},",
  "tests/synth/test_syncbox.py"
 ],
 [
  "the synthetic log keeps its old name",
  "wl_preproc/synth/syncbox.py",
  "    first = directory / segment_name(0)",
  "    first = directory / \"syncbox.log\"",
  "tests/synth/test_syncbox.py"
 ]
]
```

Expected: 4 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(timebase): the sync box's barcodes are its B records -- the box never captures its own barcode line; synthetic logs are written as the box writes them, B records and the board's pin map, named seg-000.log (section 3)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 3: Each segment is its own clock

§4: a folder's segments, read with their headers and ordered by `segment_index`; session time from the first holding a barcode; the events stage reads that segment alone (amendment 3). The synthetic restart: a second segment on a clock that starts again, and no barcode in the gap on any device (amendment 2).

**Files:**
- Modify: `wl_preproc/timebase/_syncbox_log.py` (`SyncBoxLog`, `read_syncbox_log`)
- Modify: `wl_preproc/timebase/segments.py` (`syncbox_logs`, `session_log`, `session_reference`), `wl_preproc/timebase/clock.py` (`syncbox_codes`)
- Modify: `wl_preproc/synth/recipe.py` (`SyncBoxRestartSpec`, `syncbox_restart`), `wl_preproc/synth/timeline.py`, `wl_preproc/synth/syncbox.py` (`write_syncbox_segments`), `wl_preproc/synth/session.py`
- Create: `tests/timebase/test_syncbox_segments.py`

**Interfaces:**
- Consumes: Task 2's `read_barcode_entries`, `write_syncbox_log`.
- Produces: `_syncbox_log.SyncBoxLog(path, segment_index, boot_id, clock_trusted, clock_reason, barcodes)`,
  `read_syncbox_log(path) -> SyncBoxLog`; `segments.syncbox_logs(session_dir) -> list[SyncBoxLog]`;
  `segments.session_log(session_dir) -> SyncBoxLog`, raising `FileNotFoundError` (no log) or `ValueError`
  ("decoded no barcodes"); `recipe.SyncBoxRestartSpec(at_s, gap_s=1.2)`, `SessionRecipe.syncbox_restart`;
  `synth.syncbox.write_syncbox_segments(directory, recipe, truth) -> list[Path]`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/timebase/test_syncbox_segments.py b/tests/timebase/test_syncbox_segments.py
new file mode 100644
index 0000000..4918a52
--- /dev/null
+++ b/tests/timebase/test_syncbox_segments.py
@@ -0,0 +1,93 @@
+"""Each sync box segment is its own clock (design spec
+`2026-10-22-syncbox-records-and-segments-design.md` section 4). A segment is one
+run of the box's process, and "ticks after a restart are not comparable BY
+NUMBER to ticks before it" (wl-sync's `segment.py`); a restart resumes into the
+open session's folder. Session time comes from the first segment holding a
+barcode, and a later segment's records are never mixed into it."""
+
+from __future__ import annotations
+
+import datetime
+
+import pytest
+from wl_sync.log import SCHEMA_VERSION, BarcodeEmitted, CodeWord, SyncBoxLogHeader, write_log
+from wl_sync.pins import GPIO_MAP
+
+from wl_preproc.synth.recipe import CI_RECIPE, SyncBoxRestartSpec
+from wl_preproc.synth.session import generate_session
+from wl_preproc.timebase import segments
+from wl_preproc.timebase.clock import syncbox_codes
+
+RESTART = CI_RECIPE.model_copy(update={"syncbox_restart": SyncBoxRestartSpec(at_s=7.5, gap_s=1.2)})
+
+
+def _restarted(tmp_path):
+    truth = generate_session(tmp_path, RESTART)
+    return tmp_path / RESTART.session_id, truth
+
+
+def _segment(syncbox_dir, name, index, records):
+    syncbox_dir.mkdir(parents=True, exist_ok=True)
+    header = SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id="2027-03-14_01", rig="rig0",
+                              boot_id="b0", written_at=datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC),
+                              gpio_map=GPIO_MAP, segment_index=index)
+    write_log(syncbox_dir / name, header, records)
+
+
+def test_a_restarted_box_writes_two_segments_on_unrelated_clocks(tmp_path):
+    """The fixture is the case: the box emits nothing while down, so no device
+    records a barcode in the gap, and the second segment's ticks start again
+    below the first's."""
+    session_dir, truth = _restarted(tmp_path)
+    assert 1_000_008 not in {value for value, _ in truth.barcodes}
+    first, second = segments.syncbox_logs(session_dir)
+    assert (first.path.name, first.segment_index, second.path.name, second.segment_index) == (
+        "seg-000.log", 0, "seg-001.log", 1)
+    assert [value for value, _ in first.barcodes] == list(range(1_000_000, 1_000_008))
+    assert [value for value, _ in second.barcodes] == list(range(1_000_009, 1_000_015))
+    assert second.barcodes[0][1] < first.barcodes[-1][1]
+
+
+def test_segments_are_ordered_by_their_index_not_their_names(tmp_path):
+    """`seg-1000.log` sorts before `seg-999.log` by name; the header's
+    `segment_index` is what orders them."""
+    syncbox_dir = tmp_path / "s" / "syncbox"
+    _segment(syncbox_dir, "seg-999.log", 999, [BarcodeEmitted(tick_us=1_000_000, value=50)])
+    _segment(syncbox_dir, "seg-1000.log", 1000, [BarcodeEmitted(tick_us=1_000_000, value=90)])
+    assert [log.segment_index for log in segments.syncbox_logs(tmp_path / "s")] == [999, 1000]
+    assert segments.session_reference(tmp_path / "s") == {50: 0.0}
+
+
+def test_session_time_comes_from_the_first_segment_alone(tmp_path):
+    session_dir, truth = _restarted(tmp_path)
+    reference = segments.session_reference(session_dir)
+    assert sorted(reference) == list(range(1_000_000, 1_000_008))
+    for value, start_s in truth.barcodes:
+        if value in reference:
+            assert reference[value] == pytest.approx(start_s, abs=1e-6)
+
+
+def test_the_boxs_codes_after_a_restart_are_left_out_not_mixed_in(tmp_path):
+    session_dir, truth = _restarted(tmp_path)
+    codes = syncbox_codes(session_dir)
+    before = [(time_s, word) for time_s, word in truth.code_words if time_s < RESTART.syncbox_restart.at_s]
+    assert [word for _, word in codes] == [word for _, word in before]
+    for (got_s, _), (want_s, _) in zip(codes, before, strict=True):
+        assert got_s == pytest.approx(want_s, abs=1e-5)
+
+
+def test_a_first_segment_with_no_barcode_leaves_session_time_to_the_next(tmp_path):
+    """A box that crashed within its first second logged no barcode there."""
+    syncbox_dir = tmp_path / "s" / "syncbox"
+    _segment(syncbox_dir, "seg-000.log", 0, [CodeWord(tick_us=500_000, word=0x8001)])
+    _segment(syncbox_dir, "seg-001.log", 1, [BarcodeEmitted(tick_us=2_000_000, value=70),
+                                             BarcodeEmitted(tick_us=3_000_000, value=71)])
+    assert segments.session_log(tmp_path / "s").segment_index == 1
+    assert segments.session_reference(tmp_path / "s") == {70: 0.0, 71: 1.0}
+
+
+def test_a_box_that_logged_no_barcode_in_any_segment_gives_no_session_time(tmp_path):
+    syncbox_dir = tmp_path / "s" / "syncbox"
+    _segment(syncbox_dir, "seg-000.log", 0, [CodeWord(tick_us=500_000, word=0x8001)])
+    with pytest.raises(ValueError, match="no barcodes"):
+        segments.session_reference(tmp_path / "s")
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/timebase/test_syncbox_segments.py -q -p no:cacheprovider
```

Expected: `1 error`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/synth/recipe.py b/wl_preproc/synth/recipe.py
index 6f06edf..be6342c 100644
--- a/wl_preproc/synth/recipe.py
+++ b/wl_preproc/synth/recipe.py
@@ -164,6 +164,19 @@ class RestartSpec(BaseModel):
     probe_bank: int | None = Field(default=None, ge=0)
 
 
+class SyncBoxRestartSpec(BaseModel):
+    """The sync box's process stopped at `at_s` (session time) and ran again
+    `gap_s` later, resuming into the open session's folder as its second
+    segment, on a clock that starts again (wl-sync's `segment.py`; design spec
+    `2026-10-22-syncbox-records-and-segments-design.md` section 4). It emits no
+    barcode while down, so no device records one then."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    at_s: float = Field(gt=0)
+    gap_s: float = Field(default=1.2, ge=0)
+
+
 class RigWarningSpec(BaseModel):
     """One warning the synthetic session accepted, as wl-xcon's
     `record.py::SessionRecord.warning` writes it: `run` is wl-xcon's 0-based
@@ -265,6 +278,9 @@ class SessionRecipe(BaseModel):
     # `2026-09-30-nwb-probes-design.md` section 7).
     extra_probes: tuple[ProbeSpec, ...] = ()
     spikeglx_restart: RestartSpec | None = None
+    # The sync box restarting mid-session; None by default, so every existing
+    # profile is byte-identical.
+    syncbox_restart: SyncBoxRestartSpec | None = None
     # wl-xcon's fault shapes (design spec `2026-10-01-runs-and-trials-design.md`
     # section 7), both empty by default so every existing profile is
     # byte-identical. `unclosed_blocks`: block numbers (from 1) whose BLOCK_END
diff --git a/wl_preproc/synth/session.py b/wl_preproc/synth/session.py
index 922c63b..acccc82 100644
--- a/wl_preproc/synth/session.py
+++ b/wl_preproc/synth/session.py
@@ -6,7 +6,6 @@ import datetime
 from pathlib import Path
 
 import numpy as np
-from wl_sync.segment import segment_name
 from wl_sync.session import SessionId
 
 from wl_preproc.contracts.done import DONE_SCHEMA_VERSION, DoneMarker, FileEntry, blake3_file
@@ -24,7 +23,7 @@ from wl_preproc.synth.ohdpi import write_ohdpi
 from wl_preproc.synth.recipe import Fault, SYNTH_EPOCH, SessionRecipe
 from wl_preproc.synth.rhs import write_rhs
 from wl_preproc.synth.spikeglx import write_spikeglx
-from wl_preproc.synth.syncbox import write_syncbox_log
+from wl_preproc.synth.syncbox import write_syncbox_segments
 from wl_preproc.synth.timeline import build_timeline
 from wl_preproc.synth.truth import GroundTruth
 
@@ -83,9 +82,7 @@ def generate_session(root: Path, recipe: SessionRecipe) -> GroundTruth:
             # drifting it alongside every device, which this did until Phase
             # 1c-4, cancels the drift exactly and leaves every fixture with no
             # relative drift for the rate fit to find.
-            # Named as the box names a session's first segment (wl-sync's
-            # `segment.py`), so the synthetic folder is the box's.
-            write_syncbox_log(directory / segment_name(0), recipe, truth, drift_ppm=0.0)
+            write_syncbox_segments(directory, recipe, truth)
             write_task_file(directory / "task.json", truth)
         elif system == "spikeglx":
             bin_path = write_spikeglx(
diff --git a/wl_preproc/synth/syncbox.py b/wl_preproc/synth/syncbox.py
index d5bbf4c..2ec7c58 100644
--- a/wl_preproc/synth/syncbox.py
+++ b/wl_preproc/synth/syncbox.py
@@ -16,6 +16,7 @@ from pathlib import Path
 
 from wl_sync.log import SCHEMA_VERSION, BarcodeEmitted, CodeWord, Record, SyncBoxLogHeader, write_log
 from wl_sync.pins import GPIO_MAP
+from wl_sync.segment import segment_name
 
 from wl_preproc.synth.recipe import SYNTH_EPOCH, SessionRecipe
 from wl_preproc.synth.timeline import apply_drift
@@ -27,16 +28,53 @@ from wl_preproc.synth.truth import GroundTruth
 # offset pass every alignment test. Each system gets a *different* pre-roll for
 # the same reason — see spikeglx.py.
 SYNCBOX_PRE_ROLL_S = 1.0
+# A restarted process's first tick before its first barcode. Shorter than the
+# first segment's pre-roll, so the second segment's ticks start again below the
+# first's: a reader that compared them by number would mix the two clocks, and
+# the restart fixture shows it.
+SYNCBOX_RESTART_PRE_ROLL_S = 0.6
 
 
 def write_syncbox_log(
     path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
 ) -> None:
+    """The whole session as one segment, the first."""
+    _write_segment(path, recipe, truth, index=0, since_s=None, until_s=None, origin_s=0.0,
+                   pre_roll_s=SYNCBOX_PRE_ROLL_S, drift_ppm=drift_ppm)
+
+
+def write_syncbox_segments(directory: Path, recipe: SessionRecipe, truth: GroundTruth) -> list[Path]:
+    """The session's segments, named as the box names them (`seg-000.log`):
+    one, or, for `recipe.syncbox_restart`, the second written after the
+    restart on a clock that starts again, without what the box missed while
+    down. Never drifted: session time is the box's own timeline."""
+    restart = recipe.syncbox_restart
+    first = directory / segment_name(0)
+    if restart is None:
+        write_syncbox_log(first, recipe, truth)
+        return [first]
+    resumed_s = restart.at_s + restart.gap_s
+    second = directory / segment_name(1)
+    _write_segment(first, recipe, truth, index=0, since_s=None, until_s=restart.at_s, origin_s=0.0,
+                   pre_roll_s=SYNCBOX_PRE_ROLL_S, drift_ppm=0.0)
+    _write_segment(second, recipe, truth, index=1, since_s=resumed_s, until_s=None, origin_s=resumed_s,
+                   pre_roll_s=SYNCBOX_RESTART_PRE_ROLL_S, drift_ppm=0.0)
+    return [first, second]
+
+
+def _write_segment(path: Path, recipe: SessionRecipe, truth: GroundTruth, *, index: int, since_s: float | None,
+                   until_s: float | None, origin_s: float, pre_roll_s: float, drift_ppm: float) -> None:
+    """The records of session time `[since_s, until_s)`, ticked from `origin_s`
+    after `pre_roll_s` of this segment's own clock."""
+    def kept(time_s: float) -> bool:
+        return (since_s is None or time_s >= since_s) and (until_s is None or time_s < until_s)
+
     def tick(time_s: float) -> int:
-        return int(round((apply_drift(time_s, drift_ppm) + SYNCBOX_PRE_ROLL_S) * 1e6))
+        return int(round((apply_drift(time_s - origin_s, drift_ppm) + pre_roll_s) * 1e6))
 
-    records: list[Record] = [BarcodeEmitted(tick_us=tick(start_s), value=value) for value, start_s in truth.barcodes]
-    records += [CodeWord(tick_us=tick(time_s), word=word) for time_s, word in truth.code_words]
+    records: list[Record] = [BarcodeEmitted(tick_us=tick(start_s), value=value)
+                             for value, start_s in truth.barcodes if kept(start_s)]
+    records += [CodeWord(tick_us=tick(time_s), word=word) for time_s, word in truth.code_words if kept(time_s)]
     records.sort(key=lambda record: record.tick_us)
 
     header = SyncBoxLogHeader(
@@ -46,6 +84,6 @@ def write_syncbox_log(
         boot_id=f"synth{recipe.seed:08x}",
         written_at=SYNTH_EPOCH,
         gpio_map=GPIO_MAP,
-        segment_index=0,
+        segment_index=index,
     )
     write_log(path, header, records)
diff --git a/wl_preproc/synth/timeline.py b/wl_preproc/synth/timeline.py
index 9ece5ce..266af40 100644
--- a/wl_preproc/synth/timeline.py
+++ b/wl_preproc/synth/timeline.py
@@ -98,8 +98,11 @@ def build_timeline(recipe: SessionRecipe) -> GroundTruth:
     barcodes: list[tuple[int, float]] = []
     time_s = 0.0
     value = 1_000_000
+    restart = recipe.syncbox_restart
     while time_s < recipe.duration_s:
-        barcodes.append((value, time_s))
+        # A box that is down emits nothing; its count of seconds runs on.
+        if restart is None or not restart.at_s <= time_s < restart.at_s + restart.gap_s:
+            barcodes.append((value, time_s))
         value += 1
         time_s += BARCODE_INTERVAL_S
 
diff --git a/wl_preproc/timebase/_syncbox_log.py b/wl_preproc/timebase/_syncbox_log.py
index 81bc24b..599e4ba 100644
--- a/wl_preproc/timebase/_syncbox_log.py
+++ b/wl_preproc/timebase/_syncbox_log.py
@@ -22,11 +22,35 @@ has exactly one reader.
 
 from __future__ import annotations
 
+from dataclasses import dataclass
 from pathlib import Path
 
 from wl_sync.log import BarcodeEmitted, read_log
 
 
+@dataclass(frozen=True, slots=True)
+class SyncBoxLog:
+    """One segment of the sync box's log: one run of its process, on a clock
+    of its own (wl-sync's `segment.py`), with what its header says of it and
+    the barcodes it logged emitting, `(value, tick_us)`."""
+
+    path: Path
+    segment_index: int
+    boot_id: str
+    clock_trusted: bool
+    clock_reason: str
+    barcodes: tuple[tuple[int, int], ...]
+
+
+def read_syncbox_log(path: Path) -> SyncBoxLog:
+    header, records = read_log(path)
+    return SyncBoxLog(
+        path=path, segment_index=header.segment_index, boot_id=header.boot_id,
+        clock_trusted=header.clock_trusted, clock_reason=header.clock_reason,
+        barcodes=tuple((record.value, record.tick_us) for record in records if isinstance(record, BarcodeEmitted)),
+    )
+
+
 def read_barcode_entries(path: Path) -> list[tuple[int, int]]:
     """`(value, tick_us)` for every barcode the box logged emitting, in the
     order the log holds them.
@@ -37,5 +61,4 @@ def read_barcode_entries(path: Path) -> list[tuple[int, int]]:
     yields no barcodes, and a session whose sync box logged none has no
     session time (`timebase/segments.py::session_reference`).
     """
-    _header, records = read_log(path)
-    return [(record.value, record.tick_us) for record in records if isinstance(record, BarcodeEmitted)]
+    return list(read_syncbox_log(path).barcodes)
diff --git a/wl_preproc/timebase/clock.py b/wl_preproc/timebase/clock.py
index 87cfde7..7a0a5ef 100644
--- a/wl_preproc/timebase/clock.py
+++ b/wl_preproc/timebase/clock.py
@@ -131,7 +131,11 @@ def syncbox_codes(session_dir: Path) -> list[tuple[float, int]]:
     unchanged: the sync box's codes are the session's codes while it is the
     clock, and every stage reads them through `Clock.codes`."""
     reference = segments.session_reference(session_dir)
-    scans = segments.scan_system("syncbox", session_dir / "syncbox")
+    # The segment session time comes from, alone: a later one, after a
+    # restart, is on another clock (design spec
+    # `2026-10-22-syncbox-records-and-segments-design.md` section 4).
+    timed = segments.session_log(session_dir).path
+    scans = [scan for scan in segments.scan_system("syncbox", session_dir / "syncbox") if scan.path == timed]
     if not scans:
         raise FileNotFoundError(
             f"{session_dir}: no syncbox recording, so this session has no "
@@ -157,10 +161,9 @@ def syncbox_codes(session_dir: Path) -> list[tuple[float, int]]:
         )
 
     # `decode_stream` reads an escape payload by POSITION, not by comparing
-    # timestamps -- see its own docstring -- so a session recorded across more
-    # than one sync box log file needs re-sorting to restore emission order
-    # before decoding. A single file (the common case, and every current
-    # fixture) is already sorted; this is then a no-op.
+    # timestamps -- see its own docstring -- so the stream is sorted into
+    # emission order before decoding. One segment's words already are; this is
+    # then a no-op.
     converted.sort(key=lambda pair: pair[0])
     return converted
 
diff --git a/wl_preproc/timebase/segments.py b/wl_preproc/timebase/segments.py
index 6d7362d..5cca5cc 100644
--- a/wl_preproc/timebase/segments.py
+++ b/wl_preproc/timebase/segments.py
@@ -32,6 +32,8 @@ from typing import TYPE_CHECKING
 
 from wl_sync.barcode import INTERVAL_US
 
+from wl_preproc.timebase._syncbox_log import SyncBoxLog, read_syncbox_log
+
 if TYPE_CHECKING:
     from wl_sync.barcode import Barcode
 
@@ -107,33 +109,59 @@ def classify_segment(duration_s: float, n_barcodes: int) -> str:
 # stay declarations. It also keeps this testable without a database.
 
 
-def session_reference(session_dir: Path) -> dict[int, float]:
-    """Barcode value -> session-time seconds, from the sync box's own log.
+def syncbox_logs(session_dir: Path) -> list[SyncBoxLog]:
+    """Every segment of the session's sync box log, in the order of its
+    header's `segment_index`: a file's name sorts `seg-1000.log` before
+    `seg-999.log`. Each is one run of the box's process on a clock of its own,
+    and a restart resumes into the open session's folder (wl-sync's
+    `segment.py`; design spec `2026-10-22-syncbox-records-and-segments-design.md`
+    section 4), so no tick is ever compared across two of them."""
+    from wl_preproc.timebase.extract import find_recordings
 
-    Session time is t=0 at the sync box's FIRST barcode, on every session type,
-    one code path (spec section 4.5). So the reference is built by decoding that
-    log and rebasing to its first barcode -- never from `GroundTruth`, which
-    exists only in the synthetic generator and will not exist in January.
+    logs = [read_syncbox_log(path) for path in find_recordings("syncbox", session_dir / "syncbox")]
+    return sorted(logs, key=lambda log: (log.segment_index, log.path.name))
 
-    Raises if the sync box recorded nothing decodable: without a reference there
-    is no session time at all, and every other system's fit would silently
-    become a fit against whichever device happened to be read first.
-    """
-    from wl_sync.barcode import decode_edges
 
-    from wl_preproc.timebase.extract import extract_syncbox, find_recordings
+def session_log(session_dir: Path) -> SyncBoxLog:
+    """The segment session time comes from: the first holding a barcode. A box
+    that crashed within its first second logged none in that segment.
 
-    logs = find_recordings("syncbox", session_dir / "syncbox")
+    Raises if the sync box recorded nothing, or no barcode in any segment:
+    without a reference there is no session time at all, and every other
+    system's fit would silently become a fit against whichever device happened
+    to be read first."""
+    logs = syncbox_logs(session_dir)
     if not logs:
         raise FileNotFoundError(
             f"{session_dir}: no sync box log, so this session has no session "
             "time. Every other system is positioned against it, so there is "
             "nothing to align to rather than one system missing"
         )
+    for log in logs:
+        if log.barcodes:
+            return log
+    raise ValueError(
+        f"{session_dir}: the sync box log decoded no barcodes, so session "
+        "time cannot be established"
+    )
+
+
+def session_reference(session_dir: Path) -> dict[int, float]:
+    """Barcode value -> session-time seconds, from the sync box's own log.
+
+    Session time is t=0 at the sync box's FIRST barcode, on every session type,
+    one code path (spec section 4.5), within the segment `session_log` names:
+    a later segment, after a restart, is on another clock and gives it nothing
+    (design spec `2026-10-22-syncbox-records-and-segments-design.md` section
+    4). Built by decoding that segment and rebasing to its first barcode --
+    never from `GroundTruth`, which exists only in the synthetic generator and
+    will not exist in January.
+    """
+    from wl_sync.barcode import decode_edges
+
+    from wl_preproc.timebase.extract import extract_syncbox
 
-    decoded = [
-        barcode for log in logs for barcode in decode_edges(list(extract_syncbox(log).edges))
-    ]
+    decoded = decode_edges(list(extract_syncbox(session_log(session_dir).path).edges))
     if not decoded:
         raise ValueError(
             f"{session_dir}: the sync box log decoded no barcodes, so session "
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/timebase/test_syncbox_segments.py -q -p no:cacheprovider
```

Expected: `6 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events tests/nwb/test_clock.py tests/schema/test_events.py tests/schema/test_segment_populate.py tests/schema/test_session_clock.py tests/schema/test_timebase.py tests/synth tests/timebase -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `434 passed, 1 skipped`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "session time from every segment",
  "wl_preproc/timebase/segments.py",
  "    decoded = decode_edges(list(extract_syncbox(session_log(session_dir).path).edges))",
  "    decoded = [b for log in syncbox_logs(session_dir) for b in decode_edges(list(extract_syncbox(log.path).edges))]",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "segments ordered by name",
  "wl_preproc/timebase/segments.py",
  "    return sorted(logs, key=lambda log: (log.segment_index, log.path.name))",
  "    return sorted(logs, key=lambda log: log.path.name)",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "a barcode-less first segment keeps session time",
  "wl_preproc/timebase/segments.py",
  "        if log.barcodes:\n            return log",
  "        if True:\n            return log",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "the events stage reads every segment",
  "wl_preproc/timebase/clock.py",
  "session_dir / \"syncbox\") if scan.path == timed]",
  "session_dir / \"syncbox\") if True]",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "the box emits in the gap",
  "wl_preproc/synth/timeline.py",
  "        if restart is None or not restart.at_s <= time_s < restart.at_s + restart.gap_s:",
  "        if True:",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "the restarted clock starts above the first",
  "wl_preproc/synth/syncbox.py",
  "pre_roll_s=SYNCBOX_RESTART_PRE_ROLL_S, drift_ppm=0.0)",
  "pre_roll_s=SYNCBOX_PRE_ROLL_S + 20.0, drift_ppm=0.0)",
  "tests/timebase/test_syncbox_segments.py"
 ]
]
```

Expected: 6 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(timebase): each sync box segment is its own clock -- session time comes from the first segment holding a barcode, and the events stage reads that segment alone, so a restart is never mixed in; a synthetic restart (section 4)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 4: A recording sharing no barcode with session time is rejected

§4: `fit_offset` raises on a recording none of whose barcodes is in session time's reference, and `Segment.make` did not catch it, so a sync box restart failed the whole stage. It is rejected as `no_shared_barcode`, the stage completes, and the session is tier D, a rejected file failing a check.

**Files:**
- Modify: `wl_preproc/timebase/segments.py` (`NO_SHARED_BARCODE`, `REJECTION_REASONS`)
- Modify: `wl_preproc/schema/core.py` (`Segment.make`)
- Create: `tests/schema/test_syncbox_restart.py`

**Interfaces:**
- Consumes: Task 3's restart fixture and `session_log`.
- Produces: `segments.NO_SHARED_BARCODE = "no_shared_barcode"`; `tests/schema/test_syncbox_restart.py::restarted`,
  a module fixture `(key, session_dir, report)`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/schema/test_syncbox_restart.py b/tests/schema/test_syncbox_restart.py
new file mode 100644
index 0000000..53e9b36
--- /dev/null
+++ b/tests/schema/test_syncbox_restart.py
@@ -0,0 +1,63 @@
+"""A session whose sync box restarted partway through (design spec
+`2026-10-22-syncbox-records-and-segments-design.md` section 4): its second
+segment is on another clock, so nothing in it is mixed into session time. A
+daemon pass rejects it with a reason and completes, the other devices are
+timed by their own fits, and the session is tier D until part 2 bridges it."""
+
+from __future__ import annotations
+
+import pytest
+
+from tests.identities import new_animal
+
+
+@pytest.fixture(scope="module")
+def restarted(dj_conn, prefix, tmp_path_factory):
+    """A `ci` session whose box restarted at 7.5 s for 1.2 s, landed by hand as
+    `test_eye_populate.py` lands one, after one daemon pass; deleted after the
+    module, so no later module's unrestricted populate meets it."""
+    from wl_preproc import daemon
+    from wl_preproc.schema import pipeline
+    from wl_preproc.synth.recipe import RECIPES, SyncBoxRestartSpec
+    from wl_preproc.synth.session import generate_session
+
+    from tests.schema.test_eye_populate import _land
+
+    daemon.activate_all(prefix=prefix)
+    root = tmp_path_factory.mktemp("restarted")
+    session = new_animal().session()
+    recipe = RECIPES["ci"].model_copy(update={"subject": session.subject, "session_id": session.session_id,
+                                              "syncbox_restart": SyncBoxRestartSpec(at_s=7.5, gap_s=1.2)})
+    generate_session(root, recipe)
+    key = _land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems)
+    report = daemon.run_once(prefix=prefix)
+    yield key, root / recipe.session_id, report
+    (pipeline.Session & key).delete(prompt=False)
+
+
+def test_the_boxs_segment_after_a_restart_is_rejected_with_its_reason(restarted):
+    from wl_preproc.schema import core
+
+    key, _, _ = restarted
+    rejected = (core.RejectedSegment & key).to_dicts()
+    assert [(row["system"], row["file_path"], row["reason"]) for row in rejected] == [
+        ("syncbox", "seg-001.log", "no_shared_barcode")]
+
+
+def test_the_stage_completes_and_every_device_is_timed(restarted):
+    """`fit_offset` raised on a segment sharing no barcode with session time,
+    and nothing caught it, so the whole stage failed instead of recording why."""
+    from wl_preproc.schema import core
+
+    key, _, _ = restarted
+    assert not len(core.Segment.jobs & key & "status = 'error'")
+    assert {(row["system"], row["file_path"]) for row in (core.Segment & key).to_dicts()} >= {
+        ("syncbox", "seg-000.log"), ("bcam", "frames.yaml")}
+    assert "spikeglx" in {row["system"] for row in (core.Segment & key).to_dicts()}
+
+
+def test_a_session_whose_box_restarted_is_tier_d(restarted):
+    from wl_preproc.schema import timebase
+
+    key, _, _ = restarted
+    assert (timebase.TimingProvenance & key).fetch1("tier") == "D"
PATCH
```

- [ ] **Step 2: Run them and see them fail.** `test_a_session_whose_box_restarted_is_tier_d` passes already: the stage that fails marks the session failed too. It pins the outcome Step 3 keeps.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_syncbox_restart.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `2 failed, 1 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/schema/core.py b/wl_preproc/schema/core.py
index 4f4af21..335a8a1 100644
--- a/wl_preproc/schema/core.py
+++ b/wl_preproc/schema/core.py
@@ -399,6 +399,9 @@ class Segment(dj.Computed):
                     {**key, "file_path": file_path, "reason": segments.UNFITTED_SYSTEM}
                 )
                 continue
+            if not any(barcode.value in reference for barcode in scan.barcodes):
+                rejected.append({**key, "file_path": file_path, "reason": segments.NO_SHARED_BARCODE})
+                continue
             offset = fit_offset(scan.barcodes, reference, rate)
             first_barcode_us = min(barcode.start_us for barcode in scan.barcodes)
             accepted.append(
diff --git a/wl_preproc/timebase/segments.py b/wl_preproc/timebase/segments.py
index 5cca5cc..e5a4f46 100644
--- a/wl_preproc/timebase/segments.py
+++ b/wl_preproc/timebase/segments.py
@@ -72,15 +72,26 @@ UNFITTED_SYSTEM = "unfitted_system"
 # to keep up with.
 GAP_CORRUPTED = "gap_corrupted"
 
+# Not a verdict `classify_segment` can reach either: the file decoded barcodes,
+# but none of them is in session time's reference, so nothing places it. The
+# sync box's own segment after a restart is on another clock, and a device
+# recording begun after the restart saw only barcodes that segment emitted;
+# part 2 of the design bridges both (design spec
+# `2026-10-22-syncbox-records-and-segments-design.md` sections 4 and 7). Until
+# 2026-10-22 `timebase/fit.py::fit_offset` raised on such a file and nothing
+# caught it, so the whole stage failed instead of saying why.
+NO_SHARED_BARCODE = "no_shared_barcode"
+
 #: Every reason `RejectedSegment.reason` can hold, and where each is
 #: decided. `TOO_SHORT` and `NO_BARCODE` come from `classify_segment`.
 #: `UNFITTED_SYSTEM` is decided per SYSTEM, above the file, when no rate fit
-#: exists for it to inherit. `GAP_CORRUPTED` is decided per FILE, in
-#: `Segment.make`, overriding the verdict `classify_segment` returned for
-#: that file rather than adding a fact `classify_segment` itself lacked the
-#: inputs to see.
+#: exists for it to inherit. `NO_SHARED_BARCODE` is decided per FILE, in
+#: `Segment.make`, against session time's reference. `GAP_CORRUPTED` is
+#: decided per FILE, in `Segment.make`, overriding the verdict
+#: `classify_segment` returned for that file rather than adding a fact
+#: `classify_segment` itself lacked the inputs to see.
 REJECTION_REASONS: frozenset[str] = frozenset(
-    {TOO_SHORT, NO_BARCODE, UNFITTED_SYSTEM, GAP_CORRUPTED}
+    {TOO_SHORT, NO_BARCODE, UNFITTED_SYSTEM, GAP_CORRUPTED, NO_SHARED_BARCODE}
 )
 
 
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_syncbox_restart.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `3 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_segment_populate.py tests/schema/test_syncbox_restart.py tests/schema/test_timebase.py tests/timebase/test_segments.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `40 passed`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "no check before the fit",
  "wl_preproc/schema/core.py",
  "            if not any(barcode.value in reference for barcode in scan.barcodes):",
  "            if False:",
  "tests/schema/test_syncbox_restart.py"
 ],
 [
  "the wrong reason",
  "wl_preproc/schema/core.py",
  "\"reason\": segments.NO_SHARED_BARCODE})",
  "\"reason\": segments.NO_BARCODE})",
  "tests/schema/test_syncbox_restart.py"
 ]
]
```

Expected: 2 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
fix(timebase): a recording sharing no barcode with session time is rejected as no_shared_barcode -- fit_offset raised on it and Segment.make did not catch it, so a sync box restart would have failed the whole stage instead of recording why (section 4)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 5: Each segment recorded, and the report's restarts

§5 and §6: `timebase.SyncBoxSegment`, one row per segment, its header's fields, its barcodes, its role (amendment 1) and the barcode delay applied, null while unmeasured; a daemon stage; the daily report's live section of every session whose box restarted (amendment 5).

**Files:**
- Modify: `wl_preproc/timebase/segments.py` (`SEGMENT_ROLES`, `segment_roles`)
- Modify: `wl_preproc/schema/timebase.py` (`SyncBoxSegment`), `wl_preproc/daemon.py` (`_computed_tables`)
- Modify: `wl_preproc/cli/report.py` (`_syncbox_restarts`, the section)
- Test: `tests/timebase/test_syncbox_segments.py`, `tests/schema/test_syncbox_restart.py`

**Interfaces:**
- Consumes: Task 3's `syncbox_logs`; Task 4's `restarted`.
- Produces: `timebase.SyncBoxSegment` (key: `subject`, `session_datetime`, `segment_index`): `file_path`,
  `boot_id`, `n_barcodes`, `first_barcode`, `last_barcode`, `clock_trusted`, `clock_reason`, `role`,
  `barcode_delay_us`; `segments.SEGMENT_SESSION_TIME`, `SEGMENT_NO_BARCODE`, `SEGMENT_AFTER_RESTART`,
  `SEGMENT_ROLES`, `segment_roles(logs) -> list[str]`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/schema/test_syncbox_restart.py b/tests/schema/test_syncbox_restart.py
index 53e9b36..f434072 100644
--- a/tests/schema/test_syncbox_restart.py
+++ b/tests/schema/test_syncbox_restart.py
@@ -61,3 +61,38 @@ def test_a_session_whose_box_restarted_is_tier_d(restarted):
 
     key, _, _ = restarted
     assert (timebase.TimingProvenance & key).fetch1("tier") == "D"
+
+
+def test_each_segment_of_the_box_is_recorded_with_its_role(restarted):
+    """`timebase.SyncBoxSegment` (section 6): what each segment's header says,
+    its barcodes, and its role. The barcode delay is null, unmeasured and
+    uncorrected, until wl-sync writes it into the header (section 5)."""
+    from wl_preproc.schema import timebase
+
+    key, _, _ = restarted
+    rows = (timebase.SyncBoxSegment & key).to_dicts(order_by="segment_index")
+    assert [(row["segment_index"], row["file_path"], row["n_barcodes"], row["first_barcode"], row["last_barcode"],
+             row["role"]) for row in rows] == [
+        (0, "seg-000.log", 8, 1_000_000, 1_000_007, "session_time"),
+        (1, "seg-001.log", 6, 1_000_009, 1_000_014, "after_restart_unbridged")]
+    assert {(row["boot_id"][:5], bool(row["clock_trusted"]), row["clock_reason"], row["barcode_delay_us"])
+            for row in rows} == {("synth", True, "", None)}
+
+
+def test_the_report_names_a_session_whose_box_restarted(restarted, prefix):
+    from wl_preproc.cli.report import build_report
+
+    from tests.cli.test_report import _declared_count, _line_for, _section
+
+    key, session_dir, _ = restarted
+    section = _section(build_report(session_dir.parent, prefix=prefix), "Sync box restarts")
+    line = _line_for(section, key["subject"])
+    assert "`seg-001.log`" in line and "wait for a bridge" in line
+    assert _declared_count(section) == len([row for row in section.splitlines() if row.startswith("- ")])
+
+
+def test_the_tables_role_values_are_the_segments_roles(restarted, enum_values):
+    from wl_preproc.schema import timebase
+    from wl_preproc.timebase.segments import SEGMENT_ROLES
+
+    assert enum_values(timebase.SyncBoxSegment.heading.attributes["role"].type) == set(SEGMENT_ROLES)
diff --git a/tests/timebase/test_syncbox_segments.py b/tests/timebase/test_syncbox_segments.py
index 4918a52..03f092d 100644
--- a/tests/timebase/test_syncbox_segments.py
+++ b/tests/timebase/test_syncbox_segments.py
@@ -91,3 +91,16 @@ def test_a_box_that_logged_no_barcode_in_any_segment_gives_no_session_time(tmp_p
     _segment(syncbox_dir, "seg-000.log", 0, [CodeWord(tick_us=500_000, word=0x8001)])
     with pytest.raises(ValueError, match="no barcodes"):
         segments.session_reference(tmp_path / "s")
+
+
+def test_each_segment_has_a_role(tmp_path):
+    """Session time's segment; one that logged no barcode, before it (a box
+    that crashed within its first second) or after; and one after a restart,
+    on another clock, waiting for part 2's bridge (section 6)."""
+    syncbox_dir = tmp_path / "s" / "syncbox"
+    _segment(syncbox_dir, "seg-000.log", 0, [CodeWord(tick_us=500_000, word=0x8001)])
+    _segment(syncbox_dir, "seg-001.log", 1, [BarcodeEmitted(tick_us=2_000_000, value=70)])
+    _segment(syncbox_dir, "seg-002.log", 2, [BarcodeEmitted(tick_us=500_000, value=75)])
+    _segment(syncbox_dir, "seg-003.log", 3, [CodeWord(tick_us=600_000, word=0x8002)])
+    assert segments.segment_roles(segments.syncbox_logs(tmp_path / "s")) == [
+        "no_barcode", "session_time", "after_restart_unbridged", "no_barcode"]
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_syncbox_restart.py tests/timebase/test_syncbox_segments.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `4 failed, 9 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/cli/report.py b/wl_preproc/cli/report.py
index 5775655..3fc1d2e 100644
--- a/wl_preproc/cli/report.py
+++ b/wl_preproc/cli/report.py
@@ -15,6 +15,8 @@ import dataclasses
 import datetime
 from pathlib import Path
 
+from wl_sync.clock import BARCODE_EPOCH
+
 from wl_preproc.cli.doctor import scratch_headroom
 from wl_preproc.schema import DEFAULT_PREFIX
 
@@ -1382,6 +1384,21 @@ def _landed_without(session: dict) -> str:
                    f"UTC: {row['reason']}" for row in rows)
 
 
+def _syncbox_restarts(prefix: str = DEFAULT_PREFIX) -> list[dict]:
+    """Every sync box segment after a restart that waits for a bridge
+    (`timebase.SyncBoxSegment`, role `after_restart_unbridged`; design spec
+    `2026-10-22-syncbox-records-and-segments-design.md` sections 4 and 6),
+    with its session's folder. Live, as the reclamation list is: a segment
+    part 2 bridges changes role and leaves it. Not part of `Readings`, for
+    `_eye_rows`' reason: only `wlpp report` pays for it."""
+    from wl_preproc.schema import ingest, timebase
+    from wl_preproc.timebase.segments import SEGMENT_AFTER_RESTART
+
+    timebase.activate(prefix=prefix)
+    rows = (timebase.SyncBoxSegment & {"role": SEGMENT_AFTER_RESTART}) * ingest.Ingestion.proj("session_dir")
+    return rows.to_dicts(order_by=["subject", "session_datetime", "segment_index"])
+
+
 def build_report(
     root: Path,
     prefix: str = DEFAULT_PREFIX,
@@ -1483,6 +1500,16 @@ def build_report(
         for path, missing in readings.stalled
     ] or ["- none"]
 
+    restarts = _syncbox_restarts(prefix)
+    lines += ["", f"## Sync box restarts — {len(restarts)}", ""]
+    lines += [
+        f"- `{Path(row['session_dir']).name}` — `{row['subject']}` @ {row['session_datetime']:%Y-%m-%d %H:%M} — "
+        f"the sync box restarted: `{row['file_path']}` resumed at barcode {row['first_barcode']} "
+        f"({BARCODE_EPOCH + datetime.timedelta(seconds=row['first_barcode']):%Y-%m-%d %H:%M:%S} UTC by the box's "
+        "clock), on a clock of its own, and its records wait for a bridge"
+        for row in restarts
+    ] or ["- none"]
+
     # Design spec section 5.2 (quoting parent spec §8.5): the reclamation
     # gate, surfaced here "since an ungated session is what will eventually
     # fill scratch" -- see `_unreclaimed_sessions`' own docstring for the
diff --git a/wl_preproc/daemon.py b/wl_preproc/daemon.py
index 99ba721..15d1df9 100644
--- a/wl_preproc/daemon.py
+++ b/wl_preproc/daemon.py
@@ -176,6 +176,10 @@ def _computed_tables() -> list:
     this ordering is what avoids the second.
     """
     return [
+        # Reads the sync box's folder alone; nothing here waits on it, and its
+        # rows say which segment keeps session time (design spec
+        # `2026-10-22-syncbox-records-and-segments-design.md` section 6).
+        timebase.SyncBoxSegment,
         timebase.SystemTimebase,
         core.Segment,
         # After `Segment`, whose rows are its key_source and whose `file_path`
diff --git a/wl_preproc/schema/timebase.py b/wl_preproc/schema/timebase.py
index 40dadf0..e660d67 100644
--- a/wl_preproc/schema/timebase.py
+++ b/wl_preproc/schema/timebase.py
@@ -69,6 +69,8 @@ _TIER_ENUM = "enum('A','B','C','D')"
 # not fitted -- and it is strictly more informative than absence, which cannot
 # distinguish "checked, could not fit" from "not reached yet".
 _FIT_STATUS_ENUM = "enum('fitted','no_recording','unfittable')"
+# `timebase/segments.py::SEGMENT_ROLES`, which a test holds this to.
+_SEGMENT_ROLE_ENUM = "enum('session_time','no_barcode','after_restart_unbridged')"
 
 # `TimingProvenance` once compared the measured block boundary with wl.works'
 # assertion of it (`block_agreement`), within a tolerance kept here and later
@@ -119,6 +121,53 @@ class SessionClock(dj.Imported):
         self.insert1({**key, "source": choice.source, "reason": choice.reason})
 
 
+@schema
+class SyncBoxSegment(dj.Computed):
+    definition = f"""
+    # One segment of the session's sync box log: one run of the box's process,
+    # on a clock of its own (design spec
+    # `2026-10-22-syncbox-records-and-segments-design.md` section 6).
+    # Key: (subject, session_datetime, segment_index).
+    -> pipeline.Session
+    segment_index : int unsigned  # its header's, which orders a folder's segments
+    ---
+    file_path          : varchar(255)  # relative to the session's syncbox folder
+    boot_id            : varchar(255)  # as its header writes it
+    n_barcodes         : int unsigned  # its 'B' records
+    first_barcode=null : int unsigned  # null when it logged none
+    last_barcode=null  : int unsigned
+    clock_trusted      : tinyint(1)    # the box's judgement of its own wall clock, as written; acted on nowhere
+    clock_reason       : varchar(255)
+    role               : {_SEGMENT_ROLE_ENUM}
+    barcode_delay_us=null : double  # added to every 'B' tick; null while unmeasured and uncorrected (section 5)
+    """
+
+    @property
+    def key_source(self):
+        """Every landed session with a sync box. Imported locally, as
+        `SystemTimebase`'s is."""
+        from wl_preproc.schema import ingest
+
+        return pipeline.Session & ingest.Ingestion & (core.AcquisitionSystem & {"system": "syncbox"})
+
+    def make(self, key: dict) -> None:
+        from wl_preproc.schema import ingest
+        from wl_preproc.timebase import segments
+
+        session_dir = Path((ingest.Ingestion & key).fetch1("session_dir"))
+        logs = segments.syncbox_logs(session_dir)
+        self.insert([
+            {**key, "segment_index": log.segment_index,
+             "file_path": str(log.path.relative_to(session_dir / "syncbox")), "boot_id": log.boot_id,
+             "n_barcodes": len(log.barcodes),
+             "first_barcode": log.barcodes[0][0] if log.barcodes else None,
+             "last_barcode": log.barcodes[-1][0] if log.barcodes else None,
+             "clock_trusted": log.clock_trusted, "clock_reason": log.clock_reason, "role": role,
+             "barcode_delay_us": None}
+            for log, role in zip(logs, segments.segment_roles(logs), strict=True)
+        ])
+
+
 @schema
 class SystemTimebase(dj.Computed):
     definition = f"""
diff --git a/wl_preproc/timebase/segments.py b/wl_preproc/timebase/segments.py
index e5a4f46..8be6935 100644
--- a/wl_preproc/timebase/segments.py
+++ b/wl_preproc/timebase/segments.py
@@ -157,6 +157,25 @@ def session_log(session_dir: Path) -> SyncBoxLog:
     )
 
 
+#: A sync box segment's role, as `timebase.SyncBoxSegment` stores it (design
+#: spec `2026-10-22-syncbox-records-and-segments-design.md` section 6). Part 2
+#: adds a role for each bridge.
+SEGMENT_SESSION_TIME = "session_time"
+SEGMENT_NO_BARCODE = "no_barcode"
+SEGMENT_AFTER_RESTART = "after_restart_unbridged"
+SEGMENT_ROLES = (SEGMENT_SESSION_TIME, SEGMENT_NO_BARCODE, SEGMENT_AFTER_RESTART)
+
+
+def segment_roles(logs: list[SyncBoxLog]) -> list[str]:
+    """Each segment's role, in the order of `logs` (`syncbox_logs`'s): the
+    first holding a barcode keeps session time; any other holding one came
+    after a restart, on another clock, and waits for a bridge; one holding
+    none places nothing, before session time's segment or after it."""
+    timed = next((log for log in logs if log.barcodes), None)
+    return [SEGMENT_SESSION_TIME if log is timed else SEGMENT_AFTER_RESTART if log.barcodes else SEGMENT_NO_BARCODE
+            for log in logs]
+
+
 def session_reference(session_dir: Path) -> dict[int, float]:
     """Barcode value -> session-time seconds, from the sync box's own log.
 
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_syncbox_restart.py tests/timebase/test_syncbox_segments.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `13 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli/test_report.py tests/responder tests/schema/test_consensus_schema.py tests/schema/test_daemon.py tests/schema/test_daemon_skips_freed_sessions.py tests/schema/test_guardrails.py tests/schema/test_syncbox_restart.py tests/test_cli_guardrails.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `350 passed`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "the roles swapped",
  "wl_preproc/timebase/segments.py",
  "SEGMENT_AFTER_RESTART if log.barcodes else SEGMENT_NO_BARCODE",
  "SEGMENT_NO_BARCODE if log.barcodes else SEGMENT_AFTER_RESTART",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "a delay claimed",
  "wl_preproc/schema/timebase.py",
  "             \"barcode_delay_us\": None}",
  "             \"barcode_delay_us\": 0.0}",
  "tests/schema/test_syncbox_restart.py"
 ],
 [
  "the clock's trust not copied",
  "wl_preproc/schema/timebase.py",
  "\"clock_trusted\": log.clock_trusted,",
  "\"clock_trusted\": False,",
  "tests/schema/test_syncbox_restart.py"
 ],
 [
  "the last barcode is the first",
  "wl_preproc/schema/timebase.py",
  "\"last_barcode\": log.barcodes[-1][0] if log.barcodes else None,",
  "\"last_barcode\": log.barcodes[0][0] if log.barcodes else None,",
  "tests/schema/test_syncbox_restart.py"
 ],
 [
  "the daemon never runs it",
  "wl_preproc/daemon.py",
  "        timebase.SyncBoxSegment,\n        timebase.SystemTimebase,",
  "        timebase.SystemTimebase,",
  "tests/schema/test_syncbox_restart.py"
 ],
 [
  "the report lists the wrong role",
  "wl_preproc/cli/report.py",
  "(timebase.SyncBoxSegment & {\"role\": SEGMENT_AFTER_RESTART})",
  "(timebase.SyncBoxSegment & {\"role\": \"session_time\"})",
  "tests/schema/test_syncbox_restart.py"
 ]
]
```

Expected: 6 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(timebase): timebase.SyncBoxSegment records each segment of the sync box's log, its header and its role; the daily report names every session whose box restarted (sections 5 and 6)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 6: The records

The spec's amendments 1-6, made while proving this plan; wl-preproc's answers in `HANDOVER-wl-sync.md`; this
part's handoff; and the checkpoint's and `wl.yaml`'s lines (spec §10). Then the full suite, once, on both
interpreters.

**Files:**
- Modify: `docs/superpowers/specs/2026-10-22-syncbox-records-and-segments-design.md`, `HANDOVER-wl-sync.md`,
  `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-10-22-syncbox-records.md`

**Interfaces:** none; documents only.

- [ ] **Step 1: Apply the records.** Save this script outside the repository as `records.py` and run it from
  the repository root.

```python
"""Part 1's records: the spec's amendments 1-6, wl-preproc's answers in
HANDOVER-wl-sync.md, and the checkpoint's and wl.yaml's lines (built, not
merged; next, the review). Run from the repository root."""
import pathlib

root = pathlib.Path.cwd()


def swap(rel, old, new):
    path = root / rel
    text = path.read_text()
    assert text.count(old) == 1, (rel, old[:70])
    path.write_text(text.replace(old, new))


spec = root / "docs/superpowers/specs/2026-10-22-syncbox-records-and-segments-design.md"
spec.write_text(spec.read_text().rstrip("\n") + """

## Amendments, 2026-10-22, made while proving part 1's plan

1. **A segment's roles, as stored** on `timebase.SyncBoxSegment`: `session_time`, `no_barcode` and
   `after_restart_unbridged` (`timebase/segments.py::SEGMENT_ROLES`). The second is new to §6: a
   segment holding no barcode, before session time's segment (a box that crashed within its first
   second) or after it, places nothing.
2. **The synthetic log is named as the box names a segment**, `seg-000.log` (wl-sync's
   `segment.segment_name`). The restart fixture (`synth/recipe.py::SyncBoxRestartSpec`, its `at_s` and
   `gap_s`) writes `seg-001.log` on a tick origin that starts below the first segment's, so a reader that
   compared ticks by number would mix the two clocks, and drops every barcode emitted in the gap from every
   device, since the box emits none while down.
3. **The events stage failed on a restart too.** `timebase/clock.py::syncbox_codes` ran `fit_offset` on
   every sync box log, so a restart would have failed the events stage as it failed `Segment.make` (§4); it
   reads session time's segment alone.
4. **The pin is wl-sync's `main` at `27c0e97`**, the commit current when planned. Each environment
   installs it (`uv pip install --python <its python> "wl-sync @ git+…@27c0e97…"`); CI installs it from
   `pyproject.toml`.
5. **The report's restart section** (§6) names each later segment's first barcode and that barcode's time
   by the box's clock (a barcode is seconds since 2020-01-01 UTC, `wl_sync.clock.BARCODE_EPOCH`), and is
   live, as the reclamation list is: a segment part 2 bridges leaves it.
6. **One test's fixture is corrected, not its meaning:**
   `tests/timebase/test_clock.py::test_a_sync_box_log_with_no_barcode_is_the_clock_and_cannot_time_the_session`
   made its log barcode-less by removing `E` records; it removes `B` records.
""")

handover = root / "HANDOVER-wl-sync.md"
handover.write_text(handover.read_text().rstrip("\n") + """

## wl-preproc's answers, 2026-10-22

Designed in `docs/superpowers/specs/2026-10-22-syncbox-records-and-segments-design.md`, the requester's
decisions of 2026-10-20 and 2026-10-22; its part 1 is built on `feat/syncbox-records`, not merged.

1. **The pin moves** to your `main` at `27c0e97`, and `docs/schemas/syncbox_log_header.json` is
   regenerated from it. **The barcodes are your `B` records**, and nothing is read from the barcode pin;
   our synthetic logs are written as the box writes them, `B` records and `wl_sync.pins.GPIO_MAP`.
2. **Yes, tighten `SessionId.parse`.** Nothing here relies on the lenient form: `contracts/manifest.py`
   validates a manifest's `session_id` through it, and a non-canonical id fails our folder-name check
   anyway.
3. **Every segment is read as its own clock**, as your §3.4 expects. Session time comes from the first
   segment holding a barcode; a later segment, after a restart, gives it nothing until our part 2 bridges
   it, through a device that recorded through the restart or else the task PC's clock, through the codes
   both sides record. **Please confirm** that two segments in one session folder never share a clock: that
   a roll at an open or a close always changes folders.
4. **Two asks.**
   - **The measured barcode delay in each segment's header,** from a `B` tick to the frame's lead edge on
     the wire, as your bench loopback measures it; no value meaning unmeasured. The name and shape are
     yours. Until it is there we use each tick as it is, and record the delay as unmeasured and uncorrected.
   - **Records captured before a folder change in the closing segment.** Your §3.4 says records queued
     during a roll can land up to a second on the far side, so a session's last codes could land in
     `unassigned/`, which we never ingest.
5. **`clock_trusted` and `clock_reason`** are recorded per segment and acted on nowhere, as your note asks.
""")

swap("docs/CHECKPOINT.md",
     """> Next, by the requester's order of 2026-10-19: wl-sync's asks of the same day
> (`HANDOVER-wl-sync.md`; the pinned wl-sync cannot read the logs the box writes today, and
> a real log's barcodes are its `B` records, which nothing reads yet); then XC-398's spec
> and part 3b, the NI as the clock. Fine-tuning
""",
     """> **wl-sync's asks of 2026-10-19 are designed**
> (`specs/2026-10-22-syncbox-records-and-segments-design.md`, the requester's decisions of
> 2026-10-20 and 2026-10-22, in two parts), and **part 1, reading the box's logs as it writes
> them today, is built on `feat/syncbox-records`, not merged**
> (`handoffs/2026-10-22-syncbox-records.md`). Next: its review; then part 2, the bridges,
> XC-398's spec and part 3b, the NI as the clock. Fine-tuning
""")
swap("docs/CHECKPOINT.md",
     "> *Until the saccade fixtures' merge, this named wl-sync's asks and part 3b as next, without\n",
     "> *Until wl-sync's asks were built, this named them as next, unbuilt; true when written.*\n"
     ">\n"
     "> *Until the saccade fixtures' merge, this named wl-sync's asks and part 3b as next, without\n")
swap("wl.yaml",
     """    065da53). Next: wl-sync's asks of 2026-10-19
    (HANDOVER-wl-sync.md; the pinned wl-sync cannot read today's logs,
    and a real log's barcodes are its B records); then XC-398's spec
    and part 3b, the NI as the clock.
""",
     """    065da53). wl-sync's asks of 2026-10-19 are designed
    (docs/superpowers/specs/2026-10-22-syncbox-records-and-segments-design.md,
    two parts); part 1, reading the box's logs as it writes them today,
    is built on feat/syncbox-records and not merged
    (docs/handoffs/2026-10-22-syncbox-records.md); next, its review;
    then part 2, the bridges, XC-398's spec and part 3b, the NI as the
    clock.
""")
print("ok")
```

Expected: it prints `ok`.

- [ ] **Step 2: Write the handoff.** Its §5 keeps `{{...}}` slots until Step 5.

```bash
cat > docs/handoffs/2026-10-22-syncbox-records.md <<'HANDOFF'
# Reading the sync box's logs as it writes them today (part 1)

**Part 1** of `specs/2026-10-22-syncbox-records-and-segments-design.md`: wl-sync's asks of 2026-10-19
(`HANDOVER-wl-sync.md`). The wl-sync this repository pinned could not read the logs the box writes today,
and a real log's barcodes are its `B` records, which nothing here read: no real session could get a session
time. Part 2, the bridges across a sync box restart, follows.
- **Branch:** `feat/syncbox-records`, from `plan/syncbox-records`.
- **Spec:** the design and its amendments 1-6, made while proving the plan.
- **Plan:** `docs/superpowers/plans/2026-10-22-syncbox-records.md`.

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **The pin** moves to wl-sync's `main` at `27c0e97`; the exported header schema is regenerated.
- **The barcodes are the box's `B` records** (`timebase/_syncbox_log.py`), and synthetic logs are written as
  the box writes them: `B` records, the board's pin map, `seg-000.log`.
- **Each segment is its own clock.** Session time comes from the first segment holding a barcode
  (`timebase/segments.py::session_log`), and the events stage reads that segment alone.
- **A recording sharing no barcode with session time is rejected as `no_shared_barcode`**, where the stage
  used to fail: the box's segment after a restart, and any device recording begun after it.
- **`timebase.SyncBoxSegment`** records each segment: its header's fields, its barcodes, its role, and the
  barcode delay applied (null: unmeasured, uncorrected). The daily report names every session whose box
  restarted, which is tier D until part 2 bridges it.

## 2. What the other repositories must do

- **wl-sync:** read `HANDOVER-wl-sync.md`'s answers of 2026-10-22 once this merges: the pin moved; yes to
  tightening `SessionId.parse`; confirm that two segments in one session folder never share a clock; write
  the measured barcode delay into each segment's header; put records captured before a folder change into the
  closing segment.
- **wl.works:** nothing; no published shape changes.

## 3. Still open

- **Part 2,** the bridges: a device that recorded through a restart, else the task PC's clock through the
  codes both sides record; the task PC's is complete once wl-xcon's timed event log (its build C7) exists.
- **The barcode delay's correction,** once wl-sync writes it into the header and the pin moves again.

## 4. The rulings

The spec's §0 and its amendments 1-6.

## 5. What execution measured

Executed on {{DATE}}: Task 1 `{{SHA1}}`, Task 2 `{{SHA2}}`, Task 3 `{{SHA3}}`, Task 4 `{{SHA4}}`, Task 5
`{{SHA5}}`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **{{S311}}**;
  - 3.13: **{{S313}}**.
HANDOFF
```

- [ ] **Step 3: Check the manifest.**

```bash
.venv/bin/wl-check
```

Expected: `wl.yaml: no findings`.

- [ ] **Step 4: The full suite, once, on both interpreters,** in parallel, with the BMD and NSLR references
  set, as CI has them, the Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, each logged
  outside the repository:

```bash
R=$HOME/.cache/wl-preproc-references
LOG=$(mktemp -d)
unset WLPP_OHDPI_REFERENCE
suite() { env PYTHONPATH=$PWD PYTHONDONTWRITEBYTECODE=1 WLPP_NSLR_REFERENCE=$R WLPP_BMD_REFERENCE=$R/bmd/BMD \
  WLPP_BMD_BOOST_INCLUDE=$R/bmd/boost_1_86_0 WLPP_ANDERSSON_DATA=$R/andersson \
  "$1" -m pytest -q -p no:cacheprovider -o faulthandler_timeout=1800 > "$LOG/suite-$2.txt" 2>&1; echo "$2 exit $?"; }
suite .venv/bin/python 311 & suite $HOME/.cache/wl-preproc-venv313/bin/python 313 & wait
tail -1 $LOG/suite-311.txt $LOG/suite-313.txt
```

Expected, each exiting 0: 3.11, **2526 passed, 26 skipped, 1 deselected, 1 xfailed**; 3.13, **2526 passed, 27 skipped, 1 xfailed**. About 30 minutes. That is
18 tests more than `main`'s, this part's, and one skip fewer: `tests/responder/test_health.py`'s floor test
skips on a host with more than 800 GiB free, so on a fuller disk it passes instead. CI, without the Andersson
data, should show about 2516 passed, 37 skipped. A MySQL container that does not start within its 120 s under heavy load errors every
database test at once; that is the known flake, and the suite is run again.

- [ ] **Step 5: Fill the handoff's §5** with this execution's own figures: `{{DATE}}` the day,
  `{{SHA1}}` to `{{SHA5}}` Tasks 1-5's commits (`git log --oneline -6`), and `{{S311}}` and
  `{{S313}}` Step 4's two summary lines. Then `grep -c '{{' docs/handoffs/2026-10-22-syncbox-records.md`.

Expected: `0`.

- [ ] **Step 6: Commit.**

```bash
git add -A docs wl.yaml HANDOVER-wl-sync.md
git commit -F - <<'MSG'
docs: part 1's records -- spec amendments 1-6, wl-preproc's answers in HANDOVER-wl-sync.md, its handoff, and the checkpoint's and wl.yaml's lines: built, not merged; next, the review

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```
