# Subject Corrections (Piece 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** when a request brings an animal's corrected species, sex or date of birth, the daemon's next pass corrects every `written` file of that animal where it is, without a replacement and with the lab's annotations kept, noting the change in the file and its description.

**Architecture:**
- **One new module, `wl_preproc/nwb/correct.py`,** holds the whole feature: finding stale files by value (`stale_files`), patching a file's subject (`patch_subject`), correcting one file (`correct`), and the daemon's stage (`run_corrections`).
- **A copy is patched, checked and swapped in.** The live file is never written into: a crash leaves it intact, and the lab's annotations travel inside the copy.
- **The records follow in one transaction:** the stored description, the subject's checksums, the size, and a new `NwbChange` kind, `corrected`, with the placement again for a published file. `GET /nwb` then lists the file again.
- **Two small changes outside it:** `publish.record_change` splits into `insert_change`, so the correction's change lands in its own transaction; and the placement sweep dates a move by its own change, not by a correction recorded since.

**Tech Stack:** Python ≥3.11; DataJoint 2.3 with MySQL; h5py; pynwb; nwbinspector; the daemon's NWB stages.

**Spec:** `docs/superpowers/specs/2026-10-05-subject-corrections-design.md` (`00653a0`). It is binding. The requester approved it section by section on 2026-10-05, then the written spec (*"Approve, write the plan"*). Task 5 adds its dated amendments 1–5, which record the rulings below.

**Every piece of code below was proven before this plan was written.** It was built on a scratch branch, `proof/subject-corrections`, one commit per task, from `spec/subject-corrections` at `00653a0`.
- **Each task's failing run** was measured on the previous task's code plus this task's tests.
- **Its passing runs** were measured on its own commit.
- **Every mutation check named here** was run against the final tree, and each failed its test.
- **The full suite** was run on both interpreters with every task applied (Task 5 quotes it).

## Global Constraints

- **The spec is binding,** including the dated amendments Task 5 adds. Where it and this plan disagree, the spec wins; record a ruling.
- **The requester's decisions (spec §0):**
  - corrections are automatic: every `written` file of the animal, published or not, canonical or derivative, current or superseded;
  - the file and its description each keep a note of the change, old to new, with the date;
  - a copy is patched, checked and swapped in; the live file is never written into.
