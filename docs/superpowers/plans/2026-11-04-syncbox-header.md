# Reading wl-sync's Header Change Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Read the sync box's logs as it writes them since 2026-10-30: the pin moved; one clock per process; the
measured barcode delay added and each process's late first barcode dropped; the box's lost events stored, failing
a session where they cost a run's codes; code words on a split clock named in the daily report.

**Architecture:** The pin moves and the log's one reader carries the new header, the code words and the losses;
`timebase/segments.py::SyncBoxSession` reads a session's segments once, as clocks by process, and is what session
time, the box's scans, its codes and its losses come from; the synthesizer writes the box's log as the box does;
`timebase.SyncBoxSegment` and its `Loss` part record it, `TimingProvenance` checks it, and the report names it.

**Tech Stack:** Python 3.11/3.13, wl-sync, DataJoint 2.3.3 on MySQL (testcontainers in tests), pydantic.

**Spec:** `docs/superpowers/specs/2026-11-04-syncbox-header-design.md`: §0-§13 and its five rulings, approved
2026-11-06; amendments 1-10 are Task 6's, made while proving this plan.

**Proven before written.** Every step below was run on a scratch branch, with wl-sync `fe1ea63` on the path as
Task 1 installs it, and replayed from this plan's own text to an identical tree: 56 mutation checks
caught, and the full suite on both interpreters as Task 6 states.

## Global Constraints

- **The pin is wl-sync's `main` at `fe1ea633972df9590dbe1974aeb66dc3a1eb20ad`** (§3), installed in both environments in Task 1, before
  any other task's test runs.
- **A clock is a process** (§4): segments naming one `process_uuid` are one clock; a log naming none is a process
  of its own; a contradicted clock is read as a restart (decision 3.1); every segment of session time's clock is
  `session_time` (decision 3.2).
- **The barcode delay is added to every `B` tick** in the reader, the one place; none leaves each tick as it is
  (decision 3.4). **Each process's earliest segment's first `B` is dropped**, t = 0 the first kept; a log naming
  no process keeps its first (amendment 1).
- **A `desync`, or code words lost or a gap inside a measured run, fail the timing check (tier D); a lost edge is
  reported only** (decision 3.5). **Code words on a split clock are recorded and reported, never corrected**
  (decision 6).
- **One schema change:** `timebase.SyncBoxSegment` gains four nullable columns and a part, `Loss`; a database
  declared before is refused with its `ALTER TABLE` named, and nothing here runs it.
- **Synthetic logs are the box's** (§10): the fixture is corrected, never the design bent to it.
- **Execute on a branch `feat/syncbox-header`** cut from `plan/syncbox-header` once this plan is committed there.
- **Python 3.11 and 3.13**: the full suite runs once on each, at the end (Task 6).
- **A test that lands a session deletes it**, so no later module's unrestricted populate meets it.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code,
  data or tables; leave the stray `wl-preproc` symlink at the repository root out of every commit.
- **Commands run from the repository root.** In a git worktree, `.venv/bin/python` is the main checkout's;
  every test command sets `PYTHONPATH=$PWD` and `PYTHONDONTWRITEBYTECODE=1`.
- **Patches apply with `git apply` and must apply cleanly**; `docs/schemas` is regenerated with
  `schemas export`, never edited.
- **Neighbour runs name their files in the suite's order.**
- **Every commit ends with:**
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
  ```

## Review Focus

The five inputs the spec implies that a person is most likely to meet, most likely first; each is pinned by a
test in the task that owns the code:
1. **A box that rolls in place when NTP first syncs, during a session:** one clock, nothing rejected, every code
   placed, the tier as without the roll. `test_a_roll_in_place_may_skip_one_value` (Task 2),
   `test_a_roll_in_place_rejects_nothing_and_both_segments_keep_session_time` and
   `test_a_roll_in_place_places_every_code_and_tiers_the_session_as_without_it` (Task 3).
2. **A log written before 2026-10-30,** naming no process, delay or clocks: read as part 1 read it.
   `test_an_older_log_is_read_with_its_ticks_as_they_are` (Task 1),
   `test_each_processs_late_first_barcode_is_dropped_and_an_older_logs_is_kept` (Task 2).
3. **A database declared before this change:** the stage refuses and names its `ALTER TABLE`, which restores the
   columns as DataJoint declares them. `test_a_database_without_the_header_columns_records_nothing_and_names_the_statement`,
   `test_the_statement_restores_the_columns_as_datajoint_declares_them` (Task 4).
4. **A box that lost code words or edges:** a `desync`, or a gap in a run, fail the session; a lost edge does not.
   `test_a_loss_fails_the_timing_check_where_it_can_have_cost_a_runs_codes` and
   `test_a_desync_or_a_gap_in_a_run_fails_the_timing_check_and_a_lost_edge_does_not` (Task 4).
5. **A box whose barcode delay has been measured:** session time stays where the devices see it.
   `test_the_barcode_delay_is_added_to_every_barcode_tick` (Task 1),
   `test_a_measured_barcode_delay_leaves_session_time_where_the_devices_see_it` (Task 3).

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
| `pyproject.toml`, `wl.yaml`, `docs/schemas/syncbox_log_header.json` | the pin | 1 |
| `wl_preproc/timebase/_syncbox_log.py` | the log's one reader: the header, the delay, the code words and losses; read once | 1, 2 |
| `wl_preproc/timebase/segments.py`, `timebase/extract.py`, `timebase/clock.py` | clocks by process; session time, the box's scans and codes from its records; its losses and the timing rule | 2, 4 |
| `wl_preproc/synth/recipe.py`, `synth/timeline.py`, `synth/syncbox.py` | synthetic logs as the box writes them | 3 |
| `wl_preproc/schema/timebase.py` | the matched count; `SyncBoxSegment`, its `Loss`, its guard; `TimingProvenance`'s check | 2, 4 |
| `wl_preproc/cli/report.py` | the losses, a split clock, a contradiction | 5 |
| the spec, `HANDOVER-wl-sync.md`, `docs/handoffs/2026-11-04-syncbox-header.md` (new), `docs/CHECKPOINT.md`, `wl.yaml` | the records | 6 |

---

### Task 1: The wl-sync pin moves, and the reader carries the new header

§1, §3 and §5's delay: the wl-sync pinned until now (`27c0e97`) refuses the box's headers and its `L` records since 2026-10-30. The pin moves to wl-sync's `main` at `fe1ea63`, in `pyproject.toml` and `wl.yaml`; both environments install it; the exported header schema is regenerated. The log's one reader carries each segment's `process_uuid`, `barcode_delay_us` and `timebase`, adds the delay to every barcode tick, keeps the code words and the losses, and names a file it cannot read; the barcode epoch is wl-sync's own (part 1's deferred findings).

**Files:**
- Modify: `pyproject.toml`, `wl.yaml` (the wl-sync commit and its `why`)
- Modify: `docs/schemas/syncbox_log_header.json` (exported)
- Modify: `wl_preproc/timebase/_syncbox_log.py`, `timebase/clock.py` (the epoch), `timebase/_rhs_header.py` (a comment)
- Test: `tests/cli/test_schemas_export.py`, `tests/nwb/test_clock.py`, `tests/timebase/test_syncbox_log.py`

**Interfaces:**
- Consumes: wl-sync at `fe1ea63`: `wl_sync.log.Loss`, `LOSS_SHAPES`; `SyncBoxLogHeader.process_uuid`,
  `.barcode_delay_us`, `.timebase`; `wl_sync.segment.SegmentWriter`; `wl_sync.clock.BARCODE_EPOCH`.
- Produces: `_syncbox_log.SyncBoxLog` gains `process_uuid: str | None`, `barcode_delay_us: int | None`,
  `timebase: str | None`, `codes: tuple[tuple[int, int], ...]` (`(tick_us, word)`) and
  `losses: tuple[Loss, ...]`; its `barcodes` are `(value, tick_us)` as on the wire, the delay added;
  `read_syncbox_log(path)` raises `ValueError` naming the path.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/cli/test_schemas_export.py b/tests/cli/test_schemas_export.py
index e8922da..12a7077 100644
--- a/tests/cli/test_schemas_export.py
+++ b/tests/cli/test_schemas_export.py
@@ -139,3 +139,17 @@ def test_the_committed_syncbox_header_is_the_one_wl_sync_writes_today(tmp_path):
     exported = json.loads((tmp_path / "syncbox_log_header.json").read_text())
     assert json.loads(committed.read_text()) == exported
     assert {"segment_index", "ordering", "clock_trusted", "clock_reason"} <= set(exported["properties"])
+
+
+def test_the_committed_syncbox_header_names_each_segments_process_delay_and_clocks(tmp_path):
+    """The box's headers since 2026-10-30 name the process that wrote the
+    segment, the measured barcode delay and the clocks its ticks count, which
+    the wl-sync pinned until then refused (design spec
+    `2026-11-04-syncbox-header-design.md` section 1)."""
+    from pathlib import Path
+
+    committed = Path(__file__).resolve().parents[2] / "docs" / "schemas" / "syncbox_log_header.json"
+    export_schemas(tmp_path)
+    exported = json.loads((tmp_path / "syncbox_log_header.json").read_text())
+    assert json.loads(committed.read_text()) == exported
+    assert {"process_uuid", "barcode_delay_us", "timebase"} <= set(exported["properties"])
diff --git a/tests/nwb/test_clock.py b/tests/nwb/test_clock.py
index c94ce4d..814f9a7 100644
--- a/tests/nwb/test_clock.py
+++ b/tests/nwb/test_clock.py
@@ -5,8 +5,6 @@ from __future__ import annotations
 
 import datetime
 
-import pytest
-
 
 def test_the_first_barcode_places_t0_unless_the_clocks_disagree():
     """Section 4.2. Within 60 s of the manifest the barcode wins; beyond it
@@ -26,10 +24,12 @@ def test_the_first_barcode_places_t0_unless_the_clocks_disagree():
     assert far["reference_time"] == started.replace(tzinfo=datetime.timezone.utc)
 
 
-def test_the_restated_barcode_epoch_is_wl_syncs():
-    """Pinned equal wherever wl-sync's own `clock` module is installed; the
-    commit this repository pins predates it."""
-    clock = pytest.importorskip("wl_sync.clock")
+def test_the_barcode_epoch_is_wl_syncs_own():
+    """One definition, wl-sync's: the pinned wl-sync carries its `clock`
+    module, so nothing here restates it (part 1's deferred finding)."""
+    from wl_sync import clock
+
     from wl_preproc.nwb.gather import BARCODE_EPOCH
+    from wl_preproc.timebase import clock as timebase_clock
 
-    assert BARCODE_EPOCH == clock.BARCODE_EPOCH
+    assert BARCODE_EPOCH is clock.BARCODE_EPOCH is timebase_clock.BARCODE_EPOCH
diff --git a/tests/timebase/test_syncbox_log.py b/tests/timebase/test_syncbox_log.py
index 2a7178d..8e1a936 100644
--- a/tests/timebase/test_syncbox_log.py
+++ b/tests/timebase/test_syncbox_log.py
@@ -1,40 +1,56 @@
-"""The sync box's barcodes are read from its `B` records, as the box writes
-them today (design spec `2026-10-22-syncbox-records-and-segments-design.md`
-section 3): the box never captures its own barcode line, so nothing is read
-from it, whatever a log's edges hold."""
+"""The sync box's log is read as the box writes it today (design specs
+`2026-10-22-syncbox-records-and-segments-design.md` section 3 and
+`2026-11-04-syncbox-header-design.md` sections 3 and 5): the barcodes are its
+`B` records, with the measured delay added, and nothing is read from its
+barcode line; the header's fields, the code words and the losses are
+carried."""
 
 from __future__ import annotations
 
 import datetime
 
+import pytest
 from wl_sync.barcode import encode
-from wl_sync.log import SCHEMA_VERSION, BarcodeEmitted, CodeWord, Edge, SyncBoxLogHeader, write_log
+from wl_sync.log import SCHEMA_VERSION, BarcodeEmitted, CodeWord, Edge, Loss, SyncBoxLogHeader, write_log
 from wl_sync.pins import BARCODE_PIN, GPIO_MAP
+from wl_sync.segment import SegmentWriter
 
-from wl_preproc.timebase._syncbox_log import read_barcode_entries
+from wl_preproc.timebase._syncbox_log import read_barcode_entries, read_syncbox_log
 from wl_preproc.timebase.extract import extract_syncbox
 
+PROCESS = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
+WRITTEN_AT = datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC)
 
-def _real_shaped_log(path):
-    """Written by wl-sync's own writer: the board's pin map, a photodiode's
-    edges, code words, three barcodes the box emitted, and a trailer. Edges on
-    the barcode pin carry a different value, which a reader of that pin would
-    report and a reader of the box's records never does."""
-    header = SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id="2027-03-14_01", rig="rig0",
-                              boot_id="b0", written_at=datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC),
-                              gpio_map=GPIO_MAP, segment_index=0, clock_trusted=False,
-                              clock_reason="ntp_unsynchronized")
+
+def _header(**fields) -> SyncBoxLogHeader:
+    return SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id="2027-03-14_01", rig="rig0", boot_id="b0",
+                            written_at=WRITTEN_AT, gpio_map=GPIO_MAP, **fields)
+
+
+def _real_shaped_log(path, **fields):
+    """Written by wl-sync's own segment writer, trailer and all: the board's
+    pin map, a photodiode's edges, code words, a loss on each path, and three
+    barcodes the box emitted. Edges on the barcode pin carry a different value,
+    which a reader of that pin would report and a reader of the box's records
+    never does."""
+    header = _header(segment_index=0, clock_trusted=False, clock_reason="ntp_unsynchronized", **fields)
     records = [Edge(tick_us=5_000_000, gpio=GPIO_MAP["pd1_comp"], level=1),
-               CodeWord(tick_us=5_100_000, word=0x8001)]
+               CodeWord(tick_us=5_100_000, word=0x8001),
+               Loss(tick_us=5_200_000, path="E", gpio=GPIO_MAP["pd1_comp"], count=2, bound="exact",
+                    cause="buffer_overflow", before_us=5_000_000, after_us=5_200_000),
+               CodeWord(tick_us=5_300_000, word=0x0042),
+               Loss(tick_us=5_300_000, path="W", gpio=None, count=3, bound="at_least", cause="desync",
+                    before_us=5_300_000, after_us=None)]
     tick = 9_000_000
     for level, duration_us in encode(4242):
         records.append(Edge(tick_us=tick, gpio=BARCODE_PIN, level=level))
         tick += duration_us
     records += [BarcodeEmitted(tick_us=t, value=v)
                 for t, v in ((2_000_000, 912_345_678), (3_000_000, 912_345_679), (4_000_000, 912_345_680))]
-    write_log(path, header, records)
-    with path.open("a", encoding="utf-8") as handle:
-        handle.write("T,4500000,clean\n")
+    writer = SegmentWriter(path, header)
+    for record in records:
+        writer.write(record)
+    writer.close(WRITTEN_AT + datetime.timedelta(seconds=10))
     return path
 
 
@@ -56,8 +72,50 @@ def test_the_sync_box_stream_decodes_to_the_boxs_records(tmp_path):
 
 def test_a_log_with_no_barcode_record_has_no_barcodes(tmp_path):
     path = tmp_path / "seg-000.log"
-    header = SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id="2027-03-14_01", rig="rig0",
-                              boot_id="b0", written_at=datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC),
-                              gpio_map=GPIO_MAP)
-    write_log(path, header, [CodeWord(tick_us=1_000_000, word=0x8001)])
+    write_log(path, _header(), [CodeWord(tick_us=1_000_000, word=0x8001)])
     assert read_barcode_entries(path) == []
+
+
+def test_the_reader_carries_what_the_header_says(tmp_path):
+    """Its process, its measured delay and the clocks its ticks count, beside
+    the clock's trust as the box judged it (part 1's deferred finding: nothing
+    pinned that these two were carried)."""
+    log = read_syncbox_log(_real_shaped_log(tmp_path / "seg-000.log", process_uuid=PROCESS,
+                                            barcode_delay_us=1_234, timebase="split"))
+    assert (log.segment_index, log.boot_id, log.process_uuid, log.barcode_delay_us, log.timebase) == (
+        0, "b0", PROCESS, 1_234, "split")
+    assert (log.clock_trusted, log.clock_reason) == (False, "ntp_unsynchronized")
+
+
+def test_the_barcode_delay_is_added_to_every_barcode_tick(tmp_path):
+    """wl-sync's `barcode_delay_us`: "ADD IT TO A `B` TICK to get the wire
+    time". Code words and losses are ticks of their own paths, unchanged."""
+    log = read_syncbox_log(_real_shaped_log(tmp_path / "seg-000.log", process_uuid=PROCESS,
+                                            barcode_delay_us=1_234))
+    assert log.barcodes == ((912_345_678, 2_001_234), (912_345_679, 3_001_234), (912_345_680, 4_001_234))
+    assert log.codes == ((5_100_000, 0x8001), (5_300_000, 0x0042))
+
+
+def test_an_older_log_is_read_with_its_ticks_as_they_are(tmp_path):
+    """A header from before 2026-10-30 names none of the three, and an
+    unmeasured delay is not zero: each tick is used as it is."""
+    log = read_syncbox_log(_real_shaped_log(tmp_path / "seg-000.log"))
+    assert (log.process_uuid, log.barcode_delay_us, log.timebase) == (None, None, None)
+    assert log.barcodes[0] == (912_345_678, 2_000_000)
+
+
+def test_the_reader_carries_each_loss_as_the_box_wrote_it(tmp_path):
+    log = read_syncbox_log(_real_shaped_log(tmp_path / "seg-000.log", process_uuid=PROCESS))
+    assert [(loss.path, loss.cause, loss.count, loss.bound, loss.gpio, loss.before_us, loss.after_us)
+            for loss in log.losses] == [
+        ("E", "buffer_overflow", 2, "exact", GPIO_MAP["pd1_comp"], 5_000_000, 5_200_000),
+        ("W", "desync", 3, "at_least", None, 5_300_000, None)]
+
+
+def test_an_unreadable_segment_is_named(tmp_path):
+    """Part 1's deferred finding: the error named nothing, so a session whose
+    timing stages all refused gave no file to look at."""
+    path = tmp_path / "seg-003.log"
+    path.write_text('{"schema_version": 1, "session_id": "not a session"}\nB,1000000,7\n', encoding="utf-8")
+    with pytest.raises(ValueError, match="seg-003.log"):
+        read_syncbox_log(path)
PATCH
```

- [ ] **Step 2: Move the pin, and install it in both environments.**

```bash
git apply <<'PATCH'
diff --git a/pyproject.toml b/pyproject.toml
index 99e6729..df6f0d5 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -28,8 +28,11 @@ dependencies = [
     # this repo at all. Resolved 2026-08-14 with `git ls-remote <url> main`;
     # moved 2026-10-22 to wl-sync's main of that day, because the box's logs
     # since 2026-08-17 carry header fields and `B`/`T` records the old pin
-    # refused (`HANDOVER-wl-sync.md`, 2026-10-19).
-    "wl-sync @ git+https://github.com/jakewesterberg/wl-sync.git@27c0e9780a388cbc120ecfadcb54b6f288740a98",  # main
+    # refused (`HANDOVER-wl-sync.md`, 2026-10-19); moved again to its main of
+    # 2026-10-30, because the box's logs since then carry three header fields
+    # and `L` records that pin refused (design spec
+    # `2026-11-04-syncbox-header-design.md`).
+    "wl-sync @ git+https://github.com/jakewesterberg/wl-sync.git@fe1ea633972df9590dbe1974aeb66dc3a1eb20ad",  # main
     "datajoint>=2.3,<3",
     # `wl_preproc/eye/ohdpi.py` imports pandas directly, for `read_ohdpi`'s
     # column-selective CSV reads. It already arrives unconditionally with
diff --git a/wl.yaml b/wl.yaml
index eb65eee..a75e880 100644
--- a/wl.yaml
+++ b/wl.yaml
@@ -549,7 +549,7 @@ third_party:
 # something here.
 requires:
   - name: wl-sync
-    pinned_at: 27c0e9780a388cbc120ecfadcb54b6f288740a98
+    pinned_at: fe1ea633972df9590dbe1974aeb66dc3a1eb20ad
     for: session identity, the barcode codec, and the log format
     why: >-
       The sync box owns everything it produces, and vendoring copies here would
@@ -564,7 +564,11 @@ requires:
       wl-sync's main of that day: the box's logs since 2026-08-17 carry header
       fields (segment_index, ordering, clock_trusted, clock_reason) and B and T
       records the old pin refused, so no real log could be read
-      (HANDOVER-wl-sync.md, 2026-10-19).
+      (HANDOVER-wl-sync.md, 2026-10-19). Moved again from 27c0e97 to
+      wl-sync's main of 2026-10-30 (fe1ea63): the box's logs since then carry
+      header fields (process_uuid, barcode_delay_us, timebase) and L records
+      that pin refused, and wl-sync holds the box's deploy until this pin has
+      moved (docs/superpowers/specs/2026-11-04-syncbox-header-design.md).
 
 publishes:
   - name: session-manifest
PATCH
```

```bash
uv pip install --python .venv/bin/python "wl-sync @ git+https://github.com/jakewesterberg/wl-sync.git@fe1ea633972df9590dbe1974aeb66dc3a1eb20ad"
uv pip install --python $HOME/.cache/wl-preproc-venv313/bin/python "wl-sync @ git+https://github.com/jakewesterberg/wl-sync.git@fe1ea633972df9590dbe1974aeb66dc3a1eb20ad"
.venv/bin/python -c "import wl_sync.log as log; print(log.Loss.__name__)"
$HOME/.cache/wl-preproc-venv313/bin/python -c "import wl_sync.log as log; print(log.Loss.__name__)"
```

Expected: `Loss`, twice.

