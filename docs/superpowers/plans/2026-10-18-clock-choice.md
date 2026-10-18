# The Session's Clock as a Recorded Choice (Part 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Record which clock keeps each session's time, and make every stage read the session's time through
one interface, with the sync box still the only clock and every session's results unchanged.

**Architecture:** `timebase.SessionClock` (Imported) records the choice; `timebase/clock.py` holds `choose`,
`Clock` and `clock_for`, which records the choice when a stage first asks (in the daemon's order, the events
stage). The five stages that reached the sync box directly read through `clock_for`, and a test finds any that
does not.

**Tech Stack:** Python 3.11/3.13, DataJoint 2.3 on MySQL (testcontainers in tests), pynwb.

**Spec:** `docs/superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md` (approved
2026-10-18): §2's part 2 and §4. Amendments 3-7 are Task 3's, made while proving this plan.

**Proven before written.** Every step below was run on a scratch branch and replayed from this plan's own
text to an identical tree: 9 mutation checks caught, and the full suite on both interpreters as
Task 3 states.

## Global Constraints

- **Nothing changes for any session** (spec §2, part 2): the sync box is the only clock, and every row, tier
  and NWB file's contents stay as they were. The full suite is that evidence (Task 3).
- **`SessionClock.source` is declared with all three values,** `syncbox`, `nidq` and `lab` (spec §4.1), so
  part 3 needs no `ALTER`.
- **No contract changes** (listing, description, protocol): wl.works is not told.
- **Execute on a branch `feat/clock-choice`** cut from `spec/training-records-and-backup-clock` once this plan
  is committed there; that branch's merge brings the spec and the asks to `main` too.
- **Python 3.11 and 3.13**: the full suite runs once on each, at the end (Task 3).
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code,
  data or tables; leave the stray `wl-preproc` symlink at the repository root out of every commit.
- **Commands run from the repository root.** In a git worktree, `.venv/bin/python` is the main checkout's;
  every test command sets `PYTHONPATH=$PWD` and `PYTHONDONTWRITEBYTECODE=1` (a stale `.pyc` can defeat a
  mutation check).
- **Patches apply with `git apply` and must apply cleanly**; each step's patch is the exact change.
- **Neighbour runs name their files in the suite's order** (spec amendment 7): `tests/schema/test_guardrails.py`
  run before `tests/schema/test_daemon.py` fails three of its clean-pass tests, on `main` too.