- **Only the subject's datasets change:** `species`, `sex`, `date_of_birth` and the subject's `description` under `/general/subject`. Every other written-once dataset must still match its recorded checksum.
- **A failure is reported per file and never stops the pass** or another file; the file keeps the details it has.
- **Probe areas are out** (the requester's decision of 2026-09-30, probes spec §0 decision 1).
- **element-event's and element-animal's tables are adopted, not changed:** the subject's details are read through `nwb/gather.py::_subject`.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code, data or tables.
- **Leave the stray symlink `wl-preproc` at the repository root alone,** and keep it out of commits.
- **Tests.**
  - Run with `.venv/bin/python -m pytest <files> -q -p no:cacheprovider` from the repository root. In a git worktree, prefix `PYTHONPATH=$PWD`.
  - Database tests need Docker (OrbStack on this machine: `open -a OrbStack` after a reboot). A module-wide setup error on a 120 s container timeout is the known flake: re-run it.
  - `tests/schema/test_nwb_correct.py`'s fixture builds a synthetic session and publishes it: about 35 s before its first test.
  - Mutation checks:
    - clear `__pycache__` first, and again after restoring;
    - run with `PYTHONDONTWRITEBYTECODE=1`;
    - make one mutation at a time, and restore the file afterwards.
  - **Run each task's own test files.** The full suite runs once, on both interpreters, in Task 5.
  - The shell is zsh:
    - an unquoted `$VAR` holding several arguments is not split;
    - a pipe through `tail` or `grep` hides the test command's exit status, so gate on the command's own status.
- **`wl-check` after `wl.yaml` changes,** on its own (Task 5).
- **Every commit message ends with** the trailer lines the harness gives the executing session.
- **Date what Task 5 records** (the checkpoint's and `wl.yaml`'s BUILT lines, the handoff) with the day the plan is executed; this plan proved them on 2026-10-05.

## Review Focus

Five inputs the spec implies but its own testing section (§8) does not name, most likely first. Each is pinned by a test named here, in the task that pins it.

1. **A lab member appends to the file while it is being corrected.** The copy is dropped, nothing is recorded, and the next pass tries again; the live file keeps what they wrote. Task 3: `test_a_file_written_to_during_the_copy_is_left_and_tried_again`.
2. **The same correction made twice on one day** (A to B, back, then A to B again): each is noted, because the description follows the file's correction lines by count, not by text. Task 3: `test_a_file_already_holding_the_details_gets_only_its_records`.
3. **A crash after the swap, before the records:** the next pass finds the file corrected, records it, and writes no second note. Task 3: the same test.
4. **A copy a move left behind, written to before a correction:** the placement sweep must still keep it, because a correction records the placement again, later. Task 4: `test_a_leftover_written_after_its_move_is_kept_though_the_file_was_corrected_since`.
5. **A file on the fast share with no room for the copy:** the correction waits, reported, rather than filling the share. Task 4: the same test.

## The spec's §7, verified at `00653a0`

Answered in code while proving, and recorded by Task 5 as amendment 1:
1. **h5py rewrites the subject's text datasets** of a file pynwb wrote, and pynwb reads the file afterwards.
2. **The patch changes exactly the datasets whose values differ,** and the subject's description.
3. **`nwbinspector` does not rate a date of birth after the session's start as critical.**
4. **`publish.current_placement` is the latest placement,** so the one recorded with `corrected` becomes current.
5. **`publish.write_description` writes atomically,** by way of `.partial` and `os.replace`.
6. **The swap is `os.replace`,** as publishing's and placement's renames into place are.

## The rulings this plan carries

Each is a dated amendment Task 5 adds to the spec.
- **1:** §7's items, answered as above.
- **2 (Task 4):** the placement sweep dates a move by its `published` or `moved` change, not by the current placement, which a correction records again, later.
- **3 (Task 3):** the description's notes follow the file's correction lines by count, so the same correction twice is noted twice and a pass after a crash records the crashed pass's note once.
- **4 (Task 4):** the daemon reports `nwb_corrected` (`None` with neither the builder's root nor a share, `0` when another wlpp process holds the NWB lock); failures are prefixed `NwbCorrection`; a file not yet published in a freed session waits, as publishing does.
- **5 (Task 3):** the size is recorded with each correction, the placement's for a published file and `NwbFile.n_bytes` for one in scratch, though a correction did not change it in the file measured.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/nwb/correct.py` (new) | finding stale files (Task 1), patching a subject (Task 2), correcting one file (Task 3), the daemon's stage (Task 4) |
| `wl_preproc/schema/nwb.py` | `NwbChange.kind` gains `corrected` (Task 1) |
| `wl_preproc/nwb/gather.py`, `nwb/build.py` | `described_subject`, shared by `resolved_invalid` and `stale_files` (Task 1) |
| `wl_preproc/nwb/publish.py` | `insert_change` (Task 3); the sweep's dating (Task 4) |
| `wl_preproc/daemon.py`, `cli/main.py` | the stage, its report and its printed line (Task 4) |
| `tests/schema/test_nwb_correct.py` (new), `tests/nwb/test_correct.py` (new) | the feature's tests |
| docs, `wl.yaml` | the protocol, what wl.works is told, the amendments, the records (Task 5) |

---

### Task 1: A written file is stale when its subject's details change

**Files:**
- Create: `wl_preproc/nwb/correct.py`, `tests/schema/test_nwb_correct.py`
- Modify: `wl_preproc/schema/nwb.py`, `wl_preproc/nwb/gather.py`, `wl_preproc/nwb/build.py`

**Interfaces — produces:**
- `nwb_schema.NwbChange.kind`: `enum('built','published','moved','superseded','corrected')`.
- `gather.described_subject(subject_id: str) -> dict`: `species`, `sex`, and `date_of_birth` as ISO text or None, as a description states them. `build.resolved_invalid` now uses it.
- `correct.stale_files() -> list[dict]`: the activation key (`subject`, `session_datetime`, `montage_id`, `activation_id`) of every `written` file whose stored description's subject differs from `described_subject`.
- In `tests/schema/test_nwb_correct.py`: the fixtures `daemon_module`, `shares` (`{"slow": Share, "fast": Share}`, each with its `nwb/` folder) and `published` (yields `(session_key, activation_key, runs, nwb_root)` for a canonical built and published to the slow share), and the helpers `_set_birth(date)` and `_is_stale(key)`. Later tasks append to this module.

**Why the fixture deletes its records after the module.** A placement on this module's shares reads as a missing copy to a later module's publishing pass, which looks on its own shares (`publish.missing_copies`): run before `tests/schema/test_nwb_build.py`, this module failed three of its tests until it cleaned up.

- [ ] **Step 1: Write the failing tests.** Apply this diff:

```diff
--- /dev/null
+++ b/tests/schema/test_nwb_correct.py
@@ -0,0 +1,127 @@
+"""Subject corrections in built files, end to end on a synthetic session
+through the daemon (design spec `2026-10-05-subject-corrections-design.md`).
+Date, subject and seed checked unclaimed across `tests/` on 2026-10-05. In the
+PAST, as `test_nwb_build.py`'s are: `nwbinspector` calls a future
+`session_start_time` critical."""
+
+from __future__ import annotations
+
+import datetime
+
+import pytest
+
+_SESSION_DATETIME = datetime.datetime(2025, 7, 23, 9, 0)
+_SUBJECT = "nwbfix1"
+_BIRTH = datetime.date(2016, 3, 2)
+
+
+@pytest.fixture(scope="module")
+def daemon_module(dj_conn, prefix):
+    from wl_preproc import daemon
+    from wl_preproc.schema import detect
+
+    daemon.activate_all(prefix=prefix)
+    detect.register_default_paramsets()
+    return daemon
+
+
+@pytest.fixture(scope="module")
+def shares(tmp_path_factory):
+    """A slow and a fast share for the module, each with its `nwb/` folder
+    made once, as a person sets a share up."""
+    from wl_preproc.nwb.publish import NWB_DIR, Share
+
+    made = {}
+    for tier, name in (("slow", "hdd"), ("fast", "nvme")):
+        mount = tmp_path_factory.mktemp(f"nwbfix-{tier}")
+        (mount / NWB_DIR).mkdir()
+        made[tier] = Share(tier=tier, mount=mount, host="wl-nas", name=name)
+    return made
+
+
+@pytest.fixture(scope="module")
+def published(daemon_module, prefix, shares, tmp_path_factory):
+    """`stepped_session`'s construction, its trials in two runs, through the
+    daemon; then wl.works' canonical over its runs, built and published to
+    the slow share by the next pass. Yields `(session_key, activation_key,
+    runs, nwb_root)`. Its files' records are deleted after the module: a
+    placement on this module's shares would read as a missing copy to a later
+    module's publishing pass, which looks on its own shares."""
+    from tests.schema.test_detect_populate import TRIAL_DURATION_S, _build_stepped_session
+    from tests.schema.test_nwb_build import _montage, _request
+    from wl_preproc.contracts.events import TaskTypeCode
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import core
+
+    split = [{"task_type": TaskTypeCode.RF_MAP, "n_trials": n, "trial_duration_s": TRIAL_DURATION_S} for n in (3, 2)]
+    session_key, _segment, _onsets = _build_stepped_session(
+        tmp_path_factory, dirname="nwbfix", session_id="2025-07-23_01", subject=_SUBJECT,
+        session_datetime=_SESSION_DATETIME, seed=723, recipe_update={"runs": True, "blocks": split},
+    )
+    daemon_module.run_once(prefix=prefix)
+    runs = [{"run_number": row["run_number"], "start_s": row["run_start_time"], "end_s": row["run_stop_time"],
+             "works_run_id": f"wr-fix-{row['run_number']}"}
+            for row in (core.Run & session_key).to_dicts(order_by="run_number")]
+    key = accept(_request(session_key, "nwbfix1-canonical", _montage(runs), runs), prefix=prefix)
+    nwb_root = tmp_path_factory.mktemp("nwbfix-root")
+    report = daemon_module.run_once(prefix=prefix, nwb_root=nwb_root, nwb_slow=shares["slow"],
+                                    nwb_fast=shares["fast"])
+    assert report["nwb_published"] >= 1, report["errors"]
+    yield session_key, key, runs, nwb_root
+    from wl_preproc.schema import nwb as nwb_schema
+
+    (nwb_schema.NwbFile & {"subject": _SUBJECT}).delete(prompt=False)
+
+
+def _set_birth(date_of_birth):
+    from wl_preproc.schema import pipeline
+
+    pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": date_of_birth})
+
+
+def _is_stale(key) -> bool:
+    from wl_preproc.nwb.correct import stale_files
+    from wl_preproc.nwb.publish import activation_tuple
+
+    return activation_tuple(key) in {activation_tuple(stale) for stale in stale_files()}
+
+
+def test_corrected_is_a_kind_of_change(dj_conn, prefix):
+    from wl_preproc.schema import nwb as nwb_schema
+
+    nwb_schema.activate(prefix=prefix)
+    assert "'corrected'" in nwb_schema.NwbChange.heading.attributes["kind"].type
+
+
+def test_a_written_file_is_stale_once_its_subjects_details_change(published):
+    """Spec section 2: by value, as `invalid` files are found."""
+    _session_key, key, _runs, _root = published
+    assert not _is_stale(key)
+    _set_birth(datetime.date(2016, 3, 1))
+    try:
+        assert _is_stale(key)
+    finally:
+        _set_birth(_BIRTH)
+    assert not _is_stale(key)
+
+
+def test_a_derivative_and_a_superseded_file_are_stale_too(published, daemon_module, prefix, shares):
+    """Spec section 2: every written file of the animal, whatever its role
+    or whether a replacement superseded it."""
+    from tests.schema.test_nwb_build import _montage, _request
+    from wl_preproc.responder.jobs import accept
+
+    session_key, key, runs, root = published
+    derivative = accept(_request(session_key, "nwbfix1-derivative", _montage(runs), runs,
+                                 run_numbers=[runs[-1]["run_number"]]), prefix=prefix)
+    replacement = _request(session_key, "nwbfix1-replacement", _montage(runs), runs)
+    replacement = replacement.model_copy(update={"selection": {
+        **replacement.selection, "role": "canonical", "supersedes_activation_id": key["activation_id"]}})
+    successor = accept(replacement, prefix=prefix)
+    report = daemon_module.run_once(prefix=prefix, nwb_root=root, nwb_slow=shares["slow"], nwb_fast=shares["fast"])
+    assert not [error for error in report["errors"] if "NwbFile" in error], report["errors"]
+    _set_birth(datetime.date(2016, 3, 1))
+    try:
+        assert all(_is_stale(each) for each in (key, derivative, successor))
+    finally:
+        _set_birth(_BIRTH)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_nwb_correct.py -q --tb=line -p no:cacheprovider`
Expected: 3 failed. `NwbChange.kind` has no `corrected`, and `wl_preproc.nwb.correct` does not exist (the two stale-file tests).

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/schema/nwb.py
+++ b/wl_preproc/schema/nwb.py
@@ -82,7 +82,10 @@ class NwbChange(dj.Manual):
     change_seq : int unsigned auto_increment
     ---
     -> NwbFile
-    kind : enum('built','published','moved','superseded')
+    # `corrected`: the subject's details rewritten in place (design spec
+    # `2026-10-05-subject-corrections-design.md`); a development database
+    # alters this enum.
+    kind : enum('built','published','moved','superseded','corrected')
     changed_at : datetime(6)
     """
 
```

```diff
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -190,6 +190,16 @@ def _subject(subject_id: str) -> dict:
     }
 
 
+def described_subject(subject_id: str) -> dict:
+    """The subject's current details as a file's description states them --
+    `species`, `sex`, and `date_of_birth` as ISO text or None -- for comparing
+    with what a file was built with (`build.resolved_invalid`,
+    `correct.stale_files`)."""
+    current = _subject(subject_id)
+    return {"species": current["species"], "sex": current["sex"],
+            "date_of_birth": None if current["date_of_birth"] is None else current["date_of_birth"].isoformat()}
+
+
 def _coverage(table, key_field: str, session_key: dict) -> dict:
     by_item: dict = {}
     for row in (table & session_key).to_dicts():
```

```diff
--- a/wl_preproc/nwb/build.py
+++ b/wl_preproc/nwb/build.py
@@ -168,7 +168,7 @@ def resolved_invalid() -> list[dict]:
     `responder/jobs.py::_record_subject_details` keeps no timestamp. A
     rebuild that is still invalid now matches, so it is not rebuilt again
     until the details change once more."""
-    from wl_preproc.nwb.gather import _subject
+    from wl_preproc.nwb.gather import described_subject
     from wl_preproc.schema import nwb as nwb_schema
 
     resolved, subjects = [], {}
@@ -177,10 +177,8 @@ def resolved_invalid() -> list[dict]:
         # review's M5): every real file is invalid until wl.works sends
         # subject details.
         if row["subject"] not in subjects:
-            subjects[row["subject"]] = _subject(row["subject"])
-        current = subjects[row["subject"]]
-        now = {"species": current["species"], "sex": current["sex"],
-               "date_of_birth": None if current["date_of_birth"] is None else current["date_of_birth"].isoformat()}
+            subjects[row["subject"]] = described_subject(row["subject"])
+        now = subjects[row["subject"]]
         built_with = (row["description"] or {}).get("subject") or {}
         if {field: built_with.get(field) for field in now} != now:
             resolved.append({k: row[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")})
```

```diff
--- /dev/null
+++ b/wl_preproc/nwb/correct.py
@@ -0,0 +1,31 @@
+"""Subject corrections in built files (design spec
+`2026-10-05-subject-corrections-design.md`).
+
+An animal's species, sex or date of birth corrected in wl.works reaches this
+host in the next request, which writes it into element-animal's `Subject` for
+the whole animal. Every `written` file of the animal built with other details
+is then corrected where it is: a copy is patched, checked and swapped in, so
+the live file is never written into and the lab's annotations travel inside
+the copy. Only the subject's datasets change, with a note of what did."""
+
+from __future__ import annotations
+
+
+def stale_files() -> list[dict]:
+    """Every `written` activation whose stored description names subject
+    details other than the subject's current ones (spec section 2): by value,
+    as `build.resolved_invalid` finds `invalid` files, since subject details
+    carry no timestamp. Published or not, canonical or derivative, current or
+    superseded."""
+    from wl_preproc.nwb.gather import described_subject
+    from wl_preproc.schema import nwb as nwb_schema
+
+    stale, subjects = [], {}
+    for row in (nwb_schema.NwbFile & {"status": "written"}).proj("description").to_dicts():
+        if row["subject"] not in subjects:
+            subjects[row["subject"]] = described_subject(row["subject"])
+        now = subjects[row["subject"]]
+        built_with = (row["description"] or {}).get("subject") or {}
+        if {field: built_with.get(field) for field in now} != now:
+            stale.append({k: row[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")})
+    return stale
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 3 passed.

Then what shares `described_subject` and the guardrails: `.venv/bin/python -m pytest tests/schema/test_nwb_correct.py tests/schema/test_nwb_build.py tests/schema/test_guardrails.py tests/test_cli_guardrails.py -q -p no:cacheprovider`. Expected: 150 passed.

- [ ] **Step 5: Mutation checks.** Each was measured, against the final tree, to fail the test in brackets.
  - T1a (`nwb/correct.py::stale_files`): `if {field: built_with.get(field) for field in now} != now:` becomes `if False:` [`test_a_written_file_is_stale_once_its_subjects_details_change`].
  - T1b (`nwb/gather.py::described_subject`): the date of birth's `.isoformat()` is dropped, so it is no longer compared as the description states it [the same test].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/nwb.py wl_preproc/nwb/gather.py wl_preproc/nwb/build.py wl_preproc/nwb/correct.py tests/schema/test_nwb_correct.py
git commit -m "feat(nwb): a written file is stale when its subject's details change -- NwbChange gains the corrected kind, and correct.stale_files finds every written file of the animal by value

<trailer lines>"
```

---

### Task 2: A file's subject is patched in place, with a note

**Files:**
- Modify: `wl_preproc/nwb/correct.py`
- Create: `tests/nwb/test_correct.py`

**Interfaces — consumes:** Task 1's `correct.py`.

**Interfaces — produces:**
- `correct.SUBJECT = "/general/subject"`.
- `correct.correction_note(old: dict, new: dict, day: datetime.date) -> str | None`: *"Corrected 2026-11-02: date of birth 2016-03-02 → 2016-03-01."*, naming species, sex and date of birth in that order where they differ, `unknown` for None; None when nothing differs.
- `correct.file_subject(path: Path) -> dict`: `species`, `sex`, and `date_of_birth` as a `datetime.date` or None, read from the file itself.
- `correct.patch_subject(path: Path, new: dict, day: datetime.date) -> str | None`: sets the three datasets (creating one the file lacks with pynwb's string type, removing one now unknown) and appends the note to the subject's description; returns the note, or None without opening the file for writing.

**These tests need no database:** they write small files with pynwb in `tmp_path`.

- [ ] **Step 1: Write the failing tests.** Apply this diff:

```diff
--- /dev/null
+++ b/tests/nwb/test_correct.py
@@ -0,0 +1,103 @@
+"""Patching a file's subject (design spec
+`2026-10-05-subject-corrections-design.md` section 3, step 2), on small files
+written here: no database."""
+
+from __future__ import annotations
+
+import datetime
+
+import h5py
+import pytest
+
+_DAY = datetime.date(2026, 11, 2)
+_STATED = "As wl.works' job request stated it (design spec section 9)."
+
+
+def _file(path, *, species="Macaca mulatta", sex="F", date_of_birth=datetime.date(2016, 3, 2)):
+    """A file with a subject, as `nwb/session.py` writes one, and an
+    annotation the lab appended afterwards."""
+    from pynwb import NWBFile, NWBHDF5IO
+    from pynwb.file import Subject
+
+    start = datetime.datetime(2025, 7, 20, 9, tzinfo=datetime.timezone.utc)
+    born = datetime.datetime.combine(date_of_birth, datetime.time(), tzinfo=datetime.timezone.utc)
+    nwb = NWBFile(session_description="x", identifier="monk01.2025-07-20_01.montage-0.activation-0",
+                  session_start_time=start,
+                  subject=Subject(subject_id="monk01", species=species, sex=sex, date_of_birth=born,
+                                  description=_STATED))
+    with NWBHDF5IO(str(path), "w") as handle:
+        handle.write(nwb)
+    with h5py.File(path, "r+") as handle:
+        handle.require_group("analysis").create_group("lab_note").create_dataset("text", data="seen by JW")
+    return path
+
+
+def _details(species="Macaca mulatta", sex="F", date_of_birth=datetime.date(2016, 3, 2)):
+    return {"species": species, "sex": sex, "date_of_birth": date_of_birth}
+
+
+def test_the_note_names_each_detail_that_changed():
+    from wl_preproc.nwb.correct import correction_note
+
+    assert correction_note(_details(), _details(), _DAY) is None
+    assert correction_note(_details(), _details(date_of_birth=datetime.date(2016, 3, 1)), _DAY) == (
+        "Corrected 2026-11-02: date of birth 2016-03-02 → 2016-03-01.")
+    assert correction_note(_details(species=None, sex="U"), _details(sex="M"), _DAY) == (
+        "Corrected 2026-11-02: species unknown → Macaca mulatta; sex U → M.")
+
+
+def test_the_patch_changes_only_the_subjects_datasets_and_pynwb_reads_them(tmp_path):
+    from pynwb import NWBHDF5IO
+
+    from wl_preproc.nwb.checksums import dataset_checksums
+    from wl_preproc.nwb.correct import file_subject, patch_subject
+
+    path = _file(tmp_path / "a.nwb")
+    before = {row["dataset_path"]: row["sha256"] for row in dataset_checksums(path)}
+    note = patch_subject(path, _details(sex="M", date_of_birth=datetime.date(2016, 3, 1)), _DAY)
+    assert note == "Corrected 2026-11-02: sex F → M; date of birth 2016-03-02 → 2016-03-01."
+    after = {row["dataset_path"]: row["sha256"] for row in dataset_checksums(path)}
+    assert sorted(name for name in before if before[name] != after[name]) == [
+        "/general/subject/date_of_birth", "/general/subject/description", "/general/subject/sex"]
+    assert file_subject(path) == _details(sex="M", date_of_birth=datetime.date(2016, 3, 1))
+    with NWBHDF5IO(str(path), "r") as handle:
+        subject = handle.read().subject
+        assert (subject.sex, subject.date_of_birth.date()) == ("M", datetime.date(2016, 3, 1))
+        assert subject.description == f"{_STATED}\n{note}"
+    with h5py.File(path, "r") as handle:
+        assert handle["analysis/lab_note/text"][()] == b"seen by JW"
+
+
+def test_a_second_correction_adds_a_second_line(tmp_path):
+    from wl_preproc.nwb.correct import patch_subject
+
+    path = _file(tmp_path / "a.nwb")
+    first = patch_subject(path, _details(date_of_birth=datetime.date(2016, 3, 1)), _DAY)
+    second = patch_subject(path, _details(date_of_birth=datetime.date(2016, 2, 29)), datetime.date(2026, 12, 1))
+    with h5py.File(path, "r") as handle:
+        assert handle["general/subject/description"][()].decode() == f"{_STATED}\n{first}\n{second}"
+    assert second == "Corrected 2026-12-01: date of birth 2016-03-01 → 2016-02-29."
+
+
+def test_a_file_that_already_holds_the_details_is_not_opened_for_writing(tmp_path):
+    """What a pass finds after a crash that followed the swap (spec
+    section 5): nothing to patch, and no empty note."""
+    from wl_preproc.nwb.correct import patch_subject
+
+    path = _file(tmp_path / "a.nwb")
+    stamp = path.stat().st_mtime_ns
+    assert patch_subject(path, _details(), _DAY) is None
+    assert path.stat().st_mtime_ns == stamp
+
+
+@pytest.mark.parametrize("before, after", [(None, "Macaca mulatta"), ("Macaca mulatta", None)])
+def test_a_species_is_written_or_removed_where_the_file_had_none_or_one(tmp_path, before, after):
+    from pynwb import NWBHDF5IO
+
+    from wl_preproc.nwb.correct import file_subject, patch_subject
+
+    path = _file(tmp_path / "a.nwb", species=before)
+    patch_subject(path, _details(species=after), _DAY)
+    assert file_subject(path)["species"] == after
+    with NWBHDF5IO(str(path), "r") as handle:
+        assert handle.read().subject.species == after
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/nwb/test_correct.py -q --tb=line -p no:cacheprovider`
Expected: 6 failed. `correction_note`, `file_subject` and `patch_subject` do not exist.

- [ ] **Step 3: Implement.** Apply this diff:

```diff
--- a/wl_preproc/nwb/correct.py
+++ b/wl_preproc/nwb/correct.py
@@ -10,6 +10,17 @@ the copy. Only the subject's datasets change, with a note of what did."""
 
 from __future__ import annotations
 
+import datetime
+from pathlib import Path
+
+import h5py
+
+SUBJECT = "/general/subject"
+# The details a correction may change, in the order its note names them, with
+# the words it uses. The subject's description carries the note.
+_DETAILS = (("species", "species"), ("sex", "sex"), ("date_of_birth", "date of birth"))
+_ENCODING = {"species": "utf-8", "sex": "utf-8", "date_of_birth": "ascii", "description": "utf-8"}
+
 
 def stale_files() -> list[dict]:
     """Every `written` activation whose stored description names subject
@@ -29,3 +40,65 @@ def stale_files() -> list[dict]:
         if {field: built_with.get(field) for field in now} != now:
             stale.append({k: row[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")})
     return stale
+
+
+def _text(value) -> str:
+    return "unknown" if value is None else value.isoformat() if isinstance(value, datetime.date) else str(value)
+
+
+def correction_note(old: dict, new: dict, day: datetime.date) -> str | None:
+    """The line a correction adds, naming each detail that changed, old to
+    new, with the UTC date of the correction and `unknown` for a missing
+    value: *"Corrected 2026-11-02: date of birth 2016-03-02 → 2016-03-01."*
+    None when nothing differs."""
+    changes = [f"{words} {_text(old[field])} → {_text(new[field])}" for field, words in _DETAILS
+               if old[field] != new[field]]
+    return f"Corrected {day.isoformat()}: {'; '.join(changes)}." if changes else None
+
+
+def file_subject(path: Path) -> dict:
+    """`species`, `sex` and `date_of_birth` (a date, or None) as the file
+    holds them. The old values a note names are read here, never from the
+    records, so a pass after a crash that followed the swap finds nothing
+    to change (spec section 5)."""
+    with h5py.File(path, "r") as handle:
+        subject = handle[SUBJECT]
+
+        def read(name: str):
+            return subject[name][()].decode() if name in subject else None
+
+        born = read("date_of_birth")
+        return {"species": read("species"), "sex": read("sex"),
+                "date_of_birth": None if born is None else datetime.datetime.fromisoformat(born).date()}
+
+
+def _write(subject: h5py.Group, name: str, value: str | None) -> None:
+    """One text dataset of the subject: set, written where the file had
+    none, or removed for a value now unknown."""
+    if value is None:
+        if name in subject:
+            del subject[name]
+    elif name in subject:
+        subject[name][()] = value
+    else:
+        subject.create_dataset(name, data=value, dtype=h5py.string_dtype(_ENCODING[name]))
+
+
+def patch_subject(path: Path, new: dict, day: datetime.date) -> str | None:
+    """Set the file's subject details to `new` and append the note to the
+    subject's description (spec section 3, step 2). Only those datasets
+    change; earlier notes stay. Returns the note, or None, without opening
+    the file for writing, when it already holds `new`."""
+    note = correction_note(file_subject(path), new, day)
+    if note is None:
+        return None
+    born = new["date_of_birth"]
+    with h5py.File(path, "r+") as handle:
+        subject = handle[SUBJECT]
+        _write(subject, "species", new["species"])
+        _write(subject, "sex", new["sex"])
+        _write(subject, "date_of_birth", None if born is None else datetime.datetime.combine(
+            born, datetime.time(), tzinfo=datetime.timezone.utc).isoformat())
+        stated = subject["description"][()].decode() if "description" in subject else ""
+        _write(subject, "description", f"{stated}\n{note}" if stated else note)
+    return note
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 6 passed.

Then the NWB writers' unit tests: `.venv/bin/python -m pytest tests/nwb -q -p no:cacheprovider`. Expected: 59 passed, 1 skipped.

- [ ] **Step 5: Mutation checks.**
  - T2a (`correction_note`): `if old[field] != new[field]]` becomes `if True]` [`test_the_note_names_each_detail_that_changed`].
  - T2b (`patch_subject`): the early `return None` for a file already holding the details is removed [`test_a_file_that_already_holds_the_details_is_not_opened_for_writing`].
  - T2c (`_write`): `del subject[name]` becomes `pass` [`test_a_species_is_written_or_removed_where_the_file_had_none_or_one`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/nwb/correct.py tests/nwb/test_correct.py
git commit -m "feat(nwb): a file's subject is patched in place with a note -- correct.patch_subject, file_subject and correction_note

Only the subject's datasets change, the lab's annotations untouched, and the
old values are read from the file itself.

<trailer lines>"
```

---

### Task 3: One stale file is corrected where it is

**Files:**
- Modify: `wl_preproc/nwb/correct.py`, `wl_preproc/nwb/publish.py`, `tests/schema/test_nwb_correct.py`

**Interfaces — consumes:** Task 1's `stale_files` and fixtures; Task 2's `patch_subject` and `file_subject`.

**Interfaces — produces:**
- `correct.correct(key: dict, shares: dict, day: datetime.date) -> str | None`, `shares` mapping a tier to its `publish.Share`: copy, patch, check, swap, record, and rewrite the description beside a published file. Returns the note, or None when the file already held the details and only its records were brought up to them.
- `correct.CorrectionRefused` and `correct.ChangedWhileCorrecting`; it also raises `publish.ChangedData`.
- `publish.insert_change(key: dict, kind: str, placement: dict | None = None) -> int`, inside the caller's transaction; `record_change` wraps it in one.

**Found while proving (spec amendment 3).** The first draft appended each correction line of the file to the description's notes only if that text was not there yet. The same correction made twice on one day (A to B, back, then A to B) was then never noted the second time. The notes now follow the file's correction lines by count.

- [ ] **Step 1: Write the failing tests.** Apply this diff:

```diff
--- a/tests/schema/test_nwb_correct.py
+++ b/tests/schema/test_nwb_correct.py
@@ -125,3 +125,246 @@ def test_a_derivative_and_a_superseded_file_are_stale_too(published, daemon_modu
         assert all(_is_stale(each) for each in (key, derivative, successor))
     finally:
         _set_birth(_BIRTH)
+
+
+# -- Correcting one file (spec section 3), and what stops it (section 5). Each
+# test leaves the subject's details as the fixture stated them and its file
+# holding them again, so the next one starts where a real pass would.
+
+_DAY = datetime.date(2026, 11, 2)
+_ANNOTATION = "analysis/nwbfix_note/text"
+
+
+def _live(key, shares):
+    from wl_preproc.nwb.publish import current_placement
+
+    placement = current_placement(key)
+    return shares[placement["tier"]].local(placement["path"])
+
+
+def _annotate(path):
+    import h5py
+
+    with h5py.File(path, "r+") as handle:
+        if _ANNOTATION not in handle:
+            handle.create_dataset(_ANNOTATION, data="seen by JW")
+
+
+def _restore(key, shares):
+    """The fixture's details back, and the file corrected back to them."""
+    from wl_preproc.nwb.correct import correct
+
+    _set_birth(_BIRTH)
+    correct(key, shares, _DAY)
+
+
+def test_a_published_file_is_corrected_where_it_is_with_its_annotations(published, shares, prefix):
+    """Spec sections 3 and 4: only the subject's datasets change, the lab's
+    annotation travels in the copy, and the records, the description beside
+    the file and GET /nwb all say what the file now holds."""
+    import json
+
+    import h5py
+
+    from wl_preproc.nwb.correct import correct, file_subject, stale_files
+    from wl_preproc.nwb.publish import current_placement, description_path, mismatches
+    from wl_preproc.responder.nwb import list_files
+    from wl_preproc.schema import nwb as nwb_schema
+
+    session_key, key, _runs, _root = published
+    live = _live(key, shares)
+    _annotate(live)
+    cursor = list_files(None, prefix=prefix)["cursor"]
+    changes = len(nwb_schema.NwbChange & key & {"kind": "corrected"})
+    _set_birth(datetime.date(2016, 3, 1))
+    try:
+        note = correct(key, shares, _DAY)
+        assert note == "Corrected 2026-11-02: date of birth 2016-03-02 → 2016-03-01."
+        assert file_subject(live)["date_of_birth"] == datetime.date(2016, 3, 1)
+        with h5py.File(live, "r") as handle:
+            assert handle[_ANNOTATION][()] == b"seen by JW"
+            assert handle["general/subject/description"][()].decode().endswith("\n" + note)
+        assert not live.with_name(live.name + ".partial").exists()
+        row = (nwb_schema.NwbFile & key).fetch1()
+        assert row["description"]["subject"]["date_of_birth"] == "2016-03-01"
+        assert row["description"]["subject"]["age_days"] == (_SESSION_DATETIME.date() - datetime.date(2016, 3, 1)).days
+        assert row["description"]["notes"][-1] == note
+        recorded = (nwb_schema.NwbFile.Dataset & key).to_dicts()
+        assert mismatches(live, recorded) == []
+        assert {d["dataset_path"]: d["sha256"] for d in row["description"]["checksums"]["datasets"]} == {
+            d["dataset_path"]: d["sha256"] for d in recorded}
+        assert json.loads(description_path(live).read_text()) == row["description"]
+        assert len(nwb_schema.NwbChange & key & {"kind": "corrected"}) == changes + 1
+        # The placement recorded again with the correction: same share and
+        # path, the size as it now is (spec section 3, step 5).
+        assert current_placement(key)["kind"] == "corrected"
+        assert current_placement(key)["n_bytes"] == live.stat().st_size
+        (listed,) = [item for item in list_files(cursor, prefix=prefix)["files"]
+                     if item["identifier"] == row["nwb_identifier"]]
+        assert listed["description"]["subject"]["date_of_birth"] == "2016-03-01"
+        assert key not in stale_files()
+    finally:
+        _restore(key, shares)
+    assert file_subject(live)["date_of_birth"] == _BIRTH
+
+
+def test_a_file_not_yet_published_is_corrected_in_scratch(published, daemon_module, prefix, tmp_path_factory):
+    """Spec section 2: a written file waiting to publish is corrected where
+    it is, and its recorded size follows."""
+    from tests.schema.test_nwb_build import _montage, _request
+    from wl_preproc.nwb.correct import correct, file_subject
+    from wl_preproc.nwb.publish import current_placement
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import nwb as nwb_schema
+
+    session_key, _key, runs, root = published
+    waiting = accept(_request(session_key, "nwbfix1-waiting", _montage(runs), runs,
+                              run_numbers=[runs[0]["run_number"]]), prefix=prefix)
+    daemon_module.run_once(prefix=prefix, nwb_root=root)
+    path = __import__("pathlib").Path((nwb_schema.NwbFile & waiting).fetch1("path"))
+    assert current_placement(waiting) is None and path.exists()
+    _set_birth(datetime.date(2016, 3, 1))
+    try:
+        assert correct(waiting, {}, _DAY) is not None
+        assert file_subject(path)["date_of_birth"] == datetime.date(2016, 3, 1)
+        assert (nwb_schema.NwbFile & waiting).fetch1("n_bytes") == path.stat().st_size
+        assert current_placement(waiting) is None
+    finally:
+        _restore(waiting, {})
+
+
+def test_a_file_already_holding_the_details_gets_only_its_records(published, shares):
+    """Spec section 5: a pass after a crash that followed the swap finds the
+    file corrected and its records not; it records them, and does not write
+    the file again. Here the same correction is made twice on one day -- A to
+    B, back, then A to B once more by the pass that crashed -- so the crashed
+    pass's note has the very text of one already recorded: it is recorded
+    again, because the description follows the file's correction lines by
+    count, not by text (spec amendment 3)."""
+    import h5py
+
+    from wl_preproc.nwb.correct import correct, patch_subject
+    from wl_preproc.schema import nwb as nwb_schema
+
+    _session_key, key, _runs, _root = published
+    live = _live(key, shares)
+    new = {"species": "Macaca mulatta", "sex": "F", "date_of_birth": datetime.date(2016, 3, 1)}
+    _set_birth(new["date_of_birth"])
+    try:
+        first = correct(key, shares, _DAY)
+        _set_birth(_BIRTH)
+        correct(key, shares, _DAY)
+        _set_birth(new["date_of_birth"])
+        again = patch_subject(live, new, _DAY)  # the pass that crashed after its swap
+        assert again == first
+        stamp = live.stat().st_mtime_ns
+        assert correct(key, shares, _DAY) is None
+        assert live.stat().st_mtime_ns == stamp
+        notes = (nwb_schema.NwbFile & key).fetch1("description")["notes"]
+        with h5py.File(live, "r") as handle:
+            in_file = [line for line in handle["general/subject/description"][()].decode().split("\n")
+                       if line.startswith("Corrected ")]
+        assert [line for line in notes if line.startswith("Corrected ")] == in_file
+        assert notes[-3:] == [first, in_file[-2], again] and notes.count(first) == in_file.count(first) >= 2
+    finally:
+        _restore(key, shares)
+
+
+def test_a_file_written_to_during_the_copy_is_left_and_tried_again(published, shares, monkeypatch):
+    """Spec section 5: someone wrote to the file while it was copied, so the
+    copy is dropped and nothing recorded."""
+    from wl_preproc.nwb import correct as correct_module
+    from wl_preproc.schema import nwb as nwb_schema
+
+    _session_key, key, _runs, _root = published
+    live = _live(key, shares)
+    real = correct_module.patch_subject
+
+    def written_meanwhile(path, new, day):
+        with open(live, "ab") as handle:  # what a writer does to the live file meanwhile
+            handle.write(b"\0")
+        return real(path, new, day)
+
+    before = (nwb_schema.NwbFile & key).fetch1("description")
+    monkeypatch.setattr(correct_module, "patch_subject", written_meanwhile)
+    _set_birth(datetime.date(2016, 3, 1))
+    try:
+        with pytest.raises(correct_module.ChangedWhileCorrecting):
+            correct_module.correct(key, shares, _DAY)
+        assert correct_module.file_subject(live)["date_of_birth"] == _BIRTH
+        assert (nwb_schema.NwbFile & key).fetch1("description") == before
+        assert not live.with_name(live.name + ".partial").exists()
+    finally:
+        monkeypatch.undo()
+        with open(live, "r+b") as handle:  # the byte appended above, removed
+            handle.truncate(live.stat().st_size - 1)
+        _set_birth(_BIRTH)
+
+
+def test_changed_written_once_data_stops_the_correction(published, shares):
+    """Spec section 5: data that must never change did; a person looks."""
+    import h5py
+
+    from wl_preproc.nwb.correct import correct, file_subject
+    from wl_preproc.nwb.publish import ChangedData
+
+    _session_key, key, _runs, _root = published
+    live = _live(key, shares)
+    with h5py.File(live, "r+") as handle:
+        original = handle["/intervals/trials/start_time"][0]
+        handle["/intervals/trials/start_time"][0] = original + 1.0
+    _set_birth(datetime.date(2016, 3, 1))
+    try:
+        with pytest.raises(ChangedData, match="/intervals/trials/start_time"):
+            correct(key, shares, _DAY)
+        assert file_subject(live)["date_of_birth"] == _BIRTH
+    finally:
+        with h5py.File(live, "r+") as handle:
+            handle["/intervals/trials/start_time"][0] = original
+        _set_birth(_BIRTH)
+
+
+def test_a_date_of_birth_now_unknown_is_not_written(published, shares):
+    """Spec section 5: a file without a date of birth fails validation, so
+    no copy is made and the file keeps the details it has."""
+    from wl_preproc.ingest.landing import SUBJECT_BIRTH_DATE_UNKNOWN
+    from wl_preproc.nwb.correct import CorrectionRefused, correct
+
+    _session_key, key, _runs, _root = published
+    live = _live(key, shares)
+    _set_birth(SUBJECT_BIRTH_DATE_UNKNOWN)
+    try:
+        with pytest.raises(CorrectionRefused, match="date of birth is now unknown"):
+            correct(key, shares, _DAY)
+        assert not live.with_name(live.name + ".partial").exists()
+    finally:
+        _set_birth(_BIRTH)
+
+
+def test_a_critical_finding_in_the_corrected_copy_keeps_the_old_file(published, shares, monkeypatch):
+    from wl_preproc.nwb import correct as correct_module
+
+    _session_key, key, _runs, _root = published
+    live = _live(key, shares)
+    monkeypatch.setattr(correct_module, "n_critical", lambda findings: 1)
+    _set_birth(datetime.date(2016, 3, 1))
+    try:
+        with pytest.raises(correct_module.CorrectionRefused, match="critical"):
+            correct_module.correct(key, shares, _DAY)
+        assert correct_module.file_subject(live)["date_of_birth"] == _BIRTH
+    finally:
+        _set_birth(_BIRTH)
+
+
+def test_an_unreachable_share_fails_the_file(published, tmp_path_factory):
+    from wl_preproc.nwb.correct import CorrectionRefused, correct
+    from wl_preproc.nwb.publish import Share
+
+    _session_key, key, _runs, _root = published
+    gone = Share(tier="slow", mount=tmp_path_factory.mktemp("nwbfix-gone") / "gone", host="wl-nas", name="hdd")
+    _set_birth(datetime.date(2016, 3, 1))
+    try:
+        with pytest.raises(CorrectionRefused, match="not reachable"):
+            correct(key, {"slow": gone}, _DAY)
+    finally:
+        _set_birth(_BIRTH)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_nwb_correct.py -q --tb=line -p no:cacheprovider`
Expected: 8 failed, 3 passed. `correct`, `CorrectionRefused` and `ChangedWhileCorrecting` do not exist, and `correct` has no `n_critical` to replace.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/nwb/publish.py
+++ b/wl_preproc/nwb/publish.py
@@ -176,22 +176,30 @@ def current_placement(key: dict) -> dict | None:
     return rows[-1] if rows else None
 
 
-def record_change(key: dict, kind: str, placement: dict | None = None) -> int:
-    """One `NwbChange`, and its `NwbPlacement` when it moved a file, in one
-    transaction. Returns the change's sequence number."""
+def insert_change(key: dict, kind: str, placement: dict | None = None) -> int:
+    """One `NwbChange`, and its `NwbPlacement` when it placed a file, inside
+    the caller's transaction (`record_change`, or a correction's records:
+    `correct.py`). Returns the change's sequence number."""
     import datajoint as dj
 
     from wl_preproc.schema import nwb as nwb_schema
 
-    connection = dj.conn()
-    with connection.transaction:
-        nwb_schema.NwbChange.insert1({**key_of(key), "kind": kind, "changed_at": _now()})
-        sequence = int(connection.query("SELECT LAST_INSERT_ID()").fetchone()[0])
-        if placement is not None:
-            nwb_schema.NwbPlacement.insert1({"change_seq": sequence, **placement})
+    nwb_schema.NwbChange.insert1({**key_of(key), "kind": kind, "changed_at": _now()})
+    sequence = int(dj.conn().query("SELECT LAST_INSERT_ID()").fetchone()[0])
+    if placement is not None:
+        nwb_schema.NwbPlacement.insert1({"change_seq": sequence, **placement})
     return sequence
 
 
+def record_change(key: dict, kind: str, placement: dict | None = None) -> int:
+    """One `NwbChange`, and its `NwbPlacement` when it moved a file, in one
+    transaction. Returns the change's sequence number."""
+    import datajoint as dj
+
+    with dj.conn().transaction:
+        return insert_change(key, kind, placement)
+
+
 def active_keys() -> set[tuple]:
     """The activations the latest `PUT /nwb/active` wants on the fast share."""
     from wl_preproc.schema import nwb as nwb_schema
```

```diff
--- a/wl_preproc/nwb/correct.py
+++ b/wl_preproc/nwb/correct.py
@@ -10,11 +10,16 @@ the copy. Only the subject's datasets change, with a note of what did."""
 
 from __future__ import annotations
 
+import copy
 import datetime
+import os
+import shutil
 from pathlib import Path
 
 import h5py
 
+from wl_preproc.nwb.validate import inspect_file, n_critical
+
 SUBJECT = "/general/subject"
 # The details a correction may change, in the order its note names them, with
 # the words it uses. The subject's description carries the note.
@@ -22,6 +27,18 @@ _DETAILS = (("species", "species"), ("sex", "sex"), ("date_of_birth", "date of b
 _ENCODING = {"species": "utf-8", "sex": "utf-8", "date_of_birth": "ascii", "description": "utf-8"}
 
 
+class CorrectionRefused(Exception):
+    """A correction this pass will not make (spec section 5): the share is
+    not there or is full, the subject's date of birth is now unknown, or the
+    corrected copy fails validation. The file keeps the details it has, and
+    each pass says so."""
+
+
+class ChangedWhileCorrecting(Exception):
+    """The live file changed while it was copied: someone is writing to it.
+    The copy is dropped and nothing recorded; the next pass tries again."""
+
+
 def stale_files() -> list[dict]:
     """Every `written` activation whose stored description names subject
     details other than the subject's current ones (spec section 2): by value,
@@ -102,3 +119,123 @@ def patch_subject(path: Path, new: dict, day: datetime.date) -> str | None:
         stated = subject["description"][()].decode() if "description" in subject else ""
         _write(subject, "description", f"{stated}\n{note}" if stated else note)
     return note
+
+
+def _key(row: dict) -> dict:
+    return {k: row[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")}
+
+
+def _live(key: dict, row: dict, shares: dict) -> tuple[Path, dict | None]:
+    """Where the file is now, and its placement: on its share once
+    published, in scratch until then."""
+    from wl_preproc.nwb.publish import current_placement
+
+    placement = current_placement(key)
+    if placement is None:
+        return Path(row["path"]), None
+    share = shares.get(placement["tier"])
+    if share is None:
+        raise CorrectionRefused(f"the {placement['tier']} share is not configured; not corrected")
+    if reason := share.unreachable():
+        raise CorrectionRefused(f"{reason}; not corrected")
+    live = share.local(placement["path"])
+    if placement["tier"] == "fast" and live.exists() and not share.has_room(live.stat().st_size):
+        raise CorrectionRefused("the fast share is at its headroom; not corrected")
+    return live, placement
+
+
+def _subject_paths(rows: list[dict]) -> tuple[list[dict], list[dict]]:
+    """`rows` split into the subject's datasets and the rest."""
+    inside = [row for row in rows if row["dataset_path"].startswith(SUBJECT + "/")]
+    return inside, [row for row in rows if not row["dataset_path"].startswith(SUBJECT + "/")]
+
+
+def correct(key: dict, shares: dict, day: datetime.date) -> str | None:
+    """Correct one stale file where it is (spec section 3): copy, patch,
+    check, swap, record, and rewrite the description beside it. `shares`
+    maps a tier to its `publish.Share`. Returns the note, or None when the
+    file already held the details and only its records were brought up to
+    them. Raises `CorrectionRefused`, `ChangedWhileCorrecting` or
+    `publish.ChangedData`, leaving the live file as it was."""
+    from wl_preproc.nwb.gather import _subject
+    from wl_preproc.nwb.publish import ChangedData, _stamp, mismatches
+    from wl_preproc.schema import nwb as nwb_schema
+
+    key = _key(key)
+    row = (nwb_schema.NwbFile & key).fetch1()
+    current = _subject(key["subject"])
+    current = {field: current[field] for field, _words in _DETAILS}
+    if current["date_of_birth"] is None:
+        raise CorrectionRefused("the subject's date of birth is now unknown, and a file without one fails "
+                                "validation; it keeps the details it has")
+    live, placement = _live(key, row, shares)
+    if not live.exists():
+        raise FileNotFoundError(f"{live}: the file is missing; not corrected")
+    checksums = (nwb_schema.NwbFile.Dataset & key).to_dicts()
+    _subjects, others = _subject_paths(checksums)
+    before = _stamp(live)
+    partial = live.with_name(live.name + ".partial")
+    try:
+        shutil.copyfile(live, partial)
+        note = patch_subject(partial, current, day)
+        changed = mismatches(partial, others)
+        if changed:
+            raise ChangedData(f"{live}: {len(changed)} written-once dataset(s) changed, first {changed[0]}; "
+                              "not corrected")
+        if note is not None and (critical := n_critical(inspect_file(partial))):
+            raise CorrectionRefused(f"{live}: the corrected copy has {critical} critical nwbinspector "
+                                    "finding(s); not corrected")
+        if _stamp(live) != before:
+            raise ChangedWhileCorrecting(f"{live}: changed while it was being corrected (someone is writing "
+                                         "to it); tried again next pass")
+        if note is not None:
+            with open(partial, "rb+") as handle:
+                os.fsync(handle.fileno())
+            os.replace(partial, live)
+    finally:
+        partial.unlink(missing_ok=True)
+    _record(key, row, live, placement, current)
+    return note
+
+
+def _record(key: dict, row: dict, live: Path, placement: dict | None, current: dict) -> None:
+    """The records brought up to the file (spec section 3, steps 5 and 6),
+    in one transaction: the description's subject and notes, the subject's
+    checksums, the size, and a `corrected` change -- with the placement
+    again, same share and path, for a published file. Then the description
+    beside a published file."""
+    import datajoint as dj
+
+    from wl_preproc.contracts.nwb_description import NwbDescription
+    from wl_preproc.nwb.checksums import dataset_checksums
+    from wl_preproc.nwb.describe import _age_days
+    from wl_preproc.nwb.publish import insert_change, write_description
+    from wl_preproc.schema import nwb as nwb_schema
+
+    born = current["date_of_birth"]
+    description = copy.deepcopy(row["description"])
+    description["subject"] = {"species": current["species"], "sex": current["sex"],
+                              "date_of_birth": born.isoformat(), "age_days": _age_days(born, key["session_datetime"])}
+    with h5py.File(live, "r") as handle:
+        stated = handle[SUBJECT]["description"][()].decode() if "description" in handle[SUBJECT] else ""
+    # The file's correction lines past those the description already has,
+    # counted rather than compared, so the same correction twice is noted
+    # twice; read from the file, so a pass after a crash that followed the
+    # swap records the note the crashed pass wrote.
+    corrections = [line for line in stated.split("\n") if line.startswith("Corrected ")]
+    recorded = sum(1 for note in description["notes"] if note.startswith("Corrected "))
+    description["notes"] = [*description["notes"], *corrections[recorded:]]
+    fresh, _rest = _subject_paths(dataset_checksums(live))
+    _old, kept = _subject_paths(description["checksums"]["datasets"])
+    description["checksums"]["datasets"] = sorted([*kept, *fresh], key=lambda item: item["dataset_path"])
+    description = NwbDescription.model_validate(description).model_dump(mode="json")
+    n_bytes = live.stat().st_size
+    with dj.conn().transaction:
+        nwb_schema.NwbFile.update1({**key, "description": description,
+                                    **({} if placement is not None else {"n_bytes": n_bytes})})
+        (nwb_schema.NwbFile.Dataset & key & f"dataset_path LIKE '{SUBJECT}/%'").delete_quick()
+        nwb_schema.NwbFile.Dataset.insert({**key, **item} for item in fresh)
+        insert_change(key, "corrected", None if placement is None else {
+            **{field: placement[field] for field in ("tier", "host", "share", "path")}, "n_bytes": n_bytes})
+    if placement is not None:
+        write_description(live, description)
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 11 passed.

Then everything publishing and the listing touch: `.venv/bin/python -m pytest tests/schema/test_nwb_correct.py tests/nwb tests/schema/test_nwb_build.py tests/responder/test_nwb_http.py -q -p no:cacheprovider`. Expected: 147 passed, 1 skipped.

- [ ] **Step 5: Mutation checks** (`nwb/correct.py`).
  - T3a: `if changed:` before `raise ChangedData` becomes `if False:` [`test_changed_written_once_data_stops_the_correction`].
  - T3b: `if _stamp(live) != before:` becomes `if False:` [`test_a_file_written_to_during_the_copy_is_left_and_tried_again`].
  - T3c: the `nwbinspector` check's condition becomes `False` [`test_a_critical_finding_in_the_corrected_copy_keeps_the_old_file`].
  - T3d: `if current["date_of_birth"] is None:` becomes `if False:` [`test_a_date_of_birth_now_unknown_is_not_written`].
  - T3e: the notes follow the file's lines by text, not by count [`test_a_file_already_holding_the_details_gets_only_its_records`].
  - T3f: the `corrected` change records no placement [`test_a_published_file_is_corrected_where_it_is_with_its_annotations`].
  - T3g: the subject's `NwbFile.Dataset` rows are not replaced [the same test].
  - T3h: the description beside the file is not rewritten [the same test].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/nwb/correct.py wl_preproc/nwb/publish.py tests/schema/test_nwb_correct.py
git commit -m "feat(nwb): one stale file is corrected where it is -- copied, patched, checked and swapped in, its records, checksums and description brought up to it with a corrected change

publish.record_change splits into insert_change, so a correction's change
lands in the transaction that records it.

<trailer lines>"
```

---

### Task 4: The daemon's correction stage

**Files:**
- Modify: `wl_preproc/nwb/correct.py`, `wl_preproc/nwb/publish.py`, `wl_preproc/daemon.py`, `wl_preproc/cli/main.py`
- Test: `tests/schema/test_nwb_correct.py`, `tests/schema/test_daemon.py`, `tests/schema/test_nwb_build.py`

**Interfaces — consumes:** Task 3's `correct` and `insert_change`.

**Interfaces — produces:**
- `correct.run_corrections(slow, fast=None, freed: list[dict] | None = None, day: datetime.date | None = None) -> tuple[int, list[str]]`: every stale file corrected, failures prefixed `NwbCorrection {key}:`; a file not yet published in a freed session waits.
- `daemon.run_once` reports `nwb_corrected`: `None` with neither `nwb_root` nor `nwb_slow`, `0` when the NWB lock is held elsewhere. `wlpp daemon` prints `nwb corrected: N`, or `nwb corrected: skipped (no --nwb-root or --nwb-slow-root)`.
- `publish._sweep` dates a move by its latest `published` or `moved` change (spec amendment 2).

**Why the sweep changes here.** A correction records the placement again, later, and the sweep read the move's time from the current placement. A copy left by a move, then written to before a correction, looked older than the move and was deleted. The test below failed that way before the sweep changed.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/schema/test_nwb_correct.py
+++ b/tests/schema/test_nwb_correct.py
@@ -368,3 +368,135 @@ def test_an_unreachable_share_fails_the_file(published, tmp_path_factory):
             correct(key, {"slow": gone}, _DAY)
     finally:
         _set_birth(_BIRTH)
+
+
+# -- The daemon's stage (spec section 2), and what one share's history means
+# for its leftovers once a file is corrected.
+
+def _written_of_subject() -> int:
+    from wl_preproc.schema import nwb as nwb_schema
+
+    return len(nwb_schema.NwbFile & {"subject": _SUBJECT, "status": "written"})
+
+
+def test_a_daemon_pass_corrects_every_stale_file_and_says_how_many(published, daemon_module, prefix, shares):
+    from wl_preproc.nwb.correct import file_subject, stale_files
+
+    _session_key, key, _runs, root = published
+    passes = {"nwb_root": root, "nwb_slow": shares["slow"], "nwb_fast": shares["fast"]}
+    daemon_module.run_once(prefix=prefix, **passes)  # anything still waiting is published first
+    written = _written_of_subject()  # with the earlier tests, its replacement and derivatives too
+    _set_birth(datetime.date(2016, 3, 1))
+    try:
+        report = daemon_module.run_once(prefix=prefix, **passes)
+        assert report["nwb_corrected"] == written, report["errors"]
+        assert not [error for error in report["errors"] if "NwbCorrection" in error]
+        assert not [stale for stale in stale_files() if stale["subject"] == _SUBJECT]
+        assert file_subject(_live(key, shares))["date_of_birth"] == datetime.date(2016, 3, 1)
+    finally:
+        _set_birth(_BIRTH)
+        assert daemon_module.run_once(prefix=prefix, **passes)["nwb_corrected"] == written
+    assert daemon_module.run_once(prefix=prefix, **passes)["nwb_corrected"] == 0
+
+
+def test_one_failing_file_does_not_stop_the_others(published, shares, monkeypatch):
+    from wl_preproc.nwb import correct as correct_module
+
+    _session_key, key, _runs, _root = published
+    real = correct_module.correct
+
+    def failing(each, shares_, day):
+        if each == key:
+            raise OSError("the share refused the copy")
+        return real(each, shares_, day)
+
+    monkeypatch.setattr(correct_module, "correct", failing)
+    _set_birth(datetime.date(2016, 3, 1))
+    try:
+        corrected, errors = correct_module.run_corrections(shares["slow"], shares["fast"])
+        assert corrected == _written_of_subject() - 1
+        assert [error for error in errors if "the share refused the copy" in error and error.startswith(
+            "NwbCorrection ")]
+    finally:
+        monkeypatch.undo()
+        _set_birth(_BIRTH)
+        correct_module.run_corrections(shares["slow"], shares["fast"])
+
+
+def test_a_file_not_yet_published_in_a_freed_session_waits(published, daemon_module, prefix, shares):
+    """As publishing does, the stage leaves a file still in scratch alone
+    while its session is freed (spec amendment 4); once it is not, the file
+    is corrected."""
+    from pathlib import Path
+
+    from tests.schema.test_nwb_build import _montage, _request
+    from wl_preproc.nwb.correct import file_subject, run_corrections
+    from wl_preproc.nwb.publish import current_placement
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import nwb as nwb_schema
+
+    session_key, _key, runs, root = published
+    waiting = accept(_request(session_key, "nwbfix1-freed", _montage(runs), runs,
+                              run_numbers=[run["run_number"] for run in runs]), prefix=prefix)
+    daemon_module.run_once(prefix=prefix, nwb_root=root)
+    path = Path((nwb_schema.NwbFile & waiting).fetch1("path"))
+    assert current_placement(waiting) is None
+    session = {"subject": session_key["subject"], "session_datetime": session_key["session_datetime"]}
+    _set_birth(datetime.date(2016, 3, 1))
+    try:
+        run_corrections(shares["slow"], shares["fast"], freed=[session])
+        assert file_subject(path)["date_of_birth"] == _BIRTH and _is_stale(waiting)
+        run_corrections(shares["slow"], shares["fast"])
+        assert file_subject(path)["date_of_birth"] == datetime.date(2016, 3, 1)
+    finally:
+        _set_birth(_BIRTH)
+        run_corrections(shares["slow"], shares["fast"])
+
+
+def test_a_leftover_written_after_its_move_is_kept_though_the_file_was_corrected_since(
+        published, shares, prefix, monkeypatch):
+    """A move whose old copy could not be deleted leaves it for the sweep,
+    which deletes it only if nobody wrote to it after the move. A correction
+    records the placement again, later; the sweep still dates the move by
+    the move, not by the correction. On the fast share, a correction also
+    respects its headroom (spec section 3, step 1)."""
+    import os
+
+    from tests.schema.test_nwb_build import _set_active
+    from wl_preproc.nwb import publish as publish_module
+    from wl_preproc.nwb.correct import CorrectionRefused, correct
+    from wl_preproc.nwb.publish import Share
+
+    _session_key, key, _runs, _root = published
+    old = _live(key, shares)
+    real = publish_module._remove_old_copy
+
+    def in_use(path):
+        raise PermissionError(f"{path} is in use")
+
+    monkeypatch.setattr(publish_module, "_remove_old_copy", in_use)
+    _set_active(key)
+    try:
+        publish_module.run_placement(shares["slow"], shares["fast"])
+        monkeypatch.setattr(publish_module, "_remove_old_copy", real)
+        moved = publish_module.current_placement(key)
+        assert moved["tier"] == "fast" and old.exists()
+        written_at = moved["changed_at"].replace(tzinfo=datetime.timezone.utc).timestamp() + 0.001
+        os.utime(old, (written_at, written_at))  # someone wrote to the old copy after the move
+        _set_birth(datetime.date(2016, 3, 1))
+        full = Share(tier="fast", mount=shares["fast"].mount, host="wl-nas", name="nvme", headroom_bytes=10**18)
+        with pytest.raises(CorrectionRefused, match="headroom"):
+            correct(key, {"slow": shares["slow"], "fast": full}, _DAY)
+        correct(key, shares, _DAY)
+        _moved, errors = publish_module.run_placement(shares["slow"], shares["fast"])
+        assert old.exists()
+        assert [error for error in errors if "written to after the file was moved from it" in error]
+    finally:
+        monkeypatch.setattr(publish_module, "_remove_old_copy", real)
+        _set_birth(_BIRTH)
+        correct(key, shares, _DAY)
+        old.unlink(missing_ok=True)
+        publish_module.description_path(old).unlink(missing_ok=True)
+        _set_active()
+        publish_module.run_placement(shares["slow"], shares["fast"])
+    assert publish_module.current_placement(key)["tier"] == "slow"
```

```diff
--- a/tests/schema/test_daemon.py
+++ b/tests/schema/test_daemon.py
@@ -270,10 +270,13 @@ def test_run_once_reports_what_it_did(daemon_env, prefix, tmp_path):
     first = daemon_env.run_once(prefix=prefix)
     baseline = daemon_env.run_once(prefix=prefix)
 
-    # `nwb` joined 2026-09-28 (design spec `2026-09-28-nwb-builder-design.md`).
+    # `nwb` joined 2026-09-28 (design spec `2026-09-28-nwb-builder-design.md`),
+    # `nwb_corrected` 2026-10-05 (`2026-10-05-subject-corrections-design.md`).
     assert set(baseline) == {
-        "populated", "errors", "stale_jobs_reaped", "archived", "nwb", "nwb_published", "nwb_moved", "freed_skipped"
+        "populated", "errors", "stale_jobs_reaped", "archived", "nwb", "nwb_published", "nwb_moved",
+        "nwb_corrected", "freed_skipped"
     }
+    assert baseline["nwb_corrected"] is None
     assert isinstance(baseline["freed_skipped"], int)
     assert baseline["populated"] == first["populated"], (
         "the daemon does not reach a steady state: two consecutive idle passes "
@@ -358,6 +361,7 @@ def test_daemon_cli_still_works_with_no_archival_flags(prefix, dj_conn, capsys):
 
     assert exit_code == 0
     assert "archived: skipped" in out
+    assert "nwb corrected: skipped (no --nwb-root or --nwb-slow-root)" in out
 
 
 # Every probe below is declared inside `core.schema`, not a standalone one:
```

```diff
--- a/tests/schema/test_nwb_build.py
+++ b/tests/schema/test_nwb_build.py
@@ -1129,7 +1129,7 @@ def test_a_second_wlpp_process_leaves_the_nwb_stages_alone(activation, daemon_mo
         report = daemon_module.run_once(prefix=prefix, nwb_root=tmp_path_factory.mktemp("nwb-locked"),
                                         nwb_slow=slow_share, nwb_fast=fast_share)
         assert [e for e in report["errors"] if "another wlpp process" in e], report["errors"]
-        assert (report["nwb"], report["nwb_published"], report["nwb_moved"]) == (0, 0, 0)
+        assert (report["nwb"], report["nwb_published"], report["nwb_moved"], report["nwb_corrected"]) == (0, 0, 0, 0)
         assert not nwb_schema.NwbFile & key
         assert main(_command(key, tmp_path_factory.mktemp("nwb-locked-command"), prefix)) == 1
         assert "another wlpp process" in capsys.readouterr().out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_nwb_correct.py tests/schema/test_daemon.py "tests/schema/test_nwb_build.py::test_a_second_wlpp_process_leaves_the_nwb_stages_alone" -q --tb=line -p no:cacheprovider`
Expected: 7 failed, 26 passed. No `run_corrections` and no `nwb_corrected` in the report or the printed lines; the leftover is deleted by the sweep.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/nwb/correct.py
+++ b/wl_preproc/nwb/correct.py
@@ -239,3 +239,31 @@ def _record(key: dict, row: dict, live: Path, placement: dict | None, current: d
             **{field: placement[field] for field in ("tier", "host", "share", "path")}, "n_bytes": n_bytes})
     if placement is not None:
         write_description(live, description)
+
+
+def run_corrections(slow, fast=None, freed: list[dict] | None = None,
+                    day: datetime.date | None = None) -> tuple[int, list[str]]:
+    """The daemon's correction stage (spec section 2): every stale written
+    file, corrected where it is. A file not yet published in a freed session
+    waits, as publishing does. A failure is reported per file and retried
+    next pass; it never stops another file. Returns `(corrected, failures)`."""
+    from wl_preproc.nwb.publish import current_placement
+
+    freed = freed or []
+    day = day or datetime.datetime.now(datetime.timezone.utc).date()
+    shares = {tier: share for tier, share in (("slow", slow), ("fast", fast)) if share is not None}
+    try:
+        stale = stale_files()
+    except Exception as exc:  # the daemon's other stages must still run
+        return 0, [f"NwbCorrection: {exc}"]
+    corrected, errors = 0, []
+    for key in stale:
+        try:
+            session = {"subject": key["subject"], "session_datetime": key["session_datetime"]}
+            if session in freed and current_placement(key) is None:
+                continue
+            correct(key, shares, day)
+            corrected += 1
+        except Exception as exc:  # one file must not stop the others; retried next pass
+            errors.append(f"NwbCorrection {key}: {exc}")
+    return corrected, errors
```

```diff
--- a/wl_preproc/nwb/publish.py
+++ b/wl_preproc/nwb/publish.py
@@ -391,7 +391,11 @@ def _sweep(key: dict, placement: dict, shares: dict[str, Share]) -> str | None:
     current = shares[placement["tier"]].local(placement["path"])
     if not current.exists():
         return f"{other}: left in place, because the current copy {current} is missing"
-    moved_at = placement["changed_at"].replace(tzinfo=datetime.timezone.utc).timestamp()
+    # Dated by the change that put the file where it is, never by a
+    # correction that recorded its placement again since (design spec
+    # `2026-10-05-subject-corrections-design.md`).
+    placed = [row for row in placements(key) if row["kind"] in ("published", "moved")]
+    moved_at = placed[-1]["changed_at"].replace(tzinfo=datetime.timezone.utc).timestamp()
     if other.exists() and other.stat().st_mtime > moved_at:
         return f"{other}: written to after the file was moved from it; left for a person"
     _remove_old_copy(other)
```

```diff
--- a/wl_preproc/daemon.py
+++ b/wl_preproc/daemon.py
@@ -1058,6 +1058,7 @@ def run_once(
     nwb_built: int | None = None
     nwb_published: int | None = None
     nwb_moved: int | None = None
+    nwb_corrected: int | None = None
     try:
         with exclusive(prefix) if nwb_root is not None or nwb_slow is not None else contextlib.nullcontext():
             # The NWB builder (design spec `2026-09-28-nwb-builder-design.md` section
@@ -1102,10 +1103,29 @@ def run_once(
                 except Exception as exc:  # a failing stage must not stop the others
                     nwb_moved = 0
                     errors.append(f"NwbPlacement: placement failed: {exc}")
+
+            # Subject corrections (design spec
+            # `2026-10-05-subject-corrections-design.md`): every written file
+            # whose subject's details changed, corrected where it is, after
+            # building, publishing and placement. `None` with neither the
+            # builder's root nor a share configured: there is no file to correct.
+            if nwb_root is None and nwb_slow is None:
+                nwb_corrected = None
+            else:
+                from wl_preproc.nwb.correct import run_corrections
+
+                try:
+                    nwb_corrected, correction_errors = run_corrections(
+                        nwb_slow, nwb_fast, freed=currently_freed(prefix=prefix))
+                    errors.extend(correction_errors)
+                except Exception as exc:  # a failing stage must not stop the others
+                    nwb_corrected = 0
+                    errors.append(f"NwbCorrection: corrections failed: {exc}")
     except Busy as busy:
         nwb_built = None if nwb_root is None else 0
         nwb_published = None if nwb_slow is None else 0
         nwb_moved = None if nwb_slow is None or nwb_fast is None else 0
+        nwb_corrected = None if nwb_root is None and nwb_slow is None else 0
         errors.append(f"NwbPlacement: {busy}")
 
     archived: int | None
@@ -1125,6 +1145,7 @@ def run_once(
         "nwb": nwb_built,
         "nwb_published": nwb_published,
         "nwb_moved": nwb_moved,
+        "nwb_corrected": nwb_corrected,
         # How many sessions were freed, and so skipped, when the pass began --
         # a count, so a skip never reads as an all-clear.
         "freed_skipped": freed_skipped,
```

```diff
--- a/wl_preproc/cli/main.py
+++ b/wl_preproc/cli/main.py
@@ -766,6 +766,10 @@ def main(argv: list[str] | None = None) -> int:
             print("nwb moved: skipped (no --nwb-fast-root)")
         else:
             print(f"nwb moved: {report['nwb_moved']}")
+        if report["nwb_corrected"] is None:
+            print("nwb corrected: skipped (no --nwb-root or --nwb-slow-root)")
+        else:
+            print(f"nwb corrected: {report['nwb_corrected']}")
         if report["errors"]:
             print("errors:")
             for err in report["errors"]:
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 33 passed.

Then the daemon, publishing, the listing and the CLI: `.venv/bin/python -m pytest tests/schema/test_nwb_correct.py tests/schema/test_daemon.py tests/schema/test_nwb_build.py tests/responder/test_nwb_http.py tests/cli tests/nwb -q -p no:cacheprovider`. Expected: 319 passed, 1 skipped.

- [ ] **Step 5: Mutation checks.**
  - T4a (`daemon.py`): the stage's call to `run_corrections` becomes `0, []` [`test_a_daemon_pass_corrects_every_stale_file_and_says_how_many`].
  - T4b (`publish.py::_sweep`): the move is dated by the current placement again [`test_a_leftover_written_after_its_move_is_kept_though_the_file_was_corrected_since`].
  - T4c (`correct.py::run_corrections`): `if session in freed and current_placement(key) is None:` becomes `if False:` [`test_a_file_not_yet_published_in_a_freed_session_waits`].
  - T4d (`run_corrections`): a failing file raises instead of being reported [`test_one_failing_file_does_not_stop_the_others`].
  - T4e (`cli/main.py`): the skipped line is not printed [`tests/schema/test_daemon.py::test_daemon_cli_still_works_with_no_archival_flags`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/nwb/correct.py wl_preproc/nwb/publish.py wl_preproc/daemon.py wl_preproc/cli/main.py tests/schema/test_nwb_correct.py tests/schema/test_daemon.py tests/schema/test_nwb_build.py
git commit -m "feat(daemon): the correction stage -- every stale written file corrected after building, publishing and placement, reported as nwb_corrected; the placement sweep dates a move by the move, not by a correction since

<trailer lines>"
```

---

### Task 5: The records, and the full suite

**Files:**
- Modify: `docs/ops/lab-host-protocol.md`, `docs/pending-wl-works-amendments.md`, `docs/superpowers/specs/2026-10-05-subject-corrections-design.md`, `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-10-05-subject-corrections.md`

- [ ] **Step 1: The protocol, and what wl.works is told.** Apply:

```diff
--- a/docs/ops/lab-host-protocol.md
+++ b/docs/ops/lab-host-protocol.md
@@ -519,7 +519,12 @@ Authorization: Bearer <token>
     the assignment, the aim, or neither. Read `schema_version`, and ignore fields you do
     not know.
 
-A file changes when it is built, published, or moved between shares.
+A file changes when it is built, published, moved between shares, or **its subject's details are
+corrected** (design spec `2026-10-05-subject-corrections-design.md`): a request that carries an
+animal's corrected species, sex or date of birth has every file already built for that animal
+rewritten in place within the daemon's next pass, its annotations kept. Its `description` then
+carries the new `subject`, and its `notes` a line such as *"Corrected 2026-11-02: date of birth
+2016-03-02 → 2016-03-01."* A correction that clears the date of birth is not written.
 
 ## `PUT /nwb/active`
 
```

```diff
--- a/docs/pending-wl-works-amendments.md
+++ b/docs/pending-wl-works-amendments.md
@@ -1,12 +1,37 @@
 # Amendments to wl-works
 
-**Seven are outstanding: two opened 2026-08-22, one 2026-09-28, one 2026-09-29, two 2026-09-30, one 2026-10-01.** The earlier two
+**Eight are outstanding: two opened 2026-08-22, one 2026-09-28, one 2026-09-29, two 2026-09-30, one 2026-10-01, one 2026-10-05.** The earlier two
 batches are closed; their records are kept below, because
 [`specs/2026-08-12-wl-preproc-design.md`](superpowers/specs/2026-08-12-wl-preproc-design.md)
 §14 items 10–11 point at it and a reference that dead-ends teaches nothing.
 
 ---
 
+# OPEN — subject corrections reach every file already built
+
+**Opened 2026-10-05**, answering the open question wl.works' January canonical-NWB spec left for this
+repository (`docs/superpowers/specs/2026-09-30-january-canonical-nwb-design.md` §7 and §12, read on
+its `main` at `4a994ee3`): *"a metadata-only rebuild of a published file whose subject details were
+corrected"*. Spec
+[`specs/2026-10-05-subject-corrections-design.md`](superpowers/specs/2026-10-05-subject-corrections-design.md),
+the requester's decisions of 2026-10-05.
+
+- **A correction you send is written into every file already built for that animal,** published or
+  not, canonical or derivative, current or superseded, in the daemon pass after the request that
+  carries it. Nothing is re-sorted, and the lab's annotations in the files are kept.
+- **Each corrected file says so.** Its subject's description, and its description's `notes`, gain a
+  line such as *"Corrected 2026-11-02: date of birth 2016-03-02 → 2016-03-01."*
+- **`GET /nwb` lists each corrected file again** past your cursor, with its new `subject`. Nothing
+  in the listing or description contracts changes; the description stays at version 3.
+- **A correction that clears a date of birth is not written:** a file without one fails
+  validation, so it keeps the details it has.
+- **What to amend on your side:** §7's *"A file already published keeps the details it was built
+  with, and the only fix wl.works can ask wl-preproc for today is a full replacement"* is no longer
+  true once this merges; the animal's *"N files built with earlier details"* falls to zero within a
+  pass of the request that carries the correction. Nothing new is asked of you.
+
+---
+
 # OPEN — measured runs, and what a block is now
 
 **Opened 2026-10-01**, answering ask 2 of wl.works' three asks back (its
```

- [ ] **Step 2: The spec's amendments.** Apply:

```diff
--- a/docs/superpowers/specs/2026-10-05-subject-corrections-design.md
+++ b/docs/superpowers/specs/2026-10-05-subject-corrections-design.md
@@ -201,3 +201,40 @@ Each is reported per file, and none stops the pass or another file.
 - **Each failure in §5,** with the live file untouched and the failure reported.
 - **`invalid` files** are still rebuilt as before, and never corrected.
 - **The full suite once,** on both interpreters, at the end.
+
+---
+
+## Amendments, 2026-10-05, made while proving the plan
+
+The plan (`plans/2026-10-05-subject-corrections.md`) was proven in code before it was written.
+These settle what the sections above left open; §0 stands.
+
+1. **§7's items, answered:**
+   - **1:** h5py rewrites the subject's text datasets of a file pynwb wrote, and pynwb reads the
+     file afterwards. A dataset the file lacks (a species first known now) is created with the
+     string type pynwb uses, and one now unknown is removed.
+   - **2:** the patch changes exactly the subject datasets whose values differ, and the
+     description: for a date of birth alone, `date_of_birth` and `description`.
+   - **3:** `nwbinspector` does not rate a date of birth after the session's start as critical, so
+     the validator's row in §5 is for a missing date of birth, which §5 refuses before copying,
+     and for anything else a check may find.
+   - **4:** `publish.current_placement` is the latest placement, so the one recorded with
+     `corrected` (same share and path) becomes current.
+   - **5:** `publish.write_description` writes by way of `.partial` and `os.replace`, atomically.
+   - **6:** the swap is `os.replace`, as publishing's and placement's renames into place are.
+     Untested on the NAS's mounts, as they are.
+2. **The placement sweep dates a move by its own change.** A copy left on the other share by a
+   move is deleted only if nobody wrote to it after the move. The sweep read that time from the
+   current placement, which a correction now records again, later; a leftover written to between
+   the move and the correction looked older than the move and was deleted. It now reads the time
+   of the latest `published` or `moved` change.
+3. **The description's notes follow the file's by count:** the correction lines the file carries
+   past those the description already has. The same correction made twice is noted twice, and a
+   pass after a crash that followed the swap records the note the crashed pass wrote, once.
+4. **The daemon reports `nwb_corrected`,** `None` with neither the builder's root nor a share
+   configured, and `0` when another wlpp process holds the NWB lock. Its failures are prefixed
+   `NwbCorrection`. A file not yet published in a freed session waits, as publishing does.
+5. **A correction did not change the file's size** in the small file measured; the size is
+   recorded anyway: the placement's `n_bytes` for a published file, `NwbFile.n_bytes` for one in
+   scratch.
+
```

- [ ] **Step 3: The checkpoint, `wl.yaml` and the handoff.** Apply, dating the BUILT lines with the day of execution:

```diff
--- a/docs/CHECKPOINT.md
+++ b/docs/CHECKPOINT.md
@@ -627,6 +627,17 @@ requester chose to merge the same day; true when written.*
 >    `job_request.json` and `nwb_description.json` once this merges.** See
 >    `docs/handoffs/2026-10-01-run-requests.md`.
 >
+>    **Subject corrections in built files (piece 4) are BUILT on
+>    `spec/subject-corrections` (2026-10-05), NOT merged as written** (spec
+>    `superpowers/specs/2026-10-05-subject-corrections-design.md`). When a
+>    request brings an animal's corrected species, sex or date of birth, the
+>    daemon's next pass corrects every written file of that animal where it
+>    is: a copy is patched, checked (other checksums, the live file unchanged,
+>    `nwbinspector`) and swapped in, annotations kept, with a note of the
+>    change in the file and its description; `GET /nwb` lists it again.
+>    `NwbChange.kind` gains `corrected`, so a development database alters that
+>    enum. See `docs/handoffs/2026-10-05-subject-corrections.md`.
+>
 > **Deferred minors: DONE 2026-09-26, both lists.** The gap-aware branch's
 > items 1–3 (`8af4278`; `eye/detect/validity.py` now cites commit `7d4a00f`
 > in place of a "finding H2" no document named) and all six parked
```

```diff
--- a/wl.yaml
+++ b/wl.yaml
@@ -186,6 +186,13 @@ status:
     its runs, with a description at version 3; and core.Block, ActivationBlock,
     BlockCoverage and block_agreement are retired (the same spec, Plan B;
     handoff `docs/handoffs/2026-10-01-run-requests.md`).
+    Subject corrections in built files are BUILT on `spec/subject-corrections`
+    (2026-10-05, NOT merged as written): a request bringing an animal's
+    corrected species, sex or date of birth has every written file of that
+    animal corrected in place in the daemon's next pass, annotations kept,
+    with a note of the change (design spec
+    `2026-10-05-subject-corrections-design.md`; handoff
+    `docs/handoffs/2026-10-05-subject-corrections.md`).
   next: >-
     **Hardware status as of 2026-09-19, stated by the requester at session
     close: the rig is NOT ready and the compute machine is NOT assembled.**
```

```diff
--- /dev/null
+++ b/docs/handoffs/2026-10-05-subject-corrections.md
@@ -0,0 +1,53 @@
+# Subject corrections in built files
+
+**Piece 4 of four that make a real wl-xcon recording usable here.** Pieces 1 to 3 are merged:
+runs and trials (`b0f8b52`), the landed-session listing (`eb1ff06`), and requests that name runs
+(`ecd1616`, its minors `762c17d`).
+- **Branch:** `spec/subject-corrections`, forked from `main` at `762c17d`.
+- **Spec:** `docs/superpowers/specs/2026-10-05-subject-corrections-design.md`, approved
+  2026-10-05, with the amendments made while proving the plan.
+- **Plan:** `docs/superpowers/plans/2026-10-05-subject-corrections.md`.
+- **The requester's choices:** corrections are automatic; the file keeps a note of the change; a
+  copy is patched, checked and swapped in, so the live file is never written into.
+
+Every line of the plan was proven in a scratch branch before the plan was written.
+
+---
+
+## 1. What was built
+
+- **A stale file is found by value** (`nwb/correct.py::stale_files`): a `written` file whose
+  stored description names other subject details than the subject's current ones. The comparison
+  is `gather.described_subject`, which `build.resolved_invalid` now shares.
+- **One file is corrected where it is** (`correct.correct`):
+  - copied to `.partial`;
+  - patched (`patch_subject`: species, sex and date of birth, and a note in the subject's
+    description);
+  - checked (every other written-once checksum, the live file unchanged since the copy began,
+    `nwbinspector`);
+  - swapped in with `os.replace`;
+  - recorded in one transaction: the description, the subject's checksums, the size, and a
+    `corrected` change with the placement again;
+  - its description file rewritten beside it.
+- **The daemon's stage** (`correct.run_corrections`) runs after building, publishing and
+  placement, under the NWB lock, and reports `nwb_corrected`.
+- **The placement sweep** dates a move by its own change, not by a correction since.
+- **`publish.record_change`** splits into `insert_change`, so a correction's change lands in the
+  transaction that records it.
+
+## 2. What the other repositories must do
+
+- **wl.works** (`pending-wl-works-amendments.md`, "subject corrections reach every file already
+  built"): its January spec §7's claim that a published file keeps its details is no longer true
+  once this merges. Nothing new is asked of it.
+- **wl-xcon:** nothing.
+
+## 3. Still open
+
+- **A development database** alters `NwbChange.kind` to add `corrected`. No real database exists
+  yet.
+- **The swap on the NAS's own mounts** is untested, as publishing's and placement's renames are.
+
+## 4. The rulings
+
+The spec's amendments 1 to 5, made while proving the plan.
```

Run `wl-check` on its own. Expected: `wl.yaml: no findings`.

Add §5, the measured counts, and §6, the final review, to the handoff as execution measures them.

- [ ] **Step 4: The full suite, once, on both interpreters.** With the BMD, NSLR and Andersson references set and `WLPP_OHDPI_REFERENCE` unset, as CI has it (`~/.cache/wl-preproc-references`):

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider > full311.log 2>&1; echo "311 exit $?"
~/.cache/wl-preproc-venv313/bin/python -m pytest -q -p no:cacheprovider > full313.log 2>&1; echo "313 exit $?"
```

Expected: `311 exit 0` and `313 exit 0`, **2168 passed, 25 skipped, 1 deselected, 1 xfailed** on 3.11 and **2167 passed, 27 skipped, 1 xfailed** on 3.13, proven with every task applied. The plan adds 21 tests to `main`'s `762c17d` (2147 on 3.11).

- [ ] **Step 5: Commit**

```bash
git add docs/ops/lab-host-protocol.md docs/pending-wl-works-amendments.md docs/superpowers/specs/2026-10-05-subject-corrections-design.md docs/CHECKPOINT.md wl.yaml docs/handoffs/2026-10-05-subject-corrections.md
git commit -m "docs: subject corrections -- the protocol, what wl.works is told, the spec's amendments from proving, the checkpoint, wl.yaml and the handoff

<trailer lines>"
```