- [ ] **Step 3: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli/test_schemas_export.py tests/nwb/test_clock.py tests/timebase/test_syncbox_log.py -q -p no:cacheprovider
```

Expected: `8 failed, 17 passed`

- [ ] **Step 4: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/timebase/_rhs_header.py b/wl_preproc/timebase/_rhs_header.py
index 7e5a154..4d50277 100644
--- a/wl_preproc/timebase/_rhs_header.py
+++ b/wl_preproc/timebase/_rhs_header.py
@@ -26,9 +26,9 @@ import struct
 from pathlib import Path
 
 # Intan's own format identifier, and the vendor document's, not this project's.
-# It is restated rather than imported from `wl_preproc.synth.rhs_header` for
-# the reason `_syncbox_log.py` restates its gpio key: a production reader has
-# no business depending on the synthetic fixture generator. The direction of
+# It is restated rather than imported from `wl_preproc.synth.rhs_header`: a
+# production reader has no business depending on the synthetic fixture
+# generator. The direction of
 # the check matters too -- January's real files are written by Intan, not by
 # that emitter, so agreeing with the emitter is not the property under test.
 _MAGIC = 0xD69127AC
diff --git a/wl_preproc/timebase/_syncbox_log.py b/wl_preproc/timebase/_syncbox_log.py
index 599e4ba..9057a08 100644
--- a/wl_preproc/timebase/_syncbox_log.py
+++ b/wl_preproc/timebase/_syncbox_log.py
@@ -2,19 +2,25 @@
 
 `wl_sync.log` defines the on-disk shape: a JSON header line, then one CSV
 line per record, type-tagged 'E' (a transition on an input line), 'W' (a code
-word latched on a strobe edge), 'B' (a barcode the box emitted) or 'T' (a
-segment's trailer, which `read_log` skips). **The barcodes are the 'B'
-records**: the box never captures its own barcode line, which is an output
-(wl-sync's `pins.py`, GPIO17; its edge inputs are GPIO20-27), and logs each
-frame it emits instead, "the emitter's own account of what it put on the wire,
-not a measurement of it" (`wl_sync/log.py`). Until 2026-10-22 this decoded
-edges on the line the header's `gpio_map` named `barcode_out`, the shape only
-this project's synthetic logs had: a real log yielded no barcodes (design spec
+word latched on a strobe edge), 'B' (a barcode the box emitted), 'L' (capture
+events a path saw and could not record) or 'T' (a segment's trailer, which
+`read_log` skips). **The barcodes are the 'B' records**: the box never
+captures its own barcode line, which is an output (wl-sync's `pins.py`,
+GPIO17; its edge inputs are GPIO20-27), and logs each frame it emits instead,
+"the emitter's own account of what it put on the wire, not a measurement of
+it" (`wl_sync/log.py`). Until 2026-10-22 this decoded edges on the line the
+header's `gpio_map` named `barcode_out`, the shape only this project's
+synthetic logs had: a real log yielded no barcodes (design spec
 `2026-10-22-syncbox-records-and-segments-design.md` section 1).
 
 A 'B' tick is read before the frame is sent, so it leads the frame's edge on
-the wire by a delay not yet measured; it is used as it is, and recorded as
-uncorrected (that spec's section 5).
+the wire by `barcode_delay_us`, which the box's header carries once its bench
+loopback has measured it: "ADD IT TO A `B` TICK to get the wire time". It is
+added here, the one place, and a header without it (unmeasured, or written
+before 2026-10-30) leaves each tick as it is (design spec
+`2026-11-04-syncbox-header-design.md` section 5). The code words ('W') and
+the losses ('L') are carried as written; the edges ('E') are read by no stage
+here and are not kept.
 
 Kept in its own module, separate from `extract.py`, so the log format's shape
 has exactly one reader.
@@ -25,35 +31,57 @@ from __future__ import annotations
 from dataclasses import dataclass
 from pathlib import Path
 
-from wl_sync.log import BarcodeEmitted, read_log
+from wl_sync.log import BarcodeEmitted, CodeWord, Loss, read_log
 
 
 @dataclass(frozen=True, slots=True)
 class SyncBoxLog:
-    """One segment of the sync box's log: one run of its process, on a clock
-    of its own (wl-sync's `segment.py`), with what its header says of it and
-    the barcodes it logged emitting, `(value, tick_us)`."""
+    """One segment of the sync box's log, with what its header says of it:
+    the process that wrote it (`process_uuid`, None before 2026-10-30), the
+    barcode delay added to its barcodes (None while unmeasured), the clocks
+    its ticks count (`timebase`), and its barcodes `(value, tick_us)` as on
+    the wire, its code words `(tick_us, word)` and its losses, each in the
+    order the log holds them."""
 
     path: Path
     segment_index: int
     boot_id: str
     clock_trusted: bool
     clock_reason: str
+    process_uuid: str | None
+    barcode_delay_us: int | None
+    timebase: str | None
     barcodes: tuple[tuple[int, int], ...]
+    codes: tuple[tuple[int, int], ...]
+    losses: tuple[Loss, ...]
 
 
 def read_syncbox_log(path: Path) -> SyncBoxLog:
-    header, records = read_log(path)
+    """One segment, or `ValueError` naming it: a header or a record the
+    format does not define (`wl_sync.log.read_log` validates both) refuses
+    every stage that reads the session's sync box, and the file is what a
+    person must open (design spec `2026-11-04-syncbox-header-design.md`
+    section 3)."""
+    try:
+        header, records = read_log(path)
+    except ValueError as error:
+        raise ValueError(f"{path}: the sync box log cannot be read, so its session's timing stages refuse it: "
+                         f"{error}") from error
+    delay_us = header.barcode_delay_us or 0
     return SyncBoxLog(
         path=path, segment_index=header.segment_index, boot_id=header.boot_id,
         clock_trusted=header.clock_trusted, clock_reason=header.clock_reason,
-        barcodes=tuple((record.value, record.tick_us) for record in records if isinstance(record, BarcodeEmitted)),
+        process_uuid=header.process_uuid, barcode_delay_us=header.barcode_delay_us, timebase=header.timebase,
+        barcodes=tuple((record.value, record.tick_us + delay_us) for record in records
+                       if isinstance(record, BarcodeEmitted)),
+        codes=tuple((record.tick_us, record.word) for record in records if isinstance(record, CodeWord)),
+        losses=tuple(record for record in records if isinstance(record, Loss)),
     )
 
 
 def read_barcode_entries(path: Path) -> list[tuple[int, int]]:
     """`(value, tick_us)` for every barcode the box logged emitting, in the
-    order the log holds them.
+    order the log holds them, each tick as on the wire.
 
     `wl_sync.log.read_log` validates the header as it parses it (a malformed
     header raises there, via `SyncBoxLogHeader`'s own pydantic validators),
diff --git a/wl_preproc/timebase/clock.py b/wl_preproc/timebase/clock.py
index 7a0a5ef..8d6c1f1 100644
--- a/wl_preproc/timebase/clock.py
+++ b/wl_preproc/timebase/clock.py
@@ -19,6 +19,10 @@ import dataclasses
 import datetime
 from pathlib import Path
 
+# A barcode's value is whole seconds since wl-sync's `BARCODE_EPOCH`, off the
+# sync box's wall clock; read from here by `nwb/gather.py` too.
+from wl_sync.clock import BARCODE_EPOCH
+
 from wl_preproc.events.extract import extract_syncbox_words
 from wl_preproc.timebase import segments
 from wl_preproc.timebase.extract import find_recordings
@@ -31,12 +35,6 @@ SOURCES = ("syncbox", "nidq", "lab")
 # (the NWB builder spec section 4.2). Borrowed from wl-sync's
 # CLOCK_TRUST_TOLERANCE_S.
 CLOCK_DISAGREEMENT_S = 60.0
-# A barcode's value is whole seconds since this instant, off the sync box's
-# wall clock: wl-sync's `wl_sync/clock.py::BARCODE_EPOCH`, since its commit
-# 3ce66b9 (2026-08-17). RESTATED, not imported: the wl-sync commit this
-# repository pins predates that module. `tests/nwb/test_clock.py` pins the
-# two equal wherever a newer wl-sync is installed.
-BARCODE_EPOCH = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
 
 
 class NoClock(Exception):
PATCH
```

- [ ] **Step 5: Export the schemas.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas
git status --short docs/schemas
```

Expected: ` M docs/schemas/syncbox_log_header.json`, and no other file.

- [ ] **Step 6: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli/test_schemas_export.py tests/nwb/test_clock.py tests/timebase/test_syncbox_log.py -q -p no:cacheprovider
```

Expected: `25 passed`

- [ ] **Step 7: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli/test_schemas_export.py tests/contracts tests/events tests/ingest tests/nwb tests/synth tests/timebase -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `726 passed, 1 skipped`

- [ ] **Step 8: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "delay-not-added",
  "wl_preproc/timebase/_syncbox_log.py",
  "record.tick_us + delay_us",
  "record.tick_us",
  "tests/timebase/test_syncbox_log.py"
 ],
 [
  "losses-not-carried",
  "wl_preproc/timebase/_syncbox_log.py",
  "losses=tuple(record for record in records if isinstance(record, Loss)),",
  "losses=(),",
  "tests/timebase/test_syncbox_log.py"
 ],
 [
  "codes-not-carried",
  "wl_preproc/timebase/_syncbox_log.py",
  "codes=tuple((record.tick_us, record.word) for record in records if isinstance(record, CodeWord)),",
  "codes=(),",
  "tests/timebase/test_syncbox_log.py"
 ],
 [
  "process-not-carried",
  "wl_preproc/timebase/_syncbox_log.py",
  "process_uuid=header.process_uuid,",
  "process_uuid=None,",
  "tests/timebase/test_syncbox_log.py"
 ],
 [
  "unreadable-file-not-named",
  "wl_preproc/timebase/_syncbox_log.py",
  "raise ValueError(f\"{path}: the sync box log cannot be read",
  "raise ValueError(f\"the sync box log cannot be read",
  "tests/timebase/test_syncbox_log.py"
 ],
 [
  "epoch-restated",
  "wl_preproc/timebase/clock.py",
  "from wl_sync.clock import BARCODE_EPOCH\n",
  "BARCODE_EPOCH = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)\n",
  "tests/nwb/test_clock.py"
 ],
 [
  "committed-header-schema-stale",
  "docs/schemas/syncbox_log_header.json",
  "\"process_uuid\": {",
  "\"process_uuidx\": {",
  "tests/cli/test_schemas_export.py"
 ]
]
```

Expected: 7 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 9: Commit.**

```bash
git add -A tests wl_preproc docs pyproject.toml wl.yaml
git commit -F - <<'MSG'
build: the wl-sync pin moves to its main of 2026-10-30 (fe1ea63), and the log's reader carries what its header now says -- each segment's process, the measured barcode delay (added to every barcode tick) and the clocks its ticks count -- with its code words and its losses; an unreadable segment is named; the barcode epoch is wl-sync's own (design spec 2026-11-04-syncbox-header-design.md, sections 3 and 5)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 2: A clock is a process, not a segment

§4, §5 and §9: one process's segments are one clock, each boundary checked (adjacent, a gap, or a contradiction read as a restart, decision 3.1); every segment of session time's clock keeps session time (decision 3.2); each process's late first barcode is dropped and t = 0 is the first kept, an older log's kept (amendment 1); every reader of the box's barcodes takes its `B` records as they are, with no edges rendered and decoded back; each log is read once; a segment of session time's clock that no barcode places is not scanned (amendment 3); the box counts as matched only the barcodes session time holds (amendment 8). Synthetic logs still name no process until Task 3, so every synthetic session reads as before.

**Files:**
- Modify: `wl_preproc/timebase/segments.py` (`SyncBoxSession`, `read_syncbox_session`, `syncbox_scans`, `scan_system`'s sync box; `session_log` goes)
- Modify: `wl_preproc/timebase/_syncbox_log.py` (read once), `timebase/extract.py` (`extract_syncbox`, `syncbox_stream`), `timebase/clock.py` (`syncbox_codes`), `schema/timebase.py` (`SystemTimebase`'s matched count)
- Test: `tests/schema/test_syncbox_restart.py`, `tests/timebase/test_extract.py`, `tests/timebase/test_fit.py`, `tests/timebase/test_syncbox_log.py`, `tests/timebase/test_syncbox_segments.py`

**Interfaces:**
- Consumes: Task 1's `SyncBoxLog`.
- Produces: `segments.SyncBoxSession` (`logs`, `clocks`, `follows`, `kept`, `timed`; `from_logs(logs)`,
  `in_session_time(index)`, `origin_us`, `seconds(tick_us)`, `reference()`, `codes()`, `gaps()`, `roles()`);
  `FOLLOWS_ADJACENT`, `FOLLOWS_GAP`, `FOLLOWS_CONTRADICTION`; `read_syncbox_session(session_dir)`;
  `syncbox_scans(session_dir) -> list[RecordingScan]`; `extract.syncbox_stream(log) -> BitStream`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/schema/test_syncbox_restart.py b/tests/schema/test_syncbox_restart.py
index 769ce88..d8e6d79 100644
--- a/tests/schema/test_syncbox_restart.py
+++ b/tests/schema/test_syncbox_restart.py
@@ -63,6 +63,18 @@ def test_a_session_whose_box_restarted_is_tier_d(restarted):
     assert (timebase.TimingProvenance & key).fetch1("tier") == "D"
 
 
+def test_the_sync_box_counts_as_matched_only_the_barcodes_session_time_holds(restarted):
+    """Its identity fit counted the later process's barcodes as matched too,
+    though nothing places them until a bridge does (part 1's deferred
+    finding)."""
+    from wl_preproc.schema import timebase
+    from wl_preproc.timebase.segments import session_reference
+
+    key, session_dir, _ = restarted
+    fit = (timebase.SystemTimebase & key & {"system": "syncbox"}).fetch1()
+    assert fit["n_barcodes_matched"] == len(session_reference(session_dir)) < fit["n_barcodes_decoded"]
+
+
 def test_each_segment_of_the_box_is_recorded_with_its_role(restarted):
     """`timebase.SyncBoxSegment` (section 6): what each segment's header says,
     its barcodes, and its role. The barcode delay is null, unmeasured and
diff --git a/tests/timebase/test_extract.py b/tests/timebase/test_extract.py
index fd47b18..ad5e02c 100644
--- a/tests/timebase/test_extract.py
+++ b/tests/timebase/test_extract.py
@@ -51,24 +51,24 @@ def test_bitstream_accepts_the_floor_exactly():
     assert stream.fs_hz == 400.0
 
 
-def test_syncbox_extraction_recovers_every_ground_truth_barcode(tmp_path: Path):
+def test_the_sync_box_scan_carries_every_ground_truth_barcode_from_its_records(tmp_path: Path):
     """The sync box is the reference: session time is t=0 at its first barcode
-    (spec section 4.5), so its own log needs no decode — the values and times
-    are already in it.
+    (spec section 4.5), so its own log needs no decode -- the values and times
+    are already in it, and its stream is its extent alone, with no edges to
+    decode back (design spec `2026-11-04-syncbox-header-design.md` section 5).
 
     Checked against GroundTruth rather than against a re-decode of the same
     file, because a reader that mis-parses consistently agrees with itself.
     """
+    from wl_preproc.timebase.segments import scan_system
+
     truth = generate_session(tmp_path, RECIPES["ci"])
     session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
-    stream = extract_syncbox(session_dir / "syncbox" / "seg-000.log")
+    (scan,) = scan_system("syncbox", session_dir / "syncbox")
 
-    assert stream.n_samples > 0
-    assert len(stream.edges) > 0
-    # Every ground-truth barcode is present, by value.
-    recovered = {b.value for b in decode_edges(list(stream.edges))}
-    expected = {value for value, _ in truth.barcodes}
-    assert expected - recovered == set(), f"missing barcodes: {expected - recovered}"
+    assert scan.stream.n_samples > 0 and scan.stream.edges == ()
+    assert extract_syncbox(scan.path) == scan.stream
+    assert {b.value for b in scan.barcodes} == {value for value, _ in truth.barcodes}
 
 
 def test_rhs_extraction_recovers_ground_truth_barcodes(tmp_path: Path):
@@ -376,6 +376,7 @@ def test_decode_reliability_is_measured_on_every_system(tmp_path: Path, capsys):
     started failing intermittently for no visible reason.
     """
     from wl_preproc.synth.ohdpi import FILENAME
+    from wl_preproc.timebase.segments import scan_system
 
     measured: dict[str, tuple[int, int]] = {}
     for profile, cases in (
@@ -393,8 +394,12 @@ def test_decode_reliability_is_measured_on_every_system(tmp_path: Path, capsys):
                 if relative is None
                 else session_dir / relative
             )
-            stream = EXTRACTORS[system](path)
-            recovered = {b.value for b in decode_edges(list(stream.edges))}
+            if system == "syncbox":
+                # The box's own barcodes are its records, read as they are.
+                recovered = {b.value for scan in scan_system(system, path.parent) for b in scan.barcodes}
+            else:
+                stream = EXTRACTORS[system](path)
+                recovered = {b.value for b in decode_edges(list(stream.edges))}
             emitted = {value for value, _ in truth.barcodes}
             measured[system] = (len(recovered & emitted), len(emitted))
 
diff --git a/tests/timebase/test_fit.py b/tests/timebase/test_fit.py
index 3581ae6..e71b31c 100644
--- a/tests/timebase/test_fit.py
+++ b/tests/timebase/test_fit.py
@@ -253,11 +253,9 @@ def _session_reference(session_dir: Path) -> dict[int, float]:
     on. A reference taken from the oracle would test the fit against a timeline
     the pipeline will never have.
     """
-    from wl_preproc.timebase.extract import extract_syncbox
+    from wl_preproc.timebase.segments import session_reference
 
-    decoded = decode_edges(list(extract_syncbox(_recording(session_dir, "syncbox")).edges))
-    origin_us = decoded[0].start_us
-    return {b.value: (b.start_us - origin_us) / 1e6 for b in decoded}
+    return session_reference(session_dir)
 
 
 def test_the_whole_chain_recovers_each_systems_clock(tmp_path: Path):
@@ -308,15 +306,15 @@ def test_the_syncbox_has_no_drift_against_itself(tmp_path: Path):
     have seen it, because every shipped recipe left the value at zero.
     """
     from wl_preproc.synth.recipe import RECIPES
-    from wl_preproc.timebase.extract import extract_syncbox
+    from wl_preproc.timebase.segments import scan_system
 
     recipe = RECIPES["drift"].model_copy(update={"drift_ppm": 250.0})
     generate_session(tmp_path, recipe)
     session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
     reference = _session_reference(session_dir)
 
-    decoded = decode_edges(list(extract_syncbox(_recording(session_dir, "syncbox")).edges))
-    fit = fit_rate(decoded, reference, nominal_rate_hz=1_000_000.0)
+    (scan,) = scan_system("syncbox", session_dir / "syncbox")
+    fit = fit_rate(list(scan.barcodes), reference, nominal_rate_hz=1_000_000.0)
 
     assert fit.drift_ppm == pytest.approx(0.0, abs=1e-6)
 
diff --git a/tests/timebase/test_syncbox_log.py b/tests/timebase/test_syncbox_log.py
index 8e1a936..b60cc7a 100644
--- a/tests/timebase/test_syncbox_log.py
+++ b/tests/timebase/test_syncbox_log.py
@@ -60,14 +60,14 @@ def test_the_barcodes_are_the_boxs_own_records_and_nothing_on_its_barcode_pin(tm
                                           (912_345_680, 4_000_000)]
 
 
-def test_the_sync_box_stream_decodes_to_the_boxs_records(tmp_path):
-    """`extract_syncbox` renders them into the edge form every device has,
-    so every stage downstream reads them as it reads any device."""
-    from wl_sync.barcode import decode_edges
+def test_the_sync_box_stream_is_its_extent_alone(tmp_path):
+    """From tick 0 to its last frame's end, with no edges: its barcodes are
+    its records, never rendered into edges and decoded back, which lost one
+    within 400 ms of tick 0 (part 1's deferred finding)."""
+    from wl_sync.barcode import FRAME_US
 
     stream = extract_syncbox(_real_shaped_log(tmp_path / "seg-000.log"))
-    assert [(b.value, b.start_us) for b in decode_edges(list(stream.edges))] == [
-        (912_345_678, 2_000_000), (912_345_679, 3_000_000), (912_345_680, 4_000_000)]
+    assert (stream.edges, stream.fs_hz, stream.n_samples) == ((), 1_000_000.0, 4_000_000 + FRAME_US)
 
 
 def test_a_log_with_no_barcode_record_has_no_barcodes(tmp_path):
diff --git a/tests/timebase/test_syncbox_segments.py b/tests/timebase/test_syncbox_segments.py
index 03f092d..67068f7 100644
--- a/tests/timebase/test_syncbox_segments.py
+++ b/tests/timebase/test_syncbox_segments.py
@@ -26,11 +26,11 @@ def _restarted(tmp_path):
     return tmp_path / RESTART.session_id, truth
 
 
-def _segment(syncbox_dir, name, index, records):
+def _segment(syncbox_dir, name, index, records, **fields):
     syncbox_dir.mkdir(parents=True, exist_ok=True)
     header = SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id="2027-03-14_01", rig="rig0",
                               boot_id="b0", written_at=datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC),
-                              gpio_map=GPIO_MAP, segment_index=index)
+                              gpio_map=GPIO_MAP, segment_index=index, **fields)
     write_log(syncbox_dir / name, header, records)
 
 