- **Every commit ends with:**
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
  ```

## Review Focus

The five inputs the spec implies and no other test exercises, most likely first; each is pinned by a test in
the task that owns the code:
1. **A sync box log whose barcode line is dead** (no barcode decodes): still the sync box's clock, the log
   being there; asking it the time raises as the events stage raised before, and no other clock is chosen.
   `test_a_sync_box_log_with_no_barcode_is_the_clock_and_cannot_time_the_session` (Task 1).
2. **A sync box log gone after the clock was chosen** (a scratch copy freed and not yet rehydrated): the
   recorded choice stands. `test_the_clock_is_chosen_once` (Task 1).
3. **A session no clock can time:** `NoClock`, nothing recorded, and the events stage reports it on each pass.
   `test_a_session_no_clock_can_time_records_none` (Task 1); `tests/schema/test_eye_populate.py::
   test_a_session_whose_events_never_assembled_is_excluded` still finds the events stage's error (Task 2).
4. **A recorded clock this code has not built** (a row a later part wrote, read by this one): refused, never
   answered as the sync box. `test_only_the_sync_box_clock_is_built_yet` (Task 1).
5. **A stage written later that reaches the sync box directly:** found by
   `test_every_stage_reads_the_sessions_time_through_the_clock` (Task 2).

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
| `wl_preproc/timebase/clock.py` (new) | `choose`, `Clock`, `clock_for`; the sync box's codes; t = 0's wall-clock time | 1 |
| `wl_preproc/schema/timebase.py` | `SessionClock`; the clock fits and the timing provenance read through the clock | 1, 2 |
| `wl_preproc/schema/events.py`, `schema/eye.py`, `schema/core.py` | the events stage, the eye calibration and the segments read through the clock | 2 |
| `wl_preproc/nwb/gather.py` | t = 0 through the clock; `reference_from` imported from it | 2 |
| the spec, `docs/handoffs/2026-10-18-clock-choice.md` (new), `docs/CHECKPOINT.md`, `wl.yaml` | the records | 3 |

---

### Task 1: The session's clock, recorded once

The table that records which clock keeps a session's time, and the one interface that answers for it (spec §4.1, §4.3, §4.4; amendments 3, 4 and 6). The sync box is the only clock built, so every answer is the sync box's, computed exactly as the stages computed it before.

**Files:**
- Create: `wl_preproc/timebase/clock.py`
- Modify: `wl_preproc/schema/timebase.py` (`SessionClock`, before `SystemTimebase`)
- Test: `tests/timebase/test_clock.py` (new), `tests/schema/test_session_clock.py` (new)

**Interfaces:**
- Consumes: `timebase/segments.py::session_reference(session_dir) -> dict[int, float]` and
  `scan_system`; `timebase/fit.py::RateFit`, `fit_offset`; `events/extract.py::extract_syncbox_words`;
  `timebase/extract.py::find_recordings`.
- Produces:
  - `timebase.SessionClock` (`dj.Imported`; key: the session): `source` enum(`syncbox`, `nidq`, `lab`),
    `reason` varchar(255);
  - `timebase/clock.py`: `SOURCES`, `CLOCK_DISAGREEMENT_S = 60.0`, `BARCODE_EPOCH`; `class NoClock(Exception)`;
    `Choice(source: str, reason: str)`; `choose(session_dir: Path) -> Choice`;
    `aware_utc(datetime) -> datetime`; `reference_from(first_barcode_value: int, started_at) -> dict`;
    `syncbox_codes(session_dir: Path) -> list[tuple[float, int]]`;
  - `Clock(source: str, session_dir: Path)` with `barcode_reference() -> dict[int, float]`,
    `codes() -> list[tuple[float, int]]` and `wall_reference(started_at) -> dict`; a source other than
    `syncbox` raises `NoClock`;
  - `clock_for(session_key: dict, session_dir: Path | None = None) -> Clock`, recording the choice when a
    stage first asks.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/schema/test_session_clock.py b/tests/schema/test_session_clock.py
new file mode 100644
index 0000000..70aae69
--- /dev/null
+++ b/tests/schema/test_session_clock.py
@@ -0,0 +1,99 @@
+"""`timebase.SessionClock`: which clock keeps each session's time, chosen
+once, by the first stage that asks (design spec
+`2026-10-18-training-records-and-backup-clock-design.md` section 4.1)."""
+
+from __future__ import annotations
+
+import shutil
+
+import datajoint as dj
+import pytest
+
+from tests.identities import new_animal
+
+
+@pytest.fixture(scope="module")
+def daemon_module(dj_conn, prefix):
+    from wl_preproc import daemon
+
+    daemon.activate_all(prefix=prefix)
+    return daemon
+
+
+@pytest.fixture
+def landed(daemon_module, tmp_path):
+    """Land a `ci` session, generated and landed by hand as
+    `test_eye_populate.py` lands one; returns `(key, session_dir)`. Each
+    session is deleted afterwards: one whose sync box log a test removed would
+    otherwise break any later module's unrestricted populate."""
+    from wl_preproc.schema import pipeline
+    from wl_preproc.synth.recipe import RECIPES
+    from wl_preproc.synth.session import generate_session
+
+    from tests.schema.test_eye_populate import _land
+
+    keys = []
+
+    def land() -> tuple[dict, object]:
+        session = new_animal().session()
+        recipe = RECIPES["ci"].model_copy(update={"subject": session.subject, "session_id": session.session_id})
+        generate_session(tmp_path, recipe)
+        key = _land(tmp_path, recipe, session.session_datetime, acquisition_systems=recipe.systems)
+        keys.append(key)
+        return key, tmp_path / recipe.session_id
+
+    yield land
+    for key in keys:
+        (pipeline.Session & key).delete(prompt=False)
+
+
+def test_the_clock_is_keyed_by_the_session_and_read_from_its_folder(daemon_module, enum_values):
+    """Imported, not Computed: the choice reads the session folder."""
+    from wl_preproc.schema import pipeline, timebase
+
+    assert issubclass(timebase.SessionClock, dj.Imported)
+    assert timebase.SessionClock.primary_key == pipeline.Session.primary_key
+    assert enum_values(timebase.SessionClock.heading.attributes["source"].type) == {"syncbox", "nidq", "lab"}
+
+
+def test_the_first_stage_to_ask_records_the_clock(landed):
+    from wl_preproc.schema import timebase
+    from wl_preproc.timebase.clock import clock_for
+
+    key, _session_dir = landed()
+    assert not len(timebase.SessionClock & key)
+    assert clock_for(key).source == "syncbox"
+    assert (timebase.SessionClock & key).fetch1("source", "reason") == (
+        "syncbox", "the sync box recorded this session")
+
+
+def test_the_clock_is_chosen_once(landed):
+    """A recorded choice stands: removing the sync box's log afterwards does
+    not choose again."""
+    from wl_preproc.schema import timebase
+    from wl_preproc.timebase.clock import clock_for
+
+    key, session_dir = landed()
+    clock_for(key)
+    shutil.rmtree(session_dir / "syncbox")
+    assert clock_for(key).source == "syncbox"
+    assert len(timebase.SessionClock & key) == 1
+
+
+def test_a_session_no_clock_can_time_records_none(landed):
+    from wl_preproc.schema import timebase
+    from wl_preproc.timebase.clock import NoClock, clock_for
+
+    key, session_dir = landed()
+    shutil.rmtree(session_dir / "syncbox")
+    with pytest.raises(NoClock, match="no sync box log, so no clock is built for this session yet"):
+        clock_for(key)
+    assert not len(timebase.SessionClock & key)
+
+
+def test_populate_makes_the_same_choice(landed):
+    from wl_preproc.schema import timebase
+
+    key, _session_dir = landed()
+    timebase.SessionClock.populate(key, suppress_errors=False)
+    assert (timebase.SessionClock & key).fetch1("source") == "syncbox"
diff --git a/tests/timebase/test_clock.py b/tests/timebase/test_clock.py
new file mode 100644
index 0000000..788a24b
--- /dev/null
+++ b/tests/timebase/test_clock.py
@@ -0,0 +1,106 @@
+"""Which clock keeps a session's time, and the one door every stage reads it
+through (design spec `2026-10-18-training-records-and-backup-clock-design.md`
+section 4). Part 2 of that design: the sync box is still the only clock, so
+each answer here is the sync box's, as the stages computed it before."""
+
+from __future__ import annotations
+
+import datetime
+
+import pytest
+
+
+def _session(tmp_path, recipe_name: str = "ci"):
+    from wl_preproc.synth.recipe import RECIPES
+    from wl_preproc.synth.session import generate_session
+
+    recipe = RECIPES[recipe_name]
+    generate_session(tmp_path, recipe)
+    return tmp_path / recipe.session_id
+
+
+def test_a_session_the_sync_box_recorded_is_timed_by_it(tmp_path):
+    from wl_preproc.timebase.clock import Choice, choose
+
+    assert choose(_session(tmp_path)) == Choice(source="syncbox", reason="the sync box recorded this session")
+
+
+def test_a_session_without_a_sync_box_log_has_no_clock_yet(tmp_path):
+    """Section 4.2: the NI (part 3) and lab time (part 4) are not built, so a
+    session the sync box did not record has no clock, and says why."""
+    import shutil
+
+    from wl_preproc.timebase.clock import NoClock, choose
+
+    session_dir = _session(tmp_path)
+    shutil.rmtree(session_dir / "syncbox")
+    with pytest.raises(NoClock, match="no sync box log, so no clock is built for this session yet"):
+        choose(session_dir)
+
+
+def test_the_sync_box_clock_answers_with_its_own_barcodes(tmp_path):
+    from wl_preproc.timebase.clock import Clock
+    from wl_preproc.timebase.segments import session_reference
+
+    session_dir = _session(tmp_path)
+    assert Clock(source="syncbox", session_dir=session_dir).barcode_reference() == session_reference(session_dir)
+
+
+def test_the_sync_box_clocks_codes_are_its_log_in_session_time(tmp_path):
+    """Every code the sync box logged, in emission order, at session times
+    starting at its first barcode: the canonical stream the events stage
+    decodes."""
+    from wl_preproc.contracts.events import Marker, decode_stream
+    from wl_preproc.events.extract import extract_syncbox_words
+    from wl_preproc.timebase.clock import Clock
+    from wl_preproc.timebase.extract import find_recordings
+
+    session_dir = _session(tmp_path)
+    codes = Clock(source="syncbox", session_dir=session_dir).codes()
+    (log,) = find_recordings("syncbox", session_dir / "syncbox")
+    assert [code for _time, code in codes] == [code for _position, code in extract_syncbox_words(log).words]
+    times = [time for time, _code in codes]
+    assert times == sorted(times) and 0.0 <= times[0] < 1.0
+    assert any(getattr(event, "code", None) == Marker.SESSION_END for event in decode_stream(codes))
+
+
+def test_the_sync_box_clock_places_t0_by_its_first_barcode(tmp_path):
+    """The NWB file's wall-clock time of t = 0 (the NWB builder spec section
+    4.2), as `nwb/gather.py` took it before."""
+    from wl_preproc.timebase.clock import BARCODE_EPOCH, Clock
+    from wl_preproc.timebase.segments import session_reference
+
+    session_dir = _session(tmp_path)
+    reference = session_reference(session_dir)
+    first = min(reference, key=reference.get)
+    started_at = BARCODE_EPOCH + datetime.timedelta(seconds=first + 3)
+    clock = Clock(source="syncbox", session_dir=session_dir).wall_reference(started_at)
+    assert clock["source"] == "barcode"
+    assert clock["reference_time"] == BARCODE_EPOCH + datetime.timedelta(seconds=first)
+
+
+def test_only_the_sync_box_clock_is_built_yet(tmp_path):
+    """Part 2 builds one clock; a row naming another cannot be answered for
+    until its part is built, and says so rather than answering as the sync
+    box."""
+    from wl_preproc.timebase.clock import Clock, NoClock
+
+    with pytest.raises(NoClock, match="the nidq clock is not built yet"):
+        Clock(source="nidq", session_dir=tmp_path)
+
+
+def test_a_sync_box_log_with_no_barcode_is_the_clock_and_cannot_time_the_session(tmp_path):
+    """A log whose barcode line is dead is still the sync box's: the log is
+    there, so the sync box is the clock, and asking it the time raises, as the
+    events stage raised before. Nothing chooses another clock (the plan's
+    Review Focus 1)."""
+    from wl_preproc.timebase.clock import Clock, choose
+    from wl_preproc.timebase.extract import find_recordings
+
+    session_dir = _session(tmp_path)
+    (log,) = find_recordings("syncbox", session_dir / "syncbox")
+    header, *records = log.read_text().splitlines()
+    log.write_text("\n".join([header, *(record for record in records if not record.startswith("E,"))]) + "\n")
+    assert choose(session_dir).source == "syncbox"
+    with pytest.raises(ValueError, match="the sync box log decoded no barcodes"):
+        Clock(source="syncbox", session_dir=session_dir).codes()
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/timebase/test_clock.py tests/schema/test_session_clock.py -q -p no:cacheprovider
```