@@ -82,7 +82,7 @@ def test_a_first_segment_with_no_barcode_leaves_session_time_to_the_next(tmp_pat
     _segment(syncbox_dir, "seg-000.log", 0, [CodeWord(tick_us=500_000, word=0x8001)])
     _segment(syncbox_dir, "seg-001.log", 1, [BarcodeEmitted(tick_us=2_000_000, value=70),
                                              BarcodeEmitted(tick_us=3_000_000, value=71)])
-    assert segments.session_log(tmp_path / "s").segment_index == 1
+    assert segments.read_syncbox_session(tmp_path / "s").roles() == ["no_barcode", "session_time"]
     assert segments.session_reference(tmp_path / "s") == {70: 0.0, 71: 1.0}
 
 
@@ -104,3 +104,119 @@ def test_each_segment_has_a_role(tmp_path):
     _segment(syncbox_dir, "seg-003.log", 3, [CodeWord(tick_us=600_000, word=0x8002)])
     assert segments.segment_roles(segments.syncbox_logs(tmp_path / "s")) == [
         "no_barcode", "session_time", "after_restart_unbridged", "no_barcode"]
+
+
+# Design spec `2026-11-04-syncbox-header-design.md` sections 4 and 5: a clock
+# is a process of the box, and a header from 2026-10-30 on names it.
+
+PROCESS = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
+LATER_PROCESS = "16fd2706-8baf-433b-82eb-8c7fada847da"
+
+
+def _barcodes(*pairs):
+    return [BarcodeEmitted(tick_us=round(tick_s * 1e6), value=value) for tick_s, value in pairs]
+
+
+def _codes(*pairs):
+    return [CodeWord(tick_us=round(tick_s * 1e6), word=word) for tick_s, word in pairs]
+
+
+def _rolled(tmp_path, *later, process=PROCESS):
+    """`seg-000.log` of `PROCESS`, its first frame the late one, then
+    `seg-001.log` of `process` holding `later`."""
+    syncbox_dir = tmp_path / "s" / "syncbox"
+    _segment(syncbox_dir, "seg-000.log", 0, _barcodes((1.0, 100), (2.0, 101), (3.0, 102)) + _codes((2.5, 0x8001)),
+             process_uuid=PROCESS)
+    _segment(syncbox_dir, "seg-001.log", 1, list(later), process_uuid=process)
+    return segments.read_syncbox_session(tmp_path / "s")
+
+
+def test_a_roll_in_place_is_one_clock(tmp_path):
+    """Two segments of one process in one folder are not a restart: their
+    ticks compare by number, and session time and its codes take both."""
+    session = _rolled(tmp_path, *_barcodes((4.0, 103), (5.0, 104)), *_codes((4.5, 0x8002)))
+    assert (session.follows, session.roles()) == ((None, "adjacent"), ["session_time", "session_time"])
+    assert session.reference() == {101: 0.0, 102: 1.0, 103: 2.0, 104: 3.0}
+    assert session.codes() == [(0.5, 0x8001), (2.5, 0x8002)]
+    assert session.gaps() == []
+
+
+def test_a_roll_in_place_may_skip_one_value(tmp_path):
+    """NTP's first sync can step the clock, and the frame grid placed again
+    skips one value about 1.6 s on: adjacent, not a gap."""
+    session = _rolled(tmp_path, *_barcodes((4.6, 104), (5.6, 105)))
+    assert session.follows[1] == "adjacent" and session.gaps() == []
+
+
+def test_a_gap_within_one_process_keeps_one_clock_and_is_named(tmp_path):
+    """The box recorded elsewhere, on the same clock, and came back: session
+    time is unbroken, and the stretch whose code words are not here is the
+    gap's."""
+    session = _rolled(tmp_path, *_barcodes((40.0, 140), (41.0, 141)), *_codes((40.2, 0x8003)))
+    assert (session.follows[1], session.roles()) == ("gap", ["session_time", "session_time"])
+    assert session.reference()[140] == 38.0
+    assert session.gaps() == [(1, 3_000_000, 40_000_000)]
+
+
+@pytest.mark.parametrize(("later", "why"), [
+    (_barcodes((0.5, 103)), "its first tick is before the last of the segment it follows"),
+    (_barcodes((4.0, 101)), "its barcode values do not go forward"),
+    (_barcodes((30.0, 103)), "its ticks step 27.0 s and its barcode values 1 s"),
+])
+def test_a_contradiction_of_one_clock_is_read_as_a_restart(tmp_path, later, why):
+    """Decision 3.1: the box's claim is contradicted, so neither reading is
+    safe, and the later segment waits for a bridge as a restart's does."""
+    session = _rolled(tmp_path, *later)
+    assert session.follows[1] == f"contradiction: {why}"
+    assert session.roles() == ["session_time", "after_restart_unbridged"]
+    assert sorted(session.reference()) == [101, 102]
+
+
+def test_each_processs_late_first_barcode_is_dropped_and_an_older_logs_is_kept(tmp_path):
+    """wl-sync: "EXCEPT THE FIRST `B` OF EACH `process_uuid`", whose frame
+    goes out later than its tick says by an amount nothing measures. A folder
+    cannot show whether its process began in it, so its first is dropped
+    either way; a log from before the field names no process, and is read as
+    part 1 read it."""
+    session = _rolled(tmp_path, *_barcodes((0.4, 200), (1.4, 201)), process=LATER_PROCESS)
+    assert session.kept == (((101, 2_000_000), (102, 3_000_000)), ((201, 1_400_000),))
+    assert session.roles() == ["session_time", "after_restart_unbridged"]
+    older = tmp_path / "older" / "syncbox"
+    _segment(older, "seg-000.log", 0, _barcodes((1.0, 100), (2.0, 101)))
+    assert segments.session_reference(tmp_path / "older") == {100: 0.0, 101: 1.0}
+
+
+def test_a_segment_of_session_times_clock_that_no_barcode_places_still_places_its_codes(tmp_path):
+    """A roll whose segment ends before its first frame: its codes are on
+    session time's clock, and it is neither a segment nor a rejection."""
+    session = _rolled(tmp_path, *_codes((3.4, 0x8002)))
+    assert (session.follows[1], session.roles()) == ("adjacent", ["session_time", "session_time"])
+    assert session.codes() == [(0.5, 0x8001), (1.4, 0x8002)]
+    assert [scan.path.name for scan in segments.syncbox_scans(tmp_path / "s")] == ["seg-000.log"]
+
+
+def test_a_barcode_at_tick_0_places_its_segment(tmp_path):
+    """Part 1's deferred finding: rendered into edges and decoded back, a
+    barcode within 400 ms of a segment's tick 0 had no idle before it and was
+    lost."""
+    syncbox_dir = tmp_path / "s" / "syncbox"
+    _segment(syncbox_dir, "seg-000.log", 0, _barcodes((0.1, 50), (1.1, 51)))
+    assert segments.session_reference(tmp_path / "s") == {50: 0.0, 51: 1.0}
+    (scan,) = segments.syncbox_scans(tmp_path / "s")
+    assert [(barcode.value, barcode.start_us) for barcode in scan.barcodes] == [(50, 100_000), (51, 1_100_000)]
+
+
+def test_each_log_is_read_once_however_many_answers_a_stage_asks_for(tmp_path, monkeypatch):
+    """Part 1's deferred finding: session time, the box's scans and its codes
+    each read every log again."""
+    from wl_preproc.timebase import _syncbox_log
+
+    session_dir, _truth = _restarted(tmp_path)
+    reads = []
+    real = _syncbox_log.read_log
+    monkeypatch.setattr(_syncbox_log, "read_log", lambda path: reads.append(path.name) or real(path))
+    _syncbox_log._read.cache_clear()
+    segments.session_reference(session_dir)
+    segments.scan_system("syncbox", session_dir / "syncbox")
+    syncbox_codes(session_dir)
+    assert sorted(reads) == ["seg-000.log", "seg-001.log"]
PATCH
```

- [ ] **Step 2: Run them and see them fail.** The tests that read session time through `session_reference` (`tests/timebase/test_fit.py`) pass already: they pin that the reference is the same once read from the `B` records.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_syncbox_restart.py tests/timebase/test_extract.py tests/timebase/test_fit.py tests/timebase/test_syncbox_log.py tests/timebase/test_syncbox_segments.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `14 failed, 59 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/schema/timebase.py b/wl_preproc/schema/timebase.py
index 4a6d27d..f537c88 100644
--- a/wl_preproc/schema/timebase.py
+++ b/wl_preproc/schema/timebase.py
@@ -261,12 +261,17 @@ class SystemTimebase(dj.Computed):
 
         if key["system"] == "syncbox":
             # Session time IS this system's timeline, so its fit against itself
-            # is exactly identity -- the truth, not a placeholder.
+            # is exactly identity -- the truth, not a placeholder. Matched are
+            # the barcodes session time holds: a later process's are the box's
+            # too, but nothing places them until a bridge does (part 1's
+            # deferred finding; design spec `2026-11-04-syncbox-header-design.md`
+            # section 9).
+            reference = clock_for(key, session_dir).barcode_reference() if decoded else {}
             self.insert1(
                 {
                     **row,
                     "fit_status": "fitted",
-                    "n_barcodes_matched": len(decoded),
+                    "n_barcodes_matched": sum(1 for barcode in decoded if barcode.value in reference),
                     "nominal_rate_hz": nominal_rate_hz,
                     "fitted_rate_hz": nominal_rate_hz,
                     "drift_ppm": 0.0,
diff --git a/wl_preproc/timebase/_syncbox_log.py b/wl_preproc/timebase/_syncbox_log.py
index 9057a08..d611afd 100644
--- a/wl_preproc/timebase/_syncbox_log.py
+++ b/wl_preproc/timebase/_syncbox_log.py
@@ -28,6 +28,7 @@ has exactly one reader.
 
 from __future__ import annotations
 
+import functools
 from dataclasses import dataclass
 from pathlib import Path
 
@@ -61,7 +62,19 @@ def read_syncbox_log(path: Path) -> SyncBoxLog:
     format does not define (`wl_sync.log.read_log` validates both) refuses
     every stage that reads the session's sync box, and the file is what a
     person must open (design spec `2026-11-04-syncbox-header-design.md`
-    section 3)."""
+    section 3).
+
+    Read once per process for as long as the file is unchanged: a stage asks
+    for session time, the box's scans and its codes, and each read the whole
+    log again (part 1's deferred finding). Keyed by the file's modification
+    time and size, so a file written again is read again; the edges, the bulk
+    of a real log, are never kept."""
+    stat = Path(path).stat()
+    return _read(Path(path), stat.st_mtime_ns, stat.st_size)
+
+
+@functools.lru_cache(maxsize=256)
+def _read(path: Path, _mtime_ns: int, _size: int) -> SyncBoxLog:
     try:
         header, records = read_log(path)
     except ValueError as error:
diff --git a/wl_preproc/timebase/clock.py b/wl_preproc/timebase/clock.py
index 8d6c1f1..49b23de 100644
--- a/wl_preproc/timebase/clock.py
+++ b/wl_preproc/timebase/clock.py
@@ -23,10 +23,8 @@ from pathlib import Path
 # sync box's wall clock; read from here by `nwb/gather.py` too.
 from wl_sync.clock import BARCODE_EPOCH
 
-from wl_preproc.events.extract import extract_syncbox_words
 from wl_preproc.timebase import segments
 from wl_preproc.timebase.extract import find_recordings
-from wl_preproc.timebase.fit import RateFit, fit_offset
 
 # What may keep a session's time (section 4.1), as `timebase.SessionClock`
 # declares it.
@@ -115,55 +113,24 @@ def reference_from(first_barcode_value: int, started_at: datetime.datetime) -> d
 
 
 def syncbox_codes(session_dir: Path) -> list[tuple[float, int]]:
-    """Every code word the sync box recorded, native time converted to session
-    time via `timebase/fit.py` -- the same fit primitives `core.Segment` and
-    `timebase.SystemTimebase` use, so this does not invent a second
-    conversion.
-
-    Identity rate, not `fit_rate`: session time IS the sync box's own timeline
-    (spec section 4.5), the same special case `SystemTimebase.make()` applies
-    to this same system for the same reason -- a fit of it against itself is
-    exactly zero drift with zero residual, the truth rather than a placeholder.
-
-    Moved here from `schema/events.py::decode_syncbox_in_session_time`
-    unchanged: the sync box's codes are the session's codes while it is the
-    clock, and every stage reads them through `Clock.codes`."""
-    reference = segments.session_reference(session_dir)
-    # The segment session time comes from, alone: a later one, after a
-    # restart, is on another clock (design spec
-    # `2026-10-22-syncbox-records-and-segments-design.md` section 4).
-    timed = segments.session_log(session_dir).path
-    scans = [scan for scan in segments.scan_system("syncbox", session_dir / "syncbox") if scan.path == timed]
-    if not scans:
-        raise FileNotFoundError(
-            f"{session_dir}: no syncbox recording, so this session has no "
-            "session time and no code stream to decode from -- every session "
-            "has one (SessionRecipe requires it of the synthetic fixtures, and "
-            "spec section 4.3 makes it essential sync infrastructure)"
+    """Every code word of session time's clock, in session seconds and in
+    emission order (`timebase/segments.py::SyncBoxSession.codes`): every
+    segment of the process session time comes from, and no other, since a
+    later process is on another clock (design specs
+    `2026-10-22-syncbox-records-and-segments-design.md` section 4 and
+    `2026-11-04-syncbox-header-design.md` section 4).
+
+    Identity rate: session time IS the sync box's own timeline (spec section
+    4.5), the special case `SystemTimebase.make()` applies to this same system
+    for the same reason. The sync box's codes are the session's codes while it
+    is the clock, and every stage reads them through `Clock.codes`."""
+    session = segments.read_syncbox_session(session_dir)
+    if session.timed is None:
+        raise ValueError(
+            f"{session_dir}: the sync box log decoded no barcodes, so session "
+            "time cannot be established"
         )
-
-    converted: list[tuple[float, int]] = []
-    for scan in scans:
-        rate = RateFit(
-            fitted_rate_hz=scan.stream.fs_hz,
-            drift_ppm=0.0,
-            n_matched=len(scan.barcodes),
-            residual_us_rms=0.0,
-            residual_us_max=0.0,
-        )
-        offset = fit_offset(scan.barcodes, reference, rate)
-        word_stream = extract_syncbox_words(scan.path)
-        converted.extend(
-            (native_pos / word_stream.fs_hz / rate.scale + offset.offset_s, code)
-            for native_pos, code in word_stream.words
-        )
-
-    # `decode_stream` reads an escape payload by POSITION, not by comparing
-    # timestamps -- see its own docstring -- so the stream is sorted into
-    # emission order before decoding. One segment's words already are; this is
-    # then a no-op.
-    converted.sort(key=lambda pair: pair[0])
-    return converted
+    return session.codes()
 
 
 @dataclasses.dataclass(frozen=True)
diff --git a/wl_preproc/timebase/extract.py b/wl_preproc/timebase/extract.py
index 8db5578..3fcce2a 100644
--- a/wl_preproc/timebase/extract.py
+++ b/wl_preproc/timebase/extract.py
@@ -13,9 +13,13 @@ from __future__ import annotations
 from collections.abc import Callable
 from dataclasses import dataclass
 from pathlib import Path
+from typing import TYPE_CHECKING
 
 import numpy as np
-from wl_sync.barcode import BIT_SLOT_US, FRAME_US, edges_from_samples, encode
+from wl_sync.barcode import BIT_SLOT_US, FRAME_US, edges_from_samples
+
+if TYPE_CHECKING:
+    from wl_preproc.timebase._syncbox_log import SyncBoxLog
 
 
 def min_sample_rate_hz() -> float:
@@ -97,42 +101,23 @@ _SYNCBOX_NOMINAL_FS_HZ = 1_000_000.0
 
 
 def extract_syncbox(path: Path) -> BitStream:
-    """The sync box's own log, rendered into the same edge form every other
-    system produces.
-
-    Rendering through `wl_sync.barcode.encode` rather than writing edges by
-    hand: the codec owns the frame's shape, and a second copy of it here is the
-    reimplementation this phase's constraints forbid.
-
-    A hand-built edge list has to supply two things `edges_from_samples` gives
-    every *sampled* system for free (its `previous` state starts `None`, so it
-    always emits an edge at its own t0): a LOW edge before the first frame, so
-    that frame's preceding idle is verifiable by a caller who decodes this
-    stream without passing `start_us` itself; and a LOW edge closing each
-    frame's TRAIL pulse, so `decode_edges`'s own completeness check — which
-    requires the edge list to extend to the frame's end — has something to see
-    past the last frame. Native tick 0 is the log's own time origin and is
-    known LOW there, before anything has driven the line.
-    """
-    from wl_preproc.timebase._syncbox_log import read_barcode_entries
-
-    entries = read_barcode_entries(path)
-    edges: list[tuple[int, int]] = []
-    for value, t_us in entries:
-        tick = t_us
-        for level, duration_us in encode(value):
-            edges.append((tick, level))
-            tick += duration_us
-        edges.append((tick, 0))
-    if edges:
-        edges.append((0, 0))
-    edges.sort()
-    last_us = edges[-1][0] if edges else 0
-    return BitStream(
-        edges=tuple(edges),
-        fs_hz=_SYNCBOX_NOMINAL_FS_HZ,
-        n_samples=last_us,
-    )
+    """The sync box's log as a stream: its extent alone (`syncbox_stream`).
+    Its barcodes are its `B` records, which `timebase/segments.py::
+    syncbox_scans` reads as they are; until 2026-11 this rendered them into
+    edges through `wl_sync.barcode.encode` for every stage to decode back,
+    which lost a barcode within 400 ms of tick 0 and read every log once more
+    than needed (design spec `2026-11-04-syncbox-header-design.md` section 5)."""
+    from wl_preproc.timebase._syncbox_log import read_syncbox_log
+
+    return syncbox_stream(read_syncbox_log(path))
+
+
+def syncbox_stream(log: SyncBoxLog) -> BitStream:
+    """One segment's extent, in its own ticks: from tick 0, the box's clock's
+    origin, to the end of its last barcode frame, as every device's stream
+    runs from its first sample to its last. No edges: nothing reads them."""
+    last_us = max((tick_us for _value, tick_us in log.barcodes), default=None)
+    return BitStream(edges=(), fs_hz=_SYNCBOX_NOMINAL_FS_HZ, n_samples=0 if last_us is None else last_us + FRAME_US)
 
 
 def _edges_from_bit(
diff --git a/wl_preproc/timebase/segments.py b/wl_preproc/timebase/segments.py
index 8be6935..3f74afd 100644
--- a/wl_preproc/timebase/segments.py
+++ b/wl_preproc/timebase/segments.py
@@ -123,24 +123,165 @@ def classify_segment(duration_s: float, n_barcodes: int) -> str:
 def syncbox_logs(session_dir: Path) -> list[SyncBoxLog]:
     """Every segment of the session's sync box log, in the order of its
     header's `segment_index`: a file's name sorts `seg-1000.log` before
-    `seg-999.log`. Each is one run of the box's process on a clock of its own,
-    and a restart resumes into the open session's folder (wl-sync's
-    `segment.py`; design spec `2026-10-22-syncbox-records-and-segments-design.md`
-    section 4), so no tick is ever compared across two of them."""
+    `seg-999.log`. Each file is read once however many stages ask in one
+    process (`_syncbox_log.read_syncbox_log`, part 1's deferred finding)."""
     from wl_preproc.timebase.extract import find_recordings
 
     logs = [read_syncbox_log(path) for path in find_recordings("syncbox", session_dir / "syncbox")]
     return sorted(logs, key=lambda log: (log.segment_index, log.path.name))
 
 
-def session_log(session_dir: Path) -> SyncBoxLog:
-    """The segment session time comes from: the first holding a barcode. A box
-    that crashed within its first second logged none in that segment.
+#: How a segment follows the segment before it on its clock (design spec
+#: `2026-11-04-syncbox-header-design.md` section 4): the roll in place, with
+#: nothing missing; a gap, the box recording elsewhere on the same clock; or a
+#: contradiction of the box's claim of one clock, read as a restart.
+FOLLOWS_ADJACENT = "adjacent"
+FOLLOWS_GAP = "gap"
+FOLLOWS_CONTRADICTION = "contradiction"
+#: Across a roll in place the barcode value steps by one, or by two where the
+#: frame grid was placed again after NTP's first sync stepped the clock
+#: (wl-sync's `process_uuid` docstring).
+ADJACENT_VALUE_STEPS = (1, 2)
+#: The most a boundary's tick step may differ from its barcode value step
+#: before one clock is contradicted: a roll in place's second frame comes
+#: about 1.6 s after the first for a value step of 2, and NTP slews both.
+MAX_STEP_DISAGREEMENT_S = 2.0
 
-    Raises if the sync box recorded nothing, or no barcode in any segment:
-    without a reference there is no session time at all, and every other
-    system's fit would silently become a fit against whichever device happened
-    to be read first."""
+
+@dataclass(frozen=True, slots=True)
+class SyncBoxSession:
+    """A session's sync box segments as clocks (design spec
+    `2026-11-04-syncbox-header-design.md` sections 4 and 5). A clock is one
+    process of the box (`process_uuid`), its segments' ticks comparable by
+    number, and a log written before that field is a process of its own; a
+    boundary that contradicts one clock starts another. Per segment, in
+    `logs`' order: its clock, how it follows the segment before it on that
+    clock (None for a clock's first), and the barcodes that place it -- the
+    box's own, less each process's first, whose frame goes out late by an
+    amount nothing measures. Session time is the clock of the first segment
+    whose barcodes place it, t = 0 at that clock's first."""
+
+    logs: tuple[SyncBoxLog, ...]
+    clocks: tuple[int, ...]
+    follows: tuple[str | None, ...]
+    kept: tuple[tuple[tuple[int, int], ...], ...]
+    timed: int | None
+
+    @classmethod
+    def from_logs(cls, logs: list[SyncBoxLog]) -> SyncBoxSession:
+        clocks: list[int] = []
+        follows: list[str | None] = []
+        current: dict[str, int] = {}
+        for index, log in enumerate(logs):
+            if log.process_uuid is None or log.process_uuid not in current:
+                clock, how = len(set(clocks)), None
+            else:
+                clock = current[log.process_uuid]
+                members = [logs[i] for i in range(index) if clocks[i] == clock]
+                how = _follows(members, log)
+                if how.startswith(FOLLOWS_CONTRADICTION):
+                    clock = len(set(clocks))
+            if log.process_uuid is not None:
+                current[log.process_uuid] = clock
+            clocks.append(clock)
+            follows.append(how)
+        dropped: set[str] = set()
+        kept = []
+        for log in logs:
+            barcodes = log.barcodes
+            if log.process_uuid is not None and barcodes and log.process_uuid not in dropped:
+                dropped.add(log.process_uuid)
+                barcodes = barcodes[1:]
+            kept.append(barcodes)
+        timed = next((clocks[i] for i in range(len(logs)) if kept[i]), None)
+        return cls(logs=tuple(logs), clocks=tuple(clocks), follows=tuple(follows), kept=tuple(kept), timed=timed)
+
+    def in_session_time(self, index: int) -> bool:
+        return self.timed is not None and self.clocks[index] == self.timed
+
+    @property
+    def origin_us(self) -> int:
+        """The tick of session t = 0: the first barcode that places session
+        time's clock, on the wire."""
+        if self.timed is None:
+            raise ValueError("the sync box log decoded no barcodes, so session time cannot be established")
+        return next(barcodes[0][1] for i, barcodes in enumerate(self.kept) if self.in_session_time(i) and barcodes)
+
+    def seconds(self, tick_us: int) -> float:
+        """A tick of session time's clock, in session seconds: the box's own
+        timeline, so identity rate (spec section 4.5)."""
+        return (tick_us - self.origin_us) / 1e6
+
+    def reference(self) -> dict[int, float]:
+        origin_us = self.origin_us
+        return {value: (tick_us - origin_us) / 1e6
+                for i, barcodes in enumerate(self.kept) if self.in_session_time(i) for value, tick_us in barcodes}
+
+    def codes(self) -> list[tuple[float, int]]:
+        """Every code word of session time's clock in session seconds, in
+        emission order: a stable sort, since `decode_stream` reads an escape's
+        payload by position."""
+        origin_us = self.origin_us
+        codes = [((tick_us - origin_us) / 1e6, word)
+                 for i, log in enumerate(self.logs) if self.in_session_time(i) for tick_us, word in log.codes]
+        return sorted(codes, key=lambda pair: pair[0])
+
+    def gaps(self) -> list[tuple[int, int, int]]:
+        """`(index, start_us, stop_us)` for each gap on session time's clock:
+        the box recorded elsewhere from the last record of the segment before
+        `index` to the first of `index`, and the code words of that stretch
+        are not in this folder."""
+        out = []
+        for index, how in enumerate(self.follows):
+            if how == FOLLOWS_GAP and self.in_session_time(index):
+                before = max(tick_us for i in range(index) if self.clocks[i] == self.clocks[index]
+                             for tick_us in _ticks(self.logs[i]))
+                out.append((index, before, _ticks(self.logs[index])[0]))
+        return out
+
+    def roles(self) -> list[str]:
+        """Each segment's role, as `timebase.SyncBoxSegment` stores it: every
+        segment of session time's clock keeps session time, holding a barcode
+        or not; any other holding one came after a restart, or a
+        contradiction, and waits for a bridge; one holding none places
+        nothing."""
+        return [SEGMENT_SESSION_TIME if self.in_session_time(i) else SEGMENT_AFTER_RESTART if log.barcodes
+                else SEGMENT_NO_BARCODE for i, log in enumerate(self.logs)]
+
+
+def _ticks(log: SyncBoxLog) -> list[int]:
+    return sorted([tick_us for _value, tick_us in log.barcodes] + [tick_us for tick_us, _word in log.codes])
+
+
+def _follows(members: list[SyncBoxLog], log: SyncBoxLog) -> str:
+    """How `log` follows the segments before it on its clock, `members`:
+    by the barcode values across the boundary where each side has one,
+    otherwise by its ticks alone."""
+    before = sorted(tick for member in members for tick in _ticks(member))
+    after = _ticks(log)
+    if not before or not after:
+        return FOLLOWS_ADJACENT
+    if after[0] < before[-1]:
+        return f"{FOLLOWS_CONTRADICTION}: its first tick is before the last of the segment it follows"
+    last = max(((tick_us, value) for member in members for value, tick_us in member.barcodes), default=None)
+    first = min(((tick_us, value) for value, tick_us in log.barcodes), default=None)
+    if last is None or first is None:
+        return FOLLOWS_ADJACENT if (after[0] - before[-1]) / 1e6 <= MAX_STEP_DISAGREEMENT_S else FOLLOWS_GAP
+    value_step = first[1] - last[1]
+    tick_step_s = (first[0] - last[0]) / 1e6
+    if value_step <= 0:
+        return f"{FOLLOWS_CONTRADICTION}: its barcode values do not go forward"
+    if abs(tick_step_s - value_step) > MAX_STEP_DISAGREEMENT_S:
+        return (f"{FOLLOWS_CONTRADICTION}: its ticks step {tick_step_s:.1f} s and its barcode values "
+                f"{value_step} s")
+    return FOLLOWS_ADJACENT if value_step in ADJACENT_VALUE_STEPS else FOLLOWS_GAP
+
+
+def read_syncbox_session(session_dir: Path) -> SyncBoxSession:
+    """The session's sync box as clocks. Raises if the sync box recorded
+    nothing: without a reference there is no session time at all, and every
+    other system's fit would silently become a fit against whichever device
+    happened to be read first."""
     logs = syncbox_logs(session_dir)
     if not logs:
         raise FileNotFoundError(
@@ -148,18 +289,15 @@ def session_log(session_dir: Path) -> SyncBoxLog:
             "time. Every other system is positioned against it, so there is "
             "nothing to align to rather than one system missing"
         )
-    for log in logs:
-        if log.barcodes:
-            return log
-    raise ValueError(
-        f"{session_dir}: the sync box log decoded no barcodes, so session "
-        "time cannot be established"
-    )
+    return SyncBoxSession.from_logs(logs)
 
 
-#: A sync box segment's role, as `timebase.SyncBoxSegment` stores it (design
-#: spec `2026-10-22-syncbox-records-and-segments-design.md` section 6). Part 2
-#: adds a role for each bridge.
+#: A sync box segment's role, as `timebase.SyncBoxSegment` stores it: session
+#: time's clock, holding a barcode or not; no barcode, outside it; and after a
+#: restart, not yet bridged (design specs
+#: `2026-10-22-syncbox-records-and-segments-design.md` section 6 and
+#: `2026-11-04-syncbox-header-design.md` section 4). Part 2 adds a role for
+#: each bridge.
 SEGMENT_SESSION_TIME = "session_time"
 SEGMENT_NO_BARCODE = "no_barcode"
 SEGMENT_AFTER_RESTART = "after_restart_unbridged"
@@ -167,39 +305,48 @@ SEGMENT_ROLES = (SEGMENT_SESSION_TIME, SEGMENT_NO_BARCODE, SEGMENT_AFTER_RESTART
 
 
 def segment_roles(logs: list[SyncBoxLog]) -> list[str]:
-    """Each segment's role, in the order of `logs` (`syncbox_logs`'s): the
-    first holding a barcode keeps session time; any other holding one came
-    after a restart, on another clock, and waits for a bridge; one holding
-    none places nothing, before session time's segment or after it."""
-    timed = next((log for log in logs if log.barcodes), None)
-    return [SEGMENT_SESSION_TIME if log is timed else SEGMENT_AFTER_RESTART if log.barcodes else SEGMENT_NO_BARCODE
-            for log in logs]
+    """Each segment's role, in the order of `logs` (`syncbox_logs`'s)."""
+    return SyncBoxSession.from_logs(logs).roles()
 
 
 def session_reference(session_dir: Path) -> dict[int, float]:
     """Barcode value -> session-time seconds, from the sync box's own log.
 
-    Session time is t=0 at the sync box's FIRST barcode, on every session type,
-    one code path (spec section 4.5), within the segment `session_log` names:
-    a later segment, after a restart, is on another clock and gives it nothing
-    (design spec `2026-10-22-syncbox-records-and-segments-design.md` section
-    4). Built by decoding that segment and rebasing to its first barcode --
-    never from `GroundTruth`, which exists only in the synthetic generator and
-    will not exist in January.
+    Session time is t = 0 at the first barcode that places session time's
+    clock (spec section 4.5; design spec
+    `2026-11-04-syncbox-header-design.md` section 5): every segment of that
+    clock, and no other, read from its `B` records with the barcode delay
+    added -- never from `GroundTruth`, which exists only in the synthetic
+    generator and will not exist in January.
     """
-    from wl_sync.barcode import decode_edges
-
-    from wl_preproc.timebase.extract import extract_syncbox
-
-    decoded = decode_edges(list(extract_syncbox(session_log(session_dir).path).edges))
-    if not decoded:
+    session = read_syncbox_session(session_dir)
+    if session.timed is None:
         raise ValueError(
             f"{session_dir}: the sync box log decoded no barcodes, so session "
             "time cannot be established"
         )
+    return session.reference()
+
+
+def syncbox_scans(session_dir: Path) -> list[RecordingScan]:
+    """The sync box's own scans, one per segment, from its records: its
+    barcodes those that place it, as on the wire, and its extent from tick 0
+    to its last frame's end (`timebase/extract.py::syncbox_stream`). Nothing is
+    rendered into edges and decoded back, which lost a barcode within 400 ms
+    of tick 0 (part 1's deferred finding). A segment of session time's clock
+    that no barcode places is not scanned: its records are placed by its
+    clock, and it is neither a segment nor a rejection."""
+    from wl_sync.barcode import Barcode
+
+    from wl_preproc.timebase.extract import syncbox_stream
 
-    origin_us = min(barcode.start_us for barcode in decoded)
-    return {barcode.value: (barcode.start_us - origin_us) / 1e6 for barcode in decoded}
+    session = read_syncbox_session(session_dir)
+    return [
+        RecordingScan(path=log.path, stream=syncbox_stream(log),
+                      barcodes=tuple(Barcode(value=value, start_us=tick_us) for value, tick_us in kept))
+        for index, (log, kept) in enumerate(zip(session.logs, session.kept, strict=True))
+        if kept or not session.in_session_time(index)
+    ]
 
 
 @dataclass(frozen=True, slots=True)
@@ -243,6 +390,8 @@ def scan_system(system: str, system_dir: Path) -> list[RecordingScan]:
 
     from wl_preproc.timebase.extract import EXTRACTORS, barcode_clear_of_gaps, find_recordings
 
+    if system == "syncbox":
+        return syncbox_scans(system_dir.parent) if find_recordings(system, system_dir) else []
     scans = []
     for path in find_recordings(system, system_dir):
         stream = EXTRACTORS[system](path)
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_syncbox_restart.py tests/timebase/test_extract.py tests/timebase/test_fit.py tests/timebase/test_syncbox_log.py tests/timebase/test_syncbox_segments.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `73 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events tests/schema/test_core.py tests/schema/test_coverage.py tests/schema/test_segment_populate.py tests/schema/test_session_clock.py tests/schema/test_spikeglx_restart.py tests/schema/test_syncbox_restart.py tests/schema/test_timebase.py tests/synth tests/timebase -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `443 passed, 1 skipped`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "first-barcode-kept",
  "wl_preproc/timebase/segments.py",
  "                barcodes = barcodes[1:]",
  "                barcodes = barcodes[0:]",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "older-logs-first-dropped-too",
  "wl_preproc/timebase/segments.py",
  "if log.process_uuid is not None and barcodes and log.process_uuid not in dropped:",
  "if barcodes and log.process_uuid not in dropped:",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "every-segment-its-own-clock",
  "wl_preproc/timebase/segments.py",
  "if log.process_uuid is None or log.process_uuid not in current:",
  "if True:",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "a-skipped-value-is-a-gap",
  "wl_preproc/timebase/segments.py",
  "ADJACENT_VALUE_STEPS = (1, 2)",
  "ADJACENT_VALUE_STEPS = (1,)",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "steps-never-disagree",
  "wl_preproc/timebase/segments.py",
  "    if abs(tick_step_s - value_step) > MAX_STEP_DISAGREEMENT_S:",
  "    if False:",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "values-going-back-allowed",
  "wl_preproc/timebase/segments.py",
  "    if value_step <= 0:",
  "    if False:",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "ticks-going-back-allowed",
  "wl_preproc/timebase/segments.py",
  "    if after[0] < before[-1]:",
  "    if False:",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "contradiction-keeps-the-clock",
  "wl_preproc/timebase/segments.py",
  "                if how.startswith(FOLLOWS_CONTRADICTION):",
  "                if False:",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "gap-window-empty",
  "wl_preproc/timebase/segments.py",
  "out.append((index, before, _ticks(self.logs[index])[0]))",
  "out.append((index, before, before))",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "untimed-segment-scanned",
  "wl_preproc/timebase/segments.py",
  "        if kept or not session.in_session_time(index)",
  "        if True",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "reference-takes-every-clock",
  "wl_preproc/timebase/segments.py",
  "for i, barcodes in enumerate(self.kept) if self.in_session_time(i) for value, tick_us in barcodes}",
  "for i, barcodes in enumerate(self.kept) for value, tick_us in barcodes}",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "codes-take-every-clock",
  "wl_preproc/timebase/segments.py",
  "for i, log in enumerate(self.logs) if self.in_session_time(i) for tick_us, word in log.codes]",
  "for i, log in enumerate(self.logs) for tick_us, word in log.codes]",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "read-every-time",
  "wl_preproc/timebase/_syncbox_log.py",
  "    return _read(Path(path), stat.st_mtime_ns, stat.st_size)",
  "    return _read.__wrapped__(Path(path), stat.st_mtime_ns, stat.st_size)",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "stream-ends-at-the-tick",
  "wl_preproc/timebase/extract.py",
  "n_samples=0 if last_us is None else last_us + FRAME_US)",
  "n_samples=0 if last_us is None else last_us)",
  "tests/timebase/test_syncbox_log.py"
 ],
 [
  "matched-counts-every-barcode",
  "wl_preproc/schema/timebase.py",
  "\"n_barcodes_matched\": sum(1 for barcode in decoded if barcode.value in reference),",
  "\"n_barcodes_matched\": len(decoded),",
  "tests/schema/test_syncbox_restart.py"
 ]
]
```

Expected: 15 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(timebase): a sync box clock is a process, not a segment -- one process's segments read as one clock, each boundary checked (adjacent, a gap, or a contradiction read as a restart), each process's late first barcode dropped and t = 0 at the first kept; every reader of the box's barcodes takes them from its B records, each log read once, and the box counts as matched only the barcodes session time holds (sections 4, 5 and 9)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 3: The synthetic box writes its log as the box does today

§10 and amendments 2, 9 and 10, the requester's standing rule that the fixture is corrected, not the design: each segment names its process and its clocks, and the measured delay when a recipe gives one; each session's first process starts with its late first frame, which no device records and Task 2's reader drops; a roll in place, a gap within one process, a restart's new process, and `L` records built through wl-sync's `Loss`; every segment written by wl-sync's own writer, trailer and all; the box's changes checked against the session (part 1's deferred findings).

**Files:**
- Modify: `wl_preproc/synth/recipe.py` (`SyncBoxRollSpec`, `SyncBoxLossSpec`, the recipe's fields and checks), `synth/timeline.py` (a roll in place skips a value), `synth/syncbox.py`
- Create: `tests/schema/test_syncbox_roll.py`
- Test: `tests/schema/test_syncbox_restart.py`, `tests/synth/test_syncbox.py`, `tests/timebase/test_syncbox_segments.py`

**Interfaces:**
- Consumes: Task 2's `SyncBoxSession`, `read_syncbox_session`, `syncbox_codes`.
- Produces: `recipe.SyncBoxRollSpec(at_s, gap_s=0.0)`; `recipe.SyncBoxLossSpec(cause, count=1, from_s=None,
  to_s=None, gpio=None)`, `.at_s`, `.record(tick) -> Loss`; `SessionRecipe.syncbox_roll`,
  `.syncbox_timebase = "fake"`, `.syncbox_barcode_delay_us = None`, `.syncbox_losses = ()`;
  `synth.syncbox.process_uuid(recipe, number) -> str`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/schema/test_syncbox_restart.py b/tests/schema/test_syncbox_restart.py
index d8e6d79..a3890ff 100644
--- a/tests/schema/test_syncbox_restart.py
+++ b/tests/schema/test_syncbox_restart.py
@@ -77,7 +77,8 @@ def test_the_sync_box_counts_as_matched_only_the_barcodes_session_time_holds(res
 
 def test_each_segment_of_the_box_is_recorded_with_its_role(restarted):
     """`timebase.SyncBoxSegment` (section 6): what each segment's header says,
-    its barcodes, and its role. The barcode delay is null, unmeasured and
+    its barcodes as the box logged them, its process's late first frame
+    among them, and its role. The barcode delay is null, unmeasured and
     uncorrected, until wl-sync writes it into the header (section 5)."""
     from wl_preproc.schema import timebase
 
@@ -85,7 +86,7 @@ def test_each_segment_of_the_box_is_recorded_with_its_role(restarted):
     rows = (timebase.SyncBoxSegment & key).to_dicts(order_by="segment_index")
     assert [(row["segment_index"], row["file_path"], row["n_barcodes"], row["first_barcode"], row["last_barcode"],
              row["role"]) for row in rows] == [
-        (0, "seg-000.log", 8, 1_000_000, 1_000_007, "session_time"),
+        (0, "seg-000.log", 9, 999_999, 1_000_007, "session_time"),
         (1, "seg-001.log", 6, 1_000_009, 1_000_014, "after_restart_unbridged")]
     assert {(row["boot_id"][:5], bool(row["clock_trusted"]), row["clock_reason"], row["barcode_delay_us"])
             for row in rows} == {("synth", True, "", None)}
diff --git a/tests/schema/test_syncbox_roll.py b/tests/schema/test_syncbox_roll.py
new file mode 100644
index 0000000..de21859
--- /dev/null
+++ b/tests/schema/test_syncbox_roll.py
@@ -0,0 +1,54 @@
+"""A session whose sync box rolled in place, its process going on in a second
+segment of the same folder (design spec `2026-11-04-syncbox-header-design.md`
+sections 4 and 10): one clock, so a daemon pass rejects nothing, places every
+code, and tiers the session A, as it would without the roll."""
+
+from __future__ import annotations
+
+import pytest
+
+from tests.identities import new_animal
+
+
+@pytest.fixture(scope="module")
+def landed(dj_conn, prefix, tmp_path_factory):
+    """Two `ci` sessions, one plain and one whose box rolled in place at 7.5 s,
+    after one daemon pass; deleted after the module."""
+    from wl_preproc import daemon
+    from wl_preproc.schema import pipeline
+    from wl_preproc.synth.recipe import RECIPES, SyncBoxRollSpec
+    from wl_preproc.synth.session import generate_session
+
+    from tests.schema.test_eye_populate import _land
+
+    daemon.activate_all(prefix=prefix)
+    keys = []
+    for name, update in (("plain", {}), ("rolled", {"syncbox_roll": SyncBoxRollSpec(at_s=7.5)})):
+        root = tmp_path_factory.mktemp(name)
+        session = new_animal().session()
+        recipe = RECIPES["ci"].model_copy(update={"subject": session.subject, "session_id": session.session_id,
+                                                  **update})
+        generate_session(root, recipe)
+        keys.append(_land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems))
+    report = daemon.run_once(prefix=prefix)
+    yield *keys, report
+    for key in keys:
+        (pipeline.Session & key).delete(prompt=False)
+
+
+def test_a_roll_in_place_rejects_nothing_and_both_segments_keep_session_time(landed):
+    from wl_preproc.schema import core, timebase
+
+    _plain, rolled, report = landed
+    assert not (core.RejectedSegment & rolled).to_dicts()
+    assert [(row["file_path"], row["role"]) for row in (timebase.SyncBoxSegment & rolled).to_dicts(
+        order_by="segment_index")] == [("seg-000.log", "session_time"), ("seg-001.log", "session_time")]
+    assert not [error for error in report["errors"] if rolled["subject"] in str(error)]
+
+
+def test_a_roll_in_place_places_every_code_and_tiers_the_session_as_without_it(landed):
+    from wl_preproc.schema import pipeline, timebase
+
+    plain, rolled, _ = landed
+    assert len(pipeline.trial.Trial & rolled) == len(pipeline.trial.Trial & plain) > 0
+    assert [(timebase.TimingProvenance & key).fetch1("tier") for key in (plain, rolled)] == ["A", "A"]
diff --git a/tests/synth/test_syncbox.py b/tests/synth/test_syncbox.py
index f8ce0ee..abeddfe 100644
--- a/tests/synth/test_syncbox.py
+++ b/tests/synth/test_syncbox.py
@@ -38,7 +38,7 @@ def test_every_barcode_is_a_record_of_the_box_and_none_is_drawn_on_its_pin(tmp_p
     """The box logs each barcode it emits and never captures its own barcode
     line (wl-sync's `log.py`; its GPIO17 is an output)."""
     truth, (_, records) = emit(tmp_path)
-    assert [r.value for r in emitted(records)] == [v for v, _ in truth.barcodes]
+    assert [r.value for r in emitted(records)] == [truth.barcodes[0][0] - 1, *(v for v, _ in truth.barcodes)]
     assert not [r for r in records if isinstance(r, Edge) and r.gpio == BARCODE_PIN]
 
 
@@ -46,7 +46,7 @@ def test_barcode_times_match_ground_truth_after_removing_the_pre_roll(tmp_path):
     """The log's tick origin is deliberately not session time. A fixture where
     they coincide lets a pipeline bug that ignores the offset pass."""
     truth, (_, records) = emit(tmp_path)
-    for record, (_, expected_s) in zip(emitted(records), truth.barcodes, strict=True):
+    for record, (_, expected_s) in zip(emitted(records)[1:], truth.barcodes, strict=True):
         assert record.tick_us / 1e6 - SYNCBOX_PRE_ROLL_S == pytest.approx(expected_s, abs=1e-6)
 
 
@@ -82,3 +82,121 @@ def test_a_session_names_its_log_as_the_box_names_its_first_segment(tmp_path):
 
     generate_session(tmp_path, CI_RECIPE)
     assert sorted(p.name for p in (tmp_path / CI_RECIPE.session_id / "syncbox").glob("*.log")) == ["seg-000.log"]
+
+
+# Design spec `2026-11-04-syncbox-header-design.md` section 10: the box's
+# headers since 2026-10-30, its late first frame, a roll, a gap, a restart's
+# new process, and its losses.
+
+
+def _session(tmp_path, **update):
+    from wl_preproc.synth.session import generate_session
+
+    recipe = CI_RECIPE.model_copy(update=update)
+    truth = generate_session(tmp_path, recipe)
+    return recipe, truth, [read_log(path) for path in sorted((tmp_path / recipe.session_id / "syncbox").glob("*.log"))]
+
+
+def test_the_header_names_the_process_its_clocks_and_its_delay(tmp_path):
+    from wl_preproc.synth.syncbox import process_uuid
+
+    _, (header, _) = emit(tmp_path)
+    assert (header.process_uuid, header.timebase, header.barcode_delay_us) == (
+        process_uuid(CI_RECIPE, 0), "fake", None)
+
+
+def test_a_measured_delay_is_named_and_every_barcode_tick_read_that_much_before_its_frame(tmp_path):
+    truth, (_, plain) = emit(tmp_path)
+    _, _, [(header, delayed)] = _session(tmp_path / "delayed", syncbox_barcode_delay_us=1_500)
+    assert header.barcode_delay_us == 1_500
+    assert [r.tick_us for r in emitted(delayed)][1:] == [r.tick_us - 1_500 for r in emitted(plain)][1:]
+
+
+def test_the_process_starts_with_its_late_first_frame_which_no_device_records(tmp_path):
+    """One interval before session time's first barcode, at its clock's
+    origin; a reader drops it."""
+    truth, (_, records) = emit(tmp_path)
+    first = emitted(records)[0]
+    assert (first.tick_us, first.value) == (0, truth.barcodes[0][0] - 1)
+    assert first.value not in {value for value, _ in truth.barcodes}
+
+
+def test_a_roll_in_place_writes_a_second_segment_of_the_same_process_on_the_same_clock(tmp_path):
+    """The barcode steps by two across it, the skipped value emitted by
+    nobody, and the ticks run on."""
+    from wl_preproc.synth.recipe import SyncBoxRollSpec
+
+    _, truth, [(first, before), (second, after)] = _session(tmp_path, syncbox_roll=SyncBoxRollSpec(at_s=7.5))
+    assert (first.segment_index, second.segment_index) == (0, 1)
+    assert first.process_uuid == second.process_uuid
+    assert emitted(after)[0].value - emitted(before)[-1].value == 2
+    assert emitted(after)[0].value - 1 not in {value for value, _ in truth.barcodes}
+    assert min(r.tick_us for r in after) > max(r.tick_us for r in before)
+
+
+def test_a_gap_leaves_its_records_out_of_the_folder_but_not_out_of_the_devices(tmp_path):
+    from wl_preproc.synth.recipe import SyncBoxRollSpec
+
+    _, truth, [(_, before), (_, after)] = _session(tmp_path, syncbox_roll=SyncBoxRollSpec(at_s=5.5, gap_s=3.0))
+    logged = {r.value for r in emitted(before) + emitted(after)}
+    assert {value for value, start_s in truth.barcodes if 5.5 <= start_s < 8.5} == {1_000_006, 1_000_007, 1_000_008}
+    assert not logged & {1_000_006, 1_000_007, 1_000_008}
+    assert not [r for r in before + after if isinstance(r, CodeWord) and 6_500_000 <= r.tick_us < 9_500_000]
+
+
+def test_a_restart_is_a_new_process(tmp_path):
+    from wl_preproc.synth.recipe import SyncBoxRestartSpec
+
+    _, _, [(first, _), (second, _)] = _session(tmp_path, syncbox_restart=SyncBoxRestartSpec(at_s=7.5))
+    assert first.process_uuid != second.process_uuid
+
+
+def test_losses_are_written_as_the_box_writes_them(tmp_path):
+    from wl_sync.log import Loss
+
+    from wl_preproc.synth.recipe import SyncBoxLossSpec
+
+    _, _, [(_, records)] = _session(tmp_path, syncbox_losses=(
+        SyncBoxLossSpec(cause="desync", count=3, from_s=4.0),
+        SyncBoxLossSpec(cause="buffer_overflow", count=2, from_s=2.0, to_s=2.5, gpio=GPIO_MAP["pd1_comp"])))
+    assert [r for r in records if isinstance(r, Loss)] == [
+        Loss(tick_us=3_500_000, path="E", gpio=GPIO_MAP["pd1_comp"], count=2, bound="exact",
+             cause="buffer_overflow", before_us=3_000_000, after_us=3_500_000),
+        Loss(tick_us=5_000_000, path="W", gpio=None, count=3, bound="at_least", cause="desync",
+             before_us=5_000_000, after_us=None)]
+
+
+def test_each_segment_closes_with_wl_syncs_trailer(tmp_path):
+    """Part 1's deferred finding: a hand-written trailer was not wl-sync's."""
+    emit(tmp_path)
+    trailer = (tmp_path / "seg-000.log").read_text(encoding="utf-8").splitlines()[-1]
+    assert trailer.startswith("T,2027-03-14T09:00:00+00:00,999999,")
+
+
+@pytest.mark.parametrize(("update", "match"), [
+    ({"syncbox_restart": {"at_s": 7.5}, "syncbox_roll": {"at_s": 3.0}}, "together"),
+    ({"syncbox_restart": {"at_s": 14.0, "gap_s": 1.2}}, "syncbox_restart resumes"),
+    ({"syncbox_roll": {"at_s": 14.5, "gap_s": 1.0}}, "syncbox_roll resumes"),
+    ({"syncbox_losses": ({"cause": "desync", "from_s": 2.0, "to_s": 3.0},)}, "after_us"),
+    ({"syncbox_losses": ({"cause": "coalesced", "from_s": 2.0, "to_s": 2.1},)}, "names its line"),
+    ({"syncbox_losses": ({"cause": "desync", "from_s": 20.0},)}, "outside the session"),
+])
+def test_a_sync_box_change_the_session_cannot_hold_is_refused(update, match):
+    """Part 1's deferred finding: nothing checked the restart's fit to the
+    session, and the roll and the losses are checked the same way."""
+    from pydantic import ValidationError
+
+    from wl_preproc.synth.recipe import SessionRecipe
+
+    with pytest.raises(ValidationError, match=match):
+        SessionRecipe.model_validate({**CI_RECIPE.model_dump(), **update})
+
+
+def test_a_session_generated_again_over_its_folder_writes_its_log_again(tmp_path):
+    """wl-sync's writer refuses a file that exists, as the box must; a
+    fixture regenerated over its own folder is not the box."""
+    from wl_preproc.synth.session import generate_session
+
+    generate_session(tmp_path, CI_RECIPE)
+    generate_session(tmp_path, CI_RECIPE)
+    assert read_log(tmp_path / CI_RECIPE.session_id / "syncbox" / "seg-000.log")[1]
diff --git a/tests/timebase/test_syncbox_segments.py b/tests/timebase/test_syncbox_segments.py
index 67068f7..d62ba7e 100644
--- a/tests/timebase/test_syncbox_segments.py
+++ b/tests/timebase/test_syncbox_segments.py
@@ -43,7 +43,7 @@ def test_a_restarted_box_writes_two_segments_on_unrelated_clocks(tmp_path):
     first, second = segments.syncbox_logs(session_dir)
     assert (first.path.name, first.segment_index, second.path.name, second.segment_index) == (
         "seg-000.log", 0, "seg-001.log", 1)
-    assert [value for value, _ in first.barcodes] == list(range(1_000_000, 1_000_008))
+    assert [value for value, _ in first.barcodes] == [999_999, *range(1_000_000, 1_000_008)]
     assert [value for value, _ in second.barcodes] == list(range(1_000_009, 1_000_015))
     assert second.barcodes[0][1] < first.barcodes[-1][1]
 
@@ -220,3 +220,47 @@ def test_each_log_is_read_once_however_many_answers_a_stage_asks_for(tmp_path, m
     segments.scan_system("syncbox", session_dir / "syncbox")
     syncbox_codes(session_dir)
     assert sorted(reads) == ["seg-000.log", "seg-001.log"]
+
+
+def _synthetic(tmp_path, **update):
+    recipe = CI_RECIPE.model_copy(update=update)
+    truth = generate_session(tmp_path, recipe)
+    return tmp_path / recipe.session_id, truth
+
+
+def test_a_synthetic_session_is_timed_from_its_first_barcode_with_the_late_frame_dropped(tmp_path):
+    session_dir, truth = _synthetic(tmp_path)
+    reference = segments.session_reference(session_dir)
+    assert reference == pytest.approx({value: start_s for value, start_s in truth.barcodes}, abs=1e-6)
+
+
+def test_a_synthetic_roll_in_place_is_one_clock_and_every_code_is_placed(tmp_path):
+    from wl_preproc.synth.recipe import SyncBoxRollSpec
+
+    session_dir, truth = _synthetic(tmp_path, syncbox_roll=SyncBoxRollSpec(at_s=7.5))
+    session = segments.read_syncbox_session(session_dir)
+    assert (session.follows, session.roles()) == ((None, "adjacent"), ["session_time", "session_time"])
+    assert session.reference() == pytest.approx(dict(truth.barcodes), abs=1e-6)
+    codes = syncbox_codes(session_dir)
+    assert [word for _, word in codes] == [word for _, word in truth.code_words]
+    assert [time_s for time_s, _ in codes] == pytest.approx([time_s for time_s, _ in truth.code_words], abs=1e-5)
+
+
+def test_a_synthetic_gap_keeps_one_clock_and_names_the_stretch_whose_codes_are_elsewhere(tmp_path):
+    from wl_preproc.synth.recipe import SyncBoxRollSpec
+
+    session_dir, truth = _synthetic(tmp_path, syncbox_roll=SyncBoxRollSpec(at_s=5.5, gap_s=3.0))
+    session = segments.read_syncbox_session(session_dir)
+    assert session.follows == (None, "gap")
+    ((index, start_us, stop_us),) = session.gaps()
+    assert index == 1 and session.seconds(start_us) < 5.5 and 8.5 <= session.seconds(stop_us) < 9.5
+    assert [word for _, word in syncbox_codes(session_dir)] == [
+        word for time_s, word in truth.code_words if not 5.5 <= time_s < 8.5]
+
+
+def test_a_measured_barcode_delay_leaves_session_time_where_the_devices_see_it(tmp_path):
+    """The box reads each barcode's tick that long before its frame is on
+    the wire, which every device records: the reader adds it back."""
+    session_dir, truth = _synthetic(tmp_path, syncbox_barcode_delay_us=1_500)
+    assert segments.session_reference(session_dir) == pytest.approx(dict(truth.barcodes), abs=1e-6)
+    assert syncbox_codes(session_dir)[0][0] == pytest.approx(truth.code_words[0][0], abs=1e-6)
PATCH
```

- [ ] **Step 2: Run them and see them fail.** `test_a_session_generated_again_over_its_folder_writes_its_log_again` and the clock tests of a synthetic session pass already: they pin what Step 3 keeps (a regenerated folder, and t = 0 at the timeline's first barcode).

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_syncbox_restart.py tests/schema/test_syncbox_roll.py tests/synth/test_syncbox.py tests/timebase/test_syncbox_segments.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `20 failed, 35 passed, 2 errors`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/synth/recipe.py b/wl_preproc/synth/recipe.py
index be6342c..1e51f3c 100644
--- a/wl_preproc/synth/recipe.py
+++ b/wl_preproc/synth/recipe.py
@@ -4,10 +4,12 @@ call with thirty keyword arguments."""
 from __future__ import annotations
 
 import datetime
+from collections.abc import Callable
 from enum import Enum
 from typing import Any
 
 from pydantic import BaseModel, ConfigDict, Field, model_validator
+from wl_sync.log import LOSS_SHAPES, Loss
 
 from wl_preproc.contracts.events import TaskTypeCode
 from wl_preproc.contracts.paths import SYSTEMS
@@ -177,6 +179,57 @@ class SyncBoxRestartSpec(BaseModel):
     gap_s: float = Field(default=1.2, ge=0)
 
 
+class SyncBoxRollSpec(BaseModel):
+    """The sync box's process closed its segment at `at_s` (session time) and
+    went on in the next one, in the same folder, the same process on the same
+    clock (design spec `2026-11-04-syncbox-header-design.md` sections 1 and 10).
+    With `gap_s` 0 it is the roll in place at NTP's first sync: the frame grid
+    is placed again, and the value of the first frame due after it is never
+    emitted, so the barcode steps by two across it. With `gap_s` above 0 the
+    box recorded elsewhere for that long and came back: it emitted barcodes
+    throughout, which every device records, but its records of that stretch
+    are not in this folder."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    at_s: float = Field(gt=0)
+    gap_s: float = Field(default=0.0, ge=0)
+
+
+class SyncBoxLossSpec(BaseModel):
+    """One `L` record the synthetic box writes: its `cause`, from wl-sync's
+    closed set, its `count`, its window in session time, `from_s` to `to_s`,
+    either end None for open, and `gpio`, the line an edge cause names. Built
+    through wl-sync's own `Loss` (`record`), so a fixture cannot write one the
+    format does not define."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    cause: str
+    count: int = Field(default=1, ge=1)
+    from_s: float | None = None
+    to_s: float | None = None
+    gpio: int | None = None
+
+    @property
+    def at_s(self) -> float:
+        """Where the record sits in its path's stream: at its window's end, or
+        at its start where the window has no end (a `desync`'s)."""
+        if self.to_s is None and self.from_s is None:
+            raise ValueError("a sync box loss needs an end of its window, which places its record")
+        return self.to_s if self.to_s is not None else self.from_s
+
+    def record(self, tick: Callable[[float], int]) -> Loss:
+        """The record, its session times converted to ticks by `tick`."""
+        if self.cause not in LOSS_SHAPES:
+            raise ValueError(f"a sync box loss's cause is one of {sorted(LOSS_SHAPES)}, got {self.cause!r}")
+        shape = LOSS_SHAPES[self.cause]
+        before = None if self.from_s is None else tick(self.from_s)
+        after = None if self.to_s is None else tick(self.to_s)
+        return Loss(tick_us=tick(self.at_s), path=shape.path, gpio=self.gpio, count=self.count, bound=shape.bound,
+                    cause=self.cause, before_us=before, after_us=after)
+
+
 class RigWarningSpec(BaseModel):
     """One warning the synthetic session accepted, as wl-xcon's
     `record.py::SessionRecord.warning` writes it: `run` is wl-xcon's 0-based
@@ -281,6 +334,17 @@ class SessionRecipe(BaseModel):
     # The sync box restarting mid-session; None by default, so every existing
     # profile is byte-identical.
     syncbox_restart: SyncBoxRestartSpec | None = None
+    # The sync box rolling to a new segment and running on, in the same folder;
+    # None by default (design spec `2026-11-04-syncbox-header-design.md`
+    # section 10).
+    syncbox_roll: SyncBoxRollSpec | None = None
+    # What the box's header says of its clocks, and its measured barcode delay:
+    # "fake", the test backend its unit runs until bench acceptance, and
+    # unmeasured, by default.
+    syncbox_timebase: str = "fake"
+    syncbox_barcode_delay_us: int | None = Field(default=None, ge=0)
+    # The `L` records the box writes; none by default.
+    syncbox_losses: tuple[SyncBoxLossSpec, ...] = ()
     # wl-xcon's fault shapes (design spec `2026-10-01-runs-and-trials-design.md`
     # section 7), both empty by default so every existing profile is
     # byte-identical. `unclosed_blocks`: block numbers (from 1) whose BLOCK_END
@@ -508,6 +572,20 @@ class SessionRecipe(BaseModel):
                                  "first bank's sites while the second run's .meta names others")
             if Fault.TRUNCATED_FILE in self.faults:
                 raise ValueError("TRUNCATED_FILE and a restart together: which run's file is cut is undefined")
+        # The sync box's own changes (part 1's deferred finding: nothing
+        # checked its restart).
+        if self.syncbox_restart is not None and self.syncbox_roll is not None:
+            raise ValueError("syncbox_restart and syncbox_roll together: which process the second segment is "
+                             "would be undefined")
+        for name, change in (("syncbox_restart", self.syncbox_restart), ("syncbox_roll", self.syncbox_roll)):
+            if change is not None and change.at_s + change.gap_s >= self.duration_s:
+                raise ValueError(f"{name} resumes at {change.at_s + change.gap_s} s, at or after the session ends "
+                                 f"at {self.duration_s} s; its second segment would record nothing")
+        for loss in self.syncbox_losses:
+            if any(time_s is not None and not 0.0 <= time_s < self.duration_s for time_s in (loss.from_s, loss.to_s)):
+                raise ValueError(f"a sync box loss's window, {loss.from_s} to {loss.to_s} s, lies outside the "
+                                 f"session's {self.duration_s} s")
+            loss.record(lambda time_s: round((time_s + 1.0) * 1e6))
         return self
 
 
diff --git a/wl_preproc/synth/syncbox.py b/wl_preproc/synth/syncbox.py
index 2ec7c58..97e85c9 100644
--- a/wl_preproc/synth/syncbox.py
+++ b/wl_preproc/synth/syncbox.py
@@ -6,27 +6,36 @@ board's (`wl_sync.pins.GPIO_MAP`). Until 2026-10-22 this drew each barcode as
 edges on GPIO17 and named three pins of this project's own: a log the box does
 not write, read by a reader no real log could satisfy (design spec
 `2026-10-22-syncbox-records-and-segments-design.md` section 3). Every type
-comes from `wl_sync.log`, so a change of format there breaks these fixtures
-loudly instead of letting the generator and the pipeline drift apart.
+comes from `wl_sync.log`, and every segment is written by wl-sync's own
+`SegmentWriter`, trailer and all, so a change of format there breaks these
+fixtures loudly instead of letting the generator and the pipeline drift apart.
+
+Since 2026-10-30 every header names its process, its clocks and, once
+measured, the barcode delay, and each process's first frame goes out later
+than its tick says (design spec `2026-11-04-syncbox-header-design.md` sections
+1 and 10). The session's first process starts with such a frame, one barcode
+interval before session time's first, which no device records; a restarted
+process's first frame is its first barcode.
 """
 
 from __future__ import annotations
 
+import uuid
 from pathlib import Path
 
-from wl_sync.log import SCHEMA_VERSION, BarcodeEmitted, CodeWord, Record, SyncBoxLogHeader, write_log
+from wl_sync.log import SCHEMA_VERSION, BarcodeEmitted, CodeWord, Record, SyncBoxLogHeader
 from wl_sync.pins import GPIO_MAP
-from wl_sync.segment import segment_name
+from wl_sync.segment import SegmentWriter, segment_name
 
 from wl_preproc.synth.recipe import SYNTH_EPOCH, SessionRecipe
-from wl_preproc.synth.timeline import apply_drift
+from wl_preproc.synth.timeline import BARCODE_INTERVAL_S, apply_drift
 from wl_preproc.synth.truth import GroundTruth
 
-# The log's tick origin is not session time, deliberately. Two reasons: the
-# decoder needs an idle before the first frame or it correctly refuses it, and a
-# fixture where tick == session time would let a pipeline bug that ignores the
-# offset pass every alignment test. Each system gets a *different* pre-roll for
-# the same reason — see spikeglx.py.
+# The log's tick origin is not session time, deliberately: a fixture where
+# tick == session time would let a pipeline bug that ignores the offset pass
+# every alignment test. Each system gets a *different* pre-roll for the same
+# reason -- see spikeglx.py. The process's first, late frame goes out at its
+# clock's origin, one interval before session time's first barcode.
 SYNCBOX_PRE_ROLL_S = 1.0
 # A restarted process's first tick before its first barcode. Shorter than the
 # first segment's pre-roll, so the second segment's ticks start again below the
@@ -35,46 +44,70 @@ SYNCBOX_PRE_ROLL_S = 1.0
 SYNCBOX_RESTART_PRE_ROLL_S = 0.6
 
 
+def process_uuid(recipe: SessionRecipe, number: int) -> str:
+    """The synthetic box's `number`th process, as its header names it: one
+    canonical UUID per process, fixed by the recipe's seed."""
+    return str(uuid.UUID(int=(recipe.seed << 32) + number))
+
+
 def write_syncbox_log(
     path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
 ) -> None:
     """The whole session as one segment, the first."""
     _write_segment(path, recipe, truth, index=0, since_s=None, until_s=None, origin_s=0.0,
-                   pre_roll_s=SYNCBOX_PRE_ROLL_S, drift_ppm=drift_ppm)
+                   pre_roll_s=SYNCBOX_PRE_ROLL_S, drift_ppm=drift_ppm, process=0)
 
 
 def write_syncbox_segments(directory: Path, recipe: SessionRecipe, truth: GroundTruth) -> list[Path]:
     """The session's segments, named as the box names them (`seg-000.log`):
-    one, or, for `recipe.syncbox_restart`, the second written after the
-    restart on a clock that starts again, without what the box missed while
-    down. Never drifted: session time is the box's own timeline."""
-    restart = recipe.syncbox_restart
+    one; or, for `recipe.syncbox_restart`, the second written by a new process
+    on a clock that starts again, without what the box missed while down; or,
+    for `recipe.syncbox_roll`, the second written by the same process on the
+    same clock, from the end of its gap. Never drifted: session time is the
+    box's own timeline."""
     first = directory / segment_name(0)
-    if restart is None:
-        write_syncbox_log(first, recipe, truth)
-        return [first]
-    resumed_s = restart.at_s + restart.gap_s
     second = directory / segment_name(1)
-    _write_segment(first, recipe, truth, index=0, since_s=None, until_s=restart.at_s, origin_s=0.0,
-                   pre_roll_s=SYNCBOX_PRE_ROLL_S, drift_ppm=0.0)
-    _write_segment(second, recipe, truth, index=1, since_s=resumed_s, until_s=None, origin_s=resumed_s,
-                   pre_roll_s=SYNCBOX_RESTART_PRE_ROLL_S, drift_ppm=0.0)
-    return [first, second]
+    restart, roll = recipe.syncbox_restart, recipe.syncbox_roll
+    if restart is not None:
+        resumed_s = restart.at_s + restart.gap_s
+        _write_segment(first, recipe, truth, index=0, since_s=None, until_s=restart.at_s, origin_s=0.0,
+                       pre_roll_s=SYNCBOX_PRE_ROLL_S, drift_ppm=0.0, process=0)
+        _write_segment(second, recipe, truth, index=1, since_s=resumed_s, until_s=None, origin_s=resumed_s,
+                       pre_roll_s=SYNCBOX_RESTART_PRE_ROLL_S, drift_ppm=0.0, process=1)
+        return [first, second]
+    if roll is not None:
+        _write_segment(first, recipe, truth, index=0, since_s=None, until_s=roll.at_s, origin_s=0.0,
+                       pre_roll_s=SYNCBOX_PRE_ROLL_S, drift_ppm=0.0, process=0)
+        _write_segment(second, recipe, truth, index=1, since_s=roll.at_s + roll.gap_s, until_s=None, origin_s=0.0,
+                       pre_roll_s=SYNCBOX_PRE_ROLL_S, drift_ppm=0.0, process=0)
+        return [first, second]
+    write_syncbox_log(first, recipe, truth)
+    return [first]
 
 
 def _write_segment(path: Path, recipe: SessionRecipe, truth: GroundTruth, *, index: int, since_s: float | None,
-                   until_s: float | None, origin_s: float, pre_roll_s: float, drift_ppm: float) -> None:
+                   until_s: float | None, origin_s: float, pre_roll_s: float, drift_ppm: float,
+                   process: int) -> None:
     """The records of session time `[since_s, until_s)`, ticked from `origin_s`
-    after `pre_roll_s` of this segment's own clock."""
+    after `pre_roll_s` of this segment's process's clock; a barcode's tick read
+    `barcode_delay_us` before its frame reaches the wire."""
     def kept(time_s: float) -> bool:
         return (since_s is None or time_s >= since_s) and (until_s is None or time_s < until_s)
 
     def tick(time_s: float) -> int:
         return int(round((apply_drift(time_s - origin_s, drift_ppm) + pre_roll_s) * 1e6))
 
-    records: list[Record] = [BarcodeEmitted(tick_us=tick(start_s), value=value)
+    delay_us = recipe.syncbox_barcode_delay_us or 0
+    records: list[Record] = [BarcodeEmitted(tick_us=tick(start_s) - delay_us, value=value)
                              for value, start_s in truth.barcodes if kept(start_s)]
+    if since_s is None and truth.barcodes:
+        # The session's first process's first frame, late by an amount nothing
+        # measures and recorded by no device, which a reader drops.
+        first_value, first_s = truth.barcodes[0]
+        records.insert(0, BarcodeEmitted(tick_us=max(0, tick(first_s - BARCODE_INTERVAL_S) - delay_us),
+                                         value=first_value - 1))
     records += [CodeWord(tick_us=tick(time_s), word=word) for time_s, word in truth.code_words if kept(time_s)]
+    records += [loss.record(tick) for loss in recipe.syncbox_losses if kept(loss.at_s)]
     records.sort(key=lambda record: record.tick_us)
 
     header = SyncBoxLogHeader(
@@ -85,5 +118,15 @@ def _write_segment(path: Path, recipe: SessionRecipe, truth: GroundTruth, *, ind
         written_at=SYNTH_EPOCH,
         gpio_map=GPIO_MAP,
         segment_index=index,
+        process_uuid=process_uuid(recipe, process),
+        barcode_delay_us=recipe.syncbox_barcode_delay_us,
+        timebase=recipe.syncbox_timebase,
     )
-    write_log(path, header, records)
+    # The box's writer refuses a file that exists, a collision on the box; a
+    # synthetic session generated again over its own folder writes its log
+    # again, as it always has.
+    path.unlink(missing_ok=True)
+    writer = SegmentWriter(path, header)
+    for record in records:
+        writer.write(record)
+    writer.close(SYNTH_EPOCH)
diff --git a/wl_preproc/synth/timeline.py b/wl_preproc/synth/timeline.py
index 266af40..608c096 100644
--- a/wl_preproc/synth/timeline.py
+++ b/wl_preproc/synth/timeline.py
@@ -98,10 +98,14 @@ def build_timeline(recipe: SessionRecipe) -> GroundTruth:
     barcodes: list[tuple[int, float]] = []
     time_s = 0.0
     value = 1_000_000
-    restart = recipe.syncbox_restart
+    restart, roll = recipe.syncbox_restart, recipe.syncbox_roll
     while time_s < recipe.duration_s:
-        # A box that is down emits nothing; its count of seconds runs on.
-        if restart is None or not restart.at_s <= time_s < restart.at_s + restart.gap_s:
+        # A box that is down emits nothing; its count of seconds runs on. A
+        # roll in place places the frame grid again, and the first frame due
+        # after it is never emitted (wl-sync's "+1 or +2").
+        down = restart is not None and restart.at_s <= time_s < restart.at_s + restart.gap_s
+        skipped = roll is not None and roll.gap_s == 0 and roll.at_s <= time_s < roll.at_s + BARCODE_INTERVAL_S
+        if not down and not skipped:
             barcodes.append((value, time_s))
         value += 1
         time_s += BARCODE_INTERVAL_S
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_syncbox_restart.py tests/schema/test_syncbox_roll.py tests/synth/test_syncbox.py tests/timebase/test_syncbox_segments.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `57 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events tests/nwb tests/schema/test_core.py tests/schema/test_coverage.py tests/schema/test_eye_populate.py tests/schema/test_segment_populate.py tests/schema/test_session_clock.py tests/schema/test_spikeglx_restart.py tests/schema/test_syncbox_restart.py tests/schema/test_syncbox_roll.py tests/schema/test_timebase.py tests/synth tests/timebase -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `575 passed, 1 skipped`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "no-late-first-frame",
  "wl_preproc/synth/syncbox.py",
  "    if since_s is None and truth.barcodes:",
  "    if False:",
  "tests/synth/test_syncbox.py"
 ],
 [
  "roll-in-place-skips-nothing",
  "wl_preproc/synth/timeline.py",
  "        skipped = roll is not None and roll.gap_s == 0 and",
  "        skipped = False and roll is not None and roll.gap_s == 0 and",
  "tests/synth/test_syncbox.py"
 ],
 [
  "roll-is-a-new-process",
  "wl_preproc/synth/syncbox.py",
  "since_s=roll.at_s + roll.gap_s, until_s=None, origin_s=0.0,\n                       pre_roll_s=SYNCBOX_PRE_ROLL_S, drift_ppm=0.0, process=0)",
  "since_s=roll.at_s + roll.gap_s, until_s=None, origin_s=0.0,\n                       pre_roll_s=SYNCBOX_PRE_ROLL_S, drift_ppm=0.0, process=1)",
  "tests/synth/test_syncbox.py"
 ],
 [
  "restart-is-the-same-process",
  "wl_preproc/synth/syncbox.py",
  "pre_roll_s=SYNCBOX_RESTART_PRE_ROLL_S, drift_ppm=0.0, process=1)",
  "pre_roll_s=SYNCBOX_RESTART_PRE_ROLL_S, drift_ppm=0.0, process=0)",
  "tests/synth/test_syncbox.py"
 ],
 [
  "delay-not-read-early",
  "wl_preproc/synth/syncbox.py",
  "BarcodeEmitted(tick_us=tick(start_s) - delay_us, value=value)",
  "BarcodeEmitted(tick_us=tick(start_s), value=value)",
  "tests/synth/test_syncbox.py"
 ],
 [
  "timebase-not-named",
  "wl_preproc/synth/syncbox.py",
  "        timebase=recipe.syncbox_timebase,",
  "        timebase=None,",
  "tests/synth/test_syncbox.py"
 ],
 [
  "losses-not-written",
  "wl_preproc/synth/syncbox.py",
  "    records += [loss.record(tick) for loss in recipe.syncbox_losses if kept(loss.at_s)]",
  "    records += []",
  "tests/synth/test_syncbox.py"
 ],
 [
  "resuming-after-the-end-allowed",
  "wl_preproc/synth/recipe.py",
  "            if change is not None and change.at_s + change.gap_s >= self.duration_s:",
  "            if False:",
  "tests/synth/test_syncbox.py"
 ],
 [
  "restart-and-roll-together-allowed",
  "wl_preproc/synth/recipe.py",
  "        if self.syncbox_restart is not None and self.syncbox_roll is not None:",
  "        if False:",
  "tests/synth/test_syncbox.py"
 ],
 [
  "loss-outside-the-session-allowed",
  "wl_preproc/synth/recipe.py",
  "            if any(time_s is not None and not 0.0 <= time_s < self.duration_s",
  "            if False and any(time_s is not None and not 0.0 <= time_s < self.duration_s",
  "tests/synth/test_syncbox.py"
 ],
 [
  "gap-records-kept",
  "wl_preproc/synth/syncbox.py",
  "since_s=roll.at_s + roll.gap_s, until_s=None",
  "since_s=roll.at_s, until_s=None",
  "tests/synth/test_syncbox.py"
 ],
 [
  "no-trailer",
  "wl_preproc/synth/syncbox.py",
  "    writer.close(SYNTH_EPOCH)",
  "    writer.flush()",
  "tests/synth/test_syncbox.py"
 ],
 [
  "regenerated-log-refused",
  "wl_preproc/synth/syncbox.py",
  "    path.unlink(missing_ok=True)\n",
  "",
  "tests/synth/test_syncbox.py"
 ]
]
```

Expected: 13 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(synth): the synthetic box writes its log as the box does since 2026-10-30 -- each segment names its process and its clocks, and the measured delay when given; each session's first process starts with its late first frame, which a reader drops; a roll in place, a gap within one process, a restart's new process, and L records built through wl-sync's Loss; the box's changes checked against the session (section 10; part 1's deferred finding)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 4: Each segment and each loss recorded, and the timing check

§6 and §8, decision 3.5 and amendments 4-6: `timebase.SyncBoxSegment` gains each segment's process, clocks, how it follows the one before, and its last code word's tick, and its part `Loss` holds each `L` record as written and each gap, placed in session time; a database declared before refuses the stage and names its `ALTER TABLE` (never run here); `TimingProvenance` fails the timing check for a `desync`, or for code words lost or a gap inside a measured run, and a lost edge is reported only.

**Files:**
- Modify: `wl_preproc/timebase/segments.py` (`SegmentLoss`, `GAP`, `SyncBoxSession.losses`, `.fails_the_timing_check`, `timing_check_fails`)
- Modify: `wl_preproc/schema/timebase.py` (`SyncBoxSegment`'s columns, its `Loss`, its guard and `make`; `TimingProvenance`'s check)
- Create: `tests/schema/test_syncbox_losses.py`
- Test: `tests/timebase/test_syncbox_segments.py`

**Interfaces:**
- Consumes: Task 2's `SyncBoxSession`; Task 3's `SyncBoxLossSpec`, `SyncBoxRollSpec`, `process_uuid`.
- Produces: `segments.timing_check_fails(path, cause, start_s, stop_s, runs) -> bool`; `SegmentLoss`;
  `SyncBoxSession.losses()`, `.fails_the_timing_check(runs)`; `timebase.SyncBoxSegment.process_uuid`,
  `.timebase`, `.follows`, `.last_code_tick_us`; `timebase.SyncBoxSegment.Loss`; `timebase._HEADER_COLUMNS`,
  `_lacks_header_columns()`, `_header_statement()`; the module fixture `landed` in
  `tests/schema/test_syncbox_losses.py`, yielding `(keys, report, root)`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/schema/test_syncbox_losses.py b/tests/schema/test_syncbox_losses.py
new file mode 100644
index 0000000..4574eb1
--- /dev/null
+++ b/tests/schema/test_syncbox_losses.py
@@ -0,0 +1,146 @@
+"""The sync box's losses, stored and checked (design spec
+`2026-11-04-syncbox-header-design.md` sections 6 and 8): each `L` record kept
+as the box wrote it, and each gap, in `timebase.SyncBoxSegment.Loss`, placed in
+session time; a desync, or code words lost in a measured run, fail the
+session's timing check, and a lost edge does not. A database declared before
+the header named its process is refused, and told how to alter it."""
+
+from __future__ import annotations
+
+import pytest
+
+from tests.identities import new_animal
+
+
+@pytest.fixture(scope="module")
+def landed(dj_conn, prefix, tmp_path_factory):
+    """Four `ci` sessions of one animal on one day, in runs, after one daemon
+    pass, deleted after the module: the box's word pump stopped by a desync at
+    6 s; an edge lost on a photodiode inside the first run; a gap of 3 s from
+    5.5 s, inside a run; and a box whose code words are on a split clock.
+    Yields their keys, by name, the daemon's report, and the folder they were
+    landed from."""
+    from wl_sync.pins import GPIO_MAP
+
+    from wl_preproc import daemon
+    from wl_preproc.schema import pipeline
+    from wl_preproc.synth.recipe import RECIPES, SyncBoxLossSpec, SyncBoxRollSpec
+    from wl_preproc.synth.session import generate_session
+
+    from tests.schema.test_eye_populate import _land
+
+    daemon.activate_all(prefix=prefix)
+    animal = new_animal()
+    root = tmp_path_factory.mktemp("losses")
+    keys = {}
+    for name, update in (
+        ("desync", {"syncbox_losses": (SyncBoxLossSpec(cause="desync", count=2, from_s=6.0),)}),
+        ("edge", {"syncbox_losses": (SyncBoxLossSpec(cause="buffer_overflow", count=3, from_s=1.2, to_s=1.4,
+                                                     gpio=GPIO_MAP["pd1_comp"]),)}),
+        ("gap", {"syncbox_roll": SyncBoxRollSpec(at_s=5.5, gap_s=3.0)}),
+        ("split", {"syncbox_timebase": "split"}),
+    ):
+        session = animal.session(on=animal.first_day)
+        recipe = RECIPES["ci"].model_copy(update={"subject": session.subject, "session_id": session.session_id,
+                                                  "runs": True, **update})
+        generate_session(root, recipe)
+        keys[name] = _land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems)
+    report = daemon.run_once(prefix=prefix)
+    yield keys, report, root
+    for key in keys.values():
+        (pipeline.Session & key).delete(prompt=False)
+
+
+def test_each_loss_is_stored_as_the_box_wrote_it_and_placed_in_session_time(landed):
+    from wl_sync.pins import GPIO_MAP
+
+    from wl_preproc.schema import timebase
+
+    keys, _, _ = landed
+    (row,) = (timebase.SyncBoxSegment.Loss & keys["edge"]).to_dicts()
+    assert ({field: row[field] for field in ("segment_index", "loss_number", "cause", "path", "gpio", "lost",
+                                             "bound", "tick_us", "before_us", "after_us")}
+            == {"segment_index": 0, "loss_number": 1, "cause": "buffer_overflow", "path": "E",
+                "gpio": GPIO_MAP["pd1_comp"], "lost": 3, "bound": "exact", "tick_us": 2_400_000,
+                "before_us": 2_200_000, "after_us": 2_400_000})
+    assert (row["start_s"], row["stop_s"]) == pytest.approx((1.2, 1.4))
+    (gap,) = (timebase.SyncBoxSegment.Loss & keys["gap"]).to_dicts()
+    assert (gap["segment_index"], gap["cause"], gap["lost"]) == (1, "gap", None)
+    assert gap["start_s"] < 5.5 and 8.5 <= gap["stop_s"] < 9.5
+
+
+def test_a_desync_or_a_gap_in_a_run_fails_the_timing_check_and_a_lost_edge_does_not(landed):
+    from wl_preproc.schema import timebase
+
+    keys, report, _ = landed
+    assert {name: (timebase.TimingProvenance & key).fetch1("tier") for name, key in keys.items()} == {
+        "desync": "D", "edge": "A", "gap": "D", "split": "A"}
+    assert not [error for error in report["errors"] if any(key["subject"] in str(error) for key in keys.values())]
+
+
+def test_each_segment_records_its_process_its_clocks_and_how_it_follows(landed):
+    from wl_preproc.schema import timebase
+    from wl_preproc.synth.recipe import RECIPES
+    from wl_preproc.synth.syncbox import process_uuid
+
+    keys, _, _ = landed
+    rows = (timebase.SyncBoxSegment & keys["gap"]).to_dicts(order_by="segment_index")
+    process = process_uuid(RECIPES["ci"], 0)
+    assert [(row["process_uuid"], row["timebase"], row["follows"], row["role"]) for row in rows] == [
+        (process, "fake", None, "session_time"), (process, "fake", "gap", "session_time")]
+    assert all(row["last_code_tick_us"] > 0 for row in rows)
+    assert (timebase.SyncBoxSegment & keys["split"]).fetch1("timebase") == "split"
+
+
+def test_a_database_without_the_header_columns_records_nothing_and_names_the_statement(landed, monkeypatch):
+    """As `EyeValidity` refuses without `frac_feature_lost`: DataJoint adds
+    no column to an existing table and reads a table's columns once per
+    process, so the stage refuses before any job is reserved and says how to
+    fix it."""
+    from wl_preproc.schema import timebase
+
+    assert not timebase._lacks_header_columns()
+    monkeypatch.setattr(timebase, "_lacks_header_columns", lambda: True)
+    with pytest.raises(RuntimeError) as raised:
+        timebase.SyncBoxSegment.populate()
+    assert timebase._header_statement() in str(raised.value)
+    assert "restart" in str(raised.value)
+
+
+def test_a_table_read_without_the_columns_is_refused(landed, monkeypatch):
+    from types import SimpleNamespace
+
+    from wl_preproc.schema import timebase
+
+    names = [name for name in timebase.SyncBoxSegment.heading.names if name != "follows"]
+    monkeypatch.setattr(timebase.SyncBoxSegment, "_heading", SimpleNamespace(names=names))
+    assert timebase._lacks_header_columns()
+    with pytest.raises(RuntimeError, match="has no columns"):
+        timebase.SyncBoxSegment.populate()
+
+
+def test_the_statement_restores_the_columns_as_datajoint_declares_them(landed):
+    """Dropped and restored by the named statement on a copy of the table:
+    type, nullability, default, comment and place match what declaring it
+    wrote, and no row of the table itself is touched."""
+    import datajoint as dj
+
+    from wl_preproc.schema import timebase
+
+    table = timebase.SyncBoxSegment.full_table_name
+    copy = table[:-1] + '_restore_check`'
+    statement = timebase._header_statement()
+    assert statement.count(table) == 1
+
+    def columns(name):
+        return [row[:2] + row[3:6] + row[8:9] for row in dj.conn().query(f"SHOW FULL COLUMNS FROM {name}").fetchall()]
+
+    dj.conn().query(f"CREATE TABLE {copy} LIKE {table}")
+    try:
+        dj.conn().query(f"ALTER TABLE {copy} " + ", ".join(
+            f"DROP COLUMN {column}" for column in timebase._HEADER_COLUMNS))
+        dj.conn().query(statement.replace(table, copy))
+        restored = columns(copy)
+    finally:
+        dj.conn().query(f"DROP TABLE IF EXISTS {copy}")
+    assert restored == columns(table)
diff --git a/tests/timebase/test_syncbox_segments.py b/tests/timebase/test_syncbox_segments.py
index d62ba7e..d2a8725 100644
--- a/tests/timebase/test_syncbox_segments.py
+++ b/tests/timebase/test_syncbox_segments.py
@@ -264,3 +264,56 @@ def test_a_measured_barcode_delay_leaves_session_time_where_the_devices_see_it(t
     session_dir, truth = _synthetic(tmp_path, syncbox_barcode_delay_us=1_500)
     assert segments.session_reference(session_dir) == pytest.approx(dict(truth.barcodes), abs=1e-6)
     assert syncbox_codes(session_dir)[0][0] == pytest.approx(truth.code_words[0][0], abs=1e-6)
+
+
+# Section 6: the box's losses, and which fail the session's timing check.
+
+RUNS = [(1.0, 3.0), (5.0, 9.0)]
+
+
+@pytest.mark.parametrize(("path", "cause", "start_s", "stop_s", "fails"), [
+    ("W", "fifo_full", 2.0, 2.5, True),
+    ("W", "fifo_full", 3.5, 4.5, False),
+    ("W", "fifo_full", None, 0.5, False),
+    ("W", "fifo_full", 9.5, None, False),
+    ("W", "fifo_full", 4.0, None, True),
+    ("W", "gap", 2.5, 6.0, True),
+    ("W", "gap", 3.2, 4.8, False),
+    ("W", "desync", 9.5, None, True),
+    ("W", "desync", None, None, True),
+    ("E", "buffer_overflow", 2.0, 2.5, False),
+    ("E", "unaccounted", None, 6.0, False),
+])
+def test_a_loss_fails_the_timing_check_where_it_can_have_cost_a_runs_codes(path, cause, start_s, stop_s, fails):
+    """Decision 3.5: a lost code word, or a gap, inside a measured run fails
+    it; a desync always does, since no run after it can be measured to show
+    what it lost; a lost edge never does, since no stage here reads edges."""
+    assert segments.timing_check_fails(path, cause, start_s, stop_s, RUNS) is fails
+
+
+def test_each_loss_is_kept_as_the_box_wrote_it_and_placed_in_session_time(tmp_path):
+    from wl_sync.log import Loss
+
+    session = _rolled(tmp_path, *_barcodes((40.0, 140), (41.0, 141)),
+                      Loss(tick_us=40_500_000, path="E", gpio=GPIO_MAP["pd1_comp"], count=2, bound="exact",
+                           cause="buffer_overflow", before_us=40_400_000, after_us=40_500_000),
+                      Loss(tick_us=40_600_000, path="W", gpio=None, count=1, bound="at_least", cause="desync",
+                           before_us=40_600_000, after_us=None))
+    assert [(loss.index, loss.cause, loss.path, loss.lost, loss.bound, loss.start_s, loss.stop_s)
+            for loss in session.losses()] == [
+        (1, "gap", "W", None, None, 1.0, 38.0),
+        (1, "buffer_overflow", "E", 2, "exact", 38.4, 38.5),
+        (1, "desync", "W", 1, "at_least", 38.6, None)]
+    assert session.fails_the_timing_check([])
+
+
+def test_a_loss_on_another_clock_is_not_placed_and_does_not_fail_session_time(tmp_path):
+    """A restarted process's segment is already tier D and waits for a bridge;
+    its losses are kept, unplaced."""
+    from wl_sync.log import Loss
+
+    session = _rolled(tmp_path, *_barcodes((0.4, 200), (1.4, 201)),
+                      Loss(tick_us=1_000_000, path="W", gpio=None, count=1, bound="at_least", cause="desync",
+                           before_us=1_000_000, after_us=None), process=LATER_PROCESS)
+    assert [(loss.cause, loss.start_s) for loss in session.losses()] == [("desync", None)]
+    assert not session.fails_the_timing_check([(0.0, 10.0)])
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_syncbox_losses.py tests/timebase/test_syncbox_segments.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `19 failed, 21 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/schema/timebase.py b/wl_preproc/schema/timebase.py
index f537c88..61880dd 100644
--- a/wl_preproc/schema/timebase.py
+++ b/wl_preproc/schema/timebase.py
@@ -124,9 +124,9 @@ class SessionClock(dj.Imported):
 @schema
 class SyncBoxSegment(dj.Computed):
     definition = f"""
-    # One segment of the session's sync box log: one run of the box's process,
-    # on a clock of its own (design spec
-    # `2026-10-22-syncbox-records-and-segments-design.md` section 6).
+    # One segment of the session's sync box log: one process's records between
+    # two rolls (design specs `2026-10-22-syncbox-records-and-segments-design.md`
+    # section 6 and `2026-11-04-syncbox-header-design.md` section 8).
     # Key: (subject, session_datetime, segment_index).
     -> pipeline.Session
     segment_index : int unsigned  # its header's, which orders a folder's segments
@@ -140,8 +140,34 @@ class SyncBoxSegment(dj.Computed):
     clock_reason       : varchar(255)
     role               : {_SEGMENT_ROLE_ENUM}
     barcode_delay_us=null : double  # added to every 'B' tick; null while unmeasured and uncorrected (section 5)
+    process_uuid=null  : char(36)  # the process that wrote it, its header's; null before 2026-10-30
+    timebase=null      : varchar(64)  # the clocks its ticks count, its header's; null before 2026-10-30
+    follows=null       : varchar(255)  # how it follows the segment before it on its clock; null for a clock's first
+    last_code_tick_us=null : bigint unsigned  # its last code word's tick; null when it logged none
     """
 
+    class Loss(dj.Part):
+        definition = """
+        # A loss of the segment's records: an 'L' record as the box wrote it
+        # (wl-sync's Loss), or a gap before the segment, the box recording
+        # elsewhere on the same clock (design spec
+        # `2026-11-04-syncbox-header-design.md` section 6).
+        # Key: (subject, session_datetime, segment_index, loss_number).
+        -> master
+        loss_number : smallint unsigned  # from 1, a gap first, then the log's order
+        ---
+        cause          : varchar(32)  # the record's, or gap
+        path           : char(1)  # W, code words, or E, edges; W for a gap
+        gpio=null      : tinyint unsigned  # the line, for an edge loss that names it
+        lost=null      : int unsigned  # how many, at least 1; null for a gap
+        bound=null     : varchar(16)  # exact or at_least; null for a gap
+        tick_us        : bigint unsigned  # where the record sits in its path; a gap's end
+        before_us=null : bigint unsigned  # the last record of its path known to precede it
+        after_us=null  : bigint unsigned  # the first known to follow it
+        start_s=null   : double  # before_us in session time, where the segment keeps it
+        stop_s=null    : double  # after_us in session time, likewise
+        """
+
     @property
     def key_source(self):
         """Every landed session with a sync box. Imported locally, as
@@ -150,12 +176,26 @@ class SyncBoxSegment(dj.Computed):
 
         return pipeline.Session & ingest.Ingestion & (core.AcquisitionSystem & {"system": "syncbox"})
 
+    def populate(self, *args, **kwargs):
+        """`AutoPopulate.populate`, refused before any job is reserved while
+        this process reads the table without `_HEADER_COLUMNS`: a database
+        declared before 2026-11, which DataJoint never alters (design spec
+        `2026-11-04-syncbox-header-design.md` section 8). The daemon reports
+        this stage's error and runs the others."""
+        if _lacks_header_columns():
+            raise RuntimeError(
+                f"{self.full_table_name} has no columns {', '.join(_HEADER_COLUMNS)}, as read by this process: a "
+                "database declared before the sync box's header named its process. Nothing is recorded until it "
+                f"is altered and the daemon restarted: {_header_statement()}"
+            )
+        return super().populate(*args, **kwargs)
+
     def make(self, key: dict) -> None:
         from wl_preproc.schema import ingest
         from wl_preproc.timebase import segments
 
         session_dir = Path((ingest.Ingestion & key).fetch1("session_dir"))
-        logs = segments.syncbox_logs(session_dir)
+        session = segments.read_syncbox_session(session_dir)
         self.insert([
             {**key, "segment_index": log.segment_index,
              "file_path": str(log.path.relative_to(session_dir / "syncbox")), "boot_id": log.boot_id,
@@ -163,9 +203,49 @@ class SyncBoxSegment(dj.Computed):
              "first_barcode": log.barcodes[0][0] if log.barcodes else None,
              "last_barcode": log.barcodes[-1][0] if log.barcodes else None,
              "clock_trusted": log.clock_trusted, "clock_reason": log.clock_reason, "role": role,
-             "barcode_delay_us": None}
-            for log, role in zip(logs, segments.segment_roles(logs), strict=True)
+             "barcode_delay_us": log.barcode_delay_us, "process_uuid": log.process_uuid, "timebase": log.timebase,
+             "follows": follows, "last_code_tick_us": log.codes[-1][0] if log.codes else None}
+            for log, role, follows in zip(session.logs, session.roles(), session.follows, strict=True)
         ])
+        numbers: dict[int, int] = {}
+        rows = []
+        for loss in session.losses():
+            numbers[loss.index] = numbers.get(loss.index, 0) + 1
+            rows.append({**key, "segment_index": session.logs[loss.index].segment_index,
+                         "loss_number": numbers[loss.index], "cause": loss.cause, "path": loss.path,
+                         "gpio": loss.gpio, "lost": loss.lost, "bound": loss.bound, "tick_us": loss.tick_us,
+                         "before_us": loss.before_us, "after_us": loss.after_us, "start_s": loss.start_s,
+                         "stop_s": loss.stop_s})
+        self.Loss.insert(rows)
+
+
+#: `SyncBoxSegment`'s columns from the header of 2026-10-30, after
+#: `barcode_delay_us` in order, as DataJoint declares them: a `char` or
+#: `varchar` column's comment begins with its declared type.
+_HEADER_COLUMNS = ("process_uuid", "timebase", "follows", "last_code_tick_us")
+_HEADER_DECLARATIONS = (
+    "char(36) DEFAULT NULL COMMENT ':char(36):the process that wrote it, its header''s; null before 2026-10-30'",
+    "varchar(64) DEFAULT NULL COMMENT ':varchar(64):the clocks its ticks count, its header''s; null before "
+    "2026-10-30'",
+    "varchar(255) DEFAULT NULL COMMENT ':varchar(255):how it follows the segment before it on its clock; null for "
+    "a clock''s first'",
+    "bigint unsigned DEFAULT NULL COMMENT 'its last code word''s tick; null when it logged none'",
+)
+
+
+def _lacks_header_columns() -> bool:
+    """`SyncBoxSegment`, as this process's DataJoint reads it, lacks one of
+    `_HEADER_COLUMNS`. DataJoint reads a table's columns once per process."""
+    return not set(_HEADER_COLUMNS) <= set(SyncBoxSegment.heading.names)
+
+
+def _header_statement() -> str:
+    """The statement that adds `_HEADER_COLUMNS` to a `SyncBoxSegment`
+    declared before them, as DataJoint declares them."""
+    after = ("barcode_delay_us", *_HEADER_COLUMNS[:-1])
+    return f"ALTER TABLE {SyncBoxSegment.full_table_name} " + ", ".join(
+        f"ADD COLUMN {column} {declaration} AFTER {previous}"
+        for column, declaration, previous in zip(_HEADER_COLUMNS, _HEADER_DECLARATIONS, after, strict=True))
 
 
 @schema
@@ -428,6 +508,16 @@ class TimingProvenance(dj.Computed):
 
         session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
 
+        # And if the box lost code words of session time where they mattered:
+        # in a measured run, or by a desync, after which nothing is measured
+        # (design spec `2026-11-04-syncbox-header-design.md` section 6).
+        if "syncbox" in system_names:
+            runs = [(row["run_start_time"], row["run_stop_time"]) for row in (core.Run & session_key).to_dicts()]
+            try:
+                failed = failed or segments.read_syncbox_session(session_dir).fails_the_timing_check(runs)
+            except (FileNotFoundError, ValueError):
+                pass
+
         # -- The Pi's own canonical code stream, and everything chained off
         # comparing against it. Gated behind `syncbox` actually having a
         # FITTED `SystemTimebase` row -- not merely `"syncbox" in
diff --git a/wl_preproc/timebase/segments.py b/wl_preproc/timebase/segments.py
index 3f74afd..ea8db15 100644
--- a/wl_preproc/timebase/segments.py
+++ b/wl_preproc/timebase/segments.py
@@ -26,6 +26,7 @@ has none.
 
 from __future__ import annotations
 
+import math
 from dataclasses import dataclass
 from pathlib import Path
 from typing import TYPE_CHECKING
@@ -239,6 +240,36 @@ class SyncBoxSession:
                 out.append((index, before, _ticks(self.logs[index])[0]))
         return out
 
+    def losses(self) -> list[SegmentLoss]:
+        """Every loss of the box's records, segment by segment in `logs`'
+        order: a gap before a segment on session time's clock, then its `L`
+        records as the box wrote them, each window placed in session time
+        where the segment keeps it (design spec
+        `2026-11-04-syncbox-header-design.md` section 6)."""
+        gaps = {index: (start_us, stop_us) for index, start_us, stop_us in self.gaps()}
+        out = []
+        for index, log in enumerate(self.logs):
+            def placed(tick_us: int | None, index: int = index) -> float | None:
+                return None if tick_us is None or not self.in_session_time(index) else self.seconds(tick_us)
+
+            if index in gaps:
+                start_us, stop_us = gaps[index]
+                out.append(SegmentLoss(index=index, cause=GAP, path="W", gpio=None, lost=None, bound=None,
+                                       tick_us=stop_us, before_us=start_us, after_us=stop_us,
+                                       start_s=placed(start_us), stop_s=placed(stop_us)))
+            out += [SegmentLoss(index=index, cause=loss.cause, path=loss.path, gpio=loss.gpio, lost=loss.count,
+                                bound=loss.bound, tick_us=loss.tick_us, before_us=loss.before_us,
+                                after_us=loss.after_us, start_s=placed(loss.before_us), stop_s=placed(loss.after_us))
+                    for loss in log.losses]
+        return out
+
+    def fails_the_timing_check(self, runs: list[tuple[float, float]]) -> bool:
+        """Whether a loss of session time's code words fails the session's
+        timing check, against its measured runs `(start_s, stop_s)`
+        (`timing_check_fails`)."""
+        return any(timing_check_fails(loss.path, loss.cause, loss.start_s, loss.stop_s, runs)
+                   for loss in self.losses() if self.in_session_time(loss.index))
+
     def roles(self) -> list[str]:
         """Each segment's role, as `timebase.SyncBoxSegment` stores it: every
         segment of session time's clock keeps session time, holding a barcode
@@ -249,6 +280,49 @@ class SyncBoxSession:
                 else SEGMENT_NO_BARCODE for i, log in enumerate(self.logs)]
 
 
+#: A loss the reader finds rather than one the box writes: a gap on one
+#: clock, the box recording elsewhere (`SyncBoxSession.gaps`).
+GAP = "gap"
+
+
+@dataclass(frozen=True, slots=True)
+class SegmentLoss:
+    """One loss of a segment's records, as `timebase.SyncBoxSegment.Loss`
+    stores it: an `L` record as the box wrote it (wl-sync's `Loss`), or a gap
+    before the segment, whose `lost` and `bound` are None; its window placed in
+    session time, `start_s` to `stop_s`, where the segment keeps session time
+    and the end is known."""
+
+    index: int
+    cause: str
+    path: str
+    gpio: int | None
+    lost: int | None
+    bound: str | None
+    tick_us: int
+    before_us: int | None
+    after_us: int | None
+    start_s: float | None
+    stop_s: float | None
+
+
+def timing_check_fails(path: str, cause: str, start_s: float | None, stop_s: float | None,
+                       runs: list[tuple[float, float]]) -> bool:
+    """Whether one loss of session time's records fails the session's timing
+    check (design spec `2026-11-04-syncbox-header-design.md` section 6,
+    decision 3.5): a loss of code words, or a gap, whose window overlaps a
+    measured run, and any `desync`, whose window has no end and after which no
+    run can be measured to show what it lost. A lost edge is reported only:
+    no stage here reads the box's edges."""
+    if path != "W":
+        return False
+    if cause == "desync":
+        return True
+    low = -math.inf if start_s is None else start_s
+    high = math.inf if stop_s is None else stop_s
+    return any(low <= run_stop_s and high >= run_start_s for run_start_s, run_stop_s in runs)
+
+
 def _ticks(log: SyncBoxLog) -> list[int]:
     return sorted([tick_us for _value, tick_us in log.barcodes] + [tick_us for tick_us, _word in log.codes])
 
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_syncbox_losses.py tests/timebase/test_syncbox_segments.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `40 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_core.py tests/schema/test_daemon.py tests/schema/test_guardrails.py tests/schema/test_segment_populate.py tests/schema/test_syncbox_losses.py tests/schema/test_syncbox_restart.py tests/schema/test_syncbox_roll.py tests/schema/test_timebase.py tests/timebase -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `206 passed, 1 skipped`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "desync-passes",
  "wl_preproc/timebase/segments.py",
  "    if cause == \"desync\":\n        return True",
  "    if False:\n        return True",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "a-lost-edge-fails",
  "wl_preproc/timebase/segments.py",
  "    if path != \"W\":\n        return False",
  "    if False:\n        return False",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "open-end-closed",
  "wl_preproc/timebase/segments.py",
  "    high = math.inf if stop_s is None else stop_s",
  "    high = 0.0 if stop_s is None else stop_s",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "overlap-must-contain",
  "wl_preproc/timebase/segments.py",
  "return any(low <= run_stop_s and high >= run_start_s for",
  "return any(low <= run_start_s and high >= run_stop_s for",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "losses-unplaced",
  "wl_preproc/timebase/segments.py",
  "after_us=loss.after_us, start_s=placed(loss.before_us), stop_s=placed(loss.after_us))",
  "after_us=loss.after_us, start_s=None, stop_s=None)",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "gap-not-a-loss",
  "wl_preproc/timebase/segments.py",
  "            if index in gaps:",
  "            if False:",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "another-clock-fails-session-time",
  "wl_preproc/timebase/segments.py",
  "for loss in self.losses() if self.in_session_time(loss.index))",
  "for loss in self.losses())",
  "tests/timebase/test_syncbox_segments.py"
 ],
 [
  "tier-ignores-losses",
  "wl_preproc/schema/timebase.py",
  "failed = failed or segments.read_syncbox_session(session_dir).fails_the_timing_check(runs)",
  "failed = failed or False",
  "tests/schema/test_syncbox_losses.py"
 ],
 [
  "guard-never-refuses",
  "wl_preproc/schema/timebase.py",
  "        if _lacks_header_columns():\n            raise RuntimeError(",
  "        if False:\n            raise RuntimeError(",
  "tests/schema/test_syncbox_losses.py"
 ],
 [
  "statement-without-the-type-prefix",
  "wl_preproc/schema/timebase.py",
  "COMMENT ':char(36):the process",
  "COMMENT 'the process",
  "tests/schema/test_syncbox_losses.py"
 ],
 [
  "process-not-stored",
  "wl_preproc/schema/timebase.py",
  "\"process_uuid\": log.process_uuid, \"timebase\": log.timebase,",
  "\"process_uuid\": None, \"timebase\": log.timebase,",
  "tests/schema/test_syncbox_losses.py"
 ],
 [
  "follows-not-stored",
  "wl_preproc/schema/timebase.py",
  "             \"follows\": follows,",
  "             \"follows\": None,",
  "tests/schema/test_syncbox_losses.py"
 ],
 [
  "count-not-stored",
  "wl_preproc/schema/timebase.py",
  "\"gpio\": loss.gpio, \"lost\": loss.lost,",
  "\"gpio\": loss.gpio, \"lost\": None,",
  "tests/schema/test_syncbox_losses.py"
 ]
]
```

Expected: 13 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(timebase): each sync box segment records its process, its clocks and how it follows the one before, and each loss of its records -- an L record as the box wrote it, or a gap -- placed in session time; a desync, or code words lost in a measured run, fail the session's timing check; a database declared before refuses the stage and names its ALTER TABLE (sections 6 and 8)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 5: The daily report names the losses, a split clock, and a contradiction

§4, §6 and §7, decision 6 and amendment 7: a section naming each loss of the box's records in the report's 24 h with what it did to its session, one naming each session whose code words are on a split clock with their worst-case drift at 50 ppm, and the restarts section saying why a segment that contradicts its process's clock is read as a restart.

**Files:**
- Modify: `wl_preproc/cli/report.py`
- Test: `tests/schema/test_syncbox_losses.py`, `tests/schema/test_syncbox_restart.py`

**Interfaces:**
- Consumes: Task 4's `SyncBoxSegment` columns, `SyncBoxSegment.Loss`, `timing_check_fails` and `landed`.
- Produces: the report's sections `Sync box losses (24 h)` and `Code words on a split clock (24 h)`;
  `report.SPLIT_CLOCK_PPM`, `ONE_RATE_TIMEBASES`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/schema/test_syncbox_losses.py b/tests/schema/test_syncbox_losses.py
index 4574eb1..ca66344 100644
--- a/tests/schema/test_syncbox_losses.py
+++ b/tests/schema/test_syncbox_losses.py
@@ -144,3 +144,68 @@ def test_the_statement_restores_the_columns_as_datajoint_declares_them(landed):
     finally:
         dj.conn().query(f"DROP TABLE IF EXISTS {copy}")
     assert restored == columns(table)
+
+
+def _report(landed, prefix):
+    """The daily report two hours after the fixture's last session, so all
+    four are in its 24 h."""
+    import datetime
+
+    from wl_preproc.cli.report import build_report
+
+    keys, _, root = landed
+    at = max(key["session_datetime"] for key in keys.values()) + datetime.timedelta(hours=2)
+    return build_report(root, prefix=prefix, now=at.replace(tzinfo=datetime.UTC))
+
+
+def _at(key) -> str:
+    return f"@ {key['session_datetime']:%Y-%m-%d %H:%M}"
+
+
+def test_the_report_names_each_loss_and_what_it_did_to_its_session(landed, prefix):
+    """Section 6: path, cause, count with its bound, window in session time,
+    and whether it failed the timing check."""
+    from wl_sync.pins import GPIO_MAP
+
+    from tests.cli.test_report import _declared_count, _line_for, _section
+
+    keys, _, _ = landed
+    section = _section(_report(landed, prefix), "Sync box losses (24 h)")
+    assert ("at least 2 code words lost (desync), from 6.000 s on; fails the session's timing check (tier D)"
+            in _line_for(section, _at(keys["desync"])))
+    assert (f"3 edges lost on GPIO{GPIO_MAP['pd1_comp']} (buffer_overflow), from 1.200 s to 1.400 s; reported only"
+            in _line_for(section, _at(keys["edge"])))
+    gap = _line_for(section, _at(keys["gap"]))
+    assert "the box recorded elsewhere" in gap and gap.endswith("fails the session's timing check (tier D)")
+    assert not [line for line in section.splitlines() if _at(keys["split"]) in line]
+    assert _declared_count(section) == len([line for line in section.splitlines() if line.startswith("- ")])
+
+
+def test_the_report_names_each_session_whose_code_words_are_on_a_split_clock(landed, prefix):
+    """Section 7: with the most its code words can have drifted from its
+    barcodes at 50 ppm; a box on the test backend is not named."""
+    from tests.cli.test_report import _declared_count, _line_for, _section
+
+    keys, _, _ = landed
+    section = _section(_report(landed, prefix), "Code words on a split clock (24 h)")
+    assert ("timebase `split`: its code words can lie up to 0.001 s from its barcodes at 50 ppm, the rate "
+            "unmeasured; nothing is corrected" in _line_for(section, _at(keys["split"])))
+    assert not [line for line in section.splitlines() if _at(keys["edge"]) in line]
+    assert _declared_count(section) == len([line for line in section.splitlines() if line.startswith("- ")])
+
+
+def test_a_loss_or_a_split_clock_older_than_a_day_is_not_listed(landed, prefix):
+    """Both lists are the report's 24 h, as its per-session lists are: a loss
+    is a fact of its session, not a state that clears."""
+    import datetime
+
+    from wl_preproc.cli.report import build_report
+
+    from tests.cli.test_report import _section
+
+    keys, _, root = landed
+    later = max(key["session_datetime"] for key in keys.values()) + datetime.timedelta(days=3)
+    body = build_report(root, prefix=prefix, now=later.replace(tzinfo=datetime.UTC))
+    for heading in ("Sync box losses (24 h)", "Code words on a split clock (24 h)"):
+        section = _section(body, heading)
+        assert not [line for line in section.splitlines() if any(_at(key) in line for key in keys.values())]
diff --git a/tests/schema/test_syncbox_restart.py b/tests/schema/test_syncbox_restart.py
index a3890ff..eba32eb 100644
--- a/tests/schema/test_syncbox_restart.py
+++ b/tests/schema/test_syncbox_restart.py
@@ -214,3 +214,42 @@ def test_the_report_names_a_restart_whose_later_segment_logged_no_barcode(dj_con
         assert "logged no barcode" in _line_for(section, "`seg-002.log`")
     finally:
         (pipeline.Session & key).delete(prompt=False)
+
+
+def test_the_report_says_why_a_segment_that_contradicts_its_clock_is_read_as_a_restart(dj_conn, prefix, tmp_path):
+    """Design spec `2026-11-04-syncbox-header-design.md` section 4: a segment
+    naming the process of an earlier one whose first tick comes before that
+    one's last is read as after a restart, and the report says why."""
+    import datetime
+
+    from wl_sync.log import SCHEMA_VERSION, BarcodeEmitted, SyncBoxLogHeader, write_log
+    from wl_sync.pins import GPIO_MAP
+
+    from wl_preproc import daemon
+    from wl_preproc.cli.report import build_report
+    from wl_preproc.schema import pipeline, timebase
+    from wl_preproc.synth.recipe import RECIPES
+    from wl_preproc.synth.syncbox import process_uuid
+
+    from tests.cli.test_report import _line_for, _section
+
+    def contradicting(syncbox_dir):
+        header = SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id=syncbox_dir.parent.name, rig="rig0",
+                                  boot_id="b1", written_at=datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC),
+                                  gpio_map=GPIO_MAP, segment_index=2, process_uuid=process_uuid(RECIPES["ci"], 0))
+        write_log(syncbox_dir / "seg-002.log", header, [BarcodeEmitted(tick_us=500_000, value=1_000_020),
+                                                        BarcodeEmitted(tick_us=1_500_000, value=1_000_021)])
+
+    daemon.activate_all(prefix=prefix)
+    key, session_dir = _land_restarted(prefix, tmp_path, extra_segment=contradicting)
+    try:
+        timebase.SyncBoxSegment.populate(key)
+        assert (timebase.SyncBoxSegment & key & {"segment_index": 2}).fetch1("role", "follows") == (
+            "after_restart_unbridged", "contradiction: its first tick is before the last of the segment it follows")
+        line = _line_for(_section(build_report(session_dir.parent, prefix=prefix), "Sync box restarts"),
+                         "`seg-002.log`")
+        assert ("the sync box's `seg-002.log` names the process of an earlier segment, but its first tick is "
+                "before the last of the segment it follows, so it is read as after a restart: resumed at barcode "
+                "1000020") in line
+    finally:
+        (pipeline.Session & key).delete(prompt=False)
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_syncbox_losses.py tests/schema/test_syncbox_restart.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `4 failed, 16 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/cli/report.py b/wl_preproc/cli/report.py
index 6d405c1..06baa34 100644
--- a/wl_preproc/cli/report.py
+++ b/wl_preproc/cli/report.py
@@ -1400,11 +1400,109 @@ def _landed_without(session: dict) -> str:
                    f"UTC: {row['reason']}" for row in rows)
 
 
+#: wl-sync's figure for RP1's crystal against CLOCK_MONOTONIC, the rate
+#: unmeasured: code words on a split clock drift from barcodes by about this
+#: times the time since the box's process began capture (design spec
+#: `2026-11-04-syncbox-header-design.md` section 7).
+SPLIT_CLOCK_PPM = 50.0
+#: The `timebase` values whose ticks share one rate: the test backend's, with no
+#: hardware clock. Every other value, or none, leaves the rates unknown.
+ONE_RATE_TIMEBASES = frozenset({"fake"})
+
+
+def _syncbox_losses(ingested_keys: set, prefix: str = DEFAULT_PREFIX) -> list[dict]:
+    """Every loss of the sync box's records in a session ingested in the last
+    24 h, as `timebase.SyncBoxSegment.Loss` keeps it, with its segment's file
+    and role and whether it fails the session's timing check against the
+    session's measured runs (`timebase/segments.py::timing_check_fails`;
+    design spec `2026-11-04-syncbox-header-design.md` section 6). Windowed as
+    the per-session lists are: a loss is a fact of its session, not a state
+    that clears."""
+    from wl_preproc.schema import core, ingest, timebase
+    from wl_preproc.timebase.segments import SEGMENT_SESSION_TIME, timing_check_fails
+
+    timebase.activate(prefix=prefix)
+    rows = (timebase.SyncBoxSegment.Loss * timebase.SyncBoxSegment.proj("file_path", "role")
+            * ingest.Ingestion.proj("session_dir")).to_dicts(
+        order_by=["subject", "session_datetime", "segment_index", "loss_number"])
+    out = []
+    for row in rows:
+        if (row["subject"], row["session_datetime"]) not in ingested_keys:
+            continue
+        runs = [(run["run_start_time"], run["run_stop_time"]) for run in (
+            core.Run & {"subject": row["subject"], "session_datetime": row["session_datetime"]}).to_dicts()]
+        fails = row["role"] == SEGMENT_SESSION_TIME and timing_check_fails(
+            row["path"], row["cause"], row["start_s"], row["stop_s"], runs)
+        out.append({**row, "fails": fails})
+    return out
+
+
+def _loss_line(row: dict) -> str:
+    """One loss, as a person reading the report needs it: what was lost, when
+    in session time, and what it did to the session."""
+    if row["cause"] == "gap":
+        what = "the box recorded elsewhere, and the code words of that stretch are not in this folder"
+    else:
+        amount = f"{'at least ' if row['bound'] == 'at_least' else ''}{row['lost']}"
+        kind = "code word" if row["path"] == "W" else "edge"
+        line = "" if row["gpio"] is None else f" on GPIO{row['gpio']}"
+        what = f"{amount} {kind}{'' if row['lost'] == 1 else 's'} lost{line} ({row['cause']})"
+    if row["role"] != "session_time":
+        when = "on a clock of its own"
+    elif row["start_s"] is None and row["stop_s"] is None:
+        when = "at no known time"
+    elif row["stop_s"] is None:
+        when = f"from {row['start_s']:.3f} s on"
+    elif row["start_s"] is None:
+        when = f"until {row['stop_s']:.3f} s"
+    else:
+        when = f"from {row['start_s']:.3f} s to {row['stop_s']:.3f} s"
+    verdict = "fails the session's timing check (tier D)" if row["fails"] else "reported only"
+    return (f"- `{Path(row['session_dir']).name}` — `{row['subject']}` @ {row['session_datetime']:%Y-%m-%d %H:%M} — "
+            f"`{row['file_path']}`: {what}, {when}; {verdict}")
+
+
+def _split_clocks(ingested_keys: set, prefix: str = DEFAULT_PREFIX) -> list[dict]:
+    """Each session ingested in the last 24 h whose code words came from a
+    clock not known to share one rate with its barcodes: session time's
+    segments naming a `timebase` other than `ONE_RATE_TIMEBASES`, or none.
+    With the most its code words can have drifted from its barcodes at
+    `SPLIT_CLOCK_PPM`, its latest code word's tick being the time since its
+    process's capture began (wl-sync's box clock reads 0 then). Recorded and
+    reported, never corrected (design spec
+    `2026-11-04-syncbox-header-design.md` section 7, decision 6)."""
+    from wl_preproc.schema import ingest, timebase
+    from wl_preproc.timebase.segments import SEGMENT_SESSION_TIME
+
+    timebase.activate(prefix=prefix)
+    sessions: dict = {}
+    for row in ((timebase.SyncBoxSegment & {"role": SEGMENT_SESSION_TIME}) * ingest.Ingestion.proj("session_dir")
+                ).to_dicts(order_by=["subject", "session_datetime", "segment_index"]):
+        key = (row["subject"], row["session_datetime"])
+        if key not in ingested_keys or row["timebase"] in ONE_RATE_TIMEBASES:
+            continue
+        entry = sessions.setdefault(key, {**row, "timebases": [], "drift_s": 0.0})
+        if row["timebase"] not in entry["timebases"]:
+            entry["timebases"].append(row["timebase"])
+        if row["last_code_tick_us"] is not None:
+            entry["drift_s"] = max(entry["drift_s"], SPLIT_CLOCK_PPM * 1e-6 * row["last_code_tick_us"] / 1e6)
+    return list(sessions.values())
+
+
+def _split_clock_line(row: dict) -> str:
+    named = ", ".join("not named" if value is None else f"`{value}`" for value in row["timebases"])
+    return (f"- `{Path(row['session_dir']).name}` — `{row['subject']}` @ {row['session_datetime']:%Y-%m-%d %H:%M} — "
+            f"timebase {named}: its code words can lie up to {row['drift_s']:.3f} s from its barcodes at "
+            f"{SPLIT_CLOCK_PPM:g} ppm, the rate unmeasured; nothing is corrected")
+
+
 def _syncbox_restarts(prefix: str = DEFAULT_PREFIX) -> list[dict]:
     """Every sync box segment other than session time's, with its session's
-    folder: each came from a restart of the box (`timebase.SyncBoxSegment`;
-    design spec `2026-10-22-syncbox-records-and-segments-design.md` sections 4
-    and 6), whether it waits for a bridge or logged no barcode, as a box that
+    folder: each came from a restart of the box, or contradicted its process's
+    one clock and is read as one (`timebase.SyncBoxSegment`; design specs
+    `2026-10-22-syncbox-records-and-segments-design.md` sections 4 and 6 and
+    `2026-11-04-syncbox-header-design.md` section 4), whether it waits for a
+    bridge or logged no barcode, as a box that
     crash-looped does. Live, as the reclamation list is: a segment part 2
     bridges changes role and leaves it. Not part of `Readings`, for
     `_eye_rows`' reason: only `wlpp report` pays for it."""
@@ -1522,7 +1620,10 @@ def build_report(
     lines += ["", f"## Sync box restarts — {len(restarts)}", ""]
     lines += [
         f"- `{Path(row['session_dir']).name}` — `{row['subject']}` @ {row['session_datetime']:%Y-%m-%d %H:%M} — "
-        f"the sync box restarted: `{row['file_path']}` "
+        + (f"the sync box's `{row['file_path']}` names the process of an earlier segment, but "
+           f"{row['follows'].split(': ', 1)[1]}, so it is read as after a restart: "
+           if (row["follows"] or "").startswith("contradiction") else
+           f"the sync box restarted: `{row['file_path']}` ")
         + (f"resumed at barcode {row['first_barcode']} "
            f"({BARCODE_EPOCH + datetime.timedelta(seconds=row['first_barcode']):%Y-%m-%d %H:%M:%S} UTC by the "
            "box's clock), on a clock of its own, and its records wait for a bridge"
@@ -1530,6 +1631,15 @@ def build_report(
         for row in restarts
     ] or ["- none"]
 
+    # Design spec `2026-11-04-syncbox-header-design.md` sections 6 and 7.
+    recent = {(row["subject"], row["session_datetime"]) for row in readings.ingested}
+    losses = _syncbox_losses(recent, prefix)
+    lines += ["", f"## Sync box losses (24 h) — {len(losses)}", ""]
+    lines += [_loss_line(row) for row in losses] or ["- none"]
+    split = _split_clocks(recent, prefix)
+    lines += ["", f"## Code words on a split clock (24 h) — {len(split)}", ""]
+    lines += [_split_clock_line(row) for row in split] or ["- none"]
+
     # Design spec section 5.2 (quoting parent spec §8.5): the reclamation
     # gate, surfaced here "since an ungated session is what will eventually
     # fill scratch" -- see `_unreclaimed_sessions`' own docstring for the
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_syncbox_losses.py tests/schema/test_syncbox_restart.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `20 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli tests/schema/test_syncbox_losses.py tests/schema/test_syncbox_restart.py tests/schema/test_syncbox_roll.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `201 passed`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "losses-not-windowed",
  "wl_preproc/cli/report.py",
  "        if (row[\"subject\"], row[\"session_datetime\"]) not in ingested_keys:\n            continue\n        runs =",
  "        if False:\n            continue\n        runs =",
  "tests/schema/test_syncbox_losses.py"
 ],
 [
  "split-not-windowed",
  "wl_preproc/cli/report.py",
  "        if key not in ingested_keys or row[\"timebase\"] in ONE_RATE_TIMEBASES:",
  "        if row[\"timebase\"] in ONE_RATE_TIMEBASES:",
  "tests/schema/test_syncbox_losses.py"
 ],
 [
  "verdict-inverted",
  "wl_preproc/cli/report.py",
  "fails = row[\"role\"] == SEGMENT_SESSION_TIME and timing_check_fails(",
  "fails = row[\"role\"] == SEGMENT_SESSION_TIME and not timing_check_fails(",
  "tests/schema/test_syncbox_losses.py"
 ],
 [
  "bound-not-said",
  "wl_preproc/cli/report.py",
  "{'at least ' if row['bound'] == 'at_least' else ''}",
  "{''}",
  "tests/schema/test_syncbox_losses.py"
 ],
 [
  "test-backend-named",
  "wl_preproc/cli/report.py",
  "ONE_RATE_TIMEBASES = frozenset({\"fake\"})",
  "ONE_RATE_TIMEBASES = frozenset()",
  "tests/schema/test_syncbox_losses.py"
 ],
 [
  "drift-in-the-wrong-unit",
  "wl_preproc/cli/report.py",
  "SPLIT_CLOCK_PPM * 1e-6 * row[\"last_code_tick_us\"] / 1e6",
  "SPLIT_CLOCK_PPM * 1e-6 * row[\"last_code_tick_us\"] / 1e3",
  "tests/schema/test_syncbox_losses.py"
 ],
 [
  "open-window-closed",
  "wl_preproc/cli/report.py",
  "        when = f\"from {row['start_s']:.3f} s on\"",
  "        when = f\"from {row['start_s']:.3f} s\"",
  "tests/schema/test_syncbox_losses.py"
 ],
 [
  "contradiction-not-said",
  "wl_preproc/cli/report.py",
  "if (row[\"follows\"] or \"\").startswith(\"contradiction\") else",
  "if False else",
  "tests/schema/test_syncbox_restart.py"
 ]
]
```

Expected: 8 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(report): the daily report names each loss of the sync box's records in its 24 h -- what was lost, when in session time, and whether it failed the session's timing check -- and each session whose code words are on a split clock, with their worst-case drift at 50 ppm; a segment that contradicts its process's clock says why it is read as a restart (sections 4, 6 and 7)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 6: The records

The spec's amendments 1-10, made while proving this plan; wl-preproc's answers in `HANDOVER-wl-sync.md`; this
piece's handoff; and the checkpoint's and `wl.yaml`'s lines (spec §13). Then the full suite, once, on both
interpreters.

**Files:**
- Modify: `docs/superpowers/specs/2026-11-04-syncbox-header-design.md`, `HANDOVER-wl-sync.md`,
  `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-11-04-syncbox-header.md`

**Interfaces:** none; documents only.

- [ ] **Step 1: Apply the records.** Save this script outside the repository as `records.py` and run it from
  the repository root.

```python
"""The header follow-up's records: the spec's amendments 1-10, wl-preproc's
answers in HANDOVER-wl-sync.md, and the checkpoint's and wl.yaml's lines
(built, not merged; next, the review). Run from the repository root."""
import pathlib

root = pathlib.Path.cwd()


def swap(rel, old, new):
    path = root / rel
    text = path.read_text()
    assert text.count(old) == 1, (rel, old[:70])
    path.write_text(text.replace(old, new))


spec = root / "docs/superpowers/specs/2026-11-04-syncbox-header-design.md"
spec.write_text(spec.read_text().rstrip("\n") + """

## Amendments, 2026-11-06, made while proving the plan

1. **An older log's first barcode is kept** (narrowing §5): wl-sync states the late first frame of each
   `process_uuid`, and a log written before the field names no process. Every such log is synthetic or a
   test's, since no real session ran the box before it, so part 1's reading stands for them and their t = 0 is
   unchanged.
2. **The synthetic box's late first frame** (§10): the session's first process starts with an extra frame at its
   clock's origin, one barcode interval before session time's first, which no device records and every reader
   drops; a restarted process's first frame is its first barcode. Every synthetic session's t = 0 stays at its
   timeline's first barcode, and a reader that kept the frame would put it one second early, which every timing
   test sees.
3. **A segment of session time's clock that no barcode places is not scanned** (§4, §5): its codes are placed by
   its clock, and it is neither a `core.Segment` nor a rejection, which would fail the timing check of a session
   that lost nothing.
4. **A gap's window runs from the last record of the segment before it, a barcode or a code word, to the first
   of the later segment** (correcting §4's "last code word"): the box recorded here until its last record of any
   kind. **A gap is stored as a `Loss` row with cause `gap`**, its count and bound null (widening §6's "one row
   per `L` record"), so one table holds what a session's records are missing, and the report and the timing
   check read one list.
5. **`SyncBoxSegment`'s new columns** (§8): `process_uuid`, `timebase`, `follows` (how a segment follows the one
   before it on its clock: `adjacent`, `gap`, or `contradiction: <why>`; null for a clock's first), and
   `last_code_tick_us`, from which §7's worst-case drift is read. DataJoint writes a `char` or `varchar`
   column's declared type at the start of its comment (`:char(36):...`), so the named `ALTER TABLE` writes it
   too, or the columns it restores would differ from declared ones.
6. **The timing check is computed from the files** by `TimingProvenance`, as its other evidence is, and the
   report reads the stored rows; both decide through one rule, `timebase/segments.py::timing_check_fails`. Of
   the `W` causes only `desync` and the reserved `fifo_full` exist, so a `desync` and a gap are what fail a
   session today.
7. **The report's two new sections cover its 24 h**, as its per-session lists do (§6 and §7 did not say): a loss
   is a fact of its session, not a state that clears. `"fake"` is the one `timebase` taken to share one rate.
8. **The box's identity fit counts as matched only the barcodes session time holds** (§9's fourth finding:
   `SystemTimebase.n_barcodes_matched` for the sync box counted a later process's barcodes too).
9. **A synthetic session generated again over its own folder writes its log again** (§10): wl-sync's
   `SegmentWriter` refuses a file that exists, as the box must, so the synthesizer removes its own file first.
10. **The roll fixture** (§10) is `synth/recipe.py::SyncBoxRollSpec`, `at_s` and `gap_s`: with `gap_s` 0 the roll
    in place, the first frame due after it never emitted, so the value steps by two; above 0 the box recorded
    elsewhere that long. The synthetic box's `timebase` is `"fake"`, as the box's unit writes until bench
    acceptance, and its delay unmeasured, unless a recipe says otherwise.
""")

handover = root / "HANDOVER-wl-sync.md"
handover.write_text(handover.read_text().rstrip("\n") + """

## wl-preproc's answers, 2026-11-06: the header change (your `fe1ea63`)

Designed in `docs/superpowers/specs/2026-11-04-syncbox-header-design.md` (the requester's decisions of
2026-10-30, approved 2026-11-06); built on `feat/syncbox-header`, not merged. The pin moves to your `main` at
`fe1ea63` with the merge, and you are told then: the box may run the change once it has.
1. **One clock is one process.** Segments naming one `process_uuid` are read as one clock. Across each boundary
   a barcode step of +1 or +2 is adjacent, a larger forward step with ticks agreeing is a gap (your recording out
   to `unassigned/` and back; the code words of that stretch are not in the session's folder), and anything else
   contradicts one clock and is read as a restart. A segment naming no process is a process of its own, as
   before.
2. **`barcode_delay_us` is added to every `B` tick**; none means each tick as it is.
3. **The late first `B` of a process:** a session folder cannot show whether a process began in it, so the first
   `B` of each process's earliest segment in the folder is dropped, and t = 0 is the first kept.
4. **`L` records are stored as written**, one row each. A `desync`, or code words lost inside a measured run,
   fail the session's timing check (tier D); a lost edge is reported only, since nothing here reads edges.
5. **`timebase`:** a session whose code words came from a clock other than `"fake"`, or from none named, is named
   in the daily report with its code words' worst-case drift at 50 ppm; nothing is corrected.
6. **One confirmation asked:** that a reader may take a `W` tick, in seconds, as the time since its process's
   capture began, which item 5's drift relies on. `_BoxClock` says the PIO counter reads 0 when capture starts;
   no field's docstring says it yet.
""")

swap("docs/CHECKPOINT.md",
     """> Important are fixed (handoff §6); wl-xcon and wl.works were told on 2026-11-06. Next, in the
> requester's order of 2026-10-31: wl-sync's header follow-up (its `fe1ea63`; spec approved on
> 2026-11-06, `specs/2026-11-04-syncbox-header-design.md` on `spec/syncbox-header`, its plan
> being proved), wl-xcon's asks (engine builds C2a, C2b and, since 2026-11-06, C3; XC-399; and a
> setting's value that is an appearance), the pipeline's scaling to long sessions, part 3b, the
> NI as the clock, part 2, the bridges, and the older minors in one branch.""",
     """> Important are fixed (handoff §6); wl-xcon and wl.works were told on 2026-11-06. **wl-sync's
> header follow-up is built** on `feat/syncbox-header`, not merged
> (`specs/2026-11-04-syncbox-header-design.md`, approved 2026-11-06;
> `handoffs/2026-11-04-syncbox-header.md`): the pin moves to wl-sync's `fe1ea63`, a clock is a
> process, the barcode delay is added and each process's late first barcode dropped, the box's
> lost events are stored and fail a session where they cost a run's codes, and a split clock is
> named in the report. Next: its review; then, in the requester's order of 2026-10-31, wl-xcon's
> asks (engine builds C2a, C2b and, since 2026-11-06, C3; XC-399; and a setting's value that is
> an appearance), the pipeline's scaling to long sessions, part 3b, the NI as the clock, part 2,
> the bridges, and the older minors in one branch.""")
swap("docs/CHECKPOINT.md",
     """> *Until XC-398 was built, this said its plan was being proved; until its review, that the review
> was next; until its merge, that the merge was next; each true when written.*""",
     """> *Until XC-398 was built, this said its plan was being proved; until its review, that the review
> was next; until its merge, that the merge was next; until the header follow-up was built, that
> its plan was being proved; each true when written.*""")
swap("wl.yaml",
     """    requester's order of 2026-10-31: wl-sync's header follow-up (its
    fe1ea63; spec approved 2026-11-06, its plan being proved), wl-xcon's
    asks (C2a, C2b, C3, XC-399, a setting's value that is an appearance),""",
     """    requester's order of 2026-10-31: wl-sync's header follow-up (its
    fe1ea63), built on feat/syncbox-header
    (docs/handoffs/2026-11-04-syncbox-header.md), not merged, its review
    next; then wl-xcon's
    asks (C2a, C2b, C3, XC-399, a setting's value that is an appearance),""")
```

Expected: no output, and exit 0.

- [ ] **Step 2: Write the handoff.** Its §5 keeps `{{...}}` slots until Step 5.

```bash
cat > docs/handoffs/2026-11-04-syncbox-header.md <<'HANDOFF'
# Reading wl-sync's header change: one clock per process, the barcode delay, and lost events

**`specs/2026-11-04-syncbox-header-design.md`**: wl-sync's header change of 2026-10-30 (its `fe1ea63`). Every
segment's header now names the process that wrote it, the measured barcode delay and the clocks its ticks count;
the box rolls once in place when NTP first syncs; and an `L` record marks capture events it saw and could not
record. The pin here refused all of it, and wl-sync holds the box's deploy until it has moved.
- **Branch:** `feat/syncbox-header`, from `plan/syncbox-header`.
- **Spec:** the design, its decisions of 2026-10-30, its five rulings (approved 2026-11-06), and its amendments
  1-10, made while proving the plan.
- **Plan:** `docs/superpowers/plans/2026-11-04-syncbox-header.md`.

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **The pin** moves to wl-sync's `main` at `fe1ea63`; `docs/schemas/syncbox_log_header.json` is regenerated.
- **The reader** (`timebase/_syncbox_log.py`) carries each segment's process, delay and clocks, its barcodes with
  the delay added, its code words and its losses, reads each file once, and names a file it cannot read.
- **A clock is a process** (`timebase/segments.py::SyncBoxSession`): one process's segments are one clock, each
  boundary adjacent, a gap, or a contradiction read as a restart; each process's late first barcode is dropped
  and t = 0 is the first kept; every reader of the box's barcodes takes its `B` records as they are.
- **Recorded:** `timebase.SyncBoxSegment` gains each segment's process, clocks, how it follows the one before,
  and its last code word's tick, and `SyncBoxSegment.Loss` holds each `L` record as written and each gap; a
  database declared before refuses the stage and names its `ALTER TABLE`.
- **The timing check:** a `desync`, or a gap or lost code words inside a measured run, make the session tier D.
- **The report** names each loss in its 24 h with what it did, each session whose code words are on a split clock
  with their worst-case drift at 50 ppm, and why a segment that contradicts its clock is read as a restart.
- **The synthesizer** writes the box's log as the box does today: process, clocks, the late first frame, a roll
  in place, a gap, a restart's new process, and `L` records built through wl-sync's own class.
- **Part 1's eight deferred findings** are fixed (spec §9).

## 2. What the other repositories must do

- **wl-sync:** nothing to build. Told with the merge that the pin has moved and the box may run its change;
  asked to confirm that a `W` tick, in seconds, is the time since its process's capture began
  (`HANDOVER-wl-sync.md`, its answers of 2026-11-06).

## 3. Still open

- **Part 2 of the 2026-10-22 design,** the bridges, now bridges a new process.
- **Code words on a split clock** are reported, not corrected; wl-sync's one-clock design is decided after bench
  day.

## 4. The rulings

The spec's five rulings and its amendments 1-10.

## 5. What execution measured

Executed from {{DATE}}: Task 1 `{{SHA1}}`, Task 2 `{{SHA2}}`, Task 3 `{{SHA3}}`, Task 4 `{{SHA4}}`, Task 5
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

Expected, each exiting 0: 3.11, **2664 passed, 26 skipped, 1 deselected, 1 xfailed**; 3.13, **2664 passed, 27 skipped, 1 xfailed**. About 45 minutes, or an hour on a busy host. That is 61 tests more than `main`'s, this piece's; `tests/responder/test_health.py`'s floor test skips on a host with more than 800 GiB free, so on a fuller disk it passes instead. CI, without the
Andersson data, should show about 2655 passed, 36 skipped. A MySQL container that does not start within its 120 s under
heavy load errors every database test at once; that is the known flake, and the suite is run again.

- [ ] **Step 5: Fill the handoff's §5** with this execution's own figures: `{{DATE}}` the days,
  `{{SHA1}}` to `{{SHA5}}` Tasks 1-5's commits (`git log --oneline -6`), and `{{S311}}` and
  `{{S313}}` Step 4's two summary lines. Then `grep -c '{{' docs/handoffs/2026-11-04-syncbox-header.md`.

Expected: `0`.

- [ ] **Step 6: Commit.**

```bash
git add -A docs wl.yaml HANDOVER-wl-sync.md
git commit -F - <<'MSG'
docs: the header follow-up's records -- spec amendments 1-10, wl-preproc's answers in HANDOVER-wl-sync.md, its handoff, and the checkpoint's and wl.yaml's lines: built, not merged; next, the review

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```