Expected: `12 failed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/schema/timebase.py b/wl_preproc/schema/timebase.py
index cd7195c..b0a455e 100644
--- a/wl_preproc/schema/timebase.py
+++ b/wl_preproc/schema/timebase.py
@@ -79,6 +79,38 @@ _FIT_STATUS_ENUM = "enum('fitted','no_recording','unfittable')"
 # (`responder/jobs.py::_check_runs`).
 
 
+@schema
+class SessionClock(dj.Imported):
+    definition = """
+    # Which clock keeps the session's time, chosen once, by the first stage
+    # that asks: the events stage, in the daemon's order (design spec
+    # `2026-10-18-training-records-and-backup-clock-design.md` section 4.1).
+    # Every stage reads it through `timebase/clock.py::clock_for`.
+    # Key: (subject, session_datetime).
+    -> pipeline.Session
+    ---
+    source : enum('syncbox','nidq','lab')
+    reason : varchar(255)  # one sentence: why this clock
+    """
+
+    @property
+    def key_source(self):
+        """Every landed session. Imported locally, as `SystemTimebase`'s is."""
+        from wl_preproc.schema import ingest
+
+        return pipeline.Session & ingest.Ingestion
+
+    def make(self, key: dict) -> None:
+        """Imported, not Computed: the choice reads the session folder. It is
+        made by `clock_for` when a stage first asks; `populate()` makes the
+        same row."""
+        from wl_preproc.schema import ingest
+        from wl_preproc.timebase.clock import choose
+
+        choice = choose(Path((ingest.Ingestion & key).fetch1("session_dir")))
+        self.insert1({**key, "source": choice.source, "reason": choice.reason})
+
+
 @schema
 class SystemTimebase(dj.Computed):
     definition = f"""
diff --git a/wl_preproc/timebase/clock.py b/wl_preproc/timebase/clock.py
new file mode 100644
index 0000000..7a5825b
--- /dev/null
+++ b/wl_preproc/timebase/clock.py
@@ -0,0 +1,180 @@
+"""Which clock keeps a session's time, and the one door every stage reads it
+through (design spec `2026-10-18-training-records-and-backup-clock-design.md`
+section 4).
+
+A stage asks the session's clock three things: how a system carrying the sync
+box's barcode is placed on session time (`barcode_reference`), the session's
+codes in session seconds (`codes`), and the wall-clock time of t = 0
+(`wall_reference`). `timebase.SessionClock` records which clock that is, chosen
+once by `choose` when a stage first asks (`clock_for`).
+
+**Part 2 of that design: the sync box is still the only clock** (its section
+2). Every answer here is the sync box's, exactly as the stages computed it
+before; the NI (part 3) and lab time (part 4) answer the same three questions
+once they are built."""
+
+from __future__ import annotations
+
+import dataclasses
+import datetime
+from pathlib import Path
+
+from wl_preproc.events.extract import extract_syncbox_words
+from wl_preproc.timebase import segments
+from wl_preproc.timebase.extract import find_recordings
+from wl_preproc.timebase.fit import RateFit, fit_offset
+
+# What may keep a session's time (section 4.1), as `timebase.SessionClock`
+# declares it.
+SOURCES = ("syncbox", "nidq", "lab")
+# Wall clocks that disagree by more than this are not trusted to place t = 0
+# (the NWB builder spec section 4.2). Borrowed from wl-sync's
+# CLOCK_TRUST_TOLERANCE_S.
+CLOCK_DISAGREEMENT_S = 60.0
+# A barcode's value is whole seconds since this instant, off the sync box's
+# wall clock: wl-sync's `wl_sync/clock.py::BARCODE_EPOCH`, since its commit
+# 3ce66b9 (2026-08-17). RESTATED, not imported: the wl-sync commit this
+# repository pins predates that module. `tests/nwb/test_clock.py` pins the
+# two equal wherever a newer wl-sync is installed.
+BARCODE_EPOCH = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
+
+
+class NoClock(Exception):
+    """No clock built so far can time this session; the message says why."""
+
+
+@dataclasses.dataclass(frozen=True)
+class Choice:
+    source: str
+    reason: str
+
+
+def choose(session_dir: Path) -> Choice:
+    """Section 4.2's rule, as far as part 2 builds it: the sync box, when it
+    recorded the session. The rest of the rule (whether its codes hold every
+    run, the NI otherwise, then lab time) arrives with part 3, the first part
+    that has another clock to choose."""
+    if find_recordings("syncbox", session_dir / "syncbox"):
+        return Choice(source="syncbox", reason="the sync box recorded this session")
+    raise NoClock(
+        f"{session_dir}: no sync box log, so no clock is built for this session yet: a session "
+        "without the sync box is timed by the NI (part 3 of the design) or by lab time (part 4)"
+    )
+
+
+def aware_utc(value: datetime.datetime) -> datetime.datetime:
+    return value.replace(tzinfo=datetime.timezone.utc) if value.tzinfo is None else value
+
+
+def reference_from(first_barcode_value: int, started_at: datetime.datetime) -> dict:
+    """The NWB builder spec section 4.2: the first barcode's own value places
+    session t = 0 on the wall clock, unless it disagrees with the manifest's
+    `started_at` by more than `CLOCK_DISAGREEMENT_S`, when `started_at` is
+    used and the source says so."""
+    barcode_time = BARCODE_EPOCH + datetime.timedelta(seconds=int(first_barcode_value))
+    started_at = aware_utc(started_at)
+    difference = (started_at - barcode_time).total_seconds()
+    trusted = abs(difference) <= CLOCK_DISAGREEMENT_S
+    return {
+        "reference_time": barcode_time if trusted else started_at,
+        "source": "barcode" if trusted else "manifest",
+        "manifest_started_at": started_at,
+        "started_at_difference_s": difference,
+    }
+
+
+def syncbox_codes(session_dir: Path) -> list[tuple[float, int]]:
+    """Every code word the sync box recorded, native time converted to session
+    time via `timebase/fit.py` -- the same fit primitives `core.Segment` and
+    `timebase.SystemTimebase` use, so this does not invent a second
+    conversion.
+
+    Identity rate, not `fit_rate`: session time IS the sync box's own timeline
+    (spec section 4.5), the same special case `SystemTimebase.make()` applies
+    to this same system for the same reason -- a fit of it against itself is
+    exactly zero drift with zero residual, the truth rather than a placeholder.
+
+    Moved here from `schema/events.py::decode_syncbox_in_session_time`
+    unchanged: the sync box's codes are the session's codes while it is the
+    clock, and every stage reads them through `Clock.codes`."""
+    reference = segments.session_reference(session_dir)
+    scans = segments.scan_system("syncbox", session_dir / "syncbox")
+    if not scans:
+        raise FileNotFoundError(
+            f"{session_dir}: no syncbox recording, so this session has no "
+            "session time and no code stream to decode from -- every session "
+            "has one (SessionRecipe requires it of the synthetic fixtures, and "
+            "spec section 4.3 makes it essential sync infrastructure)"
+        )
+
+    converted: list[tuple[float, int]] = []
+    for scan in scans:
+        rate = RateFit(
+            fitted_rate_hz=scan.stream.fs_hz,
+            drift_ppm=0.0,
+            n_matched=len(scan.barcodes),
+            residual_us_rms=0.0,
+            residual_us_max=0.0,
+        )
+        offset = fit_offset(scan.barcodes, reference, rate)
+        word_stream = extract_syncbox_words(scan.path)
+        converted.extend(
+            (native_pos / word_stream.fs_hz / rate.scale + offset.offset_s, code)
+            for native_pos, code in word_stream.words
+        )
+
+    # `decode_stream` reads an escape payload by POSITION, not by comparing
+    # timestamps -- see its own docstring -- so a session recorded across more
+    # than one sync box log file needs re-sorting to restore emission order
+    # before decoding. A single file (the common case, and every current
+    # fixture) is already sorted; this is then a no-op.
+    converted.sort(key=lambda pair: pair[0])
+    return converted
+
+
+@dataclasses.dataclass(frozen=True)
+class Clock:
+    """A session's clock, as `timebase.SessionClock` chose it."""
+
+    source: str
+    session_dir: Path
+
+    def __post_init__(self) -> None:
+        if self.source != "syncbox":
+            raise NoClock(f"the {self.source} clock is not built yet; only the sync box's is (part 2)")
+
+    def barcode_reference(self) -> dict[int, float]:
+        """Barcode value -> session seconds: how a system carrying the sync
+        box's barcode is placed on this clock (`timebase/fit.py`)."""
+        return segments.session_reference(self.session_dir)
+
+    def codes(self) -> list[tuple[float, int]]:
+        """The session's code words, `(session seconds, code)`, in emission
+        order: the stream the events stage decodes."""
+        return syncbox_codes(self.session_dir)
+
+    def wall_reference(self, started_at: datetime.datetime) -> dict:
+        """The wall-clock time of session t = 0, and where it came from
+        (`reference_from`)."""
+        reference = self.barcode_reference()
+        return reference_from(min(reference, key=reference.get), started_at)
+
+
+def clock_for(session_key: dict, session_dir: Path | None = None) -> Clock:
+    """The session's clock, as `timebase.SessionClock` recorded it. A
+    session not yet given one is given it now, by `choose`, and that choice
+    is recorded and never made again (section 4.1): the first stage to ask
+    is the events stage, in the daemon's order. Raises `NoClock`, recording
+    nothing, when no clock built so far can time the session."""
+    from wl_preproc.schema import ingest, timebase
+
+    session = {field: session_key[field] for field in ("subject", "session_datetime")}
+    if session_dir is None:
+        session_dir = Path((ingest.Ingestion & session).fetch1("session_dir"))
+    rows = (timebase.SessionClock & session).to_dicts()
+    if not rows:
+        choice = choose(session_dir)
+        timebase.SessionClock.insert1({**session, "source": choice.source, "reason": choice.reason},
+                                      allow_direct_insert=True, skip_duplicates=True)
+        rows = (timebase.SessionClock & session).to_dicts()
+    return Clock(source=rows[0]["source"], session_dir=session_dir)
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/timebase/test_clock.py tests/schema/test_session_clock.py -q -p no:cacheprovider
```

Expected: `12 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_daemon.py tests/schema/test_guardrails.py tests/schema/test_session_clock.py tests/schema/test_timebase.py tests/timebase -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `135 passed, 1 skipped`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "T1a every session is the sync box's",
  "wl_preproc/timebase/clock.py",
  "    if find_recordings(\"syncbox\", session_dir / \"syncbox\"):\n        return Choice(",
  "    if True:\n        return Choice(",
  "tests/timebase/test_clock.py::test_a_session_without_a_sync_box_log_has_no_clock_yet tests/schema/test_session_clock.py::test_a_session_no_clock_can_time_records_none"
 ],
 [
  "T1b chosen again on every ask",
  "wl_preproc/timebase/clock.py",
  "    rows = (timebase.SessionClock & session).to_dicts()\n    if not rows:",
  "    rows = []\n    if not rows:",
  "tests/schema/test_session_clock.py::test_the_clock_is_chosen_once"
 ],
 [
  "T1c the choice not recorded",
  "wl_preproc/timebase/clock.py",
  "        timebase.SessionClock.insert1({**session, \"source\": choice.source, \"reason\": choice.reason},\n                                      allow_direct_insert=True, skip_duplicates=True)\n        rows = (timebase.SessionClock & session).to_dicts()",
  "        rows = [{\"source\": choice.source}]",
  "tests/schema/test_session_clock.py::test_the_first_stage_to_ask_records_the_clock"
 ],
 [
  "T1d another source answered as the sync box",
  "wl_preproc/timebase/clock.py",
  "        if self.source != \"syncbox\":\n            raise NoClock(",
  "        if False:\n            raise NoClock(",
  "tests/timebase/test_clock.py::test_only_the_sync_box_clock_is_built_yet"
 ],
 [
  "T1e t = 0 from the last barcode",
  "wl_preproc/timebase/clock.py",
  "        return reference_from(min(reference, key=reference.get), started_at)",
  "        return reference_from(max(reference, key=reference.get), started_at)",
  "tests/timebase/test_clock.py::test_the_sync_box_clock_places_t0_by_its_first_barcode"
 ],
 [
  "T1f a log with no barcode read as no codes",
  "wl_preproc/timebase/clock.py",
  "    reference = segments.session_reference(session_dir)\n    scans = segments.scan_system(\"syncbox\", session_dir / \"syncbox\")\n",
  "    try:\n        reference = segments.session_reference(session_dir)\n    except ValueError:\n        return []\n    scans = segments.scan_system(\"syncbox\", session_dir / \"syncbox\")\n",
  "tests/timebase/test_clock.py::test_a_sync_box_log_with_no_barcode_is_the_clock_and_cannot_time_the_session"
 ]
]
```

Expected: 6 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(timebase): the session's clock as a recorded choice -- timebase.SessionClock, chosen once when a stage first asks, and timebase/clock.py's Clock, whose barcode reference, codes and wall-clock t = 0 are the sync box's while it is the only clock (training-records spec section 4, part 2)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 2: Every stage reads the session's time through its clock

The five stages that reached the sync box directly read through `clock_for`, and a test finds any module that does not (spec §4.3; amendments 5 and 6). `schema/events.py`'s `decode_syncbox_in_session_time` is gone, its body `timebase/clock.py::syncbox_codes` since Task 1; `nwb/gather.py` imports `reference_from` and its constants from the clock, so they are still importable there.

**Files:**
- Modify: `wl_preproc/schema/events.py` (`populate_session`; the old decode removed)
- Modify: `wl_preproc/schema/eye.py` (`EyeCalibration.make`), `wl_preproc/schema/core.py` (`Segment.make`)
- Modify: `wl_preproc/schema/timebase.py` (`SystemTimebase.make`, `TimingProvenance.make`)
- Modify: `wl_preproc/nwb/gather.py` (t = 0; `reference_from` and its constants imported)
- Test: `tests/timebase/test_clock.py`, `tests/schema/test_session_clock.py`

**Interfaces:**
- Consumes: Task 1's `clock_for`, `Clock.codes`, `Clock.barcode_reference`, `Clock.wall_reference`,
  `reference_from`, `BARCODE_EPOCH`, `CLOCK_DISAGREEMENT_S`, `aware_utc`.
- Produces: nothing new; `nwb/gather.py` still exports `BARCODE_EPOCH`, `CLOCK_DISAGREEMENT_S` and
  `reference_from` (`tests/nwb/test_clock.py` imports them there).

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/schema/test_session_clock.py b/tests/schema/test_session_clock.py
index 70aae69..7723fa6 100644
--- a/tests/schema/test_session_clock.py
+++ b/tests/schema/test_session_clock.py
@@ -97,3 +97,14 @@ def test_populate_makes_the_same_choice(landed):
     key, _session_dir = landed()
     timebase.SessionClock.populate(key, suppress_errors=False)
     assert (timebase.SessionClock & key).fetch1("source") == "syncbox"
+
+
+def test_a_daemon_pass_chooses_the_clock_in_its_events_stage(daemon_module, prefix, landed):
+    """One pass chooses the clock and, reading through it, builds the
+    session's trial list."""
+    from wl_preproc.schema import pipeline, timebase
+
+    key, _session_dir = landed()
+    report = daemon_module.run_once(prefix=prefix)
+    assert (timebase.SessionClock & key).fetch1("source") == "syncbox"
+    assert len(pipeline.event.BehaviorRecording & key), report["errors"]
diff --git a/tests/timebase/test_clock.py b/tests/timebase/test_clock.py
index 788a24b..fc5b165 100644
--- a/tests/timebase/test_clock.py
+++ b/tests/timebase/test_clock.py
@@ -104,3 +104,25 @@ def test_a_sync_box_log_with_no_barcode_is_the_clock_and_cannot_time_the_session
     assert choose(session_dir).source == "syncbox"
     with pytest.raises(ValueError, match="the sync box log decoded no barcodes"):
         Clock(source="syncbox", session_dir=session_dir).codes()
+
+
+def test_every_stage_reads_the_sessions_time_through_the_clock():
+    """Section 4.3: nothing outside `timebase/clock.py` reaches the sync box's
+    barcode reference or its codes directly, so a second clock is one module's
+    change (`timebase/segments.py` defines the reference)."""
+    import re
+    from pathlib import Path
+
+    import wl_preproc
+
+    package = Path(wl_preproc.__file__).parent
+    direct = re.compile(r"\b(session_reference|syncbox_codes|decode_syncbox_in_session_time)\(")
+    allowed = {package / "timebase" / "clock.py", package / "timebase" / "segments.py"}
+    reaching = sorted(
+        f"{path.relative_to(package)}:{number}"
+        for path in package.rglob("*.py")
+        if path not in allowed
+        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
+        if direct.search(line) and not line.lstrip().startswith("#")
+    )
+    assert reaching == []
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/timebase/test_clock.py::test_every_stage_reads_the_sessions_time_through_the_clock tests/schema/test_session_clock.py::test_a_daemon_pass_chooses_the_clock_in_its_events_stage -q -p no:cacheprovider
```

Expected: `2 failed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/nwb/gather.py b/wl_preproc/nwb/gather.py
index 41ef3ad..37d4ee6 100644
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -9,24 +9,19 @@ so every writer is tested without either."""
 from __future__ import annotations
 
 import dataclasses
-import datetime
 from pathlib import Path
 
 import numpy as np
 
 from wl_preproc.nwb.describe import task_name
 from wl_preproc.nwb.trim import BlockSet
+# Where the wall-clock time of t = 0 comes from is the session clock's to
+# answer (`timebase/clock.py`); these stay importable here, where
+# `tests/nwb/test_clock.py` has always read them.
+from wl_preproc.timebase.clock import BARCODE_EPOCH, CLOCK_DISAGREEMENT_S, clock_for, reference_from  # noqa: F401
+from wl_preproc.timebase.clock import aware_utc as _aware_utc
 
 MASK_LABELS = ("blink", "invalid")
-# Wall clocks that disagree by more than this are not trusted to place t = 0
-# (section 4.2). Borrowed from wl-sync's CLOCK_TRUST_TOLERANCE_S.
-CLOCK_DISAGREEMENT_S = 60.0
-# A barcode's value is whole seconds since this instant, off the sync box's
-# wall clock: wl-sync's `wl_sync/clock.py::BARCODE_EPOCH`, since its commit
-# 3ce66b9 (2026-08-17). RESTATED, not imported: the wl-sync commit this
-# repository pins predates that module. `tests/nwb/test_clock.py` pins the
-# two equal wherever a newer wl-sync is installed.
-BARCODE_EPOCH = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
 
 
 class Refused(Exception):
@@ -68,10 +63,6 @@ class Gathered:
     rig_record: dict | None = None
 
 
-def _aware_utc(value: datetime.datetime) -> datetime.datetime:
-    return value.replace(tzinfo=datetime.timezone.utc) if value.tzinfo is None else value
-
-
 def _run_set(activation_key: dict, session_key: dict) -> list[dict]:
     """The file's runs (`request.ActivationRun`), measured (`core.Run`), with
     wl.works' id for each and the rig's name for its task (design spec
@@ -188,30 +179,6 @@ def agreement_rows(rows: list[dict], names: dict) -> list[dict]:
             for row in rows if row["paramset_a"] in names and row["paramset_b"] in names]
 
 
-def reference_from(first_barcode_value: int, started_at: datetime.datetime) -> dict:
-    """Section 4.2: the first barcode's own value places session t = 0 on
-    the wall clock, unless it disagrees with the manifest's `started_at` by
-    more than `CLOCK_DISAGREEMENT_S`, when `started_at` is used and the
-    source says so."""
-    barcode_time = BARCODE_EPOCH + datetime.timedelta(seconds=int(first_barcode_value))
-    started_at = _aware_utc(started_at)
-    difference = (started_at - barcode_time).total_seconds()
-    trusted = abs(difference) <= CLOCK_DISAGREEMENT_S
-    return {
-        "reference_time": barcode_time if trusted else started_at,
-        "source": "barcode" if trusted else "manifest",
-        "manifest_started_at": started_at,
-        "started_at_difference_s": difference,
-    }
-
-
-def _reference_time(session_dir: Path, started_at: datetime.datetime) -> dict:
-    from wl_preproc.timebase.segments import session_reference
-
-    reference = session_reference(session_dir)
-    return reference_from(min(reference, key=reference.get), started_at)
-
-
 def _subject(subject_id: str) -> dict:
     from wl_preproc.ingest.landing import SUBJECT_BIRTH_DATE_UNKNOWN
     from wl_preproc.schema import pipeline
@@ -711,7 +678,7 @@ def gather(activation_key: dict) -> Gathered:
         probe["sorted_runs"] = sorted_runs.get(probe["serial"], [])
 
     session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
-    clock = _reference_time(session_dir, key["session_datetime"])
+    clock = clock_for(session_key, session_dir).wall_reference(key["session_datetime"])
     validity_idx, detection_idx = _paramsets()
     eye = _eye(session_key, session_dir, extent, validity_idx, detection_idx)
     no_eye_samples = eye is not None and eye.get("no_samples", False)
diff --git a/wl_preproc/schema/core.py b/wl_preproc/schema/core.py
index f7bdfc2..707ff96 100644
--- a/wl_preproc/schema/core.py
+++ b/wl_preproc/schema/core.py
@@ -263,6 +263,7 @@ class Segment(dj.Computed):
         """
         from wl_preproc.schema import ingest, timebase
         from wl_preproc.timebase import segments
+        from wl_preproc.timebase.clock import clock_for
         from wl_preproc.timebase.fit import fit_offset
 
         session_key = {k: key[k] for k in pipeline.Session.primary_key}
@@ -282,7 +283,7 @@ class Segment(dj.Computed):
         rate = (
             segments.rate_fit_from_row(fit) if fit["fit_status"] == "fitted" else None
         )
-        reference = segments.session_reference(session_dir)
+        reference = clock_for(session_key, session_dir).barcode_reference()
 
         accepted, rejected = [], []
         for scan in scans:
diff --git a/wl_preproc/schema/events.py b/wl_preproc/schema/events.py
index bd2d3be..79c935d 100644
--- a/wl_preproc/schema/events.py
+++ b/wl_preproc/schema/events.py
@@ -52,13 +52,11 @@ from wl_preproc.contracts.events import (
     decode_stream,
 )
 from wl_preproc.events.assemble import AssembledBlock, AssembledTrial, assemble
-from wl_preproc.events.extract import extract_syncbox_words
 from wl_preproc.events.rigruns import read_rig_runs
 from wl_preproc.events.rigsession import read_rig_session
 from wl_preproc.events.rigtrials import read_rig_trials
 from wl_preproc.schema import DEFAULT_PREFIX, core, pipeline
-from wl_preproc.timebase import segments
-from wl_preproc.timebase.fit import RateFit, fit_offset
+from wl_preproc.timebase.clock import clock_for
 
 
 def activate(prefix: str = DEFAULT_PREFIX) -> None:
@@ -113,70 +111,6 @@ def _u32(words: tuple[int, ...]) -> int:
     return (words[0] << 16) | words[1]
 
 
-def decode_syncbox_in_session_time(session_dir: Path) -> list[tuple[float, int]]:
-    """Every code word the sync box recorded, native time converted to session
-    time via `timebase/fit.py` -- the same fit primitives `core.Segment` and
-    `timebase.SystemTimebase` use, so this does not invent a second
-    conversion.
-
-    **Public, not module-private** (1c-5 Task 9): this docstring said all
-    along that the NI's own code words are "used only for `event_code_agreement`
-    ... which is `events/agreement.py` and `TimingProvenance`'s job ... not
-    this module's" -- and `TimingProvenance.make()` needs the Pi's SAME
-    canonical decode to compare the NI's record against. Restating this
-    function there, the way `events.py`'s own `_u32` restates `assemble.py`'s,
-    would duplicate the non-trivial half (barcode reference, identity rate,
-    offset fit) that restating a two-word integer pack does not risk --
-    `timebase/coverage.py`'s own rule against a second implementation applies
-    here for the same reason.
-
-    Sync box only, deliberately. The NI's own code words are a SECOND,
-    independent record used only for `event_code_agreement` (tier A) and the
-    tier B/C witness counts, which is `TimingProvenance`'s job, not this
-    module's: the canonical trial list is built from exactly one full-code
-    record.
-
-    Identity rate, not `fit_rate`: session time IS the sync box's own
-    timeline (spec section 4.5), the same special case `SystemTimebase.make()`
-    applies to this same system for the same reason -- a fit of it against
-    itself is exactly zero drift with zero residual, the truth rather than a
-    placeholder.
-    """
-    reference = segments.session_reference(session_dir)
-    scans = segments.scan_system("syncbox", session_dir / "syncbox")
-    if not scans:
-        raise FileNotFoundError(
-            f"{session_dir}: no syncbox recording, so this session has no "
-            "session time and no code stream to decode from -- every session "
-            "has one (SessionRecipe requires it of the synthetic fixtures, and "
-            "spec section 4.3 makes it essential sync infrastructure)"
-        )
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
-    # timestamps -- see its own docstring -- so a session recorded across more
-    # than one sync box log file needs re-sorting to restore emission order
-    # before decoding. A single file (the common case, and every current
-    # fixture) is already sorted; this is then a no-op.
-    converted.sort(key=lambda pair: pair[0])
-    return converted
-
-
 def _containing_block(
     trial: AssembledTrial, blocks: list[AssembledBlock], stream_end_s: float
 ) -> AssembledBlock | None:
@@ -413,7 +347,7 @@ def populate_session(key: dict, session_dir: Path) -> None:
     """
     session_key = {k: key[k] for k in pipeline.Session.primary_key}
 
-    converted = decode_syncbox_in_session_time(session_dir)
+    converted = clock_for(session_key, session_dir).codes()
     decoded = decode_stream(converted)
     assembly = assemble(decoded)
 
diff --git a/wl_preproc/schema/eye.py b/wl_preproc/schema/eye.py
index 5d9cd80..45c932d 100644
--- a/wl_preproc/schema/eye.py
+++ b/wl_preproc/schema/eye.py
@@ -414,13 +414,13 @@ class EyeCalibration(dj.Computed):
         `CONDITION` get one (see that function's own body) -- so the
         `(role, x, y)` triple this table needs survives only in the raw
         stream. `TimingProvenance.make()` re-decodes the identical stream
-        for the identical reason; see `schema/events.py`'s own "Public, not
-        module-private" paragraph on `decode_syncbox_in_session_time`, which
-        is what makes this a second CALLER of one decode rather than a
-        second, independently drifting implementation of it.
+        for the identical reason; both read it through the session's clock
+        (`timebase/clock.py::Clock.codes`), which is what makes this a second
+        CALLER of one decode rather than a second, independently drifting
+        implementation of it.
         """
         from wl_preproc.schema import ingest
-        from wl_preproc.schema.events import decode_syncbox_in_session_time
+        from wl_preproc.timebase.clock import clock_for
 
         session_key = {k: key[k] for k in pipeline.Session.primary_key}
         session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
@@ -471,7 +471,7 @@ class EyeCalibration(dj.Computed):
 
         # -- Re-decode the sync box's own code stream (see this method's own
         # docstring for why this cannot instead read `pipeline.event.Event`).
-        decoded = decode_stream(decode_syncbox_in_session_time(session_dir))
+        decoded = decode_stream(clock_for(session_key, session_dir).codes())
 
         # -- One pass over the stream: track the most recently announced
         # target per role and the most recently opened block, and close a
diff --git a/wl_preproc/schema/timebase.py b/wl_preproc/schema/timebase.py
index b0a455e..67efd6f 100644
--- a/wl_preproc/schema/timebase.py
+++ b/wl_preproc/schema/timebase.py
@@ -165,6 +165,7 @@ class SystemTimebase(dj.Computed):
         """
         from wl_preproc.schema import ingest
         from wl_preproc.timebase import segments
+        from wl_preproc.timebase.clock import clock_for
         from wl_preproc.timebase.fit import fit_rate_across
 
         session_dir = Path(
@@ -218,7 +219,7 @@ class SystemTimebase(dj.Computed):
             )
             return
 
-        reference = segments.session_reference(session_dir)
+        reference = clock_for(key, session_dir).barcode_reference()
         try:
             fit = fit_rate_across([scan.barcodes for scan in scans], reference, nominal_rate_hz)
         except ValueError:
@@ -338,8 +339,8 @@ class TimingProvenance(dj.Computed):
         from wl_preproc.events.extract import extract_nidq_words, extract_rhs_witness
         from wl_preproc.events.taskfile import SyntheticTaskFileReader, UnsupportedTaskFile
         from wl_preproc.schema import core, ingest
-        from wl_preproc.schema.events import decode_syncbox_in_session_time
         from wl_preproc.timebase import segments
+        from wl_preproc.timebase.clock import clock_for
         from wl_preproc.timebase.fit import fit_offset
 
         session_key = {k: key[k] for k in pipeline.Session.primary_key}
@@ -393,8 +394,8 @@ class TimingProvenance(dj.Computed):
         #
         # A second layer catches the reverse: `SystemTimebase.make()` marks
         # syncbox "fitted" even with zero decoded barcodes (a real log file
-        # that happens to be empty), so `decode_syncbox_in_session_time`'s own
-        # `session_reference` can still raise `ValueError`. Caught here rather
+        # that happens to be empty), so the clock's codes (`Clock.codes`,
+        # through `session_reference`) can still raise `ValueError`. Caught here rather
         # than left to propagate, for the same reason a single trial's decode
         # error does not lose the whole session in `assemble()`.
         #
@@ -430,7 +431,7 @@ class TimingProvenance(dj.Computed):
 
         if syncbox_fitted:
             try:
-                syncbox_pairs = decode_syncbox_in_session_time(session_dir)
+                syncbox_pairs = clock_for(session_key, session_dir).codes()
                 syncbox_available = True
             except (FileNotFoundError, ValueError):
                 syncbox_available = False
@@ -460,7 +461,7 @@ class TimingProvenance(dj.Computed):
                 )
                 if spikeglx_fit is not None:
                     rate = segments.rate_fit_from_row(spikeglx_fit)
-                    reference = segments.session_reference(session_dir)
+                    reference = clock_for(session_key, session_dir).barcode_reference()
                     pairs: list[tuple[float, int]] = []
                     for scan in segments.scan_system("spikeglx", session_dir / "spikeglx"):
                         try:
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/timebase/test_clock.py::test_every_stage_reads_the_sessions_time_through_the_clock tests/schema/test_session_clock.py::test_a_daemon_pass_chooses_the_clock_in_its_events_stage -q -p no:cacheprovider
```

Expected: `2 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/nwb/test_clock.py tests/schema/test_daemon.py tests/schema/test_events.py tests/schema/test_eye_populate.py tests/schema/test_nwb_build.py tests/schema/test_segment_populate.py tests/schema/test_session_clock.py tests/schema/test_timebase.py tests/timebase -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `263 passed, 2 skipped`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "T2a the events stage decodes the sync box itself",
  "wl_preproc/schema/events.py",
  "    converted = clock_for(session_key, session_dir).codes()\n",
  "    from wl_preproc.timebase.clock import syncbox_codes\n    converted = syncbox_codes(session_dir)\n",
  "tests/timebase/test_clock.py::test_every_stage_reads_the_sessions_time_through_the_clock"
 ],
 [
  "T2b a segment reads the sync box's reference itself",
  "wl_preproc/schema/core.py",
  "        reference = clock_for(session_key, session_dir).barcode_reference()\n",
  "        reference = segments.session_reference(session_dir)\n",
  "tests/timebase/test_clock.py::test_every_stage_reads_the_sessions_time_through_the_clock"
 ],
 [
  "T2c the NWB file's t = 0 read from the sync box itself",
  "wl_preproc/nwb/gather.py",
  "    clock = clock_for(session_key, session_dir).wall_reference(key[\"session_datetime\"])\n",
  "    from wl_preproc.timebase.segments import session_reference\n    reference = session_reference(session_dir)\n    clock = reference_from(min(reference, key=reference.get), key[\"session_datetime\"])\n",
  "tests/timebase/test_clock.py::test_every_stage_reads_the_sessions_time_through_the_clock"
 ]
]
```

Expected: 3 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
refactor(timebase): every stage reads the session's time through its clock -- the events stage, the eye calibration, the segments, the clock fits, the timing provenance and the NWB gather; the sync box's reference and codes reached nowhere else, a test says so (spec section 4.3)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 3: The records

The spec's amendments 3-7, this part's handoff, and the checkpoint's and `wl.yaml`'s lines (spec §11). Then
the full suite, once, on both interpreters.

**Files:**
- Modify: `docs/superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md`, `docs/CHECKPOINT.md`,
  `wl.yaml`
- Create: `docs/handoffs/2026-10-18-clock-choice.md`

**Interfaces:** none; documents only.

- [ ] **Step 1: Apply the records.** Save this script outside the repository as `records.py` and run it from
  the repository root.

```python
"""Part 2's records: the spec's amendments 3-7, and the checkpoint's and
wl.yaml's lines (built, not merged; next, the review). Run from the
repository root."""
import pathlib

root = pathlib.Path.cwd()


def swap(rel, old, new):
    path = root / rel
    text = path.read_text()
    assert text.count(old) == 1, (rel, old[:70])
    path.write_text(text.replace(old, new))


spec = root / "docs/superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md"
spec.write_text(spec.read_text().rstrip("\n") + """

## Amendments, 2026-10-18, made while proving part 2's plan

3. **The clock is chosen when a stage first asks for it,** in that stage's transaction, and recorded:
   `timebase.SessionClock`, an Imported table, since the choice reads the session folder. In the
   daemon's order the first to ask is the events stage. §4.1 said "in its own transaction": a stage
   ahead of the events stage was built first, and every test that runs a stage directly had then to
   choose the clock before it (285 failures and errors across 18 test files, each "no clock is
   chosen"), for nothing a recorded choice does not already give. A choice made by a stage that then
   fails rolls back with it and is made again, identically, on the next pass.
4. **Part 2 checks only that the sync box recorded the session** (its log is there). Whether its
   codes hold every run (§4.2's rule 1) arrives with part 3, the first part with another clock to
   choose. A session the sync box did not record raises "no sync box log, so no clock is built for
   this session yet" in its events stage, on each pass, and records nothing, as the missing log
   raised there before.
5. **The NWB file's sentence about t = 0** (`nwb/timebase.py`) is unchanged in part 2: while the sync
   box is the only clock it is true of every file. Part 3 makes it the clock's.
6. **What moved:** `reference_from`, `BARCODE_EPOCH` and `CLOCK_DISAGREEMENT_S` to
   `timebase/clock.py`, still importable from `nwb/gather.py`; `schema/events.py`'s
   `decode_syncbox_in_session_time` to `timebase/clock.py::syncbox_codes`, unchanged. A test finds
   any module outside `timebase/clock.py` that reaches the sync box's reference or codes directly.
7. **A plan's neighbour runs name their files in the suite's order.** `tests/schema/test_guardrails.py`'s
   blob probe leaves a session with no sync box log in the shared test database, and run before
   `tests/schema/test_daemon.py` it fails three of that module's clean-pass tests, on the base commit
   as on part 2 (checked at `54d585b`). The suite runs the daemon's tests first.
""")

swap("docs/CHECKPOINT.md",
     "> §7 of `handoffs/2026-10-11-correction-marker.md`), its review's five findings\n"
     "> fixed. Fine-tuning\n",
     "> §7 of `handoffs/2026-10-11-correction-marker.md`), its review's five findings\n"
     "> fixed. **wl-xcon's ask 1, the requester's choice of 2026-10-18, is designed**\n"
     "> with a clock for a session without the sync box\n"
     "> (`specs/2026-10-18-training-records-and-backup-clock-design.md`), in five parts:\n"
     "> part 1, the training records, waits on wl-xcon's `event_code` (its branch\n"
     "> `xc321-event-code`); **part 2, the clock as a recorded choice, is built on\n"
     "> `feat/clock-choice`, not merged** (`handoffs/2026-10-18-clock-choice.md`). Next:\n"
     "> the review. Fine-tuning\n")
swap("docs/CHECKPOINT.md",
     "> *Until 2026-10-14 this said the eye minors were built, not\n",
     "> *Until part 2 of the training-records design was built, this named ask 1 as\n"
     "> next by the January sort, undesigned; true when written.*\n"
     ">\n"
     "> *Until 2026-10-14 this said the eye minors were built, not\n")
swap("wl.yaml",
     "    review's five findings fixed.\n    Fine-tuning U'n'Eye waits for hand-labelled lab data.\n",
     "    review's five findings fixed. wl-xcon's ask 1 is designed with a\n"
     "    clock for a session without the sync box\n"
     "    (docs/superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md,\n"
     "    five parts); part 1 waits on wl-xcon's event_code; part 2, the clock\n"
     "    as a recorded choice, is built on feat/clock-choice and not merged\n"
     "    (docs/handoffs/2026-10-18-clock-choice.md); next, the review.\n"
     "    Fine-tuning U'n'Eye waits for hand-labelled lab data.\n")
print("ok")
```

Expected: it prints `ok`.

- [ ] **Step 2: Write the handoff.** Its §5 keeps `{{...}}` slots until Step 5.

```bash
cat > docs/handoffs/2026-10-18-clock-choice.md <<'HANDOFF'
# The session's clock as a recorded choice (part 2)

**Part 2 of five** of `specs/2026-10-18-training-records-and-backup-clock-design.md`: wl-xcon's ask 1, and
the requester's clock for a session without the sync box. **The sync box is still the only clock**, and
every session's results are unchanged. What changes is that every stage reads the session's time through
one interface, so the NI (part 3) and lab time (part 4) are one module's change.
- **Branch:** `feat/clock-choice`, from `spec/training-records-and-backup-clock`.
- **Spec:** amendments 3-7 are this part's, made while proving its plan.
- **Plan:** `docs/superpowers/plans/2026-10-18-clock-choice.md`.
- **The requester's choice:** to plan part 2 while wl-xcon builds part 1's `event_code` (2026-10-18).

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **`timebase.SessionClock`,** one row per session: `source` (`syncbox`, `nidq` or `lab`) and `reason`, a
  sentence. Imported, since the choice reads the session folder. A database declared before it gets it on
  activation.
- **`timebase/clock.py`:**
  - `choose(session_dir)`: the sync box, when it recorded the session; otherwise `NoClock`, saying so;
  - `Clock(source, session_dir)`: `barcode_reference()`, `codes()` and `wall_reference(started_at)`, each the
    sync box's; another source is refused until its part is built;
  - `clock_for(session_key, session_dir=None)`: the recorded clock, chosen and recorded when a stage first
    asks, and never chosen again;
  - `syncbox_codes`, moved unchanged from `schema/events.py::decode_syncbox_in_session_time`, and
    `reference_from` with its constants, moved from `nwb/gather.py` and still importable there.
- **Every stage reads through it:** `schema/events.py::populate_session`, `EyeCalibration.make`,
  `Segment.make`, `SystemTimebase.make`, `TimingProvenance.make`, and the NWB gather. A test finds any module
  outside `timebase/clock.py` that reaches the sync box's reference or codes directly.

## 2. What the other repositories must do

Nothing. No contract, listing field, description field or file content changes.

## 3. Still open

- **Parts 1, 3, 4 and 5** (the spec's §2). Part 1 waits on wl-xcon's `event_code` (its branch
  `xc321-event-code`).
- **Found while proving, not this part's:** `tests/schema/test_guardrails.py`'s blob probe leaves a session
  with no sync box log in the shared test database. Run before `tests/schema/test_daemon.py`, it fails three
  of that module's clean-pass tests, on the base commit too (checked at `54d585b`); the suite's own order
  runs the daemon's tests first.

## 4. The rulings

The spec's amendments 3-7.

## 5. What execution measured

Executed on {{DATE}}: Task 1 `{{SHA1}}`, Task 2 `{{SHA2}}`.
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

Expected, each exiting 0: 3.11, **2418 passed, 27 skipped, 1 deselected, 1 xfailed**; 3.13, **2418 passed, 28 skipped, 1 xfailed**. About 30 minutes. That is
14 more than `main`'s 2404: this part's new tests. CI, without the Andersson data, should show
about 2409 passed and 37 skipped on each.

- [ ] **Step 5: Fill the handoff's §5** with this execution's own figures: `{{DATE}}` the day, `{{SHA1}}`
  and `{{SHA2}}` Tasks 1-2's commits (`git log --oneline -3`), and `{{S311}}` and `{{S313}}` Step 4's two
  summary lines. Then `grep -c '{{' docs/handoffs/2026-10-18-clock-choice.md`.

Expected: `0`.

- [ ] **Step 6: Commit.**

```bash
git add -A docs wl.yaml
git commit -F - <<'MSG'
docs: part 2's records -- spec amendments 3-7, the handoff, and the checkpoint's and wl.yaml's lines: built, not merged; next, the review

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```
